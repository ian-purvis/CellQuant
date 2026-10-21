"""Step 5 cutoff tools: histogram, Auto method, Apply to and Reset cutoff, with the practice images.

Needs napari, Qt and OpenGL (a desktop, or xvfb-run with Mesa). Skipped when napari is missing.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

napari = pytest.importorskip("napari")

from tests.test_gui_guided_flow import _wait  # noqa: E402


def _settle(seconds: float = 0.2) -> None:
    from qtpy.QtWidgets import QApplication

    end = time.time() + seconds
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.01)


def test_cutoff_tools(tmp_path: Path, monkeypatch):
    from qtpy.QtWidgets import QMessageBox

    from cellquant.gui import guide
    from cellquant.gui.app import CellQuantWindow
    from cellquant.quicksetup import auto_threshold

    monkeypatch.setattr(guide, "choose_practice_folder", lambda parent: tmp_path / "practice")
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"napari viewer could not start: {exc}")
    shell = CellQuantWindow(viewer, None)
    try:
        shell.start_practice()
        _wait(shell)
        shell.run_current()
        _wait(shell)
        shell._marker_setup.button.click()
        _wait(shell)
        panel, controller = shell._review_panel, shell.controller
        marker = controller.recipe.classifications[0]
        image_id = shell._nav_ids[shell._nav_index]
        assert panel.histogram._values.size > 0 and panel.cutoff_tools.isVisibleTo(panel)
        assert panel.auto_method.currentData() == "otsu" and panel.apply_to.currentData() == "all"

        # Dragging the histogram line moves the shared cutoff.
        panel.histogram.cutoff_dragged.emit(500.0)
        _settle()
        assert controller.recipe.classifications[0].threshold == pytest.approx(500.0, rel=0.01)

        # This image only: a cutoff of its own; the shared one stays.
        panel.apply_to.setCurrentIndex(panel.apply_to.findData("image"))
        panel._apply_to_chosen()
        panel.threshold_slider.setValue(300.0)
        _settle()
        assert controller.own_cutoff(image_id, marker.id) == pytest.approx(300.0, rel=0.01)
        assert controller.recipe.classifications[0].threshold == pytest.approx(500.0, rel=0.01)
        assert "> 300" in panel.counts.text()  # the rule shown is this image's own

        # Choosing a method uses it at once, here for this image's own objects.
        panel.auto_method.setCurrentIndex(panel.auto_method.findData("triangle"))
        panel._auto_method_chosen()
        values = controller.marker_values(marker.id, [image_id])
        assert controller.recipe.classifications[0].auto_method == "triangle"
        assert controller.own_cutoff(image_id, marker.id) == pytest.approx(auto_threshold(values, "triangle"))

        # All images again: the image drops its own cutoff.
        panel.apply_to.setCurrentIndex(panel.apply_to.findData("all"))
        panel._apply_to_chosen()
        assert controller.own_cutoff(image_id, marker.id) is None

        # Reset (all images) finds the cutoff by the chosen method from every included image.
        panel.reset_cutoff.click()
        included = [r.image_id for r in controller.experiment.images if r.include and controller.has_result(r.image_id)]
        expected = auto_threshold(controller.marker_values(marker.id, included), "triangle")
        assert controller.recipe.classifications[0].threshold == pytest.approx(expected)
        assert panel.threshold_slider.value() == pytest.approx(expected, rel=0.01, abs=1.0)
    finally:
        viewer.close()
