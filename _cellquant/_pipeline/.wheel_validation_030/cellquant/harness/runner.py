from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sys
import threading
import time
import traceback
from typing import Callable, Mapping
import uuid
import re
import subprocess

import numpy as np
import psutil

from cellquant.config import RunConfig
from cellquant.contracts import (
    CancellationToken,
    EventSink,
    ImageVolume,
    LabelVolume,
    MutableCancellationToken,
    PipelineEvent,
)
from cellquant.io import open_volume
from cellquant.persist import RunStore
from cellquant.viz import make_qc_figures


PipelineRunner = Callable[[ImageVolume, RunConfig, CancellationToken, EventSink], LabelVolume]


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class HarnessCase:
    name: str
    input_path: Path
    spacing_override_um: tuple[float, float, float] | None = None
    axes_override: str | None = None
    series: int = 0
    position: int = 0


@dataclass(frozen=True)
class HarnessResult:
    case: str
    output_dir: Path
    success: bool
    label_count: int | None
    wall_seconds: float
    peak_host_ram_bytes: int
    peak_vram_allocated_bytes: int | None
    peak_vram_reserved_bytes: int | None
    warnings: tuple[str, ...]
    exception: Mapping[str, str] | None


class _ResourceSampler:
    def __init__(self, interval_seconds: float = 0.05) -> None:
        self.interval_seconds = interval_seconds
        self.peak_host_ram_bytes = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="cellquant-resource-sampler", daemon=True)

    def _sample(self) -> None:
        process = psutil.Process()
        rss = process.memory_info().rss
        for child in process.children(recursive=True):
            try:
                rss += child.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        self.peak_host_ram_bytes = max(self.peak_host_ram_bytes, int(rss))

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self._sample()

    def __enter__(self):
        self._sample()
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._thread.join(timeout=1)
        self._sample()


def _cuda_peaks() -> tuple[int | None, int | None]:
    torch = sys.modules.get("torch")
    try:
        if torch is not None and torch.cuda.is_available():
            return int(torch.cuda.max_memory_allocated()), int(torch.cuda.max_memory_reserved())
    except Exception:
        pass
    return None, None


def _distribution_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _gpu_environment() -> dict:
    result: dict[str, object] = {
        "available": False,
        "devices": [],
        "driver_version": None,
        "cuda_runtime_version": None,
    }
    try:
        query = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if query.returncode == 0:
            devices = []
            for line in query.stdout.splitlines():
                fields = [field.strip() for field in line.split(",")]
                if len(fields) >= 3:
                    devices.append(
                        {
                            "name": fields[0],
                            "driver_version": fields[1],
                            "memory_total_mib": int(fields[2]),
                        }
                    )
            result["devices"] = devices
            result["available"] = bool(devices)
            result["driver_version"] = devices[0]["driver_version"] if devices else None
        overview = subprocess.run(
            ["nvidia-smi"], capture_output=True, text=True, timeout=3, check=False
        )
        match = re.search(r"CUDA Version:\s*([0-9.]+)", overview.stdout)
        if match:
            result["cuda_runtime_version"] = match.group(1)
    except (FileNotFoundError, OSError, subprocess.SubprocessError, ValueError):
        pass
    torch = sys.modules.get("torch")
    if torch is not None:
        try:
            result["torch_cuda_version"] = getattr(torch.version, "cuda", None)
            result["torch_cuda_available"] = bool(torch.cuda.is_available())
        except Exception:
            result["torch_cuda_version"] = None
            result["torch_cuda_available"] = None
    else:
        result["torch_cuda_version"] = None
        result["torch_cuda_available"] = None
    return result


