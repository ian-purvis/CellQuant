"""Two-column comparisons, against R's documented results and brute-force or hand-written computations.

a commercial package (and this engine) report B - A, the second column minus the first; R's t.test(x, y) reports x - y. So R's
results for x = drug 1, y = drug 2 appear here with the sign flipped and with A = drug 1, B = drug 2.
"""

from __future__ import annotations

import itertools
import math
import statistics

import numpy as np
import pytest
from scipy import stats

from cellquant.errors import StatisticsError
from cellquant.stats import (
    ColumnTable,
    compare,
    effect_sizes,
    hedges_j,
    kolmogorov_smirnov,
    lognormal_unpaired_t,
    mann_whitney,
    paired_effect_sizes,
    paired_t,
    ratio_paired_t,
    unpaired_t,
    welch_t,
    wilcoxon_matched_pairs,
)

# R's `sleep` data (Student 1908): extra hours of sleep with drug 1 and drug 2 in the same 10 patients.
SLEEP_1 = [0.7, -1.6, -0.2, -1.2, -0.1, 3.4, 3.7, 0.8, 0.0, 2.0]
SLEEP_2 = [1.9, 0.8, 1.1, 0.1, -0.1, 4.4, 5.5, 1.6, 4.6, 3.4]
# R's `mtcars`: miles per gallon, automatic (am = 0, 19 cars) and manual (am = 1, 13 cars).
MPG_AUTO = [21.4, 18.7, 18.1, 14.3, 24.4, 22.8, 19.2, 17.8, 16.4, 17.3, 15.2, 10.4, 10.4, 14.7, 21.5, 15.5, 15.2, 13.3, 19.2]
MPG_MANUAL = [21.0, 21.0, 22.8, 32.4, 30.4, 33.9, 27.3, 26.0, 30.4, 15.8, 19.7, 15.0, 21.4]


def test_paired_t_matches_r_on_sleep():
    # R: t.test(extra ~ group, data = sleep, paired = TRUE): t = -4.0621, df = 9, p-value = 0.002833,
    # 95% CI -2.4598858 to -0.7001142, mean difference -1.58 (group 1 - group 2).
    v = paired_t(SLEEP_1, SLEEP_2).values
    assert v["t"] == pytest.approx(4.0621, abs=5e-5) and v["df"] == 9 and v["p"] == pytest.approx(0.002833, abs=5e-7)
    assert v["difference"] == pytest.approx(1.58)
    assert v["ci_low"] == pytest.approx(0.7001142, abs=5e-8) and v["ci_high"] == pytest.approx(2.4598858, abs=5e-8)


def test_unpaired_and_welch_t_match_r_on_sleep():
    # R: t.test(extra ~ group, data = sleep, var.equal = TRUE): t = -1.8608, df = 18, p-value = 0.07919,
    # 95% CI -3.363874 to 0.203874, means 0.75 and 2.33.
    v = unpaired_t(SLEEP_1, SLEEP_2).values
    assert v["t"] == pytest.approx(1.8608, abs=5e-5) and v["df"] == 18 and v["p"] == pytest.approx(0.07919, abs=5e-6)
    assert v["ci_low"] == pytest.approx(-0.203874, abs=5e-7) and v["ci_high"] == pytest.approx(3.363874, abs=5e-7)
    assert v["mean_a"] == pytest.approx(0.75) and v["mean_b"] == pytest.approx(2.33)
    # R: var.test(extra ~ group, data = sleep): F = 0.7983, num df = 9, denom df = 9, p-value = 0.7427.
    # a commercial package puts the larger variance on top, so F = 1/0.7983; the two-tailed P is the same.
    assert v["f"] == pytest.approx(1 / 0.7983, abs=5e-4) and v["f_p"] == pytest.approx(0.7427, abs=5e-5)
    # R: t.test(extra ~ group, data = sleep) (Welch): t = -1.8608, df = 17.776, p-value = 0.07939, CI -3.3654832 to 0.2054832.
    v = welch_t(SLEEP_1, SLEEP_2).values
    assert v["df"] == pytest.approx(17.776, abs=5e-4) and v["p"] == pytest.approx(0.07939, abs=5e-6)
    assert v["ci_low"] == pytest.approx(-0.2054832, abs=5e-8) and v["ci_high"] == pytest.approx(3.3654832, abs=5e-8)


