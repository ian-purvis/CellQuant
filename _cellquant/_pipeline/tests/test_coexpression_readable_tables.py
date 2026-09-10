"""Readable coexpression table layout without launching napari."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy.QtCore import Qt
from qtpy.QtWidgets import QApplication, QHeaderView, QTableWidget, QTableWidgetItem

from cellquant.plugin.coexpression import (
    _MARKER_COLUMN_MIN_WIDTHS,
    configure_readable_table,
)


def test_configure_readable_table_keeps_headers_readable():
    app = QApplication.instance() or QApplication([])
    table = QTableWidget(1, 6)
    table.setHorizontalHeaderLabels(
        ["Marker", "Channel", "Raw low", "Raw high (optional)", "Positive fraction", "Uncertainty margin"]
    )
    table.setItem(0, 3, QTableWidgetItem("optional high"))
    configure_readable_table(table, min_widths=_MARKER_COLUMN_MIN_WIDTHS)

    assert table.horizontalScrollBarPolicy() == Qt.ScrollBarAsNeeded
    assert table.horizontalHeader().sectionResizeMode(0) == QHeaderView.Interactive
    assert table.horizontalHeader().stretchLastSection() is False
    for col, minimum in enumerate(_MARKER_COLUMN_MIN_WIDTHS):
        assert table.columnWidth(col) >= minimum
    tip = table.horizontalHeaderItem(3)
    assert tip is not None
    assert tip.toolTip() == tip.text()
    app.processEvents()
