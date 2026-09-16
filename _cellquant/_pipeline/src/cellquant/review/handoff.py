"""Quantification handoff: preflight, pinned inputs, and stale-pack detection.

Quantification resolves and *pins* its mask inputs once, at start, so edits made
during a running job cannot change what that job measures. Saved provenance
records the exact mask path, content hash, review revision/status, and whether
the original or a reviewed revision was used.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from cellquant.review.constants import (
    DEFAULT_INPUT_POLICY,
    EARLIER_MASK_REVISION_LABEL,
)
from cellquant.review.labels import label_array_sha256, read_label_tiff
from cellquant.review.resolve import (
    ResolvedLabels,
    ReviewResolutionError,
    resolve_labels,
)
from cellquant.review.state import load_review_state


@dataclass(frozen=True)
class PinnedInput:
    """One run's mask input, frozen at quantification start."""

    run_dir: Path
    labels_path: Path
    labels_sha256: str
    selection: str
    review_status: str
    queue_status: str
    revision: int
    policy: str
    reason: str = ""

    def provenance(self) -> dict[str, Any]:
        """Mask identity recorded in classification packs and batch outputs."""

        return {
            "labels_path": str(self.labels_path),
            "labels_file": self.labels_path.name,
            "labels_sha256": self.labels_sha256,
            "labels_used": self.selection,
            "labels_selection": self.selection,
            "review_revision": int(self.revision),
            "review_status": self.review_status,
            "queue_status": self.queue_status,
            "input_policy": self.policy,
        }


@dataclass(frozen=True)
class PreflightRow:
    """One row of the handoff preflight table."""

    run_dir: Path
    image: str
    review_state: str
    labels_source: str
    revision: int
    included: bool
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "run": self.run_dir.name,
            "image": self.image,
            "review_state": self.review_state,
            "labels_source": self.labels_source,
            "revision": int(self.revision),
            "included": bool(self.included),
            "reason": self.reason,
        }


def _image_label(run_dir: Path) -> str:
    load = load_review_state(run_dir)
    record = load.record
    if record is not None and record.source:
        return Path(record.source).name
    return run_dir.name


def _row_from_resolution(resolved: ResolvedLabels, image: str) -> PreflightRow:
    state = resolved.review_status
    if resolved.queue_status == "skipped":
        state = f"{state} (skipped)"
    return PreflightRow(
        run_dir=resolved.run_dir,
        image=image,
        review_state=state,
        labels_source=resolved.path.name,
        revision=resolved.revision,
        included=True,
        reason=resolved.reason,
    )


def preflight_table(
    run_dirs: Iterable[str | Path],
    *,
    policy: str = DEFAULT_INPUT_POLICY,
    include_rejected: bool = False,
) -> tuple[PreflightRow, ...]:
    """Image, review state, original/reviewed source, revision, exclusion reason.

    Exclusions are rows too: nothing disappears silently from the table.
    """

    rows: list[PreflightRow] = []
    for run_dir in run_dirs:
        root = Path(run_dir)
        image = _image_label(root)
        try:
            resolved = resolve_labels(
                root, policy=policy, include_rejected=include_rejected
            )
        except ReviewResolutionError as exc:
            load = load_review_state(root)
            record = load.record
            rows.append(
                PreflightRow(
                    run_dir=root,
                    image=image,
                    review_state=record.review_status if record is not None else "pending",
                    labels_source="",
                    revision=record.revision if record is not None else 0,
                    included=False,
                    reason=str(exc),
                )
            )
            continue
        rows.append(_row_from_resolution(resolved, image))
    return tuple(rows)


