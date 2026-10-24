"""Objects marked positive or negative by hand, to set a marker's cutoff ("teach by clicking").

Kept per image in the analysis's working folder (``cutoff_examples/<image>.json``), as
``{marker id: {object id: {"positive": bool, "x": centroid_x, "y": centroid_y}}}``. The centroid
tells a marked object from a new object with the same number after the image is run again.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from cellquant.storage import _safe


def examples_path(working_dir: Path, image_id: str) -> Path:
    return Path(working_dir) / "cutoff_examples" / f"{_safe(image_id)}.json"


def load_examples(working_dir: Path, image_id: str) -> dict[str, dict[str, dict]]:
    try:
        data = json.loads(examples_path(working_dir, image_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_examples(working_dir: Path, image_id: str, examples: dict[str, dict[str, dict]]) -> None:
    path = examples_path(working_dir, image_id)
    kept = {marker: marks for marker, marks in examples.items() if marks}
    if not kept:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(kept, indent=1, sort_keys=True), encoding="utf-8")


def current_examples(marks: dict[str, dict], objects: pd.DataFrame) -> dict[int, bool]:
    """The marks whose object is still there (same number, not deleted, about the same place)."""

    if not marks or "object_id" not in objects.columns:
        return {}
    frame = objects
    if "excluded" in frame.columns:
        frame = frame.loc[~frame["excluded"].astype(bool)]
    rows = frame.set_index("object_id")
    kept = {}
    for key, mark in marks.items():
        try:
            object_id = int(key)
            row = rows.loc[object_id]
        except (KeyError, ValueError, TypeError):
            continue
        if isinstance(row, pd.DataFrame):
            continue
        if not _same_place(mark, row):
            continue
        kept[object_id] = bool(mark.get("positive"))
    return kept


def _same_place(mark: dict, row: pd.Series) -> bool:
    if "x" not in mark or "y" not in mark or "centroid_x" not in row.index:
        return True
    area = float(row.get("area", np.nan))
    # Within half the object's radius (same units as the table, so µm or px alike).
    tolerance = 0.5 * np.sqrt(area / np.pi) if np.isfinite(area) and area > 0 else 1.0
    return float(np.hypot(float(row["centroid_x"]) - float(mark["x"]), float(row["centroid_y"]) - float(mark["y"]))) <= max(tolerance, 1e-6)


def mark(row: pd.Series, positive: bool) -> dict:
    return {"positive": bool(positive), "x": float(row["centroid_x"]), "y": float(row["centroid_y"])}


def _positive(values: np.ndarray, cutoff: float, comparison: str) -> np.ndarray:
    return values >= cutoff if comparison == "at_least" else values > cutoff


def suggest_cutoff(positive: np.ndarray, negative: np.ndarray, comparison: str = "above") -> float | None:
    """The cutoff that calls the most examples right: halfway across the widest such gap between
    neighbouring example values. None without at least one finite positive and one negative example
    (or when every example has the same value)."""

    positive = np.asarray(positive, dtype=float)
    negative = np.asarray(negative, dtype=float)
    positive, negative = positive[np.isfinite(positive)], negative[np.isfinite(negative)]
    if not positive.size or not negative.size:
        return None
    values = np.unique(np.concatenate([positive, negative]))
    if values.size < 2:
        return None
    cuts = (values[:-1] + values[1:]) / 2
    gaps = values[1:] - values[:-1]
    wrong = np.array([int((~_positive(positive, cut, comparison)).sum() + _positive(negative, cut, comparison).sum()) for cut in cuts])
    best = np.flatnonzero(wrong == wrong.min())
    return float(cuts[best[np.argmax(gaps[best])]])


def disagreeing(examples: dict[int, bool], values: dict[int, float], cutoff: float, comparison: str = "above") -> list[int]:
    """Marked objects the cutoff calls the other way (unmeasured objects are left out)."""

    out = []
    for object_id, positive in sorted(examples.items()):
        value = values.get(object_id, np.nan)
        if not np.isfinite(value):
            continue
        if bool(_positive(np.array([value]), cutoff, comparison)[0]) != positive:
            out.append(object_id)
    return out
