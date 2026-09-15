"""Compare saved and proposed marker calls over an explicit population."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


_CALL_CATEGORIES = ("positive", "negative", "uncertain", "missing")


@dataclass(frozen=True)
class MarkerCallComparison:
    marker: str
    population_label: str
    baseline_available: bool
    comparable: bool
    message: str
    saved_counts: dict[str, int]
    proposed_counts: dict[str, int]
    net_positive_change: int | None
    changed_object_count: int | None
    denominator: int | None

    def summary_lines(self) -> list[str]:
        lines = [f"Population: {self.population_label}"]
        if not self.baseline_available:
            lines.append("Baseline calls unavailable.")
            return lines
        if not self.comparable:
            lines.append(self.message or "Results are not a direct cell-by-cell comparison.")
            return lines
        for category in _CALL_CATEGORIES:
            saved = self.saved_counts.get(category, 0)
            proposed = self.proposed_counts.get(category, 0)
            delta = proposed - saved
            sign = "+" if delta > 0 else ""
            lines.append(f"{category}: {saved} → {proposed} ({sign}{delta})")
        pos_saved = self.saved_counts.get("positive", 0)
        pos_proposed = self.proposed_counts.get("positive", 0)
        net = self.net_positive_change if self.net_positive_change is not None else 0
        changed = self.changed_object_count if self.changed_object_count is not None else 0
        sign = "+" if net > 0 else ""
        lines.append(
            f"{self.marker}-positive: {pos_saved} → {pos_proposed}; "
            f"net {sign}{net}; {changed} cells changed call."
        )
        if self.denominator is not None:
            lines.append(f"Aligned eligible objects: {self.denominator}")
        return lines


def _counts(series: pd.Series) -> dict[str, int]:
    counts = {name: 0 for name in _CALL_CATEGORIES}
    for value, count in series.value_counts(dropna=False).items():
        key = str(value) if value in _CALL_CATEGORIES else "missing"
        if value in _CALL_CATEGORIES:
            counts[key] = int(count)
        else:
            counts["missing"] += int(count)
    return counts


def compare_marker_calls(
    baseline_df: pd.DataFrame | None,
    proposed_df: pd.DataFrame | None,
    marker: str,
    *,
    population_label: str,
) -> MarkerCallComparison:
    """Compare Saved vs Proposed calls for one marker over aligned object IDs."""
    empty_counts = {name: 0 for name in _CALL_CATEGORIES}
    if baseline_df is None:
        return MarkerCallComparison(
            marker=marker, population_label=population_label,
            baseline_available=False, comparable=False,
            message="Baseline calls unavailable.",
            saved_counts=empty_counts,
            proposed_counts=_counts(proposed_df.loc[proposed_df["marker"] == marker, "call"])
            if proposed_df is not None and "marker" in proposed_df.columns else empty_counts,
            net_positive_change=None, changed_object_count=None, denominator=None,
        )
    if proposed_df is None:
        return MarkerCallComparison(
            marker=marker, population_label=population_label,
            baseline_available=True, comparable=False,
            message="Proposed calls unavailable. Update preview.",
            saved_counts=_counts(baseline_df.loc[baseline_df["marker"] == marker, "call"])
            if "marker" in baseline_df.columns else empty_counts,
            proposed_counts=empty_counts,
            net_positive_change=None, changed_object_count=None, denominator=None,
        )
    if "marker" not in baseline_df.columns or "marker" not in proposed_df.columns:
        raise ValueError("calls tables must include a marker column")
    saved = baseline_df.loc[baseline_df["marker"] == marker, ["label", "call"]].copy()
    proposed = proposed_df.loc[proposed_df["marker"] == marker, ["label", "call"]].copy()
    saved_ids = set(int(v) for v in saved["label"].tolist())
    proposed_ids = set(int(v) for v in proposed["label"].tolist())
    if saved_ids != proposed_ids:
        return MarkerCallComparison(
            marker=marker, population_label=population_label,
            baseline_available=True, comparable=False,
            message=(
                "Saved and proposed eligible object sets differ; "
                "results are not a direct cell-by-cell comparison."
            ),
            saved_counts=_counts(saved["call"]),
            proposed_counts=_counts(proposed["call"]),
            net_positive_change=None, changed_object_count=None, denominator=None,
        )
    merged = saved.rename(columns={"call": "saved"}).merge(
        proposed.rename(columns={"call": "proposed"}), on="label", how="inner"
    )
    changed = int((merged["saved"].astype(str) != merged["proposed"].astype(str)).sum())
    saved_counts = _counts(merged["saved"])
    proposed_counts = _counts(merged["proposed"])
    net = proposed_counts["positive"] - saved_counts["positive"]
    return MarkerCallComparison(
        marker=marker, population_label=population_label,
        baseline_available=True, comparable=True, message="",
        saved_counts=saved_counts, proposed_counts=proposed_counts,
        net_positive_change=net, changed_object_count=changed,
        denominator=len(merged),
    )
