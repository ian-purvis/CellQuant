"""Tests for microscope-style channel colors and the napari reader."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from cellquant.io.display_colors import (
    color_from_channel_name,
    normalize_rgb,
    resolve_channel_colors,
)
from cellquant.plugin.display import display_channel_kwargs
from cellquant.plugin.reader import napari_get_reader


def test_normalize_rgb_from_nd2_style_object():
    class Color:
        r, g, b = 0, 56, 255

    assert normalize_rgb(Color()) == (0.0, 56 / 255, 1.0)


def test_resolve_prefers_metadata_then_name_hints():
    colors = resolve_channel_colors(
        ("DAPI", "mystery", "AF647"),
        ((0.0, 0.2, 1.0), None, None),
    )
    assert colors[0] == (0.0, 0.2, 1.0)
    assert colors[1] is not None
    assert color_from_channel_name("AF647") == (1.0, 0.0, 1.0)
    assert colors[2] == (1.0, 0.0, 1.0)


def test_display_channel_kwargs_are_additive_and_colored():
    data = np.arange(24, dtype=np.uint16).reshape(2, 3, 4)
    kwargs = display_channel_kwargs(
        {"channel_colors": ((0.0, 0.22, 1.0),)},
        channel_index=0,
        channel_name="DAPI",
        channel_names=("DAPI",),
        spacing_um=(1.0, 0.5, 0.5),
        source=r"C:\data\sample.nd2",
        channel_data=data,
    )
    assert kwargs["name"] == "sample.nd2 · DAPI"
    assert kwargs["blending"] == "additive"
    assert kwargs["metadata"]["cellquant_display_channel"] is True
    assert kwargs["colormap"] is not None
    assert kwargs["contrast_limits"] == (float(data[1].min()), float(data[1].max()))
    assert kwargs["contrast_limits_range"] == kwargs["contrast_limits"]


def test_set_active_image_source_toggles_visibility():
    from cellquant.plugin.display import set_active_image_source

    class Layer:
        def __init__(self, source, visible=True):
            self.metadata = {"cellquant_display_channel": True, "source": source}
            self.visible = visible

    class Viewer:
        def __init__(self):
            self.layers = [
                Layer("a.nd2", True),
                Layer("a.nd2", True),
                Layer("b.nd2", True),
            ]
            self.reset_calls = 0

        def reset_view(self):
            self.reset_calls += 1

    viewer = Viewer()
    assert set_active_image_source(viewer, "a.nd2") == "a.nd2"
    assert [layer.visible for layer in viewer.layers] == [True, True, False]
    assert set_active_image_source(viewer, "b.nd2") == "b.nd2"
    assert [layer.visible for layer in viewer.layers] == [False, False, True]
    assert viewer.reset_calls == 2


def test_reader_rejects_unsupported_suffix(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("nope")
    assert napari_get_reader(str(path)) is None


def test_reader_accepts_nd2_suffix_without_opening():
    # Suffix gate only; actual open is covered by integration I/O tests.
    assert napari_get_reader(r"C:\data\sample.nd2") is not None
    assert napari_get_reader([r"C:\data\a.tif", r"C:\data\b.tiff"]) is not None
