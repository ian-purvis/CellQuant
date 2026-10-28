"""One-way analyses of a column table with two or more groups, laid out as a results sheet.

- ``ordinary_anova``: ordinary one-way ANOVA (equal SDs assumed), with the Brown-Forsythe and
  Bartlett tests of equal variances, R², and the ANOVA table.
- ``welch_anova``: Welch's ANOVA (W) and Brown-Forsythe ANOVA (F*), each with its own df, for
  when SDs are not assumed equal. Both are reported.
- ``repeated_measures_anova``: one-way repeated-measures ANOVA on matched rows, with or without
  the Geisser-Greenhouse correction (epsilon is always reported). With blanks it fits a
  mixed-effects model instead.
- ``mixed_effects_anova``: a REML mixed model with a random intercept per subject (statsmodels).
- ``kruskal_wallis`` and ``friedman``: the nonparametric counterparts.

Every analysis can be followed by multiple comparisons (``comparisons="all"``, ``"control"`` or
``"selected"``, see ``cellquant.stats.multcomp``) and a test for linear trend (``trend=True``).
Comparisons against one or two reference groups are tagged by reference in
``values["comparisons"]``.
"""

from __future__ import annotations

import itertools
import math
import warnings
from typing import Sequence

import numpy as np
import pandas as pd
from scipy import stats

from cellquant.errors import StatisticsError
from cellquant.stats._common import df_override, p_rows, record_overrides
from cellquant.stats._rank import tie_sum
from cellquant.stats.multcomp import Estimates, comparison_rows, plan_from, run_comparisons
from cellquant.stats.pvalues import format_p, p_summary
from cellquant.stats.results import AnalysisResult, fmt
from cellquant.stats.tables import ColumnTable

TECHNICAL_REPLICATE_N = 30


# ---------------------------------------------------------------------------------------------
# Shared pieces
# ---------------------------------------------------------------------------------------------


def _select(table: ColumnTable, groups: Sequence[str] | None) -> list:
    if not isinstance(table, ColumnTable):
        raise StatisticsError("Pass a ColumnTable (one column per group).")
    columns = [table[g] for g in groups] if groups is not None else list(table.columns)
    if len(columns) < 2:
        raise StatisticsError("A one-way analysis needs at least 2 groups (columns).")
    return columns


def _independent(table: ColumnTable, groups, what: str, min_each: int = 1):
    columns = _select(table, groups)
    names = [c.name for c in columns]
    samples = [c.finite for c in columns]
    dropped = sum(int(c.values.size - c.finite.size) for c in columns)
    empty = [n for n, s in zip(names, samples) if s.size < min_each]
    if empty:
        raise StatisticsError(f"{what} needs at least {min_each} value{'s' if min_each > 1 else ''} in every group; too few in: {', '.join(empty)}.")
    return names, samples, dropped


def _matrix(table: ColumnTable, groups) -> tuple[list[str], np.ndarray]:
    columns = _select(table, groups)
    rows = max(c.values.size for c in columns)
    Y = np.full((rows, len(columns)), np.nan)
    for j, c in enumerate(columns):
        Y[: c.values.size, j] = c.values
    return [c.name for c in columns], Y


def _start(analysis: str, table: ColumnTable, names: list[str], options: dict) -> AnalysisResult:
    result = AnalysisResult(analysis, table.title, options)
    head = result.section("Table analyzed")
    head.add("Table", table.title)
    head.add("Data sets analyzed", tuple(names), ", ".join(names))
    return result


def _sig_label(what: str, alpha: float) -> str:
    return f"{what} (P < {alpha:g})?"


def _group_section(result: AnalysisResult, names, samples) -> None:
    section = result.section("Group summary")
    for name, x in zip(names, samples):
        sd = float(np.std(x, ddof=1)) if x.size > 1 else np.nan
        sem = sd / math.sqrt(x.size) if x.size > 1 else np.nan
        section.add(name, {"n": int(x.size), "mean": float(x.mean()), "sd": sd, "sem": sem},
                    f"n = {x.size}; mean = {fmt(float(x.mean()))}; SD = {fmt(sd)}; SEM = {fmt(sem)}")


def _replicate_note(result: AnalysisResult, sizes) -> None:
    if max(sizes) > TECHNICAL_REPLICATE_N:
        result.warnings.append(
            f"A group has more than {TECHNICAL_REPLICATE_N} values. If these are technical replicates (n, for example many "
            "measurements from each biological replicate), the test treats them as independent and its P value is too small. "
            "Average the technical replicates within each biological replicate (N) and analyze the N values."
        )


def _small_note(result: AnalysisResult, sizes, what: str) -> None:
    if min(sizes) < 3:
        result.warnings.append(f"A group has fewer than 3 values: {what} cannot check its assumptions with so few, and power is low.")


def _blank_note(result: AnalysisResult, dropped: int) -> None:
    if dropped:
        result.warnings.append(f"{dropped} blank cell(s) left out.")


def _check_alpha(alpha: float, ci: float) -> None:
    if not 0 < alpha < 1:
        raise StatisticsError("alpha must be between 0 and 1 (for example 0.05).")
    if not 0 < ci < 1:
        raise StatisticsError("The confidence level must be between 0 and 1 (for example 0.95).")


def _check_sd(sd) -> float:
    try:
        value = float(sd)
    except (TypeError, ValueError):
        raise StatisticsError(f"sd must be a positive number, not {sd!r}.") from None
    if isinstance(sd, (bool, np.bool_)) or not np.isfinite(value) or value <= 0:
        raise StatisticsError(f"sd must be a finite number greater than 0, not {sd!r}.")
    return value


def _sd_change(sd, computed: float, changes: list, comparisons, trend) -> float:
    """Validate an SD override for the multiple comparisons; it needs comparisons (or the trend test, which uses the same error term)."""

    value = _check_sd(sd)
    if comparisons in (None, False) and not trend:
        raise StatisticsError("sd is the SD the multiple comparisons use; ask for multiple comparisons (or the test for trend) to use it.")
    changes.append(("SD for multiple comparisons", fmt(value), fmt(computed)))
    return value


def _m_change(out: dict, changes: list | None) -> None:
    if out["m"] is not None and changes is not None:
        sizes = out["per_family"]
        changes.append(("Number of comparisons (m)", str(int(out["m"])), str(sizes[0]) if len(set(sizes)) == 1 else ", ".join(map(str, sizes))))


def _follow_up(result: AnalysisResult, est: Estimates, comparisons, method, control, pairs, family, trend, alpha, ci, style,
               ss_total: float | None = None, ss_between: float | None = None, rank: bool = False, m: int | None = None,
               changes: list | None = None) -> str:
    """Multiple comparisons and the test for trend; returns text for the methods sentence.
    ``m`` is the number of comparisons for the Bonferroni-type corrections; it is added to ``changes`` when set."""

    text = ""
    if comparisons == "trend":
        comparisons, trend = None, True
    plan = plan_from(comparisons, method, control, pairs, family)
    if m is not None and plan is None:
        raise StatisticsError("The number of comparisons (m) is used by multiple comparisons; ask for comparisons to use it.")
    if plan is not None:
        out = run_comparisons(est, plan, alpha=alpha, ci=ci, style=style, m=m)
        _m_change(out, changes)
        comparison_rows(result, out, style, rank=rank)
        families = "" if out["n_families"] == 1 else f", one family per reference group ({', '.join(out['references'])})"
        level = f"Q = {100 * alpha:g}%" if out["fdr"] else f"family-wise alpha = {alpha:g}"
        text += f" Multiple comparisons: {out['name']} ({level}{families}{'; ' + est.error_label if est.error_label else ''})."
        _dunn_floor(result, est, out, alpha)
    if trend:
        if rank:
            raise StatisticsError("The test for linear trend follows ANOVA, not a nonparametric test.")
        _trend(result, est, alpha, style, ss_total, ss_between)
        text += " Test for linear trend across the groups in table order."
    return text


