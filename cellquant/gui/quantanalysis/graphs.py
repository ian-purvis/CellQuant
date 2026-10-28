"""Graphs of a column table, drawn with matplotlib (imported on first use).

``draw`` fills a matplotlib Figure from a data sheet, a graph sheet and, when the graph is linked to
a comparison, that result (for the significance bracket and the estimation plot). Everything about
the look comes from the sheet's ``GraphFormat`` (graph_format.py), so the same sheet always draws
the same figure, on screen and exported. It returns notes for the graph sheet (a column left out, a
value that a log axis cannot show), never raises for odd data. The default look is plain and
editorial: white background, no top or right axis line, labels on the columns, the n under each one.

Every artist that can be clicked carries ``_cq_target``, a tuple naming what it belongs to:
("dataset", column name), ("axis", "x" | "y" | "right"), ("title",), ("legend",), ("annotation", id),
("bracket",) or ("connect",). The graph view selects by that tag, so a selection survives a redraw.
"""

from __future__ import annotations

import math
import textwrap
from functools import lru_cache

import numpy as np

from cellquant.errors import StatisticsError
from cellquant.gui.quantanalysis.graph_format import (
    LEGACY_KEYS,
    MM_PER_INCH,  # noqa: F401 - used by the graph view
    OPEN_SYMBOLS,
    PALETTE,  # noqa: F401
    AxisFormat,
    GraphFormat,
    as_format,
    auto_decimals,
    markup,
    number_text,
    size_inches,
)
from cellquant.stats import compact_letter_display, format_p, p_summary, p_text
from cellquant.stats.descriptive import describe, percentiles

# key: (menu name, tooltip)
KINDS = {
    "scatter": ("Column scatter", "Every value as a dot, with a line at the mean (or median) and error bars. Shows the data, so it is the best default."),
    "bar": ("Bar and dots", "A bar at the mean with error bars and the dots on top. Familiar, but the bar hides how the values spread; keep the dots on."),
    "box": ("Box and dots", "Box from the 25th to the 75th percentile, a line at the median, whiskers from min to max, and the dots."),
    "violin": ("Violin and dots", "The shape of each column's distribution, with the median and quartiles. Useful with many values (n > 20)."),
    "before_after": ("Before-after (paired)", "One line per row, joining a subject's values across columns. For paired data: the same replicate in each condition."),
    "estimation": ("Estimation plot", "Two columns and, on a right axis, their difference with its confidence interval (from a t test). Shows how big the effect is, not only whether P is small."),
    "xy": ("XY", "Each data set of an XY table against X: points (or means with error bars) and, with a linear regression as its "
           "comparison, the fitted line and its confidence or prediction band."),
    "superplot": ("SuperPlot", "Images shaded by biological replicate (small dots), each replicate's value as a large outlined marker, and the mean ± SD of the replicates. Tests use the replicates (N), not the images (n)."),
}

ERRORS = {
    "sd": ("Mean ± SD", "SD shows how much the values vary. Use it to show the spread of the data."),
    "sem": ("Mean ± SEM", "SEM = SD/√n shows how precisely the mean is known; it shrinks as n grows. It is not the spread of the data."),
    "ci": ("Mean with 95% CI", "The range that contains the true mean in 95% of experiments like this one. Shows precision, like SEM, on a scale that is easier to read."),
    "median_iqr": ("Median with interquartile range", "Median and the 25th to 75th percentiles. For skewed data, or with a rank test."),
    "none": ("No error bars", "Only the dots and the mean line."),
}

# XY graphs. key: (menu name, tooltip)
XY_REPLICATES = {"mean": ("Mean and error bar", "One point per X: the mean of its replicates with the error bar chosen above."),
                 "each": ("Every replicate", "Every replicate as its own point.")}
XY_BANDS = {"none": "No band", "confidence": "Confidence band", "prediction": "Prediction band"}

# Contingency tables. key: (menu name, tooltip)
CONTINGENCY_VALUES = {
    "stacked": ("Stacked, % of each row", "One bar per row (group), split by outcome; each bar adds up to 100%."),
    "percent": ("Side by side, % of each row", "The outcomes of each row side by side, as a percentage of the row's total."),
    "count": ("Side by side, counts", "The outcomes of each row side by side, as numbers of subjects."),
}

# Grouped tables. key: (menu name, tooltip)
GROUPED_LAYOUTS = {
    "interleaved": ("Interleaved", "Each row is a block with its data sets side by side, coloured by data set. Compares the data sets within each row."),
    "separated": ("Separated", "One block per data set with its rows side by side, labelled under the axis. Compares the rows within each data set."),
}
GROUPED_KINDS = ("scatter", "bar", "box", "violin")

# The first graph format's flat keys and defaults (GraphFormat reads and writes them).
DEFAULT_FORMAT = {key: GraphFormat()[key] for key in LEGACY_KEYS}
LOG_BASES = {"linear": None, "log10": 10.0, "log2": 2.0, "ln": math.e}


def color_of(fmt, name: str, index: int) -> str:
    return as_format(fmt).color_of(name, index)


def series_names(data) -> list[str]:
    """The names a graph's data set formats and colours are keyed by: a grouped or XY table's data sets, else its columns."""

    grouped, xy, counts = getattr(data, "grouped", None), getattr(data, "xy", None), getattr(data, "contingency", None)
    if counts is not None:
        return list(counts.columns)
    return list(grouped.columns) if grouped is not None else list(xy.sets) if xy is not None else list(data.table.names)


def grouped_places(grouped, fmt) -> tuple[list[tuple[float, str, int]], list[tuple[str, float]]]:
    """Where each cell of a grouped table goes, in cells_table order (row by row): (x, data set, data set number);
    and each block: (label, centre x). Empty cells keep their place, so the blocks line up."""

    r, c = len(grouped.rows), len(grouped.columns)
    gap = min(5.0, max(0.0, float(fmt.cluster_gap)))
    separated = fmt.grouped_layout == "separated"
    places = [((j * (r + gap) + i) if separated else (i * (c + gap) + j), grouped.columns[j], j) for i in range(r) for j in range(c)]
    if separated:
        blocks = [(name, j * (r + gap) + (r - 1) / 2) for j, name in enumerate(grouped.columns)]
    else:
        blocks = [(name, i * (c + gap) + (c - 1) / 2) for i, name in enumerate(grouped.rows)]
    return places, blocks


def _jitter(values: np.ndarray, width: float) -> np.ndarray:
    """Horizontal offsets that spread dots where values are dense (a simple, repeatable beeswarm)."""

    n = values.size
    if n < 2:
        return np.zeros(n)
    span = np.ptp(values) or 1.0
    bins = max(4, min(40, int(np.sqrt(n) * 2)))
    edges = np.linspace(values.min(), values.max() + span * 1e-9, bins + 1)
    which = np.clip(np.digitize(values, edges) - 1, 0, bins - 1)
    counts = np.bincount(which, minlength=bins)
    offsets = np.zeros(n)
    for b in range(bins):
        members = np.flatnonzero(which == b)
        if members.size < 2:
            continue
        spread = width * min(1.0, members.size / max(counts.max(), 1)) ** 0.5
        order = members[np.argsort(values[members])]
        slots = np.linspace(-spread, spread, members.size)
        # From the middle outwards, so neighbours in value sit on alternate sides.
        arranged = slots[np.argsort(np.abs(slots), kind="stable")]
        offsets[order] = arranged
    return offsets


def _dot_area(fmt: GraphFormat, n: int, largest: int = 0) -> float:
    if fmt.dot_size:
        return float(fmt.dot_size)
    n = max(n, largest)
    return float(np.clip(28.0 * math.sqrt(30.0 / max(n, 1)), 3.0, 28.0))


def _center_and_bars(values: np.ndarray, error: str) -> tuple[float, float, float]:
    """(center, low, high) of the error bar for these values."""

    if values.size == 0:
        return (np.nan, np.nan, np.nan)
    if error == "median_iqr":
        p25, p50, p75 = percentiles(values, [25, 50, 75])
        return float(p50), float(p25), float(p75)
    d = describe(values)
    mean = d["mean"]
    if error == "sd":
        return mean, mean - d["sd"], mean + d["sd"]
    if error == "sem":
        return mean, mean - d["sem"], mean + d["sem"]
    if error == "ci":
        return mean, d["ci_low"], d["ci_high"]
    return mean, mean, mean


# --- Fonts and tags ---------------------------------------------------------------------------------------------


@lru_cache(maxsize=64)
def font_available(name: str) -> bool:
    if not name or name in ("sans-serif", "serif", "monospace"):
        return True
    from matplotlib import font_manager

    try:
        font_manager.findfont(font_manager.FontProperties(family=name), fallback_to_default=False)
    except ValueError:
        return False
    return True


def _family(fmt: GraphFormat, own: str = "") -> str:
    name = own or fmt.font_family
    return name if name and font_available(name) else "sans-serif"


def _text_kw(style, fmt: GraphFormat, size: float, color: str = "#111111") -> dict:
    return {"fontsize": style.size or size, "fontweight": "bold" if style.bold else "normal", "fontstyle": "italic" if style.italic else "normal",
            "color": style.color or color, "fontfamily": _family(fmt, style.family)}


