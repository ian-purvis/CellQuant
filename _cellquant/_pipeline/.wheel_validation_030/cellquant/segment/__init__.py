"""Strict Cellpose-SAM v4 adapter shared by CLI and napari."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import importlib.metadata
from pathlib import Path
import random
import sys
from time import perf_counter
from typing import Any, Mapping

import numpy as np

from cellquant.contracts import CancellationToken, ImageVolume, LabelVolume, PipelineEvent


def _sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass
class Segmenter:
    model: Any
    model_sha256: str
    device: str
    cellpose_version: str
    constructor_parameters: Mapping[str, Any] = field(default_factory=dict)


def _emit(events, kind: str, *, volume: ImageVolume | None = None, **details) -> None:
    if events is None:
        return
    metadata = volume.metadata if volume is not None else {}
    file_id = str(metadata.get("file_id", volume.source.name if volume is not None else ""))
    events(
        PipelineEvent(
            kind,
            str(metadata.get("run_id", "")),
            file_id,
            "segment",
            datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            details=details,
        )
    )


def load_model(spec: Mapping, device: str | None = None, events=None) -> Segmenter:
    """Load Cellpose v4 and verify the actual weight file before inference."""

    from cellpose import models
    import torch

    version = importlib.metadata.version("cellpose")
    if int(version.split(".", 1)[0]) != 4:
        raise RuntimeError(f"Cellpose v4 is required; found {version}")
    requested = device or str(spec["device"])
    if requested not in {"cuda", "cpu", "auto"}:
        raise ValueError("device must be cuda, cpu, or auto")
    cuda_available = bool(torch.cuda.is_available())
    effective = "cuda" if (requested == "auto" and cuda_available) else ("cpu" if requested == "auto" else requested)
    if effective == "cuda" and not cuda_available:
        if not bool(spec["allow_cpu_fallback"]):
            raise RuntimeError("CUDA was required but torch.cuda.is_available() is false")
        effective = "cpu"
        _emit(
            events,
            "warning",
            message="CUDA unavailable; using explicitly permitted CPU fallback",
            requested_device=requested,
            effective_device=effective,
        )
    constructor_parameters = {
        "gpu": effective == "cuda",
        "pretrained_model": str(spec["model"]),
        "model_type": spec["model_type"],
        "diam_mean": spec["diam_mean"],
        "nchan": spec["nchan"],
        "device": effective,
        "use_bfloat16": bool(spec["use_bfloat16"]),
    }
    model = models.CellposeModel(
        gpu=constructor_parameters["gpu"],
        pretrained_model=constructor_parameters["pretrained_model"],
        model_type=constructor_parameters["model_type"],
        diam_mean=constructor_parameters["diam_mean"],
        nchan=constructor_parameters["nchan"],
        device=torch.device(effective),
        use_bfloat16=constructor_parameters["use_bfloat16"],
    )
    actual_path = Path(model.pretrained_model)
    actual_hash = _sha256(actual_path)
    expected = str(spec["model_sha256"]).lower()
    if actual_hash.lower() != expected:
        raise RuntimeError(
            f"model weight hash mismatch for {actual_path}: expected {expected}, measured {actual_hash}"
        )
    return Segmenter(model, actual_hash, effective, version, constructor_parameters)


def _materialize(data, volume: ImageVolume, events=None) -> np.ndarray:
    compute = getattr(data, "compute", None)
    if not callable(compute):
        return np.asarray(data)
    started = perf_counter()
    result = np.asarray(compute())
    _emit(
        events,
        "materialized",
        volume=volume,
        shape=list(result.shape),
        dtype=str(result.dtype),
        reason="Cellpose eval requires one eager single-channel ZYX stack",
        duration_seconds=perf_counter() - started,
    )
    return result


def _seed(runtime: Mapping) -> None:
    seed = int(runtime["seed"])
    random.seed(seed)
    np.random.seed(seed)
    torch = sys.modules.get("torch")
    if torch is not None:
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        if bool(runtime.get("deterministic_torch", False)):
            torch.use_deterministic_algorithms(True)
            torch.backends.cudnn.benchmark = False
            torch.backends.cudnn.deterministic = True


def _eval_kwargs(spec: Mapping, spacing_um: tuple[float, float, float]) -> dict[str, Any]:
    mode = spec["mode"]
    modes = {"volume_3d", "stitch_2d", "single_plane_2d", "max_projection_2d"}
    if mode not in modes:
        raise ValueError(f"segment mode must be one of {sorted(modes)}")
    if mode != "stitch_2d" and float(spec["stitch_threshold"]) != 0.0:
        raise ValueError("segment.stitch_threshold must be 0.0 unless mode is stitch_2d")
    if not bool(spec["tile"]):
        raise ValueError("Cellpose-SAM v4 always tiles; segment.tile must be true")
    anisotropy_value = spec["anisotropy"]
    if anisotropy_value == "manifest":
        anisotropy = float(spacing_um[0] / np.sqrt(spacing_um[1] * spacing_um[2]))
    elif anisotropy_value is None:
        anisotropy = None
    else:
        anisotropy = float(anisotropy_value)
    kwargs = {
        "batch_size": int(spec["batch_size"]),
        "resample": bool(spec["resample"]),
        "channels": spec["channels"],
        "channel_axis": spec["channel_axis"],
        "z_axis": None if mode in {"single_plane_2d", "max_projection_2d"} else int(spec["z_axis"]),
        "normalize": bool(spec["normalize"]),
        "rescale": spec["rescale_factor"],
        "diameter": float(spec["diameter_px"]),
        "flow_threshold": float(spec["flow_threshold"]),
        "cellprob_threshold": float(spec["cellprob_threshold"]),
        "do_3D": mode == "volume_3d",
        "anisotropy": anisotropy if mode == "volume_3d" else None,
        "flow3D_smooth": spec["flow3D_smooth"],
        "stitch_threshold": float(spec["stitch_threshold"]),
        "min_size": int(spec["min_size"]),
        "max_size_fraction": float(spec["max_size_fraction"]),
        "niter": spec["niter"],
        "augment": bool(spec["augment"]),
        "tile_overlap": float(spec["tile_overlap"]),
        "bsize": int(spec["bsize"]),
        "compute_masks": bool(spec["compute_masks"]),
        "progress": spec["progress"],
    }
    return kwargs


def segment(
    volume: ImageVolume,
    config,
    cancel: CancellationToken | None = None,
    events=None,
    *,
    segmenter: Segmenter | None = None,
) -> LabelVolume:
    raw = config.raw if hasattr(config, "raw") else config
    spec = raw["segment"]
    if volume.data.shape[-1] != 1:
        raise ValueError("segmentation requires a single-channel ZYXC volume")
    if cancel is not None:
        cancel.raise_if_cancelled()
    _seed(raw["runtime"])
    active = segmenter or load_model(spec, events=events)
    _seed(raw["runtime"])
    image = _materialize(volume.data[..., 0], volume, events).astype(np.float32, copy=False)
    two_dimensional = spec["mode"] in {"single_plane_2d", "max_projection_2d"}
    if two_dimensional:
        if image.shape[0] != 1:
            raise ValueError(
                f"{spec['mode']} requires a singleton-Z analysis volume; received {image.shape}"
            )
        image = image[0]
    if cancel is not None:
        cancel.raise_if_cancelled()
    _emit(
        events,
        "warning",
        volume=volume,
        message=(
            "Cellpose eval is a blocking library call; cancellation is checked immediately "
            "before and after inference but cannot interrupt an eval already in progress"
        ),
        cancellation_scope="between_eval_calls",
    )
    result = active.model.eval(image, **_eval_kwargs(spec, volume.spacing_um))
    masks = np.asarray(result[0])
    if two_dimensional and masks.ndim == 2:
        masks = masks[None, ...]
    if masks.shape != tuple(volume.data.shape[:3]):
        raise ValueError(f"Cellpose returned shape {masks.shape}, expected {volume.data.shape[:3]}")
    if not np.issubdtype(masks.dtype, np.integer) or np.any(masks < 0):
        raise TypeError("Cellpose returned invalid non-integer or negative labels")
    if cancel is not None:
        cancel.raise_if_cancelled()
    provenance = {
        "cellpose_version": active.cellpose_version,
        "model_sha256": active.model_sha256,
        "device": active.device,
        "constructor_parameters": dict(active.constructor_parameters),
        "eval_parameters": _eval_kwargs(spec, volume.spacing_um),
        "analysis_volume": dict(volume.metadata.get("analysis_volume", {})),
    }
    return LabelVolume(masks.astype(np.uint32, copy=False), volume.spacing_um, provenance)


def showcase_crop(value, config, output_dir: str | Path, *, segmenter: Segmenter | None = None):
    volume = value[0] if isinstance(value, tuple) else value
    labels = segment(volume, config, segmenter=segmenter)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    path = output / "segment_labels.npy"
    np.save(path, labels.data)
    return {"labels": path}


__all__ = ["Segmenter", "load_model", "segment", "showcase_crop"]
