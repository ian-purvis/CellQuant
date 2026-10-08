"""Live napari window: threshold changes must not rebuild layers or reload the image.

Needs napari, a Qt binding, and OpenGL (a desktop, or a virtual display such as
xvfb-run with Mesa). Skipped when napari is not installed.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest

napari = pytest.importorskip("napari")

from tests.test_workflow import _recipe, _write_squares  # noqa: E402


@pytest.fixture
def window(tmp_path: Path):
    from cellquant.controller import AnalysisController
    from cellquant.gui.app import CellQuantWindow

    paths = []
    for index in range(2):
        path = tmp_path / f"s{index}.tif"
        _write_squares(path)
        paths.append(path)
    controller = AnalysisController.create(tmp_path / "experiment", "GUI")
    controller.add_image_paths(paths)
    controller.set_recipe(_recipe())
    controller.save()
    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001 - no OpenGL or display here
        pytest.skip(f"napari viewer could not start: {exc}")
    shell = CellQuantWindow(viewer, tmp_path / "experiment")
    yield shell
    viewer.close()


def _wait(shell, seconds: float = 30) -> None:
    from qtpy.QtWidgets import QApplication

    end = time.time() + seconds
    while (shell._job is not None or shell._batch is not None or shell._loader is not None) and time.time() < end:
        QApplication.processEvents()
        time.sleep(0.02)
    QApplication.processEvents()


def test_threshold_change_recolors_without_rebuilding_layers(window, monkeypatch):
    shell = window
    shell.run_current()
    _wait(shell)
    panel = shell._review_panel
    panel.display.setCurrentIndex(panel.display.findData("class_a"))
    layers_before = {name: id(shell.viewer.layers[name]) for name in [layer.name for layer in shell.viewer.layers]}
    assert "Classification" in layers_before

    def forbidden(*_args, **_kwargs):
        raise AssertionError("a threshold change must not read the image file")

    monkeypatch.setattr(shell.controller, "_load_display", forbidden)
    started = time.time()
    # Call the handler directly: an exception inside a Qt signal aborts the process under PyQt6.
    panel._threshold_moved(10_000.0)
    elapsed = time.time() - started

    layers_after = {layer.name: id(layer) for layer in shell.viewer.layers}
    assert layers_after == layers_before  # same layer objects, nothing added or removed
    overlay = shell.viewer.layers["Classification"].data
    assert set(np.unique(overlay).tolist()) <= {0, 1}  # nothing is positive at 10,000
    assert "Positive: 0" in panel.counts.text()
    assert elapsed < 2.0, f"threshold update took {elapsed:.2f} s"


def test_moving_to_another_image_reloads_it(window):
    shell = window
    shell.run_current()
    _wait(shell)
    first = shell._shown_image_id
    shell._footer._step(1)
    _wait(shell)
    assert shell._shown_image_id != first


def test_switching_images_does_not_block_the_window(window, monkeypatch):
    """A slow file read (ND2 stacks on OneDrive) must not freeze the interface thread."""

    shell = window
    _wait(shell)
    real = shell.controller._load_display

    def slow(record):
        time.sleep(1.0)
        return real(record)

    monkeypatch.setattr(shell.controller, "_load_display", slow)
    started = time.time()
    shell._footer._step(1)
    assert time.time() - started < 0.5, "switching images waited for the file on the interface thread"
    assert shell.image_loading()
    _wait(shell)
    assert shell._shown_image_id == shell._nav_ids[1]


def test_new_experiment_keeps_results_out_of_the_image_folder(tmp_path: Path, monkeypatch):
    from qtpy.QtWidgets import QDialog

    from cellquant.gui import app as gui_app

    images = tmp_path / "images" / "Retina 1"
    images.mkdir(parents=True)
    _write_squares(images / "a.tif")
    results = tmp_path / "images - CellQuant results"

    class Chosen:
        def __init__(self, parent=None):
            pass

        def exec(self):
            return QDialog.Accepted

        def experiment_name(self):
            return "Separate folders"

        def images_folder(self):
            return str(tmp_path / "images")

        def results_folder(self):
            return str(results)

    monkeypatch.setattr(gui_app, "NewExperimentDialog", Chosen)
    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"napari viewer could not start: {exc}")
    try:
        shell = gui_app.CellQuantWindow(viewer)
        shell.new_experiment()
        _wait(shell)
        assert [record.relative_path for record in shell.controller.experiment.images] == ["Retina 1/a.tif"]
        assert (results / "experiment.json").is_file()
        assert sorted(path.name for path in (tmp_path / "images").rglob("*")) == ["Retina 1", "a.tif"]
        # New experiment only makes new ones: a folder that already has one is not opened instead.
        shell.controller = None
        shell.new_experiment()
        assert shell.controller is None
    finally:
        viewer.close()


def test_3d_results_show_as_a_volume_with_a_recommendation(tmp_path: Path):
    from cellquant.controller import AnalysisController
    from cellquant.gui.app import CellQuantWindow
    from tests.test_3d import _recipe as recipe_3d
    from tests.test_3d import _stack

    path = tmp_path / "stack.tif"
    _stack(path)
    controller = AnalysisController.create(tmp_path / "results", "3D window")
    controller.add_image_paths([path])
    controller.set_recipe(recipe_3d("max_projection"))
    controller.save()
    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"napari viewer could not start: {exc}")
    try:
        shell = CellQuantWindow(viewer, tmp_path / "results")
        _wait(shell)
        panel = shell._objects_panel
        panel.show_gpu_status({"available": False, "reason": "test"})
        assert panel.z_recommend_box.isVisibleTo(panel)
        assert "Recommended here" in panel.z_recommend.text()
        assert any("★ recommended" in panel.z_stack.itemText(index) for index in range(panel.z_stack.count()))
        panel.use_recommendation()
        assert panel.z_stack.currentData() == panel._recommended == "stitch_slices"
        assert panel.z3d_box.isVisibleTo(panel)
        shell.run_current()
        _wait(shell)
        assert shell.viewer.layers["Objects"].data.shape == (9, 64, 64)
        assert shell.viewer.dims.ndim == 3  # a slice slider under the image
        assert shell.controller.last_results[shell._shown_image_id].qc.n_objects == 3
        assert "3D, 9 slices linked" in shell._footer.units.text()
        # After a real run, the estimate for that option is measured on this computer.
        panel.update_recommendation()
        index = panel.z_stack.findData("stitch_slices")
        assert "measured" in panel.z_stack.itemText(index)
    finally:
        viewer.close()


def _stack_window(tmp_path: Path, count: int = 1, mode: str = "stitch_slices"):
    from cellquant.controller import AnalysisController
    from cellquant.gui.app import CellQuantWindow
    from tests.test_3d import _recipe as recipe_3d
    from tests.test_3d import _stack

    for index in range(count):
        _stack(tmp_path / "data" / f"s{index}.tif")
    controller = AnalysisController.create(tmp_path / "results", "Window")
    controller.add_image_paths([tmp_path / "data"])
    controller.set_recipe(recipe_3d(mode))
    controller.save()
    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"napari viewer could not start: {exc}")
    shell = CellQuantWindow(viewer, tmp_path / "results")
    _wait(shell)
    return viewer, shell


def test_a_z_stack_has_a_slice_slider_in_every_z_mode(tmp_path: Path):
    viewer, shell = _stack_window(tmp_path, mode="max_projection")
    try:
        assert shell.viewer.dims.ndim == 3  # a slider for the 9 slices, though the analysis uses a projection
        assert shell.viewer.dims.current_step[0] == 4  # middle slice
        shell.run_current()
        _wait(shell)
        assert shell.viewer.layers["Objects"].data.shape == (64, 64)  # projection outlines over every slice
        assert shell.viewer.dims.ndim == 3
        assert "Analyzed: max projection of 9 slices" in shell._footer.units.text()
    finally:
        viewer.close()


def test_a_single_plane_image_has_no_slice_slider(window):
    _wait(window)
    assert window.viewer.dims.ndim == 2

def test_a_run_shows_its_progress_and_can_be_cancelled_part_way(tmp_path: Path, monkeypatch):
    from qtpy.QtWidgets import QApplication, QPushButton

    import cellquant.segmentation as segmentation

    real = segmentation.classical_segment

    def slow(channel, parameters):
        time.sleep(0.25)
        return real(channel, parameters)

    monkeypatch.setattr(segmentation, "classical_segment", slow)
    viewer, shell = _stack_window(tmp_path)
    try:
        footer = shell._footer
        run_buttons = [button for button in shell._footer.findChildren(QPushButton) if button.text() == "Run this image"]
        assert run_buttons, "footer Run this image button missing"
        assert footer.cancel.isEnabled() is False
        shell.run_current()
        end = time.time() + 20
        while "slice 3 of 9" not in footer.status.text() and time.time() < end:
            QApplication.processEvents()
            time.sleep(0.02)
        assert "Finding objects: slice 3 of 9" in footer.status.text()
        assert 0 < footer.progress.value() < 1000
        assert not any(button.isEnabled() for button in run_buttons)
        assert footer.cancel.isEnabled()
        footer._cancel()
        _wait(shell)
        assert "Stopped" in footer.status.text()
        assert shell.controller.recall(shell._nav_ids[0]) is None
        assert all(button.isEnabled() for button in run_buttons)
        assert not footer.cancel.isEnabled()
    finally:
        viewer.close()


def test_batch_progress_counts_images_and_slices(tmp_path: Path):
    from qtpy.QtWidgets import QApplication

    viewer, shell = _stack_window(tmp_path, count=2)
    try:
        seen = set()
        shell.start_batch(None)
        assert shell._footer.pause.isEnabled() and shell._footer.cancel.isEnabled()
        assert not shell._footer.resume.isEnabled()
        shell._footer.pause.click()
        assert shell._batch.paused and shell._footer.resume.isEnabled() and not shell._footer.pause.isEnabled()
        shell._footer.resume.click()
        assert not shell._batch.paused and shell._footer.pause.isEnabled() and not shell._footer.resume.isEnabled()
        end = time.time() + 60
        while shell._batch is not None and time.time() < end:
            QApplication.processEvents()
            seen.add(shell._footer.status.text())
            time.sleep(0.005)
        QApplication.processEvents()
        assert any(text.startswith("Image 1 of 2") and "slice" in text for text in seen), sorted(seen)[:10]
        # The second file has the same pixels, so its saved segmentation is reused: no slices to report.
        assert any(text.startswith("Image 2 of 2") for text in seen)
        assert "2 finished" in shell._footer.status.text()
        assert shell._footer.progress.value() == 1000
    finally:
        viewer.close()


def test_file_type_filter_hides_other_types(tmp_path: Path):
    viewer, shell = _stack_window(tmp_path, count=3, mode="max_projection")
    try:
        panel = shell._experiment_panel
        assert "3 of 3 images included" in panel.included_label.text()
        panel.show_type.setCurrentIndex(panel.show_type.findData("nd2"))
        assert all(panel.table.isRowHidden(row) for row in range(panel.table.rowCount()))  # no ND2 files here
        from qtpy.QtWidgets import QCheckBox, QPushButton

        texts = {button.text() for button in panel.findChildren(QPushButton)}
        assert {"Add images…", "Add folder…"} <= texts
        assert not any(box.text() == "Advanced" for box in panel.findChildren(QCheckBox))
    finally:
        viewer.close()


def test_channels_use_the_file_colors(tmp_path: Path):
    from cellquant.gui.app import CellQuantWindow
    from cellquant.practice import create_practice_experiment

    create_practice_experiment(tmp_path / "practice")
    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"napari viewer could not start: {exc}")
    try:
        shell = CellQuantWindow(viewer, tmp_path / "practice" / "experiment")
        _wait(shell)
        top = {
            layer.name: tuple(np.round(layer.colormap.colors[-1][:3], 3))
            for layer in viewer.layers
            if layer.name.startswith("Channel ")
        }
        assert top == {
            "Channel 1 = Nuclei": (0, 0, 1),
            "Channel 2 = Marker A": (0, 1, 0),
            "Channel 3 = Marker B": (1, 0, 0),
        }
    finally:
        viewer.close()


def test_gpu_banner_engine_menu_and_settings_locked_while_running(window):
    from qtpy.QtWidgets import QApplication

    from cellquant.engines import CellposeEngine

    shell = window
    panel = shell._objects_panel
    panel.engine = CellposeEngine(True, "4.2.0", "cellpose4", ("cpsam",), "cpsam")  # as on a Cellpose computer
    panel.method.setCurrentIndex(panel.method.findData("cellpose"))
    panel.show_gpu_status({"available": True, "name": "Test GPU", "memory_gb": 24})
    assert "NVIDIA GPU found" in panel.gpu_label.text() and "Test GPU" in panel.gpu_label.text()
    assert "NVIDIA GPU found: Test GPU" in shell._footer.log.toPlainText()
    assert "will run on the GPU" in panel.gpu_label.text() and panel.use_gpu()
    panel.show_gpu_status({"available": False, "reason": "test"})
    assert "No usable GPU found" in panel.gpu_label.text()
    # Source channel, then Method, then engine, then Z-stack mode.
    form = panel.layout().itemAt(1).layout()
    channel_row, method_row, engine_row, z_row = [
        form.getWidgetPosition(widget)[0] for widget in (panel.channel, panel.method, panel.engine_choice, panel.z_box)
    ]
    assert channel_row < method_row < engine_row < z_row
    assert panel.engine_choice.isVisibleTo(panel)
    assert panel.z_stack.isEnabled()
    panel.method.setCurrentIndex(panel.method.findData("classical"))
    assert not panel.engine_choice.isVisibleTo(panel)

    shell.run_current()
    assert shell.is_busy()
    assert shell._run_lock_note.isVisibleTo(shell._dock)
    assert not panel.method.isEnabled() and not panel.z_stack.isEnabled()
    assert not shell._measurements_panel.statistic.isEnabled()
    assert shell._review_panel.display.isEnabled()  # display only, not a setting
    _wait(shell)
    QApplication.processEvents()
    assert not shell._run_lock_note.isVisibleTo(shell._dock)
    assert panel.method.isEnabled() and shell._measurements_panel.statistic.isEnabled()


def test_cellpose_uses_a_usable_gpu_whatever_was_saved(window):
    from cellquant.engines import CellposeEngine

    shell = window
    _wait(shell)
    panel = shell._objects_panel
    panel.engine = CellposeEngine(True, "4.2.0", "cellpose4", ("cpsam",), "cpsam")
    panel.method.setCurrentIndex(panel.method.findData("cellpose"))
    panel.show_gpu_status({"available": False, "reason": "test"})
    panel.write_recipe()
    assert shell.controller.recipe.object_set.parameters["gpu"] is False
    panel.show_gpu_status({"available": True, "name": "Test GPU", "memory_gb": 24})
    panel.write_recipe()
    assert shell.controller.recipe.object_set.parameters["gpu"] is True
    panel.show_gpu_status({"available": False, "reason": "test"})


def test_plain_menus_hidden_fields_errors_and_run_bar(window):
    shell = window
    objects = shell._objects_panel
    # Plain words in the menus; the saved values are unchanged.
    assert objects.method.itemText(objects.method.findData("classical")).startswith("Classical")
    assert objects.area_unit.itemText(objects.area_unit.findData("um2")) == "µm²"
    # Only the settings the chosen method uses are shown.
    objects.method.setCurrentIndex(objects.method.findData("classical"))
    objects.threshold_method.setCurrentIndex(objects.threshold_method.findData("otsu"))
    assert objects.threshold_method.isVisibleTo(objects) and not objects.threshold.isVisibleTo(objects)
    objects.threshold_method.setCurrentIndex(objects.threshold_method.findData("manual"))
    assert objects.threshold.isVisibleTo(objects)
    box = objects.advanced_box  # collapsed until Advanced is ticked
    assert not objects.diameter.isVisibleTo(box) and objects.fill_holes.isVisibleTo(box)
    objects.method.setCurrentIndex(objects.method.findData("cellpose"))
    assert not objects.threshold_method.isVisibleTo(objects) and not objects.sigma.isVisibleTo(objects)
    assert objects.diameter.isVisibleTo(box) and not objects.fill_holes.isVisibleTo(box)
    objects.method.setCurrentIndex(objects.method.findData("classical"))
    objects.write_recipe()
    assert shell.controller.recipe.object_set.algorithm == "classical"
    measurements = shell._measurements_panel
    measurements.region.setCurrentIndex(measurements.region.findData("ring"))
    assert measurements.inner.isVisibleTo(measurements) and not measurements.distance.isVisibleTo(measurements)
    measurements.region.setCurrentIndex(measurements.region.findData("object"))
    assert not measurements.inner.isVisibleTo(measurements) and not measurements.pixel_level.isVisibleTo(measurements)

    # Problems show in a red box, cleared when the next run starts.
    shell.show_error("Test problem")
    assert shell._error_box.isVisibleTo(shell._dock) and "Test problem" in shell._error_note.text()
    shell.run_current()
    assert not shell._error_box.isVisibleTo(shell._dock)
    _wait(shell)

    # The run button the step expects stands out; Page Down moves to the next image.
    shell.go_to_step(0)
    assert shell._footer._run_buttons["current"].styleSheet() and not shell._footer._run_buttons["all"].styleSheet()
    shell.go_to_step(5)
    assert shell._footer._run_buttons["all"].styleSheet() and not shell._footer._run_buttons["current"].styleSheet()
    # Time left shows beside the progress bar while a run goes, with how it is worked out in the tooltip.
    footer = shell._footer
    footer.start_busy(batch=True)
    footer.update_progress(1, 3, "a.tif", "running")
    assert footer.time_left.text() == "Estimating…"
    footer._clock._image_start -= 60  # the first image took a minute
    footer.update_progress(1, 3, "a.tif", "Success")
    assert footer.time_left.text() == "~2 min left" and "per image" in footer.time_left.toolTip()
    footer.end_busy()
    assert footer.time_left.text() == ""


def test_cutoff_slider_recolors_with_chosen_colours_and_locks_during_runs(window, monkeypatch, tmp_path):
    from qtpy.QtCore import QSettings
    from qtpy.QtGui import QColor
    from qtpy.QtWidgets import QApplication

    from cellquant.gui import app

    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    monkeypatch.setattr(app, "_settings", lambda: settings)  # keep the user's real colours untouched
    shell = window
    shell.run_current()
    _wait(shell)
    panel = shell._review_panel
    panel.display.setCurrentIndex(panel.display.findData("class_a"))
    slider = panel.threshold_slider
    assert slider.isVisibleTo(panel) and slider.minimum() < slider.maximum()

    # Dragging to the top makes everything negative; the overlay updates after the short delay.
    slider.setValue(slider.maximum() + 1)  # past every object
    deadline = time.time() + 2
    while time.time() < deadline and "Positive: 0\n" not in panel.counts.text():
        QApplication.processEvents()
        time.sleep(0.01)
    assert "Positive: 0\n" in panel.counts.text()
    slider.setValue(slider.minimum())
    deadline = time.time() + 2
    while time.time() < deadline and 2 not in np.unique(shell.viewer.layers["Classification"].data):
        QApplication.processEvents()
        time.sleep(0.01)
    assert 2 in np.unique(shell.viewer.layers["Classification"].data)

    # Default colours: green positive, red negative; a chosen colour is applied and remembered.
    colormap = shell.viewer.layers["Classification"].colormap.color_dict
    assert np.allclose(colormap[2][:3], app._rgba(app.DEFAULT_POSITIVE_COLOR)[:3])
    assert np.allclose(colormap[1][:3], app._rgba(app.DEFAULT_NEGATIVE_COLOR)[:3])
    from qtpy.QtWidgets import QColorDialog

    monkeypatch.setattr(QColorDialog, "getColor", staticmethod(lambda *_args, **_kwargs: QColor("#3366ff")))
    panel._choose_color(positive=True)
    assert app.classification_colors() == ("#3366ff", app.DEFAULT_NEGATIVE_COLOR)
    colormap = shell.viewer.layers["Classification"].colormap.color_dict
    assert np.allclose(colormap[2][:3], app._rgba("#3366ff")[:3])
    panel._reset_colors()
    assert app.classification_colors() == (app.DEFAULT_POSITIVE_COLOR, app.DEFAULT_NEGATIVE_COLOR)

    # Locked while a run uses the settings.
    shell.run_current()
    assert not slider.isEnabled()
    assert panel.positive_color.isEnabled()  # display only
    _wait(shell)
    QApplication.processEvents()
    assert slider.isEnabled()


def test_the_mouse_wheel_changes_a_menu_only_after_it_is_clicked(window):
    from qtpy.QtCore import QPoint, QPointF, Qt
    from qtpy.QtGui import QWheelEvent
    from qtpy.QtWidgets import QApplication

    shell = window
    shell.go_to_step(1)
    QApplication.processEvents()
    box = shell._objects_panel.method

    def wheel():
        middle = QPointF(box.width() / 2, box.height() / 2)
        event = QWheelEvent(
            middle, QPointF(box.mapToGlobal(middle.toPoint())), QPoint(0, 0), QPoint(0, -120),
            Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False,
        )
        QApplication.sendEvent(box, event)
        QApplication.processEvents()

    box.clearFocus()
    before = box.currentIndex()
    wheel()
    assert box.currentIndex() == before  # scrolling past it leaves it alone
    box.setFocus()
    QApplication.processEvents()
    if box.hasFocus():  # focus needs an active window, which some test displays lack
        wheel()
        assert box.currentIndex() != before


def test_review_fixes_selection_errors_navigation_and_sizes(window, monkeypatch):
    """Fixes from the UI review: no unasked deletes, no stuck runs, plain tables, bounded navigation, sizes for many images."""

    shell = window
    controller = shell.controller
    shell.run_current()
    _wait(shell)
    panel = shell._edit_panel
    image_id = shell._nav_ids[shell._nav_index]
    before = controller.recall(image_id)
    kept = int((~before.objects["excluded"].astype(bool)).sum())

    # Nothing clicked: Delete object refuses instead of removing object 1.
    panel._delete()
    _wait(shell)
    assert "Click an object" in shell._footer.status.text()
    assert int((~controller.recall(image_id).objects["excluded"].astype(bool)).sum()) == kept
    # A picked object is deleted.
    panel.pick(1)
    assert "Selected object: 1" in panel.selected.text()
    panel._delete()
    _wait(shell)
    assert int((~controller.recall(image_id).objects["excluded"].astype(bool)).sum()) == kept - 1

    # A batch that crashes leaves the running state and says why.
    def boom(*_args, **_kwargs):
        raise KeyError("missing")

    monkeypatch.setattr(controller, "run_images", boom)
    shell.start_batch(None)
    _wait(shell)
    assert shell._batch is None and not shell._run_lock_note.isVisibleTo(shell._dock)
    assert shell._error_box.isVisibleTo(shell._dock) and "KeyError" in shell._error_note.text()

    # Remove measurement with no row chosen removes nothing.
    measurements = shell._measurements_panel
    measurements.refresh()
    count = len(controller.recipe.measurements)
    measurements.table.setCurrentCell(-1, -1)
    measurements._remove()
    assert len(controller.recipe.measurements) == count
    # The tables show channel names and plain words, not indices and keys.
    assert measurements.table.item(0, 1).text() == controller._channel_name(int(controller.recipe.measurements[0].channel))
    assert measurements.table.item(0, 3).text() == "Mean brightness"

    # Previous / Next stop at the ends instead of wrapping around.
    shell._nav_index = len(shell._nav_ids) - 1
    shell._footer._step(1)
    assert shell._nav_index == len(shell._nav_ids) - 1 and "last image" in shell._footer.status.text()
    assert f"of {len(shell._nav_ids)}" in shell._footer.position.text()

    # Set sizes (µm) by default fills every image without a size.
    for record in controller.experiment.images:
        record.pixel_size_x = record.pixel_size_y = None
    shell._experiment_panel.pixel_x.setValue(0.5)
    shell._experiment_panel.pixel_y.setValue(0.5)
    shell._experiment_panel._apply_pixel_size()
    assert all(record.pixel_size_x == 0.5 for record in controller.experiment.images)


def test_layers_are_named_by_the_channel_they_hold(tmp_path: Path):
    """An image whose file lists the channels in another order gets each layer's name right."""

    from cellquant.controller import AnalysisController
    from cellquant.gui.app import CellQuantWindow

    paths = []
    for index in range(2):
        path = tmp_path / f"s{index}.tif"
        _write_squares(path)
        paths.append(path)
    controller = AnalysisController.create(tmp_path / "experiment", "Layouts")
    controller.add_image_paths(paths)
    first, second = controller.experiment.images
    first.channel_names = ["DAPI", "Red", "Green", "Far Red"]
    second.channel_names = ["Red", "DAPI", "Green", "Far Red"]
    controller.experiment.channels[0].channel_name = controller.experiment.channels[0].display_name = "Nuclei"
    controller.experiment.channels[1].channel_name = controller.experiment.channels[1].display_name = "Marker"
    assert controller.display_channels(second, 4) == [1, 0, 2, 3]
    controller.set_recipe(_recipe())
    controller.save()
    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"napari viewer could not start: {exc}")
    try:
        shell = CellQuantWindow(viewer, tmp_path / "experiment")
        _wait(shell)
        shell._nav_index = shell._nav_ids.index(second.image_id)
        shell.show_current()
        _wait(shell)
        names = [layer.name for layer in viewer.layers if layer.name.startswith("Channel ")]
        assert names[:2] == ["Channel 1 = Red", "Channel 2 = DAPI"]
    finally:
        viewer.close()


