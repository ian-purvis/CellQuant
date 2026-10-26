"""Identify outliers in each column of a table: ROUT, Grubbs' test and iterative Grubbs'.

Each method returns the flagged values and a cleaned copy of the table in which the flagged cells
are blank. The table passed in is never modified, and blank cells keep their rows, so a paired
table stays lined up.

ROUT (Motulsky and Brown 2006, BMC Bioinformatics 7:123), for a column of values (the model is a
constant, K = 1 parameter):

1. Robust fit. Starting from the median, the constant μ minimizes the Lorentzian (Cauchy) merit
   function Σ ln(1 + ((x - μ)/RSDR)²), where RSDR, the robust SD of the residuals, is the 68.27th
   percentile of |x - μ| times N/(N - K). RSDR and μ are updated in turn until μ stops changing.
2. Outlier test. Each residual gives t = |x - μ|/RSDR and a two-tailed P from the t distribution
   with N - K df. The Benjamini-Hochberg step-up rule is applied at the desired FDR Q (Prism's
   default Q = 1%): with P sorted ascending, the j most extreme values are outliers for the largest
   j with P(j) ≤ j·Q/N, with j at most 30% of N (at least 1), as Prism allows up to 30% outliers.
   Prism fits Y = M, M a robust mean, and needs n ≥ 3; so does this engine.

Limits of this implementation: it follows the published description, not Prism's code. The
percentile uses linear interpolation; the paper's further details (for example how far it iterates
the robust fit, or whether only the most extreme residuals are tested) may differ, so on borderline
values the flagged set can differ from Prism's. If more than 68% of the values are identical,
RSDR is 0 and nothing is flagged. Only a constant model is fitted (no curve).

Grubbs' test (two-sided): G = max |x - x̄| / s. Its critical value at alpha is
((n - 1)/√n) · √(t² / (n - 2 + t²)) with t the upper α/(2n) point of t with n - 2 df (Grubbs 1969);
the P value is the matching Bonferroni bound P = min(1, 2n · P(T_{n-2} > t_G)) with
t_G = √(n(n - 2)G² / ((n - 1)² - nG²)). Iterative Grubbs' repeats the test after removing each
outlier, down to 3 values; it can be fooled when two outliers mask each other.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import optimize, stats

from cellquant.errors import StatisticsError
from cellquant.stats.pvalues import format_p
from cellquant.stats.results import AnalysisResult, fmt
from cellquant.stats.tables import Column, ColumnTable

METHODS = ("rout", "grubbs", "iterative_grubbs")
MIN_N = 3


def _rsdr(residuals: np.ndarray, k: int = 1) -> float:
    n = residuals.size
    return float(np.percentile(np.abs(residuals), 68.27) * n / (n - k))


def _cauchy_location(x: np.ndarray, scale: float) -> float:
    """The μ minimizing Σ ln(1 + ((x - μ)/scale)²): a grid over the data range, then a bounded refine."""

    def merit(mu: float) -> float:
        return float(np.sum(np.log1p(((x - mu) / scale) ** 2)))

    grid = np.unique(np.concatenate([x, np.linspace(x.min(), x.max(), 201)]))
    values = np.sum(np.log1p(((x[None, :] - grid[:, None]) / scale) ** 2), axis=1)
    best = int(np.argmin(values))
    lo = grid[max(best - 1, 0)]
    hi = grid[min(best + 1, grid.size - 1)]
    if hi > lo:
        refined = optimize.minimize_scalar(merit, bounds=(lo, hi), method="bounded", options={"xatol": 1e-10 * max(1.0, abs(grid[best]))})
        if refined.fun <= values[best]:
            return float(refined.x)
    return float(grid[best])


MAX_OUTLIER_FRACTION = 0.30


def max_outliers(n: int) -> int:
    """Most values ROUT may flag: 30% of n (Prism: "up to 30% of the values can be outliers"),
    rounded down, but at least 1 so that a column of 3 can still have one."""

    return max(1, int(math.floor(MAX_OUTLIER_FRACTION * n + 1e-9)))


def rout_fit(values, q: float = 0.01) -> dict:
    """ROUT on one column (see the module docstring). Returns the robust centre, RSDR, each value's
    t and P, and a boolean mask of outliers (blanks are never outliers)."""

    if not 0 < q < 1:
        raise StatisticsError("Q must be between 0 and 1 (for example 0.01 for 1%).")
    x_all = np.asarray(values, dtype=float)
    keep = np.isfinite(x_all)
    x = x_all[keep]
    n = x.size
    mask = np.zeros(x_all.shape, bool)
    out = {"centre": np.nan, "rsdr": np.nan, "t": np.full(x_all.shape, np.nan), "p": np.full(x_all.shape, np.nan), "mask": mask, "n": n, "note": ""}
    if n < MIN_N:
        out["note"] = f"needs at least {MIN_N} values"
        return out
    mu = float(np.median(x))
    scale = _rsdr(x - mu)
    seen = []
    for _ in range(50):
        if not scale > 0:
            break
        new = _cauchy_location(x, scale)
        new_scale = _rsdr(x - new)
        tol = 1e-8 * max(1.0, scale)
        done = abs(new - mu) <= tol and abs(new_scale - scale) <= tol
        cycling = any(abs(new - a) <= tol and abs(new_scale - b) <= tol for a, b in seen)
        seen.append((new, new_scale))
        mu, scale = new, new_scale
        if done or cycling:
            break
    out["centre"], out["rsdr"] = mu, scale
    if not scale > 0:
        out["note"] = "more than 68% of the values are identical, so the robust SD is 0 and no outliers are flagged"
        return out
    t = np.abs(x - mu) / scale
    p = 2 * stats.t.sf(t, n - 1)
    order = np.argsort(p, kind="mergesort")
    cap = max_outliers(n)
    passing = np.nonzero(p[order] <= q * np.arange(1, n + 1) / n)[0]
    passing = passing[passing < cap]
    flagged = np.zeros(n, bool)
    if passing.size:
        flagged[order[: passing.max() + 1]] = True
    mask[np.nonzero(keep)[0][flagged]] = True
    out["t"][keep], out["p"][keep] = t, p
    return out


def _grubbs_once(x: np.ndarray, alpha: float) -> dict:
    n = x.size
    mean, sd = float(x.mean()), float(np.std(x, ddof=1))
    if not sd > 0:
        return {"index": None, "g": np.nan, "critical": np.nan, "p": np.nan, "outlier": False}
    dev = np.abs(x - mean)
    index = int(np.argmax(dev))
    g = float(dev[index] / sd)
    tc = stats.t.isf(alpha / (2 * n), n - 2)
    critical = (n - 1) / math.sqrt(n) * math.sqrt(tc * tc / (n - 2 + tc * tc))
    denom = (n - 1) ** 2 - n * g * g
    p = 0.0 if denom <= 0 else float(min(1.0, 2 * n * stats.t.sf(math.sqrt(n * (n - 2) * g * g / denom), n - 2)))
    return {"index": index, "g": g, "critical": float(critical), "p": p, "outlier": bool(g > critical)}


def grubbs_fit(values, alpha: float = 0.05, iterative: bool = False) -> dict:
    """Grubbs' test (two-sided) on one column; ``iterative`` repeats it after each removal.
    Returns a boolean mask of outliers and one record per round (G, critical G, P)."""

    if not 0 < alpha < 1:
        raise StatisticsError("alpha must be between 0 and 1 (for example 0.05).")
    x_all = np.asarray(values, dtype=float)
    positions = list(np.nonzero(np.isfinite(x_all))[0])
    mask = np.zeros(x_all.shape, bool)
    rounds = []
    while len(positions) >= MIN_N:
        test = _grubbs_once(x_all[positions], alpha)
        record = {k: v for k, v in test.items() if k != "index"}
        record["n"] = len(positions)
        if test["index"] is not None:
            record["row"] = int(positions[test["index"]]) + 1
            record["value"] = float(x_all[positions[test["index"]]])
        rounds.append(record)
        if not test["outlier"]:
            break
        mask[positions[test["index"]]] = True
        del positions[test["index"]]
        if not iterative:
            break
    return {"mask": mask, "rounds": rounds, "n": int(np.isfinite(x_all).sum())}


def identify_outliers(table: ColumnTable, method: str = "rout", *, q: float = 0.01, alpha: float = 0.05, columns=None,
                      style: str = "GP") -> AnalysisResult:
    """Prism's "Identify outliers" for each column: ``method`` "rout" (FDR ``q``, default 1%),
    "grubbs" or "iterative_grubbs" (``alpha``, default 0.05).

    ``values["outliers"]`` maps each column to a list of {"row" (1-based), "value"};
    ``values["cleaned"]`` is a new ColumnTable with those cells blank; ``values["masks"]`` holds
    the boolean masks. The input table is not changed."""

    if method not in METHODS:
        raise StatisticsError(f"Unknown method {method!r}; choose one of {', '.join(METHODS)}.")
    names = list(columns) if columns is not None else table.names
    label = {"rout": f"ROUT (Q = {100 * q:g}%)", "grubbs": f"Grubbs' test (alpha = {alpha:g}, two-sided)",
             "iterative_grubbs": f"Iterative Grubbs' (alpha = {alpha:g}, two-sided)"}[method]
    result = AnalysisResult("Identify outliers", table.title, {"method": method, "q": q, "alpha": alpha})
    result.section("Method").add("Method", label)
    outliers: dict[str, list] = {}
    masks: dict[str, np.ndarray] = {}
    cleaned = []
    details: dict[str, dict] = {}
    for column in table.columns:
        values = column.values.copy()
        if column.name not in names:
            cleaned.append(Column(column.name, values))
            continue
        if method == "rout":
            fit = rout_fit(values, q)
            mask = fit["mask"]
            details[column.name] = {k: v for k, v in fit.items() if k != "mask"}
        else:
            fit = grubbs_fit(values, alpha, iterative=method == "iterative_grubbs")
            mask = fit["mask"]
            details[column.name] = {"rounds": fit["rounds"], "n": fit["n"]}
        masks[column.name] = mask
        rows = [{"row": int(i) + 1, "value": float(values[i])} for i in np.nonzero(mask)[0]]
        outliers[column.name] = rows
        section = result.section(f"Column: {column.name}")
        n = int(np.isfinite(values).sum())
        section.add("Number of values", n)
        section.add("Number of outliers", len(rows))
        if rows:
            section.add("Outliers", tuple(r["value"] for r in rows), ", ".join(f"{fmt(r['value'])} (row {r['row']})" for r in rows))
        if method == "rout" and np.isfinite(fit["centre"]):
            section.add("Robust centre", fit["centre"])
            section.add("Robust SD of residuals (RSDR)", fit["rsdr"])
        if method != "rout" and fit["rounds"]:
            last = fit["rounds"][0]
            if np.isfinite(last["g"]):
                section.add("Grubbs' G (first round)", last["g"])
                section.add("Critical G", last["critical"])
                section.add("Approximate P (first round)", last["p"], format_p(last["p"], style=style))
        if n < MIN_N:
            result.warnings.append(f"Column {column.name!r} has fewer than {MIN_N} values, so it was not tested.")
        elif method == "rout" and fit["note"]:
            result.warnings.append(f"Column {column.name!r}: {fit['note']}.")
        cleaned_values = values.copy()
        cleaned_values[mask] = np.nan
        cleaned.append(Column(column.name, cleaned_values))
    total = sum(len(v) for v in outliers.values())
    result.section("Summary").add("Total number of outliers", total)
    if method == "iterative_grubbs":
        result.warnings.append("Iterative Grubbs' can miss outliers when two or more mask each other; ROUT is better at finding several outliers.")
    if total:
        result.warnings.append("Outliers were blanked in the cleaned table only; the original data are unchanged. Remove values only with a reason "
                               "you can report (for example a failed biological replicate), not because they are inconvenient.")
    cleaned_table = ColumnTable(title=f"{table.title} (cleaned)" if table.title else "Cleaned data", columns=cleaned, paired=table.paired)
    result.values.update(outliers=outliers, masks=masks, cleaned=cleaned_table, n_outliers=total, details=details)
    result.methods = f"Outliers identified with {label} in each column; flagged values were excluded from the cleaned table."
    return result
