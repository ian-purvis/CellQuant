"""Nonlinear regression: built-in models and their starting values, SEs and bands, comparing fits and data sets,
interpolation; checked against scipy's curve_fit and statsmodels."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scipy.optimize import curve_fit

from cellquant.errors import StatisticsError
from cellquant.stats.curvefit import (
    MODELS,
    aicc,
    compare_data_sets,
    compare_models,
    curve_band,
    fit_curve,
    interpolate_x,
    nonlinear_regression,
    predict,
)
from cellquant.stats.xy import XYTable, _compare_lines, fit_line

RNG = np.random.default_rng(1)
LOG_X = np.repeat(np.linspace(-9.0, -4.0, 11), 3)
TRUE = {"Bottom": 5.0, "Top": 95.0, "LogEC50": -6.5, "HillSlope": 1.2}


def _dose(x, bottom, top, log_ec50, hill):
    return bottom + (top - bottom) / (1 + 10 ** ((log_ec50 - x) * hill))


Y = _dose(LOG_X, *TRUE.values()) + RNG.normal(0, 4, LOG_X.size)


def test_dose_response_matches_curve_fit():
    fit = fit_curve("log_dose_response", LOG_X, Y)
    params, cov = curve_fit(_dose, LOG_X, Y, p0=[0, 100, -6, 1])
    assert list(fit["params"].values()) == pytest.approx(params, rel=1e-5)
    assert list(fit["se"].values()) == pytest.approx(np.sqrt(np.diag(cov)), rel=1e-4)
    assert fit["df"] == LOG_X.size - 4 and fit["converged"] and not fit["ambiguous"]
    residual = ((Y - _dose(LOG_X, *params)) ** 2).sum()
    assert fit["ss_resid"] == pytest.approx(residual) and fit["sy_x"] == pytest.approx(np.sqrt(residual / fit["df"]))
    n, k = LOG_X.size, 5
    assert fit["aicc"] == pytest.approx(n * np.log(residual / n) + 2 * k + 2 * k * (k + 1) / (n - k - 1))
    # Holding HillSlope at 1 fits the other three only.
    held = fit_curve("log_dose_response", LOG_X, Y, {"HillSlope": 1})
    params, _cov = curve_fit(lambda x, b, t, e: _dose(x, b, t, e, 1.0), LOG_X, Y, p0=[0, 100, -6])
    assert held["free"] == ["Bottom", "Top", "LogEC50"] and held["params"]["HillSlope"] == 1.0
    assert [held["params"][name] for name in held["free"]] == pytest.approx(params, rel=1e-5)


@pytest.mark.parametrize("key, x, truth", [
    ("log_dose_response", np.linspace(-9, -4, 12), [10, 80, -6.2, -0.9]),
    ("dose_response", np.geomspace(1e-3, 1e3, 12), [2, 50, 1.5, 1.4]),
    ("michaelis_menten", np.array([0.5, 1, 2, 4, 8, 16, 32, 64.0]), [120, 6.0]),
    ("one_phase", np.linspace(0, 30, 12), [100, 12, 0.21]),
    ("one_phase", np.linspace(0, 30, 12), [3, 40, 0.1]),
    ("exponential_growth", np.linspace(0, 10, 9), [2.0, 0.35]),
    ("gaussian", np.linspace(-10, 30, 15), [7.0, 12.0, 5.0]),
    ("polynomial_3", np.linspace(-3, 3, 9), [1, -2, 0.5, 0.3]),
])
def test_every_model_finds_its_own_starting_values(key, x, truth):
    model = MODELS[key]
    y = model.f(x, np.array(truth, float))
    fit = fit_curve(key, x, y)
    assert [fit["params"][name] for name in model.params] == pytest.approx(truth, rel=1e-5, abs=1e-8)
    assert fit["r2"] == pytest.approx(1.0)


def test_bands_of_a_straight_line_match_statsmodels():
    sm = pytest.importorskip("statsmodels.api")
    x = np.array([0.0, 1, 2, 4, 8, 8, 3])
    y = np.array([1.1, 2.0, 3.2, 5.1, 9.2, 8.8, 3.6])
    fit = fit_curve("line", x, y)
    model = sm.OLS(y, sm.add_constant(x)).fit()
    assert [fit["params"]["Intercept"], fit["params"]["Slope"]] == pytest.approx(model.params)
    assert [fit["se"]["Intercept"], fit["se"]["Slope"]] == pytest.approx(model.bse, rel=1e-5)
    grid = np.array([0.0, 3.5, 8.0])
    frame = model.get_prediction(sm.add_constant(grid)).summary_frame(alpha=0.05)
    assert curve_band(fit, grid)[0] == pytest.approx(frame["mean_ci_lower"].to_numpy(), rel=1e-5)
    assert curve_band(fit, grid, prediction=True)[1] == pytest.approx(frame["obs_ci_upper"].to_numpy(), rel=1e-5)
    # The same as the linear regression of #123's fit_line.
    line = fit_line(x, y)
    assert predict(fit, grid) == pytest.approx(line["slope"] * grid + line["intercept"])


def test_comparing_fits_and_data_sets_matches_statsmodels():
    smf = pytest.importorskip("statsmodels.formula.api")
    x = np.linspace(-2, 4, 14)
    y = 1 + 0.8 * x - 0.15 * x**2 + RNG.normal(0, 0.3, x.size)
    line, quadratic = fit_curve("line", x, y), fit_curve("polynomial_2", x, y)
    item = compare_models(quadratic, line)  # either order
    frame = pd.DataFrame({"x": x, "y": y})
    f, p, dfn = smf.ols("y ~ x + I(x**2)", frame).fit().compare_f_test(smf.ols("y ~ x", frame).fit())
    assert item["simple"] is line and item["nested"] and (item["f"], item["p"], item["dfn"]) == pytest.approx((f, p, dfn))
    delta = quadratic["aicc"] - line["aicc"]
    assert item["aicc_delta"] == pytest.approx(delta) and item["aicc_simple"] == pytest.approx(1 / (1 + np.exp(-delta / 2)))
    assert not compare_models(fit_curve("gaussian", x, y), quadratic)["nested"]

    # Two data sets: a shared slope is the slope test of an analysis of covariance; one curve for both is the full test.
    x2 = np.linspace(0, 6, 10)
    data = {"A": (x, y), "B": (x2, 2.5 + 0.4 * x2 + RNG.normal(0, 0.3, x2.size))}
    fits = {name: fit_curve("line", *points) for name, points in data.items()}
    slopes = compare_data_sets("line", data, fits, "Slope")
    reference = _compare_lines({name: fit_line(*points) for name, points in data.items()}, data, False)
    assert slopes["f"] == pytest.approx(reference["slopes_f"]) and slopes["p"] == pytest.approx(reference["slopes_p"])
    pooled = pd.DataFrame([(a, b, name) for name, (xs, ys) in data.items() for a, b in zip(xs, ys)], columns=["x", "y", "g"])
    f, p, dfn = smf.ols("y ~ x * C(g)", pooled).fit().compare_f_test(smf.ols("y ~ x", pooled).fit())
    every = compare_data_sets("line", data, fits, None)
    assert (every["f"], every["p"], every["dfn"]) == pytest.approx((f, p, dfn)) and every["f_prefers"] == "separate"


def test_results_sheet_comparisons_and_interpolation():
    x = np.concatenate([np.linspace(-9, -4, 11), [np.nan, np.nan, np.nan]])
    a = _dose(x, *TRUE.values())
    values = np.stack([a, a + 10 + RNG.normal(0, 0.5, x.size)], axis=1)[:, :, None]
    values[11:, 0, 0] = [50.0, 94.9, 200.0]  # unknowns of data set A: inside, near the top, above the curve
    values[11:, 1, 0] = np.nan
    table = XYTable("Response", "log dose", x, ["A", "B"], values)
    result = nonlinear_regression(table, interpolate=True, compare="datasets")
    fits = result.values["fits"]
    assert fits["A"]["derived"]["EC50"] == pytest.approx(10**-6.5) and fits["A"]["derived"]["Span"] == pytest.approx(90)
    found = result.values["interpolated"]["A"]
    assert [item["row"] for item in found] == [11, 12, 13] and np.isnan(found[2]["x"]) and result.values["interpolated"]["B"] == []
    assert found[0]["x"] == pytest.approx(np.log10(10**-6.5 * (45 / 45) ** (1 / 1.2)) + np.log10((50 - 5) / (95 - 50)) / 1.2, abs=1e-6)
    assert predict(fits["A"], [found[1]["x"]])[0] == pytest.approx(94.9)
    wide = result.values["wide"]
    assert wide.loc[("Interpolated X (rows with Y but no X)", "Row 14: Y = 200"), "A"] == "out of range"
    assert "dose" in wide.loc[("Interpolated X (rows with Y but no X)", "Row 12: Y = 50"), "A"]
    assert result.values["datasets"]["f_prefers"] == "separate" and ("Does one curve fit every data set?", "P value") in wide.index
    roots = interpolate_x(fit_curve("gaussian", np.linspace(-5, 5, 11), np.exp(-0.5 * np.linspace(-5, 5, 11) ** 2)), 0.5)
    assert roots == pytest.approx([-1.1774, 1.1774], abs=1e-3)

    held = nonlinear_regression(table, fixed={"HillSlope": 1}, compare="model", compare_model="log_dose_response")
    item = held.values["comparisons"]["B"]  # A has no noise: no F test when the larger fit is exact
    assert item["nested"] and item["f_prefers"] == "complex"
    assert held.values["wide"].loc[("Best-fit values", "HillSlope"), "A"] == "1 (fixed)" and "HillSlope = 1 fixed" in held.methods


def test_refusals_and_ambiguous_fits():
    with pytest.raises(StatisticsError, match="at least 5 points"):
        fit_curve("log_dose_response", [1, 2, 3, 4], [1, 2, 3, 4])
    with pytest.raises(StatisticsError, match="no parameter"):
        fit_curve("line", [1, 2, 3], [1, 2, 3], {"Top": 1})
    with pytest.raises(StatisticsError, match="Every parameter is fixed"):
        fit_curve("line", [1, 2, 3], [1, 2, 3], {"Slope": 1, "Intercept": 0})
    with pytest.raises(StatisticsError, match="X ≥ 0"):
        fit_curve("dose_response", [-1, 2, 3, 4, 5], [1, 2, 3, 4, 5])
    with pytest.raises(StatisticsError, match="Unknown model"):
        fit_curve("spline", [1, 2, 3], [1, 2, 3])
    table = XYTable("", "X", np.arange(6.0), ["A"], np.arange(6.0).reshape(6, 1, 1))
    with pytest.raises(StatisticsError, match="same as the first"):
        nonlinear_regression(table, model="line", compare="model", compare_model="line")
    assert np.isnan(aicc(1.0, 3, 2))
    # Points only on the lower plateau cannot pin down Top or LogEC50.
    flat = XYTable("", "log dose", np.linspace(-12, -10, 8), ["A"], np.full((8, 1, 1), 5.0) + RNG.normal(0, 1e-3, (8, 1, 1)))
    result = nonlinear_regression(flat)
    assert any("ambiguous" in text for text in result.warnings)


def test_unreliable_fits_are_not_compared():
    # A histogram of object areas with no peak (small debris in the first bin), as in a real results folder, beside a peaked one.
    x = np.linspace(-0.37, 2.96, 15)
    flat = np.array([147, 36, 45, 25, 34, 32, 55, 29, 14, 2, 1, 0, 0, 0, 0.0])
    peak = 47 * np.exp(-0.5 * ((x - 1.58) / 0.36) ** 2) + RNG.normal(0, 2, x.size)
    table = XYTable("Objects", "log10 area", x, ["Flat", "Peak"], np.stack([flat, peak], axis=1)[:, :, None])
    result = nonlinear_regression(table, model="gaussian", compare="datasets", compare_parameter="Mean")
    fits = result.values["fits"]
    assert not fits["Peak"]["ambiguous"] and fits["Peak"]["params"]["Mean"] == pytest.approx(1.58, abs=0.05)
    assert result.values["datasets"] is None and any("not compared" in text and "Flat" in text for text in result.warnings)
    result = nonlinear_regression(table, model="gaussian", compare="model", compare_model="polynomial_2")
    assert "Flat" not in result.values["comparisons"] and "Peak" in result.values["comparisons"]


def test_parameters_in_very_different_units_are_not_ambiguous():
    # X in molar: EC50 near 1e-8 beside a Top of 100. Unscaled, the Jacobian's columns differ by ~1e10.
    x = np.repeat(np.geomspace(1e-11, 1e-5, 10), 2)
    y = 3 + 97 / (1 + (2e-8 / x) ** 1.1) + RNG.normal(0, 2, x.size)
    fit = fit_curve("dose_response", x, y)
    params, cov = curve_fit(lambda v, b, t, e, h: b + (t - b) / (1 + (e / v) ** h), x, y, p0=[0, 100, 1e-8, 1], maxfev=20000)
    assert not fit["ambiguous"] and fit["params"]["EC50"] == pytest.approx(params[2], rel=1e-4)
    assert fit["se"]["EC50"] == pytest.approx(np.sqrt(cov[2, 2]), rel=1e-3)
    cubic = np.linspace(0, 1000, 12)
    fit = fit_curve("polynomial_3", cubic, 2 + 0.5 * cubic - 1e-3 * cubic**2 + 1e-6 * cubic**3 + RNG.normal(0, 1, cubic.size))
    assert not fit["ambiguous"] and np.isfinite(fit["se"]["B3"])


def test_comparisons_name_their_data_sets_and_refuse_bad_shared_fits(monkeypatch):
    from cellquant.stats import curvefit

    x = np.linspace(0, 10, 8)
    values = np.full((8, 3, 1), np.nan)
    values[:, 0, 0] = 1 + 2 * x + RNG.normal(0, 0.3, 8)
    values[:, 1, 0] = 3 + 2.5 * x + RNG.normal(0, 0.3, 8)
    values[:2, 2, 0] = [1.0, 2.0]  # too few points to fit
    table = XYTable("Y", "X", x, ["A", "B", "C"], values)
    result = nonlinear_regression(table, model="line", compare="datasets")
    wide = result.values["wide"]
    assert wide.loc[("Does one curve fit every data set?", "Data sets compared"), "A"] == "A, B"
    assert wide.loc[("Does one curve fit every data set?", "Left out (no fit)"), "A"] == "C" and "C could not be fitted" in result.methods
    monkeypatch.setattr(curvefit, "_CACHE", {})
    real = curvefit.global_fit
    monkeypatch.setattr(curvefit, "global_fit", lambda *args, **kwargs: {**real(*args, **kwargs), "converged": False})
    result = nonlinear_regression(table, model="line", compare="datasets", compare_parameter="Slope")
    assert result.values["datasets"] is None and any("Slope shared did not converge" in text for text in result.warnings)
    wide = result.values["wide"]  # not compared, but the sheet still says which data sets and why
    assert wide.loc[("Is Slope the same for every data set?", "Result"), "A"].startswith("Not compared")
    assert wide.loc[("Is Slope the same for every data set?", "Data sets"), "A"] == "A, B"
    monkeypatch.setattr(curvefit, "_CACHE", {})
    result = nonlinear_regression(table, model="line", compare="datasets", compare_parameter="Top")  # no such parameter: a warning
    assert result.values["datasets"] is None and any("not a free parameter" in text for text in result.warnings)


def test_labels_for_unranked_models_growth_and_ambiguity_rows():
    x = np.linspace(-5, 5, 11)
    table = XYTable("Y", "X", x, ["A"], (4 * np.exp(-0.5 * (x / 2) ** 2) + RNG.normal(0, 0.1, 11)).reshape(11, 1, 1))
    wide = nonlinear_regression(table, model="gaussian", compare="model", compare_model="polynomial_2").values["wide"]
    assert wide.loc[("Which fit is better?", "Model A"), "A"].startswith("Gaussian") and ("Which fit is better?", "Model B") in wide.index
    assert ("Which fit is better?", "Simpler model") not in wide.index and wide.loc[("Data", "Ambiguous"), "A"] == "No"
    from cellquant.stats.curvefit import _derived

    away = {"model": "one_phase", "params": {"Y0": 2.0, "Plateau": 10.0, "K": -0.2}}  # K < 0 runs away from the plateau: no half-life
    assert np.isnan(_derived(away, lambda _name: (-0.3, -0.1))[1][1])
    dose = nonlinear_regression(XYTable("Y", "dose", np.geomspace(0.01, 100, 8), ["A"],
                                        (100 / (1 + 1 / np.geomspace(0.01, 100, 8)) + RNG.normal(0, 1, 8)).reshape(8, 1, 1)),
                                model="dose_response")
    assert "can reach below 0" in dose.methods


def test_strongly_different_data_sets_are_still_compared():
    # Sharing LogEC50 between curves three log units apart leaves the shared fit's other parameters poorly determined; the
    # comparison only needs its sum of squares, so it reports a tiny P (and says the shared fit is ambiguous) instead of skipping.
    rng = np.random.default_rng(1)
    xs = np.linspace(-9, -3, 9)
    values = np.empty((9, 2, 3))
    for j, ec in enumerate((-7, -4)):
        values[:, j, :] = (100 / (1 + 10 ** (ec - xs)))[:, None] + rng.normal(0, 5, (9, 3))
    table = XYTable("Response", "log[dose]", xs, ["Low", "High"], values)
    result = nonlinear_regression(table, model="log_dose_response", compare="datasets", compare_parameter="LogEC50")
    datasets = result.values["datasets"]
    assert datasets is not None and datasets["p"] < 1e-6 and datasets["f_prefers"] == "separate" and datasets["shared"]["ambiguous"]
    assert any("ambiguous" in text and "still holds" in text for text in result.warnings)
    assert "poorly determined" in result.values["wide"].loc[("Is LogEC50 the same for every data set?", "Shared LogEC50"), "Low"]
