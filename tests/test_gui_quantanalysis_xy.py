"""QuantAnalysis XY tables: typing and pasting, linear regression and correlation sheets, XY graphs with fitted
lines and bands, and projects. Needs Qt (xvfb-run on Linux) and matplotlib."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("matplotlib")

from tests.test_stats_xy import VALUES, X  # noqa: E402


@pytest.fixture
def window(qtbot):
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    window = QuantAnalysisWindow()
    qtbot.addWidget(window)
    window.resize(1400, 900)
    window.show()
    return window


def _filled(window):
    from cellquant.gui.quantanalysis.project import set_xy
    from cellquant.stats.xy import XYTable

    sheet = window.new_xy_table()
    set_xy(sheet, XYTable("Response", "Dose", X, ["A", "B"], VALUES))
    window.project.data_changed(sheet.id)
    window.views.pop(sheet.id, None)
    window.select(sheet.id)
    return sheet


def test_xy_table_edits(window):
    from cellquant.gui.quantanalysis.xy_view import XYSheetView

    sheet = window.new_xy_table()
    view = window.stack.currentWidget()
    assert isinstance(view, XYSheetView) and sheet.xy.values.shape == (5, 1, 3)
    view.grid.setCurrentCell(0, 0)
    view.paste("0\t1\t1.2\n1\t2\t2.1\t2.2\n2\t3\n3\t4\t4.1\t3.9\t4.2\t9")  # the last value makes a second data set
    xy = sheet.xy
    assert xy.x[:4].tolist() == [0, 1, 2, 3] and xy.values.shape == (5, 2, 3) and xy.values[3, 1, 1] == 9
    assert xy.sets == ["Data set A", "Data set 2"] and sheet.table.names == ["X", "Data set A", "Data set 2"]
    view._rename(0, "Dose (µM)")
    view._rename(1, "Control")
    view._rename(4, "Control")  # a repeated name is refused
    assert xy.x_title != sheet.xy.x_title and sheet.xy.x_title == "Dose (µM)" and sheet.xy.sets == ["Control", "Data set 2"]
    view.add_row()
    view.add_replicate()
    assert sheet.xy.values.shape == (6, 2, 4)
    view.grid.setCurrentCell(0, 5)
    view.remove_data_set()
    view.grid.setCurrentCell(5, 0)
    view.remove_row()
    assert sheet.xy.values.shape == (5, 1, 4) and sheet.xy.sets == ["Control"]
    from qtpy.QtWidgets import QTableWidgetItem

    view.grid.setItem(4, 1, QTableWidgetItem("7"))  # a Y with no X is counted, not analyzed
    assert view.warnings.isVisible() and "no X" in view.warnings.text()
    assert window.analyze("compare") is None and "XY table" in window.statusBar().currentMessage()
    assert window.process_data("transform", {}) is None


def test_regression_correlation_graph_and_project(window, tmp_path):
    from cellquant.gui.quantanalysis import graphs, project_file
    from cellquant.gui.quantanalysis.graph_view import GraphSheetView
    from cellquant.gui.quantanalysis.project import Project
    from cellquant.gui.quantanalysis.results_view import ResultSheetView, result_rows

    sheet = _filled(window)
    linreg = window.analyze("linreg", {"through_origin": False, "replicates": "each", "ci": 0.95, "alpha": 0.05, "style": "classic"})
    assert linreg.error == "" and linreg.name == f"Linear regression: {sheet.name}"
    headers, rows = result_rows(linreg)
    assert headers == ["", "A", "B"] and ("Best-fit values", None) in rows
    assert isinstance(window.stack.currentWidget(), ResultSheetView) and window.stack.currentWidget().options_button.isVisible()
    corr = window.analyze("xycorr", {"method": "spearman", "replicates": "mean", "ci": 0.95, "alpha": 0.05, "style": "classic"})
    assert corr.name == f"Correlation (Spearman): {sheet.name}" and "Spearman" in corr.methods

    window.select(sheet.id)
    graph = window.new_graph("xy")
    assert graph.result_id == linreg.id and graph.format.legend.show
    view = window.stack.currentWidget()
    assert isinstance(view, GraphSheetView)
    section = view.panel["graph"]
    assert section.xy_replicates.isVisibleTo(view) and not section.arrangement.isVisibleTo(view)
    assert view.panel["datasets"].names() == ["A", "B"]  # formats and colours follow the data sets
    section.xy_band.setCurrentIndex(section.xy_band.findData("confidence"))
    view.flush()
    ax = view.figure._cq_axes["main"]
    assert len(ax.collections) >= 4 and ax.get_xlabel() == "Dose" and ax.get_ylabel() == "Response"
    lines = [line for line in ax.lines if getattr(line, "_cq_target", None) == ("dataset", "A") and len(line.get_xdata()) == 200]
    fit = linreg.result.values["fits"]["A"]
    assert len(lines) == 1 and lines[0].get_ydata()[0] == pytest.approx(fit["intercept"] + fit["slope"] * X.min())

    # Editing the table recomputes the regression and redraws.
    xy = sheet.xy
    xy.values[4, 0] = [20.0, 21.0, 19.0]
    window.project.data_changed(sheet.id)
    assert linreg.result.values["fits"]["A"]["slope"] > fit["slope"]

    path = project_file.save(window.project, tmp_path / "xy.cqstats")
    other = Project()
    _current, notes = project_file.load(path, other)
    loaded = other.data[sheet.id]
    assert notes == [] and loaded.xy.sets == ["A", "B"] and loaded.xy.x_title == "Dose"
    assert np.array_equal(loaded.xy.values, sheet.xy.values, equal_nan=True)
    assert other.results[linreg.id].result.values["fits"]["A"]["slope"] == pytest.approx(linreg.result.values["fits"]["A"]["slope"])
    assert other.graphs[graph.id].format.xy_band == "confidence"

    # A column graph kind on an XY table draws the XY graph and says so.
    from matplotlib.figure import Figure

    graph.kind = "bar"
    notes = graphs.draw(Figure(), sheet, graph, linreg.result)
    assert "drawn as an XY graph" in notes[0]
