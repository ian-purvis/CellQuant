"""QuantAnalysis, the analysis guide (Help me choose…) and the Advanced settings of the analysis dialogs: the guide reads the
selected table, recommends an analysis with its degrees of freedom and opens it filled in; settings set by hand reach the
results. Needs Qt (xvfb-run on Linux) and matplotlib."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("matplotlib")

from cellquant.gui.quantanalysis.dialogs import AnovaDialog, CompareDialog, TwoWayDialog, XYDialog, _check  # noqa: E402
from cellquant.gui.quantanalysis.guide import AnalysisGuideDialog, facts_for  # noqa: E402
from cellquant.gui.quantanalysis.project import DataSheet, set_grouped, set_xy  # noqa: E402
from cellquant.stats import ColumnTable, sources  # noqa: E402
from cellquant.stats.grouped import GroupedTable  # noqa: E402
from cellquant.stats.xy import XYTable  # noqa: E402
from tests.test_stats_import import make_results  # noqa: E402

A = [12.0, 15.0, 11.0, 14.0, 13.0]
B = [18.0, 25.0, 16.0, 22.0, 30.0, 19.0]


@pytest.fixture
def window(qtbot):
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    window = QuantAnalysisWindow()
    qtbot.addWidget(window)
    window.show()
    return window


def _two(window, paired=False) -> DataSheet:
    data = window.project.add_data(DataSheet("Two groups", ColumnTable.from_columns({"Control": A, "Treated": B}, paired=paired)))
    window.select(data.id)
    return data


def _section(result, title):
    return next((section for section in result.sections if section.title == title), None)


def test_guide_reads_the_selected_table(window, qtbot):
    data = _two(window)
    guide = AnalysisGuideDialog(window, facts_for(data))
    qtbot.addWidget(guide)
    text = guide.answer.toPlainText()
    assert guide.recommendation().options["test"] == "welch_t"
    assert "N = 5 and 6" in text and "Welch-Satterthwaite" in text and "Your table" in text and "Shapiro-Wilk" in text
    assert guide.groups.property("box").isHidden() is False and guide.xy.property("box").isHidden()
    assert guide.advanced.box.isHidden() and not guide.advanced.toggle.isChecked()
    assert guide.advanced.fields["df"][0].itemText(0).startswith("Default (")
    _check(guide.dot, "cell")
    assert "Pseudoreplication" in guide.answer.toPlainText() and guide.independent.isVisibleTo(guide)
    guide.independent.setChecked(True)
    assert "Pseudoreplication" not in guide.answer.toPlainText()


def test_guide_knows_how_results_were_imported(window, tmp_path, qtbot):
    spec = sources.ImportSpec("area", dot="object", group_by="Folder 1")
    data = window.open_folder(make_results(tmp_path / "objects"), spec, accept=True)
    facts = facts_for(data)
    assert facts.dot == "cell"
    guide = AnalysisGuideDialog(window, facts)
    qtbot.addWidget(guide)
    assert guide.answers().dot == "cell" and guide.recommendation().warnings[0].startswith("Pseudoreplication")
    replicates = window.open_folder(make_results(tmp_path / "units"), accept=True)
    assert facts_for(replicates).dot == "replicate"


def test_guide_runs_with_degrees_of_freedom_set_by_hand(window, monkeypatch):
    data = _two(window)
    seen = {}

    def fake_guide(self):
        self.advanced.toggle.setChecked(True)
        mode, spin, *_rest = self.advanced.fields["df"]
        mode.setCurrentIndex(mode.findData("custom"))
        spin.setValue(4)
        seen["text"] = self.answer.toPlainText()
        return 1

    monkeypatch.setattr(AnalysisGuideDialog, "exec", fake_guide)
    monkeypatch.setattr(CompareDialog, "exec", lambda self: seen.setdefault("options", self.options()) and 1)
    window.help_choose()
    assert "Set by hand under Advanced: 4" in seen["text"]
    assert seen["options"]["test"] == "welch_t" and seen["options"]["df"] == 4
    sheet = next(iter(window.project.results.values()))
    assert sheet.error == "" and sheet.options["df"] == 4
    from scipy import stats

    t = sheet.result.values["t"]
    assert sheet.result.values["p"] == pytest.approx(2 * stats.t.sf(abs(t), 4))
    assert _section(sheet.result, "Changed from the default") is not None and "Changed from the default" in sheet.result.methods
    dialog = CompareDialog(window, data.table, sheet.options)  # Change options…: the setting is kept, Advanced ticked
    assert dialog.advanced.toggle.isChecked() and dialog.options()["df"] == 4
    dialog.advanced.toggle.setChecked(False)
    assert "df" not in dialog.options()


def test_guide_says_when_the_table_does_not_fit(window, monkeypatch):
    _two(window)
    monkeypatch.setattr(AnalysisGuideDialog, "exec", lambda self: _check(self.goal, "xy") or 1)
    window.help_choose()
    assert "XY table" in window.statusBar().currentMessage() and not window.project.results


def test_guide_fills_two_way_and_xy(window, monkeypatch):
    sheet = window.new_grouped_table()
    set_grouped(sheet, GroupedTable("t", ["WT", "KO"], ["Vehicle", "Drug"], np.arange(12, dtype=float).reshape(2, 2, 3) ** 1.3))
    window.select(sheet.id)
    seen = {}
    monkeypatch.setattr(AnalysisGuideDialog, "exec", lambda self: seen.setdefault("goal", self.answers().goal) and 1)
    monkeypatch.setattr(TwoWayDialog, "exec", lambda self: seen.setdefault("twoway", self.options()) and 0)
    window.help_choose()
    assert seen["goal"] == "two_factors" and seen["twoway"]["interaction"] is True
    xy = window.new_xy_table()
    set_xy(xy, XYTable("y", "x", np.array([1, 2, 3, 4, 5.0]), ["Y"], (np.arange(10, dtype=float) ** 1.1).reshape(5, 1, 2)))
    window.select(xy.id)
    seen.clear()
    monkeypatch.setattr(XYDialog, "exec", lambda self: seen.setdefault("xy", (self.kind, self.options())) and 0)
    window.help_choose()
    assert seen["goal"] == "xy" and seen["xy"][0] == "linreg"


def test_advanced_shows_only_what_the_test_uses(qtbot):
    table = ColumnTable.from_columns({"A": A, "B": B})
    compare = CompareDialog(None, table)
    qtbot.addWidget(compare)
    assert not compare.advanced.toggle.isChecked() and compare.advanced.box.isHidden()
    assert set(compare.options()) == {"test", "first", "second", "alternative", "alpha", "ci", "style"}
    compare.set_options({**compare.options(), "test": "mann_whitney"})
    assert compare.advanced.fields["df"][3].isHidden() and compare.advanced.fields["difference"][3].isHidden()
    compare.set_options({**compare.options(), "test": "welch_t", "difference": 2.5, "df": "conservative"})
    assert compare.advanced.toggle.isChecked() and compare.options()["difference"] == 2.5 and compare.options()["df"] == "conservative"
    three = ColumnTable.from_columns({"A": A, "B": B, "C": [5.0, 6, 7, 8]})
    anova = AnovaDialog(None, three, {"test": "ordinary", "comparisons": "all", "method": "tukey"})
    qtbot.addWidget(anova)
    fields = anova.advanced.fields
    assert not fields["df"][3].isHidden() and not fields["sd"][3].isHidden()
    assert fields["m"][3].isHidden() and fields["epsilon"][3].isHidden()
    anova.set_options({**anova.options(), "method": "bonferroni", "m": 10})
    assert not fields["m"][3].isHidden() and anova.options()["m"] == 10
    paired = ColumnTable.from_columns({"A": A, "B": A[::-1], "C": [5.0, 6, 7, 8, 9]}, paired=True)
    rm = AnovaDialog(None, paired, {"test": "repeated_measures", "epsilon": 0.5})
    qtbot.addWidget(rm)
    assert not rm.advanced.fields["epsilon"][3].isHidden() and rm.options()["epsilon"] == 0.5


def test_advanced_settings_reach_each_analysis(window):
    three = window.project.add_data(DataSheet("Three", ColumnTable.from_columns({"A": A, "B": B, "C": [5.0, 6, 7, 8]})))
    window.select(three.id)
    sheet = window.analyze("anova", {"test": "ordinary", "comparisons": "all", "method": "bonferroni", "df": 5, "m": 10, "sd": 2.0,
                                     "epsilon": 0.5})
    assert sheet.error == "" and sheet.result.values["dfd"] == 5
    changed = _section(sheet.result, "Changed from the default")
    assert changed is not None and len(changed.rows) == 3  # epsilon is for repeated measures only: dropped
    plain = window.analyze("anova", {"test": "kruskal_wallis", "comparisons": "all", "method": "dunn", "df": 5})
    assert plain.error == "" and _section(plain.result, "Changed from the default") is None


def test_guide_from_a_dialog_keeps_the_import_and_the_columns(window, tmp_path, monkeypatch):
    spec = sources.ImportSpec("area", dot="object", group_by="Folder 1")
    data = window.open_folder(make_results(tmp_path / "objects"), spec, accept=True)
    seen = {}

    def fake_guide(self):
        seen["facts"], seen["rec"] = self.facts, self.recommendation()
        return 0

    monkeypatch.setattr(AnalysisGuideDialog, "exec", fake_guide)
    dialog = CompareDialog(window, data.table, sheet=data)
    dialog._guide()
    assert seen["facts"].dot == "cell" and seen["rec"].warnings[0].startswith("Pseudoreplication")
    three = ColumnTable.from_columns({"A": A, "B": B, "C": [1.0, 2, 3]})
    sheet = window.project.add_data(DataSheet("Three", three))
    compare = CompareDialog(window, three, {"test": "welch_t", "first": "A", "second": "C", "alternative": "two-sided", "ci": 0.95,
                                           "alpha": 0.05, "style": "classic"}, sheet=sheet)
    compare._guide()
    assert seen["facts"].names == ["A", "C"] and seen["facts"].sizes == [5, 3]


def test_settings_for_a_different_table_are_flagged(window):
    data = _two(window)
    dialog = CompareDialog(window, data.table)
    dialog.set_options({**dialog.options(), "df": 4})
    options = dialog.options()
    assert options["advanced_for"] == [5, 6]
    sheet = window.analyze("compare", options)
    assert not any("different table" in text for text in sheet.result.warnings)
    data.table = ColumnTable.from_columns({"Control": A + [16.0, 17.0], "Treated": B})
    window.project.data_changed(data.id) if hasattr(window.project, "data_changed") else None
    window.project.rerun(sheet)
    assert any("different table" in text and "5, 6" in text and "7, 6" in text for text in sheet.result.warnings)
    again = CompareDialog(window, data.table, sheet.options)
    assert not again.advanced.stale.isHidden() and "different table" in again.advanced.stale.text()


def test_guide_answer_about_cells_reaches_the_results(window, monkeypatch):
    _two(window)
    monkeypatch.setattr(AnalysisGuideDialog, "exec", lambda self: _check(self.dot, "cell") or 1)
    monkeypatch.setattr(CompareDialog, "exec", lambda self: 1)
    window.help_choose()
    sheet = next(iter(window.project.results.values()))
    assert sheet.options["not_independent"] == "cells" and sheet.result.warnings[0].startswith("Pseudoreplication")


def test_repeated_measures_with_blanks_offers_no_df(qtbot, window):
    table = ColumnTable.from_columns({"A": [1.0, 2, 3, 4], "B": [2.0, 3, np.nan, 5], "C": [3.0, 5, 6, 8]}, paired=True)
    anova = AnovaDialog(None, table, {"test": "repeated_measures"})
    qtbot.addWidget(anova)
    for key in ("df", "epsilon", "sd"):
        assert anova.advanced.fields[key][3].isHidden()
    data = window.project.add_data(DataSheet("Blanks", table))
    guide = AnalysisGuideDialog(None, facts_for(data))
    qtbot.addWidget(guide)
    _check(guide.groups, 3)
    _check(guide.paired, True)
    rec = guide.recommendation()
    assert rec.mixed and rec.df is None and any("mixed-effects" in text for text in rec.warnings)
    assert guide.advanced.fields["df"][3].isHidden()


def test_dialogs_check_advanced_values_before_running(qtbot):
    three = ColumnTable.from_columns({"A": A, "B": B, "C": [5.0, 6, 7, 8]})
    anova = AnovaDialog(None, three, {"test": "ordinary", "comparisons": "all", "method": "bonferroni", "m": 1})
    qtbot.addWidget(anova)
    anova.accept()
    assert anova.result() == 0 and "less than" in anova.note.text()
    compare = CompareDialog(None, three)
    qtbot.addWidget(compare)
    compare.advanced.set_values({"df": ["not", "a", "number"], "difference": None})
    assert not compare.advanced.toggle.isChecked() and "df" not in compare.options()
