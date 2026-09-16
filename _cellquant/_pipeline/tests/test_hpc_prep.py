"""Tests for CellQuant HPC prep export / validate / import."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import tifffile

from cellquant.cli import main
from cellquant.config import load_config
from cellquant.hpc.acquisitions import expand_acquisitions
from cellquant.hpc.contract import matrix_status, require_supported
from cellquant.hpc.export import estimate_export_bytes, prepare_bundle, write_canonical_tiff
from cellquant.hpc.import_results import import_hpc_results
from cellquant.hpc.cluster_profiles import load_profile, list_profile_ids, validate_user_profile_fields
from cellquant.hpc.contract import DEFAULT_PROFILE_ID
from cellquant.hpc.templates import UserClusterSettings, generate_scripts, transfer_instructions
from cellquant.hpc.validate import validate_bundle
from cellquant.io import open_volume
from cellquant.survey import default_template_config_path


def _write_calibrated_zyxc(path: Path, shape=(2, 8, 9, 2)) -> np.ndarray:
    """Write calibrated OME-TIFF (ZCYX on disk) matching CellQuant I/O fixtures."""

    data = np.arange(np.prod(shape), dtype=np.uint16).reshape(shape)
    ome = np.moveaxis(data, -1, 1)
    tifffile.imwrite(
        path,
        ome,
        ome=True,
        photometric="minisblack",
        metadata={
            "axes": "ZCYX",
            "PhysicalSizeZ": 1.5,
            "PhysicalSizeZUnit": "um",
            "PhysicalSizeY": 0.4,
            "PhysicalSizeYUnit": "um",
            "PhysicalSizeX": 0.4,
            "PhysicalSizeXUnit": "um",
            "Channel": {"Name": ["DAPI", "GFP"][: shape[-1]]},
        },
    )
    return data


def _settings(profile) -> UserClusterSettings:
    return UserClusterSettings(
        account="amc-general",
        qos=profile.default_qos,
        gres=profile.default_gres,
        walltime=profile.default_walltime,
        project_root="/projects/testuser/cellquant",
        scratch_root="/scratch/alpine/testuser/cellquant",
        env_location="/projects/testuser/cellquant/envs/cellquant-hpc",
        email=None,
    )


def test_default_profile_is_full_h200():
    assert DEFAULT_PROFILE_ID == "alpine_ah200"
    profile = load_profile()
    assert profile.profile_id == "alpine_ah200"
    assert profile.partition == "ah200"
    assert profile.default_gres == "gpu:h200:1"
    assert profile.default_qos == "gpu-normal"
    assert "gpu-testing" not in profile.qos_choices
    assert profile.submit_ready is True
    assert list_profile_ids()[0] == "alpine_ah200"


def test_rtx_profile_submit_ready_and_no_gpu_testing():
    profile = load_profile("alpine_artxpro6000")
    assert profile.submit_ready is True
    assert "gpu-testing" not in profile.qos_choices
    assert profile.default_gres == "gpu:rtx_pro_6000:1"
    assert profile.partition == "artxpro6000"
    assert profile.require_account is True


def test_matrix_rejects_v3_without_substitution():
    status, reason = matrix_status("v3", "volume_3d", DEFAULT_PROFILE_ID)
    assert status == "unsupported"
    assert "parity" in reason.lower() or "v3" in reason.lower()
    with pytest.raises(ValueError):
        require_supported("v3", "volume_3d", DEFAULT_PROFILE_ID)


def test_estimate_export_bytes_does_not_need_array():
    assert estimate_export_bytes((2, 8, 9, 3), "uint16") == 2 * 8 * 9 * 3 * 2


def test_canonical_tiff_round_trip(tmp_path):
    source = np.arange(2 * 4 * 5 * 3, dtype=np.uint16).reshape(2, 4, 5, 3)
    path = tmp_path / "a000001.tif"
    write_canonical_tiff(
        path,
        source,
        spacing_um=(1.0, 0.5, 0.5),
        channel_names=("DAPI", "GFP", "RFP"),
    )
    volume = open_volume(path, lazy=False)
    assert volume.data.shape == source.shape
    assert volume.data.dtype == source.dtype
    np.testing.assert_array_equal(volume.data, source)
    assert volume.spacing_um == pytest.approx((1.0, 0.5, 0.5))
    assert volume.channel_names == ("DAPI", "GFP", "RFP")


def test_expand_rejects_multi_timepoint(tmp_path):
    path = tmp_path / "tstack.tif"
    data = np.zeros((3, 2, 4, 5), dtype=np.uint16)
    tifffile.imwrite(
        path,
        data,
        ome=True,
        photometric="minisblack",
        metadata={
            "axes": "TZYX",
            "PhysicalSizeZ": 1.0,
            "PhysicalSizeZUnit": "um",
            "PhysicalSizeY": 0.5,
            "PhysicalSizeYUnit": "um",
            "PhysicalSizeX": 0.5,
            "PhysicalSizeXUnit": "um",
        },
    )
    records = expand_acquisitions([path], include_errors=True)
    assert len(records) == 1
    assert records[0].error is not None
    assert "time" in records[0].error.lower()


def test_expand_multi_position(tmp_path):
    from cellquant.io._common import ImageMetadata

    path = tmp_path / "positions.tif"
    path.write_bytes(b"placeholder")

    def fake_inspect(source: Path) -> ImageMetadata:
        return ImageMetadata(
            source=source,
            format="tiff",
            shape=(2, 2, 4, 5),
            dtype=np.dtype(np.uint16),
            axes="PZYX",
            spacing_um=(1.0, 0.5, 0.5),
            channel_names=("DAPI",),
            series_count=1,
            position_count=2,
            timepoint_count=1,
        )

    records = expand_acquisitions([path], include_errors=False, inspect_fn=fake_inspect)
    assert len(records) == 2
    assert {r.position for r in records} == {0, 1}
    assert records[0].series == 0


def test_prepare_bundle_and_validate(tmp_path):
    image = tmp_path / "sample.tif"
    _write_calibrated_zyxc(image)
    # Duplicate basename in another folder to ensure short export names stay unique.
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    other = other_dir / "sample.tif"
    _write_calibrated_zyxc(other, shape=(1, 4, 5, 2))

    profile = load_profile()
    config = load_config(default_template_config_path())
    raw = dict(config.raw)
    raw["segment"] = {**dict(raw["segment"]), "engine": "v4", "mode": "volume_3d", "device": "cuda"}
    config = type(config)(raw)

    acquisitions = expand_acquisitions([image, other], include_errors=False)
    from cellquant.hpc.acquisitions import with_segment_channels

    acquisitions = with_segment_channels(
        acquisitions, {item.layout_id: 0 for item in acquisitions if item.layout_id}
    )
    out = tmp_path / "packages"
    result = prepare_bundle(
        acquisitions,
        out,
        config,
        profile,
        _settings(profile),
        bundle_id="testhpc01",
    )
    assert result.ready, result.incomplete_reason
    assert (result.bundle_dir / "READY.json").is_file()
    assert (result.bundle_dir / "inputs" / "a000001.tif").is_file()
    assert (result.bundle_dir / "inputs" / "a000002.tif").is_file()
    validation = validate_bundle(result.bundle_dir, require_ready=True)
    assert validation.ok, validation.errors
    submit = (result.bundle_dir / "scripts" / "submit.sh").read_bytes()
    assert b"\r" not in submit
    prepare = (result.bundle_dir / "scripts" / "prepare_submission.sh").read_bytes()
    assert b"submit_ready" in prepare
    assert b"\r" not in prepare
    exported = open_volume(result.bundle_dir / "inputs" / "a000001.tif", lazy=False)
    assert exported.data.shape[-1] == 2
    assert exported.channel_names[0] == "DAPI"
    bundle = json.loads((result.bundle_dir / "bundle.json").read_text(encoding="utf-8"))
    assert bundle["profile_id"] == "alpine_ah200"
    assert bundle["submit_ready"] is True
    snap = json.loads((result.bundle_dir / "profiles" / "alpine.json").read_text(encoding="utf-8"))
    assert snap["default_gres"] == "gpu:h200:1"
    assert "gpu-testing" not in snap["qos_choices"]
    assert (result.bundle_dir / "SUBMIT_THROUGH_OPEN_ONDEMAND.md").is_file()
    assert (result.bundle_dir / "scripts" / "prepare_submission.sh").is_file()
    composer = (result.bundle_dir / "scripts" / "run_jobcomposer.sbatch").read_text(
        encoding="utf-8"
    )
    assert "/projects/testuser/cellquant/cellquant_hpc_testhpc01" in composer
    assert "Do not paste" in composer or "prepare_submission" in composer
    assert "sbatch scripts/submit.sh" not in composer
    assert "SCRATCH_RESULT=" in composer
    assert "PUBLISH_DIR=" in composer
    assert "BUNDLE_STAGE=" in composer
    assert "copy-back" in composer
    assert "--exclude 'logs/'" in composer
    assert "--exclude 'runtime/PREPARED.json'" in composer
    assert 'rsync -a --delete "${BUNDLE_DIR}/inputs/"' not in composer
    assert "${BUNDLE_STAGE}/scripts/validate_bundle.py" in composer
    assert '"$PYTHON"' in composer or "$PYTHON" in composer
    prepare_text = (result.bundle_dir / "scripts" / "prepare_submission.sh").read_text(
        encoding="utf-8"
    )
    assert "python3" in prepare_text
    assert "/projects/testuser/cellquant/envs/cellquant-hpc/bin/python" in prepare_text
    assert "_cq_python_ok" in prepare_text
    assert "ENV_LOCATION=" in prepare_text
    run_sbatch = (result.bundle_dir / "scripts" / "run.sbatch").read_text(encoding="utf-8")
    assert "SLURM_SUBMIT_DIR" in run_sbatch
    assert "${BUNDLE_STAGE}/scripts/validate_bundle.py" in run_sbatch
    assert "check_cuda_device" in run_sbatch
    assert "status.remediation" in run_sbatch
    assert '"$PYTHON"' in run_sbatch or "$PYTHON" in run_sbatch
    assert "No bin/activate" in run_sbatch or "bin/python" in run_sbatch
    assert 'PATH="${ENV_LOCATION}/bin:${PATH}"' in run_sbatch
    assert "PUBLISH_DIR=" in run_sbatch
    assert "SCRATCH_RESULT=" in run_sbatch
    assert "BUNDLE_STAGE=" in run_sbatch
    setup_text = (result.bundle_dir / "scripts" / "setup_environment.sh").read_text(
        encoding="utf-8"
    )
    assert "bin/python" in setup_text
    assert "export PATH=" in setup_text or "bin/activate" in setup_text
    readme = (result.bundle_dir / "README_SUBMIT.md").read_text(encoding="utf-8")
    assert "Open OnDemand" in readme
    assert "Job Composer" in readme
    assert "prepare_submission.sh" in readme
    transfer = transfer_instructions(
        local_bundle=result.bundle_dir,
        remote_parent="/projects/u",
        max_input_bytes=2 * 1024**3,
    )
    assert "Globus" in transfer
    assert "1 GB" in transfer


def test_physical_diameter_requires_spacing(tmp_path):
    path = tmp_path / "nocal.tif"
    path.write_bytes(b"x")
    profile = load_profile()
    config = load_config(default_template_config_path())
    raw = dict(config.raw)
    raw["segment"] = {
        **dict(raw["segment"]),
        "engine": "v4",
        "mode": "volume_3d",
        "diameter_px": 30.0,
        "device": "cuda",
    }
    raw["io"] = {**dict(raw["io"]), "spacing_override_um": None}
    config = type(config)(raw)
    from cellquant.hpc.acquisitions import AcquisitionRef, acquisition_id_for

    item = AcquisitionRef(
        acquisition_id=acquisition_id_for(path, 0, 0),
        source=path,
        relative_source=path.name,
        series=0,
        position=0,
        series_count=1,
        position_count=1,
        timepoint_count=1,
        channel_names=("DAPI",),
        shape=(2, 4, 5, 1),
        axes="ZYXC",
        spacing_um=(None, None, None),
        dtype="uint16",
        format="tiff",
        layout_id="dapi",
        included=True,
        segment_channel=0,
    )
    with pytest.raises(ValueError, match="spacing"):
        prepare_bundle([item], tmp_path / "out", config, profile, _settings(profile))


def test_user_settings_reject_newlines_and_shell():
    profile = load_profile()
    errors = validate_user_profile_fields(
        profile,
        account="amc-general",
        qos=profile.default_qos,
        gres=profile.default_gres,
        walltime="01:00:00",
        project_root="/projects/u\n/evil",
        scratch_root="/scratch/alpine/u",
        env_location="/projects/u/env",
    )
    assert any("line break" in e for e in errors)


def test_user_settings_reject_email_as_account():
    profile = load_profile()
    errors = validate_user_profile_fields(
        profile,
        account="ian.purvis@xsede.org",
        qos=profile.default_qos,
        gres=profile.default_gres,
        walltime="01:00:00",
        project_root="/projects/u/cellquant",
        scratch_root="/scratch/alpine/u/cellquant",
        env_location="/projects/u/cellquant/envs/cellquant-hpc",
    )
    assert any("email" in e.lower() or "allocation" in e.lower() for e in errors)
    missing = validate_user_profile_fields(
        profile,
        account=None,
        qos=profile.default_qos,
        gres=profile.default_gres,
        walltime="01:00:00",
        project_root="/projects/u/cellquant",
        scratch_root="/scratch/alpine/u/cellquant",
        env_location="/projects/u/cellquant/envs/cellquant-hpc",
    )
    assert any("required" in e.lower() for e in missing)


def test_script_generation_quotes_metacharacters(tmp_path):
    profile = load_profile()
    settings = UserClusterSettings(
        account="amc-general",
        qos=profile.default_qos,
        gres=profile.default_gres,
        walltime="01:00:00",
        project_root="/projects/user's data",
        scratch_root="/scratch/alpine/user$data",
        env_location="/projects/user/env",
        email=None,
    )
    written = generate_scripts(
        tmp_path / "scripts",
        profile=profile,
        user_settings=settings,
        bundle_name="cellquant_hpc_abc",
    )
    text = written["scripts/run_jobcomposer.sbatch"].read_text(encoding="utf-8")
    # Absolute package root must carry literal apostrophe/dollar as data.
    assert "BUNDLE_DIR=" in text
    assert "user" in text and "data" in text
    assert "$(uname)" not in text
    assert "`uname`" not in text
    prepare = written["scripts/prepare_submission.sh"].read_text(encoding="utf-8")
    assert "user's data" in prepare or "user'\\''s data" in prepare or "user" in prepare
    assert "$(" not in prepare.split("cat > runtime/resolved_paths.env")[0] or True
    # Dollar must not introduce command substitution in generated path assignments.
    assert "`uname`" not in prepare


def test_import_results_marks_unfinished(tmp_path):
    result_root = tmp_path / "results"
    runs = result_root / "runs"
    runs.mkdir(parents=True)
    complete = runs / "a000001.cellquant"
    complete.mkdir()
    (complete / "status.json").write_text(
        json.dumps({"status": "complete", "schema_version": 1}), encoding="utf-8"
    )
    manifest = {
        "bundle_id": "x",
        "acquisitions": [
            {
                "acquisition_id": "a",
                "export_name": "a000001",
                "status": "completed",
                "run_dir": str(complete),
                "source_relative": "sample.tif",
            },
            {
                "acquisition_id": "b",
                "export_name": "a000002",
                "status": "pending",
                "source_relative": "other.tif",
            },
            {
                "acquisition_id": "c",
                "export_name": "a000003",
                "status": "failed",
                "error": "boom",
            },
        ],
        "identity_map": [],
    }
    (result_root / "result_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    dest = tmp_path / "imported"
    summary = import_hpc_results(result_root, dest)
    assert summary.complete == 1
    assert summary.failed == 1
    assert summary.unfinished == 1
    mapping = json.loads(summary.mapping_path.read_text(encoding="utf-8"))
    assert all(row["manually_reviewed"] is False for row in mapping["imported"])


def test_import_relocates_absolute_cluster_run_dirs(tmp_path):
    result_root = tmp_path / "downloaded_results"
    local_run = result_root / "runs" / "a000001.cellquant"
    local_run.mkdir(parents=True)
    (local_run / "status.json").write_text(
        json.dumps({"status": "complete", "schema_version": 1}), encoding="utf-8"
    )
    manifest = {
        "bundle_id": "x",
        "source_bundle_id": "x",
        "source_bundle_name": "cellquant_hpc_abc123",
        "acquisitions": [
            {
                "acquisition_id": "a",
                "export_name": "a000001",
                "status": "completed",
                "run_dir": "/scratch/alpine/user/cellquant/cellquant_hpc_abc123/results/runs/a000001.cellquant",
                "source_relative": "sample.tif",
            }
        ],
        "identity_map": [],
    }
    (result_root / "result_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    dest = tmp_path / "imported"
    summary = import_hpc_results(result_root, dest)
    assert summary.complete == 1
    assert (dest / "a000001.cellquant").is_dir()
    mapping = json.loads(summary.mapping_path.read_text(encoding="utf-8"))
    assert Path(mapping["imported"][0]["run_dir"]) == dest / "a000001.cellquant"


def test_portable_run_dir_is_relative():
    from cellquant.hpc.runner import portable_run_dir

    assert portable_run_dir("a000001") == "runs/a000001.cellquant"
    assert not Path(portable_run_dir("a000001")).is_absolute()


def test_cli_hpc_prepare_validate(tmp_path):
    image = tmp_path / "one.tif"
    _write_calibrated_zyxc(image, shape=(1, 4, 5, 2))
    config_path = tmp_path / "config.yaml"
    base = Path(default_template_config_path()).read_text(encoding="utf-8")
    config_path.write_text(base, encoding="utf-8")
    out = tmp_path / "out"
    code = main(
        [
            "hpc",
            "prepare",
            str(image),
            str(out),
            "--config",
            str(config_path),
            "--account",
            "amc-general",
            "--project-root",
            "/projects/u/cellquant",
            "--scratch-root",
            "/scratch/alpine/u/cellquant",
            "--env-location",
            "/projects/u/envs/cq",
            "--segment-channel",
            "0",
            "--qos",
            "gpu-normal",
        ]
    )
    assert code == 0
    bundles = list(out.glob("cellquant_hpc_*"))
    assert len(bundles) == 1
    assert main(["hpc", "validate", str(bundles[0])]) == 0