def test_welch_t_matches_r_on_mtcars_with_unequal_n():
    # R: t.test(mpg ~ am, data = mtcars): t = -3.7671, df = 18.332, p-value = 0.001374, CI -11.280194 to -3.209684,
    # means 17.14737 and 24.39231.
    v = welch_t(MPG_AUTO, MPG_MANUAL).values
    assert v["n_a"] == 19 and v["n_b"] == 13
    assert v["mean_a"] == pytest.approx(17.14737, abs=5e-6) and v["mean_b"] == pytest.approx(24.39231, abs=5e-6)
    assert v["t"] == pytest.approx(3.7671, abs=5e-5) and v["df"] == pytest.approx(18.332, abs=5e-4) and v["p"] == pytest.approx(0.001374, abs=5e-7)
    assert v["ci_low"] == pytest.approx(3.209684, abs=5e-7) and v["ci_high"] == pytest.approx(11.280194, abs=5e-7)


def test_rank_tests_match_r_with_ties():
    # R: wilcox.test(extra ~ group, data = sleep, paired = TRUE): V = 0, p-value = 0.009091 (V is the sum of positive
    # ranks of group 1 - group 2; one zero difference dropped; Gaussian approximation with tie and continuity corrections).
    # a commercial package would give an exact P here; R's approximation is asked for explicitly.
    v = wilcoxon_matched_pairs(SLEEP_1, SLEEP_2, exact=False, continuity=True).values
    assert v["sum_negative"] == 0 and v["sum_positive"] == 45 and v["p"] == pytest.approx(0.009091, abs=5e-7) and not v["exact"]
    # R: wilcox.test(extra ~ group, data = sleep): W = 25.5, p-value = 0.06933.
    v = mann_whitney(SLEEP_1, SLEEP_2, exact=False, continuity=True).values
    assert v["u_a"] == 25.5 and v["u"] == 25.5 and v["p"] == pytest.approx(0.06933, abs=5e-6) and not v["exact"]
    # R: wilcox.test(mpg ~ am, data = mtcars): W = 42, p-value = 0.001871.
    v = mann_whitney(MPG_AUTO, MPG_MANUAL, exact=False, continuity=True).values
    assert v["u_a"] == 42 and v["p"] == pytest.approx(0.001871, abs=5e-7)


def test_exact_rank_tests_match_rs_documented_examples():
    # R's ?wilcox.test, Hollander and Wolfe (1973) p. 29: depression scores before (x) and after (y) treatment.
    # wilcox.test(x, y, paired = TRUE, alternative = "greater"): V = 40, p-value = 0.01953.
    x = [1.83, 0.50, 1.62, 2.48, 1.68, 1.88, 1.55, 3.06, 1.30]
    y = [0.878, 0.647, 0.598, 2.05, 1.06, 1.29, 1.06, 3.14, 1.29]
    v = wilcoxon_matched_pairs(y, x, alternative="greater").values  # B - A = x - y
    assert v["sum_positive"] == 40 and v["exact"] and v["p"] == pytest.approx(0.01953, abs=5e-6)
    # Hollander and Wolfe (1973) p. 69: permeability constants. wilcox.test(x, y, alternative = "g"): W = 35, p-value = 0.1272.
    x = [0.80, 0.83, 1.89, 1.04, 1.45, 1.38, 1.91, 1.64, 0.73, 1.46]
    y = [1.15, 0.88, 0.90, 0.74, 1.21]
    v = mann_whitney(y, x, alternative="greater").values
    assert v["u_b"] == 35 and v["exact"] and v["p"] == pytest.approx(0.1272, abs=5e-5)


def _brute_mann_whitney(a, b):
    """Every way to split the pooled values into groups of the original sizes (exact permutation distribution of U_B)."""

    pooled = list(a) + list(b)
    m = len(a)
    us = []
    for chosen in itertools.combinations(range(len(pooled)), len(b)):
        bs = [pooled[i] for i in chosen]
        as_ = [pooled[i] for i in range(len(pooled)) if i not in chosen]
        us.append(sum((bj > ai) + 0.5 * (bj == ai) for ai in as_ for bj in bs))
    assert len(as_) == m
    return np.array(us)


