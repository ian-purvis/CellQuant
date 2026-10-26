"""Column statistics (descriptive statistics for one column) and one-sample tests.

Percentiles use the (n+1)p rule: the p-th percentile is the value at rank (n+1)p of the sorted
data, interpolated linearly between neighbouring ranks and clamped to the minimum and maximum
when (n+1)p falls outside 1..n. This is Hyndman and Fan's type 6 (``numpy`` method
``"weibull"``, the "exclusive" percentile of spreadsheets inside its range), the method most statistics
packages use for percentiles, quartiles and the median.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from cellquant.errors import StatisticsError
from cellquant.stats._common import check_options, finite, p_rows, t_critical, t_p, tails_text
from cellquant.stats._rank import hodges_lehmann_one_sample, signed_rank_test
from cellquant.stats.pvalues import format_p
from cellquant.stats.results import AnalysisResult, fmt, fmt_ci
from cellquant.stats.tables import ColumnTable

_PERCENTILES = (10, 25, 50, 75, 90)


def percentiles(values, q) -> np.ndarray:
    """Percentiles (0-100) by the (n+1)p rule (Hyndman-Fan type 6)."""

    x, _ = finite(values)
    if not x.size:
        return np.full(np.shape(q), np.nan)
    return np.percentile(x, q, method="weibull")


def skewness(x: np.ndarray) -> float:
    """Sample-adjusted skewness G1 = n / ((n-1)(n-2)) · Σ((x - x̄)/s)³, s the SD with n-1 (the usual spreadsheet SKEW)."""

    n = x.size
    s = np.std(x, ddof=1) if n > 1 else np.nan
    if n < 3 or not s > 0:
        return np.nan
    return float(n / ((n - 1) * (n - 2)) * np.sum(((x - x.mean()) / s) ** 3))


def kurtosis(x: np.ndarray) -> float:
    """Sample-adjusted excess kurtosis G2 = n(n+1)/((n-1)(n-2)(n-3)) · Σ((x - x̄)/s)⁴ - 3(n-1)²/((n-2)(n-3))
    (the usual spreadsheet KURT; 0 for a normal distribution)."""

    n = x.size
    s = np.std(x, ddof=1) if n > 1 else np.nan
    if n < 4 or not s > 0:
        return np.nan
    fourth = np.sum(((x - x.mean()) / s) ** 4)
    return float(n * (n + 1) / ((n - 1) * (n - 2) * (n - 3)) * fourth - 3 * (n - 1) ** 2 / ((n - 2) * (n - 3)))


def describe(values, ci: float = 0.95) -> dict[str, float]:
    """The column statistics as numbers (NaN where a statistic is undefined for these values)."""

    x, blanks = finite(values)
    n = int(x.size)
    nan = np.nan
    out: dict[str, float] = {"n": n, "blanks": blanks}
    p10, p25, median, p75, p90 = percentiles(x, _PERCENTILES)
    out.update(
        minimum=float(x.min()) if n else nan, p25=float(p25), median=float(median), p75=float(p75),
        maximum=float(x.max()) if n else nan, p10=float(p10), p90=float(p90),
    )
    out["range"] = out["maximum"] - out["minimum"]
    mean = float(x.mean()) if n else nan
    sd = float(np.std(x, ddof=1)) if n > 1 else nan
    sem = sd / np.sqrt(n) if n > 1 else nan
    half = t_critical(ci, n - 1) * sem if n > 1 else nan
    out.update(mean=mean, sd=sd, sem=float(sem), ci_low=float(mean - half), ci_high=float(mean + half))
    out["cv_percent"] = 100.0 * sd / mean if n > 1 and mean != 0 else nan
    positive = n > 0 and bool(np.all(x > 0))
    if positive:
        logs = np.log(x)
        log_mean = float(logs.mean())
        log_sd = float(np.std(logs, ddof=1)) if n > 1 else nan
        log_half = t_critical(ci, n - 1) * log_sd / np.sqrt(n) if n > 1 else nan
        out.update(
            geomean=float(np.exp(log_mean)), geosd_factor=float(np.exp(log_sd)),
            geomean_ci_low=float(np.exp(log_mean - log_half)), geomean_ci_high=float(np.exp(log_mean + log_half)),
            harmonic_mean=float(n / np.sum(1.0 / x)),
        )
    else:
        out.update(geomean=nan, geosd_factor=nan, geomean_ci_low=nan, geomean_ci_high=nan, harmonic_mean=nan)
    out["quadratic_mean"] = float(np.sqrt(np.mean(x**2))) if n else nan
    out["skewness"] = skewness(x)
    out["kurtosis"] = kurtosis(x)
    out["sum"] = float(x.sum())
    return out


def column_statistics(
    values,
    ci: float = 0.95,
    hypothetical: float | None = None,
    *,
    name: str = "",
    table_title: str = "",
    alternative: str = "two-sided",
    alpha: float = 0.05,
    style: str = "classic",
    zero_method: str = "exclude",
) -> AnalysisResult:
    """Column statistics for one column, plus (with ``hypothetical``) a one-sample t test and
    a Wilcoxon signed-rank test against that value.

    Statistics: n; minimum, 10th, 25th, 50th (median), 75th and 90th percentiles, maximum, range;
    mean, SD (n - 1 denominator), SEM = SD/√n, t-based CI of the mean = x̄ ± t(1-α/2, n-1)·SEM;
    CV% = 100·SD/mean; geometric mean = exp(mean of ln x) with geometric SD factor exp(SD of ln x)
    and CI exp(mean ln x ± t·SEM of ln x); harmonic mean = n / Σ(1/x); quadratic mean = √(mean x²);
    skewness and excess kurtosis (sample-adjusted, see ``skewness`` and ``kurtosis``); sum.
    The geometric and harmonic means need every value > 0 and are blank otherwise.
    """

    alternative = check_options(alternative, ci, alpha)
    d = describe(values, ci)
    level = f"{100 * ci:g}%"
    result = AnalysisResult("Column statistics", table_title, {"ci": ci, "hypothetical": hypothetical, "alternative": alternative, "alpha": alpha, "style": style}, values=dict(d))
    main = result.section(f"Column statistics{f': {name}' if name else ''}")
    main.add("Number of values", d["n"])
    if d["blanks"]:
        main.add("Number of blank cells", d["blanks"])
    for label, key in (("Minimum", "minimum"), ("25% Percentile", "p25"), ("Median", "median"), ("75% Percentile", "p75"), ("Maximum", "maximum"), ("Range", "range"),
                       ("10% Percentile", "p10"), ("90% Percentile", "p90"), ("Mean", "mean"), ("Std. Deviation", "sd"), ("Std. Error of Mean", "sem")):
        main.add(label, d[key])
    main.add(f"Lower {level} CI of mean", d["ci_low"])
    main.add(f"Upper {level} CI of mean", d["ci_high"])
    main.add("Coefficient of variation", d["cv_percent"], f"{fmt(d['cv_percent'])}%" if np.isfinite(d["cv_percent"]) else "")
    means = result.section("Geometric and other means")
    means.add("Geometric mean", d["geomean"])
    means.add("Geometric SD factor", d["geosd_factor"])
    means.add(f"Lower {level} CI of geo. mean", d["geomean_ci_low"])
    means.add(f"Upper {level} CI of geo. mean", d["geomean_ci_high"])
    means.add("Harmonic mean", d["harmonic_mean"])
    means.add("Quadratic mean", d["quadratic_mean"])
    shape = result.section("Distribution shape")
    shape.add("Skewness", d["skewness"])
    shape.add("Kurtosis", d["kurtosis"])
    result.section("Sum").add("Sum", d["sum"])
    if d["n"] < 2:
        result.warnings.append("SD, SEM and the CI need at least 2 values.")
    if d["n"] and not np.isfinite(d["geomean"]):
        result.warnings.append("The geometric and harmonic means need every value to be greater than 0.")
    if d["n"] >= 2 and not d["sd"] > 0:
        result.warnings.append("All values are identical, so skewness, kurtosis and CV are undefined or zero.")
    result.methods = f"Column statistics with a t-based {level} CI of the mean; percentiles by the (n+1)p rule."
    if hypothetical is not None:
        for test in (one_sample_t(values, hypothetical, ci=ci, alternative=alternative, alpha=alpha, style=style),
                     wilcoxon_signed_rank(values, hypothetical, ci=ci, alternative=alternative, alpha=alpha, style=style, zero_method=zero_method)):
            result.sections.extend(test.sections)
            result.warnings.extend(test.warnings)
            prefix = "t_test" if test.analysis.startswith("One") else "wilcoxon"
            result.values.update({f"{prefix}_{key}": value for key, value in test.values.items()})
            result.methods += " " + test.methods
    return result


def one_sample_t(values, hypothetical: float = 0.0, *, ci: float = 0.95, alternative: str = "two-sided", alpha: float = 0.05, style: str = "classic", table_title: str = "") -> AnalysisResult:
    """One-sample t test: t = (x̄ - μ0) / (s/√n), df = n - 1. "greater" predicts x̄ > μ0.

    R squared (partial eta squared) = t² / (t² + df)."""

    alternative = check_options(alternative, ci, alpha)
    x, blanks = finite(values)
    n = x.size
    if n < 2:
        raise StatisticsError(f"A one-sample t test needs at least 2 values (this column has {n}).")
    mean = float(x.mean())
    sd = float(np.std(x, ddof=1))
    se = sd / np.sqrt(n)
    df = n - 1
    difference = mean - hypothetical
    result = AnalysisResult("One sample t test", table_title, {"hypothetical": hypothetical, "ci": ci, "alternative": alternative, "alpha": alpha})
    if se > 0:
        t = difference / se
    else:
        t = np.nan
        result.warnings.append("All values are identical (SD = 0), so t and P are undefined.")
    p = t_p(t, df, alternative)
    half = t_critical(ci, df) * se
    r2 = t * t / (t * t + df) if np.isfinite(t) else np.nan
    level = f"{100 * ci:g}%"
    section = result.section("One sample t test")
    section.add("Theoretical mean", float(hypothetical))
    section.add("Actual mean", mean)
    section.add("Number of values", n)
    section.add("t, df", (t, df), f"t={fmt(t)}, df={df}")
    p_rows(section, p, alpha, style, alternative)
    size = result.section("How big is the discrepancy?")
    size.add("Discrepancy", difference)
    size.add("SD of discrepancy", sd)
    size.add("SEM of discrepancy", se)
    size.add(f"{level} confidence interval", (difference - half, difference + half), fmt_ci(difference - half, difference + half))
    size.add("R squared (partial eta squared)", r2)
    if blanks:
        result.warnings.append(f"{blanks} blank cell(s) left out.")
    result.values = {"t": t, "df": df, "p": p, "mean": mean, "hypothetical": float(hypothetical), "difference": difference, "sd": sd, "se": se,
                     "ci_low": float(difference - half), "ci_high": float(difference + half), "r_squared": float(r2), "n": n}
    result.methods = f"One-sample t test against {hypothetical:g} ({tails_text(alternative).lower()} P)."
    return result


def wilcoxon_signed_rank(
    values,
    hypothetical: float = 0.0,
    *,
    ci: float = 0.95,
    alternative: str = "two-sided",
    alpha: float = 0.05,
    style: str = "classic",
    zero_method: str = "exclude",
    exact: bool | None = None,
    continuity: bool = False,
    table_title: str = "",
) -> AnalysisResult:
    """Wilcoxon signed-rank test of whether the median differs from ``hypothetical``.

    Differences d = x - hypothetical are ranked by |d| (ties get the average rank). W = Σ signed
    ranks = (sum of positive ranks) - (sum of negative ranks). P is exact, ties included, for fewer
    than 200 nonzero differences, otherwise Gaussian with tie correction and no continuity
    correction (``continuity=True`` adds R's 0.5). See ``_rank.signed_rank_test`` for
    ``zero_method``."""

    alternative = check_options(alternative, ci, alpha)
    x, blanks = finite(values)
    if x.size < 1:
        raise StatisticsError("The Wilcoxon signed-rank test needs at least one value.")
    result = AnalysisResult("Wilcoxon signed rank test", table_title, {"hypothetical": hypothetical, "ci": ci, "alternative": alternative, "alpha": alpha, "zero_method": zero_method, "continuity": continuity})
    section = result.section("Wilcoxon Signed Rank Test")
    section.add("Theoretical median", float(hypothetical))
    section.add("Actual median", float(np.median(x)))
    section.add("Number of values", int(x.size))
    signed_rank_rows(result, section, x - hypothetical, ci, alternative, alpha, style, zero_method, exact, continuity, None)
    result.values["median"] = float(np.median(x))
    result.values["hypothetical"] = float(hypothetical)
    result.values["discrepancy"] = float(np.median(x)) - hypothetical
    section.add("Discrepancy (median - hypothetical)", result.values["discrepancy"])
    if blanks:
        result.warnings.append(f"{blanks} blank cell(s) left out.")
    result.methods = f"Wilcoxon signed-rank test against {hypothetical:g} ({tails_text(alternative).lower()}, zeros: {zero_method}, {'exact' if result.values['exact'] else 'normal approximation'} P)."
    return result


def signed_rank_rows(result: AnalysisResult, section, d: np.ndarray, ci: float, alternative: str, alpha: float, style: str,
                     zero_method: str, exact: bool | None, continuity: bool, names: tuple[str, str] | None) -> None:
    """Shared by the one-sample and matched-pairs Wilcoxon tests: rows and values for differences ``d``."""

    test = signed_rank_test(d, alternative, zero_method, exact, continuity)
    w = test.positive - test.negative
    section.add("Sum of signed ranks (W)", w)
    section.add("Sum of positive ranks", test.positive)
    section.add("Sum of negative ranks", test.negative)
    p_rows(section, test.p, alpha, style, alternative, names)
    section.add("Exact or estimate?", test.exact, "Exact" if test.exact else "Estimate (Gaussian approximation)")
    section.add("Zero differences", test.n_zero, f"{test.n_zero} ({'excluded' if zero_method == 'exclude' else 'ranked, Pratt'})")
    shift = hodges_lehmann_one_sample(d, ci, exact=test.exact)
    size = result.section("Median of differences" if names else "Hodges-Lehmann estimate")
    size.add("Hodges-Lehmann pseudo-median", shift.estimate)
    size.add(f"{100 * shift.confidence:.2f}% CI", (shift.low, shift.high), fmt_ci(shift.low, shift.high))
    if np.isfinite(test.min_p) and test.min_p > alpha:
        what = f"{test.n_used} nonzero difference(s)"
        result.warnings.append(f"With {what}, the smallest P this test can give is {format_p(test.min_p, style=style)}, "
                               f"which is above alpha = {alpha:g}: it cannot find a significant difference whatever the data.")
    if test.n_used == 0:
        result.warnings.append("Every difference is zero, so the test has no information.")
    if abs(shift.confidence - ci) > 0.005:
        result.warnings.append(f"The nearest confidence level these values allow is {100 * shift.confidence:.2f}%, not {100 * ci:g}%.")
    result.values.update(w=w, sum_positive=test.positive, sum_negative=test.negative, p=test.p, z=test.z, exact=test.exact, min_p=test.min_p, n_used=test.n_used,
                         n_zero=test.n_zero, hodges_lehmann=shift.estimate, ci_low=shift.low, ci_high=shift.high, ci_achieved=shift.confidence, n=int(d.size))


def column_statistics_table(table: ColumnTable, ci: float = 0.95, hypothetical: float | None = None, **options) -> pd.DataFrame:
    """Every column's statistics side by side (rows are statistics, columns are the table's columns),
    the way a column statistics sheet is usually laid out. Cells hold display text."""

    out = {}
    for column in table.columns:
        result = column_statistics(column.values, ci, hypothetical, table_title=table.title, **options)
        out[column.name] = {(row_section(section.title), row.label): row.formatted for section in result.sections for row in section.rows}
    frame = pd.DataFrame(out)
    frame.index.names = ["section", "statistic"]
    return frame


def row_section(title: str) -> str:
    return title.split(":")[0]


__all__ = ["column_statistics", "column_statistics_table", "describe", "kurtosis", "one_sample_t", "percentiles", "skewness", "wilcoxon_signed_rank"]
