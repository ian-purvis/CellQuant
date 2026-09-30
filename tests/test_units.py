"""Retina-level (unit) summaries (N4): per-unit pooled percents and the equal-unit mean per group."""

from __future__ import annotations

import math
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cellquant.storage import default_unit_column, equal_unit_summary, grouped_summary, unit_summary


def _summary() -> pd.DataFrame:
    # Condition A: Retina 1 has two images (10/40, 30/60), Retina 2 one (5/50). Condition B: one retina, one image.
    rows = [
        ("A", "Retina 1", 10, 40, 1, 40),
        ("A", "Retina 1", 30, 60, np.nan, np.nan),  # report_2 blank in this image (a marker's channel is missing)
        ("A", "Retina 2", 5, 50, 5, 50),
        ("B", "Retina 1", 20, 40, 8, 40),
    ]
    frame = pd.DataFrame(rows, columns=["Folder 1", "Folder 2", "report_1_count", "report_1_denominator", "report_2_count", "report_2_denominator"])
    for prefix in ("report_1", "report_2"):
        frame[f"{prefix}_percent"] = 100.0 * frame[f"{prefix}_count"] / frame[f"{prefix}_denominator"]
    return frame


def test_the_default_unit_is_the_folder_that_holds_the_images():
    assert default_unit_column(_summary()) == "Folder 2"
    assert default_unit_column(pd.DataFrame({"image_id": ["x"]})) is None


def test_units_pool_their_own_images_and_retinas_of_different_conditions_stay_apart():
    units = unit_summary(_summary(), "Folder 2", "Folder 1").set_index(["Folder 1", "Folder 2"])
    assert len(units) == 3
    assert units.loc[("A", "Retina 1"), "report_1_unit_pooled_percent"] == pytest.approx(40.0)  # 40 / 100
    assert units.loc[("A", "Retina 1"), "n_images"] == 2
    assert units.loc[("A", "Retina 1"), "report_2_unit_n_images"] == 1  # the blank image is left out
    assert units.loc[("A", "Retina 1"), "report_2_unit_pooled_percent"] == pytest.approx(2.5)  # 1 / 40
    assert units.loc[("A", "Retina 2"), "report_1_unit_pooled_percent"] == pytest.approx(10.0)
    assert units.loc[("B", "Retina 1"), "report_1_unit_pooled_percent"] == pytest.approx(50.0)


def test_equal_unit_mean_counts_every_retina_once():
    equal = equal_unit_summary(_summary(), "Folder 1", "Folder 2").set_index("Folder 1")
    assert equal.loc["A", "n_units"] == 2 and equal.loc["A", "report_1_n_units"] == 2
    assert equal.loc["A", "report_1_equal_unit_mean_percent"] == pytest.approx(25.0)  # (40 + 10) / 2
    assert equal.loc["A", "report_1_equal_unit_sd_percent"] == pytest.approx(math.sqrt(450.0))
    assert equal.loc["A", "report_2_equal_unit_mean_percent"] == pytest.approx((2.5 + 10.0) / 2)
    assert equal.loc["B", "report_1_equal_unit_mean_percent"] == pytest.approx(50.0)
    assert np.isnan(equal.loc["B", "report_1_equal_unit_sd_percent"])  # one retina: no SD
    # Different from the pooled percent (every nucleus equal) and the mean of image percents.
    grouped = grouped_summary(_summary(), "Folder 1").set_index("Folder 1")
    assert grouped.loc["A", "report_1_pooled_percent"] == pytest.approx(30.0)  # 45 / 150
    assert grouped.loc["A", "report_1_mean_of_per_image_percent"] == pytest.approx((25 + 50 + 10) / 3)
    assert grouped.loc["A", "report_2_pooled_percent"] == pytest.approx(6 / 90 * 100)  # blank image left out
    assert "older version" not in grouped.loc["A", "summary_note"]


def test_export_writes_unit_tables_for_condition_and_retina_folders(tmp_path: Path):
    from cellquant.controller import AnalysisController
    from cellquant.practice import PIXEL_SIZE_UM, write_practice_images
    from cellquant.quicksetup import marker_recipe

    raw = write_practice_images(tmp_path / "raw")
    images = tmp_path / "images"
    layout = [("Control", "Retina 1", raw[0]), ("Control", "Retina 1", raw[1]), ("Control", "Retina 2", raw[2]), ("Treated", "Retina 1", raw[0])]
    for index, (condition, retina, source) in enumerate(layout):
        target = images / condition / retina / f"image_{index}.tif"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    controller = AnalysisController.create(tmp_path / "experiment", "Units")
    controller.add_image_paths([images])
    for record in controller.experiment.images:
        controller.set_pixel_size(record.image_id, PIXEL_SIZE_UM, PIXEL_SIZE_UM)
    controller.set_recipe(
        {"object_set": {"name": "Nuclei", "segmentation_channel": 0, "algorithm": "classical", "parameters": {"threshold_method": "otsu", "sigma": 1.0}}}
    )
    data = marker_recipe(controller.recipe.model_dump(mode="json"), [(1, "Marker A"), (2, "Marker B")])
    for item in data["classifications"]:
        item["threshold"] = 400.0
    controller.set_recipe(data)
    controller.run_images()
    out = controller.export(tmp_path / "export", group_by="Folder 1")
    units = pd.read_csv(out / "units_by_Folder 2.csv").set_index(["Folder 1", "Folder 2"])
    # Marker A+ among all nuclei (report_1). Practice answers: 10/40, 20/40, 30/40.
    assert units.loc[("Control", "Retina 1"), "report_1_unit_pooled_percent"] == pytest.approx(100 * 30 / 80)
    equal = pd.read_csv(out / "grouped_by_Folder 1_equal_units.csv").set_index("Folder 1")
    assert equal.loc["Control", "report_1_equal_unit_mean_percent"] == pytest.approx((37.5 + 75.0) / 2)
    assert equal.loc["Treated", "n_units"] == 1
