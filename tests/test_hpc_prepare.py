"""HPC preparation: lossless export, validation, READY binding, admission rules and profiles (AC01-AC05, AC15)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

from cellquant.controller import AnalysisController
from cellquant.hpc.common import sha256_file
from cellquant.hpc.models import SchemaVersionError, load_model, BundleManifest
from cellquant.hpc.prepare import PreparationError, plan_preparation, prepare_package
from cellquant.hpc.profiles import load_profile
from cellquant.hpc.validate import validate_bundle
from cellquant.image import read_stack
from cellquant.progress import AnalysisCancelled, reporting
from tests.hpc_helpers import (
    cellpose_recipe,
    classical_recipe,
    install_fake_nd2,
    make_experiment,
    prepared_package,
    reseal,
    write_profile,
    write_runtime,
    write_stack,
)


def _profile(root: Path, **runtime_options):
    runtime_path, runtime_bytes = write_runtime(root / "profiles", **runtime_options)
    return load_profile(write_profile(root / "profiles", runtime_path, runtime_bytes))


def _codes(plan) -> set[str]:
    return {issue.code for issue in plan.all_errors()}


# --- AC01: no GPU, no Cellpose, no segmentation --------------------------------------------------------


def test_preparation_needs_no_cellpose_and_never_segments(tmp_path: Path, monkeypatch):
    import cellquant.pipeline as pipeline
    import cellquant.segmentation as segmentation

    def forbidden(*_args, **_kwargs):
        raise AssertionError("preparation must not segment")

    monkeypatch.setattr(pipeline, "segment_channel", forbidden)
    monkeypatch.setattr(segmentation, "segment_objects", forbidden)
    monkeypatch.setattr(segmentation, "engine_signature", forbidden)
    monkeypatch.setitem(sys.modules, "cellpose", None)  # importing cellpose now fails
    monkeypatch.setitem(sys.modules, "torch", None)
    controller, package, _runtime = prepared_package(tmp_path)
    report, bundle = validate_bundle(package, deep=True)
    assert report.ok and bundle is not None
    assert len(bundle.manifest.acquisitions) == 2
    assert (package / "READY").is_file()
    assert json.loads((package / "cluster.json").read_text())["runtime_contract_path"] == "runtime.json"
    sidecar = package.parent / f"{package.name}.local.json"
    assert sidecar.is_file() and str(controller.directory.parent) not in (package / "bundle.json").read_text()


# --- AC02: exact round trips --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "shape, dtype",
    [((4, 3, 40, 48), np.uint16), ((1, 2, 32, 32), np.uint16), ((1, 1, 24, 30), np.uint8), ((3, 2, 20, 20), np.float32)],
)
def test_tiff_pixels_dtype_and_singletons_round_trip(tmp_path: Path, shape, dtype):
    z, c, y, x = shape
    rng = np.random.default_rng(1)
    data = (rng.random(shape) * (200 if dtype == np.uint8 else 4000)).astype(dtype)
    folder = tmp_path / "images"
    folder.mkdir()
    source = folder / "image.tif"
    tifffile.imwrite(source, data, imagej=True, metadata={"axes": "ZCYX", "spacing": 2.0, "unit": "um"}, resolution=(2.0, 2.0))
    before = sha256_file(source)
    mtime = source.stat().st_mtime_ns
    controller = AnalysisController.create(tmp_path / "experiment", "Round trip")
    controller.add_image_paths([folder])
    recipe = cellpose_recipe("single_plane" if z > 1 else "max_projection")
    recipe["measurements"] = [m for m in recipe["measurements"] if m["channel"] < c]
    ids = {m["id"] for m in recipe["measurements"]}
    recipe["classifications"] = [item for item in recipe["classifications"] if item["measurement"] in ids]
    names = {item["name"] for item in recipe["classifications"]}
    recipe["reports"] = [item for item in recipe["reports"] if all(part.strip() in names | {"all_objects"} for part in item["numerator"].split(" AND ") + [item["denominator"]])]
    controller.set_recipe(recipe)
    controller.set_metadata(controller.experiment.images[0].image_id, "Group", "control")
    plan = plan_preparation(controller.experiment, controller.recipe, _profile(tmp_path))
    assert plan.ok, [issue.message for issue in plan.all_errors()]
    package = prepare_package(plan, tmp_path / "out").package_dir
    manifest = load_model(BundleManifest, package / "bundle.json")
    acquisition = manifest.acquisitions[0]
    copy = read_stack(package / acquisition.input_path).czyx
    assert copy.dtype == data.dtype and copy.shape == np.moveaxis(data, 0, 1).shape
    assert np.array_equal(copy, np.moveaxis(data, 0, 1))
    assert acquisition.shape_czyx == [c, z, y, x]
    assert acquisition.effective_spacing_xyz_um[:2] == pytest.approx([0.5, 0.5])
    if z > 1:
        assert acquisition.effective_spacing_xyz_um[2] == pytest.approx(2.0)
    assert acquisition.user_metadata["Group"] == "control"
    if z > 1:
        assert (acquisition.effective_z_mode, acquisition.effective_z_index) == ("single_plane", z // 2)
    else:
        assert acquisition.effective_z_mode == "none"
    assert sha256_file(source) == before and source.stat().st_mtime_ns == mtime


def test_nd2_positions_round_trip_exactly(tmp_path: Path, monkeypatch):
    install_fake_nd2(monkeypatch, sizes={"P": 3, "Z": 4, "C": 3, "Y": 20, "X": 24})
    folder = tmp_path / "images"
    folder.mkdir()
    source = folder / "plate.nd2"
    source.write_bytes(b"fake nd2 contents")
    controller = AnalysisController.create(tmp_path / "experiment", "Positions")
    controller.add_image_paths([folder])
    controller.set_recipe(cellpose_recipe("stitch_slices"))
    record = controller.experiment.images[2]
    controller.set_pixel_size(record.image_id, 0.3, 0.3, 1.0)  # a user override
    plan = plan_preparation(controller.experiment, controller.recipe, _profile(tmp_path))
    assert plan.ok, [issue.message for issue in plan.all_errors()]
    package = prepare_package(plan, tmp_path / "out").package_dir
    manifest = load_model(BundleManifest, package / "bundle.json")
    assert [item.source_position for item in manifest.acquisitions] == [0, 1, 2]
    for item in manifest.acquisitions:
        original = read_stack(source, position=item.source_position).czyx
        assert np.array_equal(read_stack(package / item.input_path).czyx, original)
    third = manifest.acquisitions[2]
    assert third.effective_spacing_xyz_um == [0.3, 0.3, 1.0]
    assert third.file_spacing_xyz_um == pytest.approx([0.575, 0.575, 1.5])
    assert third.calibration_source == "file_and_user"
    assert manifest.acquisitions[0].calibration_source == "file"
    assert len({item.acquisition_id for item in manifest.acquisitions}) == 3


# --- AC03: validation catches damage before inference ----------------------------------------------


def _damaged(tmp_path: Path, damage) -> list[str]:
    _controller, package, _runtime = prepared_package(tmp_path)
    damage(package)
    report, bundle = validate_bundle(package)
    assert not report.ok and bundle is None
    return [issue.code for issue in report.errors]


def test_truncated_input_fails(tmp_path: Path):
    def cut(package: Path):
        path = package / "inputs" / "a000001.ome.tif"
        path.write_bytes(path.read_bytes()[:-100])

    assert "E_CHECKSUM" in _damaged(tmp_path, cut)


@pytest.mark.parametrize("name", ["recipe.yaml", "scripts/job.sbatch", "runtime.json", "cluster.json"])
def test_changed_records_and_scripts_fail(tmp_path: Path, name: str):
    def edit(package: Path):
        with (package / name).open("a", encoding="utf-8") as handle:
            handle.write("\n")

    assert "E_CHECKSUM" in _damaged(tmp_path, edit)


def test_changed_recipe_is_caught_even_when_checksums_are_redone(tmp_path: Path):
    def edit(package: Path):
        text = (package / "recipe.yaml").read_text().replace("threshold: 400", "threshold: 401", 1)
        (package / "recipe.yaml").write_text(text)
        reseal(package)

    assert "E_RECIPE" in _damaged(tmp_path, edit)


def test_ready_must_bind_to_the_checksums(tmp_path: Path):
    def edit(package: Path):
        (package / "READY").write_text(json.dumps({"schema_version": 1, "bundle_id": "0" * 32, "checksums_sha256": "0" * 64}))

    assert "E_READY" in _damaged(tmp_path, edit)

    def missing(package: Path):
        (package / "READY").unlink()

    assert "E_READY" in _damaged(tmp_path / "second", missing)


def test_unsupported_schema_and_old_packages_are_refused(tmp_path: Path):
    def newer(package: Path):
        data = json.loads((package / "bundle.json").read_text())
        data["schema_version"] = 2
        (package / "bundle.json").write_text(json.dumps(data))
        reseal(package)

    assert "E_UNSUPPORTED_SCHEMA" in _damaged(tmp_path, newer)
    with pytest.raises(SchemaVersionError, match="older CellQuant pipeline"):
        load_model(BundleManifest, data={"kind": "cellquant_hpc", "bundle_id": "x"})


def test_paths_must_stay_inside_the_package(tmp_path: Path):
    def escape(package: Path):
        data = json.loads((package / "checksums.json").read_text())
        data["files"].append({"path": "../outside.txt", "bytes": 1, "sha256": "0" * 64})
        (package / "checksums.json").write_text(json.dumps(data))

    assert "E_SCHEMA" in _damaged(tmp_path, escape)

    def link(package: Path):
        target = package / "inputs" / "a000002.ome.tif"
        target.unlink()
        os.symlink(package.parent / "elsewhere.tif", target)

    assert set(_damaged(tmp_path / "second", link)) & {"E_PATH", "E_MISSING_FILE"}


def test_duplicate_acquisitions_are_refused(tmp_path: Path):
    def duplicate(package: Path):
        data = json.loads((package / "bundle.json").read_text())
        data["acquisitions"][1]["acquisition_id"] = data["acquisitions"][0]["acquisition_id"]
        (package / "bundle.json").write_text(json.dumps(data))
        reseal(package)

    assert "E_SCHEMA" in _damaged(tmp_path, duplicate)


def test_task_keys_follow_the_contents(tmp_path: Path):
    def spacing(package: Path):
        data = json.loads((package / "bundle.json").read_text())
        data["acquisitions"][0]["effective_spacing_xyz_um"] = [0.6, 0.6, 1.5]
        (package / "bundle.json").write_text(json.dumps(data))
        reseal(package)

    assert "E_TASK_KEY" in _damaged(tmp_path, spacing)


def test_deep_validation_reads_the_pixels(tmp_path: Path):
    _controller, package, _runtime = prepared_package(tmp_path)
    data = json.loads((package / "bundle.json").read_text())
    path = package / "inputs" / "a000001.ome.tif"
    image = tifffile.imread(path)
    image[0, 0, 0, 0] += 1
    tifffile.imwrite(path, image, ome=True, metadata={"axes": "ZCYX", "Channel": {"Name": data["channel_layout"]}, "PhysicalSizeX": 0.5, "PhysicalSizeY": 0.5, "PhysicalSizeZ": 1.5})
    data["acquisitions"][0]["input_file_sha256"] = sha256_file(path)
    (package / "bundle.json").write_text(json.dumps(data))
    reseal(package)
    assert validate_bundle(package)[0].ok  # file hashes are consistent again, so only the pixels can tell
    report, _bundle = validate_bundle(package, deep=True)
    assert not report.ok and "E_PIXELS" in {issue.code for issue in report.errors}


# --- AC04: frozen settings, no replayed edits ------------------------------------------------------


def test_settings_are_frozen_at_preparation(tmp_path: Path):
    controller, package, _runtime = prepared_package(tmp_path)
    checksums = (package / "checksums.json").read_bytes()
    recipe = (package / "recipe.yaml").read_bytes()
    image_id = controller.experiment.images[0].image_id
    data = controller.recipe.model_dump(mode="json")
    data["classifications"][0]["threshold"] = 999
    controller.set_recipe(data)
    controller.save()
    assert (package / "checksums.json").read_bytes() == checksums and (package / "recipe.yaml").read_bytes() == recipe
    assert validate_bundle(package)[0].ok
    manifest = load_model(BundleManifest, package / "bundle.json")
    assert any("not applied" in note for note in manifest.notes)
    assert image_id == manifest.acquisitions[0].source_image_id


# --- AC05: admission rules --------------------------------------------------------------------------


def test_3d_needs_calibration(tmp_path: Path):
    controller = make_experiment(tmp_path, 1, recipe=cellpose_recipe("stitch_slices"))
    record = controller.experiment.images[0]
    record.pixel_size_z = None
    plan = plan_preparation(controller.experiment, controller.recipe, _profile(tmp_path))
    assert "E_CALIBRATION" in _codes(plan)


def test_unequal_pixel_sizes_are_refused_not_averaged(tmp_path: Path):
    controller = make_experiment(tmp_path, 1)
    controller.set_pixel_size(controller.experiment.images[0].image_id, 0.5, 0.6)
    assert "E_CALIBRATION" in _codes(plan_preparation(controller.experiment, controller.recipe, _profile(tmp_path)))


def test_uncalibrated_2d_is_a_warning(tmp_path: Path):
    controller = make_experiment(tmp_path, 1)
    controller.set_pixel_size(controller.experiment.images[0].image_id, None, None)
    plan = plan_preparation(controller.experiment, controller.recipe, _profile(tmp_path))
    assert plan.ok and "W_UNCALIBRATED" in {issue.code for issue in plan.all_warnings()}


def test_channels_must_match(tmp_path: Path):
    controller = make_experiment(tmp_path, 1)
    write_stack(tmp_path / "images" / "Group A" / "two.tif", channels=2, names=("Green", "Red"))
    controller.add_image_paths([tmp_path / "images"])
    assert "E_CHANNELS" in _codes(plan_preparation(controller.experiment, controller.recipe, _profile(tmp_path)))


def test_renamed_channels_need_confirmation(tmp_path: Path):
    controller = make_experiment(tmp_path, 1)
    write_stack(tmp_path / "images" / "Group A" / "renamed.tif", names=("GFP", "mCherry", "Cy5"))
    controller.add_image_paths([tmp_path / "images"])
    profile = _profile(tmp_path)
    assert "E_CHANNEL_NAMES" in _codes(plan_preparation(controller.experiment, controller.recipe, profile))
    plan = plan_preparation(controller.experiment, controller.recipe, profile, confirm_channel_layout=True)
    assert plan.ok
    package = prepare_package(plan, tmp_path / "out").package_dir
    manifest = load_model(BundleManifest, package / "bundle.json")
    assert manifest.channel_layout_confirmed and [item.channel_names for item in manifest.acquisitions][-1] == ["GFP", "mCherry", "Cy5"]


@pytest.mark.parametrize(
    "recipe, runtime, code",
    [
        (cellpose_recipe("stitch_slices"), {"modes": ("max_projection",)}, "E_MODE"),
        (cellpose_recipe(engine="cellpose3", model="nuclei"), {}, "E_ENGINE"),
        (cellpose_recipe(model="cpsam"), {}, "E_MODEL"),
        (cellpose_recipe(gpu=False), {}, "E_GPU"),
        (classical_recipe(), {}, "E_ENGINE"),
        (cellpose_recipe(), {"validated": False}, "E_RUNTIME_UNVERIFIED"),
    ],
)
def test_nothing_is_substituted(tmp_path: Path, recipe, runtime, code):
    controller = make_experiment(tmp_path, 1, recipe=recipe)
    plan = plan_preparation(controller.experiment, controller.recipe, _profile(tmp_path, **runtime))
    assert code in _codes(plan)
    with pytest.raises(PreparationError):
        prepare_package(plan, tmp_path / "out")
    assert controller.recipe.model_dump(mode="json")["object_set"] == recipe["object_set"]


def test_a_different_cellquant_build_is_refused(tmp_path: Path, monkeypatch):
    controller = make_experiment(tmp_path, 1)
    profile = _profile(tmp_path)
    import cellquant.hpc.prepare as prepare

    monkeypatch.setattr(prepare, "application_build_sha256", lambda: "f" * 64)
    assert "E_RUNTIME_BUILD" in _codes(plan_preparation(controller.experiment, controller.recipe, profile))


def test_output_inside_the_image_folder_or_too_long_is_refused(tmp_path: Path):
    controller = make_experiment(tmp_path, 1)
    plan = plan_preparation(controller.experiment, controller.recipe, _profile(tmp_path))
    with pytest.raises(PreparationError) as inside:
        prepare_package(plan, tmp_path / "images" / "packages")
    assert inside.value.issues[0].code == "E_OUTPUT"
    deep = tmp_path / ("x" * 120) / ("y" * 120)
    with pytest.raises(PreparationError) as long:
        prepare_package(plan, deep)
    assert long.value.issues[0].code == "E_PATH_TOO_LONG"


def test_cloud_only_files_are_reported(tmp_path: Path, monkeypatch):
    from cellquant.hpc import prepare

    controller = make_experiment(tmp_path, 1)
    real_stat = os.stat

    class Placeholder:
        def __init__(self, result):
            self._result = result
            self.st_file_attributes = 0x400000

        def __getattr__(self, name):
            return getattr(self._result, name)

    def stat(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        return Placeholder(result) if str(path).endswith(".tif") else result

    monkeypatch.setattr(prepare.os, "stat", stat)
    plan = plan_preparation(controller.experiment, controller.recipe, _profile(tmp_path))
    assert any("online only" in issue.message for issue in plan.all_errors())


# --- cancellation and changes during export ----------------------------------------------------------


def test_cancel_leaves_an_incomplete_folder_and_a_retry_starts_fresh(tmp_path: Path):
    controller = make_experiment(tmp_path, 2)
    plan = plan_preparation(controller.experiment, controller.recipe, _profile(tmp_path))
    seen = []

    def on_update(text, _fraction):
        seen.append(text)

    with reporting(on_update, lambda: any("Image 2 of 2" in text for text in seen)):
        with pytest.raises(AnalysisCancelled):
            prepare_package(plan, tmp_path / "out")
    folders = sorted(path.name for path in (tmp_path / "out").iterdir())
    assert len(folders) == 1 and folders[0].endswith(".incomplete")
    assert not (tmp_path / "out" / folders[0] / "READY").exists()
    again = prepare_package(plan, tmp_path / "out")
    assert validate_bundle(again.package_dir)[0].ok


def test_a_source_that_changes_during_export_stops_preparation(tmp_path: Path, monkeypatch):
    from cellquant.hpc import prepare

    controller = make_experiment(tmp_path, 1)
    plan = plan_preparation(controller.experiment, controller.recipe, _profile(tmp_path))
    real = prepare.read_stack

    def read_and_touch(path, **kwargs):
        stack = real(path, **kwargs)
        if str(path).endswith("stack0.tif"):
            with open(path, "ab") as handle:
                handle.write(b"\0")
        return stack

    monkeypatch.setattr(prepare, "read_stack", read_and_touch)
    with pytest.raises(PreparationError) as error:
        prepare_package(plan, tmp_path / "out")
    assert error.value.issues[0].code == "E_SOURCE_CHANGED"
    assert not any(path.name.startswith("cq_hpc_") and not path.name.endswith((".incomplete", ".json")) for path in (tmp_path / "out").iterdir())


# --- profiles and runtime contracts ------------------------------------------------------------------


def test_relative_runtime_paths_resolve_beside_the_profile(tmp_path: Path, monkeypatch):
    runtime_path, runtime_bytes = write_runtime(tmp_path / "profiles")
    profile_path = write_profile(tmp_path / "profiles", runtime_path, runtime_bytes)
    monkeypatch.chdir(tmp_path.parent)
    resolved = load_profile(profile_path)
    assert resolved.runtime is not None and not resolved.errors
    other = write_profile(tmp_path / "profiles", runtime_path, runtime_bytes, runtime_sha256="a" * 64)
    assert "E_RUNTIME_MISMATCH" in {issue.code for issue in load_profile(other).errors}


def test_placeholders_keep_a_profile_draft(tmp_path: Path):
    runtime_path, runtime_bytes = write_runtime(tmp_path / "profiles")
    path = write_profile(tmp_path / "profiles", runtime_path, runtime_bytes, account="YOUR_ACCOUNT", scratch_root="/scratch/alpine/USER/cq")
    resolved = load_profile(path)
    assert not resolved.submission_ready
    assert "account" in resolved.errors[0].message and "scratch_root" in resolved.errors[0].message


def test_profile_fields_reject_shell_text(tmp_path: Path):
    runtime_path, runtime_bytes = write_runtime(tmp_path / "profiles")
    for field, value in (("account", "lab; rm -rf ~"), ("gpu_resource", "gpu:h200:2"), ("python_path", "relative/python"), ("modules", ["cuda && x"])):
        with pytest.raises(ValueError):
            write_profile(tmp_path / "profiles", runtime_path, runtime_bytes, **{field: value})


def test_schemas_are_published_and_fixtures_validate(tmp_path: Path):
    from cellquant.hpc.models import MODELS, write_schemas

    written = write_schemas(tmp_path / "schemas")
    assert {path.name for path in written} == {f"{name}.schema.json" for name in MODELS}
    fixtures = Path(__file__).parent / "fixtures" / "hpc"
    for path in sorted(fixtures.glob("valid_*.json")):
        name = path.stem.removeprefix("valid_")
        load_model(MODELS[name], path)
    for path in sorted(fixtures.glob("invalid_*.json")):
        name = path.stem.removeprefix("invalid_").rsplit("__", 1)[0]
        with pytest.raises(ValueError):
            load_model(MODELS[name], path)


def test_published_schemas_are_current():
    from cellquant.hpc.models import MODELS

    folder = Path(__file__).resolve().parents[1] / "cellquant" / "hpc" / "schemas"
    for name, model in MODELS.items():
        stored = json.loads((folder / f"{name}.schema.json").read_text(encoding="utf-8"))
        stored.pop("$id")
        assert stored == json.loads(json.dumps(model.model_json_schema(), sort_keys=True)), f"regenerate {name}: python -m cellquant.hpc schemas --output cellquant/hpc/schemas"


def test_the_example_profile_is_a_draft_until_filled_in():
    from cellquant.hpc.profiles import examples_dir

    resolved = load_profile(examples_dir() / "alpine_h200_example.json")
    codes = {issue.code for issue in resolved.errors}
    assert not resolved.submission_ready and {"E_RUNTIME_MISSING", "E_PROFILE_PLACEHOLDER"} <= codes


def test_the_build_digest_ignores_line_endings_and_caches(tmp_path: Path):
    import shutil

    from cellquant.hpc.runtime import application_build_sha256, package_root

    copy = tmp_path / "cellquant"
    shutil.copytree(package_root(), copy, ignore=shutil.ignore_patterns("__pycache__"))
    original = application_build_sha256(copy)
    for path in copy.rglob("*.py"):
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    (copy / "__pycache__").mkdir(exist_ok=True)
    (copy / "__pycache__" / "x.cpython-311.pyc").write_bytes(b"cache")
    (copy / "Thumbs.db").write_bytes(b"windows")
    assert application_build_sha256(copy) == original == application_build_sha256()
    (copy / "pipeline.py").write_text("# changed\n")
    assert application_build_sha256(copy) != original


def test_login_node_preflight_does_no_gpu_work_and_gpu_preflight_needs_a_gpu(tmp_path: Path, monkeypatch):
    from cellquant.hpc.preflight import run_preflight
    from tests.hpc_helpers import install_fake_cellpose, matching_observer

    install_fake_cellpose(tmp_path, monkeypatch)
    _controller, package, runtime_bytes = prepared_package(tmp_path)
    login = run_preflight(package, require_gpu=False, observe=matching_observer(runtime_bytes), forbid_interface_modules=False)
    assert login.ok and login.inference["run"] is False and not login.gpu
    no_gpu = run_preflight(package, require_gpu=True, observe=matching_observer(runtime_bytes), gpu=lambda: {"available": False, "error": "no CUDA"}, forbid_interface_modules=False)
    assert not no_gpu.ok and no_gpu.errors[0].code == "E_GPU"
    gpu = run_preflight(package, require_gpu=True, observe=matching_observer(runtime_bytes), gpu=lambda: {"available": True, "name": "test"}, forbid_interface_modules=False)
    assert gpu.ok and gpu.inference["device"] == "cuda" and gpu.inference["objects"] > 0
    wrong = run_preflight(package, require_gpu=False, observe=lambda *a, **k: {**matching_observer(runtime_bytes)(*a, **k), "model_files": {"cpsam_v2": "9" * 64}}, forbid_interface_modules=False)
    assert not wrong.ok and wrong.errors[0].code == "E_RUNTIME_MODEL_HASH"
