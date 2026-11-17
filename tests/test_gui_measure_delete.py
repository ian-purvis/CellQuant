"""The Measure window deletes shapes itself: select rows in its table, click Delete.

Needs napari and Qt; skipped without napari.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

napari = pytest.importorskip("napari")

from tests.gui_support import _texture_sizes  # noqa: E402,F401
from tests.test_gui_crop import _events, _wait, open_window  # noqa: E402,F401


def _square(x: float) -> np.ndarray:
    return np.array([[0, x], [0, x + 10], [10, x + 10], [10, x]], dtype=float)


def _open(tmp_path: Path, open_window):  # noqa: F811
    from cellquant.practice import create_practice_experiment

    controller = create_practice_experiment(tmp_path / "practice")
    controller.save()
    shell = open_window(controller.directory)
    dock = shell._measure_dock
    layer = dock.ensure_layer()
    layer.add([_square(0), _square(20), _square(40)], shape_type="rectangle")
    _events(0.2)
    return shell, dock, layer


def _select(dock, rows: list[int]) -> None:
    from qtpy.QtCore import QItemSelectionModel

    dock.table.clearSelection()
    model = dock.table.selectionModel()
    for row in rows:
        model.select(dock.table.model().index(row, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
    _events(0.05)


def test_delete_removes_selected_shapes_and_rows_together(tmp_path: Path, open_window):  # noqa: F811
    shell, dock, layer = _open(tmp_path, open_window)
    assert dock.table.rowCount() == 3 and len(layer.data) == 3
    assert not dock.delete_button.isEnabled() and "Select" in dock.delete_button.toolTip()  # nothing selected: off, and why

    _select(dock, [0, 2])
    assert dock.delete_button.isEnabled()
    kept = np.array(layer.data[1])
    dock.delete_button.click()
    _events(0.2)
    assert len(layer.data) == 1 and np.array_equal(layer.data[0], kept)  # the one that was not selected stays
    assert dock.table.rowCount() == 1
    assert not dock.delete_button.isEnabled()
    assert "Deleted 2 shapes" in shell._footer.status.text()


def test_delete_key_in_the_table_and_shapes_of_other_images(tmp_path: Path, open_window):  # noqa: F811
    from qtpy.QtCore import Qt
    from qtpy.QtTest import QTest

    shell, dock, layer = _open(tmp_path, open_window)
    other = next(record.image_id for record in shell.controller.experiment.images if record.image_id != dock._layer_image)
    dock._drawn[other] = [(_square(0), "rectangle"), (_square(30), "rectangle")]
    dock.refresh()
    assert dock.table.rowCount() == 5
    rows = [row for row, (image, _place) in enumerate(dock._row_shapes) if image == other]
    _select(dock, rows[:1])
    dock.table.setFocus()
    QTest.keyClick(dock.table, Qt.Key_Delete)
    _events(0.2)
    assert len(dock._drawn[other]) == 1 and dock.table.rowCount() == 4
    assert len(layer.data) == 3  # the image on screen is untouched

    _select(dock, [row for row, (image, _place) in enumerate(dock._row_shapes) if image == other])
    dock.delete_button.click()
    assert other not in dock._drawn and dock.table.rowCount() == 3


def test_measure_hint_no_longer_sends_deleting_to_napari(tmp_path: Path, open_window):  # noqa: F811
    shell, dock, _layer = _open(tmp_path, open_window)
    tips = " ".join(widget.toolTip() for widget in dock.findChildren(object) if hasattr(widget, "toolTip"))
    assert "delete a shape" not in tips and "Delete" in tips
