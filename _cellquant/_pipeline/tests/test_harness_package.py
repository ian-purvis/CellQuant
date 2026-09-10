import json
from pathlib import Path

import numpy as np
import tifffile

from cellquant.config import RunConfig
from cellquant.contracts import LabelVolume, PipelineEvent
from cellquant.harness import HarnessCase, run_case


def _config():
    return RunConfig({
        "schema_version": 1,
        "io": {"series": 0, "position": 0, "lazy": False, "axes_override": "ZYX", "spacing_override_um": [2.0, 0.5, 0.5], "recursive": False, "suffixes": [".tif", ".tiff", ".nd2"]},
        "preprocess": {
            "channel": 0,
            "normalize": {"enabled": False, "low_percentile": None, "high_percentile": None, "scope": None},
            "rescale": {"enabled": False, "target_spacing_um": None, "interpolation": None, "antialias": None, "boundary": "constant", "cval": 0.0},
            "denoise": {"enabled": False, "method": None, "parameters": {}, "boundary": "reflect", "cval": 0.0},
        },
        "segment": {
            "engine": "v4", "model": "cpsam", "model_sha256": "a" * 64,
            "mode": "volume_3d", "z_index": None, "diameter_px": 30, "anisotropy": 4,
            "min_size": 200, "flow_threshold": 0.4, "cellprob_threshold": 0,
            "stitch_threshold": 0.0, "channel_axis": None, "z_axis": 0,
            "tile": True, "tile_overlap": 0.1, "batch_size": 8,
            "augment": False, "resample": True, "normalize": True,
            "device": "cpu", "allow_cpu_fallback": False,
            "use_bfloat16": True, "flow3D_smooth": 0,
            "max_size_fraction": 0.4, "niter": None, "bsize": 256,
            "compute_masks": True, "channels": None, "rescale_factor": None,
            "progress": None,
            "model_type": None, "diam_mean": None, "nchan": 1,
        },
        "postprocess": {"min_volume_um3": None, "max_volume_um3": None, "min_voxels": None, "max_voxels": None, "remove_border_faces": [], "relabel": False},
        "measure": {"channels": "all", "intensity_statistics": ["mean", "median", "min", "max", "integrated_intensity"]},
        "viz": {"low_percentile": 1, "high_percentile": 99, "label_seed": 0, "dpi": 50},
        "runtime": {"seed": 0, "deterministic_torch": True, "hash_inputs": True, "output_compression": "none"},
    })


def test_harness_writes_complete_artifacts_with_injected_runner(tmp_path):
    source = tmp_path / "input.tif"
    tifffile.imwrite(source, np.arange(3 * 12 * 14, dtype=np.uint16).reshape(3, 12, 14))
    config = _config()

    def runner(image, config, cancel, events):
        events(PipelineEvent("stage_started", "run", "case", "segment", "2026-01-01T00:00:00Z"))
        data = np.zeros(image.data.shape[:3], dtype=np.uint32)
        data[:, 2:7, 3:9] = 1
        events(PipelineEvent("stage_finished", "run", "case", "segment", "2026-01-01T00:00:01Z"))
        return LabelVolume(data, image.spacing_um, {"input_fingerprint": "placeholder"})

    # The harness owns generated run/file IDs. Adapt them inside this test runner.
    def adapting_runner(image, config, cancel, events):
        data = np.zeros(image.data.shape[:3], dtype=np.uint32)
        data[:, 2:7, 3:9] = 1
        return LabelVolume(data, image.spacing_um, {"input_fingerprint": "placeholder"})

    result = run_case(HarnessCase("case", source), config, tmp_path / "out", pipeline_runner=adapting_runner)
    assert result.success and result.label_count == 1
    assert (result.output_dir / "labels.tif").is_file()
    assert (result.output_dir / "provenance.json").is_file()
    assert (result.output_dir / "qc_mid_stack_outlines.png").is_file()
    status = json.loads((result.output_dir / "status.json").read_text())
    assert status["status"] == "complete"


def test_harness_records_failure_without_claiming_success(tmp_path):
    source = tmp_path / "input.tif"
    tifffile.imwrite(source, np.zeros((2, 5, 5), dtype=np.uint16))

    def broken(*_):
        raise RuntimeError("deliberate")

    result = run_case(HarnessCase("bad", source), _config(), tmp_path / "failed", pipeline_runner=broken)
    assert not result.success
    assert result.exception["type"] == "RuntimeError"
    assert json.loads((result.output_dir / "status.json").read_text())["status"] == "failed"
    provenance = json.loads((result.output_dir / "provenance.json").read_text())
    assert provenance["exception"]["message"] == "deliberate"
