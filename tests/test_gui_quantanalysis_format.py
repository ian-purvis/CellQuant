"""QuantAnalysis graph formatting: the GraphFormat model, every format option as drawn artists, axis breaks, number
formats, click-to-select and dragging, styles (save, apply, Magic, copy and paste) and WYSIWYG export.
Needs Qt (xvfb-run on Linux) and matplotlib."""

from __future__ import annotations

import json
import re

import numpy as np
import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("matplotlib")

from matplotlib.backend_bases import MouseEvent, PickEvent  # noqa: E402
from matplotlib.collections import LineCollection, PathCollection, PolyCollection  # noqa: E402
from matplotlib.colors import to_hex, to_rgba  # noqa: E402
from matplotlib.patches import FancyArrowPatch, Rectangle  # noqa: E402

from cellquant.gui.quantanalysis import format_panel, graphs, tips  # noqa: E402
from cellquant.gui.quantanalysis import graph_format as gf  # noqa: E402
from cellquant.gui.quantanalysis.project import DataSheet, GraphSheet  # noqa: E402
from cellquant.stats import ColumnTable  # noqa: E402

NAMES = ["Control", "Treated 1", "Treated 2"]


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))  # saved styles go here, not to the real settings folder
    window = QuantAnalysisWindow()
    qtbot.addWidget(window)
    window.resize(1400, 900)
    window.show()
    return window


def _data(window, paired: bool = False) -> DataSheet:
    rng = np.random.default_rng(7)
    table = ColumnTable.from_columns({NAMES[0]: rng.normal(10, 2, 8), NAMES[1]: rng.normal(30, 3, 8), NAMES[2]: rng.normal(120, 8, 8)},
                                     title="% of target")
    table.paired = paired
    data = window.project.add_data(DataSheet("Conditions", table))
    window.select(data.id)
    return data


def _graph(window, kind: str = "scatter", **fmt):
    data = window.current_data() or _data(window)
    window.select(data.id)
    graph = window.new_graph(kind)
    graph.format.update(**fmt)
    view = window.views[graph.id]
    view.fill()
    view.flush()
    return graph, view


def _tagged(view, target) -> list:
    return view.artists_of(target)


def _collections(view, name, kind=PathCollection) -> list:
    return [artist for artist in _tagged(view, ("dataset", name)) if isinstance(artist, kind)]


def _redraw(view):
    view.draw()
    view.canvas.draw()


# --- The model ---------------------------------------------------------------------------------------------------


def test_graph_format_round_trip_and_old_keys():
    fmt = gf.GraphFormat(frame="offset", right_axis=True, palette=list(gf.COLORBLIND_PALETTE))
    fmt.y.breaks = [[40.0, 90.0]]
    fmt.y.title_style = gf.TextStyle(family="Arial", size=9.5, bold=True, italic=True, color="#123456")
    fmt.datasets["Control"] = gf.DataSetFormat(symbol="^", hatch="///", hollow=True, error_direction="above")
    fmt.legend.order = ["Treated 1", "Control"]
    fmt.legend.xy = [0.2, 0.8]
    fmt.annotations.append(gf.Annotation(kind="arrow", text="α", x=0.1, y=0.2, x2=0.3, y2=0.4))
    text = json.dumps(fmt.to_dict())
    back = gf.GraphFormat.from_dict(json.loads(text))
    assert back == fmt and back.to_dict() == fmt.to_dict()
    assert isinstance(back.datasets["Control"], gf.DataSetFormat) and isinstance(back.annotations[0], gf.Annotation)
    # The first format's flat keys still read and write.
    old = gf.GraphFormat.from_dict({"y_min": 0, "y_max": 50, "log_y": True, "y_title": "Area (µm^2)", "x_title": "", "error": "sem",
                                    "colors": {"Control": "#ff0000"}, "unknown": 1})
    assert (old.y.min, old.y.max, old.y.scale, old.y.title, old.error) == (0, 50, "log10", "Area (µm^2)", "sem")
    assert old["log_y"] and old["y_min"] == 0 and old.get("nothing", 3) == 3 and "ref_colors" in old
    old.update(log_y=False, y_max=None)
    assert old.y.scale == "linear" and old.y.max is None
    assert dict(**{key: old[key] for key in old.keys()})["error"] == "sem"
    # Lists that come back as one comma string (QSettings, hand-edited files) are split.
    assert gf.GraphFormat.from_dict({"palette": "#000000, #ffffff"}).palette == ["#000000", "#ffffff"]
    # A GraphSheet made with a dict holds a GraphFormat.
    assert isinstance(GraphSheet("g", "d", "scatter", {"error": "ci"}).format, gf.GraphFormat)


