"""The output glossary: the in-app copy matches docs/, and the Help menu opens it."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_in_app_glossary_matches_docs():
    docs = (ROOT / "docs" / "OUTPUT_GLOSSARY.md").read_text(encoding="utf-8")
    bundled = (ROOT / "cellquant" / "gui" / "OUTPUT_GLOSSARY.md").read_text(encoding="utf-8")
    assert bundled == docs, "cellquant/gui/OUTPUT_GLOSSARY.md must be a copy of docs/OUTPUT_GLOSSARY.md"


def test_glossary_text_is_found():
    from cellquant.gui.guide import glossary_text

    assert glossary_text().startswith("# CellQuant output glossary")


def _tree():
    from cellquant.gui import glossary
    from cellquant.gui.guide import glossary_text

    return glossary.parse(glossary_text())


def test_glossary_tree_is_complete():
    root = _tree()
    assert [entry.title for entry in root.children] == ["Start here", "Results folder", "Export folder"]
    entries = list(root.walk())
    terms = [entry for entry in entries if entry.is_term]
    assert len(terms) > 100
    names = {name for entry in terms for name in entry.names}
    for entry in terms:
        assert entry.fields.get("In short"), f"{entry.title} has no 'In short'"
    for entry in entries:
        for name in __import__("re").findall(r"`([^`]+)`", entry.fields.get("Related", "")):
            assert name in names, f"{entry.title} links to unknown term {name}"
        if entry.data is not None:
            assert entry.data[0] in {"csv", "json", "yaml", "tif", "log", "folder"}
    export = root.children[2]
    assert all(entry.data is None for entry in export.walk()), "export files have no known place to read from"


def test_patterns_match_real_names():
    from cellquant.gui.glossary import matching

    assert matching(["<marker>_pos"], ["af647_pos", "area"]) == ["af647_pos"]
    assert matching(["report_N_percent"], ["report_2_percent", "report_2_count"]) == ["report_2_percent"]
    assert matching(["area"], ["area", "count_area"]) == ["area"]


def _results(tmp_path):
    from tests.test_plan import _experiment

    controller = _experiment(tmp_path)
    for record in controller.experiment.images:
        controller.run_image(record.image_id)
    controller.save()
    return controller


def test_every_saved_column_and_key_is_explained_and_shown(tmp_path):
    import json

    import pandas as pd

    from cellquant.gui import glossary

    controller = _results(tmp_path)
    image_id = controller.experiment.images[0].image_id
    working = controller.directory / controller.active_analysis().working_folder
    source = glossary.Source(controller.directory, working, image_id)
    root = _tree()
    files = {entry.data[1]: entry for entry in root.walk() if entry.data_owner is entry}

    def covered(pattern, names):
        owner = files[pattern]
        terms = [entry for entry in owner.walk() if entry.is_term]
        return [name for name in names if not any(glossary.matching(entry.names, [name]) for entry in terms)]

    columns = pd.read_csv(source.path("run/measurements/<image>.csv"), nrows=0).columns
    assert covered("run/measurements/<image>.csv", columns) == []
    columns = pd.read_csv(source.path("run/summaries/<image>.csv"), nrows=0).columns
    assert covered("run/summaries/<image>.csv", columns) == []
    for pattern in ("run/provenance/<image>.json", "run/qc/<image>.json"):
        keys = json.loads(source.path(pattern).read_text(encoding="utf-8")).keys()
        assert covered(pattern, keys) == [], pattern
    for entry in root.walk():
        if entry.data is None:
            continue
        page = glossary.data_html(entry, source)
        assert "Could not read" not in page.html, entry.title
    area = next(entry for entry in root.walk() if entry.names == ["area"] and entry.data)
    assert "µm" in glossary.data_html(area, source).html or "px" in glossary.data_html(area, source).html


def test_glossary_window_search_pages_and_data(tmp_path, qtbot):
    from cellquant.gui.glossary_window import ExperimentView, GlossaryWindow
    from cellquant.gui.guide import glossary_text

    controller = _results(tmp_path)
    images = [(record.image_id, record.sample_name) for record in controller.experiment.images]
    view = ExperimentView(controller.directory, controller.directory / controller.active_analysis().working_folder,
                          images, images[0][0])
    closed = GlossaryWindow(None, glossary_text())
    qtbot.addWidget(closed)
    assert not closed.show_data.isEnabled()

    window = GlossaryWindow(None, glossary_text(), lambda: view)
    qtbot.addWidget(window)
    window.search.setText("cutoff")
    shown = [index.data() for index in window._items()]
    assert shown and len(shown) < sum(1 for _ in window.root.walk())
    window.search.clear()
    assert window.select("area")
    text = window.page.toPlainText()
    assert "In short" in text and "How it's calculated" in text and "In your data" not in text
    window.show_data.setChecked(True)
    assert "In your data" in window.page.toPlainText()
    before = window._entry
    window._step(1)
    assert window._entry is not before and window.show_data.isChecked()
    window._link(__import__("qtpy.QtCore", fromlist=["QUrl"]).QUrl("term:phenotype"))
    assert window._entry.names == ["phenotype"]
    assert window.image.count() == len(images) and window.results.count() >= 2


def test_help_menu_opens_the_glossary_with_the_open_experiment(tmp_path):
    from tests.test_gui_refresh import _open

    viewer, shell = _open(tmp_path)
    try:
        shell.open_output_glossary()
        window = shell._glossary_window
        assert window.isVisible()
        assert window.select("area") and window.show_data.isEnabled()
        assert window.image.count() == len(shell.controller.experiment.images)
        assert window.image.currentData() in {record.image_id for record in shell.controller.experiment.images}
        shell.open_output_glossary()
        assert shell._glossary_window is window  # one window, brought to the front
    finally:
        viewer.close()