def _tag(artist, *target, radius: float = 4.0):
    """Mark ``artist`` as part of ``target`` and make it clickable."""

    artist._cq_target = tuple(target)
    artist.set_picker(True)
    if hasattr(artist, "set_pickradius"):
        try:
            artist.set_pickradius(radius)
        except (TypeError, ValueError):
            pass
    return artist


def _rgba(color: str, alpha: float):
    from matplotlib.colors import to_rgba

    return to_rgba(color, max(0.0, min(1.0, alpha)))


# --- Axes ----------------------------------------------------------------------------------------------------------


def _style_frame(ax, fmt: GraphFormat, segment: str = "only") -> None:
    """Axis lines for the frame: "plain", "offset", "box" or "none". ``segment`` is "only", "top", "middle" or "bottom"
    for a Y axis split by breaks: the lines between segments are left out."""

    frame = fmt.frame
    for side, spine in ax.spines.items():
        spine.set_linewidth(fmt.axis_width)
        spine.set_color(fmt.axis_color)
        shown = frame != "none" and (side in ("left", "bottom") or frame == "box")
        if side == "bottom" and segment in ("top", "middle"):
            shown = False
        if side == "top" and segment in ("bottom", "middle"):
            shown = False
        spine.set_visible(shown)
        if frame == "offset" and side in ("left", "bottom"):
            spine.set_position(("outward", fmt.frame_offset))
        _tag(spine, "axis", {"left": "y", "bottom": "x", "right": "y", "top": "x"}[side], radius=5)
    ax.set_facecolor("white")


def _apply_scale(ax, axis: AxisFormat) -> None:
    base = LOG_BASES.get(axis.scale)
    if base:
        ax.set_yscale("log", base=base)


def _locators(ax_axis, axis: AxisFormat, notes: list[str]) -> None:
    from matplotlib import ticker

    base = LOG_BASES.get(axis.scale)
    low, high = sorted(ax_axis.get_view_interval())
    if base:
        # On a log axis the interval counts powers of the base (2: every 100-fold on log 10); a linear interval left over is ignored.
        step = base ** int(axis.major) if 1 <= axis.major <= 6 and float(axis.major).is_integer() else base
        ax_axis.set_major_locator(ticker.LogLocator(base=step))
        ax_axis.set_minor_locator(ticker.LogLocator(base=base, subs="auto") if axis.minor > 0 else ticker.NullLocator())
    else:
        if axis.major > 0:
            if (high - low) / axis.major > 100:
                notes.append(f"A tick every {axis.major:g} would draw more than 100 ticks; the interval is automatic.")
                ax_axis.set_major_locator(ticker.AutoLocator())
            else:
                ax_axis.set_major_locator(ticker.MultipleLocator(axis.major))
        ax_axis.set_minor_locator(ticker.AutoMinorLocator(axis.minor + 1) if axis.minor > 0 else ticker.NullLocator())
    ax_axis.set_major_formatter(number_formatter(axis, base))


def number_formatter(axis: AxisFormat, base=None):
    """A matplotlib formatter that writes axis numbers as ``axis`` says (decimals, thousands, scientific, power, %, prefix, suffix)."""

    from matplotlib import ticker

    class Numbers(ticker.Formatter):
        def __init__(self):
            self.decimals = 0

        def __call__(self, value, pos=None):
            decimals = self.decimals
            if base and axis.decimals < 0 and value > 0:
                decimals = max(0, min(8, -math.floor(math.log10(value) + 1e-9)))
            numbering = axis
            if axis.notation == "decimal" and axis.decimals < 0 and value and not base and (abs(value) >= 1e6 or abs(value) < 1e-4):
                numbering = AxisFormat(**{**axis.__dict__, "notation": "scientific"})  # automatic: very large or small numbers
            return number_text(float(value), numbering, decimals, base)

        def format_ticks(self, values):
            self.decimals = auto_decimals(values)
            return [self(value, index) for index, value in enumerate(values)]

    return Numbers()


def _style_ticks(ax, which: str, axis: AxisFormat, fmt: GraphFormat) -> None:
    numbers = axis.numbers
    length = 0.0 if axis.tick_direction == "none" else axis.tick_length
    direction = "out" if axis.tick_direction == "none" else axis.tick_direction
    ax.tick_params(axis=which, which="major", direction=direction, length=length, width=fmt.axis_width, color=fmt.axis_color,
                   labelsize=numbers.size or fmt.font_size, labelcolor=numbers.color or "#222222", labelfontfamily=_family(fmt, numbers.family))
    ax.tick_params(axis=which, which="minor", direction=direction, length=length * 0.55, width=fmt.axis_width * 0.8, color=fmt.axis_color)
    if not axis.visible:
        ax.tick_params(axis=which, which="both", length=0, labelleft=False, labelbottom=False, labelright=False)
        side = "bottom" if which == "x" else "right" if ax.get_label() == "right" else "left"
        ax.spines[side].set_visible(False)
    target = ax.yaxis if which == "y" else ax.xaxis
    for tick in target.get_major_ticks():  # later ticks copy the first one's font
        for label in (tick.label1, tick.label2):
            label.set_fontweight("bold" if numbers.bold else "normal")
            label.set_fontstyle("italic" if numbers.italic else "normal")


def _limits(ax, axis: AxisFormat, log: bool) -> tuple[float, float]:
    low, high = axis.min, axis.max
    bottom, top = ax.get_ylim()
    if low is not None or high is not None:
        bottom, top = (low if low is not None else bottom, high if high is not None else top)
        if log and bottom <= 0:
            bottom = ax.get_ylim()[0]
    elif not log and bottom > 0 and bottom < 0.35 * top:
        bottom = 0.0  # start at zero when the data are near it
    ax.set_ylim(bottom, top)
    return bottom, top


def _valid_breaks(axis: AxisFormat, kind: str, log: bool, notes: list[str]) -> list[tuple[float, float]]:
    gaps = []
    for item in axis.breaks[:2]:
        try:
            low, high = sorted((float(item[0]), float(item[1])))
        except (TypeError, ValueError, IndexError):
            continue
        if high > low:
            gaps.append((low, high))
    if not gaps:
        return []
    if log or kind == "estimation":
        notes.append("Axis breaks are drawn on a linear Y axis of a column graph only.")
        return []
    if axis.reverse:
        notes.append("A reversed Y axis is drawn without its breaks.")
        return []
    gaps.sort()
    if len(gaps) == 2 and gaps[1][0] <= gaps[0][1]:
        notes.append("The two axis breaks overlap; only the first is drawn.")
        gaps = gaps[:1]
    return gaps


def plot_transform(figure, axes):
    """The transform from fractions of the plot area (the union of the data axes) to the screen."""

    from matplotlib.transforms import Bbox, BboxTransformTo, TransformedBbox

    union = Bbox.union([ax.get_position() for ax in axes])
    return BboxTransformTo(TransformedBbox(union, figure.transFigure))


# --- Drawing ---------------------------------------------------------------------------------------------------


