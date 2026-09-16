"""``review.json`` contract: the authoritative segmentation-review state.

``review_status`` and ``queue_status`` are deliberately independent. Skipping an
image in the queue changes only ``queue_status`` and never grants, removes, or
implies approval. Load failures are reported as a separate ``blocked_reason`` by
callers and are not review statuses.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from cellquant.review.constants import (
    LABELS_DRAFT_NAME,
    LABELS_NAME,
    LABELS_REVIEWED_NAME,
    QUEUE_STATUSES,
    REVIEW_JSON_NAME,
    REVIEW_SCHEMA_VERSION,
    REVIEW_STATUSES,
)


class ReviewStateError(ValueError):
    """Raised when ``review.json`` cannot be interpreted."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ReviewRecord:
    """One image's durable review state.

    ``revision`` is the highest published revision number; ``labels_file`` is the
    run-relative path of that revision. A record can therefore carry a published
    revision while ``review_status`` is ``draft`` (the image was edited again) or
    ``rejected`` — in both cases the revision stays in history but is no longer
    selected automatically for quantification.
    """

    review_status: str = "pending"
    queue_status: str = "active"
    revision: int = 0
    labels_file: str | None = None
    supersedes: str = LABELS_NAME
    original_labels_sha256: str | None = None
    labels_sha256: str | None = None
    source: str | None = None
    analysis_context_sha256: str | None = None
    shape: tuple[int, ...] | None = None
    axes: str = "ZYX"
    spacing_um: tuple[float, float, float] | None = None
    label_count: int = 0
    reviewed_utc: str | None = None
    updated_utc: str | None = None
    note: str = ""
    rejection_reason: str | None = None
    draft_file: str | None = None
    draft_sha256: str | None = None
    history: tuple[Mapping[str, Any], ...] = ()
    schema_version: int = REVIEW_SCHEMA_VERSION
    origin: str = "v2"

    def __post_init__(self) -> None:
        if self.review_status not in REVIEW_STATUSES:
            raise ReviewStateError(
                f"review_status must be one of {list(REVIEW_STATUSES)}; "
                f"received {self.review_status!r}"
            )
        if self.queue_status not in QUEUE_STATUSES:
            raise ReviewStateError(
                f"queue_status must be one of {list(QUEUE_STATUSES)}; "
                f"received {self.queue_status!r}"
            )
        if int(self.revision) < 0:
            raise ReviewStateError("revision cannot be negative")
        if self.review_status == "rejected" and not (self.rejection_reason or "").strip():
            raise ReviewStateError("rejected reviews require a rejection reason")

    # -- derived state ---------------------------------------------------

    @property
    def is_approved(self) -> bool:
        """True only when a validated revision is durably published *and* approved."""

        return self.review_status == "approved" and bool(self.labels_file)

    @property
    def has_draft(self) -> bool:
        return bool(self.draft_file)

    @property
    def requires_confirmation(self) -> bool:
        """Legacy reviewed masks without metadata must be confirmed by a human."""

        return self.origin == "legacy_unverified"

    @property
    def is_legacy(self) -> bool:
        return self.origin != "v2"

    def payload(self) -> dict[str, Any]:
        """Canonical JSON body written to ``review.json``."""

        return {
            "schema_version": int(self.schema_version),
            "review_status": self.review_status,
            "queue_status": self.queue_status,
            "revision": int(self.revision),
            "labels_file": self.labels_file,
            "supersedes": self.supersedes,
            "original_labels_sha256": self.original_labels_sha256,
            "labels_sha256": self.labels_sha256,
            "source": self.source,
            "analysis_context_sha256": self.analysis_context_sha256,
            "shape": list(self.shape) if self.shape is not None else None,
            "axes": self.axes,
            "spacing_um": (
                [float(v) for v in self.spacing_um] if self.spacing_um is not None else None
            ),
            "label_count": int(self.label_count),
            "reviewed_utc": self.reviewed_utc,
            "updated_utc": self.updated_utc,
            "note": str(self.note or ""),
            "rejection_reason": self.rejection_reason,
            "draft_file": self.draft_file,
            "draft_sha256": self.draft_sha256,
            "history": [dict(entry) for entry in self.history],
        }

    def to_json(self) -> str:
        return json.dumps(self.payload(), indent=2, sort_keys=True) + "\n"

    @property
    def fingerprint(self) -> str:
        """Digest used to detect concurrent ``review.json`` updates."""

        canonical = json.dumps(
            self.payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def with_updates(self, **changes: Any) -> "ReviewRecord":
        return replace(self, **changes)

    def approved_labels_path(self, run_dir: str | Path) -> Path | None:
        if not self.labels_file:
            return None
        return Path(run_dir) / Path(self.labels_file)


@dataclass(frozen=True)
class ReviewLoad:
    """A record plus where it came from, so callers can report blocked items."""

    record: ReviewRecord | None
    path: Path | None = None
    blocked_reason: str | None = None
    migrated: bool = False
    raw: Mapping[str, Any] = field(default_factory=dict)


def _coerce_shape(value: Any) -> tuple[int, ...] | None:
    if value is None:
        return None
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ReviewStateError("shape must be a list of integers")
    return tuple(int(v) for v in value)


def _coerce_spacing(value: Any) -> tuple[float, float, float] | None:
    if value is None:
        return None
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 3:
        raise ReviewStateError("spacing_um must be a three-element list")
    return tuple(float(v) for v in value)  # type: ignore[return-value]


def _migrate_v1(payload: Mapping[str, Any], *, run_dir: Path | None) -> ReviewRecord:
    """Treat a schema-v1 record plus an existing reviewed TIFF as legacy approved.

    Grid validation happens in :mod:`cellquant.review.resolve`; migration only
    decides whether the legacy alias still exists. Nothing is rewritten on disk,
    so opening a folder cannot bulk-upgrade historical runs.
    """

    labels_file = str(payload.get("labels_file") or LABELS_REVIEWED_NAME)
    present = run_dir is None or (run_dir / labels_file).is_file()
    return ReviewRecord(
        review_status="approved" if present else "pending",
        queue_status="active",
        revision=0,
        labels_file=labels_file if present else None,
        supersedes=str(payload.get("supersedes") or LABELS_NAME),
        shape=_coerce_shape(payload.get("shape")),
        label_count=int(payload.get("label_count") or 0),
        reviewed_utc=payload.get("reviewed_utc"),
        updated_utc=payload.get("reviewed_utc"),
        note=str(payload.get("note") or ""),
        origin="legacy_v1",
    )


def parse_review(
    payload: Mapping[str, Any],
    *,
    run_dir: str | Path | None = None,
) -> ReviewRecord:
    """Parse a ``review.json`` body, migrating schema v1 to the v2 contract."""

    if not isinstance(payload, Mapping):
        raise ReviewStateError("review.json must contain a JSON object")
    root = Path(run_dir) if run_dir is not None else None
    version = int(payload.get("schema_version") or 0)
    if version <= 1:
        return _migrate_v1(payload, run_dir=root)
    if version > REVIEW_SCHEMA_VERSION:
        raise ReviewStateError(
            f"review.json schema_version {version} is newer than this CellQuant "
            f"build understands (max {REVIEW_SCHEMA_VERSION}); upgrade CellQuant "
            "rather than downgrading the file"
        )
    history_raw = payload.get("history") or ()
    if not isinstance(history_raw, Sequence) or isinstance(history_raw, (str, bytes)):
        raise ReviewStateError("history must be a list")
    return ReviewRecord(
        review_status=str(payload.get("review_status") or "pending"),
        queue_status=str(payload.get("queue_status") or "active"),
        revision=int(payload.get("revision") or 0),
        labels_file=payload.get("labels_file") or None,
        supersedes=str(payload.get("supersedes") or LABELS_NAME),
        original_labels_sha256=payload.get("original_labels_sha256") or None,
        labels_sha256=payload.get("labels_sha256") or None,
        source=payload.get("source") or None,
        analysis_context_sha256=payload.get("analysis_context_sha256") or None,
        shape=_coerce_shape(payload.get("shape")),
        axes=str(payload.get("axes") or "ZYX"),
        spacing_um=_coerce_spacing(payload.get("spacing_um")),
        label_count=int(payload.get("label_count") or 0),
        reviewed_utc=payload.get("reviewed_utc") or None,
        updated_utc=payload.get("updated_utc") or None,
        note=str(payload.get("note") or ""),
        rejection_reason=payload.get("rejection_reason") or None,
        draft_file=payload.get("draft_file") or None,
        draft_sha256=payload.get("draft_sha256") or None,
        history=tuple(dict(entry) for entry in history_raw if isinstance(entry, Mapping)),
    )


def load_review_state(run_dir: str | Path) -> ReviewLoad:
    """Read review state for a run, including legacy-compatibility detection."""

    root = Path(run_dir)
    manifest = root / REVIEW_JSON_NAME
    if manifest.is_file():
        try:
            raw = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return ReviewLoad(None, manifest, blocked_reason=f"unreadable review.json: {exc}")
        if not isinstance(raw, Mapping):
            return ReviewLoad(None, manifest, blocked_reason="review.json is not a JSON object")
        try:
            record = parse_review(raw, run_dir=root)
        except ReviewStateError as exc:
            return ReviewLoad(None, manifest, blocked_reason=str(exc))
        migrated = int(raw.get("schema_version") or 0) < REVIEW_SCHEMA_VERSION
        return ReviewLoad(record, manifest, migrated=migrated, raw=dict(raw))
    alias = root / LABELS_REVIEWED_NAME
    if alias.is_file():
        # A reviewed mask with no metadata cannot be trusted as approval.
        record = ReviewRecord(
            review_status="pending",
            labels_file=LABELS_REVIEWED_NAME,
            origin="legacy_unverified",
        )
        return ReviewLoad(record, None, migrated=True)
    draft = root / LABELS_DRAFT_NAME
    if draft.is_file():
        return ReviewLoad(ReviewRecord(review_status="draft", draft_file=LABELS_DRAFT_NAME), None)
    return ReviewLoad(None, None)


def load_review(run_dir: str | Path) -> ReviewRecord | None:
    """Convenience wrapper returning only the record (``None`` when absent)."""

    return load_review_state(run_dir).record


def review_status_of(run_dir: str | Path) -> str:
    record = load_review(run_dir)
    return record.review_status if record is not None else "pending"


def has_approved_labels(run_dir: str | Path) -> bool:
    """True when the run has a durably published, currently approved revision."""

    record = load_review(run_dir)
    if record is None or not record.is_approved or record.requires_confirmation:
        return False
    path = record.approved_labels_path(run_dir)
    return path is not None and path.is_file()


__all__ = [
    "ReviewLoad",
    "ReviewRecord",
    "ReviewStateError",
    "has_approved_labels",
    "load_review",
    "load_review_state",
    "parse_review",
    "review_status_of",
    "utc_now",
]
