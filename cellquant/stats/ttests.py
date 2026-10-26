"""Comparing two columns: t tests and their nonparametric counterparts.

Every test compares the second column (B) with the first (A), so differences are B - A and
ratios B/A, as in a commercial package's results. ``alternative`` is "two-sided", "greater" (B > A predicted)
or "less" (B < A predicted). A one-tailed P is computed in the predicted direction, so it is half
the two-tailed P when the data go that way and 1 - P/2 when they go the other way. Confidence
intervals are always two-sided, at level ``ci``.

Blank cells (NaN) are left out: per column for unpaired tests, and whole rows for paired tests
(a row needs both values).

Effect sizes (unpaired), with n = n_A + n_B and s_p the pooled SD:

- Cohen's d = (x̄_B - x̄_A) / s_p, s_p² = ((n_A-1)s_A² + (n_B-1)s_B²) / (n - 2)
- Hedges' g = J · d, with the exact small-sample correction J = Γ(ν/2) / (√(ν/2) · Γ((ν-1)/2)), ν = n - 2
  (J ≈ 1 - 3/(4n - 9))
- Glass's Δ = (x̄_B - x̄_A) / s_A, scaled by the first column's SD (A as the control)

Paired: Cohen's d_z = mean(d) / SD(d); Hedges' g_z = J(n-1) · d_z; Glass's Δ = mean(d) / s_A.
"""

from __future__ import annotations

import warnings

import numpy as np
from scipy import special, stats

from cellquant.errors import StatisticsError
from cellquant.stats._common import check_options, finite, p_rows, pairs, t_critical, t_p, tails_text
from cellquant.stats._rank import hodges_lehmann_two_sample, normal_p, rank_sum_exact_allowed, rank_sum_exact_p, tie_sum
from cellquant.stats.descriptive import signed_rank_rows
from cellquant.stats.pvalues import format_p, p_summary
from cellquant.stats.results import AnalysisResult, Row, Section, fmt, fmt_ci
from cellquant.stats.tables import ColumnTable


def _start(analysis: str, names: tuple[str, str], table_title: str, options: dict) -> AnalysisResult:
    result = AnalysisResult(analysis, table_title, options)
    section = result.section("Table analyzed")
    section.add("Table", table_title)
    section.add("Comparison", f"{names[1]} vs. {names[0]}")
    return result


def _methods(result: AnalysisResult, what: str, alternative: str, names: tuple[str, str], alpha: float) -> None:
    result.methods = f"{what} comparing {names[1]} with {names[0]} ({tails_text(alternative).lower()} P, alpha = {alpha:g})."


def _blanks(result: AnalysisResult, dropped: int, paired: bool = False) -> None:
    if dropped:
        what = "row(s) with a blank in either column" if paired else "blank cell(s)"
        result.warnings.append(f"{dropped} {what} left out.")


def hedges_j(df: float) -> float:
    """Exact small-sample correction factor for Hedges' g."""

    if not df > 1:
        return np.nan
    return float(np.exp(special.gammaln(df / 2) - 0.5 * np.log(df / 2) - special.gammaln((df - 1) / 2)))


def effect_sizes(a, b) -> dict[str, float]:
    """Cohen's d, Hedges' g and Glass's Δ for B - A (formulas in the module docstring)."""

    a, _ = finite(a)
    b, _ = finite(b)
    na, nb = a.size, b.size
    if na < 2 or nb < 2:
        return {"cohen_d": np.nan, "hedges_g": np.nan, "glass_delta": np.nan}
    va, vb = np.var(a, ddof=1), np.var(b, ddof=1)
    pooled = np.sqrt(((na - 1) * va + (nb - 1) * vb) / (na + nb - 2))
    difference = b.mean() - a.mean()
    d = difference / pooled if pooled > 0 else np.nan
    return {"cohen_d": float(d), "hedges_g": float(d * hedges_j(na + nb - 2)), "glass_delta": float(difference / np.sqrt(va)) if va > 0 else np.nan}


