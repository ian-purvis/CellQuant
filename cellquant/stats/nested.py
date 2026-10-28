"""Nested (hierarchical) analyses: every value (for example every cell) kept, with the biological replicate it came from.

The data are a replicate table (``cellquant.stats.replicates``): each column is a group, and each replicate of a group
(an animal, a culture, an experiment) contributes several values. A replicate belongs to one group only, so the
replicates are nested in the groups. The analysis compares the groups against the variation among replicates, not among
values, so many cells from few replicates do not inflate N.

``nested(table)`` fits the nested ANOVA by the method of moments (Sokal & Rohlf): sums of squares between groups,
among replicates within groups and among values within replicates, with the group test's denominator built from the
expected mean squares (Satterthwaite's df when replicates have different numbers of values; with equal numbers this is
the mean square among replicates, and the test equals the ordinary test on replicate means). Two groups give the nested
t test (difference with its CI); three or more the nested one-way ANOVA with multiple comparisons. Also the variance
components and the share of the variation that lies among replicates (the intraclass correlation).
"""

from __future__ import annotations

import math

import numpy as np
from scipy import stats

from cellquant.errors import StatisticsError
from cellquant.stats._common import check_options, df_override, p_rows, record_overrides, t_critical, t_p, tails_text
from cellquant.stats.anova import _anova_table, _follow_up
from cellquant.stats.multcomp import Estimates
from cellquant.stats.pvalues import format_p, p_summary
from cellquant.stats.replicates import ReplicateTable
from cellquant.stats.results import AnalysisResult, fmt, fmt_ci


def _layout(table: ReplicateTable) -> tuple[list[str], list[list[np.ndarray]], list[list[str]]]:
    """Each group's replicates (with values) and their values, in table order."""

    if table.paired:
        raise StatisticsError("This replicate table is Paired: each replicate is measured in every column, so the replicates are not nested "
                              "in the groups. Untick Paired for independent replicates, or use the paired analyses of the replicate means.")
    long = table.long()
    if long.empty:
        raise StatisticsError("A nested analysis needs values with a replicate name (type one in the Replicate column).")
    names, groups, units = [], [], []
    for name in table.names:
        rows = long[long["group"] == name]
        if rows.empty:
            continue
        order = list(dict.fromkeys(rows["unit"]))
        names.append(name)
        groups.append([rows.loc[rows["unit"] == unit, "value"].to_numpy(float) for unit in order])
        units.append(order)
    return names, groups, units


def nested_core(groups: list[list[np.ndarray]]) -> dict:
    """The nested ANOVA of values in replicates in groups (method of moments)."""

    k = len(groups)
    n_gr = [np.array([u.size for u in g], float) for g in groups]
    n_g = np.array([n.sum() for n in n_gr])
    big_n, r = float(n_g.sum()), sum(len(g) for g in groups)
    every = np.concatenate([u for g in groups for u in g])
    grand = float(every.mean())
    group_means = np.array([np.concatenate(g).mean() for g in groups])
    unit_means = [np.array([u.mean() for u in g]) for g in groups]
    ss_g = float(np.sum(n_g * (group_means - grand) ** 2))
    ss_r = float(sum(np.sum(n * (m - group_means[i]) ** 2) for i, (n, m) in enumerate(zip(n_gr, unit_means))))
    ss_e = float(sum(np.sum((u - u.mean()) ** 2) for g in groups for u in g))
    df_g, df_r, df_e = k - 1, r - k, big_n - r
    ms_g = ss_g / df_g if df_g > 0 else np.nan
    ms_r = ss_r / df_r if df_r > 0 else np.nan
    ms_e = ss_e / df_e if df_e > 0 else np.nan
    within = float(sum(np.sum(n ** 2) / n.sum() for n in n_gr))  # Σ_g Σ_r n_gr² / n_g
    c_r = (big_n - within) / df_r if df_r > 0 else np.nan  # E(MS replicates) = σ² + c_r σ²_rep
    c_g = (within - float(sum(np.sum(n ** 2) for n in n_gr)) / big_n) / df_g if df_g > 0 else np.nan  # E(MS groups) = σ² + c_g σ²_rep + groups
    ratio = c_g / c_r
    ms_den = ratio * ms_r + (1 - ratio) * ms_e
    parts = (ratio * ms_r) ** 2 / df_r + ((1 - ratio) * ms_e) ** 2 / df_e if df_r > 0 and df_e > 0 else np.nan
    df_den = ms_den ** 2 / parts if parts > 0 else float(df_r)
    if abs(ratio - 1) < 1e-12:
        ms_den, df_den = ms_r, float(df_r)
    f_g = ms_g / ms_den if ms_den > 0 else np.nan
    f_r = ms_r / ms_e if ms_e > 0 else np.nan
    var_rep_raw = (ms_r - ms_e) / c_r if df_r > 0 and df_e > 0 else np.nan
    var_rep, var_e = max(var_rep_raw, 0.0), ms_e
    # Variance of each group's mean from the components; the raw (possibly negative) estimate keeps the two-group t² = F
    se2 = np.array([var_rep_raw * np.sum(n ** 2) / n.sum() ** 2 + ms_e / n.sum() for n in n_gr])
    floor = np.array([ms_e / n.sum() / max(len(n), 1) for n in n_gr])
    se2 = np.maximum(se2, floor * 1e-6)
    return {"k": k, "replicates": r, "n": n_g, "n_units": np.array([len(g) for g in groups], float), "N": big_n, "group_means": group_means,
            "unit_means": unit_means, "grand": grand, "ss_g": ss_g, "ss_r": ss_r, "ss_e": ss_e, "df_g": df_g, "df_r": df_r, "df_e": df_e,
            "ms_g": ms_g, "ms_r": ms_r, "ms_e": ms_e, "ms_den": ms_den, "df_den": float(df_den), "f_g": f_g, "f_r": f_r,
            "p_g": float(stats.f.sf(f_g, df_g, df_den)) if np.isfinite(f_g) else np.nan,
            "p_r": float(stats.f.sf(f_r, df_r, df_e)) if np.isfinite(f_r) else np.nan,
            "var_rep": var_rep, "var_e": var_e, "icc": var_rep / (var_rep + var_e) if var_rep + var_e > 0 else np.nan,
            "balanced": abs(ratio - 1) < 1e-12, "var_means": se2}


