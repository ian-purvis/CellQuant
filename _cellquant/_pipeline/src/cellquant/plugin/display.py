"""Napari display helpers for CellQuant channel layers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from cellquant.io.display_colors import resolve_channel_colors


DISPLAY_CHANNEL_FLAG = "cellquant_display_channel"


def channel_colormap(rgb: tuple[float, float, float], *, name: str):
    """Build a napari Colormap from float RGB (display-only)."""

    try:
        from napari.utils.colormaps import Colormap
    except ImportError:  # pragma: no cover - headless / non-gui installs
        return _nearest_named_colormap(rgb)
    r, g, b = rgb
    return Colormap([[0.0, 0.0, 0.0, 1.0], [r, g, b, 1.0]], name=str(name)[:48] or "channel")


def _nearest_named_colormap(rgb: tuple[float, float, float]) -> str:
    named = {
        "blue": (0.0, 0.22, 1.0),
        "green": (0.0, 1.0, 0.0),
        "magenta": (1.0, 0.0, 1.0),
        "yellow": (1.0, 1.0, 0.0),
        "red": (1.0, 0.0, 0.0),
        "cyan": (0.0, 1.0, 1.0),
        "gray": (1.0, 1.0, 1.0),
    }
    best_name = "gray"
    best_dist = float("inf")
    for label, ref in named.items():
        dist = sum((a - b) ** 2 for a, b in zip(rgb, ref))
        if dist < best_dist:
            best_dist = dist
            best_name = label
    return best_name


def source_label(source: str | Path) -> str:
    """Short label for layer names and the open-image switcher."""

    return Path(source).name or str(source)


def display_layer_name(source: str | Path, channel_name: str) -> str:
    """Layer name that groups channels under their file in the layer list."""

    return f"{source_label(source)} · {channel_name}"


def contrast_limits_from_channel(data: Any) -> tuple[float, float]:
    """Match napari histogram Reset: min/max of the mid-Z plane (cheap for lazy)."""

    try:
        shape = tuple(int(v) for v in data.shape)
    except Exception:
        return (0.0, 1.0)
    if not shape:
        return (0.0, 1.0)
    plane = data[shape[0] // 2] if len(shape) >= 3 else data
    if hasattr(plane, "compute"):
        try:
            plane = plane.compute()
        except Exception:
            return (0.0, 1.0)
    try:
        values = np.asarray(plane)
        lo = float(np.nanmin(values))
        hi = float(np.nanmax(values))
    except Exception:
        return (0.0, 1.0)
    if not np.isfinite(lo) or not np.isfinite(hi):
        return (0.0, 1.0)
    if lo == hi:
        return (lo, lo + 1.0) if lo != 0 else (0.0, 1.0)
    return (lo, hi)


def display_channel_kwargs(
    volume_metadata: Mapping[str, Any],
    *,
    channel_index: int,
    channel_name: str,
    channel_names: Sequence[str],
    spacing_um: Sequence[float],
    source: str,
    channel_data: Any | None = None,
    visible: bool = True,
) -> dict[str, Any]:
    """Keyword args for one visible fluorescence channel layer."""

    colors = resolve_channel_colors(
        channel_names, volume_metadata.get("channel_colors")
    )
    rgb = colors[channel_index]
    metadata = {
        **dict(volume_metadata),
        "source": source,
        "spacing_um": tuple(spacing_um),
        "channel_names": tuple(channel_names),
        "canonical_axes": "ZYXC",
        "channel_index": int(channel_index),
        DISPLAY_CHANNEL_FLAG: True,
        "channel_color_rgb": rgb,
        "source_label": source_label(source),
    }
    kwargs: dict[str, Any] = {
        "name": display_layer_name(source, channel_name),
        "rgb": False,
        "scale": tuple(spacing_um),
        "blending": "additive",
        "colormap": channel_colormap(rgb, name=str(channel_name)),
        "visible": bool(visible),
        "metadata": metadata,
    }
    if channel_data is not None:
        clim = contrast_limits_from_channel(channel_data)
        kwargs["contrast_limits"] = clim
        kwargs["contrast_limits_range"] = clim
    return kwargs


def iter_display_channels(viewer) -> list[Any]:
    return [
        layer
        for layer in list(getattr(viewer, "layers", []) or [])
        if getattr(layer, "metadata", {}).get(DISPLAY_CHANNEL_FLAG)
    ]


def open_image_sources(viewer) -> list[str]:
    """Unique source paths currently shown as CellQuant channel layers."""

    sources: list[str] = []
    seen: set[str] = set()
    for layer in iter_display_channels(viewer):
        source = str((getattr(layer, "metadata", {}) or {}).get("source") or "")
        if source and source not in seen:
            seen.add(source)
            sources.append(source)
    return sources


def set_active_image_source(viewer, source: str | Path | None) -> str | None:
    """Show only one acquisition's channels; hide the others. Returns active source."""

    sources = open_image_sources(viewer)
    if not sources:
        return None
    target = str(source) if source is not None else sources[-1]
    if target not in sources:
        target = sources[-1]
    for layer in iter_display_channels(viewer):
        layer_source = str((getattr(layer, "metadata", {}) or {}).get("source") or "")
        try:
            layer.visible = layer_source == target
        except Exception:
            pass
    reset = getattr(viewer, "reset_view", None)
    if callable(reset):
        try:
            reset()
        except Exception:
            pass
    return target