def paired_effect_sizes(a, b) -> dict[str, float]:
    """Cohen's d_z, Hedges' g_z and Glass's Δ for paired B - A (complete rows only)."""

    a, b, _ = pairs(a, b, ("A", "B"))
    d = b - a
    n = d.size
    if n < 2:
        return {"cohen_dz": np.nan, "hedges_gz": np.nan, "glass_delta": np.nan}
    sd = np.std(d, ddof=1)
    sa = np.std(a, ddof=1)
    dz = d.mean() / sd if sd > 0 else np.nan
    return {"cohen_dz": float(dz), "hedges_gz": float(dz * hedges_j(n - 1)), "glass_delta": float(d.mean() / sa) if sa > 0 else np.nan}


def _unpaired_core(a: np.ndarray, b: np.ndarray, welch: bool, ci: float) -> dict[str, float]:
    na, nb = a.size, b.size
    va = np.var(a, ddof=1) if na > 1 else np.nan
    vb = np.var(b, ddof=1) if nb > 1 else np.nan
    if welch:
        sa2, sb2 = va / na, vb / nb
        se = np.sqrt(sa2 + sb2)
        df = (sa2 + sb2) ** 2 / (sa2**2 / (na - 1) + sb2**2 / (nb - 1)) if se > 0 else np.nan
    else:
        df = na + nb - 2
        pooled = (np.nan_to_num((na - 1) * va) + np.nan_to_num((nb - 1) * vb)) / df
        se = np.sqrt(pooled * (1 / na + 1 / nb))
    difference = float(b.mean() - a.mean())
    t = difference / se if se > 0 else np.nan
    half = t_critical(ci, df) * se if np.isfinite(df) else np.nan
    r2 = t * t / (t * t + df) if np.isfinite(t) else np.nan
    return {"n_a": na, "n_b": nb, "mean_a": float(a.mean()), "mean_b": float(b.mean()), "sd_a": float(np.sqrt(va)), "sd_b": float(np.sqrt(vb)),
            "difference": difference, "se": float(se), "df": float(df), "t": float(t), "ci_low": float(difference - half), "ci_high": float(difference + half), "r_squared": float(r2)}


def _check_unpaired(a: np.ndarray, b: np.ndarray, welch: bool, what: str) -> None:
    if a.size < 1 or b.size < 1:
        raise StatisticsError(f"The {what} needs values in both columns.")
    if welch and (a.size < 2 or b.size < 2):
        raise StatisticsError(f"The {what} needs at least 2 values in each column.")
    if a.size + b.size < 3:
        raise StatisticsError(f"The {what} needs at least 3 values in all.")


def _variance_f_test(result: AnalysisResult, a: np.ndarray, b: np.ndarray, alpha: float, style: str, names: tuple[str, str]) -> None:
    """F = larger variance / smaller variance (always ≥ 1, as a commercial package); two-tailed P = 2·P(F' > F), at most 1.
    """

    section = result.section("F test to compare variances")
    if a.size < 2 or b.size < 2:
        section.add("F, DFn, Dfd", None, "needs at least 2 values in each column")
        return
    va, vb = np.var(a, ddof=1), np.var(b, ddof=1)
    if not (va > 0 and vb > 0):
        section.add("F, DFn, Dfd", None, "undefined (a column has zero variance)")
        result.warnings.append("A column has zero variance, so the F test to compare variances was not run.")
        return
    if vb >= va:
        f, dfn, dfd = vb / va, b.size - 1, a.size - 1
    else:
        f, dfn, dfd = va / vb, a.size - 1, b.size - 1
    p = float(min(1.0, 2.0 * stats.f.sf(f, dfn, dfd)))
    section.add("F, DFn, Dfd", (f, dfn, dfd), f"{fmt(f)}, {dfn}, {dfd}")
    p_rows(section, p, alpha, style, label="Significantly different")
    result.values.update(f=float(f), f_dfn=dfn, f_dfd=dfd, f_p=p)
    if p < alpha:
        result.warnings.append(f"The variances of {names[0]} and {names[1]} differ (F test P = {format_p(p, style=style)}); consider Welch's t test.")


