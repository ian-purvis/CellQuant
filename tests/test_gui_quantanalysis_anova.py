"""QuantAnalysis, three or more groups: the ANOVA dialog, Help me choose, brackets and letters on graphs, outliers,
a stack of P values, and the follow-up checks (closing, reuse, missing libraries). Needs Qt (xvfb-run on Linux) and matplotlib."""

from __future__ import annotations

import builtins

import numpy as np
import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("matplotlib")

from qtpy.QtCore import Qt  # noqa: E402

from cellquant.errors import CellQuantError  # noqa: E402
from cellquant.gui.quantanalysis import tips  # noqa: E402
from cellquant.gui.quantanalysis.dialogs import (  # noqa: E402
    AnovaDialog,
    OutlierDialog,
    PStackDialog,
    _check,
    methods_for,
)
from cellquant.gui.quantanalysis.guide import AnalysisGuideDialog  # noqa: E402
from cellquant.gui.quantanalysis.project import DataSheet, parse_p_values  # noqa: E402
from cellquant.stats import ColumnTable, compact_letter_display  # noqa: E402

NAMES = ["Negative control", "Positive control", "Treated 1", "Treated 2"]
TWO_REFERENCES = {"test": "ordinary", "comparisons": "control", "method": "dunnett", "control": [NAMES[0], NAMES[1]], "family": "per_reference",
                  "alpha": 0.05, "ci": 0.95, "style": "classic", "geisser_greenhouse": True}
ALL_PAIRS = {**TWO_REFERENCES, "comparisons": "all", "method": "tukey", "control": []}


def make_table(outlier: bool = False) -> ColumnTable:
    rng = np.random.default_rng(3)
    columns = {NAMES[0]: rng.normal(10, 2, 8), NAMES[1]: rng.normal(40, 4, 8), NAMES[2]: rng.normal(22, 3, 8), NAMES[3]: rng.normal(12, 2, 8)}
    if outlier:
        columns[NAMES[3]][7] = 70.0
    return ColumnTable.from_columns(columns, title="% of target")


@pytest.fixture
def window(qtbot):
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    window = QuantAnalysisWindow()
    qtbot.addWidget(window)
    window.show()
    return window


def _data(window, outlier: bool = False) -> DataSheet:
    data = window.project.add_data(DataSheet("Conditions", make_table(outlier)))
    window.select(data.id)
    return data


def _hollow(ax) -> list:
    """The point sets drawn without fill (flagged outliers); the error bars are line collections and do not count."""

    from matplotlib.collections import PathCollection

    return [c for c in ax.collections if isinstance(c, PathCollection) and len(c.get_facecolor()) == 0]


def _graph(window, data, result, **fmt):
    window.select(data.id)
    graph = window.new_graph("scatter")
    graph.result_id = result.id
    graph.format.update(width=120, **fmt)
    view = window.views[graph.id]
    view.fill()
    return graph, view


# --- The dialog ------------------------------------------------------------------------------------------------


def test_methods_are_filtered_to_the_design():
    assert methods_for("ordinary", "all")[0] == "tukey" and "dunnett" not in methods_for("ordinary", "all")
    assert methods_for("ordinary", "control")[0] == "dunnett" and "tukey" not in methods_for("ordinary", "control")
    assert "dunnett" not in methods_for("ordinary", "control", combined=True)  # one reference per Dunnett family
    assert methods_for("welch", "all")[0] == "games_howell" and methods_for("welch", "control")[0] == "dunnett_t3"
    assert methods_for("kruskal_wallis", "control") == ["dunn", "dunn_uncorrected", "bky", "bh", "by"]
    assert methods_for("friedman", "all")[0] == "dunn"
    assert {"newman_keuls", "fisher_lsd", "holm_sidak", "bonferroni", "sidak"} <= set(methods_for("ordinary", "all"))


