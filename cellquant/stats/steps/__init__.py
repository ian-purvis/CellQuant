"""Show working: the intermediate quantities of an analysis, recomputed step by step from the data.

Each step is computed here again from its formula, independently of the analysis code, and where the analysis
reports the same quantity its value is kept beside it (``engine``), so a step that disagrees is visible. A user can
follow the steps with a calculator. ``working(kind, source, options, result)`` returns the blocks for one results
sheet: ``kind`` is the sheet's kind ("compare", "anova", …), ``source`` the table it analyzed (a ColumnTable,
GroupedTable, ReplicateTable, XYTable or ContingencyTable), ``options`` the sheet's options and ``result`` the
AnalysisResult it shows. Kinds without working give an empty list.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

from cellquant.stats.results import fmt


@dataclass
class Step:
    label: str          # what it is, e.g. "Pooled variance s_p²"
    formula: str        # how it is computed, in the notation of the statistics reference, with the numbers filled in where short
    value: Any          # the value computed here
    engine: Any = None  # the analysis's own value of the same quantity, when it reports one

    @property
    def checked(self) -> bool | None:
        """True or False when there is an engine value to compare with, else None."""

        if self.engine is None:
            return None
        return agrees(self.value, self.engine)


@dataclass
class Block:
    title: str
    steps: list[Step] = field(default_factory=list)
    table: pd.DataFrame | None = None  # per-value detail: ranks, deviations, the at-risk table…
    note: str = ""

    def add(self, label: str, formula: str, value: Any, engine: Any = None) -> "Block":
        self.steps.append(Step(label, formula, value, engine))
        return self


def agrees(value: Any, engine: Any, rel: float = 1e-6, abs_: float = 1e-9) -> bool:
    try:
        a, b = float(value), float(engine)
    except (TypeError, ValueError):
        return str(value) == str(engine)
    if math.isnan(a) or math.isnan(b):
        return math.isnan(a) and math.isnan(b)
    if math.isinf(a) or math.isinf(b):
        return a == b
    return abs(a - b) <= max(abs_, rel * max(abs(a), abs(b)))


Builder = Callable[[Any, dict, Any], list[Block]]
BUILDERS: dict[str, Builder] = {}


def register(*kinds: str):
    def wrap(function: Builder) -> Builder:
        for kind in kinds:
            BUILDERS[kind] = function
        return function

    return wrap


def working(kind: str, source: Any, options: dict, result: Any) -> list[Block]:
    _load()
    builder = BUILDERS.get(kind)
    if builder is None or source is None:
        return []
    return builder(source, options or {}, result)


def has_working(kind: str) -> bool:
    _load()
    return kind in BUILDERS


def to_frame(blocks: list[Block]) -> pd.DataFrame:
    """One row per step (block, quantity, formula, value, engine value, agrees), then each block's table below its steps."""

    records = []
    for block in blocks:
        for step in block.steps:
            records.append({"block": block.title, "quantity": step.label, "formula": step.formula, "value": _plain(step.value),
                            "engine": _plain(step.engine), "agrees": step.checked})
        if block.table is not None:
            for _, row in block.table.iterrows():
                records.append({"block": block.title, "quantity": "; ".join(f"{key} = {_text(value)}" for key, value in row.items()),
                                "formula": "", "value": None, "engine": None, "agrees": None})
    return pd.DataFrame.from_records(records, columns=["block", "quantity", "formula", "value", "engine", "agrees"])


def _plain(value: Any) -> Any:
    return value.item() if isinstance(value, np.generic) else value


def _text(value: Any) -> str:
    return fmt(value, 6) if isinstance(value, (float, np.floating)) else str(value)


def _load() -> None:
    # The builders register themselves when their module is imported.
    from cellquant.stats.steps import columns, models, tables  # noqa: F401
