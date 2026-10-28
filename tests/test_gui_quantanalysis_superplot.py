"""QuantAnalysis SuperPlots from any table: the Replicates column, pasting, CSV import with a replicate column, the
SuperPlot's format options and projects. Needs Qt (xvfb-run on Linux) and matplotlib."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("matplotlib")

BLOCK = ("Replicate\tControl\tTreated\n"
         "Exp 1\t1\t4\n\t2\t5\n\t3\t\n"
         "Exp 2\t10\t30\n\t11\t31\n\t\t32\n"
         "Exp 3\t20\t40\n")


@pytest.fixture
def window(qtbot):
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    window = QuantAnalysisWindow()
    qtbot.addWidget(window)
    window.resize(1400, 900)
    window.show()
    return window


def _pasted(window):
    sheet = window.new_table()
    view = window.stack.currentWidget()
    view.grid.setCurrentCell(0, 0)
    view.paste(BLOCK)
    return sheet, view


def test_pasting_replicate_names_turns_replicates_on(window):
    sheet, view = _pasted(window)
    assert sheet.replicates is not None and view.offset == 1 and view.replicate_button.isChecked()
    assert sheet.replicates.replicates() == ["Exp 1", "Exp 2", "Exp 3"] and sheet.row_labels == ["Exp 1", "Exp 2", "Exp 3"]
    assert sheet.table.names == ["Control", "Treated"]
    np.testing.assert_allclose(sheet.table["Control"].values, [2, 10.5, 20])
    assert view.summary.isVisible() and "3 biological replicates" in view.replicate_note.text()
    assert view.grid.horizontalHeaderItem(0).text() == "Replicate" and view.grid.item(3, 0).text() == "Exp 2"

    result = window.analyze("compare", {"test": "welch_t", "first": 0, "second": 1, "alternative": "two-sided", "ci": 0.95,
                                        "alpha": 0.05, "style": "classic"})
    assert result.error == "" and result.result is not None  # on the replicate means: N = 3 per column
    assert result.methods.startswith("Analyzed on one value per biological replicate") and "Control N=3, n=6" in result.methods
    view = window.views[sheet.id]
    from qtpy.QtWidgets import QTableWidgetItem

    view.grid.setItem(5, 1, QTableWidgetItem("12"))  # a value of Exp 2: its mean changes, and so do the results
    np.testing.assert_allclose(sheet.table["Control"].values, [2, 11, 20])
    view.grid.setItem(5, 0, QTableWidgetItem("Exp 4"))  # a new name starts a new replicate
    assert sheet.replicates.replicates() == ["Exp 1", "Exp 2", "Exp 4", "Exp 3"]
    view.summary.setCurrentIndex(view.summary.findData("median"))
    assert sheet.replicates.summary == "median"
    view.grid.setCurrentCell(0, 0)
    view.remove_column()
    assert sheet.table.names == ["Control", "Treated"]  # the Replicate column is not removed as a data column


def test_pasting_replicate_names_without_titles(window):
    sheet = window.new_table()
    view = window.stack.currentWidget()
    view.grid.setCurrentCell(0, 0)
    view.paste("Exp 1\t1\t4\n\t2\t5\nExp 2\t10\t30")
    assert sheet.replicates is not None and sheet.replicates.labels == ["Exp 1", "", "Exp 2"]
    assert sheet.table.names == ["Group A", "Group B"]
    np.testing.assert_allclose(sheet.table["Group A"].values, [1.5, 10])


def test_replicates_button_and_typing(window, monkeypatch):
    from qtpy.QtWidgets import QMessageBox, QTableWidgetItem

    sheet = window.new_table()
    view = window.stack.currentWidget()
    view.grid.setCurrentCell(0, 0)
    view.paste("Control\tTreated\n1\t4\n2\t5\n10\t30")
    assert sheet.replicates is None and sheet.table["Control"].values.size == 3
    view.replicate_button.click()
    assert sheet.replicates is not None and view.offset == 1 and sheet.replicates.labels == ["Replicate 1"]
    np.testing.assert_allclose(sheet.table["Control"].values, [13 / 3])  # every value in one replicate until names are typed
    view.grid.setItem(0, 0, QTableWidgetItem("A"))
    view.grid.setItem(2, 0, QTableWidgetItem("B"))
    np.testing.assert_allclose(sheet.table["Treated"].values, [4.5, 30])
    view._rename(0)  # the Replicate title is not renamed
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *args, **kwargs: QMessageBox.No))
    view.replicate_button.click()
    assert sheet.replicates is not None and view.replicate_button.isChecked()
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *args, **kwargs: QMessageBox.Yes))
    view.replicate_button.click()
    assert sheet.replicates is None and view.offset == 0
    np.testing.assert_allclose(sheet.table["Control"].values, [1, 2, 10])  # every value again


def test_superplot_graph_and_format(window, qtbot):
    from cellquant.gui.quantanalysis import graphs
    from cellquant.gui.quantanalysis.graph_view import GraphSheetView

    sheet, _view = _pasted(window)
    graph = window.new_graph("superplot")
    assert graph.format.legend.show
    view = window.stack.currentWidget()
    assert isinstance(view, GraphSheetView)
    view.flush()
    ax = view.figure.axes[0]
    assert "SuperPlot needs" not in view.notes.text()
    small = ax.collections[0]
    assert len(small.get_offsets()) == 6  # the six Control values
    legend = view.figure._cq_legend
    assert [text.get_text() for text in legend.get_texts()] == ["Exp 1", "Exp 2", "Exp 3"]
    labels = [label.get_text() for label in ax.get_xticklabels()]
    assert "N=3\nn=6" in labels[0] and "N=3\nn=6" in labels[1]

    section = view.panel["graph"]
    assert section.replicate_row is not None
    fmt = graph.format
    fmt.replicate_shapes, fmt.replicate_layout, fmt.replicate_link, fmt.replicate_colors = True, "lanes", True, "greys"
    section.fill()
    assert section.replicate_shapes.isChecked() and section.replicate_layout.currentData() == "lanes"
    view.draw()
    ax = view.figure.axes[0]
    assert any(line.get_gid() is None and len(line.get_xdata()) == 2 for line in ax.lines)  # each replicate joined across columns
    lanes = ax.collections[0].get_offsets()[:, 0]
    assert len(set(np.round(lanes, 1))) >= 3  # three lanes in the Control column

    from matplotlib.figure import Figure

    from cellquant.gui.quantanalysis.project import GraphSheet

    from cellquant.stats import ColumnTable

    plain = window.new_table()  # a table without replicates: drawn as a column scatter, and the note says how to add them
    plain.table = ColumnTable.from_columns({"A": [1, 2, 3], "B": [2, 3, 4]})
    notes = graphs.draw(Figure(), plain, GraphSheet("g", plain.id, "superplot"))
    assert any("click Replicates" in note for note in notes)


def test_csv_with_replicate_column_and_project(window, tmp_path):
    from cellquant.gui.quantanalysis import project_file
    from cellquant.gui.quantanalysis.project import Project

    path = tmp_path / "cells.csv"
    path.write_text("condition,experiment,area\nctrl,1,10\nctrl,1,12\ntreat,1,20\nctrl,2,11\ntreat,2,25\ntreat,2,27\n")
    sheet = window.import_csv(str(path), shape="long", value="area", group="condition", replicate="experiment")
    assert sheet.replicates is not None and sheet.replicates.replicates() == ["1", "2"]
    np.testing.assert_allclose(sheet.table["treat"].values, [20, 26])
    window.suggested_analysis(sheet)
    assert any(graph.kind == "superplot" for graph in window.project.graphs.values())

    sheet.replicates.summary = "median"
    target = tmp_path / "p.cqstats"
    project_file.save(window.project, target)
    project = Project()
    project_file.load(target, project)
    loaded = project.data[sheet.id]
    assert loaded.replicates is not None and loaded.replicates.labels == sheet.replicates.labels
    assert loaded.replicates.summary == "median"
    np.testing.assert_allclose(loaded.table["ctrl"].values, sheet.table["ctrl"].values)
