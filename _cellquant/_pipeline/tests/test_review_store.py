"""Persistence extensions for restored calls and parent-linked review.json."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cellquant.classify.store import (
    empty_settings_result,
    make_review_record,
    reopen_classification,
    result_from_reopened,
    save_classification,
)
from tests.test_classification_store import evidence


def test_reopen_returns_verified_calls_tables(tmp_path):
    image, labels, recipe = evidence()
    path, result = save_classification(tmp_path, image, labels, recipe)
    saved = reopen_classification(path)
    pd.testing.assert_frame_equal(
        saved.calls.reset_index(drop=True),
        result.calls.reset_index(drop=True),
        check_dtype=False,
    )
    restored = result_from_reopened(saved)
    for name in ("calls", "queries", "patterns", "exclusions"):
        pd.testing.assert_frame_equal(
            getattr(restored, name).reset_index(drop=True),
            getattr(result, name).reset_index(drop=True),
            check_dtype=False,
        )
    assert saved.review is None
    assert saved.path == path
    assert saved.manifest_sha256


def test_review_json_roundtrip_preserves_parent(tmp_path):
    image, labels, recipe = evidence()
    parent, original = save_classification(tmp_path, image, labels, recipe)
    parent_bundle = reopen_classification(parent)
    review = make_review_record(
        parent_run_id=parent.name,
        parent_manifest_sha256=parent_bundle.manifest_sha256,
        computation_status="full_image",
        preview_scope="All eligible objects in saved segmentation",
        note="threshold tweak",
        reviewer="tester",
    )
    child, _ = save_classification(
        tmp_path, image, labels, recipe, review=review, result=original,
    )
    assert child != parent
    assert (parent / "complete.json").is_file()
    reopened = reopen_classification(child)
    assert reopened.review["parent_run_id"] == parent.name
    assert reopened.review["parent_manifest_sha256"] == parent_bundle.manifest_sha256
    assert reopened.review["computation_status"] == "full_image"
    assert reopened.review["note"] == "threshold tweak"
    parent_again = reopen_classification(parent)
    pd.testing.assert_frame_equal(
        parent_again.calls.reset_index(drop=True),
        original.calls.reset_index(drop=True),
        check_dtype=False,
    )


def test_settings_only_version_marks_incomplete_results(tmp_path):
    image, labels, recipe = evidence()
    parent, _ = save_classification(tmp_path, image, labels, recipe)
    parent_bundle = reopen_classification(parent)
    review = make_review_record(
        parent_run_id=parent.name,
        parent_manifest_sha256=parent_bundle.manifest_sha256,
        computation_status="settings_only",
        preview_scope="settings draft",
    )
    placeholder = empty_settings_result(recipe, context={"image_id": "x"}, image=image)
    child, saved = save_classification(
        tmp_path, image, labels, recipe, review=review, result=placeholder,
    )
    reopened = reopen_classification(child)
    assert reopened.review["computation_status"] == "settings_only"
    assert saved.metadata["computation_status"] == "settings_only"
    assert len(reopened.calls) == 0
