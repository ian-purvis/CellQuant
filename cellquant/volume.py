"""3D segmentation of Z-stacks.

Two ways to get 3D objects:

``stitch_slices``
    Each slice is segmented in 2D. An outline is linked to the outline in the
    slice above when their overlap (intersection over union) is at least the
    stitch threshold. This is Cellpose's ``stitch_threshold`` method, done
    here so it works the same way for every segmentation method, and so the
    brightness of every slice can be scaled together first.

``full_3d``
    The method works on the whole volume at once: Cellpose's ``do_3D`` mode,
    or thresholding and watershed in 3D for the classical method.

Brightness scaling matters for linking: Cellpose scales each slice on its own
by default, so a dim top or bottom slice is stretched and its outlines stop
matching the slices next to it. With ``scale="stack"`` one scale is used for
the whole stack.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy import ndimage

from cellquant import progress
from cellquant.errors import SegmentationError

# Cellpose normalizes between these percentiles by default.
LOW_PERCENTILE = 1.0
HIGH_PERCENTILE = 99.0


def anisotropy(pixel_size_xy: float | None, pixel_size_z: float | None) -> float | None:
    """Z step divided by the XY pixel size, or None when either is unknown."""

    if not pixel_size_xy or not pixel_size_z:
        return None
    return float(pixel_size_z) / float(pixel_size_xy)


def scale_brightness(stack: np.ndarray, scope: str = "stack") -> np.ndarray:
    """Scale to 0 at the 1st and 1 at the 99th percentile, for the stack or per slice."""

    data = np.asarray(stack, dtype=np.float32)
    if scope == "slice":
        return np.stack([_scale(plane, plane) for plane in data])
    sample = data if data.size <= 16_000_000 else data[:, ::2, ::2]
    return _scale(data, sample)


def _scale(values: np.ndarray, sample: np.ndarray) -> np.ndarray:
    low, high = np.percentile(sample, [LOW_PERCENTILE, HIGH_PERCENTILE])
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        return np.zeros(values.shape, dtype=np.float32)
    return ((values - low) / (high - low)).astype(np.float32)


def stitch_slices(planes: np.ndarray, threshold: float = 0.25) -> np.ndarray:
    """Link 2D labels in consecutive slices into 3D objects.

    ``planes`` is ``(z, y, x)`` with labels numbered independently in each
    slice. For each pair of neighbouring slices, the overlap (intersection
    over union) of every pair of outlines is computed. Pairs below
    ``threshold`` are ignored; each outline in the upper slice keeps only its
    best match below; each outline below is linked to its best remaining
    match above, or starts a new object. This follows Cellpose's
    ``stitch3D``.
    """

    planes = np.asarray(planes)
    if planes.ndim != 3:
        raise SegmentationError("Linking slices needs labels shaped (z, y, x).")
    output = np.zeros(planes.shape, dtype=np.int32)
    next_id = 1
    for z in range(planes.shape[0]):
        current, n_current = _compact(planes[z])
        if n_current == 0:
            continue
        previous_ids = np.unique(output[z - 1]) if z > 0 else np.array([0])
        previous_ids = previous_ids[previous_ids != 0]
        assigned = np.zeros(n_current + 1, dtype=np.int32)
        if previous_ids.size:
            lookup = np.zeros(int(previous_ids.max()) + 1, dtype=np.int64)
            lookup[previous_ids] = np.arange(1, previous_ids.size + 1)
            previous = lookup[output[z - 1]]
            iou = _iou(current, n_current, previous, previous_ids.size)
            iou[iou < threshold] = 0.0
            iou[iou < iou.max(axis=0, keepdims=True)] = 0.0
            best = iou.argmax(axis=1)
            linked = iou.max(axis=1) > 0
            assigned[1:][linked] = previous_ids[best[linked]]
        new = np.nonzero(assigned[1:] == 0)[0] + 1
        assigned[new] = np.arange(next_id, next_id + new.size, dtype=np.int32)
        next_id += int(new.size)
        output[z] = assigned[current]
    return output


def _compact(labels: np.ndarray) -> tuple[np.ndarray, int]:
    ids = np.unique(labels)
    ids = ids[ids != 0]
    if ids.size == 0:
        return np.zeros(labels.shape, dtype=np.int64), 0
    lookup = np.zeros(int(ids.max()) + 1, dtype=np.int64)
    lookup[ids] = np.arange(1, ids.size + 1)
    return lookup[labels], int(ids.size)


def _iou(current: np.ndarray, n_current: int, previous: np.ndarray, n_previous: int) -> np.ndarray:
    """Intersection over union, rows = current outlines, columns = previous outlines."""

    width = n_previous + 1
    pairs = np.bincount((current * width + previous).ravel(), minlength=(n_current + 1) * width)
    overlap = pairs.reshape(n_current + 1, width).astype(np.float64)
    area_current = overlap.sum(axis=1, keepdims=True)
    area_previous = overlap.sum(axis=0, keepdims=True)
    union = area_current + area_previous - overlap
    with np.errstate(divide="ignore", invalid="ignore"):
        iou = np.where(union > 0, overlap / union, 0.0)
    return iou[1:, 1:]


def segment_volume(
    stack: np.ndarray,
    algorithm: str,
    parameters: dict[str, Any] | None,
    mode: str,
    *,
    stitch_threshold: float = 0.25,
    scale: str = "stack",
    pixel_size_x: float | None = None,
    pixel_size_y: float | None = None,
    pixel_size_z: float | None = None,
    min_slices: int = 1,
    details: dict[str, Any] | None = None,
) -> np.ndarray:
    """Segment one channel of a Z-stack, ``(z, y, x)``, into 3D objects.

    Size limits are applied to each object's largest cross-section, so they
    mean the same as in 2D. Objects in fewer than ``min_slices`` slices are
    removed.
    """

    from cellquant.regions import isotropic_pixel_size
    from cellquant.segmentation import _BACKENDS, available_segmentation_backends, filter_objects

    volume = np.asarray(stack)
    if volume.ndim != 3:
        raise SegmentationError("3D segmentation needs one channel shaped (z, y, x).")
    if mode not in ("stitch_slices", "full_3d"):
        raise SegmentationError(f"Unknown 3D mode '{mode}'.")
    if algorithm not in _BACKENDS:
        known = ", ".join(available_segmentation_backends()) or "none"
        raise SegmentationError(f"Unknown segmentation algorithm '{algorithm}'. Available algorithms: {known}.")
    parameters = dict(parameters or {})
    backend = _BACKENDS[algorithm]
    xy = isotropic_pixel_size(pixel_size_x, pixel_size_y)
    ratio = anisotropy(xy, pixel_size_z)
    info: dict[str, Any] = {"z_mode": mode, "z_scale_brightness": scale, "anisotropy": ratio}
    if mode == "stitch_slices":
        info["stitch_threshold"] = stitch_threshold
    if mode == "full_3d" and ratio is None:
        info["warning_z_step"] = (
            "This image has no Z step, so slices were treated as one pixel apart. "
            "Enter the Z step in step 1 for correct 3D shapes."
        )
    volume_method = getattr(backend, "segment_volume", None)
    if volume_method is not None:
        raw, engine_details = volume_method(
            volume,
            parameters,
            mode=mode,
            scale=scale,
            anisotropy=ratio or 1.0,
        )
        info["engine"] = engine_details
    elif mode == "stitch_slices":
        planes = []
        for index, plane in enumerate(volume):
            progress.update(f"Finding objects: slice {index + 1} of {len(volume)}", index, len(volume))
            planes.append(np.asarray(backend.segment(plane, parameters)))
        raw = np.stack(planes)
    else:
        raise SegmentationError(f"The '{algorithm}' method has no full 3D mode. Choose 'Link slices' instead.")
    labels = np.asarray(raw)
    if labels.shape != volume.shape:
        raise SegmentationError("Segmentation labels do not match the image shape.")
    if mode == "stitch_slices":
        progress.update("Linking outlines across slices")
        labels = stitch_slices(labels, stitch_threshold)
    labels = labels.astype(np.int32, copy=False)
    n_before = int(np.count_nonzero(np.unique(labels)))
    filtered = filter_objects(
        labels,
        parameters,
        pixel_size_x=pixel_size_x,
        pixel_size_y=pixel_size_y,
        min_slices=min_slices,
    )
    if details is not None:
        n_after = int(np.count_nonzero(np.unique(filtered)))
        details.update(info)
        if "engine" in info:
            details["engine"] = info["engine"]
        details["n_before_filter"] = n_before
        details["n_after_filter"] = n_after
        details["percent_excluded_by_size"] = float("nan") if n_before == 0 else 100.0 * (n_before - n_after) / n_before
    return filtered


def classical_volume(
    volume: np.ndarray,
    parameters: dict[str, Any],
    *,
    mode: str,
    scale: str,
    anisotropy: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Classical thresholding for a stack: per slice (to be linked) or in 3D."""

    from skimage.filters import gaussian, threshold_otsu

    from cellquant.segmentation import _parse_classical, classical_segment

    parsed = _parse_classical(parameters)
    data = np.asarray(volume, dtype=np.float64)
    sigma = parsed.sigma
    info: dict[str, Any] = {"method": "classical"}
    if mode == "stitch_slices":
        if parsed.threshold_method == "otsu" and scale == "stack":
            smoothed = np.stack([gaussian(plane, sigma=sigma, preserve_range=True) if sigma > 0 else plane for plane in data])
            threshold = _otsu(smoothed, threshold_otsu)
            info["threshold"] = threshold
            parsed = parsed.model_copy(update={"threshold_method": "manual", "threshold": threshold})
        planes = []
        for index, plane in enumerate(data):
            progress.update(f"Finding objects: slice {index + 1} of {len(data)}", index, len(data))
            planes.append(classical_segment(plane, parsed))
        return np.stack(planes), info
    # Full 3D: smoothing and distances respect the larger spacing between slices.
    progress.update("Finding objects in the whole volume")
    if sigma > 0:
        data = gaussian(data, sigma=(sigma / anisotropy, sigma, sigma), preserve_range=True)
    if parsed.threshold_method == "otsu":
        threshold = _otsu(data, threshold_otsu)
    else:
        threshold = float(parsed.threshold)  # type: ignore[arg-type]
    info["threshold"] = threshold
    mask = data > threshold
    if parsed.opening_radius_px or parsed.closing_radius_px:
        from skimage.morphology import binary_closing, binary_opening, disk

        for z in range(mask.shape[0]):
            if parsed.opening_radius_px:
                mask[z] = binary_opening(mask[z], disk(parsed.opening_radius_px))
            if parsed.closing_radius_px:
                mask[z] = binary_closing(mask[z], disk(parsed.closing_radius_px))
    if parsed.fill_holes:
        for z in range(mask.shape[0]):
            mask[z] = ndimage.binary_fill_holes(mask[z])
    if not np.any(mask):
        return np.zeros(mask.shape, dtype=np.int32), info
    if not parsed.use_watershed:
        labeled, _count = ndimage.label(mask)
        return labeled.astype(np.int32), info
    return _watershed_3d(mask, parsed.watershed_min_distance_px, parsed.watershed_compactness, anisotropy), info