def unpaired_t(a, b, *, welch: bool = False, names: tuple[str, str] = ("A", "B"), alternative: str = "two-sided", ci: float = 0.95,
               alpha: float = 0.05, style: str = "GP", table_title: str = "") -> AnalysisResult:
    """Unpaired t test.

    Pooled (Student): s_p² as above, SE = s_p·√(1/n_A + 1/n_B), df = n_A + n_B - 2.
    Welch: SE = √(s_A²/n_A + s_B²/n_B), df by Welch-Satterthwaite
    (s_A²/n_A + s_B²/n_B)² / ((s_A²/n_A)²/(n_A-1) + (s_B²/n_B)²/(n_B-1)).
    t = (x̄_B - x̄_A)/SE; CI = difference ± t(1-α/2, df)·SE; R² (eta squared) = t²/(t² + df).
    Also reports an F test comparing the two variances, and the effect sizes."""

    alternative = check_options(alternative, ci, alpha)
    a, dropped_a = finite(a)
    b, dropped_b = finite(b)
    name = "Welch's t test" if welch else "Unpaired t test"
    _check_unpaired(a, b, welch, name)
    result = _start(name, names, table_title, {"welch": welch, "alternative": alternative, "ci": ci, "alpha": alpha, "style": style})
    core = _unpaired_core(a, b, welch, ci)
    if not np.isfinite(core["t"]):
        result.warnings.append("Both columns have zero variance, so t and P are undefined.")
    p = t_p(core["t"], core["df"], alternative)
    section = result.section(name)
    p_rows(section, p, alpha, style, alternative, names)
    section.add("t, df", (core["t"], core["df"]), f"t={fmt(core['t'])}, df={fmt(core['df'])}")
    _difference_rows(result, core, names, ci, "Difference between means")
    sizes = effect_sizes(a, b)
    effect = result.section("Effect size")
    effect.add("Cohen's d", sizes["cohen_d"])
    effect.add("Hedges' g", sizes["hedges_g"])
    effect.add(f"Glass's delta (SD of {names[0]})", sizes["glass_delta"])
    result.values.update(core, p=p, **sizes)
    if not welch:
        _variance_f_test(result, a, b, alpha, style, names)
    data = result.section("Data analyzed")
    data.add(f"Sample size, {names[0]}", a.size)
    data.add(f"Sample size, {names[1]}", b.size)
    _blanks(result, dropped_a + dropped_b)
    _methods(result, "Welch's unpaired t test (unequal variances)" if welch else "Unpaired t test (pooled variance)", alternative, names, alpha)
    return result


def welch_t(a, b, **options) -> AnalysisResult:
    """Unpaired t test without assuming equal variances (``unpaired_t(..., welch=True)``)."""

    return unpaired_t(a, b, welch=True, **options)


def _difference_rows(result: AnalysisResult, core: dict, names: tuple[str, str], ci: float, label: str) -> Section:
    size = result.section("How big is the difference?")
    size.add(f"Mean of {names[0]}", core["mean_a"])
    size.add(f"Mean of {names[1]}", core["mean_b"])
    size.add(f"{label} ({names[1]} - {names[0]}) ± SEM", (core["difference"], core["se"]), f"{fmt(core['difference'])} ± {fmt(core['se'])}")
    size.add(f"{100 * ci:g}% confidence interval", (core["ci_low"], core["ci_high"]), fmt_ci(core["ci_low"], core["ci_high"]))
    size.add("R squared (eta squared)", core["r_squared"])
    return size