def test_exact_mann_whitney_p_and_hodges_lehmann_ci_match_brute_force():
    a = [3.1, 4.7, 1.2, 5.5, 2.0]
    b = [6.3, 5.9, 2.8, 7.7, 8.4, 4.9]
    us = _brute_mann_whitney(a, b)
    observed = sum(bj > ai for ai in a for bj in b)
    result = mann_whitney(a, b)
    v = result.values
    assert v["exact"] and v["u_b"] == observed
    assert v["p"] == pytest.approx(min(1, 2 * min(np.mean(us <= observed), np.mean(us >= observed))))
    assert mann_whitney(a, b, alternative="greater").values["p"] == pytest.approx(np.mean(us >= observed))
    assert mann_whitney(a, b, alternative="less").values["p"] == pytest.approx(np.mean(us <= observed))
    # Hodges-Lehmann: the median of the 30 differences; CI from order statistics k and N + 1 - k, where k gives the
    # confidence 1 - 2·P(U ≤ k - 1) nearest to 95%.
    diffs = sorted(bj - ai for ai in a for bj in b)
    assert v["hodges_lehmann"] == pytest.approx(statistics.median(diffs))
    k = min(range(1, 16), key=lambda kk: (abs(1 - 2 * np.mean(us <= kk - 1) - 0.95), -kk))
    assert (v["ci_low"], v["ci_high"]) == (pytest.approx(diffs[k - 1]), pytest.approx(diffs[len(diffs) - k]))
    assert v["ci_achieved"] == pytest.approx(1 - 2 * np.mean(us <= k - 1))
    assert v["median_a"] == 3.1 and v["median_b"] == pytest.approx(6.1) and v["difference"] == pytest.approx(3.0)
    # Complete separation of 3 vs 3: exact two-tailed P = 2 / C(6, 3) = 0.1.
    assert mann_whitney([1, 2, 3], [4, 5, 6]).values["p"] == pytest.approx(0.1)


def test_exact_signed_rank_p_matches_all_sign_flips():
    a = np.array([10.2, 8.1, 9.9, 12.4, 7.7, 11.0, 9.3])
    b = np.array([11.0, 8.5, 9.0, 14.1, 9.9, 12.8, 9.4])
    d = b - a
    ranks = stats.rankdata(np.abs(d))
    observed = ranks[d > 0].sum()
    flips = np.array([sum(r for r, s in zip(ranks, signs) if s) for signs in itertools.product([0, 1], repeat=d.size)])
    v = wilcoxon_matched_pairs(a, b).values
    assert v["exact"] and v["sum_positive"] == observed and v["w"] == observed - ranks[d < 0].sum()
    assert v["p"] == pytest.approx(min(1, 2 * min(np.mean(flips <= observed), np.mean(flips >= observed))))
    assert wilcoxon_matched_pairs(a, b, alternative="greater").values["p"] == pytest.approx(np.mean(flips >= observed))
    # Five positive differences: exact two-tailed P = 2 / 2^5.
    assert wilcoxon_matched_pairs([0, 0, 0, 0, 0], [1, 2, 3, 4, 5]).values["p"] == pytest.approx(0.0625)


def test_zero_differences_can_be_excluded_or_ranked_by_pratt():
    a = [5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0]
    b = [5.0, 6.0, 8.5, 6.0, 12.0, 14.0, 15.5, 17.0]  # two zero differences, no ties
    d = np.array(b) - np.array(a)
    excluded = wilcoxon_matched_pairs(a, b, zero_method="exclude").values
    # Excluding zeros leaves 6 untied differences, so P is exact.
    nonzero = d[d != 0]
    ranks = stats.rankdata(np.abs(nonzero))
    assert excluded["exact"] and excluded["n_zero"] == 2 and excluded["sum_positive"] == ranks[nonzero > 0].sum()
    pratt = wilcoxon_matched_pairs(a, b, zero_method="pratt", exact=False, continuity=True).values
    # Pratt (1959) with Cureton's (1967) normal approximation: rank all 8 |d| (zeros take ranks 1.5, 1.5), drop the zeros' ranks;
    # mean = (n(n+1) - n0(n0+1))/4, variance = (n(n+1)(2n+1) - n0(n0+1)(2n0+1))/24, then the 0.5 continuity correction.
    all_ranks = stats.rankdata(np.abs(d))
    t_plus = all_ranks[d > 0].sum()
    mean = (8 * 9 - 2 * 3) / 4
    sd = math.sqrt((8 * 9 * 17 - 2 * 3 * 5) / 24)
    z = (t_plus - mean - 0.5) / sd
    assert not pratt["exact"] and pratt["sum_positive"] == t_plus
    assert pratt["p"] == pytest.approx(2 * stats.norm.sf(abs(z)))
    # Exact Pratt: the zeros keep their ranks out of the sums; the six nonzero ranks get random signs.
    signed = all_ranks[d != 0]
    flips = np.array([sum(r for r, s in zip(signed, signs) if s) for signs in itertools.product([0, 1], repeat=signed.size)])
    exact = wilcoxon_matched_pairs(a, b, zero_method="pratt").values
    assert exact["exact"] and exact["p"] == pytest.approx(min(1, 2 * min(np.mean(flips <= t_plus), np.mean(flips >= t_plus))))
    with pytest.raises(ValueError, match="zero_method"):
        wilcoxon_matched_pairs(a, b, zero_method="zsplit")


