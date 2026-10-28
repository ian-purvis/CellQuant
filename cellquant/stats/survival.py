"""Survival analysis: Kaplan-Meier curves, median survival, the log-rank tests and the hazard ratio.

Each subject has a time (from the start of follow-up to the event, or to the last time it was seen) and a status: 1
when the event happened at that time, 0 when the subject was censored (still event-free when last seen). In the
table each row is one subject: X holds the time and the column of the subject's group holds its status; the other
groups' columns stay blank.

``survival`` estimates each group's survival curve by the product-limit (Kaplan-Meier) method with Greenwood's
standard error and a log-log confidence band, reads the median survival from the curve with the Brookmeyer-Crowley
interval, and compares the curves with the log-rank (Mantel-Cox) test and the Gehan-Breslow-Wilcoxon test (and, for
ordered groups, the log-rank test for trend). With two groups it reports the hazard ratio by the Mantel-Haenszel and
the log-rank (O/E) methods.
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
class SurvivalTable:
    """Subjects by group: ``times[g]`` and ``events[g]`` (1 = event, 0 = censored) for group ``groups[g]``.

    ``dropped`` counts the rows left out because the time or the status was blank."""

    title: str
    groups: list[str]
    times: list[np.ndarray]
    events: list[np.ndarray]
    dropped: int = 0

    def __post_init__(self):
        self.groups = [str(name) for name in self.groups]
        if not self.groups:
            raise StatisticsError("A survival table needs at least one group.")
        if len(set(self.groups)) != len(self.groups):
            raise StatisticsError("Group titles must be unique.")
        if not len(self.times) == len(self.events) == len(self.groups):
            raise StatisticsError("Each group needs its times and its statuses.")
        times, events = [], []
        for name, t, e in zip(self.groups, self.times, self.events):
            try:
                t, e = np.asarray(t, dtype=float).reshape(-1), np.asarray(e, dtype=float).reshape(-1)
            except (TypeError, ValueError) as exc:
                raise StatisticsError(f"The times and statuses of {name} must be numbers.") from exc
            if t.size != e.size:
                raise StatisticsError(f"{name} has {t.size} times but {e.size} statuses.")
            times.append(t)
            events.append(e)
        self.times, self.events = times, events

    @classmethod
    def from_columns(cls, time, statuses: dict[str, object], title: str = "") -> SurvivalTable:
        """The table layout: one time per row, and each subject's status (1 or 0) in its group's column; blank elsewhere.

        A row with a status but no time, or a time but no status in any group, is left out and counted in ``dropped``.
        A row with a status in more than one group is an error."""

        time = np.asarray(time, dtype=float).reshape(-1)
        names = [str(name) for name in statuses]
        grid = np.column_stack([np.asarray(statuses[name], dtype=float).reshape(-1) for name in statuses]) if names else np.empty((time.size, 0))
        if grid.shape[0] != time.size:
            raise StatisticsError("Every group column needs one value per row of X.")
        filled = np.isfinite(grid)
        several = np.flatnonzero(filled.sum(axis=1) > 1)
        if several.size:
            raise StatisticsError(f"Row {several[0] + 1} has a status in more than one group; each row is one subject in one group.")
        has_status, has_time = filled.any(axis=1), np.isfinite(time)
        keep = has_status & has_time
        dropped = int(((has_status | has_time) & ~keep).sum())
        times, events = [], []
        for j in range(len(names)):
            rows = keep & filled[:, j]
            times.append(time[rows])
            events.append(grid[rows, j])
        return cls(title, names, times, events, dropped)

    def checked(self, group: int) -> tuple[np.ndarray, np.ndarray]:
        """Group ``group``'s times and statuses (as 0/1 integers). Raises on a negative time or a status other than 0 or 1."""

        name, t, e = self.groups[group], self.times[group], self.events[group]
        if not np.isfinite(t).all() or not np.isfinite(e).all():
            raise StatisticsError(f"{name} has a blank time or status.")
        if (t < 0).any():
            raise StatisticsError(f"{name} has a negative time; times run from the start of follow-up.")
        if not np.isin(e, (0.0, 1.0)).all():
            raise StatisticsError(f"{name} has a status other than 1 (event) or 0 (censored).")
        return t, e.astype(int)


