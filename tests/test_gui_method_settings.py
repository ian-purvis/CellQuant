"""Cellpose is the default method, and step 2 shows only the settings of the chosen method.

Needs napari and Qt; skipped without napari.
"""

from __future__ import annotations

from pathlib import Path

import pytest

napari = pytest.importorskip("napari")

from tests.test_gui_crop import open_window  # noqa: E402,F401
from tests.test_workflow import _recipe, _write_squares  # noqa: E402


def _experiment(tmp_path: Path, name: str, recipe: dict | None):
    from cellquant.controller import AnalysisController

    image = tmp_path / f"{name}.tif"
    _write_squares(image)
    controller = AnalysisController.create(tmp_path / name, name)
    controller.add_image_paths([image])
    if recipe is not None:
        controller.set_recipe(recipe)
    controller.save()
    return controller


def test_new_experiment_defaults_to_cellpose(tmp_path: Path, open_window):  # noqa: F811
    controller = _experiment(tmp_path, "fresh", None)
    assert controller.recipe.object_set.algorithm == "cellpose"
    assert controller.recipe.object_set.parameters == {}
    shell = open_window(controller.directory)
    panel = shell._objects_panel
    assert panel.method.currentData() == "cellpose" and panel.method.itemData(0) == "cellpose"
    assert panel.method.findData("classical") >= 0  # still selectable


def test_saved_classical_recipe_is_kept(tmp_path: Path, open_window):  # noqa: F811
    controller = _experiment(tmp_path, "old", _recipe())
    assert controller.recipe.object_set.algorithm == "classical"
    shell = open_window(controller.directory)
    panel = shell._objects_panel
    assert panel.method.currentData() == "classical"
    panel.write_recipe()
    assert shell.controller.recipe.object_set.algorithm == "classical"


def test_only_the_chosen_methods_settings_are_shown(tmp_path: Path, open_window):  # noqa: F811
    controller = _experiment(tmp_path, "rows", None)
    shell = open_window(controller.directory)
    panel = shell._objects_panel
    panel.advanced_toggle.setChecked(True)
    box = panel.advanced_box
    classical = [panel.threshold_method, panel.sigma]
    classical_advanced = [panel.fill_holes, panel.opening, panel.closing, panel.watershed]
    cellpose = [panel.engine_choice, panel.nucleus_diameter_um, panel.size_hint]
    cellpose_advanced = [panel.cellpose_model, panel.diameter, panel.flow, panel.cellprob]

    panel.method.setCurrentIndex(panel.method.findData("cellpose"))
    assert all(w.isVisibleTo(panel) for w in cellpose)
    assert all(w.isVisibleTo(box) for w in cellpose_advanced)
    assert not any(w.isVisibleTo(panel) for w in classical + [panel.threshold])
    assert not any(w.isVisibleTo(box) for w in classical_advanced + [panel.watershed_distance, panel.compactness])

    panel.method.setCurrentIndex(panel.method.findData("classical"))
    assert all(w.isVisibleTo(panel) for w in classical)
    assert all(w.isVisibleTo(box) for w in classical_advanced)
    assert not any(w.isVisibleTo(panel) for w in cellpose)
    assert not any(w.isVisibleTo(box) for w in cellpose_advanced + [panel.niter_3d, panel.flow3d_smooth, panel.min_voxels_3d])

    # Hidden settings are still part of the run lock, and come back after it.
    inputs = shell._settings_inputs()
    assert panel.diameter in inputs and panel.sigma in inputs
