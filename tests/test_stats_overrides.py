"""Advanced overrides of the statistics engine (df, hypothesised difference, epsilon, SD, number of comparisons): defaults leave the
results unchanged, an override changes P as scipy says it should, and every change is recorded in "Changed from the default"."""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import stats

from cellquant.errors import StatisticsError
from cellquant.stats import (
    ColumnTable,
    GroupedTable,
    XYTable,
    compare,
    friedman,
    kruskal_wallis,
    linear_regression,
    one_way,
    ordinary_anova,
    paired_t,
    repeated_measures_anova,
    two_way_anova,
    welch_anova,
    xy_correlation,
)
from cellquant.stats.multcomp import adjust_p

A = [4.17, 5.58, 5.18, 6.11, 4.50, 4.61, 5.17, 4.53, 5.33, 5.14]
B = [4.81, 4.17, 4.41, 3.59, 5.87, 3.83, 6.03, 4.89, 4.32, 4.69]
C = [6.31, 5.12, 5.54, 5.50, 5.37, 5.29, 4.92, 6.15, 5.80, 5.26]
TITLE = "Changed from the default"


def table(paired: bool = False, k: int = 3, blank: bool = False) -> ColumnTable:
    columns = {"A": list(A), "B": list(B), "C": list(C)}
    if blank:
        columns["B"][2] = float("nan")
    return ColumnTable.from_columns(dict(list(columns.items())[:k]), "demo", paired=paired)


def recorded(result, label: str, value: str | None = None, default: str | None = None) -> None:
    """The change is shown in the section, in values["overrides"], as a warning and in the methods sentence."""

    section = next(s for s in result.sections if s.title == TITLE)
    row = next(r for r in section.rows if r.label == label)
    entry = result.values["overrides"][label]
    assert row.formatted == f"{entry['value']} (default {entry['default']})"
    if value is not None:
        assert entry["value"] == value
    if default is not None:
        assert entry["default"] == default
    text = f"{label} = {entry['value']} (computed {entry['default']})"
    assert any(w == f"Set by hand: {text}. Report this with the result." for w in result.warnings)
    assert result.methods.endswith(f" Changed from the default: {text}.") or f" Changed from the default: {text}." in result.methods


def untouched(result) -> None:
    assert not any(s.title == TITLE for s in result.sections)
    assert "overrides" not in result.values
    assert not any(w.startswith("Set by hand") for w in result.warnings)
    assert "Changed from the default" not in result.methods


def same(x, y) -> None:
    assert x.to_frame().equals(y.to_frame())
    assert x.methods == y.methods and x.warnings == y.warnings


# --- t tests -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("test", ["unpaired_t", "welch_t", "lognormal_t", "paired_t", "ratio_paired_t"])
def test_t_defaults_unchanged(test):
    t = table(paired=True, k=2)
    base = compare(t, test)
    explicit = compare(t, test, df=None)
    same(base, explicit)
    untouched(explicit)
    if test in ("unpaired_t", "welch_t", "paired_t"):
        same(base, compare(t, test, difference=0.0))


def test_unpaired_df_override():
    t = table(k=2)
    base = compare(t, "unpaired_t")
    r = compare(t, "unpaired_t", df=4)
    tv = base.values["t"]
    assert r.values["p"] == pytest.approx(2 * stats.t.sf(abs(tv), 4))
    assert r.values["df"] == 4 and r.values["t"] == tv
    half = stats.t.ppf(0.975, 4) * base.values["se"]
    assert r.values["ci_high"] == pytest.approx(base.values["difference"] + half)
    assert r.values["r_squared"] == pytest.approx(tv**2 / (tv**2 + 4))
    recorded(r, "df", "4", "18")
    assert not any("larger" in w for w in r.warnings)


def test_larger_df_warns():
    r = compare(table(k=2), "unpaired_t", df=500)
    recorded(r, "df")
    assert any("larger df than computed" in w and "needs a reason" in w for w in r.warnings)


