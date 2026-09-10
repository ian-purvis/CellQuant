"""Stage preprocess, postprocess, and measure on one real recorded crop."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import tifffile

from cellquant.config import load_config
from cellquant.contracts import ImageVolume, LabelVolume
from cellquant.io import open_volume
from cellquant.measure import showcase_crop as measure_showcase
from cellquant.postprocess import showcase_crop as postprocess_showcase
from cellquant.preprocess import showcase_crop as preprocess_showcase


PROJECT = Path(__file__).resolve().parents[1]
TISSUE = PROJECT.parents[2]
NAME = "Control__Explant_3__CRISPRi_cont_GFP_OTX2_far_red003.tif"
INPUT = TISSUE / "Cellpose_documentation" / "Cellpose_test" / "test_images" / NAME
BASELINE = TISSUE / "Cellpose_documentation" / "Cellpose_test" / "outputs" / NAME
OUTPUT = PROJECT / "artifacts" / "wave2_showcases"


def main() -> None:
    config = load_config(PROJECT / "reference_config.yaml")
    image = open_volume(INPUT, lazy=True)
    baseline = np.asarray(tifffile.imread(BASELINE)).astype(np.uint32, copy=False)
    foreground = np.argwhere(baseline > 0)
    centre_y, centre_x = np.median(foreground[:, 1:], axis=0).astype(int)
    size = 256
    y0 = max(0, min(baseline.shape[1] - size, int(centre_y - size // 2)))
    x0 = max(0, min(baseline.shape[2] - size, int(centre_x - size // 2)))
    cropped_image = ImageVolume(
        image.data[:, y0 : y0 + size, x0 : x0 + size, :], image.spacing_um,
        image.channel_names, image.source, {**image.metadata, "crop_yx": [y0, x0, size, size]},
    )
    cropped_labels = LabelVolume(
        baseline[:, y0 : y0 + size, x0 : x0 + size], image.spacing_um,
        {"source": str(BASELINE), "crop_yx": [y0, x0, size, size]},
    )
    OUTPUT.mkdir(parents=True, exist_ok=True)
    preprocess_artifacts = preprocess_showcase(cropped_image, config, OUTPUT / "preprocess")
    postprocess_artifacts = postprocess_showcase((cropped_image, cropped_labels), config, OUTPUT / "postprocess")
    measurement_artifacts = measure_showcase((cropped_image, cropped_labels), config, OUTPUT / "measure")
    (OUTPUT / "showcase_manifest.json").write_text(json.dumps({
        "source": str(INPUT), "baseline": str(BASELINE), "crop_yx": [y0, x0, size, size],
        "preprocess": {key: str(path) for key, path in preprocess_artifacts.items()},
        "postprocess": {key: str(path) for key, path in postprocess_artifacts.items()},
        "measure": [str(path) for path in measurement_artifacts],
    }, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