def lognormal_unpaired_t(a, b, *, welch: bool = False, names: tuple[str, str] = ("A", "B"), alternative: str = "two-sided", ci: float = 0.95,
                         alpha: float = 0.05, style: str = "GP", table_title: str = "") -> AnalysisResult:
    """Unpaired t test on log values, for lognormal data. Reports the geometric means and the ratio
    of geometric means B/A = 10^(mean log10 B - mean log10 A) with its CI (the antilog of the CI of
    the difference of logs). Needs every value > 0."""

    alternative = check_options(alternative, ci, alpha)
    a, dropped_a = finite(a)
    b, dropped_b = finite(b)
    if np.any(a <= 0) or np.any(b <= 0):
        raise StatisticsError("A t test on log values needs every value to be greater than 0.")
    name = "Unpaired t test on log values" + (" (Welch)" if welch else "")
    _check_unpaired(a, b, welch, name)
    result = _start(name, names, table_title, {"welch": welch, "alternative": alternative, "ci": ci, "alpha": alpha, "style": style})
    la, lb = np.log10(a), np.log10(b)
    core = _unpaired_core(la, lb, welch, ci)
    if not np.isfinite(core["t"]):
        result.warnings.append("Both columns have zero variance on the log scale, so t and P are undefined.")
    p = t_p(core["t"], core["df"], alternative)
    section = result.section(name)
    p_rows(section, p, alpha, style, alternative, names)
    section.add("t, df", (core["t"], core["df"]), f"t={fmt(core['t'])}, df={fmt(core['df'])}")
    size = result.section("How big is the difference?")
    ratio, low, high = 10 ** core["difference"], 10 ** core["ci_low"], 10 ** core["ci_high"]
    size.add(f"Geometric mean of {names[0]}", 10 ** core["mean_a"])
    size.add(f"Geometric mean of {names[1]}", 10 ** core["mean_b"])
    size.add(f"Difference of log10 means ({names[1]} - {names[0]})", core["difference"])
    size.add(f"Ratio of geometric means ({names[1]}/{names[0]})", ratio)
    size.add(f"{100 * ci:g}% CI of ratio", (low, high), fmt_ci(low, high))
    size.add("R squared (eta squared)", core["r_squared"])
    if not welch:
        _variance_f_test(result, la, lb, alpha, style, names)
    data = result.section("Data analyzed")
    data.add(f"Sample size, {names[0]}", a.size)
    data.add(f"Sample size, {names[1]}", b.size)
    result.values.update({f"log_{key}": value for key, value in core.items()}, n_a=a.size, n_b=b.size, t=core["t"], df=core["df"], p=p,
                         geomean_a=10 ** core["mean_a"], geomean_b=10 ** core["mean_b"], ratio=ratio, ratio_ci_low=low, ratio_ci_high=high)
    _blanks(result, dropped_a + dropped_b)
    _methods(result, f"{'Welch' if welch else 'Unpaired'} t test on log10-transformed values", alternative, names, alpha)
    return result


def _paired_core(a: np.ndarray, b: np.ndarray, ci: float) -> dict[str, float]:
    d = b - a
    n = d.size
    sd = float(np.std(d, ddof=1))
    se = sd / np.sqrt(n)
    mean = float(d.mean())
    t = mean / se if se > 0 else np.nan
    df = n - 1
    half = t_critical(ci, df) * se
    r2 = t * t / (t * t + df) if np.isfinite(t) else np.nan
    return {"n": n, "mean_a": float(a.mean()), "mean_b": float(b.mean()), "difference": mean, "sd_difference": sd, "se": se, "df": df, "t": float(t),
            "ci_low": float(mean - half), "ci_high": float(mean + half), "r_squared": float(r2)}


def _pairing_rows(result: AnalysisResult, a: np.ndarray, b: np.ndarray, alpha: float, style: str, spearman: bool = False) -> None:
    """Was the pairing effective? r between the columns and a one-tailed P for r > 0
    (t = r√(n-2)/√(1-r²), df = n - 2; Spearman's rs uses the same t approximation)."""

    section = result.section("How effective was the pairing?")
    label = "Spearman correlation (rs)" if spearman else "Correlation coefficient (r)"
    if a.size < 3 or np.all(a == a[0]) or np.all(b == b[0]):
        section.add(label, None, "needs at least 3 pairs and some spread in each column")
        return
    if spearman:
        r = float(stats.spearmanr(a, b).statistic)
    else:
        r = float(np.corrcoef(a, b)[0, 1])
    df = a.size - 2
    if r >= 1:
        p = 0.0
    else:
        p = float(stats.t.sf(r * np.sqrt(df) / np.sqrt(1 - r * r), df))
    section.add(label, r)
    section.add("P value (one tailed)", p, format_p(p, style=style))
    section.add("P value summary", p_summary(p, style), p_summary(p, style))
    section.add(f"Was the pairing significantly effective (P < {alpha:g})?", bool(p < alpha))
    result.values.update(pairing_r=r, pairing_p=p, pairing_effective=bool(p < alpha))


