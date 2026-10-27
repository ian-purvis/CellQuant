"""Circle tool in step 3: click or drag to add a round object that is recorded at once.

Needs napari and Qt; skipped without napari.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

napari = pytest.importorskip("napari")

from tests.test_gui_crop import _events, _wait, open_window  # noqa: E402,F401
from tests.test_workflow import _recipe, _write_squares  # noqa: E402


class _Press:
    """A stand-in for napari's mouse event: a press, optional moves, then a release."""

    def __init__(self, position):
        self.position = tuple(position)
        self.type = "mouse_press"
        self.view_direction = None
        self.dims_displayed = (0, 1)


def _circle_click(shell, position, drag_to=None, wait=True):
    event = _Press(position)
    gen = shell._edit_panel._on_circle(shell.viewer, event)
    next(gen, None)
    if drag_to is not None:
        event.type, event.position = "mouse_move", tuple(drag_to)
        next(gen, None)
    event.type = "mouse_release"
    try:
        next(gen)
    except StopIteration:
        pass
    if wait:
        _events(0.2)
        assert _wait(shell)
        _events(0.2)


def test_circle_tool_adds_recorded_objects(tmp_path: Path, open_window):  # noqa: F811
    from cellquant.controller import AnalysisController

    image = tmp_path / "a.tif"
    _write_squares(image)
    controller = AnalysisController.create(tmp_path / "experiment", "Circles")
    controller.add_image_paths([image])
    controller.set_recipe(_recipe())
    controller.save()
    shell = open_window(controller.directory)
    shell.run_current()
    assert _wait(shell, 120)
    image_id = shell.controller.experiment.images[0].image_id
    panel = shell._edit_panel
    shell._tabs.setCurrentWidget(shell._step_pages[2])
    layer = shell.viewer.layers["Objects"]
    found = shell.controller.last_results[image_id]
    top = int(found.automated_labels.max())
    objects = len(found.objects)
    free = np.asarray(layer.data) == 0
    spots = [(y, x) for y in range(12, free.shape[0] - 12, 24) for x in range(12, free.shape[1] - 12, 24) if free[y - 10 : y + 11, x - 10 : x + 11].all()]
    assert len(spots) >= 4

    # Off: a click does nothing.
    _circle_click(shell, spots[0])
    assert len(shell.controller.last_results[image_id].objects) == objects

    panel._buttons["Add new objects"].click()
    panel._buttons["Circle"].click()
    assert panel._circle == "add" and layer.mode == "pan_zoom" and shell.viewer.layers.selection.active is layer
    assert shell.viewer.cursor.style == "circle"
    panel.circle_size.setValue(9)

    # Another layer active: a click does nothing.
    other = shell.viewer.layers[0] if shell.viewer.layers[0] is not layer else shell.viewer.layers[1]
    shell.viewer.layers.selection.active = other
    _circle_click(shell, spots[0])
    assert len(shell.controller.last_results[image_id].objects) == objects
    shell.viewer.layers.selection.active = layer

    _circle_click(shell, spots[0])
    result = shell.controller.last_results[image_id]
    assert len(result.objects) == objects + 1
    area = int((np.asarray(result.labels) == top + 1).sum())
    assert 55 <= area <= 75  # diameter 9
    assert layer.selected_label == top + 2

    # A drag sets the radius.
    y, x = spots[1]
    _circle_click(shell, (y, x), drag_to=(y, x + 6))
    result = shell.controller.last_results[image_id]
    assert len(result.objects) == objects + 2
    area = int((np.asarray(result.labels) == top + 2).sum())
    assert 100 <= area <= 125  # radius 6

    # Existing objects are never overwritten.
    before = np.array(result.labels, copy=True)
    from scipy.ndimage import binary_erosion

    inside = np.argwhere(binary_erosion(before > 0, iterations=6))[0]  # deep inside an object, circle of 9
    _circle_click(shell, tuple(inside))
    assert "Nothing to add" in panel.record_status.text()
    assert len(shell.controller.last_results[image_id].objects) == objects + 2
    assert np.array_equal(np.asarray(shell.controller.last_results[image_id].labels), before)

    # One Undo removes the last circle.
    panel._undo()
    assert _wait(shell)
    _events(0.2)
    assert len(shell.controller.last_results[image_id].objects) == objects + 1
    assert (np.asarray(shell.controller.last_results[image_id].labels) == top + 2).sum() == 0

    # Draw new object and leaving add mode switch Circle off.
    panel._buttons["Draw new object"].click()
    assert panel._circle is None and not panel._buttons["Circle"].isChecked()
    panel._buttons["Circle"].click()
    panel._buttons["Back"].click()
    assert panel._circle is None

    # Circles placed while the last one is still being recorded are all recorded.
    panel._buttons["Add new objects"].click()
    panel._buttons["Circle"].click()
    count = len(shell.controller.last_results[image_id].objects)
    _circle_click(shell, spots[2], wait=False)
    _circle_click(shell, spots[3], wait=False)
    for _ in range(3):
        _events(0.3)
        assert _wait(shell)
    _events(0.3)
    assert len(shell.controller.last_results[image_id].objects) == count + 2

    # Moving to another step puts the circle down.
    shell._tabs.setCurrentWidget(shell._step_pages[3])
    assert panel._circle is None and not panel._buttons["Circle"].isChecked()


def test_delete_circle_removes_objects_inside_and_undo_restores(tmp_path: Path, open_window):  # noqa: F811
    from cellquant.controller import AnalysisController

    image = tmp_path / "a.tif"
    _write_squares(image)
    controller = AnalysisController.create(tmp_path / "experiment", "Delete circles")
    controller.add_image_paths([image])
    controller.set_recipe(_recipe())
    controller.save()
    shell = open_window(controller.directory)
    shell.run_current()
    assert _wait(shell, 120)
    image_id = shell.controller.experiment.images[0].image_id
    panel = shell._edit_panel
    shell._tabs.setCurrentWidget(shell._step_pages[2])
    layer = shell.viewer.layers["Objects"]

    def excluded() -> int:
        return int(shell.controller.last_results[image_id].objects["excluded"].sum())

    panel._buttons["Delete objects"].click()
    assert "Delete circle" in {name for name, button in panel._buttons.items() if not button.isHidden()}
    panel._buttons["Delete circle"].click()
    assert panel._circle == "delete" and not panel._buttons["Circle"].isChecked()
    assert layer.mode == "pan_zoom" and shell.viewer.layers.selection.active is layer

    # An object crossing the circle's edge stays.
    _circle_click(shell, (15, 15), drag_to=(15, 21))
    assert excluded() == 0

    # A click makes a circle of Size; the object inside goes, the other stays.
    panel.circle_size.setValue(30)
    _circle_click(shell, (15, 15))
    assert excluded() == 1
    assert int(np.asarray(layer.data)[15, 15]) == 0 and int(np.asarray(layer.data)[45, 45]) > 0

    # One Undo brings it back.
    panel._undo()
    assert _wait(shell)
    _events(0.2)
    assert excluded() == 0

    # Esc puts the circle down; Back and the other circle switch it off too.
    panel.leave_delete()
    assert panel._circle is None and panel._mode == "delete"
    panel._buttons["Delete circle"].click()
    panel._buttons["Back"].click()
    assert panel._circle is None
