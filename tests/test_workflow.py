"""Experiment, batch, cache, and edit behavior."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import tifffile

from cellquant.controller import AnalysisController, process_experiment
from cellquant.recipe import load_recipe, save_recipe
from cellquant.segmentation import segment_objects

import numpy as np


def _write_squares(path: Path, channels: int = 4, shift: int = 0) -> None:
    image = np.zeros((channels, 80, 80), dtype=np.uint16)
    image[0, 10 + shift : 20 + shift, 10:20] = 1000
    image[0, 40:50, 40:50] = 1000
    if channels > 1:
        image[1, 10 + shift : 20 + shift, 10:20] = 800
        image[1, 40:50, 40:50] = 20
    if channels > 2:
        image[2, 10 + shift : 20 + shift, 10:20] = 900
        image[2, 40:50, 40:50] = 30
    if channels > 3:
        image[3, 10 + shift : 20 + shift, 10:20] = 700
        image[3, 40:50, 40:50] = 40
    tifffile.imwrite(path, image, photometric="minisblack")


def _recipe() -> dict:
    return {
        "recipe_name": "batch",
        "object_set": {
            "name": "Objects",
            "segmentation_channel": 0,
            "algorithm": "classical",
            "parameters": {"threshold_method": "manual", "threshold": 100, "use_watershed": False},
        },
        "measurements": [
            {"id": "marker_a_mean", "channel": 1, "region": {"type": "object"}, "statistic": "mean"},
            {"id": "marker_b_mean", "channel": 2, "region": {"type": "object"}, "statistic": "mean"},
        ],
        "classifications": [
            {"id": "class_a", "name": "A", "measurement": "marker_a_mean", "threshold": 200},
            {"id": "class_b", "name": "B", "measurement": "marker_b_mean", "threshold": 200},
        ],
        "reports": [
            {"numerator": "A", "denominator": "all_objects"},
            {"numerator": "A AND B", "denominator": "A"},
        ],
    }


def test_batch_isolates_failures_and_can_reopen(tmp_path: Path, monkeypatch):
    good = tmp_path / "good.tif"
    other = tmp_path / "other.tif"
    _write_squares(good)
    _write_squares(other, shift=8)
    calls = {"n": 0}
    real = segment_objects

    def counted(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr("cellquant.pipeline.segment_objects", counted)
    controller = AnalysisController.create(tmp_path / "experiment", "Demo")
    notices = controller.add_image_paths([good, other, tmp_path / "missing.tif"])
    assert any("could not be opened" in notice for notice in notices)
    controller.set_recipe(_recipe())
    controller.set_sample_name(controller.experiment.images[0].image_id, "alpha")
    controller.add_metadata_column("Group")
    controller.set_metadata(controller.experiment.images[0].image_id, "Group", "treated")
    controller.set_channel_name(0, "Reference")
    report = controller.run_images()
    assert report.failed == 1
    assert report.completed == 2
    assert calls["n"] == 2
    assert (tmp_path / "experiment" / "runs" / report.run_id / "recipe_snapshot.yaml").is_file()
    assert (tmp_path / "experiment" / "runs" / report.run_id / "logs" / "run.log").is_file()

    image_id = controller.experiment.images[0].image_id
    before = controller.last_results[image_id]
    controller.update_thresholds(image_id, {"class_a": 1000})
    after = controller.last_results[image_id]
    assert int(before.objects["class_a"].sum()) == 1
    assert int(after.objects["class_a"].sum()) == 0
    assert calls["n"] == 2

    deleted = controller.delete_object(image_id, int(before.objects["object_id"].iloc[0]))
    assert calls["n"] == 2
    assert int(deleted.objects["excluded"].sum()) == 1
    assert deleted.summary.iloc[0]["total_objects"] == 1
    restored = controller.undo(image_id)
    assert restored is not None
    assert int(restored.objects["excluded"].sum()) == 0

    controller.save()
    exported = controller.export(tmp_path / "export", group_by="Group")
    assert (exported / "objects.csv").is_file()
    assert (exported / "recipe.yaml").is_file()
    assert (exported / "run.json").is_file()

    reopened = AnalysisController.open(tmp_path / "experiment")
    recalled = reopened.recall(image_id)
    assert recalled is not None
    assert recalled.provenance["recipe_sha256"]
    assert reopened.experiment.images[0].sample_name == "alpha"
    assert reopened.experiment.channels[0].channel_name == "Reference"
    rerun = reopened.run_image(image_id)
    assert calls["n"] == 2
    assert int(rerun.objects["excluded"].sum()) == 0


def test_channel_mismatch_is_reported(tmp_path: Path):
    wide = tmp_path / "wide.tif"
    narrow = tmp_path / "narrow.tif"
    _write_squares(wide, channels=4)
    _write_squares(narrow, channels=3)
    controller = AnalysisController.create(tmp_path / "experiment", "Channels")
    notices = controller.add_image_paths([wide, narrow])
    assert any("has 3 channels, but the experiment's channel list has 4" in notice for notice in notices)


def test_corrupt_segmentation_cache_is_recomputed(tmp_path: Path, monkeypatch):
    path = tmp_path / "sample.tif"
    _write_squares(path)
    calls = {"n": 0}
    real = segment_objects

    def counted(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr("cellquant.pipeline.segment_objects", counted)
    controller = AnalysisController.create(tmp_path / "experiment", "Cache")
    controller.add_image_paths([path])
    controller.set_recipe(_recipe())
    image_id = controller.experiment.images[0].image_id
    controller.run_image(image_id)
    label_files = list((tmp_path / "experiment" / ".cache" / "labels").glob("*.npy"))
    assert label_files
    label_files[0].write_bytes(b"not a numpy file")
    again = AnalysisController.open(tmp_path / "experiment")
    again.set_recipe(_recipe())
    again.run_image(image_id)
    assert calls["n"] == 2
    reloaded = again.cache.get_labels(label_files[0].stem)
    assert reloaded is not None


def test_process_experiment_command_path(tmp_path: Path):
    path = tmp_path / "sample.tif"
    _write_squares(path)
    controller = AnalysisController.create(tmp_path / "experiment", "CLI")
    controller.add_image_paths([path])
    recipe = tmp_path / "recipe.yaml"
    save_recipe(load_recipe(_recipe()), recipe)
    report = process_experiment(tmp_path / "experiment", recipe)
    measurement = next((tmp_path / "experiment" / "runs" / report.run_id / "measurements").glob("*.csv"))
    objects = pd.read_csv(measurement)
    assert len(objects) == 2
    assert report.failed == 0