def paired_t(a, b, *, names: tuple[str, str] = ("A", "B"), alternative: str = "two-sided", ci: float = 0.95, alpha: float = 0.05,
             style: str = "GP", table_title: str = "") -> AnalysisResult:
    """Paired t test on the differences d = B - A: t = mean(d) / (SD(d)/√n), df = n - 1 pairs.
    R² (partial eta squared) = t²/(t² + df). Also reports the Pearson correlation between the
    columns, whether pairing was effective, and the paired effect sizes."""

    alternative = check_options(alternative, ci, alpha)
    a, b, dropped = pairs(a, b, names)
    if a.size < 2:
        raise StatisticsError(f"A paired t test needs at least 2 complete pairs (there are {a.size}).")
    result = _start("Paired t test", names, table_title, {"alternative": alternative, "ci": ci, "alpha": alpha, "style": style})
    core = _paired_core(a, b, ci)
    if not np.isfinite(core["t"]):
        result.warnings.append("Every difference is the same (SD of differences = 0), so t and P are undefined.")
    p = t_p(core["t"], core["df"], alternative)
    section = result.section("Paired t test")
    p_rows(section, p, alpha, style, alternative, names)
    section.add("t, df", (core["t"], core["df"]), f"t={fmt(core['t'])}, df={core['df']}")
    section.add("Number of pairs", core["n"])
    size = result.section("How big is the difference?")
    size.add(f"Mean difference ({names[1]} - {names[0]})", core["difference"])
    size.add("SD of differences", core["sd_difference"])
    size.add("SEM of differences", core["se"])
    size.add(f"{100 * ci:g}% confidence interval", (core["ci_low"], core["ci_high"]), fmt_ci(core["ci_low"], core["ci_high"]))
    size.add("R squared (partial eta squared)", core["r_squared"])
    sizes = paired_effect_sizes(a, b)
    effect = result.section("Effect size")
    effect.add("Cohen's d_z", sizes["cohen_dz"])
    effect.add("Hedges' g_z", sizes["hedges_gz"])
    effect.add(f"Glass's delta (SD of {names[0]})", sizes["glass_delta"])
    result.values.update(core, p=p, **sizes)
    _pairing_rows(result, a, b, alpha, style)
    _blanks(result, dropped, paired=True)
    _methods(result, "Paired t test", alternative, names, alpha)
    return result


def ratio_paired_t(a, b, *, names: tuple[str, str] = ("A", "B"), alternative: str = "two-sided", ci: float = 0.95, alpha: float = 0.05,
                   style: str = "GP", table_title: str = "") -> AnalysisResult:
    """Ratio paired t test: a paired t test on log10(B) - log10(A) = log10(B/A). Reports the
    geometric mean of the ratios B/A = 10^mean(log10 ratio) and its CI. Needs every value > 0."""

    alternative = check_options(alternative, ci, alpha)
    a, b, dropped = pairs(a, b, names)
    if np.any(a <= 0) or np.any(b <= 0):
        raise StatisticsError("A ratio paired t test needs every value to be greater than 0.")
    if a.size < 2:
        raise StatisticsError(f"A ratio paired t test needs at least 2 complete pairs (there are {a.size}).")
    result = _start("Ratio paired t test", names, table_title, {"alternative": alternative, "ci": ci, "alpha": alpha, "style": style})
    la, lb = np.log10(a), np.log10(b)
    core = _paired_core(la, lb, ci)
    if not np.isfinite(core["t"]):
        result.warnings.append("Every ratio is the same, so t and P are undefined.")
    p = t_p(core["t"], core["df"], alternative)
    section = result.section("Ratio paired t test")
    p_rows(section, p, alpha, style, alternative, names)
    section.add("t, df", (core["t"], core["df"]), f"t={fmt(core['t'])}, df={core['df']}")
    section.add("Number of pairs", core["n"])
    ratio, low, high = 10 ** core["difference"], 10 ** core["ci_low"], 10 ** core["ci_high"]
    size = result.section("How big is the difference?")
    size.add(f"Mean of differences, log10({names[1]}) - log10({names[0]})", core["difference"])
    size.add(f"{100 * ci:g}% CI of the mean log difference", (core["ci_low"], core["ci_high"]), fmt_ci(core["ci_low"], core["ci_high"]))
    size.add(f"Geometric mean of ratios ({names[1]}/{names[0]})", ratio)
    size.add(f"{100 * ci:g}% CI of the geometric mean ratio", (low, high), fmt_ci(low, high))
    size.add("R squared (partial eta squared)", core["r_squared"])
    result.values.update({f"log_{key}": value for key, value in core.items()}, n=core["n"], t=core["t"], df=core["df"], p=p,
                         ratio=ratio, ratio_ci_low=low, ratio_ci_high=high)
    _pairing_rows(result, la, lb, alpha, style)
    _blanks(result, dropped, paired=True)
    _methods(result, "Ratio paired t test (paired t test on log10 ratios)", alternative, names, alpha)
    return result


