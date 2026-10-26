"""Graphs of a column table, drawn with matplotlib (imported on first use).

``draw`` fills a matplotlib Figure from a data sheet, a graph sheet and, when the graph is linked to
a comparison, that result (for the significance bracket and the estimation plot). It returns notes
for the graph sheet (a column left out, a value that a log axis cannot show), never raises for odd
data. The look is plain and editorial: white background, no top or right axis line, labels on
the columns, the n under each one.
"""

from __future__ import annotations

import math

import numpy as np

from cellquant.errors import StatisticsError
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
    "superplot": ("SuperPlot", "Images shaded by biological replicate (small dots), each replicate's value as a large outlined marker, and the mean ± SD of the replicates. Tests use the replicates (N), not the images (n)."),
}

ERRORS = {
    "sd": ("Mean ± SD", "SD shows how much the values vary. Use it to show the spread of the data."),
    "sem": ("Mean ± SEM", "SEM = SD/√n shows how precisely the mean is known; it shrinks as n grows. It is not the spread of the data."),
    "ci": ("Mean with 95% CI", "The range that contains the true mean in 95% of experiments like this one. Shows precision, like SEM, on a scale that is easier to read."),
    "median_iqr": ("Median with interquartile range", "Median and the 25th to 75th percentiles. For skewed data, or with a rank test."),
    "none": ("No error bars", "Only the dots and the mean line."),
}

PALETTE = ["#5B6770", "#2F7FC1", "#D1495B", "#E3A33B", "#3A9D78", "#7E5BA6", "#A0522D", "#4BA3C3"]

DEFAULT_FORMAT = {
    "title": "",
    "x_title": "",
    "y_title": None,         # None: the table's title
    "y_min": None,
    "y_max": None,
    "log_y": False,
    "error": "sd",
    "colors": {},            # column name -> "#rrggbb"
    "dot_size": 0.0,         # 0: automatic (smaller with many values)
    "font_size": 8.0,
    "width": 85.0,
    "height": 75.0,
    "units": "mm",
    "bracket": "stars",      # "stars", "p" or "none"
    "bracket_mode": "brackets",  # multiple comparisons: "brackets" (one per comparison) or "columns" (stars above the compared columns)
    "hide_ns": False,        # leave out the comparisons that are not significant
    "letters": False,        # compact letter display above the columns instead of brackets
    "letter_style": "upper",  # "upper" (A B C), "lower" (a b c) or "numbers" (1 2 3)
    "ref_colors": ["#000000", "#D62728"],  # stars and brackets vs the first and the second reference group
    "show_outliers": True,   # values flagged by Identify outliers are hollow
    "show_n": True,
}

MM_PER_INCH = 25.4


def size_inches(fmt: dict) -> tuple[float, float]:
    scale = 1.0 / MM_PER_INCH if fmt.get("units", "mm") == "mm" else 1.0
    return max(0.5, float(fmt["width"]) * scale), max(0.5, float(fmt["height"]) * scale)


def color_of(fmt: dict, name: str, index: int) -> str:
    return fmt.get("colors", {}).get(name) or PALETTE[index % len(PALETTE)]


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


def _dot_area(fmt: dict, n: int) -> float:
    if fmt.get("dot_size"):
        return float(fmt["dot_size"])
    n = max(n, int(fmt.get("_n", 0)))
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


def _style_axes(ax, fmt: dict) -> None:
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_linewidth(0.8)
        ax.spines[side].set_color("#222222")
    ax.tick_params(colors="#222222", width=0.8, labelsize=fmt["font_size"])
    ax.set_facecolor("white")


