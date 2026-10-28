"""QuantAnalysis contingency tables: typing and pasting counts, the analysis sheet, bar graphs and projects.
Needs Qt (xvfb-run on Linux) and matplotlib."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("matplotlib")


@pytest.fixture
def window(qtbot):
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    window = QuantAnalysisWindow()
    qtbot.addWidget(window)
    window.resize(1400, 900)
    window.show()
    return window


def test_table_edits_and_refusals(window):
    from qtpy.QtWidgets import QTableWidgetItem

    from cellquant.gui.quantanalysis.contingency_view import ContingencySheetView

    sheet = window.new_contingency_table()
    view = window.stack.currentWidget()
    assert isinstance(view, ContingencySheetView) and sheet.contingency.counts.shape == (2, 2)
    assert view.warnings.isVisibleTo(view) and "blank" in view.warnings.text()  # a new table asks for every count
    view.grid.setCurrentCell(0, 0)
    view.paste("28\t12\n15\t25\n9\t30")
    table = sheet.contingency
    assert table.rows == ["Group A", "Group B", "Group 3"] and table.counts.tolist() == [[28, 12], [15, 25], [9, 30]]
    assert not view.warnings.isVisibleTo(view) and "Total: 119" in view.totals.text()
    view._rename("row", 0, "Treated")
    view._rename("column", 1, "Outcome")  # a repeated title is refused
    view._rename("column", 0, "Responded")
    assert sheet.contingency.rows[0] == "Treated" and sheet.contingency.columns == ["Responded", "No outcome"]
    assert sheet.table.names == ["Responded", "No outcome"]
    view.grid.setItem(0, 1, QTableWidgetItem("2.5"))  # not a whole number: warned, kept so it can be fixed
    assert "whole numbers" in view.warnings.text()
    view.grid.setItem(0, 1, QTableWidgetItem("12"))
    view.add_column()
    view.grid.setCurrentCell(0, 2)
    view.remove_column()
    view.grid.setCurrentCell(2, 0)
    view.remove_row()
    assert sheet.contingency.counts.shape == (2, 2)
    view.grid.setCurrentCell(0, 0)
    view.remove_row()  # keeps two rows
    assert sheet.contingency.counts.shape == (2, 2)
    assert window.analyze("compare") is None and "Contingency table" in window.statusBar().currentMessage()
    assert window.process_data("transform", {}) is None
    window.new_table()
    assert window.analyze("contingency", {}) is None and "contingency table" in window.statusBar().currentMessage()


def test_analysis_graph_and_project(window, tmp_path):
    from cellquant.gui.quantanalysis import project_file
    from cellquant.gui.quantanalysis.dialogs import CONTINGENCY_DEFAULTS, ContingencyDialog
    from cellquant.gui.quantanalysis.graph_view import GraphSheetView
    from cellquant.gui.quantanalysis.project import Project, set_contingency
    from cellquant.gui.quantanalysis.results_view import ResultSheetView, result_rows
    from cellquant.stats.contingency import ContingencyTable

    sheet = window.new_contingency_table()
    set_contingency(sheet, ContingencyTable("Response", ["Treated", "Control"], ["Responded", "Did not"], [[28, 12], [15, 25]]))
    window.project.data_changed(sheet.id)
    window.views.pop(sheet.id, None)
    window.select(sheet.id)
    dialog = ContingencyDialog(window, sheet.contingency, {"diagnostic": True})
    assert dialog.options()["diagnostic"] and not dialog.yates.isEnabled() and not dialog.trend.isEnabled()  # Yates only with chi-square
    dialog.test.setCurrentIndex(dialog.test.findData("chi2"))
    assert dialog.yates.isEnabled() and [dialog.group.itemText(i) for i in range(2)] == ["Treated", "Control"]
    dialog.group.setCurrentIndex(1)
    assert dialog.options()["group"] == 1 and dialog.options()["outcome"] == 0
    result = window.analyze("contingency", dict(CONTINGENCY_DEFAULTS))
    assert result.error == "" and result.name == f"Contingency: {sheet.name}"
    headers, rows = result_rows(result)
    assert headers == ["", "Value"] and ("Effect size", None) in rows
    assert isinstance(window.stack.currentWidget(), ResultSheetView) and window.stack.currentWidget().options_button.isVisible()

    window.select(sheet.id)
    graph = window.new_graph("bar")
    assert graph.result_id == result.id and graph.format.legend.show
    view = window.stack.currentWidget()
    assert isinstance(view, GraphSheetView) and view.panel["graph"].contingency_values.isVisibleTo(view)
    assert view.panel["datasets"].names() == ["Responded", "Did not"]
    ax = view.figure._cq_axes["main"]
    bars = [patch for patch in ax.patches if getattr(patch, "_cq_target", (None,))[0] == "dataset"]
    assert len(bars) == 4 and bars[0].get_height() == pytest.approx(70) and ax.get_title(loc="left").startswith("P = ")
    assert [label.get_text() for label in ax.get_xticklabels()] == ["Treated\n(N = 40)", "Control\n(N = 40)"]
    section = view.panel["graph"]
    section.contingency_values.setCurrentIndex(section.contingency_values.findData("count"))
    view.flush()
    ax = view.figure._cq_axes["main"]
    heights = [patch.get_height() for patch in ax.patches if getattr(patch, "_cq_target", (None,))[0] == "dataset"]
    assert sorted(heights) == [12, 15, 25, 28] and ax.get_ylabel() == "Subjects"

    path = project_file.save(window.project, tmp_path / "counts.cqstats")
    other = Project()
    _current, notes = project_file.load(path, other)
    loaded = other.data[sheet.id]
    assert notes == [] and loaded.contingency.rows == ["Treated", "Control"] and np.array_equal(loaded.contingency.counts, sheet.contingency.counts)
    assert other.results[result.id].result.values["p"] == pytest.approx(result.result.values["p"])
    assert other.graphs[graph.id].format.contingency_values == "count"
    other.results[result.id].options.update(ci="wide", yates="yes", test=3)  # a damaged file still runs
    other.rerun(other.results[result.id])
    assert other.results[result.id].error == ""
