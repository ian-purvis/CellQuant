"""Multiple variables: each row is one case (a replicate, an image, a cell) and each column one variable measured on it.

``correlation_matrix`` gives r (Pearson) or rₛ (Spearman) for every pair of variables, with its CI, P value and the number
of cases used, optionally adjusted for the number of pairs. ``pca`` is principal component analysis: the variables are
centred (and by default scaled to unit variance, so the analysis is of the correlation matrix), the components are the
eigenvectors, and the number to keep is chosen by parallel analysis (Horn, 1965), the eigenvalue > 1 rule (Kaiser), a
share of the variance, or set by hand.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from cellquant.errors import StatisticsError
from cellquant.stats.multcomp import STACK_METHODS, adjust_p
from cellquant.stats.pvalues import format_p, p_summary
from cellquant.stats.results import AnalysisResult, fmt, fmt_ci
from cellquant.stats.xy import correlation

MISSING = {"pairwise": "Each pair uses every case with both values", "listwise": "Only cases with every variable"}
SELECTIONS = {"parallel": "Parallel analysis", "kaiser": "Eigenvalue greater than 1", "variance": "Share of the variance", "number": "Number set by hand"}


def _matrix(variables: dict[str, object]) -> tuple[list[str], np.ndarray]:
    """The variables as a (cases × variables) array; shorter columns end in blanks."""

    names = [str(name) for name in variables]
    if len(names) < 2:
        raise StatisticsError("Multiple-variable analyses need at least two variables (columns).")
    try:
        columns = [np.asarray(values, dtype=float).reshape(-1) for values in variables.values()]
    except (TypeError, ValueError) as exc:
        raise StatisticsError("Every variable must be numbers.") from exc
    rows = max(column.size for column in columns)
    data = np.full((rows, len(names)), np.nan)
    for j, column in enumerate(columns):
        data[:column.size, j] = column
    data[~np.isfinite(data)] = np.nan
    return names, data


def correlation_matrix(variables: dict[str, object], *, method: str = "pearson", missing: str = "pairwise", adjust: str = "none",
                       ci: float = 0.95, alpha: float = 0.05, style: str = "classic", title: str = "") -> AnalysisResult:
    """Correlation of every pair of variables (columns), the rows being the cases.

    ``missing``: "pairwise" (each pair uses the cases with both values) or "listwise" (only cases with every value). ``adjust``: a
    P value adjustment for the number of pairs ("none", or a method of ``adjust_p``: "holm", "bonferroni", "bh", "bky" …)."""

    if method not in ("pearson", "spearman"):
        raise StatisticsError("method must be 'pearson' or 'spearman'.")
    if missing not in MISSING:
        raise StatisticsError("missing must be 'pairwise' or 'listwise'.")
    if adjust not in STACK_METHODS:
        raise StatisticsError(f"adjust must be one of {', '.join(STACK_METHODS)}.")
    if not 0 < ci < 1 or not 0 < alpha < 1:
        raise StatisticsError("The confidence level and alpha must be between 0 and 1.")
    names, data = _matrix(variables)
    present = np.isfinite(data).any(axis=1)
    complete = np.isfinite(data).all(axis=1)
    if missing == "listwise":
        dropped = int((present & ~complete).sum())
        data = data[complete]
    else:
        dropped = int((present & ~complete).sum())
    k = len(names)
    r, p, n = np.eye(k), np.zeros((k, k)), np.zeros((k, k), int)
    low, high = np.eye(k), np.eye(k)
    pairs, failed = [], []
    for i in range(k):
        n[i, i] = int(np.isfinite(data[:, i]).sum())
        for j in range(i + 1, k):
            try:
                item = correlation(data[:, i], data[:, j], method, ci)
            except StatisticsError as exc:
                failed.append(f"{names[i]} – {names[j]}: {exc}")
                r[i, j] = r[j, i] = p[i, j] = p[j, i] = low[i, j] = low[j, i] = high[i, j] = high[j, i] = np.nan
                n[i, j] = n[j, i] = int((np.isfinite(data[:, i]) & np.isfinite(data[:, j])).sum())
                pairs.append((i, j))
                continue
            r[i, j] = r[j, i] = item["r"]
            p[i, j] = p[j, i] = item["p"]
            low[i, j] = low[j, i] = item["ci_low"]
            high[i, j] = high[j, i] = item["ci_high"]
            n[i, j] = n[j, i] = item["n"]
            pairs.append((i, j))
    raw = np.array([p[i, j] for i, j in pairs])
    if adjust != "none":
        adjusted = adjust_p(raw, adjust, alpha)["adjusted"]
    else:
        adjusted = raw
    p_adj = np.zeros((k, k))
    for (i, j), value in zip(pairs, adjusted):
        p_adj[i, j] = p_adj[j, i] = value

    symbol = "r" if method == "pearson" else "rₛ"
    level = f"{100 * ci:g}% CI"
    p_label = "P value" if adjust == "none" else f"Adjusted P ({STACK_METHODS[adjust]})"
    result = AnalysisResult("Correlation matrix", title, {"method": method, "missing": missing, "adjust": adjust, "ci": ci, "alpha": alpha,
                                                          "style": style})
    matrix = result.section(f"{symbol} (each row against each column)")
    for i, name in enumerate(names):
        matrix.add(name, r[i].tolist(), "; ".join(f"{names[j]} {fmt(r[i, j], 3)}" for j in range(k)))
    detail = result.section("Each pair")
    for (i, j), raw_p, adj_p in zip(pairs, raw, adjusted):
        if not np.isfinite(r[i, j]):
            detail.add(f"{names[i]} vs. {names[j]}", None, f"Undefined (n = {n[i, j]})")
            continue
        text = (f"{symbol} = {fmt(r[i, j], 3)} ({level} {fmt_ci(low[i, j], high[i, j])}); {p_label} {format_p(adj_p, style=style)} "
                f"{p_summary(adj_p, style)}; n = {n[i, j]}")
        detail.add(f"{names[i]} vs. {names[j]}", {"r": r[i, j], "p": raw_p, "p_adjusted": adj_p, "n": int(n[i, j])}, text)
    data_section = result.section("Data")
    data_section.add("Variables", k)
    data_section.add("Cases (rows) used", int(complete.sum()) if missing == "listwise" else int(present.sum()))
    data_section.add("Missing values", missing, MISSING[missing])
    data_section.add("Pairs", len(pairs))
    frame = pd.DataFrame(r, index=names, columns=names)
    result.values.update(names=names, r=r, p=p, p_adjusted=p_adj, n=n, ci_low=low, ci_high=high, matrix=frame,
                         wide=pd.DataFrame({"Correlation matrix": {(s.title, row.label): row.formatted for s in result.sections for row in s.rows}}))
    result.values["wide"].index.names = ["section", "statistic"]

    what = "Pearson correlation coefficients r" if method == "pearson" else "Spearman rank correlation coefficients rₛ"
    result.methods = (f"{what} between every pair of {k} variables, each row of the table being one case; {MISSING[missing].lower()}. "
                      f"{level}s by Fisher's z transformation{' (approximate for Spearman)' if method == 'spearman' else ''}; two-tailed P "
                      f"values (alpha = {alpha:g})")
    result.methods += "." if adjust == "none" else f", adjusted for the {len(pairs)} pairs by the {STACK_METHODS[adjust]} method."
    if dropped:
        if missing == "listwise":
            result.warnings.append(f"{dropped} row{'s' if dropped != 1 else ''} with a blank in some variable {'were' if dropped != 1 else 'was'} "
                                   "left out of every pair.")
        else:
            result.warnings.append(f"{dropped} row{'s' if dropped != 1 else ''} with a blank in some variable: each pair uses the rows with both "
                                   "values, so n differs between pairs.")
    result.warnings += [f"Not computed: {text}" for text in failed]
    if adjust == "none" and len(pairs) > 1:
        result.warnings.append(f"{len(pairs)} pairs are tested; with no adjustment some P < {alpha:g} are expected by chance alone.")
    return result


# --- Principal component analysis ------------------------------------------------------------------------------


def _parallel(n: int, k: int, standardize: bool, scale: np.ndarray, simulations: int, seed: int) -> np.ndarray:
    """The 95th percentile of each eigenvalue from random normal data of the same size (and, unscaled, the same SDs)."""

    rng = np.random.default_rng(seed)
    values = np.empty((simulations, k))
    for s in range(simulations):
        sample = rng.standard_normal((n, k))
        if not standardize:
            sample = sample * scale
        sample -= sample.mean(axis=0)
        if standardize:
            sample /= sample.std(axis=0, ddof=1)
        values[s] = np.sort(np.linalg.eigvalsh(sample.T @ sample / (n - 1)))[::-1]
    return np.percentile(values, 95, axis=0)


def pca(variables: dict[str, object], *, standardize: bool = True, select: str = "parallel", variance: float = 0.8, number: int = 2,
        simulations: int = 1000, seed: int = 0, title: str = "") -> AnalysisResult:
    """Principal component analysis of the cases (rows) with every variable present.

    ``standardize``: scale each variable to unit variance (the correlation matrix); otherwise only centre it (the covariance matrix).
    ``select`` picks how many components to keep: "parallel" (each eigenvalue above the 95th percentile of ``simulations`` random
    data sets of the same size, ``seed`` fixed so the result repeats), "kaiser" (eigenvalue > 1, standardized only, or above the mean
    eigenvalue otherwise), "variance" (the fewest reaching ``variance`` of the total) or "number". At least one is kept."""

    if select not in SELECTIONS:
        raise StatisticsError(f"select must be one of {', '.join(SELECTIONS)}.")
    if select == "variance" and not 0 < variance <= 1:
        raise StatisticsError("The share of the variance must be between 0 and 1 (for example 0.8).")
    if not isinstance(simulations, (int, np.integer)) or simulations < 100:
        raise StatisticsError("Parallel analysis needs at least 100 simulations.")
    names, data = _matrix(variables)
    present = np.isfinite(data).any(axis=1)
    complete = np.isfinite(data).all(axis=1)
    dropped = int((present & ~complete).sum())
    x = data[complete]
    n, k = x.shape
    if n < 3:
        raise StatisticsError(f"PCA needs at least 3 cases with every variable (there {'is' if n == 1 else 'are'} {n}).")
    sd = x.std(axis=0, ddof=1)
    flat = [name for name, s in zip(names, sd) if s == 0]
    if flat:
        raise StatisticsError(f"{', '.join(flat)} {'does' if len(flat) == 1 else 'do'} not vary, so {'it' if len(flat) == 1 else 'they'} "
                              "cannot enter a PCA; remove the column.")
    if select == "number" and not (isinstance(number, (int, np.integer)) and 1 <= number <= min(k, n - 1)):
        raise StatisticsError(f"The number of components must be a whole number from 1 to {min(k, n - 1)}.")
    mean = x.mean(axis=0)
    z = (x - mean) / sd if standardize else x - mean
    matrix = z.T @ z / (n - 1)
    eigenvalues, vectors = np.linalg.eigh(matrix)
    order = np.argsort(eigenvalues)[::-1]
    eigenvalues, vectors = np.clip(eigenvalues[order], 0.0, None), vectors[:, order]
    for c in range(k):  # a fixed sign: the largest loading of each component is positive
        if vectors[np.argmax(np.abs(vectors[:, c])), c] < 0:
            vectors[:, c] = -vectors[:, c]
    usable = min(k, n - 1)
    eigenvalues, vectors = eigenvalues[:usable], vectors[:, :usable]
    total = float(np.trace(matrix))
    share = eigenvalues / total
    cumulative = np.cumsum(share)
    threshold = None
    if select == "parallel":
        threshold = _parallel(n, k, standardize, sd, simulations, seed)[:usable]
        keep = 0
        while keep < usable and eigenvalues[keep] > threshold[keep]:
            keep += 1
    elif select == "kaiser":
        cut = 1.0 if standardize else total / k
        keep = int((eigenvalues > cut).sum())
    elif select == "variance":
        keep = int(np.searchsorted(cumulative, variance - 1e-12) + 1)
    else:
        keep = int(number)
    forced = keep < 1
    keep = max(1, min(keep, usable))
    scores = z @ vectors[:, :keep]
    loadings = vectors * np.sqrt(eigenvalues)  # standardized: the correlation of each variable with each component
    labels = [f"PC{c + 1}" for c in range(usable)]

    result = AnalysisResult("Principal component analysis", title, {"standardize": standardize, "select": select, "variance": variance,
                                                                   "number": number, "simulations": simulations, "seed": seed})
    part = result.section("Components")
    for c in range(usable):
        extra = "" if threshold is None else f"; parallel analysis 95th percentile {fmt(threshold[c], 3)}"
        part.add(labels[c], {"eigenvalue": eigenvalues[c], "share": share[c], "cumulative": cumulative[c]},
                 f"Eigenvalue {fmt(eigenvalues[c], 4)}; {100 * share[c]:.1f}% of the variance ({100 * cumulative[c]:.1f}% cumulative){extra}"
                 + ("; kept" if c < keep else ""))
    kept = result.section("Components kept")
    kept.add("Method", select, SELECTIONS[select] + (f" ({100 * variance:g}%)" if select == "variance" else ""))
    kept.add("Number kept", keep, str(keep))
    kept.add("Variance explained by them", cumulative[keep - 1], f"{100 * cumulative[keep - 1]:.1f}%")
    what = "Loadings (correlation of each variable with each component)" if standardize else "Loadings (eigenvector × √eigenvalue)"
    load = result.section(what)
    for i, name in enumerate(names):
        load.add(name, loadings[i, :keep].tolist(), "; ".join(f"{labels[c]} {fmt(loadings[i, c], 3)}" for c in range(keep)))
    vec = result.section("Eigenvectors (coefficients)")
    for i, name in enumerate(names):
        vec.add(name, vectors[i, :keep].tolist(), "; ".join(f"{labels[c]} {fmt(vectors[i, c], 3)}" for c in range(keep)))
    data_section = result.section("Data")
    data_section.add("Variables", k)
    data_section.add("Cases (rows) used", n)
    data_section.add("Scaling", standardize, "Each variable scaled to unit variance (correlation matrix)" if standardize else
                     "Centred only (covariance matrix)")
    rows = np.flatnonzero(complete)
    score_frame = pd.DataFrame(scores, columns=labels[:keep])
    score_frame.insert(0, "row", rows + 1)
    result.values.update(names=names, eigenvalues=eigenvalues, share=share, cumulative=cumulative, vectors=vectors, loadings=loadings, keep=keep,
                         scores=score_frame, threshold=threshold, n=n, rows=rows,
                         wide=pd.DataFrame({"PCA": {(s.title, row.label): row.formatted for s in result.sections for row in s.rows}}))
    result.values["wide"].index.names = ["section", "statistic"]

    rule = {"parallel": f"parallel analysis (eigenvalues above the 95th percentile of {simulations} random normal data sets of the same size, "
                        f"seed {seed})",
            "kaiser": "the eigenvalue > 1 rule" if standardize else "eigenvalues above their mean",
            "variance": f"the fewest components explaining at least {100 * variance:g}% of the variance",
            "number": "a number set by hand"}[select]
    result.methods = (f"Principal component analysis of {k} variables in {n} cases (rows with every value), each variable "
                      f"{'centred and scaled to unit variance (correlation matrix)' if standardize else 'centred (covariance matrix)'}; "
                      f"components are the eigenvectors, ordered by eigenvalue, each signed so its largest coefficient is positive. "
                      f"{keep} component{'s' if keep != 1 else ''} kept by {rule}, explaining {100 * cumulative[keep - 1]:.1f}% of the variance.")
    if dropped:
        result.warnings.append(f"{dropped} row{'s' if dropped != 1 else ''} with a blank in some variable {'were' if dropped != 1 else 'was'} "
                               "left out (PCA needs every value of a case).")
    if forced:
        result.warnings.append("No component passed the rule; the first is shown anyway. The variables may share little structure.")
    if n < 5 * k:
        result.warnings.append(f"{n} cases for {k} variables: fewer than 5 per variable, so the components may not be stable.")
    if not standardize and np.ptp(np.log10(sd)) > 1:
        result.warnings.append("The variables' SDs differ more than tenfold and are not scaled, so the largest ones dominate the components; "
                               "consider scaling to unit variance.")
    if math.isclose(total, 0.0):
        raise StatisticsError("The variables do not vary.")
    return result


__all__ = ["MISSING", "SELECTIONS", "correlation_matrix", "pca"]
