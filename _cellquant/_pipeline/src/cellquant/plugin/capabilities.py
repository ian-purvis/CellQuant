"""Detect which Cellpose engines and devices this environment can run."""

from __future__ import annotations

import shutil
import subprocess
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
    torch_cuda_build: str | None
    cuda_available: bool
    cuda_detail: str | None
    system_gpu_detected: bool
    system_gpu_name: str | None
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


def _detect_system_nvidia_gpu() -> tuple[bool, str | None]:
    """Return (detected, first_gpu_name) via nvidia-smi when available."""

    if shutil.which("nvidia-smi") is None:
        return False, None
    try:
        query = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False, None
    if query.returncode != 0:
        return False, None
    names = [line.strip() for line in query.stdout.splitlines() if line.strip()]
    if not names:
        return False, None
    return True, names[0]


def _is_cpu_only_torch(version: str | None, torch_cuda_build: str | None) -> bool:
    text = str(version or "").casefold()
    if "+cpu" in text:
        return True
    # CUDA builds report a toolkit version string; CPU wheels leave this None.
    return torch_cuda_build is None


def _detect_cuda(
    *,
    system_gpu_fn: Callable[[], tuple[bool, str | None]] | None = None,
) -> tuple[bool, str | None, str | None, str | None, bool, str | None]:
    """Return torch/CUDA probe fields for the UI.

    Returns
    -------
    cuda_available, torch_version, torch_cuda_build, detail,
    system_gpu_detected, system_gpu_name
    """

    probe_system = system_gpu_fn or _detect_system_nvidia_gpu
    system_gpu_detected, system_gpu_name = probe_system()

    try:
        import torch
    except Exception as exc:  # noqa: BLE001
        detail = f"PyTorch not importable ({exc})"
        if system_gpu_detected:
            gpu = system_gpu_name or "NVIDIA GPU"
            detail = f"{gpu} detected, but PyTorch is not installed/importable ({exc})"
        return False, None, None, detail, system_gpu_detected, system_gpu_name

    version = getattr(torch, "__version__", "unknown")
    torch_cuda_build = getattr(getattr(torch, "version", None), "cuda", None)
    try:
        available = bool(torch.cuda.is_available())
    except Exception as exc:  # noqa: BLE001
        return (
            False,
            version,
            torch_cuda_build,
            f"torch.cuda.is_available() failed ({exc})",
            system_gpu_detected,
            system_gpu_name,
        )

    if available:
        try:
            name = torch.cuda.get_device_name(0)
        except Exception:  # noqa: BLE001
            name = system_gpu_name or "CUDA device 0"
        return True, version, torch_cuda_build, name, system_gpu_detected, system_gpu_name

    if _is_cpu_only_torch(version, torch_cuda_build):
        if system_gpu_detected:
            gpu = system_gpu_name or "NVIDIA GPU"
            detail = (
                f"{gpu} detected, but PyTorch is a CPU-only build "
                f"({version}); install a CUDA-enabled torch wheel"
            )
        else:
            detail = (
                f"PyTorch is a CPU-only build ({version}); "
                "no NVIDIA GPU was detected via nvidia-smi"
            )
    elif system_gpu_detected:
        gpu = system_gpu_name or "NVIDIA GPU"
        detail = (
            f"{gpu} detected and PyTorch reports CUDA build "
            f"{torch_cuda_build}, but torch.cuda.is_available() is False "
            "(driver/toolkit mismatch or init failure)"
        )
    else:
        detail = (
            f"CUDA not available (PyTorch {version}, "
            f"CUDA build {torch_cuda_build or 'none'}; no NVIDIA GPU via nvidia-smi)"
        )
    return False, version, torch_cuda_build, detail, system_gpu_detected, system_gpu_name


def _torch_is_ready() -> tuple[bool, str | None]:
    """Return whether torch can allocate a tiny tensor (import alone is not enough)."""

    try:
        import torch
    except Exception as exc:  # noqa: BLE001
        return False, f"PyTorch not importable ({exc})"
    try:
        torch.zeros(1)
    except Exception as exc:  # noqa: BLE001
        return False, f"PyTorch importable but not runnable ({exc})"
    version = getattr(torch, "__version__", None)
    return True, str(version) if version else None


def _engine_options(
    cellpose_major: int | None,
    *,
    torch_ready: bool,
    torch_detail: str | None,
) -> tuple[EngineOption, ...]:
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
        elif not torch_ready:
            available = False
            reason = torch_detail or "PyTorch is not ready for inference in this environment"
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


def _device_options(
    cuda_available: bool,
    *,
    system_gpu_detected: bool,
    cuda_detail: str | None,
) -> tuple[DeviceOption, ...]:
    if cuda_available:
        return (
            DeviceOption("auto", "Auto (GPU if available)", True),
            DeviceOption("cuda", "GPU (CUDA)", True),
            DeviceOption("cpu", "CPU", True),
        )

    if system_gpu_detected:
        auto_label = "Auto (CPU — GPU present, CUDA PyTorch missing)"
        auto_reason = cuda_detail or "System GPU found, but CUDA is not usable in this env"
        cuda_reason = cuda_detail or (
            "System GPU detected, but CUDA PyTorch is not usable in this environment"
        )
    else:
        auto_label = "Auto (CPU — no NVIDIA GPU detected)"
        auto_reason = cuda_detail or "CUDA unavailable; Auto will use CPU"
        cuda_reason = cuda_detail or "CUDA is not available in this environment"

    return (
        DeviceOption("auto", auto_label, True, auto_reason),
        DeviceOption("cuda", "GPU (CUDA)", False, cuda_reason),
        DeviceOption("cpu", "CPU", True),
    )


