"""QuantAnalysis window workflow: closing with unsaved work and autosave recovery, the import dialog's suggested-analysis checkbox, the Analyze
menu greying what cannot run, the Edit menu (rename, delete), data table export and where exports start, and the project defaults.
Needs Qt (xvfb-run on Linux) and matplotlib."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("matplotlib")

from qtpy.QtCore import Qt  # noqa: E402

from cellquant.stats import sources  # noqa: E402
from tests.test_stats_import import make_results  # noqa: E402

SPEC = sources.ImportSpec("report_1_percent", dot="unit", unit="Folder 2", group_by="Folder 1")


@pytest.fixture
def window(qtbot):
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    window = QuantAnalysisWindow()
    qtbot.addWidget(window)
    window.show()
    return window


def _menu(window, name: str):
    return next(action.menu() for action in window.menuBar().actions() if action.text().replace("&", "") == name)


def _actions(window) -> dict:
    from qtpy.QtWidgets import QMenu

    return {action.text(): action for menu in window.menuBar().findChildren(QMenu) for action in menu.actions()}


def _recovery_files() -> list[Path]:
    from cellquant.gui.quantanalysis import window as window_module

    folder = window_module.recovery_folder()
    return sorted(folder.glob("*.cqstats")) if folder.is_dir() else []


# --- Closing, the dirty marker and autosave --------------------------------------------------------------------------


def test_a_never_saved_project_shows_the_unsaved_marker(window, qtbot):
    assert window.windowTitle() == "QuantAnalysis" and not window.has_unsaved_changes()
    window.new_table()
    qtbot.waitUntil(lambda: window.windowTitle() == "QuantAnalysis: Untitled*", timeout=2000)


def test_closing_with_unsaved_changes_asks_and_cancel_keeps_the_window(window, monkeypatch):
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    window.new_table()
    asked = []
    monkeypatch.setattr(QuantAnalysisWindow, "ask_save", lambda self, action: asked.append(action) or "cancel")
    assert not window.close() and window.isVisible() and asked == ["closing QuantAnalysis"]
    assert window.project.data  # nothing was lost
    monkeypatch.setattr(QuantAnalysisWindow, "ask_save", lambda self, action: asked.append(action) or "discard")
    assert window.close() and not window.isVisible()


def test_closing_a_clean_window_does_not_ask(window, monkeypatch):
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    monkeypatch.setattr(QuantAnalysisWindow, "ask_save", lambda self, action: pytest.fail("asked about a project with no changes"))
    assert window.close()


def test_closing_and_choosing_save_writes_the_project_first(window, monkeypatch, tmp_path):
    from cellquant.gui.quantanalysis import window as window_module
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    window.new_table()
    target = tmp_path / "mine.cqstats"
    monkeypatch.setattr(QuantAnalysisWindow, "ask_save", lambda self, action: "save")
    monkeypatch.setattr(window_module.QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(target), ""))
    assert window.close() and target.is_file()
    # A save that is cancelled in its own dialog keeps the window open.
    other = window_module.QuantAnalysisWindow()
    other.show()
    other.new_table()
    monkeypatch.setattr(QuantAnalysisWindow, "ask_save", lambda self, action: "save")
    monkeypatch.setattr(window_module.QFileDialog, "getSaveFileName", lambda *args, **kwargs: ("", ""))
    assert not other.close() and other.isVisible()
    monkeypatch.setattr(QuantAnalysisWindow, "ask_save", lambda self, action: "discard")
    other.close()


def test_opened_from_cellquant_discard_puts_the_project_back_and_cellquant_closing_asks_once(qtbot, monkeypatch, tmp_path):
    from qtpy.QtWidgets import QMainWindow

    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    main = QMainWindow()
    qtbot.addWidget(main)
    main.show()
    window = QuantAnalysisWindow(main)
    window.show()
    saved = tmp_path / "saved.cqstats"
    window.new_table()
    assert window._write(saved)
    window.new_table()  # an unsaved second table
    assert len(window.project.data) == 2
    asked = []
    monkeypatch.setattr(QuantAnalysisWindow, "ask_save", lambda self, action: asked.append(action) or "discard")
    window.close()  # only hides the window (CellQuant keeps it), so Discard also puts the project back to the saved one
    assert not window.isVisible() and len(window.project.data) == 1 and not window.has_unsaved_changes() and asked == ["closing QuantAnalysis"]
    # CellQuant closing asks once, and the QuantAnalysis window that closes with it does not ask again.
    window.show()
    window.new_table()
    asked.clear()
    assert main.close()
    window.close()
    assert asked == ["CellQuant closes"]


def test_autosave_writes_a_recovery_copy_and_never_the_users_file(window, tmp_path):
    from cellquant.gui.quantanalysis import project_file

    assert not window.autosave() and _recovery_files() == []  # nothing unsaved: nothing written
    window.new_table()
    assert window.autosave()
    [copy] = _recovery_files()
    assert copy.name == "untitled.cqstats" and len(project_file.read(copy)["data"]) == 1
    mine = tmp_path / "mine.cqstats"
    assert window._write(mine)
    assert _recovery_files() == []  # saved: the recovery copy is deleted
    before = mine.read_bytes()
    window.new_table()
    assert window.autosave()
    [copy] = _recovery_files()
    assert copy != mine and copy.parent != mine.parent and mine.read_bytes() == before  # the project's own file is untouched
    assert len(project_file.read(copy)["data"]) == 2
    assert window._write(mine) and _recovery_files() == []


def test_discarding_removes_the_recovery_copy(window, monkeypatch):
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    window.new_table()
    window.autosave()
    assert _recovery_files()
    monkeypatch.setattr(QuantAnalysisWindow, "ask_save", lambda self, action: "discard")
    assert window.new_project() and _recovery_files() == []
    window.new_table()
    window.autosave()
    assert _recovery_files()
    window.close()
    assert _recovery_files() == []  # a clean close leaves nothing behind


def test_an_unsaved_projects_recovery_copy_is_offered_and_restored(qtbot, monkeypatch):
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    first = QuantAnalysisWindow()
    qtbot.addWidget(first)
    first.new_table()
    first.autosave()  # ... and QuantAnalysis dies here
    second = QuantAnalysisWindow()
    qtbot.addWidget(second)
    asked = []
    monkeypatch.setattr(QuantAnalysisWindow, "ask_restore", lambda self, name, when: asked.append(name) or True)
    assert second.offer_recovery() and len(asked) == 1
    assert len(second.project.data) == 1 and second.path is None and second.has_unsaved_changes()
    second.update_title()
    assert second.windowTitle() == "QuantAnalysis: Untitled*"
    # Declined: the copy is deleted and the project stays empty.
    third = QuantAnalysisWindow()
    qtbot.addWidget(third)
    monkeypatch.setattr(QuantAnalysisWindow, "ask_restore", lambda self, name, when: False)
    assert not third.offer_recovery() and not third.project.data and _recovery_files() == []


def test_a_saved_projects_newer_recovery_copy_is_offered_on_opening(qtbot, monkeypatch, tmp_path):
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    mine = tmp_path / "mine.cqstats"
    first = QuantAnalysisWindow()
    qtbot.addWidget(first)
    first.new_table()
    assert first._write(mine)
    first.new_table()
    first.autosave()  # newer than the file
    [copy] = _recovery_files()
    os.utime(copy, (mine.stat().st_mtime + 60,) * 2)
    second = QuantAnalysisWindow()
    qtbot.addWidget(second)
    monkeypatch.setattr(QuantAnalysisWindow, "ask_restore", lambda self, name, when: False)
    assert second.open_project(mine) and len(second.project.data) == 1 and _recovery_files() == []  # declined: deleted
    first.autosave()
    [copy] = _recovery_files()
    os.utime(copy, (mine.stat().st_mtime + 60,) * 2)
    monkeypatch.setattr(QuantAnalysisWindow, "ask_restore", lambda self, name, when: True)
    assert second.open_project(mine) and len(second.project.data) == 2
    assert second.path == mine and second.has_unsaved_changes()  # restored, and still unsaved
    # A recovery copy older than the file is stale: deleted without asking.
    assert second._write(mine)
    first.autosave()
    [copy] = _recovery_files()
    os.utime(copy, (mine.stat().st_mtime - 60,) * 2)
    monkeypatch.setattr(QuantAnalysisWindow, "ask_restore", lambda self, name, when: pytest.fail("offered a stale recovery copy"))
    assert second.open_project(mine) and _recovery_files() == []


def test_a_project_that_cannot_be_written_is_only_logged_by_autosave(window):
    data = window.new_table()
    from cellquant.gui.quantanalysis.project import ResultSheet

    window.project.results["x"] = ResultSheet("Bad", data.id, "colstats", {"bad": object()}, id="x")
    assert not window.autosave() and "autosave failed" in "\n".join(window._log.lines)


# --- The import dialog's checkbox --------------------------------------------------------------------------------------


def test_the_suggested_graph_and_test_follow_the_checkbox_alone(window, tmp_path, monkeypatch):
    from cellquant.gui.quantanalysis.dialogs import ImportDialog

    folder = make_results(tmp_path / "results")
    dialog = ImportDialog(window, sources.read_results_folder(folder), SPEC)
    assert dialog.make_suggested.isChecked() and dialog.make_suggested.text() == "Also make the suggested graph and test"
    assert dialog.plan.text() == "Will make: SuperPlot + Estimation plot + Welch's t test"
    dialog.make_suggested.setChecked(False)
    assert dialog.plan.text() == "Only the data table is made."
    # The reset button only refills the fields: it neither ticks nor unticks the box.
    buttons = {button.text() for button in dialog.findChildren(type(dialog.buttons.button(dialog.buttons.Ok)))}
    assert "Reset to suggested setup" in buttons and "Use suggested setup" not in buttons
    dialog.measure.setCurrentIndex(0)
    reset = next(button for button in dialog.findChildren(type(dialog.buttons.button(dialog.buttons.Ok))) if button.text() == "Reset to suggested setup")
    reset.click()
    assert not dialog.make_suggested.isChecked() and dialog.spec().measure == dialog._default_spec().measure
    # Pressing Import with the default (ticked) box makes the table, the SuperPlot, the test and the estimation plot, without any other click.
    monkeypatch.setattr(ImportDialog, "exec", lambda self: 1)
    data = window.open_folder(folder)
    kinds = sorted(graph.kind for graph in window.project.graphs.values())
    assert data is not None and kinds == ["estimation", "superplot"] and [r.kind for r in window.project.results.values()] == ["compare"]
    # Unticked: the table only, even after the reset button was pressed.
    window.new_project()
    monkeypatch.setattr(ImportDialog, "exec", lambda self: (self.make_suggested.setChecked(False), self._use_suggested(), 1)[-1])
    assert window.open_folder(folder) is not None
    assert len(window.project.data) == 1 and not window.project.results and not window.project.graphs


def test_the_suggested_plan_says_what_is_made(window, tmp_path):
    from cellquant.gui.quantanalysis.dialogs import suggested_plan

    assert suggested_plan(2, False, True)["text"] == "SuperPlot + Estimation plot + Welch's t test"
    assert suggested_plan(2, False, False)["text"] == "Column scatter + Estimation plot + Welch's t test"
    assert suggested_plan(4, False, True)["text"] == "SuperPlot + Kruskal-Wallis test with Dunn's tests"
    assert suggested_plan(1, False, False)["text"] == "Column scatter"
    assert suggested_plan(3, True, False)["text"] == "Bar and dots + Two-way ANOVA"
    # What is announced is what is made.
    data = window.open_folder(make_results(tmp_path / "results"), SPEC, accept=True, make_suggested=True)
    plan = suggested_plan(len(data.table), False, data.units is not None)
    assert sorted(graph.kind for graph in window.project.graphs.values()) == sorted(plan["graphs"])
    assert [result.options["test"] for result in window.project.results.values()] == [plan["test"]]


# --- The Analyze menu greys what cannot run ----------------------------------------------------------------------------


KINDS = {"Column statistics": "colstats", "Normality and lognormality tests": "normality", "Compare two columns…": "compare",
         "Compare three or more columns…": "anova", "Nested t test / ANOVA…": "nested", "Two-way ANOVA…": "twoway",
         "Linear regression…": "linreg", "Nonlinear regression (curve fit)…": "curvefit", "Correlation (XY)…": "xycorr",
         "Contingency table…": "contingency", "ROC curve…": "roc", "Bland-Altman…": "blandaltman", "Survival curves (Kaplan-Meier)…": "survival",
         "Correlation matrix…": "corrmatrix", "Principal component analysis (PCA)…": "pca", "Identify outliers…": "outliers",
         "Analyze a stack of P values…": "pstack"}


def test_analyze_menu_is_greyed_with_the_reason_and_agrees_with_the_run_time_check(window):
    from cellquant.gui.quantanalysis.window import analysis_blocker

    actions = _actions(window)
    window.select(None)
    for name, kind in KINDS.items():  # nothing selected: only the P value stack needs no table
        assert actions[name].isEnabled() == (kind == "pstack"), name
        assert kind == "pstack" or "Select a data table" in actions[name].toolTip()
    tables = {"column": window.new_table(), "xy": window.new_xy_table(), "grouped": window.new_grouped_table(),
              "contingency": window.new_contingency_table(), "replicates": window.new_replicate_table()}
    expected = {"column": {"colstats", "normality", "compare", "outliers", "roc", "blandaltman", "corrmatrix", "pca", "pstack"},
                "xy": {"colstats", "normality", "linreg", "curvefit", "xycorr", "survival", "pstack"},
                "grouped": {"colstats", "normality", "compare", "anova", "twoway", "outliers", "pstack"},
                "contingency": {"contingency", "pstack"},
                "replicates": {"colstats", "normality", "compare", "nested", "outliers", "roc", "blandaltman", "corrmatrix", "pca", "pstack"}}
    for label, sheet in tables.items():
        window.select(sheet.id)
        enabled = {kind for name, kind in KINDS.items() if actions[name].isEnabled()}
        assert enabled == expected[label] - ({"anova"} if label != "grouped" else set()), (label, enabled ^ expected[label])
        for name, kind in KINDS.items():
            action = actions[name]
            reason = analysis_blocker(kind, sheet)
            assert action.isEnabled() == (reason is None), (label, name)  # one source of truth: the menu and the run-time check cannot disagree
            if reason:
                assert action.toolTip() == reason
                assert window.analyze(kind, {}) is None and window.statusBar().currentMessage() == reason
    # The reasons the task names.
    window.select(tables["column"].id)
    assert "grouped table" in actions["Two-way ANOVA…"].toolTip() and "replicates" in actions["Nested t test / ANOVA…"].toolTip()
    assert "XY table" in actions["Survival curves (Kaplan-Meier)…"].toolTip() and "XY table" in actions["Linear regression…"].toolTip()
    assert "contingency table" in actions["Contingency table…"].toolTip()
    assert "at least three columns" in actions["Compare three or more columns…"].toolTip()
    # It follows the table: a third column makes the three-or-more comparison available without reselecting.
    tables["column"].table = type(tables["column"].table).from_columns({"A": [1.0, 2.0], "B": [2.0, 3.0], "C": [3.0, 5.0]})
    window.project.data_changed(tables["column"].id)
    assert actions["Compare three or more columns…"].isEnabled()
    # Data processing follows the same rule.
    processing = next(a for a in _menu(window, "Analyze").actions() if a.text() == "Data processing").menu()
    assert all(action.isEnabled() for action in processing.actions())
    window.select(tables["xy"].id)
    assert not any(action.isEnabled() for action in processing.actions()) and "column tables" in processing.actions()[0].toolTip()
    assert _menu(window, "Analyze").toolTipsVisible()


def test_data_sheet_buttons_follow_the_same_check(window):
    from qtpy.QtWidgets import QPushButton

    sheet = window.new_table()  # two columns
    view = window.stack.currentWidget()
    buttons = {button.text(): button for button in view.findChildren(QPushButton)}
    assert "Compare 3+ columns…" not in buttons
    three = buttons["Compare three or more columns…"]
    assert not three.isEnabled() and "at least three columns" in three.toolTip()
    assert buttons["Compare two columns…"].isEnabled()
    sheet.table = type(sheet.table).from_columns({"A": [1.0], "B": [2.0], "C": [3.0]})
    window.project.data_changed(sheet.id)
    assert three.isEnabled()


# --- The Edit menu -------------------------------------------------------------------------------------------------------


def test_edit_menu_renames_and_deletes_the_selected_sheet(window, monkeypatch):
    from cellquant.gui.quantanalysis import window as window_module

    edit = _menu(window, "Edit")
    assert [action.text() for action in edit.actions()] == ["Rename", "Delete"]
    rename, delete = edit.actions()
    assert rename.shortcut().toString() == "F2" and "Del" in delete.shortcut().toString()
    assert all(action.shortcutContext() == Qt.WidgetWithChildrenShortcut for action in (rename, delete))
    assert not rename.isEnabled() and not delete.isEnabled()  # nothing selected
    data = window.new_table()
    assert rename.isEnabled() and delete.isEnabled()
    window.navigator.setCurrentItem(window.folders["data"])  # a folder row is not a sheet
    assert not rename.isEnabled() and not delete.isEnabled()
    window.select(data.id)
    monkeypatch.setattr(window_module.QInputDialog, "getText", lambda *args, **kwargs: ("Renamed", True))
    rename.trigger()
    assert data.name == "Renamed" and window.item_of(data.id).text(0) == "Renamed"
    monkeypatch.setattr(window_module.QInputDialog, "getText", lambda *args, **kwargs: ("", False))
    rename.trigger()
    assert data.name == "Renamed"
    # A table with results and graphs asks first (the same rule as the context menu).
    window.analyze("colstats")
    window.select(data.id)
    answers = iter([window_module.QMessageBox.No, window_module.QMessageBox.Yes])
    monkeypatch.setattr(window_module.QMessageBox, "question", lambda *args, **kwargs: next(answers))
    delete.trigger()
    assert data.id in window.project.data
    delete.trigger()
    assert data.id not in window.project.data and not window.project.results and not delete.isEnabled()


def test_delete_and_rename_keys_belong_to_the_sheet_list_only(window, qtbot, monkeypatch):
    """The keys work while the list on the left has the focus: never in a cell editor or a text field, and the graph's own Delete is left alone."""

    from qtpy.QtTest import QTest

    from cellquant.gui.quantanalysis import window as window_module

    first = window.new_table()
    second = window.new_table()
    window.show()
    window.activateWindow()
    qtbot.waitExposed(window)
    window.select(first.id)
    view = window.stack.currentWidget()
    # Delete in the data grid clears cells; it does not delete the sheet.
    view.grid.setFocus()
    view.grid.setCurrentCell(0, 0)
    QTest.keyClick(view.grid, Qt.Key_Delete)
    assert first.id in window.project.data
    # In the list, F2 renames and Delete deletes the selected sheet.
    names = []
    monkeypatch.setattr(window_module.QInputDialog, "getText", lambda *args, **kwargs: names.append("x") or ("Keyed", True))
    window.navigator.setFocus()
    qtbot.waitUntil(lambda: window.navigator.hasFocus(), timeout=2000)
    QTest.keyClick(window.navigator, Qt.Key_F2)
    assert names == ["x"] and first.name == "Keyed"
    QTest.keyClick(window.navigator, Qt.Key_Delete)
    assert first.id not in window.project.data and second.id in window.project.data
    # The graph's key handler: Delete on a selected annotation removes it (the window shortcut does not take the key).
    graph = window.new_graph("scatter")
    from cellquant.gui.quantanalysis import graph_format as gf

    graph.format.annotations.append(gf.Annotation(kind="text", text="note", x=0.5, y=0.5))
    graph_view = window.views[graph.id]
    graph_view.select(("annotation", graph.format.annotations[0].id))
    graph_view.canvas.setFocus()
    assert not window.navigator.hasFocus()
    graph_view._on_key(type("Key", (), {"key": "delete"})())
    assert graph.id in window.project.graphs and not graph.format.annotations


