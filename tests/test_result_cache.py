"""Large experiments: older results' masks leave memory and come back unchanged, saving skips results
already saved, and export never holds every mask."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from cellquant import result_cache
from cellquant.controller import AnalysisController
from cellquant.result_cache import labels_in_memory
from tests.test_workflow import _recipe, _write_squares


def _runs(tmp_path: Path, count: int = 6) -> tuple[AnalysisController, list[str]]:
    paths = []
    for index in range(count):
        path = tmp_path / f"s{index}.tif"
        _write_squares(path, shift=index % 3)
        paths.append(path)
    controller = AnalysisController.create(tmp_path / "experiment", "Many")
    controller.add_image_paths(paths)
    controller.set_recipe(_recipe())
    controller.run_images()
    return controller, [record.image_id for record in controller.experiment.images]


def test_older_masks_leave_memory_and_come_back_the_same(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(result_cache, "LABELS_IN_MEMORY_BYTES", 0)
    controller, ids = _runs(tmp_path)
    in_memory = [image_id for image_id in ids if labels_in_memory(controller.last_results[image_id])]
    assert len(in_memory) == result_cache.LABELS_ALWAYS_KEPT  # reading a result above made it recent
    oldest = next(image_id for image_id in ids if not labels_in_memory(dict.__getitem__(controller.last_results, image_id)))
    result = dict.__getitem__(controller.last_results, oldest)
    assert not labels_in_memory(result)
    fresh = AnalysisController.open(controller.directory).recall(oldest)  # read from the saved run
    assert np.array_equal(result.labels, fresh.labels)
    assert result.automated_labels is result.labels or np.array_equal(result.automated_labels, fresh.automated_labels)
    # Edits work on an image whose masks were out of memory.
    object_id = int(result.objects["object_id"].iloc[0])
    deleted = controller.delete_object(oldest, object_id)
    assert int(deleted.objects["excluded"].sum()) == 1


def test_masks_stay_in_memory_under_the_limit(tmp_path: Path):
    controller, ids = _runs(tmp_path, count=3)
    assert all(labels_in_memory(controller.last_results[image_id]) for image_id in ids)


def test_save_writes_only_results_that_changed(tmp_path: Path):
    controller, ids = _runs(tmp_path, count=3)
    controller.save()
    folder = controller._working_dir()

    def stamps():
        return {path.name: path.stat().st_mtime_ns for path in sorted(folder.rglob("*")) if path.is_file()}

    first = stamps()
    controller.save()
    assert stamps() == first
    object_id = int(controller.last_results[ids[1]].objects["object_id"].iloc[0])
    controller.delete_object(ids[1], object_id)
    controller.save()
    after = stamps()
    changed = {name for name in after if after[name] != first.get(name)}
    assert changed and all(name.startswith(ids[1]) for name in changed)
    reopened = AnalysisController.open(controller.directory)
    assert int(reopened.recall(ids[1]).objects["excluded"].sum()) == 1


def test_export_is_the_same_with_masks_out_of_memory(tmp_path: Path, monkeypatch):
    controller, _ids = _runs(tmp_path)
    kept = controller.export(tmp_path / "kept")
    monkeypatch.setattr(result_cache, "LABELS_IN_MEMORY_BYTES", 0)
    controller.last_results.trim()
    spilled = controller.export(tmp_path / "spilled")
    for name in ("objects.csv", "image_summary.csv", "settings_index.csv"):
        pd.testing.assert_frame_equal(pd.read_csv(kept / name), pd.read_csv(spilled / name))
    assert all(result.labels.size == 0 for result in controller._results_for_export())


def test_export_after_a_cutoff_and_count_area_change_keeps_few_masks_in_memory(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(result_cache, "LABELS_IN_MEMORY_BYTES", 0)
    controller, ids = _runs(tmp_path)
    controller.update_thresholds(ids[0], {"class_a": 10})  # a shared cutoff: every image is reclassified on export
    controller.set_count_area(ids, [[[0.0, 0.0], [0.0, 60.0], [60.0, 60.0], [60.0, 0.0]]])
    controller.export(tmp_path / "out")
    in_memory = [result for result in controller.last_results.values() if labels_in_memory(result)]
    assert len(in_memory) <= result_cache.LABELS_ALWAYS_KEPT
