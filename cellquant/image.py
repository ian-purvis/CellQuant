"""Image loading.

Reads TIFF, OME-TIFF and Nikon ND2 files. In-memory images are stored as
``(channels, y, x)``. Pixel size, when present, is micrometres per pixel and
is taken only from calibrated metadata. Source files are never modified.

Z-stacks are either reduced to one 2D plane per channel (the maximum
projection, the default, or one chosen slice) or kept as a volume
``(channels, z, y, x)`` for 3D analysis. The choice is part of the analysis
settings and is recorded with every result, so a projection is never applied
silently.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
import tifffile

from cellquant.errors import ImageLoadError

# Channel counts above this are treated as a spatial axis, not channels,
# unless file metadata says otherwise.
_MAX_INFERRED_CHANNELS = 16

IMAGE_SUFFIXES = (".tif", ".tiff", ".nd2")
Z_MODES = ("max_projection", "single_plane", "stitch_slices", "full_3d")
Z_MODES_3D = ("stitch_slices", "full_3d")


@dataclass(frozen=True)
class LoadedImage:
    data: np.ndarray
    source_path: str | None = None
    pixel_size_x: float | None = None
    pixel_size_y: float | None = None
    axes: str = "CYX"
    channel_axis_source: str = "given"
    channel_names: tuple[str, ...] = ()
    pixel_size_z: float | None = None
    position: int = 0
    # Display color of each channel from the file, as (r, g, b) in 0-1; None when the file has none.
    channel_colors: tuple[tuple[float, float, float] | None, ...] = ()
    z_planes: int = 1
    z_mode: str = "none"  # "none" for single-plane files, else a Z_MODES value
    z_index: int | None = None  # the slice used, for single_plane (0-based)
    objective: str = ""

    def __post_init__(self) -> None:
        array = np.asarray(self.data)
        if array.ndim not in (3, 4):
            raise ImageLoadError(
                "Images must have shape (channels, y, x), or (channels, z, y, x) for 3D. "
                f"Received {array.ndim} dimensions."
            )
        if min(array.shape) < 1:
            raise ImageLoadError("Image dimensions must be at least 1.")
        object.__setattr__(self, "data", array)

    @property
    def n_channels(self) -> int:
        return int(self.data.shape[0])

    @property
    def shape_yx(self) -> tuple[int, int]:
        return int(self.data.shape[-2]), int(self.data.shape[-1])

    @property
    def is_3d(self) -> bool:
        return self.data.ndim == 4

    @property
    def spatial_shape(self) -> tuple[int, ...]:
        """(y, x), or (z, y, x) for a volume. The shape of the label image."""

        return tuple(int(value) for value in self.data.shape[1:])

    @property
    def z_description(self) -> str:
        """Plain words for how Z was handled, for the interface and reports."""

        if self.z_planes <= 1:
            return "single plane"
        if self.z_mode == "single_plane":
            return f"slice {int(self.z_index or 0) + 1} of {self.z_planes}"
        if self.z_mode == "stitch_slices":
            return f"3D, {self.z_planes} slices linked slice by slice"
        if self.z_mode == "full_3d":
            return f"3D, {self.z_planes} slices as one volume"
        return f"max projection of {self.z_planes} slices"


@dataclass(frozen=True)
class ImageInfo:
    """What a file contains, read from metadata without loading the pixels."""

    path: str
    position: int = 0
    positions: int = 1
    z_planes: int = 1
    n_channels: int = 1
    height: int = 0
    width: int = 0
    dtype: str = ""
    channel_names: tuple[str, ...] = ()
    pixel_size_x: float | None = None
    pixel_size_y: float | None = None
    pixel_size_z: float | None = None
    objective: str = ""
    channel_colors: tuple[tuple[float, float, float] | None, ...] = ()
    axes: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)


def load_image(
    source: str | Path | np.ndarray | LoadedImage,
    *,
    channel_axis: int | None = None,
    pixel_size_x: float | None = None,
    pixel_size_y: float | None = None,
    position: int = 0,
    z_mode: str = "max_projection",
    z_index: int | None = None,
    pixel_size_z: float | None = None,
) -> LoadedImage:
    """Load one image as ``(channels, y, x)``.

    ``position`` selects a stage position in multi-position ND2 files.
    ``z_mode`` and ``z_index`` say how a Z-stack is handled:
    ``max_projection`` or ``single_plane`` (``z_index`` 0-based; the middle
    slice when omitted) give ``(channels, y, x)``; ``stitch_slices`` and
    ``full_3d`` keep the volume as ``(channels, z, y, x)``. A single-plane
    file is always ``(channels, y, x)``. Explicit pixel sizes override file
    metadata.
    """

    if z_mode not in Z_MODES:
        raise ImageLoadError(f"Unknown Z-stack handling '{z_mode}'. Use one of: {', '.join(Z_MODES)}.")
    if isinstance(source, LoadedImage):
        return _override_pixel_size(source, pixel_size_x, pixel_size_y, pixel_size_z)
    if isinstance(source, np.ndarray):
        data, axes, origin = normalize_array(source, channel_axis=channel_axis)
        return LoadedImage(
            data=data,
            pixel_size_x=pixel_size_x,
            pixel_size_y=pixel_size_y,
            axes=axes,
            channel_axis_source=origin,
        )
    path = Path(source)
    stack = read_stack(path, position=position, channel_axis=channel_axis)
    return reduce_stack(
        stack,
        z_mode=z_mode,
        z_index=z_index,
        pixel_size_x=pixel_size_x,
        pixel_size_y=pixel_size_y,
        pixel_size_z=pixel_size_z,
    )


@dataclass(frozen=True)
class RawStack:
    """Every channel and slice of one acquisition, as read from the file.

    ``zcyx`` is ``(z, channels, y, x)`` with the file's own values and dtype.
    ``meta`` holds what the file says (channel names and colors, pixel sizes,
    objective). Nothing is projected, scaled or cropped.
    """

    zcyx: np.ndarray
    source_path: str
    position: int
    channel_axis_source: str
    meta: dict
    file_axes: str = ""

    @property
    def czyx(self) -> np.ndarray:
        return np.moveaxis(self.zcyx, 0, 1)


def read_stack(source: str | Path, *, position: int = 0, channel_axis: int | None = None) -> RawStack:
    """Read one acquisition with all its channels and Z slices.

    This is the reading step of ``load_image``, before any Z handling, so an
    export of the full stack and an analysis see the same pixels. ND2 files are
    read whole before the position is chosen.
    """

    path = Path(source)
    if not path.is_file():
        raise ImageLoadError("File could not be opened.")
    if path.suffix.lower() == ".nd2":
        array, axes, meta = _read_nd2(path, position)
    else:
        array, axes, meta = _read_tiff(path)
    file_axes = axes or ""
    if axes is None:
        # Unlabelled 2D/3D TIFF: use the older inference rules.
        data, _out_axes, origin = normalize_array(array, channel_axis=channel_axis, axes=None)
        zcyx = data[np.newaxis]
    else:
        zcyx = _to_zcyx(array, axes)
        origin = "nd2_metadata" if path.suffix.lower() == ".nd2" else "tiff_axes"
    return RawStack(
        zcyx=zcyx,
        source_path=str(path),
        position=int(position),
        channel_axis_source=origin,
        meta=meta,
        file_axes=file_axes,
    )


def reduce_stack(
    stack: RawStack,
    *,
    z_mode: str = "max_projection",
    z_index: int | None = None,
    pixel_size_x: float | None = None,
    pixel_size_y: float | None = None,
    pixel_size_z: float | None = None,
) -> LoadedImage:
    """Apply the Z handling of an analysis to a full stack. Explicit pixel sizes override the file's."""

    if z_mode not in Z_MODES:
        raise ImageLoadError(f"Unknown Z-stack handling '{z_mode}'. Use one of: {', '.join(Z_MODES)}.")
    zcyx = stack.zcyx
    meta = stack.meta
    planes = int(zcyx.shape[0])
    if planes == 1:
        data, used_mode, used_index = zcyx[0], "none", None
    elif z_mode in Z_MODES_3D:
        data, used_mode, used_index = np.moveaxis(zcyx, 0, 1), z_mode, None
    elif z_mode == "max_projection":
        data, used_mode, used_index = zcyx.max(axis=0), "max_projection", None
    else:
        used_index = effective_z_index(planes, z_index)
        data, used_mode = zcyx[used_index], "single_plane"
    names = tuple(meta.get("channel_names") or ())
    colors = tuple(meta.get("channel_colors") or ())
    if len(colors) != data.shape[0]:
        colors = ()
    if len(names) != data.shape[0]:  # channels are the first axis in every mode
        names = ()
    return LoadedImage(
        data=np.ascontiguousarray(data),
        source_path=stack.source_path,
        pixel_size_x=pixel_size_x if pixel_size_x is not None else meta.get("pixel_size_x"),
        pixel_size_y=pixel_size_y if pixel_size_y is not None else meta.get("pixel_size_y"),
        axes="CZYX" if data.ndim == 4 else "CYX",
        channel_axis_source=stack.channel_axis_source,
        channel_names=names,
        channel_colors=colors,
        pixel_size_z=pixel_size_z if pixel_size_z is not None else meta.get("pixel_size_z"),
        position=int(stack.position),
        z_planes=planes,
        z_mode=used_mode,
        z_index=used_index,
        objective=str(meta.get("objective") or ""),
    )


