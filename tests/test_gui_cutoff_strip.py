"""Step 5 image strip: percent positive per image, all or ticked images, and the automatic cutoff
from the ticked images. Practice images; needs napari, Qt and OpenGL (xvfb-run). Skipped without napari.
"""

from __future__ import annotations

from pathlib import Path

import pytest

napari = pytest.importorskip("napari")

from tests.test_gui_cutoff_tools import _settle  # noqa: E402
from tests.test_gui_guided_flow import _wait  # noqa: E402


def test_image_strip(tmp_path: Path, monkeypatch):
    from qtpy.QtCore import QSettings, Qt
    from qtpy.QtWidgets import QMessageBox

    from cellquant.gui import app, guide
    from cellquant.gui.app import CellQuantWindow
    from cellquant.quicksetup import auto_threshold

    settings_file = tmp_path / "settings.ini"
    monkeypatch.setattr(app, "_settings", lambda: QSettings(str(settings_file), QSettings.IniFormat))
    monkeypatch.setattr(guide, "choose_practice_folder", lambda parent: tmp_path / "practice")
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"napari viewer could not start: {exc}")
    shell = CellQuantWindow(viewer, None)
    said: list[str] = []
    monkeypatch.setattr(shell, "message", said.append)
    try:
        shell.start_practice()
        _wait(shell)
        shell.run_current()
        _wait(shell)
        shell._marker_setup.button.click()
        _wait(shell)
        shell.start_batch(None)
        _wait(shell, 120)
        panel, controller = shell._review_panel, shell.controller
        marker = controller.recipe.classifications[0]
        included = [record.image_id for record in controller.experiment.images if record.include]
        assert len(included) >= 2
        table = panel.strip_table
        assert panel.strip.isVisibleTo(panel) and table.rowCount() == len(included)
        counts = controller.positive_counts(marker.id, included)
        assert set(counts) == set(included)
        for row, image_id in enumerate(included):
            positive, counted = counts[image_id]
            assert table.item(row, 1).text() == f"{positive} / {counted}"

        # A cutoff move updates every row.
        top = max(float(controller.marker_values(marker.id, [image_id]).max()) for image_id in included) + 1
        panel._threshold_moved(top)  # as typed: past this image's slider range
        _settle()
        assert all(table.item(row, 1).text().startswith("0 /") for row in range(table.rowCount()))
        assert panel.strip_total.text().startswith("All images: 0 /")

        # Selected images: start from the shown image, tick one more; the total counts only those.
        panel.strip_mode.setCurrentIndex(panel.strip_mode.findData("selected"))
        panel._strip_mode_chosen()
        shown = panel._shown_image_id()
        assert panel.strip_pool() == [shown]
        other_row = next(row for row, image_id in enumerate(included) if image_id != shown)
        table.item(other_row, 0).setCheckState(Qt.Checked)
        assert set(panel.strip_pool()) == {shown, included[other_row]}
        assert panel.strip_total.text().startswith("Ticked images:")

        # Reset (All images) finds the cutoff from the ticked images only.
        panel.reset_cutoff.click()
        pool = panel.strip_pool()
        expected = auto_threshold(controller.marker_values(marker.id, pool), marker.auto_method)
        assert controller.recipe.classifications[0].threshold == pytest.approx(expected)
        assert "from 2 images" in said[-1]

        # The choice is remembered for the experiment.
        panel._strip_experiment = ""
        panel._strip_ticks = None
        panel._load_strip_choice()
        assert panel._strip_selected() and set(panel.strip_pool()) == set(pool)
    finally:
        viewer.close()
