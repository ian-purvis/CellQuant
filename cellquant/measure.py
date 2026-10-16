"""Lengths, areas and volumes of shapes drawn on an image (the Measure window).

Vertices are (row, column) pixel positions, as a 2D napari Shapes layer stores them. A line or
freehand line gives a length; a closed shape gives its perimeter, area and volume. Volume is the
area times a depth the user picks: the whole Z stack (slices × Z step) or one slice (one Z step).
Units come from the image's calibration (µm); without one, everything is in pixels and volume in
voxels (area in pixels × slices).
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# napari shape type -> the name shown and exported.
SHAPE_NAMES = {
    "line": "line",
    "path": "freehand line",
    "rectangle": "rectangle",
    "polygon": "freehand shape",
    "ellipse": "ellipse",
}
OPEN_SHAPES = ("line", "path")
DEPTH_OPTIONS = (("stack", "Whole stack"), ("slice", "One slice"))


@dataclass(frozen=True)
class Calibration:
    """Pixel size and Z step in µm (None when the file has none) and the number of Z slices."""

    pixel_size_x: float | None = None
    pixel_size_y: float | None = None
    pixel_size_z: float | None = None
    z_slices: int = 1

    @property
    def calibrated(self) -> bool:
        return bool(self.pixel_size_x) and bool(self.pixel_size_y)


@dataclass(frozen=True)
class Measurement:
    image: str
    number: int
    shape: str
    unit: str  # "µm" or "px"
    length: float | None = None
    perimeter: float | None = None
    area: float | None = None
    volume: float | None = None
    depth: str = ""  # "whole stack (7 slices)", "one slice"; empty when there is no volume


def ellipse_outline(corners: np.ndarray, points: int = 64) -> np.ndarray:
    """napari stores an ellipse as the four corners of its bounding box."""

    angles = np.linspace(0.0, 2.0 * np.pi, points, endpoint=False)[:, None]
    half_a, half_b = (corners[1] - corners[0]) / 2.0, (corners[2] - corners[1]) / 2.0
    return corners.mean(axis=0) + np.cos(angles) * half_a + np.sin(angles) * half_b


def _scale(calibration: Calibration, use_um: bool) -> tuple[float, float]:
    if use_um:
        return float(calibration.pixel_size_y), float(calibration.pixel_size_x)
    return 1.0, 1.0


def measure_shape(
    vertices,
    kind: str,
    calibration: Calibration,
    *,
    image: str = "",
    number: int = 1,
    depth_mode: str = "stack",
    use_um: bool | None = None,
) -> Measurement:
    """One shape's measurements. use_um=None: µm when the image is calibrated."""

    use_um = calibration.calibrated if use_um is None else use_um and calibration.calibrated
    scale_y, scale_x = _scale(calibration, use_um)
    points = np.asarray(vertices, dtype=float)[:, -2:]
    if kind == "ellipse" and len(points) == 4:
        points = ellipse_outline(points)
    scaled = points * np.array([scale_y, scale_x])
    unit = "µm" if use_um else "px"
    name = SHAPE_NAMES.get(kind, kind)
    steps = np.diff(scaled, axis=0)
    if kind in OPEN_SHAPES:
        length = float(np.hypot(steps[:, 0], steps[:, 1]).sum()) if len(points) > 1 else 0.0
        return Measurement(image, number, name, unit, length=length)
    closing = scaled[0] - scaled[-1]
    perimeter = float(np.hypot(steps[:, 0], steps[:, 1]).sum() + math.hypot(*closing)) if len(points) > 1 else 0.0
    rows, cols = scaled[:, 0], scaled[:, 1]
    area = float(abs(np.dot(rows, np.roll(cols, -1)) - np.dot(cols, np.roll(rows, -1))) / 2.0)
    slices = calibration.z_slices if depth_mode == "stack" else 1
    if use_um:
        step = calibration.pixel_size_z
        volume = area * slices * float(step) if step else None
    else:
        volume = area * slices  # voxels
    if volume is None:
        depth = ""
    elif depth_mode == "stack":
        depth = f"whole stack ({slices} slice{'s' if slices != 1 else ''})"
    else:
        depth = "one slice"
    return Measurement(image, number, name, unit, perimeter=perimeter, area=area, volume=volume, depth=depth)


def measure_shapes(shapes, calibration: Calibration, *, image: str = "", depth_mode: str = "stack", use_um=None) -> list[Measurement]:
    """shapes: (vertices, napari shape type) pairs, numbered from 1 in drawing order."""

    return [
        measure_shape(vertices, kind, calibration, image=image, number=number, depth_mode=depth_mode, use_um=use_um)
        for number, (vertices, kind) in enumerate(shapes, start=1)
    ]


def volume_unit(unit: str) -> str:
    return "µm³" if unit == "µm" else "voxels"


def csv_header(unit: str) -> list[str]:
    area = "µm²" if unit == "µm" else "px²"
    return [
        "image",
        "shape_number",
        "shape",
        f"length ({unit})",
        f"perimeter ({unit})",
        f"area ({area})",
        f"volume ({volume_unit(unit)})",
        "volume_depth",
    ]


def write_csv(path: str | Path, measurements: list[Measurement]) -> None:
    """One row per shape. The unit is named in the header: µm when every row is calibrated, else pixels."""

    unit = "µm" if measurements and all(item.unit == "µm" for item in measurements) else "px"
    if any(item.unit != unit for item in measurements):
        raise ValueError("Measurements in different units; measure them again in pixels first.")

    def number(value: float | None) -> str:
        return "" if value is None else str(round(value, 4))

    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(csv_header(unit))
        for item in measurements:
            writer.writerow(
                [
                    item.image,
                    item.number,
                    item.shape,
                    number(item.length),
                    number(item.perimeter),
                    number(item.area),
                    number(item.volume),
                    item.depth,
                ]
            )
