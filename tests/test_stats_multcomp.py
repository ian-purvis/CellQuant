"""Multiple comparisons, P value adjustment, FDR q values, compact letter displays and "Analyze a stack of P values",
against R's documented examples, published tables, scipy and statsmodels, and independent formulas and simulations."""

from __future__ import annotations

import itertools
import math

import numpy as np
import pytest
from scipy import stats

from cellquant.errors import StatisticsError
from cellquant.stats import (
    ColumnTable,
    ComparisonPlan,
    adjust_p,
    compact_letter_display,
    friedman,
    kruskal_wallis,
    ordinary_anova,
    p_value_stack,
    repeated_measures_anova,
    welch_anova,
)
from cellquant.stats.multcomp import Estimates, _critical, max_abs_t_cdf, run_comparisons, smm_cdf

from tests.test_stats_anova import INSECTS, WARP, _rm_table, plant


def rows(result):
    return {r["label"]: r for r in result.values["comparisons"]}


# ------------------------------------------------------------------------------------------- Tukey and Newman-Keuls


def test_tukey_matches_r_tukeyhsd_on_plantgrowth():
    # R: TukeyHSD(aov(weight ~ group, data = PlantGrowth)):
    #             diff        lwr       upr     p adj
    #   trt1-ctrl -0.371 -1.0622161 0.3202161 0.3908711
    #   trt2-ctrl  0.494 -0.1972161 1.1852161 0.1979960
    #   trt2-trt1  0.865  0.1737839 1.5562161 0.0120064
    # R reports second minus first; this engine reports "ctrl vs. trt1" as ctrl - trt1.
    r = rows(ordinary_anova(plant(), comparisons="all"))
    expected = {"ctrl vs. trt1": (0.371, -0.3202161, 1.0622161, 0.3908711), "ctrl vs. trt2": (-0.494, -1.1852161, 0.1972161, 0.1979960),
                "trt1 vs. trt2": (-0.865, -1.5562161, -0.1737839, 0.0120064)}
    for label, (diff, low, high, p) in expected.items():
        row = r[label]
        assert row["difference"] == pytest.approx(diff, abs=1e-9)
        assert row["ci_low"] == pytest.approx(low, abs=5e-7) and row["ci_high"] == pytest.approx(high, abs=5e-7)
        assert row["p_adjusted"] == pytest.approx(p, abs=5e-7) and row["statistic_name"] == "q"
    assert r["trt1 vs. trt2"]["significant"] and r["trt1 vs. trt2"]["summary"] == "*" and r["ctrl vs. trt1"]["summary"] == "ns"


def test_tukey_engine_matches_r_tukeyhsd_example_with_a_blocking_factor():
    # R's ?TukeyHSD: TukeyHSD(aov(breaks ~ wool + tension, data = warpbreaks), "tension", ordered = TRUE):
    #         diff       lwr      upr     p adj
    #   M-H  4.722222 -4.6311985 14.07564 0.4474210
    #   L-H 14.722222  5.3688015 24.07564 0.0011218
    #   L-M 10.000000  0.6465793 19.35342 0.0336262
    # The error term comes from the two-factor model (df 50), passed to the engine directly.
    Y = np.array([WARP["L"], WARP["M"], WARP["H"]], float)
    grand = Y.mean()
    ss_tension = 18 * np.sum((Y.mean(axis=1) - grand) ** 2)
    ss_wool = 27 * np.sum((np.array([Y[:, :9].mean(), Y[:, 9:].mean()]) - grand) ** 2)
    mse = (np.sum((Y - grand) ** 2) - ss_tension - ss_wool) / 50
    est = Estimates(["L", "M", "H"], Y.mean(axis=1), np.full(3, 18.0), mse / 18 * np.eye(3), 50)
    out = {r["label"]: r for r in run_comparisons(est, ComparisonPlan("all", "tukey"))["rows"]}
    for label, (diff, low, high, p) in {"M vs. H": (4.722222, -4.6311985, 14.07564, 0.4474210), "L vs. H": (14.722222, 5.3688015, 24.07564, 0.0011218),
                                        "L vs. M": (10.0, 0.6465793, 19.35342, 0.0336262)}.items():
        row = out[label]
        assert row["difference"] == pytest.approx(diff, abs=5e-7)
        assert row["ci_low"] == pytest.approx(low, abs=5e-6) and row["ci_high"] == pytest.approx(high, abs=5e-6)
        assert row["p_adjusted"] == pytest.approx(p, abs=5e-7)


