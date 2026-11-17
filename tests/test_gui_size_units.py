"""Step 2's Min size and Max size show their unit, and choosing another Size unit converts them."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from cellquant.practice import PIXEL_SIZE_UM


def _wait(shell, seconds: float = 120) -> None:
    from qtpy.QtWidgets import QApplication

    end = time.time() + seconds
    while (shell._job is not None or shell._batch is not None or shell._loader is not None) and time.time() < end:
        QApplication.processEvents()
        time.sleep(0.02)
    for _ in range(5):
        QApplication.processEvents()


@pytest.fixture
def shell(tmp_path: Path, monkeypatch):
    napari = pytest.importorskip("napari")
    from cellquant.gui import guide
    from cellquant.gui.app import CellQuantWindow

    monkeypatch.setattr(guide, "choose_practice_folder", lambda parent: tmp_path / "practice")
    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"napari viewer could not start: {exc}")
    window = CellQuantWindow(viewer, None)
    window.start_practice()
    _wait(window)
    yield window
    viewer.close()


def _set_unit(panel, unit: str) -> None:
    panel.area_unit.setCurrentIndex(panel.area_unit.findData(unit))


def test_the_boxes_show_the_unit_chosen_next_to_them(shell):
    panel = shell._objects_panel
    assert panel.min_area.suffix() == " µm²" and panel.max_area.suffix() == " µm²"
    _set_unit(panel, "px")
    assert panel.min_area.suffix() == " px²" and panel.max_area.suffix() == " px²"
    _set_unit(panel, "um2")
    assert panel.min_area.suffix() == " µm²"
    assert panel.sigma.suffix() == " px"
    experiment = shell._experiment_panel
    assert all(box.suffix() == " µm" for box in (experiment.pixel_x, experiment.pixel_y, experiment.pixel_z))


def test_choosing_another_unit_converts_the_two_sizes_both_ways(shell):
    panel = shell._objects_panel
    panel.min_area.setValue(20.0)
    panel.max_area.setValue(300.0)
    area = PIXEL_SIZE_UM * PIXEL_SIZE_UM
    _set_unit(panel, "px")
    assert panel.min_area.value() == pytest.approx(20.0 / area, abs=0.01)
    assert panel.max_area.value() == pytest.approx(300.0 / area, abs=0.01)
    _set_unit(panel, "um2")
    assert panel.min_area.value() == pytest.approx(20.0, abs=0.01) and panel.max_area.value() == pytest.approx(300.0, abs=0.01)
    # "none" (0) stays "none".
    panel.max_area.setValue(0)
    _set_unit(panel, "px")
    assert panel.max_area.value() == 0
    panel.write_recipe()
    parameters = shell.controller.recipe.object_set.parameters
    assert parameters["min_area_px"] == pytest.approx(20.0 / area, abs=0.01) and "max_area_px" not in parameters and "min_area_um2" not in parameters


def test_an_image_without_a_pixel_size_keeps_the_numbers_and_says_so(shell, monkeypatch):
    panel = shell._objects_panel
    controller = shell.controller
    for record in controller.experiment.images:
        record.pixel_size_x = record.pixel_size_y = None
    panel.min_area.setValue(20.0)
    said = []
    monkeypatch.setattr(shell, "message", said.append)
    _set_unit(panel, "px")
    assert panel.min_area.value() == 20.0 and panel.min_area.suffix() == " px²"
    assert said and "no pixel size" in said[-1]


def test_loading_settings_or_refreshing_never_converts(shell):
    panel = shell._objects_panel
    controller = shell.controller
    data = controller.recipe.model_dump(mode="json")
    data["object_set"]["parameters"] = {**data["object_set"]["parameters"], "min_area_px": 20.0, "max_area_px": 900.0}
    data["object_set"]["parameters"].pop("min_area_um2", None)
    data["object_set"]["parameters"].pop("max_area_um2", None)
    controller.set_recipe(data)
    panel.refresh()
    assert panel.area_unit.currentData() == "px"
    assert (panel.min_area.value(), panel.max_area.value()) == (20.0, 900.0)
    assert panel.min_area.suffix() == " px²"
    data["object_set"]["parameters"] = {**data["object_set"]["parameters"], "min_area_um2": 7.0}
    data["object_set"]["parameters"].pop("min_area_px")
    data["object_set"]["parameters"].pop("max_area_px")
    controller.set_recipe(data)
    panel.refresh()
    assert panel.area_unit.currentData() == "um2" and panel.min_area.value() == 7.0 and panel.max_area.value() == 0
    shell.show_current()
    _wait(shell)
    assert panel.min_area.value() == 7.0
    # The recipe written from unchanged boxes is the recipe that was loaded.
    before = controller.recipe.object_set.parameters.copy()
    panel.write_recipe()
    assert controller.recipe.object_set.parameters == before
