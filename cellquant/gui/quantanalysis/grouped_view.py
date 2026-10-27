"""The grouped data table sheet: one row per level of the row factor, one data set per level of the column factor,
and each data set's replicates in side-by-side subcolumns.

Blank cells are missing values. Paste a block of numbers (tab-separated) at the selected cell. Double-click a row
title or a data set title to rename it. Editing a table imported from CellQuant detaches it: it keeps its numbers
but no longer follows the experiment.
"""

from __future__ import annotations

import html

import numpy as np
from qtpy.QtCore import QEvent, Qt
from qtpy.QtGui import QKeySequence
from qtpy.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cellquant.errors import CellQuantError
from cellquant.gui.quantanalysis import tips
from cellquant.gui.quantanalysis.datagrid import DANGER_STYLE, NOTE_STYLE, WARNING_STYLE, parse_cell
from cellquant.gui.quantanalysis.project import DataSheet, Project, set_grouped
from cellquant.stats.grouped import GroupedTable


def _unique(base: str, names: list[str]) -> str:
    number = len(names) + 1
    while f"{base} {number}" in names:
        number += 1
    return f"{base} {number}"


class GroupedSheetView(QWidget):
    def __init__(self, window, project: Project, sheet: DataSheet):
        super().__init__()
        self.window = window
        self.project = project
        self.sheet = sheet
        self._filling = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        self.description = QLabel()
        self.description.setWordWrap(True)
        self.description.setStyleSheet(NOTE_STYLE)
        layout.addWidget(self.description)
        self.stale = QWidget()
        stale_row = QHBoxLayout(self.stale)
        stale_row.setContentsMargins(0, 0, 0, 0)
        stale_note = QLabel("The experiment changed (approvals, cutoffs or results) since this import.")
        stale_note.setStyleSheet(WARNING_STYLE)
        stale_note.setWordWrap(True)
        refresh = QPushButton("Refresh")
        refresh.setToolTip("Import again with the same choices, so this table, its results and graphs show the experiment as it is now.")
        refresh.clicked.connect(lambda: window.refresh_imports(force=True))
        stale_row.addWidget(stale_note, 1)
        stale_row.addWidget(refresh)
        layout.addWidget(self.stale)
        self.warnings = QLabel()
        self.warnings.setWordWrap(True)
        self.warnings.setTextFormat(Qt.RichText)
        layout.addWidget(self.warnings)
        bar = QHBoxLayout()
        for text, tip, slot in (
            ("Add row", tips.GROUPED_ADD_ROW, self.add_row),
            ("Add data set", tips.GROUPED_ADD_SET, self.add_data_set),
            ("Add replicate", tips.GROUPED_ADD_REPLICATE, self.add_replicate),
            ("Remove row", "Remove the row of the selected cell.", self.remove_row),
            ("Remove data set", "Remove the data set of the selected cell.", self.remove_data_set),
        ):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(lambda _checked=False, slot=slot: slot())
            bar.addWidget(button)
        bar.addStretch(1)
        analyze = QPushButton("Two-way ANOVA…")
        analyze.setToolTip(tips.TWO_WAY)
        analyze.clicked.connect(lambda: window.analyze("twoway"))
        bar.addWidget(analyze)
        layout.addLayout(bar)
        self.grid = QTableWidget()
        self.grid.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.grid.horizontalHeader().sectionDoubleClicked.connect(self._rename_data_set)
        self.grid.horizontalHeader().setToolTip("Double-click a title to rename its data set.")
        self.grid.verticalHeader().sectionDoubleClicked.connect(self._rename_row)
        self.grid.verticalHeader().setToolTip("Double-click a row title to rename it.")
        self.grid.horizontalHeader().setDefaultSectionSize(84)
        self.grid.itemChanged.connect(self._edited)
        self.grid.installEventFilter(self)
        layout.addWidget(self.grid, 1)
        self.notes = QLabel()
        self.notes.setWordWrap(True)
        self.notes.setStyleSheet(NOTE_STYLE)
        layout.addWidget(self.notes)
        self.fill()

    @property
    def grouped(self) -> GroupedTable:
        return self.sheet.grouped

    # --- Showing the sheet ----------------------------------------------------------------------------------------

    def fill(self) -> None:
        sheet, grouped = self.sheet, self.grouped
        if grouped is None:
            return
        depth = grouped.replicates
        self._filling = True
        self.grid.clear()
        self.grid.setRowCount(len(grouped.rows))
        self.grid.setColumnCount(len(grouped.columns) * depth)
        self.grid.setHorizontalHeaderLabels([f"{name}\n{k + 1}" for name in grouped.columns for k in range(depth)])
        self.grid.setVerticalHeaderLabels(grouped.rows)
        for i in range(len(grouped.rows)):
            for j in range(len(grouped.columns)):
                for k in range(depth):
                    value = grouped.values[i, j, k]
                    if np.isfinite(value):
                        self.grid.setItem(i, j * depth + k, QTableWidgetItem(f"{value:.6g}"))
        self._filling = False
        self.description.setText(sheet.description or tips.GROUPED_TABLE)
        self.description.setToolTip("\n".join(sheet.notes))
        self.notes.setText(" ".join(sheet.notes))
        self.stale.setVisible(sheet.stale)
        text = "<br>".join(f"⚠ {html.escape(item)}" for item in sheet.warnings)
        self.warnings.setText(text)
        self.warnings.setStyleSheet(DANGER_STYLE if sheet.pseudoreplicated else WARNING_STYLE)
        self.warnings.setVisible(bool(text))

    # --- Editing ------------------------------------------------------------------------------------------------

    def _values(self) -> np.ndarray:
        grouped = self.grouped
        depth = grouped.replicates
        values = np.full((len(grouped.rows), len(grouped.columns), depth), np.nan)
        for i in range(len(grouped.rows)):
            for column in range(len(grouped.columns) * depth):
                item = self.grid.item(i, column)
                try:
                    values[i, column // depth, column % depth] = parse_cell(item.text() if item is not None else "")
                except ValueError:
                    pass
        return values

    def commit(self, rows: list[str] | None = None, columns: list[str] | None = None, values: np.ndarray | None = None) -> bool:
        """Write the grid (or the given parts) back to the sheet and recompute everything linked to it."""

        grouped = self.grouped
        try:
            new = GroupedTable(grouped.title, list(rows if rows is not None else grouped.rows),
                               list(columns if columns is not None else grouped.columns), self._values() if values is None else values)
        except CellQuantError as exc:
            self.window.status(str(exc))
            self.fill()
            return False
        set_grouped(self.sheet, new)
        sheet = self.sheet
        if sheet.spec is not None:
            sheet.spec, sheet.images, sheet.units, sheet.stale = None, None, None, False
            sheet.notes = [*sheet.notes, "Edited by hand: no longer follows the CellQuant results."]
        self.project.data_changed(sheet.id)
        return True

    def _edited(self, item: QTableWidgetItem) -> None:
        if self._filling:
            return
        text = item.text()
        if text.strip():
            try:
                parse_cell(text)
            except ValueError:
                self.window.status(f"“{text}” is not a number; the cell is left blank.")
                self._filling = True
                item.setText("")
                self._filling = False
        self.commit()

    def _resized(self, values: np.ndarray, rows: list[str], columns: list[str]) -> None:
        if self.commit(rows, columns, values):
            self.fill()

    def add_row(self) -> None:
        grouped = self.grouped
        values = np.concatenate([grouped.values, np.full((1, *grouped.values.shape[1:]), np.nan)], axis=0)
        self._resized(values, [*grouped.rows, _unique("Row", grouped.rows)], grouped.columns)

    def add_data_set(self) -> None:
        grouped = self.grouped
        values = np.concatenate([grouped.values, np.full((grouped.values.shape[0], 1, grouped.values.shape[2]), np.nan)], axis=1)
        self._resized(values, grouped.rows, [*grouped.columns, _unique("Data set", grouped.columns)])

    def add_replicate(self) -> None:
        grouped = self.grouped
        values = np.concatenate([grouped.values, np.full((*grouped.values.shape[:2], 1), np.nan)], axis=2)
        self._resized(values, grouped.rows, grouped.columns)

    def remove_row(self) -> None:
        grouped, index = self.grouped, self.grid.currentRow()
        if index < 0 or len(grouped.rows) <= 1:
            self.window.status("Click a cell of the row to remove first (a table keeps at least one row).")
            return
        self._resized(np.delete(grouped.values, index, axis=0), [name for n, name in enumerate(grouped.rows) if n != index], grouped.columns)

    def remove_data_set(self) -> None:
        grouped, column = self.grouped, self.grid.currentColumn()
        if column < 0 or len(grouped.columns) <= 1:
            self.window.status("Click a cell of the data set to remove first (a table keeps at least one data set).")
            return
        index = column // grouped.replicates
        self._resized(np.delete(grouped.values, index, axis=1), grouped.rows, [name for n, name in enumerate(grouped.columns) if n != index])

    def _rename_row(self, index: int) -> None:
        self._rename(index, "row")

    def _rename_data_set(self, column: int) -> None:
        self._rename(column // max(1, self.grouped.replicates), "column")

    def _rename(self, index: int, what: str, name: str | None = None) -> None:
        grouped = self.grouped
        names = list(grouped.rows if what == "row" else grouped.columns)
        if name is None:
            name, ok = QInputDialog.getText(self, "Rename", "Row title" if what == "row" else "Data set title", text=names[index])
            if not ok:
                return
        name = name.strip()
        if not name or name == names[index]:
            return
        if name in names:
            self.window.status(f"There is already a {'row' if what == 'row' else 'data set'} called {name}.")
            return
        names[index] = name
        self._resized(grouped.values.copy(), names if what == "row" else grouped.rows, names if what == "column" else grouped.columns)

    def eventFilter(self, watched, event) -> bool:
        if watched is self.grid and event.type() == QEvent.KeyPress:
            if event.matches(QKeySequence.Paste):
                self.paste(QApplication.clipboard().text())
                return True
            if event.key() in (Qt.Key_Delete, Qt.Key_Backspace) and not self.grid.state() == QAbstractItemView.EditingState:
                self._filling = True
                for item in self.grid.selectedItems():
                    item.setText("")
                self._filling = False
                self.commit()
                return True
        return super().eventFilter(watched, event)

    def paste(self, text: str) -> None:
        """Paste a tab-separated block of numbers at the selected cell; the table grows to hold it."""

        lines = [line.split("\t") for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
        while lines and not any(cell.strip() for cell in lines[-1]):
            lines.pop()
        if not lines:
            return
        grouped = self.grouped
        depth = grouped.replicates
        top, left = max(0, self.grid.currentRow()), max(0, self.grid.currentColumn())
        width = max(len(line) for line in lines)
        rows, columns, values = list(grouped.rows), list(grouped.columns), grouped.values.copy()
        while len(rows) < top + len(lines):
            rows.append(_unique("Row", rows))
            values = np.concatenate([values, np.full((1, *values.shape[1:]), np.nan)], axis=0)
        while len(columns) * depth < left + width:
            columns.append(_unique("Data set", columns))
            values = np.concatenate([values, np.full((values.shape[0], 1, depth), np.nan)], axis=1)
        for r, line in enumerate(lines):
            for offset in range(width):
                cell = line[offset] if offset < len(line) else ""
                column = left + offset
                try:
                    values[top + r, column // depth, column % depth] = parse_cell(cell)
                except ValueError:
                    values[top + r, column // depth, column % depth] = np.nan
        self._resized(values, rows, columns)