def draw(figure, data, graph, result=None, outliers=None) -> list[str]:
    """Draw ``graph`` (a GraphSheet) of ``data`` (a DataSheet) into ``figure``. Returns notes.

    ``outliers`` maps a column name to a mask of its flagged cells (``Project.flags``); those values are drawn hollow."""

    fmt = {**DEFAULT_FORMAT, **graph.format}
    figure.clear()
    figure.set_facecolor("white")
    figure.set_size_inches(*size_inches(fmt), forward=False)
    font = float(fmt["font_size"])
    notes: list[str] = []
    table = data.table
    columns = [column for column in table.columns if column.finite.size]
    if len(columns) < len(table.columns):
        notes.append("Empty columns are not drawn.")
    ax = figure.add_subplot(111)
    _style_axes(ax, fmt)
    if not columns:
        ax.text(0.5, 0.5, "No values to draw", ha="center", va="center", transform=ax.transAxes, fontsize=font, color="#888")
        return notes
    log = bool(fmt["log_y"])
    if log and any(np.any(column.finite <= 0) for column in columns):
        notes.append("Values ≤ 0 cannot be shown on a log axis and are left out of the graph.")
    kind = graph.kind
    compared = _compared(result, table)
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

    fmt["_n"] = max(column.finite.size for column in columns)  # one dot size for every column
    positions = {column.name: index for index, column in enumerate(columns)}
    names = [column.name for column in columns]
    error = fmt["error"]
    if kind == "estimation":
        names, positions = _draw_estimation(ax, figure, columns, compared, result, fmt, font)
    elif kind == "superplot":
        _draw_superplot(ax, data, columns, fmt)
    else:
        for index, column in enumerate(columns):
            values = column.finite[column.finite > 0] if log else column.finite
            flags = _flags(outliers, column, fmt, log)
            color = color_of(fmt, column.name, table.names.index(column.name))
            if kind == "bar":
                center, low, high = _center_and_bars(values, "median_iqr" if error == "median_iqr" else error)
                ax.bar(index, center, width=0.6, color=color, alpha=0.28, edgecolor=color, linewidth=1.0, zorder=1)
            elif kind == "box":
                parts = ax.boxplot([values], positions=[index], widths=0.5, whis=(0, 100), showfliers=False, patch_artist=True,
                                   medianprops={"color": "#111111", "linewidth": 1.4}, whiskerprops={"color": "#333333", "linewidth": 0.9},
                                   capprops={"color": "#333333", "linewidth": 0.9})
                for box in parts["boxes"]:
                    box.set(facecolor=color, alpha=0.25, edgecolor=color, linewidth=1.0)
            elif kind == "violin" and values.size > 1:
                parts = ax.violinplot([values], positions=[index], widths=0.75, showextrema=False)
                for body in parts["bodies"]:
                    body.set(facecolor=color, edgecolor=color, alpha=0.25, linewidth=0.8)
                p25, p50, p75 = percentiles(values, [25, 50, 75])
                ax.hlines([p25, p75], index - 0.12, index + 0.12, color="#333333", linewidth=0.9, linestyles=":", zorder=3)
                ax.hlines(p50, index - 0.2, index + 0.2, color="#111111", linewidth=1.4, zorder=3)
            if kind == "before_after":
                _dots(ax, np.full(values.size, index), values, flags, color, _dot_area(fmt, values.size), 1.0)
            else:
                offsets = _jitter(np.log10(values) if log else values, 0.22 if kind != "bar" else 0.18)
                _dots(ax, index + offsets, values, flags, color, _dot_area(fmt, values.size), 0.9 if values.size < 200 else 0.6)
            if kind in ("scatter", "bar") and error != "none" and values.size:
                center, low, high = _center_and_bars(values, error)
                ax.hlines(center, index - 0.25, index + 0.25, color="#111111", linewidth=1.6, zorder=4)
                if np.isfinite(low) and np.isfinite(high) and high > low:
                    ax.vlines(index, low, high, color="#111111", linewidth=1.0, zorder=4)
                    ax.hlines([low, high], index - 0.1, index + 0.1, color="#111111", linewidth=1.0, zorder=4)
        if kind == "before_after":
            _draw_pairs(ax, table, columns, log)
    ax.set_xticks([0, 1, 2.2] if kind == "estimation" else range(len(names)))
    labels = list(names)
    if fmt.get("show_n") and kind != "estimation":
        labels = [f"{name}\n{_n_text(data, column)}" for name, column in zip(names, columns)]
    ax.set_xticklabels(labels, fontsize=font)
    if kind != "estimation":
        ax.set_xlim(-0.6, len(names) - 0.4)
    if log:
        ax.set_yscale("log")
    _limits(ax, fmt, log)
    y_title = fmt["y_title"] if fmt["y_title"] is not None else table.title
    ax.set_ylabel(y_title, fontsize=font)
    if fmt["x_title"]:
        ax.set_xlabel(fmt["x_title"], fontsize=font)
    if fmt["title"]:
        ax.set_title(fmt["title"], fontsize=font + 1, loc="left", color="#111111")
    if kind != "estimation" and (fmt.get("bracket", "stars") != "none" or fmt.get("letters")):
        if compared is not None:
            first, second = compared[0], compared[1]
            if first in positions and second in positions:
                _bracket(ax, positions[first], positions[second], result, fmt, log, font)
        elif result is not None and "names" in getattr(result, "values", {}):
            _multiple_marks(ax, result, table, positions, columns, fmt, log, font, notes)
    figure.tight_layout(pad=0.6)
    return notes


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


