"""Normality and lognormality tests for one column.

Each test asks whether the values could plausibly come from a Gaussian distribution. A small P
says they probably do not. "Passed" means P > alpha: the data are consistent with a Gaussian
distribution, which is not proof that they are Gaussian (small samples pass easily).

- D'Agostino-Pearson omnibus K² (n ≥ 8): combines skewness and kurtosis, each turned into a z score.
- Shapiro-Wilk W (n ≥ 3): Royston's algorithm, as in R's ``shapiro.test``.
- Anderson-Darling A² (n ≥ 8): mean and SD estimated from the data. The unadjusted A² is
  reported; P comes from the adjusted
  A²* = A²(1 + 0.75/n + 2.25/n²) (D'Agostino and Stephens 1986). Needs statsmodels.
- Kolmogorov-Smirnov (n ≥ 5): the largest distance between the data's cumulative distribution and
  a normal one with the sample mean and SD, with the Lilliefors P value by the Dallal-Wilkinson
  approximation. That approximation is valid for P < 0.1; above it, P comes from statsmodels'
  simulated Lilliefors table. Needs statsmodels.
"""

from __future__ import annotations

import warnings

import numpy as np
from scipy import stats

from cellquant.errors import StatisticsError
from cellquant.stats._common import finite
from cellquant.stats.pvalues import format_p, p_summary
from cellquant.stats.results import AnalysisResult

# key: (Prism's name, statistic label, minimum n)
TESTS = {
    "dagostino": ("D'Agostino & Pearson test", "K2", 8),
    "shapiro": ("Shapiro-Wilk test", "W", 3),
    "anderson": ("Anderson-Darling test", "A2", 8),
    "ks": ("Kolmogorov-Smirnov test", "KS distance", 5),
}


def statsmodels_diagnostic():
    """``statsmodels.stats.diagnostic``, imported on first use."""

    try:
        from statsmodels.stats import diagnostic
    except ImportError:
        raise StatisticsError("This test needs statsmodels. Install it with: pip install \"cellquant[stats]\"") from None
    return diagnostic


def _run(key: str, x: np.ndarray) -> tuple[float, float]:
    if key == "dagostino":
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # scipy warns that kurtosistest is approximate below n = 20
            result = stats.normaltest(x)
        return float(result.statistic), float(result.pvalue)
    if key == "shapiro":
        result = stats.shapiro(x)
        return float(result.statistic), float(result.pvalue)
    diagnostic = statsmodels_diagnostic()
    if key == "anderson":
        statistic, p = diagnostic.normal_ad(x)
        return float(statistic), float(p)
    statistic, p = diagnostic.lilliefors(x, dist="norm", pvalmethod="approx")
    return float(statistic), float(p)


def normality(values, alpha: float = 0.05, tests=None, *, name: str = "", table_title: str = "", style: str = "GP", _log: bool = False) -> AnalysisResult:
    """Run the normality tests (all four unless ``tests`` names some of TESTS' keys).

    ``values`` holds, per test key, ``statistic``, ``p``, ``passed`` (P > alpha), ``n`` and
    ``note`` (why the test was not run, else empty)."""

    if not 0 < alpha < 1:
        raise StatisticsError("alpha must be between 0 and 1 (for example 0.05).")
    keys = list(TESTS) if tests is None else list(tests)
    unknown = [key for key in keys if key not in TESTS]
    if unknown:
        raise StatisticsError(f"Unknown normality test(s): {', '.join(unknown)}; choose from {', '.join(TESTS)}.")
    x, blanks = finite(values)
    kind = "Lognormality" if _log else "Normality"
    result = AnalysisResult(f"{kind} tests", table_title, {"alpha": alpha, "tests": keys, "style": style})
    if _log:
        if np.any(x <= 0):
            raise StatisticsError("Lognormality tests need every value to be greater than 0 (the log of zero or a negative number is undefined).")
        x = np.log(x)
    identical = x.size > 1 and np.all(x == x[0])
    for key in keys:
        title, label, minimum = TESTS[key]
        section = result.section(f"{title}{f' ({name})' if name else ''}")
        note = ""
        if x.size < minimum:
            note = f"n too small (needs at least {minimum} values, has {x.size})"
        elif identical:
            note = "all values are identical"
        if note:
            section.add(label, None, note)
            result.values[key] = {"statistic": np.nan, "p": np.nan, "passed": None, "n": int(x.size), "note": note}
            continue
        statistic, p = _run(key, x)
        passed = bool(p > alpha)
        section.add(label, statistic)
        section.add("P value", p, format_p(p, style=style))
        section.add(f"Passed {kind.lower()} test (alpha={alpha:g})?", passed)
        section.add("P value summary", p_summary(p, style), p_summary(p, style))
        result.values[key] = {"statistic": statistic, "p": p, "passed": passed, "n": int(x.size), "note": ""}
    result.section("Data analyzed").add("Number of values", int(x.size))
    if blanks:
        result.warnings.append(f"{blanks} blank cell(s) left out.")
    on = " on the natural logs of the values" if _log else ""
    result.methods = f"{kind} tested{on} with the {', '.join(TESTS[key][0] for key in keys)} (alpha = {alpha:g})."
    return result


def lognormality(values, alpha: float = 0.05, tests=None, *, name: str = "", table_title: str = "", style: str = "GP") -> AnalysisResult:
    """The normality tests on ln(values): passing means the data are consistent with a lognormal
    distribution. Refused when any value is ≤ 0. The log base does not change any result."""

    return normality(values, alpha, tests, name=name, table_title=table_title, style=style, _log=True)
