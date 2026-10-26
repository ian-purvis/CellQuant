"""One-way ANOVA (ordinary, Welch, Brown-Forsythe, repeated measures, mixed model), Kruskal-Wallis and Friedman,
checked against R's documented results for its built-in datasets and against independent calculations."""

from __future__ import annotations

import itertools
import math

import numpy as np
import pandas as pd
import pytest
from scipy import optimize, stats

from cellquant.errors import StatisticsError
from cellquant.stats import (
    ColumnTable,
    bartlett_test,
    brown_forsythe_test,
    friedman,
    geisser_greenhouse_epsilon,
    kruskal_wallis,
    mixed_effects_anova,
    one_way,
    ordinary_anova,
    paired_t,
    repeated_measures_anova,
    welch_anova,
)
from cellquant.stats.anova import _kw_min_p, welch_anova_core

# R's `PlantGrowth` (Dobson 1983): dried weight of plants, control and two treatments, 10 each.
PLANT = {
    "ctrl": [4.17, 5.58, 5.18, 6.11, 4.50, 4.61, 5.17, 4.53, 5.33, 5.14],
    "trt1": [4.81, 4.17, 4.41, 3.59, 5.87, 3.83, 6.03, 4.89, 4.32, 4.69],
    "trt2": [6.31, 5.12, 5.54, 5.50, 5.37, 5.29, 4.92, 6.15, 5.80, 5.26],
}
# R's `InsectSprays` (Beall 1942): insect counts after six sprays, 12 each.
INSECTS = {
    "A": [10, 7, 20, 14, 14, 12, 10, 23, 17, 20, 14, 13],
    "B": [11, 17, 21, 11, 16, 14, 17, 17, 19, 21, 7, 13],
    "C": [0, 1, 7, 2, 3, 1, 2, 1, 3, 0, 1, 4],
    "D": [3, 5, 12, 6, 4, 3, 5, 5, 5, 5, 2, 4],
    "E": [3, 5, 3, 5, 3, 6, 1, 1, 3, 2, 6, 4],
    "F": [11, 9, 15, 22, 15, 16, 13, 10, 26, 26, 24, 13],
}
# R's `warpbreaks` (Tippett 1950): breaks per loom by tension (wool A rows first, then wool B).
WARP = {
    "L": [26, 30, 54, 25, 70, 52, 51, 26, 67, 27, 14, 29, 19, 29, 31, 41, 20, 44],
    "M": [18, 21, 29, 17, 12, 18, 35, 30, 36, 42, 26, 19, 16, 39, 28, 21, 39, 29],
    "H": [36, 21, 24, 18, 10, 43, 28, 15, 26, 20, 21, 24, 17, 13, 15, 15, 16, 28],
}
# R's ?friedman.test: RoundingTimes (Hollander and Wolfe 1973, p. 140): 22 players, three ways of rounding first base.
ROUNDING = np.array([
    5.40, 5.50, 5.55, 5.85, 5.70, 5.75, 5.20, 5.60, 5.50, 5.55, 5.50, 5.40, 5.90, 5.85, 5.70, 5.45, 5.55, 5.60,
    5.40, 5.40, 5.35, 5.45, 5.50, 5.35, 5.25, 5.15, 5.00, 5.85, 5.80, 5.70, 5.25, 5.20, 5.10, 5.65, 5.55, 5.45,
    5.60, 5.35, 5.45, 5.05, 5.00, 4.95, 5.50, 5.50, 5.40, 5.45, 5.55, 5.50, 5.55, 5.55, 5.35, 5.45, 5.50, 5.55,
    5.50, 5.45, 5.25, 5.65, 5.60, 5.40, 5.70, 5.65, 5.55, 6.30, 6.30, 6.25]).reshape(22, 3)


def plant():
    return ColumnTable.from_columns(PLANT, title="PlantGrowth")


def _rm_table(seed=3, n=7, sds=(0.3, 0.5, 0.8, 1.4)):
    rng = np.random.default_rng(seed)
    k = len(sds)
    Y = rng.normal(0, 1, (n, 1)) + np.linspace(0, 1, k) + rng.normal(0, sds, (n, k))
    return Y, ColumnTable.from_columns({f"T{j + 1}": Y[:, j] for j in range(k)}, paired=True)