def test_markup_and_number_formats():
    assert gf.markup(r"\alpha-tubulin (µm^2)") == "α-tubulin (µm²)"
    assert gf.markup("CO_{2} and x^{-1}") == "CO₂ and x⁻¹"
    assert gf.markup("k_{off}") == r"k$\mathregular{_{off}}$"
    assert gf.markup("cost $5") == r"cost \$5"
    axis = gf.AxisFormat
    assert gf.number_text(12345.678, axis(decimals=1, thousands=True)) == "12,345.7"
    assert gf.number_text(-3.0, axis(decimals=0)) == "−3"
    assert gf.number_text(45, axis(percent=True)) == "45%"
    assert gf.number_text(2, axis(prefix="~", suffix=" µm", decimals=1)) == "~2.0 µm"
    assert gf.number_text(15000, axis(notation="scientific")) == "1.5×10⁴"
    assert gf.number_text(0.00012, axis(notation="scientific", decimals=2)) == "1.20×10⁻⁴"
    assert gf.number_text(1000, axis(notation="power"), base=10) == "10³"
    assert gf.number_text(8, axis(notation="power"), base=2) == "2³"
    assert gf.auto_decimals([0, 0.5, 1.0]) == 1 and gf.auto_decimals([0, 20, 40]) == 0


def test_number_format_on_the_drawn_axis(window):
    view = _graph(window)[1]
    fmt = view.sheet.format
    fmt.y.decimals, fmt.y.thousands, fmt.y.prefix, fmt.y.suffix, fmt.y.major = 1, True, "", " %", 500
    fmt.y.min, fmt.y.max = 0, 2000
    _redraw(view)
    labels = [label.get_text() for label in view.figure.axes[0].get_yticklabels() if label.get_text()]
    assert {"0.0 %", "500.0 %", "1,000.0 %", "2,000.0 %"} <= set(labels)
    fmt.y.decimals, fmt.y.thousands, fmt.y.suffix, fmt.y.notation = -1, False, "", "scientific"
    _redraw(view)
    assert "1×10³" in [label.get_text() for label in view.figure.axes[0].get_yticklabels()]
    fmt.y.notation, fmt.y.scale, fmt.y.min, fmt.y.max = "power", "log10", 1, 1000
    _redraw(view)
    assert {"10⁰", "10¹", "10²", "10³"} <= {label.get_text() for label in view.figure.axes[0].get_yticklabels()}


# --- Data sets -----------------------------------------------------------------------------------------------------


def test_symbol_options_change_the_dots(window):
    graph, view = _graph(window)
    fmt = graph.format
    dots = _collections(view, NAMES[0])[0]
    assert to_hex(dots.get_facecolor()[0]) == graphs.color_of(fmt, NAMES[0], 0).lower()
    fmt.datasets[NAMES[0]] = gf.DataSetFormat(symbol="s", symbol_size=50, symbol_fill="#00ff00", symbol_border="#0000ff",
                                              symbol_border_width=1.5, symbol_alpha=0.5)
    _redraw(view)
    dots = _collections(view, NAMES[0])[0]
    assert dots.get_sizes()[0] == 50 and to_hex(dots.get_facecolor()[0]) == "#00ff00"
    assert to_hex(dots.get_edgecolor()[0]) == "#0000ff" and dots.get_linewidths()[0] == 1.5
    assert dots.get_alpha() == 0.5
    square = dots.get_paths()[0].vertices
    assert len(square) == 5  # a square marker path, not a circle
    fmt.datasets[NAMES[0]].hollow = True
    _redraw(view)
    assert len(_collections(view, NAMES[0])[0].get_facecolor()) == 0
    fmt.datasets[NAMES[0]].show_symbols = False
    _redraw(view)
    assert _collections(view, NAMES[0]) == []
    fmt.datasets[NAMES[0]].visible = False
    _redraw(view)
    assert _tagged(view, ("dataset", NAMES[0])) == []
    assert _collections(view, NAMES[1])  # the others stay


def test_mean_line_and_error_bar_options(window):
    graph, view = _graph(window)
    fmt = graph.format

    def lines():
        return [artist for artist in _tagged(view, ("dataset", NAMES[1])) if isinstance(artist, LineCollection)]

    assert len(lines()) == 3  # mean line, error bar, caps
    fmt.datasets[NAMES[1]] = gf.DataSetFormat(center_width=3.0, center_length=0.8, center_color="#ff0000", error_direction="above",
                                              error_cap=0, error_width=2.5, error_color="#00aa00")
    _redraw(view)
    mean, error = lines()
    assert mean.get_linewidth()[0] == 3.0 and to_hex(mean.get_color()[0]) == "#ff0000"
    (x0, _y0), (x1, _y1) = mean.get_segments()[0]
    assert x1 - x0 == pytest.approx(0.8)
    (_ex, low), (_ex2, high) = error.get_segments()[0]
    values = window.current_data().table[NAMES[1]].finite
    assert low == pytest.approx(values.mean()) and high == pytest.approx(values.mean() + values.std(ddof=1))  # above only
    assert error.get_linewidth()[0] == 2.5 and to_hex(error.get_color()[0]) == "#00aa00"
    fmt.datasets[NAMES[1]].show_error = False
    fmt.datasets[NAMES[1]].show_center = False
    _redraw(view)
    assert lines() == []


