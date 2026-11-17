"""QuantAnalysis polish: settings changed from the project defaults are flagged; before-after graphs of unpaired tables draw no lines; the
estimation plot says which columns it shows; small windows reach every control; the recovery question reads right; edits to a large table
recompute what follows once typing pauses; the engine check shows its window first. Needs Qt (xvfb-run on Linux) and matplotlib."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("matplotlib")

from qtpy.QtWidgets import QMessageBox  # noqa: E402

from cellquant.stats import ColumnTable  # noqa: E402

A = [48.0, 50.5, 52.1, 49.9, 51.3, 47.2]
B = [40.1, 41.7, 39.5, 42.2, 38.8, 40.9]
C = [30.0, 31.5, 29.2, 33.0, 30.8, 31.9]
OPTIONS = {"test": "welch_t", "first": "Control", "second": "Treated", "alternative": "two-sided", "alpha": 0.05, "ci": 0.95, "style": "classic"}


@pytest.fixture
def window(qtbot):
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    window = QuantAnalysisWindow()
    qtbot.addWidget(window)
    window.show()
    return window


def _table(window, columns=("Control", "Treated"), paired=False, title="Cells per field", large=False):
    from cellquant.gui.quantanalysis.datagrid import DEFER_CELLS
    from cellquant.gui.quantanalysis.project import DataSheet

    data = dict(zip(columns, (A, B, C)))
    if large:  # more numbers than DEFER_CELLS; the first rows are the small table's
        rng = np.random.default_rng(3)
        data = {name: np.concatenate([values, rng.normal(40, 5, DEFER_CELLS // len(data) + 1)]) for name, values in data.items()}
    data = window.project.add_data(DataSheet("Data", ColumnTable.from_columns(data, title=title, paired=paired)))
    window.select(data.id)
    return data


# --- 6a: settings changed from the project defaults -------------------------------------------------------------


def test_alpha_ci_and_style_other_than_the_project_defaults_are_flagged(window):
    from cellquant.stats._common import OVERRIDES_TITLE

    _table(window)
    plain = window.analyze("compare", OPTIONS)
    assert not any(section.title == OVERRIDES_TITLE for section in plain.result.sections) and "Changed from the default" not in plain.methods
    sheet = window.analyze("compare", {**OPTIONS, "alpha": 0.01, "ci": 0.99, "style": "apa"})
    titles = [section.title for section in sheet.result.sections]
    assert OVERRIDES_TITLE in titles
    rows = {row.label: row.formatted for row in sheet.result.sections[titles.index(OVERRIDES_TITLE)].rows}
    assert rows == {"Alpha": "0.01 (project default 0.05)", "Confidence level": "99% (project default 95%)",
                    "P value style": "apa (project default classic)"}
    assert any(text.startswith("Set by hand: Alpha = 0.01 (project default 0.05)") for text in sheet.warnings)
    assert "Changed from the default: Alpha = 0.01 (project default 0.05)." in sheet.methods


def test_the_flag_follows_the_project_defaults_in_force(window):
    _table(window)
    window.project.set_defaults({"alpha": 0.01, "ci": 0.95, "style": "classic"})
    same = window.analyze("compare", {**OPTIONS, "alpha": 0.01})
    assert not any("Set by hand" in text for text in same.warnings)
    other = window.analyze("compare", OPTIONS)  # 0.05 is now the changed one
    assert any("Alpha = 0.05 (project default 0.01)" in text for text in other.warnings)


def test_a_setting_the_test_does_not_use_is_not_flagged(window):
    _table(window)
    sheet = window.analyze("compare", {**OPTIONS, "test": "kolmogorov_smirnov", "ci": 0.99})  # this test reports no interval
    assert not any("Confidence level" in text for text in sheet.warnings)


# --- 6b and 6c: graphs --------------------------------------------------------------------------------------------


def _lines(window, kind: str, paired: bool) -> tuple[int, list[str]]:
    data = _table(window, paired=paired)
    graph = window.new_graph(kind)
    view = window.views[graph.id]
    notes = view.draw()
    assert graph.data_id == data.id
    return len(view.figure.axes[0].lines), notes


def test_a_before_after_graph_of_an_unpaired_table_draws_no_joining_lines(window):
    paired_lines, paired_notes = _lines(window, "before_after", True)
    unpaired_lines, notes = _lines(window, "before_after", False)
    assert paired_lines - unpaired_lines == len(A) and not paired_notes
    assert any("no lines are drawn" in text and "Tick Paired" in text for text in notes)


def test_the_estimation_plot_says_which_columns_it_shows(window):
    from cellquant.gui.quantanalysis.project import DataSheet

    data = window.project.add_data(DataSheet("Three", ColumnTable.from_columns({"Control": A, "Treated": B, "Third": C}, title="Cells per field")))
    window.select(data.id)
    window.analyze("compare", OPTIONS)
    window.select(data.id)
    view = window.views[window.new_graph("estimation").id]
    notes = view.draw()
    assert any("shows the two columns of the comparison, Control and Treated" in text and "Third is left out" in text for text in notes), notes
    two = _table(window)
    window.analyze("compare", OPTIONS)
    window.select(two.id)
    assert window.views[window.new_graph("estimation").id].draw() == []


# --- 6d: small screens --------------------------------------------------------------------------------------------


def test_dialogs_are_clamped_to_the_screen():
    from qtpy.QtWidgets import QWidget

    from cellquant.gui.quantanalysis import tips

    widget = QWidget()
    tips.fit_to_screen(widget, 100000, 100000)
    from qtpy.QtGui import QGuiApplication

    screen = QGuiApplication.primaryScreen().availableGeometry()
    assert widget.width() <= screen.width() and widget.height() <= screen.height()
    tips.fit_to_screen(widget, 300, 200)
    assert (widget.width(), widget.height()) == (300, 200)


def test_the_guide_opens_no_larger_than_the_screen(window):
    from qtpy.QtGui import QGuiApplication

    from cellquant.gui.quantanalysis.guide import AnalysisGuideDialog

    screen = QGuiApplication.primaryScreen().availableGeometry()
    dialog = AnalysisGuideDialog(window)
    assert dialog.width() <= screen.width() and dialog.height() <= screen.height()


def test_the_layout_panel_is_wide_enough_for_its_rows(window, qtbot):
    from qtpy.QtWidgets import QScrollArea

    _table(window)
    window.new_graph("bar")
    view = window.stack.currentWidget()
    layout = window.new_layout()
    view = window.views[layout.id]
    window.resize(1280, 720)
    window.show()
    qtbot.wait(200)
    panel = view.findChildren(QScrollArea)[-1]
    inner = panel.widget()
    assert inner.minimumSizeHint().width() <= panel.viewport().width() + 2, (inner.minimumSizeHint(), panel.viewport().width())
    from qtpy.QtCore import QPoint
    from qtpy.QtWidgets import QLabel

    label = next(item for item in inner.findChildren(QLabel) if item.text() == "columns")  # the label that was cut to "co"
    assert label.mapTo(panel.viewport(), QPoint(label.width(), 0)).x() <= panel.viewport().width() + 2


# --- 6e: wording --------------------------------------------------------------------------------------------------


def test_the_recovery_question_reads_right(window, monkeypatch):
    seen = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: seen.append(self.informativeText()) or QMessageBox.No)
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    monkeypatch.undo()  # the autouse fixture's stand-in for ask_restore must not hide the real one
    monkeypatch.setattr(QMessageBox, "exec", lambda self: seen.append(self.informativeText()) or QMessageBox.No)
    assert QuantAnalysisWindow.ask_restore(window, "x.cqstats", "17 Nov 10:00") is False
    assert "No throws it away." in seen[0] and "restores it away" not in seen[0]


# --- 6f: large tables ---------------------------------------------------------------------------------------------


def _counted(window, monkeypatch) -> list:
    calls = []
    original = type(window.project).compute

    def compute(self, sheet):
        calls.append(sheet.id)
        return original(self, sheet)

    monkeypatch.setattr(type(window.project), "compute", compute)
    return calls


def test_a_burst_of_edits_recomputes_once_after_typing_pauses(window, monkeypatch, qtbot):
    data = _table(window, large=True)
    sheet = window.analyze("compare", OPTIONS)
    graph = window.new_graph("scatter")
    window.select(data.id)
    before = sheet.result.values["p"]
    calls = _counted(window, monkeypatch)
    grid = window.stack.currentWidget().grid
    for row, text in enumerate(["90", "91", "92", "93", "94"]):
        grid.item(row, 0).setText(text)
    assert list(data.table["Control"].values[:5]) == [90, 91, 92, 93, 94]  # the table itself is changed at once
    assert calls == [] and sheet.result.values["p"] == before and window.project.fit_pending(sheet.id)
    qtbot.waitUntil(lambda: not window.project.fit_pending(sheet.id), timeout=3000)
    assert calls == [sheet.id] and sheet.result.values["p"] != before
    assert graph.id in window.views


def test_saving_copying_and_exporting_first_finish_the_waiting_recomputation(window, tmp_path):
    from cellquant.gui.quantanalysis.results_view import result_text

    data = _table(window, large=True)
    sheet = window.analyze("compare", OPTIONS)
    window.select(data.id)
    before = sheet.result.values["p"]
    window.stack.currentWidget().grid.item(0, 0).setText("95")
    assert sheet.result.values["p"] == before
    assert window._write(tmp_path / "p.cqstats")  # saving: the project is written as it will be shown
    assert sheet.result.values["p"] != before and not window.project.fit_pending(sheet.id)
    window.select(data.id)
    window.stack.currentWidget().grid.item(1, 0).setText("20")
    again = sheet.result.values["p"]
    window.select(sheet.id)
    window.stack.currentWidget().copy()
    assert sheet.result.values["p"] != again
    assert "P value" in result_text(sheet)


def test_show_working_waits_for_a_waiting_edit(window):
    data = _table(window, large=True)
    sheet = window.analyze("compare", OPTIONS)
    window.select(data.id)
    window.stack.currentWidget().grid.item(0, 0).setText("95")
    assert window.project.fit_pending(sheet.id)


def test_a_typed_cell_does_not_fill_the_whole_grid_again(window, monkeypatch):
    data = _table(window, large=True)
    view = window.stack.currentWidget()
    filled = []
    original = type(view).fill
    monkeypatch.setattr(type(view), "fill", lambda self: filled.append(1) or original(self))
    view.grid.item(0, 0).setText("77")
    window.project.flush_fits()
    assert filled == [] and data.table["Control"].values[0] == 77


def test_the_wait_is_shown_while_the_recomputation_runs(window, qtbot):
    data = _table(window, large=True)
    window.analyze("compare", OPTIONS)
    window.select(data.id)
    messages = []
    window.project.working.connect(messages.append)
    window.stack.currentWidget().grid.item(0, 0).setText("95")
    window.project.flush_fits()
    assert messages and messages[0].startswith("Updating") and messages[-1] == ""


def test_the_engine_check_shows_its_window_before_the_checks_and_cannot_be_started_twice(window, monkeypatch):
    from cellquant.gui.quantanalysis import engine_check
    from cellquant.stats import benchmarks

    dialog = engine_check.EngineCheckDialog(window)
    assert dialog.rows == [] and "Not run" in dialog.summary.text()  # building it runs nothing
    seen = []
    original = benchmarks.run_benchmark

    def spy(item):
        seen.append((dialog.isVisible(), dialog.summary.text(), list(dialog.run()) if not seen else None))
        return original(item)

    monkeypatch.setattr(benchmarks, "run_benchmark", spy)
    monkeypatch.setattr(benchmarks, "BENCHMARKS", benchmarks.BENCHMARKS[:3])
    dialog.show()
    dialog.run()
    assert len(seen) == 3 and all(entry[0] for entry in seen)
    assert "Checking… 1 of 3" in seen[0][1] and "Checking… 3 of 3" in seen[2][1]
    assert seen[0][2] == []  # asking again while it runs does nothing
    assert dialog.rows and "values agree" in dialog.summary.text() and dialog.run_button.isEnabled()


def test_an_edit_in_a_small_table_updates_results_and_graphs_at_once(window):
    data = _table(window)
    sheet = window.analyze("compare", OPTIONS)
    graph = window.new_graph("scatter")
    window.select(data.id)
    before = sheet.result.values["p"]
    changed = []
    window.project.changed.connect(changed.append)
    window.stack.currentWidget().grid.item(0, 0).setText("95")  # no flush, no waiting
    assert sheet.result.values["p"] != before and not window.project.fit_pending(sheet.id)
    assert sheet.id in changed and graph.id in changed


def test_the_threshold_keeps_an_ordinary_table_immediate(window):
    from cellquant.gui.quantanalysis.datagrid import DEFER_CELLS

    small = _table(window)
    assert not window.stack.currentWidget()._is_large() and DEFER_CELLS >= 5000
    large = _table(window, large=True)
    assert window.stack.currentWidget()._is_large() and large.id != small.id
