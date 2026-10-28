"""Replicate tables: every value of each column (for example every cell) with the biological replicate it came from.

One replicate name per row, shared by every column, so a block of rows is one replicate: rows 1-50 hold the
first replicate's cells of each condition, rows 51-90 the second's, and so on. A blank name continues the
replicate above it. The analyses use one value per replicate and column (its mean or median, N); the
SuperPlot shows both levels. Rows above the first name belong to no replicate and are left out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from cellquant.errors import StatisticsError
from cellquant.stats.tables import Column, ColumnTable, _as_float

SUMMARIES = {"mean": "Mean", "median": "Median"}


@dataclass
class ReplicateTable:
    title: str = ""
    names: list[str] = field(default_factory=list)
    values: list[np.ndarray] = field(default_factory=list)  # one array per column, row by row (NaN = blank)
    labels: list[str] = field(default_factory=list)         # one replicate name per row, as typed ("" continues the one above)
    summary: str = "mean"                                    # each replicate's value: "mean" or "median"
    paired: bool = False                                     # each replicate measured in every column

    def __post_init__(self) -> None:
        self.names = [str(name) for name in self.names]
        if len(self.names) != len(self.values):
            raise StatisticsError("A replicate table needs one list of values per column.")
        repeated = sorted({name for name in self.names if self.names.count(name) > 1})
        if repeated:
            raise StatisticsError(f"Column names must be unique; repeated: {', '.join(repeated)}.")
        self.values = [_as_float(column, name) for name, column in zip(self.names, self.values)]
        self.labels = ["" if label is None or (isinstance(label, float) and label != label) else str(label).strip() for label in self.labels]
        if self.summary not in SUMMARIES:
            self.summary = "mean"

    @classmethod
    def from_long(cls, frame: pd.DataFrame, value: str, group: str, replicate: str, title: str = "") -> "ReplicateTable":
        """From a long table (one row per value, with group and replicate columns): one block of rows per replicate."""

        for name in (value, group, replicate):
            if name not in frame.columns:
                raise StatisticsError(f"The table has no column {name!r}.")
        frame = frame.loc[frame[group].notna() & frame[replicate].notna()]
        numbers = pd.to_numeric(frame[value], errors="coerce")
        groups = [str(name) for name in pd.unique(frame[group].astype(str))]
        replicates = [str(name) for name in pd.unique(frame[replicate].astype(str))]
        if not groups or not replicates:
            raise StatisticsError("No rows with a group and a replicate.")
        columns: list[list[float]] = [[] for _ in groups]
        labels: list[str] = []
        for name in replicates:
            rows = frame[replicate].astype(str) == name
            block = [numbers[rows & (frame[group].astype(str) == g)].to_list() for g in groups]
            depth = max(len(item) for item in block)
            for index, items in enumerate(block):
                columns[index].extend(items + [np.nan] * (depth - len(items)))
            labels.extend([name] + [""] * (depth - 1))
        return cls(title or value, groups, [np.asarray(column, float) for column in columns], labels)

    @classmethod
    def from_wide(cls, frame: pd.DataFrame, replicate: str, title: str = "") -> "ReplicateTable":
        """From a wide table: one column per group and a column naming each row's replicate."""

        if replicate not in frame.columns:
            raise StatisticsError(f"The table has no column {replicate!r}.")
        columns = {str(name): pd.to_numeric(frame[name], errors="coerce").to_numpy(float) for name in frame.columns
                   if name != replicate and pd.to_numeric(frame[name], errors="coerce").notna().any()}
        if not columns:
            raise StatisticsError("The file has no column of numbers.")
        labels = ["" if pd.isna(label) else str(label) for label in frame[replicate]]
        return cls(title, list(columns), list(columns.values()), labels)

    # --- Rows and replicates ---------------------------------------------------------------------------------------

    @property
    def rows(self) -> int:
        return max([column.size for column in self.values] + [len(self.labels)] + [0])

    def row_replicates(self) -> list[str]:
        """Each row's replicate, a blank name continuing the one above ("" above the first name)."""

        out, current = [], ""
        for row in range(self.rows):
            label = self.labels[row] if row < len(self.labels) else ""
            current = label or current
            out.append(current)
        return out

    def replicates(self) -> list[str]:
        """The replicates in order of first appearance."""

        return [name for name in dict.fromkeys(self.row_replicates()) if name]

    def long(self) -> pd.DataFrame:
        """``group``, ``unit`` (replicate), ``row`` (1-based) and ``value`` of every value that has a replicate."""

        owners = self.row_replicates()
        records = [{"group": name, "unit": owners[row], "row": row + 1, "value": float(number)}
                   for name, column in zip(self.names, self.values) for row, number in enumerate(column)
                   if np.isfinite(number) and owners[row]]
        return pd.DataFrame.from_records(records, columns=["group", "unit", "row", "value"])

    def unassigned(self) -> int:
        """Values above the first replicate name: they belong to no replicate and are left out."""

        owners = self.row_replicates()
        return int(sum(np.isfinite(number) and not owners[row] for column in self.values for row, number in enumerate(column)))

    def summaries(self) -> pd.DataFrame:
        """``group``, ``dot`` (replicate), ``value`` (its mean or median) and ``n``, for each replicate with values in a column."""

        long = self.long()
        if long.empty:
            return pd.DataFrame(columns=["group", "dot", "value", "n"])
        reduce = "median" if self.summary == "median" else "mean"
        frame = long.groupby(["group", "unit"], sort=False)["value"].agg([reduce, "size"]).reset_index()
        frame.columns = ["group", "dot", "value", "n"]
        order = {name: index for index, name in enumerate(self.names)}
        replicates = {name: index for index, name in enumerate(self.replicates())}
        frame = frame.assign(_g=frame["group"].map(order), _r=frame["dot"].map(replicates)).sort_values(["_g", "_r"])
        return frame.drop(columns=["_g", "_r"]).reset_index(drop=True)

    def summary_table(self) -> ColumnTable:
        """What the analyses use: one row per replicate (in order), one value per column (blank where it has none)."""

        replicates = self.replicates()
        frame = self.summaries()
        cells = {(row.group, row.dot): row.value for row in frame.itertuples()}
        columns = [Column(name, np.array([cells.get((name, unit), np.nan) for unit in replicates], float)) for name in self.names]
        return ColumnTable(title=self.title, columns=columns, paired=self.paired)

    def counts(self) -> dict[str, int]:
        """n: the values of each column that belong to a replicate."""

        long = self.long()
        return {name: int((long["group"] == name).sum()) for name in self.names}


def looks_like_replicate(name: str) -> bool:
    """A column name that probably names the biological replicate (offered first when importing)."""

    return bool(re.search(r"replicate|experiment|animal|subject|donor|batch|biological|mouse|explant|litter|culture|\brep\b|\bday\b",
                          str(name), re.IGNORECASE))


__all__ = ["SUMMARIES", "ReplicateTable", "looks_like_replicate"]