def test_welch_df_and_conservative():
    t = table(k=2)
    base = compare(t, "welch_t")
    r = compare(t, "welch_t", df="conservative")
    assert r.values["df"] == 9
    assert r.values["p"] == pytest.approx(2 * stats.t.sf(abs(base.values["t"]), 9))
    recorded(r, "df", "9 (conservative)")
    r = compare(t, "welch_t", df=base.values["df"])  # the same df is not a change
    untouched(r)


def test_difference_shifts_t():
    t = table(k=2)
    base = compare(t, "unpaired_t")
    r = compare(t, "unpaired_t", difference=0.5)
    expected = (base.values["difference"] - 0.5) / base.values["se"]
    assert r.values["t"] == pytest.approx(expected)
    assert r.values["p"] == pytest.approx(2 * stats.t.sf(abs(expected), 18))
    assert (r.values["ci_low"], r.values["ci_high"]) == (base.values["ci_low"], base.values["ci_high"])
    recorded(r, "Hypothesised difference", "0.5", "0")
    # one-tailed too
    one = compare(t, "unpaired_t", difference=0.5, alternative="greater")
    assert one.values["p"] == pytest.approx(stats.t.sf(expected, 18))


def test_lognormal_df():
    t = table(k=2)
    base = compare(t, "lognormal_t")
    r = compare(t, "lognormal_t", df=5)
    assert r.values["p"] == pytest.approx(2 * stats.t.sf(abs(base.values["t"]), 5))
    assert r.values["ratio"] == base.values["ratio"] and r.values["ratio_ci_high"] != base.values["ratio_ci_high"]
    recorded(r, "df", "5", "18")


def test_paired_df_and_difference():
    t = table(paired=True, k=2)
    base = compare(t, "paired_t")
    r = compare(t, "paired_t", df=3)
    assert r.values["p"] == pytest.approx(2 * stats.t.sf(abs(base.values["t"]), 3))
    recorded(r, "df", "3", "9")
    r = compare(t, "paired_t", difference=-0.2)
    expected = (base.values["difference"] + 0.2) / base.values["se"]
    assert r.values["t"] == pytest.approx(expected)
    assert r.values["p"] == pytest.approx(2 * stats.t.sf(abs(expected), 9))
    recorded(r, "Hypothesised difference", "-0.2", "0")
    both = compare(t, "paired_t", df=3, difference=-0.2)
    assert len(both.values["overrides"]) == 2 and both.values["p"] == pytest.approx(2 * stats.t.sf(abs(expected), 3))
    untouched(compare(t, "paired_t", df="conservative"))  # equals the computed n - 1


def test_ratio_paired_df():
    t = table(paired=True, k=2)
    base = compare(t, "ratio_paired_t")
    r = compare(t, "ratio_paired_t", df=4)
    assert r.values["p"] == pytest.approx(2 * stats.t.sf(abs(base.values["t"]), 4))
    recorded(r, "df", "4", "9")


@pytest.mark.parametrize("bad", [0, -2, float("nan"), float("inf"), "soon", True])
def test_t_df_validation(bad):
    with pytest.raises(StatisticsError, match="df"):
        compare(table(k=2), "unpaired_t", df=bad)
    with pytest.raises(StatisticsError, match="df"):
        compare(table(paired=True, k=2), "paired_t", df=bad)


def test_difference_validation():
    with pytest.raises(StatisticsError, match="difference"):
        compare(table(k=2), "unpaired_t", difference=float("nan"))


# --- adjusting P values / multiple comparisons -----------------------------------------------------------------------


