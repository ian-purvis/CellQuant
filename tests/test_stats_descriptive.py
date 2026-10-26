"""Column statistics and one-sample tests, against published values and hand-written formulas."""

from __future__ import annotations

import math
import statistics

import numpy as np
import pytest
from scipy import stats

from cellquant.errors import StatisticsError
from cellquant.stats import ColumnTable, column_statistics, column_statistics_table, describe, one_sample_t, percentiles, wilcoxon_signed_rank

# R's `sleep` data (Cushny and Peebles, via Student 1908): extra sleep in hours, drug 1 and drug 2, same 10 patients.
SLEEP_1 = [0.7, -1.6, -0.2, -1.2, -0.1, 3.4, 3.7, 0.8, 0.0, 2.0]
SLEEP_2 = [1.9, 0.8, 1.1, 0.1, -0.1, 4.4, 5.5, 1.6, 4.6, 3.4]
SLEEP_DIFF = [b - a for a, b in zip(SLEEP_1, SLEEP_2)]


def test_skewness_and_kurtosis_match_excels_documented_examples():
    # Microsoft's documentation of SKEW and KURT uses these ten values: SKEW = 0.359543, KURT = -0.151799637.
    d = describe([3, 4, 5, 2, 3, 4, 5, 6, 4, 7])
    assert d["skewness"] == pytest.approx(0.359543, abs=1e-6)
    assert d["kurtosis"] == pytest.approx(-0.151799637, abs=1e-9)
    # Independent check of the same sample-adjusted estimators.
    rng = np.random.default_rng(3)
    x = rng.gamma(2.0, size=37)
    assert describe(x)["skewness"] == pytest.approx(stats.skew(x, bias=False))
    assert describe(x)["kurtosis"] == pytest.approx(stats.kurtosis(x, bias=False))


def test_percentiles_use_the_n_plus_1_rule():
    # Microsoft's PERCENTILE.EXC example: 25th percentile of 1,2,3,6,6,6,7,8,9 is 2.5.
    assert percentiles([1, 2, 3, 6, 6, 6, 7, 8, 9], 25) == pytest.approx(2.5)
    # Microsoft's QUARTILE.EXC example (also Hyndman-Fan type 6): Q1 = 15, Q3 = 43.
    assert list(percentiles([6, 7, 15, 36, 39, 40, 41, 42, 43, 47, 49], [25, 75])) == [15, 43]
    # By hand: rank (n+1)p = 0.9 * 6 = 5.4 among 1..5 is past the end, so the maximum; 0.1 * 6 = 0.6 gives the minimum.
    assert list(percentiles([10, 20, 30, 40, 50], [10, 90])) == [10, 50]
    # rank 0.25 * 9 = 2.25: 20 + 0.25 * (30 - 20)
    assert percentiles([10, 20, 30, 40, 50, 60, 70, 80], 25) == pytest.approx(22.5)


def test_column_statistics_match_their_formulas_and_skip_blanks():
    x = [2.0, 3.5, np.nan, 4.25, 8.0, 1.5, np.nan, 6.0]
    kept = [v for v in x if not math.isnan(v)]
    n = len(kept)
    result = column_statistics(x, ci=0.90, name="WT")
    d = result.values
    mean = sum(kept) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in kept) / (n - 1))
    sem = sd / math.sqrt(n)
    t90 = 2.015048373  # t(0.95, df = 5), from a t table
    assert d["n"] == 6 and d["blanks"] == 2
    assert d["mean"] == pytest.approx(mean) and d["sd"] == pytest.approx(sd) and d["sem"] == pytest.approx(sem)
    assert d["ci_low"] == pytest.approx(mean - t90 * sem, rel=1e-7) and d["ci_high"] == pytest.approx(mean + t90 * sem, rel=1e-7)
    assert d["cv_percent"] == pytest.approx(100 * sd / mean)
    assert d["median"] == statistics.median(kept)
    assert d["minimum"] == 1.5 and d["maximum"] == 8.0 and d["range"] == 6.5 and d["sum"] == sum(kept)
    assert d["geomean"] == pytest.approx(statistics.geometric_mean(kept))
    logs = [math.log(v) for v in kept]
    log_sd = statistics.stdev(logs)
    assert d["geosd_factor"] == pytest.approx(math.exp(log_sd))
    assert d["geomean_ci_low"] == pytest.approx(math.exp(statistics.mean(logs) - t90 * log_sd / math.sqrt(n)), rel=1e-7)
    assert d["harmonic_mean"] == pytest.approx(statistics.harmonic_mean(kept))
    assert d["quadratic_mean"] == pytest.approx(math.sqrt(sum(v * v for v in kept) / n))
    assert result.row("Column statistics: WT", "Lower 90% CI of mean").value == d["ci_low"]
    assert result.row("Column statistics: WT", "Coefficient of variation").formatted.endswith("%")


