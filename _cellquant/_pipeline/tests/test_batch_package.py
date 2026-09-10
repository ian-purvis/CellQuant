import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cellquant.batch import build_queue, run_batch
from cellquant.config import RunConfig
from cellquant.contracts import ImageVolume, LabelVolume, MutableCancellationToken
from cellquant.measure import MeasurementTables


def _config():
    raw = {
        "schema_version": 1,
        "io": {"series": 0, "position": 0, "lazy": True, "axes_override": None, "spacing_override_um": None, "recursive": True, "suffixes": [".tif", ".tiff", ".nd2"]},
        "preprocess": {
            "channel": 0,
            "normalize": {"enabled": False, "low_percentile": None, "high_percentile": None, "scope": None},
            "rescale": {"enabled": False, "target_spacing_um": None, "interpolation": None, "antialias": None, "boundary": "constant", "cval": 0},
            "denoise": {"enabled": False, "method": None, "parameters": {}, "boundary": "reflect", "cval": 0},
        },
        "segment": {
            "engine": "v4", "model": "cpsam", "model_sha256": "a" * 64,
            "mode": "volume_3d", "z_index": None, "diameter_px": 30, "anisotropy": 2,
            "min_size": 200, "flow_threshold": 0.4, "cellprob_threshold": 0,
            "stitch_threshold": 0, "channel_axis": None, "z_axis": 0,
            "tile": True, "tile_overlap": 0.1, "batch_size": 8, "augment": False,
            "resample": True, "normalize": False, "device": "cpu", "allow_cpu_fallback": False,
            "use_bfloat16": False, "flow3D_smooth": 0, "max_size_fraction": 0.4,
            "niter": None, "bsize": 256, "compute_masks": True, "channels": None,
            "rescale_factor": None, "progress": None, "model_type": None, "diam_mean": None, "nchan": None,
        },
        "postprocess": {"min_volume_um3": None, "max_volume_um3": None, "min_voxels": None, "max_voxels": None, "remove_border_faces": [], "relabel": False},
        "measure": {"intensity_statistics": ["mean"], "channels": "all"},
        "viz": {"low_percentile": 0, "high_percentile": 100, "label_seed": 0, "dpi": 20},
        "runtime": {"seed": 0, "deterministic_torch": True, "hash_inputs": True, "output_compression": "none"},
    }
    return RunConfig(raw)


def _install_fakes(monkeypatch, *, bad_name=None):
    def fake_open(path, **kwargs):
        path = Path(path)
        if path.name == bad_name:
            raise ValueError("deliberate bad image")
        return ImageVolume(np.ones((1, 4, 4, 1), dtype=np.uint16), (2, 1, 1), ("DAPI",), path)

    def fake_pipeline(image, config, cancel, events):
        labels = np.zeros((1, 4, 4), dtype=np.uint32)
        labels[:, 1:3, 1:3] = 1
        return LabelVolume(labels, image.spacing_um, {"engine": "fake"})

    def fake_measure(image, labels, config, cancel, events):
        return MeasurementTables(
            pd.DataFrame([{"label": 1, "voxel_count": 4}]),
            pd.DataFrame([{"label": 1, "channel": "DAPI", "mean": 1.0}]),
        )

    def fake_qc(image, labels, output_dir, config):
        path = Path(output_dir) / "qc.png"
        path.write_bytes(b"qc")
        return {"qc": path}

    monkeypatch.setattr("cellquant.batch.open_volume", fake_open)
    monkeypatch.setattr("cellquant.batch.run_pipeline", fake_pipeline)
    monkeypatch.setattr("cellquant.batch.run_measurements", fake_measure)
    monkeypatch.setattr("cellquant.batch.make_qc_figures", fake_qc)


def test_build_queue_is_stable_preserves_extensions_and_avoids_collisions(tmp_path):
    left = tmp_path / "left"
    right = tmp_path / "right"
    (left / "nested").mkdir(parents=True)
    (right / "nested").mkdir(parents=True)
    (left / "nested" / "same.tif").write_bytes(b"left")
    (right / "nested" / "same.tif").write_bytes(b"right")
    config = _config()

    forward = build_queue([right, left], tmp_path / "out", config)
    reverse = build_queue([left, right], tmp_path / "out", config)

    assert [item.source for item in forward] == [item.source for item in reverse]
    assert len({str(item.output_dir).casefold() for item in forward}) == 2
    assert all(item.output_dir.name.endswith(".tif.cellquant") for item in forward)
    assert {item.output_dir.relative_to(tmp_path / "out").parts[0] for item in forward} == {"left", "right"}