def test_bar_box_violin_and_connecting_lines(window):
    graph, view = _graph(window, "bar")
    fmt = graph.format
    fmt.datasets[NAMES[0]] = gf.DataSetFormat(bar_fill="#ffffff", bar_alpha=1.0, hatch="///", bar_border="#000000", bar_border_width=2.0,
                                              bar_width=0.4)
    _redraw(view)
    bar = [artist for artist in _tagged(view, ("dataset", NAMES[0])) if isinstance(artist, Rectangle)][0]
    assert bar.get_hatch() == "///" and bar.get_width() == pytest.approx(0.4) and bar.get_linewidth() == 2.0
    assert to_hex(bar.get_facecolor()) == "#ffffff" and to_hex(bar.get_edgecolor()) == "#000000"
    other = [artist for artist in _tagged(view, ("dataset", NAMES[1])) if isinstance(artist, Rectangle)][0]
    assert other.get_facecolor()[3] == pytest.approx(0.28) and other.get_edgecolor()[3] == 1.0  # a light fill, a solid border
    fmt.dataset_default.show_bar = False
    fmt.datasets.clear()
    _redraw(view)
    assert not [artist for artist in _tagged(view, ("dataset", NAMES[0])) if isinstance(artist, Rectangle)]
    graph.kind = "box"
    fmt.dataset_default = gf.DataSetFormat(box_fill="#abcdef", box_alpha=0.5, box_line="#111111", box_line_width=2.0)
    _redraw(view)
    box = [artist for artist in _tagged(view, ("dataset", NAMES[0])) if artist.__class__.__name__ == "PathPatch"][0]
    assert box.get_facecolor() == pytest.approx(to_rgba("#abcdef", 0.5)) and box.get_linewidth() == 2.0
    graph.kind = "violin"
    _redraw(view)
    body = [artist for artist in _tagged(view, ("dataset", NAMES[0])) if isinstance(artist, PolyCollection)][0]
    assert to_hex(body.get_facecolor()[0]) == "#abcdef"
    graph.kind = "scatter"
    fmt.connect_means, fmt.connect_color, fmt.connect_width, fmt.connect_style = True, "#ff00ff", 2.0, "--"
    _redraw(view)
    joined = _tagged(view, ("connect",))
    assert len(joined) == 1 and joined[0].get_linestyle() == "--" and len(joined[0].get_xdata()) == 3
    view.window.project.data[graph.data_id].table.paired = True
    graph.kind = "before_after"
    fmt.connect_means, fmt.pair_color, fmt.pair_width = False, "#00ffff", 1.7
    _redraw(view)
    pairs = _tagged(view, ("connect",))
    assert len(pairs) == 8 and {to_hex(line.get_color()) for line in pairs} == {"#00ffff"}
    fmt.pair_lines = False
    _redraw(view)
    assert _tagged(view, ("connect",)) == []


def test_apply_to_all_data_sets_and_right_axis(window):
    graph, view = _graph(window)
    section = view.panel["datasets"]
    view.collapsibles["datasets"].expand()
    section.show_dataset(NAMES[1])
    section.symbol.setCurrentIndex(section.symbol.findData("D"))
    section.symbol_size.setValue(40)
    assert graph.format.datasets[NAMES[1]].symbol == "D"
    section.apply_all.click()
    assert graph.format.datasets == {} and graph.format.dataset_default.symbol == "D"
    view.flush()
    assert all(_collections(view, name)[0].get_sizes()[0] == 40 for name in NAMES)
    section.show_dataset(NAMES[2])
    section.axis.setCurrentIndex(section.axis.findData("right"))
    view.flush()
    right = view.figure._cq_axes["right"]
    assert right is not None and _collections(view, NAMES[2])[0].axes is right
    assert right.get_ylim()[1] > view.figure.axes[0].get_ylim()[1]  # its own scale


# --- Axes ----------------------------------------------------------------------------------------------------------


