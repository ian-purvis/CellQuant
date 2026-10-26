"""Column tables: one column of numbers per group, like a "column" data table.

A blank cell is NaN. Columns may have different lengths. In a paired table, row i of every column
belongs to the same subject (the same animal, image or experiment), so columns are compared row
by row and a row with a blank in either column is left out of that comparison.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

from cellquant.errors import StatisticsError


@dataclass
class Column:
    name: str
    values: np.ndarray

    def __post_init__(self) -> None:
        self.name = str(self.name)
        self.values = _as_float(self.values, self.name)

    @property
    def finite(self) -> np.ndarray:
        """The values without blanks."""

        return self.values[np.isfinite(self.values)]


@dataclass
class ColumnTable:
    title: str = ""
    columns: list[Column] = field(default_factory=list)
    paired: bool = False

    def __post_init__(self) -> None:
        names = [column.name for column in self.columns]
        repeated = sorted({name for name in names if names.count(name) > 1})
        if repeated:
            raise StatisticsError(f"Column names must be unique; repeated: {', '.join(repeated)}.")

    @classmethod
    def from_columns(cls, columns: Mapping[str, Iterable[float]], title: str = "", paired: bool = False) -> "ColumnTable":
        """A table from ``{name: values}``, in the mapping's order. None and NaN are blank cells."""

        return cls(title=title, columns=[Column(name, values) for name, values in columns.items()], paired=paired)

    @classmethod
    def from_long(
        cls,
        df: pd.DataFrame,
        value: str,
        group: str,
        order: Iterable[str] | None = None,
        subject: str | None = None,
        title: str = "",
    ) -> "ColumnTable":
        """One column per group from a long table (one row per measurement).

        Groups are in order of first appearance unless ``order`` is given (groups left out of
        ``order`` are dropped). Rows with a blank group are dropped. With ``subject``, rows are
        lined up by subject so the table is paired; a subject with two values in one group is refused.
        """

        for name in (value, group, subject):
            if name is not None and name not in df.columns:
                raise StatisticsError(f"The table has no column {name!r}.")
        frame = df.loc[df[group].notna()]
        groups = list(pd.unique(frame[group])) if order is None else list(order)
        missing = [name for name in groups if not (frame[group] == name).any()]
        if missing:
            raise StatisticsError(f"No rows for group(s): {', '.join(map(str, missing))}.")
        values = pd.Series(_as_float(frame[value].to_numpy(), value), index=frame.index)
        if subject is None:
            columns = {str(name): values[frame[group] == name].to_numpy() for name in groups}
            return cls.from_columns(columns, title=title or value)
        frame = frame.loc[frame[subject].notna()]
        if frame.duplicated([subject, group]).any():
            raise StatisticsError(f"Some {subject!r} values appear more than once in a group; a paired table needs one value per subject and group.")
        wide = pd.DataFrame({subject: frame[subject], group: frame[group], "value": values[frame.index]})
        wide = wide.pivot(index=subject, columns=group, values="value")
        subjects = list(pd.unique(frame[subject]))
        wide = wide.reindex(index=subjects, columns=groups)
        return cls.from_columns({str(name): wide[name].to_numpy() for name in groups}, title=title or value, paired=True)

    @property
    def names(self) -> list[str]:
        return [column.name for column in self.columns]

    def __len__(self) -> int:
        return len(self.columns)

    def __getitem__(self, key: int | str) -> Column:
        if isinstance(key, (int, np.integer)):
            return self.columns[int(key)]
        for column in self.columns:
            if column.name == key:
                return column
        raise KeyError(f"No column {key!r}; columns are {', '.join(self.names)}.")

    def to_long(self) -> pd.DataFrame:
        """``group``, ``row`` (1-based row in the column) and ``value``, blanks left out."""

        records = [
            {"group": column.name, "row": row + 1, "value": float(number)}
            for column in self.columns
            for row, number in enumerate(column.values)
            if np.isfinite(number)
        ]
        return pd.DataFrame.from_records(records, columns=["group", "row", "value"])


def _as_float(values: Iterable[float], name: str) -> np.ndarray:
    raw = np.asarray(list(values) if not isinstance(values, (np.ndarray, pd.Series)) else values, dtype=object).ravel()
    out = np.empty(raw.shape, dtype=float)
    for i, item in enumerate(raw):
        if item is None or (isinstance(item, str) and not item.strip()):
            out[i] = np.nan
            continue
        try:
            out[i] = float(item)
        except (TypeError, ValueError):
            raise StatisticsError(f"Column {name!r} has a value that is not a number: {item!r}.") from None
    if np.isinf(out).any():
        raise StatisticsError(f"Column {name!r} has an infinite value; enter a number or leave the cell blank.")
    return out
