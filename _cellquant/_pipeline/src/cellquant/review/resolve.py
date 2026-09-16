"""Explicit mask-input policies for quantification.

Three policies, all explicit:

``prefer_approved`` (default)
    The current valid approved revision when one exists, otherwise the original
    ``labels.tif``. Rejected and blocked entries are excluded unless the caller
    opts in. Queue skip status never overrides review status.
``approved_only``
    Only validly approved entries, including approved entries that were skipped
    in the review queue.
``original``
    Original Cellpose masks even when an approval exists. Rejected entries may be
    included explicitly and are reported with their rejection reason.

Drafts are never selected by any policy. A missing, corrupt, or stale approved
revision never falls back silently: it raises
:class:`ReviewResolutionError` so the user can choose originals or repair.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from cellquant.review.constants import (
    DEFAULT_INPUT_POLICY,
    INPUT_POLICIES,
    LABELS_NAME,
    LABELS_REVIEWED_NAME,
)
from cellquant.review.labels import (
    LabelValidationError,
    label_array_sha256,
    read_label_tiff,
)
from cellquant.review.state import ReviewRecord, load_review_state


class ReviewResolutionError(RuntimeError):
    """Raised when the requested policy cannot be satisfied safely."""


@dataclass(frozen=True)
class ResolvedLabels:
    """Which mask a policy selected, and everything provenance needs to record."""

    run_dir: Path
    path: Path
    selection: str
    policy: str
    review_status: str
    queue_status: str
    revision: int
    record: ReviewRecord | None
    reason: str = ""
    compatibility_path: Path | None = None
    labels_sha256: str | None = None

    @property
    def is_reviewed(self) -> bool:
        return self.selection == "reviewed"

    @property
    def public_path(self) -> Path:
        """Path legacy callers should see.

        The ``labels_reviewed.tif`` alias is returned only when it is verified
        byte-equivalent to the committed revision; otherwise the authoritative
        revision path is returned so a stale alias can never masquerade as
        current approval.
        """

        if self.compatibility_path is not None:
            return self.compatibility_path
        return self.path


def _normalize_policy(policy: str | None) -> str:
    value = str(policy or DEFAULT_INPUT_POLICY)
    if value not in INPUT_POLICIES:
        raise ReviewResolutionError(
            f"unknown input policy {value!r}; expected one of {list(INPUT_POLICIES)}"
        )
    return value


def _verified_alias(root: Path, approved: np.ndarray) -> Path | None:
    """Return the compatibility alias only when it matches the approved revision."""

    alias = root / LABELS_REVIEWED_NAME
    if not alias.is_file():
        return None
    try:
        candidate = read_label_tiff(alias)
    except (LabelValidationError, FileNotFoundError):
        return None
    if candidate.shape != approved.shape or not np.array_equal(candidate, approved):
        return None
    return alias


def _original_labels_path(root: Path) -> Path:
    path = root / LABELS_NAME
    if not path.is_file():
        raise ReviewResolutionError(f"original labels not found in {root}")
    return path


def _validate_approved(
    root: Path,
    record: ReviewRecord,
    *,
    verify_fingerprints: bool,
) -> tuple[Path, np.ndarray, str]:
    """Load and validate the record's approved revision, or raise."""

    path = record.approved_labels_path(root)
    if path is None or not path.is_file():
        raise ReviewResolutionError(
            f"{root.name} is marked approved but its revision "
            f"{record.labels_file or '(unset)'} is missing. Choose original masks or "
            "re-review the image; CellQuant will not substitute originals silently."
        )
    try:
        labels = read_label_tiff(path)
    except LabelValidationError as exc:
        raise ReviewResolutionError(
            f"approved revision {path.name} for {root.name} is unreadable or not an "
            f"integer label mask ({exc}). Repair or re-review it, or explicitly "
            "choose original masks."
        ) from exc
    digest = label_array_sha256(labels)
    if verify_fingerprints and record.labels_sha256 and digest != record.labels_sha256:
        raise ReviewResolutionError(
            f"approved revision {path.name} for {root.name} does not match the digest "
            "recorded in review.json; it was modified outside CellQuant. Re-review it "
            "or explicitly choose original masks."
        )
    if verify_fingerprints and record.original_labels_sha256:
        original = root / LABELS_NAME
        if original.is_file():
            try:
                current_original = label_array_sha256(read_label_tiff(original))
            except LabelValidationError:
                current_original = None
            if current_original and current_original != record.original_labels_sha256:
                raise ReviewResolutionError(
                    f"{root.name} was re-segmented after approval (original mask digest "
                    "changed), so the approved revision is stale. Re-review the image or "
                    "explicitly choose original masks."
                )
    return path, labels, digest


