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
        metadata={"sizes": sizes},
    )


def read_nd2(path: Path, *, position: int, lazy: bool):
    import nd2

    with nd2.ND2File(path) as handle:
        sizes = {str(axis).upper(): int(size) for axis, size in handle.sizes.items()}
        axes = "".join(sizes)
        spacing = _spacing(handle)
        names = _channel_names(handle, sizes.get("C", 1))
        data = handle.to_dask() if lazy else handle.asarray()

    details: dict[str, Any] = {
        "format": "nd2",
        "source_axes": axes,
        "source_shape": tuple(sizes.values()),
        "source_dtype": np.dtype(data.dtype).str,
        "series": 0,
        "series_count": 1,
        "lazy_requested": lazy,
        "memory_mapped": False,
        "dask_backed": lazy,
    }
    return data, axes, spacing, names, details


def _spacing(handle):
    try:
        voxel = handle.voxel_size()
    except Exception:
        return (None, None, None)
    return tuple(_positive(getattr(voxel, axis.lower(), None)) for axis in "ZYX")


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