def test_frame_scale_ticks_and_titles(window):
    graph, view = _graph(window)
    fmt = graph.format
    ax = view.figure.axes[0]
    assert not ax.spines["top"].get_visible() and ax.spines["left"].get_position() != ("outward", 6.0)
    fmt.frame, fmt.frame_offset, fmt.axis_width = "offset", 8.0, 1.5
    _redraw(view)
    ax = view.figure.axes[0]
    assert ax.spines["left"].get_position() == ("outward", 8.0) and ax.spines["left"].get_linewidth() == 1.5
    fmt.frame = "box"
    _redraw(view)
    assert all(view.figure.axes[0].spines[side].get_visible() for side in ("top", "right", "left", "bottom"))
    fmt.frame = "plain"
    fmt.y.scale = "log2"
    _redraw(view)
    ax = view.figure.axes[0]
    assert ax.get_yscale() == "log" and ax.yaxis.get_transform().base == 2
    fmt.y.scale, fmt.y.reverse, fmt.y.major, fmt.y.minor = "linear", True, 25, 4
    fmt.y.tick_direction, fmt.y.tick_length = "in", 6.0
    _redraw(view)
    ax = view.figure.axes[0]
    low, high = ax.get_ylim()
    assert low > high  # reversed
    locs = [loc for loc in ax.get_yticks() if min(low, high) <= loc <= max(low, high)]
    assert np.allclose(np.diff(locs), -25) or np.allclose(np.diff(locs), 25)
    assert len(ax.yaxis.get_minorticklocs()) >= 4 * (len(locs) - 1)
    tick = ax.yaxis.get_major_ticks()[0]
    assert tick._tickdir == "in" and tick.tick1line.get_markersize() == 6.0
    fmt.y.title = r"Area (µm^2), \Delta"
    fmt.y.title_style = gf.TextStyle(size=11, bold=True, italic=True, color="#aa0000")
    fmt.y.numbers = gf.TextStyle(bold=True, size=7)
    fmt.title, fmt.title_style = "Title", gf.TextStyle(bold=True, size=12)
    _redraw(view)
    ax = view.figure.axes[0]
    label = ax.yaxis.label
    assert label.get_text() == "Area (µm²), Δ" and label.get_fontsize() == 11 and label.get_fontweight() == "bold"
    assert label.get_fontstyle() == "italic" and to_hex(label.get_color()) == "#aa0000"
    assert all(tick.get_fontweight() == "bold" and tick.get_fontsize() == 7 for tick in ax.get_yticklabels())
    assert ax.get_title(loc="left") == "Title" and ax._left_title.get_fontsize() == 12
    fmt.x.label_angle, fmt.x.label_wrap = 45, 4
    _redraw(view)
    labels = view.figure.axes[0].get_xticklabels()
    assert labels[1].get_rotation() == 45 and labels[1].get_text().startswith("Treated\n1")
    fmt.x.hide_labels = True
    _redraw(view)
    assert all(not label.get_text() for label in view.figure.axes[0].get_xticklabels())
    fmt.y.visible = False
    _redraw(view)
    assert not view.figure.axes[0].spines["left"].get_visible()
    fmt.y.visible, fmt.right_axis, fmt.right.title, fmt.y.reverse = True, True, "Right", False
    _redraw(view)
    right = view.figure._cq_axes["right"]
    assert right.get_ylim() == view.figure.axes[0].get_ylim() and right.get_ylabel() == "Right"  # the same scale, numbered again


def test_axis_breaks_split_the_axis(window):
    graph, view = _graph(window, "bar")
    fmt = graph.format
    fmt.y.breaks = [[45, 95]]
    _redraw(view)
    segments = view.figure._cq_axes["segments"]
    assert len(segments) == 2
    top, bottom = segments
    assert bottom.get_ylim()[1] == pytest.approx(45) and top.get_ylim()[0] == pytest.approx(95)
    assert not top.spines["bottom"].get_visible() and not bottom.spines["top"].get_visible()
    marks = [artist for artist in _tagged(view, ("axis", "y")) if artist.__class__.__name__ == "Line2D"]
    assert len(marks) == 2  # one slanted mark at each side of the gap
    assert top.get_position().y0 > bottom.get_position().y1  # a gap between the segments
    assert not any(tick.label1.get_visible() for tick in top.xaxis.get_major_ticks())
    middle = (bottom.get_position().y0 + top.get_position().y1) / 2
    label_y = bottom.transAxes.transform((0, bottom.yaxis.label.get_position()[1]))[1]
    assert view.figure.transFigure.inverted().transform((0, label_y))[1] == pytest.approx(middle, abs=0.01)  # centred on the whole axis
    # Three segments with chosen heights, bottom first.
    fmt.y.breaks, fmt.y.segments = [[20, 25], [45, 95]], [0.5, 0.2, 0.3]
    _redraw(view)
    segments = view.figure._cq_axes["segments"]
    assert len(segments) == 3
    heights = [segment.get_position().height for segment in reversed(segments)]
    assert heights[0] / heights[1] == pytest.approx(2.5, rel=0.05) and heights[2] / heights[1] == pytest.approx(1.5, rel=0.05)
    assert view.notes.isHidden()
    fmt.y.scale = "log10"
    assert "linear Y axis" in "\n".join(view.draw())
    fmt.y.scale, fmt.y.breaks = "linear", [[200, 300]]
    assert "outside the Y range" in "\n".join(view.draw())


# --- Text, legend and annotations -----------------------------------------------------------------------------------


