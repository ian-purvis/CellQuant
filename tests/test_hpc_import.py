"""Importing cluster results: a new relocatable experiment, editable without Cellpose or the original files (AC12-AC14)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cellquant.controller import SESSION_IMAGE_LIMIT, AnalysisController
from cellquant.errors import CellQuantError
from cellquant.hpc.common import read_json
from cellquant.hpc.import_results import ImportFailed, import_results, preview_import
from cellquant.hpc.runner import EXIT_OK, Runner
from cellquant.storage import read_persisted_result
from tests.hpc_helpers import install_fake_cellpose, matching_observer, prepared_package


def _finished(tmp_path: Path, monkeypatch, *, count: int = 2, mode: str = "max_projection"):
    install_fake_cellpose(tmp_path, monkeypatch)
    controller, package, runtime_bytes = prepared_package(tmp_path, count=count, z_stack=mode)
    run_dir = tmp_path / "cluster" / "results" / package.name / "run_1"
    code = Runner(package, tmp_path / "scratch", run_dir, observe=matching_observer(runtime_bytes), log=lambda _text: None).run()
    assert code == EXIT_OK
    return controller, package, run_dir


def _no_engine(monkeypatch) -> None:
    """From here on, any segmentation or engine inspection fails the test."""

    import cellquant.controller as controller_module
    import cellquant.pipeline as pipeline
    import cellquant.segmentation as segmentation

    def forbidden(*_args, **_kwargs):
        raise AssertionError("segmentation or engine inspection was attempted")

    for module, name in ((segmentation, "engine_signature"), (controller_module, "engine_signature"), (pipeline, "segment_channel"), (segmentation, "segment_objects")):
        monkeypatch.setattr(module, name, forbidden)
    import sys

    for name in [name for name in sys.modules if name == "cellpose" or name.startswith("cellpose.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "cellpose", None)


def test_import_is_complete_and_lossless(tmp_path: Path, monkeypatch):
    source, package, run_dir = _finished(tmp_path, monkeypatch, mode="stitch_slices")
    outcome = import_results(package, run_dir, tmp_path / "imported")
    controller = AnalysisController.open(outcome.destination)
    record = read_json(outcome.destination / "hpc_import.json")
    assert record["run_id"] == "run_1" and not record["partial"] and record["experiment_id"] == controller.experiment.experiment_id
    assert record["source_experiment_id"] == source.experiment.experiment_id != controller.experiment.experiment_id
    assert controller.experiment.latest_run_id == "run_1"
    for image in controller.experiment.images:
        task = next((run_dir / "tasks" / image.image_id).iterdir())
        remote = read_persisted_result(task, image.image_id)
        local = controller.recall(image.image_id)
        assert np.array_equal(remote.labels, local.labels)
        for name in ("summary", "phenotype_counts", "combination_counts", "reports"):
            pd.testing.assert_frame_equal(getattr(remote, name), getattr(local, name))
        columns = [column for column in remote.objects.columns if column != "experiment_id"]
        pd.testing.assert_frame_equal(remote.objects[columns], local.objects[columns])
        assert set(local.objects["experiment_id"]) == {controller.experiment.experiment_id}
        assert local.provenance["experiment_id"] == controller.experiment.experiment_id
        assert local.provenance["hpc"]["source_experiment_id"] == source.experiment.experiment_id
        assert local.qc.warnings == remote.qc.warnings and local.qc.n_objects == remote.qc.n_objects
        assert local.objects["unmeasured"].dtype == bool and local.objects["core_pos"].isna().any()
        assert image.processing_status in ("analyzed", "needs_attention") and image.approved_settings_sha256 == ""
        assert image.image_metadata["hpc"]["source_image_id"] in {item.image_id for item in source.experiment.images}
        assert image.source_path_relative == f"inputs/{image.image_id}.ome.tif"
    assert controller.recipe.content_hash() == source.recipe.content_hash()
    assert not (outcome.destination / "working").exists() or not any((outcome.destination / "working").rglob("*.csv"))


def test_review_edit_reopen_and_export_without_cellpose_or_originals(tmp_path: Path, monkeypatch):
    source, package, run_dir = _finished(tmp_path, monkeypatch, count=SESSION_IMAGE_LIMIT + 2)
    outcome = import_results(package, run_dir, tmp_path / "imported")
    _no_engine(monkeypatch)
    shutil.rmtree(tmp_path / "images")  # the original TIFFs are gone
    shutil.rmtree(package)  # and so is the prepared package
    moved = tmp_path / "elsewhere" / "experiment copy"
    moved.parent.mkdir()
    shutil.move(str(outcome.destination), str(moved))
    controller = AnalysisController.open(moved)
    ids = [image.image_id for image in controller.experiment.images]
    first = ids[0]
    result = controller.recall(first)
    counted = result.objects.loc[~result.objects["unmeasured"].astype(bool), "object_id"].tolist()
    target = int(counted[0])
    before = result.qc.n_objects
    edited = controller.delete_object(first, target)
    assert edited.qc.n_objects == before - 1
    for image_id in ids[1:]:  # push the first image out of the in-memory cache
        controller.recall(image_id)
        controller.update_thresholds(image_id, {"red_pos": 300})
    controller.save()
    reopened = AnalysisController.open(moved)
    again = reopened.recall(first)
    assert again.qc.n_objects == before - 1 and bool(again.objects.loc[again.objects["object_id"] == target, "excluded"].iloc[0])
    restored = reopened.restore_object(first, target)
    assert restored.qc.n_objects == before
    undone = reopened.undo(first)
    assert undone.qc.n_objects == before - 1
    drawn = np.array(undone.labels, copy=True)
    other = int(counted[1])
    drawn[drawn == other] = 0
    committed = reopened.commit_drawn_labels(first, drawn)
    assert committed.qc.n_objects == before - 2
    reclassified = reopened.update_thresholds(first, {"red_pos": 50})
    assert reclassified.reports.loc[0, "percent"] == pytest.approx(100.0)
    reopened.save()
    exported = reopened.export(tmp_path / "export")
    objects = pd.read_csv(exported / "objects.csv")
    assert set(objects["image_id"]) == set(ids)
    rerun = reopened.run_image(ids[1])  # Run with unchanged settings reuses the cluster's objects
    assert rerun.provenance["segmentation_origin"] == "hpc"


def test_edits_never_segment_again_when_settings_changed(tmp_path: Path, monkeypatch):
    _source, package, run_dir = _finished(tmp_path, monkeypatch)
    outcome = import_results(package, run_dir, tmp_path / "imported")
    _no_engine(monkeypatch)
    controller = AnalysisController.open(outcome.destination)
    image_id = controller.experiment.images[0].image_id
    data = controller.recipe.model_dump(mode="json")
    data["object_set"]["parameters"]["flow_threshold"] = 0.8
    controller.set_recipe(data)
    with pytest.raises(CellQuantError, match="Editing never segments again"):
        controller.remeasure_persisted_result(image_id)
    controller.experiment.images[0].pixel_size_x = 0.7
    controller.experiment.images[0].pixel_size_y = 0.7
    data["object_set"]["parameters"]["flow_threshold"] = 0.4
    controller.set_recipe(data)
    with pytest.raises(CellQuantError):
        controller.remeasure_persisted_result(image_id)


def test_measurement_changes_reuse_the_objects(tmp_path: Path, monkeypatch):
    _source, package, run_dir = _finished(tmp_path, monkeypatch)
    outcome = import_results(package, run_dir, tmp_path / "imported")
    _no_engine(monkeypatch)
    controller = AnalysisController.open(outcome.destination)
    image_id = controller.experiment.images[0].image_id
    data = controller.recipe.model_dump(mode="json")
    data["measurements"].append({"id": "green_max", "channel": 0, "region": {"type": "object"}, "statistic": "max"})
    controller.set_recipe(data)
    result = controller.run_image(image_id)
    assert "green_max" in result.objects.columns and result.provenance["segmentation_origin"] == "hpc"


def test_reimport_is_idempotent_and_other_destinations_are_protected(tmp_path: Path, monkeypatch):
    _source, package, run_dir = _finished(tmp_path, monkeypatch)
    destination = tmp_path / "imported"
    first = import_results(package, run_dir, destination)
    again = import_results(package, run_dir, destination)
    assert again.already_imported and again.record.experiment_id == first.record.experiment_id
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "notes.txt").write_text("mine")
    with pytest.raises(ImportFailed, match="not empty"):
        import_results(package, run_dir, occupied)
    assert (occupied / "notes.txt").read_text() == "mine" and len(list(occupied.iterdir())) == 1
    index = read_json(run_dir / "results.json")
    index["updated_at"] = "2030-01-01T00:00:00Z"
    (run_dir / "results.json").write_text(json.dumps(index))
    with pytest.raises(ImportFailed, match="not empty"):
        import_results(package, run_dir, destination)  # different results into an existing import
    empty = tmp_path / "empty"
    empty.mkdir()
    assert not import_results(package, run_dir, empty).already_imported


def test_damaged_results_import_nothing(tmp_path: Path, monkeypatch):
    _source, package, run_dir = _finished(tmp_path, monkeypatch)
    task = next((run_dir / "tasks" / "a000002").iterdir())
    labels = task / "labels" / "a000002_automated.tif"
    labels.write_bytes(labels.read_bytes()[:-8])
    with pytest.raises(ImportFailed) as failure:
        import_results(package, run_dir, tmp_path / "imported")
    assert failure.value.exit_code == 5 and failure.value.issues[0].acquisition_id == "a000002"
    assert not (tmp_path / "imported").exists()
    assert not [path for path in tmp_path.iterdir() if ".importing-" in path.name]


def test_results_from_another_package_are_refused(tmp_path: Path, monkeypatch):
    _source, package, run_dir = _finished(tmp_path, monkeypatch)
    _other, other_package, _runtime = prepared_package(tmp_path / "other")
    with pytest.raises(ImportFailed, match="different package"):
        preview_import(other_package, run_dir)


def test_an_unfinished_index_is_shown_as_unfinished(tmp_path: Path, monkeypatch):
    _source, package, run_dir = _finished(tmp_path, monkeypatch)
    index = read_json(run_dir / "results.json")
    index["finalized"] = False
    index["compute_outcome"] = "running"
    index["acquisitions"][1].update({"state": "running", "commit_path": None, "commit_sha256": None})
    (run_dir / "results.json").write_text(json.dumps(index))
    preview = preview_import(package, run_dir)
    assert not preview.complete and "had not finished" in preview.status_text()
    assert [row.state for row in preview.rows] == ["succeeded", "running (unfinished record)"]
    outcome = import_results(package, run_dir, tmp_path / "imported", allow_partial=True, preview=preview)
    controller = AnalysisController.open(outcome.destination)
    second = controller.experiment.images[1]
    assert second.processing_status == "needs_attention" and "running" in second.last_message
    assert controller.recall(second.image_id) is None


def test_long_destinations_are_refused_before_writing(tmp_path: Path, monkeypatch):
    _source, package, run_dir = _finished(tmp_path, monkeypatch)
    deep = tmp_path / ("d" * 100) / ("e" * 100)
    with pytest.raises(ImportFailed) as failure:
        import_results(package, run_dir, deep)
    assert failure.value.issues[0].code == "E_PATH_TOO_LONG" and not deep.exists()


def test_running_one_image_does_not_hide_the_other_imported_results(tmp_path: Path, monkeypatch):
    _source, package, run_dir = _finished(tmp_path, monkeypatch, count=3)
    outcome = import_results(package, run_dir, tmp_path / "imported")
    _no_engine(monkeypatch)
    controller = AnalysisController.open(outcome.destination)
    ids = [image.image_id for image in controller.experiment.images]
    controller.run_image(ids[0])
    reopened = AnalysisController.open(outcome.destination)
    assert reopened.experiment.latest_run_id != "run_1"
    assert all(reopened.recall(image_id) is not None for image_id in ids)
    exported = reopened.export(tmp_path / "export")
    assert len(pd.read_csv(exported / "image_summary.csv")) == 3