def _otsu(values: np.ndarray, threshold_otsu) -> float:
    try:
        return float(threshold_otsu(values))
    except ValueError as exc:
        raise SegmentationError(
            "The source channel has uniform intensity, so a threshold could not be computed."
        ) from exc


def _watershed_3d(mask: np.ndarray, min_distance_px: float, compactness: float, ratio: float) -> np.ndarray:
    from skimage.feature import peak_local_max
    from skimage.segmentation import watershed

    distance = ndimage.distance_transform_edt(mask, sampling=(ratio, 1.0, 1.0))
    radius = max(1, int(round(min_distance_px)))
    z_radius = max(0, int(round(radius / max(ratio, 1e-6))))
    zz, yy, xx = np.ogrid[-z_radius : z_radius + 1, -radius : radius + 1, -radius : radius + 1]
    footprint = ((zz * ratio) ** 2 + yy**2 + xx**2) <= radius**2
    peaks = peak_local_max(distance, footprint=footprint, labels=ndimage.label(mask)[0], exclude_border=False)
    if len(peaks) == 0:
        labeled, _count = ndimage.label(mask)
        return labeled.astype(np.int32)
    markers = np.zeros(mask.shape, dtype=np.int32)
    markers[tuple(peaks.T)] = np.arange(1, len(peaks) + 1, dtype=np.int32)
    return np.asarray(watershed(-distance, markers, mask=mask, compactness=compactness), dtype=np.int32)


