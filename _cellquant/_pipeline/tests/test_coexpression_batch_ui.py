"""Stepped batch coexpression UI smoke (no napari OpenGL canvas)."""

import os
from pathlib import Path
from types import SimpleNamespace as N

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy.QtWidgets import QApplication, QTabWidget

from cellquant.classify import ClassificationRecipe
from cellquant.classify.batch import CellQuantRunRef
from cellquant.contracts import PipelineEvent
from cellquant.plugin.coexpression_batch import BatchCoexpressionPanel
from cellquant.review import REVIEW_MODE_LABEL as SEGMENTATION_REVIEW_LABEL
import cellquant.plugin.coexpression_batch as module


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


def _panel():
    app = QApplication.instance() or QApplication([])
    panel = BatchCoexpressionPanel(_FakeViewer(), _FakeController())
    panel.timer.stop()
    panel.pool.shutdown(wait=False, cancel_futures=True)
    return app, panel


def _recipe(name, channel_name, low):
    return ClassificationRecipe(
        dict(
            name=name,
            calibration_group="test",
            expected_channel_names=[channel_name],
            markers=[dict(name=name, channel=0, low=low, positive_fraction=0.5)],
        )
    )


def _loaded_panel():
    app, panel = _panel()
    a = CellQuantRunRef(Path("A.cellquant"), "A.tif", "LA", ("A",), False, "a")
    b = CellQuantRunRef(Path("B.cellquant"), "B.tif", "LB", ("B",), False, "b")
    panel.runs = (a, b)
    panel.included = {str(x.path.resolve()) for x in panel.runs}
    panel.layout_recipes = {"LA": _recipe("A", "A", 10).raw, "LB": _recipe("B", "B", 30).raw}
    panel.refresh_layout_pick()
    panel.layout_pick.setCurrentIndex(panel.layout_pick.findData("LA"))
    return app, panel, a, b


def test_batch_panel_step_tabs_hide_markers_from_select_review():
    app, panel = _panel()
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
    panel.close()
    app.processEvents()


def test_visible_threshold_draft_is_dispatched_and_survives_layout_switch():
    app, panel, _a, _b = _loaded_panel()
    panel.markers.item(0, 2).setText("20")
    assert "Unsaved changes" in panel.threshold_status.text()
    submissions = []
    panel.pool = N(
        submit=lambda fn, token: submissions.append((fn, token)) or N(done=lambda: False),
        shutdown=lambda **_k: None,
    )
    panel.output_edit.setText("audit-output-not-created")
    panel.run_batch()
    old = module.run_classify_batch
    module.run_classify_batch = lambda *a, **kw: kw
    try:
        sent = submissions[0][0](submissions[0][1])
    finally:
        module.run_classify_batch = old
    assert sent["layout_recipes"]["LA"].raw["markers"][0]["low"] == 20
    assert "events" in sent
    assert "A=20.0" in panel.summary.text()
    panel.future = None
    panel.markers.item(0, 2).setText("20")
    panel.layout_pick.setCurrentIndex(panel.layout_pick.findData("LB"))
    panel.layout_pick.setCurrentIndex(panel.layout_pick.findData("LA"))
    assert float(panel.markers.item(0, 2).text()) == 20
    panel.close()
    app.processEvents()


def test_generated_queries_follow_marker_rename_and_add():
    app, panel, _a, _b = _loaded_panel()
    panel.markers.item(0, 0).setText("Renamed")
    recipe = panel.recipe_from_table()
    assert [m["name"] for m in recipe.raw["markers"]] == ["Renamed"]
    assert recipe.raw["queries"][0]["positive"] == ["Renamed"]
    panel.add_marker()
    panel.markers.item(1, 0).setText("New")
    panel.markers.item(1, 2).setText("15")
    panel.markers.item(1, 4).setText("0.5")
    recipe = panel.recipe_from_table()
    query_names = [q["name"] for q in recipe.raw["queries"]]
    assert [m["name"] for m in recipe.raw["markers"]] == ["Renamed", "New"]
    assert any("New" in name for name in query_names)
    assert "Renamed+" in query_names
    panel.close()
    app.processEvents()


