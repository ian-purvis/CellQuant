"""Explicit image preprocessing for canonical ZYXC volumes."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter
from typing import Mapping

import numpy as np
from scipy import ndimage

from cellquant.contracts import CancellationToken, ImageVolume, PipelineEvent


_BOUNDARY_MODES = {"reflect", "constant", "nearest", "mirror", "wrap"}


def _emit(volume: ImageVolume, events, kind: str, **details) -> None:
    if events is None:
        return
    metadata = volume.metadata
    events(
        PipelineEvent(
            kind,
            str(metadata.get("run_id", "")),
            str(metadata.get("file_id", volume.source.name)),
            "preprocess",
            datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            details=details,
        )
    )


def _materialize(data, volume: ImageVolume | None = None, events=None, reason: str = "array operation") -> np.ndarray:
    compute = getattr(data, "compute", None)
    if not callable(compute):
        return np.asarray(data)
    started = perf_counter()
    result = np.asarray(compute())
    if volume is not None:
        _emit(
            volume,
            events,
            "materialized",
            shape=list(result.shape),
            dtype=str(result.dtype),
            reason=reason,
            duration_seconds=perf_counter() - started,
        )
    return result


def select_channel(volume: ImageVolume, index: int) -> ImageVolume:
    if not isinstance(index, int) or isinstance(index, bool):
        raise TypeError("channel index must be an integer")
    if not 0 <= index < volume.data.shape[-1]:
        raise IndexError(f"channel {index} is outside C axis of length {volume.data.shape[-1]}")
    return ImageVolume(
        volume.data[..., index : index + 1],
        volume.spacing_um,
        (volume.channel_names[index],),
        volume.source,
        {**volume.metadata, "selected_channel": index},
    )


def prepare_analysis_volume(
    volume: ImageVolume,
    config,
    cancel: CancellationToken | None = None,
    events=None,
) -> ImageVolume:
    """Construct the mode-specific multichannel ZYXC analysis grid.

    True-3D and stitched-2D modes retain the input grid.  Single-plane and
    maximum-projection modes intentionally return a singleton-Z grid; their
    labels must never be expanded back across the source stack.
    """

    raw = config.raw if hasattr(config, "raw") else config
    spec = raw["segment"]
    mode = spec["mode"]
    if cancel is not None:
        cancel.raise_if_cancelled()
    original_depth = int(volume.data.shape[0])
    provenance = {
        "mode": mode,
        "original_z_depth": original_depth,
    }
    if mode in {"volume_3d", "stitch_2d"}:
        provenance["z_selection"] = "all_planes"
        return ImageVolume(
            volume.data,
            volume.spacing_um,
            volume.channel_names,
            volume.source,
            {**volume.metadata, "analysis_volume": provenance},
        )
    if mode == "single_plane_2d":
        z_index = spec["z_index"]
        if not isinstance(z_index, int) or isinstance(z_index, bool) or z_index < 0:
            raise ValueError(
                "segment.z_index must be a non-negative integer for single_plane_2d"
            )
        if z_index >= original_depth:
            raise IndexError(
                f"segment.z_index {z_index} is outside Z axis of length {original_depth}"
            )
        data = volume.data[z_index : z_index + 1, ...]
        provenance["z_selection"] = {"kind": "single_plane", "z_index": z_index}
    elif mode == "max_projection_2d":
        eager = _materialize(
            volume.data,
            volume,
            events,
            "maximum-Z projection requires an eager multichannel array",
        )
        data = np.max(eager, axis=0, keepdims=True)
        provenance["z_selection"] = {
            "kind": "maximum_projection",
            "z_start": 0,
            "z_stop_exclusive": original_depth,
        }
    else:
        raise ValueError(f"unsupported segment mode {mode!r}")
    if cancel is not None:
        cancel.raise_if_cancelled()
    return ImageVolume(
        data,
        volume.spacing_um,
        volume.channel_names,
        volume.source,
        {**volume.metadata, "analysis_volume": provenance},
    )


def normalize(
    volume: ImageVolume,
    spec: Mapping,
    cancel: CancellationToken | None = None,
    events=None,
) -> ImageVolume:
    if cancel is not None:
        cancel.raise_if_cancelled()
    data = _materialize(
        volume.data,
        volume,
        events,
        "normalization requires an eager float32 array",
    ).astype(np.float32, copy=False)
    if not spec.get("enabled", False):
        return ImageVolume(data, volume.spacing_um, volume.channel_names, volume.source, volume.metadata)
    low = spec.get("low_percentile")
    high = spec.get("high_percentile")
    scope = spec.get("scope")
    if low is None or high is None or not 0 <= float(low) < float(high) <= 100:
        raise ValueError("normalization requires explicit 0 <= low < high <= 100 percentiles")
    if scope not in {"volume", "plane"}:
        raise ValueError("normalization scope must be 'volume' or 'plane'")
    output = np.empty_like(data, dtype=np.float32)
    for channel in range(data.shape[-1]):
        if cancel is not None:
            cancel.raise_if_cancelled()
        if scope == "volume":
            pairs = [(slice(None), np.percentile(data[..., channel], [low, high]))]
        else:
            pairs = [(z, np.percentile(data[z, ..., channel], [low, high])) for z in range(data.shape[0])]
        for selector, limits in pairs:
            if cancel is not None:
                cancel.raise_if_cancelled()
            lo, hi = (float(value) for value in limits)
            target = data[selector, ..., channel]
            if hi <= lo:
                output[selector, ..., channel] = 0
            else:
                output[selector, ..., channel] = np.clip((target - lo) / (hi - lo), 0, 1)
    return ImageVolume(output, volume.spacing_um, volume.channel_names, volume.source,
                       {**volume.metadata, "normalization": dict(spec)})


def rescale(
    volume: ImageVolume,
    spec: Mapping,
    cancel: CancellationToken | None = None,
    events=None,
) -> ImageVolume:
    if not spec.get("enabled", False):
        return volume
    if cancel is not None:
        cancel.raise_if_cancelled()
    target = spec.get("target_spacing_um")
    interpolation = spec.get("interpolation")
    antialias = spec.get("antialias")
    boundary = spec.get("boundary")
    cval = spec.get("cval")
    if not isinstance(target, (list, tuple)) or len(target) != 3 or any(float(v) <= 0 for v in target):
        raise ValueError("rescale.target_spacing_um must be positive (Z,Y,X) micrometres")
    if interpolation not in {"nearest", "linear", "cubic"}:
        raise ValueError("rescale.interpolation must be nearest, linear, or cubic")
    if not isinstance(antialias, bool):
        raise ValueError("rescale.antialias must be explicit true or false")
    if boundary not in _BOUNDARY_MODES:
        raise ValueError(f"rescale.boundary must be one of {sorted(_BOUNDARY_MODES)}")
    if cval is None or not np.isfinite(float(cval)):
        raise ValueError("rescale.cval must be an explicit finite number")
    data = _materialize(
        volume.data,
        volume,
        events,
        "spatial resampling requires an eager float32 array",
    ).astype(np.float32, copy=False)
    analysis_mode = dict(volume.metadata.get("analysis_volume", {})).get("mode")
    is_singleton_2d = analysis_mode in {"single_plane_2d", "max_projection_2d"}
    spatial_zoom = tuple(
        old / float(new) for old, new in zip(volume.spacing_um, target, strict=True)
    )
    if is_singleton_2d:
        spatial_zoom = (1.0, spatial_zoom[1], spatial_zoom[2])
    zoom = spatial_zoom + (1.0,)
    if antialias:
        sigmas = tuple(max(0.0, (1 / factor - 1) / 2) if factor < 1 else 0.0 for factor in zoom[:-1]) + (0.0,)
        if any(sigmas):
            data = ndimage.gaussian_filter(data, sigmas, mode=boundary, cval=float(cval))
    output = ndimage.zoom(
        data,
        zoom,
        order={"nearest": 0, "linear": 1, "cubic": 3}[interpolation],
        mode=boundary,
        cval=float(cval),
    )
    if cancel is not None:
        cancel.raise_if_cancelled()
    output_spacing = (
        (float(volume.spacing_um[0]), float(target[1]), float(target[2]))
        if is_singleton_2d
        else tuple(float(v) for v in target)
    )
    return ImageVolume(output.astype(np.float32, copy=False), output_spacing,
                       volume.channel_names, volume.source,
                       {**volume.metadata, "rescale": dict(spec)})


def denoise(volume: ImageVolume, spec: Mapping, cancel: CancellationToken | None = None, events=None) -> ImageVolume:
    if not spec.get("enabled", False):
        return volume
    if cancel is not None:
        cancel.raise_if_cancelled()
    method = spec.get("method")
    parameters = dict(spec.get("parameters") or {})
    boundary = spec.get("boundary")
    cval = spec.get("cval")
    if boundary not in _BOUNDARY_MODES:
        raise ValueError(f"denoise.boundary must be one of {sorted(_BOUNDARY_MODES)}")
    if cval is None or not np.isfinite(float(cval)):
        raise ValueError("denoise.cval must be an explicit finite number")
    data = _materialize(
        volume.data,
        volume,
        events,
        "denoising requires an eager float32 array",
    ).astype(np.float32, copy=False)
    if method == "gaussian":
        sigma = parameters.get("sigma")
        if sigma is None:
            raise ValueError("gaussian denoising requires parameters.sigma")
        output = ndimage.gaussian_filter(
            data,
            (float(sigma), float(sigma), float(sigma), 0),
            mode=boundary,
            cval=float(cval),
        )
    elif method == "median":
        size = parameters.get("size")
        if size is None:
            raise ValueError("median denoising requires parameters.size")
        output = ndimage.median_filter(
            data,
            (int(size), int(size), int(size), 1),
            mode=boundary,
            cval=float(cval),
        )
    else:
        raise ValueError("denoise.method must be gaussian or median")
    if cancel is not None:
        cancel.raise_if_cancelled()
    return ImageVolume(output.astype(np.float32, copy=False), volume.spacing_um, volume.channel_names,
                       volume.source, {**volume.metadata, "denoise": dict(spec)})


def run_preprocess(volume: ImageVolume, config, cancel: CancellationToken | None = None, events=None) -> ImageVolume:
    raw = config.raw if hasattr(config, "raw") else config
    spec = raw["preprocess"]
    if cancel is not None:
        cancel.raise_if_cancelled()
    selected = select_channel(volume, int(spec["channel"]))
    normalized = normalize(selected, spec["normalize"], cancel, events)
    scaled = rescale(normalized, spec["rescale"], cancel, events)
    return denoise(scaled, spec["denoise"], cancel, events)


def showcase_crop(volume: ImageVolume, config, output_dir: str | Path):
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    result = run_preprocess(volume, config)
    input_path, result_path = output / "preprocess_input.npy", output / "preprocess_output.npy"
    np.save(input_path, _materialize(volume.data))
    np.save(result_path, _materialize(result.data))
    summary = output / "preprocess_summary.json"
    summary.write_text(json.dumps({"input_shape": list(volume.data.shape), "output_shape": list(result.data.shape),
                                   "input_dtype": str(volume.data.dtype), "output_dtype": str(result.data.dtype),
                                   "spacing_um": result.spacing_um}, indent=2), encoding="utf-8")
    return {"input": input_path, "output": result_path, "summary": summary}


__all__ = [
    "denoise",
    "normalize",
    "prepare_analysis_volume",
    "rescale",
    "run_preprocess",
    "select_channel",
    "showcase_crop",
]