def test_anova_dialog_options_and_tooltips(qtbot):
    dialog = AnovaDialog(None, make_table())
    qtbot.addWidget(dialog)
    options = dialog.options()
    assert options["test"] == "ordinary" and options["comparisons"] == "all" and options["method"] == "tukey" and options["geisser_greenhouse"]
    dialog.set_options(TWO_REFERENCES)
    assert dialog.options()["control"] == NAMES[:2] and dialog.options()["method"] == "dunnett"
    assert dialog.control2.isEnabled() and dialog.family_mode.isEnabled()
    dialog.family_mode.setCurrentIndex(dialog.family_mode.findData("combined"))
    assert dialog.method.findData("dunnett") < 0 and dialog.options()["family"] == "combined"
    _check(dialog.family, "nonparametric")
    assert dialog.test.currentData() == "kruskal_wallis" and not dialog.ci.isEnabled()
    assert dialog.method.itemData(0, Qt.DisplayRole).startswith("Dunn's multiple")
    _check(dialog.design, True)
    assert dialog.test.currentData() == "friedman"
    _check(dialog.family, "parametric")
    assert dialog.test.currentData() == "repeated_measures" and dialog.greenhouse.isEnabled()
    _check(dialog.goal, "selected")
    dialog.pairs.item(0).setCheckState(Qt.Checked)
    assert dialog.options()["pairs"] == [NAMES[:2]] and dialog.options()["method"] == "sidak"
    for widget in (dialog.test, dialog.alpha, dialog.ci, dialog.style, dialog.method, dialog.control, dialog.control2, dialog.family_mode,
                   dialog.pairs, dialog.greenhouse, *dialog.goal.buttons(), *dialog.design.buttons(), *dialog.family.buttons()):
        assert widget.toolTip(), widget
    for index in range(dialog.method.count()):
        assert dialog.method.itemData(index, Qt.ToolTipRole)
    dialog.pairs.item(0).setCheckState(Qt.Unchecked)
    dialog.accept()
    assert dialog.result() == 0 and "at least one pair" in dialog.note.text()
    assert set(tips.METHOD_TIPS) >= set(__import__("cellquant.stats.multcomp", fromlist=["METHOD_NAMES"]).METHOD_NAMES)


def test_outlier_and_pstack_dialogs(qtbot):
    outliers = OutlierDialog(None, make_table())
    qtbot.addWidget(outliers)
    assert outliers.options() == {"method": "rout", "q": 0.01, "alpha": 0.05, "columns": None, "style": "classic"}
    _check(outliers.method, "grubbs")
    assert outliers.alpha.isEnabled() and not outliers.q.isEnabled()
    outliers.columns.item(0).setCheckState(Qt.Unchecked)
    assert outliers.options()["columns"] == NAMES[1:]
    for widget in (outliers.q, outliers.alpha, outliers.columns, *outliers.method.buttons()):
        assert widget.toolTip()
    stack = PStackDialog(None)
    qtbot.addWidget(stack)
    stack.text.setPlainText("0.01\nsecond comparison  0.04\nP < 0.0001\n\n")
    assert stack.options()["method"] == "bky"
    for widget in (stack.text, stack.method, stack.alpha, stack.style):
        assert widget.toolTip()
    stack.text.setPlainText("0.01\nnot a number")
    stack.accept()
    assert stack.result() == 0 and "Line 2" in stack.note.text()
    assert parse_p_values("0.01\nsecond comparison  0.04\nP < 0.0001\n0,5\n") == ([0.01, 0.04, 0.0001, 0.5], ["P1", "second comparison", "P3", "P4"])


def test_help_me_choose_recommends_for_three_or_more_groups(qtbot):
    from cellquant.stats.advisor import Answers

    guide = AnalysisGuideDialog(None, answers=Answers(groups=3))
    qtbot.addWidget(guide)
    text = guide.answer.toPlainText()
    assert guide.recommendation().options == {"test": "welch", "comparisons": "control", "method": None}
    assert "Welch" in text and "Kruskal" not in text.split("Alternatives")[0]
    _check(guide.equal, True)
    assert guide.recommendation().options["test"] == "ordinary" and "Dunnett" in guide.answer.toPlainText()
    _check(guide.gaussian, "no")
    assert guide.recommendation().options["test"] == "kruskal_wallis" and "Dunn's tests compare each group" in guide.answer.toPlainText()
    _check(guide.paired, True)
    assert guide.recommendation().options["test"] == "friedman"
    _check(guide.gaussian, "yes")
    assert guide.recommendation().options["test"] == "repeated_measures"
    _check(guide.comparisons, "none")
    assert guide.recommendation().options["comparisons"] is None
    assert guide.tails.property("box").isHidden() and not guide.comparisons.property("box").isHidden()
    _check(guide.groups, 2)
    assert guide.recommendation().kind == "compare" and guide.comparisons.property("box").isHidden()
    assert "How to report it" in guide.answer.toPlainText()
    for button in guide.comparisons.buttons():
        assert button.toolTip()