def test_tukey_kramer_matches_scipy_tukey_hsd_with_unequal_n():
    rng = np.random.default_rng(1)
    groups = {"A": rng.normal(0, 1, 5), "B": rng.normal(1, 1, 9), "C": rng.normal(0.4, 1, 7), "D": rng.normal(1.5, 1, 4)}
    result = ordinary_anova(ColumnTable.from_columns(groups), comparisons="all")
    ref = stats.tukey_hsd(*groups.values())
    ci = ref.confidence_interval(0.95)
    names = list(groups)
    for row in result.values["comparisons"]:
        i, j = names.index(row["group_1"]), names.index(row["group_2"])
        assert row["p_adjusted"] == pytest.approx(ref.pvalue[i, j], abs=1e-7)
        assert row["difference"] == pytest.approx(ref.statistic[i, j])
        assert row["ci_low"] == pytest.approx(ci.low[i, j], abs=1e-6) and row["ci_high"] == pytest.approx(ci.high[i, j], abs=1e-6)
    # Tukey with two groups is the pooled t test.
    est = Estimates(["a", "b"], np.array([0.0, 1.0]), np.array([5.0, 5.0]), 0.8 * np.eye(2) / 5, 8)
    row = run_comparisons(est, ComparisonPlan("all", "tukey"))["rows"][0]
    assert row["p_adjusted"] == pytest.approx(2 * stats.t.sf(1 / math.sqrt(0.32), 8), rel=1e-9)


def test_newman_keuls_uses_the_range_of_means_spanned_stepwise():
    # Ordered means trt1 < ctrl < trt2. The extreme pair spans r = 3 means (Tukey's P); adjacent pairs span r = 2
    # (the studentized range with 2 means is the t test, so Fisher's LSD P), tested only if the wider range was significant.
    nk = rows(ordinary_anova(plant(), comparisons="all", method="newman_keuls"))
    tukey = rows(ordinary_anova(plant(), comparisons="all"))
    lsd = rows(ordinary_anova(plant(), comparisons="all", method="fisher_lsd"))
    assert nk["trt1 vs. trt2"]["significant"] == (tukey["trt1 vs. trt2"]["p_adjusted"] < 0.05)
    for label in ("ctrl vs. trt1", "ctrl vs. trt2"):
        assert nk[label]["significant"] == (lsd[label]["p"] < 0.05 and tukey["trt1 vs. trt2"]["p_adjusted"] < 0.05)
        assert math.isnan(nk[label]["p_adjusted"]) and math.isnan(nk[label]["ci_low"])
    assert any("Newman-Keuls" in w for w in ordinary_anova(plant(), comparisons="all", method="newman_keuls").warnings)


# ------------------------------------------------------------------------------------------- Dunnett


def test_dunnett_critical_values_match_dunnetts_table():
    # Dunnett (1955, 1964) two-sided 5% critical values for equal group sizes (correlation 1/2), for 1 to 9
    # treatment groups against one control, at df = infinity and df = 10.
    table = {np.inf: [1.96, 2.21, 2.35, 2.44, 2.51, 2.57, 2.61, 2.65, 2.69], 10: [2.23, 2.57, 2.76, 2.89, 2.99, 3.07, 3.14, 3.19, 3.24]}
    for df, values in table.items():
        for m, expected in enumerate(values, start=1):
            corr = np.full((m, m), 0.5) + 0.5 * np.eye(m)
            assert _critical(lambda c: max_abs_t_cdf(c, corr, df), 0.95) == pytest.approx(expected, abs=0.006)


