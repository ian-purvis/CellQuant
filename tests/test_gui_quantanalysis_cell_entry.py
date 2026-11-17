"""Typing and pasting into the data grids: text that is not a number leaves the cell as it was and says so; a typographic minus counts as a
minus everywhere cells are parsed (typing, paste, CSV import). Needs Qt (xvfb-run on Linux) and matplotlib."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("matplotlib")

from qtpy.QtWidgets import QTableWidgetItem  # noqa: E402


@pytest.fixture
def window(qtbot):
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    window = QuantAnalysisWindow()
    qtbot.addWidget(window)
    window.show()
    return window


def _type(view, row: int, column: int, text: str) -> None:
    view.grid.setItem(row, column, QTableWidgetItem(text))


def _status(window) -> str:
    return window.statusBar().currentMessage()


def test_one_parser_reads_every_minus_sign():
    from cellquant.gui.quantanalysis.datagrid import parse_cell
    from cellquant.stats.tables import parse_number

    for text in ("−3", "–3", "-3", " -3 ", "−3,0"):
        assert parse_cell(text) == parse_number(text) == -3.0
    assert np.isnan(parse_cell("  "))
    for text in ("abc", "12 cells", "5%"):
        with pytest.raises(ValueError):
            parse_cell(text)


def test_typing_text_keeps_the_previous_value_and_says_so(window):
    sheet = window.new_table()
    view = window.stack.currentWidget()
    view.grid.setCurrentCell(0, 0)
    view.paste("A\tB\n12.5\t3\n4\t5\n")
    _type(view, 0, 0, "abc")
    assert view.grid.item(0, 0).text() == "12.5" and sheet.table["A"].values[0] == 12.5
    assert _status(window) == "“abc” is not a number; the cell keeps 12.5."
    _type(view, 5, 1, "12 cells")  # a cell that was empty
    assert view.grid.item(5, 1).text() == "" and sheet.table["B"].values.size == 2
    assert _status(window) == "“12 cells” is not a number; the cell stays blank."
    _type(view, 1, 1, "5%")
    assert view.grid.item(1, 1).text() == "5" and sheet.table["B"].values[1] == 5
    _type(view, 0, 0, "7")  # a number still goes in
    assert sheet.table["A"].values[0] == 7


def test_a_typographic_minus_is_a_minus(window):
    sheet = window.new_table()
    view = window.stack.currentWidget()
    view.grid.setCurrentCell(0, 0)
    view.paste("A\n1\n2\n")
    _type(view, 0, 0, "−3")
    _type(view, 1, 0, "–4.5")
    assert list(sheet.table["A"].values) == [-3.0, -4.5]
    view.paste("−7\n−8\n")
    assert list(sheet.table["A"].values) == [-7.0, -8.0]


def test_pasting_text_into_a_filled_cell_keeps_it_like_typing_does(window):
    sheet = window.new_table()
    view = window.stack.currentWidget()
    view.grid.setCurrentCell(0, 0)
    view.paste("A\tB\n1\t2\n3\t4\n")
    view.grid.setCurrentCell(0, 0)
    view.paste("abc\t9\n7\tn/a\n")
    assert list(sheet.table["A"].values) == [1.0, 7.0] and list(sheet.table["B"].values) == [9.0, 4.0]
    assert "2 pasted cells are not a number" in _status(window)


def test_csv_import_reads_the_typographic_minus(window, tmp_path):
    path = tmp_path / "minus.csv"
    path.write_text("A,B\n−1.5,2\n3,–4\n", encoding="utf-8")
    sheet = window.import_csv(str(path), shape="wide")
    assert list(sheet.table["A"].values) == [-1.5, 3.0] and list(sheet.table["B"].values) == [2.0, -4.0]


def test_the_other_grids_keep_the_value_too(window):
    sheet = window.new_contingency_table()
    view = window.stack.currentWidget()
    _type(view, 0, 0, "5")
    _type(view, 0, 0, "abc")
    assert view.grid.item(0, 0).text() == "5" and sheet.contingency.counts[0, 0] == 5
    assert _status(window) == "“abc” is not a number; the cell keeps 5."
    _type(view, 0, 1, "−2")
    assert sheet.contingency.counts[0, 1] == -2 or np.isnan(sheet.contingency.counts[0, 1])  # a count below zero is refused by the table
    sheet = window.new_xy_table()
    view = window.stack.currentWidget()
    _type(view, 0, 0, "1.5")
    _type(view, 0, 0, "x")
    assert view.grid.item(0, 0).text() == "1.5" and sheet.xy.x[0] == 1.5
    _type(view, 0, 1, "−2")
    assert sheet.xy.values[0, 0, 0] == -2
    sheet = window.new_grouped_table()
    view = window.stack.currentWidget()
    _type(view, 0, 0, "8")
    _type(view, 0, 0, "x")
    assert view.grid.item(0, 0).text() == "8" and sheet.grouped.values[0, 0, 0] == 8
