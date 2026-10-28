"""The column data table sheet: an editable grid, one column per group, like a column table.

Blank cells are missing values. Paste a block from a spreadsheet (tab-separated) at the selected cell; a
first line of words becomes the column titles. Double-click a title to rename it. Editing a table
imported from CellQuant detaches it: it keeps its numbers but no longer follows the experiment.

Replicates adds a first column naming each row's biological replicate (a blank continues the one above),
so the table can hold every value (for example every cell) while its analyses use one value per replicate
(N) and a SuperPlot shows both. A pasted block whose first column is words turns it on.
"""

from __future__ import annotations

import html

import numpy as np
from qtpy.QtCore import QEvent, Qt
from qtpy.QtGui import QBrush, QColor, QKeySequence
from qtpy.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from cellquant.gui.quantanalysis import graphs, tips
from cellquant.gui.quantanalysis.project import DataSheet, Project, set_replicates, table_with
from cellquant.stats import transforms
from cellquant.stats.replicates import SUMMARIES, ReplicateTable

SPARE_ROWS = 12
WARNING_STYLE = "QLabel { background: rgba(217, 164, 0, 0.20); border-radius: 6px; padding: 6px; }"
DANGER_STYLE = "QLabel { background: rgba(200, 50, 50, 0.28); border-radius: 6px; padding: 6px; }"
NOTE_STYLE = "QLabel { color: #a8b0ba; }"
OUTLIER_BACKGROUND = QColor(200, 60, 60, 120)
REPLICATE_BACKGROUND = QColor(90, 130, 190, 45)


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
        self.replicate_button = QPushButton("Replicates")
        self.replicate_button.setCheckable(True)
        self.replicate_button.setToolTip(tips.REPLICATES)
        self.replicate_button.toggled.connect(self._replicates_toggled)
        self.summary = QComboBox()
        for key, name in SUMMARIES.items():
            self.summary.addItem(f"Replicate {name.lower()}", key)
        self.summary.setToolTip(tips.REPLICATE_VALUE)
        self.summary.currentIndexChanged.connect(self._summary_changed)
        bar.addWidget(add)
        bar.addWidget(remove)
        bar.addWidget(self.paired)
        bar.addWidget(self.replicate_button)
        bar.addWidget(self.summary)
        bar.addStretch(1)
        for text, tip, slot in (
            ("Column statistics", "n, mean, SD, SEM, CI, median and percentiles of every column, on a new results sheet.", lambda: window.analyze("colstats")),
            ("Normality", "Normality and lognormality tests of every column. Look at the graph and the n too: small samples pass easily, large ones fail for tiny departures.", lambda: window.analyze("normality")),
            ("Compare two columns…", "t test or rank test of two columns (the t test dialog).", lambda: window.analyze("compare")),
            ("Compare 3+ columns…", "One-way ANOVA, Kruskal-Wallis or Friedman with multiple comparisons, for three or more columns.", lambda: window.analyze("anova")),
            ("Outliers…", "Find outliers in each column with ROUT or Grubbs' test. They are shaded here and hollow on graphs; this table is not changed.", lambda: window.analyze("outliers")),
        ):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            bar.addWidget(button)
        processing = QToolButton()
        processing.setText("Process ▾")
        processing.setToolTip("Make a new table from this one: transformed, normalized, as a fraction of a total, against a baseline "
                              "column, or a column computed from two others. It follows this table.")
        processing.setPopupMode(QToolButton.InstantPopup)
        process_menu = QMenu(processing)
        for kind, name in transforms.PROCESSES.items():
            action = process_menu.addAction(f"{name}…")
            action.setToolTip(tips.PROCESS[kind])
            action.triggered.connect(lambda _checked=False, kind=kind: window.process_data(kind))
        process_menu.setToolTipsVisible(True)
        processing.setMenu(process_menu)
        bar.addWidget(processing)
        self.change = QPushButton("Change processing…")
        self.change.setToolTip(tips.CHANGE_PROCESS)
        self.change.clicked.connect(lambda: window.change_process(self.sheet))
        bar.addWidget(self.change)
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
        self.replicate_note = QLabel()
        self.replicate_note.setWordWrap(True)
        layout.addWidget(self.replicate_note)

        self.grid = QTableWidget()
        self.grid.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.grid.horizontalHeader().setSectionsClickable(True)
        self.grid.horizontalHeader().sectionDoubleClicked.connect(self._rename)
        self.grid.horizontalHeader().setToolTip("Double-click a title to rename the column.")
        self.grid.horizontalHeader().setDefaultSectionSize(118)
        self.grid.itemChanged.connect(self._edited)
        self.grid.installEventFilter(self)
        layout.addWidget(self.grid, 1)
        self.flagged = QLabel()
        self.flagged.setWordWrap(True)
        self.flagged.setStyleSheet(WARNING_STYLE)
        layout.addWidget(self.flagged)
        self.notes = QLabel()
        self.notes.setWordWrap(True)
        self.notes.setStyleSheet(NOTE_STYLE)
        layout.addWidget(self.notes)
        self.fill()

    # --- Showing the sheet ----------------------------------------------------------------------------------------

    @property
    def offset(self) -> int:
        """1 while the Replicate column is shown (the data columns start after it), else 0."""

        return 1 if self.sheet.replicates is not None else 0

    def fill(self) -> None:
        sheet = self.sheet
        table = sheet.table
        replicates = sheet.replicates
        offset = self.offset
        names = replicates.names if replicates is not None else table.names
        columns = replicates.values if replicates is not None else [column.values for column in table.columns]
        self._filling = True
        rows = max([values.size for values in columns] + [len(replicates.labels) if replicates is not None else 0]) + SPARE_ROWS
        self.grid.clear()
        self.grid.setColumnCount(len(names) + offset)
        self.grid.setRowCount(max(rows, 24))
        self.grid.setHorizontalHeaderLabels((["Replicate"] if offset else []) + names)
        if offset:
            self.grid.horizontalHeaderItem(0).setToolTip(tips.REPLICATE_COLUMN)
            owners = replicates.row_replicates()
            for row in range(self.grid.rowCount()):
                label = replicates.labels[row] if row < len(replicates.labels) else ""
                item = QTableWidgetItem(label)
                item.setBackground(QBrush(REPLICATE_BACKGROUND))
                if not label and row < len(owners) and owners[row]:
                    item.setToolTip(f"Blank: continues {owners[row]}.")
                self.grid.setItem(row, 0, item)
        labels = [sheet.row_labels[row] if not offset and row < len(sheet.row_labels) and sheet.row_labels[row] else str(row + 1)
                  for row in range(self.grid.rowCount())]
        self.grid.setVerticalHeaderLabels(labels)
        for index, values in enumerate(columns):
            for row, value in enumerate(values):
                if np.isfinite(value):
                    self.grid.setItem(row, index + offset, QTableWidgetItem(f"{value:.6g}"))
        flagged = 0
        for index, column in enumerate(table.columns if not offset else []):  # outliers of a replicate table are among the replicate values
            mask = self.project.flags(sheet.id).get(column.name)
            for row in (np.flatnonzero(mask[: column.values.size]) if mask is not None else []):
                item = self.grid.item(int(row), index)
                if item is not None:
                    item.setBackground(QBrush(OUTLIER_BACKGROUND))
                    item.setToolTip("Flagged as an outlier. The value is still in this table; analyze the cleaned table to leave it out.")
                    flagged += 1
        self.flagged.setText(f"{flagged} value{'s' if flagged != 1 else ''} flagged as outliers (shaded). They stay in this table and its analyses; "
                             "Create cleaned table on the outliers results sheet makes a copy without them." if flagged else "")
        self.flagged.setVisible(bool(flagged))
        self.paired.blockSignals(True)
        self.paired.setChecked(table.paired)
        self.paired.blockSignals(False)
        self.replicate_button.blockSignals(True)
        self.replicate_button.setChecked(bool(offset))
        self.replicate_button.blockSignals(False)
        self.summary.blockSignals(True)
        self.summary.setCurrentIndex(max(0, self.summary.findData(replicates.summary if replicates is not None else "mean")))
        self.summary.blockSignals(False)
        self.summary.setVisible(bool(offset))
        self._replicate_note()
        self._filling = False
        self.description.setText(sheet.description or "Type or paste values: one column per group, blank cells are missing values.")
        self.description.setToolTip("\n".join(sheet.notes))
        self.notes.setText(" ".join(sheet.notes))
        self.stale.setVisible(sheet.stale)
        self.change.setVisible(sheet.processed_from is not None)
        danger = sheet.pseudoreplicated
        text = "<br>".join(f"⚠ {html.escape(item)}" for item in sheet.warnings)
        self.warnings.setText(text)
        self.warnings.setStyleSheet(DANGER_STYLE if danger else WARNING_STYLE)
        self.warnings.setVisible(bool(text))

    def _replicate_note(self) -> None:
        replicates = self.sheet.replicates
        if replicates is None:
            self.replicate_note.setVisible(False)
            return
        found = replicates.replicates()
        counts = replicates.counts()
        summary = SUMMARIES[replicates.summary].lower()
        if not found:
            text = ("Type the name of each block's biological replicate in the Replicate column (for example Experiment 1 on the "
                    "first row of its values); a blank continues the replicate above.")
        else:
            sizes = ", ".join(f"{column.name} N={column.finite.size}, n={counts.get(column.name, 0)}" for column in self.sheet.table.columns)
            text = (f"{len(found)} biological replicate{'s' if len(found) != 1 else ''}. Analyses use each replicate's {summary} (N); "
                    f"a SuperPlot also shows every value (n). {sizes}.")
        left_out = replicates.unassigned()
        if left_out:
            text += f" {left_out} value{'s' if left_out != 1 else ''} above the first replicate name belong to no replicate and are left out."
        self.replicate_note.setText(text)
        self.replicate_note.setStyleSheet(WARNING_STYLE if left_out or not found else NOTE_STYLE)
        self.replicate_note.setVisible(True)

    # --- Editing ------------------------------------------------------------------------------------------------

    def _labels(self) -> list[str]:
        """The Replicate column, row by row (blank rows at the end dropped)."""

        if not self.offset:
            return []
        labels = [(self.grid.item(row, 0).text().strip() if self.grid.item(row, 0) is not None else "") for row in range(self.grid.rowCount())]
        while labels and not labels[-1]:
            labels.pop()
        return labels

    def _column_values(self) -> list[np.ndarray]:
        out = []
        for index in range(self.offset, self.grid.columnCount()):
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
                for index in range(self.offset, self.grid.columnCount())]

    def commit(self, names: list[str] | None = None, values: list[np.ndarray] | None = None, paired: bool | None = None,
               labels: list[str] | None = None) -> None:
        """Write the grid back to the sheet and recompute everything linked to it. ``labels`` (the Replicate column) makes
        it a replicate table; a replicate table reads them from the grid."""

        sheet = self.sheet
        names = names if names is not None else self._names()
        values = [_trim(np.asarray(column, float)) for column in values] if values is not None else self._column_values()
        paired = self.paired.isChecked() if paired is None else paired
        if labels is None and sheet.replicates is not None:
            labels = self._labels()
        before = list(sheet.replicates.names) if sheet.replicates is not None else sheet.table.names
        try:
            if labels is not None:
                summary = sheet.replicates.summary if sheet.replicates is not None else "mean"
                set_replicates(sheet, ReplicateTable(sheet.table.title, names, values, labels, summary, paired))
            else:
                sheet.table = table_with(sheet.table, names, values, paired)
        except Exception as exc:  # noqa: BLE001 - a repeated title, for example; the grid shows the table as it was
            self.window.status(str(exc))
            self.fill()
            return
        if sheet.cleaned_from is not None:
            sheet.cleaned_from = None
            sheet.notes = [*sheet.notes, "Edited by hand: no longer follows the table it was cleaned from."]
        if sheet.processed_from is not None:
            sheet.processed_from = None
            sheet.notes = [*sheet.notes, "Edited by hand: no longer follows the table it was computed from."]
        if sheet.spec is not None:
            sheet.spec = None
            sheet.images = None
            sheet.units = None
            sheet.stale = False
            sheet.notes = [*sheet.notes, "Edited by hand: no longer follows the CellQuant results."]
        renamed = {old: new for old, new in zip(before, names) if old != new} if len(before) == len(names) else None
        self.project.data_changed(sheet.id, renamed)
        self._replicate_note()

    def _edited(self, item: QTableWidgetItem) -> None:
        if self._filling:
            return
        text = item.text()
        if self.offset and item.column() == 0:
            text = ""  # a replicate name: any text
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

    def _replicates_toggled(self, on: bool) -> None:
        """Add the Replicate column (the analyses then use one value per replicate), or remove it (every value again)."""

        if on == bool(self.offset):
            return
        if on:
            self.commit(labels=[])
            self.window.status("Name each block's biological replicate in the Replicate column; the analyses use the replicate means.")
        else:
            if any(self._labels()) and QMessageBox.question(
                    self, "Remove the replicates",
                    "Remove the Replicate column? The values stay, and the analyses then use every value (n) instead of one value "
                    "per replicate (N).") != QMessageBox.Yes:
                self.fill()
                return
            values, names = self._column_values(), self._names()
            set_replicates(self.sheet, None, values)
            self.commit(names, values)
        self.fill()

    def _summary_changed(self, _index: int) -> None:
        replicates = self.sheet.replicates
        if self._filling or replicates is None:
            return
        replicates.summary = self.summary.currentData()
        set_replicates(self.sheet, replicates)
        self.project.data_changed(self.sheet.id)
        self._replicate_note()

    def add_column(self) -> None:
        names = self._names()
        number = len(names) + 1
        while f"Group {number}" in names:
            number += 1
        self.commit(names + [f"Group {number}"], self._column_values() + [np.zeros(0)])
        self.fill()

    def remove_column(self) -> None:
        index = self.grid.currentColumn() - self.offset
        if self.offset and index == -1:
            self.window.status("Click Replicates to remove the Replicate column.")
            return
        if index < 0:
            self.window.status("Click a cell of the column to remove first.")
            return
        names, values = self._names(), self._column_values()
        del names[index]
        del values[index]
        self.commit(names, values)
        self.fill()

    def _rename(self, section: int) -> None:
        index = section - self.offset
        if index < 0:
            return
        header = self.grid.horizontalHeader()
        editor = QLineEdit(header)
        editor.setText(self._names()[index])
        editor.setGeometry(header.sectionViewportPosition(section), 0, header.sectionSize(section), header.height())
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
        # A first column of words names the replicates: into the Replicate column (turned on when it is off).
        first = [line[0].strip() for line in lines if line and line[0].strip()]
        words = (left == 0 and bool(first) and not any(is_number(cell) for cell in first)
                 and any(is_number(cell) and cell.strip() for line in lines for cell in line[1:]))
        labels = None
        if (self.offset and left == 0) or words:
            labels = self._labels()
            for row, line in enumerate(lines):
                while len(labels) <= top + row:
                    labels.append("")
                labels[top + row] = line[0].strip() if line else ""
            lines = [line[1:] for line in lines]
            titles = titles[1:] if titles else None
            if not self.offset:
                self.window.status("The first column names the biological replicates: Replicates is on, and the analyses use the replicate means.")
        else:
            left -= self.offset
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
        self.commit(names, values, labels=labels)
        self.fill()