def test_small_columns_identical_values_and_non_positive_values_leave_blanks_with_reasons():
    empty = describe([np.nan])
    assert empty["n"] == 0 and math.isnan(empty["mean"]) and math.isnan(empty["median"])
    one = column_statistics([5.0])
    assert one.values["mean"] == 5 and math.isnan(one.values["sd"]) and any("at least 2" in text for text in one.warnings)
    same = column_statistics([3.0, 3.0, 3.0, 3.0])
    assert same.values["sd"] == 0 and math.isnan(same.values["skewness"]) and math.isnan(same.values["kurtosis"])
    negative = column_statistics([-1.0, 2.0, 3.0])
    assert math.isnan(negative.values["geomean"]) and math.isnan(negative.values["harmonic_mean"])
    assert any("greater than 0" in text for text in negative.warnings)
    with pytest.raises(StatisticsError, match="at least 2"):
        one_sample_t([1.0])
    flat = one_sample_t([2.0, 2.0, 2.0], 1.0)
    assert math.isnan(flat.values["t"]) and math.isnan(flat.values["p"]) and flat.warnings


def test_one_sample_t_matches_r_on_the_sleep_differences():
    # R: t.test(extra ~ group, data = sleep, paired = TRUE) is a one-sample t test on the differences:
    # |t| = 4.0621, df = 9, p-value = 0.002833, 95% CI of the mean difference 0.7001142 to 2.4598858 (drug 2 - drug 1).
    result = one_sample_t(SLEEP_DIFF, 0.0)
    v = result.values
    assert v["t"] == pytest.approx(4.0621, abs=5e-5) and v["df"] == 9 and v["p"] == pytest.approx(0.002833, abs=5e-7)
    assert v["ci_low"] == pytest.approx(0.7001142, abs=5e-8) and v["ci_high"] == pytest.approx(2.4598858, abs=5e-8)
    assert v["r_squared"] == pytest.approx(v["t"] ** 2 / (v["t"] ** 2 + 9))


def test_one_tailed_p_depends_on_the_predicted_direction():
    two = one_sample_t(SLEEP_DIFF, 0.0).values["p"]
    right = one_sample_t(SLEEP_DIFF, 0.0, alternative="greater").values["p"]
    wrong = one_sample_t(SLEEP_DIFF, 0.0, alternative="less").values["p"]
    assert right == pytest.approx(two / 2) and wrong == pytest.approx(1 - two / 2)


def test_wilcoxon_signed_rank_matches_r_on_the_sleep_differences():
    # R: wilcox.test(SLEEP_DIFF) (one zero difference, ties): V = 45, p-value = 0.009091 (normal approximation,
    # zero dropped, tie and continuity corrections).
    result = wilcoxon_signed_rank(SLEEP_DIFF, 0.0)
    v = result.values
    assert v["sum_positive"] == 45 and v["sum_negative"] == 0 and v["w"] == 45
    assert v["p"] == pytest.approx(0.009091, abs=5e-7) and not v["exact"] and v["n_zero"] == 1
    assert any("tied" in text for text in result.warnings)


def test_column_statistics_with_a_hypothetical_value_adds_both_one_sample_tests():
    result = column_statistics(SLEEP_DIFF, hypothetical=0.0)
    titles = [section.title for section in result.sections]
    assert "One sample t test" in titles and "Wilcoxon Signed Rank Test" in titles
    assert result.values["t_test_p"] == pytest.approx(0.002833, abs=5e-7)
    assert result.values["wilcoxon_p"] == pytest.approx(0.009091, abs=5e-7)


def test_a_whole_table_side_by_side():
    table = ColumnTable.from_columns({"Drug 1": SLEEP_1, "Drug 2": SLEEP_2 + [np.nan, 9.0]}, title="sleep")
    frame = column_statistics_table(table)
    assert list(frame.columns) == ["Drug 1", "Drug 2"]
    assert frame.loc[("Column statistics", "Mean"), "Drug 1"] == "0.75"
    assert frame.loc[("Column statistics", "Number of values"), "Drug 2"] == "11"
