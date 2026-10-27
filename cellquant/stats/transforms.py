"""Data processing of column tables: transform, normalize, fraction of total, remove a baseline, and column math.

Each function returns a new table and leaves the one it is given unchanged. A value that has no result (the log of
zero or of a negative number, a division by zero) becomes a blank cell, and the notes say how many there were.
Equations are in docs/STATISTICS.md, "Data processing".
"""

from __future__ import annotations

from typing import Iterable

import numpy as np

from cellquant.errors import StatisticsError
from cellquant.stats.tables import Column, ColumnTable

# key: (label with Y for the value and K for the constant, needs K)
TRANSFORMS = {
    "multiply": ("Y × K", True),
    "divide": ("Y ÷ K", True),
    "add": ("Y + K", True),
    "subtract": ("Y − K", True),
    "power": ("Y^K", True),
    "square": ("Y²", False),
    "sqrt": ("√Y", False),
    "reciprocal": ("1 / Y", False),
    "log10": ("log10(Y)", False),
    "log2": ("log2(Y)", False),
    "ln": ("ln(Y)", False),
    "exp10": ("10^Y", False),
    "exp": ("e^Y", False),
    "abs": ("|Y|", False),
    "logit": ("ln(Y / (1 − Y))", False),
    "arcsine": ("arcsine(√Y)", False),
    "zscore": ("(Y − mean) / SD", False),
}
NORMALIZE_ZERO = {"smallest": "Smallest value in each column", "first": "First value in each column", "value": "A number"}
NORMALIZE_HUNDRED = {"largest": "Largest value in each column", "last": "Last value in each column", "sum": "Sum of each column",
                     "value": "A number"}
FRACTION_TOTALS = {"column": "Column total", "grand": "Grand total (every column)", "row": "Row total"}
BASELINE_OPERATIONS = {
    "subtract": "Y − baseline",
    "divide": "Y ÷ baseline",
    "percent": "100 × Y ÷ baseline (% of baseline)",
    "percent_change": "100 × (Y − baseline) ÷ baseline (% change)",
}
BASELINE_BY = {"mean": "The baseline column's mean", "row": "The baseline value in the same row (paired)"}
COLUMN_MATH = {
    "difference": ("{a} − {b}", lambda a, b: a - b),
    "ratio": ("{a} ÷ {b}", lambda a, b: a / b),
    "sum": ("{a} + {b}", lambda a, b: a + b),
    "product": ("{a} × {b}", lambda a, b: a * b),
    "percent_change": ("% change {b} → {a}", lambda a, b: 100.0 * (a - b) / b),
    "log2_ratio": ("log2({a} ÷ {b})", lambda a, b: np.log2(a / b)),
}


def _chosen(table: ColumnTable, columns: Iterable[str] | None) -> set[str]:
    if columns is None:
        return set(table.names)
    chosen = {str(name) for name in columns}
    missing = sorted(chosen - set(table.names))
    if missing:
        raise StatisticsError(f"The table has no column {', '.join(missing)}.")
    if not chosen:
        raise StatisticsError("Tick at least one column.")
    return chosen


def _finish(table: ColumnTable, values: dict[str, np.ndarray], notes: list[str], title: str,
            explained: Iterable[str] = ()) -> tuple[ColumnTable, list[str]]:
    """The new table; blank results are counted for the notes, except in ``explained`` columns (a note says why)."""

    columns, lost = [], 0
    for column in table.columns:
        new = values.get(column.name, column.values)
        with np.errstate(invalid="ignore"):
            bad = np.isfinite(column.values) & ~np.isfinite(new)
        lost += 0 if column.name in explained else int(bad.sum())
        new = np.where(np.isfinite(new), new, np.nan)
        columns.append(Column(column.name, new))
    if lost:
        notes.append(f"{lost} value(s) had no result (for example the log of 0 or of a negative number, or a division by 0) "
                     "and are blank.")
    return ColumnTable(title=title or table.title, columns=columns, paired=table.paired), notes