def _trend(result: AnalysisResult, est: Estimates, alpha: float, style: str, ss_total, ss_between) -> None:
    """Post-test for linear trend: the contrast c_i = n_i (x_i - x̄_w) with x_i = 1..k (column order)
    and x̄_w the n-weighted mean position. Slope = Σ c_i ȳ_i / Σ n_i (x_i - x̄_w)² (the least-squares
    slope of the values on column number); F = (Σ c_i ȳ_i)² / (c'Vc), df (1, error df);
    SS_trend = (Σ c_i ȳ_i)² / Σ n_i (x_i - x̄_w)². Two R² values:
    effect-size R² = SS_trend / SS_total (the fraction of all variation explained by the trend) and
    alerting R² = SS_trend / SS_between-means, with SS_between-means = Σ n_i (ȳ_i - ȳ_w)² (the
    fraction of the variation among the group means explained by the trend; always the larger)."""

    k = len(est.names)
    if k < 3:
        raise StatisticsError("The test for linear trend needs at least 3 groups.")
    if est.kind == "welch":
        raise StatisticsError("The test for linear trend assumes equal SDs (it uses the pooled ANOVA error term).")
    x = np.arange(1, k + 1, dtype=float)
    n = est.n.astype(float)
    centre = float(np.sum(n * x) / n.sum())
    c = n * (x - centre)
    sxx = float(np.sum(n * (x - centre) ** 2))
    contrast = float(c @ est.estimates)
    var = float(c @ est.cov @ c)
    slope = contrast / sxx
    f = contrast**2 / var if var > 0 else np.nan
    p = float(stats.f.sf(f, 1, est.df)) if np.isfinite(f) else np.nan
    ss_trend = contrast**2 / sxx
    section = result.section("Test for linear trend")
    section.add("Slope (change per column)", slope)
    r2 = ss_trend / ss_total if ss_total else np.nan
    weighted = float(np.sum(n * est.estimates) / n.sum())
    ss_means = float(np.sum(n * (est.estimates - weighted) ** 2))
    alerting = ss_trend / ss_means if ss_means > 0 else np.nan
    section.add("R squared (effect size: SS trend / SS total)", r2)
    section.add("R squared (alerting: SS trend / SS between means)", alerting)
    section.add("F, DFn, DFd", (f, 1, est.df), f"{fmt(f)}, 1, {fmt(est.df)}")
    p_rows(section, p, alpha, style, label="Is linear trend significant")
    result.values.update(trend_slope=slope, trend_r_squared=r2, trend_alerting_r_squared=alerting, trend_ss_between_means=ss_means, trend_f=f, trend_df=est.df, trend_p=p, trend_ss=ss_trend)
    if ss_between is not None and k > 2 and est.kind == "pooled":
        ms_error = var / sxx  # c'Vc = MSE · Σ n_i (x_i - x̄)² for a pooled error term
        dev_f = (ss_between - ss_trend) / (k - 2) / ms_error if ms_error > 0 else np.nan
        dev_p = float(stats.f.sf(dev_f, k - 2, est.df)) if np.isfinite(dev_f) else np.nan
        dev = result.section("Nonlinear variation (deviation from linear trend)")
        dev.add("F, DFn, DFd", (dev_f, k - 2, est.df), f"{fmt(dev_f)}, {k - 2}, {fmt(est.df)}")
        p_rows(dev, dev_p, alpha, style, label="Is nonlinear variation significant")
        result.values.update(nonlinear_f=dev_f, nonlinear_p=dev_p)


def _dunn_floor(result: AnalysisResult, est: Estimates, out: dict, alpha: float) -> None:
    """Warn when Dunn's test cannot reach significance for some pairs whatever the data (no ties)."""

    if est.kind != "rank" or out["method"] not in ("dunn", "dunn_uncorrected") or not np.isfinite(est.estimates).all():
        return
    blocked = 0
    for row in out["rows"]:
        best = row["_max_z"] if "_max_z" in row else None
        if best is None:
            continue
        m = out["m"] if out.get("m") is not None else out["per_family"][row["family"] - 1]
        floor = 2 * stats.norm.sf(best) * (m if out["method"] == "dunn" else 1)
        if min(1.0, floor) >= alpha:
            blocked += 1
    if blocked:
        result.warnings.append(f"With these group sizes, {blocked} of {len(out['rows'])} comparison(s) cannot reach P < {alpha:g} by Dunn's test "
                               "whatever the data (even with complete separation of the ranks).")


# ---------------------------------------------------------------------------------------------
# Equal-variance tests
# ---------------------------------------------------------------------------------------------


def brown_forsythe_test(samples) -> dict:
    """Brown-Forsythe test of equal variances: ordinary one-way ANOVA on z = |x - median of its
    group| (Levene's test centred on the median). F with (k - 1, N - k) df."""

    samples = [np.asarray(s, dtype=float) for s in samples]
    if any(s.size < 2 for s in samples):
        return {"f": np.nan, "dfn": np.nan, "dfd": np.nan, "p": np.nan}
    z = [np.abs(s - np.median(s)) for s in samples]
    core = _anova_core(z)
    return {"f": core["f"], "dfn": core["df_between"], "dfd": core["df_within"], "p": core["p"]}


def bartlett_test(samples) -> dict:
    """Bartlett's test of equal variances (corrected statistic):
    K² = [(N - k) ln s_p² - Σ (n_i - 1) ln s_i²] / [1 + (Σ 1/(n_i - 1) - 1/(N - k)) / (3(k - 1))],
    chi-square with k - 1 df."""

    samples = [np.asarray(s, dtype=float) for s in samples]
    if any(s.size < 2 for s in samples):
        return {"statistic": np.nan, "df": np.nan, "p": np.nan}
    v = np.array([np.var(s, ddof=1) for s in samples])
    if np.any(v <= 0):
        return {"statistic": np.nan, "df": np.nan, "p": np.nan}
    n = np.array([s.size for s in samples], dtype=float)
    k, N = len(samples), n.sum()
    sp = np.sum((n - 1) * v) / (N - k)
    num = (N - k) * math.log(sp) - np.sum((n - 1) * np.log(v))
    den = 1 + (np.sum(1 / (n - 1)) - 1 / (N - k)) / (3 * (k - 1))
    stat = float(num / den)
    return {"statistic": stat, "df": k - 1, "p": float(stats.chi2.sf(stat, k - 1))}


# ---------------------------------------------------------------------------------------------
# Ordinary one-way ANOVA
# ---------------------------------------------------------------------------------------------


def _anova_core(samples) -> dict:
    n = np.array([s.size for s in samples], dtype=float)
    means = np.array([s.mean() for s in samples])
    k, N = len(samples), n.sum()
    grand = float(np.concatenate(samples).mean())
    ss_between = float(np.sum(n * (means - grand) ** 2))
    ss_within = float(sum(np.sum((s - s.mean()) ** 2) for s in samples))
    df_b, df_w = k - 1, int(N - k)
    ms_b = ss_between / df_b
    ms_w = ss_within / df_w if df_w > 0 else np.nan
    f = ms_b / ms_w if ms_w > 0 else np.nan
    p = float(stats.f.sf(f, df_b, df_w)) if np.isfinite(f) else np.nan
    ss_total = ss_between + ss_within
    return {"ss_between": ss_between, "ss_within": ss_within, "ss_total": ss_total, "df_between": df_b, "df_within": df_w, "df_total": int(N - 1),
            "ms_between": ms_b, "ms_within": ms_w, "f": f, "p": p, "r_squared": ss_between / ss_total if ss_total > 0 else np.nan,
            "means": means, "n": n, "grand_mean": grand}


