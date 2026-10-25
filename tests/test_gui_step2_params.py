"""Step 2 shows only the settings the chosen engine and Z mode use.

Needs napari and Qt; skipped without napari.
"""

from __future__ import annotations

from pathlib import Path

import pytest

napari = pytest.importorskip("napari")

from tests.test_gui_crop import _events, open_window  # noqa: E402,F401
from tests.test_gui_no_resize import _experiment  # noqa: E402


def _shown(panel, name: str) -> bool:
    return getattr(panel, name).isVisibleTo(panel)


def test_settings_follow_the_z_mode_and_resizing(tmp_path: Path, open_window):
    from cellquant.gui.app import _choose

    shell = open_window(_experiment(tmp_path, "cellpose", {"resize_to_diameter": False}).directory)
    panel = shell._objects_panel
    panel.advanced_toggle.setChecked(True)
    _choose(panel.z_stack, "max_projection")
    _events(0.2)
    assert _shown(panel, "flow") and _shown(panel, "min_size_px") and _shown(panel, "augment")
    assert not _shown(panel, "niter_3d") and not _shown(panel, "z_link")
    assert not _shown(panel, "nucleus_diameter_um") and not _shown(panel, "diameter")
    panel.resize_to_diameter.setChecked(True)
    assert _shown(panel, "nucleus_diameter_um") and _shown(panel, "diameter")
    _choose(panel.z_stack, "stitch_slices")
    _events(0.2)
    assert _shown(panel, "z_link") and _shown(panel, "z_scale")
    _choose(panel.z_stack, "full_3d")
    _events(0.2)
    assert not _shown(panel, "flow") and not _shown(panel, "min_size_px") and not _shown(panel, "augment")
    assert not _shown(panel, "z_link") and not _shown(panel, "z_scale")
    assert _shown(panel, "niter_3d") and _shown(panel, "nucleus_diameter_um")


def test_new_levers_are_saved_only_when_changed(tmp_path: Path, open_window):
    shell = open_window(_experiment(tmp_path, "cellpose", {}).directory)
    panel = shell._objects_panel
    panel.write_recipe()
    parameters = shell.controller.recipe.object_set.parameters
    assert "min_size" not in parameters and "augment" not in parameters
    panel.min_size_px.setValue(40)
    panel.augment.setChecked(True)
    panel.write_recipe()
    parameters = shell.controller.recipe.object_set.parameters
    assert parameters["min_size"] == 40 and parameters["augment"] is True


def test_saved_classic_settings_without_cellpose_here_show_the_classic_rows(tmp_path: Path, open_window):
    from cellquant.gui.app import _choose

    saved = {"engine": "cellpose3", "model": "nuclei", "diameter_um": 6.0, "flow_threshold": 0.3}
    shell = open_window(_experiment(tmp_path, "cellpose", saved).directory)
    panel = shell._objects_panel
    panel.advanced_toggle.setChecked(True)
    _choose(panel.z_stack, "max_projection")
    _events(0.2)
    assert shell._objects_panel._engine_key() == "cellpose3"
    assert not _shown(panel, "resize_to_diameter")
    assert _shown(panel, "nucleus_diameter_um") and _shown(panel, "diameter")


def test_hidden_values_and_old_settings_survive_a_save(tmp_path: Path, open_window):
    from cellquant.gui.app import _choose

    saved = {"engine": "cellpose4", "model": "cpsam", "diameter_um": 6.0, "flow_threshold": 0.3, "min_size": 40}
    shell = open_window(_experiment(tmp_path, "cellpose", saved).directory)
    panel = shell._objects_panel
    assert panel.min_size_px.value() == 40
    _choose(panel.z_stack, "max_projection")
    _events(0.2)
    panel.write_recipe()
    assert shell.controller.recipe.object_set.parameters["min_size"] == 40
    assert shell.controller.recipe.object_set.parameters["flow_threshold"] == 0.3
    _choose(panel.z_stack, "full_3d")
    _events(0.2)
    assert not _shown(panel, "flow") and panel.flow.value() == 0.3


def test_an_old_recipe_keeps_its_content_hash_after_a_save(tmp_path: Path, open_window):
    from cellquant.gui.app import _choose

    shell = open_window(_experiment(tmp_path, "classical", {}).directory)
    panel = shell._objects_panel
    _choose(panel.method, "classical")
    panel.write_recipe()
    before = shell.controller.recipe.content_hash()
    panel.write_recipe()
    assert shell.controller.recipe.content_hash() == before
    assert "min_size" not in shell.controller.recipe.object_set.parameters


def test_an_unavailable_saved_model_is_replaced_and_logged_once(tmp_path: Path, monkeypatch, open_window):
    from cellquant import engines

    classic = engines.CellposeEngine(True, "3.1.0", engines.CELLPOSE_CLASSIC, engines.CLASSIC_MODELS, "nuclei")
    monkeypatch.setattr(engines, "cellpose_engine", lambda: classic)
    saved = {"engine": "cellpose3", "model": "cpsam_v2"}
    shell = open_window(_experiment(tmp_path, "cellpose", saved).directory)
    panel = shell._objects_panel
    assert panel.cellpose_model.currentText() == "nuclei"
    log = shell._footer.log.toPlainText()
    assert "Saved Cellpose model 'cpsam_v2' is not available in Classic Cellpose (Cellpose 3); using 'nuclei' instead." in log
    panel.refresh()
    assert shell._footer.log.toPlainText().count("cpsam_v2") == 1
    panel.write_recipe()
    assert shell.controller.recipe.object_set.parameters["model"] == "nuclei"
