"""A source channel per group of images (controller helpers and the step 2 "By image group" box)."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest
import tifffile

from cellquant.controller import AnalysisController


def _write(path: Path, labels: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(1)
    data = (rng.random((len(labels), 32, 32)) * 100).astype("uint16")
    tifffile.imwrite(path, data, imagej=True, metadata={"axes": "CYX", "Labels": labels})


def _controller(tmp_path: Path) -> AnalysisController:
    images = tmp_path / "images"
    _write(images / "a" / "m1.tif", ["DAPI", "mCherry", "far red"])
    _write(images / "a" / "m2.tif", ["DAPI", "mCherry", "far red"])
    _write(images / "b" / "g1.tif", ["DAPI", "GFP", "far red"])
    _write(images / "b" / "g2.tif", ["GFP", "DAPI", "far red"])
    controller = AnalysisController.create(tmp_path / "experiment", "B", input_directory=images)
    controller.add_image_paths([images])
    return controller


def _ids(controller):
    return {record.relative_path: record.image_id for record in controller.experiment.images}


def test_set_plan_channel_by_name(tmp_path: Path):
    controller = _controller(tmp_path)
    ids = _ids(controller)
    cherry = [ids["a/m1.tif"], ids["a/m2.tif"]]
    green = [ids["b/g1.tif"], ids["b/g2.tif"]]
    assert controller.set_plan_channel_by_name(cherry, "mCherry") == []
    assert controller.set_plan_channel_by_name(green, "GFP") == []
    assert controller.segmentation_channel_for(ids["a/m1.tif"]) == (1, "chosen for this image")
    assert controller.segmentation_channel_for(ids["b/g1.tif"]) == (1, "chosen for this image")
    assert controller.segmentation_channel_for(ids["b/g2.tif"]) == (0, "chosen for this image")
    assert controller.group_plan_channel(cherry) == "mCherry" and controller.group_plan_channel(green) == "GFP"
    assert controller.group_plan_channel(cherry + green) == "mixed"
    # A name some images lack: those are reported and the others are set; the lacking ones are unchanged.
    assert controller.set_plan_channel_by_name(cherry + green, "mCherry") == green
    assert controller.segmentation_channel_for(ids["b/g1.tif"]) == (1, "chosen for this image")  # still GFP
    controller.set_plan_channel_by_name(cherry + green, None)
    assert controller.group_plan_channel(cherry + green) is None


def test_different_channel_counts_run_and_export(tmp_path: Path):
    """A 2-channel image and a 4-channel image in one folder: each group picks its own channel."""

    import pandas as pd

    from cellquant.quicksetup import marker_recipe

    images = tmp_path / "mixed"
    _write(images / "one.tif", ["AF488", "AF647"])
    _write(images / "two.tif", ["Blue", "Green", "Red", "Far Red"])
    controller = AnalysisController.create(tmp_path / "exp2", "Mixed", input_directory=images)
    controller.add_image_paths([images])
    ids = _ids(controller)
    one, two = ids["one.tif"], ids["two.tif"]
    assert controller.image_channel_names(two) == ["Blue", "Green", "Red", "Far Red"]
    assert controller.set_plan_channel_by_name([one, two], "Red") == [one]
    assert controller.segmentation_channel_for(two) == (2, "chosen for this image")
    assert controller.set_plan_channel_by_name([one], "AF647") == []
    assert controller.segmentation_channel_for(one) == (1, "chosen for this image")
    base = controller.recipe.model_dump(mode="json")
    base["object_set"] = {"name": "Nuclei", "segmentation_channel": 0, "algorithm": "classical", "parameters": {"sigma": 1.0, "min_area_px": 0}}
    controller.set_recipe(marker_recipe(base, [(0, "Marker")]))
    for image_id in (one, two):
        assert controller.run_image(image_id) is not None
    folder = controller.export_all(tmp_path / "out")
    table = pd.read_csv(folder / "all_analyses_image_summary.csv")
    labels = dict(zip(table["image_id"], table["segmentation_channel"]))
    assert labels[two] == "Red" and labels[one] == "AF647"


def test_group_box_sets_channel(tmp_path: Path, monkeypatch):
    napari = pytest.importorskip("napari")
    from qtpy.QtWidgets import QApplication, QComboBox, QLabel

    from cellquant.gui.app import CellQuantWindow

    controller = _controller(tmp_path)
    ids = _ids(controller)
    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"napari viewer could not start: {exc}")
    shell = CellQuantWindow(viewer, controller.directory)
    try:
        end = time.time() + 120
        while (shell._loader is not None or shell._job is not None) and time.time() < end:
            QApplication.processEvents()
            time.sleep(0.02)
        panel = shell._objects_panel
        panel.refresh()
        assert panel.group_box is not None and not panel.group_box.isHidden()
        assert panel.group_by_box.currentData() == "layout"  # three layouts here
        rows = [panel.group_rows.itemAt(i).widget() for i in range(panel.group_rows.count())]
        assert len(rows) == 3
        target = next(row for row in rows if row.findChild(QLabel).text().startswith("DAPI · mCherry"))
        combo = target.findChild(QComboBox)
        combo.setCurrentIndex(combo.findData("mCherry"))
        combo.activated.emit(combo.currentIndex())
        for _ in range(10):
            QApplication.processEvents()
        assert shell.controller.segmentation_channel_for(ids["a/m1.tif"]) == (1, "chosen for this image")
        assert shell.controller.segmentation_channel_for(ids["a/m2.tif"]) == (1, "chosen for this image")
        assert shell.controller.segmentation_channel_for(ids["b/g1.tif"])[1] != "chosen for this image"
    finally:
        viewer.close()
