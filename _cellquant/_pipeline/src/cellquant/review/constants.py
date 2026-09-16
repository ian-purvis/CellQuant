"""File names, schema versions, and vocabularies shared by segmentation review."""

from __future__ import annotations

from typing import Final

# Run-directory artifacts. ``labels.tif`` is never overwritten by review.
LABELS_NAME: Final = "labels.tif"
LABELS_DRAFT_NAME: Final = "labels_draft.tif"
LABELS_REVIEWED_NAME: Final = "labels_reviewed.tif"
REVIEW_JSON_NAME: Final = "review.json"
REVIEW_JSON_PREVIOUS_NAME: Final = "review.previous.json"
REVIEWS_DIR: Final = "reviews"

# Native run artifacts review reads but never rewrites.
CONFIG_NAME: Final = "config.json"
PROVENANCE_NAME: Final = "provenance.json"
STATUS_NAME: Final = "status.json"

# Session persistence for folder/batch queues.
REVIEW_SESSION_NAME: Final = "review_session.json"
REVIEWED_MASKS_DIR_NAME: Final = "reviewed_masks"

REVIEW_SCHEMA_VERSION: Final = 2
SESSION_SCHEMA_VERSION: Final = 1
IMPORT_CONFIG_SCHEMA_VERSION: Final = 1

#: ``review_status`` vocabulary. Independent of :data:`QUEUE_STATUSES`.
REVIEW_STATUSES: Final = ("pending", "draft", "approved", "rejected")
#: ``queue_status`` vocabulary. Skipping never grants or removes approval.
QUEUE_STATUSES: Final = ("active", "skipped")
#: Quantification input policies, in UI order.
INPUT_POLICIES: Final = ("prefer_approved", "approved_only", "original")
DEFAULT_INPUT_POLICY: Final = "prefer_approved"

#: Record provenance for records that predate schema v2.
RECORD_ORIGINS: Final = ("v2", "legacy_v1", "legacy_unverified")

#: Run kinds understood by the shared run loader.
NATIVE_RUN_KIND: Final = "native"
IMPORTED_RUN_KIND: Final = "imported_labels"

#: Origin recorded for masks imported from an external Cellpose TIFF export.
IMPORT_ORIGIN_CELLPOSE_TIFF: Final = "cellpose_tiff"

#: Shown next to classification packs measured from a superseded mask revision.
EARLIER_MASK_REVISION_LABEL: Final = "Measurements use an earlier mask revision"

#: Exact top-level mode label registered by the napari plugin.
REVIEW_MODE_LABEL: Final = "Segmentation Review/QC"
REVIEW_MODE_KEY: Final = "segmentation_review"

LABEL_TIFF_SUFFIXES: Final = (".tif", ".tiff")


def revision_filename(revision: int) -> str:
    """Immutable revision file name, e.g. ``r000001.tif``."""

    value = int(revision)
    if value < 1:
        raise ValueError("revision numbers start at 1")
    return f"r{value:06d}.tif"


def revision_relative_path(revision: int) -> str:
    """Run-relative POSIX path of an immutable revision."""

    return f"{REVIEWS_DIR}/{revision_filename(revision)}"


__all__ = [
    "CONFIG_NAME",
    "DEFAULT_INPUT_POLICY",
    "EARLIER_MASK_REVISION_LABEL",
    "IMPORTED_RUN_KIND",
    "IMPORT_CONFIG_SCHEMA_VERSION",
    "IMPORT_ORIGIN_CELLPOSE_TIFF",
    "INPUT_POLICIES",
    "LABELS_DRAFT_NAME",
    "LABELS_NAME",
    "LABELS_REVIEWED_NAME",
    "LABEL_TIFF_SUFFIXES",
    "NATIVE_RUN_KIND",
    "PROVENANCE_NAME",
    "QUEUE_STATUSES",
    "RECORD_ORIGINS",
    "REVIEWED_MASKS_DIR_NAME",
    "REVIEWS_DIR",
    "REVIEW_JSON_NAME",
    "REVIEW_JSON_PREVIOUS_NAME",
    "REVIEW_MODE_KEY",
    "REVIEW_MODE_LABEL",
    "REVIEW_SCHEMA_VERSION",
    "REVIEW_SESSION_NAME",
    "REVIEW_STATUSES",
    "SESSION_SCHEMA_VERSION",
    "STATUS_NAME",
    "revision_filename",
    "revision_relative_path",
]