def _n_text(data, column) -> str:
    if data.pseudoreplicated:
        return f"n={column.finite.size} cells"
    spec = getattr(data, "spec", None)
    if spec is not None and getattr(spec, "dot", "") == "unit":
        return f"N={column.finite.size}"
    return f"n={column.finite.size}"


def _limits(ax, fmt: dict, log: bool) -> None:
    low, high = fmt.get("y_min"), fmt.get("y_max")
    if low is not None or high is not None:
        current = ax.get_ylim()
        ax.set_ylim(low if low is not None else current[0], high if high is not None else current[1])
    elif not log:
        bottom, top = ax.get_ylim()
        if bottom > 0 and bottom < 0.35 * top:
            ax.set_ylim(0, top)  # start at zero when the data are near it


def _bracket(ax, a: int, b: int, result, fmt: dict, log: bool, font: float) -> None:
    p = result.values.get("p")
    style = result.options.get("style", "GP")
    label = p_summary(p, style) if fmt.get("bracket") == "stars" else p_text(p, style)
    if not label:
        return
    data_top = max((np.nanmax(np.asarray(collection.get_offsets())[:, 1]) for collection in ax.collections
                    if len(collection.get_offsets())), default=ax.get_ylim()[1])
    bottom, top = ax.get_ylim()
    if log:
        lift = data_top * 1.12
        tick = lift * 1.04
        text_y = tick * 1.02
        ax.set_ylim(bottom, max(top, text_y * 1.35))
    else:
        span = top - bottom
        lift = data_top + 0.06 * span
        tick = lift + 0.03 * span
        text_y = tick + 0.01 * span
        if fmt.get("y_max") is None:
            ax.set_ylim(bottom, max(top, text_y + 0.12 * span))
    ax.plot([a, a, b, b], [lift, tick, tick, lift], color="#222222", linewidth=0.9, zorder=5)
    ax.text((a + b) / 2, text_y, label, ha="center", va="bottom", fontsize=font, color="#111111")


def _flags(outliers, column, fmt: dict, log: bool) -> np.ndarray:
    """Which of the column's plotted values (blanks, and values a log axis cannot show, left out) were flagged as outliers."""

    mask = (outliers or {}).get(column.name) if fmt.get("show_outliers", True) else None
    flags = np.zeros(column.finite.size, bool)
    if mask is not None and np.asarray(mask).size == column.values.size:
        flags = np.asarray(mask, bool)[np.isfinite(column.values)]
    return flags[column.finite > 0] if log else flags


def _dots(ax, x, y, flags, color: str, area: float, alpha: float) -> None:
    """Dots; the flagged ones (outliers) are hollow."""

    solid = ~flags
    ax.scatter(x[solid], y[solid], s=area, color=color, edgecolor="white", linewidth=0.4, alpha=alpha, zorder=3)
    if flags.any():
        ax.scatter(x[flags], y[flags], s=area, facecolors="none", edgecolors=color, linewidth=1.1, zorder=3)


