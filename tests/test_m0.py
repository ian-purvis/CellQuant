"""Milestone 0: threshold drag, unmeasured objects, and grouped summaries."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cellquant.controller import AnalysisController
from cellquant.quantify import classify_objects, generate_phenotypes, summarize_image
from cellquant.recipe import ClassificationSpec, ReportSpec
from cellquant.storage import grouped_summary
from tests.test_workflow import _recipe, _write_squares


def test_unmeasured_objects_are_not_negative():
    table = pd.DataFrame(
        {
            "object_id": [1, 2, 3],
            "centroid_x": [0, 0, 0],
            "centroid_y": [0, 0, 0],
            "area": [1, 1, 1],
            "marker": [5.0, np.nan, 1.0],
            "excluded": [False, False, False],
        }
    )
    classifications = [ClassificationSpec(id="class_a", name="A", measurement="marker", threshold=2)]
    reports = [ReportSpec(numerator="A", denominator="all_measured_objects")]
    phenotypes = generate_phenotypes(classify_objects(table, classifications), classifications)
    summary, exclusive, _combinations, _warnings = summarize_image(phenotypes, reports, classifications)
    row = summary.iloc[0]
    assert row["count"] == 1
    assert row["denominator_count"] == 2
    assert row["n_unmeasured"] == 1
    assert row["percent"] == pytest.approx(50)
    counts = exclusive.set_index("phenotype")["count"]
    assert counts["A+"] == 1
    assert counts["A-"] == 1
    # The unmeasured object is left out of every count, so no "A?" row exists.
    assert "A?" not in counts.index
    assert int(counts.sum()) == 2


def test_threshold_changes_do_not_load_or_hash(tmp_path: Path, monkeypatch):
    path = tmp_path / "sample.tif"
    _write_squares(path)
    controller = AnalysisController.create(tmp_path / "experiment", "Thresholds")
    controller.add_image_paths([path])
    controller.set_recipe(_recipe())
    image_id = controller.experiment.images[0].image_id
    controller.run_image(image_id)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("threshold changes must not read the image")

    monkeypatch.setattr("cellquant.inputs.load_image", forbidden)  # every record is loaded through cellquant.inputs
    monkeypatch.setattr("cellquant.controller.file_fingerprint", forbidden)
    for threshold in (1000, 50, 200):
        result = controller.update_thresholds(image_id, {"class_a": threshold})
        assert result is not None
    assert int(result.objects["class_a"].sum()) == 1


def test_delete_does_not_rehash_the_file(tmp_path: Path, monkeypatch):
    path = tmp_path / "sample.tif"
    _write_squares(path)
    controller = AnalysisController.create(tmp_path / "experiment", "Edits")
    controller.add_image_paths([path])
    controller.set_recipe(_recipe())
    image_id = controller.experiment.images[0].image_id
    first = controller.run_image(image_id)
    object_id = int(first.objects["object_id"].iloc[0])

    def forbidden(*_args, **_kwargs):
        raise AssertionError("an edit must not hash the file again")

    monkeypatch.setattr("cellquant.controller.file_fingerprint", forbidden)
    deleted = controller.delete_object(image_id, object_id)
    assert int(deleted.objects["excluded"].sum()) == 1


def test_group_summary_labels_mean_and_pooled():
    frame = pd.DataFrame(
        {
            "Group": ["a", "a"],
            "report_1_percent": [50.0, 100.0],
            "report_1_count": [1, 1],
            "report_1_denominator": [2, 1],
        }
    )
    grouped = grouped_summary(frame, "Group")
    row = grouped.iloc[0]
    assert row["report_1_mean_of_per_image_percent"] == pytest.approx(75)
    assert row["report_1_pooled_count"] == pytest.approx(2)
    assert row["report_1_pooled_denominator"] == pytest.approx(3)
    assert row["report_1_pooled_percent"] == pytest.approx(200 / 3)
    assert "average of each image" in row["summary_note"]
