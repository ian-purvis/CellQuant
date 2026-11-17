"""The unit a data table's values are in, and how a results sheet states it.

A table carries its unit in its title (an import names it, for example ``Area in µm² per object`` or ``AF647 mean intensity (a.u.)``), the
same text the graph's value axis shows. ``unit_of`` reads it back. A results sheet then says it once in a "Unit" row of the "Table analyzed"
section, and writes it after the values that are in that unit (means, medians, SDs, SEMs, differences and their confidence limits). P values,
t, F, df, ratios, correlations and other unitless statistics are left as they are.
"""

from __future__ import annotations

import re
from dataclasses import replace

import pandas as pd

from cellquant.stats.results import AnalysisResult, Row, Section

_AFTER = r"(?: per [a-z ]+)?$"
_PATTERNS = (
    re.compile(r" in (µm²|µm³|µm|px²|px)" + _AFTER),
    re.compile(r"\((a\.u\.)\)" + _AFTER),
)

# Labels of results rows whose value is in the table's unit.
_IN_UNIT = re.compile(
    r"^(?:Mean of (?!differences, log)|Median of |Difference between means|Difference: |Mean difference|SD of differences|SEM of differences"
    r"|Hodges-Lehmann pseudo-median|[\d.]+% (?:confidence interval|CI of difference|CI)$)"
)
# Packed rows: "n = 8; mean = 50.99; SD = 3.747; SEM = 1.325" and "Mean Diff. 10.71; 95% CI 6.577 to 14.85; Adjusted P = …".
_PACKED = re.compile(r"\b(mean|median|SD|SEM) = (-?[\d.]+(?:e[-+]?\d+)?)")
_MEAN_DIFF = re.compile(r"^(Mean Diff\. )([^;\s]+)(?:; ([\d.]+% CI )([^;]+))?")
# Statistics of the column statistics table that are in the table's unit.
_WIDE_IN_UNIT = re.compile(r"^(?:Minimum|Maximum|Median|Mean|Range|Sum|Geometric mean|Harmonic mean|Quadratic mean|\d+% Percentile|Std\. Deviation"
                           r"|Std\. Error of Mean|(?:Lower|Upper) [\d.]+% CI of (?:geo\. )?mean)$")


def unit_of(title: str) -> str:
    """The unit named in a table title (``µm²``, ``a.u.``…), or "" when it names none. A percent measure is in ``%``."""

    text = str(title or "")
    for pattern in _PATTERNS:
        found = pattern.search(text)
        if found:
            return found.group(1)
    return "%" if text.startswith("% ") else ""


def unit_row_text(unit: str) -> str:
    return f"{unit} (for means, medians, SDs, SEMs, differences and their CIs)"


def with_unit(result: AnalysisResult, unit: str) -> AnalysisResult:
    """A copy of ``result`` that states the unit: a "Unit" row under the table's name, and the unit after each value that is in it."""

    if not unit:
        return result
    sections = []
    for section in result.sections:
        rows = []
        for row in section.rows:
            rows.append(row)
            if section.title == "Table analyzed" and row.label == "Table":
                rows.append(Row("Unit", unit, unit_row_text(unit)))
        sections.append(Section(section.title, [_tagged(section.title, row, unit) for row in rows]))
    return replace(result, sections=sections)


def _tagged(section: str, row: Row, unit: str) -> Row:
    text = row.formatted
    if not text or row.label == "Unit":
        return row
    if _IN_UNIT.match(row.label) and not row.label.startswith("Mean of ratio"):
        if " ± " in text:
            value, error = text.split(" ± ", 1)
            return replace(row, formatted=f"{value} ± {error} {unit}")
        return replace(row, formatted=f"{text} {unit}")
    if section == "Group summary" and _PACKED.search(text):
        return replace(row, formatted=_PACKED.sub(lambda m: f"{m.group(1)} = {m.group(2)} {unit}", text))
    if section.startswith("Multiple comparisons: ") and text.startswith("Mean Diff. "):
        found = _MEAN_DIFF.match(text)
        if found:
            head = f"{found.group(1)}{found.group(2)} {unit}"
            if found.group(3):
                head += f"; {found.group(3)}{found.group(4)} {unit}"
            return replace(row, formatted=head + text[found.end():])
    return row


def wide_with_unit(frame: pd.DataFrame, unit: str) -> pd.DataFrame:
    """The column statistics table with the unit in the label of each statistic that is in it."""

    if not unit or frame is None or frame.empty or not isinstance(frame.index, pd.MultiIndex):
        return frame
    index = [(section, f"{label} ({unit})" if section in ("Column statistics", "Geometric and other means", "Sum") and _WIDE_IN_UNIT.match(str(label)) else label)
             for section, label in frame.index]
    out = frame.copy()
    out.index = pd.MultiIndex.from_tuples(index, names=frame.index.names)
    return out
