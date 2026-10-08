"""Step 5: Approve opens the next unchecked image and updates the status line."""

from __future__ import annotations

import pytest

napari = pytest.importorskip("napari")


def test_approve_opens_next_unchecked_image(tmp_path):
    from cellquant.gui.app import CellQuantWindow
    from tests.test_gui_plan import _wait
    from tests.test_plan import _experiment

    controller = _experiment(tmp_path)
    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"napari viewer could not start: {exc}")
    shell = CellQuantWindow(viewer, controller.directory)
    try:
        _wait(shell)
        panel = shell._review_panel
        controller = shell.controller
        ids = list(shell._nav_ids)
        assert len(ids) == 3
        assert not hasattr(panel, "show_boundaries") and not hasattr(panel, "show_fills")
        shell._nav_index = 0
        panel._set_status("approved")
        _wait(shell)
        assert shell._nav_index == 1
        assert controller.experiment.image(ids[0]).processing_status == "approved"
        assert "1 of 3 approved" in panel.status_line.text()
        panel._set_status("approved")
        _wait(shell)
        assert shell._nav_index == 2
        panel._set_status("approved")
        _wait(shell)
        assert shell._nav_index == 2
        assert panel.status_line.text().startswith("Approved") and "3 of 3 approved" in panel.status_line.text()
    finally:
        viewer.close()