def ordinary_anova(table: ColumnTable, *, groups: Sequence[str] | None = None, comparisons=None, method: str | None = None,
                   control=None, pairs=None, family: str = "per_reference", trend: bool = False, alpha: float = 0.05, ci: float = 0.95,
                   style: str = "classic", df=None, sd: float | None = None, m: int | None = None) -> AnalysisResult:
    """Ordinary one-way ANOVA: F = MS_between / MS_within with (k - 1, N - k) df, where
    SS_between = Σ n_i (ȳ_i - ȳ)² and SS_within = Σ Σ (y - ȳ_i)². R² = SS_between / SS_total.
    Also the Brown-Forsythe and Bartlett tests of equal SDs.

    Advanced overrides (keyword-only, recorded in "Changed from the default"): ``df`` replaces the
    error df N - k in the F test's P, the ANOVA table and the multiple comparisons; ``sd`` replaces
    the pooled SD √MS_within in the multiple comparisons (V = sd²·diag(1/n_i); the F test is
    unchanged); ``m`` is the number of comparisons the Bonferroni-type corrections count."""

    _check_alpha(alpha, ci)
    names, samples, dropped = _independent(table, groups, "Ordinary one-way ANOVA")
    core = _anova_core(samples)
    if core["df_within"] < 1:
        raise StatisticsError("Ordinary one-way ANOVA needs more values than groups (some group needs at least 2 values).")
    changes, larger = [], []
    if df is not None:
        df_used = df_override(df, core["df_within"], "Error df", changes, larger)
        core = {**core, "df_within": df_used, "p": float(stats.f.sf(core["f"], core["df_between"], df_used)) if np.isfinite(core["f"]) else np.nan}
    cov_sd = _sd_change(sd, math.sqrt(core["ms_within"]), changes, comparisons, trend) if sd is not None else None
    result = _start("Ordinary one-way ANOVA", table, names, {"alpha": alpha, "ci": ci, "style": style, "comparisons": comparisons, "method": method,
                                                             "control": control, "family": family, "trend": trend})
    summary = result.section("ANOVA summary")
    summary.add("F", core["f"])
    summary.add("P value", core["p"], format_p(core["p"], style=style))
    summary.add("P value summary", p_summary(core["p"], style), p_summary(core["p"], style))
    sig = bool(core["p"] < alpha) if np.isfinite(core["p"]) else None
    summary.add(_sig_label("Significant diff. among means", alpha), sig, "" if sig is None else ("Yes" if sig else "No"))
    summary.add("R squared", core["r_squared"])
    bf = brown_forsythe_test(samples)
    section = result.section("Brown-Forsythe test")
    if np.isfinite(bf["f"]):
        section.add("F (DFn, DFd)", (bf["f"], bf["dfn"], bf["dfd"]), f"{fmt(bf['f'])} ({bf['dfn']}, {bf['dfd']})")
        p_rows(section, bf["p"], alpha, style, label="Are SDs significantly different")
    else:
        section.add("F (DFn, DFd)", None, "needs at least 2 values in every group")
    bart = bartlett_test(samples)
    section = result.section("Bartlett's test")
    if np.isfinite(bart["statistic"]):
        section.add("Bartlett's statistic (corrected)", bart["statistic"])
        p_rows(section, bart["p"], alpha, style, label="Are SDs significantly different")
    else:
        section.add("Bartlett's statistic (corrected)", None, "needs at least 2 values and some spread in every group")
    _anova_table(result, [("Treatment (between columns)", core["ss_between"], core["df_between"], core["ms_between"], core["f"], core["p"]),
                          ("Residual (within columns)", core["ss_within"], core["df_within"], core["ms_within"], None, None),
                          ("Total", core["ss_total"], core["df_total"], None, None, None)], style)
    data = result.section("Data summary")
    data.add("Number of treatments (columns)", len(names))
    data.add("Number of values (total)", int(core["n"].sum()))
    _group_section(result, names, samples)
    sds = np.array([np.std(s, ddof=1) if s.size > 1 else np.nan for s in samples])
    result.values.update(names=names, n=core["n"].astype(int).tolist(), means=core["means"].tolist(), sds=sds.tolist(),
                         f=core["f"], dfn=core["df_between"], dfd=core["df_within"], p=core["p"], r_squared=core["r_squared"],
                         ss_between=core["ss_between"], ss_within=core["ss_within"], ss_total=core["ss_total"], ms_between=core["ms_between"],
                         ms_within=core["ms_within"], bf_f=bf["f"], bf_dfn=bf["dfn"], bf_dfd=bf["dfd"], bf_p=bf["p"],
                         bartlett=bart["statistic"], bartlett_df=bart["df"], bartlett_p=bart["p"])
    if np.isfinite(bf["p"]) and bf["p"] < alpha:
        result.warnings.append(f"The Brown-Forsythe test says the SDs differ (P = {format_p(bf['p'], style=style)}); consider Welch's or Brown-Forsythe ANOVA.")
    _small_note(result, core["n"], "ANOVA")
    _replicate_note(result, core["n"])
    _blank_note(result, dropped)
    est = Estimates(names, core["means"], core["n"], (core["ms_within"] if cov_sd is None else cov_sd**2) * np.diag(1 / core["n"]), core["df_within"], "pooled",
                    error_label="pooled SD from the ANOVA" if cov_sd is None else "SD set by hand")
    more = _follow_up(result, est, comparisons, method, control, pairs, family, trend, alpha, ci, style, core["ss_total"], core["ss_between"], m=m, changes=changes)
    result.methods = f"Ordinary one-way ANOVA ({len(names)} groups, {int(core['n'].sum())} values; alpha = {alpha:g}).{more}"
    record_overrides(result, changes, larger)
    return result


def _anova_table(result: AnalysisResult, rows, style: str) -> None:
    section = result.section("ANOVA table")
    for label, ss, df, ms, f, p in rows:
        parts = [f"SS = {fmt(ss)}", f"DF = {fmt(df)}"]
        if ms is not None:
            parts.append(f"MS = {fmt(ms)}")
        if f is not None:
            parts.append(f"F = {fmt(f)}")
        if p is not None:
            parts.append(f"P = {format_p(p, style=style)}")
        section.add(label, {"ss": ss, "df": df, "ms": ms, "f": f, "p": p}, "; ".join(parts))


# ---------------------------------------------------------------------------------------------
# Welch's and Brown-Forsythe ANOVA
# ---------------------------------------------------------------------------------------------


def welch_anova_core(samples) -> dict:
    """Welch's ANOVA (Welch 1951): w_i = n_i/s_i², ȳ_w = Σ w_i ȳ_i / Σ w_i,
    W = [Σ w_i (ȳ_i - ȳ_w)² / (k - 1)] / [1 + 2(k - 2)/(k² - 1) · Λ], Λ = Σ (1 - w_i/Σw)² / (n_i - 1),
    df = (k - 1, (k² - 1) / (3Λ)).

    Brown-Forsythe ANOVA (Brown and Forsythe 1974): F* = Σ n_i (ȳ_i - ȳ)² / Σ (1 - n_i/N) s_i²,
    df = (k - 1, 1 / Σ (c_i² / (n_i - 1))) with c_i = (1 - n_i/N) s_i² / Σ (1 - n_j/N) s_j²."""

    n = np.array([s.size for s in samples], dtype=float)
    means = np.array([s.mean() for s in samples])
    v = np.array([np.var(s, ddof=1) for s in samples])
    k, N = len(samples), n.sum()
    w = n / v
    wm = np.sum(w * means) / w.sum()
    lam = np.sum((1 - w / w.sum()) ** 2 / (n - 1))
    W = (np.sum(w * (means - wm) ** 2) / (k - 1)) / (1 + 2 * (k - 2) / (k * k - 1) * lam)
    welch_df2 = (k * k - 1) / (3 * lam)
    grand = float(np.concatenate(samples).mean())
    denom = np.sum((1 - n / N) * v)
    fstar = np.sum(n * (means - grand) ** 2) / denom
    c = (1 - n / N) * v / denom
    bf_df2 = 1 / np.sum(c * c / (n - 1))
    return {"welch_f": float(W), "welch_dfn": k - 1, "welch_dfd": float(welch_df2), "welch_p": float(stats.f.sf(W, k - 1, welch_df2)),
            "bf_anova_f": float(fstar), "bf_anova_dfn": k - 1, "bf_anova_dfd": float(bf_df2), "bf_anova_p": float(stats.f.sf(fstar, k - 1, bf_df2)),
            "means": means, "n": n, "sds": np.sqrt(v)}


