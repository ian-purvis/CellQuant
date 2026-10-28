"""QuantAnalysis correlation matrix and principal component analysis: the Analyze menu and dialogs, results sheets, graphs and projects.
Needs Qt (xvfb-run on Linux) and matplotlib."""

from __future__ import annotations

import numpy as np
import pytest
from qtpy.QtCore import Qt

pytest.importorskip("pytestqt")
pytest.importorskip("matplotlib")

from cellquant.gui.quantanalysis.dialogs import CorrelationMatrixDialog, PcaDialog  # noqa: E402
from cellquant.stats.tables import ColumnTable  # noqa: E402

NAMES = ["Area", "Intensity", "Marker %", "Perimeter", "Circularity"]


@pytest.fixture
def window(qtbot):
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    window = QuantAnalysisWindow()
    qtbot.addWidget(window)
    window.resize(1400, 900)
    window.show()
    return window


def cell_measurements(cases: int = 30, seed: int = 4) -> dict[str, np.ndarray]:
    """Cells measured five ways: size (area and perimeter go together), signal (intensity and the marker's share) and a shape that is loosely
    tied to size."""

    rng = np.random.default_rng(seed)
    size, signal = rng.normal(size=cases), rng.normal(size=cases)
    return {"Area": 420 + 90 * size + rng.normal(0, 25, cases), "Intensity": 1800 + 400 * signal + rng.normal(0, 120, cases),
            "Marker %": 38 + 9 * signal + 3 * size + rng.normal(0, 4, cases), "Perimeter": 82 + 11 * size + rng.normal(0, 3, cases),
            "Circularity": 0.78 - 0.05 * size + rng.normal(0, 0.05, cases)}


def _sheet(window, **kwargs):
    sheet = window.new_table()
    sheet.table = ColumnTable.from_columns(cell_measurements(**kwargs))
    window.project.data_changed(sheet.id)
    window.select(sheet.id)
    return sheet


def _graph_of(window, result, suffix: str):
    return next(item for item in window.project.graphs.values() if item.result_id == result.id and item.name == f"{result.name} ({suffix})")


def _view(window, graph):
    window.select(graph.id)
    view = window.stack.currentWidget()
    view.flush()
    return view


def test_run_correlation_matrix_via_project(window):
    from cellquant.gui.quantanalysis.results_view import ResultSheetView, result_rows

    sheet = _sheet(window)
    result = window.analyze("corrmatrix", {"method": "spearman", "adjust": "holm", "ci": 0.9, "alpha": 0.01, "style": "APA"})
    assert result.kind == "corrmatrix" and result.error == "" and result.name == f"Correlation matrix: {sheet.name}"
    assert result.wide is result.result.values["wide"] and result.methods and "Holm" in result.methods
    values = result.result.values
    assert values["names"] == NAMES and values["r"].shape == (5, 5) and result.result.options["method"] == "spearman"
    assert values["r"][0, 3] > 0.8 and values["n"][0, 1] == 30  # area and perimeter go together
    headers, rows = result_rows(result)
    assert any(label == "Area vs. Perimeter" for label, _ in rows)
    view = window.stack.currentWidget()
    assert isinstance(view, ResultSheetView) and view.options_button.isVisibleTo(view)
    # the results follow the table
    sheet.table["Area"].values[0] = np.nan
    window.project.data_changed(sheet.id)
    assert result.error == "" and result.result.values["n"][0, 1] == 29
    window.change_options(result, {"method": "pearson", "missing": "listwise", "adjust": "none"})
    assert result.error == "" and result.result.options["missing"] == "listwise" and result.name == f"Correlation matrix: {sheet.name}"
    window.change_options(result, {"method": 3, "missing": None, "adjust": [], "ci": "wide", "alpha": None, "style": 5})  # garbage: the defaults
    assert result.error == "" and result.result.options["method"] == "pearson"


