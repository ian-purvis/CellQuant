"""A graph sheet: the figure (white, as it will be exported) and its format panel, and graph export.

matplotlib is imported here only, when the first graph is shown, so the window opens without it.
"""

from __future__ import annotations

import html
from pathlib import Path

from qtpy.QtCore import Qt, QTimer
from qtpy.QtGui import QColor
from qtpy.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from cellquant.errors import CellQuantError
from cellquant.gui.quantanalysis import graphs, tips
from cellquant.gui.quantanalysis.datagrid import DANGER_STYLE, NOTE_STYLE, WARNING_STYLE
from cellquant.gui.quantanalysis.project import PSEUDO_WARNING, GraphSheet, Project

# key: (name, file suffix, raster)
EXPORT_FORMATS = {
    "svg": ("SVG (vector, for Illustrator / Inkscape)", ".svg", False),
    "pdf": ("PDF (vector)", ".pdf", False),
    "eps": ("EPS (vector, older journals)", ".eps", False),
    "png": ("PNG", ".png", True),
    "tiff": ("TIFF, LZW compressed (high resolution)", ".tif", True),
    "jpeg": ("JPEG", ".jpg", True),
}
DPI_CHOICES = (300, 600, 1200)


def export_figure(data, graph: GraphSheet, result, path: str | Path, kind: str, dpi: int = 300,
                  width: float | None = None, height: float | None = None, units: str | None = None, outliers=None) -> Path:
    """Draw the graph on a fresh figure at the export size and save it. Returns the path written."""

    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    sized = GraphSheet(graph.name, graph.data_id, graph.kind, {**graph.format}, graph.result_id, id=graph.id)
    for key, value in (("width", width), ("height", height), ("units", units)):
        if value is not None:
            sized.format[key] = value
    figure = Figure()
    FigureCanvasAgg(figure)
    graphs.draw(figure, data, sized, result, outliers)
    path = Path(path)
    options = {"dpi": dpi, "facecolor": "white", "format": kind}
    if kind == "tiff":
        options["pil_kwargs"] = {"compression": "tiff_lzw"}
    elif kind == "jpeg":
        options["pil_kwargs"] = {"quality": 95}
    figure.savefig(path, **options)
    return path