# --- Exporting a data table; where exports start -----------------------------------------------------------------------


def _reimport(window, path, **kwargs):
    return window.import_csv(str(path), shape="wide", **kwargs)


def test_export_data_table_csv_reads_back_for_every_kind_of_table(window, tmp_path):
    from cellquant.gui.quantanalysis.project import DataSheet, set_contingency, set_grouped, set_replicates, set_xy
    from cellquant.stats import ColumnTable
    from cellquant.stats.contingency import ContingencyTable
    from cellquant.stats.grouped import GroupedTable
    from cellquant.stats.replicates import ReplicateTable
    from cellquant.stats.xy import XYTable

    # A column table: the same headers, a short column left blank.
    column = window.project.add_data(DataSheet("Columns", ColumnTable.from_columns({"Control": [1.5, 2.25, 3.0], "Treated": [10.0, 20.5]})))
    window.select(column.id)
    path = window.export_data_table(str(tmp_path / "columns.csv"))
    lines = Path(path).read_text().splitlines()
    assert lines[0] == "Control,Treated" and lines[1] == "1.5,10.0" and lines[3] == "3.0," and len(lines) == 4
    back = _reimport(window, path)
    assert back.table.names == ["Control", "Treated"]
    assert np.allclose(back.table["Control"].values, [1.5, 2.25, 3.0]) and np.allclose(back.table["Treated"].values, [10.0, 20.5])
    # A table with replicates: the Replicate column first, a blank continuing the one above; Import CSV… finds the replicate column itself.
    replicated = window.project.add_data(DataSheet("Reps", ColumnTable()))
    set_replicates(replicated, ReplicateTable("", ["A", "B"], [np.array([1.0, 2.0, 3.0, 4.0]), np.array([5.0, 6.0, 7.0, 8.0])], ["R1", "", "R2", ""]))
    window.select(replicated.id)
    path = window.export_data_table(str(tmp_path / "reps.csv"))
    assert Path(path).read_text().splitlines()[:3] == ["Replicate,A,B", "R1,1.0,5.0", ",2.0,6.0"]
    back = _reimport(window, path, replicate="Replicate")
    assert back.replicates is not None and back.replicates.labels == ["R1", "", "R2", ""] and back.replicates.names == ["A", "B"]
    # Grouped, XY and contingency tables: the layout the grid shows, one column per data set and replicate.
    grouped = window.project.add_data(DataSheet("Grouped", ColumnTable()))
    set_grouped(grouped, GroupedTable("", ["Row 1", "Row 2"], ["Set A", "Set B"], np.arange(12, dtype=float).reshape(2, 2, 3)))
    window.select(grouped.id)
    path = window.export_data_table(str(tmp_path / "grouped.csv"))
    assert Path(path).read_text().splitlines()[0] == "Row,Set A 1,Set A 2,Set A 3,Set B 1,Set B 2,Set B 3"
    assert len(_reimport(window, path).table) == 6
    xy = window.project.add_data(DataSheet("XY", ColumnTable()))
    set_xy(xy, XYTable("", "Dose", np.array([1.0, 2.0]), ["Resp"], np.array([[[1.0, 2.0]], [[3.0, np.nan]]])))
    window.select(xy.id)
    path = window.export_data_table(str(tmp_path / "xy.csv"))
    assert Path(path).read_text().splitlines() == ["Dose,Resp 1,Resp 2", "1.0,1.0,2.0", "2.0,3.0,"]
    assert _reimport(window, path).table.names == ["Dose", "Resp 1", "Resp 2"]
    counts = window.project.add_data(DataSheet("Counts", ColumnTable()))
    set_contingency(counts, ContingencyTable("", ["A", "B"], ["Yes", "No"], np.array([[5.0, 3.0], [2.0, 6.0]])))
    window.select(counts.id)
    path = window.export_data_table(str(tmp_path / "counts.csv"))
    assert Path(path).read_text().splitlines() == ["Row,Yes,No", "A,5.0,3.0", "B,2.0,6.0"]


