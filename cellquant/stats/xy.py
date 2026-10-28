"""XY tables: one X column and one or more Y data sets, each with replicates side by side, and their linear
regression and correlation.

``values[i, j, k]`` is replicate k of data set j at row i (NaN when blank); ``x[i]`` is that row's X. A row
without X is left out of every analysis. Linear regression fits each data set on its own (every replicate as
a point, or each row's mean), with confidence intervals from Student's t, and compares the slopes (and then
the intercepts) of two or more data sets by an F test.
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


@dataclass
class XYTable:
    title: str
    x_title: str
    x: np.ndarray       # (n,)
    sets: list[str]
    values: np.ndarray  # (n, data sets, replicates)

    def __post_init__(self):
        self.x = np.asarray(self.x, dtype=float).reshape(-1)
        self.values = np.asarray(self.values, dtype=float)
        self.sets = [str(name) for name in self.sets]
        if self.values.ndim != 3 or self.values.shape[0] != self.x.size or self.values.shape[1] != len(self.sets):
            raise StatisticsError("An XY table's values must be shaped (rows, data sets, replicates) with one X per row.")
        if self.values.shape[2] < 1 or not self.sets:
            raise StatisticsError("An XY table needs at least one data set and one replicate.")
        if len(set(self.sets)) != len(self.sets):
            raise StatisticsError("Data set titles must be unique.")
        self.x_title = str(self.x_title or "X")

    @property
    def replicates(self) -> int:
        return self.values.shape[2]

    @classmethod
    def from_long(cls, frame: pd.DataFrame, x: str, y: str, data_set: str | None = None, title: str = "") -> XYTable:
        """From one row per value: an X column, a Y column and optionally a column naming the data set. Values that
        share X and data set become replicates."""

        frame = frame[[x, y] + ([data_set] if data_set else [])].copy()
        frame[x] = pd.to_numeric(frame[x], errors="coerce")
        frame[y] = pd.to_numeric(frame[y], errors="coerce")
        frame = frame.dropna(subset=[x])
        names = list(dict.fromkeys(frame[data_set].astype(str))) if data_set else [y]
        xs = sorted(frame[x].unique())
        cells = {}
        for _index, row in frame.iterrows():
            name = str(row[data_set]) if data_set else y
            cells.setdefault((row[x], name), []).append(row[y])
        depth = max((len(items) for items in cells.values()), default=1)
        values = np.full((len(xs), len(names), depth), np.nan)
        for (xv, name), items in cells.items():
            values[xs.index(xv), names.index(name), : len(items)] = items
        return cls(title or y, x, np.array(xs, dtype=float), names, values)

    def points(self, j: int, means: bool = False) -> tuple[np.ndarray, np.ndarray]:
        """(X, Y) of data set ``j``: every replicate, or each row's mean of its replicates. Blanks are left out."""

        block = self.values[:, j, :]
        if means:
            with np.errstate(invalid="ignore"):
                counts = np.isfinite(block).sum(axis=1)
                y = np.where(counts > 0, np.nansum(block, axis=1) / np.maximum(counts, 1), np.nan)
            keep = np.isfinite(self.x) & np.isfinite(y)
            return self.x[keep], y[keep]
        xs = np.repeat(self.x[:, None], block.shape[1], axis=1)
        keep = np.isfinite(xs) & np.isfinite(block)
        return xs[keep], block[keep]

    def summary(self, j: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """(X, mean, SD, n) per row of data set ``j`` that has X and at least one value."""

        block = self.values[:, j, :]
        n = np.isfinite(block).sum(axis=1)
        keep = np.isfinite(self.x) & (n > 0)
        block, n = block[keep], n[keep]
        mean = np.nanmean(block, axis=1) if block.size else np.array([])
        with np.errstate(invalid="ignore", divide="ignore"):
            sd = np.array([np.nanstd(row, ddof=1) if count > 1 else np.nan for row, count in zip(block, n)])
        return self.x[keep], mean, sd, n


# --- Linear regression ----------------------------------------------------------------------------------------


def fit_line(x: np.ndarray, y: np.ndarray, through_origin: bool = False) -> dict:
    """Least-squares line through (x, y). Raises when it cannot be fitted."""

    x, y = np.asarray(x, float), np.asarray(y, float)
    if not (np.isfinite(x).all() and np.isfinite(y).all()):
        raise StatisticsError("Linear regression needs finite X and Y values (leave blanks out first).")
    n = x.size
    params = 1 if through_origin else 2
    if n < params + 1:
        raise StatisticsError(f"Linear regression needs at least {params + 1} points (this data set has {n}).")
    if np.ptp(x) == 0:
        raise StatisticsError("Every point has the same X, so no line can be fitted.")
    if through_origin:
        sxx = float((x**2).sum())
        slope, intercept = float((x * y).sum() / sxx), 0.0
        cov = np.array([[1.0 / sxx, 0.0], [0.0, 0.0]])
    else:
        mx = x.mean()
        sxx = float(((x - mx) ** 2).sum())
        slope = float(((x - mx) * (y - y.mean())).sum() / sxx)
        intercept = float(y.mean() - slope * mx)
        cov = np.array([[1.0 / sxx, -mx / sxx], [-mx / sxx, 1.0 / n + mx**2 / sxx]])
    residuals = y - (slope * x + intercept)
    ss_resid = float((residuals**2).sum())
    df = n - params
    ss_total = float((y**2).sum()) if through_origin else float(((y - y.mean()) ** 2).sum())
    if ss_resid <= 1e-12 * max(ss_total, float((y**2).sum()), 1e-300):
        ss_resid = 0.0
    ms = ss_resid / df
    r2 = 1.0 - ss_resid / ss_total if ss_total > 0 else float("nan")
    ss_reg = ss_total - ss_resid
    f = ss_reg / ms if ms > 0 else float("nan")
    p = float(stats.f.sf(f, 1, df)) if math.isfinite(f) else float("nan")
    return {"slope": slope, "intercept": intercept, "cov": cov * ms, "unscaled": cov, "ms": ms, "sy_x": math.sqrt(ms), "df": df, "n": n,
            "r2": r2, "f": f, "p": p, "ss_resid": ss_resid, "x_min": float(x.min()), "x_max": float(x.max()),
            "through_origin": through_origin, "x_mean": float(x.mean())}


def band(fit: dict, xs, ci: float = 0.95, prediction: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """The confidence band of the line (or the prediction band of new points) at ``xs``."""

    xs = np.asarray(xs, float)
    design = np.column_stack([xs, np.ones_like(xs)])
    variance = np.einsum("ij,jk,ik->i", design, fit["cov"], design) + (fit["ms"] if prediction else 0.0)
    t = stats.t.ppf(0.5 + ci / 2, fit["df"])
    center = fit["slope"] * xs + fit["intercept"]
    half = t * np.sqrt(np.maximum(variance, 0.0))
    return center - half, center + half


def _equation(fit: dict) -> str:
    slope = fmt(fit["slope"])
    if fit["through_origin"]:
        return f"Y = {slope}*X"
    intercept = fit["intercept"]
    return f"Y = {slope}*X {'-' if intercept < 0 else '+'} {fmt(abs(intercept))}"


def _compare_lines(fits: dict[str, dict], data: dict[str, tuple[np.ndarray, np.ndarray]], through_origin: bool) -> dict:
    """F tests of equal slopes, then (slopes alike) equal intercepts, for two or more lines (analysis of covariance)."""

    names = list(fits)
    ss_separate = sum(fits[name]["ss_resid"] for name in names)
    df_separate = sum(fits[name]["df"] for name in names)
    # One common slope, an intercept per line.
    columns, ys = [], []
    for index, name in enumerate(names):
        x, y = data[name]
        block = np.zeros((x.size, len(names) + 1))
        block[:, 0] = x
        if not through_origin:
            block[:, 1 + index] = 1.0
        columns.append(block)
        ys.append(y)
    design, y_all = np.vstack(columns), np.concatenate(ys)
    if through_origin:
        design = design[:, :1]
    beta, *_rest = np.linalg.lstsq(design, y_all, rcond=None)
    ss_common = float(((y_all - design @ beta) ** 2).sum())
    df_common = y_all.size - design.shape[1]
    out = {"slopes_dfn": df_common - df_separate, "slopes_dfd": df_separate}
    ms = ss_separate / df_separate if df_separate > 0 else float("nan")
    f = (ss_common - ss_separate) / out["slopes_dfn"] / ms if ms > 0 and out["slopes_dfn"] > 0 else float("nan")
    out.update(slopes_f=f, slopes_p=float(stats.f.sf(f, out["slopes_dfn"], df_separate)) if math.isfinite(f) else float("nan"))
    if through_origin:
        return out
    # One line for everything against one slope with separate intercepts.
    single = np.column_stack([y_all * 0 + 1.0, design[:, 0]])
    beta, *_rest = np.linalg.lstsq(single, y_all, rcond=None)
    ss_single = float(((y_all - single @ beta) ** 2).sum())
    dfn, dfd = len(names) - 1, df_common
    ms_common = ss_common / dfd if dfd > 0 else float("nan")
    f = (ss_single - ss_common) / dfn / ms_common if ms_common > 0 else float("nan")
    out.update(intercepts_f=f, intercepts_dfn=dfn, intercepts_dfd=dfd,
               intercepts_p=float(stats.f.sf(f, dfn, dfd)) if math.isfinite(f) else float("nan"))
    return out


def linear_regression(table: XYTable, *, through_origin: bool = False, replicates: str = "each", ci: float = 0.95, alpha: float = 0.05,
                      style: str = "classic") -> AnalysisResult:
    """A straight line through each data set of an XY table.

    ``replicates``: "each" (every replicate is a point) or "mean" (each row's mean is one point)."""

    if not 0 < ci < 1:
        raise StatisticsError("The confidence level must be between 0 and 1 (for example 0.95).")
    if not 0 < alpha < 1:
        raise StatisticsError("alpha must be between 0 and 1 (for example 0.05).")
    if replicates not in ("each", "mean"):
        raise StatisticsError("replicates must be 'each' (every replicate a point) or 'mean' (each row's mean).")
    result = AnalysisResult("Linear regression", table.title, {"through_origin": through_origin, "replicates": replicates, "ci": ci,
                                                                "alpha": alpha, "style": style})
    level = f"{100 * ci:g}% CI"
    fits, data, out = {}, {}, {}
    for j, name in enumerate(table.sets):
        x, y = table.points(j, means=replicates == "mean")
        try:
            fit = fit_line(x, y, through_origin)
        except StatisticsError as exc:
            result.warnings.append(f"{name}: {exc}")
            continue
        fits[name], data[name] = fit, (x, y)
        t = stats.t.ppf(0.5 + ci / 2, fit["df"])
        se_slope, se_intercept = math.sqrt(fit["cov"][0, 0]), math.sqrt(fit["cov"][1, 1])
        slope_ci = (fit["slope"] - t * se_slope, fit["slope"] + t * se_slope)
        intercept_ci = (fit["intercept"] - t * se_intercept, fit["intercept"] + t * se_intercept)
        x_intercept = -fit["intercept"] / fit["slope"] if fit["slope"] else float("nan")
        fit.update(se_slope=se_slope, se_intercept=se_intercept, slope_ci=slope_ci, intercept_ci=intercept_ci, x_intercept=x_intercept,
                   equation=_equation(fit))
        sig = bool(fit["p"] < alpha) if math.isfinite(fit["p"]) else None
        rows = {
            ("Best-fit values", "Slope"): fmt(fit["slope"]),
            ("Best-fit values", "Y-intercept"): "0 (fixed)" if through_origin else fmt(fit["intercept"]),
            ("Best-fit values", "X-intercept"): "" if through_origin else fmt(x_intercept),
            ("Best-fit values", "1/slope"): fmt(1 / fit["slope"]) if fit["slope"] else "",
            ("Std. Error", "Slope"): fmt(se_slope),
            ("Std. Error", "Y-intercept"): "" if through_origin else fmt(se_intercept),
            (level, "Slope"): fmt_ci(*slope_ci),
            (level, "Y-intercept"): "" if through_origin else fmt_ci(*intercept_ci),
            ("Goodness of fit", "R squared"): fmt(fit["r2"]),
            ("Goodness of fit", "Sy.x"): fmt(fit["sy_x"]),
            ("Is the slope different from zero?", "F"): fmt(fit["f"]),
            ("Is the slope different from zero?", "DFn, DFd"): f"1, {fit['df']}",
            ("Is the slope different from zero?", "P value"): format_p(fit["p"], style=style),
            ("Is the slope different from zero?", "Significant?"): ("" if sig is None else f"{'Yes' if sig else 'No'} "
                                                                   f"({p_summary(fit['p'], style)})"),
            ("Equation", ""): fit["equation"],
            ("Data", "Number of X values"): str(int(np.isfinite(table.x).sum())),
            ("Data", "Points analyzed"): str(fit["n"]),
        }
        out[name] = rows
        section = result.section(name)
        for (part, label), text in rows.items():
            section.add(f"{part}: {label}" if label else part, text, text)
    if not fits:
        raise StatisticsError("No data set has enough points for a line: " + "; ".join(result.warnings))
    frame = pd.DataFrame(out)
    frame.index.names = ["section", "statistic"]
    comparison = None
    if len(fits) >= 2:
        comparison = _compare_lines(fits, data, through_origin)
        lines = {("Are the slopes equal?", "F"): fmt(comparison["slopes_f"]),
                 ("Are the slopes equal?", "DFn, DFd"): f"{comparison['slopes_dfn']}, {comparison['slopes_dfd']}",
                 ("Are the slopes equal?", "P value"): format_p(comparison["slopes_p"], style=style)}
        if "intercepts_p" in comparison and not comparison["slopes_p"] < alpha:
            lines.update({("Are the intercepts equal? (slopes alike)", "F"): fmt(comparison["intercepts_f"]),
                          ("Are the intercepts equal? (slopes alike)", "DFn, DFd"):
                          f"{comparison['intercepts_dfn']}, {comparison['intercepts_dfd']}",
                          ("Are the intercepts equal? (slopes alike)", "P value"): format_p(comparison["intercepts_p"], style=style)})
        elif "intercepts_p" in comparison:
            lines[("Are the intercepts equal? (slopes alike)", "")] = "Not compared: the slopes differ"
        extra = pd.DataFrame({next(iter(fits)): lines})
        extra.index.names = ["section", "statistic"]
        frame = pd.concat([frame, extra])
        section = result.section("Comparing the data sets")
        for (part, label), text in lines.items():
            section.add(f"{part}: {label}" if label else part, text, text)
    result.values.update(fits=fits, wide=frame, comparison=comparison)
    points = "each replicate as a point" if replicates == "each" else "the mean of each row's replicates as one point"
    origin = ", forced through the origin" if through_origin else ""
    compared = (" Slopes (then intercepts) of the data sets were compared by an F test (analysis of covariance)." if comparison else "")
    result.methods = (f"Least-squares linear regression of each data set ({points}{origin}); {level}s of slope and intercept from "
                      f"Student's t; the slope was tested against zero by an F test (alpha = {alpha:g}).{compared}")
    if any(fit["n"] < 5 for fit in fits.values()):
        result.warnings.append("Fewer than 5 points in a data set: the line, its CIs and R² are rough.")
    return result


# --- Correlation ----------------------------------------------------------------------------------------------


def correlation(x: np.ndarray, y: np.ndarray, method: str = "pearson", ci: float = 0.95) -> dict:
    """r (Pearson) or rs (Spearman) with its CI (Fisher z; approximate for Spearman) and two-tailed P."""

    keep = np.isfinite(x) & np.isfinite(y)
    x, y = np.asarray(x, float)[keep], np.asarray(y, float)[keep]
    n = x.size
    if n < 3:
        raise StatisticsError(f"Correlation needs at least 3 pairs (this data set has {n}).")
    if np.ptp(x) == 0 or np.ptp(y) == 0:
        raise StatisticsError("X or Y does not vary, so the correlation is undefined.")
    if method == "pearson":
        r, p = stats.pearsonr(x, y)
    elif method == "spearman":
        r, p = stats.spearmanr(x, y)
    else:
        raise StatisticsError("method must be 'pearson' or 'spearman'.")
    r, p = float(r), float(p)
    low = high = float("nan")
    if n > 3 and abs(r) < 1:
        z, half = math.atanh(r), stats.norm.ppf(0.5 + ci / 2) / math.sqrt(n - 3)
        low, high = math.tanh(z - half), math.tanh(z + half)
    elif abs(r) == 1:
        low = high = r
    return {"r": r, "p": p, "n": n, "ci_low": low, "ci_high": high, "r2": r * r}


def xy_correlation(table: XYTable, *, method: str = "pearson", ci: float = 0.95, alpha: float = 0.05, style: str = "classic",
                   replicates: str = "each") -> AnalysisResult:
    """X against each data set's Y: Pearson's r or Spearman's rs."""

    if method not in ("pearson", "spearman"):
        raise StatisticsError("method must be 'pearson' or 'spearman'.")
    name = "Pearson r" if method == "pearson" else "Spearman r"
    result = AnalysisResult(f"Correlation ({name})", table.title, {"method": method, "ci": ci, "alpha": alpha, "style": style,
                                                                     "replicates": replicates})
    out, values = {}, {}
    for j, set_name in enumerate(table.sets):
        x, y = table.points(j, means=replicates == "mean")
        try:
            item = correlation(x, y, method, ci)
        except StatisticsError as exc:
            result.warnings.append(f"{set_name}: {exc}")
            continue
        values[set_name] = item
        sig = bool(item["p"] < alpha)
        rows = {("Correlation", "r" if method == "pearson" else "rs"): fmt(item["r"]),
                ("Correlation", f"{100 * ci:g}% CI" + (" (approximate)" if method == "spearman" else "")): fmt_ci(item["ci_low"], item["ci_high"]),
                ("Correlation", "R squared" if method == "pearson" else "rs squared"): fmt(item["r2"]),
                ("Is the correlation different from zero?", "P value (two-tailed)"): format_p(item["p"], style=style),
                ("Is the correlation different from zero?", "Significant?"): f"{'Yes' if sig else 'No'} ({p_summary(item['p'], style)})",
                ("Data", "Number of XY pairs"): str(item["n"])}
        out[set_name] = rows
        section = result.section(set_name)
        for (part, label), text in rows.items():
            section.add(f"{part}: {label}", text, text)
    if not out:
        raise StatisticsError("No data set has enough XY pairs: " + "; ".join(result.warnings))
    frame = pd.DataFrame(out)
    frame.index.names = ["section", "statistic"]
    result.values.update(correlations=values, wide=frame)
    if method == "pearson":
        result.methods = (f"Pearson correlation between X and each data set; {100 * ci:g}% CI by Fisher's z transformation; two-tailed P "
                          f"(alpha = {alpha:g}). Assumes X and Y are both sampled and roughly Gaussian.")
    else:
        result.methods = (f"Spearman rank correlation between X and each data set (no Gaussian assumption); approximate {100 * ci:g}% CI by "
                          f"Fisher's z transformation; two-tailed P (alpha = {alpha:g}).")
    return result


__all__ = ["XYTable", "band", "correlation", "fit_line", "linear_regression", "xy_correlation"]
