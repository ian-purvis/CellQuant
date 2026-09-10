"""Stepped batch coexpression UI smoke (no napari OpenGL canvas)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy.QtWidgets import QApplication, QTabWidget

from cellquant.plugin.coexpression_batch import BatchCoexpressionPanel


class _FakeLayers:
    def __iter__(self):
        return iter(())


class _FakeViewer:
    layers = _FakeLayers()

    class window:
        @staticmethod
        def add_dock_widget(*_a, **_k):
            return None


class _FakeController:
    config = None
    image_volume = None

    def open_path(self, *_a, **_k):
        return None

    def _publish_labels(self, *_a, **_k):
        return None


def test_batch_panel_step_tabs_hide_markers_from_select_review():
    app = QApplication.instance() or QApplication([])
    panel = BatchCoexpressionPanel(_FakeViewer(), _FakeController())
    assert isinstance(panel.steps, QTabWidget)
    titles = [panel.steps.tabText(i).lower() for i in range(panel.steps.count())]
    assert any("select" in t for t in titles)
    assert any("review" in t for t in titles)
    assert any("threshold" in t for t in titles)
    assert any("run" in t for t in titles)
    select = panel.steps.widget(0)
    review = panel.steps.widget(1)
    assert panel.markers.parent() is not select
    assert panel.markers.parent() is not review
    assert "Discover" in panel.status.text() or "discover" in panel.status.text().lower() or panel.root_edit is not None
    panel.timer.stop()
    panel.pool.shutdown(wait=False, cancel_futures=True)
    panel.close()
    app.processEvents()
