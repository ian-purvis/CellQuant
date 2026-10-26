"""The format of a graph (Prism's Format Graph and Format Axes dialogs), as plain data, and named styles.

``GraphFormat`` holds every setting of a graph sheet: the look of each data set (column), the axes, the
text, the legend, free annotations and the significance marks. It is a dataclass that serializes to a
JSON-ready dict (``to_dict`` / ``from_dict``), so a project file can save it, and ``graphs.draw`` draws
from it alone. It also reads and writes the flat keys of the first graph format (``fmt["y_min"]``,
``fmt.update(log_y=True)``) so older code and saved dicts keep working.

Styles copy chosen groups of settings from one graph to another (Prism Magic). Saved styles live in
``graph_styles.json`` beside CellQuant's per-computer settings (%LOCALAPPDATA%\\CellQuant on Windows).
No Qt and no matplotlib here.
"""

from __future__ import annotations

import copy
import json
import math
import os
import re
import uuid
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path

PALETTE = ["#5B6770", "#2F7FC1", "#D1495B", "#E3A33B", "#3A9D78", "#7E5BA6", "#A0522D", "#4BA3C3"]
# Okabe and Ito's palette, told apart with every common colour-vision deficiency; yellow last (faint on white).
COLORBLIND_PALETTE = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7", "#56B4E9", "#000000", "#F0E442"]
MM_PER_INCH = 25.4

# key: menu name. Matplotlib marker codes.
SYMBOLS = {"o": "Circle", "s": "Square", "^": "Triangle up", "v": "Triangle down", "D": "Diamond", "h": "Hexagon",
           "P": "Plus (filled)", "X": "Cross (filled)", "*": "Star", "+": "Plus", "x": "Cross", "_": "Dash"}
OPEN_SYMBOLS = {"+", "x", "_"}  # line-only markers: drawn with the border colour
HATCHES = {"": "None", "///": "Diagonal /", "\\\\\\": "Diagonal \\", "xxx": "Cross-hatch", "---": "Horizontal", "|||": "Vertical",
           "+++": "Grid", "...": "Dots", "ooo": "Circles"}
SCALES = {"linear": "Linear", "log10": "Log 10", "log2": "Log 2", "ln": "Natural log (ln)"}
TICK_DIRECTIONS = {"out": "Out", "in": "In", "inout": "Across", "none": "None"}
FRAMES = {"plain": "Plain (X and Y meet)", "offset": "Offset X and Y axes", "box": "Box (all four sides)", "none": "No axis lines"}
NOTATIONS = {"decimal": "Decimal", "scientific": "Scientific (1.5×10³)", "power": "Power of base (10³, log axes)"}
LEGEND_POSITIONS = {"best": "Inside, where it hides least", "upper right": "Inside, top right", "upper left": "Inside, top left", "lower right": "Inside, bottom right",
                    "lower left": "Inside, bottom left", "right": "Outside, right", "master": "Master: one row above",
                    "custom": "Where dragged"}
ANNOTATION_KINDS = {"text": "Text", "arrow": "Arrow", "line": "Line", "bracket": "Bracket"}


@dataclass
class TextStyle:
    """Font of one piece of text. Empty or 0 means "the graph's" (font, size, colour)."""

    family: str = ""
    size: float = 0.0
    bold: bool = False
    italic: bool = False
    color: str = ""


@dataclass
class DataSetFormat:
    """How one data set (column) is drawn. An empty colour means the data set's colour."""

    visible: bool = True
    axis: str = "left"              # "left" or "right" Y axis
    show_symbols: bool = True
    symbol: str = "o"
    symbol_size: float = 0.0        # area in pt²; 0: the graph's dot size (automatic)
    symbol_fill: str = ""
    symbol_border: str = "#ffffff"
    symbol_border_width: float = 0.4
    symbol_alpha: float = 0.0       # 0: automatic (0.9, or 0.6 with 200 values or more)
    hollow: bool = False
    show_bar: bool = True
    bar_fill: str = ""
    bar_alpha: float = 0.28
    hatch: str = ""
    bar_border: str = ""
    bar_border_width: float = 1.0
    bar_width: float = 0.6          # of the space per column; the gap between bars is the rest
    box_fill: str = ""
    box_alpha: float = 0.25
    box_line: str = ""
    box_line_width: float = 1.0
    box_width: float = 0.0          # 0: automatic (0.5 for a box, 0.75 for a violin)
    show_center: bool = True
    center_color: str = "#111111"
    center_width: float = 1.6
    center_length: float = 0.5      # of the space per column
    show_error: bool = True
    error_direction: str = "both"   # "both" or "above"
    error_color: str = "#111111"
    error_width: float = 1.0
    error_cap: float = 0.2          # cap width, of the space per column; 0: no caps


