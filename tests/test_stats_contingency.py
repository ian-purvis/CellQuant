"""Contingency tables: chi-square, Fisher's exact test, trend, relative risk, odds ratio, difference of proportions and the
diagnostic-test reading, checked against scipy and statsmodels."""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import stats

from cellquant.errors import StatisticsError
from cellquant.stats.contingency import ContingencyTable, chi_square_trend, contingency, newcombe, two_by_two, wilson

TWO = ContingencyTable("Response", ["Treated", "Control"], ["Responded", "Did not"], [[28, 12], [15, 25]])


def test_two_by_two_tests_and_effect_sizes():
    result = contingency(TWO)
    assert result.values["p"] == pytest.approx(stats.fisher_exact([[28, 12], [15, 25]]).pvalue)
    chi = contingency(TWO, test="chi2", yates=True).values
    reference = stats.chi2_contingency([[28, 12], [15, 25]], correction=True)
    assert chi["p"] == pytest.approx(reference.pvalue) and chi["chi2"]["chi2"] == pytest.approx(reference.statistic)
    effect = result.values["effect"]
    rr = stats.contingency.relative_risk(28, 40, 15, 40)
    low, high = rr.confidence_interval(0.95)
    assert effect["rr"] == pytest.approx(rr.relative_risk) and effect["rr_ci"] == pytest.approx((low, high))
    sm = pytest.importorskip("statsmodels.stats.contingency_tables")
    table = sm.Table2x2(np.array([[28, 12], [15, 25]]))
    assert effect["odds_ratio"] == pytest.approx(table.oddsratio) and effect["or_ci"] == pytest.approx(table.oddsratio_confint(0.05))
    proportion = pytest.importorskip("statsmodels.stats.proportion")
    assert effect["difference_ci"] == pytest.approx(proportion.confint_proportions_2indep(28, 40, 15, 40, method="newcomb"))
    assert effect["nnt"] == pytest.approx(1 / (28 / 40 - 15 / 40)) and effect["phi"] == pytest.approx(
        math.copysign(math.sqrt(stats.chi2_contingency([[28, 12], [15, 25]], correction=False).statistic / 80), 1))
    wide = result.values["wide"]
    assert wide.loc[("Are rows and columns associated?", "Test"), "Value"] == "Fisher's exact test"
    assert wide.loc[("Effect size", "Responded: Treated"), "Value"] == "70%" and "Number needed to treat (1 / |difference|)" in wide.index.get_level_values(1)
    assert "Katz" in result.methods and "Newcombe" in result.methods


def test_diagnostic_reading_and_wilson():
    proportion = pytest.importorskip("statsmodels.stats.proportion")
    assert wilson(17, 40) == pytest.approx(proportion.proportion_confint(17, 40, method="wilson"))
    test = ContingencyTable("Test", ["Positive", "Negative"], ["Disease", "Healthy"], [[45, 10], [5, 90]])
    dx = two_by_two(test.checked())["diagnostic"]
    assert dx["sensitivity"][0] == pytest.approx(0.9) and dx["specificity"][0] == pytest.approx(0.9)
    assert dx["ppv"][0] == pytest.approx(45 / 55) and dx["lr_positive"] == pytest.approx(9.0) and dx["lr_negative"] == pytest.approx(1 / 9)
    wide = contingency(test, diagnostic=True).values["wide"]
    assert wide.loc[("Diagnostic test", "Sensitivity"), "Value"].startswith("90%") and ("Effect size", "Relative risk") not in wide.index
    # A zero cell: the odds ratio adds 0.5 to every cell and says so; the relative risk has no CI.
    zero = two_by_two(np.array([[10, 0], [4, 6]]))
    assert zero["or_corrected"] and zero["odds_ratio"] == pytest.approx(10.5 * 6.5 / (0.5 * 4.5))
    assert newcombe(10, 10, 4, 10)[1] <= 1.0


def test_larger_tables_trend_and_cramers_v():
    sm = pytest.importorskip("statsmodels.stats.contingency_tables")
    counts = np.array([[20, 30], [28, 22], [35, 15], [40, 10]])
    table = ContingencyTable("Dose", ["0", "1", "2", "3"], ["Response", "None"], counts)
    assert "trend" not in contingency(table).values  # unordered groups by default
    result = contingency(table, trend=True)
    reference = stats.chi2_contingency(counts)
    assert result.values["p"] == pytest.approx(reference.pvalue) and "Fisher" not in result.methods
    n = counts.sum()
    linear = sm.Table(counts).test_ordinal_association(row_scores=np.arange(1, 5), col_scores=np.array([1, 2]))
    trend = chi_square_trend(counts)
    assert trend["chi2"] == pytest.approx(linear.zscore**2 * n / (n - 1))  # Cochran-Armitage = N r²; linear-by-linear = (N − 1) r²
    assert chi_square_trend(counts.T)["chi2"] == pytest.approx(trend["chi2"])
    assert result.values["cramers_v"] == pytest.approx(math.sqrt(reference.statistic / n))
    three = ContingencyTable("", ["a", "b", "c"], ["x", "y", "z"], [[2, 1, 0], [1, 2, 1], [0, 1, 3]])
    small = contingency(three, trend=True)
    assert "trend" not in small.values and any("trend needs" in text for text in small.warnings)
    assert "Cramér's V = " in small.methods and not any("Fewer than 20" in text for text in small.warnings) and any("expected counts are small" in text for text in small.warnings)