def kaplan_meier(times, events, ci: float = 0.95) -> pd.DataFrame:
    """The product-limit curve: one row per distinct time (event or censoring), with the survival just after it.

    Columns: time, at_risk, events, censored, survival, se (Greenwood), low, high (log-log band; blank where the
    survival is 0 or 1)."""

    times, events = np.asarray(times, dtype=float), np.asarray(events, dtype=int)
    distinct = np.unique(times)
    at_risk = np.array([(times >= t).sum() for t in distinct])
    died = np.array([((times == t) & (events == 1)).sum() for t in distinct])
    censored = np.array([((times == t) & (events == 0)).sum() for t in distinct])
    with np.errstate(divide="ignore", invalid="ignore"):
        survival = np.cumprod(1.0 - died / at_risk)
        terms = np.where(died > 0, died / (at_risk * (at_risk - died)), 0.0)
        greenwood = np.cumsum(terms)
        se = survival * np.sqrt(greenwood)
        z = stats.norm.ppf(0.5 + ci / 2)
        log_s = np.log(survival)
        theta = z * np.sqrt(greenwood) / np.abs(log_s)
        inside = (survival > 0) & (survival < 1) & np.isfinite(theta)
        low = np.where(inside, survival ** np.exp(theta), np.nan)
        high = np.where(inside, survival ** np.exp(-theta), np.nan)
    se = np.where(np.isfinite(se), se, np.nan)
    return pd.DataFrame({"time": distinct, "at_risk": at_risk, "events": died, "censored": censored, "survival": survival,
                         "se": se, "low": low, "high": high, "greenwood": greenwood})


def median_survival(curve: pd.DataFrame, ci: float = 0.95) -> tuple[float, float, float]:
    """The median survival (the first time the curve is at or below 0.5) and its Brookmeyer-Crowley interval from the
    log-log transform (the event times where S(t) is consistent with 0.5). Undefined values are NaN; an open upper limit
    is infinity."""

    events = curve[curve["events"] > 0]
    s, greenwood = events["survival"].to_numpy(), events["greenwood"].to_numpy()
    t = events["time"].to_numpy()
    below = np.flatnonzero(s <= 0.5 + 1e-12)
    median = float(t[below[0]]) if below.size else math.nan
    z = stats.norm.ppf(0.5 + ci / 2)
    with np.errstate(divide="ignore", invalid="ignore"):
        g = np.log(-np.log(s)) - math.log(-math.log(0.5))
        r = g / (np.sqrt(greenwood) / np.abs(np.log(s)))  # (g(S) − g(0.5)) / (g'(S) × S × √Greenwood)
    inside = np.flatnonzero(np.abs(r) <= z)
    if not inside.size:
        return median, math.nan, math.nan
    low = float(t[inside[0]])
    high = math.inf if inside[-1] == t.size - 1 else float(t[inside[-1] + 1])
    return median, low, high