# ------------------------------------------------------------------------------------------- ordinary ANOVA


def test_ordinary_anova_matches_r_on_plantgrowth():
    # R: summary(aov(weight ~ group, data = PlantGrowth)):
    #   group      Df 2  Sum Sq 3.766  Mean Sq 1.8832  F value 4.846  Pr(>F) 0.0159
    #   Residuals  Df 27 Sum Sq 10.492 Mean Sq 0.3886
    v = ordinary_anova(plant()).values
    assert (v["dfn"], v["dfd"]) == (2, 27)
    assert v["ss_between"] == pytest.approx(3.766, abs=5e-4) and v["ss_within"] == pytest.approx(10.492, abs=5e-4)
    assert v["ms_between"] == pytest.approx(1.8832, abs=5e-5) and v["ms_within"] == pytest.approx(0.3886, abs=5e-5)
    assert v["f"] == pytest.approx(4.846, abs=5e-4) and v["p"] == pytest.approx(0.01591, abs=5e-6)
    assert v["r_squared"] == pytest.approx(v["ss_between"] / v["ss_total"]) and v["r_squared"] == pytest.approx(0.2641, abs=5e-5)
    assert v["means"] == pytest.approx([5.032, 4.661, 5.526])


def test_ordinary_anova_matches_r_on_insectsprays_and_warpbreaks():
    # R: summary(aov(count ~ spray, data = InsectSprays)): spray Df 5, Sum Sq 2669, Mean Sq 533.8, F 34.7; Residuals 66, 1015, 15.4.
    v = ordinary_anova(ColumnTable.from_columns(INSECTS)).values
    assert (v["dfn"], v["dfd"]) == (5, 66) and v["f"] == pytest.approx(34.70, abs=5e-3)
    assert v["ss_between"] == pytest.approx(2668.8, abs=0.05) and v["ss_within"] == pytest.approx(1015.2, abs=0.05)
    # R: summary(aov(breaks ~ tension, data = warpbreaks)): tension Df 2, Sum Sq 2034, Mean Sq 1017.1, F 7.206, Pr(>F) 0.00175.
    v = ordinary_anova(ColumnTable.from_columns(WARP)).values
    assert v["f"] == pytest.approx(7.206, abs=5e-4) and v["p"] == pytest.approx(0.00175, abs=5e-6) and v["ms_between"] == pytest.approx(1017.1, abs=0.05)


def test_ordinary_anova_result_sheet_and_dispatch():
    result = ordinary_anova(plant())
    assert result.row("ANOVA summary", "P value").formatted == "0.0159"
    assert result.row("ANOVA summary", "P value summary").formatted == "*"
    assert result.row("ANOVA summary", "Significant diff. among means (P < 0.05)?").formatted == "Yes"
    assert result.row("ANOVA table", "Total").value["df"] == 29
    assert result.row("Data summary", "Number of values (total)").value == 30
    assert "Ordinary one-way ANOVA" in result.methods
    assert one_way(plant(), "ordinary").values["f"] == result.values["f"]
    frame = result.to_frame()
    assert {"section", "label", "value", "formatted"} == set(frame.columns)
    # A subset of groups, and blanks.
    sub = ordinary_anova(ColumnTable.from_columns({**PLANT, "trt3": [1.0, None]}), groups=["ctrl", "trt1", "trt2"])
    assert sub.values["f"] == pytest.approx(result.values["f"])
    with pytest.raises(StatisticsError):
        one_way(plant(), "nope")
    with pytest.raises(StatisticsError, match="at least 2 groups"):
        ordinary_anova(ColumnTable.from_columns({"a": [1, 2, 3]}))