def test_dunnett_matches_scipy_and_a_single_comparison_is_the_t_test():
    # scipy's ?dunnett example (Dunnett 1955, Table 1): blood counts.
    control, drug_a, drug_b = [7.40, 8.50, 7.20, 8.24, 9.84, 8.32], [9.76, 8.80, 7.68, 9.36], [12.80, 9.68, 12.16, 9.20, 10.55]
    table = ColumnTable.from_columns({"control": control, "a": drug_a, "b": drug_b})
    r = rows(ordinary_anova(table, comparisons="control", control="control"))
    ref = stats.dunnett(drug_a, drug_b, control=control, rng=np.random.default_rng(0))
    ci = ref.confidence_interval()
    # scipy integrates by quasi-Monte Carlo (about 3-4 decimals); this engine integrates the one-factor form directly.
    for row, i in ((r["control vs. a"], 0), (r["control vs. b"], 1)):
        assert row["p_adjusted"] == pytest.approx(ref.pvalue[i], abs=5e-4)
        assert row["difference"] == pytest.approx(-ref.statistic[i] * row["se"])  # control - treatment
        assert -row["ci_high"] == pytest.approx(ci.low[i], abs=2e-3) and -row["ci_low"] == pytest.approx(ci.high[i], abs=2e-3)
        assert row["reference"] == "control"
    for m in (1,):
        assert 1 - max_abs_t_cdf(2.0, np.eye(m), 10) == pytest.approx(2 * stats.t.sf(2.0, 10), rel=1e-9)


def test_dunnett_quadrature_agrees_with_monte_carlo():
    # Unequal group sizes: correlations sqrt(n_i n_j / ((n_i + n_0)(n_j + n_0))). 4 million simulated maxima.
    n0, ns, df = 6, np.array([4, 5, 9]), 15
    lam = np.sqrt(ns / (ns + n0))
    corr = np.outer(lam, lam)
    np.fill_diagonal(corr, 1)
    rng = np.random.default_rng(0)
    draws = 4_000_000
    z0 = rng.standard_normal(draws)
    Z = lam[:, None] * z0 + np.sqrt(1 - lam**2)[:, None] * rng.standard_normal((3, draws))
    tmax = np.max(np.abs(Z), axis=0) / np.sqrt(rng.chisquare(df, draws) / df)
    for c in (2.0, 2.6, 3.4):
        simulated = np.mean(tmax >= c)
        se = math.sqrt(simulated * (1 - simulated) / draws)
        assert 1 - max_abs_t_cdf(c, corr, df) == pytest.approx(simulated, abs=4 * se + 1e-6)


def test_dunnett_with_a_general_correlation_agrees_with_monte_carlo_and_sits_below_sidak():
    # Repeated measures without sphericity: correlations come from the sample covariance and have no one-factor form,
    # so the probability comes from scipy's quasi-Monte Carlo integration.
    rng = np.random.default_rng(2)
    corr = np.corrcoef(rng.normal(size=(4, 7)))
    df, draws = 7, 2_000_000
    T = np.linalg.cholesky(corr) @ rng.standard_normal((4, draws)) / np.sqrt(rng.chisquare(df, draws) / df)
    tmax = np.max(np.abs(T), axis=0)
    for c in (2.5, 3.5):
        simulated = np.mean(tmax >= c)
        assert 1 - max_abs_t_cdf(c, corr, df) == pytest.approx(simulated, abs=4 * math.sqrt(simulated / draws) + 1e-6)
    _, table = _rm_table(n=8, sds=(0.3, 0.5, 0.8, 1.4, 0.9))
    dunnett = repeated_measures_anova(table, comparisons="control", control="T1").values["comparisons"]
    sidak = repeated_measures_anova(table, comparisons="control", control="T1", method="sidak").values["comparisons"]
    assert all(d["p_adjusted"] <= s["p_adjusted"] + 1e-9 for d, s in zip(dunnett, sidak))


# ------------------------------------------------------------------------------------------- Šidák, Bonferroni, Holm-Šidák, LSD