# --- Multiple comparisons: brackets, stars above columns and compact letters ------------------------------------------


def _label(row: dict, fmt: dict, style: str) -> str:
    if fmt.get("bracket", "stars") == "p":
        p = row["q"] if np.isfinite(row["q"]) else row["p_adjusted"]
        text = format_p(p, style=style)
        return f"q {text}" if np.isfinite(row["q"]) and not text.startswith(("<", ">")) else text
    return row["summary"]


def _column_tops(ax, columns, fmt: dict, log: bool) -> dict[str, float]:
    """The highest drawn point (dots and error bar) of each column."""

    tops: dict[str, float] = {}
    for collection in ax.collections:
        offsets = np.asarray(collection.get_offsets(), dtype=float)
        if offsets.size == 0 or offsets.ndim != 2:
            continue
        index = np.rint(offsets[:, 0]).astype(int)
        for position, column in enumerate(columns):
            ys = offsets[index == position, 1]
            ys = ys[np.isfinite(ys)]
            if ys.size:
                tops[column.name] = max(tops.get(column.name, -np.inf), float(ys.max()))
    if fmt["error"] != "none":
        for column in columns:
            values = column.finite[column.finite > 0] if log else column.finite
            _center, _low, high = _center_and_bars(values, fmt["error"])
            if np.isfinite(high):
                tops[column.name] = max(tops.get(column.name, -np.inf), float(high))
    return tops


def _reference_order(result, rows) -> list[str]:
    options = result.options.get("control")
    ordered = [options] if isinstance(options, str) else [str(name) for name in options or []]
    for row in rows:
        if row.get("reference") and row["reference"] not in ordered:
            ordered.append(row["reference"])
    return ordered


def _multiple_marks(ax, result, table, positions: dict, columns, fmt: dict, log: bool, font: float, notes: list[str]) -> None:
    rows = [row for row in result.values.get("comparisons") or [] if row["group_1"] in positions and row["group_2"] in positions]
    if not rows:
        if "comparison_method" not in result.values:
            notes.append("This ANOVA has no multiple comparisons to show. Choose some with Change options… on its results sheet.")
        return
    style = result.options.get("style", "GP")
    tops = _column_tops(ax, columns, fmt, log)
    if not tops:
        return
    bottom, top = ax.get_ylim()
    t, u = (np.log10, lambda value: 10.0**value) if log else ((lambda value: value), (lambda value: value))
    base, floor = t(max(tops.values())), t(bottom)
    if fmt.get("letters"):
        _letters_above(ax, result, rows, positions, tops, table, fmt, (t, u), base, floor, font, notes)
        return
    if fmt.get("hide_ns"):
        rows = [row for row in rows if row["significant"]]
    references = _reference_order(result, rows)
    colors = list(fmt.get("ref_colors") or DEFAULT_FORMAT["ref_colors"])
    mode = fmt.get("bracket_mode", "brackets")
    if mode == "columns" and not any(row.get("reference") for row in rows):
        notes.append("Asterisks above the columns need comparisons against a reference group; brackets are shown instead.")
        mode = "brackets"
    marks = _stack(rows, positions, references, mode)
    levels = 1 + max((level for _row, _a, _b, level in marks), default=0)
    per_level = 0.085 if mode == "brackets" else 0.075
    final = (base - floor) / max(1e-9, 1.0 - 0.05 - levels * per_level) if marks else base - floor
    if fmt.get("y_max") is None and marks:
        ax.set_ylim(bottom, u(floor + final))
    elif marks:
        final = t(ax.get_ylim()[1]) - floor
    for row, a, b, level in marks:
        color = colors[min(references.index(row["reference"]), len(colors) - 1)] if row.get("reference") in references else "#222222"
        label = _label(row, fmt, style)
        if mode == "brackets":
            y = base + 0.04 * final + level * 0.085 * final
            leg, gap = 0.02 * final, 0.006 * final
            ax.plot([a, a, b, b], [u(y), u(y + leg), u(y + leg), u(y)], color=color, linewidth=0.9, zorder=5, clip_on=False)
            ax.text((a + b) / 2, u(y + leg + gap), label, ha="center", va="bottom", fontsize=font, color=color, clip_on=False)
        else:
            column = row["group_2"] if row["group_1"] == row.get("reference") else row["group_1"]
            y = t(tops.get(column, u(base))) + 0.03 * final + level * 0.075 * final
            ax.text(positions[column], u(y), label, ha="center", va="bottom", fontsize=font, color=color, clip_on=False)
    used = [name for name in references if any(row.get("reference") == name for row in rows)]
    if len(used) == 2 and marks:
        from matplotlib.lines import Line2D

        handles = [Line2D([], [], color=colors[min(number, len(colors) - 1)], linewidth=1.6, label=f"vs {name}") for number, name in enumerate(used)]
        ax.legend(handles=handles, loc="lower right", bbox_to_anchor=(1.0, 1.0), ncol=2, frameon=False, fontsize=font - 1,
                  handlelength=1.2, borderaxespad=0.1, columnspacing=1.0)


