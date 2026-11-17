"""Dragging a cutoff slider in step 5 stays cheap: each step rewrites only the pixels of the objects whose call flipped
(no new layer data, no new colormap), counts follow at once, and the step 4 Markers table is refilled once the slider
stops or is released, before anything reads it back.

Needs napari, Qt and OpenGL (a desktop, or xvfb-run with Mesa). Skipped without napari or OpenGL.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

napari = pytest.importorskip("napari")

from tests.test_gui_crop import _events, _wait, open_window  # noqa: E402,F401
from tests.test_workflow import _recipe, _write_squares  # noqa: E402


def _expected_overlay(shell, result, classification_id):
    labels = np.asarray(result.labels)
    frame = result.objects
    codes = np.zeros(int(labels.max()) + 1, dtype=np.int32)
    for object_id, value in zip(frame["object_id"], frame[classification_id], strict=True):
        codes[int(object_id)] = 2 if value in (True, 1, "positive") else 1
    return codes[labels]


def test_cutoff_drag_rewrites_only_flipped_objects_and_settles_the_table(tmp_path: Path, open_window, monkeypatch):  # noqa: F811
    from cellquant.controller import AnalysisController

    image = tmp_path / "a.tif"
    _write_squares(image)
    controller = AnalysisController.create(tmp_path / "experiment", "Cutoff drag")
    controller.add_image_paths([image])
    controller.set_recipe(_recipe())
    controller.save()
    shell = open_window(controller.directory)
    from napari._vispy.utils.gl import get_max_texture_sizes

    if get_max_texture_sizes()[0] is None:
        pytest.skip("needs OpenGL (a desktop, or xvfb-run)")
    shell.run_current()
    assert _wait(shell, 120)
    panel, viewer = shell._review_panel, shell.viewer
    shell.go_to_step(4)
    panel.display.setCurrentIndex(panel.display.findData("class_a"))
    _events(0.2)
    image_id = shell._nav_ids[shell._nav_index]
    layer = viewer.layers["Classification"]
    data, colormap = layer.data, layer.colormap
    refills = []
    real_refresh = shell._measurements_panel.refresh
    monkeypatch.setattr(shell._measurements_panel, "refresh", lambda: (refills.append(1), real_refresh())[1])
    partial = []  # napari's in-place update of a part of a labels layer (as painting does)
    layer.events.labels_update.connect(lambda _event: partial.append(1))

    def classification():
        return next(item for item in shell.controller.recipe.classifications if item.id == "class_a")

    counts_before = panel.counts.text()
    assert (np.asarray(layer.data) == 2).any()  # object 1 (marker A 800) is positive at the cutoff of 200
    # A step that flips object 1: its pixels change in place, the colors stay, the counts follow.
    panel._slider_moved(900.0)
    _events(0.15)
    assert classification().threshold_for(image_id) == 900
    result = shell.controller.last_results[image_id]
    assert layer.data is data and layer.colormap is colormap
    assert partial == [1]
    assert np.array_equal(np.asarray(layer.data), _expected_overlay(shell, result, "class_a"))
    assert not (np.asarray(layer.data) == 2).any()
    assert len(layer._undo_history) == 0
    assert panel.counts.text() != counts_before  # the positive count follows the step
    # A step that flips nothing: nothing is rewritten.
    panel._slider_moved(850.0)
    _events(0.15)
    assert classification().threshold_for(image_id) == 850
    assert layer.data is data and layer.colormap is colormap
    assert partial == [1]
    # Still moving: the Markers table waits.
    assert refills == []
    # The slider stops: the table is refilled once.
    _events(0.5)
    assert refills == [1]

    # Released: the last step applies at once, and so does the table.
    panel._slider_moved(100.0)
    panel._slider_released()
    assert classification().threshold_for(image_id) == 100
    assert refills == [1, 1]
    assert np.array_equal(np.asarray(layer.data), _expected_overlay(shell, shell.controller.last_results[image_id], "class_a"))
    _events(0.5)
    assert refills == [1, 1]

    # Anything that reads the panels back first brings the table up to date.
    panel._slider_moved(650.0)
    _events(0.1)
    assert refills == [1, 1]
    shell._panels_to_recipe()
    assert refills == [1, 1, 1]
    _events(0.5)
    assert refills == [1, 1, 1]

    # A typed cutoff (not a drag) still refreshes everything at once.
    panel._threshold_moved(500.0)
    assert refills == [1, 1, 1, 1]