def test_equal_variance_tests_match_r():
    samples = list(PLANT.values())
    # R: car::leveneTest(weight ~ group, data = PlantGrowth) (center = median, i.e. Brown-Forsythe): F = 1.1192, Df 2, 27, P = 0.3412.
    bf = brown_forsythe_test(samples)
    assert bf["f"] == pytest.approx(1.1192, abs=5e-5) and (bf["dfn"], bf["dfd"]) == (2, 27) and bf["p"] == pytest.approx(0.3412, abs=5e-5)
    # R: bartlett.test(weight ~ group, data = PlantGrowth): Bartlett's K-squared = 2.8786, df = 2, p-value = 0.2371.
    b = bartlett_test(samples)
    assert b["statistic"] == pytest.approx(2.8786, abs=5e-5) and b["p"] == pytest.approx(0.2371, abs=5e-5)
    # R's ?bartlett.test example: bartlett.test(count ~ spray, data = InsectSprays): K-squared = 25.96, df = 5, p-value = 9.085e-05.
    b = bartlett_test(list(INSECTS.values()))
    assert b["statistic"] == pytest.approx(25.96, abs=5e-3) and b["df"] == 5 and b["p"] == pytest.approx(9.085e-05, abs=5e-9)
    # And against scipy's independent implementations on random unequal groups.
    rng = np.random.default_rng(0)
    groups = [rng.normal(0, s, n) for s, n in ((1, 5), (2, 8), (0.5, 11), (1.5, 6))]
    assert brown_forsythe_test(groups)["p"] == pytest.approx(stats.levene(*groups, center="median").pvalue, rel=1e-10)
    assert bartlett_test(groups)["p"] == pytest.approx(stats.bartlett(*groups).pvalue, rel=1e-10)


def test_unequal_sd_warning():
    rng = np.random.default_rng(1)
    table = ColumnTable.from_columns({"a": rng.normal(0, 1, 15), "b": rng.normal(0, 6, 15), "c": rng.normal(0, 12, 15)})
    assert any("Welch" in w for w in ordinary_anova(table).warnings)


# ------------------------------------------------------------------------------------------- Welch and Brown-Forsythe ANOVA


def test_welch_anova_matches_r_oneway_test():
    # R: oneway.test(weight ~ group, data = PlantGrowth): F = 5.181, num df = 2.000, denom df = 17.128, p-value = 0.01739.
    result = welch_anova(plant())
    v = result.values
    assert v["welch_f"] == pytest.approx(5.181, abs=5e-4) and v["welch_dfn"] == 2
    assert v["welch_dfd"] == pytest.approx(17.128, abs=5e-4) and v["welch_p"] == pytest.approx(0.01739, abs=5e-6)
    assert v["p"] == v["welch_p"]
    assert result.row("Welch's ANOVA test", "W (DFn, DFd)").formatted == "5.181 (2, 17.13)"


def _brown_forsythe_anova(samples):
    """Brown and Forsythe (1974) F*, written out directly."""

    N = sum(len(s) for s in samples)
    grand = np.mean(np.concatenate(samples))
    num = sum(len(s) * (np.mean(s) - grand) ** 2 for s in samples)
    parts = [(1 - len(s) / N) * np.var(s, ddof=1) for s in samples]
    den = sum(parts)
    df2 = 1 / sum((p / den) ** 2 / (len(s) - 1) for p, s in zip(parts, samples))
    return num / den, df2


def test_brown_forsythe_anova_formula_and_equal_n_identity():
    # With equal n, F* equals the ordinary F (both numerator and denominator reduce to (k - 1)·MS terms).
    v = welch_anova(plant()).values
    assert v["bf_anova_f"] == pytest.approx(4.846, abs=5e-4) and v["bf_anova_dfn"] == 2
    rng = np.random.default_rng(2)
    groups = [rng.normal(m, s, n) for m, s, n in ((0, 1, 4), (1, 3, 9), (0.5, 0.5, 6))]
    f, df2 = _brown_forsythe_anova(groups)
    core = welch_anova_core(groups)
    assert core["bf_anova_f"] == pytest.approx(f) and core["bf_anova_dfd"] == pytest.approx(df2)
    assert core["bf_anova_p"] == pytest.approx(stats.f.sf(f, 2, df2))
    with pytest.raises(StatisticsError, match="at least 2"):
        welch_anova(ColumnTable.from_columns({"a": [1, 2], "b": [3], "c": [4, 5]}))


