"""Synthetic retina z-stacks: readable like the real files, exact truth, and hard-but-fair markers."""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import tifffile

from cellquant.image import read_stack
from cellquant.pipeline import process_image
from cellquant.quicksetup import marker_recipe
from cellquant.synthetic_retina import (
    CHANNEL_NAMES,
    FULL_SCALE,
    PIXEL_SIZE_UM,
    Z_STEP_UM,
    RetinaPlan,
    main,
    write_retina_image,
    write_retina_set,
)

BASE = {"z_stack": "full_3d", "object_set": {"segmentation_channel": 2, "algorithm": "classical", "parameters": {}}}


@pytest.fixture(scope="module")
def retina(tmp_path_factory) -> tuple[Path, dict]:
    folder = tmp_path_factory.mktemp("retina")
    row = write_retina_image(RetinaPlan(name="r", size=160, n_nuclei=165, otx2_of_fluor_percent=31.0, seed=5), folder)
    return folder / "r", row


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_reads_like_the_lab_nd2_files(retina):
    stem, _ = retina
    stack = read_stack(f"{stem}.tif")
    assert stack.zcyx.shape == (7, 3, 160, 160) and stack.zcyx.dtype == np.uint16
    assert stack.meta["channel_names"] == list(CHANNEL_NAMES)
    assert stack.meta["pixel_size_x"] == pytest.approx(PIXEL_SIZE_UM, rel=1e-6)
    assert stack.meta["pixel_size_z"] == pytest.approx(Z_STEP_UM)
    green, red, far_red = stack.meta["channel_colors"]
    assert red == (1.0, 0.0, 0.0) and far_red == (1.0, 0.0, 1.0) and green[1] == 1.0
    assert int(stack.zcyx.max()) <= FULL_SCALE  # 12-bit camera
    assert int(stack.zcyx[:, 1].min()) > 40  # camera offset, as in the real files


def test_truth_matches_the_labels_exactly(retina):
    stem, row = retina
    labels = tifffile.imread(f"{stem}_labels.tif")
    truth = pd.read_csv(f"{stem}_truth.csv")
    ids = np.unique(labels[labels > 0])
    assert sorted(truth["label"].tolist()) == ids.tolist()
    counts = np.bincount(labels.ravel())
    assert truth.set_index("label")["voxels"].to_dict() == {int(i): int(counts[i]) for i in ids}
    fluor = truth[truth["fluor"] == 1]
    assert row["n_fluor"] == len(fluor) and row["n_fluor_otx2"] == int(fluor["otx2"].sum())
    assert row["pct_otx2_of_fluor"] == pytest.approx(100 * fluor["otx2"].mean(), abs=1e-3)
    assert abs(row["pct_otx2_of_fluor"] - 31.0) < 100 / len(fluor)  # as close as whole nuclei allow
    assert set(truth["otx2_pattern"]) == {"none", "whole", "partial"}
    assert ((truth["otx2_pattern"] == "none") == (truth["otx2"] == 0)).all()


def test_the_same_seed_gives_the_same_files(tmp_path):
    for folder in (tmp_path / "a", tmp_path / "b"):
        write_retina_image(RetinaPlan(name="x", size=64, n_nuclei=30, seed=3), folder)
    for suffix in (".tif", "_labels.tif", "_truth.csv"):
        assert _digest(tmp_path / "a" / f"x{suffix}") == _digest(tmp_path / "b" / f"x{suffix}")


def test_markers_are_hard_but_separable_with_the_right_objects(retina):
    """With the true nuclei, one cutoff per marker gets nearly every call right, and the
    headline percent comes back within a few points; an automatic cutoff alone may not."""

    stem, row = retina
    labels = tifffile.imread(f"{stem}_labels.tif").astype(np.int32)
    truth = pd.read_csv(f"{stem}_truth.csv").set_index("label")
    recipe = marker_recipe(dict(BASE), [(0, "OTX2"), (1, "Fluor")])
    objects = process_image(f"{stem}.tif", recipe, automated_labels=labels).objects.set_index("object_id")
    best = {}
    for slug, column in (("otx2", "otx2"), ("fluor", "fluor")):
        values = objects[f"{slug}_mean"].to_numpy(float)
        expected = truth.loc[objects.index, column].to_numpy(bool)
        cuts = np.unique(values)
        accuracy = np.array([((values > cut) == expected).mean() for cut in cuts])
        assert accuracy.max() >= 0.95, slug
        assert accuracy.max() < 1.0 or slug == "fluor"  # dim and partial OTX2 nuclei make it imperfect
        best[column] = values > cuts[int(np.argmax(accuracy))]
    both = best["fluor"] & best["otx2"]
    assert 100 * both.sum() / best["fluor"].sum() == pytest.approx(row["pct_otx2_of_fluor"], abs=5)


def test_partial_otx2_nuclei_show_up_in_the_percent_of_cell_measurement(retina):
    stem, _ = retina
    labels = tifffile.imread(f"{stem}_labels.tif").astype(np.int32)
    truth = pd.read_csv(f"{stem}_truth.csv").set_index("label")
    recipe = marker_recipe(dict(BASE), [(0, "OTX2")], rule="percent_above", min_percent=50)
    recipe["measurements"][1]["pixel_level"] = 150.0
    objects = process_image(f"{stem}.tif", recipe, automated_labels=labels).objects.set_index("object_id")
    percent = objects["otx2_pct"].to_numpy(float)
    pattern = truth.loc[objects.index, "otx2_pattern"].to_numpy()
    assert np.median(percent[pattern == "none"]) < 5
    assert 25 < np.median(percent[pattern == "partial"]) < 85
    assert np.median(percent[pattern == "whole"]) > 90


def test_classical_segmentation_finds_a_plausible_number_of_nuclei(retina):
    stem, row = retina
    recipe = {
        **BASE,
        "object_set": {
            "segmentation_channel": 2,
            "algorithm": "classical",
            "parameters": {"sigma": 1.0, "use_watershed": True, "watershed_min_distance_px": 4, "min_area_um2": 5},
        },
    }
    result = process_image(f"{stem}.tif", recipe)
    assert 0.6 * row["n_nuclei"] <= result.qc.n_objects <= 1.6 * row["n_nuclei"]


def test_control_and_crispri_set_with_known_answers(tmp_path):
    rows = write_retina_set(tmp_path, size=96, retinas=1, seed=2)
    assert [row["condition"] for row in rows] == ["Control", "CRISPRi"]
    assert (tmp_path / "Control" / "Retina 1" / "control_r1.tif").is_file()
    assert (tmp_path / "CRISPRi" / "Retina 1" / "crispri_r1_labels.tif").is_file()
    with (tmp_path / "truth_summary.csv").open(encoding="utf-8") as handle:
        summary = list(csv.DictReader(handle))
    assert [item["image"] for item in summary] == ["Control/Retina 1/control_r1.tif", "CRISPRi/Retina 1/crispri_r1.tif"]
    control, crispri = (float(item["pct_otx2_of_fluor"]) for item in summary)
    assert control > crispri
    assert "No real image data" in (tmp_path / "README.txt").read_text(encoding="utf-8")


def test_command_line(tmp_path, capsys):
    assert main([str(tmp_path / "out"), "--size", "64", "--retinas", "1", "--control-percent", "50", "--crispri-percent", "10"]) == 0
    assert "Known answers" in capsys.readouterr().out
    assert (tmp_path / "out" / "truth_summary.csv").is_file()
