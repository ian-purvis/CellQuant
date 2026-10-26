"""The format panel of a graph sheet, and Prism's Format Graph, Format Axes and Magic dialogs.

Each part of the format is one section widget (Graph, Data sets, Frame, Axis, Text and legend, Text
boxes and arrows, Significance, Styles). The side panel stacks them as collapsible sections; the
Format graph… and Format axes… dialogs show fresh copies of the same sections as tabs. Every copy
edits the sheet's GraphFormat and calls ``view.format_changed``, which redraws the graph and refills
the other copies, so the panel and an open dialog always agree.
"""

from __future__ import annotations

import copy

from qtpy.QtCore import Qt, Signal
from qtpy.QtGui import QColor
from qtpy.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from cellquant.gui.quantanalysis import graph_format as gf
from cellquant.gui.quantanalysis import graphs, tips
from cellquant.gui.quantanalysis.datagrid import NOTE_STYLE

T = tips.FORMAT
FONTS = ("Arial", "Helvetica", "Liberation Sans", "DejaVu Sans", "Times New Roman", "Liberation Serif", "DejaVu Serif")
# Groups a saved style holds: the look, not the words or the ranges of one graph.
SAVED_GROUPS = ("colors", "datasets", "frame", "ticks", "fonts", "legend", "significance", "size")
PASTED_GROUPS = tuple(group for group in gf.GROUPS if group not in ("titles", "annotations"))


def _no_buttons(widget):
    widget.setButtonSymbols(QAbstractSpinBox.NoButtons)
    return widget


def _row_visible(form: QFormLayout, widget: QWidget, visible: bool) -> None:
    if hasattr(form, "setRowVisible"):
        form.setRowVisible(widget, visible)
    else:  # Qt before 6.4
        widget.setVisible(visible)
        label = form.labelForField(widget)
        if label is not None:
            label.setVisible(visible)


class ColorButton(QPushButton):
    """A button that shows a colour and picks a new one. With ``auto``, an empty colour means automatic (for example
    the data set's colour) and a right-click goes back to it."""

    picked = Signal(str)

    def __init__(self, text: str = "", tip: str = "", auto: str | None = None):
        super().__init__(text)
        self.label = text
        self.auto = auto
        self.color = ""
        self.setToolTip(tip + ("\nRight-click: back to Auto." if auto else ""))
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.setMinimumWidth(self.fontMetrics().horizontalAdvance(text or "Auto") + 34)
        self.clicked.connect(self._choose)
        if auto:
            self.setContextMenuPolicy(Qt.CustomContextMenu)
            self.customContextMenuRequested.connect(lambda _pos: self.set_color("", emit=True))
        self.set_color("")

    def set_color(self, color: str, emit: bool = False) -> None:
        self.color = color or ""
        self.setText(self.label or ("Auto" if not self.color and self.auto else ""))
        edge = "dashed #8a8f95" if not self.color else f"solid {self.color}"
        self.setStyleSheet(f"QPushButton {{ text-align: left; border-left: 14px {edge}; padding: 3px 8px 3px 6px; }}")
        if emit:
            self.picked.emit(self.color)

    def _choose(self) -> None:
        color = QColorDialog.getColor(QColor(self.color or "#000000"), self, self.toolTip().split("\n")[0][:60])
        if color.isValid():
            self.set_color(color.name(), emit=True)


class TextStyleEdit(QWidget):
    """Font, size, bold, italic and colour of one text, on one row."""

    changed = Signal()

    def __init__(self, tip: str = T["text_style"]):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(3)
        self.family = QComboBox()
        self.family.addItem("Font", "")
        for name in FONTS:
            if graphs.font_available(name):
                self.family.addItem(name, name)
        self.family.setToolTip(T["font_family"])
        self.family.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.family.setMinimumContentsLength(5)
        self.size = _no_buttons(QDoubleSpinBox())
        self.size.setRange(0, 48)
        self.size.setSingleStep(0.5)
        self.size.setSpecialValueText("pt")
        self.size.setMaximumWidth(44)
        self.size.setToolTip("Size in points; blank (0) uses the graph's font size.")
        self.bold = QToolButton()
        self.bold.setText("B")
        self.bold.setCheckable(True)
        self.bold.setStyleSheet("QToolButton { font-weight: bold; min-width: 18px; }")
        self.bold.setToolTip("Bold")
        self.italic = QToolButton()
        self.italic.setText("I")
        self.italic.setCheckable(True)
        self.italic.setStyleSheet("QToolButton { font-style: italic; min-width: 18px; }")
        self.italic.setToolTip("Italic")
        self.color = ColorButton("", "Text colour.", auto="graph")
        for widget in (self.family, self.size, self.bold, self.italic, self.color):
            row.addWidget(widget)
        row.addStretch(1)
        self.setToolTip(tip)
        self.family.currentIndexChanged.connect(lambda _i: self.changed.emit())
        self.size.valueChanged.connect(lambda _v: self.changed.emit())
        self.bold.toggled.connect(lambda _on: self.changed.emit())
        self.italic.toggled.connect(lambda _on: self.changed.emit())
        self.color.picked.connect(lambda _c: self.changed.emit())

    def set_style(self, style: gf.TextStyle) -> None:
        for widget in (self.family, self.size, self.bold, self.italic, self.color):
            widget.blockSignals(True)
        index = self.family.findData(style.family)
        if index < 0 and style.family:
            self.family.addItem(f"{style.family} (not installed)", style.family)
            index = self.family.count() - 1
        self.family.setCurrentIndex(max(0, index))
        self.size.setValue(float(style.size))
        self.bold.setChecked(style.bold)
        self.italic.setChecked(style.italic)
        self.color.set_color(style.color)
        for widget in (self.family, self.size, self.bold, self.italic, self.color):
            widget.blockSignals(False)

    def style(self) -> gf.TextStyle:
        return gf.TextStyle(family=self.family.currentData() or "", size=self.size.value(), bold=self.bold.isChecked(),
                            italic=self.italic.isChecked(), color=self.color.color)


class Collapsible(QWidget):
    """A section of the side panel that opens and closes when its title is clicked."""

    def __init__(self, title: str, body: QWidget, tip: str = "", expanded: bool = False):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.header = QToolButton()
        self.header.setText(title)
        self.header.setCheckable(True)
        self.header.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.header.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.header.setStyleSheet("QToolButton { font-weight: bold; text-align: left; padding: 5px 4px; border: none; "
                                  "border-bottom: 1px solid #414851; }")
        if tip:
            self.header.setToolTip(tip)
        self.body = body
        layout.addWidget(self.header)
        layout.addWidget(body)
        self.header.toggled.connect(self._toggled)
        self.header.setChecked(expanded)
        self._toggled(expanded)

    def _toggled(self, on: bool) -> None:
        self.header.setArrowType(Qt.DownArrow if on else Qt.RightArrow)
        self.body.setVisible(on)

    def expand(self, on: bool = True) -> None:
        self.header.setChecked(on)

    def expanded(self) -> bool:
        return self.header.isChecked()