def test_adjust_p_m_total():
    p = np.array([0.001, 0.01, 0.03])
    assert adjust_p(p, "bonferroni", m_total=10)["adjusted"] == pytest.approx(np.minimum(1, p * 10))
    assert adjust_p(p, "sidak", m_total=10)["adjusted"] == pytest.approx(1 - (1 - p) ** 10)
    assert adjust_p(p, "holm", m_total=5)["adjusted"] == pytest.approx(np.maximum.accumulate(np.minimum(1, p * np.array([5, 4, 3]))))
    assert adjust_p(p, "bonferroni")["adjusted"] == pytest.approx(adjust_p(p, "bonferroni", m_total=3)["adjusted"])
    with pytest.raises(StatisticsError, match="applies only"):
        adjust_p(p, "bh", m_total=5)


def comparison_p(result, i=0):
    return result.values["comparisons"][i]


def test_bonferroni_m():
    t = table()
    base = ordinary_anova(t, comparisons="all", method="bonferroni")
    r = ordinary_anova(t, comparisons="all", method="bonferroni", m=10)
    for a, b in zip(base.values["comparisons"], r.values["comparisons"]):
        assert b["p_adjusted"] == pytest.approx(min(1.0, a["p"] * 10)) and b["p"] == a["p"]
        assert b["ci_high"] - b["difference"] > a["ci_high"] - a["difference"]
    assert r.values["comparisons"][0]["ci_high"] - r.values["comparisons"][0]["difference"] == pytest.approx(
        stats.t.ppf(1 - 0.05 / 20, 27) * r.values["comparisons"][0]["se"])
    recorded(r, "Number of comparisons (m)", "10", "3")
    untouched(ordinary_anova(t, comparisons="all", method="bonferroni", m=None))


def test_sidak_and_holm_sidak_m():
    t = table()
    base = ordinary_anova(t, comparisons="all", method="sidak")
    r = ordinary_anova(t, comparisons="all", method="sidak", m=6)
    for a, b in zip(base.values["comparisons"], r.values["comparisons"]):
        assert b["p_adjusted"] == pytest.approx(1 - (1 - a["p"]) ** 6)
    base = ordinary_anova(t, comparisons="all", method="holm_sidak")
    r = ordinary_anova(t, comparisons="all", method="holm_sidak", m=6)
    ps = np.array([c["p"] for c in base.values["comparisons"]])
    order = np.argsort(ps)
    step = np.maximum.accumulate(1 - (1 - ps[order]) ** (6 - np.arange(3)))
    expected = np.empty(3)
    expected[order] = step
    assert [c["p_adjusted"] for c in r.values["comparisons"]] == pytest.approx(expected.tolist())


def test_dunn_m():
    t = table()
    for func in (kruskal_wallis, friedman):
        tt = table(paired=True)
        base = func(tt, comparisons="all")
        r = func(tt, comparisons="all", m=9)
        for a, b in zip(base.values["comparisons"], r.values["comparisons"]):
            assert b["p_adjusted"] == pytest.approx(min(1.0, a["p"] * 9))
        recorded(r, "Number of comparisons (m)", "9", "3")
        untouched(func(tt, comparisons="all", m=None))
    assert one_way(t, "kruskal_wallis", comparisons="all", m=4).values["overrides"]


def test_m_validation():
    t = table()
    with pytest.raises(StatisticsError, match="cannot be less than"):
        ordinary_anova(t, comparisons="all", method="bonferroni", m=2)
    with pytest.raises(StatisticsError, match="whole number"):
        ordinary_anova(t, comparisons="all", method="bonferroni", m=4.5)
    with pytest.raises(StatisticsError, match="whole number"):
        ordinary_anova(t, comparisons="all", method="bonferroni", m=True)
    with pytest.raises(StatisticsError, match="Bonferroni, Šidák, Holm-Šidák and Dunn"):
        ordinary_anova(t, comparisons="all", method="tukey", m=10)
    with pytest.raises(StatisticsError, match="Bonferroni, Šidák, Holm-Šidák and Dunn"):
        kruskal_wallis(t, comparisons="all", method="bh", m=10)
    with pytest.raises(StatisticsError, match="ask for comparisons"):
        ordinary_anova(t, m=10)
    with pytest.raises(StatisticsError, match="cannot be less than"):  # per family: control + 2 others = 2 comparisons
        ordinary_anova(t, comparisons="control", control="A", method="bonferroni", m=1)
    ok = ordinary_anova(t, comparisons="control", control="A", method="bonferroni", m=2)
    assert "overrides" not in ok.values


