"""Discovery adapters for the three review entry scopes.

All three adapters read metadata only: run manifests, ``review.json``, and TIFF
headers. No source volume is opened, so previewing a large queue stays cheap.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from cellquant.review.constants import (
    CONFIG_NAME,
    IMPORTED_RUN_KIND,
    LABELS_DRAFT_NAME,
    LABELS_NAME,
    LABELS_REVIEWED_NAME,
    LABEL_TIFF_SUFFIXES,
    NATIVE_RUN_KIND,
    PROVENANCE_NAME,
    REVIEWED_MASKS_DIR_NAME,
    REVIEWS_DIR,
    STATUS_NAME,
)
from cellquant.review.labels import looks_like_label_tiff
from cellquant.review.state import load_review_state

RUN_SUFFIX = ".cellquant"


class DiscoveryError(ValueError):
    """Raised when a selection cannot be interpreted as a review scope."""


@dataclass(frozen=True)
class DiscoveredItem:
    """One reviewable image, with enough state to render the queue preview."""

    kind: str
    path: Path
    run_dir: Path | None = None
    mask_path: Path | None = None
    source: str | None = None
    run_id: str | None = None
    run_kind: str = NATIVE_RUN_KIND
    review_status: str = "pending"
    queue_status: str = "active"
    revision: int = 0
    note: str = ""
    blocked_reason: str | None = None
    needs_source: bool = False
    needs_confirmation: bool = False
    batch_status: str | None = None

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def identity(self) -> str:
        return str(self.path.resolve()).casefold()

    @property
    def is_reviewable(self) -> bool:
        """False for entries that are visible but cannot be edited or approved."""

        return self.blocked_reason is None and not self.needs_source


def _read_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise DiscoveryError(f"expected a JSON object: {path}")
    return raw


def run_kind_of(run_dir: str | Path) -> str:
    """Read ``run_kind`` from a run's configuration without validating it."""

    config = Path(run_dir) / CONFIG_NAME
    if not config.is_file():
        return NATIVE_RUN_KIND
    try:
        raw = _read_json(config)
    except (OSError, json.JSONDecodeError, DiscoveryError):
        return NATIVE_RUN_KIND
    return str(raw.get("run_kind") or NATIVE_RUN_KIND)


def is_cellquant_run(path: str | Path) -> bool:
    candidate = Path(path)
    return candidate.is_dir() and candidate.suffix.lower() == RUN_SUFFIX


def run_blocked_reason(run_dir: str | Path) -> str | None:
    """Why a run cannot be reviewed, or ``None`` when it is usable."""

    root = Path(run_dir)
    if not root.is_dir():
        return f"missing run directory {root}"
    if not (root / LABELS_NAME).is_file():
        return f"no {LABELS_NAME} in {root.name}"
    status_path = root / STATUS_NAME
    if status_path.is_file():
        try:
            status = _read_json(status_path)
        except (OSError, json.JSONDecodeError, DiscoveryError) as exc:
            return f"unreadable {STATUS_NAME}: {exc}"
        state = str(status.get("status") or "")
        if state and state != "complete":
            return f"run status is {state!r}, not complete"
    if not (root / CONFIG_NAME).is_file():
        return f"no {CONFIG_NAME} in {root.name}"
    return None


def is_complete_run(path: str | Path) -> bool:
    root = Path(path)
    if not is_cellquant_run(root):
        return False
    if run_blocked_reason(root) is not None:
        return False
    return (root / PROVENANCE_NAME).is_file()


def is_review_artifact(path: str | Path) -> bool:
    """True for masks review itself generates, which must not be rediscovered."""

    candidate = Path(path)
    if candidate.name in {LABELS_DRAFT_NAME, LABELS_REVIEWED_NAME}:
        return True
    parts = {part.casefold() for part in candidate.parts}
    if REVIEWS_DIR.casefold() in parts or REVIEWED_MASKS_DIR_NAME.casefold() in parts:
        return True
    stem = candidate.stem.casefold()
    return stem.startswith("r") and len(stem) == 7 and stem[1:].isdigit()


def owning_run(path: str | Path) -> Path | None:
    """The ``.cellquant`` run directory containing ``path``, if any."""

    candidate = Path(path)
    for parent in (candidate, *candidate.parents):
        if is_cellquant_run(parent):
            return parent
    return None


