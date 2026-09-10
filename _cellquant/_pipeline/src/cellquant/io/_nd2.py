from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from ._common import ImageMetadata, default_channel_names


def inspect_nd2(path: Path) -> ImageMetadata:
    import nd2

    with nd2.ND2File(path) as handle:
        sizes = {str(axis).upper(): int(size) for axis, size in handle.sizes.items()}
        axes = "".join(sizes)
        shape = tuple(sizes.values())
        dtype = np.dtype(handle.dtype)
        spacing = _spacing(handle)
        reader_spacing = _reader_voxel_size(handle)
        calibrated = _axes_calibrated(handle)
        names = _channel_names(handle, sizes.get("C", 1))
    return ImageMetadata(
        source=path,
        format="nd2",
        shape=shape,
        dtype=dtype,
        axes=axes,
        spacing_um=spacing,
        channel_names=names,
        position_count=sizes.get("P", 1),
        timepoint_count=sizes.get("T", 1),
        metadata={
            "sizes": sizes,
            "calibration_flag": calibrated,
            "reader_voxel_size_um": reader_spacing,
        },
    )


def read_nd2(path: Path, *, position: int, lazy: bool):
    import nd2

    with nd2.ND2File(path) as handle:
        sizes = {str(axis).upper(): int(size) for axis, size in handle.sizes.items()}
        axes = "".join(sizes)
        position_count = int(sizes.get("P", 1))
        # Reject an unavailable field before paying for the pixel read.
        if not 0 <= int(position) < position_count:
            raise IndexError(
                f"{path} exposes {position_count} ND2 position(s); requested position "
                f"{position} is unavailable"
            )
        spacing = _spacing(handle)
        reader_spacing = _reader_voxel_size(handle)
        calibrated = _axes_calibrated(handle)
        names = _channel_names(handle, sizes.get("C", 1))
        data = handle.to_dask() if lazy else handle.asarray()

    details: dict[str, Any] = {
        "format": "nd2",
        "source_axes": axes,
        "source_shape": tuple(sizes.values()),
        "source_dtype": np.dtype(data.dtype).str,
        "series": 0,
        "series_count": 1,
        "position": int(position),
        "position_count": position_count,
        "lazy_requested": lazy,
        "memory_mapped": False,
        "dask_backed": lazy,
        "calibration_flag": calibrated,
        "reader_voxel_size_um": reader_spacing,
    }
    return data, axes, spacing, names, details


def _spacing(handle):
    """Return trusted ZYX spacing in µm, or Nones when calibration is unknown.

    The ``nd2`` package's ``voxel_size()`` falls back to ``(1, 1, 1)`` when the
    file has no calibration metadata. Treat that as missing unless the reader
    reports the axes as calibrated.
    """

    calibrated = _axes_calibrated(handle)
    values = _reader_voxel_size(handle)
    if calibrated is False:
        return (None, None, None)
    if calibrated is None and values == (1.0, 1.0, 1.0):
        # Ambiguous: many uncalibrated ND2 files report unit 1 µm placeholders.
        return (None, None, None)
    return values


def _reader_voxel_size(handle) -> tuple[float | None, float | None, float | None]:
    """Raw ZYX ``voxel_size()`` values, whether or not they can be trusted."""

    try:
        voxel = handle.voxel_size()
    except Exception:
        return (None, None, None)
    return tuple(_positive(getattr(voxel, axis.lower(), None)) for axis in "ZYX")


def _axes_calibrated(handle) -> bool | None:
    """True/False when metadata is explicit; None when the flag is unavailable."""

    for getter in (
        lambda: getattr(handle, "axes_calibrated", None),
        lambda: getattr(handle, "axesCalibrated", None),
        lambda: getattr(getattr(handle, "metadata", None), "axesCalibrated", None),
        lambda: _channel_volume_flags(handle),
        lambda: getattr(getattr(handle, "experiment", None), "axesCalibrated", None),
    ):
        try:
            value = getter()
        except Exception:
            value = None
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            if not value:
                continue
            return all(bool(flag) for flag in value)
        return bool(value)
    try:
        text = str(getattr(handle, "text_info", {}) or {})
        if "Calibration" in text and "µm" in text.casefold():
            return True
    except Exception:
        pass
    return None


def _channel_volume_flags(handle) -> list[bool] | None:
    """``nd2`` records per-axis calibration on each channel's volume metadata."""

    channels = getattr(getattr(handle, "metadata", None), "channels", None) or ()
    flags: list[bool] = []
    for channel in channels:
        value = getattr(getattr(channel, "volume", None), "axesCalibrated", None)
        if value is None:
            return None
        flags.extend(bool(flag) for flag in value)
    return flags or None


def _positive(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if np.isfinite(result) and result > 0 else None


def _channel_names(handle, count: int) -> tuple[str, ...]:
    try:
        names = tuple(str(channel.channel.name) for channel in handle.metadata.channels)
    except Exception:
        return default_channel_names(count)
    return names if len(names) == count else default_channel_names(count)