def _normalize_cuda_probe(
    raw: tuple,
) -> tuple[bool, str | None, str | None, str | None, bool, str | None]:
    """Accept legacy 3-tuples from tests or the full 6-field probe."""

    if len(raw) == 6:
        return (
            bool(raw[0]),
            raw[1],
            raw[2],
            raw[3],
            bool(raw[4]),
            raw[5],
        )
    if len(raw) == 3:
        cuda_available, torch_version, detail = raw
        system_gpu_detected = bool(cuda_available)
        system_gpu_name = detail if cuda_available else None
        torch_cuda_build = None
        if torch_version and "+cu" in str(torch_version).casefold():
            torch_cuda_build = "bundled"
        elif cuda_available:
            torch_cuda_build = "unknown"
        return (
            bool(cuda_available),
            torch_version,
            torch_cuda_build,
            detail,
            system_gpu_detected,
            system_gpu_name,
        )
    raise TypeError(
        "cuda_fn must return (cuda_available, torch_version, detail) or "
        "(cuda_available, torch_version, torch_cuda_build, detail, "
        "system_gpu_detected, system_gpu_name)"
    )


def detect_runtime_capabilities(
    *,
    cellpose_version_fn: Callable[[], str] | None = None,
    cuda_fn: Callable[[], tuple] | None = None,
    torch_ready_fn: Callable[[], tuple[bool, str | None]] | None = None,
) -> RuntimeCapabilities:
    """Probe the active Python env for runnable Cellpose engines and devices."""

    version_fn = cellpose_version_fn or (lambda: _package_version("cellpose"))
    probe_cuda = cuda_fn or _detect_cuda
    probe_torch = torch_ready_fn or _torch_is_ready

    cellpose_version = None
    cellpose_major = None
    cellpose_error = None
    try:
        cellpose_version = version_fn()
        cellpose_major = int(str(cellpose_version).split(".", 1)[0])
    except Exception as exc:  # noqa: BLE001
        cellpose_error = str(exc)

    (
        cuda_available,
        torch_version,
        torch_cuda_build,
        cuda_detail,
        system_gpu_detected,
        system_gpu_name,
    ) = _normalize_cuda_probe(tuple(probe_cuda()))
    torch_ready, torch_ready_detail = probe_torch()
    # Prefer the readiness probe when CUDA probe could not locate a version.
    if torch_version is None and torch_ready_detail and torch_ready:
        torch_version = torch_ready_detail
    engines = _engine_options(
        cellpose_major,
        torch_ready=bool(torch_ready),
        torch_detail=None if torch_ready else (torch_ready_detail or cuda_detail),
    )
    devices = _device_options(
        cuda_available,
        system_gpu_detected=system_gpu_detected,
        cuda_detail=cuda_detail,
    )

    torch_bit = f"PyTorch {torch_version}" if torch_version else "PyTorch not found"
    if not torch_ready:
        torch_bit = torch_ready_detail or torch_bit
    elif torch_version and torch_cuda_build:
        torch_bit = f"{torch_bit} (CUDA {torch_cuda_build})"
    elif torch_version and _is_cpu_only_torch(torch_version, torch_cuda_build):
        torch_bit = f"{torch_bit} (CPU build)"

    available_engines = [engine for engine in engines if engine.available]
    if cellpose_version and available_engines:
        engine_names = ", ".join(engine.label for engine in available_engines)
        if cuda_available:
            compute = f"GPU usable ({cuda_detail})"
        else:
            compute = cuda_detail or "CPU only"
        summary = (
            f"Detected Cellpose {cellpose_version}; {torch_bit}. "
            f"Runnable here: {engine_names}. {compute}."
        )
    elif cellpose_version:
        blocked = "; ".join(
            f"{engine.label}: {engine.unavailable_reason}"
            for engine in engines
            if engine.unavailable_reason
        )
        summary = (
            f"Detected Cellpose {cellpose_version}; {torch_bit}, but no "
            f"CellQuant-supported engine is runnable yet. {blocked}"
        )
    else:
        summary = (
            f"Cellpose was not found in this environment; {torch_bit}. "
            f"Reinstall CellQuant (v3 and/or v4 launchers). ({cellpose_error})"
        )

    return RuntimeCapabilities(
        cellpose_version=cellpose_version,
        cellpose_major=cellpose_major,
        cellpose_error=cellpose_error,
        torch_version=torch_version,
        torch_cuda_build=torch_cuda_build,
        cuda_available=cuda_available,
        cuda_detail=cuda_detail,
        system_gpu_detected=system_gpu_detected,
        system_gpu_name=system_gpu_name,
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
