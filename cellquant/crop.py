"""Crop to the region of interest before segmenting.

Often only part of a retina holds the manipulated (reporter-positive) cells. With cropping on,
CellQuant finds the region(s) holding those cells, grows them by a margin, segments only inside
rectangles around them, and puts the labels back into the full frame, so edits, overlays,
centroids and exports are unchanged.

The region is found inclusively, so positive cells are not missed: each chosen channel (combined
with AND) is averaged in small blocks, smoothed, and kept where it is brighter than the image's
background by ``sensitivity`` noise units (areas that never reach twice that are noise). Holes are
filled, specks much smaller than a nucleus are dropped, the region is grown by ``margin_um``, and rectangles closer than ``merge_gap_um`` are
merged. An image whose rectangles would cover more than ``max_fraction`` of it is not cropped.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

Rectangle = tuple[int, int, int, int]  # y0, y1, x0, x1 (end exclusive)


# -- which channels define the region ---------------------------------------------------------------


def _classification_channels(recipe, classification_ids: set[str]) -> set[int]:
    measurements = {item.id: int(item.channel) for item in recipe.measurements}
    return {
        measurements[item.measurement]
        for item in recipe.classifications
        if item.id in classification_ids and item.measurement in measurements
    }


def crop_channels(recipe) -> list[int]:
    """The channels that define the region: the recipe's choice, or the channels the reports'
    denominators use (e.g. the reporter in (Fluor+ OTX2+) / Fluor+). Without a denominator that
    uses a marker, the channels of the reports' numerators; without reports, the objects' channel."""

    from cellquant.quantify import expression_classifications

    spec = recipe.crop
    if spec is not None and spec.channels:
        return sorted({int(channel) for channel in spec.channels})
    denominators: set[str] = set()
    numerators: set[str] = set()
    for report in recipe.reports:
        denominators |= expression_classifications(report.denominator, recipe.classifications)
        numerators |= expression_classifications(report.numerator, recipe.classifications)
    channels = _classification_channels(recipe, denominators) or _classification_channels(recipe, numerators)
    return sorted(channels) if channels else [int(recipe.object_set.segmentation_channel)]


def crop_channel_warnings(recipe, channel_names: dict[int, str] | None = None) -> list[str]:
    """Warn when a region channel appears only in a report's numerator.

    Requiring it (AND) removes areas that are positive for the denominator but negative for that
    channel, so the numerator/denominator percentage comes out too high.
    """

    from cellquant.quantify import expression_classifications

    names = channel_names or {}
    chosen = set(crop_channels(recipe))
    warnings = []
    for index, report in enumerate(recipe.reports, start=1):
        denominator = expression_classifications(report.denominator, recipe.classifications)
        numerator = expression_classifications(report.numerator, recipe.classifications)
        only_numerator = _classification_channels(recipe, numerator) - _classification_channels(recipe, denominator)
        for channel in sorted(chosen & only_numerator):
            label = names.get(channel, f"channel {channel + 1}")
            warnings.append(
                f"{label} is only in the numerator of report {index} ({report.numerator} / {report.denominator}). "
                f"Requiring it for the region removes {report.denominator} areas without {label}, "
                "which inflates that percentage."
            )
    return warnings


# -- finding the regions ------------------------------------------------------------------------------


def _binned(plane: np.ndarray, factor: int) -> np.ndarray:
    """Mean of factor x factor blocks (the edge rows and columns are padded with the edge values)."""

    image = np.asarray(plane, dtype=np.float64)
    if factor <= 1:
        return image
    height, width = image.shape
    pad_y, pad_x = (-height) % factor, (-width) % factor
    if pad_y or pad_x:
        image = np.pad(image, ((0, pad_y), (0, pad_x)), mode="edge")
    return image.reshape(image.shape[0] // factor, factor, image.shape[1] // factor, factor).mean(axis=(1, 3))


def _positive_mask(plane: np.ndarray, factor: int, spec) -> np.ndarray:
    """Binned pixels brighter than the background by ``sensitivity`` noise units (inclusive)."""

    binned = ndimage.gaussian_filter(_binned(plane, factor), 1.0)
    # Background and its noise from the dimmest quarter (for normal noise p25 - p5 = 0.971 SD and the
    # mean is p25 + 0.674 SD): valid while at least a quarter of the image is background.
    p5, p25 = (float(value) for value in np.percentile(binned, [5, 25]))
    noise = (p25 - p5) / 0.971
    if noise <= 0:
        noise = float(np.std(binned[binned <= p25])) or 1e-6
    background = p25 + 0.674 * noise
    mask = binned > background + spec.sensitivity * noise
    # Keep only areas that reach twice that somewhere (a positive cell, not a ripple of noise).
    labelled, count = ndimage.label(mask)
    if count:
        seeds = np.unique(labelled[binned > background + 2 * spec.sensitivity * noise])
        mask = np.isin(labelled, seeds[seeds > 0])
    return mask


def detect_regions(planes: list[np.ndarray], pixel_size_um: float, spec, info: dict | None = None) -> list[Rectangle] | None:
    """Rectangles (y0, y1, x0, x1) around everything positive on every plane, grown by the margin.

    Returns [] when nothing is positive, and None when the rectangles would cover more than
    ``spec.max_fraction`` of the image (cropping would save little: segment it in full).
    ``info`` receives ``fraction`` (the share of the image in the rectangles), ``skipped`` and
    ``seconds``.
    """

    import time

    started = time.perf_counter()
    info = {} if info is None else info
    if not planes:
        info.update(fraction=0.0, skipped=False, seconds=0.0)
        return []
    shape = np.asarray(planes[0]).shape
    factor = max(1, int(round(spec.bin_um / pixel_size_um)))
    mask = _positive_mask(planes[0], factor, spec)
    for plane in planes[1:]:
        mask &= _positive_mask(plane, factor, spec)  # AND: positive on every chosen channel
    binned_um = factor * pixel_size_um
    nucleus_area = np.pi * (spec.nucleus_diameter_um / 2.0 / binned_um) ** 2
    mask = ndimage.binary_fill_holes(mask)
    labelled, count = ndimage.label(mask)
    if count:
        areas = ndimage.sum_labels(np.ones_like(labelled), labelled, index=np.arange(1, count + 1))
        small = np.flatnonzero(areas < 0.25 * nucleus_area) + 1  # specks: much smaller than a nucleus
        mask &= ~np.isin(labelled, small)
    if not np.any(mask):
        info.update(fraction=0.0, skipped=False, seconds=time.perf_counter() - started)
        return []
    margin = int(np.ceil(spec.margin_um / binned_um))
    grown = ndimage.distance_transform_edt(~mask) <= margin
    labelled, _count = ndimage.label(grown)
    rectangles = []
    for found in ndimage.find_objects(labelled):
        if found is None:
            continue
        rows, cols = found
        rectangles.append(
            (
                max(0, rows.start * factor),
                min(shape[0], rows.stop * factor),
                max(0, cols.start * factor),
                min(shape[1], cols.stop * factor),
            )
        )
    gap = int(round(spec.merge_gap_um / pixel_size_um))
    rectangles = merge_rectangles(rectangles, gap)
    area = sum((y1 - y0) * (x1 - x0) for y0, y1, x0, x1 in rectangles)
    fraction = area / float(shape[0] * shape[1])
    skipped = fraction > spec.max_fraction
    info.update(fraction=fraction, skipped=skipped, seconds=time.perf_counter() - started)
    return None if skipped else rectangles


def merge_rectangles(rectangles: list[Rectangle], gap: int = 0) -> list[Rectangle]:
    """Merge rectangles that overlap or are closer than ``gap`` pixels, until none are.

    Many small rectangles cost more than they save (each is a separate Cellpose call with its own
    edges), so nearby ones become one. Sorted top to bottom, left to right.
    """

    boxes = [tuple(int(value) for value in box) for box in rectangles]
    merged = True
    while merged:
        merged = False
        for first in range(len(boxes)):
            for second in range(first + 1, len(boxes)):
                a, b = boxes[first], boxes[second]
                if a[0] <= b[1] + gap and b[0] <= a[1] + gap and a[2] <= b[3] + gap and b[2] <= a[3] + gap:
                    boxes[first] = (min(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), max(a[3], b[3]))
                    del boxes[second]
                    merged = True
                    break
            if merged:
                break
    return sorted(boxes)  # type: ignore[return-value]


def regions_for(loaded, recipe, info: dict | None = None) -> list[Rectangle] | None:
    """Crop rectangles for this image, or None for the full image (cropping off, 3D, no pixel size,
    or the region covers too much of the image; ``info`` says which)."""

    from cellquant.regions import isotropic_pixel_size

    info = {} if info is None else info
    spec = recipe.crop
    if spec is None or not spec.enabled or loaded.is_3d:
        return None
    pixel_size = isotropic_pixel_size(loaded.pixel_size_x, loaded.pixel_size_y)
    if pixel_size is None:
        info["note"] = "This image has no pixel size, so it was not cropped."
        return None
    channels = [channel for channel in crop_channels(recipe) if 0 <= channel < loaded.n_channels]
    if not channels:
        return None
    rectangles = detect_regions([np.asarray(loaded.data[channel]) for channel in channels], float(pixel_size), spec, info)
    if rectangles is None:
        info["note"] = (
            f"The region of interest covers {info['fraction']:.0%} of this image (more than {spec.max_fraction:.0%}), "
            "so it was segmented in full."
        )
    return rectangles


def objects_at_crop_edges(labels: np.ndarray, rectangles: list[Rectangle]) -> list[int]:
    """Objects touching a crop edge that is not the image border (they may be cut)."""

    labels = np.asarray(labels)
    height, width = labels.shape[-2:]
    touching: set[int] = set()
    for y0, y1, x0, x1 in rectangles:
        edges = []
        if y0 > 0:
            edges.append(labels[..., y0, x0:x1])
        if y1 < height:
            edges.append(labels[..., y1 - 1, x0:x1])
        if x0 > 0:
            edges.append(labels[..., y0:y1, x0])
        if x1 < width:
            edges.append(labels[..., y0:y1, x1 - 1])
        for edge in edges:
            touching.update(int(value) for value in np.unique(edge) if value)
    return sorted(touching)
