"""Outlier identification (ROUT, Grubbs', iterative Grubbs'): published critical values, the methods' own definitions
written out independently, and simulations of the false-positive rate."""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import stats

from cellquant.errors import StatisticsError
from cellquant.stats import ColumnTable, grubbs_fit, identify_outliers, rout_fit
from cellquant.stats.outliers import _grubbs_once

CLEAN = [10.1, 9.8, 10.3, 10.0, 9.9, 10.2, 10.05, 9.95, 10.15]


@pytest.mark.parametrize("n, critical", [(3, 1.155), (5, 1.715), (10, 2.290), (20, 2.709), (30, 2.908)])
def test_grubbs_critical_values_match_grubbs_1969_table(n, critical):
    # Two-sided 5% critical values of G (Grubbs 1969, Technometrics 11:1-21; the same values appear in most outlier tables).
    rng = np.random.default_rng(n)
    assert _grubbs_once(rng.normal(size=n), 0.05)["critical"] == pytest.approx(critical, abs=1.5e-3)


def test_grubbs_p_is_alpha_at_the_critical_value_and_flags_alpha_of_gaussian_samples():
    x = np.array(CLEAN + [10.0])
    base = _grubbs_once(x, 0.05)
    # Move the most extreme value until G equals the critical value: P must then equal alpha.
    i = int(np.argmax(np.abs(x - x.mean())))

    def g_minus_critical(value):
        y = x.copy()
        y[i] = value
        test = _grubbs_once(y, 0.05)
        return test["g"] - test["critical"]

    from scipy.optimize import brentq
    value = brentq(g_minus_critical, x[i], x[i] + 5 * np.std(x, ddof=1) * (1 if x[i] > x.mean() else -1))
    y = x.copy()
    y[i] = value
    assert _grubbs_once(y, 0.05)["p"] == pytest.approx(0.05, rel=1e-6)
    assert not base["outlier"]
    rng = np.random.default_rng(0)
    rate = np.mean([grubbs_fit(rng.normal(size=10))["mask"].any() for _ in range(4000)])
    assert 0.035 < rate < 0.065


def test_grubbs_and_iterative_grubbs_on_one_or_two_outliers():
    one = CLEAN + [14.5]
    fit = grubbs_fit(one)
    assert np.nonzero(fit["mask"])[0].tolist() == [9] and fit["rounds"][0]["row"] == 10 and fit["rounds"][0]["value"] == 14.5
    two = CLEAN + [14.5, 11.5]
    assert grubbs_fit(two)["mask"].sum() == 1
    assert grubbs_fit(two, iterative=True)["mask"].sum() == 2
    # Two equal outliers mask each other: Grubbs' finds neither (a known weakness), ROUT finds both.
    masked = [1, 2, 3, 2, 1, 30, 31, 2, 1, 2]
    assert grubbs_fit(masked, iterative=True)["mask"].sum() == 0
    assert rout_fit(masked)["mask"].tolist() == [False] * 5 + [True, True] + [False] * 3


def test_rout_follows_its_published_definition():
    x = np.array(CLEAN + [14.5, 10.4, 9.7, 13.0])
    fit = rout_fit(x, q=0.01)
    centre, rsdr = fit["centre"], fit["rsdr"]
    n = x.size
    # RSDR = 68.27th percentile of |residuals| × N/(N - 1), at the robust centre.
    assert rsdr == pytest.approx(np.percentile(np.abs(x - centre), 68.27) * n / (n - 1))
    # The centre minimizes the Lorentzian merit function at that scale.
    merit = lambda mu: np.sum(np.log1p(((x - mu) / rsdr) ** 2))  # noqa: E731
    grid = np.linspace(x.min(), x.max(), 5001)
    assert merit(centre) <= min(merit(g) for g in grid) + 1e-9
    # t = |residual|/RSDR, two-tailed P with N - 1 df, then the Benjamini-Hochberg step-up rule at Q.
    p = 2 * stats.t.sf(np.abs(x - centre) / rsdr, n - 1)
    assert fit["p"] == pytest.approx(p)
    order = np.argsort(p)
    passing = [j for j in range(n) if p[order[j]] <= 0.01 * (j + 1) / n]
    expected = np.zeros(n, bool)
    if passing:
        expected[order[: max(passing) + 1]] = True
    assert np.array_equal(fit["mask"], expected)
    assert fit["mask"][9] and not fit["mask"][:9].any()
    # A larger Q never flags fewer values.
    assert np.all(rout_fit(x, q=0.1)["mask"] >= fit["mask"])


def test_rout_rarely_flags_gaussian_samples():
    rng = np.random.default_rng(1)
    rate = np.mean([rout_fit(rng.normal(size=12), q=0.01)["mask"].any() for _ in range(600)])
    assert rate < 0.03


def test_rout_edge_cases():
    fit = rout_fit([5, 5, 5, 5, 5, 5, 5, 5, 9])
    assert not fit["mask"].any() and "identical" in fit["note"]
    assert not rout_fit([1, 2])["mask"].any()
    with pytest.raises(StatisticsError):
        rout_fit(CLEAN, q=0)


def test_identify_outliers_returns_flags_and_a_cleaned_copy():
    table = ColumnTable.from_columns({"A": CLEAN + [14.5], "B": [1, 2, 3, 2, 1, 30, 31, 2, 1, 2], "C": [1, None]}, title="Values", paired=False)
    before = {c.name: c.values.copy() for c in table.columns}
    result = identify_outliers(table, "rout")
    v = result.values
    assert v["outliers"]["A"] == [{"row": 10, "value": 14.5}]
    assert [r["row"] for r in v["outliers"]["B"]] == [6, 7] and v["n_outliers"] == 3
    cleaned = v["cleaned"]
    assert np.isnan(cleaned["A"].values[9]) and cleaned["A"].values.size == 10 and cleaned.title == "Values (cleaned)"
    assert np.array_equal(cleaned["B"].values, [1, 2, 3, 2, 1, np.nan, np.nan, 2, 1, 2], equal_nan=True)
    for column in table.columns:
        assert np.array_equal(column.values, before[column.name], equal_nan=True)
    assert any("fewer than 3" in w for w in result.warnings)
    assert result.row("Column: A", "Outliers").formatted == "14.5 (row 10)"
    grubbs = identify_outliers(table, "grubbs", alpha=0.05)
    assert grubbs.values["outliers"]["A"] == [{"row": 10, "value": 14.5}] and grubbs.values["outliers"]["B"] == []
    assert identify_outliers(table, "iterative_grubbs", columns=["A"]).values["outliers"] == {"A": [{"row": 10, "value": 14.5}]}
    with pytest.raises(StatisticsError):
        identify_outliers(table, "dixon")
    paired = ColumnTable.from_columns({"A": CLEAN + [14.5], "B": CLEAN + [10.0]}, paired=True)
    assert identify_outliers(paired).values["cleaned"].paired
    assert math.isnan(identify_outliers(paired).values["cleaned"]["A"].values[-1])
