"""Rank-test machinery shared by the one-sample, paired and two-sample tests.

Exact null distributions are built by recursion (no enumeration), as probabilities so that they
stay accurate for large counts. The rule for exact versus normal-approximation P values is the
one R's ``wilcox.test`` uses: exact when every sample has fewer than 50 values and there are no
ties (and, for signed ranks, no zero differences).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from scipy import stats

EXACT_LIMIT = 50


@lru_cache(maxsize=64)
def signed_rank_pmf(n: int) -> np.ndarray:
    """P(T+ = t), t = 0..n(n+1)/2, for n untied nonzero differences under H0.

    Each rank i is positive with probability 1/2, so the generating function is the product of
    (1 + q^i) / 2."""

    pmf = np.ones(1)
    for i in range(1, n + 1):
        grown = np.zeros(pmf.size + i)
        grown[: pmf.size] += 0.5 * pmf
        grown[i:] += 0.5 * pmf
        pmf = grown
    return pmf


@lru_cache(maxsize=64)
def rank_sum_pmf(m: int, n: int) -> np.ndarray:
    """P(U = u), u = 0..mn, where U counts pairs with the second-sample value larger, for m and
    n untied values under H0.

    The largest of the m + n values comes from the second sample with probability n/(m+n) and then
    beats all m first-sample values: p[m,n](u) = n/(m+n) p[m,n-1](u-m) + m/(m+n) p[m-1,n](u)."""

    previous = [np.ones(1) for _ in range(n + 1)]  # m = 0: U is always 0
    for i in range(1, m + 1):
        current = [np.ones(1)]
        for j in range(1, n + 1):
            out = np.zeros(i * j + 1)
            from_first = previous[j]
            out[: from_first.size] += i / (i + j) * from_first
            from_second = current[j - 1]
            out[i : i + from_second.size] += j / (i + j) * from_second
            current.append(out)
        previous = current
    return previous[n]


def exact_p(pmf: np.ndarray, observed: float, alternative: str) -> float:
    """P value of an integer statistic from its null pmf; "greater" means a large statistic."""

    k = int(round(observed))
    cdf = np.cumsum(pmf)
    lower = float(cdf[k])
    upper = float(1.0 - cdf[k - 1]) if k > 0 else 1.0
    if alternative == "greater":
        return min(1.0, upper)
    if alternative == "less":
        return min(1.0, lower)
    return min(1.0, 2.0 * min(lower, upper))


def normal_p(statistic: float, mean: float, sd: float, alternative: str, continuity: bool) -> tuple[float, float]:
    """(z, P) by the normal approximation, with R's continuity correction when asked."""

    if not sd > 0:
        return np.nan, np.nan
    centered = statistic - mean
    if continuity:
        shift = {"greater": 0.5, "less": -0.5}.get(alternative, 0.5 * np.sign(centered))
        centered -= shift
    z = centered / sd
    if alternative == "greater":
        return z, float(stats.norm.sf(z))
    if alternative == "less":
        return z, float(stats.norm.cdf(z))
    return z, float(min(1.0, 2.0 * stats.norm.sf(abs(z))))


def tie_sum(values: np.ndarray) -> float:
    """Σ (t³ - t) over groups of tied values."""

    _, counts = np.unique(values, return_counts=True)
    return float(np.sum(counts.astype(float) ** 3 - counts))


@dataclass
class ShiftEstimate:
    """Hodges-Lehmann estimate with its confidence interval (achieved confidence may exceed the target)."""

    estimate: float
    low: float
    high: float
    confidence: float
    exact: bool


