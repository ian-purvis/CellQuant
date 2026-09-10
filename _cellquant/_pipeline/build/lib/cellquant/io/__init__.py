"""Calibrated TIFF, OME-TIFF, and ND2 readers for canonical ZYXC volumes."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator, Sequence

import numpy as np

from cellquant.contracts import ImageVolume

from ._common import (
    FILE_TYPE_PRESETS,
    ImageMetadata,
    SUPPORTED_SUFFIXES,
    default_channel_names,
    normalize_axes,
    normalize_suffixes,
    path_matches_suffixes,
    require_spacing,
    resolve_file_type_preset,
    validate_channel_names,
    validate_path,
    validate_spacing,
)
from ._nd2 import inspect_nd2, read_nd2
from ._tiff import inspect_tiff, read_tiff

__all__ = [
    "FILE_TYPE_PRESETS",
    "ImageMetadata",
    "SUPPORTED_SUFFIXES",
    "inspect_volume",
    "iter_supported_files",
    "normalize_suffixes",
    "open_volume",
    "resolve_file_type_preset",
]


def inspect_volume(path: str | Path) -> ImageMetadata:
    """Inspect the first image series without loading its pixel array."""

    source = validate_path(path)
    if source.suffix.lower() == ".nd2":
        return inspect_nd2(source)
    return inspect_tiff(source)


def open_volume(
    path: str | Path,
    *,
    series: int = 0,
    position: int = 0,
    lazy: bool = True,
    axes_override: str | None = None,
    spacing_override_um: tuple[float, float, float] | None = None,
) -> ImageVolume:
    """Open one calibrated image as canonical ``(Z, Y, X, C)`` data.

    Axis metadata is never guessed. Missing calibration must be supplied through
    ``spacing_override_um``. Source intensity dtype is preserved.
    """

    source = validate_path(path)
    if source.suffix.lower() == ".nd2":
        if series != 0:
            raise IndexError("ND2 exposes one series; select fields with position")
        data, source_axes, detected_spacing, names, details = read_nd2(
            source, position=position, lazy=lazy
        )
    else:
        if position != 0:
            # Position axes in TIFF are handled below; retain the explicit value.
            pass
        data, source_axes, detected_spacing, names, details = read_tiff(
            source, series=series, lazy=lazy
        )

    axes = axes_override if axes_override is not None else source_axes
    normalized, _ = normalize_axes(data, axes, position=position)
    spacing = (
        validate_spacing(spacing_override_um, source=source)
        if spacing_override_um is not None
        else require_spacing(detected_spacing, source=source)
    )
    channel_count = int(normalized.shape[-1])
    # An explicit axes override may deliberately reinterpret a TIFF sample axis
    # (for example, a short unannotated Z stack reported as RGB samples). Source
    # channel names no longer describe that overridden axis contract.
    if axes_override is not None and len(tuple(names)) != channel_count:
        names = default_channel_names(channel_count)
    channel_names = validate_channel_names(tuple(names), channel_count)
    metadata = {
        **details,
        "axes_override": axes_override,
        "spacing_source": "override" if spacing_override_um is not None else "metadata",
        "detected_spacing_um": detected_spacing,
        "canonical_axes": "ZYXC",
    }
    return ImageVolume(
        data=normalized,
        spacing_um=spacing,
        channel_names=channel_names,
        source=source,
        metadata=metadata,
    )


def iter_supported_files(
    root: str | Path,
    recursive: bool = False,
    *,
    suffixes: Sequence[str] | None = None,
) -> Iterator[Path]:
    """Yield supported image files in stable, case-insensitive path order.

    ``suffixes`` restricts discovery to a subset of
    :data:`~cellquant.io.SUPPORTED_SUFFIXES`. ``None`` keeps every supported type.
    """

    allowed = set(normalize_suffixes(suffixes))
    path = Path(root)
    if not path.exists():
        raise FileNotFoundError(path)
    if path.is_file():
        if path_matches_suffixes(path, allowed):
            yield path
        return
    iterator = path.rglob("*") if recursive else path.glob("*")
    files = (
        item for item in iterator if item.is_file() and path_matches_suffixes(item, allowed)
    )
    yield from sorted(files, key=lambda item: str(item).casefold())


# Kept for compatibility with the pre-package persistence helpers. New label
# persistence belongs to cellquant.persist.
def label_dtype(labels: np.ndarray):
    return np.uint16 if int(np.max(labels, initial=0)) <= np.iinfo(np.uint16).max else np.uint32


def save_labels(path: str | Path, labels: np.ndarray, pixel_size_um=(None, None, None)):
    import tifffile

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    output = np.asarray(labels).astype(label_dtype(labels), copy=False)
    metadata = {"axes": "ZYX"} if output.ndim == 3 else {"axes": "YX"}
    z, y, x = pixel_size_um
    if z:
        metadata.update(spacing=float(z), unit="um")
    kwargs = {}
    if x and y:
        kwargs["resolution"] = (1 / float(x), 1 / float(y))
        kwargs["resolutionunit"] = "NONE"
    tifffile.imwrite(
        destination,
        output,
        imagej=output.dtype == np.uint16,
        metadata=metadata,
        **kwargs,
    )