def effective_z_index(planes: int, z_index: int | None) -> int:
    """The slice a one-slice analysis uses: the one asked for, or the middle slice."""

    used = planes // 2 if z_index is None else int(z_index)
    if not 0 <= used < planes:
        raise ImageLoadError(f"Slice {used + 1} was chosen, but this image has {planes} slices.")
    return used


def inspect_image(source: str | Path) -> list[ImageInfo]:
    """Describe a file without loading its pixels. One entry per stage position."""

    path = Path(source)
    if not path.is_file():
        raise ImageLoadError("File could not be opened.")
    if path.suffix.lower() == ".nd2":
        return _inspect_nd2(path)
    return [_inspect_tiff(path)]


# ---------------------------------------------------------------------------
# ND2


def _nd2_module():
    try:
        import nd2
    except ImportError as exc:  # pragma: no cover - nd2 is a dependency
        raise ImageLoadError("Reading ND2 files needs the 'nd2' package. Run Install CellQuant.bat and choose Update.") from exc
    return nd2


def _nd2_metadata(handle) -> dict:
    meta: dict = {"channel_names": [], "objective": ""}
    try:
        channels = handle.metadata.channels or []
    except Exception:  # noqa: BLE001 - metadata is optional
        channels = []
    meta["channel_names"] = [str(getattr(getattr(item, "channel", None), "name", "") or "") for item in channels]
    meta["channel_colors"] = [_nd2_color(getattr(item, "channel", None)) for item in channels]
    if channels:
        first = channels[0]
        try:
            meta["objective"] = str(first.microscope.objectiveName or "")
        except Exception:  # noqa: BLE001
            meta["objective"] = ""
        calibrated = (False, False, False)
        try:
            calibrated = tuple(bool(value) for value in first.volume.axesCalibrated)
        except Exception:  # noqa: BLE001
            calibrated = (False, False, False)
        # An uncalibrated ND2 reports 1 x 1 x 1. That is not a pixel size, so it is not used.
        try:
            size = handle.voxel_size()
            if calibrated[0] and calibrated[1] and size.x > 0 and size.y > 0:
                meta["pixel_size_x"] = float(size.x)
                meta["pixel_size_y"] = float(size.y)
            if len(calibrated) > 2 and calibrated[2] and size.z > 0:
                meta["pixel_size_z"] = float(size.z)
        except Exception:  # noqa: BLE001
            pass
    return meta