def test_welch_anova_holds_its_size_with_unequal_sds_where_ordinary_anova_does_not():
    # Simulation under the null (equal means) with the largest SD in the smallest group: Welch's test stays near
    # alpha, ordinary ANOVA does not (a known property; Welch 1951). 2000 data sets, fixed seed.
    rng = np.random.default_rng(7)
    sizes, sds = (4, 8, 16), (4.0, 2.0, 1.0)
    welch_hits = ordinary_hits = 0
    for _ in range(2000):
        groups = [rng.normal(0, s, n) for n, s in zip(sizes, sds)]
        welch_hits += welch_anova_core(groups)["welch_p"] < 0.05
        ordinary_hits += stats.f_oneway(*groups).pvalue < 0.05
    assert 0.03 < welch_hits / 2000 < 0.07
    assert ordinary_hits / 2000 > 0.12


# ------------------------------------------------------------------------------------------- repeated measures


def _epsilon_by_contrasts(Y):
    """Greenhouse-Geisser epsilon from orthonormal contrasts M: Σ = M S M', ε = tr(Σ)² / ((k - 1) tr(Σ²))."""

    k = Y.shape[1]
    q, _ = np.linalg.qr(np.column_stack([np.ones(k), np.eye(k)[:, : k - 1]]))
    M = q[:, 1:].T
    sigma = M @ np.cov(Y, rowvar=False) @ M.T
    return np.trace(sigma) ** 2 / ((k - 1) * np.trace(sigma @ sigma))


def test_repeated_measures_anova_matches_statsmodels_and_epsilon_matches_contrast_formula():
    statsmodels = pytest.importorskip("statsmodels.stats.anova")
    Y, table = _rm_table()
    n, k = Y.shape
    long = pd.DataFrame({"y": Y.ravel(), "s": np.repeat(np.arange(n), k), "g": np.tile(np.arange(k), n)})
    ref = statsmodels.AnovaRM(long, "y", "s", within=["g"]).fit().anova_table.iloc[0]
    plain = repeated_measures_anova(table, geisser_greenhouse=False).values
    assert plain["f"] == pytest.approx(ref["F Value"], rel=1e-10) and plain["p"] == pytest.approx(ref["Pr > F"], rel=1e-8)
    assert (plain["dfn"], plain["dfd"]) == (k - 1, (n - 1) * (k - 1))
    eps = geisser_greenhouse_epsilon(Y)
    assert eps == pytest.approx(_epsilon_by_contrasts(Y), rel=1e-10)
    assert 1 / (k - 1) <= eps <= 1
    gg = repeated_measures_anova(table).values
    assert gg["epsilon"] == pytest.approx(eps) and gg["dfn"] == pytest.approx(eps * (k - 1)) and gg["dfd"] == pytest.approx(eps * (n - 1) * (k - 1))
    assert gg["p"] == pytest.approx(stats.f.sf(plain["f"], eps * (k - 1), eps * (n - 1) * (k - 1)))
    assert gg["p"] > plain["p"] and gg["p_uncorrected"] == pytest.approx(plain["p"])
    # Sums of squares add up; matching F = MS_subjects / MS_residual.
    assert gg["ss_treatment"] + gg["ss_subjects"] + gg["ss_residual"] == pytest.approx(gg["ss_total"])
    assert gg["f_subjects"] == pytest.approx(gg["ms_subjects"] / gg["ms_residual"])


def test_repeated_measures_with_two_columns_is_the_paired_t_test():
    Y, _ = _rm_table(seed=4, n=9, sds=(0.4, 0.9))
    table = ColumnTable.from_columns({"A": Y[:, 0], "B": Y[:, 1]}, paired=True)
    rm = repeated_measures_anova(table).values
    t = paired_t(Y[:, 0], Y[:, 1]).values
    assert rm["epsilon"] == 1.0
    assert rm["f"] == pytest.approx(t["t"] ** 2) and rm["p"] == pytest.approx(t["p"])
    assert rm["r_squared"] == pytest.approx(t["r_squared"])
    # The two R² values: treatment SS_trt/(SS_trt + SS_resid), matching SS_subjects/SS_total.
    assert rm["r_squared"] == pytest.approx(rm["ss_treatment"] / (rm["ss_treatment"] + rm["ss_residual"]))
    assert rm["r_squared_matching"] == pytest.approx(rm["ss_subjects"] / rm["ss_total"])