def test_refusals():
    with pytest.raises(StatisticsError, match="blank"):
        contingency(ContingencyTable("", ["a", "b"], ["x", "y"], [[1, np.nan], [2, 3]]))
    with pytest.raises(StatisticsError, match="whole numbers"):
        contingency(ContingencyTable("", ["a", "b"], ["x", "y"], [[1.5, 2], [2, 3]]))
    with pytest.raises(StatisticsError, match="at least one count"):
        contingency(ContingencyTable("", ["a", "b"], ["x", "y"], [[0, 0], [2, 3]]))
    grown = contingency(ContingencyTable("", ["a", "b", "c"], ["x", "y"], [[1, 2], [2, 3], [4, 1]]), test="fisher", yates=True, diagnostic=True)
    assert grown.values["p"] == pytest.approx(stats.chi2_contingency([[1, 2], [2, 3], [4, 1]]).pvalue)
    assert [any(word in text for text in grown.warnings) for word in ("chi-square test is used", "Yates", "diagnostic")] == [True] * 3
    with pytest.raises(StatisticsError, match="unique"):
        ContingencyTable("", ["a", "a"], ["x", "y"], np.ones((2, 2)))
    with pytest.raises(StatisticsError, match="two rows"):
        contingency(ContingencyTable("", ["a"], ["x", "y"], [[1, 2]]))


def test_group_and_outcome_of_interest():
    swapped = contingency(TWO, group=1, outcome=1)
    effect, base = swapped.values["effect"], contingency(TWO).values["effect"]
    assert effect["rr"] == pytest.approx(stats.contingency.relative_risk(25, 40, 12, 40).relative_risk)
    assert contingency(TWO, group=1).values["effect"]["odds_ratio"] == pytest.approx(1 / base["odds_ratio"])
    wide = swapped.values["wide"]
    assert ("Effect size", "Relative risk (Control vs Treated, Did not)") in wide.index
    assert ("Effect size", "Difference of proportions (Control − Treated)") in wide.index
    assert "Control compared with Treated for Did not" in swapped.methods and "Fisher's exact test (two-sided" in swapped.methods
    assert contingency(TWO, group="x", outcome=7).options["group"] == 0  # garbage falls back to row 1 and column 1


def test_zero_cells_and_undefined_values():
    zero = two_by_two(np.array([[0, 10], [5, 5]]))
    assert zero["rr_corrected"] and zero["rr"] == pytest.approx((0.5 / 11) / (5.5 / 11))
    assert all(math.isfinite(value) for value in zero["rr_ci"])
    assert "0.5 added to every cell, as a or c was 0" in contingency(ContingencyTable("", ["a", "b"], ["x", "y"], [[0, 10], [5, 5]])).methods
    # Likelihood-ratio CIs (log method), against the formula.
    dx = two_by_two(np.array([[45, 10], [5, 90]]))["diagnostic"]
    spread = stats.norm.ppf(0.975) * math.sqrt(1 / 45 - 1 / 50 + 1 / 10 - 1 / 100)
    assert dx["lr_positive_ci"] == pytest.approx((9 * math.exp(-spread), 9 * math.exp(spread)))
    assert wilson(0, 8) == (0.0, pytest.approx(wilson(0, 8)[1])) and wilson(8, 8)[1] == 1.0
    undefined = contingency(ContingencyTable("", ["+", "-"], ["Present", "Absent"], [[0, 5], [2, 3]]), diagnostic=True).values["wide"]
    assert undefined.loc[("Diagnostic test", "Sensitivity"), "Value"].startswith("0%")
    assert undefined.loc[("Diagnostic test", "Likelihood ratio (positive)"), "Value"] == "0 (CI not defined: a cell it uses is 0)"
    assert undefined.loc[("Diagnostic test", "Layout"), "Value"].startswith("Test positive: +")
    # The difference's CI spans 0: the NNT's CI runs through infinity.
    close = contingency(ContingencyTable("", ["A", "B"], ["Yes", "No"], [[10, 10], [9, 11]])).values["wide"]
    assert "through ∞" in close.loc[("Effect size", "Number needed to treat (1 / |difference|)"), "Value"]