def _read_nd2(path: Path, position: int) -> tuple[np.ndarray, str, dict]:
    nd2 = _nd2_module()
    try:
        with nd2.ND2File(path) as handle:
            sizes = dict(handle.sizes)
            meta = _nd2_metadata(handle)
            array = np.asarray(handle.asarray())
    except ImageLoadError:
        raise
    except Exception as exc:
        raise ImageLoadError(f"ND2 file could not be read: {exc}") from exc
    axes = "".join(sizes)
    if array.ndim != len(axes):
        raise ImageLoadError(f"ND2 file has an unexpected layout ({axes}).")
    if sizes.get("T", 1) > 1:
        raise ImageLoadError(f"This ND2 file is a time series ({sizes['T']} time points), which is not supported yet.")
    if "P" in axes:
        count = sizes["P"]
        if not 0 <= position < count:
            raise ImageLoadError(f"Position {position + 1} was requested, but this file has {count} positions.")
        array = np.take(array, position, axis=axes.index("P"))
        axes = axes.replace("P", "")
    elif position:
        raise ImageLoadError(f"Position {position + 1} was requested, but this file has one position.")
    if "S" in axes:  # RGB samples: treat as channels
        if "C" in axes and sizes.get("C", 1) > 1:
            raise ImageLoadError("ND2 files with both RGB and channels are not supported.")
        if "C" in axes:
            array = np.take(array, 0, axis=axes.index("C"))
            axes = axes.replace("C", "")
        axes = axes.replace("S", "C")
        meta["channel_names"] = ["Red", "Green", "Blue"][: array.shape[axes.index("C")]]
        meta["channel_colors"] = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)][: array.shape[axes.index("C")]]
    return array, axes, meta