def test_export_data_table_is_in_the_menu_and_on_every_data_sheet(window):
    from qtpy.QtWidgets import QPushButton

    assert "Export data table CSV…" in [action.text() for action in _menu(window, "Export").actions()]
    for make in (window.new_table, window.new_grouped_table, window.new_xy_table, window.new_contingency_table):
        make()
        assert any(button.text() == "Export data table CSV…" for button in window.stack.currentWidget().findChildren(QPushButton)), make.__name__
    window.navigator.setCurrentItem(window.folders["data"])  # nothing selected (and several tables, so none is assumed)
    assert window.export_data_table() is None and "Select a data table" in window.statusBar().currentMessage()


def test_exports_start_in_the_projects_folder_else_the_results_folder_else_the_last_one(window, tmp_path, monkeypatch):
    from cellquant.gui.quantanalysis.graph_view import GraphExportDialog
    from cellquant.gui.quantanalysis.layout_view import LayoutExportDialog

    results = make_results(tmp_path / "results")
    data = window.open_folder(results, SPEC, accept=True, make_suggested=False)
    graph = window.new_graph("scatter")
    view = window.views[graph.id]
    assert Path(GraphExportDialog(view, graph).path()).parent == Path(results)  # the results folder the data came from
    window.analyze("colstats")
    result = window.project.sheet(window.current_id())
    assert Path(window.export_path("a:b.csv", result)) == Path(results) / "a b.csv"  # and a file name made safe
    layout = window.new_layout()
    assert Path(LayoutExportDialog(window.stack.currentWidget(), layout).file.text()).parent == Path(results)
    # A saved project: its folder wins over the results folder.
    project_dir = tmp_path / "projects"
    assert window._write(project_dir / "p.cqstats")
    assert Path(GraphExportDialog(view, graph).path()).parent == project_dir
    assert Path(window.export_path("x.csv", data)).parent == project_dir
    # A new table typed in (no project, no results folder): the last folder an export used, then home.
    window.new_project()
    typed = window.new_table()
    assert Path(window.export_folder(typed)) == Path.home()
    out = tmp_path / "figures"
    out.mkdir()
    window.export_data_table(str(out / "t.csv"))
    assert window.export_folder(typed) == out
    graph2 = window.new_graph("scatter")
    assert Path(GraphExportDialog(window.views[graph2.id], graph2).path()).parent == out
    written = window.views[graph2.id].export(tmp_path / "g.svg", "svg")
    assert window.export_folder(typed) == written.parent
    window.stack.currentWidget()  # the results export remembers its folder too
    window.analyze("colstats")
    sheet_view = window.stack.currentWidget()
    sub = tmp_path / "tables"
    sub.mkdir()
    sheet_view.export_csv(str(sub / "r.csv"))
    assert window.export_folder(typed) == sub


