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

    from qtpy.QtCore import Qt

    from cellquant.gui import guide
    from cellquant.gui.app import CellQuantWindow
    from cellquant.quicksetup import auto_threshold

    monkeypatch.setattr(guide, "choose_practice_folder", lambda parent: tmp_path / "practice")
    answers: list = []  # what the next Reset confirmation answers; each question is recorded
    asked: list[str] = []

    def question(_parent, _title, text, *args, **kwargs):
        asked.append(text)
        return answers.pop(0)

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
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
        panel, controller = shell._review_panel, shell.controller
        marker = controller.recipe.classifications[0]
        image_id = shell._nav_ids[shell._nav_index]
        assert panel.histogram._values.size > 0 and panel.cutoff_tools.isVisibleTo(panel)
        assert panel.auto_method.currentData() == "otsu" and panel.apply_to.currentData() == "all"

        # Dragging the histogram line moves the shared cutoff.
        from qtpy.QtCore import QEvent, QPoint, QPointF
        from qtpy.QtGui import QMouseEvent
        from qtpy.QtTest import QTest
        from qtpy.QtWidgets import QApplication

        histogram = panel.histogram
        histogram.resize(400, 80)
        x = int(round(histogram._x(500.0)))
        QTest.mousePress(histogram, Qt.LeftButton, Qt.NoModifier, QPoint(x - 40, 40))
        # QTest.mouseMove carries no buttons: send a move with the left button held.
        move = QMouseEvent(QEvent.MouseMove, QPointF(x, 40), Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(histogram, move)
        QTest.mouseRelease(histogram, Qt.LeftButton, Qt.NoModifier, QPoint(x, 40))
        _settle()
        assert controller.recipe.classifications[0].threshold == pytest.approx(500.0, rel=0.02)

        # This image only: a cutoff of its own; the shared one stays.
        panel.apply_to.setCurrentIndex(panel.apply_to.findData("image"))
        panel._apply_to_chosen()
        panel.threshold_slider.setValue(300.0)
        _settle()
        assert controller.own_cutoff(image_id, marker.id) == pytest.approx(300.0, rel=0.01)
        assert controller.recipe.classifications[0].threshold == pytest.approx(500.0, rel=0.01)
        assert "> 300" in panel.counts.text()  # the rule shown is this image's own

        # Choosing a method moves this image's own cutoff to its value; the shared one stays.
        panel.auto_method.setCurrentIndex(panel.auto_method.findData("triangle"))
        panel._auto_method_chosen()
        values = controller.marker_values(marker.id, [image_id])
        assert controller.recipe.classifications[0].auto_method == "triangle"
        assert controller.own_cutoff(image_id, marker.id) == pytest.approx(auto_threshold(values, "triangle"))
        assert controller.recipe.classifications[0].threshold == pytest.approx(500.0, rel=0.02)
        shown = controller.last_results[image_id].objects
        own = controller.own_cutoff(image_id, marker.id)
        calls = shown[marker.id].astype("boolean")
        assert calls.sum() == (shown[marker.measurement] > own).sum()  # the shown result follows the new cutoff
        # The % box losing focus with nothing changed does nothing.
        before = controller.own_cutoff(image_id, marker.id)
        panel.auto_percent.editingFinished.emit()
        assert controller.own_cutoff(image_id, marker.id) == before

        # All images again: the image drops its own cutoff.
        panel.apply_to.setCurrentIndex(panel.apply_to.findData("all"))
        panel._apply_to_chosen()
        assert controller.own_cutoff(image_id, marker.id) is None

        # Reset (all images) asks first when an image is approved; No changes nothing.
        top = float(values.max()) + 1  # nothing positive: any automatic cutoff changes the calls
        controller.update_thresholds(image_id, {marker.id: top})
        controller.set_status(image_id, "approved")
        answers.append(QMessageBox.No)
        panel.reset_cutoff.click()
        assert asked and "approving again" in asked[-1]
        assert controller.recipe.classifications[0].threshold == top
        # Yes: the cutoff by the chosen method, from every measured image.
        answers.append(QMessageBox.Yes)
        panel.reset_cutoff.click()
        expected = auto_threshold(controller.marker_values(marker.id, controller.measured_images(marker.id)), "triangle")
        assert controller.recipe.classifications[0].threshold == pytest.approx(expected)
        assert panel.threshold_slider.value() == pytest.approx(expected, rel=0.01, abs=1.0)
        assert controller.cleared_approvals == [image_id]  # its positives changed
        assert controller.experiment.image(image_id).processing_status == "analyzed"
        assert "Approval cleared for 1 image" in said[-1]

        # While a run uses the settings, the cutoff tools stay locked, even when the panel refreshes.
        shell.set_busy(True)
        try:
            panel.show_result(controller.recall(image_id))
            for widget in (panel.histogram, panel.threshold_slider, panel.auto_method, panel.apply_to, panel.reset_cutoff):
                assert not widget.isEnabled()
        finally:
            shell.set_busy(False)
        assert panel.reset_cutoff.isEnabled() and panel.histogram.isEnabled()
    finally:
        viewer.close()