# --- ANOVA -----------------------------------------------------------------------------------------------------------


def test_ordinary_defaults_unchanged():
    t = table()
    base = ordinary_anova(t, comparisons="all")
    same(base, ordinary_anova(t, comparisons="all", df=None, sd=None, m=None))
    untouched(base)
    same(base, one_way(t, "ordinary", comparisons="all", df=None, sd=None, m=None))


def test_ordinary_df():
    t = table()
    base = ordinary_anova(t, comparisons="all", method="fisher_lsd")
    r = one_way(t, "ordinary", comparisons="all", method="fisher_lsd", df=8)
    assert r.values["p"] == pytest.approx(stats.f.sf(base.values["f"], 2, 8))
    assert r.values["dfd"] == 8 and r.values["f"] == base.values["f"]
    row = r.values["comparisons"][0]
    assert row["df"] == 8 and row["p"] == pytest.approx(2 * stats.t.sf(abs(row["statistic"]), 8))
    recorded(r, "Error df", "8", "27")
    assert not any("larger" in w for w in r.warnings)
    assert any("larger" in w for w in ordinary_anova(t, df=300).warnings)


def test_ordinary_sd():
    t = table()
    base = ordinary_anova(t, comparisons="all", method="fisher_lsd")
    sd = 2 * math.sqrt(base.values["ms_within"])
    r = ordinary_anova(t, comparisons="all", method="fisher_lsd", sd=sd)
    assert r.values["p"] == base.values["p"] and r.values["f"] == base.values["f"]
    for a, b in zip(base.values["comparisons"], r.values["comparisons"]):
        assert b["se"] == pytest.approx(2 * a["se"])
        assert b["p"] > a["p"]
    recorded(r, "SD for multiple comparisons")
    with pytest.raises(StatisticsError, match="sd"):
        ordinary_anova(t, comparisons="all", sd=0)
    with pytest.raises(StatisticsError, match="sd"):
        ordinary_anova(t, comparisons="all", sd=float("nan"))
    with pytest.raises(StatisticsError, match="multiple comparisons"):
        ordinary_anova(t, sd=1.0)


def test_ordinary_df_validation():
    for bad in (0, -1, float("inf"), "x"):
        with pytest.raises(StatisticsError, match="df"):
            ordinary_anova(table(), df=bad)


def test_welch_df_and_m():
    t = table()
    base = welch_anova(t)
    same(base, welch_anova(t, df=None, m=None))
    r = one_way(t, "welch", df=7)
    assert r.values["p"] == pytest.approx(stats.f.sf(base.values["welch_f"], 2, 7))
    assert r.values["welch_dfd"] == 7
    assert r.values["bf_anova_p"] == base.values["bf_anova_p"] and r.values["bf_anova_dfd"] == base.values["bf_anova_dfd"]
    recorded(r, "Welch denominator df", "7")
    with pytest.raises(StatisticsError, match="Bonferroni, Šidák, Holm-Šidák and Dunn"):
        welch_anova(t, comparisons="all", m=5)  # Games-Howell
    ok = welch_anova(t, comparisons="all", method="tamhane_t2")
    assert "overrides" not in ok.values
    with pytest.raises(StatisticsError, match="Bonferroni, Šidák, Holm-Šidák and Dunn"):
        welch_anova(t, comparisons="all", method="tamhane_t2", m=5)


def test_rm_defaults_unchanged():
    t = table(paired=True)
    base = repeated_measures_anova(t, comparisons="all")
    same(base, repeated_measures_anova(t, comparisons="all", epsilon=None, df=None, sd=None, m=None))
    untouched(base)