def test_sidak_bonferroni_holm_sidak_and_lsd_on_the_anova_error_term():
    sm = pytest.importorskip("statsmodels.stats.multitest")
    table = ColumnTable.from_columns(INSECTS)
    lsd = ordinary_anova(table, comparisons="all", method="fisher_lsd")
    raw = np.array([r["p"] for r in lsd.values["comparisons"]])
    v = lsd.values
    # Fisher's LSD: t = diff / sqrt(MSE (1/n_i + 1/n_j)) with the ANOVA df.
    first = lsd.values["comparisons"][0]
    t = (np.mean(INSECTS["A"]) - np.mean(INSECTS["B"])) / math.sqrt(v["ms_within"] * (2 / 12))
    assert first["statistic"] == pytest.approx(t) and first["p"] == pytest.approx(2 * stats.t.sf(abs(t), 66)) and first["p_adjusted"] == first["p"]
    assert first["ci_high"] - first["difference"] == pytest.approx(stats.t.ppf(0.975, 66) * first["se"])
    for method, ref in (("sidak", "sidak"), ("bonferroni", "bonferroni"), ("holm_sidak", "holm-sidak")):
        got = np.array([r["p_adjusted"] for r in ordinary_anova(table, comparisons="all", method=method).values["comparisons"]])
        assert got == pytest.approx(sm.multipletests(raw, method=ref)[1], rel=1e-10, abs=1e-15)
    m = raw.size
    sidak = ordinary_anova(table, comparisons="all", method="sidak").values["comparisons"][0]
    assert sidak["ci_high"] - sidak["difference"] == pytest.approx(stats.t.ppf(0.5 + 0.95 ** (1 / m) / 2, 66) * sidak["se"])
    bonf = ordinary_anova(table, comparisons="all", method="bonferroni").values["comparisons"][0]
    assert bonf["ci_high"] - bonf["difference"] == pytest.approx(stats.t.ppf(1 - 0.025 / m, 66) * bonf["se"])
    holm = ordinary_anova(table, comparisons="all", method="holm_sidak").values["comparisons"][0]
    assert math.isnan(holm["ci_low"])


def test_selected_pairs_and_refusals():
    result = ordinary_anova(plant(), comparisons="selected", pairs=[("ctrl", "trt2"), ("trt1", "trt2")])
    assert [r["label"] for r in result.values["comparisons"]] == ["ctrl vs. trt2", "trt1 vs. trt2"]
    assert result.values["comparison_method"] == "sidak" and result.values["comparisons_per_family"] == [2]
    with pytest.raises(StatisticsError, match="every pair"):
        ordinary_anova(plant(), comparisons="selected", pairs=[("ctrl", "trt2")], method="tukey")
    with pytest.raises(StatisticsError, match="reference"):
        ordinary_anova(plant(), comparisons="all", method="dunnett")
    with pytest.raises(StatisticsError, match="not available"):
        ordinary_anova(plant(), comparisons="all", method="games_howell")
    with pytest.raises(StatisticsError, match="No group"):
        ordinary_anova(plant(), comparisons="control", control="nope")
    with pytest.raises(StatisticsError, match="twice"):
        ordinary_anova(plant(), comparisons="selected", pairs=[("ctrl", "trt2"), ("trt2", "ctrl")])


# ------------------------------------------------------------------------------------------- one or two reference groups


def test_two_reference_groups_make_one_family_each_and_tag_every_comparison():
    table = ColumnTable.from_columns(INSECTS)
    result = ordinary_anova(table, comparisons="control", control=["C", "F"])
    comps = result.values["comparisons"]
    assert result.values["n_families"] == 2 and result.values["comparisons_per_family"] == [5, 5]
    assert {r["reference"] for r in comps[:5]} == {"C"} and {r["reference"] for r in comps[5:]} == {"F"}
    assert all(r["group_1"] == r["reference"] for r in comps)
    # Each family is exactly Dunnett's test against that reference alone.
    for ref in ("C", "F"):
        alone = ordinary_anova(table, comparisons="control", control=ref).values["comparisons"]
        mine = [r for r in comps if r["reference"] == ref]
        assert [r["p_adjusted"] for r in mine] == pytest.approx([r["p_adjusted"] for r in alone], rel=1e-12)
    assert "one family per reference group (C, F)" in result.methods
    # Combined: one family, the C-F comparison counted once, still tagged by reference.
    combined = ordinary_anova(table, comparisons="control", control=["C", "F"], method="holm_sidak", family="combined")
    comps = combined.values["comparisons"]
    assert combined.values["n_families"] == 1 and len(comps) == 9
    assert [r["reference"] for r in comps] == ["C"] * 5 + ["F"] * 4
    with pytest.raises(StatisticsError, match="one reference group per family"):
        ordinary_anova(table, comparisons="control", control=["C", "F"], family="combined")
    with pytest.raises(StatisticsError, match="one or two"):
        ordinary_anova(table, comparisons="control", control=["A", "B", "C"])


# ------------------------------------------------------------------------------------------- unequal SDs


def _welch(a, b):
    va, vb = np.var(a, ddof=1) / len(a), np.var(b, ddof=1) / len(b)
    return math.sqrt(va + vb), (va + vb) ** 2 / (va**2 / (len(a) - 1) + vb**2 / (len(b) - 1))


