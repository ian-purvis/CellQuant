"""Count area: polygons drawn after segmentation that limit which objects are counted.

Objects whose centroid lies outside every polygon stay in the object table, marked
``in_count_area = False``, and are left out of every count. No polygons means the
whole image counts. Polygons are (row, column) vertices in the image's pixels and
apply to every Z slice. This is unrelated to cropping before segmentation
(``cellquant.crop``), which changes where objects are found.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from skimage.draw import polygon2mask
from skimage.measure import points_in_poly

Polygons = list[list[list[float]]]


def clean(polygons) -> Polygons:
    """Polygons as plain lists of [row, column], dropping any with fewer than three vertices."""

    cleaned: Polygons = []
    for polygon in polygons or []:
        vertices = np.asarray(polygon, dtype=float)
        if vertices.ndim != 2 or vertices.shape[1] < 2 or len(vertices) < 3:
            continue
        cleaned.append([[float(row), float(column)] for row, column in vertices[:, -2:]])
    return cleaned


def mark_objects(objects: pd.DataFrame, polygons, pixel_size: float | None) -> pd.DataFrame:
    """Add ``in_count_area`` from each object's centroid. Without polygons the column is removed."""

    output = objects.drop(columns=["in_count_area"], errors="ignore")
    polygons = clean(polygons)
    if not polygons:
        return output
    if output.empty:
        output["in_count_area"] = pd.Series(dtype=bool)
        return output
    scale = 1.0 if pixel_size is None else float(pixel_size)
    points = np.column_stack(
        [output["centroid_y"].to_numpy(dtype=float) / scale, output["centroid_x"].to_numpy(dtype=float) / scale]
    )
    inside = np.zeros(len(points), dtype=bool)
    for polygon in polygons:
        inside |= points_in_poly(points, np.asarray(polygon, dtype=float))
    output["in_count_area"] = inside
    return output


def area(polygons, shape_yx: tuple[int, int], pixel_size: float | None) -> float | None:
    """Area covered by the polygons inside the image (overlaps counted once), in µm² or px²."""

    polygons = clean(polygons)
    if not polygons:
        return None
    mask = np.zeros(tuple(int(value) for value in shape_yx), dtype=bool)
    for polygon in polygons:
        mask |= polygon2mask(mask.shape, np.asarray(polygon, dtype=float))
    scale = 1.0 if pixel_size is None else float(pixel_size)
    return float(mask.sum()) * scale * scale


def outside(table: pd.DataFrame) -> np.ndarray:
    """True for objects outside the count area (none when the image has no count area)."""

    if "in_count_area" not in table.columns:
        return np.zeros(len(table), dtype=bool)
    return ~table["in_count_area"].map(_inside_flag).to_numpy(dtype=bool)


def _inside_flag(value) -> bool:
    # Blank (an image without a count area in a combined table) counts as inside.
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return True
    if isinstance(value, str):
        return value.strip().casefold() not in {"false", "0", "no"}
    return bool(value)
