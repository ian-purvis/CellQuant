"""QuantAnalysis nested t test and nested one-way ANOVA: the Analyze menu and dialog, results sheets, graphs, projects, and the analysis
guide's survival goal. Needs Qt (xvfb-run on Linux) and matplotlib."""

from __future__ import annotations

import numpy as np
import pytest
from qtpy.QtCore import Qt

pytest.importorskip("pytestqt")
pytest.importorskip("matplotlib")

from cellquant.gui.quantanalysis.dialogs import NestedDialog  # noqa: E402
from cellquant.gui.quantanalysis.project import DataSheet, set_replicates  # noqa: E402
from cellquant.stats.replicates import ReplicateTable  # noqa: E402
from cellquant.stats.tables import ColumnTable  # noqa: E402

PREFIX = "Analyzed on one value per biological replicate"


@pytest.fixture
def window(qtbot):
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    window = QuantAnalysisWindow()
    qtbot.addWidget(window)
    window.resize(1400, 900)
    window.show()
    return window


def replicate_table(groups: int = 3, replicates: int = 4, seed: int = 3, paired: bool = False) -> ReplicateTable:
    """Cells of ``replicates`` experiments in ``groups`` conditions, a different number of cells in each replicate and condition."""

    rng = np.random.default_rng(seed)
    labels, columns = [], [[] for _ in range(groups)]
    for r in range(replicates):
        cells = int(rng.integers(14, 27))
        shift = rng.normal(0, 3, groups)
        for i in range(cells):
            labels.append(f"Exp {r + 1}" if i == 0 else "")
            for g in range(groups):
                missing = i >= cells - int(rng.integers(0, 4))
                columns[g].append(np.nan if missing else 50 + 8 * g + shift[g] + rng.normal(0, 9))
    return ReplicateTable("Cells", [f"G{g + 1}" for g in range(groups)], [np.array(c) for c in columns], labels, paired=paired)


def _sheet(window, groups: int = 3, **kwargs) -> DataSheet:
    sheet = window.new_replicate_table()
    set_replicates(sheet, replicate_table(groups, **kwargs))
    window.project.data_changed(sheet.id)
    window.select(sheet.id)
    return sheet


def test_run_nested_t_test_via_project(window):
    sheet = _sheet(window, groups=2)
    result = window.analyze("nested", {"alternative": "greater", "alpha": 0.01, "ci": 0.9, "style": "apa"})
    assert result.kind == "nested" and result.error == "" and result.name == f"Nested t test: {sheet.name}"
    assert result.result.analysis == "Nested t test" and result.result.options["alternative"] == "greater"
    assert result.result.values["names"] == ["G1", "G2"] and result.result.values["n"][0] > result.result.values["replicates"][0] == 4
    assert not result.methods.startswith(PREFIX) and PREFIX not in result.methods and "nested t test" in result.methods
    assert "pseudoreplication" not in " ".join(result.warnings).lower()
    # Editing the values re-runs it: the replicate means no longer decide the test, every value does
    before = result.result.values["p"]
    values = sheet.replicates.values[0].copy()
    values[np.isfinite(values)] += 20
    sheet.replicates.values[0] = values
    set_replicates(sheet, sheet.replicates)
    window.project.data_changed(sheet.id)
    assert result.result.values["p"] != before and result.error == ""


def test_run_nested_anova_with_comparisons_via_project(window):
    sheet = _sheet(window)
    result = window.analyze("nested", {"comparisons": "control", "method": "dunnett", "control": ["G1"], "family": "per_reference",
                                       "alpha": 0.05, "ci": 0.95, "style": "classic"})
    assert result.error == "" and result.name == f"Nested one-way ANOVA: {sheet.name}" and not result.methods.startswith(PREFIX)
    rows = result.result.values["comparisons"]
    assert [row["group_1"] for row in rows] == ["G1", "G1"] and result.result.values["dfn"] == 2
    # A graph of the table can show its comparisons: one bracket each
    graph = window.new_graph("scatter")
    graph.result_id = result.id
    view = window.views[graph.id]
    view.fill()
    assert view.comparison.findData(result.id) >= 0 and len(view.figure.axes[0].lines) == 2


