"""Moving to an image of another size keeps the object layers (Object fills, Objects, Classification, Object IDs) and gives
them the next image's objects in place, instead of removing and adding them again. What is shown must be exactly what
newly added layers would show, in the same order, whatever the user changed on the last image.

Needs napari, Qt and OpenGL (a desktop, or xvfb-run with Mesa). Skipped without napari or OpenGL.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

napari = pytest.importorskip("napari")

from tests.test_gui_crop import _events, _wait, open_window  # noqa: E402,F401
from tests.test_gui_switch_layers import _same, _state  # noqa: E402
from tests.test_workflow import _recipe, _write_squares  # noqa: E402

_OBJECTS = ("Object fills", "Objects", "Classification", "Object IDs")


def test_another_image_size_keeps_the_object_layers_and_looks_like_new_ones(tmp_path: Path, open_window):  # noqa: F811
    import tifffile

    from cellquant.controller import AnalysisController

    small, large = tmp_path / "a.tif", tmp_path / "b.tif"
    _write_squares(small)
    _write_squares(large)
    tifffile.imwrite(large, np.pad(tifffile.imread(large), ((0, 0), (0, 30), (0, 20))), photometric="minisblack")
    controller = AnalysisController.create(tmp_path / "experiment", "Object layers")
    controller.add_image_paths([small, large])
    controller.set_recipe(_recipe())
    controller.run_images()
    controller.save()
    shell = open_window(controller.directory)
    from napari._vispy.utils.gl import get_max_texture_sizes

    if get_max_texture_sizes()[0] is None:
        pytest.skip("needs OpenGL (a desktop, or xvfb-run)")
    viewer = shell.viewer
    shell.go_to_step(4)
    shell._review_panel.display.setCurrentIndex(shell._review_panel.display.findData("class_a"))
    _wait(shell)
    for name in [layer.name for layer in viewer.layers]:
        shell._drop(name)  # start from layers in the order a first showing adds them
    shell.show_current()
    _wait(shell)
    assert all(name in viewer.layers for name in _OBJECTS)
    shapes = set()
    for index in (1, 0, 1):
        # What the user did on this image must not follow to the next one.
        objects = viewer.layers["Objects"]
        objects.mode = "paint"
        objects.selected_label = 5
        viewer.layers["Object fills"].opacity = 0.9
        viewer.layers["Object fills"].visible = True
        viewer.layers["Object IDs"].selected_data = {0}
        before = {name: viewer.layers[name] for name in _OBJECTS}
        removed = []
        viewer.layers.events.removed.connect(lambda event, removed=removed: removed.append(event.value.name))
        shell._nav_index = index
        shell.show_current()
        _wait(shell)
        _events(0.2)
        assert not set(removed) & set(_OBJECTS), removed
        assert all(viewer.layers[name] is before[name] for name in _OBJECTS), "the object layers were kept"
        shown = _state(viewer)
        image_id = shell._nav_ids[index]
        labels = shell.controller.recall(image_id).labels
        shapes.add(labels.shape)
        assert np.array_equal(viewer.layers["Objects"].data, labels)
        assert viewer.layers["Objects"].data.shape == viewer.layers[0].data.shape[-2:]
        # The same image with every layer added anew.
        for name in [layer.name for layer in viewer.layers]:
            shell._drop(name)
        shell.show_current()
        _wait(shell)
        _events(0.2)
        _same(shown, _state(viewer))
    assert len(shapes) == 2, "the images are two sizes"