@dataclass
class AxisFormat:
    visible: bool = True
    title: str | None = None        # Y: None is the data table's title; X and right: None is no title
    title_style: TextStyle = field(default_factory=TextStyle)
    min: float | None = None        # None: automatic
    max: float | None = None
    reverse: bool = False
    scale: str = "linear"           # a key of SCALES
    major: float = 0.0              # interval between major ticks; 0: automatic
    minor: int = 0                  # minor ticks between two major ticks
    tick_direction: str = "out"
    tick_length: float = 3.5        # points
    numbers: TextStyle = field(default_factory=TextStyle)
    decimals: int = -1              # -1: automatic
    thousands: bool = False
    notation: str = "decimal"       # a key of NOTATIONS
    percent: bool = False           # write a % sign after each number (the values are not multiplied)
    prefix: str = ""
    suffix: str = ""
    breaks: list = field(default_factory=list)    # Y only: up to two gaps [from, to], so 2 or 3 segments
    segments: list = field(default_factory=list)  # relative heights of the segments, bottom first; empty: by their span
    label_angle: float = 0.0        # X (categories) only
    label_wrap: int = 0             # characters per line; 0: no wrapping
    hide_labels: bool = False


@dataclass
class LegendFormat:
    show: bool = False
    position: str = "best"          # a key of LEGEND_POSITIONS
    frame: bool = False
    order: list = field(default_factory=list)   # data set names in legend order; others follow in column order
    xy: list | None = None          # where it was dragged: its top-left corner, as a fraction of the plot
    text: TextStyle = field(default_factory=TextStyle)


@dataclass
class Annotation:
    """A free text box, line, arrow or bracket. Positions are fractions of the plot area (0 to 1, may go outside)."""

    kind: str = "text"
    text: str = ""
    x: float = 0.5
    y: float = 0.9
    x2: float = 0.7
    y2: float = 0.7
    style: TextStyle = field(default_factory=TextStyle)
    color: str = "#111111"
    line_width: float = 1.0
    box: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])


