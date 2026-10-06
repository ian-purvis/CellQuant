"""Analyses in the window: the list at the top, one per channel, Run all analyses, switching, export.

Needs napari, Qt and OpenGL (a desktop, or xvfb-run with Mesa). Skipped when napari is missing.
"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import pytest

napari = pytest.importorskip("napari")


def _wait(shell, seconds: float = 120) -> None:
    from qtpy.QtWidgets import QApplication

    end = time.time() + seconds
    while (shell._job is not None or shell._batch is not None or shell._loader is not None) and time.time() < end:
        QApplication.processEvents()
        time.sleep(0.02)
    for _ in range(5):
        QApplication.processEvents()


def _experiment(tmp_path: Path) -> Path:
    from cellquant.controller import AnalysisController
    from cellquant.quicksetup import marker_recipe
    from cellquant.synthetic_retina import write_retina_set

    write_retina_set(tmp_path / "set", size=96, retinas=1, seed=4)
    controller = AnalysisController.create(tmp_path / "experiment", "Analyses", input_directory=tmp_path / "set" / "images")
    controller.add_image_paths([tmp_path / "set" / "images"])
    for index, name in enumerate(("OTX2", "Fluor", "PAX6")):
        controller.set_channel_name(index, name)
    base = controller.recipe.model_dump(mode="json")
    base["object_set"] = {
        "name": "Nuclei",
        "segmentation_channel": 2,
        "algorithm": "classical",
        "parameters": {"sigma": 1.0, "use_watershed": True, "watershed_min_distance_px": 4, "min_area_um2": 5},
    }
    controller.set_recipe(marker_recipe(base, [(0, "OTX2"), (1, "Fluor")]))
    controller.save()
    return controller.directory


def test_analyses_in_the_window(tmp_path: Path, monkeypatch):
    from qtpy.QtWidgets import QFileDialog, QInputDialog, QMessageBox

    from cellquant.gui.app import CellQuantWindow

    directory = _experiment(tmp_path)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(tmp_path / "export")))
    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"napari viewer could not start: {exc}")
    shell = CellQuantWindow(viewer, directory)
    try:
        _wait(shell)
        bar = shell._analysis_bar
        controller = shell.controller
        assert [bar.choice.itemText(i) for i in range(bar.choice.count())] == ["Analysis 1"]
        assert not bar.remove_button.isEnabled() and not shell._footer.run_analyses.isVisibleTo(shell._footer)

        # One per channel: the dialog's choice, done directly.
        bar.make_per_channel([0, 1, 2])
        assert bar.choice.count() == 3 and bar.choice.currentText() == "Analysis 1"
        assert shell._footer.run_analyses.isVisibleTo(shell._footer)
        assert shell._results_summary.export_analyses.isVisibleTo(shell._results_summary)

        shell.run_all_analyses()
        assert not bar.choice.isEnabled()  # no switching while analyses run
        # The settings pages and image navigation are locked: they would show another analysis.
        assert not shell._tabs.isEnabled() and not shell._footer._navigation[1].isEnabled()
        save = next(button for button in shell._results_panel.findChildren(type(shell._footer.cancel)) if button.text() == "Load settings…")
        assert not save.isEnabled()
        _wait(shell, 300)
        assert bar.choice.isEnabled() and shell._tabs.isEnabled() and shell._footer._navigation[1].isEnabled()
        assert save.isEnabled()
        assert "Objects in Fluor" in shell._results_summary.batch.text()
        assert controller.recipe.recipe_id == controller.analyses()[0].recipe_id

        # Switching shows that analysis's settings and results; the image on screen stays.
        image_id = shell._nav_ids[shell._nav_index]
        bar.choice.setCurrentIndex(1)
        _wait(shell)
        assert controller.active_analysis().name == "Objects in OTX2"
        assert shell._objects_panel.channel.currentData() == 0
        result = controller.recall(image_id)
        assert result is not None and result.provenance["recipe"]["object_set"]["segmentation_channel"] == 0
        assert shell._nav_ids[shell._nav_index] == image_id

        # Settings changed on screen belong to the analysis they were changed in.
        shell._objects_panel.channel.setCurrentIndex(shell._objects_panel.channel.findData(1))
        bar.choice.setCurrentIndex(0)
        _wait(shell)
        assert shell._objects_panel.channel.currentData() == 2
        from cellquant.recipe import load_recipe

        second = controller.analyses()[1]
        assert load_recipe(controller.directory / "recipes" / f"{second.recipe_id}.yaml").object_set.segmentation_channel == 1

        # Export all analyses.
        shell.export_all_dialog()
        _wait(shell)
        combined = pd.read_csv(tmp_path / "export" / "all_analyses_image_summary.csv")
        assert set(combined["analysis"]) == {"Analysis 1", "Objects in OTX2", "Objects in Fluor"}

        # New, rename and remove.
        monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Cellpose try", True)))
        bar._new()
        assert bar.choice.currentText() == "Cellpose try" and bar.choice.count() == 4
        monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Cellpose-SAM", True)))
        bar._rename()
        assert bar.choice.currentText() == "Cellpose-SAM"
        monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
        bar._remove()
        _wait(shell)
        assert bar.choice.count() == 3 and "Cellpose-SAM" not in [bar.choice.itemText(i) for i in range(3)]
    finally:
        viewer.close()
