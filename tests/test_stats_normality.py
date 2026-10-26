"""Normality and lognormality tests, against published values and the tests' own formulas."""

from __future__ import annotations

import math
import sys

import numpy as np
import pytest
from scipy import stats

from cellquant.errors import StatisticsError
from cellquant.stats import lognormality, normality

# Shapiro and Wilk (1965), Biometrika 52:591, worked example: weights in pounds of 11 men. They report W = 0.79,
# below the 1% critical value, so P < 0.01.
WEIGHTS = [148, 154, 158, 160, 161, 162, 166, 170, 182, 195, 236]


def _dagostino_k2(x):
    """D'Agostino, Belanger and D'Agostino (1990), Am Stat 44:316: skewness and kurtosis z scores, K2 = Zs² + Zk²."""

    x = np.asarray(x, float)
    n = x.size
    m2 = np.mean((x - x.mean()) ** 2)
    sqrt_b1 = np.mean((x - x.mean()) ** 3) / m2**1.5
    b2 = np.mean((x - x.mean()) ** 4) / m2**2
    y = sqrt_b1 * math.sqrt((n + 1) * (n + 3) / (6 * (n - 2)))
    beta2 = 3 * (n * n + 27 * n - 70) * (n + 1) * (n + 3) / ((n - 2) * (n + 5) * (n + 7) * (n + 9))
    w2 = -1 + math.sqrt(2 * (beta2 - 1))
    delta = 1 / math.sqrt(0.5 * math.log(w2))
    alpha = math.sqrt(2 / (w2 - 1))
    zs = delta * math.asinh(y / alpha)
    mean_b2 = 3 * (n - 1) / (n + 1)
    var_b2 = 24 * n * (n - 2) * (n - 3) / ((n + 1) ** 2 * (n + 3) * (n + 5))
    xk = (b2 - mean_b2) / math.sqrt(var_b2)
    sqrt_beta1 = 6 * (n * n - 5 * n + 2) / ((n + 7) * (n + 9)) * math.sqrt(6 * (n + 3) * (n + 5) / (n * (n - 2) * (n - 3)))
    a = 6 + 8 / sqrt_beta1 * (2 / sqrt_beta1 + math.sqrt(1 + 4 / sqrt_beta1**2))
    term = (1 - 2 / a) / (1 + xk * math.sqrt(2 / (a - 4)))
    zk = ((1 - 2 / (9 * a)) - math.copysign(abs(term) ** (1 / 3), term)) / math.sqrt(2 / (9 * a))
    k2 = zs * zs + zk * zk
    return k2, math.exp(-k2 / 2)  # chi-square with 2 df


def _anderson_darling(x):
    """A² with estimated mean and SD; P from A²* = A²(1 + 0.75/n + 2.25/n²) by D'Agostino and Stephens (1986), table 4.9."""

    x = np.sort(np.asarray(x, float))
    n = x.size
    f = stats.norm.cdf((x - x.mean()) / x.std(ddof=1))
    i = np.arange(1, n + 1)
    a2 = -n - np.sum((2 * i - 1) * (np.log(f) + np.log(1 - f[::-1]))) / n
    a = a2 * (1 + 0.75 / n + 2.25 / n**2)
    if a < 0.2:
        p = 1 - math.exp(-13.436 + 101.14 * a - 223.73 * a * a)
    elif a < 0.34:
        p = 1 - math.exp(-8.318 + 42.796 * a - 59.938 * a * a)
    elif a < 0.6:
        p = math.exp(0.9177 - 4.279 * a - 1.38 * a * a)
    else:
        p = math.exp(1.2937 - 5.709 * a + 0.0186 * a * a)
    return a2, p


def _lilliefors(x):
    """KS distance against a normal with the sample mean and SD, and the Dallal-Wilkinson (1986) P (n ≤ 100, valid for P < 0.1)."""

    x = np.sort(np.asarray(x, float))
    n = x.size
    f = stats.norm.cdf((x - x.mean()) / x.std(ddof=1))
    i = np.arange(1, n + 1)
    d = max(np.max(i / n - f), np.max(f - (i - 1) / n))
    p = math.exp(-7.01256 * d * d * (n + 2.78019) + 2.99587 * d * math.sqrt(n + 2.78019) - 0.122119 + 0.974598 / math.sqrt(n) + 1.67997 / n)
    return d, p


def test_shapiro_wilk_matches_the_original_papers_example():
    v = normality(WEIGHTS).values["shapiro"]
    assert v["statistic"] == pytest.approx(0.79, abs=0.005)
    assert v["p"] < 0.01 and v["passed"] is False and v["n"] == 11


def test_dagostino_anderson_darling_and_ks_match_their_formulas():
    rng = np.random.default_rng(11)
    for x in (WEIGHTS, rng.normal(10, 2, 40), rng.lognormal(0, 1, 25)):
        v = normality(x).values
        k2, p = _dagostino_k2(x)
        assert v["dagostino"]["statistic"] == pytest.approx(k2, rel=1e-9) and v["dagostino"]["p"] == pytest.approx(p, rel=1e-9)
        a2, p = _anderson_darling(x)
        assert v["anderson"]["statistic"] == pytest.approx(a2, rel=1e-9) and v["anderson"]["p"] == pytest.approx(p, rel=1e-9)
        d, p = _lilliefors(x)
        assert v["ks"]["statistic"] == pytest.approx(d, rel=1e-9)
        if p < 0.1:
            assert v["ks"]["p"] == pytest.approx(p, rel=1e-9)
        else:
            assert v["ks"]["p"] >= 0.1


def test_small_samples_get_a_note_instead_of_a_number():
    result = normality([1.0, 2.5, 3.0, 4.5, 7.0])
    v = result.values
    assert v["dagostino"]["note"].startswith("n too small") and math.isnan(v["dagostino"]["p"]) and v["dagostino"]["passed"] is None
    assert v["anderson"]["note"].startswith("n too small")
    assert v["ks"]["note"] == "" and v["shapiro"]["note"] == ""
    assert result.row("D'Agostino & Pearson test", "K2").formatted == "n too small (needs at least 8 values, has 5)"
    assert normality([1.0, np.nan, 2.0]).values["shapiro"]["note"].startswith("n too small")
    flat = normality([4.0] * 10).values
    assert all(item["note"] == "all values are identical" for item in flat.values())


def test_lognormality_runs_the_same_tests_on_logs_and_refuses_non_positive_values():
    x = np.random.default_rng(5).lognormal(1, 0.6, 30)
    assert lognormality(x).values == normality(np.log(x)).values
    assert lognormality(x).values["shapiro"]["p"] == pytest.approx(normality(np.log10(x)).values["shapiro"]["p"])
    with pytest.raises(StatisticsError, match="greater than 0"):
        lognormality([1.0, 2.0, 0.0, 3.0])


def test_choosing_tests_and_a_missing_statsmodels(monkeypatch):
    assert list(normality(WEIGHTS, tests=["shapiro"]).values) == ["shapiro"]
    with pytest.raises(StatisticsError, match="Unknown"):
        normality(WEIGHTS, tests=["jarque_bera"])
    monkeypatch.setitem(sys.modules, "statsmodels.stats", None)
    assert normality(WEIGHTS, tests=["shapiro", "dagostino"]).values["shapiro"]["p"] < 0.01  # these two need only scipy
    with pytest.raises(StatisticsError, match=r"cellquant\[stats\]"):
        normality(WEIGHTS, tests=["ks"])