def test_run_pca_via_project(window):
    from cellquant.gui.quantanalysis.results_view import ResultSheetView, result_rows

    sheet = _sheet(window)
    result = window.analyze("pca", {"standardize": True, "select": "number", "number": 3})
    assert result.kind == "pca" and result.error == "" and result.name == f"PCA: {sheet.name}"
    values = result.result.values
    assert values["keep"] == 3 and values["scores"].shape == (30, 4) and values["loadings"].shape == (5, 5) and values["threshold"] is None
    assert result.wide is values["wide"] and result.methods
    headers, rows = result_rows(result)
    assert any(label == "PC1" for label, _ in rows)
    view = window.stack.currentWidget()
    assert isinstance(view, ResultSheetView) and view.options_button.isVisibleTo(view)
    window.change_options(result, {"select": "parallel", "simulations": 200, "seed": 3})
    assert result.error == "" and result.result.values["threshold"] is not None and result.result.options["simulations"] == 200
    window.change_options(result, {"select": "variance", "variance": 0.9, "standardize": False})
    assert result.error == "" and result.result.values["cumulative"][result.result.values["keep"] - 1] >= 0.9
    assert "covariance" in result.methods and any("SDs differ" in text for text in result.warnings)
    window.change_options(result, {"standardize": "no", "select": 4, "variance": "x", "number": None, "simulations": "many", "seed": []})  # garbage
    assert result.error == "" and result.result.options["select"] == "parallel" and result.result.options["standardize"] is True


def test_menu_and_tips(window, monkeypatch):
    from qtpy.QtWidgets import QMenu

    monkeypatch.setattr(CorrelationMatrixDialog, "exec", lambda self: (self.accept(), 1)[-1])
    monkeypatch.setattr(PcaDialog, "exec", lambda self: (self.accept(), 1)[-1])
    _sheet(window)
    actions = {action.text(): action for menu in window.menuBar().findChildren(QMenu) for action in menu.actions()}
    assert "not that one causes the other" in actions["Correlation matrix…"].toolTip()
    assert "components" in actions["Principal component analysis (PCA)…"].toolTip()
    actions["Correlation matrix…"].trigger()
    result = window.project.sheet(window.current_id())
    assert result.kind == "corrmatrix" and result.error == ""
    actions["Principal component analysis (PCA)…"].trigger()
    result = window.project.sheet(window.current_id())
    assert result.kind == "pca" and result.error == ""


def test_correlation_dialog(window):
    sheet = _sheet(window)
    dialog = CorrelationMatrixDialog(window, sheet.table)
    assert dialog.options() == {"method": "pearson", "missing": "pairwise", "adjust": "none", "ci": 0.95, "alpha": 0.05, "style": "classic",
                                "variables": None}
    assert dialog.adjust.itemText(0) == "No correction" and dialog.adjust.count() > 5 and dialog.minimumSizeHint().width() <= 800
    for widget in (dialog.method, dialog.missing, dialog.adjust, dialog.ci, dialog.alpha, dialog.style, dialog.variables):
        assert widget.toolTip()
    dialog.method.setCurrentIndex(dialog.method.findData("spearman"))
    dialog.adjust.setCurrentIndex(dialog.adjust.findData("holm"))
    dialog.variables.item(4).setCheckState(Qt.Unchecked)
    assert dialog.options()["variables"] == NAMES[:4]
    dialog.accept()
    assert dialog.result() == 1
    again = CorrelationMatrixDialog(window, sheet.table, {**dialog.options(), "variables": ["Area", "Gone", "Intensity"]})
    assert again.options()["variables"] == ["Area", "Intensity"] and again.options()["adjust"] == "holm" and again.options()["method"] == "spearman"
    # one variable left: the engine's message shows in the dialog
    fresh = CorrelationMatrixDialog(window, sheet.table, {"variables": ["Area"]})
    fresh.accept()
    assert fresh.result() == 0 and "at least two variables" in fresh.note.text()


