"""The result of one analysis, laid out like a results sheet.

A result has sections (a title and rows of label, value and display text), warnings, and a
one-line methods sentence. ``values`` keeps the raw numbers under stable keys (for example
``difference``, ``ci_low``, ``ci_high``, ``p``) for plots and for code that reads results.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class Row:
    label: str
    value: Any
    formatted: str


@dataclass
class Section:
    title: str
    rows: list[Row] = field(default_factory=list)

    def add(self, label: str, value: Any, formatted: str | None = None) -> "Section":
        self.rows.append(Row(label, value, fmt(value) if formatted is None else formatted))
        return self


@dataclass
class AnalysisResult:
    analysis: str
    table_title: str = ""
    options: dict[str, Any] = field(default_factory=dict)
    sections: list[Section] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    methods: str = ""
    values: dict[str, Any] = field(default_factory=dict)

    def section(self, title: str) -> Section:
        """The section with this title, added at the end when it is not there yet."""

        for section in self.sections:
            if section.title == title:
                return section
        section = Section(title)
        self.sections.append(section)
        return section

    def row(self, section: str, label: str) -> Row:
        for item in self.sections:
            if item.title == section:
                for row in item.rows:
                    if row.label == label:
                        return row
        raise KeyError(f"No row {label!r} in section {section!r}.")

    def to_frame(self) -> pd.DataFrame:
        """One row per result line (section, label, value, formatted), then warnings and methods."""

        records = [
            {"section": section.title, "label": row.label, "value": _plain(row.value), "formatted": row.formatted}
            for section in self.sections
            for row in section.rows
        ]
        records += [{"section": "Warnings", "label": "", "value": None, "formatted": text} for text in self.warnings]
        if self.methods:
            records.append({"section": "Methods", "label": "", "value": None, "formatted": self.methods})
        return pd.DataFrame.from_records(records, columns=["section", "label", "value", "formatted"])


def fmt(value: Any, digits: int = 4) -> str:
    """Display text: ``digits`` significant digits for floats, blank for missing values."""

    if value is None:
        return ""
    if isinstance(value, (bool, np.bool_)):
        return "Yes" if value else "No"
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, (float, np.floating)):
        if not math.isfinite(value):
            return "" if math.isnan(value) else ("∞" if value > 0 else "-∞")
        return f"{float(value):.{digits}g}"
    return str(value)


def fmt_ci(low: float, high: float, digits: int = 4) -> str:
    if not (math.isfinite(low) and math.isfinite(high)):
        return ""
    return f"{fmt(low, digits)} to {fmt(high, digits)}"


def _plain(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, tuple):
        return "; ".join(fmt(item) for item in value)
    return value
