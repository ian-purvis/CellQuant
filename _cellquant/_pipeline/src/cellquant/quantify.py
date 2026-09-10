from __future__ import annotations

from itertools import combinations
import numpy as np
import pandas as pd


def _threshold_rule(value):
    """Normalize scalar lower cutoff or {low, high, positive_fraction} rule."""
    if isinstance(value, dict):
        low = float(value.get("low", -np.inf))
        high = float(value.get("high", np.inf))
        cutoff = float(value.get("positive_fraction", 0.5))
    else:
        low, high, cutoff = float(value), np.inf, 0.5
    if low > high or not 0 <= cutoff <= 1:
        raise ValueError("Marker threshold requires low <= high and positive_fraction in [0,1]")
    return low, high, cutoff


def _align(image_czyx: np.ndarray, labels: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    image = np.asarray(image_czyx)
    labels = np.asarray(labels)
    if image.ndim != 4:
        raise ValueError("Image must have CZYX axes")
    if labels.ndim == 2:
        if image.shape[1] != 1:
            raise ValueError("2D labels require a single-plane or projected CZYX measurement image")
        labels = labels[None]
    if labels.shape != image.shape[1:]:
        raise ValueError(f"Mask shape {labels.shape} does not match image ZYX {image.shape[1:]}")
    if not np.issubdtype(labels.dtype, np.integer) or np.any(labels < 0):
        raise ValueError("Labels must be non-negative integers")
    return image, labels


def quantify(image_czyx, labels, channel_names=None, thresholds=None):
    """Return one row per label and channel with exact, non-binned measurements."""
    image, labels = _align(image_czyx, labels)
    channel_names = channel_names or [f"C{i + 1}" for i in range(image.shape[0])]
    if len(channel_names) != image.shape[0] or len(set(channel_names)) != len(channel_names):
        raise ValueError("Channel names must be unique and match the C axis")
    thresholds = thresholds or {}
    rows = []
    for label in np.unique(labels):
        if label == 0:
            continue
        inside = labels == label
        for c, name in enumerate(channel_names):
            pixels = np.asarray(image[c][inside], dtype=np.float64)
            rule = _threshold_rule(thresholds[name]) if name in thresholds else None
            positive_fraction = (float(np.mean((pixels >= rule[0]) & (pixels <= rule[1])))
                                 if rule is not None else np.nan)
            rows.append({
                "label": int(label), "channel": name, "pixel_count": int(pixels.size),
                "mean": float(pixels.mean()), "median": float(np.median(pixels)),
                "min": float(pixels.min()), "max": float(pixels.max()),
                "integrated_intensity": float(pixels.sum()),
                "threshold_low": rule[0] if rule is not None else np.nan,
                "threshold_high": rule[1] if rule is not None else np.nan,
                "positive_cutoff": rule[2] if rule is not None else np.nan,
                "positive_pixel_fraction": positive_fraction,
            })
    columns = ["label", "channel", "pixel_count", "mean", "median", "min", "max",
               "integrated_intensity", "threshold_low", "threshold_high",
               "positive_cutoff", "positive_pixel_fraction"]
    return pd.DataFrame(rows, columns=columns)


def overlap_metrics(image_czyx, labels, channel_names, thresholds):
    """Pairwise binary overlap restricted to each labeled object.

    Manders-style fractions are intersection/A-positive and intersection/B-positive;
    these are binary thresholded fractions, not intensity-weighted Manders coefficients.
    """
    image, labels = _align(image_czyx, labels)
    if len(channel_names) != image.shape[0] or len(set(channel_names)) != len(channel_names):
        raise ValueError("Channel names must be unique and match the C axis")
    idx = {name: i for i, name in enumerate(channel_names)}
    missing = set(thresholds) - set(idx)
    if missing:
        raise ValueError(f"Unknown threshold channel(s): {sorted(missing)}")
    rows = []
    for label in np.unique(labels):
        if label == 0:
            continue
        inside = labels == label
        rules = {name: _threshold_rule(value) for name, value in thresholds.items()}
        binary = {name: (image[idx[name]][inside] >= rule[0]) & (image[idx[name]][inside] <= rule[1])
                  for name, rule in rules.items()}
        calls = {name: bool(np.mean(values) >= rules[name][2]) for name, values in binary.items()}
        for a, b in combinations(thresholds, 2):
            av, bv = binary[a], binary[b]
            inter = int(np.count_nonzero(av & bv)); union = int(np.count_nonzero(av | bv))
            na, nb = int(np.count_nonzero(av)), int(np.count_nonzero(bv))
            rows.append({"label": int(label), "channel_a": a, "channel_b": b,
                         "intersection_pixels": inter, "jaccard": inter / union if union else 0.0,
                         "manders_a_in_b": inter / na if na else 0.0,
                         "manders_b_in_a": inter / nb if nb else 0.0})
        rows.append({"label": int(label), "channel_a": "__calls__", "channel_b": "__calls__",
                     "marker_calls": ";".join(f"{k}={'+' if v else '-'}" for k, v in calls.items()),
                     "coexpression_order": int(sum(calls.values()))})
    columns = ["label", "channel_a", "channel_b", "intersection_pixels", "jaccard",
               "manders_a_in_b", "manders_b_in_a", "marker_calls", "coexpression_order"]
    return pd.DataFrame(rows, columns=columns)


def object_table(measurements: pd.DataFrame, overlaps: pd.DataFrame) -> pd.DataFrame:
    if measurements.empty:
        return pd.DataFrame(columns=["label"])
    metrics = ["mean", "median", "min", "max", "integrated_intensity", "positive_pixel_fraction"]
    wide = measurements.pivot(index="label", columns="channel", values=metrics)
    wide.columns = [f"{channel}_{metric}" for metric, channel in wide.columns]
    out = wide.reset_index()
    counts = measurements.groupby("label").pixel_count.first().rename("object_pixel_count").reset_index()
    out = out.merge(counts, on="label", how="left")
    calls = overlaps[overlaps.channel_a == "__calls__"][["label", "marker_calls", "coexpression_order"]]
    return out.merge(calls, on="label", how="left")


def coexpression_summary(objects: pd.DataFrame) -> pd.DataFrame:
    if objects.empty or "coexpression_order" not in objects:
        return pd.DataFrame(columns=["coexpression_order", "marker_calls", "object_count", "object_fraction"])
    result = objects.groupby(["coexpression_order", "marker_calls"], dropna=False).size().rename("object_count").reset_index()
    result["object_fraction"] = result.object_count / result.object_count.sum()
    return result