def welch_anova(table: ColumnTable, *, groups: Sequence[str] | None = None, comparisons=None, method: str | None = None, control=None,
                pairs=None, family: str = "per_reference", alpha: float = 0.05, ci: float = 0.95, style: str = "classic", df=None, m: int | None = None) -> AnalysisResult:
    """Brown-Forsythe and Welch ANOVA tests, for groups whose SDs may differ (both are reported).
    ``values["p"]`` is Welch's P. Multiple comparisons default to Games-Howell (all pairs) or
    Dunnett's T3 (other goals); Tamhane's T2 is also available.

    Advanced overrides: ``df`` replaces the denominator df of Welch's W (Brown-Forsythe F* is
    unchanged); ``m`` is the number of comparisons the Bonferroni-type corrections count."""

    _check_alpha(alpha, ci)
    names, samples, dropped = _independent(table, groups, "Welch's and Brown-Forsythe ANOVA", min_each=2)
    if any(np.var(s, ddof=1) <= 0 for s in samples):
        raise StatisticsError("Welch's and Brown-Forsythe ANOVA need some spread (SD > 0) in every group.")
    core = welch_anova_core(samples)
    changes, larger = [], []
    if df is not None:
        df_used = df_override(df, core["welch_dfd"], "Welch denominator df", changes, larger)
        core = {**core, "welch_dfd": df_used, "welch_p": float(stats.f.sf(core["welch_f"], core["welch_dfn"], df_used))}
    result = _start("Brown-Forsythe and Welch ANOVA", table, names, {"alpha": alpha, "ci": ci, "style": style, "comparisons": comparisons,
                                                                     "method": method, "control": control, "family": family})
    for title, key, symbol in (("Brown-Forsythe ANOVA test", "bf_anova", "F*"), ("Welch's ANOVA test", "welch", "W")):
        section = result.section(title)
        f, dfn, dfd, p = core[f"{key}_f"], core[f"{key}_dfn"], core[f"{key}_dfd"], core[f"{key}_p"]
        section.add(f"{symbol} (DFn, DFd)", (f, dfn, dfd), f"{fmt(f)} ({dfn}, {fmt(dfd)})")
        p_rows(section, p, alpha, style, label="Significant diff. among means")
    data = result.section("Data summary")
    data.add("Number of treatments (columns)", len(names))
    data.add("Number of values (total)", int(core["n"].sum()))
    _group_section(result, names, samples)
    result.values.update({k: v for k, v in core.items() if k not in ("means", "n", "sds")}, names=names, n=core["n"].astype(int).tolist(),
                         means=core["means"].tolist(), sds=core["sds"].tolist(), p=core["welch_p"])
    _small_note(result, core["n"], "this test")
    _replicate_note(result, core["n"])
    _blank_note(result, dropped)
    est = Estimates(names, core["means"], core["n"], None, np.nan, "welch", sd=core["sds"], error_label="each comparison uses its own SDs and Welch's df")
    more = _follow_up(result, est, comparisons, method, control, pairs, family, False, alpha, ci, style, m=m, changes=changes)
    result.methods = f"Brown-Forsythe and Welch one-way ANOVA (equal SDs not assumed; {len(names)} groups; alpha = {alpha:g}).{more}"
    record_overrides(result, changes, larger)
    return result


# ---------------------------------------------------------------------------------------------
# Repeated measures
# ---------------------------------------------------------------------------------------------


def geisser_greenhouse_epsilon(Y: np.ndarray) -> float:
    """Greenhouse-Geisser epsilon from complete rows (subjects × treatments):
    ε = (tr S*)² / ((k - 1) tr(S*²)), where S* = C S C is the double-centred covariance matrix of
    the columns (C = I - J/k). 1/(k - 1) ≤ ε ≤ 1; ε = 1 means sphericity holds in the sample."""

    k = Y.shape[1]
    S = np.cov(Y, rowvar=False, ddof=1)
    C = np.eye(k) - np.ones((k, k)) / k
    Sc = C @ S @ C
    denom = (k - 1) * np.trace(Sc @ Sc)
    return float(np.trace(Sc) ** 2 / denom) if denom > 0 else np.nan


def _check_epsilon(epsilon, k: int) -> float:
    """A fixed sphericity epsilon, between 1/(k - 1) and 1."""

    try:
        value = float(epsilon)
    except (TypeError, ValueError):
        raise StatisticsError(f"epsilon must be a number between 1/(k-1) and 1, not {epsilon!r}.") from None
    low = 1.0 / (k - 1)
    if isinstance(epsilon, (bool, np.bool_)) or not np.isfinite(value) or not low - 1e-12 <= value <= 1.0:
        raise StatisticsError(f"epsilon must be between 1/(k-1) = {low:.4g} and 1 for {k} treatments, not {epsilon!r}.")
    return min(1.0, max(low, value))


def _rm_core(Y: np.ndarray) -> dict:
    n, k = Y.shape
    grand = Y.mean()
    col = Y.mean(axis=0)
    row = Y.mean(axis=1)
    ss_total = float(np.sum((Y - grand) ** 2))
    ss_treat = float(n * np.sum((col - grand) ** 2))
    ss_subj = float(k * np.sum((row - grand) ** 2))
    ss_resid = ss_total - ss_treat - ss_subj
    df_t, df_s, df_r = k - 1, n - 1, (n - 1) * (k - 1)
    ms_t, ms_s = ss_treat / df_t, ss_subj / df_s
    ms_r = ss_resid / df_r
    f = ms_t / ms_r if ms_r > 0 else np.nan
    f_s = ms_s / ms_r if ms_r > 0 else np.nan
    return {"n": n, "k": k, "means": col, "ss_total": ss_total, "ss_treatment": ss_treat, "ss_subjects": ss_subj, "ss_residual": ss_resid,
            "df_treatment": df_t, "df_subjects": df_s, "df_residual": df_r, "ms_treatment": ms_t, "ms_subjects": ms_s, "ms_residual": ms_r,
            "f": f, "f_subjects": f_s, "p_subjects": float(stats.f.sf(f_s, df_s, df_r)) if np.isfinite(f_s) else np.nan}


