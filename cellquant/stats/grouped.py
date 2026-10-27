"""Grouped tables (two grouping factors) and two-way ANOVA.

A grouped table has one row per level of the row factor and one data set per level of the column factor;
each cell holds the replicate values of that row and data set side by side. A blank is NaN.

``two_way_anova`` fits y = μ + α_i (row) + β_j (column) + (αβ)_ij (interaction) + ε by least squares with
sum-to-zero (effect) coding. Each effect's SS is the rise in the residual SS when that effect alone is left
out of the full model (Type III SS), so unequal numbers of values per cell are handled correctly; with equal
numbers it is the classical two-way ANOVA. Without the interaction (``interaction=False``, or one value per
cell) the model is additive. Multiple comparisons use the model's cell means or least-squares (marginal)
means, the pooled residual mean square and its df.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy import stats

from cellquant.errors import StatisticsError
from cellquant.stats.multcomp import ComparisonPlan, Estimates, comparison_rows, default_method, run_comparisons
from cellquant.stats.pvalues import format_p, p_summary
from cellquant.stats.results import AnalysisResult, fmt
from cellquant.stats.tables import _as_float

# key: what is compared (shown in menus)
COMPARE = {
    "within_rows": "Within each row, compare the data sets (columns)",
    "within_columns": "Within each data set (column), compare the rows",
    "column_means": "Compare the data set (column) means, averaged over rows (main column effect)",
    "row_means": "Compare the row means, averaged over data sets (main row effect)",
}
FAMILIES = {"per_group": "One family per row (or per data set)", "combined": "All comparisons in one family"}
TECHNICAL_REPLICATE_N = 30


@dataclass
class GroupedTable:
    """``values[i, j]`` are the replicates of row ``rows[i]`` in data set ``columns[j]`` (NaN = blank)."""

    title: str = ""
    rows: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    values: np.ndarray = field(default_factory=lambda: np.zeros((0, 0, 0)))

    def __post_init__(self) -> None:
        self.rows = [str(name) for name in self.rows]
        self.columns = [str(name) for name in self.columns]
        for what, names in (("Row", self.rows), ("Data set", self.columns)):
            repeated = sorted({name for name in names if names.count(name) > 1})
            if repeated:
                raise StatisticsError(f"{what} names must be unique; repeated: {', '.join(repeated)}.")
        values = np.asarray(self.values, dtype=float)
        if values.ndim != 3 or values.shape[:2] != (len(self.rows), len(self.columns)):
            raise StatisticsError(f"A grouped table needs values shaped (rows, data sets, replicates): "
                                  f"{len(self.rows)} × {len(self.columns)} × replicates, not {values.shape}.")
        if np.isinf(values).any():
            raise StatisticsError("A grouped table has an infinite value; enter a number or leave the cell blank.")
        self.values = values

    @classmethod
    def from_cells(cls, cells: Mapping[tuple[str, str], Iterable[float]], rows: Sequence[str] | None = None,
                   columns: Sequence[str] | None = None, title: str = "") -> "GroupedTable":
        """A table from ``{(row, data set): values}``. Rows and data sets are in order of first appearance unless given."""

        rows = list(rows) if rows is not None else list(dict.fromkeys(str(key[0]) for key in cells))
        columns = list(columns) if columns is not None else list(dict.fromkeys(str(key[1]) for key in cells))
        lists = {(str(r), str(c)): _as_float(v, f"{r} / {c}") for (r, c), v in cells.items()}
        depth = max([len(v) for v in lists.values()] + [1])
        values = np.full((len(rows), len(columns), depth), np.nan)
        for (r, c), v in lists.items():
            if r in rows and c in columns:
                values[rows.index(r), columns.index(c), : len(v)] = v
        return cls(title, list(rows), list(columns), values)

    @classmethod
    def from_long(cls, df: pd.DataFrame, value: str, row: str, column: str, row_order: Iterable[str] | None = None,
                  column_order: Iterable[str] | None = None, title: str = "") -> "GroupedTable":
        """A grouped table from a long table (one measurement per line): ``row`` and ``column`` name the two factors."""

        for name in (value, row, column):
            if name not in df.columns:
                raise StatisticsError(f"The table has no column {name!r}.")
        frame = df.loc[df[row].notna() & df[column].notna()]
        rows = [str(x) for x in (row_order if row_order is not None else pd.unique(frame[row]))]
        columns = [str(x) for x in (column_order if column_order is not None else pd.unique(frame[column]))]
        numbers = _as_float(frame[value].to_numpy(), value)
        cells: dict[tuple[str, str], list[float]] = {(r, c): [] for r in rows for c in columns}
        for r, c, v in zip(frame[row].astype(str), frame[column].astype(str), numbers):
            if (r, c) in cells:
                cells[(r, c)].append(v)
        return cls.from_cells(cells, rows, columns, title=title or value)

    @property
    def replicates(self) -> int:
        return int(self.values.shape[2])

    def cell(self, row: str | int, column: str | int) -> np.ndarray:
        """The values of one cell without blanks."""

        i = row if isinstance(row, (int, np.integer)) else self.rows.index(str(row))
        j = column if isinstance(column, (int, np.integer)) else self.columns.index(str(column))
        x = self.values[i, j]
        return x[np.isfinite(x)]

    def to_long(self) -> pd.DataFrame:
        """``row``, ``column``, ``replicate`` (1-based) and ``value``, blanks left out."""

        records = [{"row": r, "column": c, "replicate": k + 1, "value": float(self.values[i, j, k])}
                   for i, r in enumerate(self.rows) for j, c in enumerate(self.columns) for k in range(self.replicates)
                   if np.isfinite(self.values[i, j, k])]
        return pd.DataFrame.from_records(records, columns=["row", "column", "replicate", "value"])


# ---------------------------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------------------------


def _effect_codes(index: np.ndarray, levels: int) -> np.ndarray:
    """Sum-to-zero coding: level l < L-1 → unit vector l; the last level → all -1."""

    codes = np.zeros((index.size, levels - 1))
    for position, level in enumerate(index):
        if level == levels - 1:
            codes[position] = -1.0
        else:
            codes[position, level] = 1.0
    return codes


def _design(i: np.ndarray, j: np.ndarray, r: int, c: int, interaction: bool) -> tuple[np.ndarray, dict[str, slice]]:
    a, b = _effect_codes(i, r), _effect_codes(j, c)
    parts = [np.ones((i.size, 1)), a, b]
    if interaction:
        parts.append(np.einsum("na,nb->nab", a, b).reshape(i.size, -1))
    X = np.hstack(parts)
    terms = {"row": slice(1, r), "column": slice(r, r + c - 1)}
    if interaction:
        terms["interaction"] = slice(r + c - 1, X.shape[1])
    return X, terms


def _rss(X: np.ndarray, y: np.ndarray) -> tuple[float, np.ndarray, int]:
    beta, _res, rank, _sv = np.linalg.lstsq(X, y, rcond=None)
    residual = y - X @ beta
    return float(residual @ residual), beta, int(rank)


def fit_two_way(table: GroupedTable, interaction: bool = True) -> dict:
    """Least-squares fit of the two-way model and its Type III ANOVA table (see the module docstring)."""

    if not isinstance(table, GroupedTable):
        raise StatisticsError("Pass a GroupedTable (rows × data sets × replicates).")
    r, c = len(table.rows), len(table.columns)
    if r < 2 or c < 2:
        raise StatisticsError("Two-way ANOVA needs at least 2 rows and 2 data sets (columns).")
    long = table.to_long()
    i = long["row"].map({name: n for n, name in enumerate(table.rows)}).to_numpy(int)
    j = long["column"].map({name: n for n, name in enumerate(table.columns)}).to_numpy(int)
    y = long["value"].to_numpy(float)
    counts = np.zeros((r, c), int)
    np.add.at(counts, (i, j), 1)
    empty_rows = [table.rows[n] for n in range(r) if counts[n].sum() == 0]
    empty_columns = [table.columns[n] for n in range(c) if counts[:, n].sum() == 0]
    if empty_rows or empty_columns:
        raise StatisticsError(f"Every row and data set needs values; empty: {', '.join(empty_rows + empty_columns)}.")
    notes = []
    if interaction and (counts == 0).any():
        missing = [f"{table.rows[a]} / {table.columns[b]}" for a, b in zip(*np.nonzero(counts == 0))]
        raise StatisticsError(f"The interaction cannot be estimated with empty cells ({', '.join(missing)}). "
                              "Fit the model without the interaction, or fill those cells.")
    if interaction and y.size <= r * c:
        interaction = False
        notes.append("Each cell has only one value, so the interaction cannot be told apart from scatter: "
                     "the model has no interaction term (the additive model).")
    X, terms = _design(i, j, r, c, interaction)
    rss, beta, rank = _rss(X, y)
    df_resid = y.size - rank
    if df_resid < 1:
        raise StatisticsError("Two-way ANOVA needs more values than cells (some cell needs at least 2 values).")
    ms_resid = rss / df_resid
    ss_total = float(((y - y.mean()) ** 2).sum())
    rows = []
    for name in ("interaction", "row", "column"):
        if name not in terms:
            continue
        keep = np.ones(X.shape[1], bool)
        keep[terms[name]] = False
        reduced, _b, reduced_rank = _rss(X[:, keep], y)
        df = rank - reduced_rank
        ss = max(reduced - rss, 0.0)
        ms = ss / df if df else np.nan
        f = ms / ms_resid if ms_resid > 0 else np.nan
        p = float(stats.f.sf(f, df, df_resid)) if np.isfinite(f) else np.nan
        rows.append({"term": name, "ss": ss, "df": int(df), "ms": ms, "f": f, "p": p, "percent": 100 * ss / ss_total if ss_total > 0 else np.nan})
    unscaled = np.linalg.pinv(X.T @ X)
    # Model cell means and their covariance: row i, column j → the coded row of the design.
    cell_X, _t = _design(np.repeat(np.arange(r), c), np.tile(np.arange(c), r), r, c, interaction)
    means = cell_X @ beta
    cov = cell_X @ unscaled @ cell_X.T * ms_resid
    return {"rows": rows, "ss_resid": rss, "df_resid": int(df_resid), "ms_resid": ms_resid, "ss_total": ss_total, "n": int(y.size),
            "counts": counts, "interaction": interaction, "cell_means": means.reshape(r, c), "cell_X": cell_X, "beta": beta,
            "cov_cells": cov, "unscaled": unscaled, "notes": notes, "balanced": bool((counts == counts.flat[0]).all())}


# ---------------------------------------------------------------------------------------------
# Comparisons
# ---------------------------------------------------------------------------------------------


def _estimates(names: list[str], L: np.ndarray, fit: dict, n: np.ndarray, label: str) -> Estimates:
    """Estimates L·β with covariance L (X'X)⁻¹ L' · MS_residual."""

    return Estimates(names, L @ fit["beta"], n.astype(float), L @ fit["unscaled"] @ L.T * fit["ms_resid"], float(fit["df_resid"]), "pooled",
                     error_label=label)


def _comparisons(table: GroupedTable, fit: dict, compare: str, goal: str, method: str | None, control, family: str,
                 alpha: float, ci: float, style: str) -> list[tuple[str, dict]]:
    """(group name or "", run_comparisons output) for each family."""

    r, c = len(table.rows), len(table.columns)
    counts = fit["counts"]
    label = "pooled SD from the two-way ANOVA"
    if compare in ("column_means", "row_means"):
        by_column = compare == "column_means"
        names = table.columns if by_column else table.rows
        groups = [[a * c + b for a in range(r)] for b in range(c)] if by_column else [[a * c + b for b in range(c)] for a in range(r)]
        L = np.array([fit["cell_X"][index].mean(axis=0) for index in groups])  # least-squares mean: cells weighted equally
        n = counts.sum(axis=0) if by_column else counts.sum(axis=1)
        plan = ComparisonPlan(goal=goal, method=method, control=control)
        return [("", run_comparisons(_estimates(list(names), L, fit, n, label + "; least-squares means"), plan, alpha=alpha, ci=ci, style=style))]
    if compare not in COMPARE:
        raise StatisticsError(f"Unknown comparison {compare!r}; choose one of {', '.join(COMPARE)}.")
    within_rows = compare == "within_rows"
    outer = table.rows if within_rows else table.columns
    inner = table.columns if within_rows else table.rows

    def cells_of(o: int) -> list[int]:
        return [o * c + k for k in range(c)] if within_rows else [k * c + o for k in range(r)]

    if family == "combined":
        names = [f"{table.rows[x // c]}: {table.columns[x % c]}" for x in range(r * c)]
        if goal == "control":
            if control is None or str(control) not in inner:
                raise StatisticsError(f"Choose the reference (control) among: {', '.join(inner)}.")
            reference = inner.index(str(control))
            pairs = [(names[cells_of(o)[reference]], names[cells_of(o)[k]]) for o in range(len(outer)) for k in range(len(inner)) if k != reference]
        else:
            pairs = [(names[cells_of(o)[a]], names[cells_of(o)[b]]) for o in range(len(outer)) for a, b in itertools.combinations(range(len(inner)), 2)]
        chosen = method or "sidak"
        plan = ComparisonPlan(goal="selected", method=chosen, pairs=pairs)
        est = _estimates(names, fit["cell_X"], fit, counts.ravel(), label)
        return [("", run_comparisons(est, plan, alpha=alpha, ci=ci, style=style))]
    out = []
    for o, name in enumerate(outer):
        index = cells_of(o)
        n = counts.ravel()[index]
        plan = ComparisonPlan(goal=goal, method=method, control=control)
        out.append((name, run_comparisons(_estimates(list(inner), fit["cell_X"][index], fit, n, label), plan, alpha=alpha, ci=ci, style=style)))
    return out


def two_way_anova(table: GroupedTable, *, interaction: bool = True, compare: str | None = None, goal: str = "all", method: str | None = None,
                  control: str | None = None, family: str = "per_group", alpha: float = 0.05, ci: float = 0.95, style: str = "classic") -> AnalysisResult:
    """Ordinary two-way ANOVA of a grouped table, with optional multiple comparisons.

    ``compare``: None, or a key of COMPARE. ``goal``: "all" (every pair) or "control" (each vs. ``control``).
    ``family``: for comparisons within rows or data sets, "per_group" (each row or data set is a family; Tukey
    and Dunnett allowed) or "combined" (every comparison in one family; Šidák, Holm-Šidák, Bonferroni, Fisher's LSD
    or FDR)."""

    if not 0 < alpha < 1:
        raise StatisticsError("alpha must be between 0 and 1 (for example 0.05).")
    if not 0 < ci < 1:
        raise StatisticsError("The confidence level must be between 0 and 1 (for example 0.95).")
    if goal not in ("all", "control"):
        raise StatisticsError("goal must be 'all' (every pair) or 'control' (each vs. a reference).")
    if family not in FAMILIES:
        raise StatisticsError(f"family must be one of {', '.join(FAMILIES)}.")
    fit = fit_two_way(table, interaction)
    result = AnalysisResult("Two-way ANOVA", table.title, {"interaction": interaction, "compare": compare, "goal": goal, "method": method,
                                                            "control": control, "family": family, "alpha": alpha, "ci": ci, "style": style})
    head = result.section("Table analyzed")
    head.add("Table", table.title)
    head.add("Rows (row factor)", tuple(table.rows), ", ".join(table.rows))
    head.add("Data sets (column factor)", tuple(table.columns), ", ".join(table.columns))
    labels = {"interaction": "Interaction", "row": "Row factor", "column": "Column factor"}
    variation = result.section("Source of variation")
    significance = result.section("Significance")
    for row in fit["rows"]:
        variation.add(labels[row["term"]], row["percent"], f"{fmt(row['percent'])}% of total variation")
        sig = bool(row["p"] < alpha) if np.isfinite(row["p"]) else None
        significance.add(labels[row["term"]], row["p"],
                         f"F ({row['df']}, {fit['df_resid']}) = {fmt(row['f'])}; P = {format_p(row['p'], style=style)} "
                         f"({p_summary(row['p'], style)}); significant? {'' if sig is None else ('Yes' if sig else 'No')}")
    anova = result.section("ANOVA table")
    for row in fit["rows"]:
        anova.add(labels[row["term"]], row, f"SS = {fmt(row['ss'])}; DF = {row['df']}; MS = {fmt(row['ms'])}; "
                                            f"F ({row['df']}, {fit['df_resid']}) = {fmt(row['f'])}; P = {format_p(row['p'], style=style)}")
    anova.add("Residual", {"ss": fit["ss_resid"], "df": fit["df_resid"], "ms": fit["ms_resid"]},
              f"SS = {fmt(fit['ss_resid'])}; DF = {fit['df_resid']}; MS = {fmt(fit['ms_resid'])}")
    data = result.section("Data summary")
    data.add("Number of rows", len(table.rows))
    data.add("Number of data sets (columns)", len(table.columns))
    data.add("Number of values", fit["n"])
    data.add("Equal numbers of values in every cell", fit["balanced"])
    cells = result.section("Cell summary")
    for a, row_name in enumerate(table.rows):
        for b, column_name in enumerate(table.columns):
            x = table.cell(a, b)
            sd = float(np.std(x, ddof=1)) if x.size > 1 else np.nan
            mean = float(x.mean()) if x.size else np.nan
            cells.add(f"{row_name}: {column_name}", {"n": int(x.size), "mean": mean, "sd": sd},
                      f"n = {x.size}; mean = {fmt(mean)}; SD = {fmt(sd)}" if x.size else "no values")
    result.warnings.extend(fit["notes"])
    if fit["counts"].max() > TECHNICAL_REPLICATE_N:
        result.warnings.append(
            f"A cell has more than {TECHNICAL_REPLICATE_N} values. If these are technical replicates (n, for example many objects "
            "from each biological replicate), the ANOVA treats them as independent and its P values are too small. Average them "
            "within each biological replicate (N) and analyze the N values.")
    if fit["counts"].min() < 2 and fit["interaction"]:
        result.warnings.append("Some cells have fewer than 2 values: the ANOVA cannot check its assumptions there, and power is low.")
    if not fit["balanced"]:
        result.warnings.append("Cells have different numbers of values: sums of squares are Type III (each effect adjusted for the "
                               "others), so they do not add up to the total.")
    by_term = {row["term"]: row for row in fit["rows"]}
    result.values.update(
        rows=list(table.rows), columns=list(table.columns), anova=fit["rows"], df_resid=fit["df_resid"], ms_resid=fit["ms_resid"],
        ss_resid=fit["ss_resid"], ss_total=fit["ss_total"], n=fit["n"], counts=fit["counts"].tolist(), interaction=fit["interaction"],
        cell_means=fit["cell_means"].tolist(), p_interaction=by_term.get("interaction", {}).get("p", np.nan),
        p_rows=by_term["row"]["p"], p_columns=by_term["column"]["p"])
    more = ""
    if compare:
        families = _comparisons(table, fit, compare, goal, method, control, family, alpha, ci, style)
        everything = []
        for name, out in families:
            for row in out["rows"]:
                row = dict(row, label=f"{name}: {row['label']}" if name else row["label"], within=name or None)
                everything.append(row)
        first = families[0][1]
        merged = dict(first, rows=everything, n_families=len(families), per_family=[len(out["rows"]) for _n, out in families],
                      warnings=sorted({text for _n, out in families for text in out["warnings"]}),
                      family_mode="per_reference")
        comparison_rows(result, merged, style)
        result.values["comparisons"] = everything
        result.values["compare"] = compare
        level = f"Q = {100 * alpha:g}%" if first["fdr"] else f"family-wise alpha = {alpha:g}"
        grouping = (f"one family per {'row' if compare == 'within_rows' else 'data set'}" if compare in ("within_rows", "within_columns")
                    and family == "per_group" else "one family")
        more = f" Multiple comparisons ({COMPARE[compare].lower()}): {first['name']} ({level}, {grouping}; pooled SD from the ANOVA)."
    model = "with interaction" if fit["interaction"] else "without interaction (additive)"
    result.methods = (f"Ordinary two-way ANOVA {model}, Type III sums of squares ({len(table.rows)} rows × {len(table.columns)} data sets, "
                      f"{fit['n']} values; alpha = {alpha:g}).{more}")
    return result


def default_two_way_method(goal: str, family: str) -> str:
    return "sidak" if family == "combined" else default_method(goal, "pooled")
