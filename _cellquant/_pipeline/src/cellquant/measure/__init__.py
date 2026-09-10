"""Tidy physical morphometrics and per-channel intensity measurements."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd

from cellquant.analysis import analysis_context_identity, measures_area
from cellquant.contracts import AnalysisContext, ImageVolume, LabelVolume, PipelineEvent


# 2 added ``area_um2`` and stopped reporting a Z-spacing-dependent ``volume_um3``
# for single-plane / maximum-projection masks.
MEASURE_SCHEMA_VERSION = 2

OBJECT_COLUMNS = [
    "label", "voxel_count", "area_um2", "volume_um3",
    "centroid_z_px", "centroid_y_px", "centroid_x_px",
    "centroid_z_um", "centroid_y_um", "centroid_x_um", "bbox_z0_px", "bbox_y0_px",
    "bbox_x0_px", "bbox_z1_px", "bbox_y1_px", "bbox_x1_px",
    "bbox_z0_um", "bbox_y0_um", "bbox_x0_um", "bbox_z1_um", "bbox_y1_um", "bbox_x1_um",
]
INTENSITY_COLUMNS = [
    "label", "channel", "voxel_count", "mean", "median", "min", "max", "integrated_intensity",
]


@dataclass(frozen=True)
class MeasurementTables:
    objects: pd.DataFrame
    intensities: pd.DataFrame


_STATISTICS = ("mean", "median", "min", "max", "integrated_intensity")


def _measure_spec(config) -> Mapping:
    if config is None:
        return {}
    raw = getattr(config, "raw", config)
    if not isinstance(raw, Mapping):
        raise TypeError("measurement config must be a mapping or RunConfig")
    value = raw.get("measure", raw)
    if not isinstance(value, Mapping):
        raise TypeError("measure config section must be a mapping")
    return value


def _selection(config, image: ImageVolume) -> tuple[list[int], tuple[str, ...]]:
    spec = _measure_spec(config)
    requested_channels = spec.get("channels", "all")
    if requested_channels == "all" or requested_channels is None:
        channels = list(range(len(image.channel_names)))
    else:
        if isinstance(requested_channels, (str, int)):
            requested_channels = [requested_channels]
        channels = []
        for value in requested_channels:
            if isinstance(value, (int, np.integer)):
                index = int(value)
                if index < 0 or index >= len(image.channel_names):
                    raise ValueError(f"measurement channel index {index} is out of range")
            else:
                try:
                    index = image.channel_names.index(str(value))
                except ValueError as exc:
                    raise ValueError(f"unknown measurement channel {value!r}") from exc
            if index not in channels:
                channels.append(index)
    requested_statistics = spec.get("intensity_statistics", _STATISTICS)
    if isinstance(requested_statistics, str):
        requested_statistics = [requested_statistics]
    statistics = tuple(str(value) for value in requested_statistics)
    unsupported = set(statistics) - set(_STATISTICS)
    if unsupported:
        raise ValueError(f"unsupported intensity statistic(s): {sorted(unsupported)}")
    return channels, statistics


def _warning(image: ImageVolume, events, message: str) -> None:
    if events is None:
        return
    metadata = image.metadata
    events(PipelineEvent(
        "warning",
        str(metadata.get("run_id", "")),
        str(metadata.get("file_id", image.source.name)),
        "measure",
        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        details={"message": message},
    ))


def _materialize(value, *, role: str, image: ImageVolume, events):
    compute = getattr(value, "compute", None)
    if not callable(compute):
        return np.asarray(value)
    if events is not None:
        metadata = image.metadata
        events(PipelineEvent(
            "materialized",
            str(metadata.get("run_id", "")),
            str(metadata.get("file_id", image.source.name)),
            "measure",
            datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            details={
                "shape": [int(size) for size in value.shape],
                "dtype": np.dtype(value.dtype).name,
                "reason": f"measurement requires eager NumPy access to {role} data",
            },
        ))
    # Report the boundary before calling compute. np.asarray only normalizes
    # the eager result and therefore cannot trigger a second computation.
    return np.asarray(compute())


def _validate(labels: LabelVolume, image: ImageVolume, events=None):
    label_data = _materialize(labels.data, role="label", image=image, events=events)
    image_data = _materialize(image.data, role="image", image=image, events=events)
    if image_data.shape[:3] != label_data.shape:
        raise ValueError(f"image ZYX {image_data.shape[:3]} does not match labels {label_data.shape}")
    if image.spacing_um != labels.spacing_um:
        raise ValueError("image and label spacing_um must match")
    return label_data, image_data


def measure_labels(
    labels: LabelVolume,
    image: ImageVolume,
    config=None,
    cancel=None,
    events=None,
    *,
    context: AnalysisContext | None = None,
) -> MeasurementTables:
    """Measure per-object extent, position, and per-channel intensity.

    The reported extent depends on the analysis grid the mask came from
    (``context`` when given, otherwise ``labels.provenance['analysis_volume']``).
    Single-plane and maximum-projection masks have no object thickness, so they
    report ``area_um2`` from Y/X spacing only and leave ``volume_um3`` empty;
    volumetric and stitched masks report ``volume_um3`` and leave ``area_um2``
    empty. See ``MEASURE_SCHEMA_VERSION`` for the objects-table schema.
    """

    label_data, image_data = _validate(labels, image, events)
    two_dimensional = measures_area(labels, context)
    channels, statistics = _selection(config, image)
    intensity_columns = ["label", "channel", "voxel_count", *statistics]
    ids = np.unique(label_data)
    ids = ids[ids != 0]
    if ids.size == 0:
        _warning(image, events, "No nonzero labels; wrote header-only measurement tables")
        empty_objects = pd.DataFrame(columns=OBJECT_COLUMNS)
        empty_intensities = pd.DataFrame(columns=intensity_columns)
        for table in (empty_objects, empty_intensities):
            table.attrs["schema_version"] = MEASURE_SCHEMA_VERSION
            _record_analysis_context(table, context)
        empty_objects.attrs["extent_metric"] = "area_um2" if two_dimensional else "volume_um3"
        return MeasurementTables(empty_objects, empty_intensities)
    if cancel is not None:
        cancel.raise_if_cancelled()
    spacing = np.asarray(labels.spacing_um, dtype=float)
    flat_labels = label_data.ravel()
    foreground = flat_labels != 0
    foreground_labels = flat_labels[foreground]
    groups = np.searchsorted(ids, foreground_labels)
    counts = np.bincount(groups, minlength=ids.size).astype(np.int64)
    flat_positions = np.flatnonzero(foreground)
    coordinates = np.unravel_index(flat_positions, label_data.shape)
    coordinate_sums = [np.bincount(groups, weights=axis, minlength=ids.size) for axis in coordinates]
    centroids = np.column_stack(coordinate_sums) / counts[:, None]
    lower = np.full((ids.size, 3), np.iinfo(np.int64).max, dtype=np.int64)
    upper = np.zeros((ids.size, 3), dtype=np.int64)
    for axis, values in enumerate(coordinates):
        np.minimum.at(lower[:, axis], groups, values)
        np.maximum.at(upper[:, axis], groups, values)
    upper += 1
    empty_extent = np.full(ids.size, np.nan)
    if two_dimensional:
        # A single plane or projection has no thickness: Z spacing must not enter.
        area_um2 = counts * float(spacing[1] * spacing[2])
        volume_um3 = empty_extent
    else:
        area_um2 = empty_extent
        volume_um3 = counts * float(np.prod(spacing))
    objects = pd.DataFrame({
        "label": ids.astype(np.uint32), "voxel_count": counts,
        "area_um2": area_um2, "volume_um3": volume_um3,
        "centroid_z_px": centroids[:, 0], "centroid_y_px": centroids[:, 1],
        "centroid_x_px": centroids[:, 2], "centroid_z_um": centroids[:, 0] * spacing[0],
        "centroid_y_um": centroids[:, 1] * spacing[1], "centroid_x_um": centroids[:, 2] * spacing[2],
        "bbox_z0_px": lower[:, 0], "bbox_y0_px": lower[:, 1], "bbox_x0_px": lower[:, 2],
        "bbox_z1_px": upper[:, 0], "bbox_y1_px": upper[:, 1], "bbox_x1_px": upper[:, 2],
        "bbox_z0_um": lower[:, 0] * spacing[0], "bbox_y0_um": lower[:, 1] * spacing[1],
        "bbox_x0_um": lower[:, 2] * spacing[2], "bbox_z1_um": upper[:, 0] * spacing[0],
        "bbox_y1_um": upper[:, 1] * spacing[1], "bbox_x1_um": upper[:, 2] * spacing[2],
    }, columns=OBJECT_COLUMNS)
    order = np.argsort(groups, kind="stable")
    boundaries = np.concatenate(([0], np.cumsum(counts)))
    intensity_frames = []
    for channel in channels:
        name = image.channel_names[channel]
        if cancel is not None:
            cancel.raise_if_cancelled()
        values = image_data[..., channel].ravel()[foreground].astype(np.float64, copy=False)
        totals = np.bincount(groups, weights=values, minlength=ids.size)
        minima = np.full(ids.size, np.inf)
        maxima = np.full(ids.size, -np.inf)
        np.minimum.at(minima, groups, values)
        np.maximum.at(maxima, groups, values)
        ordered_values = values[order]
        medians = np.asarray([
            np.median(ordered_values[boundaries[index] : boundaries[index + 1]])
            for index in range(ids.size)
        ])
        values_by_statistic = {
            "mean": totals / counts,
            "median": medians,
            "min": minima,
            "max": maxima,
            "integrated_intensity": totals,
        }
        frame = {
            "label": ids.astype(np.uint32), "channel": name, "voxel_count": counts,
            **{statistic: values_by_statistic[statistic] for statistic in statistics},
        }
        intensity_frames.append(pd.DataFrame(frame, columns=intensity_columns))
    intensities = (
        pd.concat(intensity_frames, ignore_index=True)
        if intensity_frames else pd.DataFrame(columns=intensity_columns)
    )
    objects.attrs["schema_version"] = MEASURE_SCHEMA_VERSION
    objects.attrs["extent_metric"] = "area_um2" if two_dimensional else "volume_um3"
    objects.attrs["units"] = {
        "area_um2": "um^2", "volume_um3": "um^3", "centroid_*_um": "um", "bbox_*_um": "um",
        "bbox_*_px": "pixel index",
    }
    intensities.attrs["schema_version"] = MEASURE_SCHEMA_VERSION
    intensities.attrs["units"] = {"intensity": "source intensity units"}
    for table in (objects, intensities):
        _record_analysis_context(table, context)
    return MeasurementTables(objects, intensities)


def _record_analysis_context(table: pd.DataFrame, context: AnalysisContext | None) -> None:
    """Name the measured grid on the table so results identify their own pixels."""

    if context is not None:
        table.attrs["analysis_context"] = analysis_context_identity(context)


def write_measurements(tables: MeasurementTables, directory: str | Path) -> list[Path]:
    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    paths = [output / "objects.csv", output / "intensities.csv"]
    tables.objects.to_csv(paths[0], index=False)
    tables.intensities.to_csv(paths[1], index=False)
    return paths


def showcase_crop(value, config, output_dir: str | Path):
    image, labels = value
    return write_measurements(measure_labels(labels, image, config), output_dir)


__all__ = [
    "MEASURE_SCHEMA_VERSION",
    "MeasurementTables",
    "measure_labels",
    "showcase_crop",
    "write_measurements",
]
