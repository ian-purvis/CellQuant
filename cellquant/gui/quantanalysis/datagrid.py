"""The column data table sheet: an editable grid, one column per group, like Prism's Column table.

Blank cells are missing values. Paste a block from Excel (tab-separated) at the selected cell; a
first line of words becomes the column titles. Double-click a title to rename it. Editing a table
imported from CellQuant detaches it: it keeps its numbers but no longer follows the experiment.
"""

from __future__ import annotations

import html

import numpy as np
from qtpy.QtCore import QEvent, Qt
from qtpy.QtGui import QKeySequence
from qtpy.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from cellquant.gui.quantanalysis import graphs, tips
from cellquant.gui.quantanalysis.project import DataSheet, Project, table_with

SPARE_ROWS = 12
WARNING_STYLE = "QLabel { background: rgba(217, 164, 0, 0.20); border-radius: 6px; padding: 6px; }"
DANGER_STYLE = "QLabel { background: rgba(200, 50, 50, 0.28); border-radius: 6px; padding: 6px; }"
NOTE_STYLE = "QLabel { color: #a8b0ba; }"


def parse_cell(text: str) -> float:
    text = text.strip().replace(",", ".") if text.count(",") == 1 and "." not in text else text.strip()
    if not text:
        return np.nan
    return float(text)


def _trim(values: np.ndarray) -> np.ndarray:
    """A column without the blank cells below its last value."""

    filled = np.flatnonzero(np.isfinite(values))
    return values[: filled[-1] + 1] if filled.size else values[:0]