def repeated_measures_anova(table: ColumnTable, *, groups: Sequence[str] | None = None, geisser_greenhouse: bool = True, missing: str = "mixed",
                            comparisons=None, method: str | None = None, control=None, pairs=None, family: str = "per_reference",
                            rm_error: str | None = None, trend: bool = False, alpha: float = 0.05, ci: float = 0.95, style: str = "classic",
                            epsilon: float | None = None, df=None, sd: float | None = None, m: int | None = None) -> AnalysisResult:
    """Repeated-measures one-way ANOVA. Each row is one subject (matched across columns).

    SS_total = SS_treatment + SS_subjects + SS_residual; F = MS_treatment / MS_residual with
    (k - 1, (n - 1)(k - 1)) df. Geisser-Greenhouse epsilon (see ``geisser_greenhouse_epsilon``) is
    always reported; with ``geisser_greenhouse=True`` (the usual recommendation) both df are
    multiplied by ε. "Was the matching effective?": F = MS_subjects / MS_residual.
    Treatment R² = SS_treatment / (SS_treatment + SS_residual) (after Keppel: subjects left
    out; with two columns it equals the paired t test's R²). Matching R² = SS_subjects / SS_total.

    Rows with a blank: ``missing="mixed"`` (default) fits ``mixed_effects_anova``
    instead; ``missing="exclude"`` drops those rows.

    Multiple comparisons use ``rm_error``: "pooled" (MS_residual with (n-1)(k-1) df, which
    assumes sphericity) or "separate" (each comparison uses only its two columns: SE = SD of the
    differences/√n, df = n - 1, essentially a paired t test; Maxwell and Delaney pp. 552-555). The
    default is: pooled when sphericity is assumed, separate with the Geisser-Greenhouse
    correction (which is then applied only to the main P).

    Advanced overrides (not available with blanks, which fit the mixed model): ``epsilon`` is a
    fixed sphericity ε in [1/(k - 1), 1] used instead of the estimated Geisser-Greenhouse ε (it
    implies the correction is applied); ``df`` replaces the error df (n - 1)(k - 1) before ε is
    applied (and the pooled comparisons' df); ``sd`` replaces the pooled SD √MS_residual in the multiple
    comparisons (a pooled error term, V = sd²·I/n, with df as above); ``m`` is the number of comparisons
    the Bonferroni-type corrections count."""

    _check_alpha(alpha, ci)
    if missing not in ("mixed", "exclude"):
        raise StatisticsError('missing must be "mixed" or "exclude".')
    names, Y = _matrix(table, groups)
    present = np.isfinite(Y)
    Y = Y[present.any(axis=1)]
    complete = np.isfinite(Y).all(axis=1)
    if not complete.all() and missing == "mixed":
        for label, value in (("df", df), ("epsilon", epsilon), ("sd", sd)):
            if value is not None:
                raise StatisticsError(f"{label} is not available with blanks (mixed model); fill or remove the rows with a blank (or use missing=\"exclude\"), "
                                      f"or leave {label} at its default.")
        result = mixed_effects_anova(table, groups=groups, geisser_greenhouse=geisser_greenhouse, comparisons=comparisons, method=method,
                                     control=control, pairs=pairs, family=family, trend=trend, alpha=alpha, ci=ci, style=style, m=m)
        result.warnings.insert(0, f"{int((~complete).sum())} row(s) have a blank, so a mixed-effects model was fitted instead of repeated-measures ANOVA.")
        return result
    dropped = int((~complete).sum())
    Y = Y[complete]
    if Y.shape[0] < 2:
        raise StatisticsError(f"Repeated-measures ANOVA needs at least 2 complete rows (subjects); there are {Y.shape[0]}.")
    core = _rm_core(Y)
    n, k = Y.shape
    eps = geisser_greenhouse_epsilon(Y) if k > 2 else 1.0
    scale = eps if geisser_greenhouse and np.isfinite(eps) else 1.0
    changes, larger = [], []
    df_err = core["df_residual"]
    if df is not None:
        df_err = df_override(df, df_err, "Error df (before epsilon)", changes, larger)
    if epsilon is not None:
        eps_set = _check_epsilon(epsilon, k)
        changes.append(("Epsilon", fmt(eps_set), fmt(scale)))
        scale, geisser_greenhouse = eps_set, True
    dfn, dfd = core["df_treatment"] * scale, df_err * scale
    p = float(stats.f.sf(core["f"], dfn, dfd)) if np.isfinite(core["f"]) else np.nan
    r2 = core["ss_treatment"] / (core["ss_treatment"] + core["ss_residual"]) if core["ss_treatment"] + core["ss_residual"] > 0 else np.nan
    r2_matching = core["ss_subjects"] / core["ss_total"] if core["ss_total"] > 0 else np.nan
    result = _start("Repeated measures one-way ANOVA", table, names, {"alpha": alpha, "ci": ci, "style": style, "geisser_greenhouse": geisser_greenhouse,
                                                                      "comparisons": comparisons, "method": method, "control": control, "family": family,
                                                                      "rm_error": rm_error, "trend": trend})
    summary = result.section("Repeated measures ANOVA summary")
    summary.add("Assume sphericity (equal variability of differences)?", not geisser_greenhouse, "No" if geisser_greenhouse else "Yes")
    summary.add("F", core["f"])
    summary.add("P value", p, format_p(p, style=style))
    summary.add("P value summary", p_summary(p, style), p_summary(p, style))
    sig = bool(p < alpha) if np.isfinite(p) else None
    summary.add(_sig_label("Statistically significant", alpha), sig, "" if sig is None else ("Yes" if sig else "No"))
    summary.add("Geisser-Greenhouse's epsilon", eps)
    summary.add("R squared", r2)
    match = result.section("Was the matching effective?")
    match.add("F (DFn, DFd)", (core["f_subjects"], core["df_subjects"], core["df_residual"]), f"{fmt(core['f_subjects'])} ({core['df_subjects']}, {core['df_residual']})")
    p_rows(match, core["p_subjects"], alpha, style, label="Is there significant matching")
    match.add("R squared", r2_matching)
    _anova_table(result, [("Treatment (between columns)", core["ss_treatment"], dfn, core["ms_treatment"], core["f"], p),
                          ("Individual (between rows)", core["ss_subjects"], core["df_subjects"], core["ms_subjects"], core["f_subjects"], core["p_subjects"]),
                          ("Residual (random)", core["ss_residual"], dfd, core["ms_residual"], None, None),
                          ("Total", core["ss_total"], n * k - 1, None, None, None)], style)
    data = result.section("Data summary")
    data.add("Number of treatments (columns)", k)
    data.add("Number of subjects (rows)", n)
    _group_section(result, names, [Y[:, j] for j in range(k)])
    result.values.update(names=names, n_subjects=n, means=core["means"].tolist(), f=core["f"], dfn=dfn, dfd=dfd, p=p, epsilon=eps,
                         dfn_uncorrected=core["df_treatment"], dfd_uncorrected=df_err,
                         p_uncorrected=float(stats.f.sf(core["f"], core["df_treatment"], df_err)) if np.isfinite(core["f"]) else np.nan,
                         p_geisser_greenhouse=float(stats.f.sf(core["f"], core["df_treatment"] * eps, core["df_residual"] * eps)) if np.isfinite(core["f"]) else np.nan,
                         r_squared=r2, r_squared_matching=r2_matching, **{key: core[key] for key in ("ss_treatment", "ss_subjects", "ss_residual", "ss_total", "ms_treatment", "ms_subjects",
                                                                     "ms_residual", "f_subjects", "p_subjects")})
    if not table.paired:
        result.warnings.append("The table is not marked as paired; repeated-measures ANOVA assumes row i of each column is the same subject.")
    if dropped:
        result.warnings.append(f"{dropped} row(s) with a blank left out.")
    if n < 3:
        result.warnings.append("With fewer than 3 subjects, power is very low and sphericity cannot be judged.")
    _replicate_note(result, [n])
    cov_sd = _sd_change(sd, math.sqrt(core["ms_residual"]), changes, comparisons, trend) if sd is not None else None
    if cov_sd is not None and rm_error == "separate":
        raise StatisticsError('sd replaces the pooled error term, so it cannot be combined with rm_error="separate".')
    if cov_sd is not None and rm_error is None and geisser_greenhouse:
        changes.append(("Multiple comparisons error term", "pooled (a set SD replaces it)", "each pair's own differences"))
    rm_error = rm_error or ("separate" if geisser_greenhouse and cov_sd is None else "pooled")
    if rm_error == "pooled":
        est = Estimates(names, core["means"], np.full(k, n), (core["ms_residual"] if cov_sd is None else cov_sd**2) / n * np.eye(k), df_err, "pooled",
                        error_label="pooled repeated-measures error term" if cov_sd is None else "SD set by hand")
    elif rm_error == "separate":
        est = Estimates(names, core["means"], np.full(k, n), np.cov(Y, rowvar=False, ddof=1) / n, n - 1, "pooled",
                        error_label="each comparison uses only the two columns compared (sphericity not assumed)")
    else:
        raise StatisticsError('rm_error must be "pooled" or "separate".')
    more = _follow_up(result, est, comparisons, method, control, pairs, family, trend, alpha, ci, style, core["ss_total"],
                      core["ss_treatment"] if rm_error == "pooled" else None, m=m, changes=changes)
    gg = f"Geisser-Greenhouse corrected (epsilon = {eps:.3f})" if geisser_greenhouse else "sphericity assumed"
    if epsilon is not None:
        gg = f"sphericity correction with epsilon = {scale:.3f} set by hand"
        result.values["epsilon_used"] = scale
    result.methods = f"Repeated-measures one-way ANOVA ({k} treatments, {n} subjects; {gg}; alpha = {alpha:g}).{more}"
    record_overrides(result, changes, larger)
    return result


def _statsmodels_mixedlm():
    try:
        import statsmodels.formula.api as smf
        from statsmodels.regression.mixed_linear_model import MixedLMParams
    except ImportError:
        raise StatisticsError('The mixed-effects model needs statsmodels. Install it with: pip install "cellquant[stats]"') from None
    return smf, MixedLMParams


def _gls_covariance(long: pd.DataFrame, k: int, var_subject: float, var_resid: float) -> np.ndarray:
    """(X' V⁻¹ X)⁻¹ for the cell-means design, with V = σ²_e I + σ²_s J within each subject (the
    usual covariance of the fixed effects, as in lme4)."""

    info = np.zeros((k, k))
    codes = long["group"].cat.codes.to_numpy()
    for _, index in long.groupby("subject").indices.items():
        counts = np.bincount(codes[index], minlength=k).astype(float)
        m = counts.sum()
        shrink = var_subject / (var_resid + m * var_subject)
        info += (np.diag(counts) - shrink * np.outer(counts, counts)) / var_resid
    return np.linalg.inv(info)


