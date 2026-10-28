"""QuantAnalysis survival analysis: the new survival table, the Analyze menu and dialog, results sheets, graphs and projects.
Needs Qt (xvfb-run on Linux) and matplotlib."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("matplotlib")

# (time, group, status): group 0 dies early, group 2 late; several subjects are censored
SUBJECTS = [(2, 0, 1), (3, 0, 1), (4, 0, 0), (5, 0, 1), (7, 0, 1), (9, 0, 0),
            (4, 1, 1), (6, 1, 0), (8, 1, 1), (10, 1, 1), (12, 1, 0), (15, 1, 1),
            (8, 2, 1), (11, 2, 0), (14, 2, 1), (17, 2, 0), (20, 2, 1), (24, 2, 0)]


@pytest.fixture
def window(qtbot):
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    window = QuantAnalysisWindow()
    qtbot.addWidget(window)
    window.resize(1400, 900)
    window.show()
    return window


def _fill(window, groups: int = 3, blank_rows: int = 0, replicates: int = 1):
    from cellquant.stats.xy import XYTable

    sheet = window.new_survival_table()
    subjects = [item for item in SUBJECTS if item[1] < groups]
    x = np.array([item[0] for item in subjects] + [np.nan] * blank_rows, float)
    values = np.full((x.size, groups, replicates), np.nan)
    for i, (_time, group, status) in enumerate(subjects):
        values[i, group, 0] = status
    sheet.xy = XYTable("Trial", "Time (months)", x, ["Control", "Low", "High"][:groups], values)
    window.project.data_changed(sheet.id)
    window.select(sheet.id)
    return sheet


def test_new_survival_table(window):
    sheet = window.new_survival_table()
    assert sheet.xy is not None and sheet.xy.x_title == "Time" and sheet.xy.sets == ["Group A", "Group B"]
    assert sheet.xy.replicates == 1 and sheet.xy.x.size >= 5 and np.isnan(sheet.xy.values).all()
    assert window.current_id() == sheet.id


def test_menu_and_tips(window, monkeypatch):
    from qtpy.QtWidgets import QMenu

    from cellquant.gui.quantanalysis import dialogs

    monkeypatch.setattr(dialogs.SurvivalDialog, "exec", lambda self: 1)
    actions = {action.text(): action for menu in window.menuBar().findChildren(QMenu) for action in menu.actions()}
    assert "One row per subject" in actions["New survival table"].toolTip()
    actions["New survival table"].trigger()
    sheet = window.project.sheet(window.current_id())
    assert sheet.xy is not None and sheet.xy.x_title == "Time"
    _fill(window)
    assert "Kaplan-Meier" in actions["Survival curves (Kaplan-Meier)…"].toolTip()
    actions["Survival curves (Kaplan-Meier)…"].trigger()
    result = window.project.sheet(window.current_id())
    assert result.kind == "survival" and result.error == "" and result.result is not None


def test_run_survival_via_project(window):
    from cellquant.gui.quantanalysis.dialogs import SURVIVAL_DEFAULTS
    from cellquant.gui.quantanalysis.results_view import ResultSheetView, result_rows

    table = _fill(window)
    result = window.analyze("survival", {**SURVIVAL_DEFAULTS, "trend": True})
    assert result.error == "" and result.kind == "survival" and result.name == f"Survival: {table.name}"
    assert result.wide is result.result.values["wide"] and result.methods
    values = result.result.values
    assert values["groups"] == ["Control", "Low", "High"] and values["subjects"] == len(SUBJECTS)
    assert "trend" in values and "logrank" in values and "gehan" in values
    headers, rows = result_rows(result)
    assert ("Median survival", None) in rows and ("Log-rank (Mantel-Cox) test", None) in rows
    view = window.stack.currentWidget()
    assert isinstance(view, ResultSheetView) and view.options_button.isVisibleTo(view)
    # the results follow the table
    table.xy.values[0, 0, 0] = 0.0
    window.project.data_changed(table.id)
    assert result.error == "" and result.result.values["curves"]["Control"]["censored"].sum() == 3
    window.change_options(result, {"trend": False, "ci": 0.9, "alpha": 0.01, "style": "APA"})
    assert result.error == "" and "trend" not in result.result.values and result.result.options["ci"] == 0.9
    window.change_options(result, {"trend": "yes", "ci": "wide", "alpha": None, "style": 3})  # garbage falls back to the defaults
    assert result.error == "" and "trend" not in result.result.values


def test_dialog_accept_and_trend_checkbox(window):
    from cellquant.gui.quantanalysis.dialogs import SURVIVAL_DEFAULTS, SurvivalDialog

    two = _fill(window, groups=2)
    dialog = SurvivalDialog(window, two.xy, {"trend": True, "ci": 0.99})
    assert not dialog.trend.isEnabled() and not dialog.trend.isChecked() and dialog.options()["trend"] is False
    assert dialog.options()["ci"] == 0.99 and dialog.minimumSizeHint().width() <= 800
    assert dialog.trend.toolTip() and dialog.ci.toolTip() and dialog.alpha.toolTip() and dialog.style.toolTip()
    dialog.accept()
    assert dialog.result() == 1

    three = _fill(window, groups=3)
    dialog = SurvivalDialog(window, three.xy, {**SURVIVAL_DEFAULTS, "trend": True})
    assert dialog.trend.isEnabled() and dialog.trend.isChecked() and dialog.options()["trend"] is True
    dialog.accept()
    assert dialog.result() == 1

    empty = window.new_survival_table()  # nothing to analyze: the engine's message shows in the dialog
    dialog = SurvivalDialog(window, empty.xy)
    dialog.accept()
    assert dialog.result() == 0 and "at least one subject" in dialog.note.text()


def test_analyze_through_the_dialog(window, monkeypatch):
    from cellquant.gui.quantanalysis import dialogs

    _fill(window)
    monkeypatch.setattr(dialogs.SurvivalDialog, "exec", lambda self: (self.trend.setChecked(True), self.accept(), 1)[-1])
    result = window.analyze("survival")
    assert result.error == "" and result.result.options["trend"] is True and "trend" in result.result.values


def test_refusals(window):
    from cellquant.stats import ColumnTable

    count = len(window.project.results)
    column = window.new_table()
    column.table = ColumnTable.from_columns({"A": [1.0, 2.0], "B": [2.0, 3.0]})
    window.project.data_changed(column.id)
    window.select(column.id)
    assert window.analyze("survival", {}) is None and "XY table" in window.statusBar().currentMessage()
    window.select(window.new_contingency_table().id)
    assert window.analyze("survival", {}) is None
    assert len(window.project.results) == count


def test_blank_rows_and_replicates_warn(window):
    table = _fill(window, blank_rows=2)
    table.xy.values[-1, 0, 0] = 1.0  # a status with no time
    window.project.data_changed(table.id)
    result = window.analyze("survival", {})
    assert result.error == "" and any("left out" in text for text in result.warnings)
    wide = _fill(window, replicates=2)
    wide.xy.values[0, 0, 1] = 0.0
    window.project.data_changed(wide.id)
    result = window.analyze("survival", {})
    assert any("first replicate" in text for text in result.warnings)


def test_engine_errors_surface(window):
    table = _fill(window)
    table.xy.values[0, 1, 0] = 1.0  # a subject with a status in two groups
    window.project.data_changed(table.id)
    result = window.analyze("survival", {})
    assert result is not None and "more than one group" in result.error
    table.xy.values[0, 1, 0] = np.nan
    table.xy.values[1, 0, 0] = 2.0  # a status other than 0 or 1
    window.project.data_changed(table.id)
    window.project.rerun(result)
    assert "status other than" in result.error


def test_survival_graph(window):
    from cellquant.gui.quantanalysis.dialogs import SURVIVAL_DEFAULTS
    from cellquant.gui.quantanalysis.graph_view import GraphSheetView

    table = _fill(window)
    result = window.analyze("survival", dict(SURVIVAL_DEFAULTS))
    graph = next(item for item in window.project.graphs.values() if item.result_id == result.id)
    assert graph.name == f"{result.name} (graph)"
    window.select(graph.id)
    view = window.stack.currentWidget()
    assert isinstance(view, GraphSheetView)
    ax = view.figure._cq_axes["main"]
    values = result.result.values
    steps = [line for line in ax.get_lines() if line.get_linestyle() == "-" and line.get_marker() in ("None", "", None)]
    assert len(steps) == 3
    for line, name in zip(steps, values["groups"]):
        curve = values["curves"][name]
        assert line.get_xdata()[0] == 0 and line.get_ydata()[0] == 100
        assert np.allclose(line.get_ydata()[1:], 100 * curve["survival"]) and line.get_drawstyle() == "steps-post"
    ticks = [line for line in ax.get_lines() if line.get_marker() == "|"]
    assert len(ticks) == 3
    for line, name in zip(ticks, values["groups"]):
        curve = values["curves"][name]
        censored = curve[curve["censored"] > 0]
        assert len(line.get_xdata()) == len(censored) and np.allclose(line.get_xdata(), censored["time"])
    assert ax.get_ylim() == (0, 100) and ax.get_xlim()[0] == 0
    assert ax.get_ylabel() == "Percent survival" and ax.get_xlabel() == "Time (months)"
    assert ax.get_title(loc="left") == f"N = {len(SUBJECTS)} subjects"
    assert not ax.collections  # no band until asked for
    assert view.figure._cq_legend is not None and [t.get_text() for t in view.figure._cq_legend.get_texts()] == ["Control", "Low", "High"]

    graph.format.xy_band = "confidence"
    graph.format["title"] = "Trial"
    graph.format["y"] = {"title": "Alive (%)"}
    window.project.changed.emit(graph.id)
    view.flush()
    ax = view.figure._cq_axes["main"]
    assert len(ax.collections) == 3 and ax.get_ylabel() == "Alive (%)" and ax.get_title(loc="left").startswith("Trial\nN = ")
    assert graph.format.xy_band == "confidence"

    window.select(table.id)
    other = window.new_graph("xy")  # a new XY graph of the table is not a survival graph
    assert other.result_id is None


def test_project_round_trip(window, tmp_path):
    from cellquant.gui.quantanalysis import project_file
    from cellquant.gui.quantanalysis.dialogs import SURVIVAL_DEFAULTS
    from cellquant.gui.quantanalysis.project import Project

    _fill(window)
    result = window.analyze("survival", {**SURVIVAL_DEFAULTS, "trend": True, "ci": 0.9})
    graph = next(item for item in window.project.graphs.values() if item.result_id == result.id)
    path = project_file.save(window.project, tmp_path / "survival.cqstats")
    other = Project()
    _current, notes = project_file.load(path, other)
    assert notes == []
    loaded = other.results[result.id]
    assert loaded.kind == "survival" and loaded.error == "" and loaded.wide is not None and loaded.options["trend"] is True
    assert loaded.result.values["logrank"]["p"] == pytest.approx(result.result.values["logrank"]["p"])
    assert loaded.result.values["groups"] == result.result.values["groups"] and loaded.result.values["subjects"] == len(SUBJECTS)
    assert other.graphs[graph.id].result_id == result.id and other.graphs[graph.id].kind == "xy"
    loaded.options.update(trend=None, ci="x", alpha=[], style=5)  # a damaged file still runs
    other.rerun(loaded)
    assert loaded.error == ""