def test_rm_epsilon():
    t = table(paired=True)
    base = repeated_measures_anova(t)
    eps = 0.7
    r = one_way(t, "repeated_measures", epsilon=eps)
    f, dfe = base.values["f"], base.values["dfd_uncorrected"]
    assert r.values["p"] == pytest.approx(stats.f.sf(f, eps * 2, eps * dfe))
    assert r.values["epsilon_used"] == eps and r.values["epsilon"] == base.values["epsilon"]
    recorded(r, "Epsilon", "0.7")
    # implies the correction even when geisser_greenhouse=False
    off = repeated_measures_anova(t, geisser_greenhouse=False, epsilon=eps)
    assert off.values["p"] == pytest.approx(r.values["p"])
    assert off.row("Repeated measures ANOVA summary", "Assume sphericity (equal variability of differences)?").value is False
    # epsilon = 1 equals the uncorrected test
    one = repeated_measures_anova(t, epsilon=1.0)
    assert one.values["p"] == pytest.approx(base.values["p_uncorrected"])


def test_rm_df_sd_m():
    t = table(paired=True)
    base = repeated_measures_anova(t, geisser_greenhouse=False)
    r = repeated_measures_anova(t, geisser_greenhouse=False, df=12)
    assert r.values["p"] == pytest.approx(stats.f.sf(base.values["f"], 2, 12))
    recorded(r, "Error df (before epsilon)", "12", "18")
    both = repeated_measures_anova(t, df=12, epsilon=0.6)
    assert both.values["p"] == pytest.approx(stats.f.sf(base.values["f"], 1.2, 7.2))
    assert len(both.values["overrides"]) == 2
    c = repeated_measures_anova(t, geisser_greenhouse=False, comparisons="all", method="fisher_lsd")
    s = repeated_measures_anova(t, geisser_greenhouse=False, comparisons="all", method="fisher_lsd", sd=3 * math.sqrt(base.values["ms_residual"]))
    for a, b in zip(c.values["comparisons"], s.values["comparisons"]):
        assert b["se"] == pytest.approx(3 * a["se"])
    recorded(s, "SD for multiple comparisons")
    d = repeated_measures_anova(t, geisser_greenhouse=False, comparisons="all", method="fisher_lsd", df=12)
    assert d.values["comparisons"][0]["df"] == 12
    mm = repeated_measures_anova(t, geisser_greenhouse=False, comparisons="all", method="bonferroni", m=7)
    base_b = repeated_measures_anova(t, geisser_greenhouse=False, comparisons="all", method="bonferroni")
    assert mm.values["comparisons"][0]["p_adjusted"] == pytest.approx(min(1.0, base_b.values["comparisons"][0]["p"] * 7))
    recorded(mm, "Number of comparisons (m)", "7", "3")


def test_rm_validation_and_mixed():
    t = table(paired=True)
    for bad in (0.4, 1.2, 0, float("nan"), "a"):  # k = 3: 1/(k-1) = 0.5
        with pytest.raises(StatisticsError, match="epsilon"):
            repeated_measures_anova(t, epsilon=bad)
    repeated_measures_anova(t, epsilon=0.5)
    with pytest.raises(StatisticsError, match="epsilon"):
        repeated_measures_anova(table(paired=True, k=2), epsilon=0.9)
    with pytest.raises(StatisticsError, match="df"):
        repeated_measures_anova(t, df=-1)
    with pytest.raises(StatisticsError, match="sd replaces the pooled error term"):
        repeated_measures_anova(t, comparisons="all", sd=1.0, rm_error="separate")
    blank = table(paired=True, blank=True)
    for kw in ({"df": 5}, {"epsilon": 0.7}, {"sd": 1.0}):
        with pytest.raises(StatisticsError, match=r"not available with blanks \(mixed model\)"):
            repeated_measures_anova(blank, **kw)
    mixed = repeated_measures_anova(blank, comparisons="all", method="bonferroni", m=5)  # m still works
    recorded(mixed, "Number of comparisons (m)", "5", "3")


