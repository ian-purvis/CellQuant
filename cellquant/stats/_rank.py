"""Rank-test machinery shared by the one-sample, paired and two-sample tests.

Exact P values follow a commercial package's documented rules, ties included (a commercial package 6 and later):

- Wilcoxon signed rank and matched pairs: exact for fewer than 200 values or pairs.
  
- Mann-Whitney: exact when the smaller group has at most 100 values.
  
  This engine also needs n_A·n_B ≤ 20 000, which keeps the exact calculation to about 2 seconds
  (100 × 200). Larger tables use the Gaussian approximation, where a commercial package would still be exact.

The exact null distribution conditions on the observed ranks: midranks are doubled so they are
whole numbers, and the distribution is counted by dynamic programming. Without ties it is the
classic distribution. Otherwise P comes from the Gaussian approximation with tie correction and,
as the a commercial package guide describes it, no continuity correction (``continuity=True`` adds R's 0.5).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from math import comb

import numpy as np
from scipy import stats

SIGNED_RANK_EXACT_BELOW = 200
RANK_SUM_EXACT_SMALLER = 100
RANK_SUM_EXACT_CELLS = 20_000


def signed_rank_pmf(doubled_ranks) -> np.ndarray:
    """P(2T+ = s), s = 0..Σ doubled ranks: each rank's sign is + or - with probability 1/2."""

    doubled = [int(r) for r in doubled_ranks]
    pmf = np.zeros(sum(doubled) + 1)
    pmf[0] = 1.0
    top = 0
    for r in doubled:
        pmf[r : top + r + 1] = 0.5 * pmf[r : top + r + 1] + 0.5 * pmf[: top + 1]
        pmf[:r] *= 0.5
        top += r
    return pmf


@lru_cache(maxsize=32)
def _untied_signed_rank_pmf(n: int) -> np.ndarray:
    return signed_rank_pmf(range(2, 2 * n + 1, 2))[::2]  # T+ itself, 0..n(n+1)/2


def rank_sum_pmf(tie_sizes, k: int) -> np.ndarray:
    """P(2U = s), s = 0..2·k·(N-k), where U counts (other, chosen) pairs with the chosen value larger
    (a tie counts 1/2), when k of the N values are picked at random. ``tie_sizes`` lists the sizes of
    the groups of equal values from smallest to largest value (all 1s when there are no ties).

    Going through the groups in order, putting j of a group of t into the chosen sample adds
    2·j·(other values already passed) + j·(t - j) to 2U, in C(t, j) ways."""

    sizes = [int(t) for t in tie_sizes]
    total = sum(sizes)
    other = total - k
    width = 2 * k * other + 1
    counts = np.zeros((k + 1, width))
    counts[0, 0] = 1.0
    passed = 0
    for t in sizes:
        # Rows go from high to low so that every row is read before it is added to.
        for chosen in range(min(k, passed), -1, -1):
            before = passed - chosen
            if before > other:
                break
            row = counts[chosen]
            for j in range(1, min(t, k - chosen) + 1):
                shift = 2 * j * before + j * (t - j)
                if shift >= width:
                    continue  # more values than the other sample has: not a possible arrangement
                counts[chosen + j, shift:] += comb(t, j) * row[: width - shift]
        passed += t
    return counts[k] / comb(total, k)


@lru_cache(maxsize=32)
def _untied_rank_sum_pmf(m: int, n: int) -> np.ndarray:
    """P(U = u) for n values chosen among m + n untied values, u = 0..mn."""

    return rank_sum_pmf((1,) * (m + n), n)[::2]


def exact_p(pmf: np.ndarray, observed: int, alternative: str) -> float:
    """P value of an integer statistic from its null pmf; "greater" means a large statistic. The
    two-tailed P doubles the smaller tail (at most 1)."""

    k = int(observed)
    cdf = np.cumsum(pmf)
    lower = float(cdf[k])
    upper = float(1.0 - cdf[k - 1]) if k > 0 else 1.0
    if alternative == "greater":
        return min(1.0, upper)
    if alternative == "less":
        return min(1.0, lower)
    return min(1.0, 2.0 * min(lower, upper))


def minimum_p(pmf: np.ndarray, alternative: str) -> float:
    """The smallest P any outcome could give with this null distribution (for these n and ties)."""

    cdf = np.cumsum(pmf)
    upper = 1.0 - np.concatenate([[0.0], cdf[:-1]])
    possible = pmf > 1e-15
    if alternative == "greater":
        values = upper
    elif alternative == "less":
        values = cdf
    else:
        values = 2.0 * np.minimum(cdf, upper)
    return float(min(1.0, values[possible].min()))


def normal_p(statistic: float, mean: float, sd: float, alternative: str, continuity: bool) -> tuple[float, float]:
    """(z, P) by the Gaussian approximation, with R's 0.5 continuity correction when asked."""

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
    """Hodges-Lehmann estimate and the CI at the achievable level nearest to the one asked for."""

    estimate: float
    low: float
    high: float
    confidence: float
    exact: bool


