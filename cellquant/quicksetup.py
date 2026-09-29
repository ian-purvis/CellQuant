"""Quick marker setup (step 3), without any interface code.

``marker_recipe`` turns a list of marker channels into measurements,
positive/negative calls and results rows. ``starting_threshold`` proposes a
first cutoff for the user to check.
"""

from __future__ import annotations

import re

import numpy as np


RULE_MEAN = "mean"
RULE_PERCENT = "percent_above"
DEFAULT_MIN_PERCENT = 50.0


def marker_recipe(
    recipe_data: dict,
    chosen: list[tuple[int, str]],
    rule: str = RULE_MEAN,
    min_percent: float = DEFAULT_MIN_PERCENT,
) -> dict:
    """Recipe with one measurement, one classification and one report per marker.

    ``rule="mean"``: a cell is positive when its mean brightness is above a cutoff.
    Cutoffs start at 0 and are replaced with a starting guess once the image is measured.

    ``rule="percent_above"``: a cell is positive when at least ``min_percent`` % of its
    pixels are at or above a pixel level. Each marker gets the mean (``<slug>_mean``,
    kept for reference and used for the starting pixel level) and the percent
    (``<slug>_pct``); the call uses the percent. Pixel levels start at 0 and are
    replaced with a starting guess once the image is measured.

    Pairs of markers also get a double-positive report.
    """

    if rule not in (RULE_MEAN, RULE_PERCENT):
        raise ValueError(f"Unknown marker rule: {rule}")

    data = dict(recipe_data)
    measurements, classifications, reports = [], [], []
    used: set[str] = set()
    for channel_index, name in chosen:
        slug = re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_") or f"channel_{channel_index}"
        base = slug
        counter = 2
        while slug in used:
            slug = f"{base}_{counter}"
            counter += 1
        used.add(slug)
        measurements.append(
            {"id": f"{slug}_mean", "name": f"{name} mean", "channel": channel_index, "region": {"type": "object"}, "statistic": "mean"}
        )
        if rule == RULE_PERCENT:
            measurements.append(
                {
                    "id": f"{slug}_pct",
                    "name": f"{name} % of pixels at or above level",
                    "channel": channel_index,
                    "region": {"type": "object"},
                    "statistic": "percent_above",
                    "pixel_level": 0.0,
                }
            )
            classifications.append(
                {
                    "id": f"{slug}_pos",
                    "name": name,
                    "measurement": f"{slug}_pct",
                    "threshold": float(min_percent),
                    "comparison": "at_least",
                }
            )
        else:
            classifications.append({"id": f"{slug}_pos", "name": name, "measurement": f"{slug}_mean", "threshold": 0.0})
        reports.append({"numerator": f"{slug}_pos", "denominator": "all_objects"})
    ids = [item["id"] for item in classifications]
    for first in range(len(ids)):
        for second in range(first + 1, len(ids)):
            reports.append({"numerator": f"{ids[first]} AND {ids[second]}", "denominator": "all_objects"})
    data["measurements"] = measurements
    data["classifications"] = classifications
    data["reports"] = reports
    return data


def uses_pixel_level(recipe, classification) -> bool:
    """True when a classification counts pixels at or above a level (percent_above)."""

    measurement = next((item for item in recipe.measurements if item.id == classification.measurement), None)
    return measurement is not None and measurement.statistic == "percent_above"


def describe_rule(recipe, classification) -> str:
    """The positive rule in words, e.g. 'at least 50% of pixels ≥ 1200' or 'mean > 431'."""

    measurement = next((item for item in recipe.measurements if item.id == classification.measurement), None)
    sign = "≥" if classification.comparison == "at_least" else ">"
    if measurement is not None and measurement.statistic == "percent_above":
        band = f"≥ {measurement.pixel_level:g}"
        if measurement.pixel_level_high is not None:
            band = f"{measurement.pixel_level:g}–{measurement.pixel_level_high:g}"
        amount = "at least" if classification.comparison == "at_least" else "more than"
        return f"{amount} {classification.threshold:g}% of pixels {band}"
    statistic = measurement.statistic if measurement is not None else classification.measurement
    return f"{statistic} {sign} {classification.threshold:g}"


def starting_threshold(values: np.ndarray) -> float:
    """A first guess at the cutoff, for the user to check in step 4.

    Otsu's criterion (the split into two groups with the largest between-group
    variance) evaluated at every gap between the sorted object values, with the
    cutoff placed halfway across the chosen gap. The histogram version of Otsu
    returns a bin centre, which can fall just below the brightest dim object.
    """

    finite = np.sort(np.asarray(values, dtype=float)[np.isfinite(np.asarray(values, dtype=float))])
    if finite.size == 0:
        return 0.0
    if finite[0] == finite[-1]:
        return float(finite[0])
    count = finite.size
    cumulative = np.cumsum(finite)
    total = cumulative[-1]
    below = np.arange(1, count)  # objects below each candidate split
    mean_low = cumulative[:-1] / below
    mean_high = (total - cumulative[:-1]) / (count - below)
    between = (below / count) * (1 - below / count) * (mean_low - mean_high) ** 2
    between[finite[1:] == finite[:-1]] = -1  # no split between equal values
    split = int(np.argmax(between)) + 1
    return float((finite[split - 1] + finite[split]) / 2)