def test_games_howell_dunnett_t3_and_tamhane_t2_formulas():
    groups = {k: np.array(v, float) for k, v in INSECTS.items()}
    names, k = list(groups), len(groups)
    table = ColumnTable.from_columns(INSECTS)
    gh = welch_anova(table, comparisons="all").values["comparisons"]
    t3 = welch_anova(table, comparisons="all", method="dunnett_t3").values["comparisons"]
    t2 = welch_anova(table, comparisons="all", method="tamhane_t2").values["comparisons"]
    m = k * (k - 1) // 2
    for row_gh, row_t3, row_t2, (i, j) in zip(gh, t3, t2, itertools.combinations(range(k), 2)):
        a, b = groups[names[i]], groups[names[j]]
        se, df = _welch(a, b)
        diff = a.mean() - b.mean()
        # Games-Howell: q = |diff| / (SE/√2) on the studentized range with k means and Welch's df.
        assert row_gh["p_adjusted"] == pytest.approx(stats.studentized_range.sf(abs(diff) / se * math.sqrt(2), k, df), rel=1e-6)
        assert row_gh["ci_high"] - diff == pytest.approx(stats.studentized_range.ppf(0.95, k, df) / math.sqrt(2) * se, rel=1e-6)
        # Tamhane's T2: Welch's t test, Šidák-adjusted.
        welch_p = stats.ttest_ind(a, b, equal_var=False).pvalue
        assert row_t2["p"] == pytest.approx(welch_p, rel=1e-9)
        assert row_t2["p_adjusted"] == pytest.approx(1 - (1 - welch_p) ** m, rel=1e-9, abs=1e-14)
        # Dunnett's T3 (studentized maximum modulus) is never more conservative than T2 (Šidák's inequality).
        assert row_t3["p_adjusted"] <= row_t2["p_adjusted"] + 1e-12
    assert 1 - smm_cdf(2.0, 1, 7) == pytest.approx(2 * stats.t.sf(2.0, 7), rel=1e-9)


def test_studentized_maximum_modulus_agrees_with_monte_carlo():
    rng = np.random.default_rng(3)
    draws, m, df = 2_000_000, 6, 9
    smm = np.max(np.abs(rng.standard_normal((m, draws))), axis=0) / np.sqrt(rng.chisquare(df, draws) / df)
    for c in (2.5, 3.2, 4.0):
        simulated = np.mean(smm >= c)
        se = math.sqrt(simulated * (1 - simulated) / draws)
        assert 1 - smm_cdf(c, m, df) == pytest.approx(simulated, abs=4 * se + 1e-6)


def test_welch_anova_defaults_to_games_howell_or_t3():
    assert welch_anova(plant(), comparisons="all").values["comparison_method"] == "games_howell"
    assert welch_anova(plant(), comparisons="control", control="ctrl").values["comparison_method"] == "dunnett_t3"
    with pytest.raises(StatisticsError, match="not available"):
        welch_anova(plant(), comparisons="all", method="tukey")


# ------------------------------------------------------------------------------------------- linear trend


def test_test_for_linear_trend_is_the_regression_on_column_number():
    rng = np.random.default_rng(4)
    groups = {"1": rng.normal(1.0, 1, 6), "2": rng.normal(1.6, 1, 9), "3": rng.normal(2.1, 1, 5), "4": rng.normal(2.2, 1, 8)}
    result = ordinary_anova(ColumnTable.from_columns(groups), comparisons="trend")
    v = result.values
    y = np.concatenate(list(groups.values()))
    x = np.concatenate([np.full(len(g), i + 1.0) for i, g in enumerate(groups.values())])
    assert v["trend_slope"] == pytest.approx(np.polyfit(x, y, 1)[0])
    # F for trend = SS explained by the straight line (fitted to all values) / MS_residual of the one-way ANOVA.
    ss_line = np.sum((np.polyval(np.polyfit(x, y, 1), x) - y.mean()) ** 2)
    assert v["trend_f"] == pytest.approx(ss_line / v["ms_within"]) and v["trend_p"] == pytest.approx(stats.f.sf(ss_line / v["ms_within"], 1, v["dfd"]))
    assert v["trend_r_squared"] == pytest.approx(ss_line / v["ss_total"])
    # Alerting R²: the fraction of the variation among the group means explained by the trend (always the larger).
    assert v["trend_alerting_r_squared"] == pytest.approx(ss_line / v["ss_between"]) and v["trend_alerting_r_squared"] > v["trend_r_squared"]
    # Deviation from linearity: the rest of the between-group variation, k - 2 df.
    assert v["nonlinear_f"] == pytest.approx((v["ss_between"] - ss_line) / 2 / v["ms_within"])
    assert result.row("Test for linear trend", "Is linear trend significant (P < 0.05)?").value == (v["trend_p"] < 0.05)
    with pytest.raises(StatisticsError, match="at least 3"):
        ordinary_anova(ColumnTable.from_columns({"a": [1, 2], "b": [2, 3]}), trend=True)


