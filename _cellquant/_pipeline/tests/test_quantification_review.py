"""Discovery, explanations, comparison, and review session helpers."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cellquant.classify import ClassificationRecipe, classify_labels
from cellquant.classify.compare import compare_marker_calls
from cellquant.classify.discover_classify import discover_classification_analyses
from cellquant.classify.explain import explain_marker_call
from cellquant.classify.review_session import ReviewSession, ReviewState
from cellquant.classify.store import reopen_classification, save_classification, make_review_record
from cellquant.contracts import MutableCancellationToken, PipelineCancelled
from tests.test_classification_store import evidence


def test_discover_outer_folder_one_and_many(tmp_path):
    image, labels, recipe = evidence()
    outer = tmp_path / "analyses"
    outer.mkdir()
    only, _ = save_classification(outer, image, labels, recipe)
    found = discover_classification_analyses(outer)
    available = [c for c in found if c.kind == "classification" and c.status == "available"]
    assert len(available) == 1
    assert available[0].path == only

    second, _ = save_classification(outer, image, labels, recipe)
    found = discover_classification_analyses(outer)
    available = [c for c in found if c.kind == "classification" and c.status == "available"]
    assert {c.path for c in available} == {only, second}

    direct = discover_classification_analyses(only)
    assert len(direct) == 1 and direct[0].path == only


def test_discover_incomplete_and_cancel(tmp_path):
    incomplete = tmp_path / "classify_deadbeefcafe"
    incomplete.mkdir()
    (incomplete / "recipe.json").write_text("{}", encoding="utf-8")
    found = discover_classification_analyses(tmp_path)
    assert any(c.status == "incomplete" for c in found)
    cancel = MutableCancellationToken()
    cancel.cancel()
    with pytest.raises(PipelineCancelled):
        discover_classification_analyses(tmp_path, cancel=cancel)


def test_explain_matches_boundary_and_uncertainty():
    marker = dict(name="A", channel=0, low=5, high=None, positive_fraction=0.2, uncertainty_margin=0.05)
    text = explain_marker_call(marker, {"call": "positive", "fraction": 0.24, "reason": ""})
    assert "24%" in text and "20%" in text and "positive" in text.lower()
    uncertain = explain_marker_call(marker, {"call": "uncertain", "fraction": 0.18, "reason": ""})
    assert "uncertain" in uncertain.lower()
    missing = explain_marker_call(marker, {"call": "missing", "fraction": np.nan, "reason": "not_acquired"})
    assert "not acquired" in missing.lower()


def test_compare_net_and_changed_object_counts_differ():
    baseline = pd.DataFrame({
        "label": [1, 2, 3, 4],
        "marker": ["A"] * 4,
        "call": ["positive", "negative", "positive", "uncertain"],
    })
    # One + and one - swap keeps net 0 while two cells changed; plus one more positive.
    proposed = pd.DataFrame({
        "label": [1, 2, 3, 4],
        "marker": ["A"] * 4,
        "call": ["negative", "positive", "positive", "positive"],
    })
    comparison = compare_marker_calls(
        baseline, proposed, "A",
        population_label="All eligible objects in saved segmentation",
    )
    assert comparison.comparable
    assert comparison.net_positive_change == 1  # 2 -> 3
    assert comparison.changed_object_count == 3
    assert comparison.net_positive_change != comparison.changed_object_count
    lines = "\n".join(comparison.summary_lines())
    assert "Population: All eligible objects" in lines


def test_compare_mismatched_population_and_missing_baseline():
    baseline = pd.DataFrame({"label": [1, 2], "marker": ["A", "A"], "call": ["positive", "negative"]})
    proposed = pd.DataFrame({"label": [1, 3], "marker": ["A", "A"], "call": ["positive", "negative"]})
    mismatch = compare_marker_calls(baseline, proposed, "A", population_label="pop")
    assert not mismatch.comparable
    assert "not a direct" in mismatch.message
    missing = compare_marker_calls(None, proposed, "A", population_label="pop")
    assert not missing.baseline_available
    assert "Baseline calls unavailable" in missing.message


def test_review_session_revert_and_stale_preview(tmp_path):
    image, labels, recipe = evidence()
    path, result = save_classification(tmp_path, image, labels, recipe)
    bundle = reopen_classification(path)
    session = ReviewSession.from_reopened(bundle)
    assert session.state == ReviewState.SAVED_SETTINGS
    altered = ClassificationRecipe({**recipe.raw, "markers": [
        {**recipe.raw["markers"][0], "positive_fraction": 0.9},
        recipe.raw["markers"][1],
    ]})
    session.set_draft_recipe(altered)
    assert session.dirty
    assert session.state in (ReviewState.UNSAVED_CHANGES, ReviewState.PREVIEW_OUT_OF_DATE)
    revision = session.begin_compute()
    assert session.accept_preview(revision, result)
    assert session.state == ReviewState.READY_TO_SAVE
    assert not session.accept_preview(revision - 1, result)
    session.revert_to_saved()
    assert not session.dirty
    assert session.state == ReviewState.SAVED_SETTINGS


def test_parent_linked_full_save_from_session(tmp_path):
    image, labels, recipe = evidence()
    parent, _ = save_classification(tmp_path, image, labels, recipe)
    bundle = reopen_classification(parent)
    session = ReviewSession.from_reopened(bundle)
    altered = ClassificationRecipe({**recipe.raw, "markers": [
        {**recipe.raw["markers"][0], "positive_fraction": 0.9},
        recipe.raw["markers"][1],
    ]})
    review = make_review_record(
        parent_run_id=session.parent_run_id,
        parent_manifest_sha256=session.parent_manifest_sha256,
        computation_status="full_image",
        preview_scope=session.population_label,
    )
    child, _ = save_classification(
        tmp_path, image, labels, altered, review=review,
    )
    saved = reopen_classification(child)
    assert saved.review["parent_run_id"] == parent.name
    assert saved.recipe.fingerprint == altered.fingerprint
    assert reopen_classification(parent).recipe.fingerprint == recipe.fingerprint
