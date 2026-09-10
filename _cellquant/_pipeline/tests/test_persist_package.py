import json

import numpy as np
import pytest
import tifffile

from cellquant.config import RunConfig
from cellquant.contracts import LabelVolume, PipelineEvent
from cellquant.persist import RunStore


def _config():
    raw = {
        "schema_version": 1,
        "io": {"series": 0, "position": 0, "lazy": True, "axes_override": None, "spacing_override_um": None, "recursive": True, "suffixes": [".tif", ".tiff", ".nd2"]},
        "preprocess": {
            "channel": 0,
            "normalize": {"enabled": False, "low_percentile": None, "high_percentile": None, "scope": None},
            "rescale": {"enabled": False, "target_spacing_um": None, "interpolation": None, "antialias": None, "boundary": None, "cval": None},
            "denoise": {"enabled": False, "method": None, "parameters": {}, "boundary": None, "cval": None},
        },
        "postprocess": {"min_volume_um3": None, "max_volume_um3": None, "min_voxels": None, "max_voxels": None, "remove_border_faces": [], "relabel": False},
        "measure": {"intensity_statistics": ["mean"], "channels": "all"},
        "viz": {"low_percentile": 1, "high_percentile": 99, "label_seed": 0, "dpi": 72},
        "runtime": {"seed": 3, "deterministic_torch": True, "hash_inputs": True, "output_compression": "none"},
        "segment": {
            "engine": "v4", "model": "cpsam", "model_sha256": "a" * 64,
            "mode": "volume_3d", "z_index": None, "diameter_px": 30, "anisotropy": 2,
            "min_size": 200, "flow_threshold": 0.4, "cellprob_threshold": 0,
            "stitch_threshold": 0, "channel_axis": None, "z_axis": 0,
            "tile": True, "tile_overlap": 0.1, "batch_size": 8,
            "augment": False, "resample": True, "normalize": False,
            "device": "cuda", "allow_cpu_fallback": False,
            "use_bfloat16": True, "flow3D_smooth": 0,
            "max_size_fraction": 0.4, "niter": None, "bsize": 256,
            "compute_masks": True, "channels": None, "rescale_factor": None,
            "progress": None,
            "model_type": None, "diam_mean": None, "nchan": None,
        },
    }
    return RunConfig(raw)


def _complete_store(tmp_path, labels=None):
    config = _config()
    store = RunStore.create(tmp_path / "run", "input-fp", config.fingerprint, run_id="r1")
    array = np.array([[[0, 1], [2, 2]]], dtype=np.uint32) if labels is None else labels
    store.write_labels(LabelVolume(array, (2.0, 0.5, 0.5)))
    store.write_config(config)
    store.write_provenance({"model_sha256": "a" * 64})
    store.append_event(PipelineEvent("stage_finished", "r1", "f1", "persist", "2026-09-03T00:00:00Z"))
    measurement = tmp_path / "objects.csv"
    measurement.write_text("label,voxel_count\n1,1\n", encoding="utf-8")
    qc = tmp_path / "qc.png"
    qc.write_bytes(b"not interpreted by persistence, but checksummed")
    store.register_measurement(measurement)
    store.register_qc_artifact(qc)
    store.commit()
    return store


def test_complete_store_is_resumable_and_uses_uint16_losslessly(tmp_path):
    store = _complete_store(tmp_path)
    assert tifffile.imread(store.labels_path).dtype == np.uint16
    assert store.read_labels().dtype == np.uint32
    assert store.is_resumable("input-fp", _config().fingerprint)
    assert not store.is_resumable("different", _config().fingerprint)
    assert not store.is_resumable("input-fp", "different")


def test_uint32_is_used_when_uint16_would_lose_ids(tmp_path):
    labels = np.array([[[0, 70_000]]], dtype=np.uint32)
    store = _complete_store(tmp_path, labels)
    assert tifffile.imread(store.labels_path).dtype == np.uint32
    np.testing.assert_array_equal(store.read_labels(), labels)


@pytest.mark.parametrize("artifact", ["labels.tif", "objects.csv", "events.jsonl"])
def test_corruption_or_truncation_fails_closed(tmp_path, artifact):
    store = _complete_store(tmp_path)
    path = store.directory / artifact
    path.write_bytes(path.read_bytes()[:3])
    assert not store.is_resumable("input-fp", _config().fingerprint)