def _order_ci(sorted_values: np.ndarray, pmf: np.ndarray | None, mean: float, sd: float, ci: float) -> tuple[float, float, float]:
    """CI from order statistics: [v(k), v(N-k+1)] where k is the largest integer with
    P(S ≤ k-1) < α/2 under H0 (the rule R uses). Without an exact pmf, k comes from the normal
    approximation to the same rule, k = floor(N/2 - z·sd + 0.5)."""

    count = sorted_values.size
    half_alpha = (1.0 - ci) / 2.0
    if pmf is not None:
        cdf = np.cumsum(pmf)
        k = int(np.sum(cdf < half_alpha - 1e-12))
        k = max(k, 1)
        achieved = 1.0 - 2.0 * float(cdf[k - 1]) if k >= 1 else 1.0
    else:
        z = stats.norm.ppf(1.0 - half_alpha)
        k = max(int(np.floor(count / 2.0 - z * sd + 0.5)), 1)
        achieved = 1.0 - 2.0 * float(stats.norm.cdf((k - 1 + 0.5 - count / 2.0) / sd)) if sd > 0 else np.nan
    k = min(k, (count + 1) // 2)
    return float(sorted_values[k - 1]), float(sorted_values[count - k]), achieved


def hodges_lehmann_two_sample(a: np.ndarray, b: np.ndarray, ci: float, exact: bool) -> ShiftEstimate:
    """Median of all differences b_j - a_i, with its distribution-free CI (Bauer 1972, as R)."""

    differences = np.sort(np.subtract.outer(b, a).ravel())
    m, n = a.size, b.size
    pmf = rank_sum_pmf(m, n) if exact else None
    sd = np.sqrt(m * n * (m + n + 1) / 12.0)
    low, high, achieved = _order_ci(differences, pmf, m * n / 2.0, sd, ci)
    return ShiftEstimate(float(np.median(differences)), low, high, achieved, exact)


def hodges_lehmann_one_sample(d: np.ndarray, ci: float, exact: bool) -> ShiftEstimate:
    """Median of the Walsh averages (d_i + d_j)/2, i ≤ j (the pseudo-median), with its CI."""

    n = d.size
    i, j = np.triu_indices(n)
    walsh = np.sort((d[i] + d[j]) / 2.0)
    pmf = signed_rank_pmf(n) if exact else None
    sd = np.sqrt(n * (n + 1) * (2 * n + 1) / 24.0)
    low, high, achieved = _order_ci(walsh, pmf, n * (n + 1) / 4.0, sd, ci)
    return ShiftEstimate(float(np.median(walsh)), low, high, achieved, exact)


@dataclass
class SignedRank:
    n_used: int
    n_zero: int
    positive: float
    negative: float
    p: float
    z: float
    exact: bool
    ties: bool


def signed_rank_test(d: np.ndarray, alternative: str, zero_method: str, exact: bool | None, continuity: bool) -> SignedRank:
    """Wilcoxon signed-rank test of differences ``d`` against 0.

    ``zero_method="exclude"`` drops zero differences (Wilcoxon's method). ``"pratt"`` ranks
    them with the others and then leaves their ranks out of both sums; the null mean and variance
    are adjusted for the dropped ranks (Cureton 1967), and P is always from the normal approximation.
    "greater" means the differences tend to be positive."""

    if zero_method not in ("exclude", "pratt"):
        raise ValueError("zero_method must be 'exclude' or 'pratt'.")
    zero = d == 0
    n_zero = int(zero.sum())
    ranked = d[~zero] if zero_method == "exclude" else d
    ranks = stats.rankdata(np.abs(ranked))
    positive = float(ranks[ranked > 0].sum())
    negative = float(ranks[ranked < 0].sum())
    nonzero_ranks = ranks[ranked != 0]
    ties = nonzero_ranks.size != np.unique(nonzero_ranks).size
    n = ranked.size
    n_used = int(nonzero_ranks.size)
    can_exact = not ties and (n_zero == 0 or zero_method == "exclude") and n_used >= 1
    use_exact = (can_exact and n_used < EXACT_LIMIT) if exact is None else (bool(exact) and can_exact)
    if use_exact:
        return SignedRank(n_used, n_zero, positive, negative, exact_p(signed_rank_pmf(n_used), positive, alternative), np.nan, True, ties)
    mean = n * (n + 1) / 4.0
    variance = n * (n + 1) * (2 * n + 1) / 24.0
    if zero_method == "pratt" and n_zero:
        mean -= n_zero * (n_zero + 1) / 4.0
        variance -= n_zero * (n_zero + 1) * (2 * n_zero + 1) / 24.0
    variance -= tie_sum(nonzero_ranks) / 48.0
    z, p = normal_p(positive, mean, np.sqrt(max(variance, 0.0)), alternative, continuity)
    return SignedRank(n_used, n_zero, positive, negative, p, z, False, ties)
