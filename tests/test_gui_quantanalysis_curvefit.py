"""QuantAnalysis curve fits: the dialog, the results sheet, curves and bands on XY graphs, unknowns and projects.
Needs Qt (xvfb-run on Linux) and matplotlib."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("matplotlib")

from tests.test_stats_curvefit import TRUE, _dose  # noqa: E402

X = np.concatenate([np.linspace(-9.0, -4.0, 11), [np.nan]])


@pytest.fixture
def window(qtbot):
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    window = QuantAnalysisWindow()
    qtbot.addWidget(window)
    window.resize(1400, 900)
    window.show()
    return window


def _filled(window):
    from cellquant.gui.quantanalysis.project import set_xy
    from cellquant.stats.xy import XYTable

    rng = np.random.default_rng(3)
    a = _dose(X, *TRUE.values())[:, None] + rng.normal(0, 3, (X.size, 3))
    b = _dose(X, 5, 95, -5.6, 1.2)[:, None] + rng.normal(0, 3, (X.size, 3))
    values = np.stack([a, b], axis=1)
    values[11] = np.nan
    values[11, 0] = [40.0, 42.0, 44.0]  # an unknown: Y with no X
    sheet = window.new_xy_table()
    set_xy(sheet, XYTable("Response", "log dose", X, ["A", "B"], values))
    window.project.data_changed(sheet.id)
    window.views.pop(sheet.id, None)
    window.select(sheet.id)
    return sheet


def test_dialog_options_and_refusals(window):
    from cellquant.gui.quantanalysis.dialogs import CurveFitDialog

    sheet = _filled(window)
    dialog = CurveFitDialog(window, sheet.xy, {"compare": "datasets", "compare_parameter": "LogEC50", "fixed": {"Bottom": 0}})
    assert dialog.compare.currentData() == "parameter" and dialog.parameter_box.isVisibleTo(dialog)
    assert dialog.fixed.rows["Bottom"][0].isChecked() and dialog.parameter.findData("Bottom") == -1  # a fixed parameter is not compared
    options = dialog.options()
    assert options["compare"] == "datasets" and options["compare_parameter"] == "LogEC50" and options["fixed"] == {"Bottom": 0.0}
    dialog.model.setCurrentIndex(dialog.model.findData("michaelis_menten"))
    assert list(dialog.fixed.rows) == ["Vmax", "Km"] and "Km" in dialog.equation.text()
    dialog.compare.setCurrentIndex(dialog.compare.findData("model"))
    assert dialog.second.isVisibleTo(dialog) and not dialog.parameter_box.isVisibleTo(dialog)
    box, value = dialog.second_fixed.rows["Bottom"]
    box.setChecked(True)
    value.setText("lots")
    dialog.accept()
    assert dialog.note.isVisibleTo(dialog) and "Bottom" in dialog.note.text() and dialog.result() == 0
    value.setText("0")
    dialog.accept()
    assert "X ≥ 0" in dialog.note.text()  # Michaelis-Menten on log doses is refused before the sheet is made


def test_fit_sheet_graph_and_project(window, tmp_path):
    from cellquant.gui.quantanalysis import project_file
    from cellquant.gui.quantanalysis.dialogs import CURVEFIT_DEFAULTS
    from cellquant.gui.quantanalysis.graph_view import GraphSheetView
    from cellquant.gui.quantanalysis.project import Project
    from cellquant.gui.quantanalysis.results_view import ResultSheetView, result_rows
    from cellquant.stats.curvefit import predict

    sheet = _filled(window)
    fit = window.analyze("curvefit", {**CURVEFIT_DEFAULTS, "compare": "datasets", "compare_parameter": "LogEC50", "interpolate": True})
    assert fit.error == "" and fit.name == f"Nonlinear regression (Dose-response, X is log(dose)): {sheet.name}"
    headers, rows = result_rows(fit)
    assert headers == ["", "A", "B"] and ("Best-fit values", None) in rows
    assert isinstance(window.stack.currentWidget(), ResultSheetView) and window.stack.currentWidget().options_button.isVisible()
    values = fit.result.values
    assert values["fits"]["A"]["params"]["LogEC50"] == pytest.approx(-6.5, abs=0.1) and values["datasets"]["f_prefers"] == "separate"
    unknown = values["interpolated"]["A"][0]
    assert unknown["row"] == 11 and predict(values["fits"]["A"], [unknown["x"]])[0] == pytest.approx(42.0)

    window.select(sheet.id)
    graph = window.new_graph("xy")
    assert graph.result_id == fit.id
    view = window.stack.currentWidget()
    assert isinstance(view, GraphSheetView) and view.panel["graph"].comparison.currentData() == fit.id
    section = view.panel["graph"]
    section.xy_band.setCurrentIndex(section.xy_band.findData("prediction"))
    view.flush()
    ax = view.figure._cq_axes["main"]
    curves = [line for line in ax.lines if getattr(line, "_cq_target", None) == ("dataset", "A") and len(line.get_xdata()) == 200]
    assert len(curves) == 1 and curves[0].get_ydata() == pytest.approx(predict(values["fits"]["A"], curves[0].get_xdata()))
    assert len([item for item in ax.collections if getattr(item, "_cq_target", None) == ("dataset", "B")]) >= 2  # points and band

    # Changing the options re-fits; saving and loading keeps the fit, its constraints and the graph link.
    window.change_options(fit, {**fit.options, "fixed": {"HillSlope": 1.0}, "compare": "model", "compare_model": "log_dose_response",
                                "compare_fixed": {}, "compare_parameter": None})
    assert fit.result.values["fits"]["A"]["params"]["HillSlope"] == 1.0 and "A" in fit.result.values["comparisons"]
    path = project_file.save(window.project, tmp_path / "fit.cqstats")
    other = Project()
    _current, notes = project_file.load(path, other)
    loaded = other.results[fit.id]
    assert notes == [] and loaded.kind == "curvefit" and loaded.options["fixed"] == {"HillSlope": 1.0}
    assert loaded.result.values["fits"]["B"]["params"]["LogEC50"] == pytest.approx(fit.result.values["fits"]["B"]["params"]["LogEC50"])
    assert other.graphs[graph.id].result_id == fit.id
    loaded.options.update(fixed="garbage", compare_fixed=[1], compare="none")  # a damaged file still fits
    other.rerun(loaded)
    assert loaded.error == "" and loaded.result.values["fits"]["A"]["params"]["HillSlope"] != 1.0


def test_ambiguous_fits_are_dashed_on_graphs():
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    from cellquant.gui.quantanalysis import graphs
    from cellquant.gui.quantanalysis.project import DataSheet, GraphSheet, set_xy
    from cellquant.stats.curvefit import nonlinear_regression
    from cellquant.stats.xy import XYTable

    x = np.linspace(-12, -10, 8)
    table = XYTable("Y", "log dose", x, ["Flat"], (5 + 1e-3 * np.sin(np.arange(8.0))).reshape(8, 1, 1))  # only the bottom plateau
    sheet = DataSheet("D", None)
    set_xy(sheet, table)
    result = nonlinear_regression(table)
    figure = Figure()
    FigureCanvasAgg(figure)
    notes = graphs.draw(figure, sheet, GraphSheet("G", sheet.id, "xy", {"xy_band": "confidence"}), result)
    curve = [line for line in figure._cq_axes["main"].lines if len(line.get_xdata()) == 200]
    assert curve[0].get_linestyle() == "--" and any("dashed" in note for note in notes) and any("No band" in note for note in notes)