def _stack(rows, positions: dict, references: list[str], mode: str) -> list[tuple[dict, int, int, int]]:
    """(row, left, right, level): brackets stacked so none overlaps another; in "columns" mode, one level per
    reference above the compared column."""

    marks = []
    if mode == "columns":
        taken: dict[str, int] = {}
        for row in sorted(rows, key=lambda r: references.index(r["reference"]) if r.get("reference") in references else 99):
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


def _letters_above(ax, result, rows, positions: dict, tops: dict, table, fmt: dict, transform, base: float, floor: float, font: float,
                   notes: list[str]) -> None:
    t, u = transform
    if result.options.get("comparisons") != "all":
        notes.append("Compact letters need comparisons of every pair of groups. Choose “Every pair of groups” with Change options….")
        return
    try:
        letters = compact_letter_display(rows, [name for name in table.names if name in positions], style=fmt.get("letter_style", "upper"))
    except StatisticsError as exc:
        notes.append(str(exc))
        return
    bottom, _top = ax.get_ylim()
    final = (base - floor) / 0.88
    if fmt.get("y_max") is None:
        ax.set_ylim(bottom, u(floor + final))
    for name, text in letters.items():
        if name in tops:
            ax.text(positions[name], u(t(tops[name]) + 0.03 * final), text, ha="center", va="bottom", fontsize=font, color="#111111", clip_on=False)


def _draw_pairs(ax, table, columns, log: bool) -> None:
    names = [column.name for column in columns]
    rows = max(column.values.size for column in columns)
    for row in range(rows):
        xs, ys = [], []
        for index, name in enumerate(names):
            values = table[name].values
            value = values[row] if row < values.size else np.nan
            if np.isfinite(value) and (not log or value > 0):
                xs.append(index)
                ys.append(value)
        if len(xs) > 1:
            ax.plot(xs, ys, color="#8A8F95", linewidth=0.8, alpha=0.8, zorder=2)