def draw(figure, data, graph, result=None, outliers=None) -> list[str]:
    """Draw ``graph`` (a GraphSheet) of ``data`` (a DataSheet) into ``figure``. Returns notes.

    ``outliers`` maps a column name to a mask of its flagged cells (``Project.flags``); those values are drawn hollow."""

    from matplotlib import ticker

    fmt = as_format(graph.format)
    figure.clear()
    figure.set_facecolor("white")
    figure.set_size_inches(*size_inches(fmt), forward=False)
    font = float(fmt.font_size)
    notes: list[str] = []
    figure._cq_legend = None
    if getattr(data, "contingency", None) is not None:
        if graph.kind != "bar":
            notes.append(f"A contingency table is drawn as bars; {KINDS[graph.kind][0].lower()} is for column tables.")
        return _draw_contingency(figure, data, result, fmt, font, notes)
    if getattr(data, "xy", None) is not None:
        if graph.kind != "xy":
            notes.append(f"An XY table is drawn as an XY graph; {KINDS[graph.kind][0].lower()} is for column tables.")
        return _draw_xy(figure, data, result, fmt, font, notes)
    table = data.table
    columns = [column for column in table.columns if column.finite.size]
    if len(columns) < len(table.columns):
        notes.append("Empty cells are left as gaps." if getattr(data, "grouped", None) is not None else "Empty columns are not drawn.")
    figure._cq_legend = None
    if not columns:
        ax = figure.add_subplot(111)
        _style_frame(ax, fmt)
        ax.text(0.5, 0.5, "No values to draw", ha="center", va="center", transform=ax.transAxes, fontsize=font, color="#888")
        figure._cq_axes = {"main": ax, "segments": [ax], "bottom": ax, "right": None}
        figure._cq_plot = plot_transform(figure, [ax])
        return notes
    y_axis = fmt.y
    log = y_axis.scale != "linear"
    if log and any(np.any(column.finite <= 0) for column in columns):
        notes.append("Values ≤ 0 cannot be shown on a log axis and are left out of the graph.")
    kind = graph.kind
    if kind == "xy":
        notes.append("An XY graph needs an XY table (File → New XY table); this table is drawn as a column scatter.")
        kind = "scatter"
    compared = _compared(result, table)
    grouped = getattr(data, "grouped", None)
    if grouped is not None and kind not in GROUPED_KINDS:
        notes.append(f"A grouped table is drawn as a column scatter, bars, boxes or violins; {KINDS[kind][0].lower()} is for column tables.")
        kind = "scatter"
    if kind == "estimation":
        if compared is None:
            notes.append("An estimation plot needs a comparison of two columns linked to this graph (Analyze → Compare two columns…, a t test).")
            kind = "scatter"
        elif result.analysis and compared[2] not in ESTIMATION:
            notes.append("The estimation plot shows a difference of means, from an unpaired, Welch or paired t test. This graph shows the columns only.")
            kind = "scatter"
    if kind == "before_after" and not table.paired:
        notes.append("Before-after joins rows of a paired table. Tick Paired on the data table if row i is the same subject in every column.")
    if kind in ("superplot", "estimation") and any(np.any(mask) for mask in (outliers or {}).values()):
        notes.append("Outliers are marked on column graphs (hollow points), not on a SuperPlot or an estimation plot.")
    if kind == "superplot" and (data.images is None or data.images.empty or not (data.images["unit"].astype(str) != "").any()):
        notes.append("A SuperPlot needs a table imported from CellQuant results with one dot per biological replicate (Import CellQuant results, Dot = biological replicate). Drawn as a column scatter.")
        kind = "scatter"

    gaps = _valid_breaks(y_axis, kind, log, notes)
    on_right = [column.name for column in columns if fmt.dataset(column.name).axis == "right" and fmt.dataset(column.name).visible]
    use_right = (fmt.right_axis or bool(on_right)) and kind not in ("estimation", "superplot")
    if use_right and gaps:
        notes.append("A right Y axis is not drawn with axis breaks.")
        use_right = False
    if not use_right:
        on_right = []
    # With breaks, one axes per segment, top first, sharing X; the data are drawn in each and each shows its part.
    grid = figure.add_gridspec(len(gaps) + 1, 1)
    segments = []
    for row in range(len(gaps) + 1):
        segments.append(figure.add_subplot(grid[row, 0], sharex=segments[0] if segments else None))
    ax, bottom = segments[0], segments[-1]
    roles = ["only"] if len(segments) == 1 else ["top"] + ["middle"] * (len(segments) - 2) + ["bottom"]
    for segment, role in zip(segments, roles):
        _style_frame(segment, fmt, role)
    largest = max(column.finite.size for column in columns)
    positions = {column.name: index for index, column in enumerate(columns)}
    names = [column.name for column in columns]
    places = blocks = None
    if grouped is not None:
        every, blocks = grouped_places(grouped, fmt)
        by_name = dict(zip(table.names, every))
        places = [by_name[column.name] for column in columns]
        positions = {column.name: place[0] for column, place in zip(columns, places)}
    right = None
    if use_right:
        right = ax.twinx()
        right.set_label("right")
        for side, spine in right.spines.items():
            spine.set_visible(side == "right" and fmt.frame != "none")
            spine.set_linewidth(fmt.axis_width)
            spine.set_color(fmt.axis_color)
            _tag(spine, "axis", "right", radius=5)
        if fmt.frame == "offset":
            right.spines["right"].set_position(("outward", fmt.frame_offset))
        _apply_scale(right, fmt.right if on_right else y_axis)
    for segment in segments:
        _apply_scale(segment, y_axis)
        if kind == "estimation":
            names, positions = _draw_estimation(segment, figure, columns, compared, result, fmt, font)
        elif kind == "superplot":
            _draw_superplot(segment, data, columns, fmt)
        else:
            _draw_columns(segment, right if segment is ax else None, kind, table, columns, fmt, outliers, log, largest, places)
    if grouped is not None:
        _grouped_axis(bottom, grouped, fmt, font)
    else:
        bottom.set_xticks([0, 1, 2.2] if kind == "estimation" else range(len(names)))
    if fmt.show_n and kind != "estimation":
        labels = [f"{_wrap(name, fmt.x.label_wrap)}\n{_n_text(data, column)}" for name, column in zip(names, columns)]
    else:
        labels = [_wrap(name, fmt.x.label_wrap) for name in names]
    angle = float(fmt.x.label_angle)
    if grouped is None:
        bottom.set_xticklabels([] if fmt.x.hide_labels else [markup(label) for label in labels], rotation=angle,
                               ha="right" if angle > 0 else "left" if angle < 0 else "center", rotation_mode="anchor" if angle else "default",
                               **_text_kw(fmt.x.numbers, fmt, font, "#222222"))
    for segment in segments[:-1]:
        segment.tick_params(axis="x", which="both", bottom=False, labelbottom=False)
    if grouped is not None:
        xs = [place[0] for place in grouped_places(grouped, fmt)[0]]
        bottom.set_xlim(min(xs) - 0.6, max(xs) + 0.6)
    elif kind != "estimation":
        bottom.set_xlim(-0.6, len(names) - 0.4)

    # The Y range, split into segments when the axis has breaks.
    if gaps:
        low, high = _limits(bottom, y_axis, log)
        edges = [low] + [edge for gap in gaps for edge in gap] + [high]
        spans = [(edges[2 * i], edges[2 * i + 1]) for i in range(len(gaps) + 1)]  # bottom first
        if any(top_ <= low_ for low_, top_ in spans):
            notes.append("An axis break lies outside the Y range; set the range or the breaks so each segment holds some of it.")
        for segment, (low_, high_) in zip(reversed(segments), spans):
            segment.set_ylim(low_, high_ if high_ > low_ else low_ + 1)
    else:
        _limits(ax, y_axis, log)
    y_title = y_axis.title if y_axis.title is not None else table.title
    label_kw = _text_kw(y_axis.title_style, fmt, font)
    _tag((bottom if gaps else ax).set_ylabel(markup(y_title), **label_kw), "axis", "y")
    if fmt.x.title:
        _tag(bottom.set_xlabel(markup(fmt.x.title), **_text_kw(fmt.x.title_style, fmt, font)), "axis", "x")
    if fmt.title:
        pad = 6 + (font * 1.8 if fmt.legend.show and fmt.legend.position == "master" else 0)
        _tag(ax.set_title(markup(fmt.title), loc=fmt.title_loc, pad=pad, **_text_kw(fmt.title_style, fmt, font + 1)), "title")
    if kind != "estimation" and (fmt.bracket != "none" or fmt.letters):
        before = len(ax.lines), len(ax.texts)
        if grouped is not None:
            if result is not None and result.analysis == "Two-way ANOVA":
                _grouped_marks(ax, result, grouped, table, positions, blocks, columns, fmt, log, font, notes)
        elif compared is not None:
            first, second = compared[0], compared[1]
            if first in positions and second in positions:
                _bracket(ax, positions[first], positions[second], result, fmt, log, font)
        elif result is not None and "names" in getattr(result, "values", {}):
            _multiple_marks(ax, result, table, positions, columns, fmt, log, font, notes)
        for artist in ax.lines[before[0]:] + ax.texts[before[1]:]:
            _tag(artist, "bracket")
    if right is not None:
        if on_right:
            _limits(right, fmt.right, LOG_BASES.get(fmt.right.scale) is not None)
        else:
            right.set_ylim(ax.get_ylim())  # no data set on the right: the same scale, numbered on both sides
        if fmt.right.title:
            _tag(right.set_ylabel(markup(fmt.right.title), **_text_kw(fmt.right.title_style, fmt, font)), "axis", "right")
    if gaps:
        grid.set_height_ratios(_segment_heights(segments, y_axis))
    for segment in segments:
        if y_axis.reverse and not gaps:
            segment.set_ylim(*reversed(segment.get_ylim()))
        _locators(segment.yaxis, y_axis, notes)
        _style_ticks(segment, "y", y_axis, fmt)
    _style_ticks(bottom, "x", fmt.x, fmt)
    if kind != "estimation":
        bottom.xaxis.set_minor_locator(ticker.NullLocator())
    if right is not None:
        numbering = fmt.right if on_right else AxisFormat(**{**fmt.right.__dict__, "scale": y_axis.scale, "major": y_axis.major,
                                                             "minor": y_axis.minor})
        if on_right and fmt.right.reverse:
            right.set_ylim(*reversed(right.get_ylim()))
        elif not on_right:
            right.set_ylim(ax.get_ylim())
        _locators(right.yaxis, numbering, notes)
        _style_ticks(right, "y", numbering, fmt)
        right.tick_params(axis="y", which="both", left=False, right=fmt.right.visible, labelright=fmt.right.visible)
    legend = None
    if fmt.legend.show and kind != "estimation" and fmt.legend.position != "custom":
        legend = _legend(ax, kind, _series(columns, table, places), fmt, font, None)
    figure._cq_axes = {"main": ax, "segments": segments, "bottom": bottom, "right": right}
    if gaps:
        figure.align_ylabels(segments)  # the Y title clears the numbers of every segment
    try:
        figure.tight_layout(pad=0.6, h_pad=0.4)
    except (ValueError, np.linalg.LinAlgError):
        pass
    if gaps:
        _break_marks(segments, fmt)
        boxes = [segment.get_position() for segment in segments]
        middle = (min(box.y0 for box in boxes) + max(box.y1 for box in boxes)) / 2
        bottom.yaxis.label.set_y((middle - boxes[-1].y0) / max(boxes[-1].height, 1e-6))  # the Y title centred on the whole axis
    plot = plot_transform(figure, segments)
    figure._cq_plot = plot
    if fmt.legend.show and kind != "estimation" and fmt.legend.position == "custom":
        legend = _legend(ax, kind, _series(columns, table, places), fmt, font, plot)  # dragged: placed after the layout, which it does not change
    figure._cq_legend = legend
    for note in fmt.annotations:
        _annotation(figure, note, fmt, font, plot)
    return notes


