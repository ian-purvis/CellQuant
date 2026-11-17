"""The view fits the image once its layers have their µm scale: the first image of an experiment fills the window and is
centred; so does the next image; a new pixel size fits it again.

Needs napari and Qt; skipped without napari.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

napari = pytest.importorskip("napari")

from tests.test_gui_crop import _events, _wait, open_window  # noqa: E402,F401
from tests.test_workflow import _recipe, _write_squares  # noqa: E402


def _extent_centre(viewer):
    extent = np.asarray(viewer.layers.extent.world, dtype=float)
    return (extent[0] + extent[1])[-2:] / 2


def test_first_image_with_a_pixel_size_is_fitted_and_so_is_the_next(tmp_path: Path, open_window):  # noqa: F811
    from cellquant.controller import AnalysisController

    images = [tmp_path / "a.tif", tmp_path / "b.tif"]
    for image in images:
        _write_squares(image)
    controller = AnalysisController.create(tmp_path / "experiment", "View fit")
    controller.add_image_paths(images)
    controller.set_recipe(_recipe())
    for record in controller.experiment.images:
        controller.set_pixel_size(record.image_id, 0.5, 0.5, None)
    controller.save()
    shell = open_window(controller.directory)
    viewer = shell.viewer
    _events(0.3)
    layer = viewer.layers[0]
    assert np.allclose(layer.scale[-2:], (0.5, 0.5))
    assert np.allclose(viewer.camera.center[-2:], _extent_centre(viewer))
    fitted = viewer.camera.zoom
    viewer.reset_view()
    assert viewer.camera.zoom == pytest.approx(fitted)  # Home changes nothing: it was already fitted

    # The user zooms in and moves; the next image is fitted to the window again (only CellQuant's layers are open).
    viewer.camera.zoom = fitted * 3
    viewer.camera.center = (0.0, 10.0, 12.0)
    shell._footer._step(1)
    assert _wait(shell)
    _events(0.2)
    assert shell._nav_index == 1
    assert viewer.camera.zoom == pytest.approx(fitted)
    assert np.allclose(viewer.camera.center[-2:], _extent_centre(viewer))

    # A new pixel size makes the image another size in the window: it is fitted again.
    record = controller.experiment.image(shell._shown_image_id)
    shell.controller.set_pixel_size(record.image_id, 0.25, 0.25, None)
    shell.set_pixel_scale(shell.controller.experiment.image(record.image_id))
    assert np.allclose(viewer.layers[0].scale[-2:], (0.25, 0.25))
    assert np.allclose(viewer.camera.center[-2:], _extent_centre(viewer))
    assert viewer.camera.zoom == pytest.approx(fitted * 2)
