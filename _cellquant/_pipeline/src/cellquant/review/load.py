"""UI-independent loading of a review workspace for one run.

Reconstructs the exact analysis grid segmentation ran on — series/position,
channel mapping, axes, spacing, and Z selection — so masks and image pixels are
aligned by construction rather than by matching array shapes. Native and
imported runs share this path; imported runs declare their grid explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from cellquant.analysis import (
    analysis_context_from_dict,
    analysis_context_identity,
    analysis_context_summary,
)
from cellquant.config import ImportedRunConfig, load_run_config
from cellquant.contracts import ImageVolume, LabelVolume
from cellquant.io import open_volume
from cellquant.preprocess import prepare_analysis_volume
from cellquant.review.constants import LABELS_NAME, PROVENANCE_NAME
from cellquant.review.labels import label_array_sha256, read_label_tiff
from cellquant.review.resolve import ReviewResolutionError
from cellquant.review.state import ReviewRecord, load_review_state


class ReviewLoadError(RuntimeError):
    """Raised when a run cannot be opened for review."""


@dataclass(frozen=True)
class ReviewWorkspace:
    """Everything an editor needs, with its binding identity already computed."""

    run_dir: Path
    source: Path
    analysis: ImageVolume
    original_labels: np.ndarray
    working_labels: LabelVolume
    working_origin: str
    record: ReviewRecord | None
    context_summary: str
    analysis_context_sha256: str
    generation: int = 0

    @property
    def shape(self) -> tuple[int, int, int]:
        return tuple(int(v) for v in self.working_labels.data.shape)  # type: ignore[return-value]

    @property
    def spacing_um(self) -> tuple[float, float, float]:
        return tuple(float(v) for v in self.analysis.spacing_um)  # type: ignore[return-value]


def open_run_volume(source: str | Path, config: Any) -> ImageVolume:
    """Reopen a run's source with its recorded IO parameters.

    Native and imported configurations declare the same ``io`` fields, so one
    reader serves both.
    """

    io = dict((config.raw if hasattr(config, "raw") else config).get("io") or {})
    spacing = io.get("spacing_override_um")
    return open_volume(
        Path(source),
        series=int(io.get("series") or 0),
        position=int(io.get("position") or 0),
        lazy=True if io.get("lazy") is None else bool(io.get("lazy")),
        axes_override=io.get("axes_override"),
        spacing_override_um=tuple(spacing) if spacing is not None else None,
    )


def _grid_shim(config: ImportedRunConfig) -> dict[str, Any]:
    """Present an imported ``analysis`` declaration as a segment-mode spec.

    Lets imported runs reuse ``prepare_analysis_volume`` unchanged instead of
    duplicating Z-selection logic.
    """

    analysis = dict(config.raw["analysis"])
    mode = str(analysis["mode"])
    z_index: int | None = None
    selection = analysis.get("z_selection")
    if mode == "single_plane_2d" and isinstance(selection, Mapping):
        z_index = int(selection.get("z_index", 0))
    return {"segment": {"mode": mode, "z_index": z_index}}


def _source_fingerprint(source: Path) -> str:
    """Cheap source identity: path, size, and mtime.

    Content hashing an ND2 on every queue open would dominate load time; identity
    here only needs to detect that the referenced pixels changed.
    """

    stat = source.stat()
    descriptor = {
        "name": source.name,
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }
    canonical = json.dumps(descriptor, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def analysis_context_digest(
    analysis: ImageVolume,
    *,
    config: Any,
    source: Path,
) -> str:
    """Digest of the resolved grid, configuration, and source identity."""

    context = analysis_context_from_dict(
        dict(analysis.metadata.get("analysis_volume") or {}),
        spacing_um=tuple(float(v) for v in analysis.spacing_um),
        shape_zyx=tuple(int(v) for v in analysis.data.shape[:3]),
        source=source,
        series=analysis.metadata.get("series"),
        position=analysis.metadata.get("position"),
    )
    payload = {
        "grid": analysis_context_identity(context),
        "config_fingerprint": getattr(config, "fingerprint", ""),
        "source_fingerprint": _source_fingerprint(source),
        "channel_names": list(analysis.channel_names),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def prepare_run_analysis(
    run_dir: str | Path,
    *,
    source: str | Path,
    cancel=None,
) -> tuple[Any, ImageVolume]:
    """Reopen ``source`` on the exact analysis grid the run's masks live on.

    Returns the run configuration alongside the prepared volume. Imported runs
    declare their grid in ``analysis`` and are shimmed onto the same
    ``prepare_analysis_volume`` path as native runs, so no caller needs to know
    which kind of run it is holding.
    """

    config = load_run_config(Path(run_dir) / "config.json")
    if cancel is not None:
        cancel.raise_if_cancelled()
    volume = open_run_volume(source, config)
    grid_config = _grid_shim(config) if isinstance(config, ImportedRunConfig) else config
    return config, prepare_analysis_volume(volume, grid_config, cancel=cancel)


def run_source(run_dir: str | Path) -> Path:
    """Source image recorded in the run's provenance."""

    root = Path(run_dir)
    provenance = root / PROVENANCE_NAME
    if not provenance.is_file():
        raise ReviewLoadError(f"no {PROVENANCE_NAME} in {root}")
    try:
        payload = json.loads(provenance.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewLoadError(f"unreadable {provenance}: {exc}") from exc
    source = str((payload or {}).get("source") or "")
    if not source:
        raise ReviewLoadError(f"{provenance} records no source image")
    return Path(source)


def load_review_workspace(
    run_dir: str | Path,
    *,
    prefer_draft: bool = True,
    generation: int = 0,
    cancel=None,
) -> ReviewWorkspace:
    """Open one run's aligned image, original mask, and working mask.

    The working mask is the saved draft when one exists (and ``prefer_draft``),
    otherwise the approved revision, otherwise the original. A draft is offered
    for editing but is never a quantification input.
    """

    root = Path(run_dir)
    source = run_source(root)
    if not source.is_file():
        raise ReviewLoadError(
            f"source image missing for {root.name}: {source}. Relink the source before "
            "reviewing; CellQuant will not reinterpret the grid to make shapes match."
        )
    config, analysis = prepare_run_analysis(root, source=source, cancel=cancel)
    analysis_meta = dict(analysis.metadata)
    analysis_meta["classification_analysis_grid"] = True
    analysis = replace(analysis, metadata=analysis_meta)

    original = read_label_tiff(root / LABELS_NAME)
    expected = tuple(int(v) for v in analysis.data.shape[:3])
    if tuple(original.shape) != expected:
        raise ReviewLoadError(
            f"labels shape {tuple(original.shape)} does not match the reconstructed "
            f"analysis grid {expected} for {root.name}"
        )

    load = load_review_state(root)
    record = load.record
    working = original
    origin = "original"
    if prefer_draft and record is not None and record.draft_file:
        draft_path = root / record.draft_file
        if draft_path.is_file():
            candidate = read_label_tiff(draft_path)
            if tuple(candidate.shape) != expected:
                raise ReviewLoadError(
                    f"saved draft for {root.name} has shape {tuple(candidate.shape)}, "
                    f"which no longer matches the analysis grid {expected}"
                )
            working = candidate
            origin = "draft"
    if origin == "original" and record is not None and record.is_approved:
        approved = record.approved_labels_path(root)
        if approved is not None and approved.is_file():
            candidate = read_label_tiff(approved)
            if tuple(candidate.shape) != expected:
                raise ReviewResolutionError(
                    f"approved revision {approved.name} for {root.name} has shape "
                    f"{tuple(candidate.shape)}, which does not match the analysis grid "
                    f"{expected}; re-review the image"
                )
            working = candidate
            origin = "approved"

    digest = analysis_context_digest(analysis, config=config, source=source)
    context = analysis_context_from_dict(
        dict(analysis.metadata.get("analysis_volume") or {}),
        spacing_um=tuple(float(v) for v in analysis.spacing_um),
        shape_zyx=expected,
        source=source,
    )
    labels = LabelVolume(
        np.array(working, copy=True),
        analysis.spacing_um,
        {
            "source_run": str(root.resolve()),
            "review": True,
            "review_generation": int(generation),
            "review_working_origin": origin,
            "original_labels_sha256": label_array_sha256(original),
            "analysis_context_sha256": digest,
            "analysis_volume": dict(analysis.metadata.get("analysis_volume") or {}),
        },
    )
    return ReviewWorkspace(
        run_dir=root,
        source=source,
        analysis=analysis,
        original_labels=original,
        working_labels=labels,
        working_origin=origin,
        record=record,
        context_summary=analysis_context_summary(context),
        analysis_context_sha256=digest,
        generation=int(generation),
    )


__all__ = [
    "ReviewLoadError",
    "ReviewWorkspace",
    "analysis_context_digest",
    "load_review_workspace",
    "open_run_volume",
    "prepare_run_analysis",
    "run_source",
]