def _order_ci(sorted_values: np.ndarray, pmf: np.ndarray | None, sd: float, ci: float) -> tuple[float, float, float]:
    """CI [v(k), v(N+1-k)] from order statistics of the N differences or Walsh averages, which
    has confidence 1 - 2·P(S ≤ k-1) under H0 for the rank statistic S. Only some levels can be
    reached, so k gives the achievable level nearest to ``ci`` (the higher one on a tie), as
    Sheskin and Klotz describe and a commercial package reports (for example "96.2% CI"). Without an exact pmf,
    P(S ≤ k-1) comes from the Gaussian approximation with a 0.5 correction."""

    count = sorted_values.size
    largest_k = (count + 1) // 2
    ks = np.arange(1, largest_k + 1)
    if pmf is not None:
        cdf = np.cumsum(pmf)
        achieved = 1.0 - 2.0 * cdf[ks - 1]
    elif sd > 0:
        achieved = 1.0 - 2.0 * stats.norm.cdf((ks - 1 + 0.5 - count / 2.0) / sd)
    else:
        achieved = np.ones(ks.size)
    achieved = np.clip(achieved, 0.0, 1.0)
    distance = np.abs(achieved - ci)
    best = np.flatnonzero(distance <= distance.min() + 1e-12)
    k = int(ks[best[np.argmax(achieved[best])]])
    return float(sorted_values[k - 1]), float(sorted_values[count - k]), float(achieved[k - 1])


def hodges_lehmann_two_sample(a: np.ndarray, b: np.ndarray, ci: float, exact: bool) -> ShiftEstimate:
    """Median of all n_A·n_B differences b_j - a_i, with its rank-based CI (Sheskin pp. 521-524)."""

    differences = np.sort(np.subtract.outer(b, a).ravel())
    m, n = a.size, b.size
    pmf = _untied_rank_sum_pmf(m, n) if exact else None
    sd = np.sqrt(m * n * (m + n + 1) / 12.0)
    low, high, achieved = _order_ci(differences, pmf, sd, ci)
    return ShiftEstimate(float(np.median(differences)), low, high, achieved, exact)


def hodges_lehmann_one_sample(d: np.ndarray, ci: float, exact: bool) -> ShiftEstimate:
    """Median of the Walsh averages (d_i + d_j)/2, i ≤ j, with its rank-based CI (Sheskin pp. 234-235)."""

    n = d.size
    i, j = np.triu_indices(n)
    walsh = np.sort((d[i] + d[j]) / 2.0)
    pmf = _untied_signed_rank_pmf(n) if exact else None
    sd = np.sqrt(n * (n + 1) * (2 * n + 1) / 24.0)
    low, high, achieved = _order_ci(walsh, pmf, sd, ci)
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
    min_p: float = np.nan


def signed_rank_test(d: np.ndarray, alternative: str, zero_method: str, exact: bool | None, continuity: bool) -> SignedRank:
    """Wilcoxon signed-rank test of differences ``d`` against 0.

    ``zero_method="exclude"`` drops zero differences (Wilcoxon's method, a commercial package's original one).
    ``"pratt"`` ranks them with the others and then leaves their ranks out of both sums (Pratt 1959;
    an option from a commercial package 6 on). Exact P comes from the conditional distribution of the observed
    nonzero ranks. The Gaussian approximation's mean and variance are adjusted for ties and, under
    Pratt, for the dropped ranks (Cureton 1967). "greater" means the differences tend to be positive."""

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
    use_exact = n_used >= 1 and (n_used < SIGNED_RANK_EXACT_BELOW if exact is None else bool(exact))
    if use_exact:
        doubled = np.rint(2 * nonzero_ranks).astype(int)
        pmf = signed_rank_pmf(doubled)
        p = exact_p(pmf, int(round(2 * positive)), alternative)
        return SignedRank(n_used, n_zero, positive, negative, p, np.nan, True, ties, minimum_p(pmf, alternative))
    mean = n * (n + 1) / 4.0
    variance = n * (n + 1) * (2 * n + 1) / 24.0
    if zero_method == "pratt" and n_zero:
        mean -= n_zero * (n_zero + 1) / 4.0
        variance -= n_zero * (n_zero + 1) * (2 * n_zero + 1) / 24.0
    variance -= tie_sum(nonzero_ranks) / 48.0
    z, p = normal_p(positive, mean, np.sqrt(max(variance, 0.0)), alternative, continuity)
    return SignedRank(n_used, n_zero, positive, negative, p, z, False, ties)


def rank_sum_exact_allowed(m: int, n: int) -> bool:
    return min(m, n) <= RANK_SUM_EXACT_SMALLER and m * n <= RANK_SUM_EXACT_CELLS


def rank_sum_exact_p(a: np.ndarray, b: np.ndarray, u_b: float, alternative: str) -> tuple[float, float]:
    """Exact P for U_B, conditional on the tie pattern of the pooled values, and the smallest P
    any arrangement of these values could give."""

    _, sizes = np.unique(np.concatenate([a, b]), return_counts=True)
    m, n = a.size, b.size
    observed = int(round(2 * u_b))
    if n <= m:
        pmf = rank_sum_pmf(tuple(sizes), n)
        return exact_p(pmf, observed, alternative), minimum_p(pmf, alternative)
    # Count with the smaller sample chosen: 2U_A = 2mn - 2U_B, and its tails swap.
    flipped = {"greater": "less", "less": "greater"}.get(alternative, alternative)
    pmf = rank_sum_pmf(tuple(sizes), m)
    return exact_p(pmf, 2 * m * n - observed, flipped), minimum_p(pmf, flipped)
