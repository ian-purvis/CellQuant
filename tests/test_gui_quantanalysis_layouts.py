"""QuantAnalysis page layouts: several graphs on one page, saved in projects and exported.
Needs Qt (xvfb-run on Linux) and matplotlib."""

from __future__ import annotations

import re
import xml.dom.minidom

import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("matplotlib")

from cellquant.errors import CellQuantError  # noqa: E402
from cellquant.stats import ColumnTable, sources  # noqa: E402
from tests.test_stats_import import make_results  # noqa: E402


def _project():
    from cellquant.gui.quantanalysis.project import DataSheet, GraphSheet, LayoutSheet, Project

    project = Project()
    data = project.add_data(DataSheet("D", ColumnTable.from_columns({"A": [1.0, 2.0, 3.0, 4.0], "B": [3.0, 4.0, 5.0, 7.0]})))
    graph_sheets = [project.add_graph(GraphSheet(f"G{kind}", data.id, kind)) for kind in ("scatter", "bar", "box")]
    layout = project.add_layout(LayoutSheet("L", {"rows": 2, "columns": 2, "title": "Figure 1"},
                                            [graph_sheets[0].id, None, graph_sheets[1].id, graph_sheets[2].id]))
    return project, data, graph_sheets, layout


def test_page_geometry_and_panel_letters():
    from cellquant.gui.quantanalysis import layouts

    project, _data, graph_sheets, layout = _project()
    assert layouts.page_size({"paper": "a4", "orientation": "landscape"}) == (297.0, 210.0)
    assert layouts.page_size({"paper": "custom", "width": 85, "height": 100}) == (85.0, 100.0)
    boxes = layouts.cells({"rows": 2, "columns": 2, "margin": 10, "gap": 5, "paper": "custom", "width": 105, "height": 105})
    assert boxes[0] == pytest.approx((10, 10, 40, 40)) and boxes[3] == pytest.approx((55, 55, 40, 40))
    places = layouts.placements(layout, project)
    assert [place["label"] for place in places] == ["A", "B", "C"]  # the empty cell gets no letter
    assert [place["cell"] for place in places] == [0, 2, 3] and all(place["scale"] == 1.0 for place in places)
    x, y, _w, _h = places[1]["box"]
    assert (x, y) == pytest.approx((places[1]["label_at"][0], places[1]["label_at"][1] + 12 * 1.3 * 25.4 / 72))
    layout.page["labels"] = "lower"
    assert [place["label"] for place in layouts.placements(layout, project)] == ["a", "b", "c"]

    # A graph larger than its cell is shrunk to fit, and the notes say so.
    graph_sheets[0].format.width = 400
    places = layouts.placements(layout, project)
    assert places[0]["scale"] < 1 and places[0]["box"][2] <= layouts.cells(layout.page)[0][2] + 1e-6
    assert "shrunk" in layouts.notes(layout, project)[0]
    with pytest.raises(CellQuantError, match="no room"):
        layouts.cells({"paper": "custom", "width": 30, "height": 30, "margin": 20})


def test_export_keeps_graphs_vector_and_sizes_raster(tmp_path):
    from PIL import Image

    from cellquant.gui.quantanalysis import layouts

    project, *_rest, layout = _project()
    svg = layouts.export(layout, project, tmp_path / "page.svg", "svg").read_text(encoding="utf-8")
    xml.dom.minidom.parseString(svg.encode("utf-8"))  # one well-formed document
    ids = re.findall(r'\bid="([^"]+)"', svg)
    assert len(ids) == len(set(ids)) and svg.count("<svg") == 4  # the page and three graphs
    for reference in re.findall(r'(?:url\(#|href="#)([^)"]+)', svg):
        assert reference in ids
    image = Image.open(layouts.export(layout, project, tmp_path / "page.tif", "tiff", 300))
    assert image.size == (2550, 3300) and round(image.info["dpi"][0]) == 300  # US Letter at 300 dpi
    assert layouts.render(layout, project, 50).shape == (550, 425, 3)
    with pytest.raises(CellQuantError, match="export as"):
        layouts.export(layout, project, tmp_path / "page.pdf", "pdf")


def test_removing_a_graph_empties_its_cell_and_projects_keep_layouts(tmp_path):
    from cellquant.gui.quantanalysis import project_file
    from cellquant.gui.quantanalysis.project import Project

    project, data, graph_sheets, layout = _project()
    path = project_file.save(project, tmp_path / "p.cqstats", current=layout.id)
    other = Project()
    current, notes = project_file.load(path, other)
    assert notes == [] and current == layout.id
    assert other.layouts[layout.id].graphs == layout.graphs and other.layouts[layout.id].page["title"] == "Figure 1"
    assert project_file.fingerprint(other) == project_file.fingerprint(project)

    project.remove(graph_sheets[1].id)
    assert layout.graphs == [graph_sheets[0].id, None, None, graph_sheets[2].id]
    project.remove(data.id)  # its graphs go, and the layout stays, empty
    assert layout.id in project.layouts and layout.graphs == [None, None, None, None]
    project.remove(layout.id)
    assert not project.layouts


