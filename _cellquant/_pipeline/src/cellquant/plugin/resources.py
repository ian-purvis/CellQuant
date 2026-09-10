"""Estimate memory/disk needs before allocating large CellQuant snapshots."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

try:
    import psutil
except ImportError:  # pragma: no cover - declared dependency
    psutil = None

from cellquant.persist.staging import local_staging_root, looks_like_cloud_sync_path


# Refuse or warn when a snapshot would exceed this fraction of available RAM.
SAFE_RAM_FRACTION = 0.45
# Warn when free scratch space falls below this many bytes after the estimate.
MIN_SCRATCH_HEADROOM_BYTES = 2 * 1024 ** 3


@dataclass(frozen=True)
class ResourceEstimate:
    """Bytes and paths for a forthcoming preparation step."""

    image_bytes: int | None
    labels_bytes: int | None
    temporary_bytes: int
    available_ram_bytes: int | None
    available_scratch_bytes: int | None
    scratch_root: Path
    scratch_is_cloud: bool
    warnings: tuple[str, ...]
    blocking_errors: tuple[str, ...]

    @property
    def total_preparation_bytes(self) -> int | None:
        if self.image_bytes is None or self.labels_bytes is None:
            return None
        return int(self.image_bytes + self.labels_bytes + self.temporary_bytes)

    def as_preflight_lines(self) -> list[str]:
        total = self.total_preparation_bytes
        if total is None:
            prep = (
                "Estimated preparation: Not estimated"
                f" (image {_fmt_optional_bytes(self.image_bytes)},"
                f" labels {_fmt_optional_bytes(self.labels_bytes)},"
                f" temp {_fmt_bytes(self.temporary_bytes)})"
            )
        else:
            prep = (
                f"Estimated preparation: {_fmt_bytes(total)}"
                f" (image {_fmt_optional_bytes(self.image_bytes)},"
                f" labels {_fmt_optional_bytes(self.labels_bytes)},"
                f" temp {_fmt_bytes(self.temporary_bytes)})"
            )
        lines = [
            f"Scratch: {self.scratch_root}",
            prep,
        ]
        if self.available_ram_bytes is not None:
            lines.append(f"Available RAM: {_fmt_bytes(self.available_ram_bytes)}")
        if self.available_scratch_bytes is not None:
            lines.append(f"Free scratch disk: {_fmt_bytes(self.available_scratch_bytes)}")
        if self.scratch_is_cloud:
            lines.append("Scratch path looks cloud-synced; prefer a local non-synced drive.")
        lines.extend(self.warnings)
        lines.extend(self.blocking_errors)
        return lines


def configure_scratch_root(path: str | Path | None) -> Path:
    """Set ``CELLQUANT_SCRATCH`` for this process and return the resolved root."""

    if path is None or str(path).strip() == "":
        os.environ.pop("CELLQUANT_SCRATCH", None)
        return local_staging_root()
    root = Path(path).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    os.environ["CELLQUANT_SCRATCH"] = str(root.resolve())
    return local_staging_root()


def _array_nbytes(value: Any) -> int | None:
    """Estimate array size from shape/dtype without converting to a NumPy array."""

    if value is None:
        return 0
    # Prefer the object itself. ``ndarray.data`` is a memoryview without ``dtype``.
    for candidate in (value, getattr(value, "data", None)):
        if candidate is None:
            continue
        shape = getattr(candidate, "shape", None)
        dtype = getattr(candidate, "dtype", None)
        if shape is None or dtype is None:
            continue
        try:
            count = int(np.prod(tuple(int(v) for v in shape), dtype=np.int64))
            return count * int(np.dtype(dtype).itemsize)
        except Exception:
            continue
    return None


def _available_ram() -> int | None:
    if psutil is None:
        return None
    try:
        return int(psutil.virtual_memory().available)
    except Exception:
        return None


def _available_scratch(root: Path) -> int | None:
    if psutil is None:
        return None
    try:
        return int(psutil.disk_usage(str(root)).free)
    except Exception:
        return None


def _fmt_bytes(count: int) -> str:
    value = float(max(0, int(count)))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024.0 or unit == "TiB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{int(count)} B"


def _fmt_optional_bytes(count: int | None) -> str:
    if count is None:
        return "Not estimated"
    return _fmt_bytes(count)


def estimate_preparation(
    *,
    image: Any | None = None,
    labels: Any | None = None,
    temporary_bytes: int = 0,
    scratch_root: Path | None = None,
    safe_ram_fraction: float = SAFE_RAM_FRACTION,
) -> ResourceEstimate:
    """Estimate RAM/disk for copying image/labels and optional Cellpose temps."""

    root = Path(scratch_root) if scratch_root is not None else local_staging_root()
    image_bytes = _array_nbytes(image)
    labels_bytes = _array_nbytes(labels)
    temp_bytes = max(0, int(temporary_bytes))
    # Snapshotting typically needs a full extra copy of each array in RAM.
    snapshot_ram = None if image_bytes is None or labels_bytes is None else image_bytes + labels_bytes
    available_ram = _available_ram()
    available_scratch = _available_scratch(root)
    warnings: list[str] = []
    errors: list[str] = []
    cloud = looks_like_cloud_sync_path(root)
    if cloud:
        warnings.append(
            "Scratch is under a cloud-synced folder; set a local CELLQUANT_SCRATCH path."
        )
    if available_ram is not None and snapshot_ram is not None and snapshot_ram > 0:
        limit = int(available_ram * float(safe_ram_fraction))
        if snapshot_ram > limit:
            errors.append(
                f"Estimated snapshot {_fmt_bytes(snapshot_ram)} exceeds "
                f"{safe_ram_fraction:.0%} of available RAM ({_fmt_bytes(available_ram)}). "
                "Reduce Z/crop, use a 2D mode, or free memory before continuing."
            )
        elif snapshot_ram > available_ram * 0.25:
            warnings.append(
                f"Large snapshot (~{_fmt_bytes(snapshot_ram)}); close other apps if this machine is tight on RAM."
            )
    needed_disk = None if image_bytes is None or labels_bytes is None else image_bytes + labels_bytes + temp_bytes
    if available_scratch is not None and needed_disk is not None and needed_disk > 0:
        if needed_disk + MIN_SCRATCH_HEADROOM_BYTES > available_scratch:
            errors.append(
                f"Scratch disk needs ~{_fmt_bytes(needed_disk)} plus headroom; "
                f"only {_fmt_bytes(available_scratch)} free at {root}. "
                "Free space or set CELLQUANT_SCRATCH to a larger local drive."
            )
    return ResourceEstimate(
        image_bytes=image_bytes,
        labels_bytes=labels_bytes,
        temporary_bytes=temp_bytes,
        available_ram_bytes=available_ram,
        available_scratch_bytes=available_scratch,
        scratch_root=root,
        scratch_is_cloud=cloud,
        warnings=tuple(warnings),
        blocking_errors=tuple(errors),
    )


def staging_cache_usage() -> tuple[Path, int]:
    """Return staging root and total bytes used by completed/orphan caches."""

    root = local_staging_root()
    total = 0
    if root.is_dir():
        for path in root.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    continue
    return root, total


def clear_completed_staging_caches(*, dry_run: bool = False) -> tuple[Path, int, int]:
    """Remove staging mirrors that look finished (have a complete status).

    Returns ``(root, removed_dirs, freed_bytes)``. Skips directories that still
    look like in-progress or resumable incomplete work.
    """

    import json
    import shutil

    root = local_staging_root()
    removed = 0
    freed = 0
    if not root.is_dir():
        return root, 0, 0
    for child in list(root.iterdir()):
        if not child.is_dir():
            continue
        status_path = child / "status.json"
        if not status_path.is_file():
            continue
        try:
            marker = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if marker.get("status") != "complete":
            continue
        size = 0
        for path in child.rglob("*"):
            if path.is_file():
                try:
                    size += path.stat().st_size
                except OSError:
                    pass
        if not dry_run:
            shutil.rmtree(child, ignore_errors=True)
        removed += 1
        freed += size
    return root, removed, freed


__all__ = [
    "MIN_SCRATCH_HEADROOM_BYTES",
    "ResourceEstimate",
    "SAFE_RAM_FRACTION",
    "clear_completed_staging_caches",
    "configure_scratch_root",
    "estimate_preparation",
    "staging_cache_usage",
]
