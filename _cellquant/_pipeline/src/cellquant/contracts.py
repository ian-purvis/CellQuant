"""Shared data and event contracts.

Only the integration owner changes this module. Subsystems validate at their
boundaries and never silently transpose, rescale, or relabel these arrays.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol

import numpy as np


ArrayLike = Any


def _shape(value: ArrayLike) -> tuple[int, ...]:
    try:
        return tuple(int(v) for v in value.shape)
    except Exception as exc:  # pragma: no cover - defensive contract boundary
        raise TypeError("data must expose an array-like shape") from exc


def _dtype(value: ArrayLike) -> np.dtype:
    try:
        return np.dtype(value.dtype)
    except Exception as exc:  # pragma: no cover - defensive contract boundary
        raise TypeError("data must expose an array-like dtype") from exc


@dataclass(frozen=True)
class ImageVolume:
    """Canonical microscopy image with axes Z, Y, X, C."""

    data: ArrayLike
    spacing_um: tuple[float, float, float]
    channel_names: tuple[str, ...]
    source: Path
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        shape = _shape(self.data)
        if len(shape) != 4:
            raise ValueError(f"ImageVolume data must be ZYXC; received shape {shape}")
        if shape[-1] != len(self.channel_names):
            raise ValueError("channel_names must match the final C axis")
        if len(set(self.channel_names)) != len(self.channel_names):
            raise ValueError("channel names must be unique")
        if len(self.spacing_um) != 3 or any(
            not np.isfinite(v) or v <= 0 for v in self.spacing_um
        ):
            raise ValueError("spacing_um must contain positive finite (Z,Y,X) micrometres")
        if not np.issubdtype(_dtype(self.data), np.number):
            raise TypeError("image data must be numeric")


@dataclass(frozen=True)
class LabelVolume:
    """Canonical label image with axes Z, Y, X and background zero."""

    data: ArrayLike
    spacing_um: tuple[float, float, float]
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        shape = _shape(self.data)
        if len(shape) != 3:
            raise ValueError(f"LabelVolume data must be ZYX; received shape {shape}")
        if _dtype(self.data) != np.dtype(np.uint32):
            raise TypeError("LabelVolume data must be uint32")
        if len(self.spacing_um) != 3 or any(
            not np.isfinite(v) or v <= 0 for v in self.spacing_um
        ):
            raise ValueError("spacing_um must contain positive finite (Z,Y,X) micrometres")


@dataclass(frozen=True)
class AnalysisContext:
    """Immutable measurement-grid identity shared by images, masks, and review."""

    mode: str
    z_selection: Any
    original_z_depth: int
    spacing_um: tuple[float, float, float]
    shape_zyx: tuple[int, int, int]
    source: Path | None = None
    series: int | None = None
    position: int | None = None

    def __post_init__(self) -> None:
        allowed = {"volume_3d", "stitch_2d", "single_plane_2d", "max_projection_2d"}
        if self.mode not in allowed:
            raise ValueError(f"unsupported analysis mode {self.mode!r}")
        if not isinstance(self.original_z_depth, int) or isinstance(self.original_z_depth, bool):
            raise TypeError("original_z_depth must be an integer")
        if self.original_z_depth < 1:
            raise ValueError("original_z_depth must be >= 1")
        if len(self.spacing_um) != 3 or any(
            not np.isfinite(v) or v <= 0 for v in self.spacing_um
        ):
            raise ValueError("spacing_um must contain positive finite (Z,Y,X) micrometres")
        if len(self.shape_zyx) != 3 or any(int(v) < 1 for v in self.shape_zyx):
            raise ValueError("shape_zyx must be a positive ZYX triple")
        object.__setattr__(self, "shape_zyx", tuple(int(v) for v in self.shape_zyx))
        object.__setattr__(self, "spacing_um", tuple(float(v) for v in self.spacing_um))


@dataclass(frozen=True)
class PipelineEvent:
    kind: str
    run_id: str
    file_id: str
    stage: str
    timestamp_utc: str
    current: int | None = None
    total: int | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        allowed = {
            "stage_started",
            "progress",
            "materialized",
            "warning",
            "artifact_written",
            "stage_finished",
            "cancelled",
            "failed",
        }
        if self.kind not in allowed:
            raise ValueError(f"unknown pipeline event kind {self.kind!r}")
        if (self.current is None) != (self.total is None):
            raise ValueError("event progress requires both current and total")


class EventSink(Protocol):
    def __call__(self, event: PipelineEvent) -> None: ...


class CancellationToken(Protocol):
    @property
    def cancelled(self) -> bool: ...

    def raise_if_cancelled(self) -> None: ...


class PipelineCancelled(RuntimeError):
    """Raised when a run is cancelled cooperatively or by force-stopping a worker."""


class MutableCancellationToken:
    def __init__(self) -> None:
        self._cancelled = False
        self._killed = False
        from threading import RLock
        self._force_stop_lock = RLock()
        self._force_stops: list[Any] = []

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def killed(self) -> bool:
        return self._killed

    def register_force_stop(self, callback) -> None:
        """Register a callback used only by kill() to hard-stop a worker process."""

        with self._force_stop_lock:
            if callback not in self._force_stops:
                self._force_stops.append(callback)
            if self._killed:
                try:
                    callback()
                except Exception:
                    pass

    def unregister_force_stop(self, callback) -> None:
        with self._force_stop_lock:
            try:
                self._force_stops.remove(callback)
            except ValueError:
                return

    def cancel(self) -> None:
        """Request a cooperative stop at the next safe checkpoint."""

        self._cancelled = True

    def kill(self) -> None:
        """Force-stop now: mark cancelled and terminate any registered workers."""

        with self._force_stop_lock:
            self._cancelled = True
            self._killed = True
            for callback in list(self._force_stops):
                try:
                    callback()
                except Exception:
                    # Cancellation remains set even when a callback fails.
                    pass

    def raise_if_cancelled(self) -> None:
        if self._cancelled:
            raise PipelineCancelled("pipeline run cancelled")


def null_event_sink(event: PipelineEvent) -> None:
    """Default sink used by headless callers that do not need live events."""

