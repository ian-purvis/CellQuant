"""A results sheet: the analysis laid out in sections (titles, labels and values), its
warnings above it, and the methods sentence below."""

from __future__ import annotations

import html

import pandas as pd
from qtpy.QtCore import Qt
from qtpy.QtGui import QBrush, QColor, QFont
from qtpy.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cellquant.gui.quantanalysis import tips
from cellquant.gui.quantanalysis.datagrid import DANGER_STYLE, NOTE_STYLE, WARNING_STYLE
from cellquant.gui.quantanalysis.project import PSEUDO_WARNING, Project, ResultSheet

SECTION_BACKGROUND = QColor(60, 72, 88)


def result_rows(sheet: ResultSheet) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """(column headers, rows). A row whose values are all None is a section title."""

    if sheet.result is not None and sheet.wide is None:
        rows = []
        for section in sheet.result.sections:
            rows.append((section.title, None))
            rows += [(row.label, [row.formatted]) for row in section.rows]
        return ["", ""], rows
    if sheet.wide is not None and not sheet.wide.empty:
        frame = sheet.wide
        rows, last = [], None
        for (section, statistic), values in frame.iterrows():
            if section != last:
                rows.append((str(section), None))
                last = section
            rows.append((str(statistic), ["" if pd.isna(value) else str(value) for value in values]))
        return ["", *map(str, frame.columns)], rows
    return [], []


def result_frame(sheet: ResultSheet) -> pd.DataFrame:
    """What Export results CSV writes."""

    if sheet.result is not None and sheet.wide is None:
        return sheet.result.to_frame()
    frame = sheet.wide.reset_index() if sheet.wide is not None else pd.DataFrame()
    extra = [{"section": "Warnings", "statistic": text} for text in sheet.warnings]
    if sheet.methods:
        extra.append({"section": "Methods", "statistic": sheet.methods})
    return pd.concat([frame, pd.DataFrame(extra)], ignore_index=True) if extra else frame


def result_text(sheet: ResultSheet) -> str:
    """Tab-separated, for pasting into a spreadsheet."""

    headers, rows = result_rows(sheet)
    lines = [sheet.name]
    if headers and any(headers):
        lines.append("\t".join(headers))
    for label, values in rows:
        lines.append(label if values is None else "\t".join([label, *values]))
    lines += [f"Warning: {text}" for text in sheet.warnings]
    if sheet.methods:
        lines.append(f"Methods: {sheet.methods}")
    return "\n".join(lines)


class ResultSheetView(QWidget):
    def __init__(self, window, project: Project, sheet: ResultSheet):
        super().__init__()
        self.window = window
        self.project = project
        self.sheet = sheet
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        top = QHBoxLayout()
        self.source = QLabel()
        self.source.setStyleSheet(NOTE_STYLE)
        top.addWidget(self.source, 1)
        self.options_button = QPushButton("Change options…")
        self.options_button.setToolTip("Change the test or its options. The sheet and its graphs update in place.")
        self.options_button.clicked.connect(lambda: window.change_options(sheet))
        self.clean_button = QPushButton("Create cleaned table")
        self.clean_button.setToolTip(tips.CLEAN)
        self.clean_button.clicked.connect(lambda: window.create_cleaned_table(sheet))
        copy = QPushButton("Copy")
        copy.setToolTip("Copy the results as tab-separated text, to paste into a spreadsheet or a document.")
        copy.clicked.connect(self.copy)
        export = QPushButton("Export results CSV…")
        export.setToolTip("Save this results sheet as a CSV file: section, label, value and the text shown.")
        export.clicked.connect(self.export_csv)
        top.addWidget(self.options_button)
        top.addWidget(self.clean_button)
        top.addWidget(copy)
        top.addWidget(export)
        layout.addLayout(top)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet(DANGER_STYLE)
        layout.addWidget(self.error)
        self.warnings = QLabel()
        self.warnings.setWordWrap(True)
        self.warnings.setTextFormat(Qt.RichText)
        layout.addWidget(self.warnings)
        self.table = QTableWidget()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        layout.addWidget(self.table, 1)
        methods_title = QLabel("<b>Methods</b> (copy into your paper and add N and what a dot is)")
        layout.addWidget(methods_title)
        self.methods = QLabel()
        self.methods.setWordWrap(True)
        self.methods.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.methods)
        self.fill()

    def fill(self) -> None:
        sheet = self.sheet
        data = self.project.data.get(sheet.data_id)
        self.source.setText(f"From data table: {data.name}" if data else "")
        self.options_button.setVisible(sheet.kind in ("compare", "anova", "nested", "outliers", "pstack", "twoway", "linreg", "xycorr", "curvefit", "contingency", "roc", "blandaltman", "survival", "corrmatrix", "pca"))
        self.clean_button.setVisible(sheet.kind == "outliers")
        self.clean_button.setEnabled(sheet.result is not None)
        self.error.setText(f"⚠ {sheet.error}" if sheet.error else "")
        self.error.setVisible(bool(sheet.error))
        danger = PSEUDO_WARNING in sheet.warnings or any(text.startswith("Small n") or "smallest P this test can give" in text for text in sheet.warnings)
        self.warnings.setText("<br>".join(f"⚠ {html.escape(text)}" for text in sheet.warnings))
        self.warnings.setStyleSheet(DANGER_STYLE if danger else WARNING_STYLE)
        self.warnings.setVisible(bool(sheet.warnings))
        headers, rows = result_rows(sheet)
        self.table.clear()
        self.table.setColumnCount(max(2, len(headers)))
        self.table.setRowCount(len(rows) + (1 if len(headers) > 2 else 0))
        bold = QFont()
        bold.setBold(True)
        offset = 0
        if len(headers) > 2:
            for index, text in enumerate(headers):
                item = QTableWidgetItem(text)
                item.setFont(bold)
                self.table.setItem(0, index, item)
            offset = 1
        for number, (label, values) in enumerate(rows):
            row = number + offset
            item = QTableWidgetItem(label)
            if values is None:
                item.setFont(bold)
                for column in range(self.table.columnCount()):
                    cell = item if column == 0 else QTableWidgetItem("")
                    cell.setBackground(QBrush(SECTION_BACKGROUND))
                    self.table.setItem(row, column, cell)
                continue
            self.table.setItem(row, 0, item)
            for column, value in enumerate(values, start=1):
                cell = QTableWidgetItem(value)
                cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter if len(headers) > 2 else Qt.AlignLeft | Qt.AlignVCenter)
                self.table.setItem(row, column, cell)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for column in range(1, self.table.columnCount()):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents if len(headers) > 2 else QHeaderView.Stretch)
        self.table.resizeRowsToContents()
        self.methods.setText(sheet.methods or "")

    def copy(self) -> None:
        self.project.flush_fits()
        QApplication.clipboard().setText(result_text(self.sheet))
        self.window.status("Results copied.")

    def export_csv(self, path: str | None = None) -> str | None:
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, "Export results", f"{self.sheet.name}.csv", "CSV (*.csv)")
        if not path:
            return None
        self.project.flush_fits()
        result_frame(self.sheet).to_csv(path, index=False)
        self.window.status(f"Saved {path}")
        return path