# ------------------------------------------------------------------------------------------- Dunn's test


def test_dunns_test_formula_with_ties():
    groups = {k: np.array(v, float) for k, v in INSECTS.items()}
    table = ColumnTable.from_columns(INSECTS)
    result = kruskal_wallis(table, comparisons="all")
    unc = kruskal_wallis(table, comparisons="all", method="dunn_uncorrected").values["comparisons"]
    pooled = np.concatenate(list(groups.values()))
    ranks = stats.rankdata(pooled)
    N = pooled.size
    _, counts = np.unique(pooled, return_counts=True)
    # Dunn's z = |mean rank i - mean rank j| / sqrt[(N(N+1)/12)(1/Ni + 1/Nj)], with N(N+1) replaced by
    # N(N+1) - Σ(T³ - T)/(N - 1) for ties; adjusted P = P × number of comparisons, capped at 1.
    sigma2 = (N * (N + 1) - np.sum(counts**3 - counts) / (N - 1)) / 12
    split = np.split(ranks, np.cumsum([len(g) for g in groups.values()])[:-1])
    m = 15
    for row, row_u, (i, j) in zip(result.values["comparisons"], unc, itertools.combinations(range(6), 2)):
        diff = split[i].mean() - split[j].mean()
        z = diff / math.sqrt(sigma2 * (1 / len(split[i]) + 1 / len(split[j])))
        assert row["difference"] == pytest.approx(diff) and row["statistic"] == pytest.approx(z)
        assert row_u["p_adjusted"] == pytest.approx(2 * stats.norm.sf(abs(z)))
        assert row["p_adjusted"] == pytest.approx(min(1.0, m * 2 * stats.norm.sf(abs(z))))
        assert math.isnan(row["ci_low"])
    assert result.row("Multiple comparisons: Dunn's multiple comparisons test", "A vs. B").formatted.startswith("Mean rank diff.")


def test_dunns_test_after_friedman_and_fdr_after_kruskal_wallis():
    Y, table = _rm_table()
    n, k = Y.shape
    result = friedman(table, comparisons="control", control="T1")
    R = np.apply_along_axis(stats.rankdata, 1, Y).mean(axis=0)
    for row, j in zip(result.values["comparisons"], range(1, k)):
        z = (R[0] - R[j]) / math.sqrt(k * (k + 1) / (6 * n))
        assert row["statistic"] == pytest.approx(z) and row["p_adjusted"] == pytest.approx(min(1, 3 * 2 * stats.norm.sf(abs(z))))
    bh = kruskal_wallis(ColumnTable.from_columns(INSECTS), comparisons="all", method="bh").values["comparisons"]
    unc = kruskal_wallis(ColumnTable.from_columns(INSECTS), comparisons="all", method="dunn_uncorrected").values["comparisons"]
    assert [r["q"] for r in bh] == pytest.approx(adjust_p([r["p"] for r in unc], "bh")["adjusted"])
    with pytest.raises(StatisticsError, match="not available"):
        kruskal_wallis(plant(), comparisons="all", method="tukey")


# ------------------------------------------------------------------------------------------- repeated measures


