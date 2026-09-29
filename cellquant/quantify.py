"""Measurements, classifications, phenotypes, and image summaries.

These steps read an object table and a recipe. They do not rerun segmentation.
"""

from __future__ import annotations

import itertools
import math
import re

import numpy as np
import pandas as pd
from skimage.measure import regionprops

from cellquant.errors import RecipeValidationError
from cellquant.recipe import ClassificationSpec, MeasurementSpec, ReportSpec
from cellquant.regions import create_measurement_region, ring_from_background

_INTENSITY_STATS = {"mean", "median", "min", "max", "integrated"}
_AND = re.compile(r"\s+AND\s+", flags=re.IGNORECASE)


def measure_objects(
    image: np.ndarray,
    labels: np.ndarray,
    measurements: list[MeasurementSpec],
    *,
    pixel_size: float | None,
    pixel_size_z: float | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """Measure every segmented object.

    ``image`` is ``(channels, y, x)`` with 2D labels, or ``(channels, z, y,
    x)`` with 3D labels. ``centroid_x``, ``centroid_y``, and ``area`` always
    describe the segmented object. In 3D, intensities use every voxel of the
    object; ``area`` is the largest cross-section (so size limits mean the
    same as in 2D) and the table also has ``centroid_z`` (µm; blank without a
    Z step), ``volume`` (µm³, blank without a Z step; voxels when the image
    has no pixel size), ``z_slices``, ``z_first`` and ``z_last`` (slice
    numbers, 1 = first). Other columns follow each measurement definition.
    Intensity background correction is

    corrected = region statistic − background statistic

    except integrated intensity, which subtracts background mean times the
    number of pixels in the measurement region.
    """

    warnings: list[str] = []
    linear_scale = 1.0 if pixel_size is None else float(pixel_size)
    area_scale = linear_scale * linear_scale
    labels = np.asarray(labels)
    if labels.ndim == 3:
        frame = _base_table_3d(labels, pixel_size, pixel_size_z)
    else:
        frame = _base_table(labels, linear_scale, area_scale)
    # In 3D, region distances along Z are measured in XY pixels.
    z_scale = 1.0
    if labels.ndim == 3 and pixel_size_z and pixel_size:
        z_scale = float(pixel_size_z) / float(pixel_size)
    if not measurements or frame.empty:
        return frame, warnings

    region_cache: dict[str, np.ndarray] = {}
    ring_cache: dict[str, np.ndarray] = {}
    global_cache: dict[tuple[int, str], float] = {}

    for spec in measurements:
        _require_channel(image, spec.channel, f"Measurement '{spec.id}'")
        region_labels = _cached_region(
            region_cache,
            labels,
            spec,
            pixel_size,
            z_scale,
        )
        channel = np.asarray(image[spec.channel], dtype=np.float64)
        if spec.statistic == "percent_above":
            frame[spec.id] = _percent_above(
                frame["object_id"].to_numpy(),
                region_labels,
                channel,
                labels,
                spec,
                pixel_size,
                z_scale,
                ring_cache,
                global_cache,
                warnings,
            )
            continue
        values, missing = _statistic_by_object(
            frame["object_id"].to_numpy(),
            region_labels,
            channel,
            spec.statistic,
            linear_scale,
            area_scale,
        )
        if missing:
            warnings.append(
                f"Measurement '{spec.id}' has no pixels for object"
                + ("s " if len(missing) > 1 else " ")
                + _id_list(missing)
                + "."
            )
        if spec.background.type == "global":
            background = _global_background(
                global_cache,
                channel,
                labels,
                spec,
                warnings,
            )
            values = _apply_scalar_background(
                values,
                background,
                spec.statistic,
                region_labels,
                frame["object_id"].to_numpy(),
            )
        elif spec.background.type == "local_ring":
            ring = _cached_ring(ring_cache, labels, spec, pixel_size, z_scale)
            background_values, ring_missing = _statistic_by_object(
                frame["object_id"].to_numpy(),
                ring,
                channel,
                "mean" if spec.statistic == "integrated" else spec.statistic,
                linear_scale,
                area_scale,
            )
            if ring_missing:
                warnings.append(
                    f"Measurement '{spec.id}' has no local-ring pixels for object"
                    + ("s " if len(ring_missing) > 1 else " ")
                    + _id_list(ring_missing)
                    + "."
                )
            if spec.statistic == "integrated":
                counts = _pixel_counts(region_labels, frame["object_id"].to_numpy())
                values = values - background_values * counts
            else:
                values = values - background_values
        frame[spec.id] = values
    return frame, warnings


def classify_objects(
    table: pd.DataFrame,
    classifications: list[ClassificationSpec],
) -> pd.DataFrame:
    """Add one boolean column per classification. Equality is negative,
    except with ``comparison: at_least`` (equality is positive).

    An object with no measurement value is never called negative. Its
    classification stays missing and the ``unmeasured`` column is True. Such
    objects stay in the table but are left out of every count in
    ``summarize_image``. Changing a threshold only requires this step.
    """

    output = table.copy()
    for spec in classifications:
        if spec.measurement not in output.columns:
            raise RecipeValidationError(
                f"Classification '{spec.id}' references unknown measurement "
                f"'{spec.measurement}'."
            )
        values = output[spec.measurement].to_numpy(dtype=float)
        column = pd.Series(pd.NA, index=output.index, dtype="boolean")
        finite = np.isfinite(values)
        column.loc[finite] = _positive(values[finite], spec.threshold, spec.comparison)
        output[spec.id] = column
    ids = [spec.id for spec in classifications]
    output["unmeasured"] = _unmeasured_mask(output, ids) if ids else np.zeros(len(output), dtype=bool)
    return output


def count_unmeasured(table: pd.DataFrame) -> int:
    """Objects that could not be measured. Objects the user deleted are not counted."""

    if table.empty or "unmeasured" not in table.columns:
        return 0
    flagged = table["unmeasured"].fillna(False).astype(bool).to_numpy()
    if "excluded" in table.columns:
        flagged = flagged & ~table["excluded"].fillna(False).astype(bool).to_numpy()
    return int(flagged.sum())


def _positive(values: np.ndarray, threshold: float, comparison: str = "above") -> np.ndarray:
    if comparison == "at_least":
        return values >= threshold
    return values > threshold


def classification_counts(values: np.ndarray, threshold: float, comparison: str = "above") -> dict[str, float]:
    """Counts used by an interactive threshold control."""

    array = np.asarray(values, dtype=float)
    finite = np.isfinite(array)
    positive = _positive(array, threshold, comparison) & finite
    negative = ~_positive(array, threshold, comparison) & finite
    n_positive = int(positive.sum())
    n_negative = int(negative.sum())
    classified = n_positive + n_negative
    percent = float("nan") if classified == 0 else 100.0 * n_positive / classified
    return {
        "positive": n_positive,
        "negative": n_negative,
        "percent_positive": percent,
        "n_missing": int((~finite).sum()),
    }


def generate_phenotypes(
    table: pd.DataFrame,
    classifications: list[ClassificationSpec],
) -> pd.DataFrame:
    """Add a mutually exclusive phenotype string for every object.

    Names are joined with ``|``, for example ``A+|B-|C+``. A missing
    classification is marked ``?``.
    """

    output = table.copy()
    if not classifications:
        output["phenotype"] = ""
        return output
    pieces = []
    for spec in classifications:
        flags = output[spec.id]
        rendered = []
        for value in flags.tolist():
            if value is pd.NA or value is None or (isinstance(value, float) and math.isnan(value)):
                rendered.append(f"{spec.name}?")
            elif bool(value):
                rendered.append(f"{spec.name}+")
            else:
                rendered.append(f"{spec.name}-")
        pieces.append(pd.Series(rendered, index=output.index, dtype="string"))
    phenotype = pieces[0]
    for piece in pieces[1:]:
        phenotype = phenotype + "|" + piece
    output["phenotype"] = phenotype
    return output


def summarize_image(
    table: pd.DataFrame,
    reports: list[ReportSpec],
    classifications: list[ClassificationSpec],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[str]]:
    """Summaries computed only from the object table.

    Objects the user deleted, and objects that could not be measured for any
    classification, are left out of every count and denominator. The number
    left out for lack of a measurement is reported as ``n_unmeasured``.

    Returns the requested report table, mutually exclusive phenotype counts,
    and positive-combination counts.
    """

    warnings: list[str] = []
    table, n_unmeasured = _counted_objects(table, classifications)
    if n_unmeasured:
        warnings.append(
            f"{n_unmeasured} object{'s' if n_unmeasured != 1 else ''} could not be measured "
            f"and {'were' if n_unmeasured != 1 else 'was'} left out of all counts."
        )
    report_rows = []
    for index, report in enumerate(reports, start=1):
        numerator = _expression_mask(table, report.numerator, classifications)
        denominator = _expression_mask(table, report.denominator, classifications)
        denominator_count = int(denominator.sum())
        count = int((numerator & denominator).sum())
        percent = float("nan") if denominator_count == 0 else 100.0 * count / denominator_count
        report_rows.append(
            {
                "report": f"report_{index}",
                "numerator": report.numerator,
                "denominator": report.denominator,
                "count": count,
                "denominator_count": denominator_count,
                "n_unmeasured": n_unmeasured,
                "percent": percent,
            }
        )
    exclusive, combination = _phenotype_tables(table, classifications, warnings)
    return pd.DataFrame(report_rows), exclusive, combination, warnings


def image_summary_row(
    *,
    sample_name: str,
    filename: str,
    image_id: str,
    n_objects: int,
    qc_status: str,
    reports: pd.DataFrame,
    metadata: dict | None = None,
    n_unmeasured: int = 0,
) -> pd.DataFrame:
    row: dict[str, object] = {
        "sample_name": sample_name,
        "filename": filename,
        "image_id": image_id,
        "total_objects": n_objects,
        "n_unmeasured": n_unmeasured,
        "qc_status": qc_status,
    }
    for key, value in (metadata or {}).items():
        row[str(key)] = value
    for record in reports.to_dict(orient="records"):
        key = str(record["report"])
        row[f"{key}_count"] = record["count"]
        row[f"{key}_percent"] = record["percent"]
        row[f"{key}_denominator"] = record["denominator_count"]
        row[f"{key}_numerator"] = record["numerator"]
        row[f"{key}_denominator_expression"] = record["denominator"]
    return pd.DataFrame([row])


def _counted_objects(
    table: pd.DataFrame,
    classifications: list[ClassificationSpec],
) -> tuple[pd.DataFrame, int]:
    """Objects that count, and how many were left out for lack of a measurement.

    Deleted objects are dropped without being counted as unmeasured.
    """

    if table.empty:
        return table, 0
    keep = np.ones(len(table), dtype=bool)
    if "excluded" in table.columns:
        keep &= ~table["excluded"].fillna(False).astype(bool).to_numpy()
    columns = [spec.id for spec in classifications if spec.id in table.columns]
    unmeasured = _unmeasured_mask(table, columns) if columns else np.zeros(len(table), dtype=bool)
    n_unmeasured = int((keep & unmeasured).sum())
    return table.loc[keep & ~unmeasured].reset_index(drop=True), n_unmeasured


def _base_table(labels: np.ndarray, linear_scale: float, area_scale: float) -> pd.DataFrame:
    rows = []
    for prop in regionprops(np.asarray(labels)):
        centroid_y, centroid_x = prop.centroid
        rows.append(
            {
                "object_id": int(prop.label),
                "centroid_x": float(centroid_x) * linear_scale,
                "centroid_y": float(centroid_y) * linear_scale,
                "area": float(prop.area) * area_scale,
            }
        )
    frame = pd.DataFrame(rows, columns=["object_id", "centroid_x", "centroid_y", "area"])
    if not frame.empty:
        frame = frame.sort_values("object_id").reset_index(drop=True)
    return frame


def _base_table_3d(labels: np.ndarray, pixel_size: float | None, pixel_size_z: float | None) -> pd.DataFrame:
    from cellquant.volume import largest_cross_section, object_z_extent

    columns = ["object_id", "centroid_x", "centroid_y", "centroid_z", "area", "volume", "z_slices", "z_first", "z_last"]
    linear = 1.0 if pixel_size is None else float(pixel_size)
    largest = largest_cross_section(labels)
    extent = object_z_extent(labels)
    rows = []
    for prop in regionprops(labels):
        object_id = int(prop.label)
        centroid_z, centroid_y, centroid_x = prop.centroid
        if pixel_size is None:
            volume = float(prop.area)  # voxels: the image has no pixel size
        elif pixel_size_z:
            volume = float(prop.area) * linear * linear * float(pixel_size_z)
        else:
            volume = float("nan")
        count, first, last = extent[object_id]
        rows.append(
            {
                "object_id": object_id,
                "centroid_x": float(centroid_x) * linear,
                "centroid_y": float(centroid_y) * linear,
                "centroid_z": float(centroid_z) * float(pixel_size_z) if pixel_size_z else float("nan"),
                "area": float(largest[object_id]) * linear * linear,
                "volume": volume,
                "z_slices": int(count),
                "z_first": int(first) + 1,
                "z_last": int(last) + 1,
            }
        )
    frame = pd.DataFrame(rows, columns=columns)
    if not frame.empty:
        frame = frame.sort_values("object_id").reset_index(drop=True)
    return frame


def _cached_region(cache, labels, spec: MeasurementSpec, pixel_size: float | None, z_scale: float = 1.0) -> np.ndarray:
    key = spec.region.model_dump_json()
    if key not in cache:
        cache[key] = create_measurement_region(labels, spec.region, pixel_size=pixel_size, z_scale=z_scale)
    return cache[key]


def _cached_ring(cache, labels, spec: MeasurementSpec, pixel_size: float | None, z_scale: float = 1.0) -> np.ndarray:
    key = spec.background.model_dump_json()
    if key not in cache:
        cache[key] = ring_from_background(labels, spec.background, pixel_size=pixel_size, z_scale=z_scale)
    return cache[key]


def _global_background(cache, channel, labels, spec: MeasurementSpec, warnings: list[str]) -> float:
    if spec.background.value is not None:
        return float(spec.background.value)
    key = (spec.channel, "median_outside")
    if key in cache:
        return cache[key]
    outside = labels == 0
    if np.any(outside):
        value = float(np.median(channel[outside]))
    else:
        value = float(np.percentile(channel, 10))
        warnings.append(
            f"Measurement '{spec.id}' has no background pixels, so global background "
            "uses the 10th percentile of the channel."
        )
    cache[key] = value
    return value


def _apply_scalar_background(values, background, statistic, region_labels, object_ids):
    if statistic == "integrated":
        counts = _pixel_counts(region_labels, object_ids)
        return values - background * counts
    return values - background


def _statistic_by_object(
    object_ids: np.ndarray,
    region_labels: np.ndarray,
    channel: np.ndarray,
    statistic: str,
    linear_scale: float,
    area_scale: float,
) -> tuple[np.ndarray, list[int]]:
    if statistic in _INTENSITY_STATS or statistic == "std":
        properties = {
            int(prop.label): prop
            for prop in regionprops(region_labels, intensity_image=channel)
        }
    else:
        properties = {int(prop.label): prop for prop in regionprops(region_labels)}
    values = np.empty(len(object_ids), dtype=np.float64)
    missing: list[int] = []
    for index, object_id in enumerate(object_ids):
        prop = properties.get(int(object_id))
        if prop is None:
            values[index] = np.nan
            missing.append(int(object_id))
            continue
        values[index] = _property_statistic(prop, statistic, linear_scale, area_scale)
    return values, missing


def _property_statistic(prop, statistic: str, linear_scale: float, area_scale: float) -> float:
    if statistic in ("area", "equivalent_diameter"):
        # 3D: the largest cross-section, so the value means the same as in 2D.
        pixels = float(prop.area) if prop.image.ndim == 2 else float(prop.image.sum(axis=(1, 2)).max())
        if statistic == "area":
            return pixels * area_scale
        return float(math.sqrt(4.0 * pixels * area_scale / math.pi))
    if statistic == "centroid_x":
        return float(prop.centroid[-1]) * linear_scale
    if statistic == "centroid_y":
        return float(prop.centroid[-2]) * linear_scale
    intensity = getattr(prop, "image_intensity", None)
    if intensity is None:
        intensity = prop.intensity_image
    pixels = intensity[prop.image]
    if statistic == "mean":
        return float(prop.intensity_mean)
    if statistic == "median":
        return float(np.median(pixels))
    if statistic == "min":
        return float(prop.intensity_min)
    if statistic == "max":
        return float(prop.intensity_max)
    if statistic == "std":
        return float(np.std(pixels))
    if statistic == "integrated":
        return float(np.sum(pixels))
    raise RecipeValidationError(f"Unknown measurement statistic '{statistic}'.")


def _percent_above(
    object_ids: np.ndarray,
    region_labels: np.ndarray,
    channel: np.ndarray,
    labels: np.ndarray,
    spec: MeasurementSpec,
    pixel_size: float | None,
    z_scale: float,
    ring_cache: dict,
    global_cache: dict,
    warnings: list[str],
) -> np.ndarray:
    """Percent (0-100) of each object's region pixels that pass the pixel level.

    A pixel passes when ``value - background >= pixel_level`` and, if set,
    ``value - background <= pixel_level_high``. Background is 0 (none), the
    global value, or the median of the object's local ring. The percent is
    ``100 * passing / pixels`` computed as one division of whole numbers, so a
    classification "at least N%" is exact at the boundary. Objects with no
    region pixels, any non-finite pixel, or no ring pixels are left blank.
    """

    n = len(object_ids)
    result = np.full(n, np.nan, dtype=np.float64)
    if n == 0:
        return result
    order = np.argsort(object_ids)
    sorted_ids = np.asarray(object_ids)[order]
    flat_regions = region_labels.ravel()
    positions = np.flatnonzero(flat_regions)
    region_ids = flat_regions[positions]
    slots = np.searchsorted(sorted_ids, region_ids)
    slots = np.clip(slots, 0, n - 1)
    known = sorted_ids[slots] == region_ids
    positions, slots = positions[known], order[slots[known]]
    values = channel.ravel()[positions]

    background = np.zeros(n, dtype=np.float64)
    if spec.background.type == "global":
        background[:] = _global_background(global_cache, channel, labels, spec, warnings)
    elif spec.background.type == "local_ring":
        ring = _cached_ring(ring_cache, labels, spec, pixel_size, z_scale)
        background, ring_missing = _statistic_by_object(object_ids, ring, channel, "median", 1.0, 1.0)
        if ring_missing:
            warnings.append(
                f"Measurement '{spec.id}' has no local-ring pixels for object"
                + ("s " if len(ring_missing) > 1 else " ")
                + _id_list(ring_missing)
                + "."
            )
    corrected = values - background[slots]
    finite = np.isfinite(corrected)
    passing = finite & (corrected >= float(spec.pixel_level))
    if spec.pixel_level_high is not None:
        passing &= corrected <= float(spec.pixel_level_high)
    counts = np.bincount(slots, minlength=n).astype(np.int64)
    good = np.bincount(slots, weights=finite, minlength=n).astype(np.int64)
    positive = np.bincount(slots[passing], minlength=n).astype(np.int64)
    valid = (counts > 0) & (good == counts) & np.isfinite(background)
    result[valid] = (100 * positive[valid]) / counts[valid]
    missing = [int(object_ids[index]) for index in np.flatnonzero(counts == 0)]
    if missing:
        warnings.append(
            f"Measurement '{spec.id}' has no pixels for object"
            + ("s " if len(missing) > 1 else " ")
            + _id_list(missing)
            + "."
        )
    return result


def _pixel_counts(region_labels: np.ndarray, object_ids: np.ndarray) -> np.ndarray:
    counts = np.zeros(len(object_ids), dtype=np.float64)
    present = {int(value): count for value, count in zip(*np.unique(region_labels, return_counts=True))}
    for index, object_id in enumerate(object_ids):
        counts[index] = float(present.get(int(object_id), 0))
    return counts


def _phenotype_tables(
    table: pd.DataFrame,
    classifications: list[ClassificationSpec],
    warnings: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    n_objects = len(table)
    if not classifications:
        empty = pd.DataFrame(columns=["phenotype", "count", "percent_of_objects"])
        return empty, empty.copy()
    if len(classifications) > 6:
        warnings.append(
            "More than 6 classifications were defined, so exhaustive phenotype "
            "combinations were not expanded. Requested reports were still calculated."
        )
        return (
            pd.DataFrame(columns=["phenotype", "count", "percent_of_objects"]),
            pd.DataFrame(columns=["phenotype", "count", "percent_of_objects"]),
        )
    exclusive_rows = []
    states = {spec.id: _states(table[spec.id]) for spec in classifications}
    for signs in itertools.product((1, -1), repeat=len(classifications)):
        mask = np.ones(n_objects, dtype=bool)
        name_parts = []
        for spec, sign in zip(classifications, signs, strict=True):
            mask &= states[spec.id] == sign
            name_parts.append(f"{spec.name}{'+' if sign == 1 else '-'}")
        count = int(mask.sum())
        exclusive_rows.append(
            {
                "phenotype": "|".join(name_parts),
                "count": count,
                "percent_of_objects": _percent(count, n_objects),
            }
        )
    combination_rows = []
    for size in range(1, len(classifications) + 1):
        for subset in itertools.combinations(classifications, size):
            mask = np.ones(n_objects, dtype=bool)
            for spec in subset:
                mask &= states[spec.id] == 1
            count = int(mask.sum())
            combination_rows.append(
                {
                    "phenotype": "|".join(f"{spec.name}+" for spec in subset),
                    "count": count,
                    "percent_of_objects": _percent(count, n_objects),
                }
            )
    return pd.DataFrame(exclusive_rows), pd.DataFrame(combination_rows)


def _expression_mask(
    table: pd.DataFrame,
    expression: str,
    classifications: list[ClassificationSpec],
) -> np.ndarray:
    text = expression.strip()
    if text.casefold() in {"all_objects", "all_measured_objects"}:
        return np.ones(len(table), dtype=bool)
    if not text:
        raise RecipeValidationError("A report expression must not be empty.")
    mask = np.ones(len(table), dtype=bool)
    for token in _AND.split(text):
        negated, column = _token_column(token.strip(), classifications)
        if column not in table.columns:
            raise RecipeValidationError(
                f"Report expression references unknown classification '{token.strip()}'."
            )
        state = _states(table[column])
        mask &= state == (-1 if negated else 1)
    return mask


def _token_column(token: str, classifications: list[ClassificationSpec]) -> tuple[bool, str]:
    negated = token.casefold().startswith("not ")
    name = token.split(None, 1)[1].strip() if negated else token
    return negated, _resolve_classification(name, classifications)


def _unmeasured_mask(table: pd.DataFrame, columns: list[str]) -> np.ndarray:
    mask = np.zeros(len(table), dtype=bool)
    for column in columns:
        if column in table.columns:
            mask |= _states(table[column]) == 0
    return mask


def _states(series: pd.Series) -> np.ndarray:
    """1 positive, -1 negative, 0 unmeasured. Missing values are not negative."""

    output = np.empty(len(series), dtype=np.int8)
    for index, value in enumerate(series.tolist()):
        if _is_missing(value):
            output[index] = 0
        elif bool(value):
            output[index] = 1
        else:
            output[index] = -1
    return output


def _is_missing(value: object) -> bool:
    if value is pd.NA or value is None:
        return True
    if isinstance(value, str) and value.strip().casefold() in {"", "nan", "none", "<na>", "unmeasured"}:
        return True
    try:
        return bool(isinstance(value, float) and math.isnan(value))
    except TypeError:
        return False


def _resolve_classification(token: str, classifications: list[ClassificationSpec]) -> str:
    by_id = {item.id: item.id for item in classifications}
    by_name = [item.id for item in classifications if item.name == token]
    found = by_id.get(token)
    if found is not None and by_name and found not in by_name:
        raise RecipeValidationError(
            f"Report token '{token}' matches both a classification id and a different name."
        )
    if found is not None:
        return found
    if len(by_name) == 1:
        return by_name[0]
    raise RecipeValidationError(f"Unknown classification '{token}' in a report.")


def _percent(count: int, total: int) -> float:
    if total == 0:
        return float("nan")
    return 100.0 * count / total


def _require_channel(image: np.ndarray, index: int, label: str) -> None:
    if index < 0 or index >= image.shape[0]:
        raise RecipeValidationError(
            f"{label} uses channel index {index}, but the image has {image.shape[0]} channels."
        )


def _id_list(object_ids: list[int]) -> str:
    shown = object_ids[:8]
    text = ", ".join(str(item) for item in shown)
    if len(object_ids) > len(shown):
        return text + ", ..."
    return text