def test_exact_p_with_ties_matches_brute_force():
    # Mann-Whitney with ties inside and across groups: every split of the pooled values, U with ties counting 1/2.
    a = [1.0, 2.0, 2.0, 3.0, 5.0, 5.0]
    b = [2.0, 3.0, 4.0, 5.0, 5.0, 6.0, 6.0]
    us = _brute_mann_whitney(a, b)
    observed = sum((bj > ai) + 0.5 * (bj == ai) for ai in a for bj in b)
    for alternative, expected in (
        ("two-sided", min(1, 2 * min(np.mean(us <= observed + 1e-9), np.mean(us >= observed - 1e-9)))),
        ("greater", np.mean(us >= observed - 1e-9)),
        ("less", np.mean(us <= observed + 1e-9)),
    ):
        v = mann_whitney(a, b, alternative=alternative).values
        assert v["exact"] and v["u_b"] == observed and v["p"] == pytest.approx(expected), alternative
        # The same with the columns swapped (the smaller sample is counted, so this takes the other branch).
        swapped = {"greater": "less", "less": "greater"}.get(alternative, alternative)
        assert mann_whitney(b, a, alternative=swapped).values["p"] == pytest.approx(expected), alternative
    # Signed ranks with tied |d| (midranks) and a zero dropped.
    d = np.array([1.5, -1.5, 2.0, 2.0, -0.5, 3.0, 0.0, 2.0, -4.0])
    kept = d[d != 0]
    ranks = stats.rankdata(np.abs(kept))
    observed = ranks[kept > 0].sum()
    flips = np.array([sum(r for r, s in zip(ranks, signs) if s) for signs in itertools.product([0, 1], repeat=kept.size)])
    v = wilcoxon_matched_pairs(np.zeros(d.size), d).values
    assert v["exact"] and v["sum_positive"] == observed
    assert v["p"] == pytest.approx(min(1, 2 * min(np.mean(flips <= observed + 1e-9), np.mean(flips >= observed - 1e-9))))
    assert wilcoxon_matched_pairs(np.zeros(d.size), d, alternative="less").values["p"] == pytest.approx(np.mean(flips <= observed + 1e-9))


def test_exact_limits_and_the_gaussian_approximation_beyond_them():
    import time

    rng = np.random.default_rng(2)
    a, b = np.round(rng.normal(size=100), 1), np.round(rng.normal(0.3, size=200), 1)  # heavy ties
    start = time.perf_counter()
    v = mann_whitney(a, b).values
    assert v["exact"] and time.perf_counter() - start < 6
    # Exact and Gaussian P agree closely at this size.
    assert v["p"] == pytest.approx(mann_whitney(a, b, exact=False).values["p"], rel=0.05)
    assert not mann_whitney(rng.normal(size=101), rng.normal(size=101)).values["exact"]  # smaller group > 100
    assert not mann_whitney(rng.normal(size=60), rng.normal(size=400)).values["exact"]  # n_A·n_B > 20 000
    d = np.round(rng.normal(0.1, 1, 199), 1)
    start = time.perf_counter()
    v = wilcoxon_matched_pairs(np.zeros(199), d).values
    assert v["exact"] and time.perf_counter() - start < 3
    assert not wilcoxon_matched_pairs(np.zeros(200), np.round(rng.normal(0.1, 1, 200), 1) + 0.05).values["exact"]
    # Beyond the limits: Gaussian with tie correction and no continuity correction.
    x, y = rng.normal(size=150), rng.normal(0.2, size=150)
    m = n = 150
    ranks = stats.rankdata(np.concatenate([x, y]))
    u = ranks[m:].sum() - n * (n + 1) / 2
    z = (u - m * n / 2) / math.sqrt(m * n * (m + n + 1) / 12)
    assert mann_whitney(x, y).values["p"] == pytest.approx(2 * stats.norm.sf(abs(z)))


