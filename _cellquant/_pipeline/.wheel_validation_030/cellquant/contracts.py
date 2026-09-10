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
    """Raised only at cooperative cancellation checkpoints."""


class MutableCancellationToken:
    def __init__(self) -> None:
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        self._cancelled = True

    def raise_if_cancelled(self) -> None:
        if self._cancelled:
            raise PipelineCancelled("pipeline run cancelled")


def null_event_sink(event: PipelineEvent) -> None:
    """Default sink used by headless callers that do not need live events."""