def test_damaged_layouts_in_a_project_file_are_skipped_or_repaired(tmp_path):
    import json
    import zipfile

    from cellquant.gui.quantanalysis import layouts, project_file
    from cellquant.gui.quantanalysis.project import Project

    project, _data, graph_sheets, layout = _project()
    path = project_file.save(project, tmp_path / "p.cqstats")
    content = json.loads(zipfile.ZipFile(path).read("project.json"))
    good = content["layouts"][0]
    for value, kept in ((5, 0), (None, 0), ([3, "text", None], 0), ([{"name": "no id"}, {"id": ["x"], "page": 7}], 1),
                        ([dict(good, page={"rows": "a", "columns": None, "margin": "wide", "paper": ["x"], "gap": float("inf")},
                               graphs=[["x"], graph_sheets[0].id, "gone"])], 1)):
        content["layouts"] = value
        damaged = tmp_path / "damaged.cqstats"
        with zipfile.ZipFile(damaged, "w") as archive:
            archive.writestr("project.json", json.dumps(content))
        other = Project()
        project_file.load(damaged, other)
        assert len(other.layouts) == kept, value
    loaded = next(iter(other.layouts.values()))
    assert loaded.graphs == [None, graph_sheets[0].id, None]
    page = layouts.page_options(loaded.page)
    assert (page["rows"], page["columns"], page["margin"], page["paper"]) == (2, 2, 12.0, "letter") and page["gap"] == 2000.0
    with pytest.raises(CellQuantError, match="no room"):
        layouts.cells(loaded.page)  # an enormous gap: the page says so instead of failing
    assert layouts.page_options({"rows": float("nan"), "title_size": float("nan")})["title_size"] == 14.0


@pytest.fixture
def window(qtbot):
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    window = QuantAnalysisWindow()
    qtbot.addWidget(window)
    window.resize(1400, 900)
    window.show()
    return window


def test_layout_sheet_follows_its_graphs(window, qtbot, tmp_path):
    from cellquant.gui.quantanalysis import layouts
    from cellquant.gui.quantanalysis.layout_view import LayoutSheetView

    data = window.open_folder(make_results(tmp_path / "results"),
                              sources.ImportSpec("report_1_percent", dot="unit", unit="Folder 2", group_by="Folder 1"), accept=True)
    window.select(data.id)
    scatter = window.new_graph("scatter")
    window.select(data.id)
    bar = window.new_graph("bar")
    window.select(bar.id)
    layout = window.new_layout()
    assert layout.graphs == [bar.id, scatter.id] and (layout.page["rows"], layout.page["columns"]) == (1, 2)
    assert window.folders["layouts"].childCount() == 1
    view = window.stack.currentWidget()
    assert isinstance(view, LayoutSheetView)
    view.draw()
    assert view.preview.pixmap() is not None and not view.preview.pixmap().isNull()
    assert [box.currentData() for box in view.cell_boxes] == [bar.id, scatter.id]

    # More rows keep each graph where it was; choosing a graph for a cell is saved at once.
    view.rows.setValue(2)
    assert layout.graphs == [bar.id, scatter.id, None, None] and len(view.cell_boxes) == 4
    view.cell_boxes[3].setCurrentIndex(view.cell_boxes[3].findData(bar.id))
    assert layout.graphs[3] == bar.id
    view.columns.setValue(1)  # graphs in column 2 move to empty cells; with none left, they wait for the grid to grow
    assert layout.graphs == [bar.id, scatter.id, bar.id] and "outside the grid" in layouts.notes(layout, window.project)[-1]
    assert window.has_unsaved_changes()

    # A cell click picks its graph box; clicking the margin picks nothing.
    view.draw()
    left, top, _w, _h = layouts.cells(layout.page)[0]
    assert view.cell_at(left * view._scale + 2, top * view._scale + 2) == 0 and view.cell_at(1, 1) is None

    # Editing the data redraws the page (it follows the graphs' data).
    drawn = []
    view.draw = lambda: drawn.append(1) or []
    view._redraw.timeout.disconnect()
    view._redraw.timeout.connect(view.draw)
    window.select(data.id)
    window.select(layout.id)
    qtbot.waitUntil(lambda: bool(drawn), timeout=2000)
    drawn.clear()
    values = [column.values.copy() for column in data.table.columns]
    values[0] = values[0] * 2
    data.table = ColumnTable.from_columns({column.name: value for column, value in zip(data.table.columns, values)})
    window.project.data_changed(data.id)
    qtbot.waitUntil(lambda: bool(drawn), timeout=2000)

    written = view.export(tmp_path / "figure.png", "png", 300)
    assert written.exists() and "Saved" in window.statusBar().currentMessage()
    menu = window.menuBar().actions()[2].menu()
    assert "New page layout" in [action.text() for action in menu.actions()]
    window.project.remove(scatter.id)
    assert layout.graphs == [bar.id, None, bar.id]
