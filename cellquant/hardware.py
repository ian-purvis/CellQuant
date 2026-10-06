"""Recommend how to handle Z-stacks on this computer.

Every option stays available. This module only estimates how long each one
takes here and suggests one. Estimates start from rough per-slice speeds and
are replaced by speeds measured on this computer once an image has been
analyzed.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

# Seconds to segment one megapixel of one 2D slice. Rough starting values; the
# first real run on this computer replaces them.
_SECONDS_PER_MEGAPIXEL = {
    ("classical", "cpu"): 0.15,
    ("cellpose3", "cuda"): 0.4,
    ("cellpose3", "cpu"): 5.0,
    ("cellpose4", "cuda"): 1.0,
    ("cellpose4", "cpu"): 45.0,
}
# Longer than this per image and a 2D projection is suggested for a first pass.
SLOW_SECONDS_PER_IMAGE = 300.0
# Measured speeds, keyed like _SECONDS_PER_MEGAPIXEL plus the Z mode.
_MEASURED: dict[tuple[str, str, str], float] = {}

# Insertion order is the Step 2 dropdown order.
Z_OPTION_LABELS = {
    "single_plane": "2D: one slice",
    "max_projection": "2D: max projection",
    "stitch_slices": "2D + stitching (link slices)",
    "full_3d": "True 3D (whole volume)",
}


@dataclass(frozen=True)
class Hardware:
    gpu_available: bool = False
    gpu_name: str = ""
    gpu_memory_gb: float | None = None
    ram_gb: float | None = None
    cpu_count: int = 1

    def describe(self) -> str:
        parts = []
        if self.gpu_available:
            memory = f", {self.gpu_memory_gb:.0f} GB" if self.gpu_memory_gb else ""
            parts.append(f"GPU: {self.gpu_name or 'NVIDIA GPU'}{memory}")
        else:
            parts.append("no usable GPU")
        if self.ram_gb:
            parts.append(f"{self.ram_gb:.0f} GB memory")
        parts.append(f"{self.cpu_count} CPU cores")
        return ", ".join(parts)


@dataclass(frozen=True)
class Estimate:
    mode: str
    seconds_per_image: float | None
    memory_gb: float | None
    measured: bool
    warning: str = ""


@dataclass
class Recommendation:
    mode: str
    reason: str
    estimates: dict[str, Estimate] = field(default_factory=dict)
    use_gpu: bool = False


def system_memory_gb() -> float | None:
    """Installed memory in GB, or None when it cannot be read."""

    try:
        if sys.platform.startswith("win"):
            import ctypes

            class _Status(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _Status()
            status.dwLength = ctypes.sizeof(_Status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
                return status.ullTotalPhys / 1024**3
            return None
        pages = os.sysconf("SC_PHYS_PAGES")
        size = os.sysconf("SC_PAGE_SIZE")
        return pages * size / 1024**3
    except (AttributeError, OSError, ValueError):
        return None


def detect_hardware(gpu: dict | None = None) -> Hardware:
    """Combine the background GPU check (``engines.gpu_status``) with memory and CPU count."""

    gpu = gpu or {}
    memory = gpu.get("memory_gb")
    return Hardware(
        gpu_available=bool(gpu.get("available")),
        gpu_name=str(gpu.get("name") or ""),
        gpu_memory_gb=float(memory) if memory else None,
        ram_gb=system_memory_gb(),
        cpu_count=os.cpu_count() or 1,
    )


def _speed_key(method: str, engine: str | None, use_gpu: bool) -> tuple[str, str]:
    if method != "cellpose":
        return ("classical", "cpu")
    return (engine or "cellpose4", "cuda" if use_gpu else "cpu")


def record_speed(method: str, engine: str | None, device: str, mode: str, seconds: float, slice_megapixels: float) -> None:
    """Remember how fast segmentation ran here.

    ``slice_megapixels`` is the image area times the number of slices analyzed
    (1 for a 2D mode).
    """

    if seconds <= 0 or slice_megapixels <= 0:
        return
    key = (*_speed_key(method, engine, device == "cuda"), mode)
    _MEASURED[key] = seconds / slice_megapixels


def forget_speeds() -> None:
    _MEASURED.clear()


def estimate(
    mode: str,
    *,
    method: str,
    engine: str | None,
    use_gpu: bool,
    slices: int,
    height: int,
    width: int,
    anisotropy: float | None,
    hardware: Hardware,
) -> Estimate:
    """Rough seconds per image and working memory for one Z option."""

    speed_key = _speed_key(method, engine, use_gpu)
    measured_speed = _MEASURED.get((*speed_key, mode))
    base = measured_speed if measured_speed is not None else _SECONDS_PER_MEGAPIXEL[speed_key]
    megapixels = height * width / 1e6
    ratio = max(1.0, anisotropy or 1.0)
    if mode in ("max_projection", "single_plane"):
        planes = 1.0
    elif mode == "stitch_slices":
        planes = float(slices)
    else:
        # Cellpose's 3D mode also runs the network across XZ and YZ, with Z stretched by the anisotropy.
        planes = float(slices) * (1.0 + 2.0 * ratio) if method == "cellpose" else float(slices) * 1.5
    if measured_speed is None:
        seconds = base * megapixels * planes
    else:
        # Measured speeds are per megapixel per slice analyzed, for this exact mode.
        seconds = measured_speed * megapixels * (slices if mode in ("stitch_slices", "full_3d") else 1)
    # Working memory: the stack as floats plus, for Cellpose 3D, flows over the stretched volume.
    voxels = slices * height * width
    if mode == "full_3d" and method == "cellpose":
        memory = voxels * ratio * 4 * 8 / 1024**3
    elif mode in ("stitch_slices", "full_3d"):
        memory = voxels * 4 * 4 / 1024**3
    else:
        memory = height * width * 4 * 4 / 1024**3
    warning = ""
    if hardware.ram_gb and memory > 0.5 * hardware.ram_gb:
        warning = f"needs about {memory:.0f} GB of the {hardware.ram_gb:.0f} GB memory; may fail"
    return Estimate(mode, seconds, memory, measured_speed is not None, warning)


def recommend(
    *,
    method: str,
    engine: str | None,
    use_gpu: bool,
    stacks: list[tuple[int, int, int]],
    anisotropy: float | None,
    hardware: Hardware,
) -> Recommendation | None:
    """Suggest a Z option for these stacks, ``(slices, height, width)``, on this computer.

    None when no image is a Z-stack.
    """

    stacks = [item for item in stacks if item[0] > 1]
    if not stacks:
        return None
    slices = sorted(item[0] for item in stacks)[len(stacks) // 2]
    height = max(item[1] for item in stacks)
    width = max(item[2] for item in stacks)
    gpu_on = use_gpu or (method == "cellpose" and hardware.gpu_available)
    estimates = {
        mode: estimate(
            mode,
            method=method,
            engine=engine,
            use_gpu=gpu_on,
            slices=slices,
            height=height,
            width=width,
            anisotropy=anisotropy,
            hardware=hardware,
        )
        for mode in Z_OPTION_LABELS
    }
    turn_on_gpu = method == "cellpose" and hardware.gpu_available and not use_gpu
    stitched = estimates["stitch_slices"]
    full = estimates["full_3d"]
    where = hardware.describe()
    gpu_note = " Tick 'Use GPU' (step 2, beside the GPU box at the top)." if turn_on_gpu else ""
    if (
        method == "cellpose"
        and gpu_on
        and (hardware.gpu_memory_gb or 0) >= 8
        and slices >= 10
        and anisotropy is not None
        and anisotropy <= 2.0
        and not full.warning
    ):
        return Recommendation(
            "full_3d",
            f"This computer has a GPU ({where}) and the slices are close enough together "
            f"({slices} slices, Z step {anisotropy:.1f}x the pixel size) for Cellpose's 3D mode.{gpu_note}",
            estimates,
            turn_on_gpu,
        )
    if stitched.seconds_per_image is not None and stitched.seconds_per_image > SLOW_SECONDS_PER_IMAGE:
        return Recommendation(
            "max_projection",
            f"Good for a first pass: linking slices would take about "
            f"{format_seconds(stitched.seconds_per_image)} per image here ({where}). "
            "3D is still available; it is more accurate when nuclei overlap in depth. "
            + ("Classic Cellpose is much faster without a GPU." if engine == "cellpose4" and method == "cellpose" and not gpu_on else ""),
            estimates,
            turn_on_gpu,
        )
    reason = (
        f"Nuclei at different depths are counted separately, and it takes about "
        f"{format_seconds(stitched.seconds_per_image)} per image here ({where})."
    )
    if anisotropy is not None and anisotropy > 2.0:
        reason += f" The Z step is {anisotropy:.1f}x the pixel size, too coarse for whole-volume 3D to help."
    elif slices < 10:
        reason += f" With {slices} slices, whole-volume 3D has little to work with."
    return Recommendation("stitch_slices", reason + gpu_note, estimates, turn_on_gpu)


def format_seconds(seconds: float | None) -> str:
    if seconds is None:
        return "unknown time"
    if seconds < 1:
        return "under a second"
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


# -- per-computer settings: Cellpose precision and CPU threads ------------------------------------------

PRECISIONS = ("auto", "bfloat16", "float32")
_BF16: dict[str, bool] = {}


def settings_path():
    """Per-computer settings, beside the install record: %LOCALAPPDATA%\\CellQuant\\cellquant_settings.json."""

    from pathlib import Path

    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / "CellQuant" / "cellquant_settings.json"


def load_settings() -> dict:
    import json

    path = settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(data: dict) -> None:
    import json

    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temporary.replace(path)


def bfloat16_native(gpu: bool) -> bool:
    """Whether the device Cellpose will use computes bfloat16 natively (not emulated).

    A CUDA GPU must report bfloat16 support; a CPU must report oneDNN (mkldnn) bfloat16
    support (AVX-512 BF16 or AMX). Otherwise bfloat16 is emulated, which on a laptop CPU
    made Cellpose-SAM several times slower than float32. Anything unknown counts as no.
    """

    key = "cuda" if gpu else "cpu"
    if key not in _BF16:
        supported = False
        try:
            import torch

            if gpu:
                supported = bool(torch.cuda.is_available() and torch.cuda.is_bf16_supported())
            else:
                supported = bool(torch.ops.mkldnn._is_mkldnn_bf16_supported())
        except Exception:  # noqa: BLE001 - no PyTorch, or an old one: float32
            supported = False
        _BF16[key] = supported
    return _BF16[key]


def cellpose_precision(parameters: dict, engine: str) -> str:
    """"bfloat16" or "float32" for Cellpose-SAM: the recipe's ``precision`` when it names one,
    otherwise bfloat16 only where the device supports it natively. Classic Cellpose runs float32."""

    if engine != "cellpose4":
        return "float32"
    chosen = str(parameters.get("precision") or "auto")
    if chosen not in PRECISIONS:
        raise ValueError(f"Unknown precision '{chosen}'. Choose one of: {', '.join(PRECISIONS)}.")
    if chosen != "auto":
        return chosen
    return "bfloat16" if bfloat16_native(bool(parameters.get("gpu", False))) else "float32"


def best_threads(engine: str) -> int | None:
    """The fastest CPU thread count measured here for this engine, or None when not measured."""

    value = (load_settings().get("cpu_threads") or {}).get(engine)
    try:
        return int(value["threads"]) if isinstance(value, dict) else None
    except (KeyError, TypeError, ValueError):
        return None


_THREADS_APPLIED: set[str] = set()


def apply_threads(engine: str) -> int | None:
    """Use the measured thread count for PyTorch, once per program run (it changes speed, not results)."""

    if engine in _THREADS_APPLIED:
        return None
    _THREADS_APPLIED.add(engine)
    threads = best_threads(engine)
    if threads:
        try:
            import torch

            torch.set_num_threads(threads)
        except Exception:  # noqa: BLE001 - PyTorch is optional for classical runs
            return None
    return threads


def benchmark_threads(engine: str | None = None, counts=None, run=None, repeats: int = 1) -> dict:
    """Time Cellpose on a small fixed synthetic image at a few CPU thread counts and keep the fastest.

    ``run(image) -> labels`` defaults to CellQuant's Cellpose on the engine installed here. The
    labels must be identical at every thread count (the result is refused otherwise). The fastest
    count is stored in the per-computer settings and used for later runs of that engine.
    """

    import time

    import numpy as np

    from cellquant.engines import cellpose_engine

    engine = engine or cellpose_engine().key
    available = os.cpu_count() or 1
    counts = sorted({min(int(count), available) for count in (counts or (2, 4, 6, available)) if int(count) > 0})
    image = _benchmark_image()
    if run is None:
        from cellquant.segmentation import segment_objects

        def run(data):
            return segment_objects(data, "cellpose", {"engine": engine})

    try:
        import torch
    except Exception:  # noqa: BLE001
        torch = None
    before = torch.get_num_threads() if torch is not None else None
    _THREADS_APPLIED.add(engine)  # the benchmark sets the thread count itself
    timings, labels = {}, {}
    try:
        for count in counts:
            if torch is not None:
                torch.set_num_threads(count)
            run(image)  # warm-up: loading the model is not timed
            best = None
            for _ in range(max(1, repeats)):
                started = time.perf_counter()
                labels[count] = np.asarray(run(image))
                elapsed = time.perf_counter() - started
                best = elapsed if best is None else min(best, elapsed)
            timings[count] = best
    finally:
        if torch is not None and before:
            torch.set_num_threads(before)
    first = labels[counts[0]]
    identical = all(np.array_equal(first, other) for other in labels.values())
    fastest = min(timings, key=timings.get)
    result = {"engine": engine, "seconds": {str(key): value for key, value in timings.items()}, "threads": fastest, "identical": identical}
    if not identical:
        result["error"] = "Results differed between thread counts; the setting was not saved."
        return result
    data = load_settings()
    data.setdefault("cpu_threads", {})[engine] = {"threads": fastest, "seconds": result["seconds"]}
    save_settings(data)
    return result


def _benchmark_image():
    """A fixed 256 x 256 image of 36 nucleus-sized discs, the same on every computer."""

    import numpy as np

    rng = np.random.default_rng(0)
    yy, xx = np.mgrid[:256, :256]
    image = rng.normal(40, 5, (256, 256))
    for row in range(6):
        for column in range(6):
            cy, cx = 22 + 42 * row, 22 + 42 * column
            image[(yy - cy) ** 2 + (xx - cx) ** 2 <= 100] += 800
    return np.clip(image, 0, 65535).astype(np.uint16)