def test_repeated_measures_comparisons_use_the_rm_error_term_or_the_two_columns():
    Y, table = _rm_table()
    n, k = Y.shape
    pooled = repeated_measures_anova(table, geisser_greenhouse=False, comparisons="all", method="fisher_lsd")
    v = pooled.values
    row = pooled.values["comparisons"][0]
    assert row["se"] == pytest.approx(math.sqrt(2 * v["ms_residual"] / n)) and row["df"] == (n - 1) * (k - 1)
    tukey = repeated_measures_anova(table, geisser_greenhouse=False, comparisons="all").values["comparisons"][0]
    assert tukey["p_adjusted"] == pytest.approx(stats.studentized_range.sf(abs(row["difference"]) / row["se"] * math.sqrt(2), k, (n - 1) * (k - 1)), rel=1e-6)
    # Without sphericity: each comparison is a paired t test on its two columns (SE = SD of differences/√n, df = n - 1).
    separate = repeated_measures_anova(table, comparisons="selected", pairs=[("T1", "T3")], method="fisher_lsd").values["comparisons"][0]
    t = stats.ttest_rel(Y[:, 0], Y[:, 2])
    assert separate["df"] == n - 1 and separate["statistic"] == pytest.approx(t.statistic) and separate["p"] == pytest.approx(t.pvalue)
    dunnett = repeated_measures_anova(table, geisser_greenhouse=False, comparisons="control", control="T1").values["comparisons"]
    corr = np.full((3, 3), 0.5) + 0.5 * np.eye(3)
    assert dunnett[0]["p_adjusted"] == pytest.approx(1 - max_abs_t_cdf(abs(dunnett[0]["statistic"]), corr, (n - 1) * (k - 1)), rel=1e-9)
    trend = repeated_measures_anova(table, geisser_greenhouse=False, trend=True).values
    assert trend["trend_df"] == (n - 1) * (k - 1)


# ------------------------------------------------------------------------------------------- FDR and P value stacks


@pytest.mark.parametrize("seed", range(6))
def test_fdr_q_values_match_statsmodels(seed):
    sm = pytest.importorskip("statsmodels.stats.multitest")
    rng = np.random.default_rng(seed)
    p = np.concatenate([rng.uniform(0, 1, 20), rng.uniform(0, 0.01 * seed, 3 * seed)])
    for method, ref in (("bh", "fdr_bh"), ("by", "fdr_by"), ("holm", "holm"), ("sidak", "sidak"), ("bonferroni", "bonferroni")):
        for q in (0.05, 0.1):
            mine = adjust_p(p, method, q)
            reject, corrected, _, _ = sm.multipletests(p, alpha=q, method=ref)
            assert mine["adjusted"] == pytest.approx(corrected, rel=1e-10, abs=1e-15), method
            if method in ("bh", "by", "bky"):
                assert np.array_equal(mine["significant"], reject), method


def _bky_discoveries(p, Q):
    """The two-stage thresholds written out: stage 1 BH at Q/(1+Q); stage 2 rank-i threshold
    i·Q/[(1+Q)·N_true] (from Q/[(1+Q)·N_true] up to [Q/(1+Q)]·N/N_true), step-up."""

    p = np.asarray(p, float)
    m = p.size
    order = np.argsort(p)
    ranks = np.arange(1, m + 1)

    def step_up(thresholds):
        passed = np.nonzero(p[order] <= thresholds)[0]
        out = np.zeros(m, bool)
        if passed.size:
            out[order[: passed.max() + 1]] = True
        return out

    first = step_up(ranks * Q / ((1 + Q) * m))
    r1 = first.sum()
    if r1 in (0, m):
        return first
    return step_up(ranks * Q / ((1 + Q) * (m - r1)))


@pytest.mark.parametrize("seed", range(8))
def test_two_stage_q_values_sit_on_the_discovery_border(seed):
    sm = pytest.importorskip("statsmodels.stats.multitest")
    rng = np.random.default_rng(seed)
    p = np.concatenate([rng.uniform(0, 1, rng.integers(2, 15)), rng.uniform(0, 0.02, rng.integers(0, 8))])
    q = adjust_p(p, "bky", 0.05)["adjusted"]
    for i in range(p.size):
        if q[i] < 1:
            # At Q = q_i the comparison is a discovery; at any smaller Q it is not (the definition of q).
            assert _bky_discoveries(p, q[i] * (1 + 1e-12))[i] and not _bky_discoveries(p, q[i] * (1 - 1e-7))[i]
            assert sm.multipletests(p, alpha=q[i] * (1 + 1e-12), method="fdr_tsbky")[0][i]
    for Q in (0.01, 0.05, 0.1, 0.3):
        mine = adjust_p(p, "bky", Q)
        assert np.array_equal(mine["significant"], _bky_discoveries(p, Q))
        assert np.array_equal(mine["significant"], sm.multipletests(p, alpha=Q, method="fdr_tsbky")[0])
        assert np.array_equal(mine["significant"], mine["adjusted"] <= Q)


