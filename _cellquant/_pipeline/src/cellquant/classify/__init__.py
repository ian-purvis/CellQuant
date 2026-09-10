"""Exact nuclear marker scoring, independent of segmentation and display settings."""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from itertools import combinations, product
import hashlib
import json
from numbers import Real, Integral
from collections.abc import Mapping

import numpy as np
import pandas as pd

from cellquant.contracts import ImageVolume, LabelVolume


def default_queries_for_markers(names):
    """Inclusive positive combinations used when a recipe omits explicit queries."""

    names = list(names)
    return [
        dict(name=" & ".join(f"{n}+" for n in subset), positive=list(subset))
        for size in range(1, len(names) + 1)
        for subset in combinations(names, size)
    ]


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, Real) or not np.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def _text(value, name, *, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ValueError(f"{name} must be a {'possibly empty ' if empty else 'nonempty '}string")
    return value


def _json_safe(value, name="calibration"):
    """Copy JSON metadata without coercing keys or allowing nonfinite numbers."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        return _number(value, name)
    if isinstance(value, Mapping):
        if any(not isinstance(k, str) for k in value):
            raise ValueError(f"{name} keys must be strings")
        return {k: _json_safe(v, f"{name}.{k}") for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v, name) for v in value]
    raise ValueError(f"{name} must contain only JSON-safe values")


class ClassificationRecipe:
    """Validated recipe with a defensive JSON copy and stable content fingerprint."""

    def __init__(self, raw):
        if not isinstance(raw, Mapping):
            raise ValueError("classification recipe must be a mapping")
        version = raw.get("schema_version", 1)
        if isinstance(version, bool) or not isinstance(version, Integral) or version != 1:
            raise ValueError("schema_version must be 1")
        markers = raw.get("markers")
        if not isinstance(markers, (list, tuple)) or not 1 <= len(markers) <= 6:
            raise ValueError("recipe must contain 1–6 markers")
        canonical = dict(schema_version=1, name=_text(raw.get("name", "Nuclear coexpression"), "name"),
                         calibration_group=_text(raw.get("calibration_group", ""), "calibration_group", empty=True),
                         region_policy=raw.get("region_policy", "whole_object"), markers=[])
        if canonical["region_policy"] not in ("whole_object", "centroid"):
            raise ValueError("region_policy must be whole_object or centroid")
        expected = raw.get("expected_channel_names")
        if expected is not None:
            if not isinstance(expected, (list, tuple)) or not expected:
                raise ValueError("expected_channel_names must be a nonempty list or null")
            expected = [_text(n, "expected channel name") for n in expected]
            if len(set(expected)) != len(expected):
                raise ValueError("expected_channel_names must be unique")
        canonical["expected_channel_names"] = expected
        for marker in markers:
            if not isinstance(marker, Mapping):
                raise ValueError("marker must be a mapping")
            name = _text(marker.get("name"), "marker name")
            channel = marker.get("channel")
            if channel is not None and (isinstance(channel, bool) or not isinstance(channel, Integral) or channel < 0):
                raise ValueError("marker channel must be a nonnegative integer or null")
            low = _number(marker.get("low"), "low")
            high = None if marker.get("high") is None else _number(marker["high"], "high")
            cutoff = _number(marker.get("positive_fraction"), "positive_fraction")
            margin = _number(marker.get("uncertainty_margin", 0), "uncertainty_margin")
            if high is not None and high < low:
                raise ValueError("high must be >= low")
            if not 0 < cutoff <= 1 or not 0 <= margin <= min(cutoff, 1-cutoff):
                raise ValueError("positive_fraction or uncertainty_margin is out of range")
            if marker.get("compartment", "nucleus") != "nucleus":
                raise ValueError("only nucleus compartment is supported")
            canonical["markers"].append(dict(name=name, channel=None if channel is None else int(channel),
                low=low, high=high, positive_fraction=cutoff, uncertainty_margin=margin, compartment="nucleus"))
            if "calibration" in marker:
                if not isinstance(marker["calibration"], Mapping):
                    raise ValueError("marker calibration must be a mapping")
                canonical["markers"][-1]["calibration"] = _json_safe(marker["calibration"])
        names = [m["name"] for m in canonical["markers"]]
        if len(set(names)) != len(names):
            raise ValueError("marker names must be unique")
        queries = raw.get("queries")
        if queries is None:
            queries = default_queries_for_markers(names)
        if not isinstance(queries, (list, tuple)):
            raise ValueError("queries must be a list")
        canonical["queries"] = []
        for query in queries:
            if not isinstance(query, Mapping):
                raise ValueError("query must be a mapping")
            q = dict(name=_text(query.get("name"), "query name"))
            for key in ("positive", "negative", "denominator_positive"):
                values = query.get(key, [])
                if not isinstance(values, (list, tuple)) or any(not isinstance(n, str) for n in values):
                    raise ValueError(f"query {key} must be a list of marker names")
                if len(set(values)) != len(values) or set(values) - set(names):
                    raise ValueError(f"query {key} has duplicate or unknown marker names")
                q[key] = [n for n in names if n in values]
            if set(q["negative"]) & (set(q["positive"]) | set(q["denominator_positive"])):
                raise ValueError("query requires the same marker positive and negative")
            canonical["queries"].append(q)
        if len({q["name"] for q in canonical["queries"]}) != len(canonical["queries"]):
            raise ValueError("query names must be unique")
        self._json = json.dumps(canonical, sort_keys=True, separators=(",", ":"), allow_nan=False)

    @property
    def raw(self):
        return json.loads(self._json)

    @property
    def fingerprint(self):
        return hashlib.sha256(self._json.encode("utf-8")).hexdigest()


_THRESHOLD_DENOMINATOR = 1_000_000


def _boundaries(cutoff, margin):
    """Decimal call boundaries as exact rationals.

    Thresholds are authored as decimals, so ``cutoff + margin`` must not inherit
    binary rounding (0.2 + 0.1 stores as 0.30000000000000004). Recovering the
    intended decimal with ``limit_denominator`` keeps the endpoints exact:
    the lower endpoint belongs to *uncertainty*, the upper endpoint to *positive*.
    """
    exact_cutoff = Fraction(cutoff).limit_denominator(_THRESHOLD_DENOMINATOR)
    exact_margin = Fraction(margin).limit_denominator(_THRESHOLD_DENOMINATOR)
    return exact_cutoff - exact_margin, exact_cutoff + exact_margin


def _at_least(positives, counts, threshold):
    """Exact ``positives/counts >= threshold`` using integer arithmetic only."""
    numerator, denominator = threshold.numerator, threshold.denominator
    largest = max(int(positives.max(initial=0))*denominator, int(counts.max(initial=0))*numerator)
    dtype = object if largest > np.iinfo(np.int64).max else np.int64
    return np.asarray(positives.astype(dtype)*denominator >= counts.astype(dtype)*numerator, dtype=bool)


@dataclass(frozen=True)
class ClassificationResult:
    calls: pd.DataFrame
    queries: pd.DataFrame
    patterns: pd.DataFrame
    exclusions: pd.DataFrame
    metadata: dict


def classify_labels(image: ImageVolume, labels: LabelVolume, recipe, *, region=None, context=None, cancel=None):
    """Score complete objects on a shared ZYX grid; zero is background."""
    def checkpoint():
        if cancel is not None:
            cancel.raise_if_cancelled()

    checkpoint()
    recipe = recipe if isinstance(recipe, ClassificationRecipe) else ClassificationRecipe(recipe)
    spec = recipe.raw
    if spec["expected_channel_names"] is not None and tuple(spec["expected_channel_names"]) != tuple(image.channel_names):
        raise ValueError("image channel names differ from recipe; remap markers and review thresholds")
    data, mask = np.asarray(image.data), np.asarray(labels.data)
    if data.shape[:3] != mask.shape or tuple(image.spacing_um) != tuple(labels.spacing_um):
        raise ValueError("image and labels must have matching ZYX shape and spacing_um")
    if np.iscomplexobj(data):
        raise ValueError("classification image must contain real intensities")
    for marker in spec["markers"]:
        if marker["channel"] is not None and marker["channel"] >= data.shape[-1]:
            raise ValueError(f"marker {marker['name']} channel index is out of range")
    if context is not None and not isinstance(context, Mapping):
        raise ValueError("context must be a mapping")
    ctx = dict(context or {})
    for key in ("specimen_id", "eye_id", "section_id", "image_id", "region_id"):
        default = str(image.source.name) if key == "image_id" else "whole_image" if key == "region_id" and region is None else ""
        ctx[key] = _text(ctx.get(key, default), key, empty=True)
    if region is not None and not ctx["region_id"].strip():
        raise ValueError("an explicit region requires a nonempty context region_id")
    ctx = json.loads(json.dumps(ctx, allow_nan=False))
    roi = None if region is None else np.asarray(region)
    if roi is not None and (roi.dtype != np.bool_ or roi.shape != mask.shape):
        raise ValueError("region must be a boolean mask matching the full ZYX grid")
    positions = np.flatnonzero(mask.ravel())
    ids, groups = np.unique(mask.ravel()[positions], return_inverse=True)
    counts = np.bincount(groups, minlength=len(ids))
    eligible = np.ones(len(ids), dtype=bool)
    if roi is not None and len(ids):
        if spec["region_policy"] == "whole_object":
            eligible = np.bincount(groups, weights=roi.ravel()[positions], minlength=len(ids)) == counts
        else:
            coords = np.unravel_index(positions, mask.shape)
            centers = [np.floor(np.bincount(groups, weights=axis, minlength=len(ids))/counts + .5).astype(int) for axis in coords]
            eligible = roi[tuple(centers)]
    exclusions = pd.DataFrame({"label": ids[~eligible], "reason": "outside_region"})
    frames, states = [], []
    for marker in spec["markers"]:
        checkpoint()
        fraction = np.full(len(ids), np.nan)
        calls = np.full(len(ids), "missing", dtype=object)
        reasons = np.full(len(ids), "not_acquired", dtype=object)
        channel = marker["channel"]
        if channel is not None:
            values = data[..., channel].ravel()[positions]
            finite = np.isfinite(values)
            valid = np.bincount(groups, weights=finite, minlength=len(ids)) == counts
            within = finite & (values >= marker["low"])
            if marker["high"] is not None:
                within &= values <= marker["high"]
            positives = np.bincount(groups[within], minlength=len(ids))
            np.divide(positives, counts, out=fraction, where=valid & (counts > 0))
            # Boundaries are compared on exact counts, never on the reported float
            # fraction: the lower endpoint stays uncertain, the upper is positive.
            lower, upper = _boundaries(marker["positive_fraction"], marker["uncertainty_margin"])
            calls[valid] = "uncertain"
            calls[valid & ~_at_least(positives, counts, lower)] = "negative"
            calls[valid & _at_least(positives, counts, upper)] = "positive"
            reasons[:] = "nonfinite_pixels"
            reasons[valid] = ""
        states.append(calls[eligible])
        frames.append(pd.DataFrame(dict(label=ids[eligible], marker=marker["name"], channel=channel,
            voxel_count=counts[eligible], fraction=fraction[eligible], call=calls[eligible], reason=reasons[eligible])))
    calls_table = pd.concat(frames, ignore_index=True)
    state = np.column_stack(states)
    names = [m["name"] for m in spec["markers"]]
    total = int(eligible.sum())

    def summarize(positive, negative, denominator_positive):
        """Complete-case rates plus how much of the denominator they cover.

        ``denominator`` counts only cells whose involved markers are all called,
        so ``percentage`` stays complete-case. ``denominator_population`` counts
        every cell positive for the denominator markers regardless of the other
        markers, and coverage reports which share of it was evaluable.
        """
        involved = [names.index(n) for n in names if n in set(positive+negative+denominator_positive)]
        relevant = state[:, involved]
        missing = (relevant == "missing").any(axis=1)
        uncertain = ~missing & (relevant == "uncertain").any(axis=1)
        evaluable = ~(missing | uncertain)
        population = np.ones(len(state), dtype=bool)
        for name in denominator_positive:
            population &= state[:, names.index(name)] == "positive"
        denominator = evaluable & population
        numerator = denominator.copy()
        for name in positive:
            numerator &= state[:, names.index(name)] == "positive"
        for name in negative:
            numerator &= state[:, names.index(name)] == "negative"
        den, num, pop = int(denominator.sum()), int(numerator.sum()), int(population.sum())
        return dict(total_eligible=total, evaluable=int(evaluable.sum()), missing=int(missing.sum()),
            uncertain=int(uncertain.sum()), denominator=den, numerator=num, percentage=100*num/den if den else np.nan,
            denominator_population=pop, denominator_coverage_pct=100*den/pop if pop else np.nan,
            denominator_missing=int((population & missing).sum()),
            denominator_uncertain=int((population & uncertain).sum()))

    query_rows = []
    for q in spec["queries"]:
        checkpoint()
        query_rows.append(dict(name=q["name"], **{key: json.dumps(q[key], ensure_ascii=False) for key in ("positive", "negative", "denominator_positive")},
                               **summarize(q["positive"], q["negative"], q["denominator_positive"])))
    summary_columns = ["total_eligible", "evaluable", "missing", "uncertain", "denominator", "numerator", "percentage",
                       "denominator_population", "denominator_coverage_pct", "denominator_missing", "denominator_uncertain"]
    query_table = pd.DataFrame(query_rows, columns=["name", "positive", "negative", "denominator_positive", *summary_columns])
    pattern_rows = []
    for bits in product((False, True), repeat=len(names)):
        checkpoint()
        positive = [n for n,b in zip(names,bits) if b]
        negative = [n for n,b in zip(names,bits) if not b]
        pattern_rows.append(dict(pattern=" ".join(n + ("+" if b else "−") for n,b in zip(names,bits)),
                                 **summarize(positive, negative, [])))
    metadata = dict(schema_version=1, recipe_fingerprint=recipe.fingerprint, context=ctx,
                    region_policy=spec["region_policy"], region_kind="whole_image" if roi is None else "explicit_mask",
                    total_objects=len(ids), total_eligible=total, excluded=int((~eligible).sum()),
                    spacing_um=list(image.spacing_um), channel_names=list(image.channel_names),
                    measurement_grid="ZYXC", analysis_metadata=dict(image.metadata))
    checkpoint()
    return ClassificationResult(calls_table, query_table, pd.DataFrame(pattern_rows), exclusions, metadata)
