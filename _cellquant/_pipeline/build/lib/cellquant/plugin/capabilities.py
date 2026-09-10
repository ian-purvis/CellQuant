"""Detect which Cellpose engines and devices this environment can run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence


@dataclass(frozen=True)
class EngineOption:
    """A CellQuant segmentation engine that may be offered in the UI."""

    engine_id: str
    label: str
    description: str
    min_cellpose_major: int
    max_cellpose_major: int
    available: bool
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class DeviceOption:
    device_id: str  # auto | cuda | cpu
    label: str
    available: bool
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class RuntimeCapabilities:
    cellpose_version: str | None
    cellpose_major: int | None
    cellpose_error: str | None
    torch_version: str | None
    cuda_available: bool
    cuda_detail: str | None
    engines: tuple[EngineOption, ...]
    devices: tuple[DeviceOption, ...]
    summary: str

    @property
    def available_engines(self) -> tuple[EngineOption, ...]:
        return tuple(engine for engine in self.engines if engine.available)

    @property
    def available_devices(self) -> tuple[DeviceOption, ...]:
        return tuple(device for device in self.devices if device.available)

    @property
    def default_engine_id(self) -> str | None:
        available = self.available_engines
        return available[0].engine_id if available else None

    @property
    def default_device_id(self) -> str:
        ids = {device.device_id for device in self.available_devices}
        if "auto" in ids:
            return "auto"
        if "cuda" in ids:
            return "cuda"
        return "cpu"


# Engines CellQuant knows how to drive today (and stubs for future expansion).
_ENGINE_CATALOG: tuple[dict, ...] = (
    {
        "engine_id": "v4",
        "label": "Cellpose-SAM (v4)",
        "description": (
            "Current CellQuant engine: Cellpose-SAM with cpsam weights. "
            "Works for 2D and 3D modes. Requires the cellpose 4.x package."
        ),
        "min_cellpose_major": 4,
        "max_cellpose_major": 4,
    },
    {
        "engine_id": "v3",
        "label": "Cellpose classic (v3)",
        "description": (
            "Classic Cellpose 3.x (nuclei/cyto). Lighter than Cellpose-SAM — "
            "prefer the v3 Napari launcher if v4 is too slow or runs out of memory. "
            "Requires Cellpose 3.x in this environment."
        ),
        "min_cellpose_major": 3,
        "max_cellpose_major": 3,
    },
)


def _package_version(name: str) -> str:
    import importlib.metadata

    return importlib.metadata.version(name)


def _detect_cuda() -> tuple[bool, str | None, str | None]:
    """Return (cuda_available, torch_version, detail)."""

    try:
        import torch
    except Exception as exc:  # noqa: BLE001
        return False, None, f"PyTorch not importable ({exc})"
    version = getattr(torch, "__version__", "unknown")
    try:
        available = bool(torch.cuda.is_available())
    except Exception as exc:  # noqa: BLE001
        return False, version, f"torch.cuda.is_available() failed ({exc})"
    if available:
        try:
            name = torch.cuda.get_device_name(0)
        except Exception:  # noqa: BLE001
            name = "CUDA device 0"
        return True, version, name
    detail = "CUDA not available"
    if "+cpu" in str(version).casefold():
        detail = "PyTorch is a CPU-only build (no CUDA wheels installed)"
    return False, version, detail


def _engine_options(cellpose_major: int | None) -> tuple[EngineOption, ...]:
    options: list[EngineOption] = []
    for spec in _ENGINE_CATALOG:
        implemented = bool(spec.get("implemented", True))
        min_major = int(spec["min_cellpose_major"])
        max_major = int(spec["max_cellpose_major"])
        if cellpose_major is None:
            available = False
            reason = "Cellpose is not installed in this environment"
        elif not (min_major <= cellpose_major <= max_major):
            available = False
            reason = (
                f"Requires Cellpose {min_major}.x–{max_major}.x; "
                f"this environment has {cellpose_major}.x"
            )
        elif not implemented:
            available = False
            reason = "Installed, but not yet supported by CellQuant (coming later)"
        else:
            available = True
            reason = None
        options.append(
            EngineOption(
                engine_id=str(spec["engine_id"]),
                label=str(spec["label"]),
                description=str(spec["description"]),
                min_cellpose_major=min_major,
                max_cellpose_major=max_major,
                available=available,
                unavailable_reason=reason,
            )
        )
    return tuple(options)


def _device_options(cuda_available: bool) -> tuple[DeviceOption, ...]:
    if cuda_available:
        return (
            DeviceOption("auto", "Auto (GPU if available)", True),
            DeviceOption("cuda", "GPU (CUDA)", True),
            DeviceOption("cpu", "CPU", True),
        )
    return (
        DeviceOption(
            "auto",
            "Auto (CPU — no GPU detected)",
            True,
            "CUDA unavailable; Auto will use CPU",
        ),
        DeviceOption(
            "cuda",
            "GPU (CUDA)",
            False,
            "CUDA is not available in this environment",
        ),
        DeviceOption("cpu", "CPU", True),
    )


def detect_runtime_capabilities(
    *,
    cellpose_version_fn: Callable[[], str] | None = None,
    cuda_fn: Callable[[], tuple[bool, str | None, str | None]] | None = None,
) -> RuntimeCapabilities:
    """Probe the active Python env for runnable Cellpose engines and devices."""

    version_fn = cellpose_version_fn or (lambda: _package_version("cellpose"))
    probe_cuda = cuda_fn or _detect_cuda

    cellpose_version = None
    cellpose_major = None
    cellpose_error = None
    try:
        cellpose_version = version_fn()
        cellpose_major = int(str(cellpose_version).split(".", 1)[0])
    except Exception as exc:  # noqa: BLE001
        cellpose_error = str(exc)

    cuda_available, torch_version, cuda_detail = probe_cuda()
    engines = _engine_options(cellpose_major)
    devices = _device_options(cuda_available)

    available_engines = [engine for engine in engines if engine.available]
    if cellpose_version and available_engines:
        engine_names = ", ".join(engine.label for engine in available_engines)
        if cuda_available:
            compute = f"GPU available ({cuda_detail})"
        else:
            compute = f"CPU only ({cuda_detail})"
        summary = (
            f"Detected Cellpose {cellpose_version}. Runnable here: {engine_names}. {compute}."
        )
    elif cellpose_version:
        blocked = "; ".join(
            f"{engine.label}: {engine.unavailable_reason}"
            for engine in engines
            if engine.unavailable_reason
        )
        summary = (
            f"Detected Cellpose {cellpose_version}, but no CellQuant-supported engine "
            f"is runnable yet. {blocked}"
        )
    else:
        summary = (
            "Cellpose was not found in this environment. "
            f"Reinstall CellQuant (v3 and/or v4 launchers). ({cellpose_error})"
        )

    return RuntimeCapabilities(
        cellpose_version=cellpose_version,
        cellpose_major=cellpose_major,
        cellpose_error=cellpose_error,
        torch_version=torch_version,
        cuda_available=cuda_available,
        cuda_detail=cuda_detail,
        engines=engines,
        devices=devices,
        summary=summary,
    )


def compatible_engine_choices(
    capabilities: RuntimeCapabilities | None = None,
) -> Sequence[tuple[str, str]]:
    """Return ``(label, engine_id)`` pairs for UI combo boxes."""

    caps = capabilities or detect_runtime_capabilities()
    return tuple((engine.label, engine.engine_id) for engine in caps.available_engines)


def compatible_device_choices(
    capabilities: RuntimeCapabilities | None = None,
) -> Sequence[tuple[str, str]]:
    """Return ``(label, device_id)`` pairs for UI combo boxes."""

    caps = capabilities or detect_runtime_capabilities()
    return tuple((device.label, device.device_id) for device in caps.available_devices)


__all__ = [
    "DeviceOption",
    "EngineOption",
    "RuntimeCapabilities",
    "compatible_device_choices",
    "compatible_engine_choices",
    "detect_runtime_capabilities",
]