def test_a_popped_out_panel_has_minimize_maximize_and_reset(window):
    from qtpy.QtWidgets import QApplication

    header = window._floating_headers[0]
    dock = header.dock
    dock.setFloating(True)
    end = time.time() + 2
    while dock.titleBarWidget() is not header and time.time() < end:
        QApplication.processEvents()
        time.sleep(0.02)
    assert dock.titleBarWidget() is header
    assert [button.toolTip() for button in header.buttons.values()] == ["Minimize", "Maximize", "Restore size", "Reset", "Close"]
    assert not header.buttons["restore"].isEnabled()
    header.buttons["minimize"].click()
    assert header.minimized and not dock.widget().isVisibleTo(dock)
    # Each button does one thing: Minimize again is unavailable; Restore size unfolds.
    assert not header.buttons["minimize"].isEnabled() and header.buttons["restore"].isEnabled()
    header.buttons["restore"].click()
    assert not header.minimized and dock.widget().isVisibleTo(dock)
    assert header.buttons["minimize"].isEnabled() and not header.buttons["restore"].isEnabled()
    header.buttons["reset"].click()
    QApplication.processEvents()
    assert not dock.isFloating()
    assert dock.titleBarWidget() is not header


def test_find_objects_has_preview_but_run_lives_in_the_run_window(window):
    from qtpy.QtWidgets import QPushButton

    names = [button.text() for button in window._objects_panel.findChildren(QPushButton)]
    assert "Preview" in names
    assert "Run this image" not in names
    footer_names = [button.text() for button in window._footer.findChildren(QPushButton)]
    assert footer_names.count("Run this image") == 1


def test_remove_marker_also_removes_its_result_rows_and_settings_save_themselves(window, monkeypatch):
    from qtpy.QtWidgets import QMessageBox

    from cellquant.controller import AnalysisController

    shell = window
    panel = shell._measurements_panel
    panel.refresh()
    panel.classes.setCurrentCell(1, 0)  # marker B, used by the row "A AND B among A"
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    panel._remove_class()
    recipe = shell.controller.recipe
    assert [item.name for item in recipe.classifications] == ["A"]
    assert [(item.numerator, item.denominator) for item in recipe.reports] == [("A", "all_objects")]
    # A new marker never reuses an id still taken.
    panel.class_name.setText("C")
    panel._add_class()
    panel.write_recipe()
    ids = [item.id for item in shell.controller.recipe.classifications]
    assert len(ids) == len(set(ids))

    # A changed step 2 setting is saved without a Save button.
    objects = shell._objects_panel
    objects.sigma.setValue(2.5)
    objects.sigma.editingFinished.emit()
    shell._settings_save_timer.timeout.emit()
    saved = AnalysisController.open(shell.controller.directory)
    assert saved.recipe.object_set.parameters["sigma"] == 2.5