def mann_whitney(a, b, *, names: tuple[str, str] = ("A", "B"), alternative: str = "two-sided", ci: float = 0.95, alpha: float = 0.05,
                 style: str = "GP", exact: bool | None = None, continuity: bool = False, table_title: str = "") -> AnalysisResult:
    """Mann-Whitney U test (Wilcoxon rank-sum).

    All values are ranked together (ties get the average rank). U_B = R_B - n_B(n_B+1)/2 counts
    pairs with the B value larger (a tie counts 1/2); U_A = n_A·n_B - U_B; the reported U is the
    smaller. P is exact, ties included, when the smaller column has at most 100 values (a commercial package's
    rule) and n_A·n_B ≤ 20 000 (this engine's limit on run time); otherwise it comes from the
    Gaussian approximation with σ² = n_A n_B/12 · ((N+1) - Σ(t³-t)/(N(N-1))) and no continuity
    correction (``continuity=True`` adds R's 0.5). The Hodges-Lehmann difference is the median of
    all B_j - A_i; its CI is at the achievable level nearest to ``ci``, which is reported.
    """

    alternative = check_options(alternative, ci, alpha)
    a, dropped_a = finite(a)
    b, dropped_b = finite(b)
    if a.size < 1 or b.size < 1:
        raise StatisticsError("The Mann-Whitney test needs values in both columns.")
    m, n = a.size, b.size
    result = _start("Mann-Whitney test", names, table_title, {"alternative": alternative, "ci": ci, "alpha": alpha, "style": style, "exact": exact, "continuity": continuity})
    pooled = np.concatenate([a, b])
    ranks = stats.rankdata(pooled)
    rank_a, rank_b = float(ranks[:m].sum()), float(ranks[m:].sum())
    u_b = rank_b - n * (n + 1) / 2.0
    u_a = m * n - u_b
    use_exact = rank_sum_exact_allowed(m, n) if exact is None else bool(exact)
    min_p = np.nan
    if use_exact:
        (p, min_p), z = rank_sum_exact_p(a, b, u_b, alternative), np.nan
        _minimum_p_warning(result, min_p, alpha, style, f"{m} and {n} values")
    else:
        big = m + n
        sd = np.sqrt(m * n / 12.0 * ((big + 1) - tie_sum(pooled) / (big * (big - 1))))
        z, p = normal_p(u_b, m * n / 2.0, sd, alternative, continuity)
        if not sd > 0:
            result.warnings.append("Every value is the same, so P is undefined.")
    shift = hodges_lehmann_two_sample(a, b, ci, exact=use_exact)
    section = result.section("Mann Whitney test")
    p_rows(section, p, alpha, style, alternative, names)
    section.add("Exact or approximate P value?", use_exact, "Exact" if use_exact else "Approximate (Gaussian, tie-corrected)")
    section.add(f"Sum of ranks in {names[0]}, {names[1]}", (rank_a, rank_b), f"{fmt(rank_a)} , {fmt(rank_b)}")
    section.add("Mann-Whitney U", min(u_a, u_b))
    size = result.section("Difference between medians")
    median_a, median_b = float(np.median(a)), float(np.median(b))
    size.add(f"Median of {names[0]}", median_a)
    size.add(f"Median of {names[1]}", median_b)
    size.add("Difference: Actual", median_b - median_a)
    size.add("Difference: Hodges-Lehmann", shift.estimate)
    size.add(f"{100 * shift.confidence:.2f}% CI of difference", (shift.low, shift.high), fmt_ci(shift.low, shift.high))
    data = result.section("Data analyzed")
    data.add(f"Sample size, {names[0]}", m)
    data.add(f"Sample size, {names[1]}", n)
    if abs(shift.confidence - ci) > 0.005:
        result.warnings.append(f"The nearest confidence level these values allow is {100 * shift.confidence:.2f}%, not {100 * ci:g}%.")
    result.values.update(n_a=m, n_b=n, rank_sum_a=rank_a, rank_sum_b=rank_b, u=min(u_a, u_b), u_a=u_a, u_b=u_b, p=p, min_p=min_p, z=z, exact=use_exact,
                         median_a=median_a, median_b=median_b, difference=median_b - median_a, hodges_lehmann=shift.estimate,
                         ci_low=shift.low, ci_high=shift.high, ci_achieved=shift.confidence)
    _blanks(result, dropped_a + dropped_b)
    _methods(result, f"Mann-Whitney test ({'exact' if use_exact else 'normal approximation'})", alternative, names, alpha)
    return result