def _wrap(name: str, width: int) -> str:
    return textwrap.fill(str(name), width, break_long_words=False) if width and width > 0 else str(name)


def _segment_heights(segments, axis: AxisFormat) -> list[float]:
    """Height ratios, top segment first: as set in the format (bottom first there), else by each segment's span."""

    spans = [abs(segment.get_ylim()[1] - segment.get_ylim()[0]) for segment in segments]
    chosen = [float(value) for value in axis.segments if float(value) > 0] if axis.segments else []
    if len(chosen) == len(segments):
        return list(reversed(chosen))
    total = sum(spans) or 1.0
    return [max(0.12, span / total) for span in spans]


def _break_marks(segments, fmt: GraphFormat) -> None:
    """Two short slanted lines where a segment ends, on the Y axis line (and the right one when boxed)."""

    from matplotlib.lines import Line2D
    from matplotlib.transforms import offset_copy

    if fmt.frame == "none":
        return
    offset = fmt.frame_offset if fmt.frame == "offset" else 0.0
    for index, segment in enumerate(segments):
        ends = ([0.0] if index < len(segments) - 1 else []) + ([1.0] if index > 0 else [])
        for side, x, shift in (("left", 0.0, -offset), ("right", 1.0, 0.0)):
            if side == "right" and fmt.frame != "box":
                continue
            transform = offset_copy(segment.transAxes, segment.figure, x=shift, y=0, units="points")
            for y in ends:
                mark = Line2D([x], [y], transform=transform, marker=[(-1, -0.5), (1, 0.5)], markersize=7, linestyle="none",
                              color=fmt.axis_color, markeredgecolor=fmt.axis_color, markeredgewidth=fmt.axis_width, clip_on=False, zorder=6)
                segment.add_artist(_tag(mark, "axis", "y"))


def _draw_columns(ax, right, kind: str, table, columns, fmt: GraphFormat, outliers, log: bool, largest: int, places=None) -> None:
    """``places``: (x, data set name, colour number) of each column, for a grouped table; else column i at x = i."""

    error = fmt.error
    centers: list[tuple[float, float, str]] = []
    for number, column in enumerate(columns):
        index, key, shade = places[number] if places else (number, column.name, table.names.index(column.name))
        style = fmt.dataset(key)
        if not style.visible:
            continue
        target = right if right is not None and style.axis == "right" else ax
        values = column.finite[column.finite > 0] if log else column.finite
        flags = _flags(outliers, column, fmt, log)
        color = fmt.color_of(key, shade)
        tag = ("dataset", key)
        if kind == "bar" and style.show_bar:
            center, _low, _high = _center_and_bars(values, "median_iqr" if error == "median_iqr" else error)
            border = style.bar_border or color
            bars = target.bar(index, center, width=style.bar_width, facecolor=_rgba(style.bar_fill or color, style.bar_alpha), edgecolor=border,
                              linewidth=style.bar_border_width, hatch=style.hatch or None, hatchcolor=border, zorder=1)
            for patch in bars.patches:
                _tag(patch, *tag)
        elif kind == "box":
            parts = target.boxplot([values], positions=[index], widths=style.box_width or 0.5, whis=(0, 100), showfliers=False, patch_artist=True,
                                   medianprops={"color": style.center_color, "linewidth": style.center_width * 0.875 if style.show_center else 0},
                                   whiskerprops={"color": style.error_color, "linewidth": style.error_width * 0.9 if style.show_error else 0},
                                   capprops={"color": style.error_color, "linewidth": style.error_width * 0.9 if style.show_error else 0})
            for box in parts["boxes"]:
                box.set(facecolor=_rgba(style.box_fill or color, style.box_alpha), edgecolor=style.box_line or color, linewidth=style.box_line_width)
            for artist in (*parts["boxes"], *parts["medians"], *parts["whiskers"], *parts["caps"]):
                _tag(artist, *tag)
        elif kind == "violin" and values.size > 1:
            parts = target.violinplot([values], positions=[index], widths=style.box_width or 0.75, showextrema=False)
            for body in parts["bodies"]:
                body.set(facecolor=_rgba(style.box_fill or color, style.box_alpha), edgecolor=style.box_line or color, linewidth=style.box_line_width)
                _tag(body, *tag)
            p25, p50, p75 = percentiles(values, [25, 50, 75])
            if style.show_error:
                _tag(target.hlines([p25, p75], index - 0.12, index + 0.12, color=style.error_color, linewidth=style.error_width * 0.9,
                                   linestyles=":", zorder=3), *tag)
            if style.show_center:
                half = style.center_length * 0.4
                _tag(target.hlines(p50, index - half, index + half, color=style.center_color, linewidth=style.center_width * 0.875, zorder=3), *tag)
        area = style.symbol_size or _dot_area(fmt, values.size, largest)
        if style.show_symbols:
            if kind == "before_after":
                _dots(target, np.full(values.size, index), values, flags, style, color, area, 1.0, tag)
            else:
                offsets = _jitter(np.log10(values) if log else values, 0.22 if kind != "bar" else 0.18)
                _dots(target, index + offsets, values, flags, style, color, area, 0.9 if values.size < 200 else 0.6, tag)
        if kind in ("scatter", "bar") and values.size:
            center, low, high = _center_and_bars(values, error)
            centers.append((index, center, key))
            half = style.center_length / 2
            if style.show_center and (error != "none" or kind == "scatter"):
                _tag(target.hlines(center, index - half, index + half, color=style.center_color, linewidth=style.center_width, zorder=4), *tag)
            if style.show_error and error != "none" and np.isfinite(low) and np.isfinite(high) and high > low:
                start = center if style.error_direction == "above" else low
                _tag(target.vlines(index, start, high, color=style.error_color, linewidth=style.error_width, zorder=4), *tag)
                if style.error_cap > 0:
                    caps = [high] if style.error_direction == "above" else [low, high]
                    cap = style.error_cap / 2
                    _tag(target.hlines(caps, index - cap, index + cap, color=style.error_color, linewidth=style.error_width, zorder=4), *tag)
        elif kind in ("box", "violin", "before_after") and values.size:
            centers.append((index, float(np.median(values)), key))
    if kind == "before_after" and fmt.pair_lines:
        _draw_pairs(ax, table, [column for column in columns if fmt.dataset(column.name).visible], log, fmt)
    # A grouped table joins each data set's means across the blocks (an interaction plot); a column table, all of them.
    for key in dict.fromkeys(key for _x, _y, key in centers) if places else [None]:
        line_points = [(x, y) for x, y, own in centers if key is None or own == key]
        if fmt.connect_means and len(line_points) > 1:
            xs, ys = zip(*line_points)
            line, = ax.plot(xs, ys, color=fmt.connect_color, linewidth=fmt.connect_width, linestyle=fmt.connect_style, zorder=4.5)
            _tag(line, "connect")


def _n_text(data, column) -> str:
    if data.pseudoreplicated:
        return f"n={column.finite.size} cells"
    spec = getattr(data, "spec", None)
    if spec is not None and getattr(spec, "dot", "") == "unit":
        return f"N={column.finite.size}"
    return f"n={column.finite.size}"