def test_legend_annotations_and_brackets(window):
    graph, view = _graph(window, "bar")
    fmt = graph.format
    fmt.legend.show, fmt.legend.order, fmt.legend.frame = True, [NAMES[2]], True
    _redraw(view)
    legend = _tagged(view, ("legend",))[0]
    assert [text.get_text() for text in legend.get_texts()] == [NAMES[2], NAMES[0], NAMES[1]]
    assert legend.get_frame_on() and legend.legend_handles[0].get_hatch() is None
    fmt.legend.position = "master"
    _redraw(view)
    legend = _tagged(view, ("legend",))[0]
    assert legend._ncols == 3
    fmt.legend.position, fmt.legend.xy = "custom", [0.1, 0.5]
    _redraw(view)
    legend = _tagged(view, ("legend",))[0]
    box = legend.get_window_extent(view.canvas.get_renderer())
    corner = view.figure._cq_plot.inverted().transform((box.x0, box.y1))
    assert corner == pytest.approx([0.1, 0.5], abs=0.03)
    fmt.annotations = [gf.Annotation(kind="text", text="N=3 replicates", x=0.3, y=0.8, box=True),
                       gf.Annotation(kind="arrow", text="high", x=0.5, y=0.9, x2=0.8, y2=0.7, line_width=1.5),
                       gf.Annotation(kind="line", x=0.1, y=0.1, x2=0.9, y2=0.1),
                       gf.Annotation(kind="bracket", text="**", x=0.2, y=0.95, x2=0.6, y2=0.95)]
    _redraw(view)
    text, arrow, line, bracket = fmt.annotations
    shown = _tagged(view, ("annotation", text.id))[0]
    assert shown.get_text() == "N=3 replicates" and shown.get_bbox_patch() is not None
    assert view.figure._cq_plot.inverted().transform(shown.get_transform().transform(shown.get_position())) == pytest.approx([0.3, 0.8])
    assert any(isinstance(artist, FancyArrowPatch) for artist in _tagged(view, ("annotation", arrow.id)))
    assert len(_tagged(view, ("annotation", line.id))) == 1 and len(_tagged(view, ("annotation", bracket.id))) == 2


def test_significance_bracket_format(window):
    data = _data(window)
    result = window.analyze("compare", {"test": "welch_t", "first": 0, "second": 1, "alternative": "two-sided", "ci": 0.95, "alpha": 0.05,
                                        "style": "GP"})
    graph, view = _graph(window)
    graph.result_id = result.id
    graph.format.update(bracket_width=2.2, star_size=14)
    _redraw(view)
    marks = _tagged(view, ("bracket",))
    line = [artist for artist in marks if artist.__class__.__name__ == "Line2D"][0]
    stars = [artist for artist in marks if artist.__class__.__name__ == "Text"][0]
    assert line.get_linewidth() == 2.2 and stars.get_fontsize() == 14 and set(stars.get_text()) == {"*"}
    lift_before = line.get_ydata()[0]
    graph.format.update(bracket_gap=0.2, bracket="p")
    _redraw(view)
    marks = _tagged(view, ("bracket",))
    assert [artist for artist in marks if artist.__class__.__name__ == "Line2D"][0].get_ydata()[0] > lift_before
    assert any(re.search(r"\d", artist.get_text()) for artist in marks if artist.__class__.__name__ == "Text")
    assert data is not None


def test_same_reference_colours_dash_the_second(window):
    _data(window)
    result = window.analyze("anova", {"test": "kruskal_wallis", "comparisons": "control", "method": "dunn", "control": [NAMES[0], NAMES[1]],
                                      "family": "per_reference", "alpha": 0.05, "ci": 0.95, "style": "GP", "geisser_greenhouse": True})
    graph, view = _graph(window)
    graph.result_id = result.id
    graph.format.update(ref_colors=["#000000", "#000000"])
    _redraw(view)
    lines = [artist for artist in _tagged(view, ("bracket",)) if artist.__class__.__name__ == "Line2D"]
    assert {line.get_linestyle() for line in lines} == {"-", "--"}
    graph.format.update(ref_colors=["#000000", "#cc2222"])
    _redraw(view)
    lines = [artist for artist in _tagged(view, ("bracket",)) if artist.__class__.__name__ == "Line2D"]
    assert {line.get_linestyle() for line in lines} == {"-"}


# --- Click to edit -------------------------------------------------------------------------------------------------


def _point(view, artist):
    offsets = np.asarray(artist.get_offsets())
    return artist.axes.transData.transform(offsets[0])


def _press(view, x, y, dblclick=False):
    MouseEvent("button_press_event", view.canvas, x, y, button=1, dblclick=dblclick)._process()


