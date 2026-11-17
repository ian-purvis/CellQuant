"""A locked tab's reason names what the user must do, and never contradicts the tick of the step it names."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cellquant.controller import AnalysisController
from cellquant.gui import guide
from cellquant.practice import PIXEL_SIZE_UM, write_practice_images
from cellquant.quicksetup import marker_recipe


def _controller(tmp_path: Path) -> AnalysisController:
    raw = write_practice_images(tmp_path / "raw")
    controller = AnalysisController.create(tmp_path / "experiment", "Lock")
    controller.add_image_paths(raw[:1])
    for record in controller.experiment.images:
        controller.set_pixel_size(record.image_id, PIXEL_SIZE_UM, PIXEL_SIZE_UM)
    controller.set_recipe(
        {"object_set": {"name": "Nuclei", "segmentation_channel": 0, "algorithm": "classical", "parameters": {"threshold_method": "otsu", "sigma": 1.0}}}
    )
    controller.run_images()
    return controller


def test_step_5_blocker_names_the_run_when_markers_exist_but_were_not_measured(tmp_path: Path):
    controller = _controller(tmp_path)
    window = SimpleNamespace(controller=controller, _exported_to=None)
    # No markers yet: step 4 is the step to finish.
    states = guide.step_states(window)
    assert not states[3].done and states[4].blocker == "Finish step 4 first."
    # Markers set up, but this session's results lack their columns (for example after Load settings or reopening).
    data = marker_recipe(controller.recipe.model_dump(mode="json"), [(1, "Marker A")])
    controller.set_recipe(data)
    states = guide.step_states(window)
    assert states[3].done  # step 4 shows its tick
    blocker = states[4].blocker
    assert blocker and "step 4" not in blocker.lower() and "Run" in blocker
    # Measured: nothing blocks.
    controller.run_images()
    assert guide.step_states(window)[4].blocker == ""


def test_tab_tooltip_and_lock_note_do_not_send_the_user_to_a_finished_step(tmp_path: Path, monkeypatch):
    napari = pytest.importorskip("napari")
    from cellquant.gui.app import CellQuantWindow

    controller = _controller(tmp_path)
    controller.set_recipe(marker_recipe(controller.recipe.model_dump(mode="json"), [(1, "Marker A")]))
    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"napari viewer could not start: {exc}")
    try:
        window = CellQuantWindow(viewer, None)
        window.controller = controller
        window.refresh_guidance()
        tabs = window._tabs
        results_tab = tabs.indexOf(window._step_pages[5])
        tip = tabs.tabToolTip(results_tab)
        assert tip == "Results: locked. Step 5 first: Run this image again so its markers are measured."
        assert "✓" in tabs.tabText(tabs.indexOf(window._step_pages[3]))  # step 4 is ticked
        assert "step 4" not in tip.lower() and "Finish step 4" not in window._step_pages[0].lock_note.text()
    finally:
        viewer.close()