def test_pca_dialog(window):
    sheet = _sheet(window)
    dialog = PcaDialog(window, sheet.table)
    dialog.show()
    assert dialog.options() == {"standardize": True, "select": "parallel", "variance": 0.8, "number": 2, "variables": None}
    for widget in (dialog.standardize, dialog.select, dialog.variance, dialog.number, dialog.variables):
        assert widget.toolTip()
    assert "units differ" in dialog.standardize.toolTip() or "different units" in dialog.standardize.toolTip()
    # only the rule's own setting is shown
    assert not dialog.variance.isVisibleTo(dialog) and not dialog.number.isVisibleTo(dialog)
    dialog.select.setCurrentIndex(dialog.select.findData("variance"))
    assert dialog.variance.isVisibleTo(dialog) and not dialog.number.isVisibleTo(dialog)
    dialog.select.setCurrentIndex(dialog.select.findData("number"))
    assert dialog.number.isVisibleTo(dialog) and not dialog.variance.isVisibleTo(dialog)
    # simulations and the seed are Advanced and used only by parallel analysis
    dialog.advanced.toggle.setChecked(True)
    mode, spin, *_rest = dialog.advanced.fields["simulations"]
    mode.setCurrentIndex(mode.findData("custom"))
    spin.setValue(500)
    assert "simulations" not in dialog.options()  # hidden for this rule
    dialog.select.setCurrentIndex(dialog.select.findData("parallel"))
    assert dialog.options()["simulations"] == 500 and "seed" not in dialog.options() and "advanced_for" not in dialog.options()
    dialog.accept()
    assert dialog.result() == 1
    again = PcaDialog(window, sheet.table, dialog.options())
    assert again.advanced.toggle.isChecked() and again.options()["simulations"] == 500
    # the engine's message shows in the dialog
    tiny = ColumnTable.from_columns({"A": [1.0, 2.0], "B": [2.0, 1.0]})
    refused = PcaDialog(window, tiny)
    refused.accept()
    assert refused.result() == 0 and "at least 3 cases" in refused.note.text()


def test_analyze_through_the_dialogs(window, monkeypatch):
    _sheet(window)

    def run(self):
        self.select.setCurrentIndex(self.select.findData("kaiser"))
        self.standardize.setChecked(False)
        self.accept()
        return 1

    monkeypatch.setattr(PcaDialog, "exec", run)
    result = window.analyze("pca")
    assert result.error == "" and result.result.options["select"] == "kaiser" and result.result.options["standardize"] is False
    monkeypatch.setattr(CorrelationMatrixDialog, "exec", lambda self: (self.missing.setCurrentIndex(1), self.accept(), 1)[-1])
    result = window.analyze("corrmatrix")
    assert result.error == "" and result.result.options["missing"] == "listwise"


def test_variables_subset(window):
    sheet = _sheet(window)
    result = window.analyze("corrmatrix", {"variables": ["Area", "Perimeter", "Gone"]})
    assert result.error == "" and result.result.values["names"] == ["Area", "Perimeter"]
    pca = window.analyze("pca", {"variables": ["Intensity", "Marker %", "Area"], "select": "number", "number": 2})
    assert pca.error == "" and pca.result.values["names"] == ["Area", "Intensity", "Marker %"]  # in the table's order
    sheet.table = ColumnTable.from_columns({"X": [1.0, 2.0, 3.0], "Y": [1.0, 3.0, 2.0]})
    window.project.data_changed(sheet.id)
    assert result.error.startswith("The columns this analysis used are gone")  # none of the ticked columns is left
    window.change_options(result, {"variables": None})
    assert result.error == "" and result.result.values["names"] == ["X", "Y"]


def test_refusals(window):
    count = len(window.project.results)
    window.select(window.new_grouped_table().id)
    for kind in ("corrmatrix", "pca"):
        assert window.analyze(kind, {}) is None and "column table" in window.statusBar().currentMessage()
    window.select(window.new_xy_table().id)
    for kind in ("corrmatrix", "pca"):
        assert window.analyze(kind, {}) is None and "XY table" in window.statusBar().currentMessage()
    window.select(window.new_contingency_table().id)
    for kind in ("corrmatrix", "pca"):
        assert window.analyze(kind, {}) is None
    one = window.new_table()
    one.table = ColumnTable.from_columns({"A": [1.0, 2.0, 3.0]})
    window.project.data_changed(one.id)
    window.select(one.id)
    for kind in ("corrmatrix", "pca"):
        assert window.analyze(kind) is None and "at least two columns" in window.statusBar().currentMessage()
    assert len(window.project.results) == count


