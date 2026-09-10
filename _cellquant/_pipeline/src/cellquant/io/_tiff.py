from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
import tifffile

from ._common import ImageMetadata, default_channel_names, validate_index


def inspect_tiff(path: Path, *, series: int = 0) -> ImageMetadata:
    with tifffile.TiffFile(path) as tif:
        validate_index(series, len(tif.series), "series")
        selected = tif.series[series]
        spacing, names = _tiff_metadata(tif, series, _channel_count(selected.axes, selected.shape))
        axes = str(selected.axes).upper()
        return ImageMetadata(
            source=path,
            format="ome-tiff" if tif.is_ome else "tiff",
            shape=tuple(int(value) for value in selected.shape),
            dtype=np.dtype(selected.dtype),
            axes=axes,
            spacing_um=spacing,
            channel_names=names,
            series_count=len(tif.series),
            position_count=_axis_size(axes, selected.shape, "P"),
            timepoint_count=_axis_size(axes, selected.shape, "T"),
            metadata={"is_ome": bool(tif.is_ome), "is_imagej": bool(tif.is_imagej)},
        )


def read_tiff(path: Path, *, series: int, lazy: bool):
    with tifffile.TiffFile(path) as tif:
        validate_index(series, len(tif.series), "series")
        selected = tif.series[series]
        spacing, names = _tiff_metadata(tif, series, _channel_count(selected.axes, selected.shape))
        axes = str(selected.axes).upper()
        shape = tuple(int(value) for value in selected.shape)
        dtype = np.dtype(selected.dtype)
        series_count = len(tif.series)
        is_ome = bool(tif.is_ome)
        is_imagej = bool(tif.is_imagej)

    memory_mapped = False
    if lazy:
        try:
            data = tifffile.memmap(path, series=series, mode="r")
            memory_mapped = True
        except (ValueError, TypeError):
            with tifffile.TiffFile(path) as tif:
                data = tif.series[series].asarray()
    else:
        with tifffile.TiffFile(path) as tif:
            data = tif.series[series].asarray()

    details = {
        "format": "ome-tiff" if is_ome else "tiff",
        "source_axes": axes,
        "source_shape": shape,
        "source_dtype": dtype.str,
        "series": series,
        "series_count": series_count,
        "is_imagej": is_imagej,
        "lazy_requested": lazy,
        "memory_mapped": memory_mapped,
    }
    return data, axes, spacing, names, details


def _tiff_metadata(tif: tifffile.TiffFile, series: int, channels: int):
    if tif.ome_metadata:
        return _ome_metadata(tif.ome_metadata, series, channels)
    return _imagej_metadata(tif), default_channel_names(channels)


def _ome_metadata(xml: str, series: int, channels: int):
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError("OME-TIFF contains invalid OME-XML metadata") from exc
    images = [element for element in root.iter() if element.tag.endswith("Image")]
    if series >= len(images):
        raise ValueError(f"OME-XML does not describe TIFF series {series}")
    pixels = next(
        (element for element in images[series].iter() if element.tag.endswith("Pixels")),
        None,
    )
    if pixels is None:
        raise ValueError(f"OME-XML image {series} has no Pixels element")
    spacing = tuple(_physical_size_um(pixels, axis) for axis in "ZYX")
    channel_elements = [
        element for element in pixels if element.tag.endswith("Channel")
    ]
    names = tuple(
        (channel_elements[index].get("Name") or f"C{index + 1}")
        if index < len(channel_elements)
        else f"C{index + 1}"
        for index in range(channels)
    )
    return spacing, names


def _physical_size_um(pixels: ET.Element, axis: str) -> float | None:
    raw = pixels.get(f"PhysicalSize{axis}")
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    unit = (pixels.get(f"PhysicalSize{axis}Unit") or "um").strip().lower()
    unit = unit.replace("µ", "u").replace("μ", "u")
    factors = {"um": 1.0, "nm": 1e-3, "mm": 1e3, "cm": 1e4, "m": 1e6}
    factor = factors.get(unit)
    if factor is None or not np.isfinite(value) or value <= 0:
        return None
    return value * factor


def _imagej_metadata(tif: tifffile.TiffFile):
    metadata = tif.imagej_metadata or {}
    unit = str(metadata.get("unit", "")).strip().lower()
    unit = unit.replace("µ", "u").replace("μ", "u")
    factors = {"um": 1.0, "micron": 1.0, "microns": 1.0, "nm": 1e-3, "mm": 1e3}
    factor = factors.get(unit)
    if factor is None:
        return (None, None, None)
    z = _positive(metadata.get("spacing"), factor)
    page = tif.pages[0]
    x = _resolution_spacing(page.tags.get("XResolution"), factor)
    y = _resolution_spacing(page.tags.get("YResolution"), factor)
    return (z, y, x)


def _resolution_spacing(tag, factor: float) -> float | None:
    if tag is None:
        return None
    value = tag.value
    try:
        pixels_per_unit = float(value[0]) / float(value[1])
    except (TypeError, ValueError, ZeroDivisionError):
        try:
            pixels_per_unit = float(value)
        except (TypeError, ValueError):
            return None
    if not np.isfinite(pixels_per_unit) or pixels_per_unit <= 0:
        return None
    return factor / pixels_per_unit


def _positive(value, factor: float) -> float | None:
    try:
        result = float(value) * factor
    except (TypeError, ValueError):
        return None
    return result if np.isfinite(result) and result > 0 else None


def _channel_count(axes: str, shape: tuple[int, ...]) -> int:
    axes = axes.upper().replace("S", "C")
    return int(shape[axes.index("C")]) if "C" in axes else 1


def _axis_size(axes: str, shape: tuple[int, ...], axis: str) -> int:
    return int(shape[axes.index(axis)]) if axis in axes else 1
