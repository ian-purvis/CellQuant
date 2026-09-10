import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cellquant.classify import ClassificationRecipe, classify_labels
from cellquant.classify.store import reopen_classification, reclassify_run, save_classification
from cellquant.contracts import ImageVolume, LabelVolume, MutableCancellationToken, PipelineCancelled


def evidence():
    labels = LabelVolume(np.array([[[1, 1, 2, 2]]], dtype=np.uint32), (2., 1., 1.))
    image = ImageVolume(np.array([[[[0, 8], [8, 8], [8, 0], [8, 0]]]], dtype=np.uint16),
                        labels.spacing_um, ("A", "B"), Path("retina.tif"),
                        {"analysis_volume": {"mode": "single_plane_2d", "z_index": 3}})
    recipe = ClassificationRecipe({"schema_version": 1, "name": "../never-a-path",
        "calibration_group": "trial", "region_policy": "whole_object", "markers": [
        {"name": "A", "channel": 0, "low": 5, "positive_fraction": 0.75},
        {"name": "B", "channel": 1, "low": 5, "positive_fraction": 0.5}]})
    return image, labels, recipe


def assert_result_equal(first, second):
    for name in ("calls", "queries", "patterns", "exclusions"):
        pd.testing.assert_frame_equal(getattr(first, name), getattr(second, name))
    assert first.metadata == second.metadata


def test_roundtrip_reclassification_preserves_exact_inputs_and_results(tmp_path):
    image, labels, recipe = evidence()
    context = {"specimen_id": "animal-1", "region_id": "central", "notes": "sample"}
    region = np.ones(labels.data.shape, bool)
    path, result = save_classification(tmp_path, image, labels, recipe, region=region, context=context)
    saved = reopen_classification(path)
    np.testing.assert_array_equal(saved.image.data, image.data)
    np.testing.assert_array_equal(saved.labels.data, labels.data)
    np.testing.assert_array_equal(saved.region, region)
    assert saved.image.metadata == image.metadata
    assert saved.image.spacing_um == image.spacing_um
    assert saved.context == context
    assert saved.recipe.fingerprint == recipe.fingerprint
    second_path, second = reclassify_run(path, tmp_path)
    assert path != second_path
    assert_result_equal(result, second)
    assert path.name.startswith("classify_")
    assert (path / "complete.json").is_file()
    assert not (path / "complete.pending").exists()


def test_label_edits_create_fresh_results_and_preserve_original(tmp_path):
    image, labels, recipe = evidence()
    path, original = save_classification(tmp_path, image, labels, recipe)
    saved = reopen_classification(path)
    saved.labels.data[:] = 1
    new_path, edited = save_classification(tmp_path, saved.image, saved.labels, saved.recipe)
    assert new_path != path
    assert len(edited.calls) != len(original.calls)
    np.testing.assert_array_equal(reopen_classification(path).labels.data, labels.data)
    assert_result_equal(edited, classify_labels(saved.image, saved.labels, saved.recipe))


@pytest.mark.parametrize("name", ["image.npy", "labels.npy", "recipe.json", "context.json",
    "inputs.json", "metadata.json", "calls.csv", "queries.csv", "patterns.csv", "exclusions.csv", "manifest.json"])
def test_any_modified_artifact_is_rejected(tmp_path, name):
    path, _ = save_classification(tmp_path, *evidence())
    with (path / name).open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        reopen_classification(path)


def rewrite_manifest(path, change):
    manifest = json.loads((path / "manifest.json").read_text())
    change(manifest)
    encoded = json.dumps(manifest).encode()
    (path / "manifest.json").write_bytes(encoded)
    (path / "complete.json").write_text(json.dumps({"schema_version": 1,
        "manifest_sha256": hashlib.sha256(encoded).hexdigest()}))


@pytest.mark.parametrize("filename", ["../outside.npy", "C:/outside.npy", "..\\outside.npy"])
def test_manifest_path_traversal_rejected_before_read(tmp_path, filename):
    path, _ = save_classification(tmp_path, *evidence())
    rewrite_manifest(path, lambda m: m["files"].update({filename: m["files"]["image.npy"]}))
    with pytest.raises(ValueError, match="artifact names"):
        reopen_classification(path)


def test_omitting_required_artifact_from_manifest_cannot_hide_it(tmp_path):
    path, _ = save_classification(tmp_path, *evidence())
    rewrite_manifest(path, lambda m: m["files"].pop("labels.npy"))
    with pytest.raises(ValueError, match="artifact names"):
        reopen_classification(path)


def test_object_npy_rejected_even_with_matching_checksum(tmp_path):
    path, _ = save_classification(tmp_path, *evidence())
    np.save(path / "image.npy", np.array([{"untrusted": "object"}], dtype=object))
    payload = (path / "image.npy").read_bytes()
    rewrite_manifest(path, lambda m: m["files"].update({"image.npy": {
        "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}}))
    with pytest.raises(ValueError, match="allow_pickle=False"):
        reopen_classification(path)


def test_missing_completion_is_not_a_completed_run(tmp_path):
    path, _ = save_classification(tmp_path, *evidence())
    (path / "complete.json").unlink()
    with pytest.raises(ValueError, match="complete.json"):
        reopen_classification(path)


def test_cancelled_before_scoring_creates_no_run(tmp_path):
    cancel = MutableCancellationToken()
    cancel.cancel()
    with pytest.raises(PipelineCancelled):
        save_classification(tmp_path, *evidence(), cancel=cancel)
    assert list(tmp_path.iterdir()) == []


def test_cancelled_during_array_write_never_publishes_completion(tmp_path, monkeypatch):
    cancel = MutableCancellationToken()
    original = np.save
    def cancel_after_write(*args, **kwargs):
        original(*args, **kwargs)
        cancel.cancel()
    monkeypatch.setattr(np, "save", cancel_after_write)
    with pytest.raises(PipelineCancelled):
        save_classification(tmp_path, *evidence(), cancel=cancel)
    runs = list(tmp_path.glob("classify_*"))
    assert len(runs) == 1
    assert not (runs[0] / "complete.json").exists()
    with pytest.raises(ValueError):
        reopen_classification(runs[0])


def test_long_path_rejected_before_creation(tmp_path):
    output = tmp_path / ("x" * 150)
    with pytest.raises(ValueError, match="shorter"):
        save_classification(output, *evidence())
    assert not output.exists()