def _minimum_p_warning(result: AnalysisResult, min_p: float, alpha: float, style: str, what: str) -> None:
    if np.isfinite(min_p) and min_p > alpha:
        result.warnings.append(f"With {what}, the smallest P this test can give is {format_p(min_p, style=style)}, "
                               f"which is above alpha = {alpha:g}: it cannot find a significant difference whatever the data.")


def kolmogorov_smirnov(a, b, *, names: tuple[str, str] = ("A", "B"), alternative: str = "two-sided", alpha: float = 0.05, style: str = "GP",
                       exact: bool | None = None, table_title: str = "") -> AnalysisResult:
    """Two-sample Kolmogorov-Smirnov test: D = the largest vertical distance between the two
    cumulative distributions. One-tailed "greater" (B larger) uses D⁺ = max(F_A - F_B). P is exact
    (scipy) unless the samples are large (n_A·n_B ≥ 10 000) or ``exact=False``. With ties the
    exact P assumes continuous data and is conservative."""

    alternative = check_options(alternative, 0.95, alpha)
    a, dropped_a = finite(a)
    b, dropped_b = finite(b)
    if a.size < 1 or b.size < 1:
        raise StatisticsError("The Kolmogorov-Smirnov test needs values in both columns.")
    result = _start("Kolmogorov-Smirnov test", names, table_title, {"alternative": alternative, "alpha": alpha, "style": style, "exact": exact})
    use_exact = (a.size * b.size < 10_000) if exact is None else bool(exact)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        test = stats.ks_2samp(a, b, alternative=alternative, method="exact" if use_exact else "asymp")
    if any("exact" in str(item.message).lower() for item in caught):
        use_exact = False
        result.warnings.append("The exact P value could not be computed; the asymptotic P is shown.")
    pooled = np.concatenate([a, b])
    if np.unique(pooled).size < pooled.size:
        result.warnings.append("There are tied values; the Kolmogorov-Smirnov P value assumes continuous data and is conservative with ties.")
    p, d = float(test.pvalue), float(test.statistic)
    section = result.section("Kolmogorov-Smirnov test")
    p_rows(section, p, alpha, style, alternative, names)
    section.add("Exact or approximate P value?", use_exact, "Exact" if use_exact else "Approximate (asymptotic)")
    section.add("Kolmogorov-Smirnov D", d)
    size = result.section("Medians")
    size.add(f"Median of {names[0]}", float(np.median(a)))
    size.add(f"Median of {names[1]}", float(np.median(b)))
    data = result.section("Data analyzed")
    data.add(f"Sample size, {names[0]}", a.size)
    data.add(f"Sample size, {names[1]}", b.size)
    result.values.update(n_a=a.size, n_b=b.size, d=d, p=p, exact=use_exact, median_a=float(np.median(a)), median_b=float(np.median(b)),
                         difference=float(np.median(b) - np.median(a)))
    _blanks(result, dropped_a + dropped_b)
    _methods(result, "Two-sample Kolmogorov-Smirnov test", alternative, names, alpha)
    return result


