"""Strict Cellpose adapter (v3 classic + v4 Cellpose-SAM) shared by CLI and napari."""

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

from cellquant.contracts import (
    CancellationToken,
    ImageVolume,
    LabelVolume,
    PipelineEvent,
)

# All-zero hash: accept whatever weight file Cellpose resolved (builtin models).
ACCEPT_MEASURED_MODEL_HASH = "0" * 64
_V3_BUILTIN_MODELS = frozenset(
    {"nuclei", "cyto", "cyto2", "cyto3", "tissuenet_cp3", "livecell_cp3", "yeast_PhC_cp3", "yeast_BF_cp3"}
)


def _sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def apply_engine_segment_defaults(segment: dict[str, Any]) -> dict[str, Any]:
    """Normalize model / hash fields when the UI or survey switches engines."""

    payload = dict(segment)
    engine = str(payload.get("engine", "v4"))
    if engine == "v3":
        model = str(payload.get("model") or "")
        if not model or model.startswith("cpsam"):
            payload["model"] = "nuclei"
            payload["model_sha256"] = ACCEPT_MEASURED_MODEL_HASH
            payload["model_type"] = None
            payload["tile"] = True
        payload["use_bfloat16"] = False
    elif engine == "v4":
        model = str(payload.get("model") or "")
        if model in _V3_BUILTIN_MODELS:
            payload["model"] = "cpsam_v2"
            payload["model_sha256"] = (
                "0f1cc3f7ecdd8a037a57c6c48d9d8921391be4cbce3fa9f13c3e3a2e1253c667"
            )
            payload["tile"] = True
            payload["use_bfloat16"] = True
    return payload


def _resolve_weight_path(model: Any) -> Path:
    raw = getattr(model, "pretrained_model", None)
    if isinstance(raw, (list, tuple)):
        if not raw:
            raise RuntimeError("Cellpose model has no pretrained_model path")
        raw = raw[0]
    if raw is None:
        raise RuntimeError("Cellpose model has no pretrained_model path")
    return Path(str(raw))


@dataclass
class Segmenter:
    model: Any
    model_sha256: str
    device: str
    cellpose_version: str
    constructor_parameters: Mapping[str, Any] = field(default_factory=dict)


class _CancelProgress:
    """Cellpose-compatible progress object that raises when cancel is requested.

    Cellpose calls ``setValue`` at coarse checkpoints (for example between 3D
    orientations). That lets Cancel stop many long runs without waiting for the
    entire blocking eval to finish.
    """

    def __init__(self, cancel: CancellationToken | None, inner: Any | None = None) -> None:
        self._cancel = cancel
        self._inner = inner
        self.value = 0

    def setValue(self, value: int) -> None:  # noqa: N802 - Cellpose API
        if self._cancel is not None:
            self._cancel.raise_if_cancelled()
        self.value = int(value)
        if self._inner is not None and hasattr(self._inner, "setValue"):
            self._inner.setValue(value)

    def setMaximum(self, value: int) -> None:  # noqa: N802
        if self._inner is not None and hasattr(self._inner, "setMaximum"):
            self._inner.setMaximum(value)

    def setMinimum(self, value: int) -> None:  # noqa: N802
        if self._inner is not None and hasattr(self._inner, "setMinimum"):
            self._inner.setMinimum(value)


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


def _emit_plane_progress(
    events,
    volume: ImageVolume,
    *,
    current: int,
    total: int,
    message: str,
) -> None:
    if events is None:
        return
    metadata = volume.metadata
    events(
        PipelineEvent(
            "progress",
            str(metadata.get("run_id", "")),
            str(metadata.get("file_id", volume.source.name)),
            "segment",
            datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            current=current,
            total=total,
            details={"message": message, "status": "running"},
        )
    )


