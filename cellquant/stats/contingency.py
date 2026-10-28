"""Contingency tables: counts of subjects in rows (groups, exposures or test results) by columns (outcomes), and
their analysis.

Every table gets Pearson's chi-square test (Yates-corrected for a 2 × 2 table if chosen); a 2 × 2 table also gets
Fisher's exact test, the relative risk, the odds ratio, the difference between the two proportions, the number needed
to treat (or harm) and phi, or, read as a diagnostic test, sensitivity, specificity, predictive values and likelihood
ratios. A table with two columns and three or more ordered rows (or the transpose) also gets the chi-square test for
trend (Cochran-Armitage). Larger tables report Cramér's V.

In a 2 × 2 table, row 1 is the group of interest (treated, exposed, test positive) and column 1 the outcome of
interest (disease, response, condition present).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

from cellquant.errors import StatisticsError
from cellquant.stats.pvalues import format_p, p_summary
from cellquant.stats.results import AnalysisResult, fmt, fmt_ci

TESTS = {"auto": "Fisher's exact test for 2 × 2, chi-square otherwise", "fisher": "Fisher's exact test (2 × 2 only)",
         "chi2": "Chi-square test"}


@dataclass
class ContingencyTable:
    title: str
    rows: list[str]
    columns: list[str]
    counts: np.ndarray  # (rows, columns); NaN while a cell is blank

    def __post_init__(self):
        self.rows = [str(name) for name in self.rows]
        self.columns = [str(name) for name in self.columns]
        self.counts = np.asarray(self.counts, dtype=float).reshape(len(self.rows), len(self.columns))
        if not self.rows or not self.columns:
            raise StatisticsError("A contingency table needs at least one row and one column.")
        if len(set(self.rows)) != len(self.rows) or len(set(self.columns)) != len(self.columns):
            raise StatisticsError("Row titles and column titles must be unique.")

    def checked(self) -> np.ndarray:
        """The counts as integers. Raises when a cell is blank, negative or not a whole number."""

        counts = self.counts
        blank = [f"{self.rows[i]} / {self.columns[j]}" for i, j in zip(*np.nonzero(~np.isfinite(counts)))]
        if blank:
            raise StatisticsError(f"Every cell needs a count (type 0 for none); blank: {', '.join(blank[:4])}"
                                  + ("…" if len(blank) > 4 else "") + ".")
        if (counts < 0).any() or (np.abs(counts - np.round(counts)) > 1e-9).any():
            raise StatisticsError("A contingency table holds counts: whole numbers, 0 or more. For means or percentages use a "
                                  "column or grouped table.")
        return np.round(counts).astype(int)


def wilson(successes: float, n: float, ci: float = 0.95) -> tuple[float, float]:
    """Wilson score interval of a proportion."""

    if n <= 0:
        return float("nan"), float("nan")
    z = stats.norm.ppf(0.5 + ci / 2)
    p = successes / n
    center = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, center - half), min(1.0, center + half)


def newcombe(a: int, n1: int, c: int, n2: int, ci: float = 0.95) -> tuple[float, float]:
    """Newcombe's hybrid score interval of p1 − p2 (his method 10), from the two Wilson intervals."""

    p1, p2 = a / n1, c / n2
    l1, u1 = wilson(a, n1, ci)
    l2, u2 = wilson(c, n2, ci)
    d = p1 - p2
    return d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2), d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)


def chi_square(counts: np.ndarray, yates: bool = False) -> dict:
    """Pearson's chi-square test of independence."""

    chi2, p, df, expected = stats.chi2_contingency(counts, correction=bool(yates) and counts.shape == (2, 2))
    return {"chi2": float(chi2), "p": float(p), "df": int(df), "expected": expected}


def chi_square_trend(counts: np.ndarray) -> dict:
    """Cochran-Armitage chi-square test for trend, for a table with two columns and ordered rows (scores 1, 2, …)
    or two rows and ordered columns."""

    if counts.shape[1] != 2:
        counts = counts.T
    if counts.shape[1] != 2 or counts.shape[0] < 3:
        raise StatisticsError("The test for trend needs two columns (or two rows) and at least three ordered groups.")
    scores = np.arange(1, counts.shape[0] + 1, dtype=float)
    first, totals = counts[:, 0].astype(float), counts.sum(axis=1).astype(float)
    n, r1 = totals.sum(), first.sum()
    p = r1 / n
    t = float((scores * (first - totals * p)).sum())
    variance = p * (1 - p) * float((totals * scores**2).sum() - (totals * scores).sum() ** 2 / n)
    if variance <= 0:
        return {"chi2": float("nan"), "p": float("nan"), "df": 1}
    chi2 = t * t / variance
    return {"chi2": chi2, "p": float(stats.chi2.sf(chi2, 1)), "df": 1}