def test_blank_rows_and_replicates_warn(window):
    sheet = _sheet(window)
    sheet.table["Intensity"].values[2] = np.nan
    window.project.data_changed(sheet.id)
    matrix = window.analyze("corrmatrix", {})
    assert any("n differs between pairs" in text for text in matrix.warnings)
    listwise = window.analyze("corrmatrix", {"missing": "listwise"})
    assert any("left out of every pair" in text for text in listwise.warnings)
    pca = window.analyze("pca", {})
    assert any("left out" in text for text in pca.warnings) and pca.result.values["n"] == 29
    # a table of replicates: each row is a replicate mean
    from cellquant.gui.quantanalysis.project import set_replicates
    from cellquant.stats.replicates import ReplicateTable

    rng = np.random.default_rng(1)
    labels = [f"Exp {i + 1}" if j == 0 else "" for i in range(8) for j in range(5)]
    base = np.repeat(rng.normal(size=8), 5)
    nested = window.new_replicate_table()
    set_replicates(nested, ReplicateTable("Cells", ["A", "B", "C"], [base + rng.normal(0, 0.5, 40) for _ in range(3)], labels))
    window.project.data_changed(nested.id)
    window.select(nested.id)
    result = window.analyze("corrmatrix", {})
    assert result.error == "" and result.warnings[0].startswith("Each row is one biological replicate") and result.result.values["n"][0, 1] == 8
    assert result.methods.startswith("Analyzed on one value per biological replicate")


def test_engine_errors_surface(window):
    sheet = _sheet(window)
    sheet.table["Area"].values[:] = 5.0  # does not vary
    window.project.data_changed(sheet.id)
    result = window.analyze("pca", {})
    assert result is not None and "does not vary" in result.error
    result = window.analyze("corrmatrix", {})
    assert result.error == "" and any(text.startswith("Not computed") for text in result.warnings)  # the pairs with Area are undefined
    assert not [item for item in window.project.graphs.values() if item.result_id == next(r.id for r in window.project.results.values() if r.kind == "pca")]


def test_correlation_matrix_graph(window):
    from matplotlib.image import AxesImage

    sheet = _sheet(window)
    result = window.analyze("corrmatrix", {"adjust": "holm"})
    graph = _graph_of(window, result, "graph")
    assert graph.kind == "scatter" and graph.name == f"{result.name} (graph)"
    view = _view(window, graph)
    ax = view.figure._cq_axes["main"]
    values = result.result.values
    k = len(NAMES)
    cells = [text for text in ax.texts]
    assert len(cells) == k * k
    image = next(artist for artist in ax.get_children() if isinstance(artist, AxesImage))
    assert image.get_clim() == (-1, 1) and image.get_cmap().name == "RdBu_r" and np.allclose(image.get_array(), values["r"])
    by_cell = {(round(text.get_position()[1]), round(text.get_position()[0])): text.get_text() for text in cells}
    assert by_cell[(0, 0)] == "1.00" and by_cell[(0, 3)].replace("*", "") == f"{values['r'][0, 3]:.2f}"
    for i in range(k):
        for j in range(k):
            assert by_cell[(i, j)].endswith("*") == (i != j and values["p_adjusted"][i, j] < 0.05)
    assert by_cell[(0, 3)].endswith("*") and sum(text.endswith("*") for text in by_cell.values()) >= 2
    assert [label.get_text() for label in ax.get_xticklabels()] == NAMES and [label.get_text() for label in ax.get_yticklabels()] == NAMES
    assert all(label.get_rotation() == 45 for label in ax.get_xticklabels())
    colors = {by_cell[(0, 0)]: cells[0].get_color()}
    assert colors["1.00"] == "#FFFFFF"  # white on the darkest red
    assert ax.get_title(loc="left") == "N = 30 cases" and ax.get_xlabel() == "* adjusted P < 0.05"
    bar = view.figure._cq_colorbar
    assert bar.ax.get_ylabel() == "r" and bar.ax.get_ylim() == (-1, 1)
    # Spearman is labelled rₛ, and a title is added above N
    window.change_options(result, {"method": "spearman", "adjust": "none"})
    graph.format["title"] = "Cells"
    window.project.changed.emit(graph.id)
    view.flush()
    assert view.figure._cq_colorbar.ax.get_ylabel() == "rₛ" and view.figure._cq_axes["main"].get_title(loc="left").startswith("Cells\nN = ")
    assert view.figure._cq_axes["main"].get_xlabel() == "* P < 0.05"
    # undefined pairs are drawn as a dash
    sheet.table["Area"].values[:] = 5.0
    window.project.data_changed(sheet.id)
    view.flush()
    assert "–" in [text.get_text() for text in view.figure._cq_axes["main"].texts]
    # pairwise deletion with different n: the title gives the range
    sheet.table["Area"].values[:] = cell_measurements()["Area"]
    sheet.table["Intensity"].values[:3] = np.nan
    window.project.data_changed(sheet.id)
    view.flush()
    assert view.figure._cq_axes["main"].get_title(loc="left") == "Cells\nN = 27 to 30 cases"


