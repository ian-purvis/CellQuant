"""Which segmentation a set of labels belongs to.

A lineage key binds automated labels to the input pixels, calibration,
position, segmentation settings and the software that made them. Manual edits
are stamped with it, so they only ever apply to the objects they were made on.
"""

from __future__ import annotations

import hashlib
from typing import Any

import numpy as np

from cellquant.recipe import Recipe

SEGMENTATION_SETTINGS = ("z_stack", "z_index", "z_stitch_threshold", "z_scale_brightness", "z_min_slices", "object_set")


def segmentation_settings(recipe: Recipe, effective_z_index: int | None = None) -> dict[str, Any]:
    """The part of the settings that decides the objects (not measurements or thresholds)."""

    scientific = recipe.scientific_dict()
    settings = {key: scientific[key] for key in SEGMENTATION_SETTINGS if key in scientific}
    if recipe.z_stack == "single_plane":
        settings["z_index"] = effective_z_index
    return settings


def labels_sha256(labels: np.ndarray) -> str:
    array = np.ascontiguousarray(np.asarray(labels, dtype=np.int32))
    digest = hashlib.sha256()
    digest.update(str(tuple(array.shape)).encode())
    digest.update(array.astype("<i4", copy=False).tobytes())
    return digest.hexdigest()


def lineage_key(
    *,
    input_sha256: str,
    position: int,
    calibration: list[float | None],
    settings: dict[str, Any],
    runtime: str,
    labels: np.ndarray,
) -> str:
    from cellquant.hpc.common import sha256_json

    return "lineage-" + sha256_json(
        {
            "input_sha256": input_sha256,
            "position": int(position),
            "calibration": [None if value is None else float(value) for value in calibration],
            "settings": settings,
            "runtime": runtime,
            "labels_sha256": labels_sha256(labels),
            "labels_shape": [int(value) for value in np.asarray(labels).shape],
        }
    )