# --- Project defaults ------------------------------------------------------------------------------------------------------


def test_project_defaults_start_every_analysis_dialog_and_show_one_summary_line(window, monkeypatch):
    from qtpy.QtWidgets import QPushButton

    from cellquant.gui.quantanalysis.dialogs import (AnovaDialog, CompareDialog, ContingencyDialog, CorrelationMatrixDialog, CurveFitDialog,
                                                     NestedDialog, ProjectDefaultsDialog, RocDialog, SurvivalDialog, TwoWayDialog, XYDialog)
    from cellquant.gui.quantanalysis.project import DataSheet, set_replicates
    from cellquant.stats import STYLES, ColumnTable
    from cellquant.stats.replicates import ReplicateTable

    assert "Project defaults…" in _actions(window)
    assert window.project.defaults == {"alpha": 0.05, "ci": 0.95, "style": "classic"}
    table = ColumnTable.from_columns({"A": [1.0, 2.0, 4.0], "B": [2.0, 6.0, 8.0], "C": [3.0, 5.0, 9.0]})
    column = window.project.add_data(DataSheet("T", table))
    reps = ReplicateTable("", ["A", "B"], [np.array([1.0, 2.0, 3.0, 4.0]), np.array([5.0, 6.0, 7.0, 8.0])], ["R1", "R1", "R2", "R2"])
    style = other = next(name for name in STYLES if name != "classic")
    assert window.project_defaults_dialog({"alpha": 0.01, "ci": 0.99, "style": style})
    assert window.project.defaults == {"alpha": 0.01, "ci": 0.99, "style": style} and window.has_unsaved_changes()
    window.new_xy_table(), window.new_grouped_table(), window.new_contingency_table()
    xy, grouped, counts = (list(window.project.data.values())[-3:])
    made = [CompareDialog(window, table), AnovaDialog(window, table), NestedDialog(window, table, reps), TwoWayDialog(window, grouped.grouped),
            XYDialog(window, xy.xy, "linreg"), XYDialog(window, xy.xy, "xycorr"), CurveFitDialog(window, xy.xy),
            ContingencyDialog(window, counts.contingency), RocDialog(window, table), SurvivalDialog(window, xy.xy),
            CorrelationMatrixDialog(window, table)]
    for dialog in made:
        assert (dialog.alpha.value(), dialog.ci.currentData(), dialog.style.currentData()) == (0.01, 0.99, other), type(dialog).__name__
        assert dialog.stats_summary.text() == f"α 0.01 · 99% CI · P style: {other}", type(dialog).__name__
        assert dialog.stats_selectors.isHidden()  # the three selectors are folded into the summary line ...
        dialog.stats_change.click()
        assert not dialog.stats_selectors.isHidden() and dialog.alpha.isVisibleTo(dialog)  # ... until Change… is pressed
        dialog.alpha.setValue(0.1)  # for this analysis only
        assert dialog.stats_summary.text().startswith("α 0.1 ·")
        assert dialog.options()["alpha"] == 0.1
    assert window.project.defaults["alpha"] == 0.01
    # An analysis run with its own values keeps them when reopened; other analyses pick up the new defaults.
    window.select(column.id)
    own = window.analyze("compare", {"test": "welch_t", "first": 0, "second": 1, "alternative": "two-sided", "ci": 0.9, "alpha": 0.1, "style": "classic"})
    reopened = CompareDialog(window, table, own.options)
    assert (reopened.alpha.value(), reopened.ci.currentData(), reopened.style.currentData()) == (0.1, 0.9, "classic")
    assert reopened.stats_summary.text() == "α 0.1 · 90% CI · P style: classic"
    monkeypatch.setattr(CompareDialog, "exec", lambda self: 1)
    fresh = window.analyze("compare")
    assert (fresh.options["alpha"], fresh.options["ci"], fresh.options["style"]) == (0.01, 0.99, other)
    # The dialog behind Analyze → Project defaults… edits them.
    dialog = ProjectDefaultsDialog(window, window.project.defaults)
    assert dialog.options() == window.project.defaults
    dialog.alpha.setValue(0.02)
    monkeypatch.setattr(ProjectDefaultsDialog, "exec", lambda self: (self.alpha.setValue(0.02), 1)[-1])
    assert window.project_defaults_dialog() and window.project.defaults["alpha"] == 0.02
    window.new_project()
    assert window.project.defaults == {"alpha": 0.05, "ci": 0.95, "style": "classic"}