def test_nested_t_test_bracket_on_a_graph(window):
    _sheet(window, groups=2)
    result = window.analyze("nested", {})
    graph = window.new_graph("superplot")
    assert graph.result_id == result.id  # a new graph links to the latest comparison
    view = window.views[graph.id]
    view.fill()
    ax = view.figure.axes[0]
    assert len(ax.lines) >= 1 and any(text.get_text() in ("ns", "*", "**", "***", "****") for text in ax.texts)


def test_dialog_two_groups_and_three(window):
    sheet = _sheet(window, groups=2)
    dialog = NestedDialog(window, sheet.table, sheet.replicates)
    assert dialog.tails.isVisibleTo(dialog) and not dialog.compare.isVisibleTo(dialog)
    assert dialog.options() == {"alternative": "two-sided", "alpha": 0.05, "ci": 0.95, "style": "classic"}
    dialog.tails.setCurrentIndex(dialog.tails.findData("less"))
    dialog.accept()
    assert dialog.result() == 1 and dialog.options()["alternative"] == "less"
    assert dialog.tails.toolTip() and dialog.alpha.toolTip() and dialog.ci.toolTip() and dialog.style.toolTip()

    sheet = _sheet(window, groups=3)
    dialog = NestedDialog(window, sheet.table, sheet.replicates)
    assert not dialog.tails.isVisibleTo(dialog) and dialog.compare.isVisibleTo(dialog)
    options = dialog.options()
    assert options["comparisons"] == "all" and options["method"] == "tukey" and "alternative" not in options
    assert [button.property("key") for button in dialog.goal.buttons()] == ["none", "all", "control", "selected"]  # no trend
    dialog.accept()
    assert dialog.result() == 1


def test_dialog_through_the_menu(window, monkeypatch):
    from qtpy.QtWidgets import QMenu

    _sheet(window)
    monkeypatch.setattr(NestedDialog, "exec", lambda self: (self.accept(), 1)[-1])
    actions = {action.text(): action for menu in window.menuBar().findChildren(QMenu) for action in menu.actions()}
    assert "nested" in actions["Nested t test / ANOVA…"].toolTip().lower()
    actions["Nested t test / ANOVA…"].trigger()
    result = window.project.sheet(window.current_id())
    assert result.kind == "nested" and result.error == "" and result.result.analysis == "Nested one-way ANOVA"


def test_advanced_df_is_recorded_and_stripped(window):
    sheet = _sheet(window)
    dialog = NestedDialog(window, sheet.table, sheet.replicates)
    assert dialog.advanced.values() == {}  # unticked: nothing set by hand
    dialog.advanced.toggle.setChecked(True)
    assert dialog.advanced.values() == {}  # ticked, all Default: still nothing
    mode, spin, *_rest = dialog.advanced.fields["df"]
    mode.setCurrentIndex(mode.findData("custom"))
    spin.setValue(30)
    options = dialog.options()
    assert options["df"] == 30 and options["advanced_for"] == [4, 4, 4]
    dialog.accept()  # the engine would refuse advanced_for: it is stripped before the test run
    assert dialog.result() == 1
    result = window.analyze("nested", options)
    assert result.error == "" and result.result.values["dfd"] == 30
    assert any(section.title == "Changed from the default" for section in result.result.sections) and "Changed from the default" in result.methods
    mode.setCurrentIndex(mode.findData("conservative"))
    result = window.analyze("nested", dialog.options())
    assert result.result.values["dfd"] == pytest.approx(min(9, 8.867423854162018))  # the smaller of replicates (12) − groups (3) and the computed df
    # Change options shows it again; a changed table flags it
    again = NestedDialog(window, sheet.table, sheet.replicates, result.options)
    assert again.advanced.toggle.isChecked() and again.options()["df"] == "conservative"
    sheet.replicates.values[0] = np.append(sheet.replicates.values[0], 5.0)
    sheet.replicates.labels.append("Exp 5")
    for column in range(1, 3):
        sheet.replicates.values[column] = np.append(sheet.replicates.values[column], 6.0 + column)
    set_replicates(sheet, sheet.replicates)
    window.project.data_changed(sheet.id)
    assert any("different table" in text for text in result.warnings)


