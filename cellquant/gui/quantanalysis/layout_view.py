"""A page layout sheet: the page as it will be exported, the page settings and which graph goes in each cell.

The page redraws whenever one of its graphs (or their data) changes, and when it is shown again. Clicking a cell
of the page picks its graph box in the panel. The drawing is layouts.py; this is the Qt side.
"""

from __future__ import annotations

from pathlib import Path

from qtpy.QtCore import Qt, QTimer
from qtpy.QtGui import QImage, QPixmap
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from cellquant.errors import CellQuantError
from cellquant.gui.quantanalysis import layouts, tips
from cellquant.gui.quantanalysis.datagrid import NOTE_STYLE, WARNING_STYLE
from cellquant.gui.quantanalysis.graph_format import MM_PER_INCH
from cellquant.gui.quantanalysis.project import LayoutSheet, Project

LAYOUT_DPI_CHOICES = (300, 600)
EMPTY = "(empty)"


class LayoutSheetView(QWidget):
    def __init__(self, window, project: Project, sheet: LayoutSheet):
        super().__init__()
        self.window = window
        self.project = project
        self.sheet = sheet
        self.cell_boxes: list[QComboBox] = []
        self._filling = False
        self._scale = 1.0          # screen pixels per mm of the page drawn
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        left = QVBoxLayout()
        top = QHBoxLayout()
        source = QLabel("A page of graphs, as it will be exported. It follows its graphs and their data.")
        source.setStyleSheet(NOTE_STYLE)
        top.addWidget(source, 1)
        export = QPushButton("Export layout…")
        export.setToolTip(tips.LAYOUT_EXPORT)
        export.clicked.connect(lambda _checked=False: self.export_dialog())
        top.addWidget(export)
        left.addLayout(top)
        self.notes = QLabel()
        self.notes.setWordWrap(True)
        self.notes.setStyleSheet(WARNING_STYLE)
        left.addWidget(self.notes)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea { background: #3b4048; border: none; }")
        holder = QWidget()
        holder.setStyleSheet("background: #3b4048;")
        holder_layout = QVBoxLayout(holder)
        holder_layout.setAlignment(Qt.AlignCenter)
        self.preview = QLabel()
        self.preview.setStyleSheet("background: white;")
        self.preview.setToolTip("Click a cell to choose its graph in the panel.")
        self.preview.mousePressEvent = self._clicked
        holder_layout.addWidget(self.preview)
        self.scroll.setWidget(holder)
        left.addWidget(self.scroll, 1)
        layout.addLayout(left, 1)
        layout.addWidget(self._panel())
        self._redraw = QTimer(self)
        self._redraw.setSingleShot(True)
        self._redraw.setInterval(150)
        self._redraw.timeout.connect(self.draw)
        self._dirty = True
        project.changed.connect(self._project_changed)
        self.fill()

    # --- The panel -----------------------------------------------------------------------------------------------

    def _panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setFixedWidth(372)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        panel = QWidget()
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 6, 0)

        page = QGroupBox("Page")
        form = QFormLayout(page)
        self.paper = QComboBox()
        for key, (name, _w, _h) in layouts.PAPERS.items():
            self.paper.addItem(name, key)
        self.paper.setToolTip(tips.LAYOUT_PAPER)
        form.addRow("Paper", self.paper)
        self.orientation = QComboBox()
        self.orientation.addItem("Portrait", "portrait")
        self.orientation.addItem("Landscape", "landscape")
        form.addRow("Orientation", self.orientation)
        self.width = self._mm(20, 1000, tips.LAYOUT_CUSTOM)
        self.height = self._mm(20, 1000, tips.LAYOUT_CUSTOM)
        size = QHBoxLayout()
        size.addWidget(self.width)
        size.addWidget(QLabel("×"))
        size.addWidget(self.height)
        form.addRow("Size (mm)", size)
        self.rows = QSpinBox()
        self.columns = QSpinBox()
        for box in (self.rows, self.columns):
            box.setRange(1, 6)
            box.setToolTip(tips.LAYOUT_GRID)
        grid = QHBoxLayout()
        grid.addWidget(self.rows)
        grid.addWidget(QLabel("rows ×"))
        grid.addWidget(self.columns)
        grid.addWidget(QLabel("columns"))
        form.addRow("Grid", grid)
        self.margin = self._mm(0, 100, tips.LAYOUT_MARGIN)
        form.addRow("Margin (mm)", self.margin)
        self.gap = self._mm(0, 100, tips.LAYOUT_GAP)
        form.addRow("Gap (mm)", self.gap)
        self.align = QComboBox()
        for key, name in layouts.ALIGN.items():
            self.align.addItem(name, key)
        self.align.setToolTip(tips.LAYOUT_ALIGN)
        form.addRow("Graph in cell", self.align)
        column.addWidget(page)

        text = QGroupBox("Text")
        form = QFormLayout(text)
        self.labels = QComboBox()
        for key, name in layouts.LABELS.items():
            self.labels.addItem(name, key)
        self.labels.setToolTip(tips.LAYOUT_LABELS)
        form.addRow("Panel letters", self.labels)
        self.label_size = self._points(tips.LAYOUT_LABEL_SIZE)
        self.label_bold = QCheckBox("Bold")
        letters = QHBoxLayout()
        letters.addWidget(self.label_size)
        letters.addWidget(self.label_bold)
        form.addRow("Letter size (pt)", letters)
        self.title = QLineEdit()
        self.title.setPlaceholderText("No title")
        self.title.setToolTip(tips.LAYOUT_TITLE)
        form.addRow("Title", self.title)
        self.title_size = self._points(tips.LAYOUT_TITLE)
        form.addRow("Title size (pt)", self.title_size)
        column.addWidget(text)

        self.cells_box = QGroupBox("Graphs")
        self.cells_box.setToolTip(tips.LAYOUT_CELLS)
        self.cells_form = QFormLayout(self.cells_box)
        column.addWidget(self.cells_box)
        column.addStretch(1)
        scroll.setWidget(panel)

        for box in (self.paper, self.orientation, self.labels, self.align):
            box.currentIndexChanged.connect(lambda _index: self._changed())
        for box in (self.width, self.height, self.margin, self.gap, self.label_size, self.title_size, self.rows, self.columns):
            box.valueChanged.connect(lambda _value: self._changed())
        self.label_bold.toggled.connect(lambda _on: self._changed())
        self.title.textChanged.connect(lambda _text: self._changed())
        return scroll

    @staticmethod
    def _mm(low: float, high: float, tip: str) -> QDoubleSpinBox:
        box = QDoubleSpinBox()
        box.setRange(low, high)
        box.setDecimals(1)
        box.setToolTip(tip)
        return box

    @staticmethod
    def _points(tip: str) -> QDoubleSpinBox:
        box = QDoubleSpinBox()
        box.setRange(4, 72)
        box.setDecimals(1)
        box.setToolTip(tip)
        return box

    def fill(self) -> None:
        """Show the sheet's settings in the panel, then draw."""

        page = layouts.page_options(self.sheet.page)
        self._filling = True
        for box, key in ((self.paper, "paper"), (self.orientation, "orientation"), (self.labels, "labels"), (self.align, "align")):
            box.setCurrentIndex(max(0, box.findData(page[key])))
        for box, key in ((self.width, "width"), (self.height, "height"), (self.margin, "margin"), (self.gap, "gap"),
                         (self.label_size, "label_size"), (self.title_size, "title_size"), (self.rows, "rows"), (self.columns, "columns")):
            box.setValue(page[key])
        self.label_bold.setChecked(page["label_bold"])
        if self.title.text() != page["title"]:
            self.title.setText(page["title"])
        self._filling = False
        self._enable()
        self._fill_cells()
        self._dirty = True
        self._redraw.start()

    def _enable(self) -> None:
        custom = self.paper.currentData() == "custom"
        self.width.setEnabled(custom)
        self.height.setEnabled(custom)
        self.orientation.setEnabled(not custom)
        lettered = self.labels.currentData() != "none"
        self.label_size.setEnabled(lettered)
        self.label_bold.setEnabled(lettered)
        self.title_size.setEnabled(bool(self.title.text().strip()))

    def _fill_cells(self) -> None:
        """One graph box per cell, named by its place in the grid."""

        while self.cells_form.rowCount():
            self.cells_form.removeRow(0)
        self.cell_boxes = []
        page = layouts.page_options(self.sheet.page)
        count = page["rows"] * page["columns"]
        graphs = self.project.graphs
        for index in range(count):
            box = QComboBox()
            box.addItem(EMPTY, None)
            for graph in graphs.values():
                box.addItem(graph.name, graph.id)
            current = self.sheet.graphs[index] if index < len(self.sheet.graphs) else None
            box.setCurrentIndex(max(0, box.findData(current)))
            box.setToolTip(tips.LAYOUT_CELLS)
            box.currentIndexChanged.connect(lambda _i, index=index, box=box: self._cell_changed(index, box.currentData()))
            row, col = divmod(index, page["columns"])
            self.cells_form.addRow(f"Row {row + 1}, column {col + 1}", box)
            self.cell_boxes.append(box)

    def _changed(self) -> None:
        if self._filling:
            return
        page = dict(self.sheet.page)
        before = layouts.page_options(page)
        page.update({
            "paper": self.paper.currentData(), "orientation": self.orientation.currentData(), "labels": self.labels.currentData(),
            "align": self.align.currentData(),
            "width": self.width.value(), "height": self.height.value(), "margin": self.margin.value(), "gap": self.gap.value(),
            "label_size": self.label_size.value(), "title_size": self.title_size.value(), "rows": self.rows.value(),
            "columns": self.columns.value(), "label_bold": self.label_bold.isChecked(), "title": self.title.text().strip(),
        })
        self.sheet.page = page
        self._enable()
        if (before["rows"], before["columns"]) != (page["rows"], page["columns"]):
            self._regrid(before["columns"], page["columns"], page["rows"])
            self._fill_cells()
        self.project.touch()
        self._dirty = True
        self._redraw.start()

    def _regrid(self, old_columns: int, columns: int, rows: int) -> None:
        """Keep each graph in its row and column when the grid changes; graphs that no longer fit move to empty cells."""

        placed = {}
        left_over = []
        for index, graph_id in enumerate(self.sheet.graphs):
            if not graph_id:
                continue
            row, col = divmod(index, old_columns)
            if row < rows and col < columns:
                placed[row * columns + col] = graph_id
            else:
                left_over.append(graph_id)
        cells = [placed.get(index) for index in range(rows * columns)]
        for graph_id in left_over:
            if None in cells:
                cells[cells.index(None)] = graph_id
            else:
                cells.append(graph_id)  # kept: shown again when the grid grows
        self.sheet.graphs = cells

    def _cell_changed(self, index: int, graph_id) -> None:
        cells = list(self.sheet.graphs)
        while len(cells) <= index:
            cells.append(None)
        cells[index] = graph_id
        self.sheet.graphs = cells
        self.project.touch()
        self._dirty = True
        self._redraw.start()

    # --- The page -----------------------------------------------------------------------------------------------------

    def _project_changed(self, sheet_id: str) -> None:
        if sheet_id and sheet_id not in self.sheet.graphs and sheet_id != self.sheet.id:
            graph_data = {self.project.graphs[graph_id].data_id for graph_id in self.sheet.graphs if graph_id in self.project.graphs}
            if sheet_id not in graph_data:
                return
        if not sheet_id and self.sheet.id in self.project.layouts:
            self._fill_cells()  # graphs added, removed or renamed
        self._dirty = True
        if self.isVisible():
            self._redraw.start()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._dirty = True  # a graph's format may have changed while another sheet was shown
        self._redraw.start()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._redraw.start()

    def draw(self) -> list[str]:
        try:
            width, height = layouts.page_size(self.sheet.page)
            available = self.scroll.viewport().size()
            dpi = max(30.0, min(150.0, (available.width() - 30) / (width / MM_PER_INCH), (available.height() - 30) / (height / MM_PER_INCH)))
            image = layouts.render(self.sheet, self.project, dpi)
            notes = layouts.notes(self.sheet, self.project)
        except ImportError:
            self.preview.setText('Page layouts need matplotlib: pip install "cellquant[stats]"')
            return []
        except (CellQuantError, ValueError, TypeError) as exc:  # a page that cannot be drawn says why instead of failing
            self.notes.setText(str(exc))
            self.notes.setVisible(True)
            return [str(exc)]
        rows, columns, _ = image.shape
        qimage = QImage(image.data, columns, rows, 3 * columns, QImage.Format_RGB888).copy()
        self.preview.setPixmap(QPixmap.fromImage(qimage))
        self.preview.setFixedSize(columns, rows)
        self._scale = dpi / MM_PER_INCH
        self._dirty = False
        self.notes.setText("\n".join(notes))
        self.notes.setVisible(bool(notes))
        return notes

    def cell_at(self, x: float, y: float) -> int | None:
        """The cell under a point of the preview (pixels), or None."""

        mm_x, mm_y = x / self._scale, y / self._scale
        for index, (left, top, width, height) in enumerate(layouts.cells(self.sheet.page)):
            if left <= mm_x <= left + width and top <= mm_y <= top + height:
                return index
        return None

    def _clicked(self, event) -> None:
        index = self.cell_at(event.pos().x(), event.pos().y())
        if index is not None and index < len(self.cell_boxes):
            self.cell_boxes[index].setFocus()
            self.cell_boxes[index].showPopup()

    # --- Export ---------------------------------------------------------------------------------------------------

    def export_dialog(self) -> None:
        dialog = LayoutExportDialog(self, self.sheet)
        if dialog.exec():
            try:
                self.export(dialog.path(), dialog.kind(), dialog.dpi())
            except CellQuantError as exc:
                self.window.status(str(exc))

    def export(self, path, kind: str, dpi: int = 600) -> Path:
        written = layouts.export(self.sheet, self.project, path, kind, dpi)
        self.window.status(f"Saved {written}")
        self.window.log(f"page layout {self.sheet.name} exported to {written} ({kind}, {dpi} dpi)")
        return written


