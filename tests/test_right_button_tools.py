"""The right mouse button only moves the view, whatever tool is on: real Qt mouse events sent to the viewer's canvas.

A right drag moves camera.center and nothing else (no zoom, no deleting, selecting, painting, marking or drawing);
a plain right click changes nothing at all. The same left click still reaches the tool.

Needs napari, Qt and OpenGL (a desktop, or xvfb-run with Mesa). Skipped without napari or OpenGL.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

napari = pytest.importorskip("napari")

from tests.test_gui_crop import _events, _wait, open_window  # noqa: E402,F401
from tests.test_workflow import _recipe, _write_squares  # noqa: E402


def _open(tmp_path, open_window, name):  # noqa: F811
    from cellquant.controller import AnalysisController

    image = tmp_path / "a.tif"
    _write_squares(image)
    controller = AnalysisController.create(tmp_path / "experiment", name)
    controller.add_image_paths([image])
    controller.set_recipe(_recipe())
    controller.save()
    shell = open_window(controller.directory)
    from napari._vispy.utils.gl import get_max_texture_sizes

    if get_max_texture_sizes()[0] is None:
        pytest.skip("needs OpenGL (a desktop, or xvfb-run)")
    return shell


def _canvas_point(viewer, data_point):
    """The canvas pixel showing this point of the Objects layer."""

    from qtpy.QtCore import QPointF

    world = viewer.layers["Objects"].data_to_world(tuple(data_point))
    canvas = viewer.window._qt_viewer.canvas
    x, y = canvas.view.scene.transform.map([world[-1], world[-2]])[:2]
    return QPointF(float(x), float(y))


def _mouse(viewer, button, points):
    """Press at the first point, move through the others, release at the last: real Qt events on the canvas widget."""

    from qtpy.QtCore import QEvent, QPointF, Qt
    from qtpy.QtGui import QMouseEvent
    from qtpy.QtWidgets import QApplication

    native = viewer.window._qt_viewer.canvas.native
    none = Qt.KeyboardModifier.NoModifier

    def send(kind, point, which, held):
        point = QPointF(point)
        QApplication.sendEvent(native, QMouseEvent(kind, point, QPointF(native.mapToGlobal(point.toPoint())), which, held, none))

    send(QEvent.Type.MouseButtonPress, points[0], button, button)
    for point in points[1:]:
        send(QEvent.Type.MouseMove, point, Qt.MouseButton.NoButton, button)
    send(QEvent.Type.MouseButtonRelease, points[-1], button, Qt.MouseButton.NoButton)
    _events(0.1)


def _state(shell):
    viewer, edit = shell.viewer, shell._edit_panel
    image_id = shell._nav_ids[shell._nav_index]
    return {
        "objects": np.asarray(viewer.layers["Objects"].data).copy(),
        "excluded": int(shell.controller.last_results[image_id].objects["excluded"].sum()),
        "found": len(shell.controller.last_results[image_id].objects),
        "selected": set(edit._selected),
        "unrecorded": edit.unrecorded_count(),
        "shapes": {layer.name: len(layer.data) for layer in viewer.layers if layer._type_string == "shapes"},
        "examples": {item.id: dict(shell.controller.cutoff_examples(item.id, image_id)) for item in shell.controller.recipe.classifications},
    }


def _same(before, after):
    assert np.array_equal(before["objects"], after["objects"])
    assert {k: v for k, v in before.items() if k != "objects"} == {k: v for k, v in after.items() if k != "objects"}


def _right_click_and_drag(shell, data_point):
    """A plain right click changes nothing; a right drag only moves camera.center. The view is put back after."""

    from qtpy.QtCore import QPointF

    viewer = shell.viewer
    center, zoom = tuple(viewer.camera.center), viewer.camera.zoom
    before = _state(shell)
    point = _canvas_point(viewer, data_point)
    _mouse(viewer, _right(), [point, point])
    assert _wait(shell)
    _same(before, _state(shell))
    assert tuple(viewer.camera.center) == center and viewer.camera.zoom == zoom
    _mouse(viewer, _right(), [point, point + QPointF(15, 10), point + QPointF(40, 60)])
    assert _wait(shell)
    _same(before, _state(shell))
    assert tuple(viewer.camera.center) != center
    assert viewer.camera.zoom == zoom
    viewer.camera.center = center


def _right():
    from qtpy.QtCore import Qt

    return Qt.MouseButton.RightButton


def test_right_button_never_reaches_a_tool(tmp_path: Path, open_window):  # noqa: F811
    from qtpy.QtCore import Qt

    from cellquant.gui.app import RightButtonPan

    shell = _open(tmp_path, open_window, "Right button")
    shell.run_current()
    assert _wait(shell, 120)
    viewer, edit, review = shell.viewer, shell._edit_panel, shell._review_panel
    assert viewer.window._qt_viewer.canvas.native.findChild(RightButtonPan) is not None
    layer = viewer.layers["Objects"]
    on_object, empty = (15.0, 15.0), (30.0, 65.0)
    assert np.asarray(layer.data)[15, 15] > 0 and np.asarray(layer.data)[30, 65] == 0
    shell._tabs.setCurrentWidget(shell._step_pages[2])
    viewer.layers.selection.active = layer
    _right_click_and_drag(shell, on_object)  # napari's own pan_zoom: a right drag zooms there

    edit._buttons["Delete objects"].click()
    _right_click_and_drag(shell, on_object)
    edit._buttons["Select objects"].click()
    _right_click_and_drag(shell, on_object)
    edit._buttons["Delete circle"].click()
    assert edit._circle == "delete"
    _right_click_and_drag(shell, on_object)
    edit._buttons["Delete circle"].click()
    edit._buttons["Delete objects"].click()
    edit._draw_area()
    assert viewer.layers.selection.active.mode == "add_polygon"
    _right_click_and_drag(shell, on_object)
    viewer.layers.selection.active = layer
    edit._buttons["Back"].click()

    edit._buttons["Add new objects"].click()
    edit._buttons["Circle"].click()
    assert edit._circle == "add"
    _right_click_and_drag(shell, empty)
    edit._buttons["Circle"].click()
    for mode in ("paint", "erase", "fill"):
        layer.mode = mode
        _right_click_and_drag(shell, on_object)
    layer.mode = "pan_zoom"
    edit._buttons["Back"].click()

    # Step 5: Mark + / Mark − (Set cutoff by: Examples), and drawing a count area.
    shell.go_to_step(4)
    _events(0.2)
    setup = shell._marker_setup
    setup.refresh()
    box = next(box for box in setup.mode_boxes if box.property("classification_id") == "class_a")
    box.setCurrentIndex(box.findData("examples"))
    box.activated.emit(box.currentIndex())
    review.display.setCurrentIndex(review.display.findData("class_a"))
    shell._tabs.setCurrentWidget(shell._step_pages[4])
    viewer.layers.selection.active = layer
    _events(0.2)
    review.mark_positive.click()
    assert review._marking is True
    _right_click_and_drag(shell, on_object)
    review.mark_negative.click()
    assert review._marking is False
    _right_click_and_drag(shell, (45.0, 45.0))
    review.leave_marking()
    review._draw_count_area()
    for mode in ("add_polygon", "add_line", "add_path", "add_rectangle"):
        viewer.layers.selection.active.mode = mode
        _right_click_and_drag(shell, on_object)
    viewer.layers.selection.active = layer

    # The left button still reaches the tool: one left click in Delete objects removes the object.
    shell._tabs.setCurrentWidget(shell._step_pages[2])
    edit._buttons["Delete objects"].click()
    image_id = shell._nav_ids[shell._nav_index]
    point = _canvas_point(viewer, on_object)
    _mouse(viewer, Qt.MouseButton.LeftButton, [point, point])
    assert _wait(shell)
    assert int(shell.controller.last_results[image_id].objects["excluded"].sum()) == 1


def test_right_drag_in_3d_moves_without_zoom_or_turn(tmp_path: Path, open_window):  # noqa: F811
    from qtpy.QtCore import QPointF

    shell = _open(tmp_path, open_window, "Right button 3D")
    viewer = shell.viewer
    viewer.dims.ndisplay = 3
    _events(0.3)
    center, zoom, angles = tuple(viewer.camera.center), viewer.camera.zoom, tuple(viewer.camera.angles)
    start = QPointF(100, 100)
    _mouse(viewer, _right(), [start, start + QPointF(20, 10), start + QPointF(50, 70)])
    assert tuple(viewer.camera.center) != center
    assert viewer.camera.zoom == zoom and tuple(viewer.camera.angles) == angles
