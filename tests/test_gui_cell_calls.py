"""Step 5 Edit cells: select objects in the image and call them positive or negative by hand.

Needs napari and Qt; skipped without napari.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

napari = pytest.importorskip("napari")

from tests.test_gui_crop import _events, _wait, open_window  # noqa: E402,F401
from tests.test_gui_delete_area import _click  # noqa: E402
from tests.test_workflow import _recipe, _write_squares  # noqa: E402


def _shift_click(window, viewer, data, position):
    event = SimpleNamespace(
        position=position, type="mouse_press", view_direction=None, dims_displayed=(data.ndim - 2, data.ndim - 1), modifiers={"Shift"}
    )
    gen = window._on_click(viewer, event)
    next(gen, None)
    event.type = "mouse_release"
    next(gen, None)


def test_edit_cells_window(tmp_path: Path, open_window):  # noqa: F811
    from cellquant.controller import AnalysisController
    from cellquant.gui.cell_calls_window import CALLED_LAYER, SELECTED_LAYER

    image = tmp_path / "a.tif"
    _write_squares(image)
    controller = AnalysisController.create(tmp_path / "experiment", "Edit cells")
    controller.add_image_paths([image])
    controller.set_recipe(_recipe())
    controller.save()
    shell = open_window(controller.directory)
    shell.run_current()
    assert _wait(shell, 120)
    controller = shell.controller
    image_id = controller.experiment.images[0].image_id
    panel, viewer = shell._review_panel, shell.viewer
    data = np.asarray(viewer.layers["Objects"].data)
    shell.go_to_step(4)
    shell._tabs.setCurrentWidget(shell._step_pages[4])
    panel.display.setCurrentIndex(panel.display.findData("class_a"))
    _events(0.2)
    controller.set_status(image_id, "approved")

    panel.edit_cells.click()
    window = shell._edit_cells
    _events(0.2)
    assert window.isVisible() and "Marker shown" in window.marker_line.text()
    viewer.layers.selection.active = viewer.layers["Objects"]
    # Click selects one object; Shift+click adds one; background clears.
    _click(window, viewer, data, (15.0, 15.0))
    assert window.selected == {1} and SELECTED_LAYER in viewer.layers
    _shift_click(window, viewer, data, (45.0, 45.0))
    assert window.selected == {1, 2}
    _click(window, viewer, data, (1.0, 1.0))
    assert window.selected == set() and SELECTED_LAYER not in viewer.layers

    # The dim object called positive: counts and overlay follow, the cutoff does not move.
    _click(window, viewer, data, (45.0, 45.0))
    assert window.selected_table.rowCount() == len(controller.recipe.classifications)
    window.positive_button.click()
    _events(0.2)
    assert controller.cell_calls(image_id, "class_a") == {2: True}
    assert controller.recipe.classifications[0].threshold_for(image_id) == 200
    assert "2 positive of 2" in window.counts.text() and "1 called by hand" in window.counts.text()
    assert window.called_table.rowCount() == 1 and CALLED_LAYER in viewer.layers
    overlay = np.asarray(viewer.layers["Classification"].data)
    assert set(np.unique(overlay[data == 2]).tolist()) == {2}  # drawn positive
    assert controller.experiment.image(image_id).processing_status == "analyzed"  # percent changed

    # Select all…: by call. Undo takes the call back.
    window.select_all.setCurrentIndex(window.select_all.findData("by_hand"))
    window.select_all.activated.emit(window.select_all.currentIndex())
    assert window.selected == {2}
    window.undo_button.click()
    _events(0.2)
    assert controller.cell_calls(image_id, "class_a") == {}
    assert window.called_table.rowCount() == 0 and CALLED_LAYER not in viewer.layers
    window.close()
    _events(0.1)
    assert SELECTED_LAYER not in viewer.layers
