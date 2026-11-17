"""Help → Check the engine…: the statistics engine run on published reference data sets, each number beside its
published value (cellquant.stats.benchmarks)."""

from __future__ import annotations

from qtpy.QtCore import QEventLoop, Qt
from qtpy.QtGui import QBrush, QColor
from qtpy.QtWidgets import (
    QApplication,
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
        tips.fit_to_screen(self, 1200, 700)
        self.rows: list = []
        self._running = False
        layout = QVBoxLayout(self)
        intro = QLabel(tips.CHECK_ENGINE + " The same checks run in the tests every time the code changes.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.summary = QLabel()
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, len(COLUMNS))
        tips.tip_corner_button(self.table)
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        self.run_button = QPushButton("Run again")
        self.run_button.setToolTip("Run every check again with the engine as it is now.")
        self.run_button.clicked.connect(self.run)
        export = QPushButton("Export CSV…")
        export.setToolTip("Save every check with its published and engine values, for a methods supplement.")
        export.clicked.connect(self.export_csv)
        close = QPushButton("Close")
        close.setToolTip("Close this window.")
        close.clicked.connect(self.close)
        buttons.addWidget(self.run_button)
        buttons.addWidget(export)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.summary.setText("Not run yet.")

    def run(self) -> list:
        """Run every check, one reference analysis at a time, showing how far it is. It runs on the window's own thread, so the window
        is painted between the checks (input is held back, which also keeps it from being started twice)."""

        from cellquant.stats import benchmarks

        if self._running:
            return self.rows
        self._running = True
        self.run_button.setEnabled(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            todo = [item for item in benchmarks.BENCHMARKS if not (item.needs_statsmodels and benchmarks.statsmodels is None)]
            self.rows = []
            for number, item in enumerate(todo, 1):
                self.summary.setText(f"<b>Checking… {number} of {len(todo)} reference analyses</b> ({item.name})")
                QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)
                self.rows.extend(benchmarks.run_benchmark(item))
        finally:
            QApplication.restoreOverrideCursor()
            self.run_button.setEnabled(True)
            self._running = False
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
