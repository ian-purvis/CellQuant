"""Delete in area in step 3: draw an area, delete the objects inside, Undo brings them back.

Needs napari and Qt; skipped without napari.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

napari = pytest.importorskip("napari")

from tests.test_gui_crop import _events, _wait, open_window  # noqa: E402,F401
from tests.test_workflow import _recipe, _write_squares  # noqa: E402


def test_delete_in_area_buttons(tmp_path: Path, open_window):  # noqa: F811
    from cellquant.controller import AnalysisController
    from cellquant.gui.app import DELETE_AREA_LAYER

    image = tmp_path / "a.tif"
    _write_squares(image)
    controller = AnalysisController.create(tmp_path / "experiment", "Delete area")
    controller.add_image_paths([image])
    controller.set_recipe(_recipe())
    controller.save()
    shell = open_window(controller.directory)
    shell.run_current()
    assert _wait(shell, 120)
    image_id = shell.controller.experiment.images[0].image_id
    panel = shell._edit_panel

    panel._delete_in_area()  # nothing drawn yet: nothing deleted
    assert _wait(shell)
    assert int(shell.controller.last_results[image_id].objects["excluded"].sum()) == 0

    panel._draw_area()
    layer = shell.viewer.layers[DELETE_AREA_LAYER]
    assert layer.mode == "add_polygon"
    layer.add(np.array([[0.0, 0.0], [0.0, 30.0], [30.0, 30.0], [30.0, 0.0]]), shape_type="polygon")
    panel._delete_in_area()
    assert _wait(shell)
    _events(0.2)
    assert int(shell.controller.last_results[image_id].objects["excluded"].sum()) == 1
    assert DELETE_AREA_LAYER not in shell.viewer.layers
    assert int(np.asarray(shell.viewer.layers["Objects"].data)[15, 15]) == 0

    panel._undo()
    assert _wait(shell)
    assert int(shell.controller.last_results[image_id].objects["excluded"].sum()) == 0


def test_deleted_object_leaves_the_outline_layer_and_drawing_is_kept(tmp_path: Path, open_window):  # noqa: F811
    from cellquant.controller import AnalysisController

    first, second = tmp_path / "a.tif", tmp_path / "b.tif"
    _write_squares(first)
    _write_squares(second)
    controller = AnalysisController.create(tmp_path / "experiment", "Delete object")
    controller.add_image_paths([first, second])
    controller.set_recipe(_recipe())
    controller.save()
    shell = open_window(controller.directory)
    shell.run_current()
    assert _wait(shell, 120)
    panel = shell._edit_panel

    # Delete object: the outline goes from the Objects layer too, and is not kept as a drawing.
    panel.pick(int(np.asarray(shell.viewer.layers["Objects"].data)[15, 15]))
    panel._delete()
    assert _wait(shell)
    _events(0.2)
    assert int(np.asarray(shell.viewer.layers["Objects"].data)[15, 15]) == 0
    assert not panel._pending

    # Drawing not recorded yet still comes back after showing another image.
    layer = shell.viewer.layers["Objects"]
    painted = np.array(layer.data, copy=True)
    painted[60:65, 60:65] = 7
    layer.data = painted
    shell._nav_index = 1
    shell.show_current()
    assert _wait(shell)
    _events(0.3)
    assert len(panel._pending) == 1
    shell._nav_index = 0
    shell.show_current()
    assert _wait(shell)
    _events(0.3)
    assert int(np.asarray(shell.viewer.layers["Objects"].data)[62, 62]) == 7
