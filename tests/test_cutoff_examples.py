"""Teach by clicking: objects marked positive or negative set a marker's cutoff."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import numpy as np
import pytest

from cellquant.controller import AnalysisController
from cellquant.cutoff_examples import disagreeing, suggest_cutoff
from cellquant.errors import CellQuantError
from tests.test_m0_followup import _three_runs


def test_suggested_cutoff_sits_in_the_widest_gap_that_calls_the_most_examples_right():
    assert suggest_cutoff([800, 600], [20, 100]) == 350
    assert suggest_cutoff([800], []) is None
    assert suggest_cutoff([], [20]) is None
    assert suggest_cutoff([5], [5]) is None
    # 300 marked negative among positives: one example must be wrong; the widest such gap wins.
    assert suggest_cutoff([800, 700, 250], [20, 30, 300]) == pytest.approx(500)
    assert suggest_cutoff([np.nan, 800], [20]) == 410
    # "at least": a value equal to the cutoff is positive, the midpoint still separates them.
    assert suggest_cutoff([10.0], [0.0], "at_least") == 5.0
    assert disagreeing({1: True, 2: False, 3: True}, {1: 800, 2: 20, 3: np.nan}, 900) == [1]


def test_marking_examples_sets_this_images_cutoff_only(tmp_path: Path):
    controller, ids = _three_runs(tmp_path)
    first = ids[0]
    controller.set_cutoff_by("class_a", "examples")
    assert controller.recipe.classifications[0].cutoff_by == "examples"
    controller.mark_example("class_a", first, 1, True)
    assert controller.recipe.classifications[0].threshold_for(first) == 200  # one example: no suggestion yet
    result = controller.mark_example("class_a", first, 2, False)
    classification = controller.recipe.classifications[0]
    assert classification.threshold_for(first) == 410  # halfway between 20 and 800
    assert classification.threshold == 200 and classification.threshold_for(ids[1]) == 200
    assert result.objects.set_index("object_id")["class_a"].to_dict() == {1: True, 2: False}
    assert controller.cutoff_examples("class_a", first) == {1: True, 2: False}
    assert controller.example_cutoff("class_a", first) == (410, [])


def test_the_same_mark_again_removes_it_and_the_other_mark_replaces_it(tmp_path: Path):
    controller, ids = _three_runs(tmp_path)
    first = ids[0]
    controller.mark_example("class_a", first, 1, True)
    controller.mark_example("class_a", first, 1, True)
    assert controller.cutoff_examples("class_a", first) == {}
    controller.mark_example("class_a", first, 1, True)
    controller.mark_example("class_a", first, 1, False)
    assert controller.cutoff_examples("class_a", first) == {1: False}
    controller.clear_examples("class_a", first)
    assert controller.cutoff_examples("class_a", first) == {}
    with pytest.raises(CellQuantError, match="not in this image"):
        controller.mark_example("class_a", first, 99, True)


def test_examples_that_the_cutoff_calls_the_other_way_are_listed(tmp_path: Path):
    controller, ids = _three_runs(tmp_path)
    first = ids[0]
    controller.mark_example("class_a", first, 1, True)
    controller.mark_example("class_a", first, 2, False)
    controller.update_thresholds(first, {"class_a": 900}, this_image_only=True)  # moved by hand afterwards
    assert controller.example_cutoff("class_a", first) == (410, [1])


def test_examples_are_kept_after_reopening_and_dropped_when_their_object_moved(tmp_path: Path):
    controller, ids = _three_runs(tmp_path)
    first = ids[0]
    controller.mark_example("class_a", first, 1, True)
    controller.mark_example("class_a", first, 2, False)
    controller.save()
    reopened = AnalysisController.open(controller.directory)
    assert reopened.cutoff_examples("class_a", first) == {1: True, 2: False}
    # Run again with different objects under the same numbers: object 1 is now elsewhere.
    result = reopened.recall(first)
    objects = result.objects.copy()
    objects.loc[objects["object_id"] == 1, "centroid_x"] += 30
    reopened.last_results[first] = dataclasses.replace(result, objects=objects)
    assert reopened.cutoff_examples("class_a", first) == {2: False}


def test_examples_marked_on_an_approved_image_clear_its_approval_only_if_its_percent_changes(tmp_path: Path):
    controller, ids = _three_runs(tmp_path)
    first = ids[0]
    controller.set_status(first, "approved")
    controller.mark_example("class_a", first, 1, True)
    controller.mark_example("class_a", first, 2, False)  # 200 -> 410: still 1 of 2 positive
    assert controller.cleared_approvals == []
    assert controller.experiment.image(first).processing_status == "approved"
    controller.mark_example("class_a", first, 1, False)  # both negative: no suggestion, cutoff stays
    assert controller.recipe.classifications[0].threshold_for(first) == 410
    assert controller.experiment.image(first).processing_status == "approved"


def test_the_cutoff_mode_is_kept_out_of_the_settings_hash():
    from cellquant.recipe import load_recipe
    from tests.test_workflow import _recipe

    recipe = load_recipe(_recipe())
    data = recipe.model_dump(mode="json")
    assert "cutoff_by" not in data["classifications"][0]  # default left out of the saved settings
    data["classifications"][0]["cutoff_by"] = "examples"
    assert load_recipe(data).content_hash() == recipe.content_hash()