@dataclass
class GraphFormat:
    version: int = 1
    # The graph
    error: str = "sd"
    width: float = 85.0
    height: float = 75.0
    units: str = "mm"
    font_family: str = ""           # "": matplotlib's sans-serif
    font_size: float = 8.0
    dot_size: float = 0.0           # 0: automatic (smaller with many values)
    palette: list = field(default_factory=lambda: list(PALETTE))
    colors: dict = field(default_factory=dict)            # column name -> "#rrggbb"
    show_outliers: bool = True
    show_n: bool = True
    # Data sets
    dataset_default: DataSetFormat = field(default_factory=DataSetFormat)
    datasets: dict = field(default_factory=dict)          # column name -> DataSetFormat (only those changed)
    pair_lines: bool = True         # before-after: the line joining each row
    pair_color: str = "#8A8F95"
    pair_width: float = 0.8
    connect_means: bool = False     # a line joining the column means (medians)
    connect_color: str = "#111111"
    connect_width: float = 1.0
    connect_style: str = "-"
    # Axes
    frame: str = "plain"
    frame_offset: float = 6.0       # points, for the offset frame
    axis_color: str = "#222222"
    axis_width: float = 0.8
    x: AxisFormat = field(default_factory=lambda: AxisFormat(title=""))
    y: AxisFormat = field(default_factory=AxisFormat)
    right_axis: bool = False
    right: AxisFormat = field(default_factory=lambda: AxisFormat(title=""))
    # Text, legend and annotations
    title: str = ""
    title_style: TextStyle = field(default_factory=TextStyle)
    title_loc: str = "left"
    legend: LegendFormat = field(default_factory=LegendFormat)
    annotations: list = field(default_factory=list)
    # Significance
    bracket: str = "stars"          # "stars", "p" or "none"
    bracket_mode: str = "brackets"  # multiple comparisons: one bracket per comparison, or "columns" (stars above the compared columns)
    hide_ns: bool = False
    letters: bool = False           # compact letter display above the columns instead of brackets
    letter_style: str = "upper"
    ref_colors: list = field(default_factory=lambda: ["#000000", "#D62728"])
    bracket_width: float = 0.9      # line width
    bracket_gap: float = 0.06       # between the data and the bracket, as a fraction of the axis
    bracket_tip: float = 0.03       # length of the bracket's ends, as a fraction of the axis
    star_size: float = 0.0          # 0: the graph's font size

    # --- The data sets ---------------------------------------------------------------------------------------

    def dataset(self, name: str) -> DataSetFormat:
        """The format of this data set: its own, or a copy of the default."""

        own = self.datasets.get(name)
        return own if own is not None else copy.deepcopy(self.dataset_default)

    def set_dataset(self, name: str, value: DataSetFormat) -> None:
        self.datasets[name] = value

    def apply_to_all(self, name: str) -> None:
        """Every data set takes this one's format (Prism's "Apply to all data sets"); their colours stay their own."""

        chosen = self.dataset(name)
        self.dataset_default = copy.deepcopy(chosen)
        self.datasets = {}

    def color_of(self, name: str, index: int) -> str:
        palette = self.palette or PALETTE
        return self.colors.get(name) or palette[index % len(palette)]

    # --- Serialization ---------------------------------------------------------------------------------------

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data) -> GraphFormat:
        """From ``to_dict`` output, the flat dict of the first graph format, or a mix of both. Unknown keys are ignored."""

        if isinstance(data, GraphFormat):
            return copy.deepcopy(data)
        fmt = cls()
        for key, value in dict(data or {}).items():
            if key in ALIASES or key == "log_y":
                fmt[key] = value
            elif key in _FIELDS:
                setattr(fmt, key, _load(_FIELDS[key], value, getattr(fmt, key)))
        return fmt

    def copy(self) -> GraphFormat:
        return copy.deepcopy(self)

    # --- The flat keys of the first format, so fmt["y_min"] and fmt.update(...) still work ---------------------

    def __getitem__(self, key: str):
        if key == "log_y":
            return self.y.scale != "linear"
        if key in ALIASES:
            return getattr(getattr(self, ALIASES[key][0]), ALIASES[key][1])
        if key in _FIELDS:
            return getattr(self, key)
        raise KeyError(key)

    def __setitem__(self, key: str, value) -> None:
        if key == "log_y":
            if bool(value) != (self.y.scale != "linear"):
                self.y.scale = "log10" if value else "linear"
        elif key in ALIASES:
            setattr(getattr(self, ALIASES[key][0]), ALIASES[key][1], value)
        elif key in _FIELDS:
            setattr(self, key, _load(_FIELDS[key], value, getattr(self, key)))
        else:
            raise KeyError(key)

    def __contains__(self, key) -> bool:
        return key in LEGACY_KEYS or key in _FIELDS

    def get(self, key: str, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def setdefault(self, key: str, default=None):
        return self[key]

    def update(self, other=(), **values) -> None:
        for key, value in {**dict(other), **values}.items():
            self[key] = value

    def keys(self):
        return list(LEGACY_KEYS)


# The first graph format's keys that now live inside an axis.
ALIASES = {"x_title": ("x", "title"), "y_title": ("y", "title"), "y_min": ("y", "min"), "y_max": ("y", "max")}
LEGACY_KEYS = ("title", "x_title", "y_title", "y_min", "y_max", "log_y", "error", "colors", "dot_size", "font_size", "width", "height",
               "units", "bracket", "bracket_mode", "hide_ns", "letters", "letter_style", "ref_colors", "show_outliers", "show_n")
_FIELDS = {item.name: item for item in fields(GraphFormat)}
_NESTED = {"TextStyle": TextStyle, "DataSetFormat": DataSetFormat, "AxisFormat": AxisFormat, "LegendFormat": LegendFormat, "Annotation": Annotation}


def _load(spec, value, current):
    """``value`` (from JSON) as the type of the field ``spec``, whose current value is ``current``."""

    if is_dataclass(current):
        return _dataclass_from(type(current), value)
    if spec.name == "datasets":
        return {str(name): _dataclass_from(DataSetFormat, item) for name, item in dict(value or {}).items()}
    if spec.name == "annotations":
        return [_dataclass_from(Annotation, item) for item in value or []]
    if spec.name in ("palette", "ref_colors"):
        return [str(item) for item in _as_list(value)]
    if spec.name == "colors":
        return {str(name): str(item) for name, item in dict(value or {}).items()}
    return _scalar(value, current)


def _dataclass_from(kind, value):
    if isinstance(value, kind):
        return copy.deepcopy(value)
    made = kind()
    for item in fields(kind):
        if isinstance(value, dict) and item.name in value:
            current = getattr(made, item.name)
            raw = value[item.name]
            if is_dataclass(current):
                setattr(made, item.name, _dataclass_from(type(current), raw))
            elif item.name in ("breaks", "segments", "order"):
                setattr(made, item.name, _as_list(raw))
            elif item.name == "xy":
                setattr(made, item.name, [float(v) for v in raw] if raw else None)
            else:
                setattr(made, item.name, _scalar(raw, current))
    return made


def _as_list(value) -> list:
    """QSettings and hand-edited files can give a comma string for a list."""

    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return list(value)


def _scalar(value, current):
    if isinstance(current, bool):
        return value in (True, 1, "true", "True", "1") if isinstance(value, (str, int)) else bool(value)
    if isinstance(current, int) and not isinstance(current, bool):
        try:
            return int(value)
        except (TypeError, ValueError):
            return current
    if isinstance(current, float):
        try:
            return float(value)
        except (TypeError, ValueError):
            return current
    if current is None and value is not None and not isinstance(value, str):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if value is None:
        return None
    return value if isinstance(value, (str, list, dict)) else str(value)


def as_format(value) -> GraphFormat:
    return value if isinstance(value, GraphFormat) else GraphFormat.from_dict(value)


def size_inches(fmt) -> tuple[float, float]:
    scale = 1.0 / MM_PER_INCH if fmt.get("units", "mm") == "mm" else 1.0
    return max(0.5, float(fmt["width"]) * scale), max(0.5, float(fmt["height"]) * scale)


# --- Text markup --------------------------------------------------------------------------------------------------

GREEK = {name: chr(code) for name, code in (
    ("alpha", 0x3B1), ("beta", 0x3B2), ("gamma", 0x3B3), ("delta", 0x3B4), ("epsilon", 0x3B5), ("zeta", 0x3B6), ("eta", 0x3B7),
    ("theta", 0x3B8), ("iota", 0x3B9), ("kappa", 0x3BA), ("lambda", 0x3BB), ("mu", 0x3BC), ("nu", 0x3BD), ("xi", 0x3BE),
    ("pi", 0x3C0), ("rho", 0x3C1), ("sigma", 0x3C3), ("tau", 0x3C4), ("upsilon", 0x3C5), ("phi", 0x3C6), ("chi", 0x3C7),
    ("psi", 0x3C8), ("omega", 0x3C9), ("Gamma", 0x393), ("Delta", 0x394), ("Theta", 0x398), ("Lambda", 0x39B), ("Xi", 0x39E),
    ("Pi", 0x3A0), ("Sigma", 0x3A3), ("Phi", 0x3A6), ("Psi", 0x3A8), ("Omega", 0x3A9))}
GREEK["micro"] = "µ"
_SUPER = str.maketrans("0123456789+-−=()n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁻⁼⁽⁾ⁿ")
_SUB = str.maketrans("0123456789+-−=()", "₀₁₂₃₄₅₆₇₈₉₊₋₋₌₍₎")
_SCRIPT = re.compile(r"([\^_])\{([^{}]*)\}|([\^_])([0-9A-Za-z+\-−])")


def markup(text: str) -> str:
    """Plain text with simple markup, ready for matplotlib: \\alpha (or \\mu, \\Delta …) is the Greek letter, ^{2} or ^2 is
    superscript and _{2} subscript. Digits and signs become Unicode super/subscripts (they keep the font and stay editable
    text in SVG); anything else uses matplotlib's mathtext. A literal $ is kept as $."""

    if not text:
        return ""
    text = re.sub(r"\\([A-Za-z]+)", lambda m: GREEK.get(m.group(1), m.group(0)), text)
    text = text.replace("$", r"\$")

    def script(match) -> str:
        mark = match.group(1) or match.group(3)
        body = match.group(2) if match.group(1) else match.group(4)
        table = _SUPER if mark == "^" else _SUB
        plain = body.translate(table)
        if all(ord(character) > 127 or character == " " for character in plain) and body:
            return plain
        escaped = body.replace("\\$", "$").replace(" ", r"\ ")
        return f"$\\mathregular{{{mark}{{{escaped}}}}}$"

    return _SCRIPT.sub(script, text)


# --- Numbers on an axis ---------------------------------------------------------------------------------------------


def auto_decimals(values) -> int:
    """Enough decimals to tell the ticks apart."""

    finite = sorted({float(v) for v in values if v is not None and math.isfinite(float(v))})
    if len(finite) < 2:
        return 0 if not finite or float(finite[0]).is_integer() else 2
    step = min(b - a for a, b in zip(finite, finite[1:]) if b > a) if any(b > a for a, b in zip(finite, finite[1:])) else 1.0
    return max(0, min(8, -math.floor(math.log10(step) + 1e-9))) if step < 1 else 0


def number_text(value: float, axis: AxisFormat, decimals: int | None = None, base: float | None = None) -> str:
    """One axis number written as the axis's numbering asks: decimals, thousands separator, scientific or power
    notation, a % sign, prefix and suffix."""

    if value is None or not math.isfinite(value):
        return ""
    places = axis.decimals if axis.decimals >= 0 else (decimals if decimals is not None else 0)
    if axis.notation == "power" and base:
        exponent = math.log(value, base) if value > 0 else float("nan")
        if math.isfinite(exponent) and abs(exponent - round(exponent)) < 1e-6:
            name = {10.0: "10", 2.0: "2"}.get(float(base), "e")
            body = f"{name}{str(int(round(exponent))).translate(_SUPER)}"
        else:
            body = _decimal(value, places, axis.thousands)
    elif axis.notation == "scientific" and value != 0:
        exponent = int(math.floor(math.log10(abs(value))))
        mantissa = value / 10**exponent
        digits = axis.decimals if axis.decimals >= 0 else (1 if abs(mantissa - round(mantissa)) > 1e-9 else 0)
        body = f"{mantissa:.{digits}f}×10{str(exponent).translate(_SUPER)}".replace("-", "−")
    else:
        body = _decimal(value, places, axis.thousands)
    if axis.percent:
        body += "%"
    return f"{axis.prefix}{body}{axis.suffix}"


def _decimal(value: float, places: int, thousands: bool) -> str:
    text = f"{value:,.{places}f}" if thousands else f"{value:.{places}f}"
    if float(text.replace(",", "")) == 0:
        text = text.lstrip("-")
    return text.replace("-", "−")


# --- Styles: Prism Magic ----------------------------------------------------------------------------------------------

# key: (name in the Magic dialog, tooltip, settings copied). "datasets" and "colors" are copied by data set order.
GROUPS = {
    "colors": ("Colours", "The palette and each data set's colour, by order: the first data set takes the first colour.",
               ["palette", "colors"]),
    "datasets": ("Data set appearance", "Symbols, bars, boxes, mean lines, error bars and connecting lines of every data set, by order.",
                 ["dataset_default", "datasets", "dot_size", "pair_lines", "pair_color", "pair_width", "connect_means", "connect_color",
                  "connect_width", "connect_style"]),
    "frame": ("Frame and axis lines", "Plain, offset or boxed axes, and the axis line width and colour.",
              ["frame", "frame_offset", "axis_color", "axis_width"]),
    "ticks": ("Scale, ticks and numbering", "Linear or log scale, tick interval, direction and length, and how numbers are written.",
              [f"{axis}.{name}" for axis in ("x", "y", "right") for name in ("scale", "major", "minor", "tick_direction", "tick_length",
               "decimals", "thousands", "notation", "percent", "prefix", "suffix", "label_angle", "label_wrap", "hide_labels", "visible")]
              + ["right_axis"]),
    "range": ("Axis ranges and breaks", "The Y range, reversal and axis breaks. Copy only between graphs of similar values.",
              [f"{axis}.{name}" for axis in ("y", "right") for name in ("min", "max", "reverse", "breaks", "segments")]),
    "fonts": ("Fonts", "Font, size, bold, italic and colour of every text.",
              ["font_family", "font_size", "title_style", "title_loc", "x.title_style", "y.title_style", "right.title_style",
               "x.numbers", "y.numbers", "right.numbers", "legend.text"]),
    "titles": ("Title and axis titles (text)", "The words of the graph title and the axis titles.", ["title", "x.title", "y.title", "right.title"]),
    "legend": ("Legend", "Whether there is a legend, where, its frame and order.", ["legend.show", "legend.position", "legend.frame",
                                                                                   "legend.xy", "legend.order"]),
    "significance": ("Significance marks", "Bracket and star format: P value or stars, line width, gap, star size and reference colours.",
                     ["bracket", "bracket_mode", "hide_ns", "letters", "letter_style", "ref_colors", "bracket_width", "bracket_gap",
                      "bracket_tip", "star_size"]),
    "size": ("Graph size", "Width and height of the graph.", ["width", "height", "units"]),
    "annotations": ("Text boxes and arrows", "The free text boxes, lines, arrows and brackets, at the same place.", ["annotations"]),
    "error": ("Error bar type", "SD, SEM, CI or median with interquartile range.", ["error"]),
}
DEFAULT_GROUPS = ("colors", "datasets", "frame", "ticks", "fonts", "legend", "significance")


def _get(target, path: str):
    for part in path.split("."):
        target = getattr(target, part)
    return target


def _set(target, path: str, value) -> None:
    *parents, last = path.split(".")
    for part in parents:
        target = getattr(target, part)
    current = getattr(target, last)
    if is_dataclass(current):
        value = _dataclass_from(type(current), value)
    elif last == "annotations":
        value = [_dataclass_from(Annotation, item) for item in value or []]
        for item in value:
            item.id = Annotation().id  # copies are new annotations
    elif last in ("breaks", "segments", "order", "palette", "ref_colors"):
        value = _as_list(value)
    elif last == "xy":
        value = [float(v) for v in value] if value else None
    else:
        value = _scalar(value, current)
    setattr(target, last, value)


def make_style(fmt: GraphFormat, names, groups=None) -> dict:
    """The settings of ``groups`` (all by default) as a JSON-ready dict. Per-column settings become lists by column order."""

    names = list(names)
    values: dict = {}
    for group in groups or GROUPS:
        for path in GROUPS[group][2]:
            if path == "colors":
                values["colors_by_order"] = [fmt.colors.get(name, "") for name in names]
            elif path == "datasets":
                values["datasets_by_order"] = [asdict(fmt.datasets[name]) if name in fmt.datasets else None for name in names]
            else:
                value = _get(fmt, path)
                values[path] = asdict(value) if is_dataclass(value) else [asdict(item) for item in value] if path == "annotations" \
                    else copy.deepcopy(value)
    return {"version": 1, "groups": sorted(groups or GROUPS), "values": values}


def apply_style(fmt: GraphFormat, style: dict, names, groups=None) -> None:
    """Copy the chosen groups of ``style`` (from ``make_style``) onto ``fmt``; a group the style does not hold is left alone."""

    names = list(names)
    values = dict(style.get("values") or {})
    held = set(_as_list(style.get("groups")) or GROUPS)
    for group in groups or GROUPS:
        if group not in held:
            continue
        for path in GROUPS[group][2]:
            if path == "colors":
                listed = _as_list(values.get("colors_by_order"))
                fmt.colors = {name: listed[index % len(listed)] for index, name in enumerate(names) if listed and listed[index % len(listed)]}
            elif path == "datasets":
                listed = values.get("datasets_by_order") or []
                fmt.datasets = {name: _dataclass_from(DataSetFormat, listed[index % len(listed)])
                                for index, name in enumerate(names) if listed and listed[index % len(listed)]}
            elif path in values:
                _set(fmt, path, copy.deepcopy(values[path]))


def _builtin_styles() -> dict[str, dict]:
    editorial = GraphFormat()
    styles = {"Editorial (default)": make_style(editorial, [], DEFAULT_GROUPS)}
    classic = GraphFormat(frame="offset", axis_color="#000000", axis_width=1.0, palette=["#000000"], font_family="Arial",
                          pair_color="#000000", pair_width=1.0, connect_color="#000000")
    classic.dataset_default = DataSetFormat(symbol_border="#000000", symbol_border_width=0.6, symbol_alpha=1.0, bar_fill="#ffffff", bar_alpha=1.0,
                                            bar_border="#000000", bar_border_width=1.0, box_fill="#ffffff", box_alpha=1.0, box_line="#000000",
                                            center_color="#000000", center_width=1.4, error_color="#000000", error_width=1.0)
    bold = TextStyle(bold=True, color="#000000")
    for axis in (classic.x, classic.y, classic.right):
        axis.title_style = copy.deepcopy(bold)
        axis.numbers = copy.deepcopy(bold)
        axis.tick_direction, axis.tick_length = "out", 4.0
    classic.title_style = copy.deepcopy(bold)
    classic.legend.text = copy.deepcopy(bold)
    classic.ref_colors = ["#000000", "#000000"]
    classic.bracket_width = 1.0
    styles["Classic Prism"] = make_style(classic, [], DEFAULT_GROUPS)
    safe = GraphFormat(palette=list(COLORBLIND_PALETTE))
    safe.ref_colors = ["#000000", "#D55E00"]
    styles["Colour-blind safe"] = make_style(safe, [], DEFAULT_GROUPS)
    return styles


BUILTIN_STYLES = _builtin_styles()
BUILTIN_TIPS = {
    "Editorial (default)": "CellQuant's look: plain axes, light bars and boxes, coloured dots with a white edge.",
    "Classic Prism": "Prism's classic look: offset X and Y axes, bold text, black and white.",
    "Colour-blind safe": "Okabe and Ito's palette, which stays distinct with every common colour-vision deficiency.",
}


def styles_path() -> Path:
    """Saved styles, beside CellQuant's per-computer settings (%LOCALAPPDATA%\\CellQuant\\graph_styles.json)."""

    from cellquant.hardware import settings_path

    return settings_path().parent / "graph_styles.json"


def load_styles() -> dict[str, dict]:
    """The user's saved styles by name; {} when there are none or the file cannot be read."""

    path = styles_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        return {}
    styles = data.get("styles") if isinstance(data, dict) else None
    return {str(name): style for name, style in (styles or {}).items() if isinstance(style, dict)}


def _write_styles(styles: dict[str, dict]) -> None:
    path = styles_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps({"version": 1, "styles": styles}, indent=1), encoding="utf-8")
    temporary.replace(path)


def save_style(name: str, style: dict) -> None:
    styles = load_styles()
    styles[name] = style
    _write_styles(styles)


def delete_style(name: str) -> None:
    styles = load_styles()
    if styles.pop(name, None) is not None:
        _write_styles(styles)


def all_styles() -> dict[str, dict]:
    """Built-in styles first, then the user's (a saved style with a built-in name is listed as the user's)."""

    return {**BUILTIN_STYLES, **load_styles()}


CLIPBOARD_PREFIX = "cellquant-graph-format:"


def to_clipboard(fmt: GraphFormat, names) -> str:
    return CLIPBOARD_PREFIX + json.dumps(make_style(fmt, names))


def from_clipboard(text: str) -> dict | None:
    if not text or not text.startswith(CLIPBOARD_PREFIX):
        return None
    try:
        style = json.loads(text[len(CLIPBOARD_PREFIX):])
    except ValueError:
        return None
    return style if isinstance(style, dict) else None