def test_a_warning_when_n_is_too_small_to_ever_reach_alpha():
    # 3 vs 3: the most extreme split has probability 1 / C(6, 3) = 1/20, so the two-tailed P is at least 0.10.
    result = mann_whitney([1, 2, 3], [4, 5, 6])
    assert result.values["min_p"] == pytest.approx(0.1)
    assert any("smallest P this test can give is 0.1000" in text for text in result.warnings)
    one_tailed = mann_whitney([1, 2, 3], [4, 5, 6], alternative="greater")
    assert one_tailed.values["min_p"] == pytest.approx(0.05) and not any("smallest P" in text for text in one_tailed.warnings)
    assert not any("smallest P" in text for text in mann_whitney([1, 2, 3, 4], [5, 6, 7, 8]).warnings)  # 2/70 < 0.05
    # Ties can only raise the floor.
    tied = mann_whitney([1.0, 2.0, 2.0], [2.0, 3.0, 4.0]).values
    assert tied["min_p"] >= 0.1
    # Wilcoxon: five pairs give at least 2 / 2^5 = 0.0625 two-tailed; with alpha = 0.1 there is no warning.
    paired = wilcoxon_matched_pairs([0, 0, 0, 0, 0], [1, 2, 3, 4, 5])
    assert paired.values["min_p"] == pytest.approx(0.0625) and any("0.0625" in text for text in paired.warnings)
    assert not any("smallest P" in text for text in wilcoxon_matched_pairs([0, 0, 0, 0, 0], [1, 2, 3, 4, 5], alpha=0.1).warnings)
    assert not any("smallest P" in text for text in wilcoxon_matched_pairs(np.zeros(6), [1, 2, 3, 4, 5, 6]).warnings)  # 2/64


def test_hodges_lehmann_ci_reports_the_nearest_achievable_level():
    result = mann_whitney([1.2, 3.4, 2.2, 5.0], [4.1, 6.3, 5.6, 7.7, 3.9])
    level = result.values["ci_achieved"]
    assert level != 0.95 and abs(level - 0.95) < 0.05
    assert any(row.label == f"{100 * level:.2f}% CI of difference" for row in result.section("Difference between medians").rows)
    paired = wilcoxon_matched_pairs([1, 2, 3, 4, 5, 6], [2.5, 2.1, 4.4, 6.0, 5.2, 9.9]).values
    # 21 Walsh averages of 6 differences; the levels 1 - 2·P(T+ ≤ k - 1) are 96.88% (k = 1) and 93.75% (k = 2):
    # 93.75% is nearer to 95%.
    assert paired["ci_achieved"] == pytest.approx(1 - 4 / 64)


def test_kolmogorov_smirnov_two_sample_matches_a_permutation_count():
    a = [1.1, 2.3, 2.9, 4.0, 5.2]
    b = [3.5, 4.4, 6.1, 6.6, 7.0, 8.2]
    pooled = np.array(a + b)

    def distances(xs, ys):
        grid = np.sort(np.concatenate([xs, ys]))
        fa = np.searchsorted(np.sort(xs), grid, side="right") / len(xs)
        fb = np.searchsorted(np.sort(ys), grid, side="right") / len(ys)
        return np.max(np.abs(fa - fb)), np.max(fa - fb)

    d, d_plus = distances(np.array(a), np.array(b))
    null = []
    for chosen in itertools.combinations(range(pooled.size), len(a)):
        mask = np.zeros(pooled.size, bool)
        mask[list(chosen)] = True
        null.append(distances(pooled[mask], pooled[~mask]))
    null = np.array(null)
    v = kolmogorov_smirnov(a, b).values
    assert v["exact"] and v["d"] == pytest.approx(d)
    assert v["p"] == pytest.approx(np.mean(null[:, 0] >= d - 1e-12))
    # "greater" (B larger): the first column's cumulative distribution lies above the second's.
    v = kolmogorov_smirnov(a, b, alternative="greater").values
    assert v["d"] == pytest.approx(d_plus) and v["p"] == pytest.approx(np.mean(null[:, 1] >= d_plus - 1e-12))


