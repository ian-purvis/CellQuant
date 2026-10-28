"""The XY data table sheet: an X column, then one or more Y data sets with their replicates in side-by-side
subcolumns. Each row is one X value.

Blank cells are missing values; a row without X is left out of the analyses. Paste a block of numbers
(tab-separated) at the selected cell. Double-click the X title or a data set title to rename it.
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
from cellquant.gui.quantanalysis.project import DataSheet, Project, set_xy
from cellquant.stats.xy import XYTable


class XYSheetView(QWidget):
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
            ("Add row", "Add a row (one more X value) at the bottom.", self.add_row),
            ("Add data set", tips.XY_ADD_SET, self.add_data_set),
            ("Add replicate", tips.XY_ADD_REPLICATE, self.add_replicate),
            ("Remove row", "Remove the row of the selected cell.", self.remove_row),
            ("Remove data set", "Remove the data set of the selected cell.", self.remove_data_set),
        ):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(lambda _checked=False, slot=slot: slot())
            bar.addWidget(button)
        bar.addStretch(1)
        for text, tip, kind in (("Linear regression…", tips.LINREG, "linreg"), ("Fit a curve…", tips.CURVEFIT, "curvefit"),
                                ("Correlation…", tips.XYCORR, "xycorr")):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(lambda _checked=False, kind=kind: window.analyze(kind))
            bar.addWidget(button)
        layout.addLayout(bar)
        self.grid = QTableWidget()
        self.grid.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.grid.horizontalHeader().sectionDoubleClicked.connect(self._rename_column)
        self.grid.horizontalHeader().setToolTip("Double-click a title to rename X or a data set.")
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
    def xy(self) -> XYTable:
        return self.sheet.xy

    # --- Showing the sheet ----------------------------------------------------------------------------------------

    def fill(self) -> None:
        xy = self.xy
        if xy is None:
            return
        depth = xy.replicates
        self._filling = True
        self.grid.clear()
        self.grid.setRowCount(xy.x.size)
        self.grid.setColumnCount(1 + len(xy.sets) * depth)
        self.grid.setHorizontalHeaderLabels([f"{xy.x_title}\nX"] + [f"{name}\n{k + 1}" for name in xy.sets for k in range(depth)])
        for i in range(xy.x.size):
            if np.isfinite(xy.x[i]):
                self.grid.setItem(i, 0, QTableWidgetItem(f"{xy.x[i]:.6g}"))
            for j in range(len(xy.sets)):
                for k in range(depth):
                    value = xy.values[i, j, k]
                    if np.isfinite(value):
                        self.grid.setItem(i, 1 + j * depth + k, QTableWidgetItem(f"{value:.6g}"))
        self._filling = False
        self.description.setText(self.sheet.description or tips.XY_TABLE)
        missing = int((~np.isfinite(xy.x) & np.isfinite(xy.values).any(axis=(1, 2))).sum())
        self.warnings.setText(html.escape(f"⚠ {missing} row(s) have Y values but no X; they are left out of the fits (a curve fit can interpolate their X as "
                                                f"unknowns).") if missing else "")
        self.warnings.setVisible(bool(missing))
        self.notes.setText(" ".join(self.sheet.notes))

    # --- Editing ------------------------------------------------------------------------------------------------

    def _read(self) -> tuple[np.ndarray, np.ndarray]:
        xy = self.xy
        depth = xy.replicates
        x = np.full(xy.x.size, np.nan)
        values = np.full(xy.values.shape, np.nan)
        for i in range(xy.x.size):
            for column in range(self.grid.columnCount()):
                item = self.grid.item(i, column)
                try:
                    value = parse_cell(item.text() if item is not None else "")
                except ValueError:
                    continue
                if column == 0:
                    x[i] = value
                else:
                    values[i, (column - 1) // depth, (column - 1) % depth] = value
        return x, values

    def commit(self, x: np.ndarray | None = None, sets: list[str] | None = None, values: np.ndarray | None = None,
               x_title: str | None = None) -> bool:
        """Write the grid (or the given parts) back to the sheet and recompute everything linked to it."""

        xy = self.xy
        if x is None or values is None:
            read_x, read_values = self._read()
            x = read_x if x is None else x
            values = read_values if values is None else values
        try:
            new = XYTable(xy.title, xy.x_title if x_title is None else x_title, x, list(sets if sets is not None else xy.sets), values)
        except CellQuantError as exc:
            self.window.status(str(exc))
            self.fill()
            return False
        set_xy(self.sheet, new)
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
        self.commit()

    def _resized(self, x: np.ndarray, values: np.ndarray, sets: list[str], x_title: str | None = None) -> None:
        if self.commit(x, sets, values, x_title):
            self.fill()

    def add_row(self) -> None:
        xy = self.xy
        self._resized(np.append(xy.x, np.nan), np.concatenate([xy.values, np.full((1, *xy.values.shape[1:]), np.nan)]), xy.sets)

    def add_data_set(self) -> None:
        xy = self.xy
        values = np.concatenate([xy.values, np.full((xy.values.shape[0], 1, xy.values.shape[2]), np.nan)], axis=1)
        self._resized(xy.x, values, [*xy.sets, _unique("Data set", xy.sets)])

    def add_replicate(self) -> None:
        xy = self.xy
        self._resized(xy.x, np.concatenate([xy.values, np.full((*xy.values.shape[:2], 1), np.nan)], axis=2), xy.sets)

    def remove_row(self) -> None:
        xy, index = self.xy, self.grid.currentRow()
        if index < 0 or xy.x.size <= 1:
            self.window.status("Click a cell of the row to remove first (a table keeps at least one row).")
            return
        self._resized(np.delete(xy.x, index), np.delete(xy.values, index, axis=0), xy.sets)

    def remove_data_set(self) -> None:
        xy, column = self.xy, self.grid.currentColumn()
        if column < 1 or len(xy.sets) <= 1:
            self.window.status("Click a Y cell of the data set to remove first (a table keeps at least one data set).")
            return
        index = (column - 1) // xy.replicates
        self._resized(xy.x, np.delete(xy.values, index, axis=1), [name for n, name in enumerate(xy.sets) if n != index])

    def _rename_column(self, column: int) -> None:
        self._rename(column, None)

    def _rename(self, column: int, name: str | None = None) -> None:
        """Rename X (column 0) or the data set of ``column``."""

        xy = self.xy
        current = xy.x_title if column == 0 else xy.sets[(column - 1) // xy.replicates]
        if name is None:
            name, ok = QInputDialog.getText(self, "Rename", "X title" if column == 0 else "Data set title", text=current)
            if not ok:
                return
        name = name.strip()
        if not name or name == current:
            return
        if column == 0:
            self._resized(xy.x.copy(), xy.values.copy(), xy.sets, name)
            return
        if name in xy.sets:
            self.window.status(f"There is already a data set called {name}.")
            return
        sets = [name if item == current else item for item in xy.sets]
        self._resized(xy.x.copy(), xy.values.copy(), sets)

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
        """Paste a tab-separated block of numbers at the selected cell (X first when pasting into the X column); the
        table grows to hold it."""

        lines = [line.split("\t") for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
        while lines and not any(cell.strip() for cell in lines[-1]):
            lines.pop()
        if not lines:
            return
        xy = self.xy
        depth = xy.replicates
        top, left = max(0, self.grid.currentRow()), max(0, self.grid.currentColumn())
        width = max(len(line) for line in lines)
        x, sets, values = xy.x.copy(), list(xy.sets), xy.values.copy()
        while x.size < top + len(lines):
            x = np.append(x, np.nan)
            values = np.concatenate([values, np.full((1, *values.shape[1:]), np.nan)])
        while 1 + len(sets) * depth < left + width:
            sets.append(_unique("Data set", sets))
            values = np.concatenate([values, np.full((values.shape[0], 1, depth), np.nan)], axis=1)
        for r, line in enumerate(lines):
            for offset in range(width):
                cell = line[offset] if offset < len(line) else ""
                try:
                    value = parse_cell(cell)
                except ValueError:
                    value = np.nan
                column = left + offset
                if column == 0:
                    x[top + r] = value
                else:
                    values[top + r, (column - 1) // depth, (column - 1) % depth] = value
        self._resized(x, values, sets)
