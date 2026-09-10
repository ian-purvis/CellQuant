import json

import numpy as np
import tifffile

from cellquant.cli import main, output_directory


def test_output_directory_retains_extension_to_avoid_stem_collision(tmp_path):
    nd2 = output_directory(tmp_path, "nested/sample.nd2")
    tif = output_directory(tmp_path, "nested/sample.tif")
    assert nd2 == tmp_path / "nested" / "sample.nd2.cellquant"
    assert tif == tmp_path / "nested" / "sample.tif.cellquant"
    assert nd2 != tif


def test_parity_command_writes_numerical_report(tmp_path):
    labels = np.zeros((2, 8, 8), dtype=np.uint16)
    labels[:, 2:6, 2:6] = 1
    reference = tmp_path / "reference.tif"
    candidate = tmp_path / "candidate.tif"
    tifffile.imwrite(reference, labels, photometric="minisblack", metadata={"axes": "ZYX"})
    tifffile.imwrite(candidate, labels, photometric="minisblack", metadata={"axes": "ZYX"})

    output = tmp_path / "report"
    assert main(["parity", str(reference), str(candidate), str(output)]) == 0
    report = json.loads((output / "parity_report.json").read_text(encoding="utf-8"))
    assert report["aggregate"]["mean_optimal_matched_iou"] == 1.0
    assert (output / "parity_summary.png").is_file()
