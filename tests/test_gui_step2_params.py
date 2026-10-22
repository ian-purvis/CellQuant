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
