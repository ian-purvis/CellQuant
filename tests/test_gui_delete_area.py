"""Delete in area in step 3: draw an area, delete the objects inside, Undo brings them back.

Needs napari and Qt; skipped without napari.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

napari = pytest.importorskip("napari")

from tests.test_gui_crop import _events, _wait, open_window  # noqa: E402,F401
from tests.test_workflow import _recipe, _write_squares  # noqa: E402


def test_delete_in_area_buttons(tmp_path: Path, open_window):  # noqa: F811
    from cellquant.controller import AnalysisController
    from cellquant.gui.app import DELETE_AREA_LAYER

    image = tmp_path / "a.tif"
    _write_squares(image)
    controller = AnalysisController.create(tmp_path / "experiment", "Delete area")
    controller.add_image_paths([image])
    controller.set_recipe(_recipe())
    controller.save()
    shell = open_window(controller.directory)
    shell.run_current()
    assert _wait(shell, 120)
    image_id = shell.controller.experiment.images[0].image_id
    panel = shell._edit_panel

    panel._delete_in_area()  # nothing drawn yet: nothing deleted
    assert _wait(shell)
    assert int(shell.controller.last_results[image_id].objects["excluded"].sum()) == 0

    panel._draw_area()
    layer = shell.viewer.layers[DELETE_AREA_LAYER]
    assert layer.mode == "add_polygon"
    layer.add(np.array([[0.0, 0.0], [0.0, 30.0], [30.0, 30.0], [30.0, 0.0]]), shape_type="polygon")
    panel._delete_in_area()
    assert _wait(shell)
    _events(0.2)
    assert int(shell.controller.last_results[image_id].objects["excluded"].sum()) == 1
    assert DELETE_AREA_LAYER not in shell.viewer.layers
    assert int(np.asarray(shell.viewer.layers["Objects"].data)[15, 15]) == 0

    panel._undo()
    assert _wait(shell)
    assert int(shell.controller.last_results[image_id].objects["excluded"].sum()) == 0


def test_deleted_object_leaves_the_outline_layer_and_drawing_is_kept(tmp_path: Path, open_window):  # noqa: F811
    from cellquant.controller import AnalysisController

    first, second = tmp_path / "a.tif", tmp_path / "b.tif"
    _write_squares(first)
    _write_squares(second)
    controller = AnalysisController.create(tmp_path / "experiment", "Delete object")
    controller.add_image_paths([first, second])
    controller.set_recipe(_recipe())
    controller.save()
    shell = open_window(controller.directory)
    shell.run_current()
    assert _wait(shell, 120)
    panel = shell._edit_panel

    # Delete object: the outline goes from the Objects layer too, and is not kept as a drawing.
    panel.delete_object(int(np.asarray(shell.viewer.layers["Objects"].data)[15, 15]))
    assert _wait(shell)
    _events(0.2)
    assert int(np.asarray(shell.viewer.layers["Objects"].data)[15, 15]) == 0
    assert not panel._pending

    # Drawing not recorded yet still comes back after showing another image.
    layer = shell.viewer.layers["Objects"]
    painted = np.array(layer.data, copy=True)
    painted[60:65, 60:65] = 7
    layer.data = painted
    shell._nav_index = 1
    shell.show_current()
    assert _wait(shell)
    _events(0.3)
    assert len(panel._pending) == 1
    shell._nav_index = 0
    shell.show_current()
    assert _wait(shell)
    _events(0.3)
    assert int(np.asarray(shell.viewer.layers["Objects"].data)[62, 62]) == 7


def test_step3_modes_and_draw_new_object(tmp_path: Path, open_window):  # noqa: F811
    from cellquant.controller import AnalysisController

    image = tmp_path / "a.tif"
    _write_squares(image)
    controller = AnalysisController.create(tmp_path / "experiment", "Modes")
    controller.add_image_paths([image])
    controller.set_recipe(_recipe())
    controller.save()
    shell = open_window(controller.directory)
    shell.run_current()
    assert _wait(shell, 120)
    image_id = shell.controller.experiment.images[0].image_id
    panel = shell._edit_panel

    def shown():
        return {name for name, button in panel._buttons.items() if not button.isHidden()}

    assert shown() == {"Delete objects", "Add new objects", "Undo", "Undo all", "Record drawn edits", "Reset all images"}
    panel._buttons["Delete objects"].click()
    assert shown() == {"Delete objects", "Draw area", "Delete in area", "Keep only", "Undo", "Undo all", "Back"}
    panel._buttons["Back"].click()
    panel._buttons["Add new objects"].click()
    assert shown() == {"Draw new object", "Record drawn edits", "Record all images", "Undo", "Undo all", "Back"}

    top = int(shell.controller.last_results[image_id].automated_labels.max())
    panel._buttons["Draw new object"].click()
    layer = shell.viewer.layers["Objects"]
    assert shell.viewer.layers.selection.active is layer and layer.mode == "paint"
    assert layer.selected_label == top + 1
    data = np.asarray(layer.data).copy()
    free = np.argwhere(data == 0)
    y, x = free[len(free) // 2]
    data[y : y + 3, x : x + 3] = top + 1
    layer.data = data
    panel._buttons["Record drawn edits"].click()
    assert _wait(shell)
    assert (np.asarray(shell.controller.last_results[image_id].labels) == top + 1).sum() == 9

    panel.show_all.setChecked(True)
    assert "Back" not in shown() and len(shown()) == len(panel._buttons) - 1


def _ring(shape, top: int, left: int, size: int = 8):
    """Pixel indices of a closed square outline, one pixel wide."""

    mask = np.zeros(shape, dtype=bool)
    mask[top : top + size, left : left + size] = True
    mask[top + 1 : top + size - 1, left + 1 : left + size - 1] = False
    return np.nonzero(mask)


def test_each_closed_outline_is_recorded_as_a_new_object(tmp_path: Path, open_window):  # noqa: F811
    from cellquant.controller import AnalysisController

    image = tmp_path / "a.tif"
    _write_squares(image)
    controller = AnalysisController.create(tmp_path / "experiment", "Outlines")
    controller.add_image_paths([image])
    controller.set_recipe(_recipe())
    controller.save()
    shell = open_window(controller.directory)
    shell.run_current()
    assert _wait(shell, 120)
    image_id = shell.controller.experiment.images[0].image_id
    panel = shell._edit_panel
    found = shell.controller.last_results[image_id]
    top = int(found.automated_labels.max())
    objects = len(found.objects)

    panel._buttons["Add new objects"].click()
    panel._buttons["Draw new object"].click()
    layer = shell.viewer.layers["Objects"]
    assert layer.selected_label == top + 1
    free = np.asarray(layer.data) == 0
    corners = [(y, x) for y in range(0, free.shape[0] - 8, 9) for x in range(0, free.shape[1] - 8, 9) if free[y : y + 8, x : x + 8].all()]
    assert len(corners) >= 2

    # An open line is not an object yet: the number stays.
    layer.data_setitem((np.full(4, corners[0][0]), np.arange(corners[0][1], corners[0][1] + 4)), top + 1)
    _events(0.2)
    assert layer.selected_label == top + 1 and not shell.is_busy()

    for number, (y, x) in enumerate(corners[:2], start=top + 1):
        assert layer.selected_label == number
        layer.data_setitem(_ring(free.shape, y, x), number)
        _events(0.2)
        assert _wait(shell)
        _events(0.2)
        result = shell.controller.last_results[image_id]
        assert (np.asarray(result.labels) == number).sum() == 64  # outline and filled inside
        assert layer.selected_label == number + 1

    result = shell.controller.last_results[image_id]
    assert len(result.objects) == objects + 2
    assert {top + 1, top + 2} <= set(np.unique(result.labels).tolist())


def test_keep_only_deletes_what_does_not_touch_the_area(tmp_path: Path, open_window):  # noqa: F811
    from cellquant.controller import AnalysisController
    from cellquant.gui.app import DELETE_AREA_LAYER

    image = tmp_path / "a.tif"
    _write_squares(image)
    controller = AnalysisController.create(tmp_path / "experiment", "Keep only")
    controller.add_image_paths([image])
    controller.set_recipe(_recipe())
    controller.save()
    shell = open_window(controller.directory)
    shell.run_current()
    assert _wait(shell, 120)
    image_id = shell.controller.experiment.images[0].image_id
    panel = shell._edit_panel
    start = int((~shell.controller.last_results[image_id].objects["excluded"].astype(bool)).sum())
    assert start >= 2

    panel._draw_area()
    layer = shell.viewer.layers[DELETE_AREA_LAYER]
    layer.add(np.array([[0.0, 0.0], [0.0, 20.0], [20.0, 20.0], [20.0, 0.0]]), shape_type="polygon")  # touches the object at (15, 15)
    panel._keep_only()
    assert _wait(shell)
    _events(0.2)
    objects = shell.controller.last_results[image_id].objects
    assert int((~objects["excluded"].astype(bool)).sum()) == 1
    assert int(np.asarray(shell.viewer.layers["Objects"].data)[15, 15]) != 0

    panel._undo()  # one Undo for the whole keep-only
    assert _wait(shell)
    assert int((~shell.controller.last_results[image_id].objects["excluded"].astype(bool)).sum()) == start


def _click(panel, viewer, data, position):
    from types import SimpleNamespace

    event = SimpleNamespace(position=position, type="mouse_press", view_direction=None, dims_displayed=(data.ndim - 2, data.ndim - 1))
    gen = panel._on_click(viewer, event)
    next(gen, None)
    event.type = "mouse_release"
    next(gen, None)


def test_click_delete_mode_rules(tmp_path: Path, open_window):  # noqa: F811
    from cellquant.controller import AnalysisController

    image = tmp_path / "a.tif"
    _write_squares(image)
    controller = AnalysisController.create(tmp_path / "experiment", "Click delete")
    controller.add_image_paths([image])
    controller.set_recipe(_recipe())
    controller.save()
    shell = open_window(controller.directory)
    shell.run_current()
    assert _wait(shell, 120)
    shell._tabs.setCurrentWidget(shell._step_pages[2])
    panel, viewer = shell._edit_panel, shell.viewer
    image_id = shell.controller.experiment.images[0].image_id
    data = np.asarray(viewer.layers["Objects"].data)

    def count():
        return int(shell.controller.last_results[image_id].objects["excluded"].sum())

    button = panel._buttons["Delete objects"]
    button.click()
    assert button.isChecked() and viewer.cursor.style == "cross"
    _click(panel, viewer, data, (1.0, 1.0))  # background: nothing
    assert count() == 0
    # Drawing an area: a click over an object is a polygon corner, not a delete.
    panel._draw_area()
    _click(panel, viewer, data, (15.0, 15.0))
    assert count() == 0
    viewer.layers.selection.active = viewer.layers["Objects"]
    _click(panel, viewer, data, (15.0, 15.0))
    assert _wait(shell)
    assert count() == 1
    button.click()  # pressing again leaves the mode
    assert panel._mode == "home" and not button.isChecked() and viewer.cursor.style != "cross"
    button.click()
    panel._buttons["Back"].click()
    assert viewer.cursor.style != "cross"
    button.click()
    panel.leave_delete()  # Esc
    assert panel._mode == "home" and viewer.cursor.style != "cross"
    _click(panel, viewer, data, (15.0, 15.0))
    assert count() == 1


def test_keep_only_in_3d(tmp_path: Path):
    from cellquant.edits import objects_outside

    labels = np.zeros((4, 40, 40), dtype=np.int32)
    labels[0:2, 5:10, 5:10] = 1  # inside the area on two slices
    labels[3, 30:35, 30:35] = 2  # outside on every slice
    labels[1, 18:24, 18:24] = 3  # crosses the edge
    area = [np.array([[0.0, 0.0], [0.0, 20.0], [20.0, 20.0], [20.0, 0.0]])]
    assert objects_outside(labels, area) == [2]