def test_one_tailed_p_is_half_in_the_predicted_direction_and_the_rest_otherwise():
    a = [1.9, 0.8, 1.1, 0.3, 2.2, 3.0]
    b = [2.8, 3.1, 2.1, 1.9, 6.4, 5.0]
    for test in (unpaired_t, welch_t, paired_t, ratio_paired_t, lognormal_unpaired_t):
        two = test(a, b).values["p"]
        right = test(a, b, alternative="greater").values["p"]
        wrong = test(a, b, alternative="less").values["p"]
        assert right == pytest.approx(two / 2) and wrong == pytest.approx(1 - two / 2), test.__name__
    result = paired_t(SLEEP_1, SLEEP_2, alternative="less", names=("Drug 1", "Drug 2"))
    assert result.row("Paired t test", "One- or two-tailed P value?").formatted == "One-tailed (predicted Drug 2 < Drug 1)"


def test_blanks_are_dropped_per_column_or_per_pair():
    v = unpaired_t(SLEEP_1 + [np.nan, np.nan], [np.nan] + SLEEP_2).values
    assert v["n_a"] == 10 and v["n_b"] == 10 and v["p"] == pytest.approx(0.07919, abs=5e-6)
    a = SLEEP_1 + [np.nan, 4.0]
    b = SLEEP_2 + [5.0, np.nan]
    result = paired_t(a, b)
    assert result.values["n"] == 10 and result.values["p"] == pytest.approx(0.002833, abs=5e-7)
    assert any("2 row(s)" in text for text in result.warnings)
    with pytest.raises(StatisticsError, match="same number of rows"):
        paired_t([1, 2, 3], [1, 2])


def test_too_few_values_and_zero_variance():
    with pytest.raises(StatisticsError, match="at least 2 values in each"):
        welch_t([1.0], [2.0, 3.0])
    with pytest.raises(StatisticsError, match="at least 3 values"):
        unpaired_t([1.0], [2.0])
    assert math.isfinite(unpaired_t([1.0], [2.0, 3.0, 4.0]).values["p"])  # pooled t works with n = 1 in one column
    with pytest.raises(StatisticsError, match="at least 2 complete pairs"):
        paired_t([1.0, np.nan], [2.0, 3.0])
    flat = unpaired_t([2.0, 2.0, 2.0], [2.0, 2.0])
    assert math.isnan(flat.values["t"]) and math.isnan(flat.values["p"]) and flat.warnings
    shifted = paired_t([1.0, 2.0, 3.0], [2.0, 3.0, 4.0])
    assert math.isnan(shifted.values["p"]) and any("same" in text for text in shifted.warnings)
    one_flat = unpaired_t([2.0, 2.0, 2.0], [1.0, 4.0, 6.0])
    assert math.isfinite(one_flat.values["p"]) and "f" not in one_flat.values
    assert mann_whitney([3.0, 3.0], [3.0, 3.0]).values["p"] == 1.0  # exact: every arrangement gives the same U
    assert math.isnan(mann_whitney([3.0, 3.0], [3.0, 3.0], exact=False).values["p"])
    with pytest.raises(StatisticsError, match="greater than 0"):
        ratio_paired_t([1.0, 0.0, 2.0], [2.0, 3.0, 4.0])
    with pytest.raises(StatisticsError, match="greater than 0"):
        lognormal_unpaired_t([1.0, -2.0, 2.0], [2.0, 3.0, 4.0])