class GraphSheetView(QWidget):
    def __init__(self, window, project: Project, sheet: GraphSheet):
        super().__init__()
        self.window = window
        self.project = project
        self.sheet = sheet
        self._filling = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        left = QVBoxLayout()
        top = QHBoxLayout()
        self.source = QLabel()
        self.source.setStyleSheet(NOTE_STYLE)
        top.addWidget(self.source, 1)
        export = QPushButton("Export graph…")
        export.setToolTip("Save as SVG, PDF, EPS, PNG, TIFF (300 to 1200 dpi, LZW) or JPEG, at a size in mm or inches.")
        export.clicked.connect(self.export_dialog)
        top.addWidget(export)
        left.addLayout(top)
        self.warning = QLabel()
        self.warning.setWordWrap(True)
        left.addWidget(self.warning)
        self.notes = QLabel()
        self.notes.setWordWrap(True)
        self.notes.setStyleSheet(WARNING_STYLE)
        left.addWidget(self.notes)
        self.page = QScrollArea()
        self.page.setWidgetResizable(True)
        self.page.setStyleSheet("QScrollArea { background: #3b4048; border: none; }")
        holder = QWidget()
        holder.setStyleSheet("background: #3b4048;")
        self.holder_layout = QVBoxLayout(holder)
        self.holder_layout.setAlignment(Qt.AlignCenter)
        self.page.setWidget(holder)
        left.addWidget(self.page, 1)
        layout.addLayout(left, 1)
        self.canvas = None
        self.figure = None
        self.missing = None
        try:
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
            from matplotlib.figure import Figure
        except ImportError:
            self.missing = QLabel(
                "Graphs need matplotlib, which is not installed.<br>Install it with:<br>"
                "<code>pip install \"cellquant[stats]\"</code><br>then open QuantAnalysis again."
            )
            self.missing.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.missing.setStyleSheet("QLabel { color: #f0f0f0; font-size: 13px; }")
            self.holder_layout.addWidget(self.missing)
        else:
            self.figure = Figure()
            self.canvas = FigureCanvasQTAgg(self.figure)
            self.canvas.setStyleSheet("background: white;")
            self.holder_layout.addWidget(self.canvas)
        layout.addWidget(self._format_panel())
        self._redraw = QTimer(self)
        self._redraw.setSingleShot(True)
        self._redraw.setInterval(120)
        self._redraw.timeout.connect(self.draw)
        self.fill()

    # --- The format panel -----------------------------------------------------------------------------------------

    def _format_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setFixedWidth(350)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        panel = QWidget()
        form = QFormLayout(panel)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.kind = QComboBox()
        for key, (name, tip) in graphs.KINDS.items():
            self.kind.addItem(name, key)
            self.kind.setItemData(self.kind.count() - 1, tip, Qt.ToolTipRole)
        self.kind.setToolTip("The kind of graph. Hover each choice for when to use it.")
        self.kind.currentIndexChanged.connect(lambda _index: self._changed())
        form.addRow("Graph", self.kind)
        self.error = QComboBox()
        for key, (name, _tip) in graphs.ERRORS.items():
            self.error.addItem(name, key)
            self.error.setItemData(self.error.count() - 1, tips.ERROR[key], Qt.ToolTipRole)
        self.error.setToolTip("\n\n".join(tips.ERROR[key] for key in ("sd", "sem", "ci", "median_iqr")))
        self.error.currentIndexChanged.connect(lambda _index: self._changed())
        form.addRow("Error bars", self.error)
        self.comparison = QComboBox()
        self.comparison.setToolTip(tips.GRAPH_COMPARISON)
        self.comparison.currentIndexChanged.connect(lambda _index: self._changed())
        form.addRow("Comparison", self.comparison)
        self.bracket = QComboBox()
        for text, key in (("Stars (P summary)", "stars"), ("P value", "p"), ("None", "none")):
            self.bracket.addItem(text, key)
        self.bracket.setToolTip(tips.BRACKET)
        self.bracket.currentIndexChanged.connect(lambda _index: self._changed())
        form.addRow("Bracket", self.bracket)
        self.bracket_mode = QComboBox()
        for text, key in (("Brackets", "brackets"), ("Asterisks above columns", "columns")):
            self.bracket_mode.addItem(text, key)
        self.bracket_mode.setToolTip(tips.BRACKET_MODE)
        self.bracket_mode.currentIndexChanged.connect(lambda _index: self._changed())
        form.addRow("Multiple comparisons", self.bracket_mode)
        self.hide_ns = QCheckBox("Hide ns")
        self.hide_ns.setToolTip(tips.HIDE_NS)
        self.hide_ns.toggled.connect(lambda _on: self._changed())
        self.letters = QCheckBox("Compact letters")
        self.letters.setToolTip(tips.LETTERS)
        self.letters.toggled.connect(lambda _on: self._changed())
        self.letter_style = QComboBox()
        for text, key in (("A B C", "upper"), ("a b c", "lower"), ("1 2 3", "numbers")):
            self.letter_style.addItem(text, key)
        self.letter_style.setToolTip(tips.LETTER_STYLE)
        self.letter_style.currentIndexChanged.connect(lambda _index: self._changed())
        marks = QVBoxLayout()
        marks.addWidget(self.hide_ns)
        marks.addWidget(self.letters)
        marks.addWidget(self.letter_style)
        form.addRow("", marks)
        self.ref_colors = QHBoxLayout()
        ref_title = QLabel("Reference colours")
        ref_title.setToolTip(tips.REF_COLORS)
        form.addRow(ref_title, self.ref_colors)
        self.title = QLineEdit()
        self.title.setPlaceholderText("none")
        self.title.setToolTip("A title above the graph. Journals usually put it in the legend instead.")
        self.y_title = QLineEdit()
        self.y_title.setToolTip("The Y axis title. Say what a dot is, for example “% of target (per replicate)”.")
        self.x_title = QLineEdit()
        self.x_title.setPlaceholderText("none")
        self.x_title.setToolTip("The X axis title. Often not needed: the column names are the labels.")
        for widget, label in ((self.title, "Title"), (self.y_title, "Y title"), (self.x_title, "X title")):
            widget.editingFinished.connect(self._changed)
            form.addRow(label, widget)
        self.y_min = QLineEdit()
        self.y_min.setPlaceholderText("auto")
        self.y_max = QLineEdit()
        self.y_max.setPlaceholderText("auto")
        for widget in (self.y_min, self.y_max):
            widget.setToolTip("Leave blank for automatic. The axis starts at 0 when the data are near it.")
            widget.editingFinished.connect(self._changed)
        y_range = QHBoxLayout()
        y_range.addWidget(self.y_min)
        y_range.addWidget(QLabel("to"))
        y_range.addWidget(self.y_max)
        form.addRow("Y range", y_range)
        self.log_y = QCheckBox("Log scale")
        self.log_y.setToolTip(tips.LOG_Y)
        self.log_y.toggled.connect(lambda _on: self._changed())
        form.addRow("Y axis", self.log_y)
        self.show_outliers = QCheckBox("Outliers hollow")
        self.show_outliers.setToolTip("Draw the values flagged by Analyze → Identify outliers… as hollow points. The values stay in the table.")
        self.show_outliers.toggled.connect(lambda _on: self._changed())
        form.addRow("Outliers", self.show_outliers)
        self.show_n = QCheckBox("n under columns")
        self.show_n.setToolTip(tips.SHOW_N)
        self.show_n.toggled.connect(lambda _on: self._changed())
        form.addRow("Labels", self.show_n)
        self.dot_size = QDoubleSpinBox()
        self.dot_size.setRange(0, 200)
        self.dot_size.setSpecialValueText("auto")
        self.dot_size.setToolTip(tips.DOT_SIZE)
        self.font_size = QDoubleSpinBox()
        self.font_size.setRange(5, 24)
        self.font_size.setSingleStep(0.5)
        self.font_size.setSuffix(" pt")
        self.font_size.setToolTip("Font size of every label, in points at the exported size. 7 to 9 pt suits most journals.")
        for widget in (self.dot_size, self.font_size):
            widget.setButtonSymbols(QAbstractSpinBox.NoButtons)
        for widget, label in ((self.dot_size, "Dot size"), (self.font_size, "Font")):
            widget.valueChanged.connect(lambda _value: self._changed())
            form.addRow(label, widget)
        self.width = QDoubleSpinBox()
        self.height = QDoubleSpinBox()
        for widget in (self.width, self.height):
            widget.setRange(10, 1000)
            widget.setDecimals(1)
            widget.setButtonSymbols(QAbstractSpinBox.NoButtons)
            widget.setToolTip(tips.GRAPH_SIZE)
            widget.valueChanged.connect(lambda _value: self._changed())
        self.units = QComboBox()
        self.units.addItem("mm", "mm")
        self.units.addItem("in", "in")
        self.units.setToolTip(tips.GRAPH_SIZE)
        self.units.currentIndexChanged.connect(lambda _index: self._units_changed())
        size = QHBoxLayout()
        size.addWidget(self.width)
        size.addWidget(QLabel("×"))
        size.addWidget(self.height)
        size.addWidget(self.units)
        form.addRow("Size", size)
        self.colors_box = QVBoxLayout()
        colors_title = QLabel("Colours")
        colors_title.setToolTip(tips.COLOR)
        form.addRow(colors_title, self.colors_box)
        scroll.setWidget(panel)
        return scroll

    def _fill_colors(self) -> None:
        while self.colors_box.count():
            item = self.colors_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        data = self.project.data.get(self.sheet.data_id)
        if data is None:
            return
        fmt = {**graphs.DEFAULT_FORMAT, **self.sheet.format}
        for index, name in enumerate(data.table.names):
            color = graphs.color_of(fmt, name, index)
            button = QPushButton(name)
            button.setToolTip(tips.COLOR)
            button.setStyleSheet(f"QPushButton {{ text-align: left; border-left: 14px solid {color}; padding-left: 6px; }}")
            button.clicked.connect(lambda _checked=False, name=name, color=color: self._pick_color(name, color))
            self.colors_box.addWidget(button)

    def _fill_ref_colors(self) -> None:
        while self.ref_colors.count():
            item = self.ref_colors.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        colors = {**graphs.DEFAULT_FORMAT, **self.sheet.format}["ref_colors"]
        for number, label in enumerate(("1st", "2nd")):
            color = colors[number] if number < len(colors) else graphs.DEFAULT_FORMAT["ref_colors"][number]
            button = QPushButton(label)
            button.setToolTip(tips.REF_COLORS)
            button.setStyleSheet(f"QPushButton {{ text-align: left; border-left: 14px solid {color}; padding-left: 6px; }}")
            button.clicked.connect(lambda _checked=False, number=number, color=color: self._pick_ref_color(number, color))
            self.ref_colors.addWidget(button)

    def _pick_ref_color(self, number: int, current: str) -> None:
        color = QColorDialog.getColor(QColor(current), self, f"Colour of comparisons with the {'first' if number == 0 else 'second'} reference group")
        if color.isValid():
            self.set_ref_color(number, color.name())

    def set_ref_color(self, number: int, color: str) -> None:
        colors = list({**graphs.DEFAULT_FORMAT, **self.sheet.format}["ref_colors"])
        colors[number] = color
        self.sheet.format["ref_colors"] = colors
        self._fill_ref_colors()
        self.draw()

    def _pick_color(self, name: str, current: str) -> None:
        color = QColorDialog.getColor(QColor(current), self, f"Colour of {name}")
        if color.isValid():
            self.sheet.format.setdefault("colors", {})[name] = color.name()
            self._fill_colors()
            self.draw()

    def fill(self) -> None:
        """Show the sheet's settings in the panel, then draw."""

        sheet = self.sheet
        fmt = {**graphs.DEFAULT_FORMAT, **sheet.format}
        data = self.project.data.get(sheet.data_id)
        self._filling = True
        self.kind.setCurrentIndex(max(0, self.kind.findData(sheet.kind)))
        self.error.setCurrentIndex(max(0, self.error.findData(fmt["error"])))
        self.bracket.setCurrentIndex(max(0, self.bracket.findData(fmt["bracket"])))
        self.bracket_mode.setCurrentIndex(max(0, self.bracket_mode.findData(fmt["bracket_mode"])))
        self.hide_ns.setChecked(bool(fmt["hide_ns"]))
        self.letters.setChecked(bool(fmt["letters"]))
        self.letter_style.setCurrentIndex(max(0, self.letter_style.findData(fmt["letter_style"])))
        self.show_outliers.setChecked(bool(fmt["show_outliers"]))
        self.comparison.clear()
        self.comparison.addItem("none", None)
        for result in self.project.results.values():
            if result.data_id == sheet.data_id and result.kind in ("compare", "anova"):
                self.comparison.addItem(result.name, result.id)
        self.comparison.setCurrentIndex(max(0, self.comparison.findData(sheet.result_id)))
        self.title.setText(fmt["title"])
        self.x_title.setText(fmt["x_title"])
        self.y_title.setText(fmt["y_title"] if fmt["y_title"] is not None else (data.table.title if data else ""))
        self.y_min.setText("" if fmt["y_min"] is None else f"{fmt['y_min']:g}")
        self.y_max.setText("" if fmt["y_max"] is None else f"{fmt['y_max']:g}")
        self.log_y.setChecked(bool(fmt["log_y"]))
        self.show_n.setChecked(bool(fmt["show_n"]))
        self.dot_size.setValue(float(fmt["dot_size"]))
        self.font_size.setValue(float(fmt["font_size"]))
        self.units.setCurrentIndex(max(0, self.units.findData(fmt["units"])))
        self.width.setValue(float(fmt["width"]))
        self.height.setValue(float(fmt["height"]))
        self._filling = False
        self._fill_colors()
        self._fill_ref_colors()
        self.draw()

    def _number(self, widget: QLineEdit):
        text = widget.text().strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            widget.setText("")
            return None

    def _changed(self) -> None:
        if self._filling:
            return
        sheet = self.sheet
        data = self.project.data.get(sheet.data_id)
        sheet.kind = self.kind.currentData()
        y_title = self.y_title.text()
        sheet.format.update(
            error=self.error.currentData(), bracket=self.bracket.currentData(), title=self.title.text(), x_title=self.x_title.text(),
            y_title=None if data is not None and y_title == data.table.title else y_title,
            y_min=self._number(self.y_min), y_max=self._number(self.y_max), log_y=self.log_y.isChecked(), show_n=self.show_n.isChecked(),
            dot_size=self.dot_size.value(), font_size=self.font_size.value(), width=self.width.value(), height=self.height.value(),
            units=self.units.currentData(), bracket_mode=self.bracket_mode.currentData(), hide_ns=self.hide_ns.isChecked(),
            letters=self.letters.isChecked(), letter_style=self.letter_style.currentData(), show_outliers=self.show_outliers.isChecked(),
        )
        sheet.result_id = self.comparison.currentData()
        self.window.sheet_renamed(sheet)
        self._redraw.start()

    def _units_changed(self) -> None:
        if self._filling:
            return
        old = self.sheet.format.get("units", "mm")
        new = self.units.currentData()
        if old != new:
            factor = 1 / graphs.MM_PER_INCH if new == "in" else graphs.MM_PER_INCH
            self._filling = True
            for widget in (self.width, self.height):
                widget.setDecimals(2 if new == "in" else 1)
                widget.setValue(widget.value() * factor)
            self._filling = False
        self._changed()

    # --- Drawing ------------------------------------------------------------------------------------------------

    def linked_result(self):
        result = self.project.results.get(self.sheet.result_id) if self.sheet.result_id else None
        return result.result if result is not None else None

    def draw(self) -> list[str]:
        data = self.project.data.get(self.sheet.data_id)
        self.source.setText(f"From data table: {data.name}" if data else "")
        pseudo = data is not None and data.pseudoreplicated
        self.warning.setText(f"⚠ {html.escape(PSEUDO_WARNING)}" if pseudo else "")
        self.warning.setStyleSheet(DANGER_STYLE)
        self.warning.setVisible(pseudo)
        if self.figure is None or data is None:
            self.notes.setVisible(False)
            return []
        width, height = graphs.size_inches({**graphs.DEFAULT_FORMAT, **self.sheet.format})
        available = self.page.viewport().size()
        dpi = max(60.0, min(200.0, (available.width() - 30) / width, (available.height() - 30) / height)) if available.width() > 100 else 110.0
        self.figure.set_dpi(dpi)
        self.canvas.setFixedSize(int(width * dpi), int(height * dpi))
        notes = graphs.draw(self.figure, data, self.sheet, self.linked_result(), self.project.flags(data.id))
        self.figure.set_dpi(dpi)
        self.canvas.draw_idle()
        self.notes.setText("\n".join(notes))
        self.notes.setVisible(bool(notes))
        return notes

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.figure is not None:
            self._redraw.start()

    def export_dialog(self) -> None:
        if self.figure is None:
            self.window.status('Graphs need matplotlib: pip install "cellquant[stats]"')
            return
        dialog = GraphExportDialog(self, self.sheet)
        if dialog.exec():
            self.export(dialog.path(), dialog.kind(), dialog.dpi(), *dialog.size())

    def export(self, path, kind: str, dpi: int = 300, width=None, height=None, units=None) -> Path:
        if self.figure is None:
            raise CellQuantError('Graphs need matplotlib: pip install "cellquant[stats]"')
        data = self.project.data[self.sheet.data_id]
        written = export_figure(data, self.sheet, self.linked_result(), path, kind, dpi, width, height, units, self.project.flags(data.id))
        self.window.status(f"Saved {written}")
        self.window.log(f"graph {self.sheet.name} exported to {written} ({kind}, {dpi} dpi)")
        return written


