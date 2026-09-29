"""AC08-style check with real Cellpose weights (CPU): original source vs prepared package, same process, same runtime."""
import json, os, sys, time, subprocess, tempfile
from pathlib import Path
import numpy as np, pandas as pd, tifffile
sys.path.insert(0, "/home/claude/cq2_m0")
from cellquant.controller import AnalysisController
from cellquant.image import read_stack
from cellquant.inputs import load_record_image
from cellquant.pipeline import process_image
from cellquant.hpc.runtime import inspect_runtime
from cellquant.hpc.profiles import load_profile
from cellquant.hpc.prepare import plan_preparation, prepare_package
from cellquant.hpc.runner import Runner
from cellquant.hpc.validate import open_bundle
from cellquant.storage import read_persisted_result
from tests.hpc_helpers import write_profile

engine = sys.argv[1]
cases = sys.argv[2].split(",")
out = Path(sys.argv[3]); out.mkdir(parents=True, exist_ok=True)
model = "cpsam_v2" if engine == "cellpose4" else "nuclei"
# The real image is not part of the repository: point CELLQUANT_PARITY_ND2 at a 3-channel ND2 z-stack.
ND2 = Path(os.environ["CELLQUANT_PARITY_ND2"])
root = Path(tempfile.mkdtemp(prefix=f"parity_{engine}_"))
lock = root / "lock.txt"
lock.write_text(subprocess.run([sys.executable, "-m", "pip", "freeze"], capture_output=True, text=True).stdout)
contract = inspect_runtime(engine=engine, model=model, runtime_id=f"cloud-{engine}", lock_file=lock,
                           supported_modes=["max_projection", "single_plane", "stitch_slices", "full_3d"], models_dir="/root/.cellpose/models",
                           validation_date="2026-09-29")
runtime_path = root / "profiles" / f"cloud-{engine}.json"; runtime_path.parent.mkdir()
runtime_bytes = (contract.model_dump_json(indent=2) + "\n").encode(); runtime_path.write_bytes(runtime_bytes)
profile = load_profile(write_profile(root / "profiles", runtime_path, runtime_bytes, runtime_id=f"cloud-{engine}"))
assert not profile.errors, profile.errors
full = read_stack(ND2).zcyx  # Z, C, Y, X
evidence = {"engine": engine, "model": model, "cellpose_version": contract.cellpose_version, "runtime_fingerprint": None, "cases": []}

def recipe(mode):
    return {
        "recipe_name": "parity", "z_stack": mode,
        "object_set": {"name": "Nuclei", "segmentation_channel": 2, "algorithm": "cellpose",
                       "parameters": {"engine": engine, "model": model, "gpu": True, "diameter_px": None, "flow_threshold": 0.4, "cellprob_threshold": 0.0}},
        "measurements": [
            {"id": "green", "channel": 0, "region": {"type": "object"}, "statistic": "mean"},
            {"id": "red", "channel": 1, "region": {"type": "object"}, "statistic": "mean"},
            {"id": "far", "channel": 2, "region": {"type": "object"}, "statistic": "mean"}],
        "classifications": [
            {"id": "green_pos", "name": "Green", "measurement": "green", "threshold": 500},
            {"id": "red_pos", "name": "Red", "measurement": "red", "threshold": 350}],
        "reports": [{"numerator": "Green AND Red", "denominator": "Red"}],
    }

for case in cases:
    mode, size = case.split(":")
    folder = root / case.replace(":", "_") / "images"; folder.mkdir(parents=True)
    if size == "nd2":
        source = folder / ND2.name
        source.write_bytes(ND2.read_bytes())
    else:
        n = int(size); y0 = 400; x0 = 380
        crop = np.ascontiguousarray(full[:, :, y0:y0 + n, x0:x0 + n])
        source = folder / f"crop{n}.tif"
        tifffile.imwrite(source, crop, imagej=True, metadata={"axes": "ZCYX", "spacing": 1.5, "unit": "um", "Labels": ["Green", "Red", "Far Red"]},
                         resolution=(1 / 0.5754449716687396, 1 / 0.5754449716687396))
    controller = AnalysisController.create(folder.parent / "experiment", f"parity {case}")
    controller.add_image_paths([folder])
    controller.set_recipe(recipe(mode)); controller.save()
    plan = plan_preparation(controller.experiment, controller.recipe, profile)
    assert plan.ok, [i.message for i in plan.all_errors()]
    package = prepare_package(plan, folder.parent / "packages").package_dir
    record = controller.experiment.images[0]
    started = time.time()
    local = process_image(load_record_image(record, z_mode=controller.recipe.z_stack, z_index=controller.recipe.z_index, experiment_dir=controller.directory),
                          controller.recipe, sample_name=record.sample_name, image_id="x", filename=record.filename, user_metadata=record.user_metadata)
    local_seconds = time.time() - started
    run_dir = folder.parent / "results" / "run_parity"
    started = time.time()
    code = Runner(package, folder.parent / "scratch", run_dir, require_gpu=False, log=lambda t: None).run()
    remote_seconds = time.time() - started
    assert code == 0, (run_dir / "attempts").iterdir()
    task = next((run_dir / "tasks" / "a000001").iterdir())
    remote = read_persisted_result(task, "a000001")
    result = {"case": case, "mode": mode, "source": "ND2 (whole file)" if size == "nd2" else f"TIFF crop {size}x{size} of the same ND2",
              "shape_czyx": open_bundle(package).manifest.acquisitions[0].shape_czyx,
              "pixels_identical": bool(np.array_equal(read_stack(package / "inputs" / "a000001.ome.tif").czyx, read_stack(source, position=0).czyx)),
              "labels_identical": bool(np.array_equal(local.automated_labels, remote.automated_labels)),
              "objects_local": int(local.qc.n_objects), "objects_remote": int(remote.qc.n_objects),
              "seconds_local": round(local_seconds, 1), "seconds_worker": round(remote_seconds, 1)}
    columns = [c for c in local.objects.columns if c not in ("image_id", "run_id", "experiment_id")]
    try:
        pd.testing.assert_frame_equal(local.objects[columns].reset_index(drop=True), remote.objects[columns].reset_index(drop=True), check_exact=False, rtol=1e-6, atol=1e-8)
        pd.testing.assert_frame_equal(local.reports.reset_index(drop=True), remote.reports.reset_index(drop=True), check_exact=False, rtol=1e-6, atol=1e-8)
        result["measurements_within_tolerance"] = True
    except AssertionError as exc:
        result["measurements_within_tolerance"] = False
        result["difference"] = str(exc)[:500]
    if not result["labels_identical"]:
        result["label_pixels_differing"] = int(np.count_nonzero(local.automated_labels != remote.automated_labels))
    result["report"] = remote.reports.to_dict("records")
    evidence["runtime_fingerprint"] = remote.provenance["hpc"]["runtime_fingerprint"]
    evidence["cases"].append(result)
    print(json.dumps(result), flush=True)
(out / f"parity_{engine}.json").write_text(json.dumps(evidence, indent=2))