def test_build_queue_respects_suffix_filter(tmp_path):
    inputs = tmp_path / "input"
    inputs.mkdir()
    (inputs / "keep.tif").write_bytes(b"tif")
    (inputs / "skip.nd2").write_bytes(b"nd2")
    config = _config()
    raw = dict(config.raw)
    raw["io"] = {**raw["io"], "suffixes": [".tif"]}
    queue = build_queue([inputs], tmp_path / "out", RunConfig(raw))
    assert [item.source.name for item in queue] == ["keep.tif"]


def test_recursive_queue_excludes_nested_output_tree_and_prior_run_labels(tmp_path):
    inputs = tmp_path / "input"
    inputs.mkdir()
    acquisition = inputs / "a.tif"
    acquisition.write_bytes(b"acquisition")
    output_root = inputs / "out"
    prior_run = output_root / "input" / "a.tif.cellquant"
    prior_run.mkdir(parents=True)
    (prior_run / "labels.tif").write_bytes(b"generated labels")

    queue = build_queue([inputs], output_root, _config())

    assert [item.source for item in queue] == [acquisition.resolve()]
    assert all("labels.tif" not in item.file_id for item in queue)


def test_output_tree_exclusion_does_not_match_similarly_named_sibling(tmp_path):
    inputs = tmp_path / "input"
    output_root = inputs / "out"
    sibling = inputs / "out_archive"
    sibling.mkdir(parents=True)
    acquisition = sibling / "another.tif"
    acquisition.write_bytes(b"another acquisition")

    queue = build_queue([inputs], output_root, _config())

    assert [item.source for item in queue] == [acquisition.resolve()]


def test_prior_cellquant_directory_is_excluded_even_outside_output_root(tmp_path):
    inputs = tmp_path / "input"
    prior_run = inputs / "old.tif.cellquant"
    prior_run.mkdir(parents=True)
    (prior_run / "labels.tif").write_bytes(b"generated labels")

    queue = build_queue([inputs], tmp_path / "separate-output", _config())

    assert queue == []


def test_one_bad_file_does_not_stop_later_and_good_artifacts_commit(tmp_path, monkeypatch):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    for name in ("a.tif", "b_bad.tif", "c.tif"):
        (inputs / name).write_bytes(name.encode())
    _install_fakes(monkeypatch, bad_name="b_bad.tif")
    queue = build_queue([inputs], tmp_path / "out", _config())

    summary = run_batch(queue, _config(), MutableCancellationToken())

    assert (summary.completed, summary.failed) == (2, 1)
    assert [result.status for result in summary.results] == ["completed", "failed", "completed"]
    for result in (summary.results[0], summary.results[2]):
        marker = json.loads((Path(result.output_dir) / "status.json").read_text())
        assert marker["status"] == "complete"
        assert {entry["role"] for entry in marker["artifacts"]} >= {"labels", "config", "provenance", "event_log", "measurement", "qc"}
    rows = (tmp_path / "out" / "failures.csv").read_text().splitlines()
    assert len(rows) == 2
    assert "b_bad.tif" in rows[1]


def test_exact_resume_skips_pipeline_but_changed_input_reruns(tmp_path, monkeypatch):
    source = tmp_path / "one.tif"
    source.write_bytes(b"first")
    _install_fakes(monkeypatch)
    config = _config()
    queue = build_queue([source], tmp_path / "out", config)
    first = run_batch(queue, config, MutableCancellationToken())
    assert first.completed == 1

    def forbidden(*args, **kwargs):
        raise AssertionError("pipeline should not run during exact resume")
    monkeypatch.setattr("cellquant.batch.run_pipeline", forbidden)
    second = run_batch(build_queue([source], tmp_path / "out", config), config, MutableCancellationToken())
    assert second.resumed == 1

    source.write_bytes(b"changed")
    changed = build_queue([source], tmp_path / "out", config)
    # A changed input invalidates resume. Completed stores with different
    # fingerprints are preserved (not silently overwritten); the item fails.
    third = run_batch(changed, config, MutableCancellationToken())
    assert (third.resumed, third.failed) == (0, 1)
    assert "FileExistsError" in (third.results[0].message or "")
    marker = json.loads((Path(third.results[0].output_dir) / "status.json").read_text())
    assert marker["status"] == "complete"
    assert marker["input_fingerprint"] != changed[0].input_fingerprint


def test_pre_cancel_marks_every_item_and_writes_explicit_summary(tmp_path, monkeypatch):
    source = tmp_path / "inputs"
    source.mkdir()
    (source / "a.tif").write_bytes(b"a")
    (source / "b.tif").write_bytes(b"b")
    _install_fakes(monkeypatch)
    token = MutableCancellationToken()
    token.cancel()

    summary = run_batch(build_queue([source], tmp_path / "out", _config()), _config(), token)

    assert summary.cancelled == 2
    assert json.loads(summary.summary_path.read_text())["cancelled"] == 2