def _item_from_run(run_dir: Path, *, batch_status: str | None = None) -> DiscoveredItem:
    root = Path(run_dir)
    blocked = run_blocked_reason(root)
    source: str | None = None
    run_id: str | None = None
    provenance = root / PROVENANCE_NAME
    if provenance.is_file():
        try:
            payload = _read_json(provenance)
            source = str(payload.get("source") or "") or None
            run_id = str(payload.get("run_id") or "") or None
        except (OSError, json.JSONDecodeError, DiscoveryError) as exc:
            blocked = blocked or f"unreadable {PROVENANCE_NAME}: {exc}"
    if blocked is None and source and not Path(source).is_file():
        blocked = f"source image missing: {source}"
    load = load_review_state(root)
    record = load.record
    kind = run_kind_of(root)
    return DiscoveredItem(
        kind="imported_run" if kind == IMPORTED_RUN_KIND else "run",
        path=root,
        run_dir=root,
        mask_path=root / LABELS_NAME,
        source=source,
        run_id=run_id,
        run_kind=kind,
        review_status=record.review_status if record is not None else "pending",
        queue_status=record.queue_status if record is not None else "active",
        revision=record.revision if record is not None else 0,
        note=record.note if record is not None else "",
        blocked_reason=blocked or load.blocked_reason,
        needs_confirmation=bool(record is not None and record.requires_confirmation),
        batch_status=batch_status,
    )


def _item_from_standalone(mask: Path) -> DiscoveredItem:
    return DiscoveredItem(
        kind="standalone_mask",
        path=mask,
        run_dir=None,
        mask_path=mask,
        source=None,
        review_status="pending",
        needs_source=True,
    )


def discover_single(target: str | Path) -> DiscoveredItem:
    """Resolve a single selection: a run, a mask inside a run, or a bare TIFF.

    Selecting ``labels.tif``, ``labels_reviewed.tif``, or a published revision
    inside a run resolves the owning run so the existing review record is reused
    rather than imported again as a new segmentation.
    """

    candidate = Path(target).expanduser()
    if not candidate.exists():
        raise DiscoveryError(f"selection does not exist: {candidate}")
    if candidate.is_dir():
        if is_cellquant_run(candidate):
            return _item_from_run(candidate)
        raise DiscoveryError(
            f"{candidate} is not a .cellquant run directory; choose a run, a label "
            "TIFF, or use the folder scope"
        )
    owner = owning_run(candidate)
    if owner is not None:
        return _item_from_run(owner)
    if candidate.suffix.lower() not in LABEL_TIFF_SUFFIXES:
        raise DiscoveryError(
            f"{candidate.name} is not a .tif/.tiff label export. Cellpose _seg.npy "
            "files, outline-only exports, and RGB renderings are not accepted; export "
            "integer-label TIFFs instead."
        )
    if not looks_like_label_tiff(candidate):
        raise DiscoveryError(
            f"{candidate.name} does not look like an integer instance-label TIFF. "
            "Export integer labels (background 0, positive object IDs) rather than an "
            "intensity image or RGB rendering."
        )
    return _item_from_standalone(candidate)


def discover_folder(
    root: str | Path,
    *,
    recursive: bool = True,
) -> tuple[DiscoveredItem, ...]:
    """Discover runs and standalone label TIFFs under ``root``.

    The selected directory itself is included when it is a run. Run artifacts are
    deduplicated (a run contributes one item, not one per mask file) and review
    outputs are never rediscovered as new images.
    """

    base = Path(root).expanduser()
    if not base.is_dir():
        raise DiscoveryError(f"not a directory: {base}")
    items: list[DiscoveredItem] = []
    seen: set[str] = set()

    runs: list[Path] = []
    if is_cellquant_run(base):
        runs.append(base)
    candidates: Iterable[Path] = base.rglob("*") if recursive else base.glob("*")
    files: list[Path] = []
    for entry in sorted(candidates, key=lambda item: str(item).casefold()):
        if entry.is_dir():
            if is_cellquant_run(entry):
                runs.append(entry)
            continue
        files.append(entry)

    run_roots = {str(path.resolve()).casefold() for path in runs}
    for run in runs:
        item = _item_from_run(run)
        if item.identity in seen:
            continue
        seen.add(item.identity)
        items.append(item)

    for entry in files:
        if entry.suffix.lower() not in LABEL_TIFF_SUFFIXES:
            continue
        if is_review_artifact(entry):
            continue
        owner = owning_run(entry)
        if owner is not None:
            # Runs are represented once, by the run itself.
            if str(owner.resolve()).casefold() in run_roots:
                continue
            item = _item_from_run(owner)
            if item.identity not in seen:
                seen.add(item.identity)
                items.append(item)
            continue
        if not looks_like_label_tiff(entry):
            continue
        item = _item_from_standalone(entry)
        if item.identity in seen:
            continue
        seen.add(item.identity)
        items.append(item)
    return tuple(items)