def test_m_only_with_a_correction_that_counts_comparisons(window):
    sheet = _sheet(window)
    dialog = NestedDialog(window, sheet.table, sheet.replicates, {"comparisons": "all", "method": "bonferroni"})
    dialog.advanced.toggle.setChecked(True)
    assert dialog.advanced.fields["m"][3].isVisibleTo(dialog)
    dialog.method.setCurrentIndex(dialog.method.findData("tukey"))
    assert not dialog.advanced.fields["m"][3].isVisibleTo(dialog)
    dialog.method.setCurrentIndex(dialog.method.findData("bonferroni"))
    mode, spin, *_rest = dialog.advanced.fields["m"]
    mode.setCurrentIndex(mode.findData("custom"))
    spin.setValue(6)
    result = window.analyze("nested", dialog.options())
    assert result.error == "" and result.options["m"] == 6


def test_selected_pairs_need_a_tick(window):
    sheet = _sheet(window)
    dialog = NestedDialog(window, sheet.table, sheet.replicates, {"comparisons": "selected", "method": "bonferroni"})
    dialog.accept()
    assert dialog.result() == 0 and "Tick at least one pair" in dialog.note.text()
    dialog.pairs.item(0).setCheckState(Qt.Checked)
    dialog.accept()
    assert dialog.result() == 1 and dialog.options()["pairs"] == [["G1", "G2"]]


def test_paired_replicate_table_error_shows_in_the_dialog_and_sheet(window):
    sheet = _sheet(window, groups=2, paired=True)
    dialog = NestedDialog(window, sheet.table, sheet.replicates)
    dialog.accept()
    assert dialog.result() == 0 and "Paired" in dialog.note.text() and dialog.note.isVisibleTo(dialog)
    result = window.analyze("nested", {})
    assert result.error and "Paired" in result.error and result.result is None


def test_too_few_replicates_error_shows_in_the_dialog(window):
    sheet = _sheet(window)
    set_replicates(sheet, ReplicateTable("Cells", ["A", "B", "C"], [np.array([1.0, 2.0, 3.0])] * 3, ["Exp 1", "", ""]))
    window.project.data_changed(sheet.id)
    dialog = NestedDialog(window, sheet.table, sheet.replicates)
    dialog.accept()
    assert dialog.result() == 0 and "more replicates than groups" in dialog.note.text()


def test_table_without_replicates_is_refused(window):
    sheet = window.new_table()
    sheet.table = ColumnTable.from_columns({"A": [1.0, 2.0, 3.0], "B": [2.0, 3.0, 5.0]})
    window.project.data_changed(sheet.id)
    window.select(sheet.id)
    count = len(window.project.results)
    assert window.analyze("nested", {}) is None
    assert "Nested analyses need a table with replicates: tick Replicates above the table (or File → New table with replicates)" in (
        window.statusBar().currentMessage())
    assert len(window.project.results) == count
    window.select(window.new_grouped_table().id)
    assert window.analyze("nested", {}) is None
    window.select(window.new_xy_table().id)
    assert window.analyze("nested", {}) is None
    window.select(window.new_contingency_table().id)
    assert window.analyze("nested", {}) is None
    assert len(window.project.results) == count


def test_turning_replicates_off_leaves_an_error_on_the_sheet(window):
    sheet = _sheet(window, groups=2)
    result = window.analyze("nested", {})
    assert result.error == ""
    set_replicates(sheet, None)
    window.project.data_changed(sheet.id)
    assert "Nested analyses need a table with replicates" in result.error
    window.change_options(result)
    assert "Nested analyses need a table with replicates" in window.statusBar().currentMessage()


def test_other_analyses_still_start_with_the_replicate_sentence(window):
    sheet = _sheet(window, groups=2)
    result = window.analyze("compare", {"test": "welch_t", "first": 0, "second": 1, "alternative": "two-sided", "ci": 0.95, "alpha": 0.05,
                                        "style": "classic"})
    assert result.methods.startswith(PREFIX) and sheet.replicates is not None


def test_change_options_and_name_follow_the_columns(window, monkeypatch):
    _sheet(window)
    result = window.analyze("nested", {"comparisons": "none"})
    assert result.name.startswith("Nested one-way ANOVA") and not result.result.values.get("comparisons")
    seen = {}
    monkeypatch.setattr(NestedDialog, "exec", lambda self: seen.setdefault("options", self.options()) and 1)
    window.change_options(result)
    assert seen["options"]["comparisons"] is None and result.error == ""


