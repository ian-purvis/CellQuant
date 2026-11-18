"""Markers called by overlap with another analysis's objects, in the window: step 4's tick boxes and step 5's second outline layer.

Needs napari, Qt and OpenGL (a desktop, or xvfb-run with Mesa). Skipped when napari is missing.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

napari = pytest.importorskip("napari")

from tests.test_gui_crop import _events, _wait, open_window  # noqa: E402,F401

CLASSICAL = {"sigma": 1.0, "use_watershed": True, "watershed_min_distance_px": 4, "min_area_um2": 5}
LAYER = "OTX2 objects (other analysis)"


def _controller(tmp_path: Path, with_other: bool, run_other: bool = True):
    """An experiment whose active analysis finds objects in PAX6 (channel 2), with one run image; with the OTX2 one if asked."""

    from cellquant.controller import AnalysisController
    from cellquant.synthetic_retina import write_retina_set

    write_retina_set(tmp_path / "images", size=96, retinas=1, seed=4)
    controller = AnalysisController.create(tmp_path / "experiment", "Overlap", input_directory=tmp_path / "images" / "images")
    controller.add_image_paths([tmp_path / "images" / "images"])
    for index, name in enumerate(("OTX2", "Fluor", "PAX6")):
        controller.set_channel_name(index, name)
    base = controller.recipe.model_dump(mode="json")
    base["object_set"] = {"name": "Nuclei", "segmentation_channel": 2, "algorithm": "classical", "parameters": CLASSICAL}
    controller.set_recipe(base)
    controller.save()
    other = controller.analyses_for_channels([0])[0] if with_other else ""
    if run_other:
        controller.run_analyses()
    else:
        controller.run_image(controller.experiment.images[0].image_id)  # only the active analysis has objects
    return controller, other


def _shell(tmp_path: Path, open_window, with_other: bool = True, run_other: bool = True):  # noqa: F811
    controller, other = _controller(tmp_path, with_other, run_other)
    shell = open_window(controller.directory)
    _events(0.3)
    assert _wait(shell, 120) and shell._shown_image_id is not None
    return shell, other


def _tick_overlap(shell, on: bool = True, index: int = 0) -> None:
    setup = shell._marker_setup
    setup.overlap_boxes[index].setChecked(on)
    assert setup._auto_timer.isActive()  # a short pause first, no button press
    _events(1.4)
    assert _wait(shell)
    _events(0.3)


def _overlap_marker(controller):
    from cellquant.gui import guide

    return [item for item in controller.recipe.classifications if guide.uses_overlap(controller.recipe, item)]


def test_the_section_shows_only_with_another_analysis_and_follows_the_list(tmp_path: Path, open_window):  # noqa: F811
    shell, _other = _shell(tmp_path, open_window, with_other=False)
    setup = shell._marker_setup
    shell.go_to_step(3)
    assert not setup.overlap_section.isVisibleTo(setup) and not setup.overlap_boxes
    shell._analysis_bar.make_per_channel([0])  # a second analysis: the section appears by itself
    assert setup.overlap_section.isVisibleTo(setup)
    assert [box.text() for box in setup.overlap_boxes] == ['OTX2 objects (analysis "Objects in OTX2")']
    assert not setup.overlap_boxes[0].isChecked() and not setup.reverse_overlap.isChecked()
    assert setup.min_overlap.value() == 50.0 and setup.min_overlap.suffix() == " %"
    assert "No brightness cutoff" in setup.overlap_boxes[0].toolTip()
    # Renaming the analysis shows in its box at once.
    shell.controller.rename_analysis(shell.controller.analyses()[1].recipe_id, "Blue cells")
    shell._analysis_bar.refresh()
    assert [box.text() for box in setup.overlap_boxes] == ['OTX2 objects (analysis "Blue cells")']
    # Removing it takes the section away again.
    shell.controller.remove_analysis(shell.controller.analyses()[1].recipe_id)
    shell._analysis_bar.refresh()
    assert not setup.overlap_section.isVisibleTo(setup) and not setup.overlap_boxes


def test_ticking_adds_the_marker_measures_it_and_keeps_the_minimum_overlap(tmp_path: Path, open_window, monkeypatch):  # noqa: F811
    shell, other = _shell(tmp_path, open_window)
    setup, controller = shell._marker_setup, shell.controller
    said: list[str] = []
    monkeypatch.setattr(shell, "message", said.append)
    shell.go_to_step(3)
    image_id = controller.experiment.images[0].image_id
    _tick_overlap(shell)
    marker = _overlap_marker(controller)
    assert [item.id for item in marker] == ["otx2_objects_pos"] and marker[0].comparison == "at_least"
    assert marker[0].threshold == 50.0 and not marker[0].image_thresholds  # the minimum overlap, never a guess from the image
    result = controller.recall(image_id)
    values = result.objects["otx2_objects_overlap"].to_numpy(dtype=float)
    assert np.isfinite(values).all() and values.max() > 50 and values.min() >= 0
    assert shell._tabs.currentWidget() is shell._step_pages[3]  # never moved
    assert said[-1] == "Markers measured. Next: check the cutoffs in step 5."
    assert setup.overlap_boxes[0].isChecked()
    # Another minimum overlap goes to the marker like the minimum percent goes to percent markers.
    setup.min_overlap.setValue(30.0)
    _events(1.4)
    assert _wait(shell)
    _events(0.3)
    assert _overlap_marker(controller)[0].threshold == 30.0 and not _overlap_marker(controller)[0].image_thresholds
    assert setup.min_overlap.value() == 30.0
    # The same box ticked again does nothing more: the marker is not added twice.
    assert len(controller.recipe.classifications) == 1
    # It is saved with the experiment.
    from cellquant.controller import AnalysisController

    reopened = AnalysisController.open(controller.directory)
    assert [item.threshold for item in reopened.recipe.classifications] == [30.0] and reopened.overlap_analyses() == [other]


def test_unticking_removes_the_marker_after_asking_when_calls_by_hand_would_be_lost(tmp_path: Path, open_window, monkeypatch):  # noqa: F811
    from qtpy.QtWidgets import QMessageBox

    shell, _other = _shell(tmp_path, open_window)
    setup, controller = shell._marker_setup, shell.controller
    shell.go_to_step(3)
    image_id = controller.experiment.images[0].image_id
    _tick_overlap(shell)
    marker = _overlap_marker(controller)[0]
    controller.call_cells(image_id, marker.id, [1], True)
    controller.set_status(image_id, "approved")
    asked: list[str] = []
    answers = [QMessageBox.No, QMessageBox.Yes]
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda _parent, _title, text, *a, **k: asked.append(text) or answers.pop(0)))
    setup.overlap_boxes[0].setChecked(False)
    setup._auto_apply()
    assert "1 object you called by hand for OTX2 objects" in asked[-1] and "1 approved image will need approving again" in asked[-1]
    assert _overlap_marker(controller) and setup.overlap_boxes[0].isChecked()  # No: nothing changed, the box went back
    setup.overlap_boxes[0].setChecked(False)
    setup._auto_apply()
    assert _wait(shell)
    _events(0.3)
    assert not controller.recipe.classifications and not controller.recipe.measurements and not setup.overlap_boxes[0].isChecked()
    assert controller.experiment.image(image_id).processing_status == "analyzed"
    # Without calls by hand nothing is asked.
    asked.clear()
    _tick_overlap(shell)
    setup.overlap_boxes[0].setChecked(False)
    _events(1.4)
    assert _wait(shell)
    _events(0.3)
    assert not asked and not controller.recipe.classifications


def test_channel_ticks_and_rule_changes_leave_the_overlap_marker_alone(tmp_path: Path, open_window, monkeypatch):  # noqa: F811
    from qtpy.QtWidgets import QMessageBox

    from cellquant.gui import guide

    shell, other = _shell(tmp_path, open_window)
    setup, controller = shell._marker_setup, shell.controller
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    shell.go_to_step(3)
    _tick_overlap(shell)
    setup.min_overlap.setValue(35.0)
    _events(1.4)
    assert _wait(shell)
    _events(0.3)
    # The overlap marker is the only one: the channel boxes are still all ticked and the first setup button is still there.
    assert [box.text() for box in setup.boxes] == ["OTX2", "Fluor"] and all(box.isChecked() for box in setup.boxes)
    assert setup.button.isVisibleTo(setup) and not setup.auto_note.isVisibleTo(setup)
    setup.button.click()  # the first setup of the channel markers: the overlap marker is kept
    assert _wait(shell)
    _events(0.3)
    names = [item.name for item in controller.recipe.classifications]
    assert names == ["OTX2 objects", "OTX2", "Fluor"]
    kept = controller.recipe.classifications[0]
    assert guide.uses_overlap(controller.recipe, kept) and kept.threshold == 35.0 and controller.overlap_analyses() == [other]
    assert not setup.button.isVisibleTo(setup) and setup.auto_note.isVisibleTo(setup)
    assert setup.overlap_boxes[0].isChecked() and next(box for box in setup.boxes if box.text() == "OTX2").isChecked()
    # No starting cutoff was guessed for it, while the channel markers got theirs.
    assert kept.threshold == 35.0 and not kept.image_thresholds
    # Unticking a channel, ticking it again, and another rule: the overlap marker stays.
    next(box for box in setup.boxes if box.text() == "Fluor").setChecked(False)
    _events(1.4)
    assert _wait(shell)
    _events(0.3)
    assert [item.name for item in controller.recipe.classifications] == ["OTX2 objects", "OTX2"]
    setup.rule.setCurrentIndex(setup.rule.findData(guide.RULE_PERCENT))
    setup.rule.activated.emit(setup.rule.currentIndex())
    setup.min_percent.setValue(70.0)
    _events(1.4)
    assert _wait(shell)
    _events(0.3)
    kept = controller.recipe.classifications[0]
    assert guide.uses_overlap(controller.recipe, kept) and kept.threshold == 35.0
    percent = controller.recipe.classifications[1]
    assert guide.uses_pixel_level(controller.recipe, percent) and percent.threshold == 70.0  # only percent markers get the minimum percent
    assert setup.min_overlap.value() == 35.0


def test_a_first_setup_that_replaces_markers_still_keeps_the_overlap_marker(tmp_path: Path, open_window):  # noqa: F811
    shell, other = _shell(tmp_path, open_window)
    controller = shell.controller
    _tick_overlap(shell)
    shell.apply_marker_setup([(1, "Fluor")])  # what the first setup calls
    assert _wait(shell)
    _events(0.3)
    assert [item.name for item in controller.recipe.classifications] == ["OTX2 objects", "Fluor"]
    assert controller.overlap_analyses() == [other]


def test_the_reverse_box_gives_the_other_analysis_the_mirror_marker(tmp_path: Path, open_window):  # noqa: F811
    from cellquant.quicksetup import overlap_markers
    from cellquant.recipe import load_recipe

    shell, other = _shell(tmp_path, open_window)
    setup, controller = shell._marker_setup, shell.controller
    here = controller.recipe.recipe_id
    shell.go_to_step(3)
    path = controller.directory / "recipes" / f"{other}.yaml"
    assert overlap_markers(load_recipe(path).model_dump(mode="json")) == {}
    setup.reverse_overlap.setChecked(True)
    _tick_overlap(shell)
    assert controller.recipe.recipe_id == here  # the active analysis did not change
    mirror = overlap_markers(load_recipe(path).model_dump(mode="json"))
    assert list(mirror) == [here] and mirror[here]["threshold"] == 50.0 and mirror[here]["name"] == "PAX6 objects"
    assert len(_overlap_marker(controller)) == 1
    # Without the box ticked, the other analysis is left as it was.
    setup.overlap_boxes[0].setChecked(False)
    _events(1.4)
    assert _wait(shell)
    setup.reverse_overlap.setChecked(False)
    assert overlap_markers(load_recipe(path).model_dump(mode="json")) == {here: mirror[here]}  # unticking does not take the mirror away


def test_step_5_adds_the_other_objects_as_a_second_outline_layer(tmp_path: Path, open_window):  # noqa: F811
    from cellquant.gui.app import classification_colors, mask_colors, other_objects_color

    shell, other = _shell(tmp_path, open_window)
    setup, controller, panel, viewer = shell._marker_setup, shell.controller, shell._review_panel, shell.viewer
    shell.go_to_step(3)
    _tick_overlap(shell)
    setup.button.click()  # also an ordinary marker to compare with
    assert _wait(shell)
    _events(0.3)
    marker = _overlap_marker(controller)[0]
    ordinary = next(item for item in controller.recipe.classifications if item.id != marker.id)
    image_id = shell._shown_image_id
    shell.go_to_step(4)
    _events(0.3)
    assert LAYER not in viewer.layers  # Objects is shown: no marker, no second layer
    assert not panel.show_other_objects.isVisibleTo(panel)

    panel.display.setCurrentIndex(panel.display.findData(marker.id))
    _events(0.3)
    assert LAYER in viewer.layers
    layer = viewer.layers[LAYER]
    result = controller.recall(image_id)
    theirs = controller.objects_of(other, image_id)
    assert np.array_equal(np.asarray(layer.data), theirs) and layer.data.shape == result.labels.shape
    assert layer.contour == 2 and not layer.editable and layer.visible
    # Its colour is its own: not the positive, negative or chosen mask colours.
    color = other_objects_color()
    assert color not in (*classification_colors(), *mask_colors())
    assert "Classification" in viewer.layers and viewer.layers.index(LAYER) > viewer.layers.index("Classification")
    # Never the layer the edit tools use, even when it is the one selected.
    viewer.layers.selection.active = layer
    assert shell._edit_panel._labels is viewer.layers["Objects"] and panel._labels is viewer.layers["Objects"]
    assert shell._edit_panel._labels is not layer
    # The panel says what the slider is: percent of the object inside the other objects.
    assert panel.show_other_objects.isVisibleTo(panel) and panel.show_other_objects.text() == "Show OTX2 objects"
    assert panel.show_other_objects.isChecked() and panel.show_other_objects.toolTip()
    assert "inside OTX2 objects" in panel.cutoff_label.text() and "%" in panel.cutoff_label.text()
    assert (panel.threshold_slider.minimum(), panel.threshold_slider.maximum()) == (0.0, 100.0)
    assert "at least 50% of the object inside" in panel.counts.text()

    # Moving the cutoff keeps the one layer (it is not made again).
    panel.threshold_slider.setValue(80.0)
    _events(0.3)
    assert viewer.layers[LAYER] is layer and [item.name for item in viewer.layers].count(LAYER) == 1

    # The box hides and shows it; its eye icon and the box agree.
    panel.show_other_objects.setChecked(False)
    assert not layer.visible
    panel.show_other_objects.setChecked(True)
    assert layer.visible
    layer.visible = False
    assert not panel.show_other_objects.isChecked()
    layer.visible = True
    assert panel.show_other_objects.isChecked()
    panel.show_other_objects.setChecked(False)
    shell._set_other_objects(controller.recall(image_id))  # still hidden after it is set again
    assert not viewer.layers[LAYER].visible
    panel.show_other_objects.setChecked(True)

    # Another step, another marker, Objects: the layer goes; it comes back with the marker on step 5.
    shell.go_to_step(3)
    assert LAYER not in viewer.layers
    shell.go_to_step(4)
    _events(0.3)
    assert LAYER in viewer.layers
    panel.display.setCurrentIndex(panel.display.findData(ordinary.id))
    _events(0.3)
    assert LAYER not in viewer.layers and not panel.show_other_objects.isVisibleTo(panel)
    assert "Cutoff" == panel.cutoff_label.text() or "min % of object" in panel.cutoff_label.text()
    panel.display.setCurrentIndex(panel.display.findData(marker.id))
    _events(0.3)
    assert LAYER in viewer.layers
    panel.display.setCurrentIndex(0)
    _events(0.3)
    assert LAYER not in viewer.layers

    # Switching the analysis takes it away; the other analysis has no marker of this kind to show.
    panel.display.setCurrentIndex(panel.display.findData(marker.id))
    _events(0.3)
    assert LAYER in viewer.layers
    shell._analysis_bar.choice.setCurrentIndex(1)
    _wait(shell)
    _events(0.3)
    assert LAYER not in viewer.layers and controller.recipe.recipe_id == other


def test_without_objects_of_the_other_analysis_the_marker_is_blank_and_there_is_no_layer(tmp_path: Path, open_window, monkeypatch):  # noqa: F811
    shell, other = _shell(tmp_path, open_window, run_other=False)
    controller, panel = shell.controller, shell._review_panel
    said: list[str] = []
    monkeypatch.setattr(shell, "message", said.append)
    shell.go_to_step(3)
    _tick_overlap(shell)
    image_id = shell._shown_image_id
    result = controller.recall(image_id)
    assert result.objects["otx2_objects_overlap"].isna().all()  # blank, not an error
    assert "is blank in this image" in said[-1] and "run that analysis on this image first" in said[-1]
    marker = _overlap_marker(controller)[0]
    assert not controller.recipe.classifications[0].image_thresholds and marker.threshold == 50.0
    shell.go_to_step(4)
    panel.display.setCurrentIndex(panel.display.findData(marker.id))
    _events(0.3)
    assert LAYER not in shell.viewer.layers and not panel.show_other_objects.isVisibleTo(panel)
    assert "inside OTX2 objects" in panel.cutoff_label.text()  # the cutoff row still reads in percent
    # Running the other analysis on this image gives the marker its values; going back to step 5 shows its objects.
    here = controller.recipe.recipe_id
    controller.switch_analysis(other)
    controller.run_image(image_id)
    controller.switch_analysis(here)
    shell.go_to_step(3)
    shell.go_to_step(4)
    _events(0.5)
    assert _wait(shell)
    panel.display.setCurrentIndex(panel.display.findData(marker.id))
    _events(0.3)
    assert controller.recall(image_id).objects["otx2_objects_overlap"].notna().all()
    assert LAYER in shell.viewer.layers and panel.show_other_objects.isVisibleTo(panel)


def test_the_other_analysis_losing_its_objects_for_the_image_takes_the_layer_away(tmp_path: Path, open_window):  # noqa: F811
    shell, other = _shell(tmp_path, open_window)
    controller, panel = shell.controller, shell._review_panel
    shell.go_to_step(3)
    _tick_overlap(shell)
    marker = _overlap_marker(controller)[0]
    shell.go_to_step(4)
    panel.display.setCurrentIndex(panel.display.findData(marker.id))
    _events(0.3)
    assert LAYER in shell.viewer.layers
    # Another shape (the other analysis was run with another Z handling): nothing to compare, so nothing to draw.
    wrong = np.zeros((5, 5), dtype=np.int32)
    real = controller.objects_of
    controller.objects_of = lambda recipe_id, image_id: wrong  # type: ignore[method-assign]
    try:
        shell.refresh_other_objects()
        assert LAYER not in shell.viewer.layers
    finally:
        controller.objects_of = real  # type: ignore[method-assign]
    shell.refresh_other_objects()
    assert LAYER in shell.viewer.layers


def test_boxes_ticked_together_are_all_applied_and_a_waiting_channel_tick_is_kept(tmp_path: Path, open_window):  # noqa: F811
    controller, _other = _controller(tmp_path, True, run_other=False)
    controller.analyses_for_channels([1])
    controller.run_analyses()
    controller.save()
    shell = open_window(controller.directory)
    _events(0.3)
    assert _wait(shell, 120) and shell._shown_image_id is not None
    setup, controller = shell._marker_setup, shell.controller
    shell.go_to_step(3)
    assert [box.text() for box in setup.overlap_boxes] == ['OTX2 objects (analysis "Objects in OTX2")', 'Fluor objects (analysis "Objects in Fluor")']
    # Two boxes within the pause: one change, both markers.
    setup.overlap_boxes[0].setChecked(True)
    setup.overlap_boxes[1].setChecked(True)
    _events(1.4)
    assert _wait(shell)
    _events(0.3)
    assert [item.name for item in _overlap_marker(controller)] == ["OTX2 objects", "Fluor objects"]
    assert all(box.isChecked() for box in setup.overlap_boxes)
    # With channel markers set up: a channel tick and an overlap tick within the pause are both applied.
    setup.button.click()
    assert _wait(shell)
    _events(0.3)
    assert [item.name for item in controller.recipe.classifications] == ["OTX2 objects", "Fluor objects", "OTX2", "Fluor"]
    next(box for box in setup.boxes if box.text() == "Fluor").setChecked(False)
    setup.overlap_boxes[1].setChecked(False)
    for _ in range(4):
        _events(1.4)
        assert _wait(shell)
    _events(0.3)
    assert [item.name for item in controller.recipe.classifications] == ["OTX2 objects", "OTX2"]
    assert [box.isChecked() for box in setup.overlap_boxes] == [True, False]
    assert [box.isChecked() for box in setup.boxes] == [True, False]


def test_a_marker_compared_with_a_removed_analysis_can_still_be_unticked(tmp_path: Path, open_window):  # noqa: F811
    shell, other = _shell(tmp_path, open_window)
    setup, controller = shell._marker_setup, shell.controller
    shell.go_to_step(3)
    _tick_overlap(shell)
    controller.remove_analysis(other)
    shell._analysis_bar.refresh()
    assert [box.text() for box in setup.overlap_boxes] == ['OTX2 objects (analysis "Objects in OTX2", removed)']
    assert setup.overlap_boxes[0].isChecked() and "removed" in setup.overlap_boxes[0].toolTip()
    _tick_overlap(shell, on=False)
    assert not _overlap_marker(controller)
    assert not setup.overlap_section.isVisibleTo(setup) and not setup.overlap_boxes
