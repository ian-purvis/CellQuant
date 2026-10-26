"""Multiple comparisons after a one-way ANOVA (or its nonparametric and repeated-measures forms),
P value adjustment, FDR q values, compact letter displays and "Analyze a stack of P values".

Every comparison is a difference between two groups, ``group_1 - group_2`` for the row
"group_1 vs. group_2", as in the usual tables. For comparisons against a reference (control)
group the row is "reference vs. group", so the difference is reference - group.

The engine works on group estimates (means, or mean ranks for Dunn's test) with a covariance
matrix V of those estimates and an error df:

- ordinary ANOVA: V = MS_residual · diag(1/n_i), df = N - k
- repeated measures, sphericity assumed: V = MS_residual · I/n, df = (n-1)(k-1)
- repeated measures without assuming sphericity: V = S/n (S = covariance of the columns), df = n-1,
  so each comparison uses only the two columns it compares
- mixed-effects model: V = the covariance of the fitted group means, df from the model
- unequal SDs (Welch / Brown-Forsythe ANOVA): SE² = s_i²/n_i + s_j²/n_j with Welch's df per comparison
- Dunn's test: mean ranks with V = σ²_rank · diag(1/n_i) and df = ∞ (a z test)

Methods and what they report (adjusted P = multiplicity-adjusted P; CI = simultaneous CI):

=================  =============================================  ============  ===
method             statistic and adjustment                       adjusted P    CI
=================  =============================================  ============  ===
tukey              q = |diff|/SE·√2, studentized range (k, df);   yes           yes
                   Tukey-Kramer when n differ
newman_keuls       q as Tukey, range of the r means spanned,      no          no
                   stepwise
dunnett            t = diff/SE, max |t| of the k-1 comparisons    yes           yes
                   with one control (multivariate t)
sidak              t, P_adj = 1 - (1-P)^m                         yes           yes
bonferroni         t, P_adj = min(1, m·P)                         yes           yes
holm_sidak         t, step-down Šidák                             yes           no
fisher_lsd         t, no adjustment                               (= P)         yes
games_howell       q with Welch SE and df, studentized range      yes           yes
dunnett_t3         t with Welch SE and df, studentized maximum    yes           yes
                   modulus (m, df)
tamhane_t2         Welch t, Šidák                                 yes           yes
dunn               z of mean-rank difference, ties corrected,     yes           no
                   P_adj = min(1, m·P)
dunn_uncorrected   z, no adjustment                               (= P)         no
bky, bh, by        q values from the unadjusted P (Fisher's LSD,  q             no
                   or uncorrected Dunn's)
=================  =============================================  ============  ===
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np
from scipy import optimize, stats

from cellquant.errors import StatisticsError
from cellquant.stats.pvalues import format_p, p_summary
from cellquant.stats.results import AnalysisResult, fmt, fmt_ci

# ---------------------------------------------------------------------------------------------
# Distributions
# ---------------------------------------------------------------------------------------------


def _df(df: float) -> float:
    return math.inf if df is None or not np.isfinite(df) or df > 1e7 else float(df)


def ptukey_sf(q: float, k: int, df: float) -> float:
    """P(studentized range of k means with ``df`` error df ≥ q)."""

    if not np.isfinite(q):
        return np.nan
    return float(min(1.0, max(0.0, stats.studentized_range.sf(q, k, _df(df) if np.isfinite(_df(df)) else 1e7))))


def qtukey(level: float, k: int, df: float) -> float:
    return float(stats.studentized_range.ppf(level, k, _df(df) if np.isfinite(_df(df)) else 1e7))


_LEGENDRE = np.polynomial.legendre.leggauss(256)


def _expect_over_u(fn, df: float) -> float:
    """E[fn(u)] for u = √(χ²_df / df) (u = 1 when df is infinite). ``fn`` takes an array of u.
    256-point Gauss-Legendre over the central 1 - 2·10⁻¹⁵ of u's distribution (agrees with
    adaptive quadrature to about 1e-11)."""

    df = _df(df)
    if not np.isfinite(df):
        return float(np.asarray(fn(np.array([1.0])))[0])
    lo = math.sqrt(stats.chi2.ppf(1e-15, df) / df)
    hi = math.sqrt(stats.chi2.isf(1e-15, df) / df)
    x, w = _LEGENDRE
    u = (hi - lo) / 2 * x + (hi + lo) / 2
    log_density = (df / 2) * math.log(df) - (df / 2 - 1) * math.log(2) - math.lgamma(df / 2) + (df - 1) * np.log(u) - df * u * u / 2
    return float(np.sum(w * (hi - lo) / 2 * np.exp(log_density) * np.asarray(fn(u))))


_HERMITE = np.polynomial.hermite_e.hermegauss(120)


def _one_factor(corr: np.ndarray) -> np.ndarray | None:
    """λ with corr_ij = λ_i λ_j (i ≠ j), 0 ≤ λ < 1, when the correlation matrix has that form."""

    m = corr.shape[0]
    if m == 1:
        return np.array([0.0])
    off = corr[~np.eye(m, dtype=bool)]
    if np.any(off < 0) or np.any(off >= 1):
        return None
    if m == 2:
        lam = math.sqrt(corr[0, 1])
        return np.array([lam, lam])
    lam = np.empty(m)
    for i in range(m):
        j, k = [x for x in range(m) if x != i][:2]
        if corr[j, k] <= 0:
            return None
        lam[i] = math.sqrt(corr[i, j] * corr[i, k] / corr[j, k])
    fitted = np.outer(lam, lam)
    if np.any(lam >= 1) or np.max(np.abs((fitted - corr)[~np.eye(m, dtype=bool)])) > 1e-9:
        return None
    return lam


def max_abs_t_cdf(c: float, corr: np.ndarray, df: float, maxpts: int = 100_000) -> float:
    """P(max_i |T_i| ≤ c) for a multivariate t with correlation ``corr`` and ``df`` degrees of freedom.

    With a one-factor correlation (corr_ij = λ_i λ_j, true for Dunnett's comparisons with a pooled
    error term), the probability is the classic double integral (Dunnett 1955): Gauss-Hermite over
    the shared normal factor and Gauss-Legendre quadrature over the error SD. Otherwise scipy's
    quasi-Monte Carlo multivariate t is used with ``maxpts`` points and a fixed seed (with the
    default 100 000 points, about 6 correct decimals)."""

    corr = np.atleast_2d(np.asarray(corr, dtype=float))
    if not np.isfinite(c):
        return np.nan
    if c <= 0:
        return 0.0
    lam = _one_factor(corr)
    if lam is not None:
        nodes, weights = _HERMITE
        weights = weights / math.sqrt(2 * math.pi)
        scale = np.sqrt(1 - lam**2)[:, None]

        def inner(u: np.ndarray) -> np.ndarray:
            shift = lam[:, None, None] * nodes[None, :, None]
            cu = c * np.asarray(u)[None, None, :]
            upper = (cu + shift) / scale[:, :, None]
            lower = (-cu + shift) / scale[:, :, None]
            inside = np.prod(stats.norm.cdf(upper) - stats.norm.cdf(lower), axis=0)
            return np.sum(weights[:, None] * inside, axis=0)

        return min(1.0, max(0.0, _expect_over_u(inner, df)))
    m = corr.shape[0]
    if np.isfinite(_df(df)):
        dist = stats.multivariate_t(loc=np.zeros(m), shape=corr, df=_df(df), seed=0)
        return float(dist.cdf(np.full(m, c), lower_limit=np.full(m, -c), maxpts=maxpts))
    dist = stats.multivariate_normal(mean=np.zeros(m), cov=corr, seed=0, maxpts=maxpts, abseps=1e-8, releps=1e-8)
    return float(dist.cdf(np.full(m, c), lower_limit=np.full(m, -c)))


def smm_cdf(c: float, m: int, df: float) -> float:
    """Studentized maximum modulus: P(max of m independent |Z_i|/u ≤ c), u = √(χ²_df/df)."""

    if c <= 0:
        return 0.0
    return min(1.0, max(0.0, _expect_over_u(lambda u: (2 * stats.norm.cdf(c * u) - 1) ** m, df)))


def _critical(cdf, level: float) -> float:
    """c with cdf(c) = level."""

    hi = 2.0
    while cdf(hi) < level:
        hi *= 2
        if hi > 1e4:
            return np.inf
    return float(optimize.brentq(lambda c: cdf(c) - level, 0.0, hi, xtol=1e-10))


# ---------------------------------------------------------------------------------------------
# Adjusting a list of P values
# ---------------------------------------------------------------------------------------------

ADJUSTMENTS = ("bonferroni", "sidak", "holm", "holm_sidak", "bh", "by", "bky", "none")


def _bh(p: np.ndarray) -> np.ndarray:
    m = p.size
    order = np.argsort(p, kind="mergesort")
    ranked = p[order] * m / np.arange(1, m + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(m)
    out[order] = np.minimum(ranked, 1.0)
    return out


def _bky_at(p: np.ndarray, q: float) -> tuple[np.ndarray, int]:
    """Discoveries of the two-stage BKY procedure at FDR level q, and N_true (m when undefined)."""

    m = p.size
    base = _bh(p)
    q1 = q / (1 + q)
    r1 = int(np.sum(base <= q1))
    if r1 == 0:
        return np.zeros(m, bool), m
    if r1 == m:
        return np.ones(m, bool), m
    n_true = m - r1
    return base <= q1 * m / n_true, n_true


def _bky(p: np.ndarray, q: float) -> tuple[np.ndarray, np.ndarray, int]:
    """Border q values of the two-stage BKY procedure, discoveries at ``q``, and N_true at ``q``.

    With x = BH-adjusted P, stage 1 at level Q passes the x_j ≤ Q/(1+Q), so r₁(Q) is a step
    function that changes at Q = x_j/(1 - x_j). Within each interval where r₁ = r is constant
    (0 < r < m), comparison i is a discovery once Q/(1+Q) ≥ x_i·(m - r)/m; with r = m every
    comparison is. Its q value is the smallest such Q (capped at 1). Discovery is monotone in Q
    (a larger Q passes more in stage 1, so N_true falls and every threshold rises)."""

    m = p.size
    x = _bh(p)
    with np.errstate(divide="ignore"):
        breaks = np.sort(np.where(x < 1, x / (1 - x), np.inf))
    edges = np.concatenate([[0.0], breaks, [np.inf]])
    qv = np.full(m, np.inf)
    for r in range(1, m + 1):
        left, right = edges[r], edges[r + 1]
        if not np.isfinite(left) or left >= right:
            continue
        if r == m:
            qv = np.minimum(qv, left)
            break
        y = x * (m - r) / m
        with np.errstate(divide="ignore"):
            need = np.where(y < 1, y / (1 - y), np.inf)
        need = np.maximum(need, left)
        ok = need < right
        qv = np.where(ok & (need < qv), need, qv)
    discovered, n_true = _bky_at(p, q)
    return np.minimum(qv, 1.0), discovered, n_true


def adjust_p(pvalues: Iterable[float], method: str, q: float = 0.05) -> dict:
    """Multiplicity-adjusted P values (or FDR q values) for a list of P values.

    - ``bonferroni``: min(1, m·P). ``sidak``: 1 - (1 - P)^m.
    - ``holm``: step-down Bonferroni, ``holm_sidak``: step-down Šidák. With P sorted ascending,
      P_adj(r) = max over s ≤ r of 1 - (1 - P(s))^(m - s + 1) (Holm-Šidák).
    - ``bh`` (Benjamini-Hochberg 1995): q(r) = min over s ≥ r of m·P(s)/s.
    - ``by`` (Benjamini-Yekutieli 2001): the BH q times Σ_{i=1..m} 1/i (valid under any dependence).
    - ``bky`` (two-stage step-up, Benjamini, Krieger and Yekutieli 2006):
      stage 1 is BH at Q' = Q/(1 + Q), giving r₁ discoveries (none if r₁ = 0, all if r₁ = m);
      otherwise N_true = m - r₁ estimates the true nulls and stage 2 is BH at Q'·m/N_true, i.e. the
      rank-i threshold is i·Q/[(1 + Q)·N_true] (q/[(1+q)·Ntrue] … [q/(1+q)]·N/Ntrue).
      The q value is the Q at which the comparison sits exactly on the border of being a
      discovery (N_true changes with Q, so it is found interval by interval; see ``_bky``).
      Discoveries at the chosen Q are those with q ≤ Q.

    Returns ``{"adjusted": array, "significant": array, "m0": ...}``; blanks (NaN) are left out of m
    and stay blank."""

    method = method.lower().replace("-", "_")
    if method not in ADJUSTMENTS:
        raise StatisticsError(f"Unknown adjustment {method!r}; choose one of {', '.join(ADJUSTMENTS)}.")
    if not 0 < q < 1:
        raise StatisticsError("The family-wise alpha or Q must be between 0 and 1.")
    p_all = np.asarray(list(pvalues), dtype=float)
    keep = np.isfinite(p_all)
    if np.any((p_all[keep] < 0) | (p_all[keep] > 1)):
        raise StatisticsError("P values must be between 0 and 1.")
    p = p_all[keep]
    m = p.size
    out = np.full(p_all.shape, np.nan)
    info: dict = {"m": m, "m0": np.nan}
    if m == 0:
        return {"adjusted": out, "significant": np.zeros(p_all.shape, bool), **info}
    order = np.argsort(p, kind="mergesort")
    if method == "none":
        adj = p.copy()
    elif method == "bonferroni":
        adj = np.minimum(1.0, m * p)
    elif method == "sidak":
        adj = -np.expm1(m * np.log1p(-np.minimum(p, 1 - 1e-300))) if m else p
        adj = np.where(p >= 1, 1.0, adj)
    elif method in ("holm", "holm_sidak"):
        sorted_p = p[order]
        factors = m - np.arange(m)
        if method == "holm":
            step = np.minimum(1.0, factors * sorted_p)
        else:
            step = np.where(sorted_p >= 1, 1.0, -np.expm1(factors * np.log1p(-np.minimum(sorted_p, 1 - 1e-300))))
        step = np.maximum.accumulate(step)
        adj = np.empty(m)
        adj[order] = np.minimum(step, 1.0)
    elif method == "bh":
        adj = _bh(p)
    elif method == "by":
        adj = np.minimum(1.0, _bh(p) * np.sum(1.0 / np.arange(1, m + 1)))
    else:  # bky
        adj, discovered, m0 = _bky(p, q)
        info["m0"] = m0
    out[keep] = adj
    if method == "bky":
        significant = np.zeros(p_all.shape, bool)
        significant[keep] = discovered
        return {"adjusted": out, "significant": significant, **info}
    out[keep] = adj
    significant = np.zeros(p_all.shape, bool)
    fdr = method in ("bh", "by", "bky")
    significant[keep] = (adj <= q) if fdr else (adj < q)
    return {"adjusted": out, "significant": significant, **info}


FDR_METHODS = {"bky": "Two-stage step-up method of Benjamini, Krieger and Yekutieli", "bh": "Original FDR method of Benjamini and Hochberg",
               "by": "Corrected method of Benjamini and Yekutieli"}
STACK_METHODS = {**FDR_METHODS, "bonferroni": "Bonferroni-Dunn", "sidak": "Šidák", "holm": "Holm (Bonferroni step-down)",
                 "holm_sidak": "Holm-Šidák", "none": "No correction"}


def p_value_stack(pvalues: Iterable[float], method: str = "bky", *, alpha: float = 0.05, labels: Sequence[str] | None = None,
                  style: str = "classic", title: str = "") -> AnalysisResult:
    """Analyze a stack of P values: FDR q values (``bky``, ``bh``, ``by``; ``alpha`` is
    the desired FDR, Q) or family-wise adjusted P values (``bonferroni``, ``sidak``, ``holm``,
    ``holm_sidak``) for a pasted list of P values. Blank entries are left out.
    ``values["table"]`` lists each P with its rank, adjusted P or q, and discovery/significance."""

    p = np.asarray(list(pvalues), dtype=float)
    method = method.lower().replace("-", "_")
    if method not in STACK_METHODS:
        raise StatisticsError(f"Unknown method {method!r}; choose one of {', '.join(STACK_METHODS)}.")
    if labels is not None and len(labels) != p.size:
        raise StatisticsError("Give one label per P value.")
    labels = [str(x) for x in labels] if labels is not None else [f"P{i + 1}" for i in range(p.size)]
    adj = adjust_p(p, method, alpha)
    fdr = method in FDR_METHODS
    result = AnalysisResult("Analyze a stack of P values", title, {"method": method, "alpha": alpha, "style": style})
    head = result.section("Method")
    head.add("Approach", STACK_METHODS[method])
    head.add("Q (desired FDR)" if fdr else "Family-wise alpha", alpha, f"{100 * alpha:g}%")
    head.add("Number of P values", adj["m"])
    keep = np.isfinite(p)
    ranks = np.full(p.size, np.nan)
    ranks[keep] = stats.rankdata(p[keep], method="ordinal")
    rows = []
    section = result.section("Results")
    word = "Discovery?" if fdr else "Significant?"
    for i, label in enumerate(labels):
        row = {"label": label, "p": float(p[i]), "rank": None if not keep[i] else int(ranks[i]), "adjusted": float(adj["adjusted"][i]),
               "significant": bool(adj["significant"][i]) if keep[i] else None, "summary": p_summary(float(adj["adjusted"][i]), style)}
        rows.append(row)
        if keep[i]:
            section.add(label, row, f"P = {format_p(row['p'], style=style)}; {'q' if fdr else 'adjusted P'} = {format_p(row['adjusted'], style=style)}; "
                                     f"{word} {'Yes' if row['significant'] else 'No'}")
    found = int(np.sum(adj["significant"]))
    result.section("Summary").add("Discoveries" if fdr else "Significant", found)
    if method == "bky":
        result.section("Summary").add("Estimated number of true null hypotheses (m0)", adj["m0"])
    if (~keep).sum():
        result.warnings.append(f"{int((~keep).sum())} blank entr{'y' if (~keep).sum() == 1 else 'ies'} left out.")
    result.values.update(table=rows, adjusted=adj["adjusted"], significant=adj["significant"], m=adj["m"], m0=adj["m0"], discoveries=found)
    what = f"false discovery rate controlled at Q = {100 * alpha:g}% ({STACK_METHODS[method]})" if fdr else f"{STACK_METHODS[method]} correction, family-wise alpha = {alpha:g}"
    result.methods = f"{adj['m']} P values analyzed with the {what}."
    return result


# ---------------------------------------------------------------------------------------------
# Comparisons between groups
# ---------------------------------------------------------------------------------------------

METHOD_NAMES = {
    "tukey": "Tukey's multiple comparisons test",
    "newman_keuls": "Newman-Keuls multiple comparisons test",
    "dunnett": "Dunnett's multiple comparisons test",
    "sidak": "Šidák's multiple comparisons test",
    "bonferroni": "Bonferroni's multiple comparisons test",
    "holm_sidak": "Holm-Šidák's multiple comparisons test",
    "fisher_lsd": "Uncorrected Fisher's LSD",
    "games_howell": "Games-Howell's multiple comparisons test",
    "dunnett_t3": "Dunnett's T3 multiple comparisons test",
    "tamhane_t2": "Tamhane's T2 multiple comparisons test",
    "dunn": "Dunn's multiple comparisons test",
    "dunn_uncorrected": "Uncorrected Dunn's test",
    "bky": "FDR: two-stage step-up method of Benjamini, Krieger and Yekutieli",
    "bh": "FDR: original method of Benjamini and Hochberg",
    "by": "FDR: corrected method of Benjamini and Yekutieli",
}
EQUAL_SD_METHODS = ("tukey", "newman_keuls", "dunnett", "sidak", "bonferroni", "holm_sidak", "fisher_lsd", "bky", "bh", "by")
UNEQUAL_SD_METHODS = ("games_howell", "dunnett_t3", "tamhane_t2", "fisher_lsd", "bky", "bh", "by")
RANK_METHODS = ("dunn", "dunn_uncorrected", "bky", "bh", "by")
ALL_PAIRS_ONLY = ("tukey", "newman_keuls", "games_howell")
GOALS = ("all", "control", "selected")


@dataclass
class Estimates:
    """What a multiple comparison needs from the analysis before it."""

    names: list[str]
    estimates: np.ndarray  # group means (or mean ranks)
    n: np.ndarray
    cov: np.ndarray | None  # covariance matrix of the estimates (None for Welch-type SEs)
    df: float  # error df (inf for z tests); ignored when welch
    kind: str = "pooled"  # "pooled" | "welch" | "rank"
    sd: np.ndarray | None = None  # group SDs, for Welch-type SEs
    error_label: str = ""


@dataclass
class ComparisonPlan:
    """Which comparisons to make.

    ``goal``: "all" (every pair), "control" (every group vs. one or two reference groups) or
    "selected" (the ``pairs`` given). ``control`` is a group name, or a list of up to two
    (for example a negative and a positive control). ``family``: with two references,
    "per_reference" (default) makes each reference its own family, as running a vs.-control
    analysis once per reference; "combined" corrects all of them as one family."""

    goal: str = "all"
    method: str | None = None
    control: str | Sequence[str] | None = None
    pairs: Sequence[tuple[str, str]] | None = None
    family: str = "per_reference"

    def references(self) -> list[str]:
        if self.control is None:
            return []
        return [self.control] if isinstance(self.control, str) else [str(x) for x in self.control]


def plan_from(comparisons, method=None, control=None, pairs=None, family="per_reference") -> ComparisonPlan | None:
    if comparisons is None or comparisons is False:
        return None
    if isinstance(comparisons, ComparisonPlan):
        return comparisons
    if isinstance(comparisons, dict):
        return ComparisonPlan(**comparisons)
    return ComparisonPlan(goal=str(comparisons), method=method, control=control, pairs=pairs, family=family)


def default_method(goal: str, kind: str) -> str:
    if kind == "rank":
        return "dunn"
    if kind == "welch":
        return "games_howell" if goal == "all" else "dunnett_t3"
    return {"all": "tukey", "control": "dunnett", "selected": "sidak"}[goal]


@dataclass
class _Family:
    reference: str | None
    pairs: list[tuple[int, int]] = field(default_factory=list)


def _families(plan: ComparisonPlan, names: list[str]) -> list[_Family]:
    index = {name: i for i, name in enumerate(names)}

    def find(name) -> int:
        if str(name) not in index:
            raise StatisticsError(f"No group {name!r}; groups are {', '.join(names)}.")
        return index[str(name)]

    k = len(names)
    if plan.goal == "all":
        return [_Family(None, [(i, j) for i in range(k) for j in range(i + 1, k)])]
    if plan.goal == "selected":
        if not plan.pairs:
            raise StatisticsError("Choose at least one pair of groups to compare.")
        chosen = []
        for a, b in plan.pairs:
            i, j = find(a), find(b)
            if i == j:
                raise StatisticsError(f"Cannot compare {a!r} with itself.")
            if (i, j) in chosen or (j, i) in chosen:
                raise StatisticsError(f"The pair {a!r}, {b!r} is listed twice.")
            chosen.append((i, j))
        return [_Family(None, chosen)]
    refs = plan.references()
    if not 1 <= len(refs) <= 2:
        raise StatisticsError("Compare against one or two reference groups (for example a negative and a positive control).")
    if len(set(refs)) != len(refs):
        raise StatisticsError("The two reference groups must differ.")
    if plan.family not in ("per_reference", "combined"):
        raise StatisticsError('family must be "per_reference" or "combined".')
    families = [_Family(ref, [(find(ref), j) for j in range(k) if j != find(ref)]) for ref in refs]
    if plan.family == "combined" and len(families) == 2:
        first = families[0].pairs
        second = [(i, j) for i, j in families[1].pairs if (j, i) not in first]
        tagged = [(f.reference, p) for f, ps in ((families[0], first), (families[1], second)) for p in ps]
        combined = _Family(None, [p for _, p in tagged])
        combined.tags = [r for r, _ in tagged]  # type: ignore[attr-defined]
        return [combined]
    return families


def _welch_df(sd: np.ndarray, n: np.ndarray, i: int, j: int) -> tuple[float, float]:
    a, b = sd[i] ** 2 / n[i], sd[j] ** 2 / n[j]
    se = math.sqrt(a + b)
    df = (a + b) ** 2 / (a * a / (n[i] - 1) + b * b / (n[j] - 1)) if (a + b) > 0 else np.nan
    return se, df


def _contrast(est: Estimates, i: int, j: int) -> tuple[float, float, float, np.ndarray]:
    k = len(est.names)
    vec = np.zeros(k)
    vec[i], vec[j] = 1.0, -1.0
    diff = float(est.estimates[i] - est.estimates[j])
    if est.kind == "welch":
        se, df = _welch_df(est.sd, est.n, i, j)
    else:
        se, df = math.sqrt(max(float(vec @ est.cov @ vec), 0.0)), est.df
    return diff, se, df, vec


def run_comparisons(est: Estimates, plan: ComparisonPlan, *, alpha: float = 0.05, ci: float = 0.95, style: str = "classic") -> dict:
    """Make the comparisons in ``plan`` from ``est``. Returns ``{"rows": [...], "method": ..., ...}``;
    each row is a dict (see docs/STATISTICS.md, "Multiple comparisons")."""

    if plan.goal not in GOALS:
        raise StatisticsError(f"Unknown comparison goal {plan.goal!r}; choose one of {', '.join(GOALS)} (or use the test for trend).")
    method = (plan.method or default_method(plan.goal, est.kind)).lower().replace("-", "_")
    allowed = {"pooled": EQUAL_SD_METHODS, "welch": UNEQUAL_SD_METHODS, "rank": RANK_METHODS}[est.kind]
    if method not in METHOD_NAMES:
        raise StatisticsError(f"Unknown multiple comparisons method {method!r}; choose one of {', '.join(METHOD_NAMES)}.")
    if method not in allowed:
        what = {"pooled": "when equal SDs are assumed", "welch": "when SDs are not assumed equal", "rank": "after a nonparametric test"}[est.kind]
        raise StatisticsError(f"{METHOD_NAMES[method]} is not available {what}; choose one of {', '.join(allowed)}.")
    if method in ALL_PAIRS_ONLY and plan.goal != "all":
        raise StatisticsError(f"{METHOD_NAMES[method]} compares every pair of groups; for other comparisons use Šidák, Holm-Šidák, Bonferroni"
                              f"{' or Dunnett' if est.kind == 'pooled' else ''}.")
    if method == "dunnett" and plan.goal != "control":
        raise StatisticsError("Dunnett's test compares each group with a reference (control) group; use comparisons='control'.")
    families = _families(plan, est.names)
    if method == "dunnett" and plan.family == "combined" and len(plan.references()) == 2:
        raise StatisticsError("Dunnett's test is defined for one reference group per family. With two references, use family='per_reference' "
                              "(one Dunnett family per reference) or Šidák, Holm-Šidák or Bonferroni on the combined family.")
    k = len(est.names)
    fdr = method in FDR_METHODS
    rows: list[dict] = []
    warnings: list[str] = []
    for number, family in enumerate(families, start=1):
        m = len(family.pairs)
        tags = getattr(family, "tags", [family.reference] * m)
        data = [(*_contrast(est, i, j), i, j) for i, j in family.pairs]
        diffs = np.array([d[0] for d in data])
        ses = np.array([d[1] for d in data])
        dfs = np.array([d[2] for d in data], dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            t = np.where(ses > 0, diffs / ses, np.nan)
        if np.any(~(ses > 0)):
            warnings.append("Some comparisons have zero standard error (no variation), so their statistic and P are undefined.")
        raw = np.array([2 * stats.norm.sf(abs(x)) if not np.isfinite(_df(d)) else 2 * stats.t.sf(abs(x), d) for x, d in zip(t, dfs)])
        raw = np.where(np.isfinite(t), np.minimum(raw, 1.0), np.nan)
        adjusted = np.full(m, np.nan)
        half = np.full(m, np.nan)
        stat_name = "z" if est.kind == "rank" else "t"
        statistic = t.copy()
        significant = np.zeros(m, bool)
        if method in ("tukey", "games_howell"):
            stat_name = "q"
            statistic = np.abs(t) * math.sqrt(2)
            adjusted = np.array([ptukey_sf(q, k, d) for q, d in zip(statistic, dfs)])
            half = np.array([qtukey(ci, k, d) / math.sqrt(2) for d in dfs]) * ses
        elif method == "newman_keuls":
            stat_name = "q"
            statistic = np.abs(t) * math.sqrt(2)
            order = np.argsort(est.estimates, kind="mergesort")
            position = np.empty(k, int)
            position[order] = np.arange(k)
            spans = [(min(position[i], position[j]), max(position[i], position[j])) for i, j in family.pairs]
            step_p = np.array([ptukey_sf(q, b - a + 1, d) for q, (a, b), d in zip(statistic, spans, dfs)])
            for idx, (a, b) in enumerate(spans):
                containing = [step_p[o] for o, (a2, b2) in enumerate(spans) if a2 <= a and b2 >= b]
                significant[idx] = bool(np.nanmax(containing) < alpha)
        elif method == "dunnett":
            vecs = np.array([d[3] for d in data])
            cov = vecs @ est.cov @ vecs.T
            corr = cov / np.outer(ses, ses)
            df0 = float(dfs[0])
            adjusted = np.array([1 - max_abs_t_cdf(abs(x), corr, df0) if np.isfinite(x) else np.nan for x in t])
            crit = _critical(lambda c: max_abs_t_cdf(c, corr, df0, maxpts=20_000), ci)
            half = crit * ses
        elif method in ("sidak", "tamhane_t2"):
            adjusted = adjust_p(raw, "sidak")["adjusted"]
            level = ci ** (1.0 / m)
            half = np.array([stats.t.ppf(0.5 + level / 2, d) if np.isfinite(_df(d)) else stats.norm.ppf(0.5 + level / 2) for d in dfs]) * ses
        elif method == "bonferroni":
            adjusted = adjust_p(raw, "bonferroni")["adjusted"]
            level = 1 - (1 - ci) / m
            half = np.array([stats.t.ppf(0.5 + level / 2, d) if np.isfinite(_df(d)) else stats.norm.ppf(0.5 + level / 2) for d in dfs]) * ses
        elif method == "holm_sidak":
            adjusted = adjust_p(raw, "holm_sidak")["adjusted"]
        elif method == "fisher_lsd":
            adjusted = raw.copy()
            half = np.array([stats.t.ppf(0.5 + ci / 2, d) for d in dfs]) * ses
        elif method == "dunnett_t3":
            adjusted = np.array([1 - smm_cdf(abs(x), m, d) if np.isfinite(x) else np.nan for x, d in zip(t, dfs)])
            half = np.array([_critical(lambda c, d=d: smm_cdf(c, m, d), ci) for d in dfs]) * ses
        elif method == "dunn":
            adjusted = adjust_p(raw, "bonferroni")["adjusted"]
        elif method == "dunn_uncorrected":
            adjusted = raw.copy()
        else:  # FDR
            adjusted = adjust_p(raw, method, alpha)["adjusted"]
        if method != "newman_keuls":
            significant = (adjusted <= alpha) if fdr else (adjusted < alpha)
        if est.kind == "rank":
            half[:] = np.nan
        for idx, (diff, se, df, _vec, i, j) in enumerate(data):
            p_adj = float(adjusted[idx])
            summary = p_summary(p_adj, style) if np.isfinite(p_adj) else ("*" if significant[idx] else "ns")
            rows.append({
                "label": f"{est.names[i]} vs. {est.names[j]}", "group_1": est.names[i], "group_2": est.names[j],
                "reference": tags[idx], "family": number,
                "estimate_1": float(est.estimates[i]), "estimate_2": float(est.estimates[j]), "n_1": float(est.n[i]), "n_2": float(est.n[j]),
                "difference": diff, "se": se, "df": float(df), "statistic": float(statistic[idx]), "statistic_name": stat_name,
                "p": float(raw[idx]), "p_adjusted": p_adj if not fdr else np.nan, "q": p_adj if fdr else np.nan,
                "ci_low": diff - half[idx], "ci_high": diff + half[idx],
                "significant": bool(significant[idx]), "summary": summary,
            })
    sizes = [len(f.pairs) for f in families]
    return {"rows": rows, "method": method, "name": METHOD_NAMES[method], "fdr": fdr, "n_families": len(families),
            "per_family": sizes, "alpha": alpha, "ci": ci, "warnings": warnings, "kind": est.kind, "goal": plan.goal,
            "references": plan.references(), "family_mode": plan.family}


def comparison_rows(result: AnalysisResult, out: dict, style: str, *, rank: bool = False) -> None:
    """Add the multiple comparisons to a result as a results sheet lays them out."""

    head = result.section(f"Multiple comparisons: {out['name']}")
    alpha, fdr = out["alpha"], out["fdr"]
    head.add("Number of families", out["n_families"])
    sizes = out["per_family"]
    head.add("Number of comparisons per family", sizes[0] if len(set(sizes)) == 1 else tuple(sizes),
             str(sizes[0]) if len(set(sizes)) == 1 else ", ".join(map(str, sizes)))
    head.add("Q (desired FDR)" if fdr else "Alpha (family-wise)", alpha, f"{100 * alpha:g}%" if fdr else fmt(alpha))
    if out["references"]:
        head.add("Reference group(s)", tuple(out["references"]), ", ".join(out["references"]))
    what = "Mean rank diff." if rank else "Mean Diff."
    for row in out["rows"]:
        parts = [f"{what} {fmt(row['difference'])}"]
        if np.isfinite(row["ci_low"]):
            parts.append(f"{100 * out['ci']:g}% CI {fmt_ci(row['ci_low'], row['ci_high'])}")
        if fdr:
            parts.append(f"q = {format_p(row['q'], style=style)}; Discovery? {'Yes' if row['significant'] else 'No'}")
        else:
            if np.isfinite(row["p_adjusted"]):
                parts.append(f"{'P' if out['method'] in ('fisher_lsd', 'dunn_uncorrected') else 'Adjusted P'} = {format_p(row['p_adjusted'], style=style)}")
            parts.append(f"Significant? {'Yes' if row['significant'] else 'No'} ({row['summary']})")
        label = row["label"] + (f" [ref: {row['reference']}]" if out["n_families"] > 1 or (out["family_mode"] == "combined" and row["reference"]) else "")
        head.add(label, row, "; ".join(parts))
    for text in out["warnings"]:
        if text not in result.warnings:
            result.warnings.append(text)
    if out["method"] == "newman_keuls":
        result.warnings.append("Newman-Keuls does not control the family-wise error rate and gives no adjusted P values or CIs; Tukey's test is preferred.")
    result.values["comparisons"] = out["rows"]
    result.values["comparison_method"] = out["method"]
    result.values["family_alpha"] = alpha
    result.values["n_families"] = out["n_families"]
    result.values["comparisons_per_family"] = sizes


# ---------------------------------------------------------------------------------------------
# Compact letter display
# ---------------------------------------------------------------------------------------------


LETTER_STYLES = ("upper", "lower", "numbers")


def compact_letter_display(comparisons, groups: Sequence[str] | None = None, *, style: str = "upper",
                           alpha: float | None = None) -> dict[str, str]:
    """Letters for each group such that two groups share a letter exactly when they are NOT
    significantly different, i.e. their adjusted P ≥ alpha (insert-absorb algorithm, Piepho 2004).
    Every group gets at least one letter.

    ``comparisons`` is a result with ``values["comparisons"]``, or a list of rows with
    ``group_1``, ``group_2`` and ``significant``. With ``alpha``, significance is recomputed as
    adjusted P < alpha (q ≤ alpha for FDR rows). Pairs that were not compared count as not
    different. ``style``: "upper" (A, B, C; the default), "lower" (a, b, c) or "numbers"
    (1, 2, 3, joined with commas). Letters are given in order of the groups (``groups``, or first
    appearance); the ordering rule is this engine's choice."""

    if style not in LETTER_STYLES:
        raise StatisticsError(f"style must be one of {', '.join(LETTER_STYLES)}.")

    rows = comparisons.values.get("comparisons") if isinstance(comparisons, AnalysisResult) else comparisons
    if not rows:
        raise StatisticsError("There are no comparisons to summarise with letters.")
    if groups is None:
        groups = []
        for row in rows:
            for name in (row["group_1"], row["group_2"]):
                if name not in groups:
                    groups.append(name)
    groups = list(groups)
    index = {g: i for i, g in enumerate(groups)}
    different = set()
    for row in rows:
        if alpha is not None:
            q = row.get("q", np.nan)
            significant = bool(q <= alpha) if q is not None and np.isfinite(q) else bool(row.get("p_adjusted", np.nan) < alpha)
        else:
            significant = bool(row["significant"])
        if significant:
            different.add(frozenset((index[row["group_1"]], index[row["group_2"]])))
    columns: list[set[int]] = [set(range(len(groups)))]
    for pair in sorted(different, key=lambda s: sorted(s)):
        i, j = sorted(pair)
        updated: list[set[int]] = []
        for column in columns:
            if i in column and j in column:
                updated += [column - {i}, column - {j}]
            else:
                updated.append(column)
        # absorb: drop columns contained in another column
        unique: list[set[int]] = []
        for column in updated:
            if column and column not in unique:
                unique.append(column)
        columns = [c for c in unique if not any(c < other for other in unique)]
    columns.sort(key=lambda c: (min(c), sorted(c)))
    letters = _letters(len(columns), style)
    joiner = "," if style == "numbers" else ""
    return {g: joiner.join(letters[n] for n, column in enumerate(columns) if index[g] in column) for g in groups}


def _letters(count: int, style: str = "upper") -> list[str]:
    if style == "numbers":
        return [str(n + 1) for n in range(count)]
    alphabet = "abcdefghijklmnopqrstuvwxyz" if style == "lower" else "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return [alphabet[n] if n < 26 else alphabet[n // 26 - 1] + alphabet[n % 26] for n in range(count)]
