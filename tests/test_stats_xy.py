"""XY tables: linear regression, bands, comparing lines and correlation, checked against scipy and statsmodels."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from cellquant.errors import StatisticsError
from cellquant.stats.xy import XYTable, band, correlation, fit_line, linear_regression, xy_correlation

X = np.array([0.0, 1.0, 2.0, 4.0, 8.0])
# 2 data sets × 3 replicates; row 3 of B has a blank
VALUES = np.array([
    [[1.1, 0.9, 1.3], [2.0, 2.4, 1.8]],
    [[2.0, 2.3, 1.8], [2.9, 3.1, 3.3]],
    [[3.2, 2.9, 3.0], [4.1, np.nan, 3.8]],
    [[5.1, 4.8, 5.3], [5.2, 5.6, 5.0]],
    [[9.2, 8.8, 9.1], [8.1, 7.7, 8.4]],
])


def _table() -> XYTable:
    return XYTable("Response", "Dose", X, ["A", "B"], VALUES)


def test_line_matches_scipy_and_bands_match_statsmodels():
    table = _table()
    x, y = table.points(0)
    fit = fit_line(x, y)
    reference = stats.linregress(x, y)
    assert fit["slope"] == pytest.approx(reference.slope) and fit["intercept"] == pytest.approx(reference.intercept)
    assert np.sqrt(fit["cov"][0, 0]) == pytest.approx(reference.stderr) and np.sqrt(fit["cov"][1, 1]) == pytest.approx(reference.intercept_stderr)
    assert fit["r2"] == pytest.approx(reference.rvalue**2) and fit["p"] == pytest.approx(reference.pvalue)
    sm = pytest.importorskip("statsmodels.api")
    model = sm.OLS(y, sm.add_constant(x)).fit()
    grid = np.array([0.0, 3.0, 8.0])
    frame = model.get_prediction(sm.add_constant(grid)).summary_frame(alpha=0.05)
    low, high = band(fit, grid)
    assert low == pytest.approx(frame["mean_ci_lower"].to_numpy()) and high == pytest.approx(frame["mean_ci_upper"].to_numpy())
    low, high = band(fit, grid, prediction=True)
    assert low == pytest.approx(frame["obs_ci_lower"].to_numpy()) and high == pytest.approx(frame["obs_ci_upper"].to_numpy())
    origin = fit_line(x, y, through_origin=True)
    through = sm.OLS(y, x).fit()
    assert origin["slope"] == pytest.approx(through.params[0]) and np.sqrt(origin["cov"][0, 0]) == pytest.approx(through.bse[0])
    assert origin["df"] == x.size - 1 and origin["r2"] == pytest.approx(through.rsquared)


def test_regression_sheet_replicates_and_comparing_lines():
    table = _table()
    result = linear_regression(table)
    fits = result.values["fits"]
    assert fits["A"]["n"] == 15 and fits["B"]["n"] == 14  # the blank is left out
    wide = result.values["wide"]
    assert list(wide.columns) == ["A", "B"] and wide.loc[("Best-fit values", "Slope"), "A"] == f"{fits['A']['slope']:.4g}"
    assert fits["A"]["equation"].startswith("Y = ") and "Student's t" in result.methods
    means = linear_regression(table, replicates="mean").values["fits"]["A"]
    x, y = table.points(0, means=True)
    assert means["n"] == 5 and means["slope"] == pytest.approx(stats.linregress(x, y).slope)
    # Comparing slopes: the interaction F test of an ANCOVA
    smf = pytest.importorskip("statsmodels.formula.api")
    sm = pytest.importorskip("statsmodels.api")
    rows = [(xv, yv, name) for j, name in enumerate(table.sets) for xv, yv in zip(*table.points(j))]
    frame = pd.DataFrame(rows, columns=["x", "y", "g"])
    full = smf.ols("y ~ x * C(g)", frame).fit()
    common = smf.ols("y ~ x + C(g)", frame).fit()
    f, p, _df = full.compare_f_test(common)
    comparison = result.values["comparison"]
    assert comparison["slopes_f"] == pytest.approx(f) and comparison["slopes_p"] == pytest.approx(p)
    single = smf.ols("y ~ x", frame).fit()
    f, p, _df = common.compare_f_test(single)
    assert comparison["intercepts_f"] == pytest.approx(f) and comparison["intercepts_p"] == pytest.approx(p)
    assert ("Are the slopes equal?", "P value") in result.values["wide"].index
    assert sm is not None


def test_bad_tables_and_short_data_sets():
    with pytest.raises(StatisticsError, match="shaped"):
        XYTable("", "X", [1, 2], ["A"], np.zeros((3, 1, 1)))
    with pytest.raises(StatisticsError, match="unique"):
        XYTable("", "X", [1], ["A", "A"], np.zeros((1, 2, 1)))
    with pytest.raises(StatisticsError, match="same X"):
        fit_line(np.ones(4), np.arange(4.0))
    short = XYTable("", "X", [1.0, 2.0, 3.0], ["A", "B"], np.array([[[1.0], [1.0]], [[2.0], [np.nan]], [[3.5], [np.nan]]]))
    result = linear_regression(short)
    assert list(result.values["fits"]) == ["A"] and "B: Linear regression needs at least 3 points" in result.warnings[0]
    assert result.values["comparison"] is None
    with pytest.raises(StatisticsError, match="No data set"):
        linear_regression(XYTable("", "X", [1.0, 2.0], ["A"], np.ones((2, 1, 1))))
    perfect = XYTable("", "X", [1.0, 2.0, 3.0, 4.0], ["A"], np.array([2.0, 4.0, 6.0, 8.0]).reshape(4, 1, 1))
    fit = linear_regression(perfect).values["fits"]["A"]
    assert fit["r2"] == pytest.approx(1.0) and fit["sy_x"] == 0.0 and np.isnan(fit["f"])
    frame = pd.DataFrame({"dose": [1, 1, 2, 2, "x"], "y": [3.0, 4.0, 5.0, 6.0, 1.0], "g": ["a", "a", "a", "b", "b"]})
    long = XYTable.from_long(frame, "dose", "y", "g")
    assert long.x.tolist() == [1.0, 2.0] and long.sets == ["a", "b"] and long.values[0, 0].tolist() == [3.0, 4.0]


def test_correlation_matches_scipy():
    table = _table()
    result = xy_correlation(table)
    x, y = table.points(0)
    r, p = stats.pearsonr(x, y)
    item = result.values["correlations"]["A"]
    assert item["r"] == pytest.approx(r) and item["p"] == pytest.approx(p) and item["n"] == 15
    z = np.arctanh(r)
    half = stats.norm.ppf(0.975) / np.sqrt(15 - 3)
    assert (item["ci_low"], item["ci_high"]) == pytest.approx((np.tanh(z - half), np.tanh(z + half)))
    spearman = xy_correlation(table, method="spearman").values["correlations"]["B"]
    assert spearman["r"] == pytest.approx(stats.spearmanr(*table.points(1)).statistic)
    with pytest.raises(StatisticsError, match="at least 3"):
        correlation(np.array([1.0, 2.0]), np.array([1.0, 3.0]))
    with pytest.raises(StatisticsError, match="does not vary"):
        correlation(np.array([1.0, 2.0, 3.0]), np.array([1.0, 1.0, 1.0]))
