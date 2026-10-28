"""The statistics reference (Help → Statistics reference…): every worked example matches the engine, every link
and equations section resolves, every results sheet has a page, and the window opens them."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from cellquant.gui.quantanalysis import reference

ROOT = Path(__file__).resolve().parents[1]
DOCS = (ROOT / "docs" / "STATS_REFERENCE.md").read_text(encoding="utf-8")
ROOT_PAGE = reference.parse(DOCS)
INDEX = reference.Index(ROOT_PAGE)
PAGES = [page for page in ROOT_PAGE.walk() if page.is_page]
EXAMPLES = [
    pytest.param(item, id=f"{page.title} {number}")
    for page in PAGES
    for number, item in enumerate(reference.examples(page.fields.get("Worked example", "")))
]


def test_packaged_reference_matches_docs():
    assert (ROOT / "cellquant" / "gui" / "STATS_REFERENCE.md").read_text(encoding="utf-8") == DOCS


def test_every_analysis_page_has_the_basics():
    analyses = next(page for page in ROOT_PAGE.children if page.title == "Analyses")
    for page in analyses.walk():
        if page.is_page:
            missing = {"In short", "What is done to your data", "How it's calculated", "Worked example", "Interpreting the results"} - set(page.fields)
            assert not missing, f"{page.title} lacks {sorted(missing)}"
    terms = next(page for page in ROOT_PAGE.children if page.title == "Terms")
    assert len(terms.children) >= 40
    assert all("In short" in page.fields for page in terms.children)


@pytest.mark.parametrize("item", EXAMPLES)
def test_worked_example_matches_the_engine(item):
    checks = reference.run_example(item)
    assert checks, "an example states at least one result"
    wrong = [f"{check.key}: page {check.documented}, engine {reference.live_text(check.live)}" for check in checks if not check.ok]
    assert not wrong, wrong


def test_check_uses_the_digits_shown():
    assert reference._check("p", "0.0213", 0.021311).ok
    assert not reference._check("p", "0.0213", 0.02136).ok
    assert reference._check("t", "3.674", 3.6742346).ok
    assert reference._check("x", "1.5e-05", 1.54e-05).ok
    assert not reference._check("x", "6", None).ok


def test_links_and_equations_resolve():
    guide = (ROOT / "docs" / "STATISTICS.md").read_text(encoding="utf-8")
    for target in set(re.findall(r"\[\[([A-Za-z][^\]|]*)", DOCS)):
        assert INDEX.page(target) is not None, f"[[{target}]] has no page"
    for page in PAGES:
        if page.equations:
            assert reference.equations(guide, page.equations), f"{page.title}: no section {page.equations!r} in STATISTICS.md"


def test_names_are_unique():
    names = [name for page in ROOT_PAGE.walk() for name in {item.casefold() for item in (page.title, *page.aliases)}]
    assert len(names) == len(set(names)), sorted({name for name in names if names.count(name) > 1})


def test_every_results_sheet_opens_a_page():
    from cellquant.gui.quantanalysis.project import ANOVA_NAMES, COMPARE_TESTS, OUTLIER_NAMES
    from cellquant.gui.quantanalysis.project_file import RESULT_KINDS
    from cellquant.stats import CURVE_MODELS

    sheets = [("compare", {"test": test}) for test in COMPARE_TESTS]
    sheets += [("anova", {"test": test}) for test in ANOVA_NAMES]
    sheets += [("outliers", {"method": method}) for method in OUTLIER_NAMES]
    sheets += [("curvefit", {"model": model}) for model in CURVE_MODELS]
    sheets += [("xycorr", {"method": method}) for method in ("pearson", "spearman")]
    variants = {kind for kind, _options in sheets}
    sheets += [(kind, {}) for kind in sorted(RESULT_KINDS - variants)]  # every kind a project can hold, new ones included
    for kind, options in sheets:
        assert INDEX.for_sheet(kind, options) is not None, f"no page for {reference.sheet_key(kind, options)}"
    assert INDEX.for_sheet("compare", {"test": "welch_t"}).title == "Welch's t test"
    assert INDEX.for_sheet("anova", {"test": "kruskal_wallis"}).title == "Kruskal-Wallis test"


def test_missing_reference_file_gives_a_stub(monkeypatch):
    def missing(*_args, **_kwargs):
        raise OSError("gone")

    monkeypatch.setattr(Path, "read_text", missing)
    stub = reference.text()
    assert "docs/STATS_REFERENCE.md" in stub
    assert reference.parse(stub).title == "Statistics reference"


def test_row_labels_open_terms():
    assert INDEX.for_label("P value").title == "P value"
    assert INDEX.for_label("Unpaired t test") is None  # an analysis, not a term


# --- The window -------------------------------------------------------------------------------------------------

def test_reference_window(qtbot):
    pytest.importorskip("pytestqt")
    pytest.importorskip("matplotlib")
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow
    from cellquant.gui.quantanalysis.project import ResultSheet

    window = QuantAnalysisWindow()
    qtbot.addWidget(window)
    window.show()
    assert "Statistics reference…" in [action.text() for action in window.menuBar().actions()[-1].menu().actions()]
    ref = window.statistics_reference(ResultSheet("t", "", "compare", {"test": "unpaired_t"}))
    assert ref.title.text() == "Unpaired t test"
    assert "Full equations" in ref.page.toPlainText()
    ref._link(reference_url("full:"))
    assert "s_p²" in ref.page.toPlainText()
    checks = ref.run_example(0)
    assert checks and all(check.ok for check in checks)
    assert "The engine now" in ref.page.toPlainText()
    ref._link(reference_url("table:0"))
    data = window.current_data()
    assert data.name == "Example: Unpaired t test" and [column.name for column in data.table.columns] == ["Control", "Treated"]
    ref._link(reference_url("page:P value"))
    assert ref.title.text() == "P value"
    ref._go_back()
    assert ref.title.text() == "Unpaired t test"
    ref.search.setText("Kruskal")
    ref._open_first_match()
    assert "Kruskal" in ref.title.text()
    assert window.statistics_reference() is ref  # reused


def test_results_sheet_links_to_its_page(qtbot):
    pytest.importorskip("pytestqt")
    pytest.importorskip("matplotlib")
    from qtpy.QtWidgets import QPushButton

    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow
    from cellquant.stats import ColumnTable

    window = QuantAnalysisWindow()
    qtbot.addWidget(window)
    window.show()
    data = window.open_example("Example", {"Control": [3, 5, 7], "Treated": [9, 11, 13]})
    sheet = window.analyze("compare", {"test": "unpaired_t", "first": 0, "second": 1, "alternative": "two-sided", "ci": 0.95, "alpha": 0.05, "style": "classic"})
    view = window.stack.currentWidget()
    button = next(item for item in view.findChildren(QPushButton) if item.text() == "How is this calculated?")
    button.click()
    assert window._reference.title.text() == "Unpaired t test"
    row = next(row for row in range(view.table.rowCount()) if view.table.item(row, 0) and view.table.item(row, 0).text() == "P value")
    view._explain(row, 0)
    assert window._reference.title.text() == "P value"
    assert isinstance(data.table, ColumnTable) and sheet is not None


def reference_url(text: str):
    from qtpy.QtCore import QUrl

    return QUrl(text)


def test_dialog_button_opens_the_chosen_test(qtbot):
    pytest.importorskip("pytestqt")
    from qtpy.QtWidgets import QPushButton

    from cellquant.gui.quantanalysis.dialogs import AnovaDialog, CompareDialog
    from cellquant.stats import ColumnTable

    table = ColumnTable.from_columns({"A": [1, 2, 3], "B": [4, 5, 6], "C": [7, 8, 9]})
    dialog = CompareDialog(None, table, {"test": "welch_t"})
    qtbot.addWidget(dialog)
    next(item for item in dialog.findChildren(QPushButton) if item.text() == "How is it calculated?").click()
    assert dialog._reference.title.text() == "Welch's t test"
    assert dialog._reference.parent() is dialog  # takes input while the modal dialog is open
    anova = AnovaDialog(None, table)
    qtbot.addWidget(anova)
    next(item for item in anova.findChildren(QPushButton) if item.text() == "How is it calculated?").click()
    assert anova._reference.title.text() == "Ordinary one-way ANOVA"


def test_show_working_on_a_results_sheet(qtbot, tmp_path):
    pytest.importorskip("pytestqt")
    pytest.importorskip("matplotlib")
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    window = QuantAnalysisWindow()
    qtbot.addWidget(window)
    window.show()
    window.open_example("Example", {"Control": [3, 5, 7], "Treated": [9, 11, 13]})
    window.analyze("compare", {"test": "unpaired_t", "first": 0, "second": 1, "alternative": "two-sided", "ci": 0.95, "alpha": 0.05, "style": "classic"})
    view = window.stack.currentWidget()
    assert not view.working.isVisibleTo(view)  # off by default
    view.working_box.setChecked(True)
    assert view.working.isVisibleTo(view) and view.working_export.isVisibleTo(view)
    text = view.working.toPlainText()
    assert "Pooled" in text and "✓" in text and text.count("✗") == 1  # only in the key above the steps
    frame = __import__("pandas").read_csv(view.export_working(str(tmp_path / "w.csv")))
    assert {"block", "quantity", "formula", "value", "engine", "agrees"} <= set(frame.columns)
    view.working_box.setChecked(False)


def test_check_the_engine_window(qtbot, tmp_path):
    pytest.importorskip("pytestqt")
    pytest.importorskip("matplotlib")
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    window = QuantAnalysisWindow()
    qtbot.addWidget(window)
    dialog = window.check_engine()
    assert dialog.rows and all(row.ok for row in dialog.rows)
    assert "values agree" in dialog.summary.text() and dialog.table.rowCount() == len(dialog.rows)
    assert window.check_engine() is dialog
    assert Path(dialog.export_csv(str(tmp_path / "check.csv"))).exists()