def _apply(function: str, y: np.ndarray, k: float | None) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        if function == "multiply":
            return y * k
        if function == "divide":
            return y / k if k != 0 else np.full_like(y, np.nan)
        if function == "add":
            return y + k
        if function == "subtract":
            return y - k
        if function == "power":
            return np.power(y, k)
        if function == "square":
            return y * y
        if function == "sqrt":
            return np.where(y >= 0, np.sqrt(np.abs(y)), np.nan)
        if function == "reciprocal":
            return np.where(y != 0, 1.0 / y, np.nan)
        if function in ("log10", "log2", "ln"):
            log = {"log10": np.log10, "log2": np.log2, "ln": np.log}[function]
            return np.where(y > 0, log(np.where(y > 0, y, 1.0)), np.nan)
        if function == "exp10":
            return np.power(10.0, y)
        if function == "exp":
            return np.exp(y)
        if function == "abs":
            return np.abs(y)
        if function == "logit":
            inside = (y > 0) & (y < 1)
            safe = np.where(inside, y, 0.5)
            return np.where(inside, np.log(safe / (1 - safe)), np.nan)
        if function == "arcsine":
            inside = (y >= 0) & (y <= 1)
            return np.where(inside, np.arcsin(np.sqrt(np.where(inside, y, 0.0))), np.nan)
        if function == "zscore":
            finite = y[np.isfinite(y)]
            if finite.size < 2 or np.std(finite, ddof=1) == 0:
                return np.full_like(y, np.nan)
            return (y - finite.mean()) / np.std(finite, ddof=1)
    raise StatisticsError(f"Unknown transform {function!r}.")


def transform(table: ColumnTable, function: str, k: float | None = None, columns: Iterable[str] | None = None) -> tuple[ColumnTable, list[str]]:
    """Every value Y of the chosen columns becomes f(Y); the other columns are copied."""

    if function not in TRANSFORMS:
        raise StatisticsError(f"Unknown transform {function!r}.")
    label, needs_k = TRANSFORMS[function]
    if needs_k and (k is None or not np.isfinite(k)):
        raise StatisticsError(f"{label} needs a number for K.")
    chosen = _chosen(table, columns)
    values = {column.name: _apply(function, column.values, None if k is None else float(k)) for column in table.columns
              if column.name in chosen}
    shown = label.replace("K", f"{k:g}") if needs_k else label
    notes = [f"Each value Y replaced by {shown}" + ("" if chosen == set(table.names) else f" in {', '.join(sorted(chosen))}") + "."]
    if function in ("logit", "arcsine"):
        notes.append("These transforms take proportions from 0 to 1: divide percentages by 100 first (Y ÷ K with K = 100).")
    return _finish(table, values, notes, table.title)


def normalize(table: ColumnTable, zero: str = "smallest", hundred: str = "largest", zero_value: float = 0.0,
              hundred_value: float = 100.0, result: str = "percent", columns: Iterable[str] | None = None) -> tuple[ColumnTable, list[str]]:
    """Each chosen column scaled so that its 0% value becomes 0 and its 100% value becomes 100 (or 1):
    Y' = (Y − Z) ÷ (H − Z) × 100."""

    if zero not in NORMALIZE_ZERO or hundred not in NORMALIZE_HUNDRED:
        raise StatisticsError("Choose what 0% and 100% are.")
    scale = 100.0 if result == "percent" else 1.0
    chosen = _chosen(table, columns)
    values, flat = {}, []
    for column in table.columns:
        if column.name not in chosen:
            continue
        finite_rows = np.flatnonzero(np.isfinite(column.values))
        if finite_rows.size == 0:
            values[column.name] = column.values.copy()
            continue
        y = column.values
        z = {"smallest": np.nanmin(y), "first": y[finite_rows[0]], "value": float(zero_value)}[zero]
        h = {"largest": np.nanmax(y), "last": y[finite_rows[-1]], "sum": np.nansum(y), "value": float(hundred_value)}[hundred]
        if h == z:
            values[column.name] = np.full_like(y, np.nan)
            flat.append(column.name)
            continue
        values[column.name] = (y - z) / (h - z) * scale
    notes = [f"Normalized: 0{'%' if scale == 100 else ''} = {NORMALIZE_ZERO[zero].lower()}"
             + (f" ({zero_value:g})" if zero == "value" else "")
             + f", {'100%' if scale == 100 else '1'} = {NORMALIZE_HUNDRED[hundred].lower()}"
             + (f" ({hundred_value:g})" if hundred == "value" else "") + "."]
    if flat:
        notes.append(f"{', '.join(flat)}: the 0% and 100% values are the same, so the column cannot be normalized and is blank.")
    return _finish(table, values, notes, table.title, flat)