def two_by_two(counts: np.ndarray, ci: float = 0.95) -> dict:
    """Relative risk (Katz log CI), odds ratio (Woolf logit CI; 0.5 added to every cell when one is 0), difference of
    proportions (Newcombe CI), NNT, phi, and the diagnostic-test reading of a 2 × 2 table."""

    (a, b), (c, d) = (int(value) for value in counts[0]), (int(value) for value in counts[1])
    n1, n2 = a + b, c + d
    z = stats.norm.ppf(0.5 + ci / 2)
    out = {"p1": a / n1, "p2": c / n2}
    if a > 0 and c > 0:
        rr = out["p1"] / out["p2"]
        se = math.sqrt(1 / a - 1 / n1 + 1 / c - 1 / n2)
        out["rr"], out["rr_ci"] = rr, (rr * math.exp(-z * se), rr * math.exp(z * se))
    else:
        out["rr"], out["rr_ci"] = (float("inf") if a > 0 else 0.0 if c > 0 else float("nan")), (float("nan"), float("nan"))
    shift = 0.5 if 0 in (a, b, c, d) else 0.0
    aa, bb, cc, dd = a + shift, b + shift, c + shift, d + shift
    odds = aa * dd / (bb * cc)
    se = math.sqrt(1 / aa + 1 / bb + 1 / cc + 1 / dd)
    out.update(odds_ratio=odds, or_ci=(odds * math.exp(-z * se), odds * math.exp(z * se)), or_corrected=bool(shift))
    difference = out["p1"] - out["p2"]
    out.update(difference=difference, difference_ci=newcombe(a, n1, c, n2, ci))
    low, high = out["difference_ci"]
    out["nnt"] = 1 / abs(difference) if difference else float("inf")
    out["nnt_ci"] = (1 / abs(high), 1 / abs(low)) if low * high > 0 else None  # a CI of the difference spanning 0 has no NNT CI
    denominator = math.sqrt(n1 * n2 * (a + c) * (b + d))
    out["phi"] = (a * d - b * c) / denominator if denominator else float("nan")
    sens, spec = (a / (a + c) if a + c else float("nan")), (d / (b + d) if b + d else float("nan"))
    out["diagnostic"] = {
        "sensitivity": (sens, wilson(a, a + c, ci)), "specificity": (spec, wilson(d, b + d, ci)),
        "ppv": (a / n1, wilson(a, n1, ci)), "npv": (d / n2 if n2 else float("nan"), wilson(d, n2, ci)),
        "lr_positive": sens / (1 - spec) if spec < 1 else float("inf"), "lr_negative": (1 - sens) / spec if spec > 0 else float("inf")}
    return out


def _pct(value: float) -> str:
    return fmt(100 * value) + "%" if math.isfinite(value) else ""


def _pct_ci(interval: tuple[float, float]) -> str:
    return fmt_ci(100 * interval[0], 100 * interval[1]) + "%"