def test_geisser_greenhouse_epsilon_is_one_under_compound_symmetry_and_bounded():
    # Exact compound symmetry in the sample: subject effect + treatment effect + a residual matrix whose double-centred
    # covariance is proportional to the centring matrix gives epsilon = 1.
    k = 4
    base = np.array([[1, -1, 0, 0], [0, 1, -1, 0], [0, 0, 1, -1], [-1, 0, 0, 1], [1, 0, -1, 0], [0, 1, 0, -1]], float)
    Y = np.vstack([base, -base]) + np.arange(k) + np.linspace(0, 3, 12)[:, None]
    assert geisser_greenhouse_epsilon(Y) == pytest.approx(1.0)
    rng = np.random.default_rng(5)
    for _ in range(20):
        Y = rng.normal(size=(6, 5)) * rng.uniform(0.1, 5, 5)
        assert 1 / 4 - 1e-12 <= geisser_greenhouse_epsilon(Y) <= 1 + 1e-12


def test_repeated_measures_drops_or_models_missing_values():
    Y, _ = _rm_table()
    Y[2, 1] = np.nan
    table = ColumnTable.from_columns({f"T{j + 1}": Y[:, j] for j in range(Y.shape[1])}, paired=True)
    mixed = repeated_measures_anova(table)
    assert mixed.analysis.startswith("Mixed-effects") and "mixed-effects model" in mixed.warnings[0]
    dropped = repeated_measures_anova(table, missing="exclude")
    assert dropped.values["n_subjects"] == Y.shape[0] - 1 and any("left out" in w for w in dropped.warnings)
    assert any("not marked as paired" in w for w in repeated_measures_anova(ColumnTable.from_columns({"a": [1, 2, 3], "b": [2, 4, 5], "c": [3, 3, 8]})).warnings)


# ------------------------------------------------------------------------------------------- mixed-effects model


def test_mixed_model_without_blanks_reproduces_repeated_measures_anova():
    pytest.importorskip("statsmodels")
    Y, table = _rm_table()
    rm = repeated_measures_anova(table, geisser_greenhouse=False, comparisons="all").values
    mixed = mixed_effects_anova(table, geisser_greenhouse=False, comparisons="all").values
    assert mixed["dfd"] == rm["dfd"] and mixed["dfn"] == rm["dfn"]
    assert mixed["f"] == pytest.approx(rm["f"], rel=1e-4) and mixed["p"] == pytest.approx(rm["p"], rel=1e-3)
    assert mixed["means"] == pytest.approx(rm["means"], rel=1e-8)
    assert mixed["var_residual"] == pytest.approx(rm["ms_residual"], rel=1e-4)
    # Random-intercept variance from the ANOVA mean squares (balanced data): (MS_subjects - MS_residual) / k.
    assert mixed["var_subject"] == pytest.approx((rm["ms_subjects"] - rm["ms_residual"]) / Y.shape[1], rel=1e-3)
    for a, b in zip(mixed["comparisons"], rm["comparisons"]):
        assert a["p_adjusted"] == pytest.approx(b["p_adjusted"], rel=1e-3, abs=1e-6)


def _reml_negloglik(log_ratio, y, X, subjects):
    """Profiled REML -log likelihood of y = Xβ + subject effect + error, as a function of log(σ²_subject/σ²_error)."""

    ratio = math.exp(log_ratio)
    blocks = []
    for s in np.unique(subjects):
        m = int(np.sum(subjects == s))
        blocks.append(np.eye(m) + ratio * np.ones((m, m)))
    order = np.argsort(subjects, kind="stable")
    y, X = y[order], X[order]
    V = np.zeros((y.size, y.size))
    start = 0
    for b in blocks:
        V[start:start + len(b), start:start + len(b)] = b
        start += len(b)
    Vi = np.linalg.inv(V)
    XtViX = X.T @ Vi @ X
    beta = np.linalg.solve(XtViX, X.T @ Vi @ y)
    r = y - X @ beta
    N, p = X.shape
    sigma2 = r @ Vi @ r / (N - p)
    value = 0.5 * ((N - p) * math.log(sigma2) + np.linalg.slogdet(V)[1] + np.linalg.slogdet(XtViX)[1])
    return value, beta, sigma2, np.linalg.inv(XtViX) * sigma2


