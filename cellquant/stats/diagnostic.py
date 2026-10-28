"""Diagnostic accuracy and agreement: the ROC curve of a measurement that separates patients from controls, and
Bland-Altman agreement between two ways of measuring the same thing.

``roc_curve`` takes the values of a control group and a patient group, sweeps a cutoff over every gap between
distinct values and reports sensitivity, specificity and the likelihood ratio at each cutoff (Wilson score
intervals for the proportions), then the area under the curve (the Mann-Whitney U statistic over n1*n2, ties
counting one half) with the Hanley & McNeil (1982) standard error. ``bland_altman`` takes paired measurements and
reports the bias and the 95% limits of agreement of their differences (or ratios, or percent differences) with
confidence intervals (Bland & Altman, 1986).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy import stats

from cellquant.errors import StatisticsError
from cellquant.stats.pvalues import format_p, p_summary
from cellquant.stats.results import AnalysisResult, fmt, fmt_ci


# --- ROC curve ------------------------------------------------------------------------------------------------


def _finite(values, label: str) -> np.ndarray:
    try:
        array = np.asarray(values, dtype=float).reshape(-1)
    except (TypeError, ValueError) as exc:
        raise StatisticsError(f"The {label} must be numbers.") from exc
    array = array[np.isfinite(array)]
    if array.size < 1:
        raise StatisticsError(f"The {label} need at least one value.")
    return array


def _wilson(successes: np.ndarray, n: int, ci: float) -> tuple[np.ndarray, np.ndarray]:
    """Wilson score interval for ``successes`` out of ``n`` (as proportions)."""

    z = stats.norm.ppf(0.5 + ci / 2)
    p = np.asarray(successes, float) / n
    denominator = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return np.clip(center - half, 0.0, 1.0), np.clip(center + half, 0.0, 1.0)


def _hanley_mcneil_se(auc: float, n_patients: int, n_controls: int) -> float:
    q1 = auc / (2.0 - auc)
    q2 = 2.0 * auc * auc / (1.0 + auc)
    variance = (auc * (1 - auc) + (n_patients - 1) * (q1 - auc * auc) + (n_controls - 1) * (q2 - auc * auc)) / (n_patients * n_controls)
    return math.sqrt(max(variance, 0.0))


def roc_curve(controls, patients, *, ci: float = 0.95, alpha: float = 0.05, style: str = "classic", direction: str = "auto",
              names: tuple[str, str] = ("Controls", "Patients")) -> AnalysisResult:
    """The ROC curve of a measurement, with the area under it.

    ``direction``: "auto" (patients tend higher when their mean rank is at least the controls'), ">" (a value above the cutoff is
    called positive) or "<" (below the cutoff is positive). The cutoffs are the midpoints between consecutive distinct pooled
    values, one step below the smallest and one above the largest, so the curve runs from (0, 0) to (1, 1); the outer cutoffs lie
    5% of the pooled range beyond the data."""

    if not 0 < ci < 1:
        raise StatisticsError("The confidence level must be between 0 and 1 (for example 0.95).")
    if not 0 < alpha < 1:
        raise StatisticsError("alpha must be between 0 and 1 (for example 0.05).")
    if direction not in ("auto", ">", "<"):
        raise StatisticsError("direction must be 'auto', '>' or '<'.")
    controls, patients = _finite(controls, "controls"), _finite(patients, "patients")
    n_c, n_p = controls.size, patients.size
    pooled = np.concatenate([controls, patients])
    ranks = stats.rankdata(pooled)
    rank_c, rank_p = ranks[:n_c], ranks[n_c:]
    u_patients = float(rank_p.sum() - n_p * (n_p + 1) / 2.0)
    auc_higher = u_patients / (n_p * n_c)  # P(patient > control) + half the ties
    if direction == "auto":
        direction = ">" if rank_p.mean() >= rank_c.mean() else "<"
    auc = auc_higher if direction == ">" else 1.0 - auc_higher

    se = _hanley_mcneil_se(auc, n_p, n_c)
    z = stats.norm.ppf(0.5 + ci / 2)
    ci_low, ci_high = max(auc - z * se, 0.0), min(auc + z * se, 1.0)
    se_null = _hanley_mcneil_se(0.5, n_p, n_c)
    p = float(2 * stats.norm.sf(abs(auc - 0.5) / se_null))
    sig = bool(p < alpha)

    distinct = np.unique(pooled)
    if distinct.size > 1:
        pad = 0.05 * float(distinct[-1] - distinct[0])
    else:
        pad = max(0.05 * abs(float(distinct[0])), 1.0)
    cutoffs = np.concatenate([[distinct[0] - pad], (distinct[:-1] + distinct[1:]) / 2.0, [distinct[-1] + pad]])
    sorted_c, sorted_p = np.sort(controls), np.sort(patients)
    if direction == ">":
        cutoffs = cutoffs[::-1]  # strictest first, so the false positive rate rises from 0
        true_pos = n_p - np.searchsorted(sorted_p, cutoffs, side="right")
        false_pos = n_c - np.searchsorted(sorted_c, cutoffs, side="right")
    else:
        true_pos = np.searchsorted(sorted_p, cutoffs, side="left")
        false_pos = np.searchsorted(sorted_c, cutoffs, side="left")
    true_neg = n_c - false_pos
    sens_low, sens_high = _wilson(true_pos, n_p, ci)
    spec_low, spec_high = _wilson(true_neg, n_c, ci)
    sensitivity, specificity = 100.0 * true_pos / n_p, 100.0 * true_neg / n_c
    with np.errstate(divide="ignore", invalid="ignore"):
        likelihood = np.where(false_pos > 0, (true_pos / n_p) / (false_pos / n_c), np.nan)
    curve = pd.DataFrame({
        "cutoff": cutoffs, "sensitivity": sensitivity, "sens_low": 100 * sens_low, "sens_high": 100 * sens_high,
        "specificity": specificity, "spec_low": 100 * spec_low, "spec_high": 100 * spec_high,
        "likelihood_ratio": likelihood, "fpr_percent": 100.0 - specificity,
    })

    level = f"{100 * ci:g}% CI"
    word = "above" if direction == ">" else "below"
    rows = {
        ("Area under the ROC curve", "Area"): fmt(auc),
        ("Area under the ROC curve", "Std. Error"): fmt(se),
        ("Area under the ROC curve", level): fmt_ci(ci_low, ci_high),
        ("Area under the ROC curve", "P value"): format_p(p, style=style),
        ("Area under the ROC curve", "Significant?"): f"{'Yes' if sig else 'No'} ({p_summary(p, style)})",
    }
    youden = curve["sensitivity"] + curve["specificity"] - 100.0
    best = int(np.argmax(youden.to_numpy()))
    rows[("Best cutoff (Youden's J)", f"{word.capitalize()} {fmt(cutoffs[best])}")] = (
        f"Sensitivity {sensitivity[best]:.1f}%, specificity {specificity[best]:.1f}%, J = {youden.iloc[best] / 100:.3f}")
    rows[("Data", f"{names[0]} (controls) n")] = str(n_c)
    rows[("Data", f"{names[1]} (patients) n")] = str(n_p)
    rows[("Data", "Positive when")] = f"the value is {word} the cutoff"
    part = f"Sensitivity and specificity at each cutoff ({level}s)"
    for i, cutoff in enumerate(cutoffs):
        ratio = "" if not np.isfinite(likelihood[i]) else f"; likelihood ratio {fmt(likelihood[i])}"
        rows[(part, f"{'>' if direction == '>' else '<'} {fmt(cutoff)}")] = (
            f"Sensitivity {sensitivity[i]:.1f}% ({100 * sens_low[i]:.1f} to {100 * sens_high[i]:.1f}%); "
            f"specificity {specificity[i]:.1f}% ({100 * spec_low[i]:.1f} to {100 * spec_high[i]:.1f}%){ratio}")
    result = AnalysisResult("ROC curve", "", {"ci": ci, "alpha": alpha, "style": style, "direction": direction, "names": tuple(names)})
    for (part, label), text in rows.items():
        result.section(part).add(label, text, text)
    frame = pd.DataFrame({"ROC curve": rows})
    frame.index.names = ["section", "statistic"]
    result.values.update(auc=auc, se=se, ci_low=ci_low, ci_high=ci_high, p=p, direction=direction, n_controls=n_c, n_patients=n_p,
                         curve=curve, best_cutoff=float(cutoffs[best]), wide=frame)
    result.methods = (f"Receiver operating characteristic curve: a value {word} the cutoff is called positive; sensitivity and specificity "
                      f"at every cutoff with {level}s by the Wilson score method. Area under the curve = Mann-Whitney U / (n1 × n2), ties "
                      f"counted one half, with the standard error of Hanley & McNeil (1982) and a {level} of area ± z × SE limited to 0 to 1. "
                      f"The P value is a two-tailed z test of the area against 0.5 that uses the standard error computed under the null "
                      f"(area = 0.5) (alpha = {alpha:g}). The best cutoff maximizes Youden's J = sensitivity + specificity − 1.")
    if min(n_c, n_p) < 5:
        result.warnings.append("Fewer than 5 values in a group: the area, its CI and the cutoffs are rough.")
    return result


# --- Bland-Altman ---------------------------------------------------------------------------------------------


def bland_altman(a, b, *, method: str = "difference", ci: float = 0.95, names: tuple[str, str] = ("A", "B")) -> AnalysisResult:
    """Agreement between two methods measuring the same items (paired by position).

    ``method``: "difference" (A - B), "ratio" (A / B; both must be positive) or "percent" (100 * (A - B) / mean of A and B).
    The limits of agreement are bias ± 1.96 SD; ``ci`` sets the CI of the bias (Student's t) and of each limit (t, with the
    approximate standard error sqrt(3 SD² / n) of Bland & Altman, 1986)."""

    if method not in ("difference", "ratio", "percent"):
        raise StatisticsError("method must be 'difference', 'ratio' or 'percent'.")
    if not 0 < ci < 1:
        raise StatisticsError("The confidence level must be between 0 and 1 (for example 0.95).")
    try:
        a, b = np.asarray(a, dtype=float).reshape(-1), np.asarray(b, dtype=float).reshape(-1)
    except (TypeError, ValueError) as exc:
        raise StatisticsError("Both methods must be numbers.") from exc
    if a.size != b.size:
        raise StatisticsError(f"Both methods need the same number of values (got {a.size} and {b.size}); they are paired by position.")
    keep = np.isfinite(a) & np.isfinite(b)
    dropped = int(a.size - keep.sum())
    a, b = a[keep], b[keep]
    n = int(a.size)
    if n < 2:
        raise StatisticsError(f"Bland-Altman needs at least 2 complete pairs (there are {n}).")
    average = (a + b) / 2.0
    if method == "ratio":
        if (a <= 0).any() or (b <= 0).any():
            raise StatisticsError("Ratios need every value of both methods to be positive.")
        measure = a / b
    elif method == "percent":
        if (average == 0).any():
            raise StatisticsError("Percent differences are undefined where the mean of a pair is zero.")
        measure = 100.0 * (a - b) / average
    else:
        measure = a - b

    bias, sd = float(measure.mean()), float(measure.std(ddof=1))
    low, high = bias - 1.96 * sd, bias + 1.96 * sd
    t = stats.t.ppf(0.5 + ci / 2, n - 1)
    se_bias, se_limit = sd / math.sqrt(n), math.sqrt(3.0 * sd * sd / n)
    bias_ci = (bias - t * se_bias, bias + t * se_bias)
    low_ci = (low - t * se_limit, low + t * se_limit)
    high_ci = (high - t * se_limit, high + t * se_limit)

    level = f"{100 * ci:g}% CI"
    measure_name = {"difference": f"{names[0]} − {names[1]}", "ratio": f"{names[0]} / {names[1]}",
                    "percent": f"% difference ({names[0]} − {names[1]})"}[method]
    rows = {
        ("Bias", measure_name): fmt(bias),
        ("Bias", "SD of bias"): fmt(sd),
        ("Bias", level): fmt_ci(*bias_ci),
        ("Limits of agreement", "Lower limit"): fmt(low),
        ("Limits of agreement", f"Lower limit {level}"): fmt_ci(*low_ci),
        ("Limits of agreement", "Upper limit"): fmt(high),
        ("Limits of agreement", f"Upper limit {level}"): fmt_ci(*high_ci),
        ("Data", "Number of pairs"): str(n),
    }
    result = AnalysisResult("Bland-Altman", "", {"method": method, "ci": ci, "names": tuple(names)})
    for (part, label), text in rows.items():
        result.section(part).add(label, text, text)
    frame = pd.DataFrame({f"{names[0]} vs {names[1]}": rows})
    frame.index.names = ["section", "statistic"]
    result.values.update(bias=bias, sd=sd, low=low, high=high, bias_ci=bias_ci, low_ci=low_ci, high_ci=high_ci, n=n,
                         average=average, measure=measure, wide=frame)
    result.methods = (f"Bland-Altman analysis of {names[0]} against {names[1]}: the bias is the mean of {measure_name} and the limits of "
                      f"agreement are the bias ± 1.96 SD. The {level} of the bias uses Student's t; the {level}s of the limits use "
                      f"Student's t with the approximate standard error √(3 SD² / n) (Bland & Altman, 1986).")
    if dropped:
        result.warnings.append(f"{dropped} pair{'s' if dropped != 1 else ''} with a missing value {'were' if dropped != 1 else 'was'} left out.")
    if n < 10:
        result.warnings.append("Fewer than 10 pairs: the limits of agreement are imprecise.")
    return result


__all__ = ["bland_altman", "roc_curve"]
