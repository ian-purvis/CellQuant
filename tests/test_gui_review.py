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
        shell._nav_index = 0
        panel._set_status("approved")
        assert controller.experiment.image(ids[0]).processing_status != "approved", "an image not run yet cannot be approved"
        for image_id in ids:
            controller.experiment.image(image_id).processing_status = "analyzed"
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


def test_images_run_before_the_markers_get_them_measured_when_shown(tmp_path):
    """Step 4 measures the markers in the image shown only. The next image Approve opens shows its
    positive/negative overlay too: its saved objects are measured (never segmented again)."""

    from cellquant.gui.app import CellQuantWindow
    from tests.test_gui_plan import _wait
    from tests.test_plan import _experiment

    controller = _experiment(tmp_path)
    markers = controller.recipe.model_dump(mode="json")
    controller.set_recipe({**markers, "measurements": [], "classifications": [], "reports": []})
    controller.run_images()
    controller.set_recipe(markers)
    controller.save()
    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"napari viewer could not start: {exc}")
    shell = CellQuantWindow(viewer, controller.directory)
    try:
        _wait(shell)
        panel, controller = shell._review_panel, shell.controller
        marker = controller.recipe.classifications[0]
        shell.go_to_step(4)
        panel.display.setCurrentIndex(panel.display.findData(marker.id))
        shell._nav_index = 0
        shell.show_current()
        _wait(shell)
        assert "Classification" in viewer.layers and "Positive:" in panel.counts.text()
        panel._set_status("approved")
        _wait(shell)
        _wait(shell)
        next_id = shell._nav_ids[shell._nav_index]
        assert shell._nav_index == 1 and shell._shown_image_id == next_id
        assert marker.measurement in controller.last_results[next_id].objects.columns
        assert "Classification" in viewer.layers and "Positive:" in panel.counts.text()
        assert controller.experiment.image(next_id).processing_status == "analyzed"
    finally:
        viewer.close()