def test_pick_selects_highlights_and_opens_the_section(window):
    view = _graph(window)[1]
    view.canvas.draw()
    dots = _collections(view, NAMES[1])[0]
    # A synthesized pick event, as matplotlib sends when the dots are clicked.
    mouse = MouseEvent("button_press_event", view.canvas, *_point(view, dots), button=1)
    PickEvent("pick_event", view.canvas, mouse, dots)._process()
    assert view.selection == ("dataset", NAMES[1])
    assert view.collapsibles["datasets"].expanded() and view.panel["datasets"].current() == NAMES[1]
    assert len(view._highlights) == 1 and view._highlights[0] in view.figure.artists
    outline, plot_box = view._highlights[0], view.figure.axes[0].bbox
    assert outline.get_width() < plot_box.width / 2  # around that column's dots and bars, not the whole data area
    # A real click on a dot of another data set goes through matplotlib's own picking.
    view.canvas.draw()
    _press(view, *_point(view, _collections(view, NAMES[2])[0]))
    assert view.selection == ("dataset", NAMES[2]) and view.panel["datasets"].current() == NAMES[2]
    # The selection is a tag, so it survives a redraw: the outline is drawn again around the new artists.
    view.sheet.format.font_size = 10
    view.draw()
    assert view.selection == ("dataset", NAMES[2]) and len(view._highlights) == 1
    # The axis numbers, the title and empty space.
    plot = view.figure._cq_plot
    x0, y0 = plot.transform((0, 0))
    _press(view, x0 - 12, y0 + 30)
    assert view.selection == ("axis", "y") and view.collapsibles["axes"].expanded()
    assert view.panel["axes"].axis.axis_key() == "y"
    _press(view, x0 + 40, y0 - 10)
    assert view.selection == ("axis", "x") and view.panel["axes"].axis.axis_key() == "x"
    view.sheet.format.title = "A title"
    view.draw()
    view.canvas.draw()
    title = _tagged(view, ("title",))[0]
    box = title.get_window_extent(view.canvas.get_renderer())
    _press(view, box.x0 + 3, (box.y0 + box.y1) / 2)
    assert view.selection == ("title",) and view.collapsibles["text"].expanded()
    x1, y1 = plot.transform((1, 1))
    _press(view, x1 - 2, y1 - 2)  # inside the plot, on nothing
    assert view.selection is None and view._highlights == []


def test_priority_and_double_click_opens_the_dialog(window):
    graph, view = _graph(window)
    graph.format.annotations.append(gf.Annotation(kind="text", text="note", x=0.5, y=0.5))
    view.draw()
    view.canvas.draw()
    note = _tagged(view, ("annotation", graph.format.annotations[0].id))[0]
    dots = _collections(view, NAMES[0])[0]
    mouse = MouseEvent("button_press_event", view.canvas, 10, 10, button=1)
    PickEvent("pick_event", view.canvas, mouse, note)._process()
    PickEvent("pick_event", view.canvas, mouse, dots)._process()  # behind the text box: the text box stays selected
    assert view.selection[0] == "annotation"
    double = MouseEvent("button_press_event", view.canvas, 10, 10, button=1, dblclick=True)
    PickEvent("pick_event", view.canvas, double, dots)._process()
    double._process()
    dialog = view.dialogs["graph"]
    assert dialog.isVisible() and dialog.current() is dialog.pages["datasets"]
    assert dialog.pages["datasets"].current() == NAMES[0]
    # The dialog edits live; Cancel puts the format back.
    dialog.pages["datasets"].symbol_size.setValue(77)
    view.flush()
    assert _collections(view, NAMES[0])[0].get_sizes()[0] == 77 and view.panel["datasets"].symbol_size.value() == 77
    dialog.reject()
    view.flush()
    assert NAMES[0] not in graph.format.datasets and "graph" not in view.dialogs
    axes = view.format_dialog("axes", "right")
    assert axes.current() is axes.pages["right"] and axes.windowTitle() == "Format axes"
    axes.accept()


def test_dragging_an_annotation_and_the_legend_saves_the_position(window):
    graph, view = _graph(window)
    fmt = graph.format
    note = gf.Annotation(kind="text", text="Drag me", x=0.4, y=0.6)
    arrow = gf.Annotation(kind="arrow", x=0.2, y=0.3, x2=0.3, y2=0.2)
    fmt.annotations += [note, arrow]
    fmt.legend.show = True
    view.draw()
    view.canvas.draw()
    plot = view.figure._cq_plot
    x, y = plot.transform((0.4, 0.6))
    width, height = plot.transform((1, 1)) - plot.transform((0, 0))
    _press(view, x, y)
    assert view.selection == ("annotation", note.id)
    MouseEvent("motion_notify_event", view.canvas, x + 20, y - 10)._process()
    MouseEvent("motion_notify_event", view.canvas, x + 40, y - 20)._process()
    MouseEvent("button_release_event", view.canvas, x + 40, y - 20, button=1)._process()
    assert note.x == pytest.approx(0.4 + 40 / width, abs=1e-3) and note.y == pytest.approx(0.6 - 20 / height, abs=1e-3)
    moved = _tagged(view, ("annotation", note.id))[0]
    assert plot.inverted().transform(moved.get_transform().transform(moved.get_position())) == pytest.approx([note.x, note.y])
    assert view.selection == ("annotation", note.id) and view._highlights
    # An arrow moves both of its ends.
    view.canvas.draw()
    start = np.asarray(plot.transform((0.25, 0.25)))
    _press(view, *start)
    assert view.selection == ("annotation", arrow.id)
    MouseEvent("motion_notify_event", view.canvas, start[0] + 30, start[1])._process()
    MouseEvent("button_release_event", view.canvas, start[0] + 30, start[1], button=1)._process()
    assert arrow.x == pytest.approx(0.2 + 30 / width, abs=1e-3) and arrow.x2 == pytest.approx(0.3 + 30 / width, abs=1e-3)
    # The legend.
    view.canvas.draw()
    legend = view.figure._cq_legend
    box = legend.get_window_extent(view.canvas.get_renderer())
    _press(view, box.x0 + 4, box.y1 - 4)
    assert view.selection == ("legend",)
    MouseEvent("motion_notify_event", view.canvas, box.x0 - 26, box.y1 - 34)._process()
    MouseEvent("button_release_event", view.canvas, box.x0 - 26, box.y1 - 34, button=1)._process()
    assert fmt.legend.position == "custom"
    corner = plot.inverted().transform((box.x0, box.y1))
    assert fmt.legend.xy == pytest.approx([corner[0] - 30 / width, corner[1] - 30 / height], abs=2e-3)
    # A click without moving changes nothing; Delete removes a selected text box.
    before = (note.x, note.y)
    x, y = plot.transform((note.x, note.y))
    view.canvas.draw()
    _press(view, x, y)
    MouseEvent("button_release_event", view.canvas, x, y, button=1)._process()
    assert (note.x, note.y) == before
    from matplotlib.backend_bases import KeyEvent

    KeyEvent("key_press_event", view.canvas, "delete", x, y)._process()
    assert note not in fmt.annotations


