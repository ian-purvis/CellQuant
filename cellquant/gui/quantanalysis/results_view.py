"""A results sheet: the analysis laid out in sections (titles, labels and values), its
warnings above it, and the methods sentence below."""

from __future__ import annotations

import html
import logging

import numpy as np
import pandas as pd
from qtpy.QtCore import Qt
from qtpy.QtGui import QBrush, QColor, QFont
from qtpy.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QCheckBox,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cellquant.gui.quantanalysis import reference, tips
from cellquant.gui.quantanalysis.datagrid import DANGER_STYLE, NOTE_STYLE, WARNING_STYLE
from cellquant.gui.quantanalysis.project import PSEUDO_WARNING, Project, ResultSheet
from cellquant.stats import working
from cellquant.stats.results import fmt

SECTION_BACKGROUND = QColor(60, 72, 88)
LOG = logging.getLogger(__name__)
ROW_LIMIT = 300  # rows of a working table shown; the CSV has them all
WORKING_CSS = """<style>
h3 { margin-top: 12px; margin-bottom: 2px; }
table.steps td, table.steps th, table.detail td, table.detail th { padding: 1px 10px 1px 0; }
th { text-align: left; }
td.num { text-align: right; font-family: monospace; }
.muted { color: #9a9a9a; }
.bad { color: #ff8a80; }
</style>"""


def working_source(kind: str, data, options: dict):
    """The table an analysis read: what cellquant.stats.working recomputes from."""

    if kind == "pstack":
        return options
    if data is None:
        return None
    if kind == "twoway":
        return data.grouped
    if kind == "nested":
        return data.replicates
    if kind in ("linreg", "xycorr", "curvefit", "survival"):
        return data.xy
    if kind == "contingency":
        return data.contingency
    return data.table


def working_html(blocks: list) -> str:
    """The steps of each block (quantity, how, value, the analysis's value and whether they agree), then its detail table."""

    parts = [WORKING_CSS, "<p class='muted'>Each value is worked out again from the data, step by step. Where the analysis reports "
                          "the same quantity, its value is beside it: ✓ agrees, ✗ differs.</p>"]
    for block in blocks:
        parts.append(f"<h3>{html.escape(block.title)}</h3>")
        if block.note:
            parts.append(f"<p class='muted'>{html.escape(block.note)}</p>")
        if block.steps:
            rows = "".join(
                f"<tr><td>{html.escape(step.label)}</td><td>{html.escape(step.formula)}</td><td class='num'>{html.escape(_value(step.value))}</td>"
                f"<td class='num'>{html.escape(_value(step.engine)) if step.engine is not None else ''}</td>"
                f"<td>{'' if step.checked is None else ('✓' if step.checked else '<span class=bad>✗</span>')}</td></tr>"
                for step in block.steps
            )
            parts.append("<table class='steps'><tr><th>Quantity</th><th>How</th><th>Value</th><th>Analysis</th><th></th></tr>" + rows + "</table>")
        if block.table is not None and not block.table.empty:
            frame = block.table
            head = "".join(f"<th>{html.escape(str(name))}</th>" for name in frame.columns)
            body = "".join(
                "<tr>" + "".join(f"<td class='num'>{html.escape(_value(value))}</td>" for value in row) + "</tr>"
                for row in frame.head(ROW_LIMIT).itertuples(index=False)
            )
            more = f"<p class='muted'>… {len(frame) - ROW_LIMIT} more rows in Export working CSV…</p>" if len(frame) > ROW_LIMIT else ""
            parts.append(f"<table class='detail'><tr>{head}</tr>{body}</table>{more}")
    return "".join(parts)


def _value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (bool, np.bool_)):
        return "Yes" if value else "No"
    if isinstance(value, (float, np.floating)):
        return fmt(float(value), 6)
    return str(value)


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
    show_working = False  # Show working stays ticked for every results sheet once ticked on one

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
        how = QPushButton("How is this calculated?")
        how.setToolTip(tips.HOW_CALCULATED)
        how.clicked.connect(lambda: window.statistics_reference(sheet))
        copy = QPushButton("Copy")
        copy.setToolTip("Copy the results as tab-separated text, to paste into a spreadsheet or a document.")
        copy.clicked.connect(self.copy)
        export = QPushButton("Export results CSV…")
        export.setToolTip("Save this results sheet as a CSV file: section, label, value and the text shown.")
        export.clicked.connect(self.export_csv)
        self.working_box = QCheckBox("Show working")
        self.working_box.setToolTip(tips.SHOW_WORKING)
        self.working_box.setChecked(ResultSheetView.show_working)
        self.working_box.toggled.connect(self._working_toggled)
        self.working_export = QPushButton("Export working CSV…")
        self.working_export.setToolTip("Save the working as a CSV file: each step, its formula, the value worked out and the analysis's own value.")
        self.working_export.clicked.connect(self.export_working)
        top.addWidget(self.options_button)
        top.addWidget(self.clean_button)
        top.addWidget(self.working_box)
        top.addWidget(self.working_export)
        top.addWidget(how)
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
        self.table.cellDoubleClicked.connect(self._explain)
        self.working = QTextBrowser()
        self.working.setOpenLinks(False)
        split = QSplitter(Qt.Vertical)
        split.addWidget(self.table)
        split.addWidget(self.working)
        split.setSizes([500, 400])
        layout.addWidget(split, 1)
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
            if reference.default_index().for_label(label) is not None:
                item.setToolTip("Double-click: what this means and how it's calculated")
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
        self._fill_working()

    def blocks(self) -> list:
        """The working of this sheet (cellquant.stats.working), or [] when it has none or the sheet is not computed."""

        sheet = self.sheet
        if sheet.error or (sheet.result is None and sheet.wide is None):
            return []
        data = self.project.data.get(sheet.data_id)
        source = working_source(sheet.kind, data, sheet.options)
        try:
            return working.working(sheet.kind, source, sheet.options, sheet.result if sheet.result is not None else sheet.wide)
        except Exception:  # noqa: BLE001 - the working is an extra; the results sheet stays as it is
            LOG.exception("show working failed for %s", sheet.name)
            return []

    def _working_toggled(self, on: bool) -> None:
        ResultSheetView.show_working = on
        self._fill_working()

    def _fill_working(self) -> None:
        available = working.has_working(self.sheet.kind)
        self.working_box.setEnabled(available)
        if not available:
            self.working_box.setToolTip("This analysis has no working to show yet.")
        on = available and self.working_box.isChecked()
        self.working.setVisible(on)
        self.working_export.setVisible(on)
        if on:
            blocks = self.blocks()
            self.working.setHtml(working_html(blocks) if blocks else "<p>No working for this sheet (it could not be computed).</p>")

    def export_working(self, path: str | None = None) -> str | None:
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, "Export working", f"{self.sheet.name} working.csv", "CSV (*.csv)")
        if not path:
            return None
        working.to_frame(self.blocks()).to_csv(path, index=False)
        self.window.status(f"Saved {path}")
        return path

    def _explain(self, row: int, _column: int) -> None:
        """Double-clicking a row label opens the term it names in the statistics reference."""

        item = self.table.item(row, 0)
        page = reference.default_index().for_label(item.text()) if item is not None else None
        if page is not None:
            self.window.statistics_reference(term=page.title)

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