class LayoutExportDialog(QDialog):
    def __init__(self, parent, sheet: LayoutSheet):
        super().__init__(parent)
        self.setWindowTitle("Export page layout")
        form = QFormLayout(self)
        self.format = QComboBox()
        for key, (name, _suffix, _raster) in layouts.EXPORT_FORMATS.items():
            self.format.addItem(name, key)
        self.format.setToolTip(tips.LAYOUT_EXPORT)
        self.format.currentIndexChanged.connect(lambda _index: self._format_changed())
        form.addRow("Format", self.format)
        self.dpi_box = QComboBox()
        for value in LAYOUT_DPI_CHOICES:
            self.dpi_box.addItem(f"{value} dpi", value)
        self.dpi_box.setCurrentIndex(1)
        self.dpi_box.setToolTip("Dots per inch for PNG, TIFF and JPEG. 300 for screens and drafts, 600 for print.")
        form.addRow("Resolution", self.dpi_box)
        safe = "".join(" " if character in '\\/:*?"<>|' else character for character in sheet.name).strip()
        self.file = QLineEdit(str(Path.home() / f"{' '.join(safe.split())}.svg"))
        self.file.setMinimumWidth(320)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        where = QHBoxLayout()
        where.addWidget(self.file, 1)
        where.addWidget(browse)
        form.addRow("File", where)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self._format_changed()

    def _format_changed(self) -> None:
        _name, suffix, raster = layouts.EXPORT_FORMATS[self.kind()]
        self.dpi_box.setEnabled(raster)
        self.file.setText(str(Path(self.file.text()).with_suffix(suffix)))

    def _browse(self) -> None:
        name, suffix, _raster = layouts.EXPORT_FORMATS[self.kind()]
        path, _ = QFileDialog.getSaveFileName(self, "Export page layout", self.file.text(), f"{name} (*{suffix})")
        if path:
            self.file.setText(path)

    def kind(self) -> str:
        return self.format.currentData()

    def dpi(self) -> int:
        return int(self.dpi_box.currentData())

    def path(self) -> str:
        return self.file.text()