def test_marker_without_artifacts_and_temporary_files_are_not_resumable(tmp_path):
    config = _config()
    store = RunStore.create(tmp_path / "run", "input-fp", config.fingerprint)
    store.status_path.write_text(json.dumps({"status": "complete"}), encoding="utf-8")
    assert not store.is_resumable("input-fp", config.fingerprint)
    complete = _complete_store(tmp_path / "other")
    (complete.directory / ".labels.tif.abandoned.tmp").write_bytes(b"partial")
    assert not complete.is_resumable("input-fp", config.fingerprint)


def test_completion_requires_all_artifact_roles(tmp_path):
    config = _config()
    store = RunStore.create(tmp_path / "run", "input-fp", config.fingerprint)
    store.write_labels(LabelVolume(np.zeros((1, 2, 2), dtype=np.uint32), (1, 1, 1)))
    store.write_config(config)
    store.write_provenance({})
    with pytest.raises(RuntimeError, match="measurement"):
        store.commit()


def test_reusing_directory_starts_clean_non_resumable_run(tmp_path):
    config = _config()
    first = _complete_store(tmp_path)
    assert first.is_resumable("input-fp", config.fingerprint)
    reviewed = first.directory / "labels_reviewed.tif"
    reviewed.write_bytes(b"reviewed-sidecar")

    second = RunStore.create(
        first.directory, "input-fp", config.fingerprint, run_id="r2"
    )
    assert not second.is_resumable("input-fp", config.fingerprint)
    assert not reviewed.is_file()
    status = json.loads(second.status_path.read_text(encoding="utf-8"))
    assert status["status"] == "running"
    assert status["run_id"] == "r2"

    second.append_event(
        PipelineEvent("stage_started", "r2", "f1", "persist", "2026-09-03T00:00:01Z")
    )
    events = [json.loads(line) for line in second.events_path.read_text().splitlines()]
    assert {event["run_id"] for event in events} == {"r2"}


def test_create_refuses_complete_store_with_mismatched_fingerprints(tmp_path):
    config = _config()
    first = _complete_store(tmp_path)
    reviewed = first.directory / "labels_reviewed.tif"
    reviewed.write_bytes(b"keep-me")
    with pytest.raises(FileExistsError, match="fingerprints differ"):
        RunStore.create(first.directory, "other-input", config.fingerprint, run_id="r2")
    assert first.is_resumable("input-fp", config.fingerprint)
    assert reviewed.read_bytes() == b"keep-me"


def test_configured_label_compression_is_applied_and_requires_config_first(tmp_path):
    raw = dict(_config().raw)
    raw["runtime"] = {**raw["runtime"], "output_compression": "zlib"}
    config = RunConfig(raw)
    store = RunStore.create(tmp_path / "compressed", "input-fp", config.fingerprint)
    store.write_config(config)
    store.write_labels(LabelVolume(np.ones((1, 4, 4), dtype=np.uint32), (1, 1, 1)))
    with tifffile.TiffFile(store.labels_path) as tif:
        assert tif.pages[0].compression.name != "NONE"

    wrong_order = RunStore.create(tmp_path / "wrong-order", "input-fp", config.fingerprint)
    wrong_order.write_labels(LabelVolume(np.ones((1, 2, 2), dtype=np.uint32), (1, 1, 1)))
    with pytest.raises(RuntimeError, match="write_config must precede write_labels"):
        wrong_order.write_config(config)


def test_create_for_user_output_publishes_to_cloud_path(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localapp"))
    cloud = tmp_path / "OneDrive - Contoso" / "Batch" / "sample.nd2.cellquant"
    config = _config()
    store = RunStore.create_for_user_output(
        cloud, "input-fp", config.fingerprint, run_id="cloud1"
    )
    assert store.publish_to == cloud
    assert "staging" in str(store.directory)

    array = np.array([[[0, 1], [2, 2]]], dtype=np.uint32)
    store.write_labels(LabelVolume(array, (2.0, 0.5, 0.5)))
    store.write_config(config)
    store.write_provenance({"model_sha256": "a" * 64})
    store.append_event(PipelineEvent("stage_finished", "cloud1", "f1", "persist", "2026-09-03T00:00:00Z"))
    measurement = store.directory / "objects.csv"
    measurement.write_text("label,voxel_count\n1,1\n", encoding="utf-8")
    qc = store.directory / "qc.png"
    qc.write_bytes(b"png-bytes")
    store.register_measurement(measurement)
    store.register_qc_artifact(qc)
    store.commit()

    assert (cloud / "status.json").is_file()
    assert (cloud / "events.jsonl").is_file()
    assert (cloud / "labels.tif").is_file()
    published = RunStore(cloud, "input-fp", config.fingerprint)
    assert published.is_resumable("input-fp", config.fingerprint)
