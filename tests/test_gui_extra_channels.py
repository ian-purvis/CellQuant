"""Step 2 Advanced "Extra input channels": up to two more channels Cellpose-SAM reads with the source channel.

Needs napari and Qt; skipped without napari.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

napari = pytest.importorskip("napari")

import numpy as np  # noqa: E402
import tifffile  # noqa: E402
from qtpy.QtCore import Qt  # noqa: E402

from tests.test_gui_crop import _events, open_window  # noqa: E402,F401

SAM = {"engine": "cellpose4", "model": "cpsam", "diameter_um": 6.0}
CLASSIC = {"engine": "cellpose3", "model": "nuclei", "diameter_um": 6.0}


def _four_channels(tmp_path: Path, algorithm: str, parameters: dict):
    from cellquant.controller import AnalysisController

    path = tmp_path / "four.tif"
    tifffile.imwrite(path, np.zeros((4, 32, 32), dtype=np.uint16), photometric="minisblack")
    controller = AnalysisController.create(tmp_path / "experiment", "Extra")
    controller.add_image_paths([path])
    recipe = controller.recipe.model_dump(mode="json")
    recipe["object_set"].update(algorithm=algorithm, parameters=parameters)
    controller.set_recipe(recipe)
    controller.save()
    return controller


def _shown(panel, name: str) -> bool:
    return getattr(panel, name).isVisibleTo(panel)


def _open(tmp_path, monkeypatch, open_window, algorithm="cellpose", parameters=None, engine="cellpose4"):
    shell = open_window(_four_channels(tmp_path, algorithm, dict(SAM if parameters is None else parameters)).directory)
    panel = shell._objects_panel
    # Stand in for the installed Cellpose, so the tests need neither the package nor a model.
    model = "cpsam" if engine == "cellpose4" else "nuclei"
    monkeypatch.setattr(panel, "engine", SimpleNamespace(installed=True, key=engine, default_model=model, models=[model], label="Cellpose"))
    panel.refresh()
    panel.advanced_toggle.setChecked(True)
    _events(0.2)
    return shell, panel


def _tick(panel, *rows: int) -> None:
    for row in rows:
        panel.extra_channels.item(row).setCheckState(Qt.Checked)


def _untick(panel, *rows: int) -> None:
    for row in rows:
        panel.extra_channels.item(row).setCheckState(Qt.Unchecked)


def test_row_is_shown_only_for_cellpose_sam(tmp_path: Path, monkeypatch, open_window):
    from cellquant.gui.app import _choose

    shell, panel = _open(tmp_path, monkeypatch, open_window, "classical", {})
    assert not _shown(panel, "extra_channels")
    _choose(panel.method, "cellpose")
    _events(0.2)
    assert _shown(panel, "extra_channels")
    assert not _shown(panel, "extra_channels_note")  # nothing ticked
    _tick(panel, 0)
    assert _shown(panel, "extra_channels_note")


def test_row_is_hidden_for_classic_cellpose(tmp_path: Path, monkeypatch, open_window):
    shell, panel = _open(tmp_path, monkeypatch, open_window, "cellpose", CLASSIC, "cellpose3")
    assert panel._engine_key() == "cellpose3"
    assert not _shown(panel, "extra_channels") and not _shown(panel, "extra_channels_note")


def test_the_default_channel_is_not_offered(tmp_path: Path, monkeypatch, open_window):
    shell, panel = _open(tmp_path, monkeypatch, open_window)
    source = panel.channel.currentData()
    offered = [panel.extra_channels.item(row).data(Qt.UserRole) for row in range(panel.extra_channels.count())]
    assert source not in offered and len(offered) == 3
    assert panel.extra_channels.item(0).text() == panel.channel.itemText(panel.channel.findData(offered[0]))
    # Choosing another Default channel moves it out of the list and brings the old one back.
    _tick(panel, 0)
    ticked = panel.extra_channel_choice()
    panel.channel.setCurrentIndex(panel.channel.findData(offered[1]))
    offered_now = [panel.extra_channels.item(row).data(Qt.UserRole) for row in range(panel.extra_channels.count())]
    assert offered[1] not in offered_now and source in offered_now
    assert panel.extra_channel_choice() == ticked


def test_ticked_channels_are_written_and_nothing_ticked_writes_no_key(tmp_path: Path, monkeypatch, open_window):
    shell, panel = _open(tmp_path, monkeypatch, open_window)
    panel.write_recipe()
    assert "extra_channels" not in shell.controller.recipe.object_set.parameters
    _tick(panel, 0, 2)
    chosen = panel.extra_channel_choice()
    assert len(chosen) == 2 and panel.channel.currentData() not in chosen
    panel.write_recipe()
    assert shell.controller.recipe.object_set.parameters["extra_channels"] == chosen
    _untick(panel, 0, 2)
    panel.write_recipe()
    assert "extra_channels" not in shell.controller.recipe.object_set.parameters


def test_no_key_is_written_for_classic_cellpose(tmp_path: Path, monkeypatch, open_window):
    shell, panel = _open(tmp_path, monkeypatch, open_window, "cellpose", CLASSIC, "cellpose3")
    _tick(panel, 0)
    panel.write_recipe()
    assert "extra_channels" not in shell.controller.recipe.object_set.parameters


def test_a_third_channel_cannot_be_ticked(tmp_path: Path, monkeypatch, open_window):
    shell, panel = _open(tmp_path, monkeypatch, open_window)
    item = panel.extra_channels.item
    _tick(panel, 0)
    assert all(item(row).flags() & Qt.ItemIsEnabled for row in range(3))
    _tick(panel, 1)
    assert not item(2).flags() & Qt.ItemIsEnabled
    assert item(2).toolTip() == "Cellpose-SAM reads at most 3 channels: the source channel and 2 more."
    assert item(0).flags() & Qt.ItemIsEnabled and item(1).flags() & Qt.ItemIsEnabled
    _untick(panel, 0)
    assert item(2).flags() & Qt.ItemIsEnabled and item(2).toolTip() == ""


def test_refresh_restores_the_saved_ticks(tmp_path: Path, monkeypatch, open_window):
    shell, panel = _open(tmp_path, monkeypatch, open_window, "cellpose", {**SAM, "extra_channels": [1, 3]})
    assert panel.extra_channel_choice() == [1, 3]
    assert _shown(panel, "extra_channels_note")
    panel.refresh()
    _events(0.2)
    assert panel.extra_channel_choice() == [1, 3]


def test_the_note_names_the_channels_at_once(tmp_path: Path, monkeypatch, open_window):
    shell, panel = _open(tmp_path, monkeypatch, open_window)
    source = shell.controller._channel_name(panel.channel.currentData())
    _tick(panel, 0)
    first = panel.extra_channels.item(0).text().split(" = ", 1)[-1]
    assert panel.extra_channels_note.text().startswith(f"Objects are found from {source} + {first} together.")
    assert "can favor marker-positive cells" in panel.extra_channels_note.text()
    _tick(panel, 1)
    second = panel.extra_channels.item(1).text().split(" = ", 1)[-1]
    assert f"Objects are found from {source} + {first} + {second} together." in panel.extra_channels_note.text()