def object_z_extent(labels: np.ndarray) -> dict[int, tuple[int, int, int]]:
    """For each object in a (z, y, x) label image: (slices present, first slice, last slice), 0-based."""

    extent: dict[int, tuple[int, int, int]] = {}
    present: dict[int, list[int]] = {}
    for z in range(labels.shape[0]):
        for object_id in np.unique(labels[z]):
            if object_id:
                present.setdefault(int(object_id), []).append(z)
    for object_id, slices in present.items():
        extent[object_id] = (len(slices), min(slices), max(slices))
    return extent


def largest_cross_section(labels: np.ndarray) -> dict[int, int]:
    """Pixels in each object's largest slice, for a (z, y, x) label image."""

    largest: dict[int, int] = {}
    for z in range(labels.shape[0]):
        ids, counts = np.unique(labels[z], return_counts=True)
        for object_id, count in zip(ids.tolist(), counts.tolist()):
            if object_id and count > largest.get(object_id, 0):
                largest[object_id] = count
    return largest


# How much deeper than wide an object may be before it is flagged as two nuclei on top of each other.
DEPTH_FLAG_RATIO = 1.6


def flag_z_problems(objects, pixel_size_xy: float | None, pixel_size_z: float | None, z_planes: int):
    """Add a ``z_flag`` column and return (table, warnings) for 3D object tables.

    ``one_slice``: the object is in only one slice of a stack. With linked
    slices this is often part of a nucleus that did not link (a split).
    ``possibly_merged``: the object is more than 1.6 times as deep as the
    typical object is wide, which usually means nuclei stacked in Z were linked into
    one. Flags are for checking; they do not change counts.
    """

    import pandas as pd

    table = objects.copy()
    if table.empty or "z_slices" not in table.columns:
        return table, []
    flags = pd.Series("", index=table.index, dtype="object")
    counted = ~table["excluded"].fillna(False).astype(bool) if "excluded" in table.columns else pd.Series(True, index=table.index)
    slices = table["z_slices"].astype(float)
    if z_planes > 1:
        flags[slices <= 1] = "one_slice"
    if pixel_size_z:
        # Width from the largest cross-section; area is µm² with a pixel size, else px².
        if pixel_size_xy:
            width = np.sqrt(4.0 * table["area"].astype(float) / np.pi)
            depth = slices * float(pixel_size_z)
        else:
            # Without an XY pixel size, depth in µm cannot be compared with width in pixels.
            width = pd.Series(np.nan, index=table.index)
            depth = slices
        typical = float(np.nanmedian(width[counted])) if counted.any() else float("nan")
        if np.isfinite(typical) and typical > 0:
            flags[(depth > DEPTH_FLAG_RATIO * typical) & (slices > 2)] = "possibly_merged"
    table["z_flag"] = flags
    warnings = []
    n_one = int(((flags == "one_slice") & counted).sum())
    n_deep = int(((flags == "possibly_merged") & counted).sum())
    total = int(counted.sum())
    if n_one:
        warnings.append(
            f"{n_one} of {total} objects are in only one slice. Some may be parts of nuclei that did not link "
            "(try a lower linking threshold) or nuclei at the top or bottom of the stack."
        )
    if n_deep:
        warnings.append(
            f"{n_deep} of {total} objects are much deeper than they are wide and may be nuclei stacked in Z "
            "that were linked into one (try a higher linking threshold)."
        )
    return table, warnings
