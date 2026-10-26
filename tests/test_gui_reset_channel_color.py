"""Reset channel color: a channel picked in the layer list goes back to the color saved in its file.

Needs napari and Qt; skipped without napari.
"""

from __future__ import annotations

from pathlib import Path

import pytest

napari = pytest.importorskip("napari")

from tests.test_gui_crop import _wait, open_window  # noqa: E402,F401
from tests.test_workflow import _recipe, _write_squares  # noqa: E402


def test_reset_channel_color(tmp_path: Path, open_window):  # noqa: F811
    from cellquant.controller import AnalysisController

    image = tmp_path / "a.tif"
    _write_squares(image)
    controller = AnalysisController.create(tmp_path / "experiment", "Colors")
    controller.add_image_paths([image])
    controller.set_recipe(_recipe())
    controller.save()
    shell = open_window(controller.directory)
    shell.run_current()
    assert _wait(shell, 120)
    box = shell._view_box
    layer = box._image_layers()[0]
    original = layer.colormap.name

    shell.viewer.layers.selection = {layer}
    assert not box.reset_color.isEnabled()  # still the file's color
    layer.colormap = "magenta"
    assert box.reset_color.isEnabled()  # picking a color in napari is noticed at once
    box.reset_color.click()
    assert layer.colormap.name == original
    assert not box.reset_color.isEnabled()

    shell.viewer.layers.selection = set()
    layer.colormap = "magenta"
    assert not box.reset_color.isEnabled()  # nothing selected: nothing to reset
