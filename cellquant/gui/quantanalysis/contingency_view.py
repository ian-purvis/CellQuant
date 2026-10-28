"""The contingency table sheet: counts of subjects, one row per group (or test result) and one column per outcome.

Each cell is a whole number of subjects (0 for none). Paste a block of counts (tab-separated) at the selected cell.
Double-click a row or column title to rename it.
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
from cellquant.gui.quantanalysis.datagrid import NOTE_STYLE, WARNING_STYLE, parse_cell
from cellquant.gui.quantanalysis.grouped_view import _unique
from cellquant.gui.quantanalysis.project import DataSheet, Project, set_contingency
from cellquant.stats.contingency import ContingencyTable


class ContingencySheetView(QWidget):
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
        self.warnings = QLabel()
        self.warnings.setWordWrap(True)
        self.warnings.setTextFormat(Qt.RichText)
        self.warnings.setStyleSheet(WARNING_STYLE)
        layout.addWidget(self.warnings)
        bar = QHBoxLayout()
        for text, tip, slot in (
            ("Add row", "Add a row (another group or test result) at the bottom.", self.add_row),
            ("Add column", "Add a column (another outcome) at the right.", self.add_column),
            ("Remove row", "Remove the row of the selected cell.", self.remove_row),
            ("Remove column", "Remove the column of the selected cell.", self.remove_column),
        ):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(lambda _checked=False, slot=slot: slot())
            bar.addWidget(button)
        bar.addStretch(1)
        analyze = QPushButton("Analyze contingency table…")
        analyze.setToolTip(tips.CONTINGENCY)
        analyze.clicked.connect(lambda _checked=False: window.analyze("contingency"))
        bar.addWidget(analyze)
        layout.addLayout(bar)
        self.grid = QTableWidget()
        self.grid.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.grid.horizontalHeader().sectionDoubleClicked.connect(lambda column: self._rename("column", column))
        self.grid.verticalHeader().sectionDoubleClicked.connect(lambda row: self._rename("row", row))
        self.grid.horizontalHeader().setToolTip("Double-click a title to rename the outcome.")
        self.grid.verticalHeader().setToolTip("Double-click a title to rename the group.")
        self.grid.horizontalHeader().setDefaultSectionSize(110)
        self.grid.itemChanged.connect(self._edited)
        self.grid.installEventFilter(self)
        layout.addWidget(self.grid, 1)
        self.totals = QLabel()
        self.totals.setWordWrap(True)
        self.totals.setStyleSheet(NOTE_STYLE)
        layout.addWidget(self.totals)
        self.fill()

    @property
    def counts(self) -> ContingencyTable:
        return self.sheet.contingency

    # --- Showing the sheet ----------------------------------------------------------------------------------------

    def fill(self) -> None:
        table = self.counts
        if table is None:
            return
        self._filling = True
        self.grid.clear()
        self.grid.setRowCount(len(table.rows))
        self.grid.setColumnCount(len(table.columns))
        self.grid.setHorizontalHeaderLabels(table.columns)
        self.grid.setVerticalHeaderLabels(table.rows)
        for i in range(len(table.rows)):
            for j in range(len(table.columns)):
                value = table.counts[i, j]
                if np.isfinite(value):
                    self.grid.setItem(i, j, QTableWidgetItem(f"{value:.10g}"))
        self._filling = False
        self.description.setText(self.sheet.description or tips.CONTINGENCY_TABLE)
        problem = ""
        try:
            counts = table.checked()
        except CellQuantError as exc:
            problem = str(exc)
            counts = None
        self.warnings.setText(html.escape(f"⚠ {problem}") if problem else "")
        self.warnings.setVisible(bool(problem))
        if counts is not None:
            rows = ", ".join(f"{name} {total}" for name, total in zip(table.rows, counts.sum(axis=1)))
            self.totals.setText(f"Row totals: {rows}. Total: {int(counts.sum())}.")
        else:
            self.totals.setText("")

    # --- Editing ------------------------------------------------------------------------------------------------

    def _read(self) -> np.ndarray:
        table = self.counts
        counts = np.full(table.counts.shape, np.nan)
        for i in range(counts.shape[0]):
            for j in range(counts.shape[1]):
                item = self.grid.item(i, j)
                try:
                    counts[i, j] = parse_cell(item.text() if item is not None else "")
                except ValueError:
                    continue
        return counts

    def commit(self, counts: np.ndarray | None = None, rows: list[str] | None = None, columns: list[str] | None = None) -> bool:
        """Write the grid (or the given parts) back to the sheet and recompute everything linked to it."""

        table = self.counts
        try:
            new = ContingencyTable(table.title, list(rows if rows is not None else table.rows), list(columns if columns is not None else table.columns),
                                   self._read() if counts is None else counts)
        except CellQuantError as exc:
            self.window.status(str(exc))
            self.fill()
            return False
        set_contingency(self.sheet, new)
        self.project.data_changed(self.sheet.id)
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
        if self.commit():
            self.fill()

    def _resized(self, counts: np.ndarray, rows: list[str], columns: list[str]) -> None:
        if self.commit(counts, rows, columns):
            self.fill()

    def add_row(self) -> None:
        table = self.counts
        self._resized(np.vstack([table.counts, np.full((1, len(table.columns)), np.nan)]), [*table.rows, _unique("Group", table.rows)],
                      table.columns)

    def add_column(self) -> None:
        table = self.counts
        self._resized(np.hstack([table.counts, np.full((len(table.rows), 1), np.nan)]), table.rows,
                      [*table.columns, _unique("Outcome", table.columns)])

    def remove_row(self) -> None:
        table, index = self.counts, self.grid.currentRow()
        if index < 0 or len(table.rows) <= 2:
            self.window.status("Click a cell of the row to remove first (a table keeps at least two rows).")
            return
        self._resized(np.delete(table.counts, index, axis=0), [name for n, name in enumerate(table.rows) if n != index], table.columns)

    def remove_column(self) -> None:
        table, index = self.counts, self.grid.currentColumn()
        if index < 0 or len(table.columns) <= 2:
            self.window.status("Click a cell of the column to remove first (a table keeps at least two columns).")
            return
        self._resized(np.delete(table.counts, index, axis=1), table.rows, [name for n, name in enumerate(table.columns) if n != index])

    def _rename(self, which: str, index: int, name: str | None = None) -> None:
        """Rename row or column ``index``."""

        table = self.counts
        names = table.rows if which == "row" else table.columns
        current = names[index]
        if name is None:
            name, ok = QInputDialog.getText(self, "Rename", "Group (row) title" if which == "row" else "Outcome (column) title", text=current)
            if not ok:
                return
        name = name.strip()
        if not name or name == current:
            return
        if name in names:
            self.window.status(f"There is already a {which} called {name}.")
            return
        renamed = [name if item == current else item for item in names]
        self._resized(table.counts.copy(), renamed if which == "row" else table.rows, renamed if which == "column" else table.columns)

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
                if self.commit():
                    self.fill()
                return True
        return super().eventFilter(watched, event)

    def paste(self, text: str) -> None:
        """Paste a tab-separated block of counts at the selected cell; the table grows to hold it."""

        lines = [line.split("\t") for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
        while lines and not any(cell.strip() for cell in lines[-1]):
            lines.pop()
        if not lines:
            return
        table = self.counts
        top, left = max(0, self.grid.currentRow()), max(0, self.grid.currentColumn())
        width = max(len(line) for line in lines)
        counts, rows, columns = table.counts.copy(), list(table.rows), list(table.columns)
        while len(rows) < top + len(lines):
            rows.append(_unique("Group", rows))
            counts = np.vstack([counts, np.full((1, counts.shape[1]), np.nan)])
        while len(columns) < left + width:
            columns.append(_unique("Outcome", columns))
            counts = np.hstack([counts, np.full((counts.shape[0], 1), np.nan)])
        for r, line in enumerate(lines):
            for offset in range(width):
                try:
                    counts[top + r, left + offset] = parse_cell(line[offset] if offset < len(line) else "")
                except ValueError:
                    counts[top + r, left + offset] = np.nan
        self._resized(counts, rows, columns)