def test_help_me_choose_fills_the_anova_dialog(window, monkeypatch):
    data = _data(window)
    answers = {}

    def fake_guide(self):
        _check(self.gaussian, "no")
        return 1

    monkeypatch.setattr(AnalysisGuideDialog, "exec", fake_guide)
    monkeypatch.setattr(AnovaDialog, "exec", lambda self: answers.setdefault("options", self.options()) and 1)
    window.help_choose()
    assert answers["options"]["test"] == "kruskal_wallis" and answers["options"]["comparisons"] == "control"
    assert answers["options"]["control"] == [NAMES[0]] and answers["options"]["method"] == "dunn"
    sheet = next(iter(window.project.results.values()))
    assert sheet.kind == "anova" and sheet.name == f"Kruskal-Wallis test: {data.name}" and sheet.error == ""


# --- Results and graphs ------------------------------------------------------------------------------------------


def test_anova_results_sheet_and_live_links(window):
    from cellquant.gui.quantanalysis.results_view import ResultSheetView, result_text

    data = _data(window)
    sheet = window.analyze("anova", TWO_REFERENCES)
    assert sheet.error == "" and sheet.name == "Ordinary one-way ANOVA: Conditions"
    view = window.stack.currentWidget()
    assert isinstance(view, ResultSheetView) and view.options_button.isVisible() and not view.clean_button.isVisible()
    titles = [section.title for section in sheet.result.sections]
    assert "ANOVA table" in titles and any(title.startswith("Multiple comparisons: Dunnett") for title in titles)
    rows = sheet.result.values["comparisons"]
    assert [row["reference"] for row in rows] == [NAMES[0]] * 3 + [NAMES[1]] * 3 and {row["family"] for row in rows} == {1, 2}
    assert "Dunnett" in result_text(sheet) and "Reference group(s)" in result_text(sheet)
    before = sheet.result.values["p"]
    window.select(data.id)
    window.stack.currentWidget().grid.item(0, 3).setText("60")  # edit the table: the sheet recomputes
    assert sheet.result.values["p"] != before
    window.change_options(sheet, {**TWO_REFERENCES, "test": "kruskal_wallis", "method": "dunn"})
    assert sheet.name.startswith("Kruskal-Wallis") and len(window.project.results) == 1 and sheet.error == ""
    window.change_options(sheet, {**ALL_PAIRS, "test": "repeated_measures"})
    assert sheet.error == "" and sheet.result.analysis.startswith("Repeated measures")
    assert window.analyze("anova", {**ALL_PAIRS, "test": "repeated_measures", "control": []}) is not None


def test_anova_needs_three_columns(window):
    window.new_table()
    assert window.analyze("anova") is None and not window.project.results


def test_brackets_are_coloured_by_reference(window):
    data = _data(window)
    result = window.analyze("anova", TWO_REFERENCES)
    graph, view = _graph(window, data, result)
    assert view.comparison.findData(result.id) >= 0  # the graph's comparison menu lists the ANOVA
    ax = view.figure.axes[0]
    assert len(ax.lines) == 6  # one bracket per comparison
    colours = {line.get_color() for line in ax.lines}
    assert colours == {"#000000", "#D62728"}
    stars = [text for text in ax.texts if text.get_text() in ("ns", "*", "**", "***", "****")]
    assert len(stars) == 6 and {text.get_color() for text in stars} == {"#000000", "#D62728"}
    assert ax.get_legend() is not None and [t.get_text() for t in ax.get_legend().get_texts()] == [f"vs {NAMES[0]}", f"vs {NAMES[1]}"]
    top = max(line.get_ydata().max() for line in ax.lines)
    assert top <= ax.get_ylim()[1]  # the axis grew to hold every bracket
    view.set_ref_color(1, "#0000ff")
    assert {line.get_color() for line in view.figure.axes[0].lines} == {"#000000", "#0000ff"}
    assert graph.format["ref_colors"] == ["#000000", "#0000ff"]


def test_asterisks_above_columns_hide_ns_and_p_values(window):
    data = _data(window)
    result = window.analyze("anova", TWO_REFERENCES)
    graph, view = _graph(window, data, result, bracket_mode="columns")
    ax = view.figure.axes[0]
    assert len(ax.lines) == 0
    stars = [text for text in ax.texts if text.get_text() in ("ns", "*", "**", "***", "****")]
    assert len(stars) == 6
    assert sorted(round(text.get_position()[0]) for text in stars) == [0, 1, 2, 2, 3, 3]  # over every compared column, once per reference
    graph.format.update(bracket_mode="brackets", hide_ns=True)
    view.fill()
    significant = [row for row in result.result.values["comparisons"] if row["significant"]]
    assert len(significant) < 6 and len(view.figure.axes[0].lines) == len(significant)
    graph.format.update(hide_ns=False, bracket="p")
    view.fill()
    assert any(text.get_text().startswith("<") or text.get_text()[0].isdigit() for text in view.figure.axes[0].texts)
    graph.format.update(bracket="none")
    view.fill()
    assert len(view.figure.axes[0].lines) == 0


