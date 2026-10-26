"""QuantAnalysis project files (.cqstats): saving, opening, unsaved changes and recent projects.
Needs Qt (xvfb-run on Linux) and matplotlib."""

from __future__ import annotations

import json
import re
import zipfile

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("matplotlib")

from cellquant.errors import CellQuantError  # noqa: E402
from cellquant.stats import ColumnTable, sources  # noqa: E402
from tests.test_stats_import import make_results  # noqa: E402

WELCH = {"test": "welch_t", "first": 0, "second": 1, "alternative": "two-sided", "ci": 0.95, "alpha": 0.05, "style": "classic"}
ANOVA = {"test": "ordinary", "comparisons": "control", "method": "dunnett", "control": ["A"], "family": "per_reference",
         "alpha": 0.05, "style": "classic"}


def _project():
    from cellquant.gui.quantanalysis.project import DataSheet, GraphSheet, Project, ResultSheet

    project = Project()
    table = ColumnTable.from_columns({"A": [1.0, 2.0, 3.0, np.nan, 2.5], "B": [2.0, 3.5, 5.0, 4.0, 3.0], "C": [4.0, 5.0, 6.0, 30.0, 5.5]})
    units = pd.DataFrame({"group": ["A", "B"], "dot": ["r1", "r2"], "value": [1.0, np.nan]})
    images = pd.DataFrame({"group": ["A", "B"], "unit": ["r1", None], "image": ["x.nd2", "y.nd2"], "count": [3, 4], "ok": [True, False]})
    data = project.add_data(DataSheet("Data 1", table, row_labels=["", "", "", "", ""], origin="experiment", units=units, images=images,
                                      spec=sources.ImportSpec("report_1_percent", dot="unit", unit="Folder 2", order=["A", "B", "C"]),
                                      notes=["a note"], warnings=["a warning"], description="three groups"))
    results = [
        project.add_result(ResultSheet("Welch", data.id, "compare", dict(WELCH))),
        project.add_result(ResultSheet("ANOVA", data.id, "anova", dict(ANOVA))),
        project.add_result(ResultSheet("Column statistics", data.id, "colstats", {})),
        project.add_result(ResultSheet("Normality", data.id, "normality", {})),
        project.add_result(ResultSheet("Outliers", data.id, "outliers", {"method": "rout", "q": 0.01})),
        project.add_result(ResultSheet("Stack", "", "pstack", {"text": "a 0.01\nb 0.04\nc 0.2", "method": "bh", "q": 0.05})),
    ]
    cleaned = project.add_cleaned(results[4])
    graph = project.add_graph(GraphSheet("Scatter", data.id, "scatter", {"error": "sd"}, results[1].id))
    graph.format.title = "My title"
    graph.format.y.reverse = True
    return project, data, results, cleaned, graph


def test_round_trip_keeps_tables_analyses_and_graphs(tmp_path):
    from cellquant.gui.quantanalysis import project_file
    from cellquant.gui.quantanalysis.project import Project, next_id

    project, data, results, cleaned, graph = _project()
    path = project_file.save(project, tmp_path / "study", experiment_dir=tmp_path / "exp", current=graph.id)
    assert path.name == "study.cqstats" and zipfile.is_zipfile(path)
    content = json.loads(zipfile.ZipFile(path).read("project.json"))
    assert content["version"] == 1 and content["saved_with"].startswith("CellQuant ")
    assert "result" not in content["results"][0]  # analyses are saved as their options and computed again

    other = Project()
    current, notes = project_file.load(path, other, experiment_dir=tmp_path / "exp")
    assert notes == [] and current == graph.id
    assert project_file.fingerprint(other) == project_file.fingerprint(project)
    copy = other.data[data.id]
    assert copy.table.names == ["A", "B", "C"] and np.isnan(copy.table.columns[0].values[3])
    assert copy.spec == data.spec and copy.origin == "experiment" and copy.description == "three groups"
    pd.testing.assert_frame_equal(copy.images, data.images)  # text, whole numbers, true/false and blanks come back as they were
    pd.testing.assert_frame_equal(copy.units, data.units)
    assert other.data[cleaned.id].cleaned_from == data.id
    for result in results:
        loaded = other.results[result.id]
        assert loaded.error == result.error == ""
        if result.result is not None:
            for key in ("p", "statistic"):
                if key in result.result.values:
                    assert loaded.result.values[key] == pytest.approx(result.result.values[key])
        if result.wide is not None:
            pd.testing.assert_frame_equal(loaded.wide, result.wide)
    loaded = other.graphs[graph.id]
    assert loaded.format.title == "My title" and loaded.format.y.reverse and loaded.result_id == results[1].id
    def number(sheet_id: str) -> int:
        return int(re.search(r"(\d+)$", sheet_id).group(1))

    assert number(next_id("data")) > max(number(sheet_id) for sheet_id in [*other.data, *other.results, *other.graphs])


def test_tables_from_another_experiment_follow_its_folder(tmp_path):
    from cellquant.gui.quantanalysis import project_file
    from cellquant.gui.quantanalysis.project import Project

    project, data, *_ = _project()
    path = project_file.save(project, tmp_path / "study.cqstats", experiment_dir=tmp_path / "exp")
    other = Project()
    project_file.load(path, other, experiment_dir=tmp_path / "another")
    assert other.data[data.id].origin == str(tmp_path / "exp")
    project_file.load(path, other, experiment_dir=None)
    assert other.data[data.id].origin == str(tmp_path / "exp")


