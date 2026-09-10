from __future__ import annotations

from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class CellposeSettings:
    engine: str = "auto"
    model: str = "nuclei"
    diameter_px: float = 30.0
    cellprob_threshold: float = 0.0
    flow_threshold: float = 0.4
    stitch_threshold: float = 0.25
    min_size: int = 200
    anisotropy: float | None = None
    gpu: str = "auto"


def detect_engine(version: str) -> str:
    return "v4" if int(version.split(".", 1)[0]) >= 4 else "v3"


def gpu_available() -> bool:
    try:
        from cellpose import core
        return bool(core.use_gpu())
    except Exception:
        return False


def model_kwargs(settings: CellposeSettings, engine: str, gpu: bool) -> dict:
    if engine == "v3":
        return {"gpu": gpu, "pretrained_model": settings.model} if _is_custom(settings.model) else {"gpu": gpu, "model_type": settings.model}
    return {"gpu": gpu, "pretrained_model": settings.model or "cpsam"}


def eval_kwargs(settings: CellposeSettings, engine: str, mode: str) -> dict:
    kwargs = dict(
        diameter=float(settings.diameter_px),
        cellprob_threshold=float(settings.cellprob_threshold),
        flow_threshold=float(settings.flow_threshold),
        min_size=int(settings.min_size),
    )
    if engine == "v3":
        kwargs["channels"] = [0, 0]
    else:
        kwargs["channel_axis"] = None
    if mode == "stitch":
        kwargs.update(do_3D=False, stitch_threshold=float(settings.stitch_threshold))
        if engine == "v4":
            kwargs["z_axis"] = 0
    elif mode == "volume":
        kwargs.update(do_3D=True)
        if settings.anisotropy is not None:
            kwargs["anisotropy"] = float(settings.anisotropy)
        if engine == "v4":
            kwargs["z_axis"] = 0
    return kwargs


def _is_custom(model: str) -> bool:
    from pathlib import Path
    return bool(model and Path(model).exists())


class CellposeAdapter:
    def __init__(self, settings: CellposeSettings):
        self.settings = settings

    def segment(self, image, mode: str):
        import importlib.metadata
        from cellpose import models
        version = importlib.metadata.version("cellpose")
        engine = detect_engine(version) if self.settings.engine == "auto" else self.settings.engine
        if self.settings.gpu not in {"auto", "on", "off", "gpu", "cpu"}:
            raise ValueError("gpu must be auto, on, or off")
        requested_gpu = gpu_available() if self.settings.gpu == "auto" else self.settings.gpu in {"on", "gpu"}
        try:
            model = models.CellposeModel(**model_kwargs(self.settings, engine, requested_gpu))
            masks = model.eval(np.asarray(image), **eval_kwargs(self.settings, engine, mode))[0]
        except Exception:
            if not requested_gpu:
                raise
            model = models.CellposeModel(**model_kwargs(self.settings, engine, False))
            masks = model.eval(np.asarray(image), **eval_kwargs(self.settings, engine, mode))[0]
        masks = np.asarray(masks)
        return masks.astype(np.uint16 if masks.max(initial=0) <= 65535 else np.uint32, copy=False)
