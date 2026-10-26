"""Rotate in the View panel: the picture turns, the data and the pointer mapping stay in pixel coordinates.

Needs napari and Qt; skipped without napari.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

napari = pytest.importorskip("napari")

from tests.test_gui_crop import _wait, open_window  # noqa: E402,F401
from tests.test_workflow import _recipe, _write_squares  # noqa: E402


def test_rotation_turns_every_layer_and_keeps_pixel_coordinates(tmp_path: Path, open_window):  # noqa: F811
    from cellquant.controller import AnalysisController

    image = tmp_path / "a.tif"
    _write_squares(image)
    controller = AnalysisController.create(tmp_path / "experiment", "Rotate")
    controller.add_image_paths([image])
    controller.set_recipe(_recipe())
    controller.save()
    shell = open_window(controller.directory)
    shell.run_current()
    assert _wait(shell, 120)
    box = shell._view_box
    layers = list(shell.viewer.layers)
    before = [np.array(layer.data, copy=True) for layer in layers if hasattr(layer.data, "shape")]

    box.rotation.setValue(90)
    labels = shell.viewer.layers["Objects"]
    height, width = labels.data.shape[-2:]
    for layer in shell.viewer.layers:
        if layer.ndim >= 2:
            assert not np.allclose(layer.affine.affine_matrix, np.eye(layer.ndim + 1))
    # the top-left pixel of the image is at the top-right of the turned picture (clockwise)
    corner = labels.data_to_world((0, 0))
    assert corner[-1] > corner[-2]
    back = labels.world_to_data(corner)
    assert np.allclose(back[-2:], (0, 0))
    assert all(np.array_equal(a, np.asarray(layer.data)) for a, layer in zip(before, layers, strict=False))
    # a click on the turned picture reads the object under it, as without rotation
    value = labels.get_value(labels.data_to_world((15, 15)), world=True)
    assert value == labels.data[15, 15]

    box.rotation.setValue(360)
    assert np.allclose(labels.affine.affine_matrix, np.eye(labels.ndim + 1))
    # each image keeps its own angle
    image_id = shell.controller.experiment.images[0].image_id
    box.rotation.setValue(45)
    assert shell.controller.experiment.image(image_id).view_angle == 45
    box.reset()
    assert box.rotation.value() == 45
    box.reset_rotation.click()
    assert box.rotation.value() == 0
    assert shell.controller.experiment.image(image_id).view_angle == 0
    assert (height, width) == labels.data.shape[-2:]