def test_mixed_model_with_blanks_matches_a_direct_reml_fit():
    pytest.importorskip("statsmodels")
    Y, _ = _rm_table(seed=11, n=8)
    Y[1, 2] = Y[4, 0] = Y[6, 3] = np.nan
    table = ColumnTable.from_columns({f"T{j + 1}": Y[:, j] for j in range(Y.shape[1])}, paired=True)
    result = mixed_effects_anova(table, geisser_greenhouse=False, comparisons="control", control="T1")
    v = result.values
    rows, cols = np.nonzero(np.isfinite(Y))
    y, X = Y[rows, cols], np.eye(Y.shape[1])[cols]
    best = optimize.minimize_scalar(lambda a: _reml_negloglik(a, y, X, rows)[0], bounds=(-12, 6), method="bounded", options={"xatol": 1e-10})
    _, beta, sigma2, cov = _reml_negloglik(best.x, y, X, rows)
    assert v["means"] == pytest.approx(beta, rel=1e-4)
    assert v["var_residual"] == pytest.approx(sigma2, rel=1e-3) and v["var_subject"] == pytest.approx(math.exp(best.x) * sigma2, rel=1e-3)
    k = Y.shape[1]
    L = np.column_stack([np.ones(k - 1), -np.eye(k - 1)])
    f = (L @ beta) @ np.linalg.solve(L @ cov @ L.T, L @ beta) / (k - 1)
    dfd = y.size - Y.shape[0] - (k - 1)
    assert v["dfd"] == dfd and v["f"] == pytest.approx(f, rel=1e-3) and v["p"] == pytest.approx(stats.f.sf(f, k - 1, dfd), rel=1e-2)
    assert v["n_values"] == y.size and result.row("Data summary", "Number of missing values").value == 3
    assert [r["reference"] for r in v["comparisons"]] == ["T1"] * 3
    assert any("Mixed-effects model" in w for w in result.warnings)
    # Geisser-Greenhouse (the default): epsilon from pairwise-complete column covariances scales both df.
    import pandas as pd
    S = pd.DataFrame(Y).cov(min_periods=2).to_numpy()
    C = np.eye(k) - np.ones((k, k)) / k
    eps = np.trace(C @ S @ C) ** 2 / ((k - 1) * np.trace(C @ S @ C @ C @ S @ C))
    gg = mixed_effects_anova(table).values
    assert gg["epsilon"] == pytest.approx(eps) and gg["dfn"] == pytest.approx(eps * (k - 1)) and gg["dfd"] == pytest.approx(eps * dfd)
    assert gg["p"] == pytest.approx(stats.f.sf(gg["f"], eps * (k - 1), eps * dfd)) and gg["p_uncorrected"] == pytest.approx(v["p"])


def test_mixed_model_geisser_greenhouse_without_blanks_equals_rm_anova():
    pytest.importorskip("statsmodels")
    _, table = _rm_table()
    rm = repeated_measures_anova(table).values
    mixed = mixed_effects_anova(table).values
    assert mixed["epsilon"] == pytest.approx(rm["epsilon"], rel=1e-10)
    assert mixed["dfd"] == pytest.approx(rm["dfd"]) and mixed["p"] == pytest.approx(rm["p"], rel=1e-3)


# ------------------------------------------------------------------------------------------- nonparametric