def test_selecting_image_loads_override_and_layout_channels():
    app, panel, _a, b = _loaded_panel()
    key = str(b.path.resolve())
    panel.override_pick.setCurrentIndex(panel.override_pick.findData(key))
    assert panel.layout_pick.currentData() == "LB"
    assert panel._current_layout_channels() == ["B"]
    panel.markers.item(0, 2).setText("99")
    panel.apply_image_override()
    assert panel.image_overrides[key]["expected_channel_names"] == ["B"]
    assert panel.image_overrides[key]["markers"][0]["low"] == 99
    panel.override_pick.setCurrentIndex(panel.override_pick.findData(str(Path("A.cellquant").resolve())))
    panel.override_pick.setCurrentIndex(panel.override_pick.findData(key))
    assert float(panel.markers.item(0, 2).text()) == 99
    assert "override" in panel.threshold_status.text()
    panel.clear_image_override()
    assert float(panel.markers.item(0, 2).text()) == 30
    assert "inherited" in panel.threshold_status.text()
    panel.close()
    app.processEvents()


def test_batch_progress_events_update_before_finish():
    app, panel, _a, _b = _loaded_panel()
    panel._enqueue_event(
        PipelineEvent(
            "progress",
            "a",
            "A.cellquant",
            "batch",
            "2026-09-10T00:00:00Z",
            current=1,
            total=2,
            details={"status": "running", "message": "Classifying A.cellquant"},
        )
    )
    panel._batch_started = 0
    panel.poll()
    assert "A.cellquant" in panel.progress.text()
    assert "1/2" in panel.progress.text()
    panel._progress["cancel_requested"] = True
    panel._refresh_progress_label(running=True)
    assert "Cancellation requested" in panel.progress.text()
    panel.close()
    app.processEvents()


def test_review_step_summarizes_state_and_has_no_edit_controls():
    app, panel = _panel()
    a1 = CellQuantRunRef(Path("A1.cellquant"), "A1.tif", "LA", ("A",), False, "a1")
    a2 = CellQuantRunRef(Path("A2.cellquant"), "A2.tif", "LA", ("A",), True, "a2")
    panel.runs = (a1, a2)
    panel.included = {str(x.path.resolve()) for x in panel.runs}
    panel.refresh_review_pick()
    # The wizard reports state only; mask editing moved to the dedicated mode.
    assert panel.review_summary.rowCount() == 2
    assert panel.review_summary.item(0, 1).text() == "pending"
    assert "0 of 2" in panel.review_status.text()
    for removed in ("open_for_review", "save_curated_labels", "build_guided_review_queue"):
        assert not hasattr(panel, removed)
    panel.close()
    app.processEvents()


def test_open_segmentation_review_preserves_selection_and_switches_mode():
    app, panel = _panel()
    panel.root_edit.setText("some-batch-root")
    review = N(
        scope_pick=N(findData=lambda key: 1, setCurrentIndex=lambda index: None),
        input_edit=N(setText=lambda text: opened.append(text)),
    )
    opened: list[str] = []
    activated: list[bool] = []
    panel.controller.segmentation_review_panel = review
    panel.controller.activate_segmentation_review = lambda: activated.append(True)
    panel.open_segmentation_review()
    assert opened == ["some-batch-root"]
    assert activated == [True]
    assert SEGMENTATION_REVIEW_LABEL in panel.status.text()
    panel.close()
    app.processEvents()


def test_handoff_pins_revisions_and_sets_policy():
    app, panel, a, b = _loaded_panel()
    pin = N(run_dir=Path("A.cellquant"))
    panel.accept_review_handoff([pin], policy="approved_only")
    assert panel.input_policy() == "approved_only"
    assert panel.pinned_masks == (pin,)
    assert panel.included == {str(Path("A.cellquant").resolve())}
    panel.close()
    app.processEvents()
