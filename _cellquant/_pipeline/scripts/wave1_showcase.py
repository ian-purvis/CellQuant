"""Generate Wave 1 artifacts from the real Cellpose reference corpus."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import tifffile

from cellquant.config import load_config
from cellquant.contracts import LabelVolume
from cellquant.harness import HarnessCase, run_case
from cellquant.verify import compare_pair, render_parity_report


PROJECT = Path(__file__).resolve().parents[1]
TISSUE_IMAGES = PROJECT.parents[2]
REFERENCE = TISSUE_IMAGES / "Cellpose_documentation" / "Cellpose_test"
INPUTS = REFERENCE / "test_images"
LABELS = REFERENCE / "outputs"
ARTIFACTS = PROJECT / "artifacts" / "wave1"

CASES = [
    ("low", "Control__Explant_3__CRISPRi_cont_GFP_OTX2_far_red003.tif", 655),
    ("median", "Control__Explant_4__CRISPRi_cont_GFP_OTX2_far_red006.tif", 2430),
    (
        "high",
        "CRISPRi_DHS2_DHS15__Explant_3__CRISPRi_DHS2-mCherry_DHS15-GFP_OTX2_far_red003.tif",
        3287,
    ),
]


def main() -> None:
    config = load_config(PROJECT / "reference_config.yaml")
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    parity = []
    harness_results = []
    for density, filename, expected_count in CASES:
        baseline_path = LABELS / filename
        baseline = np.asarray(tifffile.imread(baseline_path)).astype(np.uint32, copy=False)
        measured_count = int(np.unique(baseline).size - (0 in baseline))
        if measured_count != expected_count:
            raise RuntimeError(
                f"{filename}: CSV says {expected_count} labels but TIFF contains {measured_count}"
            )
        parity.append(compare_pair(baseline, baseline, filename))

        def baseline_runner(image, _config, _cancel, _events, data=baseline):
            if tuple(image.data.shape[:3]) != tuple(data.shape):
                raise ValueError(f"reference image/mask shape mismatch: {image.data.shape[:3]} vs {data.shape}")
            return LabelVolume(
                data.copy(), image.spacing_um, {"input_fingerprint": f"reference-baseline-{density}"}
            )

        harness_results.append(
            run_case(
                HarnessCase(filename, INPUTS / filename),
                config,
                ARTIFACTS / f"harness_{density}",
                pipeline_runner=baseline_runner,
            )
        )

    report = render_parity_report(parity, ARTIFACTS / "identity_parity", title="Wave 1 parity-checker identity control")
    (ARTIFACTS / "showcase_results.json").write_text(
        json.dumps(
            {
                "note": "Harness used existing reference labels as an injected control; this is not a candidate Cellpose run.",
                "parity_report": str(report),
                "cases": [
                    {
                        "density": density,
                        "filename": filename,
                        "expected_count": expected,
                        "harness_success": result.success,
                        "harness_label_count": result.label_count,
                        "wall_seconds": result.wall_seconds,
                        "peak_host_ram_bytes": result.peak_host_ram_bytes,
                    }
                    for (density, filename, expected), result in zip(CASES, harness_results, strict=True)
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