def test_project_round_trip(window, tmp_path):
    from cellquant.gui.quantanalysis import project_file
    from cellquant.gui.quantanalysis.project import Project

    _sheet(window)
    result = window.analyze("nested", {"comparisons": "all", "method": "bonferroni", "df": "conservative", "m": 5, "advanced_for": [4, 4, 4],
                                       "alpha": 0.01, "ci": 0.9, "style": "apa"})
    assert result.error == ""
    graph = window.new_graph("superplot")
    assert graph.result_id == result.id
    path = project_file.save(window.project, tmp_path / "nested.cqstats")
    other = Project()
    _current, notes = project_file.load(path, other)
    assert notes == []
    loaded = other.results[result.id]
    assert loaded.kind == "nested" and loaded.error == "" and loaded.options["m"] == 5 and loaded.options["df"] == "conservative"
    assert loaded.result.values["p"] == pytest.approx(result.result.values["p"])
    assert loaded.result.values["dfd"] == result.result.values["dfd"] and not loaded.methods.startswith(PREFIX)
    assert other.graphs[graph.id].result_id == result.id
    loaded.options.update(df="x", alpha=[], m=None, comparisons="trend")  # a damaged file still runs (or says why on the sheet)
    other.rerun(loaded)
    assert isinstance(loaded.error, str)


def test_guide_survival_goal_hides_the_other_questions(window, qtbot):
    from cellquant.gui.quantanalysis.guide import AnalysisGuideDialog
    from cellquant.gui.quantanalysis.dialogs import _check
    from cellquant.stats.advisor import Facts

    guide = AnalysisGuideDialog(window, Facts())
    qtbot.addWidget(guide)
    guide.show()
    boxes = {name: getattr(guide, name).property("box") for name in ("dot", "groups", "paired", "xy", "xy_replicates", "gaussian", "equal",
                                                                     "comparisons", "tails", "interaction")}
    assert boxes["dot"].isVisibleTo(guide) and boxes["gaussian"].isVisibleTo(guide)
    _check(guide.goal, "survival")
    assert guide.recommendation().kind == "survival"
    assert not any(box.isVisibleTo(guide) for box in boxes.values())
    assert guide.goal.property("box").isVisibleTo(guide) and "Kaplan-Meier" in guide.answer.toPlainText()
    assert not guide.advanced.fields["df"][3].isVisibleTo(guide)
    _check(guide.goal, "groups")
    assert boxes["dot"].isVisibleTo(guide) and boxes["groups"].isVisibleTo(guide) and boxes["gaussian"].isVisibleTo(guide)


def test_guide_survival_run_opens_the_survival_dialog(window, monkeypatch):
    from cellquant.gui.quantanalysis import dialogs
    from cellquant.gui.quantanalysis.dialogs import _check
    from cellquant.gui.quantanalysis.guide import AnalysisGuideDialog
    from cellquant.stats.xy import XYTable

    sheet = window.new_survival_table()
    sheet.xy = XYTable("Trial", "Time", np.array([2.0, 3, 5, 7, 4, 6, 8, 10]), ["Control", "Treated"],
                       np.array([[1, np.nan], [1, np.nan], [0, np.nan], [1, np.nan], [np.nan, 1], [np.nan, 0], [np.nan, 1], [np.nan, 1.0]])[:, :, None])
    window.project.data_changed(sheet.id)
    window.select(sheet.id)
    seen = {}
    monkeypatch.setattr(AnalysisGuideDialog, "exec", lambda self: _check(self.goal, "survival") or 1)
    monkeypatch.setattr(dialogs.SurvivalDialog, "exec", lambda self: seen.setdefault("xy", self.xy is sheet.xy) and 1)
    window.help_choose()
    assert seen["xy"] is True
    result = next(iter(window.project.results.values()))
    assert result.kind == "survival" and result.error == ""


def test_guide_survival_needs_an_xy_table(window, monkeypatch):
    from cellquant.gui.quantanalysis import dialogs
    from cellquant.gui.quantanalysis.dialogs import _check
    from cellquant.gui.quantanalysis.guide import AnalysisGuideDialog

    window.select(window.new_table().id)
    opened = []
    monkeypatch.setattr(AnalysisGuideDialog, "exec", lambda self: _check(self.goal, "survival") or 1)
    monkeypatch.setattr(dialogs.SurvivalDialog, "exec", lambda self: opened.append(self) or 0)
    window.help_choose()
    assert not opened and not window.project.results and "File → New survival table" in window.statusBar().currentMessage()