def mixed_effects_anova(table: ColumnTable, *, groups: Sequence[str] | None = None, geisser_greenhouse: bool = True, comparisons=None, method: str | None = None, control=None,
                        pairs=None, family: str = "per_reference", trend: bool = False, alpha: float = 0.05, ci: float = 0.95,
                        style: str = "classic", m: int | None = None) -> AnalysisResult:
    """Mixed-effects model for repeated measures with missing values: value = group mean + subject
    effect + error, with a random intercept per subject (row), fitted by REML (statsmodels MixedLM).

    What this engine reports:

    - Fixed effect of treatment: a Wald F test that every group mean is equal, with DFn = k - 1 and
      DFd = (number of values) - (number of subjects) - (k - 1) (the "between-within" df). With no
      blanks this is the same F, df and P as repeated-measures ANOVA assuming sphericity.
    - Random effects: the SD and variance of the subject effect and of the residual.
    - Was the matching effective: a likelihood-ratio test of the subject variance (REML log
      likelihoods, chi-square with 1 df, which is conservative at the boundary).
    - Multiple comparisons use the fitted group means, their covariance (X'V⁻¹X)⁻¹ at the REML
      variance estimates, and the same df.

    - Geisser-Greenhouse (``geisser_greenhouse=True``, the default as for repeated-measures ANOVA):
      ε is computed from the covariance matrix of the columns, each entry from the rows where both
      columns have values (pairwise complete; with no blanks this is the RM ANOVA ε), and DFn and
      DFd are both multiplied by it. There is no standard way to estimate ε with missing values,
      so this is this engine's choice.

    The model has subjects random and columns fixed, with no choice of covariance structure, and
    it equals repeated-measures ANOVA when nothing is missing. The denominator df is a choice;
    this engine uses the between-within df above. The multiple comparisons
    stay model-based (they do not switch to two-column comparisons under Geisser-Greenhouse).
    ``m`` is the number of comparisons the Bonferroni-type corrections count.
    """

    _check_alpha(alpha, ci)
    smf, MixedLMParams = _statsmodels_mixedlm()
    names, Y = _matrix(table, groups)
    k = len(names)
    rows, cols = np.nonzero(np.isfinite(Y))
    long = pd.DataFrame({"value": Y[rows, cols], "subject": rows, "group": pd.Categorical([names[c] for c in cols], categories=names)})
    counts = long.groupby("group", observed=False).size()
    if (counts < 1).any():
        raise StatisticsError(f"Every group needs at least one value; none in: {', '.join(counts.index[counts < 1])}.")
    subjects = long["subject"].nunique()
    n_obs = len(long)
    dfd = n_obs - subjects - (k - 1)
    if subjects < 2 or dfd < 1:
        raise StatisticsError("The mixed-effects model needs at least 2 subjects and more values than subjects plus groups.")
    model = smf.mixedlm("value ~ 0 + C(group)", long, groups="subject")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        fit = model.fit(reml=True)
    beta = np.asarray(fit.fe_params, dtype=float)
    var_subject = float(np.asarray(fit.cov_re)[0, 0])
    var_resid = float(fit.scale)
    V = _gls_covariance(long, k, max(var_subject, 0.0), var_resid)
    L = np.zeros((k - 1, k))
    L[:, 0] = 1
    L[np.arange(k - 1), np.arange(1, k)] = -1
    Lb = L @ beta
    f = float(Lb @ np.linalg.solve(L @ V @ L.T, Lb) / (k - 1))
    p_uncorrected = float(stats.f.sf(f, k - 1, dfd))
    eps = np.nan
    if k > 2:
        S = pd.DataFrame(Y).cov(min_periods=2).to_numpy()
        if np.isfinite(S).all():
            C = np.eye(k) - np.ones((k, k)) / k
            Sc = C @ S @ C
            denom = (k - 1) * np.trace(Sc @ Sc)
            if denom > 0:
                eps = float(min(1.0, max(1 / (k - 1), np.trace(Sc) ** 2 / denom)))
    else:
        eps = 1.0
    gg_on = geisser_greenhouse and np.isfinite(eps)
    scale = eps if gg_on else 1.0
    dfn_used, dfd_used = (k - 1) * scale, dfd * scale
    p = float(stats.f.sf(f, dfn_used, dfd_used))
    null = MixedLMParams.from_components(fe_params=beta, cov_re=np.array([[1e-10]]))
    llf0 = float(model.loglike(null, profile_fe=True))
    lr = max(0.0, 2 * (float(fit.llf) - llf0))
    p_match = float(stats.chi2.sf(lr, 1))
    result = _start("Mixed-effects model (REML)", table, names, {"alpha": alpha, "ci": ci, "style": style, "geisser_greenhouse": geisser_greenhouse,
                                                                 "comparisons": comparisons, "method": method,
                                                                 "control": control, "family": family, "trend": trend})
    fixed = result.section("Fixed effects (type III)")
    fixed.add("Assume sphericity (equal variability of differences)?", not geisser_greenhouse, "No" if geisser_greenhouse else "Yes")
    fixed.add("Treatment: F (DFn, DFd)", (f, dfn_used, dfd_used), f"{fmt(f)} ({fmt(dfn_used)}, {fmt(dfd_used)})")
    p_rows(fixed, p, alpha, style, label="Statistically significant")
    fixed.add("Geisser-Greenhouse's epsilon", eps)
    random = result.section("Random effects")
    random.add("Subject: SD", math.sqrt(max(var_subject, 0.0)))
    random.add("Subject: variance", var_subject)
    random.add("Residual: SD", math.sqrt(var_resid))
    random.add("Residual: variance", var_resid)
    match = result.section("Was the matching effective?")
    match.add("Chi-square, df", (lr, 1), f"{fmt(lr)}, 1")
    p_rows(match, p_match, alpha, style, label="Is there significant matching")
    data = result.section("Data summary")
    data.add("Number of treatments (columns)", k)
    data.add("Number of subjects (rows)", subjects)
    data.add("Number of missing values", int(subjects * k - n_obs))
    data.add("Number of values (total)", n_obs)
    means_section = result.section("Fitted means")
    for name, b, s in zip(names, beta, np.sqrt(np.diag(V))):
        means_section.add(name, {"mean": float(b), "se": float(s)}, f"{fmt(float(b))} ± {fmt(float(s))} (SE)")
    result.values.update(names=names, means=beta.tolist(), se_means=np.sqrt(np.diag(V)).tolist(), f=f, dfn=dfn_used, dfd=dfd_used, p=p, dfn_uncorrected=k - 1, dfd_uncorrected=dfd,
                         p_uncorrected=p_uncorrected, epsilon=eps, var_subject=var_subject, var_residual=var_resid, lr_matching=lr, p_matching=p_match, n_subjects=subjects, n_values=n_obs,
                         llf=float(fit.llf), converged=bool(getattr(fit, "converged", True)))
    if var_subject <= 1e-8 * max(var_resid, 1e-300):
        result.warnings.append("The subject variance was estimated as zero (the matching explains nothing), so the model is the same as ordinary ANOVA.")
    if any("converge" in str(w.message).lower() for w in caught) or not getattr(fit, "converged", True):
        result.warnings.append("The mixed model fit reported convergence problems; check the result.")
    result.warnings.append("Mixed-effects model: P uses a Wald F test with between-within df. Other denominator-df methods exist"
                           + (" and there is no standard way to estimate Geisser-Greenhouse epsilon with missing values (here: pairwise-complete covariances)" if gg_on else "")
                           + ", so P may differ slightly from other packages'.")
    if geisser_greenhouse and not gg_on:
        result.warnings.append("Geisser-Greenhouse epsilon could not be estimated (a pair of columns has fewer than 2 rows with both values), so P is uncorrected.")
    if not table.paired:
        result.warnings.append("The table is not marked as paired; a mixed-effects model for repeated measures assumes row i of each column is the same subject.")
    counts_arr = counts.to_numpy().astype(float)
    est = Estimates(names, beta, counts_arr, V, float(dfd), "pooled", error_label="mixed-model SEs, between-within df")
    ss_total = float(np.sum((long["value"] - long["value"].mean()) ** 2))
    changes: list = []
    more = _follow_up(result, est, comparisons, method, control, pairs, family, trend, alpha, ci, style, ss_total, None, m=m, changes=changes)
    result.methods = (f"Mixed-effects model (REML; random intercept per subject; {k} treatments, {subjects} subjects, {n_obs} values), "
                      f"Wald F test with between-within df{f', Geisser-Greenhouse corrected (epsilon = {eps:.3f})' if gg_on else ''} (alpha = {alpha:g}).{more}")
    record_overrides(result, changes)
    return result


# ---------------------------------------------------------------------------------------------
# Exact permutation P values for the rank tests
# ---------------------------------------------------------------------------------------------

KW_EXACT_MAX_PARTITIONS = 2_000_000
FRIEDMAN_EXACT_MAX = 1e9


def kw_partitions(sizes: Sequence[int]) -> int:
    """Number of distinct ways to split N values into groups of these sizes, counting groups of
    equal size as interchangeable (the Kruskal-Wallis statistic does not change when they swap):
    N! / (Π n_i! · Π m_s!), with m_s the number of groups of size s."""

    sizes = [int(n) for n in sizes]
    total = math.factorial(sum(sizes))
    for n in sizes:
        total //= math.factorial(n)
    for s in set(sizes):
        total //= math.factorial(sizes.count(s))
    return total