# --- Two-way ANOVA ---------------------------------------------------------------------------------------------------


def grouped() -> GroupedTable:
    rng = np.random.default_rng(3)
    cells = {(r, c): list(rng.normal(i + 2 * j, 1.0, 4)) for i, r in enumerate(["r1", "r2", "r3"]) for j, c in enumerate(["a", "b"])}
    return GroupedTable.from_cells(cells, title="grid")


def test_two_way_df():
    g = grouped()
    base = two_way_anova(g, compare="column_means", method="fisher_lsd")
    same(base, two_way_anova(g, compare="column_means", method="fisher_lsd", df=None))
    untouched(base)
    r = two_way_anova(g, df=9, compare="column_means", method="fisher_lsd")
    assert base.values["df_resid"] == 18 and r.values["df_resid"] == 9
    for a, b in zip(base.values["anova"], r.values["anova"]):
        assert b["f"] == a["f"] and b["p"] == pytest.approx(stats.f.sf(a["f"], a["df"], 9))
    row = r.values["comparisons"][0]
    assert row["df"] == 9 and row["p"] == pytest.approx(2 * stats.t.sf(abs(row["statistic"]), 9))
    recorded(r, "Residual df", "9", "18")
    with pytest.raises(StatisticsError, match="df"):
        two_way_anova(g, df=0)


# --- XY ---------------------------------------------------------------------------------------------------------------


def xy() -> XYTable:
    x = np.arange(1.0, 9.0)
    rng = np.random.default_rng(1)
    y = 2 * x[:, None, None] + rng.normal(0, 1, (8, 1, 3))
    return XYTable("xy", "X", x, ["Y"], y)


def test_linear_regression_df():
    t = xy()
    base = linear_regression(t)
    same(base, linear_regression(t, df=None))
    untouched(base)
    r = linear_regression(t, df=5)
    fb, fr = base.values["fits"]["Y"], r.values["fits"]["Y"]
    assert fb["df"] == 22 and fr["df"] == 5 and fr["slope"] == fb["slope"] and fr["se_slope"] == fb["se_slope"]
    tv = fb["slope"] / fb["se_slope"]
    assert fr["p"] == pytest.approx(2 * stats.t.sf(abs(tv), 5))
    assert fr["f"] == fb["f"] and fr["p"] == pytest.approx(stats.f.sf(fb["f"], 1, 5))
    half = stats.t.ppf(0.975, 5) * fb["se_slope"]
    assert fr["slope_ci"] == pytest.approx((fb["slope"] - half, fb["slope"] + half))
    assert fr["intercept_ci"][1] - fr["intercept_ci"][0] > fb["intercept_ci"][1] - fb["intercept_ci"][0]
    recorded(r, "Residual df", "5", "22")
    with pytest.raises(StatisticsError, match="df"):
        linear_regression(t, df=-3)


def test_correlation_df():
    t = xy()
    base = xy_correlation(t)
    same(base, xy_correlation(t, df=None))
    untouched(base)
    r = xy_correlation(t, df=6)
    cb, cr = base.values["correlations"]["Y"], r.values["correlations"]["Y"]
    tv = cb["r"] * math.sqrt(22) / math.sqrt(1 - cb["r"] ** 2)
    assert cr["p"] == pytest.approx(2 * stats.t.sf(abs(tv), 6))
    assert (cr["ci_low"], cr["ci_high"]) == (cb["ci_low"], cb["ci_high"])
    recorded(r, "df", "6", "22")
    with pytest.raises(StatisticsError, match="df"):
        xy_correlation(t, df=float("inf"))


def test_paired_t_direct_call_records_methods_once():
    r = paired_t(table(paired=True)["A"].values, table(paired=True)["B"].values, df=3, difference=0.1)
    assert r.methods.count("Changed from the default") == 2
    assert r.warnings[0].startswith("Set by hand")