class DataSheetView(QWidget):
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
        self.stale_note = QLabel("The experiment changed (approvals, cutoffs or results) since this import.")
        self.stale_note.setStyleSheet(WARNING_STYLE)
        self.stale_note.setWordWrap(True)
        refresh = QPushButton("Refresh")
        refresh.setToolTip("Import again with the same choices, so this table, its results and graphs show the experiment as it is now.")
        refresh.clicked.connect(lambda: window.refresh_imports(force=True))
        stale_row.addWidget(self.stale_note, 1)
        stale_row.addWidget(refresh)
        layout.addWidget(self.stale)
        self.warnings = QLabel()
        self.warnings.setWordWrap(True)
        self.warnings.setTextFormat(Qt.RichText)
        layout.addWidget(self.warnings)

        bar = QHBoxLayout()
        add = QPushButton("Add column")
        add.setToolTip("Add an empty column (a new group) at the right.")
        add.clicked.connect(self.add_column)
        remove = QPushButton("Remove column")
        remove.setToolTip("Remove the column of the selected cell. Results that used it say so.")
        remove.clicked.connect(self.remove_column)
        self.paired = QCheckBox("Paired")
        self.paired.setToolTip(tips.PAIRED)
        self.paired.toggled.connect(self._paired_toggled)
        bar.addWidget(add)
        bar.addWidget(remove)
        bar.addWidget(self.paired)
        bar.addStretch(1)
        for text, tip, slot in (
            ("Column statistics", "n, mean, SD, SEM, CI, median and percentiles of every column, on a new results sheet.", lambda: window.analyze("colstats")),
            ("Normality", "Normality and lognormality tests of every column. Look at the graph and the n too: small samples pass easily, large ones fail for tiny departures.", lambda: window.analyze("normality")),
            ("Compare two columns…", "t test or rank test of two columns (Prism's t test dialog).", lambda: window.analyze("compare")),
        ):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            bar.addWidget(button)
        graph = QToolButton()
        graph.setText("Graph ▾")
        graph.setToolTip("Draw this table on a new graph sheet.")
        graph.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(graph)
        for key, (name, tip) in graphs.KINDS.items():
            action = menu.addAction(name)
            action.setToolTip(tip)
            action.triggered.connect(lambda _checked=False, key=key: window.new_graph(key))
        menu.setToolTipsVisible(True)
        graph.setMenu(menu)
        bar.addWidget(graph)
        layout.addLayout(bar)

        self.grid = QTableWidget()
        self.grid.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.grid.horizontalHeader().setSectionsClickable(True)
        self.grid.horizontalHeader().sectionDoubleClicked.connect(self._rename)
        self.grid.horizontalHeader().setToolTip("Double-click a title to rename the column.")
        self.grid.horizontalHeader().setDefaultSectionSize(118)
        self.grid.itemChanged.connect(self._edited)
        self.grid.installEventFilter(self)
        layout.addWidget(self.grid, 1)
        self.notes = QLabel()
        self.notes.setWordWrap(True)
        self.notes.setStyleSheet(NOTE_STYLE)
        layout.addWidget(self.notes)
        self.fill()

    # --- Showing the sheet ----------------------------------------------------------------------------------------

    def fill(self) -> None:
        sheet = self.sheet
        table = sheet.table
        self._filling = True
        rows = max([column.values.size for column in table.columns] + [0]) + SPARE_ROWS
        self.grid.clear()
        self.grid.setColumnCount(len(table.columns))
        self.grid.setRowCount(max(rows, 24))
        self.grid.setHorizontalHeaderLabels(table.names)
        labels = [sheet.row_labels[row] if row < len(sheet.row_labels) and sheet.row_labels[row] else str(row + 1)
                  for row in range(self.grid.rowCount())]
        self.grid.setVerticalHeaderLabels(labels)
        for index, column in enumerate(table.columns):
            for row, value in enumerate(column.values):
                if np.isfinite(value):
                    self.grid.setItem(row, index, QTableWidgetItem(f"{value:.6g}"))
        self.paired.blockSignals(True)
        self.paired.setChecked(table.paired)
        self.paired.blockSignals(False)
        self._filling = False
        self.description.setText(sheet.description or "Type or paste values: one column per group, blank cells are missing values.")
        self.description.setToolTip("\n".join(sheet.notes))
        self.notes.setText(" ".join(sheet.notes))
        self.stale.setVisible(sheet.stale)
        danger = sheet.pseudoreplicated
        text = "<br>".join(f"⚠ {html.escape(item)}" for item in sheet.warnings)
        self.warnings.setText(text)
        self.warnings.setStyleSheet(DANGER_STYLE if danger else WARNING_STYLE)
        self.warnings.setVisible(bool(text))

    # --- Editing ------------------------------------------------------------------------------------------------

    def _column_values(self) -> list[np.ndarray]:
        out = []
        for index in range(self.grid.columnCount()):
            values = []
            for row in range(self.grid.rowCount()):
                item = self.grid.item(row, index)
                text = item.text() if item is not None else ""
                try:
                    values.append(parse_cell(text))
                except ValueError:
                    values.append(np.nan)
            out.append(_trim(np.asarray(values, float)))
        return out

    def _names(self) -> list[str]:
        return [self.grid.horizontalHeaderItem(index).text() if self.grid.horizontalHeaderItem(index) else f"Column {index + 1}"
                for index in range(self.grid.columnCount())]

    def commit(self, names: list[str] | None = None, values: list[np.ndarray] | None = None, paired: bool | None = None) -> None:
        """Write the grid back to the sheet and recompute everything linked to it."""

        sheet = self.sheet
        names = names if names is not None else self._names()
        values = [_trim(np.asarray(column, float)) for column in values] if values is not None else self._column_values()
        paired = self.paired.isChecked() if paired is None else paired
        try:
            sheet.table = table_with(sheet.table, names, values, paired)
        except Exception as exc:  # noqa: BLE001 - a repeated title, for example; the grid shows the table as it was
            self.window.status(str(exc))
            self.fill()
            return
        if sheet.spec is not None:
            sheet.spec = None
            sheet.images = None
            sheet.units = None
            sheet.stale = False
            sheet.notes = [*sheet.notes, "Edited by hand: no longer follows the CellQuant results."]
        self.project.data_changed(sheet.id)

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
        if item.row() >= self.grid.rowCount() - 3:
            self._filling = True
            self.grid.setRowCount(self.grid.rowCount() + SPARE_ROWS)
            self._filling = False
        self.commit()

    def _paired_toggled(self, on: bool) -> None:
        self.commit(paired=on)

    def add_column(self) -> None:
        names = self._names()
        number = len(names) + 1
        while f"Group {number}" in names:
            number += 1
        self.commit(names + [f"Group {number}"], self._column_values() + [np.zeros(0)])
        self.fill()

    def remove_column(self) -> None:
        index = self.grid.currentColumn()
        if index < 0:
            self.window.status("Click a cell of the column to remove first.")
            return
        names, values = self._names(), self._column_values()
        del names[index]
        del values[index]
        self.commit(names, values)
        self.fill()

    def _rename(self, index: int) -> None:
        header = self.grid.horizontalHeader()
        editor = QLineEdit(header)
        editor.setText(self._names()[index])
        editor.setGeometry(header.sectionViewportPosition(index), 0, header.sectionSize(index), header.height())
        editor.selectAll()
        editor.setFocus()

        def done() -> None:
            name = editor.text().strip()
            editor.deleteLater()
            if not name:
                return
            names = self._names()
            if name != names[index]:
                if name in names:
                    self.window.status(f"There is already a column called {name}.")
                    return
                names[index] = name
                self.commit(names)
                self.fill()

        editor.editingFinished.connect(done)
        editor.show()

    def eventFilter(self, watched, event) -> bool:
        if watched is self.grid and event.type() == QEvent.KeyPress:
            if event.matches(QKeySequence.Paste):
                self.paste(QApplication.clipboard().text())
                return True
            if event.matches(QKeySequence.Copy):
                self.copy()
                return True
            if event.key() in (Qt.Key_Delete, Qt.Key_Backspace) and not self.grid.state() == QAbstractItemView.EditingState:
                self._filling = True
                for item in self.grid.selectedItems():
                    item.setText("")
                self._filling = False
                self.commit()
                return True
        return super().eventFilter(watched, event)

    def copy(self) -> None:
        ranges = self.grid.selectedRanges()
        if not ranges:
            return
        block = ranges[0]
        lines = []
        for row in range(block.topRow(), block.bottomRow() + 1):
            cells = []
            for column in range(block.leftColumn(), block.rightColumn() + 1):
                item = self.grid.item(row, column)
                cells.append(item.text() if item else "")
            lines.append("\t".join(cells))
        QApplication.clipboard().setText("\n".join(lines))

    def paste(self, text: str) -> None:
        """Paste a tab-separated block at the selected cell. A first line of words becomes the titles."""

        lines = [line.split("\t") for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
        while lines and not any(cell.strip() for cell in lines[-1]):
            lines.pop()
        if not lines:
            return
        top = max(0, self.grid.currentRow())
        left = max(0, self.grid.currentColumn())
        names = self._names()
        titles = None

        def is_number(cell: str) -> bool:
            try:
                parse_cell(cell)
                return True
            except ValueError:
                return False

        if not all(is_number(cell) for cell in lines[0]):
            titles, lines = lines[0], lines[1:]
            top = 0
        width = max([len(line) for line in lines] + [len(titles or [])])
        values = self._column_values()
        while len(names) < left + width:
            number = len(names) + 1
            while f"Group {number}" in names:
                number += 1
            names.append(f"Group {number}")
            values.append(np.zeros(0))
        if titles:
            for offset, title in enumerate(titles):
                title = title.strip()
                if title and title not in names[:left + offset] + names[left + offset + 1:]:
                    names[left + offset] = title
        for offset in range(width):
            column = list(values[left + offset])
            for row, line in enumerate(lines):
                cell = line[offset] if offset < len(line) else ""
                target = top + row
                while len(column) <= target:
                    column.append(np.nan)
                try:
                    column[target] = parse_cell(cell)
                except ValueError:
                    column[target] = np.nan
            values[left + offset] = np.asarray(column, float)
        self.commit(names, values)
        self.fill()
