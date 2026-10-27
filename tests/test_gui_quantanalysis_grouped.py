"""QuantAnalysis grouped tables: typing, pasting and renaming, two-way ANOVA, importing two factors and projects.
Needs Qt (xvfb-run on Linux)."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("pytestqt")

from cellquant.stats import sources  # noqa: E402
from tests.test_stats_import import make_results  # noqa: E402

TUKEY = {"interaction": True, "compare": "within_rows", "goal": "all", "method": "tukey", "control": None, "family": "per_group",
         "alpha": 0.05, "ci": 0.95, "style": "classic"}


@pytest.fixture
def window(qtbot):
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    window = QuantAnalysisWindow()
    qtbot.addWidget(window)
    window.show()
    return window


def test_grouped_table_edits_and_two_way_anova(window, tmp_path, monkeypatch):
    from cellquant.gui.quantanalysis import project_file
    from cellquant.gui.quantanalysis.dialogs import TwoWayDialog
    from cellquant.gui.quantanalysis.grouped_view import GroupedSheetView

    sheet = window.new_grouped_table()
    view = window.stack.currentWidget()
    assert isinstance(view, GroupedSheetView) and sheet.grouped.values.shape == (2, 2, 3)
    view.paste("1\t2\t3\t4\t5\t6\n2\t3\t4\t7\t8\t9.5")
    assert sheet.grouped.values[1, 1].tolist() == [7.0, 8.0, 9.5]
    assert sheet.table.names == ["Row 1: Data set A", "Row 1: Data set B", "Row 2: Data set A", "Row 2: Data set B"]
    view._rename(0, "row", "Control")
    view._rename(1, "column", "Knockout")
    view._rename(1, "row", "Control")  # a repeated name is refused
    assert sheet.grouped.rows == ["Control", "Row 2"] and sheet.grouped.columns == ["Data set A", "Knockout"]
    view.add_replicate()
    view.add_row()
    view.add_data_set()
    assert sheet.grouped.values.shape == (3, 3, 4) and np.isnan(sheet.grouped.values[2]).all()
    view.grid.setCurrentCell(2, 0)
    view.remove_row()
    view.grid.setCurrentCell(0, 2 * 4)
    view.remove_data_set()
    assert sheet.grouped.values.shape == (2, 2, 4)

    result = window.analyze("twoway", TUKEY)
    assert result.error == "" and result.name == f"Two-way ANOVA: {sheet.name}"
    assert [row["label"] for row in result.result.values["comparisons"]] == ["Control: Data set A vs. Knockout", "Row 2: Data set A vs. Knockout"]
    before = result.result.values["p_columns"]
    view.grid.item(1, 4 + 2).setText("30")  # editing a cell recomputes the analysis
    assert result.result.values["p_columns"] != pytest.approx(before)
    window.change_options(result, {**TUKEY, "compare": "column_means"})
    assert result.result.values["compare"] == "column_means"

    # The dialog offers the right references and methods, and refuses what cannot be fitted.
    dialog = TwoWayDialog(window, sheet.grouped, {**TUKEY, "compare": "within_columns", "goal": "control"})
    assert [dialog.control.itemData(i) for i in range(dialog.control.count())] == ["Control", "Row 2"]
    assert dialog.method.currentData() == "dunnett"
    dialog.family.setCurrentIndex(dialog.family.findData("combined"))
    assert dialog.method.currentData() == "sidak" and dialog.method.findData("dunnett") < 0

    # Column analyses, processing and cleaned copies: cells as columns, or a clear refusal.
    assert window.process_data("transform", {"function": "log10"}) is None
    assert "grouped" in window.statusBar().currentMessage()

    path = project_file.save(window.project, tmp_path / "grouped.cqstats")
    assert window.open_project(path, ask=False)
    copy = window.project.data[sheet.id]
    assert copy.grouped is not None and copy.grouped.rows == ["Control", "Row 2"]
    assert np.array_equal(np.nan_to_num(copy.grouped.values, nan=-1), np.nan_to_num(sheet.grouped.values, nan=-1))
    assert window.project.results[result.id].result.values["compare"] == "column_means"
    window.select(sheet.id)
    assert isinstance(window.stack.currentWidget(), GroupedSheetView)


def test_two_way_needs_a_grouped_table(window):
    window.new_table()
    assert window.analyze("twoway", TUKEY) is None and "grouped table" in window.statusBar().currentMessage()


def test_import_with_a_data_set_factor(window, tmp_path):
    from cellquant.gui.quantanalysis.dialogs import ImportDialog

    folder = make_results(tmp_path / "results")
    spec = sources.ImportSpec("area", dot="image", group_by="Folder 1", column_by="mk_pos", approved_only=False)
    source = sources.read_results_folder(folder)
    dialog = ImportDialog(window, source, spec)
    assert dialog.column_by.currentData() == "mk_pos" and dialog.spec().column_by == "mk_pos"
    assert dialog.imported is not None and dialog.imported.grouped is not None
    sheet = window.open_folder(folder, spec, accept=True)
    grouped = sheet.grouped
    assert grouped is not None and grouped.rows == sources.groups(source, "Folder 1")
    assert grouped.columns == ["Marker−", "Marker+"] and "data sets by Marker +/−" in sheet.description
    assert sheet.units is None and sheet.images is None
    result = window.analyze("twoway", TUKEY)
    assert result.error == "" and np.isfinite(result.result.values["p_rows"])
    window.refresh_imports(force=True)  # imported again with the same two factors
    assert window.project.data[sheet.id].grouped.columns == ["Marker−", "Marker+"]
