"""M0 follow-up: unmeasured objects, export after reopening, settings hash, memory, group SD."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import tifffile

from cellquant.controller import SESSION_IMAGE_LIMIT, AnalysisController, process_experiment
from cellquant.pipeline import process_image
from cellquant.quantify import classify_objects, generate_phenotypes, summarize_image
from cellquant.recipe import ClassificationSpec, ReportSpec, load_recipe
from cellquant.storage import grouped_summary
from tests.test_workflow import _recipe, _write_squares


def _write_big_and_small(path: Path) -> None:
    """One 30 px nucleus and one 10 px nucleus, and a marker in both."""

    image = np.zeros((2, 90, 90), dtype=np.uint16)
    image[0, 5:35, 5:35] = 1000
    image[0, 60:70, 60:70] = 1000
    image[1, 5:35, 5:35] = 800
    image[1, 60:70, 60:70] = 800
    tifffile.imwrite(path, image, photometric="minisblack")


def _eroded_recipe() -> dict:
    return {
        "object_set": {
            "segmentation_channel": 0,
            "algorithm": "classical",
            "parameters": {"threshold_method": "manual", "threshold": 100},
        },
        # Eroding by 6 px leaves nothing of the 10 px nucleus and part of the 30 px one.
        "measurements": [
            {"id": "m", "channel": 1, "region": {"type": "eroded_object", "distance_px": 6}, "statistic": "mean"}
        ],
        "classifications": [{"id": "c", "name": "A", "measurement": "m", "threshold": 100}],
        "reports": [{"numerator": "A", "denominator": "all_objects"}],
    }


def test_unmeasured_nucleus_is_left_out_of_every_count(tmp_path: Path):
    path = tmp_path / "s.tif"
    _write_big_and_small(path)
    result = process_image(path, _eroded_recipe())
    assert len(result.objects) == 2
    assert int(result.objects["unmeasured"].sum()) == 1
    assert result.qc.n_objects == 1
    row = result.summary.iloc[0]
    assert row["total_objects"] == 1
    assert row["n_unmeasured"] == 1
    report = result.reports.iloc[0]
    assert (report["count"], report["denominator_count"], report["percent"]) == (1, 1, 100.0)
    assert any("could not be measured" in warning for warning in result.qc.warnings)
    assert int(result.phenotype_counts["count"].sum()) == 1


def test_expression_denominator_also_ignores_unmeasured_objects():
    nan = np.nan
    table = pd.DataFrame(
        {
            "object_id": range(1, 9),
            "centroid_x": 0,
            "centroid_y": 0,
            "area": 1,
            "a": [9, 9, 1, 1, nan, 9, 1, nan],
            "b": [9, 1, 9, nan, 1, nan, nan, 9],
            "excluded": False,
        }
    )
    classes = [
        ClassificationSpec(id="ca", name="A", measurement="a", threshold=5),
        ClassificationSpec(id="cb", name="B", measurement="b", threshold=5),
    ]
    reports = [
        ReportSpec(numerator="A", denominator="all_objects"),
        ReportSpec(numerator="B", denominator="A"),
        ReportSpec(numerator="A AND NOT B", denominator="A"),
    ]
    tt = generate_phenotypes(classify_objects(table, classes), classes)
    summary, exclusive, _combos, warnings = summarize_image(tt, reports, classes)
    rows = summary.set_index("report")
    # Objects 1-3 are measured for both markers. 4-8 are not and are left out.
    assert (rows.loc["report_1", "count"], rows.loc["report_1", "denominator_count"]) == (2, 3)
    assert (rows.loc["report_2", "count"], rows.loc["report_2", "denominator_count"]) == (1, 2)
    assert rows.loc["report_2", "percent"] == pytest.approx(50)
    assert (rows.loc["report_3", "count"], rows.loc["report_3", "denominator_count"]) == (1, 2)
    assert (summary["n_unmeasured"] == 5).all()
    assert int(exclusive["count"].sum()) == 3
    assert any("5 objects could not be measured" in warning for warning in warnings)


def test_user_deleted_objects_are_not_reported_as_unmeasured():
    table = pd.DataFrame(
        {
            "object_id": [1, 2, 3],
            "centroid_x": 0,
            "centroid_y": 0,
            "area": 1,
            "m": [9.0, np.nan, 1.0],
            "excluded": [False, True, False],
        }
    )
    classes = [ClassificationSpec(id="c", name="A", measurement="m", threshold=5)]
    tt = generate_phenotypes(classify_objects(table, classes), classes)
    summary, _e, _c, warnings = summarize_image(tt, [ReportSpec(numerator="A", denominator="all_objects")], classes)
    assert summary.iloc[0]["n_unmeasured"] == 0
    assert summary.iloc[0]["denominator_count"] == 2
    assert warnings == []


def test_export_works_after_reopening_the_experiment(tmp_path: Path):
    paths = []
    for index in range(3):
        path = tmp_path / f"s{index}.tif"
        _write_squares(path)
        paths.append(path)
    controller = AnalysisController.create(tmp_path / "experiment", "Reopen")
    controller.add_image_paths(paths)
    controller.set_recipe(_recipe())
    controller.save()
    process_experiment(tmp_path / "experiment")

    reopened = AnalysisController.open(tmp_path / "experiment")
    assert reopened.last_results == {}
    out = reopened.export(tmp_path / "out")
    objects = pd.read_csv(out / "objects.csv")
    summary = pd.read_csv(out / "image_summary.csv")
    assert len(summary) == 3
    assert objects["image_id"].nunique() == 3
    assert reopened.last_results == {}  # export did not pull every result into memory
    assert (out / "recipe.yaml").is_file() and (out / "settings_index.csv").is_file()


def test_thresholds_work_in_a_reopened_session(tmp_path: Path):
    path = tmp_path / "s.tif"
    _write_squares(path)
    controller = AnalysisController.create(tmp_path / "experiment", "Reopen thresholds")
    controller.add_image_paths([path])
    controller.set_recipe(_recipe())
    image_id = controller.experiment.images[0].image_id
    controller.run_image(image_id)
    reopened = AnalysisController.open(tmp_path / "experiment")
    result = reopened.update_thresholds(image_id, {"class_a": 10_000})
    assert result is not None
    assert int(result.objects["class_a"].sum()) == 0


def test_one_unchanged_batch_has_one_settings_hash(tmp_path: Path):
    paths = []
    for index in range(4):
        path = tmp_path / f"s{index}.tif"
        _write_squares(path)
        paths.append(path)
    controller = AnalysisController.create(tmp_path / "experiment", "Hash")
    controller.add_image_paths(paths)
    controller.set_recipe(_recipe())
    controller.run_images()
    out = controller.export(tmp_path / "out")
    index = pd.read_csv(out / "settings_index.csv")
    assert index["recipe_sha256"].nunique() == 1
    assert not (out / "mixed_settings.txt").exists()


def test_settings_hash_ignores_timestamps_but_not_thresholds():
    recipe = load_recipe(_recipe())
    original = recipe.content_hash()
    recipe.modified_at = "2030-01-01T00:00:00+00:00"
    recipe.software_version = "9.9.9"
    recipe.recipe_name = "renamed"
    assert recipe.content_hash() == original
    recipe.classifications[0].threshold = 201
    assert recipe.content_hash() != original


def test_changed_threshold_is_still_reported_as_mixed_settings(tmp_path: Path):
    paths = []
    for index in range(2):
        path = tmp_path / f"s{index}.tif"
        _write_squares(path)
        paths.append(path)
    controller = AnalysisController.create(tmp_path / "experiment", "Mixed")
    controller.add_image_paths(paths)
    controller.set_recipe(_recipe())
    controller.run_images()
    first = controller.experiment.images[0].image_id
    controller.update_thresholds(first, {"class_a": 900})
    out = controller.export(tmp_path / "out")
    assert (out / "mixed_settings.txt").exists()
    # Exporting again after the settings match removes the stale note.
    controller.update_thresholds(first, {"class_a": 200})
    controller.export(out)
    assert not (out / "mixed_settings.txt").exists()


def test_batch_keeps_only_a_few_images_in_memory_and_edits_still_work(tmp_path: Path, monkeypatch):
    paths = []
    for index in range(5):
        path = tmp_path / f"s{index}.tif"
        _write_squares(path)
        paths.append(path)
    controller = AnalysisController.create(tmp_path / "experiment", "Memory")
    controller.add_image_paths(paths)
    controller.set_recipe(_recipe())
    controller.run_images()
    assert len(controller._session_images) <= SESSION_IMAGE_LIMIT

    evicted = controller.experiment.images[0].image_id  # first image was pushed out
    assert evicted not in controller._session_images
    monkeypatch.setattr("cellquant.controller.file_fingerprint", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("rehash")))
    first = controller.last_results[evicted]
    object_id = int(first.objects["object_id"].iloc[0])
    deleted = controller.delete_object(evicted, object_id)
    fresh = process_image(paths[0], controller.recipe, image_id=evicted, manual_edits=controller.edits[evicted])
    assert int(deleted.objects["excluded"].sum()) == 1
    assert deleted.objects["object_id"].tolist() == fresh.objects["object_id"].tolist()
    assert deleted.objects["marker_a_mean"].tolist() == fresh.objects["marker_a_mean"].tolist()


def test_group_summary_uses_sample_sd_and_blank_for_one_image():
    frame = pd.DataFrame(
        {
            "Group": ["x", "x", "y"],
            "report_1_percent": [50.0, 100.0, 40.0],
            "report_1_count": [1, 1, 2],
            "report_1_denominator": [2, 1, 5],
        }
    )
    grouped = grouped_summary(frame, "Group").set_index("Group")
    assert grouped.loc["x", "report_1_sd_of_per_image_percent"] == pytest.approx(np.std([50, 100], ddof=1))
    assert np.isnan(grouped.loc["y", "report_1_sd_of_per_image_percent"])


def test_group_summary_from_an_older_version_is_flagged_not_silently_blank():
    old = pd.DataFrame(
        {
            "Group": ["x", "x"],
            "report_1_percent": [50.0, 100.0],
            "report_1_count": [1, 1],
            "report_1_denominator": ["all_objects", "all_objects"],  # older versions stored the text
        }
    )
    row = grouped_summary(old, "Group").iloc[0]
    assert np.isnan(row["report_1_pooled_percent"])
    assert row["report_1_mean_of_per_image_percent"] == pytest.approx(75)
    assert "older version" in row["summary_note"]
