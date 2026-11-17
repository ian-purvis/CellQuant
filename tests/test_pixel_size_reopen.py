"""An experiment whose pixel sizes were never changed is never judged out of date, in any Z mode, before or after reopening."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import tifffile

from cellquant.controller import AnalysisController
from cellquant.practice import PIXEL_SIZE_UM, write_practice_images

PIXEL, STEP = 0.5, 1.5


def _stack_path(folder: Path) -> Path:
    shape = (7, 48, 48)
    z, y, x = np.ogrid[: shape[0], : shape[1], : shape[2]]
    nuclear = np.full(shape, 50, dtype=np.uint16)
    for cz, cy, cx in ((2, 14, 14), (4, 34, 34)):
        nuclear[((z - cz) * STEP) ** 2 + ((y - cy) * PIXEL) ** 2 + ((x - cx) * PIXEL) ** 2 <= 2.8**2] = 1000
    path = folder / "stack.tif"
    folder.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(path, np.stack([nuclear, nuclear], axis=1), imagej=True, resolution=(1 / PIXEL, 1 / PIXEL), metadata={"axes": "ZCYX", "unit": "um", "spacing": STEP})
    return path


def _recipe(mode: str) -> dict:
    return {
        "z_stack": mode,
        "z_index": 3 if mode == "single_plane" else None,
        "z_stitch_threshold": 0.01,
        "object_set": {"name": "Nuclei", "segmentation_channel": 0, "algorithm": "classical", "parameters": {"threshold_method": "otsu", "min_area_px": 5}},
    }


@pytest.mark.parametrize("mode", ["max_projection", "single_plane", "stitch_slices", "full_3d"])
def test_no_false_positive_in_any_z_mode(tmp_path: Path, mode: str):
    path = _stack_path(tmp_path / "images")
    controller = AnalysisController.create(tmp_path / "experiment", "Zmodes")
    controller.add_image_paths([path])
    image_id = controller.experiment.images[0].image_id
    controller.set_recipe(_recipe(mode))
    controller.run_image(image_id)
    controller.set_status(image_id, "approved")
    approval = controller.experiment.image(image_id).approved_settings_sha256
    assert controller.recall(image_id) is not None
    controller.save()
    reopened = AnalysisController.open(tmp_path / "experiment")
    record = reopened.experiment.image(image_id)
    assert reopened.recall(image_id) is not None
    assert record.processing_status == "approved" and record.approved_settings_sha256 == approval
    # Setting the sizes it already has changes nothing, and a Z step is stale only for results analysed in 3D.
    assert not reopened.pixel_size_makes_stale(image_id, record.pixel_size_x, record.pixel_size_y, record.pixel_size_z)
    three_d = mode in ("stitch_slices", "full_3d")
    assert reopened.pixel_size_makes_stale(image_id, record.pixel_size_x, record.pixel_size_y, 9.0) is three_d
    assert reopened.pixel_size_makes_stale(image_id, 3.0, 3.0, record.pixel_size_z)


def test_practice_experiment_reopens_unchanged(tmp_path: Path):
    from cellquant.practice import create_practice_experiment

    controller = create_practice_experiment(tmp_path / "practice")
    controller.run_images()
    ids = [record.image_id for record in controller.experiment.images]
    for image_id in ids:
        controller.set_status(image_id, "approved")
    controller.save()
    reopened = AnalysisController.open(tmp_path / "practice" / "experiment")
    for image_id in ids:
        assert reopened.recall(image_id) is not None
        assert reopened.experiment.image(image_id).processing_status == "approved"


def test_results_saved_with_sizes_the_file_supplied_stay_current(tmp_path: Path):
    """Records without a size (None) and a file that states one: the result carries the file's size and is current."""

    raw = write_practice_images(tmp_path / "raw")
    controller = AnalysisController.create(tmp_path / "experiment", "FileSizes")
    controller.add_image_paths(raw[:1])
    record = controller.experiment.images[0]
    controller.set_recipe({"object_set": {"name": "N", "segmentation_channel": 0, "algorithm": "classical", "parameters": {"threshold_method": "otsu"}}})
    record.pixel_size_x = record.pixel_size_y = record.pixel_size_z = None
    controller.run_image(record.image_id)
    assert controller.recall(record.image_id).provenance["pixel_size_x_um"] == PIXEL_SIZE_UM
    controller.save()
    reopened = AnalysisController.open(tmp_path / "experiment")
    assert reopened.recall(record.image_id) is not None
