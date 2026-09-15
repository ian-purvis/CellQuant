"""npe2 reader so napari File→Open / drag-and-drop can load CellQuant images."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from cellquant.io import SUPPORTED_SUFFIXES, open_volume
from cellquant.plugin.display import display_channel_kwargs


def napari_get_reader(path: str | list[str]) -> Callable | None:
    """Return a reader function when every path is a supported image file."""

    paths = [path] if isinstance(path, str) else list(path)
    if not paths:
        return None
    for item in paths:
        suffix = Path(item).suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            return None
    return _read_paths


def _read_paths(path: str | list[str]) -> list[tuple[Any, dict[str, Any], str]]:
    paths = [Path(item) for item in ([path] if isinstance(path, str) else list(path))]
    layers: list[tuple[Any, dict[str, Any], str]] = []
    active_source = str(paths[-1]) if paths else ""
    for item in paths:
        layers.extend(_layers_for_path(item, visible_source=active_source))
    return layers


def _layers_for_path(
    path: Path, *, visible_source: str
) -> list[tuple[Any, dict[str, Any], str]]:
    volume = open_volume(path, lazy=True)
    metadata = dict(volume.metadata)
    source_key = str(volume.source)
    visible = source_key == visible_source
    layers: list[tuple[Any, dict[str, Any], str]] = []
    for index, channel_name in enumerate(volume.channel_names):
        channel_data = volume.data[..., index]
        kwargs = display_channel_kwargs(
            metadata,
            channel_index=index,
            channel_name=channel_name,
            channel_names=volume.channel_names,
            spacing_um=volume.spacing_um,
            source=source_key,
            channel_data=channel_data,
            visible=visible,
        )
        layers.append((channel_data, kwargs, "image"))
    return layers
