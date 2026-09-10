"""Shared analysis-grid context for segmentation, classification, and review."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import numpy as np

from cellquant.contracts import AnalysisContext, ImageVolume, LabelVolume
from cellquant.preprocess import prepare_analysis_volume


TWO_DIMENSIONAL_MODES = frozenset({"single_plane_2d", "max_projection_2d"})


def labels_analysis_mode(
    labels: LabelVolume, context: AnalysisContext | None = None
) -> str | None:
    """Resolve the analysis mode behind ``labels``; an explicit context wins."""

    if context is not None:
        return context.mode
    mode = dict(labels.provenance.get("analysis_volume") or {}).get("mode")
    return mode if isinstance(mode, str) else None


def measures_area(labels: LabelVolume, context: AnalysisContext | None = None) -> bool:
    """True when the mask extent is an area (µm²) instead of a volume (µm³).

    Single-plane and maximum-projection masks carry no reconstructed object
    thickness, so their extent must ignore Z spacing. Masks without analysis
    provenance fall back to their Z extent: a singleton-Z mask is never charged
    a Z spacing it does not represent.
    """

    mode = labels_analysis_mode(labels, context)
    if mode is not None:
        return mode in TWO_DIMENSIONAL_MODES
    return int(labels.data.shape[0]) == 1


def analysis_volume_dict(context: AnalysisContext) -> dict[str, Any]:
    """Serialize the subset of context stored on volumes and label provenance."""

    return {
        "mode": context.mode,
        "original_z_depth": int(context.original_z_depth),
        "z_selection": _jsonable_z_selection(context.z_selection),
    }


def _jsonable_z_selection(value: Any) -> Any:
    if value == "all_planes":
        return "all_planes"
    if isinstance(value, Mapping):
        kind = value.get("kind")
        if kind == "single_plane":
            return {"kind": "single_plane", "z_index": int(value["z_index"])}
        if kind == "maximum_projection":
            return {
                "kind": "maximum_projection",
                "z_start": int(value.get("z_start", 0)),
                "z_stop_exclusive": int(value["z_stop_exclusive"]),
            }
    raise ValueError(f"unsupported z_selection {value!r}")


def analysis_context_from_dict(
    provenance: Mapping[str, Any],
    *,
    spacing_um: tuple[float, float, float],
    shape_zyx: tuple[int, int, int],
    source: Path | None = None,
    series: int | None = None,
    position: int | None = None,
) -> AnalysisContext:
    if not isinstance(provenance, Mapping) or not provenance:
        raise ValueError("analysis_volume provenance is required")
    mode = provenance.get("mode")
    if not isinstance(mode, str):
        raise ValueError("analysis_volume.mode must be a string")
    depth = provenance.get("original_z_depth")
    if not isinstance(depth, int) or isinstance(depth, bool) or depth < 1:
        raise ValueError("analysis_volume.original_z_depth must be a positive integer")
    z_selection = _jsonable_z_selection(provenance.get("z_selection"))
    return AnalysisContext(
        mode=mode,
        z_selection=z_selection,
        original_z_depth=depth,
        spacing_um=tuple(float(v) for v in spacing_um),
        shape_zyx=tuple(int(v) for v in shape_zyx),
        source=Path(source) if source is not None else None,
        series=series,
        position=position,
    )


def analysis_context_from_labels(
    labels: LabelVolume,
    *,
    source: Path | None = None,
    series: int | None = None,
    position: int | None = None,
) -> AnalysisContext:
    provenance = dict(labels.provenance.get("analysis_volume") or {})
    if not provenance:
        raise ValueError(
            "Selected labels have no analysis_volume provenance. "
            "Declare the measurement plane/projection explicitly, or re-segment so masks "
            "record their source grid."
        )
    return analysis_context_from_dict(
        provenance,
        spacing_um=tuple(labels.spacing_um),
        shape_zyx=tuple(int(v) for v in labels.data.shape),
        source=source,
        series=series,
        position=position,
    )


def analysis_context_from_config(
    config,
    *,
    original_z_depth: int,
    spacing_um: tuple[float, float, float],
    source: Path | None = None,
    series: int | None = None,
    position: int | None = None,
    shape_yx: tuple[int, int] | None = None,
) -> AnalysisContext:
    """Build an explicit declared context from segment settings (imported masks)."""

    raw = config.raw if hasattr(config, "raw") else config
    spec = raw["segment"]
    mode = spec["mode"]
    if mode in {"volume_3d", "stitch_2d"}:
        z_selection: Any = "all_planes"
        z_size = int(original_z_depth)
    elif mode == "single_plane_2d":
        z_index = spec["z_index"]
        if not isinstance(z_index, int) or isinstance(z_index, bool) or z_index < 0:
            raise ValueError("segment.z_index must be a non-negative integer for single_plane_2d")
        if z_index >= original_z_depth:
            raise IndexError(
                f"segment.z_index {z_index} is outside Z axis of length {original_z_depth}"
            )
        z_selection = {"kind": "single_plane", "z_index": int(z_index)}
        z_size = 1
    elif mode == "max_projection_2d":
        z_selection = {
            "kind": "maximum_projection",
            "z_start": 0,
            "z_stop_exclusive": int(original_z_depth),
        }
        z_size = 1
    else:
        raise ValueError(f"unsupported segment mode {mode!r}")
    if shape_yx is None:
        shape_zyx = (z_size, 1, 1)
    else:
        shape_zyx = (z_size, int(shape_yx[0]), int(shape_yx[1]))
    return AnalysisContext(
        mode=mode,
        z_selection=z_selection,
        original_z_depth=int(original_z_depth),
        spacing_um=tuple(float(v) for v in spacing_um),
        shape_zyx=shape_zyx,
        source=Path(source) if source is not None else None,
        series=series,
        position=position,
    )


def analysis_context_identity(context: AnalysisContext) -> dict[str, Any]:
    """Serialize the full grid identity for persistence alongside results."""

    identity: dict[str, Any] = {
        **analysis_volume_dict(context),
        "spacing_um": [float(value) for value in context.spacing_um],
        "shape_zyx": [int(value) for value in context.shape_zyx],
        "summary": analysis_context_summary(context),
    }
    if context.source is not None:
        identity["source"] = str(context.source)
    if context.series is not None:
        identity["series"] = int(context.series)
    if context.position is not None:
        identity["position"] = int(context.position)
    return identity


def analysis_context_summary(context: AnalysisContext) -> str:
    selection = context.z_selection
    if selection == "all_planes":
        plane = f"all {context.original_z_depth} planes"
    elif isinstance(selection, Mapping) and selection.get("kind") == "single_plane":
        plane = f"single plane Z={selection['z_index']}"
    elif isinstance(selection, Mapping) and selection.get("kind") == "maximum_projection":
        plane = (
            f"max projection Z={selection.get('z_start', 0)}:"
            f"{selection['z_stop_exclusive']}"
        )
    else:
        plane = repr(selection)
    shape = "×".join(str(v) for v in context.shape_zyx)
    return f"{context.mode} · {plane} · grid {shape}"


def contexts_compatible(left: AnalysisContext, right: AnalysisContext, *, rtol: float = 1e-6) -> bool:
    if left.mode != right.mode:
        return False
    if left.z_selection != right.z_selection:
        return False
    if left.original_z_depth != right.original_z_depth:
        return False
    if left.shape_zyx != right.shape_zyx:
        return False
    if not np.allclose(left.spacing_um, right.spacing_um, rtol=rtol, atol=0):
        return False
    if left.series is not None and right.series is not None and left.series != right.series:
        return False
    if left.position is not None and right.position is not None and left.position != right.position:
        return False
    if left.source is not None and right.source is not None and Path(left.source) != Path(right.source):
        return False
    return True


def assert_contexts_compatible(left: AnalysisContext, right: AnalysisContext) -> None:
    if not contexts_compatible(left, right):
        raise ValueError(
            "Analysis context mismatch between image and labels. "
            f"Labels: {analysis_context_summary(left)}. "
            f"Image/declaration: {analysis_context_summary(right)}. "
            "Re-select matching masks or redeclare the measurement grid."
        )


def _config_from_context(context: AnalysisContext) -> dict[str, Any]:
    segment: dict[str, Any] = {"mode": context.mode}
    selection = context.z_selection
    if isinstance(selection, Mapping) and selection.get("kind") == "single_plane":
        segment["z_index"] = int(selection["z_index"])
    return {"segment": segment}


def prepare_analysis_volume_for_context(
    volume: ImageVolume,
    context: AnalysisContext,
    cancel=None,
    events=None,
) -> ImageVolume:
    """Materialize the measurement grid described by ``context``."""

    if int(volume.data.shape[0]) != int(context.original_z_depth):
        raise ValueError(
            f"source Z depth {volume.data.shape[0]} does not match context "
            f"original_z_depth {context.original_z_depth}"
        )
    prepared = prepare_analysis_volume(
        volume, _config_from_context(context), cancel=cancel, events=events
    )
    prepared_shape = tuple(int(v) for v in prepared.data.shape[:3])
    # Declared contexts may use placeholder YX when only mode/Z is known.
    if context.shape_zyx[1:] != (1, 1) and prepared_shape != tuple(context.shape_zyx):
        raise ValueError(
            f"prepared analysis grid {prepared_shape} does not match context "
            f"shape {tuple(context.shape_zyx)}"
        )
    if not np.allclose(prepared.spacing_um, context.spacing_um):
        raise ValueError("prepared analysis spacing_um does not match context")
    return prepared


def resolve_measurement_image(
    image: ImageVolume,
    labels: LabelVolume,
    *,
    declared: AnalysisContext | None = None,
    cancel=None,
    events=None,
) -> tuple[ImageVolume, AnalysisContext]:
    """Resolve classification pixels from mask provenance or an explicit declaration."""

    label_shape = tuple(int(v) for v in labels.data.shape)
    if image.metadata.get("classification_analysis_grid"):
        provenance = dict(image.metadata.get("analysis_volume") or {})
        if provenance:
            context = analysis_context_from_dict(
                provenance,
                spacing_um=tuple(image.spacing_um),
                shape_zyx=tuple(int(v) for v in image.data.shape[:3]),
                source=image.source,
            )
        else:
            context = AnalysisContext(
                mode="volume_3d",
                z_selection="all_planes",
                original_z_depth=int(image.data.shape[0]),
                spacing_um=tuple(image.spacing_um),
                shape_zyx=tuple(int(v) for v in image.data.shape[:3]),
                source=image.source,
            )
        if label_shape != tuple(context.shape_zyx) or not np.allclose(
            labels.spacing_um, context.spacing_um
        ):
            raise ValueError(
                "Reopened analysis image and labels must share ZYX shape and spacing_um"
            )
        return image, context

    provenance = dict(labels.provenance.get("analysis_volume") or {})
    if provenance:
        context = analysis_context_from_dict(
            provenance,
            spacing_um=tuple(labels.spacing_um),
            shape_zyx=label_shape,
            source=image.source,
        )
        prepared = prepare_analysis_volume_for_context(image, context, cancel=cancel, events=events)
        if tuple(int(v) for v in prepared.data.shape[:3]) != label_shape:
            raise ValueError(
                f"labels shape {label_shape} does not match mask analysis grid "
                f"{prepared.data.shape[:3]}. Re-segment or choose matching labels."
            )
        if not np.allclose(prepared.spacing_um, labels.spacing_um):
            raise ValueError("labels spacing_um does not match the mask analysis grid")
        return prepared, context

    if declared is None:
        raise ValueError(
            "Imported labels lack analysis_volume provenance. "
            "Declare the measurement plane/projection explicitly before scoring."
        )
    # Fill YX from the image once declared mode/Z is known.
    filled = AnalysisContext(
        mode=declared.mode,
        z_selection=declared.z_selection,
        original_z_depth=declared.original_z_depth,
        spacing_um=tuple(declared.spacing_um),
        shape_zyx=(
            declared.shape_zyx[0],
            int(image.data.shape[1]) if declared.shape_zyx[1] == 1 else declared.shape_zyx[1],
            int(image.data.shape[2]) if declared.shape_zyx[2] == 1 else declared.shape_zyx[2],
        ),
        source=declared.source or image.source,
        series=declared.series,
        position=declared.position,
    )
    prepared = prepare_analysis_volume_for_context(image, filled, cancel=cancel, events=events)
    prepared_shape = tuple(int(v) for v in prepared.data.shape[:3])
    if prepared_shape != label_shape:
        raise ValueError(
            f"labels shape {label_shape} does not match declared analysis grid {prepared_shape}"
        )
    if not np.allclose(prepared.spacing_um, labels.spacing_um):
        raise ValueError("labels spacing_um does not match the declared analysis grid")
    return prepared, AnalysisContext(
        mode=filled.mode,
        z_selection=filled.z_selection,
        original_z_depth=filled.original_z_depth,
        spacing_um=tuple(prepared.spacing_um),
        shape_zyx=prepared_shape,
        source=filled.source,
        series=filled.series,
        position=filled.position,
    )


__all__ = [
    "TWO_DIMENSIONAL_MODES",
    "analysis_context_from_config",
    "analysis_context_from_dict",
    "analysis_context_from_labels",
    "analysis_context_identity",
    "analysis_context_summary",
    "analysis_volume_dict",
    "assert_contexts_compatible",
    "contexts_compatible",
    "labels_analysis_mode",
    "measures_area",
    "prepare_analysis_volume_for_context",
    "resolve_measurement_image",
]