def fraction_of_total(table: ColumnTable, total: str = "column", result: str = "percent",
                      columns: Iterable[str] | None = None) -> tuple[ColumnTable, list[str]]:
    """Each value divided by its column's total, the grand total of the chosen columns, or its row's total."""

    if total not in FRACTION_TOTALS:
        raise StatisticsError(f"Unknown total {total!r}.")
    scale = 100.0 if result == "percent" else 1.0
    chosen = _chosen(table, columns)
    picked = [column for column in table.columns if column.name in chosen]
    values = {}
    with np.errstate(divide="ignore", invalid="ignore"):
        if total == "column":
            for column in picked:
                values[column.name] = column.values / np.nansum(column.values) * scale
        elif total == "grand":
            grand = float(sum(np.nansum(column.values) for column in picked))
            for column in picked:
                values[column.name] = column.values / grand * scale
        else:
            rows = max(column.values.size for column in picked)
            padded = np.full((len(picked), rows), np.nan)
            for index, column in enumerate(picked):
                padded[index, : column.values.size] = column.values
            sums = np.nansum(padded, axis=0)
            for index, column in enumerate(picked):
                values[column.name] = padded[index, : column.values.size] / sums[: column.values.size] * scale
    notes = [f"Each value as a {'percentage' if scale == 100 else 'fraction'} of the {FRACTION_TOTALS[total].lower()}."]
    return _finish(table, values, notes, table.title)


def remove_baseline(table: ColumnTable, baseline: str, operation: str = "subtract", by: str = "mean",
                    keep_baseline: bool = True) -> tuple[ColumnTable, list[str]]:
    """Every column compared with a baseline column: by its mean (for example % of control) or row by row (paired)."""

    if baseline not in table.names:
        raise StatisticsError(f"The table has no column {baseline}.")
    if operation not in BASELINE_OPERATIONS or by not in BASELINE_BY:
        raise StatisticsError("Choose how to compare with the baseline.")
    base = table[baseline].values
    if by == "mean":
        finite = base[np.isfinite(base)]
        if finite.size == 0:
            raise StatisticsError(f"The baseline column {baseline} has no values.")
        reference = float(finite.mean())
    values = {}
    with np.errstate(divide="ignore", invalid="ignore"):
        for column in table.columns:
            y = column.values
            if by == "mean":
                b = reference
            else:
                b = np.full(y.size, np.nan)
                b[: min(y.size, base.size)] = base[: min(y.size, base.size)]
            values[column.name] = {"subtract": lambda: y - b, "divide": lambda: y / b, "percent": lambda: 100.0 * y / b,
                                   "percent_change": lambda: 100.0 * (y - b) / b}[operation]()
    processed, notes = _finish(table, values, [], table.title)
    if not keep_baseline:
        processed = ColumnTable(title=processed.title, columns=[column for column in processed.columns if column.name != baseline],
                                paired=processed.paired)
        if not processed.columns:
            raise StatisticsError("Leaving out the baseline column leaves no columns.")
    where = f"the mean of {baseline} ({reference:.4g})" if by == "mean" else f"{baseline} in the same row"
    said = {"subtract": "Each value minus {}", "divide": "Each value divided by {}", "percent": "Each value as a % of {}",
            "percent_change": "Each value as a % change from {}"}[operation].format(where)
    notes.insert(0, said + ("" if keep_baseline else f"; {baseline} left out") + ".")
    if by == "row" and not table.paired:
        notes.append("Row by row assumes each row is one subject (a paired table); tick Paired on the data table if it is.")
    return processed, notes