def test_annotation_section_adds_edits_and_deletes(window):
    graph, view = _graph(window)
    section = view.panel["annotations"]
    for kind in ("text", "arrow", "line", "bracket"):
        section.add_buttons[kind].click()
    view.flush()
    assert [note.kind for note in graph.format.annotations] == ["text", "arrow", "line", "bracket"]
    assert view.selection == ("annotation", graph.format.annotations[-1].id)
    section.list.setCurrentRow(0)
    section.text.setText(r"\mu m^2")
    section.text.editingFinished.emit()
    section.note_style.bold.setChecked(True)
    view.flush()
    shown = _tagged(view, ("annotation", graph.format.annotations[0].id))[0]
    assert shown.get_text() == "μ m²" and shown.get_fontweight() == "bold"
    section.delete.click()
    assert len(graph.format.annotations) == 3


# --- Styles ----------------------------------------------------------------------------------------------------------


def test_builtin_styles(window):
    graph, view = _graph(window)
    assert set(gf.BUILTIN_STYLES) >= {"Editorial (default)", "Classic Prism",
                                      "Colour-blind safe"}
    graph.format.title = "Kept"
    view.apply_named_style("Classic Prism")
    view.flush()
    fmt = graph.format
    assert fmt.frame == "offset" and fmt.title == "Kept" and fmt.y.title_style.bold
    ax = view.figure.axes[0]
    assert ax.spines["left"].get_position() == ("outward", 6.0)
    assert to_hex(_collections(view, NAMES[1])[0].get_facecolor()[0]) == "#000000" and ax.yaxis.label.get_fontweight() == "bold"
    view.apply_named_style("Colour-blind safe")
    view.flush()
    assert fmt.frame == "plain" and fmt.palette == gf.COLORBLIND_PALETTE
    assert to_hex(_collections(view, NAMES[0])[0].get_facecolor()[0]) == gf.COLORBLIND_PALETTE[0].lower()
    view.apply_named_style("Editorial (default)")
    assert fmt.palette == gf.PALETTE and not fmt.y.title_style.bold


def test_save_apply_and_magic(window, tmp_path):
    first, view = _graph(window, "bar")
    first.format.frame = "box"
    first.format.datasets[NAMES[0]] = gf.DataSetFormat(hatch="xxx", bar_fill="#ffffff")
    first.format.colors[NAMES[1]] = "#ff0000"
    first.format.y.min = 5
    style = view.save_style("Lab figure")
    path = gf.styles_path()
    assert path.is_file() and path.parent == tmp_path / "local" / "CellQuant"
    assert gf.load_styles()["Lab figure"] == json.loads(json.dumps(style))
    assert "range" not in style["groups"] and "titles" not in style["groups"]
    styles = view.panel["styles"]
    assert styles.choice.findData("Lab figure") >= 0 and styles.delete.isEnabled()
    second, other = _graph(window, "bar")
    other.apply_named_style("Lab figure")
    assert second.format.frame == "box" and second.format.datasets[NAMES[0]].hatch == "xxx" and second.format.colors[NAMES[1]] == "#ff0000"
    assert second.format.y.min is None  # ranges are not part of a saved style
    # Magic: only the chosen groups, onto this graph and the ticked others.
    third, last = _graph(window, "scatter")
    first.format.font_size, first.format.title = 11, "Copied title"
    dialog = last.magic_dialog()
    dialog.source.setCurrentIndex(dialog.source.findData(f"graph:{first.id}"))
    for key, box in dialog.groups.items():
        box.setChecked(key in ("fonts", "titles"))
    for row in range(dialog.targets.count()):
        item = dialog.targets.item(row)
        item.setCheckState(format_panel.Qt.Checked if item.data(format_panel.Qt.UserRole) == second.id else format_panel.Qt.Unchecked)
    dialog.accept()
    assert third.format.font_size == 11 and third.format.title == "Copied title" and third.format.frame == "plain"
    assert second.format.font_size == 11 and second.format.title == "Copied title"
    assert all(box.toolTip() for box in dialog.groups.values()) and dialog.source.toolTip()
    gf.delete_style("Lab figure")
    assert "Lab figure" not in gf.load_styles()


