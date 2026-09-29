"""Shared test data for the HPC preparation tests: small stacks, recipes and a runtime contract."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import tifffile


def write_stack(path: Path, *, z: int = 5, channels: int = 3, size: int = 64, seed: int = 0, dtype=np.uint16,
                spacing=(0.5, 0.5, 1.5), names=("Green", "Red", "Far Red")) -> np.ndarray:
    """A ZCYX ImageJ stack with a few bright nuclei that differ between channels. Returns CZYX."""

    rng = np.random.default_rng(seed)
    data = rng.integers(90, 110, size=(z, channels, size, size)).astype(dtype)
    centers = [(12, 14), (40, 18), (22, 44), (48, 48), (30, 30)]
    yy, xx = np.mgrid[0:size, 0:size]
    radii = [25, 25, 25, 25, 25, 5]
    centers.append((56, 8))  # a small nucleus, too small for the eroded measurement
    for index, (cy, cx) in enumerate(centers):
        disk = (yy - cy) ** 2 + (xx - cx) ** 2 <= radii[index]
        for plane in range(max(0, z // 2 - 1), min(z, z // 2 + 2)):
            data[plane, 0][disk] = 1500 + 100 * index
            if channels > 1:
                data[plane, 1][disk] = 900 if index % 2 == 0 else 150
            if channels > 2:
                data[plane, 2][disk] = 800 if index < 3 else 120
    metadata = {"axes": "ZCYX", "unit": "um"}
    if spacing and spacing[2]:
        metadata["spacing"] = spacing[2]
    if names:
        metadata["Labels"] = list(names)[:channels]
    kwargs = {}
    if spacing:
        kwargs["resolution"] = (1.0 / spacing[0], 1.0 / spacing[1])
    tifffile.imwrite(path, data, imagej=True, metadata=metadata, **kwargs)
    return np.moveaxis(data, 0, 1)


def classical_recipe(z_stack: str = "max_projection", **extra) -> dict:
    recipe = {
        "recipe_name": "hpc test",
        "z_stack": z_stack,
        "object_set": {
            "name": "Nuclei",
            "segmentation_channel": 0,
            "algorithm": "classical",
            "parameters": {"threshold_method": "manual", "threshold": 600, "use_watershed": False},
        },
        "measurements": [
            {"id": "red_mean", "channel": 1, "region": {"type": "object"}, "statistic": "mean"},
            {"id": "far_mean", "channel": 2, "region": {"type": "object"}, "statistic": "mean"},
            # Too deep for small objects: some objects cannot be measured.
            {"id": "far_core", "channel": 2, "region": {"type": "eroded_object", "distance_px": 4}, "statistic": "mean"},
        ],
        "classifications": [
            {"id": "red_pos", "name": "Red", "measurement": "red_mean", "threshold": 400},
            {"id": "far_pos", "name": "Far", "measurement": "far_mean", "threshold": 400},
            {"id": "core_pos", "name": "Core", "measurement": "far_core", "threshold": 400},
        ],
        "reports": [
            {"numerator": "Red", "denominator": "all_objects"},
            {"numerator": "Red AND Far", "denominator": "Red"},
        ],
    }
    recipe.update(extra)
    return recipe


def cellpose_recipe(z_stack: str = "max_projection", *, engine: str = "cellpose4", model: str = "cpsam_v2", gpu: bool = True, **extra) -> dict:
    recipe = classical_recipe(z_stack, **extra)
    recipe["object_set"] = {
        "name": "Nuclei",
        "segmentation_channel": 0,
        "algorithm": "cellpose",
        "parameters": {"engine": engine, "model": model, "gpu": gpu, "diameter_px": None, "flow_threshold": 0.4, "cellprob_threshold": 0.0},
    }
    return recipe


def write_runtime(folder: Path, *, engine: str = "cellpose4", model: str = "cpsam_v2", modes=("max_projection", "single_plane", "stitch_slices", "full_3d"),
                  validated: bool = True, runtime_id: str = "alpine-cp4-test") -> tuple[Path, bytes]:
    """A runtime contract for this very copy of CellQuant (so the build digests match)."""

    from cellquant.hpc.models import RuntimeContract
    from cellquant.hpc.runtime import application_build_sha256

    contract = RuntimeContract(
        runtime_id=runtime_id,
        python_version="3.11.9",
        application_build_sha256=application_build_sha256(),
        dependency_lock_sha256="0" * 64,
        packages={"cellpose": "4.2.1.1" if engine == "cellpose4" else "3.1.1.3", "numpy": "2.4.6"},
        engine=engine,
        cellpose_version="4.2.1.1" if engine == "cellpose4" else "3.1.1.3",
        model=model,
        model_files={model: "1" * 64} if engine == "cellpose4" else {"nucleitorch_0": "1" * 64, "size_nucleitorch_0.npy": "2" * 64},
        model_directory="/projects/lab/cellquant/models",
        supported_modes=list(modes),
        runtime_validation_date="2026-09-29" if validated else None,
    )
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{runtime_id}.json"
    data = (contract.model_dump_json(indent=2) + "\n").encode("utf-8")
    path.write_bytes(data)
    return path, data


def write_profile(folder: Path, runtime_path: Path, runtime_bytes: bytes, **changes) -> Path:
    from cellquant.hpc.common import sha256_bytes
    from cellquant.hpc.models import ClusterProfile

    fields = dict(
        profile_id="alpine_h200_test",
        display_name="H200 test",
        host="login.rc.colorado.edu",
        account="amc-general",
        partition="ah200",
        qos="gpu-normal",
        gpu_resource="gpu:h200_3g.71gb:1",
        cpus=8,
        memory_mib=65536,
        walltime_seconds=4 * 3600,
        remote_durable_root="/projects/tester/cellquant",
        scratch_root="/scratch/alpine/tester/cellquant",
        python_path="/projects/tester/envs/cq-cp4/bin/python",
        runtime_id=runtime_path.stem,
        runtime_contract_path=runtime_path.name,
        runtime_sha256=sha256_bytes(runtime_bytes),
        validation_status="experimental",
        profile_verified_date="2026-09-29",
    )
    fields.update(changes)
    profile = ClusterProfile(**fields)
    path = folder / "profile.json"
    path.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
    return path


def make_experiment(root: Path, count: int = 2, *, z: int = 5, recipe: dict | None = None):
    """An experiment of small TIFF stacks, with a Cellpose recipe for the cluster."""

    from cellquant.controller import AnalysisController

    data = root / "images"
    (data / "Group A").mkdir(parents=True, exist_ok=True)
    for index in range(count):
        write_stack(data / "Group A" / f"stack{index}.tif", z=z, seed=index)
    controller = AnalysisController.create(root / "experiment", "HPC test", input_directory=data)
    controller.add_image_paths([data])
    controller.set_recipe(recipe or cellpose_recipe())
    controller.save()
    return controller


# A stand-in Cellpose 4 that finds bright blobs by thresholding, with the real 4.2 signatures.
# FAKE_CELLPOSE_DEVICE sets the device it reports ("cuda" by default, as on a GPU node).
FAKE_V4 = '''
import os, types
import numpy as np
from skimage.measure import label
MODEL_NAMES = ["cpsam_v2", "cpsam"]
CALLS = []

class CellposeModel:
    def __init__(self, gpu=False, pretrained_model="cpsam_v2", model_type=None,
                 diam_mean=None, device=None, nchan=None, use_bfloat16=True):
        CALLS.append(("init", dict(gpu=gpu, pretrained_model=pretrained_model)))
        self.device = types.SimpleNamespace(type=os.environ.get("FAKE_CELLPOSE_DEVICE", "cuda"))

    def eval(self, x, batch_size=8, resample=True, channels=None, channel_axis=None, z_axis=None,
             normalize=True, rescale=None, diameter=None, flow_threshold=0.4, cellprob_threshold=0.0,
             do_3D=False, anisotropy=None, flow3D_smooth=0, stitch_threshold=0.0, min_size=15,
             max_size_fraction=0.4, niter=None, augment=False, tile_overlap=0.1, bsize=256,
             compute_masks=True, progress=None):
        CALLS.append(("eval", dict(do_3D=do_3D, shape=np.asarray(x).shape)))
        array = np.asarray(x, dtype=float)
        cut = (float(array.min()) + float(array.max())) / 2.0
        masks = label(array > cut).astype(np.int32)
        return masks, None, None
'''


def install_fake_cellpose(tmp_path: Path, monkeypatch, device: str = "cuda"):
    from tests.test_cellpose_engines import _install_fake

    monkeypatch.setenv("FAKE_CELLPOSE_DEVICE", device)
    from cellquant import segmentation

    segmentation._MODELS.clear()
    return _install_fake(tmp_path, monkeypatch, "4.2.1.1", FAKE_V4)


def matching_observer(runtime_bytes: bytes):
    """An observed-runtime function that reports exactly the contract (the cluster's software, as installed)."""

    import json

    contract = json.loads(runtime_bytes)

    def observe(engine, model, models_dir=None):
        return {
            "python_version": contract["python_version"],
            "application_build_sha256": contract["application_build_sha256"],
            "packages": contract["packages"],
            "cellpose_version": contract["cellpose_version"],
            "engine": contract["engine"],
            "model": contract["model"],
            "model_directory": contract["model_directory"],
            "model_files": contract["model_files"],
            "missing_model_files": [],
        }

    return observe


def prepared_package(root: Path, *, count: int = 2, z_stack: str = "max_projection", z: int = 5, recipe: dict | None = None):
    """(controller, package folder, runtime bytes) for a READY package of small stacks."""

    from cellquant.hpc.prepare import plan_preparation, prepare_package
    from cellquant.hpc.profiles import load_profile

    controller = make_experiment(root, count, z=z, recipe=recipe or cellpose_recipe(z_stack))
    runtime_path, runtime_bytes = write_runtime(root / "profiles")
    profile = load_profile(write_profile(root / "profiles", runtime_path, runtime_bytes))
    plan = plan_preparation(controller.experiment, controller.recipe, profile)
    assert plan.ok, [issue.message for issue in plan.all_errors()]
    result = prepare_package(plan, root / "packages")
    return controller, result.package_dir, runtime_bytes


class FakeND2:
    """nd2.ND2File stand-in with seeded random pixels, positions and Z, for exact round-trip checks."""

    def __init__(self, path, sizes: dict, names=("Green", "Red", "Far Red"), size=(0.575, 0.575, 1.5), seed: int = 3):
        import types

        self.sizes = sizes
        self.dtype = np.dtype("uint16")
        self._size = size
        self._seed = seed
        channel = lambda name: types.SimpleNamespace(  # noqa: E731
            channel=types.SimpleNamespace(name=name, color=types.SimpleNamespace(r=0, g=255, b=0)),
            microscope=types.SimpleNamespace(objectiveName="APO LWD 20x WI"),
            volume=types.SimpleNamespace(axesCalibrated=(True, True, True)),
        )
        self.metadata = types.SimpleNamespace(channels=[channel(name) for name in names])

    def voxel_size(self):
        import types

        return types.SimpleNamespace(x=self._size[0], y=self._size[1], z=self._size[2])

    def asarray(self):
        rng = np.random.default_rng(self._seed)
        return rng.integers(0, 4096, size=tuple(self.sizes.values())).astype(np.uint16)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def install_fake_nd2(monkeypatch, **kwargs) -> None:
    import sys
    import types

    monkeypatch.setitem(sys.modules, "nd2", types.SimpleNamespace(ND2File=lambda path: FakeND2(path, **kwargs)))


def reseal(package: Path) -> None:
    """Recompute checksums, validation and READY after deliberately changing a package (to test deeper checks)."""

    import json

    from cellquant.hpc.common import sha256_bytes, utc_now
    from cellquant.hpc.validate import checksums_document, ready_document

    listed = sorted(
        path.relative_to(package).as_posix()
        for path in package.rglob("*")
        if path.is_file() and path.relative_to(package).as_posix() not in ("checksums.json", "validation.json", "READY")
    )
    data = (json.dumps(checksums_document(package, listed), indent=2) + "\n").encode()
    (package / "checksums.json").write_bytes(data)
    bundle = json.loads((package / "bundle.json").read_text())
    digest = sha256_bytes(data)
    (package / "validation.json").write_text(json.dumps({"ok": True, "checksums_sha256": digest, "bundle_id": bundle["bundle_id"], "checked_at": utc_now()}))
    (package / "READY").write_text(json.dumps(ready_document(bundle["bundle_id"], digest)))