class Section(QWidget):
    """One part of the format. ``load`` shows the sheet's settings; ``store`` writes the widgets back."""

    title = ""
    key = ""
    tip = ""

    def __init__(self, view):
        super().__init__()
        self.view = view
        self._filling = False
        self.form = QFormLayout(self)
        self.form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.form.setContentsMargins(6, 6, 6, 8)
        self.form.setHorizontalSpacing(8)
        self.build()
        _compact(self)
        self.setStyleSheet(COMPACT_STYLE)


    @property
    def fmt(self) -> gf.GraphFormat:
        return self.view.sheet.format

    def names(self) -> list[str]:
        data = self.view.project.data.get(self.view.sheet.data_id)
        return list(data.table.names) if data is not None else []

    def build(self) -> None:
        raise NotImplementedError

    def load(self) -> None:
        raise NotImplementedError

    def store(self) -> None:
        raise NotImplementedError

    def fill(self) -> None:
        self._filling = True
        try:
            self.load()
        finally:
            self._filling = False

    def changed(self, *_args) -> None:
        if self._filling:
            return
        self.store()
        self.view.format_changed(self)

    # Widgets that write back when they change.

    def check(self, text: str, tip: str) -> QCheckBox:
        box = QCheckBox(text)
        box.setToolTip(tip)
        box.toggled.connect(self.changed)
        return box

    def combo(self, items: dict, tip: str) -> QComboBox:
        box = QComboBox()
        for key, name in items.items():
            box.addItem(name, key)
        box.setToolTip(tip)
        box.currentIndexChanged.connect(self.changed)
        return box

    def number(self, low: float, high: float, tip: str, step: float = 0.1, decimals: int = 2, suffix: str = "", special: str = "") -> QDoubleSpinBox:
        box = _no_buttons(QDoubleSpinBox())
        box.setRange(low, high)
        box.setSingleStep(step)
        box.setDecimals(decimals)
        if suffix:
            box.setSuffix(suffix)
        if special:
            box.setSpecialValueText(special)
        box.setToolTip(tip)
        box.valueChanged.connect(self.changed)
        return box

    def integer(self, low: int, high: int, tip: str, special: str = "", suffix: str = "") -> QSpinBox:
        box = _no_buttons(QSpinBox())
        box.setRange(low, high)
        if special:
            box.setSpecialValueText(special)
        if suffix:
            box.setSuffix(suffix)
        box.setToolTip(tip)
        box.valueChanged.connect(self.changed)
        return box

    def line(self, tip: str, placeholder: str = "") -> QLineEdit:
        edit = QLineEdit()
        edit.setToolTip(tip)
        if placeholder:
            edit.setPlaceholderText(placeholder)
        edit.editingFinished.connect(self.changed)
        return edit

    def color(self, tip: str, auto: str | None = "data set", text: str = "") -> ColorButton:
        button = ColorButton(text, tip, auto)
        button.picked.connect(self.changed)
        return button

    def text_style(self, tip: str = T["text_style"]) -> TextStyleEdit:
        edit = TextStyleEdit(tip)
        edit.changed.connect(self.changed)
        return edit

    def heading(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("QLabel { font-weight: bold; color: #c9ced6; padding-top: 4px; }")
        self.form.addRow(label)
        return label


# napari's theme gives every spin box at least 90 px; rows of two or three would not fit the side panel.
COMPACT_STYLE = "QAbstractSpinBox { min-width: 30px; padding: 1px 4px; }"


def _compact(widget: QWidget) -> None:
    """Let rows of several controls share the side panel's width: long choices and wide number ranges must not widen it
    (a list still opens with every choice in full)."""

    for box in widget.findChildren(QComboBox):
        box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        box.setMinimumContentsLength(4)
        box.setMinimumWidth(64)
    for box in widget.findChildren(QAbstractSpinBox):
        box.setMinimumWidth(46)
    for edit in widget.findChildren(QLineEdit):
        if not isinstance(edit.parent(), QAbstractSpinBox) and not isinstance(edit.parent(), QComboBox):
            edit.setMinimumWidth(40)


def _float_or_none(edit: QLineEdit):
    text = edit.text().strip().replace(",", ".") if edit.text().count(",") == 1 and "." not in edit.text() else edit.text().strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        edit.setText("")
        return None


def _set_number(edit: QLineEdit, value) -> None:
    edit.setText("" if value is None else f"{value:g}")


def _pair(*widgets, stretch: bool = False) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(4)
    for widget in widgets:
        if isinstance(widget, str):
            row.addWidget(QLabel(widget))
        else:
            row.addWidget(widget)
    if stretch:
        row.addStretch(1)
    return row


# --- The graph -------------------------------------------------------------------------------------------------------


class GraphSection(Section):
    title = "Graph"
    key = "graph"
    tip = "The kind of graph, its comparison, error bars, fonts and size."

    def build(self) -> None:
        form = self.form
        self.kind = QComboBox()
        for key, (name, tip) in graphs.KINDS.items():
            self.kind.addItem(name, key)
            self.kind.setItemData(self.kind.count() - 1, tip, Qt.ToolTipRole)
        self.kind.setToolTip("The kind of graph. Hover each choice for when to use it.")
        self.kind.currentIndexChanged.connect(self.changed)
        form.addRow("Graph", self.kind)
        self.error = QComboBox()
        for key, (name, _tip) in graphs.ERRORS.items():
            self.error.addItem(name, key)
            self.error.setItemData(self.error.count() - 1, tips.ERROR[key], Qt.ToolTipRole)
        self.error.setToolTip("\n\n".join(tips.ERROR[key] for key in ("sd", "sem", "ci", "median_iqr")))
        self.error.currentIndexChanged.connect(self.changed)
        form.addRow("Error bars", self.error)
        self.comparison = QComboBox()
        self.comparison.setToolTip(tips.GRAPH_COMPARISON)
        self.comparison.currentIndexChanged.connect(self.changed)
        form.addRow("Comparison", self.comparison)
        self.show_outliers = self.check("Outliers hollow", "Draw the values flagged by Analyze → Identify outliers… as hollow points. The values stay in the table.")
        form.addRow("Outliers", self.show_outliers)
        self.dot_size = self.number(0, 400, tips.DOT_SIZE, 1, 1, special="auto")
        form.addRow("Dot size", self.dot_size)
        self.font_family = QComboBox()
        self.font_family.addItem("Default (sans-serif)", "")
        for name in FONTS:
            if graphs.font_available(name):
                self.font_family.addItem(name, name)
        self.font_family.setToolTip(T["font_family"])
        self.font_family.currentIndexChanged.connect(self.changed)
        self.font_size = self.number(5, 24, T["font_size"], 0.5, 1, " pt")
        form.addRow("Font", _pair(self.font_family, self.font_size))
        self.width_box = self.number(10, 1000, tips.GRAPH_SIZE, 1, 1)
        self.height_box = self.number(10, 1000, tips.GRAPH_SIZE, 1, 1)
        self.units = QComboBox()
        self.units.addItem("mm", "mm")
        self.units.addItem("in", "in")
        self.units.setToolTip(tips.GRAPH_SIZE)
        self.units.currentIndexChanged.connect(self._units_changed)
        form.addRow("Size", _pair(self.width_box, "×", self.height_box, self.units))

    def load(self) -> None:
        sheet, fmt = self.view.sheet, self.fmt
        self.kind.setCurrentIndex(max(0, self.kind.findData(sheet.kind)))
        self.error.setCurrentIndex(max(0, self.error.findData(fmt.error)))
        self.comparison.clear()
        self.comparison.addItem("none", None)
        for result in self.view.project.results.values():
            if result.data_id == sheet.data_id and result.kind in ("compare", "anova"):
                self.comparison.addItem(result.name, result.id)
        self.comparison.setCurrentIndex(max(0, self.comparison.findData(sheet.result_id)))
        self.show_outliers.setChecked(fmt.show_outliers)
        self.dot_size.setValue(float(fmt.dot_size))
        index = self.font_family.findData(fmt.font_family)
        if index < 0:
            self.font_family.addItem(f"{fmt.font_family} (not installed)", fmt.font_family)
            index = self.font_family.count() - 1
        self.font_family.setCurrentIndex(index)
        self.font_size.setValue(float(fmt.font_size))
        self.units.setCurrentIndex(max(0, self.units.findData(fmt.units)))
        for widget in (self.width_box, self.height_box):
            widget.setDecimals(2 if fmt.units == "in" else 1)
        self.width_box.setValue(float(fmt.width))
        self.height_box.setValue(float(fmt.height))

    def store(self) -> None:
        sheet, fmt = self.view.sheet, self.fmt
        sheet.kind = self.kind.currentData()
        sheet.result_id = self.comparison.currentData()
        fmt.error = self.error.currentData()
        fmt.show_outliers = self.show_outliers.isChecked()
        fmt.dot_size = self.dot_size.value()
        fmt.font_family = self.font_family.currentData() or ""
        fmt.font_size = self.font_size.value()
        fmt.width, fmt.height, fmt.units = self.width_box.value(), self.height_box.value(), self.units.currentData()
        self.view.window.sheet_renamed(sheet)

    def _units_changed(self, *_args) -> None:
        if self._filling:
            return
        old, new = self.fmt.units, self.units.currentData()
        if old != new:
            factor = 1 / gf.MM_PER_INCH if new == "in" else gf.MM_PER_INCH
            self._filling = True
            for widget in (self.width_box, self.height_box):
                widget.setDecimals(2 if new == "in" else 1)
                widget.setValue(widget.value() * factor)
            self._filling = False
        self.changed()


# --- Data sets -------------------------------------------------------------------------------------------------------


class DataSetSection(Section):
    title = "Data sets"
    key = "datasets"
    tip = "Symbols, bars, boxes, mean lines and error bars of each data set (column), as in Prism's Format Graph dialog."

    def build(self) -> None:
        form = self.form
        self.which = QComboBox()
        self.which.setToolTip(T["dataset"])
        self.which.currentIndexChanged.connect(lambda _i: self.fill())
        form.addRow("Data set", self.which)
        self.main_color = ColorButton("Colour", T["dataset_color"])
        self.main_color.picked.connect(self._main_color)
        self.visible = self.check("Show", T["visible"])
        self.axis = self.combo({"left": "Left Y", "right": "Right Y"}, T["axis"])
        form.addRow("", _pair(self.visible, self.main_color, self.axis))
        self.heading("Symbols")
        self.show_symbols = self.check("Show", T["show_symbols"])
        self.hollow = self.check("Hollow", T["hollow"])
        form.addRow("", _pair(self.show_symbols, self.hollow, stretch=True))
        self.symbol = self.combo(gf.SYMBOLS, T["symbol"])
        self.symbol_size = self.number(0, 400, T["symbol_size"], 1, 1, special="auto")
        form.addRow("Shape", _pair(self.symbol, "Size", self.symbol_size))
        self.symbol_fill = self.color(T["symbol_fill"])
        self.symbol_border = self.color(T["symbol_border"])
        form.addRow("Fill", _pair(self.symbol_fill, "Border", self.symbol_border, stretch=True))
        self.symbol_border_width = self.number(0, 5, T["symbol_border_width"], 0.1, 2, " pt")
        self.symbol_alpha = self.integer(0, 100, T["symbol_alpha"], special="auto", suffix="%")
        form.addRow("Border", _pair(self.symbol_border_width, "Opacity", self.symbol_alpha))
        self.bars = QGroupBox("Bars")
        self.bars.setFlat(True)
        bars = QFormLayout(self.bars)
        bars.setContentsMargins(0, 4, 0, 0)
        self.show_bar = self.check("Show", T["show_bar"])
        bars.addRow("", self.show_bar)
        self.bar_fill = self.color(T["bar_fill"])
        self.bar_alpha = self.integer(0, 100, T["bar_alpha"], suffix="%")
        bars.addRow("Fill", _pair(self.bar_fill, "Opacity", self.bar_alpha))
        self.hatch = self.combo(gf.HATCHES, T["hatch"])
        bars.addRow("Pattern", self.hatch)
        self.bar_border = self.color(T["bar_border"])
        self.bar_border_width = self.number(0, 5, T["bar_border_width"], 0.1, 2, " pt")
        bars.addRow("Border", _pair(self.bar_border, self.bar_border_width, stretch=True))
        self.bar_width = self.number(0.05, 1.0, T["bar_width"], 0.05, 2)
        bars.addRow("Width", self.bar_width)
        form.addRow(self.bars)
        self.boxes = QGroupBox("Box or violin")
        self.boxes.setFlat(True)
        boxes = QFormLayout(self.boxes)
        boxes.setContentsMargins(0, 4, 0, 0)
        self.box_fill = self.color(T["box_fill"])
        self.box_alpha = self.integer(0, 100, T["box_alpha"], suffix="%")
        boxes.addRow("Fill", _pair(self.box_fill, "Opacity", self.box_alpha, stretch=True))
        self.box_line = self.color(T["box_line"])
        self.box_line_width = self.number(0, 5, T["box_line_width"], 0.1, 2, " pt")
        boxes.addRow("Line", _pair(self.box_line, self.box_line_width, stretch=True))
        self.box_width = self.number(0, 1.0, T["box_width"], 0.05, 2, special="auto")
        boxes.addRow("Width", self.box_width)
        form.addRow(self.boxes)
        self.heading("Mean or median line")
        self.show_center = self.check("Show", T["show_center"])
        self.center_color = self.color(T["center_color"], auto=None)
        form.addRow("", _pair(self.show_center, self.center_color, stretch=True))
        self.center_width = self.number(0, 6, T["center_width"], 0.1, 2, " pt")
        self.center_length = self.number(0.05, 1.0, T["center_length"], 0.05, 2)
        form.addRow("Width", _pair(self.center_width, "Length", self.center_length))
        self.heading("Error bars")
        self.show_error = self.check("Show", T["show_error"])
        self.error_direction = self.combo({"both": "Both directions", "above": "Above only"}, T["error_direction"])
        form.addRow("", _pair(self.show_error, self.error_direction))
        self.error_color = self.color(T["error_color"], auto=None)
        self.error_width = self.number(0, 6, T["error_width"], 0.1, 2, " pt")
        form.addRow("Line", _pair(self.error_color, self.error_width, stretch=True))
        self.error_cap = self.number(0, 1.0, T["error_cap"], 0.05, 2)
        form.addRow("Cap width", self.error_cap)
        self.apply_all = QPushButton("Apply to all data sets")
        self.apply_all.setToolTip(T["apply_all"])
        self.apply_all.clicked.connect(self.apply_to_all)
        self.reset = QPushButton("Reset")
        self.reset.setToolTip(T["reset_dataset"])
        self.reset.clicked.connect(self.reset_dataset)
        form.addRow(_pair(self.apply_all, self.reset, stretch=True))
        self.heading("Connecting lines")
        self.pair_lines = self.check("Before-after", T["pair_lines"])
        self.pair_color = self.color(T["pair_lines"], auto=None)
        self.pair_width = self.number(0, 5, T["pair_lines"], 0.1, 2, " pt")
        form.addRow("", _pair(self.pair_lines, self.pair_color, self.pair_width))
        self.connect_means = self.check("Join means", T["connect_means"])
        self.connect_color = self.color(T["connect_means"], auto=None)
        self.connect_width = self.number(0, 5, T["connect_means"], 0.1, 2, " pt")
        self.connect_style = self.combo({"-": "Solid", "--": "Dashed", ":": "Dotted"}, T["connect_style"])
        form.addRow("", _pair(self.connect_means, self.connect_color, self.connect_width))
        form.addRow("Line style", self.connect_style)

    def current(self) -> str | None:
        return self.which.currentData()

    def show_dataset(self, name: str) -> None:
        index = self.which.findData(name)
        if index >= 0:
            self.which.setCurrentIndex(index)

    def load(self) -> None:
        names = self.names()
        current = self.current()
        self.which.blockSignals(True)
        if [self.which.itemData(i) for i in range(self.which.count())] != names:
            self.which.clear()
            for name in names:
                self.which.addItem(name, name)
        self.which.setCurrentIndex(max(0, self.which.findData(current)) if names else -1)
        self.which.blockSignals(False)
        name = self.current()
        fmt = self.fmt
        kind = self.view.sheet.kind
        self.bars.setVisible(kind == "bar")
        self.boxes.setVisible(kind in ("box", "violin"))
        for widget in (self.pair_lines, self.pair_color, self.pair_width):
            widget.setEnabled(kind == "before_after")
        self.setEnabled(name is not None)
        if name is None:
            return
        self.main_color.set_color(fmt.color_of(name, names.index(name)))
        ds = fmt.dataset(name)
        self.visible.setChecked(ds.visible)
        self.axis.setCurrentIndex(max(0, self.axis.findData(ds.axis)))
        self.show_symbols.setChecked(ds.show_symbols)
        self.hollow.setChecked(ds.hollow)
        self.symbol.setCurrentIndex(max(0, self.symbol.findData(ds.symbol)))
        self.symbol_size.setValue(ds.symbol_size)
        self.symbol_fill.set_color(ds.symbol_fill)
        self.symbol_border.set_color(ds.symbol_border)
        self.symbol_border_width.setValue(ds.symbol_border_width)
        self.symbol_alpha.setValue(round(ds.symbol_alpha * 100))
        self.show_bar.setChecked(ds.show_bar)
        self.bar_fill.set_color(ds.bar_fill)
        self.bar_alpha.setValue(round(ds.bar_alpha * 100))
        self.hatch.setCurrentIndex(max(0, self.hatch.findData(ds.hatch)))
        self.bar_border.set_color(ds.bar_border)
        self.bar_border_width.setValue(ds.bar_border_width)
        self.bar_width.setValue(ds.bar_width)
        self.box_fill.set_color(ds.box_fill)
        self.box_alpha.setValue(round(ds.box_alpha * 100))
        self.box_line.set_color(ds.box_line)
        self.box_line_width.setValue(ds.box_line_width)
        self.box_width.setValue(ds.box_width)
        self.show_center.setChecked(ds.show_center)
        self.center_color.set_color(ds.center_color)
        self.center_width.setValue(ds.center_width)
        self.center_length.setValue(ds.center_length)
        self.show_error.setChecked(ds.show_error)
        self.error_direction.setCurrentIndex(max(0, self.error_direction.findData(ds.error_direction)))
        self.error_color.set_color(ds.error_color)
        self.error_width.setValue(ds.error_width)
        self.error_cap.setValue(ds.error_cap)
        self.pair_lines.setChecked(fmt.pair_lines)
        self.pair_color.set_color(fmt.pair_color)
        self.pair_width.setValue(fmt.pair_width)
        self.connect_means.setChecked(fmt.connect_means)
        self.connect_color.set_color(fmt.connect_color)
        self.connect_width.setValue(fmt.connect_width)
        self.connect_style.setCurrentIndex(max(0, self.connect_style.findData(fmt.connect_style)))

    def store(self) -> None:
        name = self.current()
        if name is None:
            return
        fmt = self.fmt
        ds = fmt.dataset(name)
        ds.visible, ds.axis = self.visible.isChecked(), self.axis.currentData()
        ds.show_symbols, ds.hollow, ds.symbol = self.show_symbols.isChecked(), self.hollow.isChecked(), self.symbol.currentData()
        ds.symbol_size, ds.symbol_fill, ds.symbol_border = self.symbol_size.value(), self.symbol_fill.color, self.symbol_border.color
        ds.symbol_border_width, ds.symbol_alpha = self.symbol_border_width.value(), self.symbol_alpha.value() / 100
        ds.show_bar, ds.bar_fill, ds.bar_alpha = self.show_bar.isChecked(), self.bar_fill.color, self.bar_alpha.value() / 100
        ds.hatch, ds.bar_border, ds.bar_border_width = self.hatch.currentData(), self.bar_border.color, self.bar_border_width.value()
        ds.bar_width = self.bar_width.value()
        ds.box_fill, ds.box_alpha, ds.box_line = self.box_fill.color, self.box_alpha.value() / 100, self.box_line.color
        ds.box_line_width, ds.box_width = self.box_line_width.value(), self.box_width.value()
        ds.show_center, ds.center_color = self.show_center.isChecked(), self.center_color.color or "#111111"
        ds.center_width, ds.center_length = self.center_width.value(), self.center_length.value()
        ds.show_error, ds.error_direction = self.show_error.isChecked(), self.error_direction.currentData()
        ds.error_color, ds.error_width, ds.error_cap = self.error_color.color or "#111111", self.error_width.value(), self.error_cap.value()
        if ds != fmt.dataset_default or name in fmt.datasets:
            fmt.set_dataset(name, ds)
        fmt.pair_lines, fmt.pair_color, fmt.pair_width = self.pair_lines.isChecked(), self.pair_color.color or "#8A8F95", self.pair_width.value()
        fmt.connect_means, fmt.connect_color = self.connect_means.isChecked(), self.connect_color.color or "#111111"
        fmt.connect_width, fmt.connect_style = self.connect_width.value(), self.connect_style.currentData()

    def _main_color(self, color: str) -> None:
        name = self.current()
        if name is not None and color:
            self.fmt.colors[name] = color
            self.view.format_changed(self)

    def apply_to_all(self) -> None:
        name = self.current()
        if name is not None:
            self.fmt.apply_to_all(name)
            self.view.format_changed(None)

    def reset_dataset(self) -> None:
        name = self.current()
        if name is not None:
            self.fmt.datasets.pop(name, None)
            self.view.format_changed(None)


# --- Axes ------------------------------------------------------------------------------------------------------------


class FrameSection(Section):
    title = "Frame and origin"
    key = "frame"
    tip = T["frame"]

    def build(self) -> None:
        self.frame = self.combo(gf.FRAMES, T["frame"])
        self.form.addRow("Frame", self.frame)
        self.frame_offset = self.number(0, 30, T["frame_offset"], 0.5, 1, " pt")
        self.form.addRow("Offset", self.frame_offset)
        self.axis_width = self.number(0.1, 5, T["axis_width"], 0.1, 2, " pt")
        self.axis_color = self.color(T["axis_color"], auto=None)
        self.form.addRow("Axis lines", _pair(self.axis_width, self.axis_color, stretch=True))

    def load(self) -> None:
        fmt = self.fmt
        self.frame.setCurrentIndex(max(0, self.frame.findData(fmt.frame)))
        self.frame_offset.setValue(fmt.frame_offset)
        self.frame_offset.setEnabled(fmt.frame == "offset")
        self.axis_width.setValue(fmt.axis_width)
        self.axis_color.set_color(fmt.axis_color)

    def store(self) -> None:
        fmt = self.fmt
        fmt.frame, fmt.frame_offset = self.frame.currentData(), self.frame_offset.value()
        fmt.axis_width, fmt.axis_color = self.axis_width.value(), self.axis_color.color or "#222222"


AXIS_NAMES = {"y": "Left Y axis", "x": "X axis", "right": "Right Y axis"}


class AxisSection(Section):
    title = "Axis"
    key = "axis"
    tip = T["which_axis"]

    def __init__(self, view, which: str | None = None):
        self.fixed = which
        super().__init__(view)

    def build(self) -> None:
        form = self.form
        self.which = self.combo(AXIS_NAMES, T["which_axis"])
        self.which.currentIndexChanged.disconnect()
        self.which.currentIndexChanged.connect(lambda _i: self.fill())
        if self.fixed:
            self.which.blockSignals(True)
            self.which.setCurrentIndex(self.which.findData(self.fixed))
            self.which.blockSignals(False)
            self.which.setVisible(False)
        else:
            form.addRow("Axis", self.which)
        self.right_axis = self.check("Right Y axis", T["right_axis"])
        form.addRow("", self.right_axis)
        self.visible = self.check("Show axis", T["axis_visible"])
        self.reverse = self.check("Reverse", T["reverse"])
        self.visible_row = _pair(self.visible, self.reverse, stretch=True)
        form.addRow("", self.visible_row)
        self.title_text = self.line(T["axis_title"], "none")
        form.addRow("Title", self.title_text)
        self.title_style = self.text_style()
        form.addRow("", self.title_style)
        self.low, self.high = self.line(T["range"], "auto"), self.line(T["range"], "auto")
        self.range_row = QWidget()
        self.range_row.setLayout(_pair(self.low, "to", self.high))
        self.range_row.layout().setContentsMargins(0, 0, 0, 0)
        form.addRow("Range", self.range_row)
        self.scale = self.combo(gf.SCALES, T["scale"])
        form.addRow("Scale", self.scale)
        self.major = self.line(T["major"], "auto")
        self.minor = self.integer(0, 9, T["minor"], special="none")
        self.ticks_row = QWidget()
        self.ticks_row.setLayout(_pair(self.major, "Minor", self.minor))
        self.ticks_row.layout().setContentsMargins(0, 0, 0, 0)
        form.addRow("Tick every", self.ticks_row)
        self.tick_direction = self.combo(gf.TICK_DIRECTIONS, T["tick_direction"])
        self.tick_length = self.number(0, 20, T["tick_length"], 0.5, 1, " pt")
        form.addRow("Ticks", _pair(self.tick_direction, self.tick_length))
        self.decimals = self.integer(-1, 8, T["decimals"], special="auto")
        self.thousands = self.check("1,000", T["thousands"])
        self.percent = self.check("%", T["percent"])
        self.numbers_row = QWidget()
        self.numbers_row.setLayout(_pair(self.decimals, self.thousands, self.percent, stretch=True))
        self.numbers_row.layout().setContentsMargins(0, 0, 0, 0)
        form.addRow("Decimals", self.numbers_row)
        self.notation = self.combo(gf.NOTATIONS, T["notation"])
        form.addRow("Notation", self.notation)
        self.prefix, self.suffix = self.line(T["prefix"], "prefix"), self.line(T["suffix"], "suffix")
        self.affix_row = QWidget()
        self.affix_row.setLayout(_pair(self.prefix, self.suffix))
        self.affix_row.layout().setContentsMargins(0, 0, 0, 0)
        form.addRow("Text", self.affix_row)
        self.numbers = self.text_style()
        form.addRow("Numbers", self.numbers)
        # Y only: breaks.
        self.gaps = [(self.line(T["breaks"], "from"), self.line(T["breaks"], "to")) for _ in range(2)]
        self.break_rows = []
        for number, (low, high) in enumerate(self.gaps):
            row = QWidget()
            row.setLayout(_pair(low, "to", high))
            row.layout().setContentsMargins(0, 0, 0, 0)
            form.addRow(f"Break {number + 1}", row)
            self.break_rows.append(row)
        self.segments = [self.integer(0, 95, T["segments"], special="auto", suffix="%") for _ in range(3)]
        self.segments_row = QWidget()
        self.segments_row.setLayout(_pair(*self.segments))
        self.segments_row.layout().setContentsMargins(0, 0, 0, 0)
        form.addRow("Heights", self.segments_row)
        # X only: the column labels.
        self.show_n = self.check("n under columns", tips.SHOW_N)
        self.hide_labels = self.check("Hide labels", T["hide_labels"])
        self.labels_row = QWidget()
        self.labels_row.setLayout(_pair(self.show_n, self.hide_labels, stretch=True))
        self.labels_row.layout().setContentsMargins(0, 0, 0, 0)
        form.addRow("Labels", self.labels_row)
        self.label_angle = self.number(-90, 90, T["label_angle"], 5, 0, "°")
        self.label_wrap = self.integer(0, 60, T["label_wrap"], special="off", suffix=" chars")
        self.angle_row = QWidget()
        self.angle_row.setLayout(_pair(self.label_angle, "Wrap", self.label_wrap))
        self.angle_row.layout().setContentsMargins(0, 0, 0, 0)
        form.addRow("Angle", self.angle_row)

    def axis_key(self) -> str:
        return self.fixed or self.which.currentData() or "y"

    def show_axis(self, key: str) -> None:
        if not self.fixed:
            self.which.setCurrentIndex(max(0, self.which.findData(key)))

    def axis(self) -> gf.AxisFormat:
        return getattr(self.fmt, self.axis_key())

    def load(self) -> None:
        key, fmt = self.axis_key(), self.fmt
        axis = self.axis()
        data = self.view.project.data.get(self.view.sheet.data_id)
        is_x, is_y, is_right = key == "x", key == "y", key == "right"
        form = self.form
        _row_visible(form, self.right_axis, is_right)
        for widget in (self.range_row, self.scale, self.ticks_row, self.numbers_row, self.notation, self.affix_row):
            _row_visible(form, widget, not is_x)
        for widget in (*self.break_rows, self.segments_row):
            _row_visible(form, widget, is_y)
        for widget in (self.labels_row, self.angle_row):
            _row_visible(form, widget, is_x)
        self.reverse.setVisible(not is_x)
        self.right_axis.setChecked(fmt.right_axis)
        self.visible.setChecked(axis.visible)
        self.reverse.setChecked(axis.reverse)
        title = axis.title
        if is_y and title is None:
            title = data.table.title if data is not None else ""
        self.title_text.setText(title or "")
        self.title_style.set_style(axis.title_style)
        _set_number(self.low, axis.min)
        _set_number(self.high, axis.max)
        self.scale.setCurrentIndex(max(0, self.scale.findData(axis.scale)))
        self.major.setText(f"{axis.major:g}" if axis.major else "")
        self.minor.setValue(axis.minor)
        self.tick_direction.setCurrentIndex(max(0, self.tick_direction.findData(axis.tick_direction)))
        self.tick_length.setValue(axis.tick_length)
        self.decimals.setValue(axis.decimals)
        self.thousands.setChecked(axis.thousands)
        self.percent.setChecked(axis.percent)
        self.notation.setCurrentIndex(max(0, self.notation.findData(axis.notation)))
        self.prefix.setText(axis.prefix)
        self.suffix.setText(axis.suffix)
        self.numbers.set_style(axis.numbers)
        for number, (low, high) in enumerate(self.gaps):
            gap = axis.breaks[number] if number < len(axis.breaks) else None
            _set_number(low, float(gap[0]) if gap else None)
            _set_number(high, float(gap[1]) if gap else None)
        for number, box in enumerate(self.segments):
            box.setValue(int(round(float(axis.segments[number]) * 100)) if number < len(axis.segments) else 0)
            box.setEnabled(number <= len([gap for gap in axis.breaks if gap]))
        self.show_n.setChecked(fmt.show_n)
        self.hide_labels.setChecked(axis.hide_labels)
        self.label_angle.setValue(axis.label_angle)
        self.label_wrap.setValue(axis.label_wrap)
        enabled = not is_right or fmt.right_axis or any(fmt.dataset(name).axis == "right" for name in self.names())
        for widget in (self.visible, self.title_text, self.title_style, self.range_row, self.scale, self.ticks_row, self.numbers_row,
                       self.notation, self.affix_row, self.numbers):
            widget.setEnabled(enabled)

    def store(self) -> None:
        key, fmt = self.axis_key(), self.fmt
        axis = self.axis()
        data = self.view.project.data.get(self.view.sheet.data_id)
        if key == "right":
            fmt.right_axis = self.right_axis.isChecked()
        axis.visible, axis.reverse = self.visible.isChecked(), self.reverse.isChecked() and key != "x"
        title = self.title_text.text()
        axis.title = None if key == "y" and data is not None and title == data.table.title else title
        axis.title_style = self.title_style.style()
        axis.min, axis.max = _float_or_none(self.low), _float_or_none(self.high)
        axis.scale = self.scale.currentData()
        axis.major = _float_or_none(self.major) or 0.0
        if axis.major < 0:
            axis.major = 0.0
        axis.minor = self.minor.value()
        axis.tick_direction, axis.tick_length = self.tick_direction.currentData(), self.tick_length.value()
        axis.decimals, axis.thousands, axis.percent = self.decimals.value(), self.thousands.isChecked(), self.percent.isChecked()
        axis.notation, axis.prefix, axis.suffix = self.notation.currentData(), self.prefix.text(), self.suffix.text()
        axis.numbers = self.numbers.style()
        if key == "y":
            gaps = []
            for low, high in self.gaps:
                a, b = _float_or_none(low), _float_or_none(high)
                if a is not None and b is not None and a != b:
                    gaps.append(sorted((a, b)))
            axis.breaks = gaps
            heights = [box.value() / 100 for box in self.segments[:len(gaps) + 1]]
            axis.segments = heights if gaps and all(heights) else []
        if key == "x":
            fmt.show_n = self.show_n.isChecked()
            axis.hide_labels, axis.label_angle, axis.label_wrap = self.hide_labels.isChecked(), self.label_angle.value(), self.label_wrap.value()


class AxesSection(QWidget):
    """The side panel's Axes section: the frame, then one axis at a time."""

    title = "Axes"
    key = "axes"
    tip = "Frame and origin, and each axis's range, scale, ticks, numbering, breaks and title (Prism's Format Axes dialog)."

    def __init__(self, view):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.frame = FrameSection(view)
        self.axis = AxisSection(view)
        layout.addWidget(self.frame)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: #414851;")
        layout.addWidget(line)
        layout.addWidget(self.axis)

    def sections(self):
        return [self.frame, self.axis]


# --- Text, legend and annotations ---------------------------------------------------------------------------------------


class TextSection(Section):
    title = "Title and legend"
    key = "text"
    tip = "The graph title, and the legend: whether it shows, where, its frame and order."

    def build(self) -> None:
        form = self.form
        self.title_text = self.line(T["title"], "none")
        form.addRow("Title", self.title_text)
        self.title_style = self.text_style()
        self.title_loc = self.combo({"left": "Left", "center": "Centre", "right": "Right"}, T["title_loc"])
        form.addRow("", self.title_style)
        form.addRow("Align", self.title_loc)
        self.heading("Legend")
        self.legend_show = self.check("Show", T["legend_show"])
        self.legend_frame = self.check("Frame", T["legend_frame"])
        form.addRow("", _pair(self.legend_show, self.legend_frame, stretch=True))
        self.legend_position = self.combo(gf.LEGEND_POSITIONS, T["legend_position"])
        form.addRow("Position", self.legend_position)
        self.legend_order = QListWidget()
        self.legend_order.setDragDropMode(QAbstractItemView.InternalMove)
        self.legend_order.setToolTip(T["legend_order"])
        self.legend_order.setMaximumHeight(96)
        self.legend_order.model().rowsMoved.connect(self.changed)
        form.addRow("Order", self.legend_order)
        self.legend_text = self.text_style()
        form.addRow("Text", self.legend_text)

    def load(self) -> None:
        fmt = self.fmt
        self.title_text.setText(fmt.title)
        self.title_style.set_style(fmt.title_style)
        self.title_loc.setCurrentIndex(max(0, self.title_loc.findData(fmt.title_loc)))
        legend = fmt.legend
        self.legend_show.setChecked(legend.show)
        self.legend_frame.setChecked(legend.frame)
        self.legend_position.setCurrentIndex(max(0, self.legend_position.findData(legend.position)))
        names = self.names()
        order = [name for name in legend.order if name in names] + [name for name in names if name not in legend.order]
        self.legend_order.clear()
        for name in order:
            self.legend_order.addItem(QListWidgetItem(name))
        self.legend_text.set_style(legend.text)

    def store(self) -> None:
        fmt = self.fmt
        fmt.title, fmt.title_style, fmt.title_loc = self.title_text.text(), self.title_style.style(), self.title_loc.currentData()
        legend = fmt.legend
        legend.show, legend.frame = self.legend_show.isChecked(), self.legend_frame.isChecked()
        legend.position = self.legend_position.currentData()
        if legend.position == "custom" and not legend.xy:
            legend.xy = [0.7, 0.98]
        legend.order = [self.legend_order.item(row).text() for row in range(self.legend_order.count())]
        legend.text = self.legend_text.style()


class AnnotationSection(Section):
    title = "Text boxes and arrows"
    key = "annotations"
    tip = "Free text boxes, arrows, lines and brackets. Drag them on the graph to move them."

    def build(self) -> None:
        form = self.form
        adds = QHBoxLayout()
        adds.setSpacing(3)
        adds.addWidget(QLabel("Add"))
        self.add_buttons = {}
        for kind, name in gf.ANNOTATION_KINDS.items():
            button = QPushButton(name)
            button.setToolTip(T[f"add_{kind}"])
            button.clicked.connect(lambda _checked=False, kind=kind: self.add(kind))
            adds.addWidget(button)
            self.add_buttons[kind] = button
        adds.addStretch(1)
        form.addRow(adds)
        self.list = QListWidget()
        self.list.setMaximumHeight(90)
        self.list.setToolTip("The text boxes, arrows, lines and brackets on this graph. Click one here or on the graph to edit it.")
        self.list.currentRowChanged.connect(self._picked)
        self.delete = QPushButton("Delete")
        self.delete.setToolTip(T["delete_annotation"])
        self.delete.clicked.connect(self.remove)
        listed = QHBoxLayout()
        listed.addWidget(self.list, 1)
        listed.addWidget(self.delete, 0, Qt.AlignTop)
        form.addRow(listed)
        self.text = self.line(T["annotation_text"], "text")
        form.addRow("Text", self.text)
        self.note_style = self.text_style()
        form.addRow("", self.note_style)
        self.color_button = self.color("Colour of the text, line or arrow.", auto=None)
        self.line_width = self.number(0.1, 6, "Line thickness, in points (also the text box border).", 0.1, 2, " pt")
        self.box = self.check("Box", T["annotation_box"])
        form.addRow("Line", _pair(self.color_button, self.line_width, self.box, stretch=True))
        self.pos_x, self.pos_y, self.pos_x2, self.pos_y2 = (self.number(-0.5, 1.5, T["annotation_position"], 0.01, 3) for _ in range(4))
        form.addRow("From x, y", _pair(self.pos_x, self.pos_y))
        form.addRow("To x, y", _pair(self.pos_x2, self.pos_y2))
        self.editors = (self.text, self.note_style, self.color_button, self.line_width, self.box, self.pos_x, self.pos_y, self.pos_x2, self.pos_y2, self.delete)

    def current(self) -> gf.Annotation | None:
        row = self.list.currentRow()
        notes = self.fmt.annotations
        return notes[row] if 0 <= row < len(notes) else None

    def show_annotation(self, note_id: str) -> None:
        for row, note in enumerate(self.fmt.annotations):
            if note.id == note_id:
                self.list.setCurrentRow(row)

    def load(self) -> None:
        notes = self.fmt.annotations
        row = self.list.currentRow()
        self.list.blockSignals(True)
        self.list.clear()
        for note in notes:
            self.list.addItem(f"{gf.ANNOTATION_KINDS.get(note.kind, note.kind)}: {note.text or '(no text)'}")
        self.list.setCurrentRow(min(max(row, 0), len(notes) - 1) if notes else -1)
        self.list.blockSignals(False)
        note = self.current()
        for widget in self.editors:
            widget.setEnabled(note is not None)
        if note is None:
            return
        self.text.setText(note.text)
        self.note_style.set_style(note.style)
        self.color_button.set_color(note.color)
        self.line_width.setValue(note.line_width)
        self.box.setChecked(note.box)
        self.box.setVisible(note.kind == "text")
        for widget, value in ((self.pos_x, note.x), (self.pos_y, note.y), (self.pos_x2, note.x2), (self.pos_y2, note.y2)):
            widget.setValue(value)
        for widget in (self.pos_x2, self.pos_y2):
            widget.setEnabled(note.kind != "text")

    def store(self) -> None:
        note = self.current()
        if note is None:
            return
        note.text, note.style, note.color = self.text.text(), self.note_style.style(), self.color_button.color or "#111111"
        note.line_width, note.box = self.line_width.value(), self.box.isChecked()
        note.x, note.y, note.x2, note.y2 = self.pos_x.value(), self.pos_y.value(), self.pos_x2.value(), self.pos_y2.value()
        if note.kind == "bracket":
            note.y2 = note.y

    def _picked(self, row: int) -> None:
        if self._filling:
            return
        self.fill()
        note = self.current()
        if note is not None:
            self.view.select(("annotation", note.id), reveal=False)

    def add(self, kind: str) -> gf.Annotation:
        count = len(self.fmt.annotations)
        shift = 0.04 * (count % 5)
        if kind == "text":
            note = gf.Annotation(kind="text", text="Text", x=0.5 + shift, y=0.92 - shift)
        elif kind == "arrow":
            note = gf.Annotation(kind="arrow", text="", x=0.35 + shift, y=0.92 - shift, x2=0.5 + shift, y2=0.75 - shift)
        elif kind == "line":
            note = gf.Annotation(kind="line", x=0.25 + shift, y=0.9 - shift, x2=0.75 + shift, y2=0.9 - shift)
        else:
            note = gf.Annotation(kind="bracket", text="*", x=0.25 + shift, y=0.93 - shift, x2=0.75 + shift, y2=0.93 - shift)
        self.fmt.annotations.append(note)
        self.view.format_changed(None)
        self.show_annotation(note.id)
        self.view.select(("annotation", note.id), reveal=False)
        return note

    def remove(self) -> None:
        note = self.current()
        if note is not None:
            self.fmt.annotations.remove(note)
            self.view.select(None)
            self.view.format_changed(None)


# --- Significance ------------------------------------------------------------------------------------------------------


class SignificanceSection(Section):
    title = "Significance"
    key = "significance"
    tip = "Brackets, stars, P values and compact letters of the linked comparison, and how they are drawn."

    def build(self) -> None:
        form = self.form
        self.bracket = self.combo({"stars": "Stars (P summary)", "p": "P value", "none": "None"}, tips.BRACKET)
        form.addRow("Show", self.bracket)
        self.bracket_mode = self.combo({"brackets": "Brackets", "columns": "Asterisks above columns"}, tips.BRACKET_MODE)
        form.addRow("Style", self.bracket_mode)
        self.hide_ns = self.check("Hide ns", tips.HIDE_NS)
        self.letters = self.check("Compact letters", tips.LETTERS)
        self.letter_style = self.combo({"upper": "A B C", "lower": "a b c", "numbers": "1 2 3"}, tips.LETTER_STYLE)
        marks = QVBoxLayout()
        marks.addWidget(self.hide_ns)
        marks.addLayout(_pair(self.letters, self.letter_style))
        form.addRow("", marks)
        self.ref_buttons = [ColorButton(label, tips.REF_COLORS) for label in ("1st", "2nd")]
        for number, button in enumerate(self.ref_buttons):
            button.picked.connect(lambda color, number=number: self.view.set_ref_color(number, color))
        ref_title = QLabel("Reference colours")
        ref_title.setToolTip(tips.REF_COLORS)
        form.addRow(ref_title, _pair(*self.ref_buttons, stretch=True))
        self.bracket_width = self.number(0.1, 5, T["bracket_width"], 0.1, 2, " pt")
        self.star_size = self.number(0, 30, T["star_size"], 0.5, 1, " pt", special="auto")
        form.addRow("Line", _pair(self.bracket_width, "Stars", self.star_size))
        self.bracket_gap = self.number(0, 0.5, T["bracket_gap"], 0.01, 2)
        self.bracket_tip = self.number(0, 0.2, T["bracket_tip"], 0.01, 2)
        form.addRow("Gap", _pair(self.bracket_gap, "Ends", self.bracket_tip))

    def load(self) -> None:
        fmt = self.fmt
        self.bracket.setCurrentIndex(max(0, self.bracket.findData(fmt.bracket)))
        self.bracket_mode.setCurrentIndex(max(0, self.bracket_mode.findData(fmt.bracket_mode)))
        self.hide_ns.setChecked(fmt.hide_ns)
        self.letters.setChecked(fmt.letters)
        self.letter_style.setCurrentIndex(max(0, self.letter_style.findData(fmt.letter_style)))
        colors = list(fmt.ref_colors) + graphs.DEFAULT_FORMAT["ref_colors"][len(fmt.ref_colors):]
        for number, button in enumerate(self.ref_buttons):
            button.set_color(colors[number])
        self.bracket_width.setValue(fmt.bracket_width)
        self.star_size.setValue(fmt.star_size)
        self.bracket_gap.setValue(fmt.bracket_gap)
        self.bracket_tip.setValue(fmt.bracket_tip)

    def store(self) -> None:
        fmt = self.fmt
        fmt.bracket, fmt.bracket_mode = self.bracket.currentData(), self.bracket_mode.currentData()
        fmt.hide_ns, fmt.letters, fmt.letter_style = self.hide_ns.isChecked(), self.letters.isChecked(), self.letter_style.currentData()
        fmt.bracket_width, fmt.star_size = self.bracket_width.value(), self.star_size.value()
        fmt.bracket_gap, fmt.bracket_tip = self.bracket_gap.value(), self.bracket_tip.value()


# --- Styles ----------------------------------------------------------------------------------------------------------------


class StyleSection(Section):
    title = "Styles"
    key = "styles"
    tip = "Apply a built-in or saved style, save this graph's look, make it look like another graph (Magic), or copy and paste its format."

    def build(self) -> None:
        form = self.form
        self.choice = QComboBox()
        self.choice.setToolTip(T["style"])
        self.choice.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.choice.setMinimumContentsLength(14)
        self.apply = QPushButton("Apply")
        self.apply.setToolTip(T["apply_style"])
        self.apply.clicked.connect(lambda: self.view.apply_named_style(self.choice.currentData()))
        form.addRow("Style", _pair(self.choice, self.apply))
        self.save = QPushButton("Save as…")
        self.save.setToolTip(T["save_style"])
        self.save.clicked.connect(self._save)
        self.delete = QPushButton("Delete")
        self.delete.setToolTip(T["delete_style"])
        self.delete.clicked.connect(self._delete)
        form.addRow("", _pair(self.save, self.delete, stretch=True))
        self.magic = QPushButton("Make this graph look like…")
        self.magic.setToolTip(T["magic"])
        self.magic.clicked.connect(self.view.magic_dialog)
        form.addRow("Magic", self.magic)
        self.copy = QPushButton("Copy format")
        self.copy.setToolTip(T["copy_format"])
        self.copy.clicked.connect(self.view.copy_format)
        self.paste = QPushButton("Paste format")
        self.paste.setToolTip(T["paste_format"])
        self.paste.clicked.connect(self.view.paste_format)
        form.addRow("", _pair(self.copy, self.paste, stretch=True))
        self.choice.currentIndexChanged.connect(lambda _i: self.delete.setEnabled(self.choice.currentData() in gf.load_styles()))

    def load(self) -> None:
        current = self.choice.currentData()
        self.choice.blockSignals(True)
        self.choice.clear()
        saved = gf.load_styles()
        for name in gf.BUILTIN_STYLES:
            if name not in saved:
                self.choice.addItem(name, name)
                self.choice.setItemData(self.choice.count() - 1, gf.BUILTIN_TIPS.get(name, ""), Qt.ToolTipRole)
        for name in saved:
            self.choice.addItem(f"{name} (saved)", name)
        self.choice.setCurrentIndex(max(0, self.choice.findData(current)))
        self.choice.blockSignals(False)
        self.delete.setEnabled(self.choice.currentData() in saved)

    def store(self) -> None:
        pass

    def _save(self) -> None:
        name, ok = QInputDialog.getText(self, "Save style", "Name of the style:")
        if ok and name.strip():
            self.view.save_style(name.strip())

    def _delete(self) -> None:
        name = self.choice.currentData()
        if name in gf.load_styles():
            gf.delete_style(name)
            self.view.format_changed(None)


SECTIONS = (GraphSection, DataSetSection, AxesSection, TextSection, AnnotationSection, SignificanceSection, StyleSection)


# --- Dialogs -------------------------------------------------------------------------------------------------------------


class FormatDialog(QDialog):
    """Prism's Format Graph or Format Axes dialog: the same sections as the side panel, as tabs. Changes show at once;
    Cancel puts back the format the graph had when the dialog opened."""

    def __init__(self, view, which: str = "graph", tab: str | None = None):
        super().__init__(view)
        self.view = view
        self.which = which
        self.setWindowTitle("Format graph" if which == "graph" else "Format axes")
        self._before = (view.sheet.format.copy(), view.sheet.kind, view.sheet.result_id)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        if which == "graph":
            pages = [("datasets", "Data sets", DataSetSection(view)), ("graph", "Graph", GraphSection(view)),
                     ("text", "Title, legend", TextSection(view)), ("annotations", "Text, arrows", AnnotationSection(view)),
                     ("significance", "Significance", SignificanceSection(view))]
        else:
            pages = [("frame", "Frame and origin", FrameSection(view)), ("y", "Left Y axis", AxisSection(view, "y")),
                     ("x", "X axis", AxisSection(view, "x")), ("right", "Right Y axis", AxisSection(view, "right"))]
        self.pages = {}
        for key, name, section in pages:
            page = QScrollArea()
            page.setWidgetResizable(True)
            page.setFrameShape(QFrame.NoFrame)
            holder = QWidget()
            column = QVBoxLayout(holder)
            column.setContentsMargins(0, 0, 0, 0)
            column.addWidget(section)
            column.addStretch(1)
            page.setWidget(holder)
            self.tabs.addTab(page, name)
            section.page = page
            self.tabs.setTabToolTip(self.tabs.count() - 1, section.tip)
            self.pages[key] = section
            view.add_section(section)
            section.fill()
        if tab in self.pages:
            self.tabs.setCurrentWidget(self.pages[tab].page)
        hint = QLabel("Changes show on the graph at once. " + tips.SELECT)
        hint.setWordWrap(True)
        hint.setStyleSheet(NOTE_STYLE)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Cancel).setToolTip("Put back the format the graph had when this dialog opened.")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setMinimumWidth(500)
        self.resize(500, 760 if which == "graph" else 600)
        self.finished.connect(self._closed)

    def show_tab(self, key: str) -> None:
        if key in self.pages:
            self.tabs.setCurrentWidget(self.pages[key].page)

    def current(self):
        """The section on the tab shown."""

        return next((section for section in self.pages.values() if section.page is self.tabs.currentWidget()), None)

    def reject(self) -> None:
        fmt, kind, result_id = self._before
        self.view.sheet.format = fmt
        self.view.sheet.kind, self.view.sheet.result_id = kind, result_id
        self.view.format_changed(None)
        super().reject()

    def _closed(self, _result) -> None:
        for section in self.pages.values():
            self.view.remove_section(section)
        self.view.dialogs.pop(self.which, None)