def test_kruskal_wallis_matches_r():
    # R: kruskal.test(weight ~ group, data = PlantGrowth): Kruskal-Wallis chi-squared = 7.9882, df = 2, p-value = 0.01842.
    result = kruskal_wallis(plant())
    v = result.values
    assert v["h"] == pytest.approx(7.9882, abs=5e-5) and v["df"] == 2 and v["p"] == pytest.approx(0.01842, abs=5e-6)
    assert result.row("Kruskal-Wallis test", "Exact or approximate P value?").formatted.startswith("Approximate (chi-square")
    # R: kruskal.test(count ~ spray, data = InsectSprays): chi-squared = 54.691, df = 5, p-value = 1.511e-10 (many ties).
    v = kruskal_wallis(ColumnTable.from_columns(INSECTS)).values
    assert v["h"] == pytest.approx(54.691, abs=5e-4) and v["p"] == pytest.approx(1.511e-10, rel=5e-4)
    assert v["h"] > v["h_uncorrected"]
    # R's ?kruskal.test: Hollander and Wolfe (1973) p. 116, mucociliary efficiency: chi-squared = 0.77143, df = 2, p-value = 0.68.
    hw = ColumnTable.from_columns({"normal": [2.9, 3.0, 2.5, 2.6, 3.2], "obstructive": [3.8, 2.7, 4.0, 2.4], "asbestosis": [2.8, 3.4, 3.7, 2.2, 2.0]})
    result = kruskal_wallis(hw, exact=False)
    assert result.values["h"] == pytest.approx(0.77143, abs=5e-6) and result.values["p"] == pytest.approx(0.68, abs=5e-3)
    assert any("fewer than 5" in w for w in result.warnings)
    # By default these sizes (126 126 splits) get an exact P, which R's chi-square value does not give.
    exact = kruskal_wallis(hw)
    assert exact.values["exact"] and exact.values["p_chi2"] == pytest.approx(0.68, abs=5e-3)
    assert exact.row("Kruskal-Wallis test", "Exact or approximate P value?").formatted == "Exact"
    assert not kruskal_wallis(plant()).values["exact"]
    assert kruskal_wallis(plant()).values["p"] == pytest.approx(stats.kruskal(*PLANT.values()).pvalue)


def _brute_kw(samples):
    """Every labelled split of the pooled midranks into groups of the observed sizes."""

    r = stats.rankdata(np.concatenate(samples))
    sizes = [len(x) for x in samples]
    starts = np.cumsum([0] + sizes[:-1])
    observed = sum(r[a:a + n].sum() ** 2 / n for a, n in zip(starts, sizes))
    values = []

    def split(avail, level, acc):
        if level == len(sizes) - 1:
            values.append(acc + r[avail].sum() ** 2 / sizes[level])
            return
        for chosen in itertools.combinations(avail, sizes[level]):
            split([a for a in avail if a not in chosen], level + 1, acc + r[list(chosen)].sum() ** 2 / sizes[level])

    split(list(range(r.size)), 0, 0.0)
    values = np.array(values)
    return np.mean(values >= observed - 1e-9)


@pytest.mark.parametrize("samples", [
    [[1, 2, 2], [3, 3], [4, 5, 2]],          # ties, unequal sizes
    [[1, 1], [2, 3], [3, 4]],                # ties, equal sizes (interchangeable groups)
    [[1, 2, 3], [2, 3, 4], [5, 5, 6], [1, 9, 4]],
    [[0.3, 1.9, 2.2, 5.0], [2.0, 2.0, 7.1], [6.0, 1.1, 3.3, 3.3]],
])
def test_exact_kruskal_wallis_matches_brute_force_permutation(samples):
    samples = [np.array(x, float) for x in samples]
    table = ColumnTable.from_columns({f"G{i}": x for i, x in enumerate(samples)})
    result = kruskal_wallis(table)
    assert result.values["exact"] and result.values["p"] == pytest.approx(_brute_kw(samples), abs=1e-12)


def _brute_friedman(Y):
    n, k = Y.shape
    R = np.apply_along_axis(stats.rankdata, 1, Y)
    observed = np.sum(R.sum(axis=0) ** 2)
    hits = total = 0
    for orders in itertools.product(list(itertools.permutations(range(k))), repeat=n):
        sums = sum(R[i][list(o)] for i, o in enumerate(orders))
        hits += np.sum(sums**2) >= observed - 1e-9
        total += 1
    return hits / total


