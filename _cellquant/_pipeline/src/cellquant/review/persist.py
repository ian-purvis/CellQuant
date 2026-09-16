"""Durable draft and approved-revision persistence for segmentation review.

Publication is a five-step transaction whose commit point is the atomic
``review.json`` replacement:

1. validate the working labels and their binding to the run,
2. write a temporary revision TIFF and reopen-check it,
3. move it to its immutable ``reviews/rNNNNNN.tif`` name,
4. atomically replace ``review.json`` (commit),
5. refresh the compatibility ``labels_reviewed.tif`` alias.

Any failure before step 4 leaves the previously committed state intact and
raises, so the editor keeps its dirty state. A failure in step 5 is recoverable
from the committed revision and is reported as a warning rather than a loss.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping
import uuid

import numpy as np

from cellquant.persist.atomic import replace_with_retry
from cellquant.review.constants import (
    LABELS_DRAFT_NAME,
    LABELS_NAME,
    LABELS_REVIEWED_NAME,
    REVIEWS_DIR,
    REVIEW_JSON_NAME,
    REVIEW_JSON_PREVIOUS_NAME,
    REVIEW_SCHEMA_VERSION,
    revision_filename,
    revision_relative_path,
)
from cellquant.review.labels import (
    LabelValidationError,
    label_array_sha256,
    read_label_tiff,
    sha256_of_file,
    summarize_labels,
    validate_label_array,
    write_label_tiff,
)
from cellquant.review.state import (
    ReviewRecord,
    ReviewStateError,
    load_review_state,
    utc_now,
)


class ReviewConflictError(RuntimeError):
    """Raised when ``review.json`` changed under an in-progress edit.

    Publication refuses last-writer-wins overwrites: the caller must reload the
    record (and decide what to do with its edits) before retrying.
    """


class ReviewPublishError(RuntimeError):
    """Raised when a revision could not be published; nothing was committed."""


@dataclass(frozen=True)
class PublishResult:
    """Outcome of a successful :func:`publish_approved` transaction."""

    record: ReviewRecord
    revision_path: Path
    compatibility_path: Path | None
    warnings: tuple[str, ...] = ()


def _require_run_dir(run_dir: str | Path) -> Path:
    root = Path(run_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"not a CellQuant run directory: {root}")
    return root


def _original_digest(root: Path) -> str | None:
    original = root / LABELS_NAME
    if not original.is_file():
        return None
    try:
        return label_array_sha256(read_label_tiff(original))
    except (LabelValidationError, FileNotFoundError):
        return None


def _write_json_atomically(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        replace_with_retry(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _keep_previous_metadata(root: Path) -> None:
    """Retain a recoverable copy of the metadata about to be replaced."""

    manifest = root / REVIEW_JSON_NAME
    if not manifest.is_file():
        return
    try:
        (root / REVIEW_JSON_PREVIOUS_NAME).write_text(
            manifest.read_text(encoding="utf-8"), encoding="utf-8"
        )
    except OSError:
        # Losing the backup copy must not abort a publication whose revision
        # TIFF is already durable; the committed revision remains recoverable.
        pass


def _assert_no_conflict(root: Path, loaded: ReviewRecord | None) -> ReviewRecord | None:
    """Compare on-disk state with the record the editor loaded."""

    current = load_review_state(root).record
    if loaded is None:
        return current
    if current is None:
        return None
    if current.fingerprint != loaded.fingerprint:
        raise ReviewConflictError(
            f"review.json for {root.name} changed since it was opened "
            f"(on-disk revision {current.revision}, editor saw {loaded.revision}). "
            "Reload the review record before saving so another reviewer's work "
            "is not overwritten."
        )
    return current


def _validate_binding(
    root: Path,
    labels: np.ndarray,
    *,
    bound_run: str | Path | None,
    expected_shape: tuple[int, ...] | None,
) -> None:
    if bound_run is not None and Path(bound_run).resolve() != root.resolve():
        raise ReviewPublishError(
            f"labels are bound to {Path(bound_run)} but publication targets {root}; "
            "re-open the run before saving"
        )
    if expected_shape is not None and tuple(labels.shape) != tuple(expected_shape):
        raise ReviewPublishError(
            f"label shape {tuple(labels.shape)} does not match the bound review grid "
            f"{tuple(expected_shape)}"
        )
    original = root / LABELS_NAME
    if original.is_file():
        try:
            reference = read_label_tiff(original)
        except LabelValidationError:
            reference = None
        if reference is not None and reference.shape != labels.shape:
            raise ReviewPublishError(
                f"label shape {tuple(labels.shape)} does not match the run's original "
                f"mask grid {tuple(reference.shape)}; review does not resample labels"
            )


def save_draft(
    run_dir: str | Path,
    labels: np.ndarray,
    *,
    note: str = "",
    bound_run: str | Path | None = None,
    expected_shape: tuple[int, ...] | None = None,
    loaded_record: ReviewRecord | None = None,
    spacing_um: tuple[float, float, float] | None = None,
) -> ReviewRecord:
    """Persist ``labels`` as ``labels_draft.tif`` without granting approval.

    A draft never becomes a quantification input. If the run was previously
    approved, the published revision stays in history but stops being selected
    automatically until the draft is discarded or a new revision is approved.
    """

    root = _require_run_dir(run_dir)
    validated = validate_label_array(labels)
    _validate_binding(root, validated, bound_run=bound_run, expected_shape=expected_shape)
    current = _assert_no_conflict(root, loaded_record)
    summary = summarize_labels(validated)
    write_label_tiff(root / LABELS_DRAFT_NAME, validated, spacing_um=spacing_um)
    base = current or ReviewRecord()
    record = base.with_updates(
        review_status="draft",
        rejection_reason=None,
        draft_file=LABELS_DRAFT_NAME,
        draft_sha256=summary.sha256,
        shape=summary.shape,
        label_count=summary.label_count,
        note=str(note) if note else base.note,
        updated_utc=utc_now(),
        schema_version=REVIEW_SCHEMA_VERSION,
        origin="v2",
    )
    _keep_previous_metadata(root)
    _write_json_atomically(root / REVIEW_JSON_NAME, record.to_json())
    return record


def load_draft(run_dir: str | Path) -> np.ndarray | None:
    """Return the saved draft labels, or ``None`` when no draft exists."""

    root = Path(run_dir)
    record = load_review_state(root).record
    if record is None or not record.draft_file:
        return None
    path = root / record.draft_file
    if not path.is_file():
        return None
    return read_label_tiff(path)


def publish_approved(
    run_dir: str | Path,
    labels: np.ndarray,
    *,
    note: str = "",
    source: str | Path | None = None,
    spacing_um: tuple[float, float, float] | None = None,
    axes: str = "ZYX",
    analysis_context_sha256: str | None = None,
    bound_run: str | Path | None = None,
    expected_shape: tuple[int, ...] | None = None,
    loaded_record: ReviewRecord | None = None,
    acknowledge_empty: bool = False,
    keep_draft: bool = False,
) -> PublishResult:
    """Publish ``labels`` as the next immutable approved revision.

    ``loaded_record`` enables concurrent-update detection: pass the record the
    editor opened and publication refuses to overwrite a newer one. Pass
    ``acknowledge_empty=True`` to approve an all-background mask.
    """

    root = _require_run_dir(run_dir)
    validated = validate_label_array(labels)
    summary = summarize_labels(validated)
    if summary.is_empty and not acknowledge_empty:
        raise ReviewPublishError(
            "the working mask contains no objects; approve it only with an explicit "
            "empty-mask acknowledgement"
        )
    _validate_binding(root, validated, bound_run=bound_run, expected_shape=expected_shape)
    current = _assert_no_conflict(root, loaded_record)

    revision = int(current.revision if current is not None else 0) + 1
    reviews_dir = root / REVIEWS_DIR
    reviews_dir.mkdir(parents=True, exist_ok=True)
    final_revision = reviews_dir / revision_filename(revision)
    while final_revision.exists():
        # Revisions are immutable; never reuse a name already on disk.
        revision += 1
        final_revision = reviews_dir / revision_filename(revision)

    staging = reviews_dir / f".{final_revision.name}.{uuid.uuid4().hex}.staged"
    try:
        write_label_tiff(staging, validated, spacing_um=spacing_um)
        replace_with_retry(staging, final_revision)
    except Exception as exc:  # noqa: BLE001 - no commit happened; report the path
        staging.unlink(missing_ok=True)
        raise ReviewPublishError(
            f"could not write approved revision {final_revision}: {exc}"
        ) from exc

    resolved_source = str(source) if source is not None else (
        current.source if current is not None else None
    )
    history = list(current.history) if current is not None else []
    history.append(
        {
            "revision": revision,
            "labels_file": revision_relative_path(revision),
            "labels_sha256": summary.sha256,
            "label_count": summary.label_count,
            "reviewed_utc": utc_now(),
        }
    )
    timestamp = utc_now()
    record = ReviewRecord(
        review_status="approved",
        queue_status=current.queue_status if current is not None else "active",
        revision=revision,
        labels_file=revision_relative_path(revision),
        supersedes=LABELS_NAME,
        original_labels_sha256=_original_digest(root),
        labels_sha256=summary.sha256,
        source=resolved_source,
        analysis_context_sha256=analysis_context_sha256
        or (current.analysis_context_sha256 if current is not None else None),
        shape=summary.shape,
        axes=str(axes or "ZYX"),
        spacing_um=tuple(float(v) for v in spacing_um) if spacing_um is not None else None,
        label_count=summary.label_count,
        reviewed_utc=timestamp,
        updated_utc=timestamp,
        note=str(note or ""),
        rejection_reason=None,
        draft_file=(current.draft_file if (current is not None and keep_draft) else None),
        draft_sha256=(current.draft_sha256 if (current is not None and keep_draft) else None),
        history=tuple(history),
    )

    _keep_previous_metadata(root)
    try:
        _write_json_atomically(root / REVIEW_JSON_NAME, record.to_json())
    except OSError as exc:
        # The revision TIFF is durable but uncommitted, so it is not approval.
        raise ReviewPublishError(
            f"could not commit {REVIEW_JSON_NAME} for {root.name}: {exc}. "
            "The previous committed state is unchanged; your edits are still open."
        ) from exc

    warnings: list[str] = []
    compatibility: Path | None = None
    if not keep_draft:
        try:
            (root / LABELS_DRAFT_NAME).unlink(missing_ok=True)
        except OSError as exc:
            warnings.append(f"stale draft file could not be removed: {exc}")
    try:
        compatibility = write_label_tiff(
            root / LABELS_REVIEWED_NAME, validated, spacing_um=spacing_um
        )
    except Exception as exc:  # noqa: BLE001 - post-commit, recoverable
        warnings.append(
            f"approved revision {revision} is committed, but the compatibility copy "
            f"{LABELS_REVIEWED_NAME} could not be refreshed ({exc}). "
            "Readers follow review.json, so no approval was lost."
        )
    return PublishResult(record, final_revision, compatibility, tuple(warnings))


def reject_review(
    run_dir: str | Path,
    *,
    reason: str,
    loaded_record: ReviewRecord | None = None,
) -> ReviewRecord:
    """Mark the run rejected with a required reason.

    Existing revisions stay in history and existing measurements are untouched;
    the run simply stops being selected automatically for quantification.
    """

    text = str(reason or "").strip()
    if not text:
        raise ReviewStateError("Reject requires a reason.")
    root = _require_run_dir(run_dir)
    current = _assert_no_conflict(root, loaded_record)
    base = current or ReviewRecord()
    record = base.with_updates(
        review_status="rejected",
        rejection_reason=text,
        updated_utc=utc_now(),
        origin="v2",
    )
    _keep_previous_metadata(root)
    _write_json_atomically(root / REVIEW_JSON_NAME, record.to_json())
    return record


def discard_draft(
    run_dir: str | Path,
    *,
    restore_approval: bool = True,
    loaded_record: ReviewRecord | None = None,
) -> ReviewRecord:
    """Delete the saved draft and optionally restore the previous approval."""

    root = _require_run_dir(run_dir)
    current = _assert_no_conflict(root, loaded_record)
    base = current or ReviewRecord()
    (root / LABELS_DRAFT_NAME).unlink(missing_ok=True)
    restored = base.review_status
    if base.review_status == "draft":
        has_revision = bool(base.labels_file) and (root / base.labels_file).is_file()
        restored = "approved" if (restore_approval and has_revision) else "pending"
    record = base.with_updates(
        review_status=restored,
        draft_file=None,
        draft_sha256=None,
        updated_utc=utc_now(),
        origin="v2",
    )
    _keep_previous_metadata(root)
    _write_json_atomically(root / REVIEW_JSON_NAME, record.to_json())
    return record


def set_queue_status(
    run_dir: str | Path,
    status: str,
    *,
    loaded_record: ReviewRecord | None = None,
) -> ReviewRecord:
    """Change only ``queue_status``; review status and drafts are preserved."""

    root = _require_run_dir(run_dir)
    current = _assert_no_conflict(root, loaded_record)
    base = current or ReviewRecord()
    record = base.with_updates(queue_status=str(status), updated_utc=utc_now(), origin="v2")
    _keep_previous_metadata(root)
    _write_json_atomically(root / REVIEW_JSON_NAME, record.to_json())
    return record


def set_note(
    run_dir: str | Path,
    note: str,
    *,
    loaded_record: ReviewRecord | None = None,
) -> ReviewRecord:
    """Persist an image-level note. Notes never change the mask revision."""

    root = _require_run_dir(run_dir)
    current = _assert_no_conflict(root, loaded_record)
    base = current or ReviewRecord()
    record = base.with_updates(note=str(note or ""), updated_utc=utc_now(), origin="v2")
    _keep_previous_metadata(root)
    _write_json_atomically(root / REVIEW_JSON_NAME, record.to_json())
    return record


def confirm_legacy_approval(
    run_dir: str | Path,
    *,
    note: str = "",
) -> PublishResult:
    """Promote a metadata-less ``labels_reviewed.tif`` to a real revision.

    Legacy-unverified masks are never treated as approval implicitly; this is the
    explicit confirmation path. The original ``labels.tif`` is left untouched.
    """

    root = _require_run_dir(run_dir)
    alias = root / LABELS_REVIEWED_NAME
    if not alias.is_file():
        raise ReviewPublishError(f"no {LABELS_REVIEWED_NAME} to confirm in {root}")
    labels = read_label_tiff(alias)
    return publish_approved(
        root,
        labels,
        note=note or "confirmed legacy reviewed mask",
        acknowledge_empty=True,
    )


def previous_metadata(run_dir: str | Path) -> Mapping[str, Any] | None:
    """Read the retained previous ``review.json`` body, if any."""

    path = Path(run_dir) / REVIEW_JSON_PREVIOUS_NAME
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, Mapping) else None


def revision_digest(run_dir: str | Path, record: ReviewRecord) -> str | None:
    """File digest of the record's published revision, for integrity reporting."""

    path = record.approved_labels_path(run_dir)
    if path is None or not path.is_file():
        return None
    return sha256_of_file(path)


__all__ = [
    "PublishResult",
    "ReviewConflictError",
    "ReviewPublishError",
    "confirm_legacy_approval",
    "discard_draft",
    "load_draft",
    "previous_metadata",
    "publish_approved",
    "reject_review",
    "revision_digest",
    "save_draft",
    "set_note",
    "set_queue_status",
]