class MagicDialog(QDialog):
    """Make this graph look like another graph or a style (Prism Magic), choosing which parts of the format to copy."""

    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.setWindowTitle("Make this graph look like…")
        form = QFormLayout(self)
        intro = QLabel("Copy the look of another graph or of a style onto this graph. The data, the kind of graph and the analyses do not change.")
        intro.setWordWrap(True)
        intro.setStyleSheet(NOTE_STYLE)
        form.addRow(intro)
        self.source = QComboBox()
        self.source.setToolTip(tips.MAGIC_SOURCE)
        for graph in view.project.graphs.values():
            if graph.id != view.sheet.id:
                self.source.addItem(f"Graph: {graph.name}", f"graph:{graph.id}")
        for name in gf.all_styles():
            self.source.addItem(f"Style: {name}", f"style:{name}")
        form.addRow("Look like", self.source)
        self.groups = {}
        boxes = QVBoxLayout()
        boxes.setSpacing(2)
        for key, (name, tip, _paths) in gf.GROUPS.items():
            box = QCheckBox(name)
            box.setToolTip(tip)
            box.setChecked(key in gf.DEFAULT_GROUPS)
            boxes.addWidget(box)
            self.groups[key] = box
        form.addRow("Copy", boxes)
        self.targets = QListWidget()
        self.targets.setToolTip(tips.MAGIC_TARGETS)
        self.targets.setMaximumHeight(110)
        for graph in view.project.graphs.values():
            if graph.id != view.sheet.id:
                item = QListWidgetItem(graph.name)
                item.setData(Qt.UserRole, graph.id)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Unchecked)
                self.targets.addItem(item)
        form.addRow("Also apply to", self.targets)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Apply")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.setMinimumWidth(440)

    def chosen_groups(self) -> list[str]:
        return [key for key, box in self.groups.items() if box.isChecked()]

    def target_ids(self) -> list[str]:
        ids = [self.view.sheet.id]
        for row in range(self.targets.count()):
            item = self.targets.item(row)
            if item.checkState() == Qt.Checked:
                ids.append(item.data(Qt.UserRole))
        return ids

    def style(self) -> dict | None:
        source = self.source.currentData()
        if source is None:
            return None
        kind, _, key = source.partition(":")
        if kind == "style":
            return copy.deepcopy(gf.all_styles().get(key))
        graph = self.view.project.graphs.get(key)
        if graph is None:
            return None
        data = self.view.project.data.get(graph.data_id)
        return gf.make_style(graph.format, data.table.names if data else [])


def confirm(parent, text: str) -> bool:
    return QMessageBox.question(parent, "QuantAnalysis", text) == QMessageBox.Yes


def clipboard():
    return QApplication.clipboard()
