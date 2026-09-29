"""Checks before analysis: the package, the installed software, and (on a GPU node) a small inference.

On a login node the check stops before any GPU work. With ``require_gpu`` it
must run inside a GPU allocation: PyTorch must see a usable GPU and a small
synthetic segmentation in the package's Z mode must run on it. A login-node
pass is never reported as a GPU pass.
"""

from __future__ import annotations

import os
import socket
import sys
import time
from typing import Callable

import numpy as np

from cellquant.hpc.common import utc_now
from cellquant.hpc.models import Issue, PreflightReport
from cellquant.hpc.runtime import compare_runtime, fingerprint, gpu_report, observed_runtime
from cellquant.hpc.validate import validate_bundle

_GUI_MODULES = ("napari", "qtpy", "PyQt5", "PyQt6", "PySide2", "PySide6")


def synthetic_image(bundle, *, size: int = 160):
    """A small image with round nuclei in the segmentation channel, shaped like the package's images."""

    from cellquant.image import LoadedImage

    first = bundle.manifest.acquisitions[0]
    channels = len(bundle.manifest.channel_layout)
    three_d = bundle.recipe.z_stack in ("stitch_slices", "full_3d")
    planes = 5 if three_d else 1
    x, y, z = first.effective_spacing_xyz_um
    yy, xx = np.mgrid[0:size, 0:size]
    rng = np.random.default_rng(0)
    base = rng.normal(100, 5, size=(channels, planes, size, size)).clip(0)
    for cy, cx in ((40, 40), (40, 110), (110, 60), (120, 125)):
        blob = np.exp(-(((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * 7.0**2)))
        for plane in range(planes):
            weight = 1.0 - abs(plane - planes // 2) / (planes + 1)
            base[bundle.recipe.object_set.segmentation_channel, plane] += 2000 * blob * weight
    data = base.astype(np.uint16)
    if not three_d:
        data = data[:, 0]
    return LoadedImage(
        data=data,
        pixel_size_x=x,
        pixel_size_y=y,
        pixel_size_z=z if three_d else None,
        axes="CZYX" if three_d else "CYX",
        z_planes=planes,
        z_mode=bundle.recipe.z_stack if three_d else "none",
        channel_names=tuple(bundle.manifest.channel_layout),
    )


def run_preflight(
    package,
    *,
    require_gpu: bool,
    observe: Callable[..., dict] | None = None,
    gpu: Callable[[], dict] | None = None,
    forbid_interface_modules: bool = True,
) -> PreflightReport:
    errors: list[Issue] = []
    report = PreflightReport(ok=False, checked_at=utc_now(), host=socket.gethostname(), job_id=os.environ.get("SLURM_JOB_ID", ""))
    validation, bundle = validate_bundle(package, require_ready=True)
    if bundle is None:
        report.errors = validation.errors
        return report
    report.bundle_id = bundle.manifest.bundle_id
    report.runtime_id = bundle.runtime.runtime_id
    observed = (observe or observed_runtime)(bundle.runtime.engine, bundle.runtime.model, models_dir=bundle.runtime.model_directory)
    errors.extend(compare_runtime(bundle.runtime, observed))
    report.runtime_fingerprint = fingerprint(observed)
    try:
        import cellquant.pipeline  # noqa: F401 - the analysis entry point must import without a display
    except Exception as exc:  # noqa: BLE001
        errors.append(Issue(code="E_IMPORT", message=f"The analysis code could not be imported: {exc}"))
    loaded_gui = [name for name in _GUI_MODULES if name in sys.modules] if forbid_interface_modules else []
    if loaded_gui:
        errors.append(Issue(code="E_IMPORT", message=f"The worker loaded interface modules ({', '.join(loaded_gui)}); it must run without them."))
    if not require_gpu:
        report.inference = {"run": False, "reason": "No GPU check was asked for (login node). The job repeats this check on its GPU node."}
    elif not errors:
        info = (gpu or gpu_report)()
        report.gpu = info
        if not info.get("available"):
            errors.append(
                Issue(
                    code="E_GPU",
                    message="PyTorch cannot use a GPU on this node: " + str(info.get("error") or "no CUDA device is visible."),
                    fix="Check the job's --gres request and that the environment's PyTorch was built with CUDA for this GPU.",
                )
            )
        else:
            errors.extend(_small_inference(bundle, report))
    report.errors = errors
    report.ok = not errors
    return report


def _small_inference(bundle, report: PreflightReport) -> list[Issue]:
    from cellquant.pipeline import segment_channel

    loaded = synthetic_image(bundle)
    details: dict = {}
    started = time.perf_counter()
    try:
        labels = segment_channel(loaded, bundle.recipe, details, record_timing=False)
    except Exception as exc:  # noqa: BLE001
        report.inference = {"run": True, "ok": False, "error": str(exc)}
        return [Issue(code="E_INFERENCE", message=f"A small test segmentation failed: {exc}")]
    engine = details.get("engine") or {}
    device = str(engine.get("device") or "")
    report.inference = {
        "run": True,
        "ok": device == "cuda",
        "mode": bundle.recipe.z_stack,
        "shape": list(loaded.data.shape),
        "seconds": round(time.perf_counter() - started, 2),
        "device": device,
        "engine": engine,
        "objects": int(len(np.unique(labels)) - 1),
    }
    if device != "cuda":
        return [Issue(code="E_GPU", message=f"The test segmentation ran on '{device or 'unknown'}', not the GPU. The job will not fall back to the CPU.")]
    return []
