"""QuantAnalysis, the statistics window: import, analyses, graphs, export, and its two entry points.
Needs Qt (xvfb-run on Linux) and matplotlib; the entry-point test also needs napari."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("matplotlib")

from cellquant.stats import sources  # noqa: E402
from tests.test_stats_import import make_results  # noqa: E402

WELCH = {"test": "welch_t", "first": 0, "second": 1, "alternative": "two-sided", "ci": 0.95, "alpha": 0.05, "style": "GP"}


@pytest.fixture
def window(qtbot):
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    window = QuantAnalysisWindow()
    qtbot.addWidget(window)
    window.show()
    return window


def _replicates(window, tmp_path):
    spec = sources.ImportSpec("report_1_percent", dot="unit", unit="Folder 2", group_by="Folder 1")
    return window.open_folder(make_results(tmp_path / "results"), spec, accept=True)


def test_window_is_separate_and_dark(window):
    assert window.windowTitle() == "QuantAnalysis"
    assert window.isWindow()
    assert [action.text().replace("&", "") for action in window.menuBar().actions()] == ["File", "Analyze", "Graph", "Export", "Help"]
    assert "QWidget" in window.styleSheet()
    assert [window.folders[key].text(0) for key in ("data", "results", "graphs")] == ["Data tables", "Results", "Graphs"]


def test_import_compare_and_live_links(window, tmp_path):
    from cellquant.gui.quantanalysis.results_view import ResultSheetView, result_text

    data = _replicates(window, tmp_path)
    assert data.table.names == ["ctrl", "treated"]
    assert data.units is not None and len(data.images) == 5
    result = window.analyze("compare", WELCH)
    assert result.error == ""
    view = window.stack.currentWidget()
    assert isinstance(view, ResultSheetView)
    assert view.table.rowCount() > 10
    assert "Welch" in view.methods.text()
    before = result.result.values["p"]
    text = result_text(result)
    assert "P value" in text and "Methods:" in text
    path = view.export_csv(str(tmp_path / "welch.csv"))
    assert Path(path).read_text().startswith("section,label,value,formatted")
    # Re-running with other options updates the same sheet.
    window.change_options(result, {**WELCH, "test": "mann_whitney"})
    assert result.name.startswith("Mann-Whitney") and len(window.project.results) == 1
    assert any("smallest P this test can give" in text for text in result.warnings)  # 2 vs 2: an exact test cannot reach 0.05
    assert view.warnings.isVisible()
    window.change_options(result, WELCH)
    # Editing the table recomputes its results (Prism's live links) and detaches it from the folder.
    window.select(data.id)
    grid = window.stack.currentWidget()
    grid.grid.item(0, 1).setText("95")
    assert result.result.values["p"] != before
    assert data.spec is None and data.units is None


def test_each_graph_and_export_format(window, tmp_path):
    from PIL import Image

    data = _replicates(window, tmp_path)
    result = window.analyze("compare", WELCH)
    views = {}
    for kind in ("scatter", "bar", "box", "violin", "before_after", "estimation", "superplot"):
        window.select(data.id)
        graph = window.new_graph(kind)
        assert graph.result_id == result.id
        view = window.stack.currentWidget()
        notes = view.draw()
        views[kind] = view
        if kind not in ("before_after",):
            assert notes == [], (kind, notes)
    superplot = views["superplot"].figure.axes[0]
    assert len(superplot.collections) >= 4  # images and replicates for each group
    estimation = views["estimation"].figure.axes[0]
    assert any("Difference" in child.get_ylabel() for child in estimation.child_axes)
    view = views["scatter"]
    view.sheet.format.update(log_y=True)
    view.draw()
    assert view.figure.axes[0].get_yscale() == "log"
    for kind, suffix in (("svg", ".svg"), ("pdf", ".pdf"), ("eps", ".eps"), ("png", ".png"), ("jpeg", ".jpg")):
        written = view.export(tmp_path / f"graph{suffix}", kind, 300, 80, 60, "mm")
        assert written.stat().st_size > 1000, kind
    for dpi in (300, 600, 1200):
        written = view.export(tmp_path / f"graph_{dpi}.tif", "tiff", dpi, 2, 1.5, "in")
        with Image.open(written) as image:
            assert image.info.get("compression") == "tiff_lzw"
            assert tuple(round(value) for value in image.info["dpi"]) == (dpi, dpi)
            assert image.size == (2 * dpi, round(1.5 * dpi))


def test_suggested_setup_builds_superplot_and_welch(window, tmp_path):
    data = window.open_folder(make_results(tmp_path / "results"), accept=True)
    assert data.spec.dot == "unit" and data.spec.unit == "Folder 2" and data.spec.group_by == "Folder 1"
    kinds = sorted(graph.kind for graph in window.project.graphs.values())
    assert kinds == ["estimation", "superplot"]
    tests = [result.options["test"] for result in window.project.results.values()]
    assert tests == ["welch_t"]


def test_object_dots_warn_about_pseudoreplication(window, tmp_path):
    spec = sources.ImportSpec("area", dot="object", group_by="Folder 1")
    data = window.open_folder(make_results(tmp_path / "results"), spec, accept=True)
    assert data.pseudoreplicated
    assert window.stack.currentWidget().warnings.isVisible()
    result = window.analyze("compare", WELCH)
    assert result.warnings[0].startswith("Pseudoreplication")


def test_table_editing_paste_and_csv(window, tmp_path):
    sheet = window.new_table()
    view = window.stack.currentWidget()
    view.grid.setCurrentCell(0, 0)
    view.paste("WT\tKO\n1\t4\n2\t5\n3\t\n")
    assert sheet.table.names == ["WT", "KO"]
    assert np.allclose(sheet.table["WT"].values, [1, 2, 3]) and sheet.table["KO"].values.size == 2
    view.add_column()
    assert len(sheet.table) == 3
    view.grid.setCurrentCell(0, 2)
    view.remove_column()
    assert sheet.table.names == ["WT", "KO"]
    view.paired.setChecked(True)
    assert sheet.table.paired
    stats = window.analyze("colstats")
    assert stats.wide is not None and list(stats.wide.columns) == ["WT", "KO"]
    normal = window.analyze("normality")
    assert normal.error == ""
    long = tmp_path / "long.csv"
    long.write_text("value,group\n1,a\n2,a\n3,b\n5,b\n")
    imported = window.import_csv(str(long), shape="long", value="value", group="group")
    assert imported.table.names == ["a", "b"]
    wide = tmp_path / "wide.csv"
    wide.write_text("x,y\n1,2\n3,\n")
    imported = window.import_csv(str(wide), shape="wide")
    assert imported.table.names == ["x", "y"] and imported.table["y"].values.size == 1


def test_dialogs_teach_and_prefill(qtbot):
    from cellquant.gui.quantanalysis.dialogs import ChooseTestDialog, CompareDialog
    from cellquant.stats import ColumnTable

    guide = ChooseTestDialog(None)
    qtbot.addWidget(guide)
    assert guide.choice()["test"] == "welch_t"
    from cellquant.gui.quantanalysis.dialogs import _check

    _check(guide.gaussian, "no")
    assert guide.choice()["test"] == "mann_whitney"
    _check(guide.paired, True)
    assert guide.choice()["test"] == "wilcoxon"
    _check(guide.groups, 3)
    assert guide.choice() is None and "next update" in guide.answer.toPlainText()
    table = ColumnTable.from_columns({"A": [1, 2, 3], "B": [4, 5, 6]})
    dialog = CompareDialog(None, table)
    qtbot.addWidget(dialog)
    assert dialog.options()["test"] == "welch_t"
    assert dialog.tails.toolTip() and dialog.test.toolTip()
    dialog.set_options({**dialog.options(), "test": "mann_whitney"})
    assert dialog.note.isVisibleTo(dialog) and "cannot give P" in dialog.note.text()


def test_missing_packages_are_explained(qtbot, monkeypatch):
    import importlib.util
    import sys

    from cellquant.gui.quantanalysis import window as module

    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *args: None if name in ("matplotlib", "statsmodels") else real(name, *args))
    window = module.QuantAnalysisWindow()
    qtbot.addWidget(window)
    assert 'pip install "cellquant[stats]"' in window.banner.text()
    assert not window.banner.isHidden()
    monkeypatch.setitem(sys.modules, "matplotlib.backends.backend_qtagg", None)
    window.new_table()
    graph = window.new_graph("scatter")
    view = window.stack.currentWidget()
    assert graph is not None and view.figure is None and "cellquant[stats]" in view.missing.text()


def test_statistics_guide_and_log(window):
    dialog = window.statistics_guide()
    assert "Statistics reference" in dialog.findChild(__import__("qtpy.QtWidgets", fromlist=["QTextBrowser"]).QTextBrowser).toPlainText()
    dialog.close()
    window.new_table()
    log = window.show_log()
    log.close()


def test_packaged_statistics_guide_matches_docs():
    root = Path(__file__).resolve().parents[1]
    assert (root / "cellquant" / "gui" / "STATISTICS.md").read_bytes() == (root / "docs" / "STATISTICS.md").read_bytes()


def test_entry_points_from_cellquant(tmp_path, monkeypatch):
    napari = pytest.importorskip("napari")
    from qtpy.QtWidgets import QPushButton

    from cellquant.gui import app as app_module
    from cellquant.gui.quantanalysis.dialogs import ImportDialog
    from tests.test_gui_plan import _settle, _wait

    monkeypatch.setattr(ImportDialog, "exec", lambda self: 1)
    from tests.test_plan import _experiment

    controller = _experiment(tmp_path)
    for record in controller.experiment.images:
        controller.run_image(record.image_id)
        record.processing_status = "approved"
    controller.save()
    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"napari viewer could not start: {exc}")
    shell = app_module.CellQuantWindow(viewer, controller.directory)
    _wait(shell)
    try:
        texts = {button.text() for button in shell._tabs.findChildren(QPushButton)}
        assert {"Analyze…", "Analyze a results folder…"} <= texts
        assert sum(button.text() == "Analyze a results folder…" for button in shell._tabs.findChildren(QPushButton)) == 2
        window = shell.open_quantanalysis()
        _settle()
        assert window.isVisible() and window.windowTitle() == "QuantAnalysis"
        data = next(iter(window.project.data.values()))
        assert data.origin == "experiment"
        assert data.table.names and data.description
        # Taking back an approval marks the imported table out of date; it is imported again.
        record = shell.controller.experiment.images[0]
        record.processing_status = "analyzed"
        window._check_experiment()
        assert data.stale or any("imported again" in line for line in window._log.lines)
        window.refresh_imports(force=True)
        assert not data.stale
        assert shell.open_quantanalysis() is window and len(window.project.data) == 1  # Analyze… again: no second import
        # The second entry point reuses the same window.
        monkeypatch.setattr(app_module, "choose_existing_directory", lambda *args: str(make_results(tmp_path / "saved")))
        monkeypatch.setattr(window, "_import", lambda source, origin, spec, accept: setattr(window, "_opened", origin))
        shell.analyze_results_folder()
        assert shell._quantanalysis is window and window._opened.endswith("saved")
        window.close()
    finally:
        viewer.close()
        _wait(shell)
