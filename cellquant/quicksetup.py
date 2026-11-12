"""Quick marker setup (step 4), without any interface code.

``marker_recipe`` turns a list of marker channels into measurements,
positive/negative calls and results rows. ``starting_threshold`` proposes a
first cutoff for the user to check.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

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
        slug = _unused_slug(name, channel_index, used)
        used.add(slug)
        found, call = _marker_entries(channel_index, name, slug, rule, min_percent)
        measurements.extend(found)
        classifications.append(call)
        reports.append({"numerator": f"{slug}_pos", "denominator": "all_objects"})
    ids = [item["id"] for item in classifications]
    for first in range(len(ids)):
        for second in range(first + 1, len(ids)):
            reports.append({"numerator": f"{ids[first]} AND {ids[second]}", "denominator": "all_objects"})
    data["measurements"] = measurements
    data["classifications"] = classifications
    data["reports"] = reports
    return data


def channel_slug(name: str, channel_index: int) -> str:
    """The start of the ids quick setup makes for a marker channel: ``AF 594`` gives ``af_594``
    (then ``af_594_mean``, ``af_594_pos``)."""

    return re.sub(r"[^a-z0-9]+", "_", str(name).casefold()).strip("_") or f"channel_{channel_index}"


def _unused_slug(name: str, channel_index: int, used: set[str]) -> str:
    slug = channel_slug(name, channel_index)
    base = slug
    counter = 2
    while slug in used:
        slug = f"{base}_{counter}"
        counter += 1
    return slug


def _marker_entries(channel_index: int, name: str, slug: str, rule: str, min_percent: float) -> tuple[list[dict], dict]:
    """The measurements and the positive/negative call of one marker channel (see ``marker_recipe``)."""

    measurements = [
        {"id": f"{slug}_mean", "name": f"{name} mean", "channel": channel_index, "region": {"type": "object"}, "statistic": "mean"}
    ]
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
        call = {
            "id": f"{slug}_pos",
            "name": name,
            "measurement": f"{slug}_pct",
            "threshold": float(min_percent),
            "comparison": "at_least",
        }
    else:
        call = {"id": f"{slug}_pos", "name": name, "measurement": f"{slug}_mean", "threshold": 0.0}
    return measurements, call


@dataclass
class MarkerMerge:
    """What changing the ticked marker channels does to a recipe that already has markers."""

    data: dict
    added: list[str] = field(default_factory=list)  # names of markers measured now
    removed: list[str] = field(default_factory=list)  # names of markers taken away
    reset: list[str] = field(default_factory=list)  # names whose rule changed: their cutoffs start again
    new_ids: list[str] = field(default_factory=list)  # classification ids that need a starting cutoff
    calls_lost: dict[str, int] = field(default_factory=dict)  # removed marker -> objects called by hand
    own_cutoffs_lost: dict[str, int] = field(default_factory=dict)  # reset marker -> images with their own cutoff
    reports_lost: list[str] = field(default_factory=list)  # result rows made by hand that use a removed marker
    reports_dropped: int = 0  # result rows (made by quick setup or by hand) removed with their marker

    @property
    def changes(self) -> bool:
        return bool(self.added or self.removed or self.reset)

    @property
    def loses_something(self) -> bool:
        """Something the user made would be lost: calls by hand, own cutoffs, a cutoff set (rule change), their result rows."""

        return bool(self.calls_lost or self.reports_lost or self.reset or self.own_cutoffs_lost)


def _uses_marker(expression: str, tokens: set[str]) -> bool:
    return any(re.search(rf"(?<![^\s()]){re.escape(token)}(?![^\s()])", expression) for token in tokens if token)


def _words(expression: str, names: dict[str, str]) -> str:
    """'a_pos AND NOT b_pos' as 'A+ and B-'; the all-objects denominators as 'all objects'."""

    text = str(expression).strip()
    if text.casefold() in {"all_objects", "all_measured_objects"}:
        return "all objects"
    words = []
    for part in re.split(r"\s+AND\s+", text, flags=re.IGNORECASE):
        negated = part.casefold().startswith("not ")
        token = part.split(None, 1)[1].strip() if negated else part.strip()
        words.append(f"{names.get(token, token)}{'-' if negated else '+'}")
    return " and ".join(words)


def _is_quick_report(report: dict, ids: set[str]) -> bool:
    """A result row quick setup makes: one marker (or two joined by AND) among all objects."""

    if str(report.get("denominator", "")).strip().casefold() not in {"all_objects", "all_measured_objects"}:
        return False
    parts = [part.strip() for part in re.split(r"\s+AND\s+", str(report.get("numerator", "")).strip(), flags=re.IGNORECASE)]
    return 1 <= len(parts) <= 2 and all(part in ids for part in parts)


def merge_marker_recipe(
    recipe_data: dict,
    chosen: list[tuple[int, str]],
    shown: set[int],
    rule: str = RULE_MEAN,
    min_percent: float = DEFAULT_MIN_PERCENT,
    *,
    reset_rule: bool = False,
) -> MarkerMerge:
    """Change the markers of a recipe that has some, to the ``chosen`` channels (``shown``: every channel
    that can be ticked, so a marker on any other channel is left alone).

    A marker on a channel that stays ticked is kept as it is: cutoffs, cutoffs of single images, calls by
    hand. Only ``reset_rule`` (the user picked another rule) rebuilds the kept markers that use a different
    one; their cutoffs start again and their calls by hand stay. A newly ticked channel gets a marker, and a
    result row for it and for each pair; an unticked channel loses its marker, its measurements and the
    result rows that use it. Result rows that use no removed marker stay.
    """

    if rule not in (RULE_MEAN, RULE_PERCENT):
        raise ValueError(f"Unknown marker rule: {rule}")
    data = copy.deepcopy(dict(recipe_data))
    measurements: list[dict] = data.get("measurements", [])
    classifications: list[dict] = data.get("classifications", [])
    reports: list[dict] = data.get("reports", [])
    result = MarkerMerge(data=data)
    by_id = {item["id"]: item for item in measurements}

    def channel_of(call: dict) -> int | None:
        spec = by_id.get(call["measurement"])
        return int(spec["channel"]) if spec is not None else None

    wanted = {int(index): name for index, name in chosen}
    existing: dict[int, list[dict]] = {}
    for call in classifications:
        channel = channel_of(call)
        if channel is not None:
            existing.setdefault(channel, []).append(call)

    def drop_measurements(call: dict, others: list[dict]) -> None:
        """Remove the measurements this marker made (its own and the mean quick setup keeps beside a percent)
        unless a marker that stays uses them."""

        prefix = re.sub(r"_(mean|pct)$", "", call["measurement"])
        used = {item["measurement"] for item in others}
        for suffix in ("_mean", "_pct"):
            for spec in [m for m in measurements if m["id"] == prefix + suffix and m["id"] not in used]:
                measurements.remove(spec)
        if call["measurement"] not in used:
            measurements[:] = [m for m in measurements if m["id"] != call["measurement"]]

    all_names = {call["id"]: call["name"] for call in classifications}
    # Unticked channels: the marker goes, with its measurements and result rows.
    gone = [call for channel in sorted(shown - set(wanted)) for call in existing.get(channel, [])]
    for call in gone:
        result.removed.append(call["name"])
        calls = sum(len(per_image) for per_image in (call.get("cell_calls") or {}).values())
        if calls:
            result.calls_lost[call["name"]] = calls
    if gone:
        remaining = [call for call in classifications if call not in gone]
        for call in gone:
            drop_measurements(call, remaining)
        classifications[:] = remaining
        names = {call["id"] for call in gone} | {call["name"] for call in gone}
        every_id = {call["id"] for call in remaining} | {call["id"] for call in gone}
        kept_reports = []
        for report in reports:
            if _uses_marker(f"{report.get('numerator', '')} {report.get('denominator', '')}", names):
                result.reports_dropped += 1
                if not _is_quick_report(report, every_id):
                    result.reports_lost.append(
                        f"{_words(report.get('numerator', ''), all_names)} among {_words(report.get('denominator', ''), all_names)}"
                    )
            else:
                kept_reports.append(report)
        reports[:] = kept_reports

    # Ticked channels that already have a marker: kept, unless the user changed the rule.
    for channel in sorted(set(wanted) & set(existing)):
        primary = existing[channel][0]
        statistic = by_id.get(primary["measurement"], {}).get("statistic")
        current = RULE_PERCENT if statistic == "percent_above" else RULE_MEAN
        if not reset_rule or current == rule:
            continue
        others = [call for call in classifications if call is not primary]
        drop_measurements(primary, others)
        slug = re.sub(r"_(mean|pct)$", "", primary["measurement"])
        fresh, call = _marker_entries(channel, primary["name"], slug, rule, min_percent)
        taken = {m["id"] for m in measurements}
        measurements.extend(m for m in fresh if m["id"] not in taken)
        for key in ("id", "name"):
            call[key] = primary[key]
        for key in ("auto_method", "auto_percent", "cutoff_by", "cell_calls", "positive_label", "negative_label"):
            if key in primary:
                call[key] = primary[key]
        own = len(primary.get("image_thresholds") or {})
        if own:
            result.own_cutoffs_lost[primary["name"]] = own
        classifications[classifications.index(primary)] = call
        result.reset.append(primary["name"])
        result.new_ids.append(call["id"])

    # Newly ticked channels: a marker, a result row for it, and one per pair with every other marker.
    taken_slugs = {re.sub(r"_(pos|mean|pct)$", "", item["id"]) for item in [*measurements, *classifications]}
    for channel, name in chosen:
        if int(channel) in existing:
            continue
        slug = _unused_slug(name, int(channel), taken_slugs)
        taken_slugs.add(slug)
        fresh, call = _marker_entries(int(channel), name, slug, rule, min_percent)
        measurements.extend(fresh)
        classifications.append(call)
        reports.append({"numerator": call["id"], "denominator": "all_objects"})
        for other in classifications[:-1]:
            reports.append({"numerator": f"{other['id']} AND {call['id']}", "denominator": "all_objects"})
        result.added.append(name)
        result.new_ids.append(call["id"])
    return result


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


# Automatic cutoffs, in the order the step 5 menu lists them (recipe value, shown text, tooltip).
AUTO_METHODS = (
    ("otsu", "Otsu", "Splits the objects into a dim and a bright group as cleanly as possible. Good when both groups are clear."),
    ("triangle", "Triangle", "Finds where the bright tail leaves the main peak. Good when most objects are negative."),
    ("li", "Li", "Minimizes the difference between the two groups' brightness. Often lower than Otsu, so it keeps dimmer positives."),
    ("top_percent", "Top N %", "Calls the brightest N % of objects positive."),
)


def auto_threshold(values: np.ndarray, method: str = "otsu", percent: float = 10.0) -> float:
    """The automatic cutoff for these object values, by ``method`` (see AUTO_METHODS)."""

    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if method == "otsu" or finite.size < 2 or finite.min() == finite.max():
        return starting_threshold(finite)
    if method == "top_percent":
        return float(np.percentile(finite, 100.0 - float(percent)))
    from skimage.filters import threshold_li, threshold_triangle

    if method == "triangle":
        return float(threshold_triangle(finite, nbins=min(256, max(16, finite.size))))
    if method == "li":
        return float(threshold_li(finite))
    raise ValueError(f"Unknown automatic cutoff method: {method}")


def starting_threshold(values: np.ndarray) -> float:
    """A first guess at the cutoff, for the user to check in step 5.

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