def resolve_labels(
    run_dir: str | Path,
    *,
    policy: str = DEFAULT_INPUT_POLICY,
    include_rejected: bool = False,
    verify_fingerprints: bool = True,
) -> ResolvedLabels:
    """Resolve which mask ``policy`` selects for ``run_dir``."""

    root = Path(run_dir)
    chosen = _normalize_policy(policy)
    load = load_review_state(root)
    record = load.record
    if load.blocked_reason:
        raise ReviewResolutionError(
            f"review state for {root.name} could not be read: {load.blocked_reason}"
        )
    review_status = record.review_status if record is not None else "pending"
    queue_status = record.queue_status if record is not None else "active"
    revision = record.revision if record is not None else 0

    if chosen == "original":
        if review_status == "rejected" and not include_rejected:
            raise ReviewResolutionError(
                f"{root.name} was rejected"
                f"{f' ({record.rejection_reason})' if record and record.rejection_reason else ''}"
                "; include rejected entries explicitly to quantify its original masks"
            )
        reason = "original masks requested explicitly"
        if record is not None and record.rejection_reason:
            reason = f"rejected: {record.rejection_reason}"
        path = _original_labels_path(root)
        return ResolvedLabels(
            run_dir=root,
            path=path,
            selection="original",
            policy=chosen,
            review_status=review_status,
            queue_status=queue_status,
            revision=revision,
            record=record,
            reason=reason,
        )

    approved = record is not None and record.is_approved and not record.requires_confirmation
    if approved:
        assert record is not None
        path, labels, digest = _validate_approved(
            root, record, verify_fingerprints=verify_fingerprints
        )
        return ResolvedLabels(
            run_dir=root,
            path=path,
            selection="reviewed",
            policy=chosen,
            review_status=review_status,
            queue_status=queue_status,
            revision=revision,
            record=record,
            reason=f"approved revision {revision}",
            compatibility_path=_verified_alias(root, labels),
            labels_sha256=digest,
        )

    if chosen == "approved_only":
        detail = review_status
        if record is not None and record.requires_confirmation:
            detail = "legacy reviewed mask awaiting confirmation"
        elif review_status == "rejected" and record is not None and record.rejection_reason:
            detail = f"rejected: {record.rejection_reason}"
        raise ReviewResolutionError(
            f"{root.name} has no currently approved mask revision ({detail}); "
            "it is excluded under the Approved only policy"
        )

    # prefer_approved with no approval: originals, unless rejected/blocked.
    if review_status == "rejected" and not include_rejected:
        raise ReviewResolutionError(
            f"{root.name} was rejected"
            f"{f' ({record.rejection_reason})' if record and record.rejection_reason else ''}"
            "; rejected entries are excluded from Prefer approved"
        )
    if record is not None and record.requires_confirmation:
        raise ReviewResolutionError(
            f"{root.name} has a {LABELS_REVIEWED_NAME} with no review metadata "
            "(legacy-unverified). Confirm it in Segmentation Review/QC, or choose "
            "original Cellpose masks explicitly."
        )
    reason = "no approved revision; using original masks"
    if review_status == "draft":
        reason = "draft edits are never quantified; using original masks"
    path = _original_labels_path(root)
    return ResolvedLabels(
        run_dir=root,
        path=path,
        selection="original",
        policy=chosen,
        review_status=review_status,
        queue_status=queue_status,
        revision=revision,
        record=record,
        reason=reason,
    )


def resolve_labels_path(
    run_dir: str | Path,
    *,
    policy: str = DEFAULT_INPUT_POLICY,
    include_rejected: bool = False,
) -> Path:
    """Path of the mask ``policy`` selects (compatibility-friendly form)."""

    return resolve_labels(
        run_dir, policy=policy, include_rejected=include_rejected
    ).public_path


def read_resolved_labels(
    run_dir: str | Path,
    *,
    policy: str = DEFAULT_INPUT_POLICY,
    include_rejected: bool = False,
) -> tuple[np.ndarray, ResolvedLabels]:
    """Read the selected mask and return it with its resolution record."""

    resolved = resolve_labels(run_dir, policy=policy, include_rejected=include_rejected)
    return read_label_tiff(resolved.path), resolved


def describe_resolution(resolved: ResolvedLabels) -> str:
    """One-line preflight description for the handoff table and status bar."""

    if resolved.is_reviewed:
        return f"reviewed · revision {resolved.revision} · {resolved.path.name}"
    return f"original · {resolved.path.name} ({resolved.reason})"


__all__ = [
    "ResolvedLabels",
    "ReviewResolutionError",
    "describe_resolution",
    "read_resolved_labels",
    "resolve_labels",
    "resolve_labels_path",
]
