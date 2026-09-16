"""Draft/approved persistence, validation, migration, and input policies."""

import json
from pathlib import Path

import numpy as np
import pytest
import tifffile

from cellquant.review import (
    LABELS_DRAFT_NAME,
    LABELS_NAME,
    LABELS_REVIEWED_NAME,
    REVIEW_JSON_NAME,
    LabelValidationError,
    ReviewConflictError,
    ReviewPublishError,
    ReviewResolutionError,
    confirm_legacy_approval,
    discard_draft,
    load_draft,
    load_review,
    parse_review,
    publish_approved,
    read_label_tiff,
    reject_review,
    resolve_labels,
    save_draft,
    set_queue_status,
    validate_label_array,
)
from cellquant.review.labels import label_array_sha256, summarize_labels


def _run(tmp_path: Path, labels: np.ndarray, *, name: str = "a") -> Path:
    run = tmp_path / f"{name}.cellquant"
    run.mkdir(parents=True)
    tifffile.imwrite(
        run / LABELS_NAME,
        labels.astype(np.uint16),
        photometric="minisblack",
        metadata={"axes": "ZYX"},
    )
    return run


def _labels() -> np.ndarray:
    data = np.zeros((2, 4, 4), np.uint32)
    data[0, 0, 0] = 1
    data[1, 3, 3] = 2
    return data


# -- label validation --------------------------------------------------


def test_validate_rejects_negative_float_and_oversized_ids():
    with pytest.raises(LabelValidationError, match="negative"):
        validate_label_array(np.array([[[-1, 0]]], dtype=np.int32))
    with pytest.raises(LabelValidationError, match="fractional"):
        validate_label_array(np.array([[[1.5, 0.0]]], dtype=np.float32))
    with pytest.raises(LabelValidationError, match="uint32"):
        validate_label_array(np.array([[[2**33, 0]]], dtype=np.int64))
    with pytest.raises(LabelValidationError, match="binary"):
        validate_label_array(np.ones((1, 2, 2), dtype=bool))
    with pytest.raises(LabelValidationError, match="2D"):
        validate_label_array(np.zeros((1, 1, 2, 2), dtype=np.uint16))


def test_two_dimensional_masks_become_singleton_z():
    promoted = validate_label_array(np.array([[1, 0], [0, 2]], dtype=np.uint16))
    assert promoted.shape == (1, 2, 2)
    assert promoted.dtype == np.uint32


def test_large_ids_survive_round_trip(tmp_path):
    labels = np.zeros((1, 2, 2), np.uint32)
    labels[0, 0, 0] = 70_000
    run = _run(tmp_path, np.zeros((1, 2, 2), np.uint32))
    result = publish_approved(run, labels, acknowledge_empty=False)
    reopened = read_label_tiff(result.revision_path)
    assert reopened.dtype == np.uint32
    assert int(reopened.max()) == 70_000
    np.testing.assert_array_equal(reopened, labels)


def test_empty_mask_requires_acknowledgement_and_reports_zero(tmp_path):
    run = _run(tmp_path, _labels())
    empty = np.zeros((2, 4, 4), np.uint32)
    with pytest.raises(ReviewPublishError, match="empty-mask"):
        publish_approved(run, empty)
    result = publish_approved(run, empty, acknowledge_empty=True)
    assert result.record.label_count == 0
    assert result.record.review_status == "approved"


# -- draft vs approved -------------------------------------------------


def test_draft_survives_restart_without_granting_approval(tmp_path):
    original = _labels()
    run = _run(tmp_path, original)
    edited = original.copy()
    edited[0, 1, 1] = 3
    record = save_draft(run, edited, note="wip")

    assert record.review_status == "draft"
    assert record.is_approved is False
    assert (run / LABELS_DRAFT_NAME).is_file()
    # A fresh read (as after a restart) still sees the draft, not approval.
    reloaded = load_review(run)
    assert reloaded.review_status == "draft"
    np.testing.assert_array_equal(load_draft(run), edited)
    # No policy selects the draft.
    for policy in ("prefer_approved", "original"):
        resolved = resolve_labels(run, policy=policy)
        assert resolved.selection == "original"
        assert resolved.path == run / LABELS_NAME
    with pytest.raises(ReviewResolutionError, match="no currently approved"):
        resolve_labels(run, policy="approved_only")
    # Original bytes are untouched.
    np.testing.assert_array_equal(
        tifffile.imread(run / LABELS_NAME), original.astype(np.uint16)
    )


def test_publish_creates_immutable_revision_and_compatibility_copy(tmp_path):
    original = _labels()
    run = _run(tmp_path, original)
    edited = original.copy()
    edited[0, 2, 2] = 5
    result = publish_approved(run, edited, note="approved")

    assert result.revision_path == run / "reviews" / "r000001.tif"
    assert result.compatibility_path == run / LABELS_REVIEWED_NAME
    assert result.record.revision == 1
    np.testing.assert_array_equal(read_label_tiff(result.revision_path), edited)

    again = edited.copy()
    again[1, 0, 0] = 6
    second = publish_approved(run, again, loaded_record=load_review(run))
    assert second.record.revision == 2
    # The first revision is immutable.
    np.testing.assert_array_equal(read_label_tiff(run / "reviews" / "r000001.tif"), edited)
    assert [entry["revision"] for entry in second.record.history] == [1, 2]
    np.testing.assert_array_equal(
        tifffile.imread(run / LABELS_NAME), original.astype(np.uint16)
    )


