from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


SUPPORTED_SUFFIXES = (".tif", ".tiff", ".nd2")


@dataclass(frozen=True)
class ImageMetadata:
    """Metadata available without committing to an image materialization.

    ``spacing_um`` contains ``None`` for calibration absent from the source.
    In contrast, :func:`open_volume` requires a complete calibrated spacing.
    """

    source: Path
    format: str
    shape: tuple[int, ...]
    dtype: np.dtype
    axes: str
    spacing_um: tuple[float | None, float | None, float | None]
    channel_names: tuple[str, ...]
    series_count: int = 1
    position_count: int = 1
    timepoint_count: int = 1
    metadata: dict[str, Any] | None = None


def validate_path(path: str | Path) -> Path:
    result = Path(path)
    if not result.is_file():
        raise FileNotFoundError(result)
    if result.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError(
            f"Unsupported image type {result.suffix!r}; expected TIFF, OME-TIFF, or ND2"
        )
    return result


def validate_index(value: int, size: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if not 0 <= value < size:
        raise IndexError(f"{name} {value} is outside the available range 0..{size - 1}")
    return value


def normalize_axes(
    data: Any,
    axes: str,
    *,
    position: int = 0,
) -> tuple[Any, str]:
    """Normalize explicitly named source dimensions to canonical ZYXC."""

    axes = str(axes).upper().replace("S", "C")
    if len(axes) != data.ndim:
        raise ValueError(
            f"Axes {axes!r} do not match the {data.ndim}-dimensional source shape"
        )
    unknown = sorted(set(axes) - set("TPZYXC"))
    if unknown:
        raise ValueError(
            f"Ambiguous or unsupported source axes {axes!r} (unknown: {''.join(unknown)}); "
            "provide axes_override"
        )
    if len(set(axes)) != len(axes):
        raise ValueError(f"Ambiguous source axes {axes!r}: every axis must be named once")
    if "Y" not in axes or "X" not in axes:
        raise ValueError(f"Source axes must contain Y and X; received {axes!r}")

    if "T" in axes:
        axis = axes.index("T")
        if data.shape[axis] != 1:
            raise ValueError(
                f"Source has {data.shape[axis]} time points; open_volume does not select time"
            )
        data = _take(data, axis, 0)
        axes = axes.replace("T", "")

    if "P" in axes:
        axis = axes.index("P")
        validate_index(position, int(data.shape[axis]), "position")
        data = _take(data, axis, position)
        axes = axes.replace("P", "")
    elif position != 0:
        raise IndexError("position was specified, but the source has no position axis")

    for missing in "ZC":
        if missing not in axes:
            data = np.expand_dims(data, axis=data.ndim)
            axes += missing

    order = tuple(axes.index(axis) for axis in "ZYXC")
    return data.transpose(order), axes


def _take(data: Any, axis: int, index: int) -> Any:
    key = [slice(None)] * data.ndim
    key[axis] = index
    return data[tuple(key)]


def validate_spacing(
    spacing: tuple[float | None, float | None, float | None] | Any,
    *,
    source: Path,
) -> tuple[float, float, float]:
    try:
        values = tuple(float(value) for value in spacing)
    except (TypeError, ValueError) as exc:
        raise ValueError("spacing_override_um must contain exactly three numeric values") from exc
    if len(values) != 3 or any(not np.isfinite(value) or value <= 0 for value in values):
        raise ValueError("spacing must contain positive finite (Z,Y,X) micrometres")
    return values  # type: ignore[return-value]


def require_spacing(
    spacing: tuple[float | None, float | None, float | None],
    *,
    source: Path,
) -> tuple[float, float, float]:
    if any(value is None for value in spacing):
        missing = ", ".join(
            axis for axis, value in zip(("Z", "Y", "X"), spacing, strict=True) if value is None
        )
        raise ValueError(
            f"{source} has no calibrated {missing} spacing; provide spacing_override_um=(z, y, x)"
        )
    return validate_spacing(spacing, source=source)


def default_channel_names(count: int) -> tuple[str, ...]:
    return tuple(f"C{index + 1}" for index in range(count))


def validate_channel_names(names: tuple[str, ...], count: int) -> tuple[str, ...]:
    if len(names) != count:
        raise ValueError(f"Source declares {len(names)} channels but its C axis has size {count}")
    if any(not name for name in names) or len(set(names)) != len(names):
        raise ValueError("Source channel names must be non-empty and unique")
    return names