def _draw_estimation(ax, figure, columns, compared, result, fmt: dict, font: float):
    first, second, _analysis = compared
    lookup = {column.name: column for column in columns}
    a, b = lookup[first].finite, lookup[second].finite
    values = result.values
    mean_a, difference = float(np.mean(a)), float(values.get("difference", np.mean(b) - np.mean(a)))
    for index, (name, column) in enumerate(((first, a), (second, b))):
        color = color_of(fmt, name, index)
        offsets = _jitter(column, 0.2)
        ax.scatter(index + offsets, column, s=_dot_area(fmt, column.size), color=color, edgecolor="white", linewidth=0.4, zorder=3)
        center, low, high = _center_and_bars(column, fmt["error"] if fmt["error"] != "median_iqr" else "sd")
        ax.hlines(center, index - 0.25, index + 0.25, color="#111111", linewidth=1.6, zorder=4)
        if np.isfinite(low) and np.isfinite(high) and high > low:
            ax.vlines(index, low, high, color="#111111", linewidth=1.0, zorder=4)
    x = 2.2
    low, high = values.get("ci_low", np.nan), values.get("ci_high", np.nan)
    ax.hlines(mean_a, 0, x + 0.3, color="#8A8F95", linewidth=0.8, linestyles="--", zorder=1)
    ax.hlines(mean_a + difference, 1, x + 0.3, color="#8A8F95", linewidth=0.8, linestyles="--", zorder=1)
    ax.vlines(x, mean_a + low, mean_a + high, color="#111111", linewidth=1.6, zorder=4)
    ax.hlines([mean_a + low, mean_a + high], x - 0.08, x + 0.08, color="#111111", linewidth=1.2, zorder=4)
    ax.scatter([x], [mean_a + difference], s=28, color="#111111", zorder=5)
    secondary = ax.secondary_yaxis("right", functions=(lambda y: y - mean_a, lambda d: d + mean_a))
    secondary.spines["right"].set_visible(True)
    secondary.tick_params(labelsize=font, colors="#222222")
    ci = result.options.get("ci", 0.95)
    secondary.set_ylabel(f"Difference ({second} − {first})\nwith {100 * ci:g}% CI", fontsize=font)
    ax.set_xlim(-0.6, x + 0.6)
    names = [first, second, "Difference"]
    return names, {first: 0, second: 1}


UNIT_PALETTE = ["#2F7FC1", "#D1495B", "#3A9D78", "#E3A33B", "#7E5BA6", "#4BA3C3", "#A0522D", "#C9629D", "#6B8E23", "#5B6770"]


def _draw_superplot(ax, data, columns, fmt: dict) -> None:
    """Images (n) as small dots shaded by biological replicate; replicates (N) as large outlined markers;
    the line and bars are the mean ± SD (or the chosen error) of the replicates, which is what the tests use."""

    images = data.images
    units = data.units if data.units is not None else None
    every_unit = list(dict.fromkeys(str(name) for name in images["unit"]))
    colors = {name: UNIT_PALETTE[index % len(UNIT_PALETTE)] for index, name in enumerate(every_unit)}
    log = bool(fmt["log_y"])
    error = fmt["error"] if fmt["error"] != "none" else "sd"
    small = _dot_area(fmt, len(images)) * 0.55
    for index, column in enumerate(columns):
        subset = images.loc[images["group"] == column.name]
        values = subset["value"].to_numpy(dtype=float)
        keep = np.isfinite(values) & ((values > 0) if log else True)
        offsets = _jitter(np.log10(values[keep]) if log else values[keep], 0.26)
        ax.scatter(index + offsets, values[keep], s=small, c=[colors[str(name)] for name in subset["unit"].to_numpy()[keep]],
                   alpha=0.55, linewidth=0, zorder=2)
        if units is not None:
            replicate = units.loc[units["group"] == column.name]
            unit_values = replicate["value"].to_numpy(dtype=float)
            ok = np.isfinite(unit_values)
            unit_offsets = _jitter(unit_values[ok], 0.14)
            ax.scatter(index + unit_offsets, unit_values[ok], s=70, c=[colors.get(str(name), "#555555") for name in replicate["dot"].to_numpy()[ok]],
                       edgecolor="#111111", linewidth=1.0, zorder=4)
        center, low, high = _center_and_bars(column.finite, error)
        ax.hlines(center, index - 0.3, index + 0.3, color="#111111", linewidth=1.8, zorder=5)
        if np.isfinite(low) and np.isfinite(high) and high > low:
            ax.vlines(index, low, high, color="#111111", linewidth=1.1, zorder=5)
            ax.hlines([low, high], index - 0.12, index + 0.12, color="#111111", linewidth=1.1, zorder=5)