def contingency(table: ContingencyTable, *, test: str = "auto", yates: bool = False, diagnostic: bool = False, ci: float = 0.95,
                alpha: float = 0.05, style: str = "classic") -> AnalysisResult:
    """Analyze a contingency table. ``test``: "auto", "fisher" (2 × 2 only) or "chi2". ``diagnostic``: read a 2 × 2 table
    as rows = test result (positive first), columns = condition (present first)."""

    if test not in TESTS:
        raise StatisticsError(f"test must be one of {', '.join(TESTS)}.")
    if not 0 < ci < 1 or not 0 < alpha < 1:
        raise StatisticsError("The confidence level and alpha must be between 0 and 1.")
    counts = table.checked()
    if counts.shape[0] < 2 or counts.shape[1] < 2:
        raise StatisticsError("A contingency table needs at least two rows and two columns.")
    if (counts.sum(axis=1) == 0).any() or (counts.sum(axis=0) == 0).any():
        raise StatisticsError("Every row and every column needs at least one count.")
    square = counts.shape == (2, 2)
    if test == "fisher" and not square:
        raise StatisticsError("Fisher's exact test here is for 2 × 2 tables; use the chi-square test for larger tables.")
    use_fisher = square and test in ("auto", "fisher")
    result = AnalysisResult("Contingency", table.title, {"test": test, "yates": yates, "diagnostic": diagnostic, "ci": ci, "alpha": alpha,
                                                          "style": style})
    level = f"{100 * ci:g}% CI"
    rows: dict[tuple[str, str], str] = {}
    chi = chi_square(counts, yates)
    n = int(counts.sum())
    values = {"n": n, "chi2": chi, "counts": counts}
    if use_fisher:
        p = float(stats.fisher_exact(counts).pvalue)
        name = "Fisher's exact test"
        values["fisher_p"] = p
    else:
        p, name = chi["p"], "Chi-square" + (" with Yates' correction" if yates and square else "")
    values["p"] = p
    sig = bool(p < alpha)
    section = "Are rows and columns associated?"
    rows[(section, "Test")] = name
    if not use_fisher:
        rows[(section, "Chi-square, df")] = f"{fmt(chi['chi2'])}, {chi['df']}"
    rows[(section, "P value (two-sided)")] = format_p(p, style=style)
    rows[(section, "Significant?")] = f"{'Yes' if sig else 'No'} ({p_summary(p, style)})"
    expected = chi["expected"]
    if not use_fisher and ((expected < 1).any() or (expected < 5).mean() > 0.2):
        result.warnings.append("Some expected counts are small (below 5 in more than a fifth of the cells, or below 1), so the chi-square "
                               "P value is approximate." + (" Fisher's exact test suits a 2 × 2 table." if square else
                                                            " Merge sparse rows or columns, or collect more data."))
    trend = None
    if 2 in counts.shape and max(counts.shape) >= 3:
        trend = chi_square_trend(counts)
        values["trend"] = trend
        groups = "rows" if counts.shape[1] == 2 else "columns"
        rows[(f"Chi-square test for trend (the {groups} in order)", "Chi-square, df")] = f"{fmt(trend['chi2'])}, 1"
        rows[(f"Chi-square test for trend (the {groups} in order)", "P value")] = format_p(trend["p"], style=style)
    if square:
        effect = two_by_two(counts, ci)
        values["effect"] = effect
        first, second = table.rows
        if diagnostic:
            dx = effect["diagnostic"]
            for key, label in (("sensitivity", "Sensitivity"), ("specificity", "Specificity"), ("ppv", "Positive predictive value"),
                               ("npv", "Negative predictive value")):
                value, interval = dx[key]
                rows[("Diagnostic test", label)] = f"{_pct(value)} ({level} {_pct_ci(interval)})"
            rows[("Diagnostic test", "Likelihood ratio (positive)")] = fmt(dx["lr_positive"])
            rows[("Diagnostic test", "Likelihood ratio (negative)")] = fmt(dx["lr_negative"])
            result.warnings.append("Predictive values depend on how common the condition is; they apply only to a population with "
                                   "this table's prevalence.")
        else:
            outcome = table.columns[0]
            rows[("Effect size", f"{outcome}: {first}")] = _pct(effect["p1"])
            rows[("Effect size", f"{outcome}: {second}")] = _pct(effect["p2"])
            rows[("Effect size", "Relative risk")] = f"{fmt(effect['rr'])} ({level} {fmt_ci(*effect['rr_ci'])})"
            rows[("Effect size", "Odds ratio")] = (f"{fmt(effect['odds_ratio'])} ({level} {fmt_ci(*effect['or_ci'])})"
                                                   + (" (0.5 added to every cell)" if effect["or_corrected"] else ""))
            low, high = effect["difference_ci"]
            rows[("Effect size", "Difference of proportions")] = f"{_pct(effect['difference'])} ({level} {_pct_ci((low, high))})"
            nnt = effect["nnt"]
            if effect["difference"] and math.isfinite(nnt):
                interval = effect["nnt_ci"]  # benefit or harm depends on what the outcome is, so the label stays neutral
                rows[("Effect size", "Number needed to treat (1 / |difference|)")] = (fmt(nnt) + (f" ({level} {fmt_ci(*interval)})" if interval else
                                                                               " (CI not defined: the difference's CI includes 0)"))
            rows[("Effect size", "Phi")] = fmt(effect["phi"])
    else:
        v = math.sqrt(chi["chi2"] / (n * (min(counts.shape) - 1)))
        values["cramers_v"] = v
        rows[("Effect size", "Cramér's V")] = fmt(v)
    rows[("Data", "Total")] = str(n)
    rows[("Data", "Table")] = f"{counts.shape[0]} rows × {counts.shape[1]} columns"
    for (part, label), text in rows.items():
        result.section(part).add(label, text, text)
    frame = pd.DataFrame({"Value": rows})
    frame.index.names = ["section", "statistic"]
    values["wide"] = frame
    values["row_percent"] = 100 * counts / counts.sum(axis=1, keepdims=True)
    result.values.update(values)
    methods = (f"{name} (two-sided, alpha = {alpha:g})" if use_fisher else
               f"Pearson's chi-square test{' with Yates continuity correction' if yates and square else ''} (two-sided, alpha = {alpha:g})")
    if trend is not None:
        methods += "; Cochran-Armitage chi-square test for trend with equally spaced scores"
    if square and diagnostic:
        methods += f"; sensitivity, specificity and predictive values with Wilson score {level}s"
    elif square:
        methods += (f"; relative risk with Katz's log {level}, odds ratio with Woolf's logit {level}"
                    + (" (0.5 added to every cell, as one was 0)" if values["effect"]["or_corrected"] else "")
                    + f", difference of proportions with Newcombe's hybrid score {level}")
    result.methods = methods + "."
    if n < 20:
        result.warnings.append("Fewer than 20 subjects: CIs are wide and approximate.")
    return result


__all__ = ["ContingencyTable", "TESTS", "chi_square", "chi_square_trend", "contingency", "newcombe", "two_by_two", "wilson"]