def test_bh_and_by_q_values_use_the_two_stage_thresholds():
    # BH thresholds Q/n … Q (rank i: i·Q/n); BY thresholds Q/(n·H) … Q/H, H = 1 + 1/2 + … + 1/n.
    rng = np.random.default_rng(3)
    p = rng.uniform(0, 0.1, 12)
    n = p.size
    H = np.sum(1 / np.arange(1, n + 1))
    order = np.argsort(p)
    for method, scale in (("bh", 1.0), ("by", H)):
        q = adjust_p(p, method)["adjusted"]
        expected = np.minimum.accumulate((p[order] * n * scale / np.arange(1, n + 1))[::-1])[::-1]
        assert q[order] == pytest.approx(np.minimum(expected, 1.0))


def test_analyze_a_stack_of_p_values():
    p = [0.01, 0.04, 0.03, 0.005, 0.20, None]
    result = p_value_stack(p, "bh", alpha=0.05, labels=["a", "b", "c", "d", "e", "f"])
    table = result.values["table"]
    assert [r["rank"] for r in table] == [2, 4, 3, 1, 5, None]
    assert [r["adjusted"] for r in table[:5]] == pytest.approx([0.025, 0.05, 0.05, 0.025, 0.2])
    assert result.values["discoveries"] == 4 and result.values["m"] == 5 and any("blank" in w for w in result.warnings)
    assert result.row("Results", "a").formatted == "P = 0.0100; q = 0.0250; Discovery? Yes"
    bonf = p_value_stack(p[:5], "bonferroni")
    assert [r["adjusted"] for r in bonf.values["table"]] == pytest.approx([0.05, 0.2, 0.15, 0.025, 1.0])
    assert bonf.values["discoveries"] == 1  # adjusted P < 0.05: only d (0.025); a is exactly 0.05
    assert "Bonferroni" in bonf.methods
    with pytest.raises(StatisticsError):
        p_value_stack([0.5, 1.2])
    with pytest.raises(StatisticsError):
        p_value_stack([0.5], "nope")


def test_fdr_after_anova_uses_fishers_lsd_p_values():
    lsd = ordinary_anova(plant(), comparisons="all", method="fisher_lsd").values["comparisons"]
    bky = ordinary_anova(plant(), comparisons="all", method="bky")
    q = adjust_p([r["p"] for r in lsd], "bky", 0.05)["adjusted"]
    assert [r["q"] for r in bky.values["comparisons"]] == pytest.approx(q)
    assert all(math.isnan(r["p_adjusted"]) for r in bky.values["comparisons"])
    assert "Discovery?" in bky.row("Multiple comparisons: FDR: two-stage step-up method of Benjamini, Krieger and Yekutieli", "trt1 vs. trt2").formatted


# ------------------------------------------------------------------------------------------- compact letter display


def test_compact_letter_display_on_plantgrowth_tukey():
    # Only trt1 vs. trt2 differs: ctrl shares a letter with both.
    result = ordinary_anova(plant(), comparisons="all")
    assert compact_letter_display(result) == {"ctrl": "AB", "trt1": "A", "trt2": "B"}
    assert compact_letter_display(result, style="lower") == {"ctrl": "ab", "trt1": "a", "trt2": "b"}
    assert compact_letter_display(result, style="numbers") == {"ctrl": "1,2", "trt1": "1", "trt2": "2"}
    # Groups share a letter when their adjusted P ≥ alpha: at alpha = 0.01, trt1 vs. trt2 (P = 0.012) is not different.
    assert compact_letter_display(result, alpha=0.01) == {"ctrl": "A", "trt1": "A", "trt2": "A"}
    with pytest.raises(StatisticsError):
        compact_letter_display(result, style="roman")


def test_compact_letters_share_a_letter_exactly_when_not_different():
    rng = np.random.default_rng(9)
    names = list("ABCDEF")
    for _ in range(200):
        comps = [{"group_1": a, "group_2": b, "significant": bool(rng.random() < 0.5)} for a, b in itertools.combinations(names, 2)]
        letters = compact_letter_display(comps, names)
        for row in comps:
            share = bool(set(letters[row["group_1"]]) & set(letters[row["group_2"]]))
            assert share == (not row["significant"])
    assert compact_letter_display([{"group_1": "x", "group_2": "y", "significant": False}]) == {"x": "A", "y": "A"}