def _inspect_nd2(path: Path) -> list[ImageInfo]:
    nd2 = _nd2_module()
    try:
        with nd2.ND2File(path) as handle:
            sizes = dict(handle.sizes)
            meta = _nd2_metadata(handle)
            dtype = str(handle.dtype)
    except Exception as exc:
        raise ImageLoadError(f"ND2 file could not be read: {exc}") from exc
    notes = []
    if sizes.get("T", 1) > 1:
        notes.append(f"time series with {sizes['T']} time points (not supported yet)")
    if "pixel_size_x" not in meta:
        notes.append("no pixel size in the file")
    positions = int(sizes.get("P", 1))
    channels = int(sizes.get("C", sizes.get("S", 1)))
    return [
        ImageInfo(
            path=str(path),
            position=index,
            positions=positions,
            z_planes=int(sizes.get("Z", 1)),
            n_channels=channels,
            height=int(sizes.get("Y", 0)),
            width=int(sizes.get("X", 0)),
            dtype=dtype,
            channel_names=tuple(meta.get("channel_names") or ()),
            channel_colors=tuple(meta.get("channel_colors") or ()),
            pixel_size_x=meta.get("pixel_size_x"),
            pixel_size_y=meta.get("pixel_size_y"),
            pixel_size_z=meta.get("pixel_size_z"),
            objective=str(meta.get("objective") or ""),
            axes="".join(sizes),
            notes=tuple(notes),
        )
        for index in range(positions)
    ]


# ---------------------------------------------------------------------------
# TIFF


def _read_tiff(path: Path) -> tuple[np.ndarray, str | None, dict]:
    try:
        with tifffile.TiffFile(path) as tif:
            series = tif.series[0]
            array = series.asarray()
            file_axes = series.axes
            meta = _tiff_metadata(tif)
    except ImageLoadError:
        raise
    except Exception as exc:
        raise ImageLoadError("File could not be opened.") from exc
    axes = _usable_axes(file_axes, array.ndim)
    if axes is None and array.ndim > 3:
        raise ImageLoadError(
            f"The layout of this TIFF could not be worked out (axes '{file_axes}'). "
            "Save it from Fiji or the microscope software with channel and slice labels."
        )
    return array, axes, meta


def _inspect_tiff(path: Path) -> ImageInfo:
    try:
        with tifffile.TiffFile(path) as tif:
            series = tif.series[0]
            shape = tuple(series.shape)
            file_axes = series.axes
            dtype = str(series.dtype)
            meta = _tiff_metadata(tif)
    except Exception as exc:
        raise ImageLoadError("File could not be opened.") from exc
    axes = _usable_axes(file_axes, len(shape))
    notes = [] if meta.get("pixel_size_x") else ["no pixel size in the file"]
    if axes is None:
        if len(shape) == 2:
            z, c, h, w = 1, 1, shape[0], shape[1]
        elif len(shape) == 3:
            small = min(range(3), key=lambda index: shape[index])
            c = shape[small]
            h, w = [shape[index] for index in range(3) if index != small]
            z = 1
        else:
            raise ImageLoadError(f"The layout of this TIFF could not be worked out (axes '{file_axes}').")
    else:
        sizes = dict(zip(axes, shape))
        z, c, h, w = sizes.get("Z", 1), sizes.get("C", 1), sizes.get("Y", 0), sizes.get("X", 0)
        if sizes.get("T", 1) > 1:
            notes.append(f"time series with {sizes['T']} time points (not supported yet)")
    return ImageInfo(
        path=str(path),
        z_planes=int(z),
        n_channels=int(c),
        height=int(h),
        width=int(w),
        dtype=dtype,
        channel_names=tuple(meta.get("channel_names") or ()),
        channel_colors=tuple(meta.get("channel_colors") or ()),
        pixel_size_x=meta.get("pixel_size_x"),
        pixel_size_y=meta.get("pixel_size_y"),
        pixel_size_z=meta.get("pixel_size_z"),
        axes=file_axes,
        notes=tuple(notes),
    )


