"""Step 2 "Resize to nucleus size": off for a new Cellpose setup, on for settings saved before it.

Needs napari and Qt; skipped without napari.
"""

from __future__ import annotations

from pathlib import Path

import pytest

napari = pytest.importorskip("napari")

import numpy as np  # noqa: E402
import tifffile  # noqa: E402

from tests.test_gui_crop import _events, _wait, open_window  # noqa: E402,F401


def _experiment(tmp_path: Path, algorithm: str, parameters: dict):
    from cellquant.controller import AnalysisController

    path = tmp_path / "nuclei.tif"
    tifffile.imwrite(path, np.zeros((2, 32, 32), dtype=np.uint16), photometric="minisblack")
    controller = AnalysisController.create(tmp_path / "experiment", "Resize")
    controller.add_image_paths([path])
    recipe = controller.recipe.model_dump(mode="json")
    recipe["object_set"].update(algorithm=algorithm, parameters=parameters)
    controller.set_recipe(recipe)
    controller.save()
    return controller


def test_a_new_cellpose_setup_does_not_resize(tmp_path: Path, open_window):
    from cellquant.gui.app import _choose

    shell = open_window(_experiment(tmp_path, "classical", {}).directory)
    panel = shell._objects_panel
    _choose(panel.method, "cellpose")
    _events(0.2)
    assert not panel.resize_to_diameter.isChecked()
    panel.write_recipe()
    assert shell.controller.recipe.object_set.parameters["resize_to_diameter"] is False


def test_saved_cellpose_settings_keep_resizing(tmp_path: Path, open_window):
    controller = _experiment(tmp_path, "cellpose", {"diameter_um": 6.0, "flow_threshold": 0.4, "cellprob_threshold": 0.0})
    shell = open_window(controller.directory)
    panel = shell._objects_panel
    assert panel.resize_to_diameter.isChecked()
    panel.write_recipe()
    assert "resize_to_diameter" not in shell.controller.recipe.object_set.parameters
    panel.resize_to_diameter.setChecked(False)
    panel.write_recipe()
    assert shell.controller.recipe.object_set.parameters["resize_to_diameter"] is False
