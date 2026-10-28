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
    result = contingency(table)
    reference = stats.chi2_contingency(counts)
    assert result.values["p"] == pytest.approx(reference.pvalue) and "Fisher" not in result.methods
    n = counts.sum()
    linear = sm.Table(counts).test_ordinal_association(row_scores=np.arange(1, 5), col_scores=np.array([1, 2]))
    trend = chi_square_trend(counts)
    assert trend["chi2"] == pytest.approx(linear.zscore**2 * n / (n - 1))  # Cochran-Armitage = N r²; linear-by-linear = (N − 1) r²
    assert chi_square_trend(counts.T)["chi2"] == pytest.approx(trend["chi2"])
    assert result.values["cramers_v"] == pytest.approx(math.sqrt(reference.statistic / n))
    three = ContingencyTable("", ["a", "b", "c"], ["x", "y", "z"], [[2, 1, 0], [1, 2, 1], [0, 1, 3]])
    small = contingency(three)
    assert "trend" not in small.values and any("expected counts are small" in text for text in small.warnings)


def test_refusals():
    with pytest.raises(StatisticsError, match="blank"):
        contingency(ContingencyTable("", ["a", "b"], ["x", "y"], [[1, np.nan], [2, 3]]))
    with pytest.raises(StatisticsError, match="whole numbers"):
        contingency(ContingencyTable("", ["a", "b"], ["x", "y"], [[1.5, 2], [2, 3]]))
    with pytest.raises(StatisticsError, match="at least one count"):
        contingency(ContingencyTable("", ["a", "b"], ["x", "y"], [[0, 0], [2, 3]]))
    with pytest.raises(StatisticsError, match="2 × 2"):
        contingency(ContingencyTable("", ["a", "b", "c"], ["x", "y"], [[1, 2], [2, 3], [4, 1]]), test="fisher")
    with pytest.raises(StatisticsError, match="unique"):
        ContingencyTable("", ["a", "a"], ["x", "y"], np.ones((2, 2)))
    with pytest.raises(StatisticsError, match="two rows"):
        contingency(ContingencyTable("", ["a"], ["x", "y"], [[1, 2]]))