def test_effect_sizes_follow_their_formulas():
    a, b = MPG_AUTO, MPG_MANUAL
    na, nb = len(a), len(b)
    pooled = math.sqrt(((na - 1) * statistics.variance(a) + (nb - 1) * statistics.variance(b)) / (na + nb - 2))
    diff = statistics.mean(b) - statistics.mean(a)
    sizes = effect_sizes(a, b)
    df = na + nb - 2
    j = math.gamma(df / 2) / (math.sqrt(df / 2) * math.gamma((df - 1) / 2))
    assert sizes["cohen_d"] == pytest.approx(diff / pooled)
    assert sizes["hedges_g"] == pytest.approx(j * diff / pooled)
    assert sizes["glass_delta"] == pytest.approx(diff / statistics.stdev(a))
    assert hedges_j(df) == pytest.approx(1 - 3 / (4 * (na + nb) - 9), abs=2e-4)  # Hedges' approximation
    paired = paired_effect_sizes(SLEEP_1, SLEEP_2)
    d = [y - x for x, y in zip(SLEEP_1, SLEEP_2)]
    assert paired["cohen_dz"] == pytest.approx(statistics.mean(d) / statistics.stdev(d))
    assert paired["hedges_gz"] == pytest.approx(paired["cohen_dz"] * hedges_j(9))
    assert unpaired_t(a, b).values["r_squared"] == pytest.approx(unpaired_t(a, b).values["t"] ** 2 / (unpaired_t(a, b).values["t"] ** 2 + df))


def test_paired_t_reports_whether_pairing_worked():
    v = paired_t(SLEEP_1, SLEEP_2).values
    r = statistics.correlation(SLEEP_1, SLEEP_2)
    t = r * math.sqrt(8) / math.sqrt(1 - r * r)
    assert v["pairing_r"] == pytest.approx(r) and v["pairing_p"] == pytest.approx(stats.t.sf(t, 8))
    assert v["pairing_effective"] is True
    unrelated = paired_t([1.0, 5.0, 2.0, 4.0, 3.0], [3.0, 2.5, 4.5, 1.0, 5.0]).values
    assert unrelated["pairing_r"] < 0 and unrelated["pairing_p"] > 0.5 and unrelated["pairing_effective"] is False


def test_ratio_paired_and_lognormal_t_work_on_logs_and_report_ratios():
    a = [12.0, 30.0, 7.5, 55.0, 20.0, 9.0]
    b = [20.0, 61.0, 14.0, 98.0, 33.0, 21.0]
    logs = [math.log10(y / x) for x, y in zip(a, b)]
    mean, sd, n = statistics.mean(logs), statistics.stdev(logs), len(logs)
    t = mean / (sd / math.sqrt(n))
    half = stats.t.ppf(0.975, n - 1) * sd / math.sqrt(n)
    v = ratio_paired_t(a, b).values
    assert v["t"] == pytest.approx(t) and v["p"] == pytest.approx(2 * stats.t.sf(abs(t), n - 1))
    assert v["ratio"] == pytest.approx(statistics.geometric_mean([y / x for x, y in zip(a, b)]))
    assert (v["ratio_ci_low"], v["ratio_ci_high"]) == (pytest.approx(10 ** (mean - half)), pytest.approx(10 ** (mean + half)))
    v = lognormal_unpaired_t(a, b).values
    plain = unpaired_t(np.log(a), np.log(b)).values
    assert v["t"] == pytest.approx(plain["t"]) and v["p"] == pytest.approx(plain["p"])
    assert v["ratio"] == pytest.approx(statistics.geometric_mean(b) / statistics.geometric_mean(a))
    assert v["ratio_ci_low"] == pytest.approx(math.exp(plain["ci_low"])) and v["geomean_a"] == pytest.approx(statistics.geometric_mean(a))


def test_compare_runs_a_test_on_two_columns_of_a_table():
    table = ColumnTable.from_columns({"Drug 1": SLEEP_1, "Drug 2": SLEEP_2}, title="sleep", paired=True)
    result = compare(table, "paired_t", "Drug 1", "Drug 2")
    assert result.table_title == "sleep" and result.values["p"] == pytest.approx(0.002833, abs=5e-7) and not result.warnings
    assert result.row("Table analyzed", "Comparison").value == "Drug 2 vs. Drug 1"
    uneven = ColumnTable.from_columns({"A": SLEEP_1, "B": SLEEP_2[:8]})
    result = compare(uneven, "wilcoxon")
    assert result.values["n"] == 8 and any("not marked as paired" in text for text in result.warnings)
    assert compare(uneven, "mann_whitney", 0, 1).values["n_b"] == 8
    with pytest.raises(StatisticsError, match="Unknown test"):
        compare(table, "anova")
    with pytest.raises(StatisticsError, match="alternative"):
        compare(table, "unpaired_t", alternative="up")