def load_model(spec: Mapping, device: str | None = None, events=None) -> Segmenter:
    """Load Cellpose (v3 or v4) and verify the weight file before inference."""

    from cellpose import models
    import torch

    version = importlib.metadata.version("cellpose")
    major = int(version.split(".", 1)[0])
    engine = str(spec.get("engine", "v4"))
    if engine == "v4" and major != 4:
        raise RuntimeError(
            f"segment.engine=v4 requires Cellpose 4.x; found {version}. "
            "Open CellQuant.bat and choose Cellpose-SAM v4, or set engine to v3."
        )
    if engine == "v3" and major != 3:
        raise RuntimeError(
            f"segment.engine=v3 requires Cellpose 3.x; found {version}. "
            "Open CellQuant.bat and choose classic v3."
        )
    if engine not in {"v3", "v4"}:
        raise ValueError("segment.engine must be v3 or v4")

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

    model_name = str(spec["model"])
    if engine == "v3":
        custom = Path(model_name).exists()
        constructor_parameters: dict[str, Any] = {
            "gpu": effective == "cuda",
            "device": effective,
            "engine": "v3",
        }
        if custom:
            constructor_parameters["pretrained_model"] = model_name
            model = models.CellposeModel(
                gpu=constructor_parameters["gpu"],
                pretrained_model=model_name,
            )
        else:
            constructor_parameters["model_type"] = model_name
            model = models.CellposeModel(
                gpu=constructor_parameters["gpu"],
                model_type=model_name,
            )
    else:
        constructor_parameters = {
            "gpu": effective == "cuda",
            "pretrained_model": model_name,
            "model_type": spec["model_type"],
            "diam_mean": spec["diam_mean"],
            "nchan": spec["nchan"],
            "device": effective,
            "use_bfloat16": bool(spec["use_bfloat16"]),
            "engine": "v4",
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

    actual_path = _resolve_weight_path(model)
    actual_hash = _sha256(actual_path)
    expected = str(spec["model_sha256"]).lower()
    if expected != ACCEPT_MEASURED_MODEL_HASH and actual_hash.lower() != expected:
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
    engine = str(spec.get("engine", "v4"))
    modes = {"volume_3d", "stitch_2d", "single_plane_2d", "max_projection_2d"}
    if mode not in modes:
        raise ValueError(f"segment mode must be one of {sorted(modes)}")
    if mode != "stitch_2d" and float(spec["stitch_threshold"]) != 0.0:
        raise ValueError("segment.stitch_threshold must be 0.0 unless mode is stitch_2d")
    if not bool(spec["tile"]):
        raise ValueError(f"Cellpose {engine} always tiles; segment.tile must be true (tile=False is unsupported)")
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
        "channels": [0, 0] if engine == "v3" and spec["channels"] is None else spec["channels"],
        "channel_axis": spec["channel_axis"],
        "z_axis": None if mode in {"single_plane_2d", "max_projection_2d"} else int(spec["z_axis"]),
        "normalize": bool(spec["normalize"]),
        "rescale": spec["rescale_factor"],
        "diameter": None if spec["diameter_px"] is None else float(spec["diameter_px"]),
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


def _with_cancel_progress(
    kwargs: Mapping[str, Any],
    cancel: CancellationToken | None,
) -> dict[str, Any]:
    payload = dict(kwargs)
    if cancel is not None:
        payload["progress"] = _CancelProgress(cancel, payload.get("progress"))
    return payload


def _eval_stitch_cancellable(
    model: Any,
    image_zyx: np.ndarray,
    kwargs: Mapping[str, Any],
    cancel: CancellationToken | None,
    events,
    volume: ImageVolume,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Run stitch_2d plane-by-plane so Cancel can stop between Z slices."""

    from cellpose.utils import stitch3D

    if image_zyx.ndim != 3:
        raise ValueError(f"stitch_2d expects ZYX; received shape {image_zyx.shape}")
    plane_kwargs = _with_cancel_progress(kwargs, cancel)
    stitch_threshold = float(plane_kwargs.get("stitch_threshold", 0.0))
    plane_kwargs["stitch_threshold"] = 0.0
    plane_kwargs["do_3D"] = False
    plane_kwargs["z_axis"] = None
    plane_kwargs["anisotropy"] = None

    depth = int(image_zyx.shape[0])
    plane_masks: list[np.ndarray] = []
    for index in range(depth):
        if cancel is not None:
            cancel.raise_if_cancelled()
        _emit_plane_progress(
            events,
            volume,
            current=index + 1,
            total=depth,
            message=(
                f"Cellpose stitch plane {index + 1}/{depth} — "
                "press Cancel to stop before the next plane"
            ),
        )
        result = model.eval(image_zyx[index], **plane_kwargs)
        masks = np.asarray(result[0])
        if masks.ndim != 2:
            raise ValueError(f"expected 2D plane masks; received shape {masks.shape}")
        plane_masks.append(masks)

    if cancel is not None:
        cancel.raise_if_cancelled()
    stacked = np.stack(plane_masks, axis=0)
    if stitch_threshold > 0 and depth > 1:
        stacked = np.asarray(stitch3D(stacked, stitch_threshold=stitch_threshold))
    eval_parameters = dict(kwargs)
    eval_parameters["cancellable_stitch_planes"] = True
    eval_parameters["stitch_threshold"] = stitch_threshold
    return stacked, eval_parameters


def _should_use_killable_process(model: Any, cancel: CancellationToken | None) -> bool:
    if cancel is None or not hasattr(cancel, "register_force_stop"):
        return False
    from cellquant.segment.killable import is_killable_cellpose_model

    return is_killable_cellpose_model(model)


def _run_cellpose_eval(
    *,
    segmenter: Segmenter,
    image: np.ndarray,
    kwargs: Mapping[str, Any],
    mode: str,
    cancel: CancellationToken | None,
    events,
    volume: ImageVolume,
) -> tuple[np.ndarray, dict[str, Any]]:
    if segmenter.model is None or _should_use_killable_process(segmenter.model, cancel):
        from cellquant.segment.killable import run_killable_cellpose_eval

        def on_plane(current: int, total: int, message: str) -> None:
            _emit_plane_progress(
                events,
                volume,
                current=current,
                total=total,
                message=message,
            )

        _emit(
            events,
            "warning",
            volume=volume,
            message=(
                "Cellpose is running in a killable worker process. "
                "Cancel stops after the current plane/checkpoint. "
                "Kill force-stops immediately if Napari feels stuck on a long mid-plane run."
            ),
            cancellation_scope="force_killable_process",
        )
        return run_killable_cellpose_eval(
            constructor_parameters=segmenter.constructor_parameters,
            image=image,
            eval_kwargs=kwargs,
            mode=mode,
            cancel=cancel,  # type: ignore[arg-type]
            on_plane_progress=on_plane if mode == "stitch_2d" else None,
        )

    if mode == "stitch_2d":
        _emit(
            events,
            "warning",
            volume=volume,
            message=(
                "Cellpose stitch is running plane-by-plane. "
                "Press Cancel to stop after the current Z plane finishes "
                "(usually much sooner than waiting for the whole stack)."
            ),
            cancellation_scope="between_stitch_planes",
        )
        return _eval_stitch_cancellable(
            segmenter.model, image, kwargs, cancel, events, volume
        )

    _emit(
        events,
        "warning",
        volume=volume,
        message=(
            "Cellpose is running. Press Cancel to stop at the next Cellpose checkpoint "
            "(for 3D, between orientation passes). Mid-plane network work cannot be force-killed."
        ),
        cancellation_scope="cellpose_progress_checkpoints",
    )
    result = segmenter.model.eval(image, **_with_cancel_progress(kwargs, cancel))
    return np.asarray(result[0]), dict(kwargs)


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
    # Do not allocate a parent GPU model merely to reconstruct it in the child.
    if segmenter is None and cancel is not None and hasattr(cancel, "register_force_stop"):
        active = Segmenter(None, str(spec["model_sha256"]), str(spec["device"]), "",
                           {"_model_spec": dict(spec), "_runtime": dict(raw["runtime"])})
    else:
        active = segmenter or load_model(spec, events=events)
        if _should_use_killable_process(active.model, cancel):
            active = Segmenter(active.model, active.model_sha256, active.device, active.cellpose_version,
                               {"_model_spec": dict(spec), "_runtime": dict(raw["runtime"])})
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

    kwargs = _eval_kwargs(spec, volume.spacing_um)
    masks, eval_parameters = _run_cellpose_eval(
        segmenter=active,
        image=image,
        kwargs=kwargs,
        mode=str(spec["mode"]),
        cancel=cancel,
        events=events,
        volume=volume,
    )
    if eval_parameters.get("force_killable_process"):
        active = Segmenter(None, eval_parameters.pop("model_sha256"), eval_parameters.pop("device"),
                           eval_parameters.pop("cellpose_version"), eval_parameters.pop("constructor_parameters"))
        eval_parameters = {**dict(kwargs), **eval_parameters}
    # Preserve stitch provenance fields expected by callers when killable path ran stitch.
    if spec["mode"] == "stitch_2d":
        eval_parameters = {
            **dict(kwargs),
            **dict(eval_parameters),
            "cancellable_stitch_planes": True,
            "stitch_threshold": float(kwargs.get("stitch_threshold", 0.0)),
        }

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
        "eval_parameters": eval_parameters,
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


__all__ = [
    "ACCEPT_MEASURED_MODEL_HASH",
    "Segmenter",
    "apply_engine_segment_defaults",
    "load_model",
    "segment",
    "showcase_crop",
]