def test_editing_an_approved_run_creates_a_draft_that_is_not_selected(tmp_path):
    original = _labels()
    run = _run(tmp_path, original)
    approved = original.copy()
    approved[0, 1, 1] = 3
    publish_approved(run, approved)

    edited = approved.copy()
    edited[1, 1, 1] = 4
    save_draft(run, edited, loaded_record=load_review(run))
    record = load_review(run)
    assert record.review_status == "draft"
    assert record.revision == 1
    resolved = resolve_labels(run, policy="prefer_approved")
    assert resolved.selection == "original"
    assert "draft" in resolved.reason

    restored = discard_draft(run, restore_approval=True, loaded_record=record)
    assert restored.review_status == "approved"
    assert not (run / LABELS_DRAFT_NAME).exists()
    reselected = resolve_labels(run, policy="prefer_approved")
    assert reselected.selection == "reviewed"
    np.testing.assert_array_equal(read_label_tiff(reselected.path), approved)


def test_reject_requires_reason_and_excludes_from_policies(tmp_path):
    run = _run(tmp_path, _labels())
    with pytest.raises(Exception, match="reason"):
        reject_review(run, reason="   ")
    record = reject_review(run, reason="mask covers debris")
    assert record.review_status == "rejected"
    with pytest.raises(ReviewResolutionError, match="rejected"):
        resolve_labels(run, policy="prefer_approved")
    with pytest.raises(ReviewResolutionError, match="rejected"):
        resolve_labels(run, policy="original")
    explicit = resolve_labels(run, policy="original", include_rejected=True)
    assert explicit.selection == "original"
    assert "mask covers debris" in explicit.reason


def test_skip_preserves_approval_and_draft(tmp_path):
    original = _labels()
    run = _run(tmp_path, original)
    publish_approved(run, original, acknowledge_empty=True)
    record = set_queue_status(run, "skipped", loaded_record=load_review(run))
    assert record.queue_status == "skipped"
    assert record.review_status == "approved"
    # Approved-only includes approved entries skipped in the queue.
    resolved = resolve_labels(run, policy="approved_only")
    assert resolved.selection == "reviewed"
    assert resolved.queue_status == "skipped"


# -- transaction safety ------------------------------------------------


def test_concurrent_revision_conflict_is_refused(tmp_path):
    run = _run(tmp_path, _labels())
    publish_approved(run, _labels(), acknowledge_empty=True)
    opened = load_review(run)
    # Another reviewer publishes in the meantime.
    publish_approved(run, _labels(), acknowledge_empty=True, loaded_record=opened)
    with pytest.raises(ReviewConflictError, match="changed since it was opened"):
        publish_approved(run, _labels(), acknowledge_empty=True, loaded_record=opened)


def test_wrong_bound_run_and_wrong_shape_are_rejected_before_publication(tmp_path):
    run = _run(tmp_path, _labels())
    other = _run(tmp_path, _labels(), name="b")
    with pytest.raises(ReviewPublishError, match="bound to"):
        publish_approved(run, _labels(), bound_run=other)
    with pytest.raises(ReviewPublishError, match="does not match"):
        publish_approved(run, np.zeros((2, 4, 5), np.uint32), acknowledge_empty=True)
    with pytest.raises(ReviewPublishError, match="bound review grid"):
        publish_approved(run, _labels(), expected_shape=(3, 4, 4))
    assert not (run / REVIEW_JSON_NAME).exists()
    assert not (run / "reviews").exists()


def test_failed_metadata_commit_keeps_previous_state(tmp_path, monkeypatch):
    run = _run(tmp_path, _labels())
    first = publish_approved(run, _labels(), acknowledge_empty=True)
    before = (run / REVIEW_JSON_NAME).read_text(encoding="utf-8")

    import cellquant.review.persist as persist

    def boom(path, text):
        raise OSError("disk full")

    monkeypatch.setattr(persist, "_write_json_atomically", boom)
    edited = _labels()
    edited[0, 2, 2] = 9
    with pytest.raises(ReviewPublishError, match="disk full"):
        persist.publish_approved(run, edited, loaded_record=load_review(run))
    # The commit point never ran, so the previous approval is still current.
    assert (run / REVIEW_JSON_NAME).read_text(encoding="utf-8") == before
    assert load_review(run).revision == first.record.revision


