"""Checks and result rows shared by the analyses."""

from __future__ import annotations

import numpy as np
from scipy import stats

from cellquant.errors import StatisticsError
from cellquant.stats.pvalues import format_p, p_summary
from cellquant.stats.results import Section

ALTERNATIVES = ("two-sided", "greater", "less")


def check_options(alternative: str, ci: float, alpha: float) -> str:
    alternative = {"two-tailed": "two-sided", "two_sided": "two-sided"}.get(alternative, alternative)
    if alternative not in ALTERNATIVES:
        raise StatisticsError(f"alternative must be one of {', '.join(ALTERNATIVES)}, not {alternative!r}.")
    if not 0 < ci < 1:
        raise StatisticsError("The confidence level must be between 0 and 1 (for example 0.95).")
    if not 0 < alpha < 1:
        raise StatisticsError("alpha must be between 0 and 1 (for example 0.05).")
    return alternative


def finite(values) -> tuple[np.ndarray, int]:
    """The values without blanks, and how many blanks were dropped."""

    array = np.asarray(values, dtype=float).ravel()
    keep = np.isfinite(array)
    return array[keep], int((~keep).sum())


def pairs(a, b, names: tuple[str, str]) -> tuple[np.ndarray, np.ndarray, int]:
    """Rows where both values are present, and how many rows were dropped."""

    a = np.asarray(a, dtype=float).ravel()
    b = np.asarray(b, dtype=float).ravel()
    if a.size != b.size:
        raise StatisticsError(f"Paired columns must have the same number of rows ({names[0]}: {a.size}, {names[1]}: {b.size}).")
    keep = np.isfinite(a) & np.isfinite(b)
    present = np.isfinite(a) | np.isfinite(b)
    return a[keep], b[keep], int((present & ~keep).sum())


def t_p(t: float, df: float, alternative: str) -> float:
    if not (np.isfinite(t) and df > 0):
        return np.nan
    if alternative == "greater":
        return float(stats.t.sf(t, df))
    if alternative == "less":
        return float(stats.t.cdf(t, df))
    return float(min(1.0, 2.0 * stats.t.sf(abs(t), df)))


def t_critical(ci: float, df: float) -> float:
    return float(stats.t.ppf(1.0 - (1.0 - ci) / 2.0, df)) if df > 0 else np.nan


def tails_text(alternative: str, names: tuple[str, str] | None = None) -> str:
    if alternative == "two-sided":
        return "Two-tailed"
    sign = ">" if alternative == "greater" else "<"
    if names is None:
        return f"One-tailed (predicted {sign} hypothetical)"
    return f"One-tailed (predicted {names[1]} {sign} {names[0]})"


def p_rows(section: Section, p: float, alpha: float, style: str, alternative: str | None = None, names: tuple[str, str] | None = None, label: str = "Significantly different") -> None:
    """P value, P value summary, significant yes/no and (when given) the tails, as results sheets usually list them."""

    section.add("P value", p, format_p(p, style=style))
    section.add("P value summary", p_summary(p, style), p_summary(p, style))
    significant = bool(p < alpha) if np.isfinite(p) else None
    section.add(f"{label} (P < {alpha:g})?", significant, "" if significant is None else ("Yes" if significant else "No"))
    if alternative is not None:
        section.add("One- or two-tailed P value?", alternative, tails_text(alternative, names))
