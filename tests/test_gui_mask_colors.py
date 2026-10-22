"""Mask colours in step 3: every object its own colour, or a fixed number of colours used in turn.

Needs napari and Qt; skipped without napari.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

napari = pytest.importorskip("napari")

from tests.test_gui_crop import _wait, open_window  # noqa: E402,F401
from tests.test_workflow import _recipe, _write_squares  # noqa: E402


def _colors(layer, labels):
    return [tuple(np.round(layer.get_color(label), 3)) for label in labels]


def test_mask_colors_option(tmp_path: Path, open_window):  # noqa: F811
    from qtpy.QtCore import QSettings

    from cellquant.controller import AnalysisController
    from cellquant.gui.app import mask_color_count

    QSettings("CellQuant", "CellQuant").remove("view/mask_colors")
    image = tmp_path / "a.tif"
    _write_squares(image)
    controller = AnalysisController.create(tmp_path / "experiment", "Mask colours")
    controller.add_image_paths([image])
    controller.set_recipe(_recipe())
    controller.save()
    shell = open_window(controller.directory)
    shell.run_current()
    assert _wait(shell, 120)
    panel = shell._edit_panel
    layers = [shell.viewer.layers["Objects"], shell.viewer.layers["Object fills"]]
    many = np.zeros((20, 60), dtype=np.int32)  # the test image has few objects: show five
    for index in range(5):
        many[5:15, index * 12 : index * 12 + 8] = index + 1
    for layer in layers:
        layer.data = many.copy()
    ids = [1, 2, 3, 4, 5]

    assert mask_color_count() == 0  # default: every object its own colour
    assert len(set(_colors(layers[0], ids))) == len(ids)
    assert panel.mask_colors_row.isVisible() or not panel.isVisible()

    panel.mask_colors.setValue(2)
    assert mask_color_count() == 2
    for layer in layers:
        colors = _colors(layer, ids)
        assert len(set(colors)) == 2
        assert colors[0] == colors[2] and colors[0] != colors[1]

    panel.mask_colors.setValue(0)
    assert len(set(_colors(layers[0], ids))) == len(ids)

    panel.mask_colors.setValue(3)
    shell.run_current()  # layers are rebuilt or refilled: the choice stays
    assert _wait(shell, 120)
    for layer in (shell.viewer.layers["Objects"], shell.viewer.layers["Object fills"]):
        assert len(set(_colors(layer, ids))) == 3
    QSettings("CellQuant", "CellQuant").remove("view/mask_colors")
