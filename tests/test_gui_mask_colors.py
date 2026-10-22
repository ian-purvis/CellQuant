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
    from cellquant.gui.app import MASK_PALETTE, mask_colors

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
    assert len(MASK_PALETTE) == 64 and len(set(MASK_PALETTE)) == 64

    assert mask_colors() == []  # default: every object its own colour
    assert panel.mask_colors_button.text() == "All different"
    assert len(set(_colors(layers[0], ids))) == len(ids)

    panel._pick_mask_colors()
    dialog = panel._mask_dialog
    assert dialog.all_different.isChecked() and not dialog._grid_holder.isEnabled()
    dialog.all_different.setChecked(False)
    assert mask_colors() == []  # nothing ticked yet: still all different
    red, blue = "#ff0000", "#0000ff"
    dialog.swatches[red].setChecked(True)
    dialog.swatches[blue].setChecked(True)
    assert mask_colors() == [red, blue]
    assert panel.mask_colors_button.text() == "2 colours"
    for layer in layers:
        colors = _colors(layer, ids)
        assert colors[0] == colors[2] == colors[4] == (1.0, 0.0, 0.0, 1.0)
        assert colors[1] == colors[3] == (0.0, 0.0, 1.0, 1.0)

    shell.run_current()  # layers are rebuilt or refilled: the choice stays
    assert _wait(shell, 120)
    for layer in (shell.viewer.layers["Objects"], shell.viewer.layers["Object fills"]):
        assert tuple(layer.colormap.colors[1]) == (1.0, 0.0, 0.0, 1.0)

    dialog.all_different.setChecked(True)
    assert mask_colors() == []
    layer = shell.viewer.layers["Objects"]
    layer.data = many.copy()
    assert len(set(_colors(layer, ids))) == len(ids)
    QSettings("CellQuant", "CellQuant").remove("view/mask_colors")


def test_mask_colors_read_back(tmp_path: Path):
    """Several colours survive a restart: stored text, a list from the backend, or an older comma string."""
    from qtpy.QtCore import QSettings

    from cellquant.gui.app import _settings, mask_colors

    pair = ["#ff0000", "#0000ff"]
    try:
        _settings().setValue("view/mask_colors", ";".join(pair))
        _settings().sync()
        assert mask_colors() == pair  # read through a fresh QSettings
        for stored in (pair, "#ff0000,#0000ff", ["#ff0000", "#not-a-colour", "#0000ff"]):
            _settings().setValue("view/mask_colors", stored)
            assert mask_colors() == pair
    finally:
        QSettings("CellQuant", "CellQuant").remove("view/mask_colors")