def test_compact_letters_above_columns(window):
    data = _data(window)
    result = window.analyze("anova", ALL_PAIRS)
    graph, view = _graph(window, data, result, letters=True)
    ax = view.figure.axes[0]
    letters = compact_letter_display(result.result)
    assert sorted(text.get_text() for text in ax.texts if text.get_text() in set(letters.values())) == sorted(letters.values())
    assert len(ax.lines) == 0 and view.notes.isHidden()
    assert set("".join(letters.values())) <= set("ABCD")
    graph.format["letter_style"] = "lower"
    view.fill()
    lower = compact_letter_display(result.result, style="lower")
    assert sorted(t.get_text() for t in view.figure.axes[0].texts if t.get_text() in set(lower.values())) == sorted(lower.values())
    assert view.letter_style.toolTip() and graph.format["letter_style"] == "lower"
    only_controls = window.analyze("anova", TWO_REFERENCES)
    graph.result_id = only_controls.id
    view.fill()
    assert "Compact letters need comparisons of every pair" in view.notes.text()
    none = window.analyze("anova", {**ALL_PAIRS, "comparisons": None})
    graph.result_id, graph.format["letters"] = none.id, False
    view.fill()
    assert "no multiple comparisons" in view.notes.text()


def test_graph_panel_controls_have_tooltips(window):
    data = _data(window)
    result = window.analyze("anova", ALL_PAIRS)
    _sheet, view = _graph(window, data, result)
    for widget in (view.bracket_mode, view.hide_ns, view.letters, view.show_outliers, view.comparison):
        assert widget.toolTip()
    view.bracket_mode.setCurrentIndex(view.bracket_mode.findData("columns"))
    view.hide_ns.setChecked(True)
    view.letters.setChecked(True)
    assert view.sheet.format["bracket_mode"] == "columns" and view.sheet.format["hide_ns"] and view.sheet.format["letters"]


def test_graph_with_comparisons_exports(window, tmp_path):
    data = _data(window, outlier=True)
    result = window.analyze("anova", TWO_REFERENCES)
    window.analyze("outliers", {"method": "rout", "q": 0.01})
    graph, view = _graph(window, data, result)
    for kind in ("svg", "png"):
        assert view.export(tmp_path / f"anova.{kind}", kind, 300, 90, 70, "mm").stat().st_size > 1000


# --- Outliers --------------------------------------------------------------------------------------------------


def test_outliers_are_shaded_hollow_and_cleaned_copy_follows(window):
    data = _data(window, outlier=True)
    before = data.table[NAMES[3]].values.copy()
    graph = window.new_graph("scatter")
    view = window.views[graph.id]
    assert _hollow(view.figure.axes[0]) == []
    sheet = window.analyze("outliers", {"method": "grubbs", "alpha": 0.05, "columns": [NAMES[3]]})
    assert sheet.error == "" and sheet.result.values["outliers"][NAMES[3]] == [{"row": 8, "value": 70.0}]
    assert window.project.flags(data.id)[NAMES[3]].tolist() == [False] * 7 + [True] and NAMES[0] not in window.project.flags(data.id)
    # Highlighted in the data grid, hollow on the graph; both at once, with no refresh.
    grid = window.views[data.id]
    assert grid.grid.item(7, 3).background().style() != Qt.NoBrush and grid.grid.item(0, 3).background().style() == Qt.NoBrush
    assert not grid.flagged.isHidden() and "1 value flagged" in grid.flagged.text()
    hollow = _hollow(view.figure.axes[0])
    assert len(hollow) == 1 and np.asarray(hollow[0].get_offsets()).shape == (1, 2)
    graph.format["show_outliers"] = False
    view.fill()
    assert _hollow(view.figure.axes[0]) == []
    # The cleaned table is new, linked, and leaves the original alone.
    sheet_view = window.views[sheet.id]
    assert sheet_view.clean_button.isVisible() and sheet_view.clean_button.toolTip()
    cleaned = window.create_cleaned_table(sheet)
    assert cleaned.name == "Conditions (cleaned)" and cleaned.cleaned_from == data.id and cleaned is not data
    assert np.isnan(cleaned.table[NAMES[3]].values[7]) and np.array_equal(data.table[NAMES[3]].values, before)
    grid.grid.item(7, 3).setText("12")  # fix the original: the cleaned copy follows
    assert np.isfinite(cleaned.table[NAMES[3]].values[7]) and sheet.result.values["n_outliers"] == 0
    assert window.create_cleaned_table(sheet) is None  # nothing flagged now
    window.select(cleaned.id)
    window.stack.currentWidget().grid.item(0, 0).setText("11")  # editing the copy detaches it
    assert cleaned.cleaned_from is None and any("no longer follows" in note for note in cleaned.notes)
    window.change_options(sheet, {"method": "grubbs", "alpha": 0.05, "columns": [NAMES[0]]})
    assert sheet.name.startswith("Outliers (Grubbs") and sheet.error == ""