def pin_inputs(
    run_dirs: Iterable[str | Path],
    *,
    policy: str = DEFAULT_INPUT_POLICY,
    include_rejected: bool = False,
) -> tuple[tuple[PinnedInput, ...], tuple[PreflightRow, ...]]:
    """Resolve and pin every mask input, returning pins plus the full preflight.

    Rows for excluded runs are returned alongside the pins so callers can report
    why an image will not be measured.
    """

    pins: list[PinnedInput] = []
    rows: list[PreflightRow] = []
    for run_dir in run_dirs:
        root = Path(run_dir)
        image = _image_label(root)
        try:
            resolved = resolve_labels(
                root, policy=policy, include_rejected=include_rejected
            )
        except ReviewResolutionError as exc:
            load = load_review_state(root)
            record = load.record
            rows.append(
                PreflightRow(
                    run_dir=root,
                    image=image,
                    review_state=record.review_status if record is not None else "pending",
                    labels_source="",
                    revision=record.revision if record is not None else 0,
                    included=False,
                    reason=str(exc),
                )
            )
            continue
        digest = resolved.labels_sha256 or label_array_sha256(read_label_tiff(resolved.path))
        pins.append(
            PinnedInput(
                run_dir=root,
                labels_path=resolved.path,
                labels_sha256=digest,
                selection=resolved.selection,
                review_status=resolved.review_status,
                queue_status=resolved.queue_status,
                revision=resolved.revision,
                policy=resolved.policy,
                reason=resolved.reason,
            )
        )
        rows.append(_row_from_resolution(resolved, image))
    return tuple(pins), tuple(rows)


@dataclass(frozen=True)
class PackMaskState:
    """Whether a saved classification pack reflects the current mask revision."""

    pack: Path
    run_dir: Path | None
    pack_revision: int
    pack_labels_sha256: str | None
    current_revision: int
    current_labels_sha256: str | None
    stale: bool
    label: str = ""

    @property
    def offer_rerun(self) -> bool:
        return self.stale


def _pack_label_provenance(pack_dir: Path) -> Mapping[str, Any]:
    inputs = pack_dir / "inputs.json"
    if not inputs.is_file():
        return {}
    try:
        raw = json.loads(inputs.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    labels = (raw or {}).get("labels") or {}
    provenance = labels.get("provenance") or {}
    return provenance if isinstance(provenance, Mapping) else {}


def classification_pack_mask_state(pack_dir: str | Path) -> PackMaskState:
    """Compare a pack's recorded mask with its run's current review state.

    Packs are immutable snapshots. When the run has since been re-reviewed, the
    pack is labelled :data:`EARLIER_MASK_REVISION_LABEL` and a rerun is offered;
    nothing is recomputed or overwritten here.
    """

    pack = Path(pack_dir)
    provenance = _pack_label_provenance(pack)
    run_raw = str(provenance.get("source_run") or "")
    run_dir = Path(run_raw) if run_raw else None
    pack_revision = int(provenance.get("review_revision") or 0)
    pack_digest = provenance.get("labels_sha256") or provenance.get("mask_sha256") or None
    current_revision = 0
    current_digest: str | None = None
    if run_dir is not None and run_dir.is_dir():
        record = load_review_state(run_dir).record
        if record is not None:
            current_revision = int(record.revision)
            current_digest = record.labels_sha256
    stale = False
    if current_revision > pack_revision:
        stale = True
    elif (
        pack_digest
        and current_digest
        and current_revision == pack_revision
        and pack_digest != current_digest
    ):
        stale = True
    return PackMaskState(
        pack=pack,
        run_dir=run_dir,
        pack_revision=pack_revision,
        pack_labels_sha256=pack_digest,
        current_revision=current_revision,
        current_labels_sha256=current_digest,
        stale=stale,
        label=EARLIER_MASK_REVISION_LABEL if stale else "",
    )


def approved_run_dirs(items: Sequence[Any]) -> tuple[Path, ...]:
    """Run directories of queue members whose current review state is approved.

    Queue skip status does not exclude an approved entry.
    """

    selected: list[Path] = []
    for item in items:
        run_dir = getattr(item, "run_dir", None) or getattr(item, "path", None)
        if run_dir is None:
            continue
        record = load_review_state(run_dir).record
        if record is not None and record.is_approved and not record.requires_confirmation:
            selected.append(Path(run_dir))
    return tuple(selected)


__all__ = [
    "EARLIER_MASK_REVISION_LABEL",
    "PackMaskState",
    "PinnedInput",
    "PreflightRow",
    "approved_run_dirs",
    "classification_pack_mask_state",
    "pin_inputs",
    "preflight_table",
]