@pytest.mark.parametrize("Y", [
    [[1, 2, 3], [2, 2, 5], [3, 1, 4], [5, 6, 6]],
    [[1, 2, 3, 4], [2, 2, 5, 1], [3, 1, 4, 4]],
    [[5.1, 4.0, 3.2], [6.0, 4.4, 4.1], [5.5, 5.6, 3.9], [4.9, 4.2, 4.0], [5.0, 3.1, 3.3]],
])
def test_exact_friedman_matches_brute_force_permutation(Y):
    Y = np.array(Y, float)
    result = friedman(ColumnTable.from_columns({f"T{j}": Y[:, j] for j in range(Y.shape[1])}, paired=True))
    assert result.values["exact"] and result.values["p"] == pytest.approx(_brute_friedman(Y), abs=1e-12)


def test_friedman_exact_cutoff_is_k_factorial_to_the_n():
    # Exact unless (k!)^n > 10⁹: 3 treatments are exact up to 11 subjects (6^11 ≈ 3.6e8), not at 12 (2.2e9).
    rng = np.random.default_rng(0)
    for n, exact in ((11, True), (12, False)):
        Y = rng.normal(size=(n, 3))
        assert friedman(ColumnTable.from_columns({f"T{j}": Y[:, j] for j in range(3)}, paired=True)).values["exact"] is exact


def test_kruskal_wallis_smallest_possible_p_matches_full_enumeration():
    # Every way of splitting ranks 1..6 into three labelled groups of 2: the smallest P is the share of splits with the largest H.
    sizes = (2, 2, 2)
    ranks = np.arange(1, 7)
    hs = []
    for first in itertools.combinations(range(6), 2):
        rest = [i for i in range(6) if i not in first]
        for second in itertools.combinations(rest, 2):
            third = [i for i in rest if i not in second]
            hs.append(sum(ranks[list(g)].sum() ** 2 / 2 for g in (first, second, third)))
    hs = np.array(hs)
    assert hs.size == 90
    assert _kw_min_p(list(sizes)) == pytest.approx(np.mean(hs >= hs.max() - 1e-9))
    result = kruskal_wallis(ColumnTable.from_columns({"a": [1, 2], "b": [3, 4], "c": [5, 6]}), comparisons="all")
    assert result.values["min_exact_p"] == pytest.approx(6 / 90)
    assert any("cannot find a significant difference" in w for w in result.warnings)
    assert any("cannot reach P < 0.05 by Dunn's test" in w for w in result.warnings)


def test_friedman_matches_r_rounding_times():
    # R's ?friedman.test: friedman.test(RoundingTimes): Friedman chi-squared = 11.143, df = 2, p-value = 0.003805.
    table = ColumnTable.from_columns({"Round Out": ROUNDING[:, 0], "Narrow Angle": ROUNDING[:, 1], "Wide Angle": ROUNDING[:, 2]}, paired=True)
    result = friedman(table)
    assert result.values["statistic"] == pytest.approx(11.143, abs=5e-4) and result.values["p"] == pytest.approx(0.003805, abs=5e-7)
    assert result.values["statistic"] == pytest.approx(stats.friedmanchisquare(*ROUNDING.T).statistic)
    assert result.row("Friedman test", "Exact or approximate P value?").formatted.startswith("Approximate")
    assert result.values["n_subjects"] == 22


def test_friedman_small_samples_and_blanks():
    # Two subjects, three treatments: the exact P can never go below (3!)^(1-2) = 1/6.
    table = ColumnTable.from_columns({"a": [1, 2], "b": [3, 4], "c": [5, 6]}, paired=True)
    result = friedman(table)
    assert result.values["min_exact_p"] == pytest.approx(1 / 6)
    assert any("cannot find a significant difference" in w for w in result.warnings)
    table = ColumnTable.from_columns({"a": [1, 2, 3, 1], "b": [3, 4, None, 2], "c": [5, 6, 7, 3]}, paired=True)
    result = friedman(table)
    assert result.values["n_subjects"] == 3 and any("blank left out" in w for w in result.warnings)


def test_technical_replicate_wording_is_generic():
    rng = np.random.default_rng(0)
    table = ColumnTable.from_columns({"A": rng.normal(size=40), "B": rng.normal(size=40), "C": rng.normal(size=40)})
    text = " ".join(ordinary_anova(table).warnings)
    assert "technical replicates (n" in text and "biological replicate (N)" in text