def test_removing_the_outlier_result_clears_the_marks(window):
    data = _data(window, outlier=True)
    sheet = window.analyze("outliers", {"method": "rout", "q": 0.01})
    assert window.project.flags(data.id)
    window.project.remove(sheet.id)
    assert window.project.flags(data.id) == {} and window.views[data.id].flagged.isHidden()


# --- A stack of P values ---------------------------------------------------------------------------------------


def test_stack_of_p_values(window):
    from cellquant.gui.quantanalysis.results_view import result_text

    sheet = window.analyze("pstack", {"text": "0.001\n0.02\nthird  0.04\n0.5\n", "method": "bky", "alpha": 0.05, "style": "classic"})
    assert sheet.error == "" and sheet.data_id == "" and sheet.name == "Stack of P values"
    assert sheet.result.values["m"] == 4 and "third" in result_text(sheet)
    assert window.item_of(sheet.id).toolTip(0) == "P values pasted into the analysis; no data table."
    window.change_options(sheet, {"text": "0.001\n0.02\n", "method": "holm_sidak", "alpha": 0.05, "style": "classic"})
    assert sheet.result.values["m"] == 2 and sheet.result.options["method"] == "holm_sidak"
    bad = window.analyze("pstack", {"text": "oops", "method": "bky", "alpha": 0.05, "style": "classic"})
    assert "Line 1" in bad.error
    empty = window.analyze("pstack", {"text": "", "method": "bky", "alpha": 0.05, "style": "classic"})
    assert "at least one P value" in empty.error
    too_big = window.analyze("pstack", {"text": "1.5", "method": "bky", "alpha": 0.05, "style": "classic"})
    assert "between 0 and 1" in too_big.error


# --- The suggested analysis for three or more groups ------------------------------------------------------------


def test_suggested_analysis_for_three_groups_is_kruskal_wallis_with_dunn(window):
    data = window.project.add_data(DataSheet("Phenotypes", make_table()))
    window.suggested_analysis(data)
    results = list(window.project.results.values())
    assert [(r.kind, r.options["test"], r.options["method"], r.options["control"]) for r in results] == [("anova", "kruskal_wallis", "dunn", [NAMES[0]])]
    assert results[0].error == "" and [row["reference"] for row in results[0].result.values["comparisons"]] == [NAMES[0]] * 3
    graph = next(iter(window.project.graphs.values()))
    assert graph.result_id == results[0].id
    assert "Dunn" in window.statusBar().currentMessage() and "coming" not in window.statusBar().currentMessage()


# --- Follow-up checks -------------------------------------------------------------------------------------------


def _block_imports(monkeypatch, *prefixes):
    import importlib.util
    import sys

    real_import, real_find = builtins.__import__, importlib.util.find_spec

    def guarded(name, *args, **kwargs):
        if name.split(".")[0] in prefixes:
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *args: None if name.split(".")[0] in prefixes else real_find(name, *args))
    for name in [name for name in sys.modules if name.split(".")[0] in prefixes]:
        monkeypatch.setitem(sys.modules, name, None)