def _comparison(groups: list[tuple[np.ndarray, np.ndarray]], weight: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Observed and expected events per group and the covariance of O − E, weighted at each event time (1 for the
    log-rank test, the number at risk for Gehan-Breslow-Wilcoxon)."""

    k = len(groups)
    pooled_t = np.concatenate([t for t, _ in groups])
    pooled_e = np.concatenate([e for _, e in groups])
    distinct = np.unique(pooled_t[pooled_e == 1])
    observed, expected, covariance = np.zeros(k), np.zeros(k), np.zeros((k, k))
    for time in distinct:
        n_g = np.array([(t >= time).sum() for t, _ in groups], dtype=float)
        d_g = np.array([((t == time) & (e == 1)).sum() for t, e in groups], dtype=float)
        n, d = n_g.sum(), d_g.sum()
        w = n if weight == "gehan" else 1.0
        share = n_g / n
        observed += w * d_g
        expected += w * d * share
        if n > 1:
            covariance += w * w * d * (n - d) / (n - 1) * (np.diag(share) - np.outer(share, share))
    return observed, expected, covariance


def _chi_square(observed: np.ndarray, expected: np.ndarray, covariance: np.ndarray, contrast: np.ndarray | None = None) -> tuple[float, int]:
    u = observed - expected
    if contrast is not None:
        variance = float(contrast @ covariance @ contrast)
        return (float((contrast @ u) ** 2 / variance), 1) if variance > 0 else (math.nan, 1)
    df = len(u) - 1
    sub_u, sub_v = u[:-1], covariance[:-1, :-1]
    try:
        chi = float(sub_u @ np.linalg.solve(sub_v, sub_u))
    except np.linalg.LinAlgError:
        chi = float(sub_u @ np.linalg.pinv(sub_v) @ sub_u)
    return chi, df


def survival(table: SurvivalTable, *, trend: bool = False, ci: float = 0.95, alpha: float = 0.05, style: str = "classic") -> AnalysisResult:
    """Kaplan-Meier curves and their comparison. ``trend``: the groups are in order (a dose, an age band), so add the
    log-rank test for trend (scores 1, 2, 3 … in the table's order; three or more groups)."""

    if not 0 < ci < 1 or not 0 < alpha < 1:
        raise StatisticsError("The confidence level and alpha must be between 0 and 1.")
    names, data = [], []
    for g, name in enumerate(table.groups):
        t, e = table.checked(g)
        if t.size:
            names.append(name)
            data.append((t, e))
    empty = [name for name in table.groups if name not in names]
    if not data:
        raise StatisticsError("Survival analysis needs at least one subject with a time and a status.")
    level = f"{100 * ci:g}% CI"
    result = AnalysisResult("Survival", table.title, {"trend": trend, "ci": ci, "alpha": alpha, "style": style})
    rows: dict[tuple[str, str], str] = {}
    curves, medians = {}, {}
    for name, (t, e) in zip(names, data):
        curve = kaplan_meier(t, e, ci)
        curves[name] = curve
        median, low, high = median_survival(curve, ci)
        medians[name] = (median, low, high)
        events = int(e.sum())
        rows[("Subjects", name)] = f"{t.size} subjects: {events} event{'s' if events != 1 else ''}, {t.size - events} censored"
        if math.isnan(median):
            text = "Undefined (survival stays above 50%)"
        else:
            upper = "∞" if math.isinf(high) else fmt(high)
            ci_text = "" if math.isnan(low) else f" ({level} {fmt(low)} to {upper})"
            text = f"{fmt(median)}{ci_text}"
        rows[("Median survival", name)] = text

    values: dict = {"curves": curves, "medians": medians, "groups": names}
    if len(data) >= 2:
        for weight, title in (("logrank", "Log-rank (Mantel-Cox) test"), ("gehan", "Gehan-Breslow-Wilcoxon test")):
            observed, expected, covariance = _comparison(data, weight)
            chi, df = _chi_square(observed, expected, covariance)
            p = float(stats.chi2.sf(chi, df)) if np.isfinite(chi) else math.nan
            rows[(title, "Chi-square, df")] = f"{fmt(chi)}, {df}"
            rows[(title, "P value")] = format_p(p, style=style)
            rows[(title, "Significant?")] = f"{'Yes' if p < alpha else 'No'} ({p_summary(p, style)})"
            values[weight] = {"chi2": chi, "df": df, "p": p, "observed": observed, "expected": expected}
        if trend:
            if len(data) < 3:
                result.warnings.append("The log-rank test for trend needs three or more groups in order; it is left out.")
            else:
                observed, expected, covariance = _comparison(data, "logrank")
                scores = np.arange(1, len(data) + 1, dtype=float)
                chi, df = _chi_square(observed, expected, covariance, scores - scores.mean())
                p = float(stats.chi2.sf(chi, 1)) if np.isfinite(chi) else math.nan
                rows[("Log-rank test for trend", "Chi-square, df")] = f"{fmt(chi)}, 1"
                rows[("Log-rank test for trend", "P value")] = format_p(p, style=style)
                rows[("Log-rank test for trend", "Significant?")] = f"{'Yes' if p < alpha else 'No'} ({p_summary(p, style)})"
                values["trend"] = {"chi2": chi, "df": 1, "p": p}
    if len(data) == 2:
        observed, expected, covariance = _comparison(data, "logrank")
        z = stats.norm.ppf(0.5 + ci / 2)
        label = f"{names[0]} / {names[1]}"
        variance = covariance[0, 0]
        hazard = {}
        if variance > 0:
            log_hr = (observed[0] - expected[0]) / variance
            hazard["mantel_haenszel"] = (math.exp(log_hr), math.exp(log_hr - z / math.sqrt(variance)), math.exp(log_hr + z / math.sqrt(variance)))
        if (observed > 0).all() and (expected > 0).all():
            log_hr = math.log((observed[0] / expected[0]) / (observed[1] / expected[1]))
            spread = z * math.sqrt(1 / expected[0] + 1 / expected[1])
            hazard["logrank"] = (math.exp(log_hr), math.exp(log_hr - spread), math.exp(log_hr + spread))
        for key, title in (("mantel_haenszel", "Mantel-Haenszel"), ("logrank", "Log-rank (O/E)")):
            if key in hazard:
                hr, low, high = hazard[key]
                rows[(f"Hazard ratio ({label})", title)] = f"{fmt(hr)} ({level} {fmt_ci(low, high)})"
            else:
                rows[(f"Hazard ratio ({label})", title)] = "Undefined (a group has no events)"
        values["hazard_ratio"] = hazard
        a, b = medians[names[0]][0], medians[names[1]][0]
        if np.isfinite(a) and np.isfinite(b) and b > 0:
            rows[("Ratio of median survival", label)] = fmt(a / b)
            values["median_ratio"] = a / b

    for (part, label), text in rows.items():
        result.section(part).add(label, text, text)
    frame = pd.DataFrame({"Survival": rows})
    frame.index.names = ["section", "statistic"]
    values["wide"] = frame
    values["subjects"] = int(sum(t.size for t, _ in data))
    result.values.update(values)

    result.methods = (f"Survival curves by the Kaplan-Meier (product-limit) method; each row of the table is one subject, so n is the "
                      f"number of subjects ({values['subjects']} in all). Standard errors by Greenwood's formula and {level}s of the curve "
                      f"on the log-log scale. Median survival is the first time the curve reaches 50% or less, with the "
                      f"Brookmeyer-Crowley {level} (log-log).")
    if len(data) >= 2:
        result.methods += (f" Curves compared by the log-rank (Mantel-Cox) test and the Gehan-Breslow-Wilcoxon test (weighted by the "
                           f"number at risk), chi-square with {len(data) - 1} df (alpha = {alpha:g}).")
        if "trend" in values:
            result.methods += " The log-rank test for trend uses scores 1, 2, 3 … in the order of the groups."
    if len(data) == 2:
        result.methods += (f" Hazard ratio of {names[0]} to {names[1]} by the Mantel-Haenszel method, exp((O − E) / V), with "
                           f"{level} exp(ln HR ± z / √V), and by the log-rank method, (O₁/E₁)/(O₂/E₂), with {level} "
                           f"exp(ln HR ± z √(1/E₁ + 1/E₂)).")
    if table.dropped:
        result.warnings.append(f"{table.dropped} row{'s' if table.dropped != 1 else ''} with a blank time or status "
                               f"{'were' if table.dropped != 1 else 'was'} left out.")
    if empty:
        result.warnings.append(f"No subjects in {', '.join(empty)}; left out.")
    for name, (t, e) in zip(names, data):
        if not e.any():
            result.warnings.append(f"{name} has no events (every subject censored): its curve stays at 100%.")
    if any(t.size < 5 for t, _ in data):
        result.warnings.append("Fewer than 5 subjects in a group: the curves, medians and tests are rough.")
    return result
