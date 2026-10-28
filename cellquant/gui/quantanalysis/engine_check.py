"""Help → Check the engine…: the statistics engine run on published reference data sets, each number beside its
published value (cellquant.stats.benchmarks)."""

from __future__ import annotations

from qtpy.QtCore import Qt
from qtpy.QtGui import QBrush, QColor
from qtpy.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from cellquant.gui.quantanalysis import tips

COLUMNS = ("Check", "Quantity", "Reference value", "Engine", "", "Reference")
BAD = QColor(120, 40, 40)


class EngineCheckDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("QuantAnalysis: check the engine")
        self.setModal(False)
        self.resize(1200, 700)
        self.rows: list = []
        layout = QVBoxLayout(self)
        intro = QLabel(tips.CHECK_ENGINE + " The same checks run in the tests every time the code changes.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.summary = QLabel()
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        run = QPushButton("Run again")
        run.setToolTip("Run every check again with the engine as it is now.")
        run.clicked.connect(self.run)
        export = QPushButton("Export CSV…")
        export.setToolTip("Save every check with its published and engine values, for a methods supplement.")
        export.clicked.connect(self.export_csv)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons.addWidget(run)
        buttons.addWidget(export)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.run()

    def run(self) -> list:
        from cellquant.stats.benchmarks import run_benchmarks

        self.rows = run_benchmarks()
        self.table.setRowCount(len(self.rows))
        for number, row in enumerate(self.rows):
            cells = (row.name, row.key, _text(row.published), _text(row.engine), "✓" if row.ok else "✗ differs", row.source)
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setToolTip(f"{row.analysis}\n{row.source}")
                if column in (2, 3):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if not row.ok:
                    item.setBackground(QBrush(BAD))
                self.table.setItem(number, column, item)
        passed = sum(row.ok for row in self.rows)
        names = len({row.name for row in self.rows})
        self.summary.setText(f"<b>{passed} of {len(self.rows)} values agree</b> with the reference values, in {names} reference analyses (published outputs and tables; a few computed live by scipy or statsmodels, as the Reference column says).")
        return self.rows

    def export_csv(self, path: str | None = None) -> str | None:
        import pandas as pd

        if not path:
            path, _ = QFileDialog.getSaveFileName(self, "Export engine check", "engine check.csv", "CSV (*.csv)")
        if not path:
            return None
        pd.DataFrame([{"check": row.name, "analysis": row.analysis, "source": row.source, "quantity": row.key,
                       "published": row.published, "engine": row.engine, "agrees": row.ok} for row in self.rows]).to_csv(path, index=False)
        return path


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.10g}"
    return str(value)