class GraphExportDialog(QDialog):
    def __init__(self, parent, sheet: GraphSheet):
        super().__init__(parent)
        self.setWindowTitle("Export graph")
        fmt = {**graphs.DEFAULT_FORMAT, **sheet.format}
        form = QFormLayout(self)
        self.format = QComboBox()
        for key, (name, _suffix, _raster) in EXPORT_FORMATS.items():
            self.format.addItem(name, key)
        self.format.setToolTip("Vector formats (SVG, PDF, EPS) stay sharp at any size and can be edited. "
                               "TIFF at 600 dpi or more is what most journals ask for as a raster.")
        self.format.currentIndexChanged.connect(lambda _index: self._format_changed())
        form.addRow("Format", self.format)
        self.dpi_box = QComboBox()
        for value in DPI_CHOICES:
            self.dpi_box.addItem(f"{value} dpi", value)
        self.dpi_box.setCurrentIndex(1)
        self.dpi_box.setToolTip("Dots per inch for PNG, TIFF and JPEG. 300 for screens and drafts, 600 for print, 1200 for line art.")
        form.addRow("Resolution", self.dpi_box)
        self.width = QDoubleSpinBox()
        self.height = QDoubleSpinBox()
        for widget, value in ((self.width, fmt["width"]), (self.height, fmt["height"])):
            widget.setRange(1, 1000)
            widget.setDecimals(2)
            widget.setValue(float(value))
            widget.setToolTip(tips.GRAPH_SIZE)
        self.units = QComboBox()
        self.units.addItem("mm", "mm")
        self.units.addItem("inches", "in")
        self.units.setCurrentIndex(max(0, self.units.findData(fmt["units"])))
        self.units.currentIndexChanged.connect(lambda _index: self._units_changed())
        self._units = self.units.currentData()
        size = QHBoxLayout()
        size.addWidget(self.width)
        size.addWidget(QLabel("×"))
        size.addWidget(self.height)
        size.addWidget(self.units)
        form.addRow("Size", size)
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
        _name, suffix, raster = EXPORT_FORMATS[self.kind()]
        self.dpi_box.setEnabled(raster)
        path = Path(self.file.text())
        self.file.setText(str(path.with_suffix(suffix)))

    def _units_changed(self) -> None:
        new = self.units.currentData()
        if new != self._units:
            factor = 1 / graphs.MM_PER_INCH if new == "in" else graphs.MM_PER_INCH
            for widget in (self.width, self.height):
                widget.setValue(widget.value() * factor)
            self._units = new

    def _browse(self) -> None:
        name, suffix, _raster = EXPORT_FORMATS[self.kind()]
        path, _ = QFileDialog.getSaveFileName(self, "Export graph", self.file.text(), f"{name} (*{suffix})")
        if path:
            self.file.setText(path)

    def kind(self) -> str:
        return self.format.currentData()

    def dpi(self) -> int:
        return int(self.dpi_box.currentData())

    def size(self) -> tuple[float, float, str]:
        return self.width.value(), self.height.value(), self.units.currentData()

    def path(self) -> str:
        return self.file.text()
