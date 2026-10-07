"""Count area: only objects whose centroid is inside the drawn polygons are counted."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from cellquant.controller import AnalysisController
from cellquant.pipeline import apply_count_area, process_image, reclassify_result

from tests.test_workflow import _recipe, _write_squares

# Around the first square (rows/columns 10-19) only; the second square is at 40-49.
TOP_LEFT = [[[0, 0], [0, 30], [30, 30], [30, 0]]]


def _counts(result) -> tuple[int, float]:
    row = result.summary.iloc[0]
    return int(row["total_objects"]), float(row["report_1_percent"])


def test_objects_outside_are_kept_marked_and_not_counted(tmp_path: Path):
    path = tmp_path / "image.tif"
    _write_squares(path)
    whole = process_image(path, _recipe())
    assert "in_count_area" not in whole.objects.columns
    assert "count_area" not in whole.summary.columns
    assert _counts(whole) == (2, 50.0)

    inside = process_image(path, _recipe(), count_area=TOP_LEFT)
    assert len(inside.objects) == 2  # nothing dropped from the object table
    assert inside.objects.sort_values("object_id")["in_count_area"].tolist() == [True, False]
    assert _counts(inside) == (1, 100.0)
    assert 900 <= inside.summary.iloc[0]["count_area"] <= 961  # px², no pixel size
    assert inside.provenance["count_area"] == [[[float(r), float(c)] for r, c in TOP_LEFT[0]]]
    # New cutoffs still count only inside the area.
    assert _counts(reclassify_result(inside, _recipe())) == (1, 100.0)


def test_apply_count_area_matches_a_fresh_run_and_clears(tmp_path: Path):
    path = tmp_path / "image.tif"
    _write_squares(path)
    whole = process_image(path, _recipe())
    applied = apply_count_area(whole, TOP_LEFT)
    fresh = process_image(path, _recipe(), count_area=TOP_LEFT)
    pd.testing.assert_frame_equal(applied.summary, fresh.summary)
    assert applied.qc.n_objects == 1

    # A polygon holding no object centroid: nothing counted, and the checks say why.
    empty = apply_count_area(whole, [[[60, 0], [60, 5], [70, 5], [70, 0]]])
    assert empty.qc.n_objects == 0
    assert "No objects are inside the count area." in empty.qc.warnings

    cleared = apply_count_area(applied, [])
    assert "in_count_area" not in cleared.objects.columns
    assert "count_area" not in cleared.provenance
    assert _counts(cleared) == (2, 50.0)


def test_controller_sets_one_image_or_all_and_exports(tmp_path: Path):
    first, second = tmp_path / "a.tif", tmp_path / "b.tif"
    _write_squares(first)
    _write_squares(second)
    controller = AnalysisController.create(tmp_path / "experiment", "Demo")
    controller.add_image_paths([first, second])
    controller.set_recipe(_recipe())
    controller.run_images()
    ids = [record.image_id for record in controller.experiment.images]
    controller.set_status(ids[0], "approved")

    controller.set_count_area([ids[0]], TOP_LEFT)
    assert controller.experiment.image(ids[0]).processing_status == "analyzed"  # approval cleared
    assert _counts(controller.last_results[ids[0]]) == (1, 100.0)
    assert _counts(controller.last_results[ids[1]]) == (2, 50.0)
    assert controller.histogram(ids[0], "marker_a_mean", 200)["negative"] == 0

    controller.set_count_area(ids, TOP_LEFT)
    exported = controller.export(tmp_path / "export")
    summary = pd.read_csv(exported / "image_summary.csv")
    assert summary["total_objects"].tolist() == [1, 1]
    assert summary["count_area"].notna().all()
    objects = pd.read_csv(exported / "objects.csv")
    assert sorted(objects["in_count_area"].tolist()) == [False, False, True, True]

    # Saved with the experiment; a rerun keeps counting inside it.
    reopened = AnalysisController.open(tmp_path / "experiment")
    assert reopened.experiment.image(ids[1]).count_area
    assert _counts(reopened.recall(ids[0])) == (1, 100.0)
    assert _counts(reopened.run_image(ids[1])) == (1, 100.0)

    reopened.set_count_area([ids[1]], [])
    assert _counts(reopened.last_results[ids[1]]) == (2, 50.0)