def _usable_axes(file_axes: str | None, ndim: int) -> str | None:
    """Axis letters CellQuant can use, or None when the file does not say."""

    if not file_axes or len(file_axes) != ndim:
        return None
    axes = file_axes.upper().replace("S", "C") if "C" not in file_axes.upper() else file_axes.upper()
    if not set(axes) <= set("TZCYX") or "Y" not in axes or "X" not in axes or len(set(axes)) != len(axes):
        return None
    return axes


def _tiff_metadata(tif: tifffile.TiffFile) -> dict:
    meta: dict = {}
    x, y = _pixel_size_um(tif)
    imagej = tif.imagej_metadata or {}
    if x is None and imagej:
        # ImageJ stores the unit in its own metadata and leaves the TIFF unit blank.
        unit = str(imagej.get("unit", "")).casefold()
        if unit in {"micron", "microns", "um", "µm", "\\u00b5m"}:
            x, y = _pixel_size_raw(tif)
    if x is not None and y is not None:
        meta["pixel_size_x"], meta["pixel_size_y"] = x, y
    spacing = imagej.get("spacing") if imagej else None
    if spacing and x is not None:
        meta["pixel_size_z"] = float(spacing)
    labels = imagej.get("Labels") if imagej else None
    if isinstance(labels, (list, tuple)):
        meta["channel_names"] = [str(label) for label in labels]
    colors = _imagej_colors(imagej) or _ome_colors(tif)
    if colors:
        meta["channel_colors"] = colors
    return meta


def _imagej_colors(imagej: dict) -> list[tuple[float, float, float] | None]:
    """Channel colors from ImageJ/Fiji lookup tables: the brightest entry of each LUT."""

    luts = imagej.get("LUTs") if imagej else None
    if not isinstance(luts, (list, tuple)) or not luts:
        return []
    colors: list[tuple[float, float, float] | None] = []
    for lut in luts:
        table = np.asarray(lut)
        if table.ndim != 2 or table.shape[0] != 3 or table.shape[1] < 2:
            colors.append(None)
            continue
        top = table[:, -1].astype(float) / (255.0 if table.dtype == np.uint8 or table.max() > 1 else 1.0)
        colors.append(tuple(float(value) for value in np.clip(top, 0, 1)))  # type: ignore[arg-type]
    return colors


def _ome_colors(tif: tifffile.TiffFile) -> list[tuple[float, float, float] | None]:
    """Channel colors from OME-XML: a signed 32-bit RGBA integer per channel."""

    import re

    xml = getattr(tif, "ome_metadata", None)
    if not xml:
        return []
    colors: list[tuple[float, float, float] | None] = []
    for tag in re.findall(r"<(?:\w+:)?Channel\b[^>]*>", xml):
        match = re.search(r'\bColor="(-?\d+)"', tag)
        if match is None:
            colors.append(None)
            continue
        value = int(match.group(1)) & 0xFFFFFFFF
        colors.append((((value >> 24) & 255) / 255.0, ((value >> 16) & 255) / 255.0, ((value >> 8) & 255) / 255.0))
    return colors if any(color is not None for color in colors) else []


def _nd2_color(channel) -> tuple[float, float, float] | None:
    """The channel's display color in NIS-Elements."""

    if channel is None:
        return None
    color = getattr(channel, "color", None)
    if color is not None and all(hasattr(color, name) for name in ("r", "g", "b")):
        return (color.r / 255.0, color.g / 255.0, color.b / 255.0)
    raw = getattr(channel, "colorRGBA", getattr(channel, "colorRGB", None))
    if isinstance(raw, int):
        # Stored as ABGR: red in the lowest byte.
        return ((raw & 255) / 255.0, ((raw >> 8) & 255) / 255.0, ((raw >> 16) & 255) / 255.0)
    return None


def _to_zcyx(array: np.ndarray, axes: str) -> np.ndarray:
    """Rearrange to (Z, C, Y, X), adding length-1 axes where missing."""

    axes = axes.upper()
    if "T" in axes:
        if array.shape[axes.index("T")] > 1:
            raise ImageLoadError("Time series are not supported yet. Save one time point.")
        array = np.take(array, 0, axis=axes.index("T"))
        axes = axes.replace("T", "")
    for letter in "ZC":
        if letter not in axes:
            array = np.expand_dims(array, 0)
            axes = letter + axes
    unknown = set(axes) - set("ZCYX")
    if unknown or len(axes) != 4:
        raise ImageLoadError(f"Unsupported image layout ({axes}).")
    order = [axes.index(letter) for letter in "ZCYX"]
    return np.transpose(array, order)