def test_missing_matplotlib_and_statsmodels_show_the_message(qtbot, monkeypatch):
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    _block_imports(monkeypatch, "matplotlib", "statsmodels")
    window = QuantAnalysisWindow()
    qtbot.addWidget(window)
    window.show()
    assert "matplotlib and statsmodels are not installed" in window.banner.text() and 'pip install "cellquant[stats]"' in window.banner.text()
    data = window.project.add_data(DataSheet("Conditions", make_table(outlier=True)))
    window.select(data.id)
    graph = window.new_graph("scatter")
    view = window.stack.currentWidget()
    assert view.figure is None and "matplotlib" in view.missing.text() and "cellquant[stats]" in view.missing.text()
    window.export_graph()  # a message in the status bar, not a crash
    assert "matplotlib" in window.statusBar().currentMessage()
    with pytest.raises(CellQuantError, match="matplotlib"):
        view.export("graph.svg", "svg")
    assert view.draw() == [] and graph is not None
    # Everything that does not need them still runs.
    assert window.analyze("normality").error == "" and window.analyze("colstats").error == ""
    assert window.analyze("anova", {**ALL_PAIRS, "test": "kruskal_wallis", "method": "dunn"}).error == ""
    assert window.analyze("outliers", {"method": "rout", "q": 0.01}).error == ""
    assert window.analyze("pstack", {"text": "0.01\n0.03", "method": "bky", "alpha": 0.05, "style": "classic"}).error == ""
    blank = ColumnTable.from_columns({"A": [1, 2, 3, np.nan], "B": [2, 3, 4, 5], "C": [3, 5, 4, 6]}, paired=True)
    window.select(window.project.add_data(DataSheet("Blank", blank)).id)
    mixed = window.analyze("anova", {**ALL_PAIRS, "test": "repeated_measures", "method": "tukey"})
    assert "statsmodels" in mixed.error  # the sheet says what is missing; the window keeps running


def test_open_quantanalysis_reports_a_missing_library(monkeypatch):
    pytest.importorskip("napari")
    from types import SimpleNamespace

    from cellquant.gui import app as app_module
    from cellquant.gui import quantanalysis

    shown = []

    def broken(*args, **kwargs):
        raise ImportError("No module named 'matplotlib'")

    monkeypatch.setattr(quantanalysis, "open_window", broken)
    shell = SimpleNamespace(viewer=SimpleNamespace(window=SimpleNamespace(_qt_window=None)), controller=None, is_busy=lambda: False,
                            _quantanalysis=None, show_error=shown.append)
    assert app_module.CellQuantWindow.open_quantanalysis(shell) is None
    assert len(shown) == 1 and 'pip install "cellquant[stats]"' in shown[0] and "QuantAnalysis could not start" in shown[0]


def test_opening_twice_reuses_the_window_and_a_deleted_one_is_replaced(qtbot):
    from qtpy.QtWidgets import QWidget

    from cellquant.gui.quantanalysis import open_window

    parent = QWidget()
    qtbot.addWidget(parent)
    first = open_window(parent)
    assert first.isVisible() and open_window(parent, existing=first) is first
    first.close()
    assert not first.isVisible()
    assert open_window(parent, existing=first) is first and first.isVisible()  # closed, then opened again: the same window
    # The window's C++ object is gone (for example its parent was deleted): opening again makes a new one instead of failing.
    try:
        from shiboken6 import delete
    except ImportError:
        try:
            from PyQt6.sip import delete
        except ImportError:
            pytest.skip("no way to delete the C++ object (shiboken6 or PyQt6.sip)")
    delete(first)
    second = open_window(parent, existing=first)
    assert second is not first and second.isVisible()
    second.close()


def test_closing_quantanalysis_leaves_cellquant_running(tmp_path):
    napari = pytest.importorskip("napari")
    from qtpy.QtWidgets import QApplication

    from cellquant.gui import app as app_module
    from tests.test_gui_plan import _settle, _wait
    from tests.test_plan import _experiment

    controller = _experiment(tmp_path)
    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"napari viewer could not start: {exc}")
    shell = app_module.CellQuantWindow(viewer, controller.directory)
    _wait(shell)
    quits = []
    application = QApplication.instance()
    application.aboutToQuit.connect(lambda: quits.append(True))
    main = viewer.window._qt_window
    main.show()
    try:
        window = shell.open_quantanalysis(experiment=False)
        _settle()
        assert window.isVisible() and main.isVisible()
        window.close()
        _settle()
        assert not window.isVisible()
        assert main.isVisible() and not quits and not application.closingDown()  # CellQuant keeps running
        assert shell.controller is controller or shell.controller is not None
        again = shell.open_quantanalysis(experiment=False)
        _settle()
        assert again is window and window.isVisible() and shell._quantanalysis is window  # and Analyze… again reuses it
        window.close()
        _settle()
        assert main.isVisible() and not quits
    finally:
        viewer.close()
        _wait(shell)