def test_pca_graphs(window):
    _sheet(window)
    result = window.analyze("pca", {})
    scree, scores = _graph_of(window, result, "scree"), _graph_of(window, result, "scores")
    assert scree.format.pca_view == "scree" and scores.format.pca_view == "scores"
    values = result.result.values
    keep = values["keep"]
    assert keep >= 2
    view = _view(window, scree)
    ax = view.figure._cq_axes["main"]
    dashed = [line for line in ax.get_lines() if line.get_linestyle() == "--"]
    assert len(dashed) == 1 and np.allclose(dashed[0].get_ydata(), values["threshold"])
    points = [c for c in ax.collections if len(c.get_offsets())]
    assert sum(len(c.get_offsets()) for c in points) == len(values["eigenvalues"])
    filled = next(c for c in points if len(c.get_offsets()) == keep)
    assert np.allclose(filled.get_offsets()[:, 1], values["eigenvalues"][:keep])
    assert ax.get_xlabel() == "Component" and ax.get_ylabel() == "Eigenvalue" and ax.get_ylim()[0] == 0
    labels = [text.get_text() for text in view.figure._cq_legend.get_texts()]
    assert labels == ["Kept", "Not kept", "Parallel analysis (95th percentile)"]
    assert ax.get_title(loc="left") == f"N = 30 cases, {keep} of 5 components kept"

    view = _view(window, scores)
    ax = view.figure._cq_axes["main"]
    names = [text.get_text() for text in ax.texts if text.get_text()]
    assert names == NAMES  # a name at every arrow tip
    assert len([text for text in ax.texts if text.get_text() == "" and getattr(text, "arrowprops", None)]) == 5  # and an arrow from the origin
    dots = ax.collections[0].get_offsets()
    assert np.allclose(dots[:, 0], values["scores"]["PC1"]) and np.allclose(dots[:, 1], values["scores"]["PC2"])
    share = values["share"]
    assert ax.get_xlabel() == f"PC1 ({100 * share[0]:.1f}%)" and ax.get_ylabel() == f"PC2 ({100 * share[1]:.1f}%)"
    tips_ = [text.xy for text in ax.texts if getattr(text, "arrowprops", None)]
    reach = max(max(abs(x), abs(y)) for x, y in tips_)
    assert reach <= max(np.abs(dots).max(), 1e-9) * 1.0001  # the arrows fit within the scores
    assert ax.get_title(loc="left") == "N = 30 cases"

    # the Kaiser rule: the line at an eigenvalue of 1; a rule with no line: none
    window.change_options(result, {"select": "kaiser"})
    view = _view(window, scree)
    ax = view.figure._cq_axes["main"]
    assert [line.get_ydata()[0] for line in ax.get_lines() if line.get_linestyle() == "--"] == [1.0]
    assert [text.get_text() for text in view.figure._cq_legend.get_texts()][-1] == "Eigenvalue = 1"
    window.change_options(result, {"select": "number", "number": 2})
    view = _view(window, scree)
    assert not [line for line in view.figure._cq_axes["main"].get_lines() if line.get_linestyle() == "--"]
    assert [text.get_text() for text in view.figure._cq_legend.get_texts()] == ["Kept", "Not kept"]


