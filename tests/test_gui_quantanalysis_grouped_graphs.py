"""QuantAnalysis graphs of grouped tables: interleaved and separated blocks, colours by data set, two-way ANOVA brackets.
Needs matplotlib; the window test needs Qt (xvfb-run on Linux)."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("matplotlib")

from cellquant.stats import ColumnTable  # noqa: E402
from cellquant.stats.grouped import GroupedTable, two_way_anova  # noqa: E402
from tests.test_stats_grouped import BALANCED  # noqa: E402


def _sheet(cells=BALANCED):
    from cellquant.gui.quantanalysis.project import DataSheet, set_grouped

    sheet = DataSheet("D", None)
    set_grouped(sheet, GroupedTable.from_cells(cells, title="Response"))
    return sheet


def _draw(sheet, kind="bar", fmt=None, result=None):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    from cellquant.gui.quantanalysis import graphs
    from cellquant.gui.quantanalysis.project import GraphSheet

    figure = Figure()
    FigureCanvasAgg(figure)
    notes = graphs.draw(figure, sheet, GraphSheet("G", sheet.id, kind, fmt or {}), result)
    figure.canvas.draw()
    return figure, notes


def _brackets(figure) -> list[str]:
    ax = figure._cq_axes["main"]
    return [text.get_text() for text in ax.texts if getattr(text, "_cq_target", None) == ("bracket",)]


def test_blocks_keep_their_places():
    from cellquant.gui.quantanalysis import graphs
    from cellquant.gui.quantanalysis.graph_format import GraphFormat

    table = GroupedTable.from_cells(BALANCED)
    places, blocks = graphs.grouped_places(table, GraphFormat(cluster_gap=1.0))
    assert [x for x, _key, _shade in places] == [0, 1, 2, 4, 5, 6]  # Control: WT KO1 KO2, gap, Treated: WT KO1 KO2
    assert [key for _x, key, _shade in places[:3]] == ["WT", "KO1", "KO2"] and blocks == [("Control", 1.0), ("Treated", 5.0)]
    places, blocks = graphs.grouped_places(table, GraphFormat(grouped_layout="separated", cluster_gap=0.5))
    assert [x for x, _key, _shade in places] == [0, 2.5, 5.0, 1, 3.5, 6.0]  # WT: Control Treated, KO1: …, KO2: …
    assert blocks == [("WT", 0.5), ("KO1", 3.0), ("KO2", 5.5)]


def test_interleaved_bars_are_coloured_by_data_set_with_brackets_within_rows():
    sheet = _sheet()
    result = two_way_anova(sheet.grouped, compare="within_rows")
    figure, notes = _draw(sheet, "bar", {"legend": {"show": True, "position": "master"}}, result)
    ax = figure._cq_axes["main"]
    assert notes == []
    assert [label.get_text() for label in ax.get_xticklabels()] == ["Control", "Treated"]
    bars = [patch for patch in ax.patches if getattr(patch, "_cq_target", (None,))[0] == "dataset"]
    assert [patch._cq_target[1] for patch in bars] == ["WT", "KO1", "KO2"] * 2
    assert [tuple(patch.get_facecolor()) for patch in bars[:3]] == [tuple(patch.get_facecolor()) for patch in bars[3:]]
    assert [text.get_text() for text in figure._cq_legend.get_texts()] == ["WT", "KO1", "KO2"]
    # The legend in the row above the plot fits on the page: the layout makes room for it.
    assert figure._cq_legend.get_window_extent().y1 <= figure.bbox.y1 + 0.5
    expected = [row["summary"] for row in result.values["comparisons"]]
    assert sorted(_brackets(figure)) == sorted(expected) and len(expected) == 6
    # Against a reference: one colour per reference name, not one per cell.
    control = two_way_anova(sheet.grouped, compare="within_rows", goal="control", control="WT")
    figure, _notes = _draw(sheet, "box", {"bracket_mode": "columns"}, control)
    assert len(_brackets(figure)) == 4


def test_separated_blocks_main_effects_and_fallbacks():
    sheet = _sheet()
    within = two_way_anova(sheet.grouped, compare="within_columns")
    figure, notes = _draw(sheet, "scatter", {"grouped_layout": "separated"}, within)
    ax = figure._cq_axes["main"]
    assert [label.get_text() for label in ax.get_xticklabels()] == ["Control", "Treated"] * 3
    assert [text.get_text() for text in ax.texts if getattr(text, "_cq_target", None) == ("axis", "x")] == ["WT", "KO1", "KO2"]
    assert len(_brackets(figure)) == 3 and notes == []

    columns = two_way_anova(sheet.grouped, compare="column_means")
    figure, notes = _draw(sheet, "bar", {"grouped_layout": "separated"}, columns)
    assert len(_brackets(figure)) == 3 and notes == []
    figure, notes = _draw(sheet, "bar", {}, columns)  # data set means are not blocks when interleaved
    assert _brackets(figure) == [] and "Separated" in notes[0]
    figure, notes = _draw(sheet, "bar", {"letters": True}, two_way_anova(sheet.grouped))
    assert "no multiple comparisons" in notes[0]

    figure, notes = _draw(sheet, "estimation")
    assert "column scatter" in notes[0]
    figure, _notes = _draw(sheet, "scatter", {"connect_means": True})
    connect = [line for line in figure._cq_axes["main"].lines if getattr(line, "_cq_target", None) == ("connect",)]
    assert len(connect) == 3 and all(len(line.get_xdata()) == 2 for line in connect)  # each data set across the rows

    holed = dict(BALANCED)
    holed[("Treated", "KO1")] = []
    figure, notes = _draw(_sheet(holed), "bar")
    assert "gaps" in notes[0]
    ax = figure._cq_axes["main"]
    assert len([patch for patch in ax.patches if getattr(patch, "_cq_target", (None,))[0] == "dataset"]) == 5
    assert ax.get_xlim()[1] > 6  # the empty cell keeps its place


def test_column_graph_legend_outside_fits_the_page():
    from cellquant.gui.quantanalysis.project import DataSheet

    sheet = DataSheet("C", ColumnTable.from_columns({"A": [1.0, 2.0, 3.0], "B": [2.0, 3.0, 4.0]}))
    for position in ("master", "right"):
        figure, _notes = _draw(sheet, "bar", {"legend": {"show": True, "position": position}})
        box = figure._cq_legend.get_window_extent()
        assert box.x1 <= figure.bbox.x1 + 0.5 and box.y1 <= figure.bbox.y1 + 0.5


def test_window_draws_grouped_graphs_and_layouts(qtbot, tmp_path):
    pytest.importorskip("pytestqt")
    from cellquant.gui.quantanalysis import layouts
    from cellquant.gui.quantanalysis.graph_view import GraphSheetView
    from cellquant.gui.quantanalysis.project import set_grouped
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    window = QuantAnalysisWindow()
    qtbot.addWidget(window)
    window.show()
    sheet = window.new_grouped_table()
    set_grouped(sheet, GroupedTable.from_cells(BALANCED, title="Response"))
    window.project.data_changed(sheet.id)
    window.suggested_analysis(sheet)
    result = next(item for item in window.project.results.values() if item.kind == "twoway")
    graph = next(item for item in window.project.graphs.values() if item.data_id == sheet.id)
    assert graph.kind == "bar" and graph.result_id == result.id and graph.format.legend.show
    window.select(graph.id)
    view = window.stack.currentWidget()
    assert isinstance(view, GraphSheetView)
    section = view.panel["graph"]
    assert section.arrangement.isVisibleTo(view) and section.comparison.currentData() == result.id
    assert view.panel["datasets"].names() == ["WT", "KO1", "KO2"]  # formats and colours follow the data sets
    section.arrangement.setCurrentIndex(section.arrangement.findData("separated"))
    assert graph.format.grouped_layout == "separated"
    view.flush()
    assert len(_brackets(view.figure)) == 6

    window.select(sheet.id)
    other = window.new_graph("scatter")
    assert other.result_id == result.id and other.format.legend.position == "master"
    layout = window.new_layout()
    svg = layouts.export(layout, window.project, tmp_path / "page.svg", "svg").read_text(encoding="utf-8")
    assert svg.count("<svg") == 3
    window.new_table()
    column_graph = window.new_graph("scatter")
    column_view = window.stack.currentWidget()
    assert column_graph.result_id is None and not column_view.panel["graph"].arrangement.isVisibleTo(column_view)
    assert np.isfinite(result.result.values["p_columns"])


def test_comparisons_that_no_longer_match_are_counted():
    from cellquant.gui.quantanalysis.graph_format import GraphFormat

    sheet = _sheet()
    result = two_way_anova(sheet.grouped, compare="within_rows")  # 6 comparisons, 3 per row
    renamed = dict(BALANCED)
    for column in ("WT", "KO1", "KO2"):
        renamed[("Untreated", column)] = renamed.pop(("Control", column))
    figure, notes = _draw(_sheet(renamed), "bar", {}, result)
    assert len(_brackets(figure)) == 3 and notes == ["3 of 6 comparisons are not shown: the table's rows or data sets changed since "
                                                     "the two-way ANOVA; run it again."]
    fewer = {key: value for key, value in BALANCED.items() if key[1] != "KO2"}
    figure, notes = _draw(_sheet(fewer), "bar", {}, result)
    assert len(_brackets(figure)) == 2 and notes[0].startswith("4 of 6")
    assert (GraphFormat.from_dict({"grouped_layout": ["x"], "cluster_gap": 1e9}).grouped_layout,
            GraphFormat.from_dict({"cluster_gap": 1e9}).cluster_gap, GraphFormat.from_dict({"cluster_gap": "wide"}).cluster_gap) == ("interleaved", 5.0, 0.8)