def column_math(table: ColumnTable, a: str, b: str, operation: str = "difference") -> tuple[ColumnTable, list[str]]:
    """A new table with one column computed row by row from two columns (for example the paired difference A − B)."""

    for name in (a, b):
        if name not in table.names:
            raise StatisticsError(f"The table has no column {name}.")
    if operation not in COLUMN_MATH:
        raise StatisticsError(f"Unknown column math {operation!r}.")
    label, function = COLUMN_MATH[operation]
    first, second = table[a].values, table[b].values
    rows = max(first.size, second.size)
    x = np.full(rows, np.nan)
    y = np.full(rows, np.nan)
    x[: first.size] = first
    y[: second.size] = second
    with np.errstate(divide="ignore", invalid="ignore"):
        result = function(x, y)
    name = label.format(a=a, b=b)
    source = ColumnTable(title=table.title, columns=[Column(name, np.where(np.isfinite(x) & np.isfinite(y), x, np.nan))])
    processed, notes = _finish(source, {name: result}, [], table.title)
    notes.insert(0, f"{name}, row by row: each row is one subject.")
    if not table.paired:
        notes.append("Column math pairs the two columns row by row; that only means something when each row is one subject.")
    return processed, notes


PROCESSES = {
    "transform": "Transform",
    "normalize": "Normalize",
    "fraction": "Fraction of total",
    "baseline": "Remove baseline",
    "column_math": "Column math",
}


def process(table: ColumnTable, options: dict) -> tuple[ColumnTable, list[str]]:
    """Run the processing ``options["kind"]`` names with the rest of ``options``."""

    kind = options.get("kind")
    if kind == "transform":
        return transform(table, options.get("function", "log10"), options.get("k"), options.get("columns"))
    if kind == "normalize":
        return normalize(table, options.get("zero", "smallest"), options.get("hundred", "largest"), options.get("zero_value", 0.0),
                         options.get("hundred_value", 100.0), options.get("result", "percent"), options.get("columns"))
    if kind == "fraction":
        return fraction_of_total(table, options.get("total", "column"), options.get("result", "percent"), options.get("columns"))
    if kind == "baseline":
        return remove_baseline(table, options.get("baseline") or (table.names[0] if table.names else ""), options.get("operation", "subtract"),
                               options.get("by", "mean"), bool(options.get("keep_baseline", True)))
    if kind == "column_math":
        names = table.names
        return column_math(table, options.get("a") or (names[0] if names else ""), options.get("b") or (names[1] if len(names) > 1 else ""),
                           options.get("operation", "difference"))
    raise StatisticsError(f"Unknown data processing {kind!r}.")


def renamed(options: dict, names: dict[str, str]) -> dict:
    """``options`` with the columns it names renamed (``names``: old name -> new name)."""

    out = dict(options)
    if out.get("columns") is not None:
        out["columns"] = [names.get(name, name) for name in out["columns"]]
    for key in ("baseline", "a", "b"):
        if out.get(key) in names:
            out[key] = names[out[key]]
    return out


def label(options: dict) -> str:
    """A short name for the processing, for sheet names: "log10(Y)", "Normalized", "% of A"…"""

    kind = options.get("kind")
    if kind == "transform":
        text, needs_k = TRANSFORMS.get(options.get("function", ""), ("Transformed", False))
        return text.replace("K", f"{options.get('k', 0):g}") if needs_k and options.get("k") is not None else text
    if kind == "normalize":
        return "Normalized"
    if kind == "fraction":
        return "% of total" if options.get("result", "percent") == "percent" else "Fraction of total"
    if kind == "baseline":
        base = options.get("baseline", "baseline")
        return {"subtract": f"Minus {base}", "divide": f"Ratio to {base}", "percent": f"% of {base}",
                "percent_change": f"% change from {base}"}.get(options.get("operation", "subtract"), "Baseline removed")
    if kind == "column_math":
        text = COLUMN_MATH.get(options.get("operation", "difference"), ("{a} − {b}", None))[0]
        return text.format(a=options.get("a", "A"), b=options.get("b", "B"))
    return "Processed"