def test_pca_scores_with_one_component(window):
    _sheet(window)
    result = window.analyze("pca", {"select": "number", "number": 1})
    graph = _graph_of(window, result, "scores")
    view = _view(window, graph)
    ax = view.figure._cq_axes["main"]
    assert ax.get_ylabel() == "Row" and not ax.texts and ax.get_xlabel().startswith("PC1 (")
    assert any("Only one component was kept" in note for note in graph_notes(view))


def graph_notes(view) -> list[str]:
    from matplotlib.figure import Figure

    from cellquant.gui.quantanalysis import graphs

    project = view.project
    return graphs.draw(Figure(), project.data[view.sheet.data_id], view.sheet, project.results[view.sheet.result_id].result)


def test_pca_view_in_the_format_panel(window):
    _sheet(window)
    result = window.analyze("pca", {})
    scores = _graph_of(window, result, "scores")
    window.select(scores.id)
    view = window.stack.currentWidget()
    section = view.panel["graph"]
    assert section.pca_view.currentData() == "scores" and section.pca_view.isVisibleTo(section) and section.pca_view.toolTip()
    section.pca_view.setCurrentIndex(section.pca_view.findData("scree"))
    assert scores.format.pca_view == "scree"
    view.flush()
    assert view.figure._cq_axes["main"].get_xlabel() == "Component"
    section.comparison.setCurrentIndex(0)  # no comparison: the row is gone and the columns are drawn
    assert not section.pca_view.isVisibleTo(section) and scores.result_id is None


def test_project_round_trip(window, tmp_path):
    from cellquant.gui.quantanalysis import project_file
    from cellquant.gui.quantanalysis.project import Project

    _sheet(window)
    matrix = window.analyze("corrmatrix", {"method": "spearman", "adjust": "bh", "variables": ["Area", "Perimeter", "Intensity"]})
    pca = window.analyze("pca", {"select": "parallel", "simulations": 300, "seed": 5, "standardize": False, "variables": ["Area", "Intensity", "Marker %"]})
    scores = _graph_of(window, pca, "scores")
    path = project_file.save(window.project, tmp_path / "multivariate.cqstats")
    other = Project()
    _current, notes = project_file.load(path, other)
    assert notes == []
    loaded = other.results[matrix.id]
    assert loaded.kind == "corrmatrix" and loaded.error == "" and loaded.wide is not None and loaded.options["adjust"] == "bh"
    assert loaded.options["variables"] == ["Area", "Perimeter", "Intensity"] and loaded.result.values["names"] == ["Area", "Intensity", "Perimeter"]
    assert np.allclose(loaded.result.values["r"], matrix.result.values["r"])
    again = other.results[pca.id]
    assert again.kind == "pca" and again.error == "" and again.options["simulations"] == 300 and again.options["seed"] == 5
    assert np.allclose(again.result.values["eigenvalues"], pca.result.values["eigenvalues"])
    assert np.allclose(again.result.values["threshold"], pca.result.values["threshold"])
    assert other.graphs[scores.id].result_id == pca.id and other.graphs[scores.id].format.pca_view == "scores"
    assert other.graphs[_graph_of(window, pca, "scree").id].format.pca_view == "scree"
    again.options.update(standardize=None, select=1, variance=[], number="a", simulations=None, seed={})  # a damaged file still runs
    other.rerun(again)
    assert again.error == ""
