"""Measurement regions derived from a label image.

Distances are Euclidean, in XY pixels. Expanded regions and rings assign each
pixel to the nearest object, so neighboring regions do not overlap. For a 3D
label image ``(z, y, x)``, ``z_scale`` is the Z step in XY pixels, so a
distance means the same thing along Z as in X and Y.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from cellquant.errors import CalibrationError, RecipeValidationError
from cellquant.recipe import BackgroundSpec, RegionSpec


def isotropic_pixel_size(
    pixel_size_x: float | None,
    pixel_size_y: float | None,
) -> float | None:
    """Return µm per pixel, or None when the image is uncalibrated.

    Physical morphology in this version uses one pixel size for X and Y.
    Unequal sizes are rejected instead of averaged.
    """

    if pixel_size_x is None and pixel_size_y is None:
        return None
    if pixel_size_x is None or pixel_size_y is None:
        raise CalibrationError(
            "Set both pixel sizes, in µm per pixel, or leave both unset to work in pixels."
        )
    if pixel_size_x <= 0 or pixel_size_y <= 0:
        raise CalibrationError("Pixel size must be positive.")
    relative = abs(pixel_size_x - pixel_size_y) / max(pixel_size_x, pixel_size_y)
    if relative > 1e-3:
        raise CalibrationError(
            "Physical distances need equal X and Y pixel sizes in this version. "
            f"This image has pixel_size_x={pixel_size_x} and pixel_size_y={pixel_size_y} µm."
        )
    return float(pixel_size_x)


def spatial_unit(pixel_size: float | None) -> str:
    return "um" if pixel_size is not None else "px"


def length_to_px(
    um: float | None,
    px: float | None,
    pixel_size: float | None,
    description: str,
) -> float:
    if um is None and px is None:
        raise RecipeValidationError(f"{description} requires a distance.")
    if um is not None and px is not None:
        raise RecipeValidationError(f"{description} must use either µm or pixels, not both.")
    if px is not None:
        return float(px)
    if pixel_size is None:
        raise CalibrationError(
            f"{description} is in µm, but this image has no pixel size. "
            "Provide a pixel size or specify the distance in pixels."
        )
    return float(um) / pixel_size


def create_measurement_region(
    labels: np.ndarray,
    region: RegionSpec,
    *,
    pixel_size: float | None,
    z_scale: float = 1.0,
) -> np.ndarray:
    """Build a label image for one measurement region definition."""

    if region.type == "object":
        return np.array(labels, copy=True)
    if region.type == "eroded_object":
        radius = length_to_px(
            region.distance_um,
            region.distance_px,
            pixel_size,
            "Eroded-object distance",
        )
        return erode_labels(labels, radius, z_scale=z_scale)
    if region.type == "expanded_object":
        radius = length_to_px(
            region.distance_um,
            region.distance_px,
            pixel_size,
            "Expanded-object distance",
        )
        return expand_labels(labels, radius, z_scale=z_scale)
    inner = length_to_px(region.inner_um, region.inner_px, pixel_size, "Ring inner distance")
    outer = length_to_px(region.outer_um, region.outer_px, pixel_size, "Ring outer distance")
    return ring_labels(labels, inner, outer, z_scale=z_scale)


def ring_from_background(
    labels: np.ndarray,
    background: BackgroundSpec,
    *,
    pixel_size: float | None,
    z_scale: float = 1.0,
) -> np.ndarray:
    inner = length_to_px(
        background.inner_um,
        background.inner_px,
        pixel_size,
        "Local-ring inner distance",
    )
    outer = length_to_px(
        background.outer_um,
        background.outer_px,
        pixel_size,
        "Local-ring outer distance",
    )
    return ring_labels(labels, inner, outer, z_scale=z_scale)


def _sampling(labels: np.ndarray, z_scale: float) -> tuple[float, ...]:
    return (float(z_scale), 1.0, 1.0) if labels.ndim == 3 else (1.0, 1.0)


def erode_labels(labels: np.ndarray, radius_px: float, *, z_scale: float = 1.0) -> np.ndarray:
    """Contract each object inward. Objects that disappear keep no pixels."""

    labels = np.asarray(labels)
    if radius_px < 0:
        raise RecipeValidationError("Erosion radius must be non-negative.")
    if radius_px == 0 or labels.max(initial=0) == 0:
        return labels.copy()
    output = labels.copy()
    sampling = _sampling(labels, z_scale)
    inner = tuple(slice(1, -1) for _ in range(labels.ndim))
    for index, bounds in enumerate(ndimage.find_objects(labels), start=1):
        if bounds is None:
            continue
        crop = labels[bounds] == index
        padded = np.pad(crop, 1, constant_values=False)
        distance = ndimage.distance_transform_edt(padded, sampling=sampling)[inner]
        remove = crop & (distance <= radius_px)
        view = output[bounds]
        view[remove] = 0
    return output


def expand_labels(labels: np.ndarray, radius_px: float, *, z_scale: float = 1.0) -> np.ndarray:
    """Grow objects without overlap, assigning each new pixel to the nearest object."""

    labels = np.asarray(labels)
    if radius_px < 0:
        raise RecipeValidationError("Expansion radius must be non-negative.")
    output = labels.copy()
    if radius_px == 0 or labels.max(initial=0) == 0:
        return output
    foreground = labels > 0
    distance, nearest = _distance_to_objects(foreground, labels, z_scale)
    grow = (~foreground) & (distance <= radius_px)
    output[grow] = nearest[grow]
    return output


def ring_labels(labels: np.ndarray, inner_px: float, outer_px: float, *, z_scale: float = 1.0) -> np.ndarray:
    """Label pixels whose distance to the nearest object is between inner and outer."""

    labels = np.asarray(labels)
    if inner_px < 0 or outer_px < 0:
        raise RecipeValidationError("Ring distances must be non-negative.")
    if not inner_px < outer_px:
        raise RecipeValidationError("Ring inner distance must be less than the outer distance.")
    output = np.zeros(labels.shape, dtype=labels.dtype)
    if labels.max(initial=0) == 0:
        return output
    foreground = labels > 0
    distance, nearest = _distance_to_objects(foreground, labels, z_scale)
    ring = (~foreground) & (distance >= inner_px) & (distance <= outer_px)
    output[ring] = nearest[ring]
    return output


def _distance_to_objects(
    foreground: np.ndarray,
    labels: np.ndarray,
    z_scale: float = 1.0,
) -> tuple[np.ndarray, np.ndarray]:
    distance, indices = ndimage.distance_transform_edt(
        ~foreground, sampling=_sampling(labels, z_scale), return_indices=True
    )
    nearest = labels[tuple(indices)]
    return distance, nearest
