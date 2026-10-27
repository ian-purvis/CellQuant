"""QuantAnalysis data processing: new tables computed from a data table, that follow it.
Needs Qt (xvfb-run on Linux) and matplotlib."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("matplotlib")

from cellquant.stats import ColumnTable, sources  # noqa: E402
from tests.test_stats_import import make_results  # noqa: E402

WELCH = {"test": "welch_t", "first": 0, "second": 1, "alternative": "two-sided", "ci": 0.95, "alpha": 0.05, "style": "classic"}


@pytest.fixture
def window(qtbot):
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    window = QuantAnalysisWindow()
    qtbot.addWidget(window)
    window.show()
    return window


def test_percent_of_control_follows_its_source(window, tmp_path):
    from cellquant.gui.quantanalysis import project_file
    from cellquant.gui.quantanalysis.datagrid import DataSheetView

    data = window.open_folder(make_results(tmp_path / "results"),
                              sources.ImportSpec("report_1_percent", dot="unit", unit="Folder 2", group_by="Folder 1"), accept=True)
    control = data.table.names[0]
    processed = window.process_data("baseline", {"baseline": control, "operation": "percent", "by": "mean"})
    mean = data.table[control].finite.mean()
    assert processed.name == f"% of {control}: {data.name}" and processed.processed_from == data.id
    assert processed.table[control].finite.mean() == pytest.approx(100.0)
    assert processed.table.columns[1].values == pytest.approx(data.table.columns[1].values / mean * 100, nan_ok=True)
    assert "Follows" in processed.description and processed.pseudoreplicated == data.pseudoreplicated
    view = window.stack.currentWidget()
    assert isinstance(view, DataSheetView) and view.change.isVisible()
    result = window.analyze("compare", WELCH)
    assert result.data_id == processed.id and result.error == ""
    before = result.result.values["p"]

    # Editing the source recomputes the processed table and its results.
    window.select(data.id)
    source_view = window.stack.currentWidget()
    values = [column.values.copy() for column in data.table.columns]
    values[1] = values[1] * 3
    source_view.commit(values=values)
    assert processed.table.columns[1].values == pytest.approx(values[1] / mean * 100, nan_ok=True)
    assert result.result.values["p"] != pytest.approx(before)

    # New options rename a table that still has its first name.
    window.change_process(processed, {"baseline": control, "operation": "subtract", "by": "mean", "keep_baseline": False})
    assert processed.name == f"Minus {control}: {data.name}" and processed.table.names == data.table.names[1:]

    # Saved and opened again, it still follows its source.
    path = project_file.save(window.project, tmp_path / "p.cqstats")
    assert window.open_project(path, ask=False)
    copy = window.project.data[processed.id]
    assert copy.processed_from == data.id and copy.process["operation"] == "subtract"

    # Edited by hand, it keeps its numbers and stops following.
    window.select(copy.id)
    window.stack.currentWidget().commit()
    assert copy.processed_from is None and "no longer follows" in copy.notes[-1]
    # Deleting a source leaves its processed tables as plain tables.
    window.select(data.id)
    other = window.process_data("transform", {"function": "log10"})
    window.project.remove(data.id)
    assert other.id in window.project.data and other.processed_from is None


def test_every_kind_from_its_dialog(window, qtbot):
    from qtpy.QtCore import Qt

    from cellquant.gui.quantanalysis.dialogs import ProcessDialog
    from cellquant.stats import transforms

    data = window.new_table()
    data.table = ColumnTable.from_columns({"A": [1.0, 2.0, 4.0], "B": [2.0, 6.0, 8.0]}, paired=True)
    window.project.data_changed(data.id)
    for kind in transforms.PROCESSES:
        dialog = ProcessDialog(window, data.table, kind)
        qtbot.addWidget(dialog)
        options = dialog.options()
        assert options["kind"] == kind
        window.select(data.id)
        sheet = window.process_data(kind, options)
        assert sheet is not None and sheet.processed_from == data.id, kind
    names = [sheet.name for sheet in window.project.data.values()][1:]
    assert names == ["log10(Y): Data 1", "Normalized: Data 1", "% of total: Data 1", "% of A: Data 1", "B − A: Data 1"]
    column_math = window.project.data[list(window.project.data)[-1]]
    assert column_math.table.columns[0].values == pytest.approx([1, 4, 4])

    dialog = ProcessDialog(window, data.table, "transform", {"function": "multiply", "k": 2})
    qtbot.addWidget(dialog)
    assert dialog.k.isEnabled() and dialog.options()["k"] == 2
    dialog.columns.item(0).setCheckState(Qt.Unchecked)
    dialog.columns.item(1).setCheckState(Qt.Unchecked)
    dialog.accept()
    assert not dialog.note.isHidden() and "Tick at least one column" in dialog.note.text()
    dialog.function.setCurrentIndex(dialog.function.findData("log2"))
    assert not dialog.k.isEnabled() and "doubling" in dialog.function_note.text()
    menu = window.menuBar().actions()[1].menu()
    processing = next(action.menu() for action in menu.actions() if action.text() == "Data processing")
    assert [action.text() for action in processing.actions()] == ["Transform…", "Normalize…", "Fraction of total…",
                                                                  "Remove baseline…", "Column math…"]


def test_renamed_and_removed_source_columns(window):
    data = window.new_table()
    data.table = ColumnTable.from_columns({"A": [1.0, 2.0, 4.0], "B": [2.0, 6.0, 8.0]})
    window.project.data_changed(data.id)
    window.select(data.id)
    percent = window.process_data("baseline", {"baseline": "A", "operation": "percent", "by": "mean"})
    window.select(percent.id)
    logs = window.process_data("transform", {"function": "log10", "columns": ["A", "B"]})
    window.select(percent.id)
    window.analyze("compare", WELCH)

    # Renaming a column renames it in the options of every table that follows it, down the chain.
    window.select(data.id)
    window.stack.currentWidget().commit(names=["X", "B"])
    assert percent.process["baseline"] == "X" and percent.name == "% of X: Data 1" and percent.table.names == ["X", "B"]
    assert logs.process["columns"] == ["X", "B"] and logs.table.names == ["X", "B"] and not logs.warnings
    assert logs.name == "log10(Y): % of X: Data 1"  # each level keeps its automatic name
    # Swapping two names follows both; a table renamed by hand keeps its name; a repeated name is refused.
    logs.name = "Mine"
    window.stack.currentWidget().commit(names=["B", "X"])
    assert percent.process["baseline"] == "B" and percent.name == "% of B: Data 1" and logs.process["columns"] == ["B", "X"]
    assert logs.name == "Mine"
    window.stack.currentWidget().commit(names=["B", "B"])
    assert data.table.names == ["B", "X"] and percent.process["baseline"] == "B"
    window.stack.currentWidget().commit(names=["X", "B"])
    assert logs.table["B"].values == pytest.approx(np.log10(np.array([2, 6, 8]) / (7 / 3) * 100))

    # Removing the column it needs blanks the table and says so; results do not look current.
    window.stack.currentWidget().commit(names=["B"], values=[data.table["B"].values])
    assert np.isnan(percent.table["X"].values).all() and "Out of date" in percent.warnings[-1]
    assert np.isnan(logs.table["B"].values).all() and any("Out of date" in item for item in logs.warnings)
    result = next(item for item in window.project.results.values() if item.data_id == percent.id)
    assert result.result is None or not np.isfinite(result.result.values["p"])


def test_tables_computed_from_each_other_do_not_loop(window):
    data = window.new_table()
    data.table = ColumnTable.from_columns({"A": [1.0, 2.0], "B": [3.0, 4.0]})
    window.select(data.id)
    first = window.process_data("transform", {"function": "multiply", "k": 2})
    data.processed_from, data.process = first.id, {"kind": "transform", "function": "multiply", "k": 2}
    window.project.data_changed(data.id)  # returns: each table is computed once
    assert window.project.processed_description(first, data).startswith("Transform")
    first.process = {"kind": "nothing"}
    assert window.project.processed_description(first, data).startswith("Processing")


def test_unknown_processing_loads_as_a_plain_table(window, tmp_path):
    from cellquant.errors import CellQuantError, StatisticsError
    from cellquant.gui.quantanalysis import project_file

    assert issubclass(StatisticsError, CellQuantError)
    data = window.new_table()
    data.table = ColumnTable.from_columns({"A": [1.0, 2.0]})
    window.select(data.id)
    sheet = window.process_data("transform", {"function": "log10"})
    sheet.process = {"kind": "from a newer version"}
    path = project_file.save(window.project, tmp_path / "p.cqstats")
    assert window.open_project(path, ask=False)
    copy = window.project.data[sheet.id]
    assert copy.processed_from is None and copy.table["A"].values == pytest.approx(np.log10([1, 2]))


def test_recent_projects_survive_commas_and_bad_values(window, tmp_path):
    from qtpy.QtCore import QSettings

    QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, str(tmp_path))
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))
    path = tmp_path / "a, b" / "c.cqstats"
    path.parent.mkdir()
    window._remember_recent(path)
    assert window.recent_projects() == [str(path.resolve())]
    QSettings("CellQuant", "CellQuant").setValue("quantanalysis/recent_projects", 7)
    assert window.recent_projects() == []
    QSettings("CellQuant", "CellQuant").setValue("quantanalysis/recent_projects", "not json")
    assert window.recent_projects() == ["not json"]
    QSettings("CellQuant", "CellQuant").remove("quantanalysis/recent_projects")
    assert window.recent_projects() == []
