"""Shared pipeline orchestration used by the harness, batch CLI, and plugin."""

from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from typing import Callable

import numpy as np
from scipy import ndimage

from cellquant.contracts import (
    ImageVolume,
    LabelVolume,
    PipelineCancelled,
    PipelineEvent,
    null_event_sink,
)
from cellquant.measure import MeasurementTables, measure_labels
from cellquant.postprocess import run_postprocess
from cellquant.preprocess import prepare_analysis_volume, run_preprocess
from cellquant.segment import segment


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _event(image: ImageVolume, kind: str, stage: str, **details) -> PipelineEvent:
    return PipelineEvent(
        kind,
        str(image.metadata.get("run_id", "unknown")),
        str(image.metadata.get("file_id", image.source.name)),
        stage,
        _utc(),
        details=details,
    )


def _stage(image: ImageVolume, name: str, events, operation: Callable):
    events(_event(image, "stage_started", name))
    try:
        value = operation()
    except PipelineCancelled as exc:
        events(_event(image, "cancelled", name, message=str(exc)))
        raise
    except Exception as exc:
        events(_event(image, "failed", name, exception_type=type(exc).__name__, message=str(exc)))
        raise
    events(_event(image, "stage_finished", name))
    return value


def _restore_original_grid(
    labels: LabelVolume,
    processed: ImageVolume,
    original: ImageVolume,
    cancel,
    events,
) -> LabelVolume:
    """Return labels on ``original``'s spatial grid using nearest neighbours.

    Segmentation and postprocessing operate in the preprocessed image grid.  A
    rescale there is an implementation detail: downstream persistence,
    measurement, and napari layers always receive the input image grid.
    """

    if cancel is not None:
        cancel.raise_if_cancelled()

    source_shape = tuple(int(value) for value in labels.data.shape)
    processed_shape = tuple(int(value) for value in processed.data.shape[:3])
    source_spacing = tuple(float(value) for value in labels.spacing_um)
    processed_spacing = tuple(float(value) for value in processed.spacing_um)
    if source_shape != processed_shape or source_spacing != processed_spacing:
        raise ValueError(
            "segmentation labels do not match the preprocessed image grid: "
            f"labels shape/spacing={source_shape}/{source_spacing}, "
            f"image shape/spacing={processed_shape}/{processed_spacing}"
        )

    target_shape = tuple(int(value) for value in original.data.shape[:3])
    target_spacing = tuple(float(value) for value in original.spacing_um)
    if source_shape == target_shape and source_spacing == target_spacing:
        return labels

    source_extent = np.multiply(source_shape, source_spacing)
    target_extent = np.multiply(target_shape, target_spacing)
    tolerance = np.maximum(np.asarray(source_spacing), np.asarray(target_spacing))
    if np.any(np.abs(source_extent - target_extent) > tolerance):
        raise ValueError(
            "cannot restore labels to the original grid because physical extents "
            f"differ: source={tuple(source_extent)}, target={tuple(target_extent)}"
        )

    data = labels.data
    compute = getattr(data, "compute", None)
    if callable(compute):
        started = perf_counter()
        data = np.asarray(compute())
        events(
            _event(
                original,
                "materialized",
                "restore_grid",
                reason="nearest-neighbor label regridding requires an eager array",
                shape=list(data.shape),
                dtype=str(data.dtype),
                duration_seconds=perf_counter() - started,
            )
        )
    else:
        data = np.asarray(data)

    if data.dtype != np.dtype(np.uint32):
        raise TypeError(f"label regridding requires uint32 input, received {data.dtype}")
    if cancel is not None:
        cancel.raise_if_cancelled()

    zoom = tuple(target / source for target, source in zip(target_shape, source_shape, strict=True))
    restored = ndimage.zoom(
        data,
        zoom,
        order=0,
        mode="nearest",
        prefilter=False,
    )
    if restored.shape != target_shape:
        raise RuntimeError(
            f"nearest-neighbor label regridding produced {restored.shape}, expected {target_shape}"
        )
    if cancel is not None:
        cancel.raise_if_cancelled()

    provenance = {
        **labels.provenance,
        "original_grid_regrid": {
            "source_shape_zyx": list(source_shape),
            "source_spacing_um": list(source_spacing),
            "target_shape_zyx": list(target_shape),
            "target_spacing_um": list(target_spacing),
            "interpolation": "nearest",
        },
    }
    events(
        _event(
            original,
            "warning",
            "restore_grid",
            message="labels were regridded to the original image grid",
            **provenance["original_grid_regrid"],
        )
    )
    return LabelVolume(restored.astype(np.uint32, copy=False), target_spacing, provenance)


def run_pipeline(image: ImageVolume, config, cancel, events=null_event_sink):
    """Run preprocessing, Cellpose, and postprocessing and return labels."""

    analysis = _stage(
        image,
        "analysis_grid",
        events,
        lambda: prepare_analysis_volume(image, config, cancel, events),
    )
    preprocessed = _stage(
        image,
        "preprocess",
        events,
        lambda: run_preprocess(analysis, config, cancel, events),
    )
    labels = _stage(
        image,
        "segment",
        events,
        lambda: segment(preprocessed, config, cancel, events),
    )
    postprocessed = _stage(
        image,
        "postprocess",
        events,
        lambda: run_postprocess(labels, config, cancel, events),
    )
    return _stage(
        image,
        "restore_grid",
        events,
        lambda: _restore_original_grid(postprocessed, preprocessed, analysis, cancel, events),
    )


def run_measurements(image: ImageVolume, labels, config, cancel, events=null_event_sink) -> MeasurementTables:
    analysis = _stage(
        image,
        "analysis_grid",
        events,
        lambda: prepare_analysis_volume(image, config, cancel, events),
    )
    return _stage(
        image,
        "measure",
        events,
        lambda: measure_labels(labels, analysis, config, cancel, events),
    )


__all__ = ["run_measurements", "run_pipeline"]