def test_copy_and_paste_format(window):
    first, view = _graph(window)
    first.format.frame, first.format.title = "offset", "Only here"
    first.format.y.major = 20
    first.format.annotations.append(gf.Annotation(text="mine"))
    text = view.copy_format()
    assert text.startswith(gf.CLIPBOARD_PREFIX) and format_panel.clipboard().text() == text
    second, other = _graph(window)
    assert other.paste_format()
    other.flush()
    assert second.format.frame == "offset" and second.format.y.major == 20
    assert second.format.title == "" and second.format.annotations == []  # the words and text boxes stay on their own graph
    format_panel.clipboard().setText("something else")
    assert other.paste_format()  # the window keeps the last copied format too


def test_styles_file_survives_odd_content(window, tmp_path):
    path = gf.styles_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json", encoding="utf-8")
    assert gf.load_styles() == {}
    path.write_text(json.dumps({"styles": {"Odd": {"groups": "colors,frame", "values": {"palette": "#111111,#222222", "frame": "box"}}}}))
    fmt = gf.GraphFormat()
    style = gf.load_styles()["Odd"]
    gf.apply_style(fmt, style, NAMES, style["groups"].split(","))
    assert fmt.frame == "box" and fmt.palette == ["#111111", "#222222"]


# --- The panel ---------------------------------------------------------------------------------------------------------


def test_panel_sections_have_tooltips_and_fit(window):
    view = _graph(window)[1]
    from qtpy.QtWidgets import QAbstractButton, QAbstractSpinBox, QComboBox, QLineEdit

    for key, box in view.collapsibles.items():
        box.expand()
        assert box.header.toolTip()
        for widget in box.body.findChildren((QComboBox, QAbstractSpinBox, QLineEdit, QAbstractButton)):
            if widget.isVisibleTo(box.body) and not isinstance(widget.parent(), (QComboBox, QAbstractSpinBox)):
                assert widget.toolTip() or widget.parent().toolTip(), (key, widget)
        assert box.minimumSizeHint().width() <= view.panel_scroll.width() - 14, key  # no section wider than the panel
    buttons = view.panel["significance"].ref_buttons
    assert buttons[1].text() == "2nd" and buttons[1].width() >= buttons[1].fontMetrics().horizontalAdvance("2nd") + 20
    assert tips.FORMAT_GRAPH and tips.FORMAT_AXES and all(tips.FORMAT.values())


# --- Export ------------------------------------------------------------------------------------------------------------


def test_export_matches_the_format_size_and_screen(window, tmp_path):
    from PIL import Image

    graph, view = _graph(window, "bar", width=80, height=60, units="mm")
    fmt = graph.format
    fmt.y.breaks = [[45, 95]]
    fmt.annotations.append(gf.Annotation(kind="arrow", text="note", x=0.3, y=0.8, x2=0.5, y2=0.6))
    fmt.legend.show = True
    view.draw()
    for dpi in (300, 600, 1200):
        written = view.export(tmp_path / f"g{dpi}.tif", "tiff", dpi)
        with Image.open(written) as image:
            assert image.size == (int(80 / 25.4 * dpi), int(60 / 25.4 * dpi))  # whole pixels: within one pixel of 80 × 60 mm
            assert image.info.get("compression") == "tiff_lzw" and round(image.info["dpi"][0]) == dpi
    with Image.open(view.export(tmp_path / "g.png", "png", 300, 2, 1.5, "in")) as image:
        assert image.size == (600, 450)
    with Image.open(view.export(tmp_path / "g.jpg", "jpeg", 300)) as image:
        assert image.size == (int(80 / 25.4 * 300), int(60 / 25.4 * 300))
    svg = view.export(tmp_path / "g.svg", "svg").read_text()
    width_pt = float(re.search(r'<svg[^>]*width="([\d.]+)pt"', svg).group(1))
    assert width_pt == pytest.approx(80 / 25.4 * 72, abs=0.1)
    pdf = view.export(tmp_path / "g.pdf", "pdf").read_bytes()
    box = [float(value) for value in re.search(rb"/MediaBox \[ *([\d. ]+)\]", pdf).group(1).split()]
    assert box[2] == pytest.approx(80 / 25.4 * 72, abs=0.1) and box[3] == pytest.approx(60 / 25.4 * 72, abs=0.1)
    eps = view.export(tmp_path / "g.eps", "eps").read_text(errors="ignore")
    assert "%%BoundingBox" in eps
    # WYSIWYG: the exported figure has the screen's layout (axes, legend, annotations at the same fractions of the page).
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    exported = Figure()
    FigureCanvasAgg(exported)
    graphs.draw(exported, window.project.data[graph.data_id], graph, None, None)
    screen = [ax.get_position().bounds for ax in view.figure._cq_axes["segments"]]
    paper = [ax.get_position().bounds for ax in exported._cq_axes["segments"]]
    assert np.allclose(screen, paper, atol=0.01)
    assert exported.get_size_inches() == pytest.approx(view.figure.get_size_inches())