def kw_exact_p(samples, max_partitions: int = KW_EXACT_MAX_PARTITIONS) -> float | None:
    """Exact permutation P of the Kruskal-Wallis test, ties included: the share of all ways of
    splitting the observed (mid)ranks into groups of the observed sizes whose Σ R_i²/n_i is at
    least the observed one (H is a fixed increasing function of it, with or without the tie
    correction). Groups of equal size are enumerated once (by increasing smallest member), which
    leaves every share unchanged. Returns None when there are more than ``max_partitions`` splits."""

    sizes = [len(s) for s in samples]
    if kw_partitions(sizes) > max_partitions:
        return None
    pooled = np.concatenate([np.asarray(s, float) for s in samples])
    ranks = stats.rankdata(pooled)
    N = ranks.size
    start = np.cumsum([0] + sizes[:-1])
    observed = sum(ranks[a:a + n].sum() ** 2 / n for a, n in zip(start, sizes))
    order = sorted(sizes)
    tol = 1e-9 * max(1.0, observed)
    hits = 0
    total = 0

    def expand(used: np.ndarray, partial: np.ndarray, last_min: np.ndarray, level: int) -> None:
        nonlocal hits, total
        n = order[level]
        remaining = N - int(used[0].sum())
        rem = np.argsort(used, axis=1, kind="stable")[:, :remaining].astype(np.int16)
        if level == len(order) - 1:
            final = partial + ranks[rem].sum(axis=1) ** 2 / n
            hits += int(np.sum(final >= observed - tol))
            total += final.size
            return
        combos = np.array(list(itertools.combinations(range(remaining), n)), dtype=np.int16)
        same = level > 0 and order[level - 1] == n
        step = max(1, 200_000 // len(combos))
        for a in range(0, used.shape[0], step):
            chosen = rem[a:a + step][:, combos]  # rows × combos × n
            keep = chosen[:, :, 0] > last_min[a:a + step, None] if same else np.ones(chosen.shape[:2], bool)
            r_idx, c_idx = np.nonzero(keep)
            if r_idx.size == 0:
                continue
            picked = chosen[r_idx, c_idx]
            new_used = used[a:a + step][r_idx].copy()
            np.put_along_axis(new_used, picked.astype(np.intp), True, axis=1)
            new_partial = partial[a:a + step][r_idx] + ranks[picked].sum(axis=1) ** 2 / n
            expand(new_used, new_partial, picked[:, 0].astype(np.int32), level + 1)

    expand(np.zeros((1, N), bool), np.zeros(1), np.full(1, -1, np.int32), 0)
    return hits / total


def friedman_exact_p(Y: np.ndarray, limit: float = FRIEDMAN_EXACT_MAX) -> float | None:
    """Exact permutation P of the Friedman test, ties included: under the null hypothesis each row's
    ranks are in a random order (all k! orders equally likely, independently across rows). The
    distribution of the column rank sums is built row by row; since every order is applied, a state
    can be kept sorted. P is the probability that Σ R_j² is at least the observed value (Q is an
    increasing function of it for fixed ties). Returns None when (k!)^n > ``limit`` (the usual rule
    is exact unless (T!)^S > 10⁹)."""

    n, k = Y.shape
    if n * math.lgamma(k + 1) > math.log(limit):
        return None
    doubled = np.rint(2 * np.apply_along_axis(stats.rankdata, 1, Y)).astype(np.int64)
    observed = int(np.sum(doubled.sum(axis=0) ** 2))
    perms = np.array(list(itertools.permutations(range(k))), dtype=np.intp)
    states = np.zeros((1, k), np.int64)
    probs = np.ones(1)
    for row in doubled:
        options = row[perms]  # k! × k
        new = (states[:, None, :] + options[None, :, :]).reshape(-1, k)
        new.sort(axis=1)
        weights = np.repeat(probs / len(perms), len(perms))
        states, inverse = np.unique(new, axis=0, return_inverse=True)
        probs = np.bincount(inverse.ravel(), weights=weights)
    statistic = np.sum(states**2, axis=1)
    return float(min(1.0, probs[statistic >= observed].sum()))


# ---------------------------------------------------------------------------------------------
# Nonparametric
# ---------------------------------------------------------------------------------------------


def _kw_min_p(sizes: list[int]) -> float:
    """Smallest exact permutation P the Kruskal-Wallis test can give for these group sizes (no ties):
    the arrangements with the largest H are the ones where each group holds a block of consecutive
    ranks; P_min = (number of block orders giving that H) / (N! / Π n_i!)."""

    k = len(sizes)
    if k > 8:
        return 0.0
    N = sum(sizes)
    log_total = math.lgamma(N + 1) - sum(math.lgamma(n + 1) for n in sizes)
    best, count = -np.inf, 0
    for order in itertools.permutations(range(k)):
        start, h = 0, 0.0
        for g in order:
            n = sizes[g]
            mean_rank = start + (n + 1) / 2
            h += n * mean_rank * mean_rank
            start += n
        if h > best + 1e-9:
            best, count = h, 1
        elif abs(h - best) <= 1e-9:
            count += 1
    # identical block orders (groups of equal size swapped) are distinct arrangements, already counted
    return float(math.exp(math.log(count) - log_total))


def kruskal_wallis(table: ColumnTable, *, groups: Sequence[str] | None = None, comparisons=None, method: str | None = None, control=None,
                   pairs=None, family: str = "per_reference", exact: bool | None = None, alpha: float = 0.05, style: str = "classic",
                   m: int | None = None) -> AnalysisResult:
    """Kruskal-Wallis test. All values are ranked together (ties get the average rank).
    H = [12 / (N(N+1)) · Σ R_i²/n_i - 3(N+1)] / [1 - Σ(t³ - t)/(N³ - N)] (tie-corrected).

    P is exact (permutation, ties included, as usual) when there are at most
    KW_EXACT_MAX_PARTITIONS distinct ways to split the values into the groups (``kw_partitions``;
    for example 5 groups of 3 or 4 groups of 4, a few seconds at most); otherwise it is the
    chi-square approximation with k - 1 df. The cutoff is this engine's choice. ``exact=False`` forces
    chi-square, ``exact=True`` refuses when enumeration is too large. Multiple comparisons: Dunn's
    test on the mean ranks. ``m`` is the number of comparisons Dunn's Bonferroni-type correction counts."""

    _check_alpha(alpha, 0.95)
    names, samples, dropped = _independent(table, groups, "The Kruskal-Wallis test")
    pooled = np.concatenate(samples)
    N = pooled.size
    ranks = stats.rankdata(pooled)
    sizes = [s.size for s in samples]
    split = np.split(ranks, np.cumsum(sizes)[:-1])
    sums = np.array([r.sum() for r in split])
    n = np.array(sizes, dtype=float)
    k = len(samples)
    h_raw = 12 / (N * (N + 1)) * np.sum(sums**2 / n) - 3 * (N + 1)
    ties = tie_sum(pooled)
    correction = 1 - ties / (N**3 - N) if N > 1 else np.nan
    if not correction > 0:
        raise StatisticsError("Every value is the same, so the Kruskal-Wallis test is undefined.")
    h = float(h_raw / correction)
    p_chi2 = float(stats.chi2.sf(h, k - 1))
    p_exact = kw_exact_p(samples) if exact is not False else None
    if exact is True and p_exact is None:
        raise StatisticsError(f"An exact Kruskal-Wallis P needs at most {KW_EXACT_MAX_PARTITIONS:,} ways to split the values; these group sizes have more.")
    p = p_exact if p_exact is not None else p_chi2
    result = _start("Kruskal-Wallis test", table, names, {"alpha": alpha, "style": style, "comparisons": comparisons, "method": method,
                                                          "control": control, "family": family})
    section = result.section("Kruskal-Wallis test")
    section.add("P value", p, format_p(p, style=style))
    section.add("Exact or approximate P value?", p_exact is not None, "Exact" if p_exact is not None else f"Approximate (chi-square, {k - 1} df)")
    section.add("P value summary", p_summary(p, style), p_summary(p, style))
    sig = bool(p < alpha)
    section.add(_sig_label("Do the medians vary signif.", alpha), sig)
    section.add("Number of groups", k)
    section.add("Kruskal-Wallis statistic", h)
    data = result.section("Data summary")
    data.add("Number of treatments (columns)", k)
    data.add("Number of values (total)", N)
    groups_section = result.section("Group summary")
    for name, s, r in zip(names, samples, split):
        groups_section.add(name, {"n": int(s.size), "median": float(np.median(s)), "rank_sum": float(r.sum()), "mean_rank": float(r.mean())},
                           f"n = {s.size}; median = {fmt(float(np.median(s)))}; sum of ranks = {fmt(float(r.sum()))}; mean rank = {fmt(float(r.mean()))}")
    result.values.update(names=names, n=sizes, h=h, h_uncorrected=float(h_raw), df=k - 1, p=p, p_chi2=p_chi2, exact=p_exact is not None, rank_sums=sums.tolist(), mean_ranks=(sums / n).tolist(),
                         medians=[float(np.median(s)) for s in samples])
    if p_exact is None and min(sizes) < 5:
        result.warnings.append("A group has fewer than 5 values; the chi-square approximation behind the Kruskal-Wallis P can be inaccurate with such small groups.")
    floor = _kw_min_p(sizes)
    if floor >= alpha:
        result.warnings.append(f"With these group sizes, the smallest exact P the Kruskal-Wallis test can give is {format_p(floor, style=style)}, "
                               f"which is not below alpha = {alpha:g}: it cannot find a significant difference whatever the data.")
    result.values["min_exact_p"] = floor
    _replicate_note(result, sizes)
    _blank_note(result, dropped)
    variance = N * (N + 1) / 12 - ties / (12 * (N - 1))
    est = Estimates(names, sums / n, n, variance * np.diag(1 / n), np.inf, "rank", error_label="mean ranks, ties corrected")
    plan = plan_from(comparisons, method, control, pairs, family)
    changes: list = []
    more = _rank_follow_up(result, est, plan, alpha, style, lambda i, j: (N - (n[i] + n[j]) / 2) / math.sqrt(N * (N + 1) / 12 * (1 / n[i] + 1 / n[j])), m, changes)
    how = "exact permutation P" if p_exact is not None else f"approximate P from chi-square with {k - 1} df"
    result.methods = f"Kruskal-Wallis test ({k} groups, {N} values; tie-corrected H, {how}; alpha = {alpha:g}).{more}"
    record_overrides(result, changes)
    return result


def _rank_follow_up(result, est, plan, alpha, style, max_z, m=None, changes=None) -> str:
    if plan is None:
        if m is not None:
            raise StatisticsError("The number of comparisons (m) is used by multiple comparisons; ask for comparisons to use it.")
        return ""
    out = run_comparisons(est, plan, alpha=alpha, ci=0.95, style=style, m=m)
    _m_change(out, changes)
    index = {name: i for i, name in enumerate(est.names)}
    for row in out["rows"]:
        row["_max_z"] = max_z(index[row["group_1"]], index[row["group_2"]])
    _dunn_floor(result, est, out, alpha)
    for row in out["rows"]:
        row.pop("_max_z", None)
    comparison_rows(result, out, style, rank=True)
    families = "" if out["n_families"] == 1 else f", one family per reference group ({', '.join(out['references'])})"
    level = f"Q = {100 * alpha:g}%" if out["fdr"] else f"family-wise alpha = {alpha:g}"
    return f" Multiple comparisons: {out['name']} ({level}{families})."


def friedman(table: ColumnTable, *, groups: Sequence[str] | None = None, comparisons=None, method: str | None = None, control=None,
             pairs=None, family: str = "per_reference", exact: bool | None = None, alpha: float = 0.05, style: str = "classic",
             m: int | None = None) -> AnalysisResult:
    """Friedman test for matched rows. Values are ranked within each row (ties get the average
    rank); R_j is the rank sum of column j. With tie correction (as R's friedman.test):
    Q = 12 Σ (R_j - n(k+1)/2)² / [n k (k+1) - Σ(t³ - t)/(k - 1)]. P is exact (permutation of the
    ranks within rows, ties included) unless (k!)^n > 10⁹, the usual rule; then it is the chi-square
    approximation with k - 1 df. ``exact=False`` forces chi-square. Rows with a blank are left out. Multiple comparisons: Dunn's test on the mean
    ranks R_j/n with SE √(k(k+1)/(6n)) (no tie correction). ``m`` is the number of comparisons Dunn's
    Bonferroni-type correction counts."""

    _check_alpha(alpha, 0.95)
    names, Y = _matrix(table, groups)
    present = np.isfinite(Y).any(axis=1)
    complete = np.isfinite(Y).all(axis=1)
    dropped = int((present & ~complete).sum())
    Y = Y[complete]
    n, k = Y.shape
    if n < 2:
        raise StatisticsError(f"The Friedman test needs at least 2 complete rows (subjects); there are {n}.")
    R = np.apply_along_axis(stats.rankdata, 1, Y)
    sums = R.sum(axis=0)
    ties = sum(tie_sum(row) for row in Y)
    denom = n * k * (k + 1) - ties / (k - 1)
    if not denom > 0:
        raise StatisticsError("Every row has all values tied, so the Friedman test is undefined.")
    q = float(12 * np.sum((sums - n * (k + 1) / 2) ** 2) / denom)
    p_chi2 = float(stats.chi2.sf(q, k - 1))
    p_exact = friedman_exact_p(Y) if exact is not False else None
    if exact is True and p_exact is None:
        raise StatisticsError("An exact Friedman P is computed only when (k!)^n ≤ 10⁹.")
    p = p_exact if p_exact is not None else p_chi2
    result = _start("Friedman test", table, names, {"alpha": alpha, "style": style, "comparisons": comparisons, "method": method,
                                                    "control": control, "family": family})
    section = result.section("Friedman test")
    section.add("P value", p, format_p(p, style=style))
    section.add("Exact or approximate P value?", p_exact is not None, "Exact" if p_exact is not None else f"Approximate (chi-square, {k - 1} df)")
    section.add("P value summary", p_summary(p, style), p_summary(p, style))
    section.add(_sig_label("Are the medians signif. different", alpha), bool(p < alpha))
    section.add("Number of groups", k)
    section.add("Friedman statistic", q)
    data = result.section("Data summary")
    data.add("Number of treatments (columns)", k)
    data.add("Number of subjects (rows)", n)
    groups_section = result.section("Group summary")
    for j, name in enumerate(names):
        groups_section.add(name, {"median": float(np.median(Y[:, j])), "rank_sum": float(sums[j])}, f"median = {fmt(float(np.median(Y[:, j])))}; sum of ranks = {fmt(float(sums[j]))}")
    result.values.update(names=names, n_subjects=n, statistic=q, df=k - 1, p=p, p_chi2=p_chi2, exact=p_exact is not None, rank_sums=sums.tolist(), mean_ranks=(sums / n).tolist())
    floor = float(math.exp((1 - n) * math.lgamma(k + 1)))
    result.values["min_exact_p"] = floor
    if floor >= alpha:
        result.warnings.append(f"With {n} subjects and {k} groups, the smallest exact P the Friedman test can give is {format_p(floor, style=style)} "
                               f"(= (k!)^(1-n)), not below alpha = {alpha:g}: it cannot find a significant difference whatever the data.")
    elif n < 10 and p_exact is None:
        result.warnings.append("With fewer than 10 subjects the chi-square approximation behind the Friedman P can be inaccurate.")
    if dropped:
        result.warnings.append(f"{dropped} row(s) with a blank left out (the Friedman test needs complete rows).")
    if not table.paired:
        result.warnings.append("The table is not marked as paired; the Friedman test assumes row i of each column is the same subject.")
    est = Estimates(names, sums / n, np.full(k, n), k * (k + 1) / (12 * n) * np.eye(k), np.inf, "rank", error_label="mean ranks within rows")
    plan = plan_from(comparisons, method, control, pairs, family)
    changes: list = []
    more = _rank_follow_up(result, est, plan, alpha, style, lambda i, j: (k - 1) / math.sqrt(k * (k + 1) / (6 * n)), m, changes)
    how = "exact permutation P" if p_exact is not None else f"approximate P from chi-square with {k - 1} df"
    result.methods = f"Friedman test ({k} treatments, {n} subjects; {how}; alpha = {alpha:g}).{more}"
    record_overrides(result, changes)
    return result


ANOVA_TESTS = {
    "ordinary": ordinary_anova,
    "welch": welch_anova,
    "brown_forsythe": welch_anova,
    "repeated_measures": repeated_measures_anova,
    "mixed": mixed_effects_anova,
    "kruskal_wallis": kruskal_wallis,
    "friedman": friedman,
}


def one_way(table: ColumnTable, test: str = "ordinary", **options) -> AnalysisResult:
    """Run a one-way analysis by key: "ordinary", "welch" (= "brown_forsythe"; both are reported),
    "repeated_measures", "mixed", "kruskal_wallis" or "friedman"."""

    if test not in ANOVA_TESTS:
        raise StatisticsError(f"Unknown test {test!r}; choose one of {', '.join(ANOVA_TESTS)}.")
    return ANOVA_TESTS[test](table, **options)