def test_files_that_are_not_projects_are_refused(tmp_path):
    from cellquant.gui.quantanalysis import project_file
    from cellquant.gui.quantanalysis.project import Project

    (tmp_path / "text.cqstats").write_text("hello")
    with pytest.raises(CellQuantError, match="not a QuantAnalysis project file"):
        project_file.read(tmp_path / "text.cqstats")
    with zipfile.ZipFile(tmp_path / "newer.cqstats", "w") as archive:
        archive.writestr("project.json", json.dumps({"format": project_file.FORMAT, "version": 99, "saved_with": "CellQuant 9"}))
    with pytest.raises(CellQuantError, match="newer CellQuant"):
        project_file.read(tmp_path / "newer.cqstats")
    with pytest.raises(CellQuantError, match="does not exist"):
        project_file.read(tmp_path / "missing.cqstats")
    (tmp_path / "file").write_text("")
    with pytest.raises(CellQuantError, match="could not be saved"):
        project_file.save(Project(), tmp_path / "file" / "inside.cqstats")
    assert project_file.default_name(tmp_path / "My: experiment") == "My_ experiment.cqstats"
    assert project_file.default_name(None) == "QuantAnalysis project.cqstats"


@pytest.fixture
def window(qtbot, monkeypatch):
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    recent: list[str] = []
    monkeypatch.setattr(QuantAnalysisWindow, "recent_projects", lambda self: list(recent))
    monkeypatch.setattr(QuantAnalysisWindow, "_set_recent", lambda self, paths: recent.__setitem__(slice(None), paths))
    window = QuantAnalysisWindow()
    qtbot.addWidget(window)
    window.show()
    return window


def test_save_open_and_unsaved_changes(window, qtbot, tmp_path, monkeypatch):
    from cellquant.gui.quantanalysis.project import GraphSheet
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    file_menu = window.menuBar().actions()[0].menu()
    assert [action.text() for action in file_menu.actions()][:5] == ["New project", "Open project…", "Open recent", "Save project",
                                                                     "Save project as…"]
    assert not window.has_unsaved_changes()
    data = window.open_folder(make_results(tmp_path / "results"),
                              sources.ImportSpec("report_1_percent", dot="unit", unit="Folder 2", group_by="Folder 1"), accept=True)
    result = window.analyze("compare", WELCH)
    graph = window.project.add_graph(GraphSheet("Scatter", data.id, "scatter", {"error": "sd"}, result.id))
    window.select(graph.id)
    assert window.has_unsaved_changes()
    assert window._write(tmp_path / "saved")
    assert window.path == tmp_path / "saved.cqstats" and window.windowTitle() == "QuantAnalysis: saved.cqstats"
    assert not window.has_unsaved_changes() and window.recent_projects() == [str((tmp_path / "saved.cqstats").resolve())]
    # A format change is an unsaved change, and the title says so.
    view = window.stack.currentWidget()
    graph.format.title = "Changed"
    view.format_changed(None)
    qtbot.waitUntil(lambda: window.windowTitle().endswith("*"), timeout=2000)
    # Cancel keeps the project; "don't save" starts a new one.
    monkeypatch.setattr(QuantAnalysisWindow, "ask_save", lambda self, action: "cancel")
    assert not window.new_project() and graph.id in window.project.graphs
    monkeypatch.setattr(QuantAnalysisWindow, "ask_save", lambda self, action: "discard")
    assert window.new_project() and not window.project.data and window.path is None and window.windowTitle() == "QuantAnalysis"

    assert window.open_project(tmp_path / "saved.cqstats")
    assert set(window.project.data) == {data.id} and window.project.results[result.id].result.values["p"] == pytest.approx(
        result.result.values["p"])
    assert window.project.graphs[graph.id].format.title in ("", None) and window.current_id() == graph.id
    assert window.project.data[data.id].origin == str(tmp_path / "results")
    assert not window.has_unsaved_changes()
    window._fill_recent()
    assert [action.text() for action in window.recent_menu.actions()][0] == "saved.cqstats"

    (tmp_path / "bad.cqstats").write_text("no")
    monkeypatch.setattr("cellquant.gui.quantanalysis.window.QMessageBox.warning", lambda *args: None)
    assert not window.open_project(tmp_path / "bad.cqstats") and set(window.project.data) == {data.id}


def test_closing_cellquant_asks_to_save(qtbot, monkeypatch):
    from qtpy.QtWidgets import QMainWindow

    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    main = QMainWindow()
    qtbot.addWidget(main)
    main.show()
    window = QuantAnalysisWindow(main)
    window.show()
    window.new_table()
    asked = []
    monkeypatch.setattr(QuantAnalysisWindow, "ask_save", lambda self, action: asked.append(action) or "cancel")
    assert not main.close() and main.isVisible() and asked == ["CellQuant closes"]
    monkeypatch.setattr(QuantAnalysisWindow, "ask_save", lambda self, action: "discard")
    assert main.close()


def test_a_style_that_cannot_be_saved_says_so(window, tmp_path, monkeypatch):
    from cellquant.gui.quantanalysis import graph_format
    from cellquant.gui.quantanalysis.project import GraphSheet

    data = window.new_table()
    graph = window.project.add_graph(GraphSheet("Scatter", data.id, "scatter"))
    window.select(graph.id)

    def refuse(name, style):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(graph_format, "save_style", refuse)
    window.stack.currentWidget().save_style("Mine")
    assert "could not be saved" in window.statusBar().currentMessage()
