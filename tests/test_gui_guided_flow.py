"""The guided steps in the live napari window, with the practice images.

Needs napari, Qt and OpenGL (a desktop, or xvfb-run with Mesa). Skipped when napari is missing.
"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import pytest

napari = pytest.importorskip("napari")


def _wait(shell, seconds: float = 60) -> None:
    from qtpy.QtWidgets import QApplication

    end = time.time() + seconds
    while (shell._job is not None or shell._batch is not None or shell._loader is not None) and time.time() < end:
        QApplication.processEvents()
        time.sleep(0.02)
    for _ in range(5):
        QApplication.processEvents()


def test_practice_run_through_all_steps(tmp_path: Path, monkeypatch):
    from qtpy.QtWidgets import QFileDialog

    from cellquant.gui import guide
    from cellquant.gui.app import CellQuantWindow
    from cellquant.practice import expected_answers

    monkeypatch.setattr(guide, "choose_practice_folder", lambda parent: tmp_path / "practice")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(tmp_path / "export")))
    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"napari viewer could not start: {exc}")
    shell = CellQuantWindow(viewer, None)
    try:
        pages = shell._step_pages
        assert not shell._start_page.continue_button.isEnabled()
        assert not pages[0].next.isEnabled() and "Try practice images" in pages[0].blocker.text()

        shell.start_practice()
        _wait(shell)
        assert pages[0].next.isEnabled()
        assert not pages[1].next.isEnabled() and "Run" in pages[1].blocker.text()

        shell.run_current()
        _wait(shell)
        assert pages[1].next.isEnabled()
        assert not pages[2].next.isEnabled()

        shell._marker_setup.button.click()
        _wait(shell)
        assert shell._tabs.currentWidget() is pages[3]
        assert "Positive: 10" in shell._review_panel.counts.text()

        # A dragged cutoff is kept when the image is run again.
        shell._review_panel._threshold_moved(500.0)
        shell.run_current()
        _wait(shell)
        assert shell.controller.recipe.classifications[0].threshold == 500.0

        shell.start_batch(None)
        _wait(shell, 120)
        shell.export_dialog()
        _wait(shell)
        summary = pd.read_csv(tmp_path / "export" / "image_summary.csv")
        counts = [tuple(row) for row in summary[["report_1_count", "report_2_count", "report_3_count"]].to_numpy()]
        assert counts == [(item.marker_a, item.marker_b, item.both) for item in expected_answers()]
        states = guide.step_states(shell)
        assert [state.done for state in states] == [True, True, True, False, True]  # step 4 needs an approval
    finally:
        viewer.close()


def test_percent_of_cell_rule_in_the_window(tmp_path: Path, monkeypatch):
    """Quick setup with 'enough of its pixels are bright', then the minimum percent and pixel
    level changed in step 4; the settings survive the Measurements table and a rerun."""

    from qtpy.QtWidgets import QFileDialog

    from cellquant.gui import guide
    from cellquant.gui.app import CellQuantWindow
    from cellquant.practice import expected_answers

    monkeypatch.setattr(guide, "choose_practice_folder", lambda parent: tmp_path / "practice")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(tmp_path / "export")))
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
        setup = shell._marker_setup
        assert not setup.min_percent.isEnabled()
        setup.rule.setCurrentIndex(setup.rule.findData(guide.RULE_PERCENT))
        assert setup.min_percent.isEnabled()
        setup.min_percent.setValue(40.0)
        setup.button.click()
        _wait(shell)
        recipe = shell.controller.recipe
        assert all(item.comparison == "at_least" and item.threshold == 40.0 for item in recipe.classifications)
        levels = [m.pixel_level for m in recipe.measurements if m.statistic == "percent_above"]
        assert len(levels) == 2 and all(level > 0 for level in levels)  # starting levels were picked
        panel = shell._review_panel
        assert panel.level_box.isVisibleTo(panel)
        assert panel.min_percent.value() == 40.0 and panel.pixel_level.value() == pytest.approx(levels[0], abs=1e-3)
        text = panel.counts.text()
        assert "at least 40% of pixels" in text
        first = expected_answers()[0]
        assert f"Positive: {first.marker_a}\n" in text  # same answer as the mean rule on these images

        # Minimum percent typed in; pixel level raised past every pixel: nothing is positive.
        panel.min_percent.setValue(75.0)
        assert shell.controller.recipe.classifications[0].threshold == 75.0
        panel.pixel_level.setValue(1e9)
        panel.apply_level.click()
        assert shell.controller.recipe.measurements[1].pixel_level == 1e9
        assert "Positive: 0\n" in panel.counts.text()

        # The Measurements table does not drop the rule when it is read back before a run.
        shell._measurements_panel.write_recipe()
        assert shell.controller.recipe.classifications[0].comparison == "at_least"
        shell.run_current()
        _wait(shell)
        assert shell.controller.recipe.measurements[1].pixel_level == 1e9
        assert shell.controller.recipe.classifications[0].threshold == 75.0
        assert "Positive: 0\n" in panel.counts.text()

        # Quick setup shows the rule in use.
        setup.refresh()
        assert setup.rule.currentData() == guide.RULE_PERCENT and setup.min_percent.value() == 75.0

        # Advanced: a percent measurement added by hand, with an upper level, classified "at least".
        measurements = shell._measurements_panel
        measurements.statistic.setCurrentIndex(measurements.statistic.findData("percent_above"))
        assert measurements.pixel_level.isEnabled() and not measurements.pixel_level_high.isEnabled()
        measurements.pixel_level.setValue(100.0)
        measurements.use_high.setChecked(True)
        measurements.pixel_level_high.setValue(5000.0)
        measurements._add()
        added = shell.controller.recipe.measurements[-1]
        assert (added.statistic, added.pixel_level, added.pixel_level_high) == ("percent_above", 100.0, 5000.0)
        measurements.class_name.setText("Manual")
        measurements.class_measurement.setCurrentIndex(measurements.class_measurement.findData(added.id))
        assert measurements.class_comparison.currentData() == "at_least"
        measurements.class_threshold.setValue(20.0)
        measurements._add_class()
        measurements.write_recipe()
        manual = shell.controller.recipe.classifications[-1]
        assert (manual.measurement, manual.threshold, manual.comparison) == (added.id, 20.0, "at_least")
    finally:
        viewer.close()