def _source_provenance() -> dict:
    """Identify the code actually imported, independently of package metadata."""

    package = importlib.import_module("cellquant")
    source_version = getattr(package, "__version__", None)
    package_file = Path(package.__file__).resolve() if package.__file__ else None
    source_root = package_file.parent if package_file else None
    digest = hashlib.sha256()
    files_hashed = 0
    if source_root and source_root.is_dir():
        for path in sorted(source_root.rglob("*.py"), key=lambda value: value.as_posix()):
            relative = path.relative_to(source_root).as_posix().encode("utf-8")
            digest.update(relative)
            digest.update(b"\0")
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            digest.update(b"\0")
            files_hashed += 1
    installed_version = _distribution_version("cellquant")
    mismatch = (
        source_version is not None
        and installed_version is not None
        and source_version != installed_version
    )
    warnings = []
    if mismatch:
        warnings.append(
            "Imported CellQuant source version differs from installed distribution metadata; "
            "the source version and fingerprint describe the executed code."
        )
    return {
        "source_version": source_version,
        "installed_distribution_version": installed_version,
        "version_mismatch": mismatch,
        "source_file": str(package_file) if package_file else None,
        "source_root": str(source_root) if source_root else None,
        "source_fingerprint_sha256": digest.hexdigest() if files_hashed else None,
        "source_fingerprint_scope": "sorted relative paths and bytes of cellquant/**/*.py",
        "source_files_hashed": files_hashed,
        "vcs_revision": None,
        "exact_reproducibility_claimed": False,
        "reproducibility_limitation": (
            "This inventory and source fingerprint identify the observed run environment, "
            "but do not guarantee reconstruction of the complete operating system, drivers, "
            "native libraries, model cache, or mutable source-tree state."
        ),
        "warnings": warnings,
    }


def _environment_provenance() -> dict:
    packages: dict[str, str] = {}
    try:
        for distribution in importlib.metadata.distributions():
            name = distribution.metadata.get("Name")
            if name:
                packages[str(name).lower()] = distribution.version
    except Exception:
        pass
    memory = psutil.virtual_memory()
    frequency = psutil.cpu_freq()
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "executable": sys.executable,
        "environment": {
            "conda_default_env": os.environ.get("CONDA_DEFAULT_ENV"),
            "conda_prefix": os.environ.get("CONDA_PREFIX"),
            "virtual_env": os.environ.get("VIRTUAL_ENV"),
        },
        "cpu": {
            "processor": platform.processor() or None,
            "physical_cores": psutil.cpu_count(logical=False),
            "logical_cores": psutil.cpu_count(logical=True),
            "max_frequency_mhz": float(frequency.max) if frequency else None,
        },
        "ram": {"total_bytes": int(memory.total)},
        "gpu": _gpu_environment(),
        "torch_version": _distribution_version("torch"),
        "cellpose_version": _distribution_version("cellpose"),
        "cellquant": _source_provenance(),
        "packages": dict(sorted(packages.items())),
    }


def _default_runner(
    image: ImageVolume, config: RunConfig, cancel: CancellationToken, events: EventSink
) -> LabelVolume:
    module = importlib.import_module("cellquant.orchestrator")
    return module.run_pipeline(image, config, cancel, events)