def wilcoxon_matched_pairs(a, b, *, names: tuple[str, str] = ("A", "B"), alternative: str = "two-sided", ci: float = 0.95, alpha: float = 0.05,
                           style: str = "GP", zero_method: str = "exclude", exact: bool | None = None, continuity: bool = False,
                           table_title: str = "") -> AnalysisResult:
    """Wilcoxon matched-pairs signed-rank test on d = B - A.

    ``zero_method``: "exclude" (the default) drops rows with d = 0 before ranking, Wilcoxon's
    original method; "pratt" ranks them and then leaves their ranks out of the sums (Pratt 1959).
    Pratt is an option, from a commercial package 6 on.
    
    W = sum of signed ranks. P is exact, ties included, for fewer than 200 pairs, otherwise
    Gaussian with tie correction and no continuity correction. Also reports the median of the
    differences, the Hodges-Lehmann pseudo-median with its CI (at the achievable level nearest to
    ``ci``), and Spearman's rs between the columns with a one-tailed P, which tells whether pairing
    was effective, as a commercial package does.
    """

    alternative = check_options(alternative, ci, alpha)
    a, b, dropped = pairs(a, b, names)
    if a.size < 1:
        raise StatisticsError("The Wilcoxon matched-pairs test needs at least one complete pair.")
    result = _start("Wilcoxon matched-pairs signed rank test", names, table_title,
                    {"alternative": alternative, "ci": ci, "alpha": alpha, "style": style, "zero_method": zero_method, "exact": exact, "continuity": continuity})
    section = result.section("Wilcoxon matched-pairs signed rank test")
    d = b - a
    signed_rank_rows(result, section, d, ci, alternative, alpha, style, zero_method, exact, continuity, names)
    section.add("Number of pairs", int(d.size))
    result.section("Median of differences").rows.insert(0, Row(f"Median of differences ({names[1]} - {names[0]})", float(np.median(d)), fmt(float(np.median(d)))))
    result.values.update(median_a=float(np.median(a)), median_b=float(np.median(b)), median_difference=float(np.median(d)))
    _pairing_rows(result, a, b, alpha, style, spearman=True)
    _blanks(result, dropped, paired=True)
    _methods(result, f"Wilcoxon matched-pairs signed-rank test (zeros: {zero_method}, {'exact' if result.values['exact'] else 'normal approximation'})", alternative, names, alpha)
    return result


TESTS = {
    "unpaired_t": unpaired_t,
    "welch_t": welch_t,
    "lognormal_t": lognormal_unpaired_t,
    "paired_t": paired_t,
    "ratio_paired_t": ratio_paired_t,
    "mann_whitney": mann_whitney,
    "kolmogorov_smirnov": kolmogorov_smirnov,
    "wilcoxon": wilcoxon_matched_pairs,
}
PAIRED_TESTS = {"paired_t", "ratio_paired_t", "wilcoxon"}


def compare(table: ColumnTable, test: str, first: int | str = 0, second: int | str = 1, **options) -> AnalysisResult:
    """Run ``test`` (a key of TESTS) on two columns of a table, B = ``second`` vs A = ``first``.
    For a paired test, a shorter column counts as blank at the bottom."""

    if test not in TESTS:
        raise StatisticsError(f"Unknown test {test!r}; choose one of {', '.join(TESTS)}.")
    column_a, column_b = table[first], table[second]
    a, b = column_a.values, column_b.values
    if test in PAIRED_TESTS and a.size != b.size:
        rows = max(a.size, b.size)
        a = np.concatenate([a, np.full(rows - a.size, np.nan)])
        b = np.concatenate([b, np.full(rows - b.size, np.nan)])
    result = TESTS[test](a, b, names=(column_a.name, column_b.name), table_title=table.title, **options)
    if test in PAIRED_TESTS and not table.paired:
        result.warnings.append("The table is not marked as paired; a paired test assumes row i of each column is the same subject.")
    return result
