"""QuantAnalysis ROC curves and Bland-Altman agreement: the Analyze menu, dialogs, results sheets, graphs and projects.
Needs Qt (xvfb-run on Linux) and matplotlib."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("matplotlib")

CONTROLS = [4.1, 5.2, 3.8, 6.0, 5.5, 4.7, 5.9, 6.4, 4.4, 5.0]
PATIENTS = [6.2, 7.5, 5.8, 8.1, 6.9, 7.2, 5.4, 8.8, 7.7, 6.5]


@pytest.fixture
def window(qtbot):
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    window = QuantAnalysisWindow()
    qtbot.addWidget(window)
    window.resize(1400, 900)
    window.show()
    return window


@pytest.fixture
def table(window):
    from cellquant.stats import ColumnTable

    sheet = window.new_table()
    sheet.table = ColumnTable.from_columns({"Healthy": CONTROLS, "Disease": PATIENTS, "Third": CONTROLS[::-1]}, title="Marker")
    window.project.data_changed(sheet.id)
    window.select(sheet.id)
    return sheet


def _artists(ax):
    return [line for line in ax.get_lines() if len(set(line.get_ydata())) == 1 and len(line.get_ydata()) == 2 and len(set(line.get_xdata())) == 2]


def test_roc_analysis_dialog_and_change_options(window, table):
    from cellquant.gui.quantanalysis.dialogs import ROC_DEFAULTS, RocDialog
    from cellquant.gui.quantanalysis.results_view import ResultSheetView, result_rows

    dialog = RocDialog(window, table.table, {"controls": "Disease", "patients": "Disease", "direction": "<", "ci": 0.99})
    assert dialog.options()["controls"] == "Disease" and dialog.options()["patients"] == "Healthy"  # the same column is moved off
    assert dialog.options()["direction"] == "<" and dialog.options()["ci"] == 0.99
    assert [dialog.direction.itemData(i) for i in range(3)] == ["auto", ">", "<"]
    dialog.patients.setCurrentIndex(dialog.controls.currentIndex())
    dialog.accept()
    assert dialog.result() == 0 and "different columns" in dialog.note.text()
    assert dialog.minimumSizeHint().width() <= 800 and dialog.sizeHint().height() <= 600

    window.select(table.id)
    result = window.analyze("roc", {**ROC_DEFAULTS, "controls": "Healthy", "patients": "Disease"})
    assert result.error == "" and result.name == f"ROC curve: {table.name}" and result.kind == "roc"
    assert result.wide is result.result.values["wide"] and result.methods and 0.5 < result.result.values["auc"] <= 1
    assert result.result.options["names"] == ("Healthy", "Disease")
    headers, rows = result_rows(result)
    assert ("Area under the ROC curve", None) in rows
    view = window.stack.currentWidget()
    assert isinstance(view, ResultSheetView) and view.options_button.isVisibleTo(view)

    window.change_options(result, {"controls": "Healthy", "patients": "Third", "direction": ">", "ci": 0.9, "alpha": 0.01, "style": "APA"})
    assert result.error == "" and result.result.options["names"] == ("Healthy", "Third") and result.result.values["direction"] == ">"
    assert result.result.options["ci"] == 0.9
    window.change_options(result, {"controls": "Gone", "patients": "Disease"})
    assert "gone" in result.error
    window.change_options(result, {"controls": 7, "patients": object(), "direction": 3, "ci": "wide", "alpha": None})  # garbage: defaults
    assert result.error == "" and result.result.options["names"] == ("Healthy", "Disease")


def test_bland_altman_analysis_dialog_and_change_options(window, table):
    from cellquant.gui.quantanalysis.dialogs import BLAND_ALTMAN_DEFAULTS, BlandAltmanDialog

    dialog = BlandAltmanDialog(window, table.table, {"method": "percent", "a": "Third", "b": "Third"})
    assert dialog.options() == {"a": "Third", "b": "Healthy", "method": "percent", "ci": 0.95}
    assert [dialog.method.itemText(i) for i in range(3)] == ["Difference (A − B)", "Ratio (A / B)", "% difference"]
    dialog.second.setCurrentIndex(dialog.first.currentIndex())
    dialog.accept()
    assert dialog.result() == 0 and "different columns" in dialog.note.text()
    dialog.second.setCurrentIndex(0)
    dialog.accept()
    assert dialog.result() == 1

    result = window.analyze("blandaltman", {**BLAND_ALTMAN_DEFAULTS, "a": "Healthy", "b": "Disease"})
    assert result.error == "" and result.name == f"Bland-Altman: {table.name}" and result.kind == "blandaltman"
    assert result.wide is result.result.values["wide"] and result.result.values["bias"] == pytest.approx(np.mean(np.array(CONTROLS) - PATIENTS))
    assert window.stack.currentWidget().options_button.isVisibleTo(window.stack.currentWidget())
    window.change_options(result, {"a": "Disease", "b": "Healthy", "method": "ratio", "ci": 0.99})
    assert result.error == "" and result.result.options["method"] == "ratio" and result.result.values["bias"] > 1
    window.change_options(result, {"a": "Healthy", "b": "Disease", "method": "bogus"})  # a damaged option the analysis itself refuses
    assert "method" in result.error
    window.change_options(result, {"a": None, "b": 99, "method": 5, "ci": "x"})
    assert result.error == "" and result.result.options["method"] == "difference"


def test_menu_actions_run_the_dialogs(window, table, monkeypatch):
    from qtpy.QtWidgets import QMenu

    from cellquant.gui.quantanalysis import dialogs

    monkeypatch.setattr(dialogs.RocDialog, "exec", lambda self: 1)
    monkeypatch.setattr(dialogs.BlandAltmanDialog, "exec", lambda self: 1)
    window.select(table.id)
    actions = {action.text(): action for menu in window.menuBar().findChildren(QMenu) for action in menu.actions()}
    for name, kind in (("ROC curve…", "roc"), ("Bland-Altman…", "blandaltman")):
        assert actions[name].toolTip() and "column" in actions[name].toolTip()
        actions[name].trigger()
        sheet = window.project.sheet(window.current_id())
        assert sheet.kind == kind and sheet.error == "" and sheet.result is not None


def test_refusals(window, table):
    from cellquant.gui.quantanalysis.project import set_contingency
    from cellquant.stats import ColumnTable
    from cellquant.stats.contingency import ContingencyTable

    count = len(window.project.results)
    counts = window.new_contingency_table()
    set_contingency(counts, ContingencyTable("", ["A", "B"], ["Yes", "No"], [[5, 3], [2, 6]]))
    window.select(counts.id)
    for kind in ("roc", "blandaltman"):
        assert window.analyze(kind, {}) is None and "contingency table" in window.statusBar().currentMessage()
    xy = window.new_xy_table()
    window.select(xy.id)
    for kind in ("roc", "blandaltman"):
        assert window.analyze(kind, {}) is None and "XY table" in window.statusBar().currentMessage()
    grouped = window.new_grouped_table()
    window.select(grouped.id)
    for kind in ("roc", "blandaltman"):
        assert window.analyze(kind, {}) is None and "grouped table" in window.statusBar().currentMessage()
    one = window.new_table()
    one.table = ColumnTable.from_columns({"Only": [1.0, 2.0, 3.0]})
    window.project.data_changed(one.id)
    window.select(one.id)
    for kind in ("roc", "blandaltman"):
        assert window.analyze(kind) is None and "at least two columns" in window.statusBar().currentMessage()
    assert len(window.project.results) == count


def test_unequal_columns_leave_out_the_unpaired_rows(window):
    from cellquant.stats import ColumnTable

    sheet = window.new_table()
    sheet.table = ColumnTable.from_columns({"A": [1.0, 2.0, 3.0], "B": [1.0, 2.0]})
    window.project.data_changed(sheet.id)
    window.select(sheet.id)
    result = window.analyze("blandaltman", {"a": "A", "b": "B"})
    assert result.error == "" and result.result.values["n"] == 2 and any("1 pair with a missing value" in text for text in result.warnings)


def test_roc_graph(window, table):
    from cellquant.gui.quantanalysis.dialogs import ROC_DEFAULTS
    from cellquant.gui.quantanalysis.graph_view import GraphSheetView

    window.select(table.id)
    column = window.new_graph("scatter")
    result = window.analyze("roc", {**ROC_DEFAULTS, "controls": "Healthy", "patients": "Disease"})
    assert column.result_id is None  # the table's column graph stays a column graph
    graph = next(item for item in window.project.graphs.values() if item.result_id == result.id)  # the analysis made its own graph
    assert graph.name == f"{result.name} (graph)"
    window.select(graph.id)
    view = window.stack.currentWidget()
    assert isinstance(view, GraphSheetView)
    ax = view.figure._cq_axes["main"]
    curve = result.result.values["curve"]
    steps = [line for line in ax.get_lines() if len(line.get_xdata()) == len(curve)]
    assert len(steps) == 1 and np.allclose(steps[0].get_xdata(), curve["fpr_percent"]) and np.allclose(steps[0].get_ydata(), curve["sensitivity"])
    diagonal = [line for line in ax.get_lines() if list(line.get_xdata()) == [0, 100] and list(line.get_ydata()) == [0, 100]]
    assert len(diagonal) == 1 and diagonal[0].get_linestyle() == ":"
    assert ax.get_xlim() == (0, 100) and ax.get_ylim() == (0, 100)
    assert ax.get_ylabel() == "Sensitivity (%)" and ax.get_xlabel() == "100% − Specificity (%)"
    title = ax.get_title(loc="left")
    assert title.startswith(f"Area = {result.result.values['auc']:.2f}") and "P " in title

    graph.format["y"] = {"title": "True positives"}
    graph.format["title"] = "Marker"
    window.project.changed.emit(graph.id)
    view.flush()
    ax = view.figure._cq_axes["main"]
    assert ax.get_ylabel() == "True positives" and ax.get_title(loc="left").startswith("Marker\nArea = ")

    window.select(table.id)
    later = window.new_graph("bar")  # a new column graph is not an ROC curve
    assert later.result_id is None


def test_bland_altman_graph(window, table):
    from cellquant.gui.quantanalysis.dialogs import BLAND_ALTMAN_DEFAULTS
    from cellquant.gui.quantanalysis.graph_view import GraphSheetView

    result = window.analyze("blandaltman", {**BLAND_ALTMAN_DEFAULTS, "a": "Healthy", "b": "Disease"})
    graph = next(item for item in window.project.graphs.values() if item.result_id == result.id)
    window.select(graph.id)
    view = window.stack.currentWidget()
    assert isinstance(view, GraphSheetView)
    ax = view.figure._cq_axes["main"]
    values = result.result.values
    heights = sorted(float(line.get_ydata()[0]) for line in _artists(ax))
    assert heights == pytest.approx(sorted([values["low"], values["bias"], values["high"]]))
    by_height = {round(float(line.get_ydata()[0]), 9): line for line in _artists(ax)}
    assert by_height[round(values["bias"], 9)].get_linestyle() == "-"
    assert by_height[round(values["low"], 9)].get_linestyle() == "--" and by_height[round(values["high"], 9)].get_linestyle() == "--"
    points = ax.collections[0].get_offsets()
    assert len(points) == len(CONTROLS) and np.allclose(points[:, 0], values["average"]) and np.allclose(points[:, 1], values["measure"])
    labels = [text.get_text() for text in ax.texts]
    assert len(labels) == 3 and any(label.startswith("Bias ") for label in labels) and any("limit" in label for label in labels)
    assert ax.get_xlabel() == "Average of Healthy and Disease" and ax.get_ylabel() == "Healthy − Disease"

    window.change_options(result, {"a": "Healthy", "b": "Disease", "method": "percent", "ci": 0.95})
    view.flush()
    assert view.figure._cq_axes["main"].get_ylabel() == "% difference (Healthy − Disease)"
    section = view.panel["graph"]
    assert section.comparison.findData(result.id) >= 0


def test_project_round_trip(window, table, tmp_path):
    from cellquant.gui.quantanalysis import project_file
    from cellquant.gui.quantanalysis.dialogs import BLAND_ALTMAN_DEFAULTS, ROC_DEFAULTS
    from cellquant.gui.quantanalysis.project import Project

    roc = window.analyze("roc", {**ROC_DEFAULTS, "controls": "Healthy", "patients": "Disease", "direction": ">"})
    agreement = window.analyze("blandaltman", {**BLAND_ALTMAN_DEFAULTS, "a": "Healthy", "b": "Disease", "method": "ratio"})
    graph = next(item for item in window.project.graphs.values() if item.result_id == agreement.id)
    path = project_file.save(window.project, tmp_path / "diagnostic.cqstats")
    other = Project()
    _current, notes = project_file.load(path, other)
    assert notes == []
    assert other.results[roc.id].kind == "roc" and other.results[roc.id].result.values["auc"] == pytest.approx(roc.result.values["auc"])
    assert other.results[roc.id].options["direction"] == ">" and other.results[roc.id].wide is not None
    assert other.results[agreement.id].result.values["bias"] == pytest.approx(agreement.result.values["bias"])
    assert other.results[agreement.id].options["method"] == "ratio" and other.graphs[graph.id].result_id == agreement.id
    other.results[roc.id].options.update(ci="wide", direction=5, controls=None)  # a damaged file still runs
    other.rerun(other.results[roc.id])
    assert other.results[roc.id].error == ""
    other.results[agreement.id].options.update(a=-3, method=None, ci=[])
    other.rerun(other.results[agreement.id])
    assert other.results[agreement.id].error == ""
