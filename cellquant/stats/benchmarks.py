"""The "Check the engine" benchmark set: the statistics engine run on published reference data, each number beside the
value its source published.

Every entry holds its data inline, the engine call, and the expected values. Two kinds of expected value:

* Published: a string with the source's own digits (``"-4.0621"``, ``"9.085e-05"``). The engine agrees when it matches
  to those digits, that is, within half a unit of the last published digit (``"0.0159"`` allows 0.00005). A leading
  ``<`` or ``>`` states a published bound (``"<0.01"``).
* Computed live: the reference is the installed scipy or statsmodels run on the same inline data when
  ``run_benchmarks`` is called, so the source says "(computed live)" and gives the version. These agree to a relative
  1e-6 (``LIVE_RTOL``), or to the stated tolerance where the reference itself is approximate.

``run_benchmarks()`` returns one ``BenchmarkRow`` per expected value; ``summarize(rows)`` counts them.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import scipy
from scipy import stats as sps

from cellquant.stats import (
    ColumnTable,
    ComparisonPlan,
    adjust_p,
    bartlett_test,
    brown_forsythe_test,
    contingency,
    describe,
    friedman,
    grubbs_fit,
    kruskal_wallis,
    linear_regression,
    mann_whitney,
    normality,
    one_sample_t,
    ordinary_anova,
    paired_t,
    percentiles,
    repeated_measures_anova,
    survival,
    two_way_anova,
    unpaired_t,
    welch_anova,
    welch_t,
    wilcoxon_matched_pairs,
    wilcoxon_signed_rank,
    xy_correlation,
)
from cellquant.stats.contingency import ContingencyTable
from cellquant.stats.grouped import GroupedTable
from cellquant.stats.multcomp import Estimates, _critical, max_abs_t_cdf, run_comparisons
from cellquant.stats.survival import SurvivalTable
from cellquant.stats.xy import XYTable

LIVE_RTOL = 1e-6

try:  # the live references that need statsmodels are skipped, and said so, without it
    import statsmodels
except ImportError:  # pragma: no cover
    statsmodels = None

SCIPY = f"scipy {scipy.__version__} (computed live)"
STATSMODELS = f"statsmodels {statsmodels.__version__} (computed live)" if statsmodels else ""


# ------------------------------------------------------------------------------------------------ data

# R's `sleep` (Student 1908): extra hours of sleep with drug 1 and drug 2 in the same 10 patients.
SLEEP_1 = [0.7, -1.6, -0.2, -1.2, -0.1, 3.4, 3.7, 0.8, 0.0, 2.0]
SLEEP_2 = [1.9, 0.8, 1.1, 0.1, -0.1, 4.4, 5.5, 1.6, 4.6, 3.4]
# R's `mtcars`: miles per gallon, automatic (19 cars) and manual (13 cars).
MPG_AUTO = [21.4, 18.7, 18.1, 14.3, 24.4, 22.8, 19.2, 17.8, 16.4, 17.3, 15.2, 10.4, 10.4, 14.7, 21.5, 15.5, 15.2, 13.3, 19.2]
MPG_MANUAL = [21.0, 21.0, 22.8, 32.4, 30.4, 33.9, 27.3, 26.0, 30.4, 15.8, 19.7, 15.0, 21.4]
# R's `PlantGrowth` (Dobson 1983).
PLANT = {
    "ctrl": [4.17, 5.58, 5.18, 6.11, 4.50, 4.61, 5.17, 4.53, 5.33, 5.14],
    "trt1": [4.81, 4.17, 4.41, 3.59, 5.87, 3.83, 6.03, 4.89, 4.32, 4.69],
    "trt2": [6.31, 5.12, 5.54, 5.50, 5.37, 5.29, 4.92, 6.15, 5.80, 5.26],
}
# R's `InsectSprays` (Beall 1942).
INSECTS = {
    "A": [10, 7, 20, 14, 14, 12, 10, 23, 17, 20, 14, 13],
    "B": [11, 17, 21, 11, 16, 14, 17, 17, 19, 21, 7, 13],
    "C": [0, 1, 7, 2, 3, 1, 2, 1, 3, 0, 1, 4],
    "D": [3, 5, 12, 6, 4, 3, 5, 5, 5, 5, 2, 4],
    "E": [3, 5, 3, 5, 3, 6, 1, 1, 3, 2, 6, 4],
    "F": [11, 9, 15, 22, 15, 16, 13, 10, 26, 26, 24, 13],
}
# R's `warpbreaks` (Tippett 1950): breaks by tension (wool A rows first, then wool B).
WARP = {
    "L": [26, 30, 54, 25, 70, 52, 51, 26, 67, 27, 14, 29, 19, 29, 31, 41, 20, 44],
    "M": [18, 21, 29, 17, 12, 18, 35, 30, 36, 42, 26, 19, 16, 39, 28, 21, 39, 29],
    "H": [36, 21, 24, 18, 10, 43, 28, 15, 26, 20, 21, 24, 17, 13, 15, 15, 16, 28],
}
# R's ?friedman.test RoundingTimes (Hollander and Wolfe 1973, p. 140): 22 players, three ways of rounding first base.
ROUNDING = [
    [5.40, 5.50, 5.55], [5.85, 5.70, 5.75], [5.20, 5.60, 5.50], [5.55, 5.50, 5.40], [5.90, 5.85, 5.70], [5.45, 5.55, 5.60],
    [5.40, 5.40, 5.35], [5.45, 5.50, 5.35], [5.25, 5.15, 5.00], [5.85, 5.80, 5.70], [5.25, 5.20, 5.10], [5.65, 5.55, 5.45],
    [5.60, 5.35, 5.45], [5.05, 5.00, 4.95], [5.50, 5.50, 5.40], [5.45, 5.55, 5.50], [5.55, 5.55, 5.35], [5.45, 5.50, 5.55],
    [5.50, 5.45, 5.25], [5.65, 5.60, 5.40], [5.70, 5.65, 5.55], [6.30, 6.30, 6.25]]
# Dunnett (1955), Table 1 as given in scipy's dunnett example: blood counts.
BLOOD = {"control": [7.40, 8.50, 7.20, 8.24, 9.84, 8.32], "a": [9.76, 8.80, 7.68, 9.36], "b": [12.80, 9.68, 12.16, 9.20, 10.55]}
# Shapiro and Wilk (1965): weights in pounds of 11 men.
WEIGHTS = [148, 154, 158, 160, 161, 162, 166, 170, 182, 195, 236]

# Data for the computed-live entries (typed once from the engine's own tests).
XY_X = [0.0, 1.0, 2.0, 4.0, 8.0]
XY_A = [[1.1, 0.9, 1.3], [2.0, 2.3, 1.8], [3.2, 2.9, 3.0], [5.1, 4.8, 5.3], [9.2, 8.8, 9.1]]
TWO_WAY = {
    ("Control", "WT"): [4.0, 5.0, 6.0], ("Control", "KO1"): [6.0, 7.0, 8.0], ("Control", "KO2"): [5.0, 6.5, 8.0],
    ("Treated", "WT"): [7.0, 8.0, 9.5], ("Treated", "KO1"): [12.0, 13.0, 15.0], ("Treated", "KO2"): [8.0, 9.0, 11.0],
}
TA = [1.1, 3.9, 14.0, 22.0, 3.4, 2.6, 4.3, 1.0, 10.0, 0.7, 4.5, 33.5, 2.7, 12.9, 5.2, 20.3, 9.1, 14.8, 2.9, 1.8, 0.0, 11.7, 8.9, 12.2, 6.5, 5.7, 5.9, 5.8, 0.4, 21.6]
EA = [1, 1, 1, 0, 1, 1, 1, 1, 1, 1, 0, 1, 1, 0, 1, 1, 0, 1, 0, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1]
TB = [3.0, 28.8, 7.0, 43.8, 17.8, 8.8, 54.1, 1.3, 17.5, 12.8, 1.2, 2.5, 2.1, 5.3, 5.0, 5.5, 9.1, 3.2, 38.9, 16.6, 27.2, 32.5, 1.4, 15.2, 15.4]
EB = [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 0, 1, 1, 0, 1, 1, 1]
RM = [[1.971339, 1.941646, 5.365985, 3.35702], [-2.661454, -2.362975, -2.423435, -3.032876], [0.300859, 0.992405, 0.893923, 2.758961],
      [-0.62771, -0.222306, 1.335554, 1.195378], [-0.604218, -0.210735, 0.646437, 3.256474], [-0.296483, -0.004043, 1.25292, -0.456641],
      [-2.107502, -1.245383, -0.889039, -0.891863]]
P_STACK = [0.26161, 0.29849, 0.81423, 0.09192, 0.6001, 0.72856, 0.1879, 0.05515, 0.27497, 0.65743, 0.56227, 0.15006, 0.43263, 0.6693, 0.42278,
           0.63318, 0.96744, 0.68306, 0.39162, 0.18725, 0.01038, 0.01533, 0.02674, 0.02327, 0.00954, 0.02773, 0.01413, 0.02081, 0.00322]


# ------------------------------------------------------------------------------------------------ structure


@dataclass(frozen=True)
class Expected:
    """One number to check: ``key`` is a path into the engine's values, separated by ``/`` (an integer picks a list
    item; a text picks the dict key, or the list item with that ``label``). ``published`` is the source's digits as
    text; leave it ``None`` for a live reference, which supplies the number under the same key. ``tol`` overrides the
    tolerance: of a live number, or (stated beside the entry) of a published one whose digits are rounded."""

    key: str
    published: str | None = None
    tol: float | None = None


@dataclass(frozen=True)
class Benchmark:
    name: str
    analysis: str
    source: str
    data: dict
    run: Callable[[dict], dict]
    expected: tuple[Expected, ...]
    reference: Callable[[dict], dict] | None = None  # live reference: key -> number
    needs_statsmodels: bool = False


@dataclass(frozen=True)
class BenchmarkRow:
    name: str
    analysis: str
    source: str
    key: str
    published: Any  # the published text, or the live reference number
    engine: float | None
    ok: bool


@dataclass(frozen=True)
class BenchmarkSummary:
    benchmarks: int
    values: int
    agreeing: int
    disagreeing: tuple[BenchmarkRow, ...] = field(default=())
    skipped: tuple[str, ...] = field(default=())

    @property
    def text(self) -> str:
        line = f"{self.agreeing} of {self.values} values agree with the published values, in {self.benchmarks} reference analyses."
        if self.skipped:
            line += f" Skipped (statsmodels not installed): {', '.join(self.skipped)}."
        return line


def _ex(*pairs: tuple[str, str]) -> tuple[Expected, ...]:
    return tuple(Expected(key, text) for key, text in pairs)


def _live(*keys: str, tol: float | None = None) -> tuple[Expected, ...]:
    return tuple(Expected(key, None, tol) for key in keys)


# ------------------------------------------------------------------------------------------------ comparing values


_NUMBER = re.compile(r"^\s*([<>]?)\s*([+-]?)(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d+))?\s*$")


def tolerance(published: str) -> tuple[str, float, float]:
    """(bound, value, half unit of the last published digit) for a published text such as ``"9.085e-05"``."""

    match = _NUMBER.match(published)
    if not match:
        raise ValueError(f"not a published number: {published!r}")
    bound, sign, whole, fraction, exponent = match.groups()
    fraction = fraction or ""
    scale = 10.0 ** int(exponent or 0)
    value = float(f"{sign}{whole}.{fraction or '0'}") * scale
    return bound, value, 0.5 * 10.0 ** -len(fraction) * scale


def agrees(published: str, engine: float | None) -> bool:
    """Whether ``engine`` matches ``published`` to the published digits."""

    if engine is None or not math.isfinite(engine):
        return False
    bound, value, half = tolerance(published)
    if bound == "<":
        return engine < value
    if bound == ">":
        return engine > value
    return abs(engine - value) <= half * (1 + 1e-9)


def _dig(values: Any, path: str) -> float:
    node = values
    for part in path.split("/"):
        if isinstance(node, (list, tuple, np.ndarray)):
            if re.fullmatch(r"-?\d+", part):
                node = node[int(part)]
            else:
                node = next(item for item in node if item.get("label") == part)
        else:
            node = node[part]
    return float(node)


# ------------------------------------------------------------------------------------------------ engine calls


def _cols(data: dict, **options) -> ColumnTable:
    return ColumnTable.from_columns({k: v for k, v in data.items()}, **options)


def _run_paired_t(d):
    return paired_t(d["a"], d["b"]).values


def _run_unpaired_t(d):
    return unpaired_t(d["a"], d["b"]).values


def _run_welch_t(d):
    return welch_t(d["a"], d["b"]).values


def _run_wilcoxon_pairs(d):
    return wilcoxon_matched_pairs(d["a"], d["b"], **d.get("options", {})).values


def _run_mann_whitney(d):
    return mann_whitney(d["a"], d["b"], **d.get("options", {})).values


def _run_one_sample_t(d):
    return one_sample_t(d["x"], 0.0).values


def _run_wilcoxon_signed(d):
    return wilcoxon_signed_rank(d["x"], 0.0, exact=False, continuity=True).values


def _run_describe(d):
    out = describe(d["x"])
    return {"skewness": out["skewness"], "kurtosis": out["kurtosis"]}


def _run_percentiles(d):
    return {"p25": float(percentiles(d["a"], 25)), "q1": float(percentiles(d["b"], [25, 75])[0]), "q3": float(percentiles(d["b"], [25, 75])[1])}


def _run_ordinary_anova(d):
    return ordinary_anova(_cols(d["groups"])).values


def _run_brown_forsythe(d):
    return brown_forsythe_test(list(d["groups"].values()))


def _run_bartlett(d):
    return bartlett_test(list(d["groups"].values()))


def _run_welch_anova(d):
    return welch_anova(_cols(d["groups"])).values


def _run_kruskal(d):
    return kruskal_wallis(_cols(d["groups"]), **d.get("options", {})).values


def _run_friedman(d):
    names = ["Round Out", "Narrow Angle", "Wide Angle"]
    rows = np.array(d["rows"], float)
    return friedman(ColumnTable.from_columns({n: rows[:, j] for j, n in enumerate(names)}, paired=True)).values


def _run_tukey(d):
    return {"comparisons": ordinary_anova(_cols(d["groups"]), comparisons="all").values["comparisons"]}


def _run_tukey_blocked(d):
    # The error term (MSE, df 50) comes from the two-factor model wool + tension; the engine's comparison step gets it directly.
    Y = np.array([d["groups"]["L"], d["groups"]["M"], d["groups"]["H"]], float)
    grand = Y.mean()
    ss_tension = 18 * np.sum((Y.mean(axis=1) - grand) ** 2)
    ss_wool = 27 * np.sum((np.array([Y[:, :9].mean(), Y[:, 9:].mean()]) - grand) ** 2)
    mse = (np.sum((Y - grand) ** 2) - ss_tension - ss_wool) / 50
    est = Estimates(["L", "M", "H"], Y.mean(axis=1), np.full(3, 18.0), mse / 18 * np.eye(3), 50)
    return {"comparisons": run_comparisons(est, ComparisonPlan("all", "tukey"))["rows"]}


def _run_dunnett_table(d):
    out = {}
    for df_label, df in (("inf", np.inf), ("10", 10)):
        for m in range(1, 10):
            corr = np.full((m, m), 0.5) + 0.5 * np.eye(m)
            out[f"df={df_label}, {m} treatments"] = _critical(lambda c: max_abs_t_cdf(c, corr, df), 0.95)
    return out


def _run_dunnett(d):
    return {"comparisons": ordinary_anova(_cols(d["groups"]), comparisons="control", control="control").values["comparisons"]}


def _run_shapiro(d):
    return normality(d["x"], tests=["shapiro"]).values["shapiro"]


def _run_grubbs(d):
    return {f"n={n}": grubbs_fit(np.arange(n) + np.sin(np.arange(n)))["rounds"][0]["critical"] for n in d["sizes"]}


def _grouped(d) -> GroupedTable:
    return GroupedTable.from_cells({tuple(k.split("|")): v for k, v in d["cells"].items()})


def _run_two_way(d):
    return {row["term"]: row for row in two_way_anova(_grouped(d)).values["anova"]}


def _xy(d) -> XYTable:
    return XYTable("Response", "Dose", d["x"], ["A"], np.array(d["y"], float).reshape(len(d["x"]), 1, -1))


def _run_regression(d):
    return linear_regression(_xy(d)).values["fits"]["A"]


def _run_correlation(d):
    return {m: xy_correlation(_xy(d), method=m).values["correlations"]["A"] for m in ("pearson", "spearman")}


def _contingency(d, **options):
    return contingency(ContingencyTable("Response", ["Treated", "Control"], ["Responded", "Did not"], d["counts"]), **options)


def _run_fisher(d):
    v = _contingency(d).values
    return {"p": v["p"], "rr": v["effect"]["rr"], "odds_ratio": v["effect"]["odds_ratio"]}


def _run_chi2(d):
    v = _contingency(d, test="chi2", yates=True).values
    return {"p": v["p"], "chi2": v["chi2"]["chi2"]}


def _run_trend_table(d):
    v = contingency(ContingencyTable("Dose", ["0", "1", "2", "3"], ["Response", "None"], d["counts"])).values
    return {"p": v["p"], "cramers_v": v["cramers_v"]}


def _run_survival(d):
    table = SurvivalTable("Trial", ["A", "B"], [d["ta"], d["tb"]], [d["ea"], d["eb"]])
    v = survival(table).values
    return {"logrank": v["logrank"], "gehan": v["gehan"]}


def _run_repeated(d):
    table = ColumnTable.from_columns({f"T{j + 1}": [row[j] for row in d["rows"]] for j in range(4)}, paired=True)
    return repeated_measures_anova(table, geisser_greenhouse=False).values


def _run_adjust(d):
    out = {}
    for method in ("bonferroni", "sidak", "holm", "bh", "by"):
        out[method] = list(adjust_p(d["p"], method, 0.05)["adjusted"])
    return out


# ------------------------------------------------------------------------------------------------ live references


def _ref_dunnett(d):
    ref = sps.dunnett(d["groups"]["a"], d["groups"]["b"], control=d["groups"]["control"], rng=np.random.default_rng(0))
    return {"comparisons/control vs. a/p_adjusted": float(ref.pvalue[0]), "comparisons/control vs. b/p_adjusted": float(ref.pvalue[1])}


def _ref_two_way(d):
    import pandas as pd
    import statsmodels.api as sm
    import statsmodels.formula.api as smf

    rows = [{"row": k.split("|")[0], "column": k.split("|")[1], "value": x} for k, v in d["cells"].items() for x in v]
    table = sm.stats.anova_lm(smf.ols("value ~ C(row) * C(column)", pd.DataFrame(rows)).fit(), typ=2)
    out = {}
    for term, label in (("row", "C(row)"), ("column", "C(column)"), ("interaction", "C(row):C(column)")):
        out[f"{term}/f"] = float(table.loc[label, "F"])
        out[f"{term}/p"] = float(table.loc[label, "PR(>F)"])
    return out


def _ref_regression(d):
    x = np.repeat(d["x"], len(d["y"][0]))
    y = np.array(d["y"], float).ravel()
    ref = sps.linregress(x, y)
    return {"slope": ref.slope, "intercept": ref.intercept, "r2": ref.rvalue**2, "p": ref.pvalue}


def _ref_correlation(d):
    x = np.repeat(d["x"], len(d["y"][0]))
    y = np.array(d["y"], float).ravel()
    pearson, spearman = sps.pearsonr(x, y), sps.spearmanr(x, y)
    return {"pearson/r": pearson[0], "pearson/p": pearson[1], "spearman/r": spearman.statistic}


def _ref_fisher(d):
    c = np.array(d["counts"])
    rr = sps.contingency.relative_risk(c[0, 0], c[0].sum(), c[1, 0], c[1].sum())
    return {"p": sps.fisher_exact(c).pvalue, "rr": rr.relative_risk}


def _ref_odds_ratio(d):
    from statsmodels.stats.contingency_tables import Table2x2

    return {"odds_ratio": float(Table2x2(np.array(d["counts"])).oddsratio)}


def _ref_chi2(d):
    ref = sps.chi2_contingency(d["counts"], correction=True)
    return {"p": ref.pvalue, "chi2": ref.statistic}


def _ref_trend_table(d):
    ref = sps.chi2_contingency(d["counts"])
    return {"p": ref.pvalue, "cramers_v": math.sqrt(ref.statistic / np.sum(d["counts"]))}


def _ref_survival(d):
    from statsmodels.duration.survfunc import survdiff

    times = np.r_[d["ta"], d["tb"]]
    status = np.r_[d["ea"], d["eb"]]
    group = np.r_[[0] * len(d["ta"]), [1] * len(d["tb"])]
    lr, gb = survdiff(times, status, group), survdiff(times, status, group, weight_type="gb")
    return {"logrank/chi2": lr[0], "logrank/p": lr[1], "gehan/chi2": gb[0], "gehan/p": gb[1]}


def _ref_repeated(d):
    import pandas as pd
    from statsmodels.stats.anova import AnovaRM

    rows = [{"y": y, "s": i, "g": j} for i, row in enumerate(d["rows"]) for j, y in enumerate(row)]
    ref = AnovaRM(pd.DataFrame(rows), "y", "s", within=["g"]).fit().anova_table.iloc[0]
    return {"f": float(ref["F Value"]), "p": float(ref["Pr > F"])}


def _ref_adjust(d):
    from statsmodels.stats.multitest import multipletests

    out = {}
    for method, name in (("bonferroni", "bonferroni"), ("sidak", "sidak"), ("holm", "holm"), ("bh", "fdr_bh"), ("by", "fdr_by")):
        for i, value in enumerate(multipletests(d["p"], alpha=0.05, method=name)[1]):
            out[f"{method}/{i}"] = float(value)
    return out


# ------------------------------------------------------------------------------------------------ the set

R = "R 4.x "
_HW73 = "Hollander and Wolfe (1973), as in R's ?"

BENCHMARKS: list[Benchmark] = [
    # ---- t tests ----
    Benchmark(
        "Paired t test, R sleep data", "Paired t test",
        R + "t.test(extra ~ group, data = sleep, paired = TRUE)",
        {"a": SLEEP_2, "b": SLEEP_1},  # the engine reports B - A, R reports drug 1 - drug 2
        _run_paired_t,
        _ex(("t", "-4.0621"), ("df", "9"), ("p", "0.002833"), ("difference", "-1.58"), ("ci_low", "-2.4598858"), ("ci_high", "-0.7001142")),
    ),
    Benchmark(
        "Unpaired t test, R sleep data", "Unpaired t test (equal variances), with F test of the variances",
        R + "t.test(extra ~ group, data = sleep, var.equal = TRUE); var.test(extra ~ group, data = sleep)",
        {"a": SLEEP_2, "b": SLEEP_1},
        _run_unpaired_t,
        _ex(("t", "-1.8608"), ("df", "18"), ("p", "0.07919"), ("ci_low", "-3.363874"), ("ci_high", "0.203874"),
            ("mean_a", "2.33"), ("mean_b", "0.75"), ("f_p", "0.7427")),
    ),
    Benchmark(
        "Welch t test, R sleep data", "Welch's t test",
        R + "t.test(extra ~ group, data = sleep)",
        {"a": SLEEP_2, "b": SLEEP_1},
        _run_welch_t,
        _ex(("t", "-1.8608"), ("df", "17.776"), ("p", "0.07939"), ("ci_low", "-3.3654832"), ("ci_high", "0.2054832")),
    ),
    Benchmark(
        "Welch t test, R mtcars (unequal n)", "Welch's t test",
        R + "t.test(mpg ~ am, data = mtcars)",
        {"a": MPG_MANUAL, "b": MPG_AUTO},
        _run_welch_t,
        _ex(("t", "-3.7671"), ("df", "18.332"), ("p", "0.001374"), ("ci_low", "-11.280194"), ("ci_high", "-3.209684"),
            ("mean_b", "17.14737"), ("mean_a", "24.39231")),
    ),
    Benchmark(
        "One-sample t test, R sleep differences", "One-sample t test",
        R + "t.test(extra ~ group, data = sleep, paired = TRUE) (a one-sample test on the differences)",
        {"x": [round(a - b, 10) for a, b in zip(SLEEP_1, SLEEP_2)]},
        _run_one_sample_t,
        _ex(("t", "-4.0621"), ("df", "9"), ("p", "0.002833"), ("ci_low", "-2.4598858"), ("ci_high", "-0.7001142")),
    ),
    # ---- rank tests ----
    Benchmark(
        "Wilcoxon matched pairs, R sleep data", "Wilcoxon matched-pairs signed-rank test (normal approximation)",
        R + "wilcox.test(extra ~ group, data = sleep, paired = TRUE)",
        {"a": SLEEP_2, "b": SLEEP_1, "options": {"exact": False, "continuity": True}},
        _run_wilcoxon_pairs,
        _ex(("sum_positive", "0"), ("p", "0.009091")),
    ),
    Benchmark(
        "Wilcoxon signed rank, R sleep differences", "Wilcoxon signed-rank test (one sample)",
        R + "wilcox.test(SLEEP_DIFF), the drug 2 - drug 1 differences",
        {"x": [round(b - a, 10) for a, b in zip(SLEEP_1, SLEEP_2)]},
        _run_wilcoxon_signed,
        _ex(("sum_positive", "45"), ("p", "0.009091")),
    ),
    Benchmark(
        "Mann-Whitney, R sleep data", "Mann-Whitney test (normal approximation)",
        R + "wilcox.test(extra ~ group, data = sleep)",
        {"a": SLEEP_1, "b": SLEEP_2, "options": {"exact": False, "continuity": True}},
        _run_mann_whitney,
        _ex(("u_a", "25.5"), ("p", "0.06933")),
    ),
    Benchmark(
        "Mann-Whitney, R mtcars", "Mann-Whitney test (normal approximation)",
        R + "wilcox.test(mpg ~ am, data = mtcars)",
        {"a": MPG_AUTO, "b": MPG_MANUAL, "options": {"exact": False, "continuity": True}},
        _run_mann_whitney,
        _ex(("u_a", "42"), ("p", "0.001871")),
    ),
    Benchmark(
        "Exact Wilcoxon, depression scores", "Wilcoxon matched-pairs signed-rank test (exact, one-tailed)",
        _HW73 + "wilcox.test, p. 29: wilcox.test(x, y, paired = TRUE, alternative = \"greater\")",
        {"a": [0.878, 0.647, 0.598, 2.05, 1.06, 1.29, 1.06, 3.14, 1.29], "b": [1.83, 0.50, 1.62, 2.48, 1.68, 1.88, 1.55, 3.06, 1.30],
         "options": {"alternative": "greater"}},
        _run_wilcoxon_pairs,
        _ex(("sum_positive", "40"), ("p", "0.01953")),
    ),
    Benchmark(
        "Exact Mann-Whitney, permeability constants", "Mann-Whitney test (exact, one-tailed)",
        _HW73 + "wilcox.test, p. 69: wilcox.test(x, y, alternative = \"g\")",
        {"a": [1.15, 0.88, 0.90, 0.74, 1.21], "b": [0.80, 0.83, 1.89, 1.04, 1.45, 1.38, 1.91, 1.64, 0.73, 1.46],
         "options": {"alternative": "greater"}},
        _run_mann_whitney,
        _ex(("u_b", "35"), ("p", "0.1272")),
    ),
    # ---- one-way ANOVA family ----
    Benchmark(
        "Ordinary ANOVA, R PlantGrowth", "Ordinary one-way ANOVA",
        R + "summary(aov(weight ~ group, data = PlantGrowth))",
        {"groups": PLANT},
        _run_ordinary_anova,
        _ex(("dfn", "2"), ("dfd", "27"), ("ss_between", "3.766"), ("ss_within", "10.492"), ("ms_between", "1.8832"),
            ("ms_within", "0.3886"), ("f", "4.846"), ("p", "0.0159"), ("means/0", "5.032"), ("means/1", "4.661"), ("means/2", "5.526")),
    ),
    Benchmark(
        "Ordinary ANOVA, R InsectSprays", "Ordinary one-way ANOVA",
        R + "summary(aov(count ~ spray, data = InsectSprays))",
        {"groups": INSECTS},
        _run_ordinary_anova,
        _ex(("dfn", "5"), ("dfd", "66"), ("ss_between", "2669"), ("ss_within", "1015"), ("ms_between", "533.8"), ("ms_within", "15.4"), ("f", "34.7")),
    ),
    Benchmark(
        "Ordinary ANOVA, R warpbreaks", "Ordinary one-way ANOVA",
        R + "summary(aov(breaks ~ tension, data = warpbreaks))",
        {"groups": WARP},
        _run_ordinary_anova,
        _ex(("dfn", "2"), ("ms_between", "1017.1"), ("f", "7.206"), ("p", "0.00175")),
    ),
    Benchmark(
        "Brown-Forsythe test, R PlantGrowth", "Brown-Forsythe test of equal variances",
        R + "car::leveneTest(weight ~ group, data = PlantGrowth) (center = median)",
        {"groups": PLANT},
        _run_brown_forsythe,
        _ex(("f", "1.1192"), ("dfn", "2"), ("dfd", "27"), ("p", "0.3412")),
    ),
    Benchmark(
        "Bartlett's test, R PlantGrowth", "Bartlett's test of equal variances",
        R + "bartlett.test(weight ~ group, data = PlantGrowth)",
        {"groups": PLANT},
        _run_bartlett,
        _ex(("statistic", "2.8786"), ("p", "0.2371")),
    ),
    Benchmark(
        "Bartlett's test, R InsectSprays", "Bartlett's test of equal variances",
        R + "bartlett.test(count ~ spray, data = InsectSprays)",
        {"groups": INSECTS},
        _run_bartlett,
        _ex(("statistic", "25.96"), ("df", "5"), ("p", "9.085e-05")),
    ),
    Benchmark(
        "Welch ANOVA, R PlantGrowth", "Welch's ANOVA",
        R + "oneway.test(weight ~ group, data = PlantGrowth)",
        {"groups": PLANT},
        _run_welch_anova,
        _ex(("welch_f", "5.181"), ("welch_dfn", "2"), ("welch_dfd", "17.128"), ("welch_p", "0.01739")),
    ),
    Benchmark(
        "Kruskal-Wallis, R PlantGrowth", "Kruskal-Wallis test",
        R + "kruskal.test(weight ~ group, data = PlantGrowth)",
        {"groups": PLANT},
        _run_kruskal,
        _ex(("h", "7.9882"), ("df", "2"), ("p", "0.01842")),
    ),
    Benchmark(
        "Kruskal-Wallis, R InsectSprays (many ties)", "Kruskal-Wallis test",
        R + "kruskal.test(count ~ spray, data = InsectSprays)",
        {"groups": INSECTS},
        _run_kruskal,
        _ex(("h", "54.691"), ("df", "5"), ("p", "1.511e-10")),
    ),
    Benchmark(
        "Kruskal-Wallis, mucociliary efficiency", "Kruskal-Wallis test (chi-square approximation)",
        _HW73 + "kruskal.test, p. 116: mucociliary efficiency in three groups of subjects",
        {"groups": {"normal": [2.9, 3.0, 2.5, 2.6, 3.2], "obstructive": [3.8, 2.7, 4.0, 2.4], "asbestosis": [2.8, 3.4, 3.7, 2.2, 2.0]},
         "options": {"exact": False}},
        _run_kruskal,
        _ex(("h", "0.77143"), ("df", "2"), ("p", "0.68")),
    ),
    Benchmark(
        "Friedman test, R RoundingTimes", "Friedman test",
        _HW73 + "friedman.test: friedman.test(RoundingTimes)",
        {"rows": ROUNDING},
        _run_friedman,
        _ex(("statistic", "11.143"), ("p", "0.003805"), ("n_subjects", "22")),
    ),
    # ---- multiple comparisons ----
    Benchmark(
        "Tukey HSD, R PlantGrowth", "Tukey multiple comparisons after ANOVA",
        R + "TukeyHSD(aov(weight ~ group, data = PlantGrowth))",
        {"groups": {"trt2": PLANT["trt2"], "trt1": PLANT["trt1"], "ctrl": PLANT["ctrl"]}},  # column order gives R's "second - first"
        _run_tukey,
        _ex(("comparisons/trt1 vs. ctrl/difference", "-0.371"), ("comparisons/trt1 vs. ctrl/ci_low", "-1.0622161"),
            ("comparisons/trt1 vs. ctrl/ci_high", "0.3202161"), ("comparisons/trt1 vs. ctrl/p_adjusted", "0.3908711"),
            ("comparisons/trt2 vs. ctrl/difference", "0.494"), ("comparisons/trt2 vs. ctrl/ci_low", "-0.1972161"),
            ("comparisons/trt2 vs. ctrl/ci_high", "1.1852161"), ("comparisons/trt2 vs. ctrl/p_adjusted", "0.1979960"),
            ("comparisons/trt2 vs. trt1/difference", "0.865"), ("comparisons/trt2 vs. trt1/ci_low", "0.1737839"),
            ("comparisons/trt2 vs. trt1/ci_high", "1.5562161"), ("comparisons/trt2 vs. trt1/p_adjusted", "0.0120064")),
    ),
    Benchmark(
        "Tukey HSD, R ?TukeyHSD warpbreaks with wool", "Tukey multiple comparisons with a blocking factor (error df 50)",
        R + "TukeyHSD(aov(breaks ~ wool + tension, data = warpbreaks), \"tension\", ordered = TRUE)",
        {"groups": WARP},
        _run_tukey_blocked,
        _ex(("comparisons/M vs. H/difference", "4.722222"), ("comparisons/M vs. H/ci_low", "-4.6311985"), ("comparisons/M vs. H/ci_high", "14.07564"),
            ("comparisons/M vs. H/p_adjusted", "0.4474210"), ("comparisons/L vs. H/difference", "14.722222"), ("comparisons/L vs. H/ci_low", "5.3688015"),
            ("comparisons/L vs. H/ci_high", "24.07564"), ("comparisons/L vs. H/p_adjusted", "0.0011218"), ("comparisons/L vs. M/difference", "10.0"),
            ("comparisons/L vs. M/ci_low", "0.6465793"), ("comparisons/L vs. M/ci_high", "19.35342"), ("comparisons/L vs. M/p_adjusted", "0.0336262")),
    ),
    Benchmark(
        "Dunnett critical values, Dunnett's table", "Dunnett's test: two-sided 5% critical values, 1 to 9 treatments against one control",
        "Dunnett (1955, 1964) tables of two-sided 5% critical values, equal group sizes",
        {},
        _run_dunnett_table,
        _ex(*[(f"df=inf, {m} treatments", v) for m, v in enumerate(["1.96", "2.21", "2.35", "2.44", "2.51", "2.57", "2.61", "2.65", "2.69"], 1)],
            *[(f"df=10, {m} treatments", v) for m, v in enumerate(["2.23", "2.57", "2.76", "2.89", "2.99", "3.07", "3.14", "3.19", "3.24"], 1)]),
    ),
    Benchmark(
        "Dunnett's test, blood counts", "Dunnett's multiple comparisons against a control",
        SCIPY + ": scipy.stats.dunnett (quasi-Monte Carlo, about 3 to 4 decimals) on Dunnett (1955) Table 1",
        {"groups": BLOOD},
        _run_dunnett,
        _live("comparisons/control vs. a/p_adjusted", "comparisons/control vs. b/p_adjusted", tol=5e-4),
        reference=_ref_dunnett,
    ),
    Benchmark(
        "Adjusted P values and FDR q values", "Bonferroni, Sidak, Holm, Benjamini-Hochberg and Benjamini-Yekutieli adjustment",
        STATSMODELS + ": statsmodels.stats.multitest.multipletests",
        {"p": P_STACK},
        _run_adjust,
        _live(*[f"{m}/{i}" for m in ("bonferroni", "sidak", "holm", "bh", "by") for i in (0, 5, 20, 28)]),
        reference=_ref_adjust,
        needs_statsmodels=True,
    ),
    # ---- two-way and repeated measures ----
    Benchmark(
        "Two-way ANOVA, balanced 2 x 3 table", "Two-way ANOVA with interaction",
        STATSMODELS + ": ols(value ~ C(row) * C(column)) with anova_lm",
        {"cells": {"|".join(k): v for k, v in TWO_WAY.items()}},
        _run_two_way,
        _live(*[f"{t}/{q}" for t in ("row", "column", "interaction") for q in ("f", "p")]),
        reference=_ref_two_way,
        needs_statsmodels=True,
    ),
    Benchmark(
        "Repeated-measures ANOVA, 7 subjects x 4 treatments", "Repeated-measures one-way ANOVA (no Geisser-Greenhouse)",
        STATSMODELS + ": statsmodels.stats.anova.AnovaRM",
        {"rows": RM},
        _run_repeated,
        _live("f", "p"),
        reference=_ref_repeated,
        needs_statsmodels=True,
    ),
    # ---- regression and correlation ----
    Benchmark(
        "Linear regression, 5 doses x 3 replicates", "Simple linear regression",
        SCIPY + ": scipy.stats.linregress",
        {"x": XY_X, "y": XY_A},
        _run_regression,
        _live("slope", "intercept", "r2", "p"),
        reference=_ref_regression,
    ),
    Benchmark(
        "Correlation, 5 doses x 3 replicates", "Pearson and Spearman correlation",
        SCIPY + ": scipy.stats.pearsonr, spearmanr",
        {"x": XY_X, "y": XY_A},
        _run_correlation,
        _live("pearson/r", "pearson/p", "spearman/r"),
        reference=_ref_correlation,
    ),
    # ---- contingency ----
    Benchmark(
        "Fisher's exact test, 2 x 2 response table", "Fisher's exact test and relative risk",
        SCIPY + ": scipy.stats.fisher_exact, scipy.stats.contingency.relative_risk",
        {"counts": [[28, 12], [15, 25]]},
        _run_fisher,
        _live("p", "rr"),
        reference=_ref_fisher,
    ),
    Benchmark(
        "Odds ratio, 2 x 2 response table", "Odds ratio",
        STATSMODELS + ": statsmodels.stats.contingency_tables.Table2x2",
        {"counts": [[28, 12], [15, 25]]},
        _run_fisher,
        _live("odds_ratio"),
        reference=_ref_odds_ratio,
        needs_statsmodels=True,
    ),
    Benchmark(
        "Chi-square with Yates correction, 2 x 2", "Chi-square test with continuity correction",
        SCIPY + ": scipy.stats.chi2_contingency(correction=True)",
        {"counts": [[28, 12], [15, 25]]},
        _run_chi2,
        _live("p", "chi2"),
        reference=_ref_chi2,
    ),
    Benchmark(
        "Chi-square, 4 x 2 dose table", "Chi-square test and Cramer's V",
        SCIPY + ": scipy.stats.chi2_contingency",
        {"counts": [[20, 30], [28, 22], [35, 15], [40, 10]]},
        _run_trend_table,
        _live("p", "cramers_v"),
        reference=_ref_trend_table,
    ),
    # ---- survival ----
    Benchmark(
        "Log-rank and Gehan-Breslow, two groups", "Survival curve comparison",
        STATSMODELS + ": statsmodels.duration.survfunc.survdiff",
        {"ta": TA, "ea": EA, "tb": TB, "eb": EB},
        _run_survival,
        _live("logrank/chi2", "logrank/p", "gehan/chi2", "gehan/p"),
        reference=_ref_survival,
        needs_statsmodels=True,
    ),
    # ---- normality, outliers, descriptive ----
    Benchmark(
        "Shapiro-Wilk, 11 men's weights", "Shapiro-Wilk normality test",
        "Shapiro and Wilk (1965), Biometrika 52:591, worked example (W = 0.79, below the 1% critical value)",
        {"x": WEIGHTS},
        _run_shapiro,
        _ex(("statistic", "0.79"), ("p", "<0.01"), ("n", "11")),
    ),
    Benchmark(
        "Grubbs' critical values, Grubbs' table", "Grubbs' outlier test: two-sided 5% critical G",
        "Grubbs (1969), Technometrics 11:1-21, table of 5% critical values",
        {"sizes": [3, 5, 10, 20, 30]},
        _run_grubbs,
        # The engine gives the exact critical values (n = 3: 1.1543, n = 20: 2.7082); the table's rounded entries 1.155 and 2.709
        # sit 0.0007 and 0.0008 away, so these two carry the tolerance 1.5e-3 that the engine's own test uses for this table.
        (Expected("n=3", "1.155", 1.5e-3), Expected("n=5", "1.715"), Expected("n=10", "2.290"), Expected("n=20", "2.709", 1.5e-3), Expected("n=30", "2.908")),
    ),
    Benchmark(
        "Skewness and kurtosis, documented example", "Sample skewness and kurtosis",
        "Spreadsheet function documentation, SKEW and KURT example on ten values",
        {"x": [3, 4, 5, 2, 3, 4, 5, 6, 4, 7]},
        _run_describe,
        _ex(("skewness", "0.359543"), ("kurtosis", "-0.151799637")),
    ),
    Benchmark(
        "Percentiles, documented examples", "Percentiles by the (n + 1) rule",
        "Spreadsheet function documentation, PERCENTILE.EXC and QUARTILE.EXC examples",
        {"a": [1, 2, 3, 6, 6, 6, 7, 8, 9], "b": [6, 7, 15, 36, 39, 40, 41, 42, 43, 47, 49]},
        _run_percentiles,
        _ex(("p25", "2.5"), ("q1", "15"), ("q3", "43")),
    ),
]


# ------------------------------------------------------------------------------------------------ running


def run_benchmark(benchmark: Benchmark) -> list[BenchmarkRow]:
    """Run one benchmark: a row for each expected value. An engine error makes every row disagree, with ``engine`` None."""

    try:
        values = benchmark.run(benchmark.data)
        error = None
    except Exception as exc:  # the row says the engine failed; one failure must not hide the rest
        values, error = {}, exc
    live = benchmark.reference(benchmark.data) if benchmark.reference else {}
    rows = []
    for item in benchmark.expected:
        try:
            engine = None if error else _dig(values, item.key)
        except (KeyError, StopIteration, IndexError, TypeError, ValueError):
            engine = None
        if item.published is None:
            reference = float(live[item.key])
            tol = item.tol if item.tol is not None else LIVE_RTOL * abs(reference) + 1e-12
            ok = engine is not None and math.isfinite(engine) and abs(engine - reference) <= tol
            published: Any = reference
        else:
            published = item.published
            ok = agrees(item.published, engine) or (item.tol is not None and engine is not None and abs(engine - tolerance(item.published)[1]) <= item.tol)
        rows.append(BenchmarkRow(benchmark.name, benchmark.analysis, benchmark.source, item.key, published, engine, bool(ok)))
    return rows


def run_benchmarks(names: list[str] | None = None) -> list[BenchmarkRow]:
    """Run the benchmark set (or just the named entries) and return one row per checked value."""

    rows: list[BenchmarkRow] = []
    for benchmark in BENCHMARKS:
        if names is not None and benchmark.name not in names:
            continue
        if benchmark.needs_statsmodels and statsmodels is None:
            continue
        rows.extend(run_benchmark(benchmark))
    return rows


def summarize(rows: list[BenchmarkRow]) -> BenchmarkSummary:
    """Counts for a run: benchmarks, values, how many agree, the rows that differ, and entries skipped for a missing package."""

    skipped = tuple(b.name for b in BENCHMARKS if b.needs_statsmodels and statsmodels is None)
    return BenchmarkSummary(len({row.name for row in rows}), len(rows), sum(row.ok for row in rows),
                            tuple(row for row in rows if not row.ok), skipped)
