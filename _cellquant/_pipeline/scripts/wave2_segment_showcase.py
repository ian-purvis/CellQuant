"""Run an actual Cellpose-SAM v4 CPU showcase on a real reference crop."""

from __future__ import annotations

import json
import copy
from pathlib import Path
import time

import numpy as np
import tifffile

from cellquant.config import RunConfig, load_config
from cellquant.contracts import ImageVolume, MutableCancellationToken
from cellquant.segment import segment


PROJECT = Path(__file__).resolve().parents[1]
TISSUE = PROJECT.parents[2]
NAME = "Control__Explant_3__CRISPRi_cont_GFP_OTX2_far_red003.tif"
INPUT = TISSUE / "Cellpose_documentation" / "Cellpose_test" / "test_images" / NAME
BASELINE = TISSUE / "Cellpose_documentation" / "Cellpose_test" / "outputs" / NAME
OUTPUT = PROJECT / "artifacts" / "wave2_segment"


def main() -> None:
    print("showcase: loading crop", flush=True)
    image = np.asarray(tifffile.imread(INPUT))
    baseline = np.asarray(tifffile.imread(BASELINE))
    foreground = np.argwhere(baseline > 0)
    centre_y, centre_x = np.median(foreground[:, 1:], axis=0).astype(int)
    size = 128
    y0 = max(0, min(image.shape[1] - size, int(centre_y - size // 2)))
    x0 = max(0, min(image.shape[2] - size, int(centre_x - size // 2)))
    crop = image[:, y0 : y0 + size, x0 : x0 + size]
    baseline_crop = baseline[:, y0 : y0 + size, x0 : x0 + size]

    raw = copy.deepcopy(dict(load_config(PROJECT / "reference_config.yaml").raw))
    raw["segment"] = {
        **raw["segment"],
        "anisotropy": 1.5 / 0.575445178776431,
        "batch_size": 1,
        "device": "cpu",
        "use_bfloat16": False,
    }
    config = RunConfig(raw)
    volume = ImageVolume(crop[..., None].astype(np.float32), (1.5, 0.575445178776431, 0.575445178776431),
                         ("nuclear",), INPUT, {"crop_yx": [y0, x0, size, size]})
    OUTPUT.mkdir(parents=True, exist_ok=True)
    print(f"showcase: segmenting shape={crop.shape} on CPU", flush=True)
    started = time.perf_counter()
    labels = segment(volume, config, MutableCancellationToken())
    elapsed = time.perf_counter() - started
    tifffile.imwrite(OUTPUT / "input_crop.tif", crop, photometric="minisblack", metadata={"axes": "ZYX"})
    tifffile.imwrite(OUTPUT / "baseline_crop.tif", baseline_crop.astype(np.uint32), photometric="minisblack", metadata={"axes": "ZYX"})
    tifffile.imwrite(OUTPUT / "candidate_crop.tif", labels.data, photometric="minisblack", metadata={"axes": "ZYX"})
    (OUTPUT / "run.json").write_text(json.dumps({
        "source": str(INPUT), "crop_yx": [y0, x0, size, size], "wall_seconds": elapsed,
        "candidate_label_count": int(np.unique(labels.data).size - (0 in labels.data)),
        "baseline_crop_label_count": int(np.unique(baseline_crop).size - (0 in baseline_crop)),
        "provenance": labels.provenance, "config": raw,
        "note": "CPU crop showcase only; not a full-stack parity run.",
    }, indent=2), encoding="utf-8")
    print(json.dumps({"wall_seconds": elapsed, "candidate_labels": int(np.unique(labels.data).size - (0 in labels.data))}))


if __name__ == "__main__":
    main()
