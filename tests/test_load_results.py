"""Load results: objects from a folder of masks, whatever made them."""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest
import tifffile

import cellquant.pipeline as pipeline
from cellquant.controller import AnalysisController
from cellquant.results_folder import name_key


def _write_image(path: Path) -> None:
    image = np.zeros((2, 80, 80), dtype=np.uint16)
    image[0, 10:20, 10:20] = 1000
    image[0, 40:50, 40:50] = 1000
    image[1, 10:20, 10:20] = 800
    tifffile.imwrite(path, image, photometric="minisblack")


def _mask() -> np.ndarray:
    mask = np.zeros((80, 80), dtype=np.uint16)
    mask[8:22, 8:22] = 1
    mask[38:52, 38:52] = 2
    mask[60:70, 60:70] = 3
    return mask


def _recipe() -> dict:
    return {
        "recipe_name": "load",
        "object_set": {
            "name": "Objects",
            "segmentation_channel": 0,
            "algorithm": "classical",
            "parameters": {"threshold_method": "manual", "threshold": 100, "use_watershed": False},
        },
        "measurements": [{"id": "marker_mean", "channel": 1, "region": {"type": "object"}, "statistic": "mean"}],
        "classifications": [],
        "reports": [],
    }


def _experiment(tmp_path: Path, name: str = "exp") -> AnalysisController:
    images = tmp_path / "images"
    if not images.is_dir():
        (images / "Retina 1").mkdir(parents=True)
        _write_image(images / "Retina 1" / "sample A.tif")
    controller = AnalysisController.create(tmp_path / name, name)
    controller.add_image_paths([images])
    controller.set_recipe(_recipe())
    return controller


@pytest.fixture
def no_segmentation(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("segmented")

    monkeypatch.setattr(pipeline, "segment_channel", refuse)


def test_masks_named_after_images_load_without_segmenting(tmp_path: Path, no_segmentation):
    controller = _experiment(tmp_path)
    folder = tmp_path / "masks"
    folder.mkdir()
    tifffile.imwrite(folder / "sample A_cp_masks.tif", _mask())
    tifffile.imwrite(folder / "someone else_masks.tif", _mask())

    report = controller.load_results(folder)

    record = controller.experiment.images[0]
    assert report.loaded == [record.image_id]
    assert report.unmatched == 1 and not report.problems and not report.missing
    assert record.processing_status == "analyzed"
    result = controller.last_results[record.image_id]
    assert result.qc.n_objects == 3
    assert np.array_equal(result.labels, _mask())
    assert result.provenance["segmentation_origin"] == "loaded"

    reopened = AnalysisController.open(controller.directory)
    edited = reopened.delete_object(record.image_id, 3)
    assert edited.qc.n_objects == 2
    again = reopened.run_image(record.image_id)
    assert again.qc.n_objects == 2  # the edit is kept and nothing is segmented


def test_cluster_package_matches_by_images_csv(tmp_path: Path, no_segmentation):
    controller = _experiment(tmp_path)
    package = tmp_path / "cq_hpc_1"
    (package / "results" / "masks").mkdir(parents=True)
    tifffile.imwrite(package / "results" / "masks" / "renamed_masks.tif", _mask())
    with (package / "images.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["name", "source_file", "position"])
        writer.writerow(["renamed", controller.experiment.images[0].source_path, 1])

    assert len(controller.load_results(package / "results").loaded) == 1


def test_cluster_name_rule_matches_without_images_csv(tmp_path: Path, no_segmentation):
    controller = _experiment(tmp_path)
    folder = tmp_path / "masks"
    folder.mkdir()
    tifffile.imwrite(folder / f"{name_key('Retina 1/sample A')}_masks.tif", _mask())

    assert len(controller.load_results(folder).loaded) == 1


def test_another_cellquant_experiment_loads_its_edited_objects(tmp_path: Path):
    first = _experiment(tmp_path, "first")
    image_id = first.experiment.images[0].image_id
    first.run_image(image_id)
    first.delete_object(image_id, 2)
    first.save()
    second = _experiment(tmp_path, "second")

    report = second.load_results(first.directory)

    loaded = second.last_results[second.experiment.images[0].image_id]
    assert len(report.loaded) == 1 and report.duplicates == 0
    assert loaded.qc.n_objects == 1


def test_mask_of_wrong_z_or_size_is_reported_not_used(tmp_path: Path):
    controller = _experiment(tmp_path)
    folder = tmp_path / "masks"
    folder.mkdir()
    tifffile.imwrite(folder / "sample A_masks.tif", np.zeros((40, 40), dtype=np.uint16))

    report = controller.load_results(folder)

    assert not report.loaded
    assert "40 x 40" in report.problems[0]
    assert controller.experiment.images[0].processing_status == "not_analyzed"