def test_compatibility_copy_failure_is_recoverable(tmp_path, monkeypatch):
    run = _run(tmp_path, _labels())
    import cellquant.review.persist as persist

    real = persist.write_label_tiff
    calls = {"n": 0}

    def flaky(path, labels, **kwargs):
        if Path(path).name == LABELS_REVIEWED_NAME:
            raise OSError("read-only destination")
        calls["n"] += 1
        return real(path, labels, **kwargs)

    monkeypatch.setattr(persist, "write_label_tiff", flaky)
    result = persist.publish_approved(run, _labels(), acknowledge_empty=True)
    assert result.compatibility_path is None
    assert result.warnings and "no approval was lost" in result.warnings[0]
    # The committed revision is authoritative and still resolvable.
    resolved = resolve_labels(run, policy="approved_only")
    assert resolved.path == result.revision_path


# -- integrity and staleness -------------------------------------------


def test_corrupt_or_missing_approved_revision_never_falls_back(tmp_path):
    run = _run(tmp_path, _labels())
    result = publish_approved(run, _labels(), acknowledge_empty=True)
    result.revision_path.unlink()
    with pytest.raises(ReviewResolutionError, match="missing"):
        resolve_labels(run, policy="prefer_approved")

    result.revision_path.write_bytes(b"not a tiff")
    with pytest.raises(ReviewResolutionError, match="unreadable|not an"):
        resolve_labels(run, policy="prefer_approved")


def test_modified_revision_and_resegmented_original_are_stale(tmp_path):
    labels = _labels()
    run = _run(tmp_path, labels)
    result = publish_approved(run, labels, acknowledge_empty=True)
    tampered = labels.copy()
    tampered[1, 2, 2] = 77
    tifffile.imwrite(
        result.revision_path,
        tampered.astype(np.uint16),
        photometric="minisblack",
        metadata={"axes": "ZYX"},
    )
    with pytest.raises(ReviewResolutionError, match="does not match the digest"):
        resolve_labels(run, policy="prefer_approved")

    # Re-segmenting the original invalidates automatic reviewed selection.
    run2 = _run(tmp_path, labels, name="c")
    publish_approved(run2, labels, acknowledge_empty=True)
    resegmented = labels.copy()
    resegmented[0, 3, 0] = 8
    tifffile.imwrite(
        run2 / LABELS_NAME,
        resegmented.astype(np.uint16),
        photometric="minisblack",
        metadata={"axes": "ZYX"},
    )
    with pytest.raises(ReviewResolutionError, match="re-segmented"):
        resolve_labels(run2, policy="prefer_approved")


def test_original_policy_ignores_an_existing_approval(tmp_path):
    original = _labels()
    run = _run(tmp_path, original)
    approved = original.copy()
    approved[0, 3, 3] = 4
    publish_approved(run, approved)
    resolved = resolve_labels(run, policy="original")
    assert resolved.path == run / LABELS_NAME
    np.testing.assert_array_equal(read_label_tiff(resolved.path), original)


# -- migration and legacy ----------------------------------------------


def test_schema_v1_migrates_to_legacy_approved(tmp_path):
    labels = _labels()
    run = _run(tmp_path, labels)
    tifffile.imwrite(
        run / LABELS_REVIEWED_NAME,
        labels.astype(np.uint16),
        photometric="minisblack",
        metadata={"axes": "ZYX"},
    )
    (run / REVIEW_JSON_NAME).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "labels_file": LABELS_REVIEWED_NAME,
                "supersedes": LABELS_NAME,
                "note": "legacy",
                "label_count": 2,
                "shape": [2, 4, 4],
            }
        ),
        encoding="utf-8",
    )
    record = load_review(run)
    assert record.origin == "legacy_v1"
    assert record.review_status == "approved"
    assert record.requires_confirmation is False
    resolved = resolve_labels(run, policy="approved_only")
    assert resolved.path == run / LABELS_REVIEWED_NAME
    # Opening the folder does not rewrite the historical file.
    assert json.loads((run / REVIEW_JSON_NAME).read_text(encoding="utf-8"))["schema_version"] == 1


def test_reviewed_tiff_without_metadata_is_legacy_unverified(tmp_path):
    labels = _labels()
    run = _run(tmp_path, labels)
    tifffile.imwrite(
        run / LABELS_REVIEWED_NAME,
        labels.astype(np.uint16),
        photometric="minisblack",
        metadata={"axes": "ZYX"},
    )
    record = load_review(run)
    assert record.requires_confirmation is True
    assert record.review_status == "pending"
    with pytest.raises(ReviewResolutionError, match="legacy-unverified"):
        resolve_labels(run, policy="prefer_approved")
    result = confirm_legacy_approval(run)
    assert result.record.revision == 1
    assert resolve_labels(run, policy="approved_only").selection == "reviewed"


def test_future_schema_version_is_not_downgraded_silently():
    with pytest.raises(Exception, match="newer than this CellQuant"):
        parse_review({"schema_version": 99, "review_status": "approved"})


def test_digest_is_dtype_independent():
    small = np.zeros((1, 2, 2), np.uint16)
    small[0, 0, 0] = 5
    assert label_array_sha256(small) == label_array_sha256(small.astype(np.uint32))
    assert summarize_labels(small.astype(np.uint32)).label_count == 1