def nested(table: ReplicateTable, *, alternative: str = "two-sided", comparisons=None, method: str | None = None, control=None, pairs=None,
           family: str = "per_reference", alpha: float = 0.05, ci: float = 0.95, style: str = "classic", df=None, m: int | None = None) -> AnalysisResult:
    """Nested t test (two groups) or nested one-way ANOVA (three or more) of a replicate table.

    Advanced overrides (keyword-only, recorded in "Changed from the default"): ``df`` replaces the denominator df of the group
    test, the t test and the comparisons (a positive number, or "conservative" = the smaller of replicates − groups and the computed df); ``m`` is the number of
    comparisons the Bonferroni-type corrections count."""

    alternative = check_options(alternative, ci, alpha)
    names, groups, units = _layout(table)
    if len(names) < 2:
        raise StatisticsError("A nested analysis compares groups: it needs at least two columns with values.")
    core = nested_core(groups)
    if core["df_r"] < 1:
        raise StatisticsError("A nested analysis needs more replicates than groups: some group needs at least 2 replicates.")
    if core["df_e"] < 1:
        raise StatisticsError("Every replicate has one value, so there is nothing nested: analyze the values as they are (one per replicate).")
    two = len(names) == 2
    if comparisons == "trend":
        raise StatisticsError("The test for linear trend is not part of the nested analyses; compare the groups instead.")
    if not two and alternative != "two-sided":
        raise StatisticsError("A one-tailed P applies to two groups only.")
    changes, larger = [], []
    if df is not None:
        core["df_den"] = df_override(df, core["df_den"], "Denominator df", changes, larger, conservative=min(float(core["df_r"]), core["df_den"]))
        core["p_g"] = float(stats.f.sf(core["f_g"], core["df_g"], core["df_den"])) if np.isfinite(core["f_g"]) else np.nan
    analysis = "Nested t test" if two else "Nested one-way ANOVA"
    result = AnalysisResult(analysis, table.title, {"alternative": alternative, "comparisons": comparisons, "method": method, "control": control,
                                                    "family": family, "alpha": alpha, "ci": ci, "style": style})
    head = result.section("Table analyzed")
    head.add("Table", table.title)
    head.add("Data sets analyzed", tuple(names), ", ".join(names))
    level = f"{100 * ci:g}%"
    values = {"names": names, "n": core["n"].astype(int).tolist(), "replicates": core["n_units"].astype(int).tolist(), "means": core["group_means"].tolist(),
              "f": core["f_g"], "dfn": core["df_g"], "dfd": core["df_den"], "p": core["p_g"], "f_replicates": core["f_r"], "p_replicates": core["p_r"],
              "var_replicates": core["var_rep"], "var_residual": core["var_e"], "icc": core["icc"], "balanced": core["balanced"]}
    if two:
        diff = float(core["group_means"][1] - core["group_means"][0])
        se = math.sqrt(float(core["var_means"].sum()))
        t = diff / se if se > 0 else np.nan
        p = t_p(t, core["df_den"], alternative)
        crit = t_critical(ci, core["df_den"])
        section = result.section(analysis)
        p_rows(section, p, alpha, style, alternative, (names[0], names[1]))
        section.add("t, df", (t, core["df_den"]), f"t={fmt(t)}, df={fmt(core['df_den'])}")
        size = result.section("How big is the difference?")
        size.add(f"Mean of {names[0]}", core["group_means"][0])
        size.add(f"Mean of {names[1]}", core["group_means"][1])
        size.add(f"Difference between means ({names[1]} - {names[0]}) ± SEM", (diff, se), f"{fmt(diff)} ± {fmt(se)}")
        size.add(f"{level} confidence interval", (diff - crit * se, diff + crit * se), fmt_ci(diff - crit * se, diff + crit * se))
        values.update(t=t, df=core["df_den"], p_t=p, difference=diff, se=se, ci_low=diff - crit * se, ci_high=diff + crit * se)
        values["p"] = p
    else:
        summary = result.section("ANOVA summary")
        summary.add("F", core["f_g"])
        summary.add("P value", core["p_g"], format_p(core["p_g"], style=style))
        summary.add("P value summary", p_summary(core["p_g"], style), p_summary(core["p_g"], style))
        sig = bool(core["p_g"] < alpha) if np.isfinite(core["p_g"]) else None
        summary.add(f"Significant diff. among means (P < {alpha:g})?", sig, "" if sig is None else ("Yes" if sig else "No"))
    rows = [("Between groups (columns)", core["ss_g"], core["df_g"], core["ms_g"], core["f_g"], core["p_g"]),
            ("Among replicates (within columns)", core["ss_r"], core["df_r"], core["ms_r"], core["f_r"], core["p_r"]),
            ("Residual (within replicates)", core["ss_e"], core["df_e"], core["ms_e"], None, None),
            ("Total", core["ss_g"] + core["ss_r"] + core["ss_e"], core["N"] - 1, None, None, None)]
    _anova_table(result, rows, style)
    table_note = result.section("ANOVA table")
    if core["balanced"]:
        table_note.add("Groups tested against", "replicates", f"MS among replicates, DF = {fmt(core['df_den'])}")
    else:
        table_note.add("Groups tested against", "synthesized", f"MS = {fmt(core['ms_den'])} from the expected mean squares, DF = {fmt(core['df_den'])} "
                                                               "(Satterthwaite)")
    parts = result.section("Variance components")
    parts.add("Among replicates (σ² replicates)", core["var_rep"])
    parts.add("Within replicates (σ² residual)", core["var_e"])
    parts.add("Share of the variation among replicates (intraclass correlation)", core["icc"],
              "" if not np.isfinite(core["icc"]) else f"{100 * core['icc']:.1f}%")
    data = result.section("Data summary")
    for name, unit_names, values_in, mean in zip(names, units, groups, core["group_means"]):
        counts = [u.size for u in values_in]
        data.add(name, {"replicates": len(unit_names), "n": int(sum(counts)), "mean": float(mean)},
                 f"N = {len(unit_names)} replicates; n = {sum(counts)} values ({min(counts)} to {max(counts)} per replicate); mean = {fmt(float(mean))}")
    data.add("Number of groups (columns)", len(names))
    data.add("Number of replicates (total)", core["replicates"])
    data.add("Number of values (total)", int(core["N"]))
    result.values.update(values)

    test = f"nested t test ({tails_text(alternative).lower()} P)" if two else "nested one-way ANOVA"
    denominator = ("the mean square among replicates" if core["balanced"] else
                   "a mean square synthesized from the expected mean squares, with Satterthwaite's df (replicates have different numbers of values)")
    result.methods = (f"A {test} (alpha = {alpha:g}) of {int(core['N'])} values (n) from {core['replicates']} biological replicates (N) in "
                      f"{len(names)} groups: replicates nested in groups, fitted by the method of moments. The groups are tested against "
                      f"{denominator}, so N is the number of replicates, not of values.")
    if two:
        result.methods += (f" Difference {names[1]} − {names[0]} with its {level} CI; the SE of each group mean comes from the variance "
                           "components.")
    else:
        est = Estimates(names, core["group_means"], core["n_units"], np.diag(core["var_means"]), core["df_den"], "pooled",
                        error_label="SEs from the variance components, denominator df")
        result.methods += _follow_up(result, est, comparisons, method, control, pairs, family, False, alpha, ci, style, m=m, changes=changes)
    if table.unassigned():
        result.warnings.append(f"{table.unassigned()} value(s) above the first replicate name belong to no replicate and were left out.")
    blanks = [name for name in table.names if name not in names]
    if blanks:
        result.warnings.append(f"No values in {', '.join(blanks)}; left out.")
    if (core["n_units"] < 2).any():
        result.warnings.append("A group has only one replicate: it adds nothing to the variation among replicates, and its mean has no "
                               "replicate-level check.")
    if core["replicates"] - len(names) < 3:
        result.warnings.append(f"Only {core['replicates']} replicates for {len(names)} groups: the test has {fmt(core['df_den'])} df and little "
                               "power. Add biological replicates rather than more values per replicate.")
    if core["var_rep"] == 0 and np.isfinite(core["ms_r"]):
        result.warnings.append("The replicates vary less than the values within them (MS among replicates < MS residual): the "
                               "replicate variance component is set to 0.")
    record_overrides(result, changes, larger)
    return result


__all__ = ["nested", "nested_core"]
