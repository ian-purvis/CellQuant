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

from cellquant.stats import p_summary, p_text
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


def draw(figure, data, graph, result=None) -> list[str]:
    """Draw ``graph`` (a GraphSheet) of ``data`` (a DataSheet) into ``figure``. Returns notes."""

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
                ax.scatter(np.full(values.size, index), values, s=_dot_area(fmt, values.size), color=color, edgecolor="white", linewidth=0.4, zorder=3)
            else:
                offsets = _jitter(np.log10(values) if log else values, 0.22 if kind != "bar" else 0.18)
                ax.scatter(index + offsets, values, s=_dot_area(fmt, values.size), color=color, edgecolor="white",
                           linewidth=0.4, alpha=0.9 if values.size < 200 else 0.6, zorder=3)
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
    if compared is not None and fmt.get("bracket", "stars") != "none" and kind != "estimation":
        first, second = compared[0], compared[1]
        if first in positions and second in positions:
            _bracket(ax, positions[first], positions[second], result, fmt, log, font)
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