def test_project_defaults_are_saved_in_the_project_and_older_files_open_with_todays(window, tmp_path):
    import json
    import zipfile

    from cellquant.gui.quantanalysis import project_file

    window.new_table()
    window.project_defaults_dialog({"alpha": 0.01, "ci": 0.9, "style": "classic"})
    path = tmp_path / "d.cqstats"
    assert window._write(path)
    assert project_file.read(path)["defaults"] == {"alpha": 0.01, "ci": 0.9, "style": "classic"}
    window.new_project()
    assert window.open_project(path) and window.project.defaults == {"alpha": 0.01, "ci": 0.9, "style": "classic"}
    assert not window.has_unsaved_changes()
    # A file from before project defaults has none: today's values, and nothing is "unsaved".
    old = tmp_path / "old.cqstats"
    content = project_file.read(path)
    del content["defaults"]
    with zipfile.ZipFile(old, "w") as archive:
        archive.writestr(project_file.MEMBER, json.dumps(content))
    assert window.open_project(old) and window.project.defaults == {"alpha": 0.05, "ci": 0.95, "style": "classic"}
    assert not window.has_unsaved_changes()
    # Damaged values fall back to the standard ones one by one.
    content["defaults"] = {"alpha": "x", "ci": 0.5, "style": "nope"}
    with zipfile.ZipFile(old, "w") as archive:
        archive.writestr(project_file.MEMBER, json.dumps(content))
    assert window.open_project(old) and window.project.defaults == {"alpha": 0.05, "ci": 0.95, "style": "classic"}
    content["defaults"] = {"alpha": 0.2}
    with zipfile.ZipFile(old, "w") as archive:
        archive.writestr(project_file.MEMBER, json.dumps(content))
    assert window.open_project(old) and window.project.defaults == {"alpha": 0.2, "ci": 0.95, "style": "classic"}