def run_case(
    case: HarnessCase,
    config: RunConfig,
    output_dir: str | Path,
    *,
    pipeline_runner: PipelineRunner | None = None,
    cancel: CancellationToken | None = None,
    raise_on_error: bool = False,
) -> HarnessResult:
    """Run one case and always leave a structured JSON record on failure."""

    if not isinstance(case, HarnessCase) or not isinstance(config, RunConfig):
        raise TypeError("case and config must be HarnessCase and RunConfig")
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    input_fingerprint = _sha256(case.input_path)
    run_id = uuid.uuid4().hex
    store = RunStore.create(output, input_fingerprint, config.fingerprint, run_id=run_id)
    token = cancel or MutableCancellationToken()
    event_values: list[PipelineEvent] = []
    warnings: list[str] = []
    stage_start: dict[str, float] = {}
    stage_seconds: dict[str, float] = {}

    def emit(event: PipelineEvent) -> None:
        if event.run_id != run_id or event.file_id != case.name:
            raise ValueError("pipeline event run_id/file_id does not match harness case")
        event_values.append(event)
        store.append_event(event)
        if event.kind == "warning":
            warnings.append(str(event.details.get("message", event.details)))
        if event.kind == "stage_started":
            stage_start[event.stage] = time.perf_counter()
        elif event.kind in {"stage_finished", "failed", "cancelled"} and event.stage in stage_start:
            stage_seconds[event.stage] = time.perf_counter() - stage_start.pop(event.stage)

    def own_event(kind: str, stage: str, **details) -> PipelineEvent:
        return PipelineEvent(kind, run_id, case.name, stage, _utc(), details=details)

    started = time.perf_counter()
    exception_value: dict[str, str] | None = None
    label_count: int | None = None
    vram_allocated = vram_reserved = None
    with _ResourceSampler() as resources:
        try:
            store.write_config(config)
            emit(own_event("stage_started", "io"))
            io_config = config.raw["io"]
            image = open_volume(
                case.input_path,
                series=case.series,
                position=case.position,
                lazy=bool(io_config["lazy"]),
                axes_override=case.axes_override or io_config.get("axes_override"),
                spacing_override_um=case.spacing_override_um or io_config.get("spacing_override_um"),
            )
            image = ImageVolume(
                image.data,
                image.spacing_um,
                image.channel_names,
                image.source,
                {
                    **image.metadata,
                    "run_id": run_id,
                    "file_id": case.name,
                    "input_fingerprint": input_fingerprint,
                },
            )
            emit(own_event("stage_finished", "io", shape=list(image.data.shape), dtype=str(image.data.dtype)))
            labels = (pipeline_runner or _default_runner)(image, config, token, emit)
            if not isinstance(labels, LabelVolume):
                raise TypeError("pipeline runner must return LabelVolume")
            store.write_labels(labels)
            label_count = int(np.unique(np.asarray(labels.data)).size - (0 in np.asarray(labels.data)))
            emit(own_event("stage_started", "viz"))
            qc_paths = make_qc_figures(image, labels, output, config)
            for path in qc_paths.values():
                store.register_qc_artifact(path)
            emit(own_event("stage_finished", "viz", artifacts=[path.name for path in qc_paths.values()]))
            metrics_path = output / "harness_metrics.csv"
            metrics_path.write_text(
                "case,label_count,input_fingerprint,config_fingerprint\n"
                f"{case.name},{label_count},{input_fingerprint},{config.fingerprint}\n",
                encoding="utf-8",
            )
            store.register_measurement(metrics_path)
        except Exception as exc:
            exception_value = {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
            try:
                emit(own_event("failed", "harness", exception_type=type(exc).__name__, message=str(exc)))
            except Exception:
                pass
        finally:
            vram_allocated, vram_reserved = _cuda_peaks()

    wall_seconds = time.perf_counter() - started
    provenance = {
        "schema_version": 1,
        "case": case.name,
        "source": str(case.input_path.resolve()),
        "input_fingerprint": input_fingerprint,
        "config_fingerprint": config.fingerprint,
        "run_id": run_id,
        "stage_seconds": stage_seconds,
        "wall_seconds": wall_seconds,
        "peak_host_ram_bytes": resources.peak_host_ram_bytes,
        "peak_vram_allocated_bytes": vram_allocated,
        "peak_vram_reserved_bytes": vram_reserved,
        "label_count": label_count,
        "warnings": warnings,
        "exception": exception_value,
        "environment": _environment_provenance(),
    }
    try:
        store.write_provenance(provenance)
        store.commit("failed" if exception_value else "complete")
    except Exception as persist_exc:
        if exception_value is None:
            exception_value = {
                "type": type(persist_exc).__name__,
                "message": str(persist_exc),
                "traceback": traceback.format_exc(),
            }
            store.commit("failed")
    result = HarnessResult(
        case.name,
        output,
        exception_value is None,
        label_count,
        wall_seconds,
        resources.peak_host_ram_bytes,
        vram_allocated,
        vram_reserved,
        tuple(warnings),
        exception_value,
    )
    if exception_value is not None and raise_on_error:
        raise RuntimeError(f"harness case {case.name} failed: {exception_value['message']}")
    return result


def showcase(
    module: str,
    crop: tuple[ImageVolume, LabelVolume],
    config: RunConfig,
    output_dir: str | Path,
):
    """Run one package's public showcase on a recorded image/label crop."""

    if module == "viz":
        from cellquant.viz import showcase_crop

        return showcase_crop(crop[0], crop[1], output_dir, config)
    target = importlib.import_module(f"cellquant.{module}")
    helper = getattr(target, "showcase_crop", None)
    if helper is None:
        raise ValueError(f"module {module!r} does not expose showcase_crop")
    return helper(crop, config, Path(output_dir))