def batch_manifest_path(selection: str | Path) -> Path:
    """Accept ``batch_summary.json`` or the directory containing it."""

    candidate = Path(selection).expanduser()
    if candidate.is_dir():
        candidate = candidate / "batch_summary.json"
    if not candidate.is_file():
        raise DiscoveryError(f"batch manifest not found: {candidate}")
    return candidate


def discover_batch(selection: str | Path) -> tuple[DiscoveredItem, ...]:
    """Build queue membership from a segmentation batch manifest only.

    Neighbouring runs under the same parent directory are deliberately *not*
    included. Failed, cancelled, and missing entries stay visible with reasons so
    batch totals remain honest, but they cannot be approved.
    """

    manifest = batch_manifest_path(selection)
    payload = _read_json(manifest)
    results = payload.get("results")
    if not isinstance(results, Sequence) or isinstance(results, (str, bytes)):
        raise DiscoveryError(
            f"{manifest} has no per-file 'results' membership; choose the "
            "segmentation batch_summary.json written next to the run folders"
        )
    items: list[DiscoveredItem] = []
    seen: set[str] = set()
    for entry in results:
        if not isinstance(entry, Mapping):
            continue
        status = str(entry.get("status") or "")
        output = str(entry.get("output_dir") or "")
        source = str(entry.get("source") or "") or None
        message = str(entry.get("message") or "") or None
        run_id = str(entry.get("run_id") or "") or None
        if not output:
            items.append(
                DiscoveredItem(
                    kind="run",
                    path=manifest.parent / (source or "unknown"),
                    source=source,
                    run_id=run_id,
                    review_status="pending",
                    blocked_reason=message or f"batch entry {status or 'missing'} has no output",
                    batch_status=status or "missing",
                )
            )
            continue
        run_dir = Path(output)
        if not run_dir.is_absolute():
            run_dir = manifest.parent / run_dir
        key = str(run_dir.resolve()).casefold()
        if key in seen:
            continue
        seen.add(key)
        if status in {"completed", "resumed"} and run_dir.is_dir():
            item = _item_from_run(run_dir, batch_status=status)
            if item.run_id is None and run_id:
                item = replace(item, run_id=run_id)
            items.append(item)
            continue
        reason = message or f"batch entry status {status or 'missing'}"
        if not run_dir.is_dir():
            reason = f"{reason}; run directory missing at {run_dir}"
        items.append(
            DiscoveredItem(
                kind="run",
                path=run_dir,
                run_dir=run_dir if run_dir.is_dir() else None,
                source=source,
                run_id=run_id,
                review_status="pending",
                blocked_reason=reason,
                batch_status=status or "missing",
            )
        )
    return tuple(items)


def discover(
    scope: str,
    selection: str | Path,
    *,
    recursive: bool = True,
) -> tuple[DiscoveredItem, ...]:
    """Dispatch to the adapter for ``scope`` (``single``/``folder``/``batch``)."""

    if scope == "single":
        return (discover_single(selection),)
    if scope == "folder":
        return discover_folder(selection, recursive=recursive)
    if scope == "batch":
        return discover_batch(selection)
    raise DiscoveryError(f"unknown review scope {scope!r}")


def queue_counts(items: Sequence[DiscoveredItem]) -> dict[str, int]:
    """Approval, skip, and blocked tallies. Approved and skipped can overlap."""

    counts = {
        "total": len(items),
        "pending": 0,
        "draft": 0,
        "approved": 0,
        "rejected": 0,
        "skipped": 0,
        "blocked": 0,
    }
    for item in items:
        if item.blocked_reason is not None:
            counts["blocked"] += 1
        if item.queue_status == "skipped":
            counts["skipped"] += 1
        if item.review_status in counts:
            counts[item.review_status] += 1
    return counts


def filter_items(
    items: Sequence[DiscoveredItem],
    *,
    statuses: Iterable[str] | None = None,
) -> tuple[DiscoveredItem, ...]:
    """Filter by the queue filter vocabulary (statuses plus skipped/blocked)."""

    wanted = set(statuses) if statuses is not None else None
    if not wanted:
        return tuple(items)
    selected: list[DiscoveredItem] = []
    for item in items:
        tags = {item.review_status}
        if item.queue_status == "skipped":
            tags.add("skipped")
        if item.blocked_reason is not None:
            tags.add("blocked")
        if tags & wanted:
            selected.append(item)
    return tuple(selected)


__all__ = [
    "DiscoveredItem",
    "DiscoveryError",
    "batch_manifest_path",
    "discover",
    "discover_batch",
    "discover_folder",
    "discover_single",
    "filter_items",
    "is_cellquant_run",
    "is_complete_run",
    "is_review_artifact",
    "owning_run",
    "queue_counts",
    "run_blocked_reason",
    "run_kind_of",
]