def _grouped_axis(bottom, grouped, fmt: GraphFormat, font: float) -> None:
    """Labels under a grouped graph: each row under its block (interleaved), or each row under its column and each
    data set under its block (separated)."""

    every, blocks = grouped_places(grouped, fmt)
    separated = fmt.grouped_layout == "separated"
    if separated:
        ticks, labels = map(list, zip(*sorted((x, grouped.rows[n // len(grouped.columns)]) for n, (x, _key, _shade) in enumerate(every))))
    else:
        ticks, labels = [x for _name, x in blocks], [name for name, _x in blocks]
    labels = [_wrap(label, fmt.x.label_wrap) for label in labels]
    longest = max((len(part) for label in labels for part in label.split("\n")), default=0)
    angle = float(fmt.x.label_angle)
    xs = [x for x, _key, _shade in every]
    room = size_inches(fmt)[0] * 72 * 0.8 / (max(xs) - min(xs) + 1.2)  # points per column, roughly
    if separated and not angle and 0.6 * font * longest > room:
        angle = 45.0  # row names wider than their columns would run into each other
    bottom.set_xticks(ticks)
    bottom.set_xticklabels([] if fmt.x.hide_labels else [markup(label) for label in labels], rotation=angle,
                           ha="right" if angle > 0 else "left" if angle < 0 else "center", rotation_mode="anchor" if angle else "default",
                           **_text_kw(fmt.x.numbers, fmt, font, "#222222"))
    if not separated or fmt.x.hide_labels:
        return
    lines = max((label.count("\n") + 1 for label in labels), default=1)
    drop = font * (1.3 * lines + 1.0) + (0.55 * font * longest * abs(math.sin(math.radians(angle))) if angle else 0.0)
    for name, x in blocks:
        _tag(bottom.annotate(markup(_wrap(name, fmt.x.label_wrap)), xy=(x, 0), xycoords=bottom.get_xaxis_transform(), xytext=(0, -drop),
                             textcoords="offset points", ha="center", va="top", annotation_clip=False,
                             **_text_kw(fmt.x.numbers, fmt, font, "#111111")), "axis", "x")


def _grouped_marks(ax, result, grouped, table, positions: dict, blocks, columns, fmt: GraphFormat, log: bool, font: float,
                   notes: list[str]) -> None:
    """A two-way ANOVA's multiple comparisons on a grouped graph: brackets between cells (within a row or a data set)
    or between blocks (row or data set means)."""

    rows = result.values.get("comparisons") or []
    if not rows:
        notes.append("This two-way ANOVA has no multiple comparisons to show. Choose some with Change options… on its results sheet.")
        return
    if fmt.letters:
        notes.append("Compact letters are drawn on column graphs; this grouped graph shows brackets.")
        fmt = fmt.copy()
        fmt.letters = False
    compare, combined = result.options.get("compare"), result.options.get("family") == "combined"
    c = len(grouped.columns)
    cells = {(row, column): table.names[i * c + j] for i, row in enumerate(grouped.rows) for j, column in enumerate(grouped.columns)
             if i * c + j < len(table.names)}
    positions = dict(positions)
    if compare in ("column_means", "row_means"):
        if (compare == "column_means") != (fmt.grouped_layout == "separated"):
            notes.append("Comparisons of data set means are drawn on the Separated arrangement, and of row means on Interleaved "
                         "(each mean is one block).")
            return
        for name, x in blocks:
            positions[f"\0block {name}"] = x
        key = lambda _row, group: f"\0block {group}"  # noqa: E731
    elif combined:
        named = {f"{row}: {column}": name for (row, column), name in cells.items()}
        key = lambda _row, group: named.get(group)  # noqa: E731
    elif compare == "within_columns":
        key = lambda row, group: cells.get((group, row.get("within")))  # noqa: E731
    else:
        key = lambda row, group: cells.get((row.get("within"), group))  # noqa: E731
    mapped, skipped = [], 0
    for row in rows:
        first, second = key(row, row["group_1"]), key(row, row["group_2"])
        if first is None or second is None:
            skipped += 1
            continue
        reference = row.get("reference")
        mapped.append(dict(row, group_1=first, group_2=second, reference=key(row, reference) if reference else None, color_reference=reference))
    if skipped:
        notes.append(f"{skipped} of {len(rows)} comparisons are not shown: the table's rows or data sets changed since the two-way ANOVA; "
                     "run it again.")
    if not mapped:
        return
    _multiple_marks(ax, result, table, positions, columns, fmt, log, font, notes, rows=mapped)


ESTIMATION = {"Unpaired t test", "Welch's t test", "Paired t test"}


def _compared(result, table):
    """(first name, second name, analysis) of a two-column comparison of this table, or None."""

    if result is None or getattr(result, "analysis", None) is None:
        return None
    try:
        comparison = result.row("Table analyzed", "Comparison").value
    except KeyError:
        return None
    second, _, first = str(comparison).partition(" vs. ")
    if first not in table.names or second not in table.names:
        return None
    return first, second, result.analysis


def _star_kw(fmt: GraphFormat, font: float, color: str) -> dict:
    return {"fontsize": fmt.star_size or font, "color": color, "fontfamily": _family(fmt)}


def _bracket(ax, a: int, b: int, result, fmt: GraphFormat, log: bool, font: float) -> None:
    p = result.values.get("p")
    style = result.options.get("style", "classic")
    label = p_summary(p, style) if fmt.bracket == "stars" else p_text(p, style)
    if not label:
        return
    data_top = max((np.nanmax(np.asarray(collection.get_offsets())[:, 1]) for collection in ax.collections
                    if len(collection.get_offsets())), default=ax.get_ylim()[1])
    bottom, top = ax.get_ylim()
    if log:
        lift = data_top * (1 + 2 * fmt.bracket_gap)
        tick = lift * (1 + 1.3 * fmt.bracket_tip)
        text_y = tick * 1.02
        ax.set_ylim(bottom, max(top, text_y * 1.35))
    else:
        span = top - bottom
        lift = data_top + fmt.bracket_gap * span
        tick = lift + fmt.bracket_tip * span
        text_y = tick + 0.01 * span
        if fmt.y.max is None:
            ax.set_ylim(bottom, max(top, text_y + 0.12 * span))
    ax.plot([a, a, b, b], [lift, tick, tick, lift], color="#222222", linewidth=fmt.bracket_width, zorder=5)
    ax.text((a + b) / 2, text_y, label, ha="center", va="bottom", **_star_kw(fmt, font, "#111111"))


def _flags(outliers, column, fmt: GraphFormat, log: bool) -> np.ndarray:
    """Which of the column's plotted values (blanks, and values a log axis cannot show, left out) were flagged as outliers."""

    mask = (outliers or {}).get(column.name) if fmt.show_outliers else None
    flags = np.zeros(column.finite.size, bool)
    if mask is not None and np.asarray(mask).size == column.values.size:
        flags = np.asarray(mask, bool)[np.isfinite(column.values)]
    return flags[column.finite > 0] if log else flags


def _dots(ax, x, y, flags, style, color: str, area: float, alpha: float, tag) -> None:
    """Dots in the data set's symbol; the flagged ones (outliers) are hollow."""

    solid = ~flags
    fill = style.symbol_fill or color
    alpha = style.symbol_alpha or alpha
    marker = style.symbol
    if marker in OPEN_SYMBOLS:
        made = ax.scatter(x[solid], y[solid], s=area, marker=marker, color=fill, linewidth=max(0.8, style.symbol_border_width), alpha=alpha, zorder=3)
    elif style.hollow:
        made = ax.scatter(x[solid], y[solid], s=area, marker=marker, facecolors="none", edgecolors=fill,
                          linewidth=max(0.8, style.symbol_border_width), alpha=alpha, zorder=3)
    else:
        made = ax.scatter(x[solid], y[solid], s=area, marker=marker, color=fill, edgecolor=style.symbol_border or color,
                          linewidth=style.symbol_border_width, alpha=alpha, zorder=3)
    _tag(made, *tag)
    if flags.any():
        _tag(ax.scatter(x[flags], y[flags], s=area, marker=marker if marker not in OPEN_SYMBOLS else "o", facecolors="none", edgecolors=fill,
                        linewidth=1.1, zorder=3), *tag)


# --- Legend and annotations ---------------------------------------------------------------------------------------


def _series(columns, table, places) -> dict[str, int]:
    """The legend's entries: data set name -> colour number, in drawing order."""

    if places:
        return {key: shade for _x, key, shade in sorted(places, key=lambda place: place[2])}
    return {column.name: table.names.index(column.name) for column in columns}


def _legend(ax, kind: str, series: dict, fmt: GraphFormat, font: float, plot):
    from matplotlib.font_manager import FontProperties
    from matplotlib.legend import Legend
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    shown = [name for name in series if fmt.dataset(name).visible]
    order = [name for name in fmt.legend.order if name in shown] + [name for name in shown if name not in fmt.legend.order]
    if not order:
        return None
    handles = []
    for name in order:
        style = fmt.dataset(name)
        color = fmt.color_of(name, series[name])
        if kind == "bar" and style.show_bar:
            handles.append(Patch(facecolor=_rgba(style.bar_fill or color, style.bar_alpha), edgecolor=style.bar_border or color,
                                 hatch=style.hatch or None, linewidth=style.bar_border_width))
        elif kind in ("box", "violin"):
            handles.append(Patch(facecolor=_rgba(style.box_fill or color, style.box_alpha), edgecolor=style.box_line or color,
                                 linewidth=style.box_line_width))
        else:
            fill = style.symbol_fill or color
            hollow = style.hollow or style.symbol in OPEN_SYMBOLS
            handles.append(Line2D([], [], marker=style.symbol, linestyle="-" if kind == "before_after" else "none", color=fmt.pair_color,
                                  markerfacecolor="none" if hollow else fill, markeredgecolor=fill if hollow else (style.symbol_border or color),
                                  markersize=math.sqrt(style.symbol_size or _dot_area(fmt, 10)) * 1.1, markeredgewidth=style.symbol_border_width))
    text = fmt.legend.text
    prop = FontProperties(family=_family(fmt, text.family), size=text.size or font, weight="bold" if text.bold else "normal",
                          style="italic" if text.italic else "normal")
    options = {"frameon": fmt.legend.frame, "prop": prop, "labelcolor": text.color or "#111111", "handlelength": 1.4, "borderaxespad": 0.3}
    position = fmt.legend.position
    if position == "custom" and plot is not None:
        x, y = (fmt.legend.xy or [0.7, 0.98])[:2]
        options.update(loc="upper left", bbox_to_anchor=(x, y), bbox_transform=plot)
    elif position == "right":
        options.update(loc="center left", bbox_to_anchor=(1.02, 0.5))
    elif position == "master":
        options.update(loc="lower center", bbox_to_anchor=(0.5, 1.01), ncol=len(order), columnspacing=1.2, borderaxespad=0.1)
    else:
        options.update(loc=position if position in ("best", "upper right", "upper left", "lower right", "lower left") else "best")
    legend = Legend(ax, handles, [markup(name) for name in order], **options)
    legend.set_clip_on(False)  # so the layout makes room for a legend outside the plot (right, or the row above)
    if fmt.legend.frame:
        legend.get_frame().set_edgecolor(fmt.axis_color)
        legend.get_frame().set_linewidth(fmt.axis_width)
    ax.add_artist(legend)
    _tag(legend, "legend")
    return legend


def _annotation(figure, note, fmt: GraphFormat, font: float, plot) -> None:
    from matplotlib.lines import Line2D
    from matplotlib.patches import FancyArrowPatch

    tag = ("annotation", note.id)
    kw = _text_kw(note.style, fmt, font, note.color)
    if note.kind == "text":
        box = {"boxstyle": "square,pad=0.3", "facecolor": "white", "edgecolor": note.color, "linewidth": note.line_width} if note.box else None
        _tag(figure.text(note.x, note.y, markup(note.text) or " ", transform=plot, ha="center", va="center", bbox=box, zorder=8, **kw), *tag)
        return
    if note.kind == "arrow":
        arrow = FancyArrowPatch((note.x, note.y), (note.x2, note.y2), transform=plot, arrowstyle="-|>", mutation_scale=4 + 3 * note.line_width,
                                color=note.color, linewidth=note.line_width, shrinkA=0, shrinkB=0, zorder=8)
        figure.add_artist(_tag(arrow, *tag))
        if note.text:
            _tag(figure.text(note.x, note.y, markup(note.text), transform=plot, ha="center", va="bottom" if note.y >= note.y2 else "top",
                             zorder=8, **kw), *tag)
        return
    if note.kind == "line":
        figure.add_artist(_tag(Line2D([note.x, note.x2], [note.y, note.y2], transform=plot, color=note.color, linewidth=note.line_width,
                                      solid_capstyle="butt", zorder=8), *tag))
        return
    # A bracket: from (x, y) to (x2, y), its ends hanging down, the text above the middle.
    tip = 0.03
    figure.add_artist(_tag(Line2D([note.x, note.x, note.x2, note.x2], [note.y - tip, note.y, note.y, note.y - tip], transform=plot, color=note.color,
                                  linewidth=note.line_width, zorder=8), *tag))
    if note.text:
        _tag(figure.text((note.x + note.x2) / 2, note.y + 0.005, markup(note.text), transform=plot, ha="center", va="bottom", zorder=8, **kw), *tag)


# --- Multiple comparisons: brackets, stars above columns and compact letters ------------------------------------------


def _label(row: dict, fmt: GraphFormat, style: str) -> str:
    if fmt.bracket == "p":
        p = row["q"] if np.isfinite(row["q"]) else row["p_adjusted"]
        text = format_p(p, style=style)
        return f"q {text}" if np.isfinite(row["q"]) and not text.startswith(("<", ">")) else text
    return row["summary"]


def _column_tops(ax, columns, fmt: GraphFormat, log: bool, positions: dict | None = None) -> dict[str, float]:
    """The highest drawn point (dots and error bar) of each column (column i at x = i unless ``positions`` says)."""

    tops: dict[str, float] = {}
    for collection in ax.collections:
        offsets = np.asarray(collection.get_offsets(), dtype=float)
        if offsets.size == 0 or offsets.ndim != 2:
            continue
        for number, column in enumerate(columns):
            x = positions.get(column.name, number) if positions else number
            ys = offsets[np.abs(offsets[:, 0] - x) < 0.45, 1]
            ys = ys[np.isfinite(ys)]
            if ys.size:
                tops[column.name] = max(tops.get(column.name, -np.inf), float(ys.max()))
    if fmt.error != "none":
        for column in columns:
            values = column.finite[column.finite > 0] if log else column.finite
            _center, _low, high = _center_and_bars(values, fmt.error)
            if np.isfinite(high):
                tops[column.name] = max(tops.get(column.name, -np.inf), float(high))
    return tops


def _reference_order(result, rows) -> list[str]:
    options = result.options.get("control")
    ordered = [options] if isinstance(options, str) else [str(name) for name in options or []]
    for row in rows:
        reference = _reference_of(row)
        if reference and reference not in ordered:
            ordered.append(reference)
    return ordered


def _reference_of(row: dict):
    """The reference group a comparison's colour follows (a grouped table's cells keep their data set's or row's name)."""

    return row.get("color_reference", row.get("reference"))


def _multiple_marks(ax, result, table, positions: dict, columns, fmt: GraphFormat, log: bool, font: float, notes: list[str], rows=None) -> None:
    rows = [row for row in (result.values.get("comparisons") or [] if rows is None else rows)
            if row["group_1"] in positions and row["group_2"] in positions]
    if not rows:
        if "comparison_method" not in result.values:
            notes.append("This ANOVA has no multiple comparisons to show. Choose some with Change options… on its results sheet.")
        return
    style = result.options.get("style", "classic")
    tops = _column_tops(ax, columns, fmt, log, positions)
    if not tops:
        return
    bottom, top = ax.get_ylim()
    t, u = (np.log10, lambda value: 10.0**value) if log else ((lambda value: value), (lambda value: value))
    base, floor = t(max(tops.values())), t(bottom)
    if fmt.letters:
        _letters_above(ax, result, rows, positions, tops, table, fmt, (t, u), base, floor, font, notes)
        return
    if fmt.hide_ns:
        rows = [row for row in rows if row["significant"]]
    references = _reference_order(result, rows)
    colors = list(fmt.ref_colors or DEFAULT_FORMAT["ref_colors"])
    mode = fmt.bracket_mode
    if mode == "columns" and not any(row.get("reference") for row in rows):
        notes.append("Asterisks above the columns need comparisons against a reference group; brackets are shown instead.")
        mode = "brackets"
    marks = _stack(rows, positions, references, mode)
    levels = 1 + max((level for _row, _a, _b, level in marks), default=0)
    per_level = 0.085 if mode == "brackets" else 0.075
    gap = fmt.bracket_gap * 2 / 3  # 0.04 of the axis at the default gap
    final = (base - floor) / max(1e-9, 1.0 - 0.05 - levels * per_level) if marks else base - floor
    if fmt.y.max is None and marks:
        ax.set_ylim(bottom, u(floor + final))
    elif marks:
        final = t(ax.get_ylim()[1]) - floor
    # One colour for both references (a black and white style): the 2nd reference's brackets are dashed instead.
    same = len(colors) > 1 and str(colors[0]).lower() == str(colors[1]).lower()
    for row, a, b, level in marks:
        number = min(references.index(_reference_of(row)), len(colors) - 1) if _reference_of(row) in references else -1
        color = colors[number] if number >= 0 else "#222222"
        dashes = "--" if same and number == 1 else "-"
        label = _label(row, fmt, style)
        if mode == "brackets":
            y = base + gap * final + level * 0.085 * final
            leg, space = fmt.bracket_tip * 2 / 3 * final, 0.006 * final
            ax.plot([a, a, b, b], [u(y), u(y + leg), u(y + leg), u(y)], color=color, linewidth=fmt.bracket_width, linestyle=dashes, zorder=5,
                    clip_on=False)
            ax.text((a + b) / 2, u(y + leg + space), label, ha="center", va="bottom", clip_on=False, **_star_kw(fmt, font, color))
        else:
            column = row["group_2"] if row["group_1"] == row.get("reference") else row["group_1"]
            y = t(tops.get(column, u(base))) + 0.03 * final + level * 0.075 * final
            ax.text(positions[column], u(y), label, ha="center", va="bottom", clip_on=False, **_star_kw(fmt, font, color))
    used = [name for name in references if any(_reference_of(row) == name for row in rows)]
    if len(used) == 2 and marks:
        from matplotlib.lines import Line2D

        handles = [Line2D([], [], color=colors[min(number, len(colors) - 1)], linewidth=1.6, linestyle="--" if same and number == 1 else "-",
                          label=f"vs {name}") for number, name in enumerate(used)]
        legend = ax.legend(handles=handles, loc="lower right", bbox_to_anchor=(1.0, 1.0), ncol=2, frameon=False, fontsize=font - 1,
                           handlelength=2.2 if same else 1.2, borderaxespad=0.1, columnspacing=1.0)
        _tag(legend, "bracket")


def _stack(rows, positions: dict, references: list[str], mode: str) -> list[tuple[dict, int, int, int]]:
    """(row, left, right, level): brackets stacked so none overlaps another; in "columns" mode, one level per
    reference above the compared column."""

    marks = []
    if mode == "columns":
        taken: dict[str, int] = {}
        for row in sorted(rows, key=lambda r: references.index(_reference_of(r)) if _reference_of(r) in references else 99):
            if not row.get("reference"):
                continue
            column = row["group_2"] if row["group_1"] == row["reference"] else row["group_1"]
            level = taken.get(column, 0)
            taken[column] = level + 1
            marks.append((row, positions[column], positions[column], level))
        return marks
    placed: list[tuple[int, int, int]] = []
    for row in sorted(rows, key=lambda r: (abs(positions[r["group_1"]] - positions[r["group_2"]]), min(positions[r["group_1"]], positions[r["group_2"]]))):
        a, b = sorted((positions[row["group_1"]], positions[row["group_2"]]))
        level = 0
        while any(lv == level and a <= hi and lo <= b for lo, hi, lv in placed):
            level += 1
        placed.append((a, b, level))
        marks.append((row, a, b, level))
    return marks


def _letters_above(ax, result, rows, positions: dict, tops: dict, table, fmt: GraphFormat, transform, base: float, floor: float, font: float,
                   notes: list[str]) -> None:
    t, u = transform
    if result.options.get("comparisons") != "all":
        notes.append("Compact letters need comparisons of every pair of groups. Choose “Every pair of groups” with Change options….")
        return
    try:
        letters = compact_letter_display(rows, [name for name in table.names if name in positions], style=fmt.letter_style)
    except StatisticsError as exc:
        notes.append(str(exc))
        return
    bottom, _top = ax.get_ylim()
    final = (base - floor) / 0.88
    if fmt.y.max is None:
        ax.set_ylim(bottom, u(floor + final))
    for name, text in letters.items():
        if name in tops:
            ax.text(positions[name], u(t(tops[name]) + 0.03 * final), text, ha="center", va="bottom", clip_on=False, **_star_kw(fmt, font, "#111111"))


def _draw_pairs(ax, table, columns, log: bool, fmt: GraphFormat) -> None:
    rows = max((column.values.size for column in columns), default=0)
    drawn = [column.name for column in table.columns if column.finite.size]  # the x position of each column
    for row in range(rows):
        xs, ys = [], []
        for column in columns:
            values = table[column.name].values
            value = values[row] if row < values.size else np.nan
            if np.isfinite(value) and (not log or value > 0):
                xs.append(drawn.index(column.name))
                ys.append(value)
        if len(xs) > 1:
            line, = ax.plot(xs, ys, color=fmt.pair_color, linewidth=fmt.pair_width, alpha=0.8, zorder=2)
            _tag(line, "connect")


def _draw_estimation(ax, figure, columns, compared, result, fmt: GraphFormat, font: float):
    first, second, _analysis = compared
    lookup = {column.name: column for column in columns}
    a, b = lookup[first].finite, lookup[second].finite
    values = result.values
    mean_a, difference = float(np.mean(a)), float(values.get("difference", np.mean(b) - np.mean(a)))
    for index, (name, column) in enumerate(((first, a), (second, b))):
        style = fmt.dataset(name)
        color = fmt.color_of(name, index)
        offsets = _jitter(column, 0.2)
        if style.show_symbols:
            _dots(ax, index + offsets, column, np.zeros(column.size, bool), style, color, style.symbol_size or _dot_area(fmt, column.size), 1.0,
                  ("dataset", name))
        center, low, high = _center_and_bars(column, fmt.error if fmt.error != "median_iqr" else "sd")
        half = style.center_length / 2
        if style.show_center:
            _tag(ax.hlines(center, index - half, index + half, color=style.center_color, linewidth=style.center_width, zorder=4), "dataset", name)
        if style.show_error and np.isfinite(low) and np.isfinite(high) and high > low:
            _tag(ax.vlines(index, center if style.error_direction == "above" else low, high, color=style.error_color, linewidth=style.error_width,
                           zorder=4), "dataset", name)
    x = 2.2
    low, high = values.get("ci_low", np.nan), values.get("ci_high", np.nan)
    ax.hlines(mean_a, 0, x + 0.3, color="#8A8F95", linewidth=0.8, linestyles="--", zorder=1)
    ax.hlines(mean_a + difference, 1, x + 0.3, color="#8A8F95", linewidth=0.8, linestyles="--", zorder=1)
    ax.vlines(x, mean_a + low, mean_a + high, color="#111111", linewidth=1.6, zorder=4)
    ax.hlines([mean_a + low, mean_a + high], x - 0.08, x + 0.08, color="#111111", linewidth=1.2, zorder=4)
    ax.scatter([x], [mean_a + difference], s=28, color="#111111", zorder=5)
    secondary = ax.secondary_yaxis("right", functions=(lambda y: y - mean_a, lambda d: d + mean_a))
    secondary.spines["right"].set_visible(True)
    secondary.spines["right"].set_color(fmt.axis_color)
    secondary.spines["right"].set_linewidth(fmt.axis_width)
    secondary.tick_params(labelsize=font, colors="#222222", width=fmt.axis_width)
    _tag(secondary.spines["right"], "axis", "right", radius=5)
    ci = result.options.get("ci", 0.95)
    secondary.set_ylabel(f"Difference ({second} − {first})\nwith {100 * ci:g}% CI", fontsize=font, fontfamily=_family(fmt))
    ax.set_xlim(-0.6, x + 0.6)
    names = [first, second, "Difference"]
    return names, {first: 0, second: 1}


def _draw_xy(figure, data, result, fmt: GraphFormat, font: float, notes: list[str]) -> list[str]:
    """An XY table: each data set's points (or row means with error bars) against X, and a linked linear regression's
    lines with their bands."""

    from cellquant.stats.xy import band

    xy = data.xy
    ax = figure.add_subplot(111)
    _style_frame(ax, fmt)
    x_base, y_base = LOG_BASES.get(fmt.x.scale), LOG_BASES.get(fmt.y.scale)
    if x_base:
        ax.set_xscale("log", base=x_base)
    _apply_scale(ax, fmt.y)
    if fmt.y.breaks:
        notes.append("Axis breaks are drawn on column graphs only.")
    fits = result.values.get("fits") if result is not None and getattr(result, "analysis", "") == "Linear regression" else None
    if fmt.xy_band != "none" and not fits:
        notes.append("A band is drawn around a fitted line: choose a linear regression of this table as the graph's comparison.")
    left_out = 0
    for j, name in enumerate(xy.sets):
        style = fmt.dataset(name)
        if not style.visible:
            continue
        color, tag = fmt.color_of(name, j), ("dataset", name)
        rows = []  # (x, center, low, high) per row
        for i in range(xy.x.size):
            values = xy.values[i, j]
            values = values[np.isfinite(values)]
            if y_base:
                values = values[values > 0]
            if not np.isfinite(xy.x[i]) or values.size == 0 or (x_base and xy.x[i] <= 0):
                left_out += int(values.size > 0 and np.isfinite(xy.x[i]))
                continue
            rows.append((xy.x[i], values))
        if not rows:
            continue
        each = fmt.xy_replicates == "each" or xy.replicates == 1
        area = style.symbol_size or _dot_area(fmt, sum(values.size for _x, values in rows) if each else len(rows))
        if each:
            xs = np.concatenate([np.full(values.size, x) for x, values in rows])
            ys = np.concatenate([values for _x, values in rows])
        else:
            bars = [(x, *_center_and_bars(values, fmt.error)) for x, values in rows]
            xs, ys = np.array([b[0] for b in bars]), np.array([b[1] for b in bars])
            if style.show_error and fmt.error != "none":
                for x, center, low, high in bars:
                    if np.isfinite(low) and np.isfinite(high) and high > low:
                        start = center if style.error_direction == "above" else low
                        _tag(ax.vlines(x, start, high, color=style.error_color, linewidth=style.error_width, zorder=4), *tag)
                        if style.error_cap > 0:
                            caps = [high] if style.error_direction == "above" else [low, high]
                            _tag(ax.plot([x] * len(caps), caps, linestyle="none", marker="_", markersize=style.error_cap * 14,
                                         markeredgewidth=style.error_width, color=style.error_color, zorder=4)[0], *tag)
        if style.show_symbols:
            _dots(ax, xs, ys, np.zeros(xs.size, bool), style, color, area, 0.9 if xs.size < 200 else 0.6, tag)
        if fmt.connect_means:  # each data set's means joined in X order
            means = [float(np.mean(ys[xs == x])) for x in np.unique(xs)]
            _tag(ax.plot(np.unique(xs), means, color=color, linewidth=fmt.connect_width, linestyle=fmt.connect_style, zorder=2)[0], *tag)
        fit = (fits or {}).get(name)
        if fit is not None:
            low_x, high_x = fit["x_min"], fit["x_max"]
            grid = (np.geomspace(low_x, high_x, 200) if x_base and low_x > 0 else np.linspace(low_x, high_x, 200))
            ys = fit["slope"] * grid + fit["intercept"]
            if fmt.xy_band != "none":
                low, high = band(fit, grid, result.options.get("ci", 0.95), prediction=fmt.xy_band == "prediction")
                _tag(ax.fill_between(grid, low, high, color=color, alpha=0.15, linewidth=0, zorder=1), *tag)
            _tag(ax.plot(grid, ys, color=color, linewidth=max(0.8, style.center_width * 0.75), zorder=4.5)[0], *tag)
    if left_out:
        notes.append(f"{left_out} value(s) cannot be shown on a log axis (≤ 0) and are left out of the graph.")
    if fmt.x.min is not None or fmt.x.max is not None:
        low, high = ax.get_xlim()
        ax.set_xlim(fmt.x.min if fmt.x.min is not None else low, fmt.x.max if fmt.x.max is not None else high)
    _limits(ax, fmt.y, bool(y_base))
    if fmt.y.reverse:
        ax.set_ylim(*reversed(ax.get_ylim()))
    if fmt.x.reverse:
        ax.set_xlim(*reversed(ax.get_xlim()))
    y_title = fmt.y.title if fmt.y.title is not None else xy.title
    _tag(ax.set_ylabel(markup(y_title), **_text_kw(fmt.y.title_style, fmt, font)), "axis", "y")
    _tag(ax.set_xlabel(markup(fmt.x.title or xy.x_title), **_text_kw(fmt.x.title_style, fmt, font)), "axis", "x")
    if fmt.title:
        pad = 6 + (font * 1.8 if fmt.legend.show and fmt.legend.position == "master" else 0)
        _tag(ax.set_title(markup(fmt.title), loc=fmt.title_loc, pad=pad, **_text_kw(fmt.title_style, fmt, font + 1)), "title")
    for which, axis in (("y", fmt.y), ("x", fmt.x)):
        _locators(ax.yaxis if which == "y" else ax.xaxis, axis, notes)
        _style_ticks(ax, which, axis, fmt)
    series = {name: j for j, name in enumerate(xy.sets)}
    legend = None
    if fmt.legend.show and fmt.legend.position != "custom":
        legend = _legend(ax, "scatter", series, fmt, font, None)
    figure._cq_axes = {"main": ax, "segments": [ax], "bottom": ax, "right": None}
    try:
        figure.tight_layout(pad=0.6)
    except (ValueError, np.linalg.LinAlgError):
        pass
    plot = plot_transform(figure, [ax])
    figure._cq_plot = plot
    if fmt.legend.show and fmt.legend.position == "custom":
        legend = _legend(ax, "scatter", series, fmt, font, plot)
    figure._cq_legend = legend
    for note in fmt.annotations:
        _annotation(figure, note, fmt, font, plot)
    return notes


def _draw_contingency(figure, data, result, fmt: GraphFormat, font: float, notes: list[str]) -> list[str]:
    """A contingency table: one bar per row split by outcome (100% stacked), or the outcomes side by side as % of the
    row or as counts, coloured by outcome. A linked analysis's P value is written above the bars."""

    table = data.contingency
    ax = figure.add_subplot(111)
    _style_frame(ax, fmt)
    counts = np.where(np.isfinite(table.counts), table.counts, 0.0)
    totals = counts.sum(axis=1, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        percent = np.where(totals > 0, 100 * counts / totals, 0.0)
    if not np.isfinite(table.counts).all():
        notes.append("Blank cells are drawn as 0; type a count (0 for none) in every cell.")
    rows = len(table.rows)
    shown = [j for j, name in enumerate(table.columns) if fmt.dataset(name).visible]
    stacked = fmt.contingency_values == "stacked"
    values = counts if fmt.contingency_values == "count" else percent
    if stacked:
        bottom = np.zeros(rows)
        for j in shown:
            name = table.columns[j]
            style, color = fmt.dataset(name), fmt.color_of(name, j)
            bars = ax.bar(np.arange(rows), values[:, j], style.bar_width, bottom=bottom, color=_rgba(style.bar_fill or color, style.bar_alpha),
                          edgecolor=style.bar_border or color, linewidth=style.bar_border_width, hatch=style.hatch or None, zorder=3)
            for bar in bars:
                _tag(bar, "dataset", name)
            bottom += values[:, j]
        ax.set_xticks(np.arange(rows))
    else:
        step = len(shown) + 0.6
        for k, j in enumerate(shown):
            name = table.columns[j]
            style, color = fmt.dataset(name), fmt.color_of(name, j)
            bars = ax.bar(np.arange(rows) * step + k, values[:, j], style.bar_width, color=_rgba(style.bar_fill or color, style.bar_alpha),
                          edgecolor=style.bar_border or color, linewidth=style.bar_border_width, hatch=style.hatch or None, zorder=3)
            for bar in bars:
                _tag(bar, "dataset", name)
        ax.set_xticks(np.arange(rows) * step + (len(shown) - 1) / 2)
    labels = [markup(name) for name in table.rows]
    if fmt.contingency_values != "count":  # a percentage hides how many subjects it is of
        labels = [f"{label}\n(N = {int(total)})" for label, total in zip(labels, totals[:, 0])]
    ax.set_xticklabels(labels, fontsize=font, color=fmt.axis_color)
    if stacked and len(shown) < len(table.columns):
        notes.append("Some outcomes are hidden, so the stacked bars do not reach 100%.")
    _apply_scale(ax, fmt.y)
    _limits(ax, fmt.y, False)
    if stacked and fmt.y.max is None:
        ax.set_ylim(0, 100)
    if fmt.y.reverse:
        ax.set_ylim(*reversed(ax.get_ylim()))
    default = "Subjects" if fmt.contingency_values == "count" else "% of row"
    _tag(ax.set_ylabel(markup(fmt.y.title if fmt.y.title is not None else default), **_text_kw(fmt.y.title_style, fmt, font)), "axis", "y")
    if fmt.x.title:
        _tag(ax.set_xlabel(markup(fmt.x.title), **_text_kw(fmt.x.title_style, fmt, font)), "axis", "x")
    p = result.values.get("p") if result is not None and getattr(result, "analysis", "") == "Contingency" else None
    title = fmt.title
    if p is not None:
        from cellquant.stats.pvalues import p_text

        text = p_text(p, style=result.options.get("style", "classic"))
        title = f"{title}\n{text}" if title else text
    if title:
        pad = 6 + (font * 1.8 if fmt.legend.show and fmt.legend.position == "master" else 0)
        _tag(ax.set_title(markup(title), loc=fmt.title_loc, pad=pad, **_text_kw(fmt.title_style, fmt, font + 1)), "title")
    _locators(ax.yaxis, fmt.y, notes)
    _style_ticks(ax, "y", fmt.y, fmt)
    series = {name: j for j, name in enumerate(table.columns)}
    legend = None
    if fmt.legend.show and fmt.legend.position != "custom":
        legend = _legend(ax, "bar", series, fmt, font, None)
    figure._cq_axes = {"main": ax, "segments": [ax], "bottom": ax, "right": None}
    try:
        figure.tight_layout(pad=0.6)
    except (ValueError, np.linalg.LinAlgError):
        pass
    plot = plot_transform(figure, [ax])
    figure._cq_plot = plot
    if fmt.legend.show and fmt.legend.position == "custom":
        legend = _legend(ax, "bar", series, fmt, font, plot)
    figure._cq_legend = legend
    for note in fmt.annotations:
        _annotation(figure, note, fmt, font, plot)
    return notes


UNIT_PALETTE = ["#2F7FC1", "#D1495B", "#3A9D78", "#E3A33B", "#7E5BA6", "#4BA3C3", "#A0522D", "#C9629D", "#6B8E23", "#5B6770"]


def _draw_superplot(ax, data, columns, fmt: GraphFormat) -> None:
    """Images (n) as small dots shaded by biological replicate; replicates (N) as large outlined markers;
    the line and bars are the mean ± SD (or the chosen error) of the replicates, which is what the tests use."""

    images = data.images
    units = data.units if data.units is not None else None
    every_unit = list(dict.fromkeys(str(name) for name in images["unit"]))
    colors = {name: UNIT_PALETTE[index % len(UNIT_PALETTE)] for index, name in enumerate(every_unit)}
    log = fmt.y.scale != "linear"
    error = fmt.error if fmt.error != "none" else "sd"
    small = _dot_area(fmt, len(images)) * 0.55
    for index, column in enumerate(columns):
        style = fmt.dataset(column.name)
        if not style.visible:
            continue
        tag = ("dataset", column.name)
        subset = images.loc[images["group"] == column.name]
        values = subset["value"].to_numpy(dtype=float)
        keep = np.isfinite(values) & ((values > 0) if log else True)
        offsets = _jitter(np.log10(values[keep]) if log else values[keep], 0.26)
        _tag(ax.scatter(index + offsets, values[keep], s=small, c=[colors[str(name)] for name in subset["unit"].to_numpy()[keep]],
                        alpha=0.55, linewidth=0, zorder=2), *tag)
        if units is not None and style.show_symbols:
            replicate = units.loc[units["group"] == column.name]
            unit_values = replicate["value"].to_numpy(dtype=float)
            ok = np.isfinite(unit_values)
            unit_offsets = _jitter(unit_values[ok], 0.14)
            _tag(ax.scatter(index + unit_offsets, unit_values[ok], s=style.symbol_size or 70, marker=style.symbol,
                            c=[colors.get(str(name), "#555555") for name in replicate["dot"].to_numpy()[ok]],
                            edgecolor="#111111", linewidth=1.0, zorder=4), *tag)
        center, low, high = _center_and_bars(column.finite, error)
        half = style.center_length * 0.6
        if style.show_center:
            _tag(ax.hlines(center, index - half, index + half, color=style.center_color, linewidth=style.center_width * 1.125, zorder=5), *tag)
        if style.show_error and np.isfinite(low) and np.isfinite(high) and high > low:
            start = center if style.error_direction == "above" else low
            _tag(ax.vlines(index, start, high, color=style.error_color, linewidth=style.error_width * 1.1, zorder=5), *tag)
            if style.error_cap > 0:
                caps = [high] if style.error_direction == "above" else [low, high]
                _tag(ax.hlines(caps, index - style.error_cap * 0.6, index + style.error_cap * 0.6, color=style.error_color,
                               linewidth=style.error_width * 1.1, zorder=5), *tag)
