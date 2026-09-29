"""Saved results reload completely: QC, reports, combinations, column types and missing values."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cellquant.controller import AnalysisController
from cellquant.pipeline import process_image
from cellquant.storage import persist_image_result, read_persisted_result
from tests.hpc_helpers import classical_recipe, write_stack


def _assert_same_result(first, second) -> None:
    assert np.array_equal(first.labels, second.labels)
    assert np.array_equal(first.automated_labels, second.automated_labels)
    for name in ("objects", "summary", "phenotype_counts", "combination_counts", "reports"):
        pd.testing.assert_frame_equal(getattr(first, name).reset_index(drop=True), getattr(second, name).reset_index(drop=True), check_dtype=True, obj=name)
    assert first.qc.status == second.qc.status and first.qc.n_objects == second.qc.n_objects
    assert first.qc.warnings == second.qc.warnings
    assert first.qc.median_area == pytest.approx(second.qc.median_area)
    assert first.qc.fraction_touching_border == pytest.approx(second.qc.fraction_touching_border, nan_ok=True)
    assert first.qc.percent_excluded_by_size == pytest.approx(second.qc.percent_excluded_by_size, nan_ok=True)
    assert first.spatial_unit == second.spatial_unit


@pytest.mark.parametrize("mode", ["max_projection", "stitch_slices"])
def test_a_saved_result_reloads_unchanged(tmp_path: Path, mode: str):
    path = tmp_path / "stack.tif"
    write_stack(path)
    from cellquant.image import load_image

    loaded = load_image(path, z_mode=mode)
    result = process_image(loaded, classical_recipe(mode), sample_name="s", image_id="img_1", user_metadata={"Group": "A", "Blank": ""})
    assert result.objects["unmeasured"].any(), "the test needs an object that cannot be measured"
    assert result.objects["core_pos"].isna().any()
    assert len(result.reports) == 2 and len(result.combination_counts)
    run = tmp_path / "run"
    persist_image_result(run, result, [])
    again = read_persisted_result(run, "img_1")
    _assert_same_result(result, again)
    assert again.summary.loc[0, "Blank"] == ""


def test_results_saved_by_older_versions_still_open(tmp_path: Path):
    path = tmp_path / "stack.tif"
    write_stack(path)
    result = process_image(path, classical_recipe(), image_id="img_1")
    run = tmp_path / "run"
    persist_image_result(run, result, [])
    for folder in ("tables", "qc", "reports"):
        for item in (run / folder).iterdir():
            item.unlink()
    (run / "classifications" / "img_1_combinations.csv").unlink()
    old = read_persisted_result(run, "img_1")
    assert old.reports.empty and old.combination_counts.empty
    assert old.qc.n_objects == result.qc.n_objects
    assert old.objects["excluded"].dtype == bool


def test_a_cancelled_batch_is_not_recorded_as_completed(tmp_path: Path):
    import json

    (tmp_path / "data").mkdir()
    for index in range(3):
        write_stack(tmp_path / "data" / f"s{index}.tif", seed=index)
    controller = AnalysisController.create(tmp_path / "results", "Cancel")
    controller.add_image_paths([tmp_path / "data"])
    controller.set_recipe(classical_recipe())
    seen = {"n": 0}

    def keep_going() -> bool:
        seen["n"] += 1
        return seen["n"] <= 1

    report = controller.run_images(should_continue=keep_going)
    assert len(report.jobs) == 1
    run = json.loads((Path(report.run_dir) / "run.json").read_text(encoding="utf-8"))
    assert run["processing_status"] == "cancelled"
