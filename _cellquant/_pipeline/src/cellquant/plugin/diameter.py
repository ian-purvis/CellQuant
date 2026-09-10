"""Helpers for measuring a typical object diameter in napari."""

from __future__ import annotations

from typing import Any

import numpy as np

DIAMETER_SHAPES_LAYER = "CellQuant diameter"


def line_length_px(vertices: Any, *, scale_xy: tuple[float, float] | None = None) -> float:
    """Return Euclidean length of a polyline in image XY pixel coordinates.

    When ``scale_xy`` is provided (micrometres per pixel in Y,X or the layer's
    last-two axis scales), vertices are treated as world/data coordinates and
    converted into pixels by dividing each axis by its scale.
    """

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
    if scale_xy is not None:
        sy, sx = float(scale_xy[0]), float(scale_xy[1])
        if not np.isfinite(sy) or not np.isfinite(sx) or sy <= 0 or sx <= 0:
            raise ValueError("image XY scale must be positive and finite")
        xy = xy / np.asarray([sy, sx], dtype=float)
    deltas = np.diff(xy, axis=0)
    length = float(np.sum(np.linalg.norm(deltas, axis=1)))
    if not np.isfinite(length) or length <= 0:
        raise ValueError("measured line length must be a positive finite number")
    return length


def line_length_um(vertices: Any, *, scale_xy: tuple[float, float]) -> float:
    """Physical length in micrometres for the same vertices."""

    points = np.asarray(vertices, dtype=float)
    if points.ndim != 2 or points.shape[0] < 2:
        raise ValueError("a diameter measurement needs a line with at least two vertices")
    xy = points[:, -2:] if points.shape[1] >= 3 else points
    deltas = np.diff(xy, axis=0)
    length = float(np.sum(np.linalg.norm(deltas, axis=1)))
    if not np.isfinite(length) or length <= 0:
        raise ValueError("measured line length must be a positive finite number")
    return length


def _image_xy_scale(image_layer: Any | None) -> tuple[float, float] | None:
    if image_layer is None:
        return None
    metadata = dict(getattr(image_layer, "metadata", {}) or {})
    spacing = metadata.get("spacing_um")
    if spacing is not None and len(spacing) >= 3:
        return float(spacing[1]), float(spacing[2])
    scale = np.asarray(getattr(image_layer, "scale", ()), dtype=float)
    if scale.size >= 2:
        return float(scale[-2]), float(scale[-1])
    return None


def diameter_from_shapes_layer(layer: Any, *, image_layer: Any | None = None) -> float:
    """Use the last line-like shape as diameter_px in the bound image's pixels."""

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
    scale_xy = _image_xy_scale(image_layer)
    # If the shapes layer itself carries a matching scale, prefer that for conversion.
    if scale_xy is None:
        scale_xy = _image_xy_scale(layer)
    return line_length_px(data[index], scale_xy=scale_xy)


def bind_diameter_shapes_to_image(shapes_layer: Any, image_layer: Any) -> None:
    """Match the diameter shapes layer scale/translate to the selected image."""

    if shapes_layer is None or image_layer is None:
        return
    image_scale = np.asarray(getattr(image_layer, "scale", ()), dtype=float)
    if image_scale.size >= 2:
        # Shapes are typically 2D (Y,X) even when the image is 3D ZYX.
        shapes_layer.scale = tuple(float(v) for v in image_scale[-2:])
    translate = getattr(image_layer, "translate", None)
    if translate is not None:
        values = np.asarray(translate, dtype=float)
        if values.size >= 2:
            shapes_layer.translate = tuple(float(v) for v in values[-2:])
