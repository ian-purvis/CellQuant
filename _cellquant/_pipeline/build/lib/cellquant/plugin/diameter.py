"""Helpers for measuring a typical object diameter in napari."""

from __future__ import annotations

from typing import Any

import numpy as np

DIAMETER_SHAPES_LAYER = "CellQuant diameter"


def line_length_px(vertices: Any) -> float:
    """Return Euclidean length of a polyline in data/pixel coordinates."""

    points = np.asarray(vertices, dtype=float)
    if points.ndim != 2 or points.shape[0] < 2:
        raise ValueError("a diameter measurement needs a line with at least two vertices")
    # Napari shapes are N×D; for image data D is usually (z, y, x) or (y, x).
    # Diameter for Cellpose is an XY size — ignore Z if present.
    if points.shape[1] >= 3:
        xy = points[:, -2:]
    elif points.shape[1] == 2:
        xy = points
    else:
        raise ValueError(f"unsupported shape vertex dimensionality {points.shape[1]}")
    deltas = np.diff(xy, axis=0)
    length = float(np.sum(np.linalg.norm(deltas, axis=1)))
    if not np.isfinite(length) or length <= 0:
        raise ValueError("measured line length must be a positive finite number")
    return length


def diameter_from_shapes_layer(layer: Any) -> float:
    """Use the last line-like shape on a napari Shapes layer as diameter_px."""

    if layer is None:
        raise ValueError(
            f"Draw a line on the '{DIAMETER_SHAPES_LAYER}' layer first "
            "(or create that layer with Add measure layer)."
        )
    data = list(getattr(layer, "data", []) or [])
    if not data:
        raise ValueError(
            f"No shapes on '{getattr(layer, 'name', DIAMETER_SHAPES_LAYER)}'. "
            "Select the line tool and draw across a typical nucleus or cell."
        )
    shape_types = list(getattr(layer, "shape_type", []) or [])
    # Prefer the last explicit line; otherwise fall back to the last shape.
    index = len(data) - 1
    for i in range(len(data) - 1, -1, -1):
        kind = shape_types[i] if i < len(shape_types) else None
        if kind in {None, "line", "path"}:
            index = i
            if kind in {"line", "path"}:
                break
    return line_length_px(data[index])
