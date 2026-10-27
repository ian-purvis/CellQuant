"""Calls by hand: single objects called positive or negative for a marker, overriding the cutoff."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pandas as pd

from cellquant.controller import AnalysisController
from tests.test_m0_followup import _three_runs


def _calls(result, marker="class_a"):
    return result.objects.set_index("object_id")[marker].to_dict()


def test_a_call_by_hand_overrides_the_cutoff_for_that_object_only(tmp_path: Path):
    controller, ids = _three_runs(tmp_path)
    first = ids[0]
    assert _calls(controller.recall(first)) == {1: True, 2: False}
    result = controller.call_cells(first, "class_a", [2], True)
    assert _calls(result) == {1: True, 2: True}
    assert result.objects.set_index("object_id")["class_a_by_hand"].to_dict() == {1: False, 2: True}
    assert int(result.summary.iloc[0]["n_called_by_hand"]) == 1
    assert controller.cell_calls(first, "class_a") == {2: True}
    # Other images and the cutoff are untouched.
    assert controller.recipe.classifications[0].threshold_for(first) == 200
    assert "class_a_by_hand" not in controller.classified_now(ids[1]).objects.columns
    assert int(controller.classified_now(ids[1]).summary.iloc[0]["n_called_by_hand"]) == 0


def test_a_call_survives_a_cutoff_move_and_use_cutoff_gives_the_object_back(tmp_path: Path):
    controller, ids = _three_runs(tmp_path)
    first = ids[0]
    controller.call_cells(first, "class_a", [1], False)
    result = controller.update_thresholds(first, {"class_a": 10}, this_image_only=True)
    assert _calls(result) == {1: False, 2: True}  # 2 follows the cutoff, 1 keeps its call
    assert controller.positive_counts("class_a", [first]) == {first: (1, 2)}
    result = controller.call_cells(first, "class_a", [1], None)
    assert _calls(result) == {1: True, 2: True}
    assert "class_a_by_hand" not in result.objects.columns
    assert controller.cell_calls(first, "class_a") == {}


def test_undo_takes_back_one_call_at_a_time(tmp_path: Path):
    controller, ids = _three_runs(tmp_path)
    first = ids[0]
    controller.call_cells(first, "class_a", [1, 2], False)
    controller.call_cells(first, "class_a", [2], True)
    assert _calls(controller.undo_cell_calls(first)) == {1: False, 2: False}
    assert _calls(controller.undo_cell_calls(first)) == {1: True, 2: False}
    assert not controller.can_undo_cell_calls(first)
    controller.call_cells(first, "class_a", [1, 2], False)
    assert _calls(controller.clear_cell_calls(first, "class_a")) == {1: True, 2: False}
    assert _calls(controller.undo_cell_calls(first)) == {1: False, 2: False}


def test_calls_are_saved_with_the_analysis_and_dropped_when_their_object_moved(tmp_path: Path):
    controller, ids = _three_runs(tmp_path)
    first = ids[0]
    controller.call_cells(first, "class_a", [2], True)
    controller.save()
    reopened = AnalysisController.open(controller.directory)
    assert _calls(reopened.classified_now(first)) == {1: True, 2: True}
    saved = reopened.recall(first).provenance["classification_definitions"][0]
    assert saved["cell_calls"] == {first: {"2": {"positive": True, "x": saved["cell_calls"][first]["2"]["x"], "y": saved["cell_calls"][first]["2"]["y"]}}}
    # A new object under the same number elsewhere is not the object that was called.
    result = reopened.recall(first)
    objects = result.objects.copy()
    objects.loc[objects["object_id"] == 2, "centroid_x"] += 30
    reopened.last_results[first] = dataclasses.replace(result, objects=objects)
    assert reopened.cell_calls(first, "class_a") == {}


def test_calls_are_in_the_exported_tables(tmp_path: Path):
    controller, ids = _three_runs(tmp_path)
    controller.call_cells(ids[0], "class_a", [2], True)
    out = controller.export(tmp_path / "out")
    objects = pd.concat(pd.read_csv(path) for path in out.rglob("*objects*.csv"))
    called = objects.loc[objects["image_id"] == ids[0]].set_index("object_id")
    assert bool(called.loc[2, "class_a"]) and bool(called.loc[2, "class_a_by_hand"])
    summary = pd.concat(pd.read_csv(path) for path in out.rglob("*summary*.csv"))
    assert summary.set_index("image_id").loc[ids[0], "n_called_by_hand"].max() == 1


def test_a_call_clears_an_approval_only_if_the_counts_change(tmp_path: Path):
    controller, ids = _three_runs(tmp_path)
    first = ids[0]
    controller.set_status(first, "approved")
    controller.call_cells(first, "class_a", [1], True)  # the cutoff already calls it positive
    record = controller.experiment.image(first)
    assert record.processing_status == "approved"
    assert record.approved_settings_sha256 == controller._approval_fingerprint(first)
    controller.call_cells(first, "class_a", [2], True)
    assert record.processing_status == "analyzed"
    assert "called by hand" in record.last_message


def test_calls_are_kept_out_of_the_settings_hash():
    from cellquant.recipe import load_recipe
    from tests.test_workflow import _recipe

    recipe = load_recipe(_recipe())
    data = recipe.model_dump(mode="json")
    assert "cell_calls" not in data["classifications"][0]  # default left out of the saved settings
    data["classifications"][0]["cell_calls"] = {"img": {"1": {"positive": True, "x": 1.0, "y": 1.0}}}
    assert load_recipe(data).content_hash() == recipe.content_hash()
    narrowed = load_recipe(data).for_image("other")
    assert narrowed.classifications[0].cell_calls == {}


def test_step_5_counts_count_calls_by_hand(tmp_path: Path):
    controller, ids = _three_runs(tmp_path)
    first = ids[0]
    controller.call_cells(first, "class_a", [2], True)
    counts = controller.histogram(first, "marker_a_mean", 200, "above", "class_a")
    assert (counts["positive"], counts["negative"], counts["percent_positive"]) == (2, 0, 100.0)
    assert controller.histogram(first, "marker_a_mean", 200)["positive"] == 1  # the cutoff alone


def test_stale_calls_are_dropped_and_the_provenance_has_a_checksum(tmp_path: Path):
    controller, ids = _three_runs(tmp_path)
    first = ids[0]
    result = controller.call_cells(first, "class_a", [2], True)
    digest = result.provenance["hand_calls_sha256"]
    assert len(digest) == 64
    assert "hand_calls_sha256" not in controller.classified_now(ids[1]).provenance
    # Object 2 moves (run again with other objects): the next call drops its stale entry.
    objects = result.objects.copy()
    objects.loc[objects["object_id"] == 2, "centroid_x"] += 30
    controller.last_results[first] = dataclasses.replace(result, objects=objects)
    controller.call_cells(first, "class_a", [1], False)
    assert set(controller.recipe.classifications[0].cell_calls[first]) == {"1"}
    controller.call_cells(first, "class_a", [1], None)
    assert "hand_calls_sha256" not in controller.classified_now(first).provenance