def test_the_suggested_analysis_starts_from_the_project_defaults(window, tmp_path):
    window.project_defaults_dialog({"alpha": 0.01, "ci": 0.9, "style": "classic"})
    window.open_folder(make_results(tmp_path / "results"), SPEC, accept=True, make_suggested=True)
    [result] = window.project.results.values()
    assert (result.options["alpha"], result.options["ci"]) == (0.01, 0.9)


# --- One name for each ---------------------------------------------------------------------------------------------------


def test_one_name_for_each_menu_item_button_and_dialog(window, monkeypatch):
    from qtpy.QtWidgets import QPushButton

    texts = set(_actions(window))
    assert {"Compare three or more columns…", "Export page layout…", "Open CellQuant results folder…"} <= texts
    assert not {"Compare 3+ columns…", "Export layout…", "Open results folder…"} & texts
    welcome = {button.text() for button in window.empty.findChildren(QPushButton)}
    assert "Open CellQuant results folder…" in welcome
    # The folder dialog's title is the menu item's wording.
    titles = []
    monkeypatch.setattr("cellquant.gui.app.choose_existing_directory", lambda parent, title, *args, **kwargs: titles.append(title) or "")
    window.open_folder_dialog()
    assert titles == ["Open a CellQuant results folder"]
    layout_buttons = []
    window.new_table()
    window.new_graph("scatter")
    window.new_layout()
    layout_buttons = [button.text() for button in window.stack.currentWidget().findChildren(QPushButton)]
    assert "Export page layout…" in layout_buttons and "Export layout…" not in layout_buttons