def normalize_array(
    array: np.ndarray,
    *,
    channel_axis: int | None = None,
    axes: str | None = None,
) -> tuple[np.ndarray, str, str]:
    """Return ``(channels, y, x)``, the resulting axis order, and how channels were chosen.

    For arrays passed in directly. Arrays with a Z axis must be reduced first.
    """

    array = np.asarray(array)
    if array.ndim == 2:
        return array[np.newaxis, ...], "CYX", "single_plane"
    if array.ndim != 3:
        raise ImageLoadError(
            "Arrays passed in directly must be 2D or (channels, y, x). "
            f"This one has {array.ndim} dimensions"
            + (f" ({axes})." if axes else ".")
        )
    if channel_axis is not None:
        if channel_axis not in (0, -1, 2):
            raise ImageLoadError("channel_axis must be 0 or -1 for a 3D array.")
        if channel_axis in (-1, 2):
            return np.moveaxis(array, -1, 0), "CYX", "argument"
        return array, "CYX", "argument"
    if axes and "C" in axes and "Z" not in axes and "T" not in axes:
        return _to_zcyx(array, axes)[0], "CYX", "tiff_axes"
    leading, middle, trailing = array.shape
    if leading <= _MAX_INFERRED_CHANNELS and leading < middle and leading < trailing:
        return array, "CYX", "inferred"
    if trailing <= _MAX_INFERRED_CHANNELS and trailing < leading and trailing < middle:
        return np.moveaxis(array, -1, 0), "CYX", "inferred"
    raise ImageLoadError(
        "Channel axis could not be determined. Pass channel_axis=0 for "
        "(channels, y, x) or channel_axis=-1 for (y, x, channels)."
    )


def _override_pixel_size(
    image: LoadedImage,
    pixel_size_x: float | None,
    pixel_size_y: float | None,
    pixel_size_z: float | None = None,
) -> LoadedImage:
    if pixel_size_x is None and pixel_size_y is None and pixel_size_z is None:
        return image
    return replace(
        image,
        pixel_size_x=pixel_size_x if pixel_size_x is not None else image.pixel_size_x,
        pixel_size_y=pixel_size_y if pixel_size_y is not None else image.pixel_size_y,
        pixel_size_z=pixel_size_z if pixel_size_z is not None else image.pixel_size_z,
    )


def _pixel_size_um(tif: tifffile.TiffFile) -> tuple[float | None, float | None]:
    """Read µm/pixel from TIFF resolution tags when the unit is physical.

    Resolution without a physical unit is ignored so a raw tag is never
    treated as micrometres.
    """

    page = tif.pages[0]
    tags = page.tags
    if "XResolution" not in tags or "YResolution" not in tags:
        return None, None
    unit = int(tags["ResolutionUnit"].value) if "ResolutionUnit" in tags else 1
    # TIFF ResolutionUnit: 2 = inch, 3 = centimetre. 1 = no absolute unit.
    if unit == 2:
        microns_per_unit = 25400.0
    elif unit == 3:
        microns_per_unit = 10000.0
    else:
        return None, None
    x_resolution = _rational(tags["XResolution"].value)
    y_resolution = _rational(tags["YResolution"].value)
    if x_resolution <= 0 or y_resolution <= 0:
        return None, None
    return microns_per_unit / x_resolution, microns_per_unit / y_resolution


def _pixel_size_raw(tif: tifffile.TiffFile) -> tuple[float | None, float | None]:
    """Pixels per unit as unit per pixel, when another source says the unit is µm."""

    tags = tif.pages[0].tags
    if "XResolution" not in tags or "YResolution" not in tags:
        return None, None
    x_resolution = _rational(tags["XResolution"].value)
    y_resolution = _rational(tags["YResolution"].value)
    if x_resolution <= 0 or y_resolution <= 0:
        return None, None
    return 1.0 / x_resolution, 1.0 / y_resolution


def _rational(value: object) -> float:
    if isinstance(value, tuple) and len(value) == 2 and value[1]:
        return float(value[0]) / float(value[1])
    return float(value)  # type: ignore[arg-type]
