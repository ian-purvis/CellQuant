"""Check a finished CellQuant environment.

Usage: python smoke_test.py <cellpose4|cellpose3> [--benchmark-threads]

Imports the app, confirms the expected Cellpose engine, runs a small analysis
with the classical method, and reports whether PyTorch can use the GPU. The
last line is ``CELLQUANT_SMOKE <json>``. Exit code 0 means the environment works.
With ``--benchmark-threads`` it also times Cellpose at a few CPU thread counts and
keeps the fastest for this computer (``cellquant.hardware.benchmark_threads``).
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    arguments = [item for item in sys.argv[1:] if not item.startswith("--")]
    expected = arguments[0] if arguments else ""
    report: dict[str, object] = {"ok": False}
    try:
        import numpy as np

        import cellquant
        from cellquant.engines import cellpose_engine, gpu_status
        from cellquant.pipeline import process_image

        report["cellquant"] = cellquant.__version__
        engine = cellpose_engine()
        report["engine"] = engine.key
        report["cellpose"] = engine.version
        report["default_model"] = engine.default_model
        if not engine.installed:
            report["error"] = "Cellpose is not installed in this environment."
            return _finish(report, 1)
        if expected and engine.key != expected:
            report["error"] = f"Expected {expected}, but this environment has {engine.label}."
            return _finish(report, 1)

        import napari
        import qtpy

        report["napari"] = napari.__version__
        report["qt"] = qtpy.API_NAME

        image = np.zeros((2, 64, 64), dtype=np.uint16)
        image[0, 8:20, 8:20] = 1000
        image[0, 36:50, 36:50] = 1000
        image[1, 8:20, 8:20] = 600
        recipe = {
            "object_set": {
                "segmentation_channel": 0,
                "algorithm": "classical",
                "parameters": {"threshold_method": "otsu"},
            },
            "measurements": [{"id": "m", "channel": 1, "region": {"type": "object"}, "statistic": "mean"}],
            "classifications": [{"id": "c", "name": "A", "measurement": "m", "threshold": 100}],
            "reports": [{"numerator": "A", "denominator": "all_objects"}],
        }
        result = process_image(image, recipe)
        percent = float(result.reports.iloc[0]["percent"])
        if result.qc.n_objects != 2 or abs(percent - 50.0) > 1e-6:
            report["error"] = f"Test analysis gave {result.qc.n_objects} objects and {percent}% (expected 2 and 50%)."
            return _finish(report, 1)
        report["test_analysis"] = "2 objects, 50% positive"
        report["gpu"] = gpu_status()
        if "--benchmark-threads" in sys.argv[1:]:
            from cellquant.hardware import benchmark_threads

            try:
                report["cpu_threads"] = benchmark_threads(engine.key)
            except Exception as exc:  # noqa: BLE001 - speed only: the environment still works
                report["cpu_threads"] = {"error": f"{type(exc).__name__}: {exc}"}
    except Exception as exc:  # noqa: BLE001 - reported to the installer
        report["error"] = f"{type(exc).__name__}: {exc}"
        return _finish(report, 1)
    report["ok"] = True
    return _finish(report, 0)


def _finish(report: dict[str, object], code: int) -> int:
    print("CELLQUANT_SMOKE " + json.dumps(report))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
