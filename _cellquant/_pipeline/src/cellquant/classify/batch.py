"""Batch nuclear classification over existing CellQuant ``*.cellquant`` runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping, Sequence
import uuid

import numpy as np
import pandas as pd
import tifffile

from cellquant.classify import ClassificationRecipe, ClassificationResult
from cellquant.classify.store import save_classification
from cellquant.config import load_config
from cellquant.contracts import (
    LabelVolume,
    MutableCancellationToken,
    PipelineCancelled,
    PipelineEvent,
    null_event_sink,
)
from cellquant.io import inspect_volume, open_volume
from cellquant.preprocess import prepare_analysis_volume
from cellquant.survey import layout_id_for, load_survey

LABELS_NAME = "labels.tif"
LABELS_REVIEWED_NAME = "labels_reviewed.tif"
REVIEW_JSON_NAME = "review.json"
STATUS_NAME = "status.json"
PROVENANCE_NAME = "provenance.json"
CONFIG_NAME = "config.json"


@dataclass(frozen=True)
class CellQuantRunRef:
    """One complete segmentation run eligible for classification."""

    path: Path
    source: str
    layout_id: str
    channel_names: tuple[str, ...]
    has_reviewed_labels: bool
    run_id: str | None


def labels_path_for_run(run_dir: str | Path) -> Path:
    """Prefer curated labels when present; otherwise Cellpose ``labels.tif``."""

    root = Path(run_dir)
    reviewed = root / LABELS_REVIEWED_NAME
    if reviewed.is_file():
        return reviewed
    return root / LABELS_NAME


def read_run_labels(run_dir: str | Path) -> np.ndarray:
    path = labels_path_for_run(run_dir)
    if not path.is_file():
        raise FileNotFoundError(f"labels not found in CellQuant run: {run_dir}")
    value = np.asarray(tifffile.imread(path))
    if value.ndim != 3 or not np.issubdtype(value.dtype, np.integer):
        raise ValueError(f"labels must be an integer ZYX TIFF: {path}")
    return value.astype(np.uint32, copy=False)


def save_reviewed_labels(run_dir: str | Path, labels: np.ndarray, *, note: str = "") -> Path:
    """Write curated labels without overwriting original Cellpose ``labels.tif``."""

    root = Path(run_dir)
    if not root.is_dir():
        raise FileNotFoundError(root)
    array = np.asarray(labels)
    if array.ndim != 3 or not np.issubdtype(array.dtype, np.integer):
        raise ValueError("reviewed labels must be an integer ZYX array")
    if (array < 0).any():
        raise ValueError("reviewed labels must not contain negative IDs")
    maximum = int(array.max(initial=0))
    disk_dtype = np.uint16 if maximum <= np.iinfo(np.uint16).max else np.uint32
    destination = root / LABELS_REVIEWED_NAME
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        tifffile.imwrite(
            temporary,
            array.astype(disk_dtype, copy=False),
            photometric="minisblack",
            metadata={"axes": "ZYX"},
        )
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    payload = {
        "schema_version": 1,
        "reviewed_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "labels_file": LABELS_REVIEWED_NAME,
        "supersedes": LABELS_NAME,
        "note": str(note or ""),
        "label_count": int(len(np.unique(array)) - (1 if 0 in array else 0)),
        "shape": list(array.shape),
    }
    (root / REVIEW_JSON_NAME).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return destination


def _read_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"expected JSON object: {path}")
    return raw


def _is_complete_run(path: Path) -> bool:
    status_path = path / STATUS_NAME
    if not status_path.is_file():
        return False
    try:
        status = _read_json(status_path)
    except (OSError, json.JSONDecodeError, ValueError):
        return False
    if status.get("status") != "complete":
        return False
    if not (path / LABELS_NAME).is_file():
        return False
    if not (path / PROVENANCE_NAME).is_file():
        return False
    if not (path / CONFIG_NAME).is_file():
        return False
    return True


def _channel_names_from_source(source: str) -> tuple[str, ...]:
    if not source:
        return ()
    path = Path(source)
    if not path.is_file():
        return ()
    try:
        return tuple(inspect_volume(path).channel_names)
    except Exception:  # noqa: BLE001 - discovery must stay best-effort
        return ()


def _open_run_volume(source: Path, config):
    io = dict(config.raw.get("io") or {})
    spacing = io.get("spacing_override_um")
    return open_volume(
        source,
        series=int(io.get("series") or 0),
        position=int(io.get("position") or 0),
        lazy=True if io.get("lazy") is None else bool(io.get("lazy")),
        axes_override=io.get("axes_override"),
        spacing_override_um=tuple(spacing) if spacing is not None else None,
    )


def _layout_from_survey(
    run_path: Path,
    source: str,
    survey_root: Path | None,
) -> tuple[str, tuple[str, ...]] | None:
    if survey_root is None:
        # Walk parents for survey/survey.json next to batch outputs.
        for parent in [run_path.parent, *run_path.parents]:
            candidate = parent / "survey" / "survey.json"
            if candidate.is_file():
                survey_root = candidate
                break
    if survey_root is None:
        return None
    survey_path = Path(survey_root)
    if survey_path.is_dir():
        survey_path = survey_path / "survey.json"
    if not survey_path.is_file():
        return None
    try:
        survey = load_survey(survey_path)
    except Exception:  # noqa: BLE001
        return None
    source_resolved = str(Path(source).resolve()) if source else ""
    for record in survey.records:
        rec_source = str(Path(record.source).resolve()) if record.source else ""
        if record.source == source or rec_source == source_resolved:
            layout_id = record.layout_id or layout_id_for(record.channel_names)
            return layout_id, tuple(record.channel_names)
    return None


def discover_cellquant_runs(
    root: str | Path,
    *,
    survey_json: str | Path | None = None,
) -> tuple[CellQuantRunRef, ...]:
    """Find complete ``*.cellquant`` directories under ``root``."""

    base = Path(root).expanduser().resolve()
    if not base.is_dir():
        raise FileNotFoundError(base)
    survey_hint = Path(survey_json) if survey_json is not None else None
    found: list[CellQuantRunRef] = []
    for path in sorted(base.rglob("*.cellquant")):
        if not path.is_dir() or not _is_complete_run(path):
            continue
        try:
            provenance = _read_json(path / PROVENANCE_NAME)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        source = str(provenance.get("source") or "")
        mapped = _layout_from_survey(path, source, survey_hint)
        if mapped is not None:
            layout_id, channel_names = mapped
        else:
            channel_names = _channel_names_from_source(source)
            if not channel_names:
                layout_id = f"Lunknown-{path.name[:12]}"
                channel_names = ()
            else:
                layout_id = layout_id_for(channel_names)
        found.append(
            CellQuantRunRef(
                path=path,
                source=source,
                layout_id=str(layout_id),
                channel_names=tuple(channel_names),
                has_reviewed_labels=(path / LABELS_REVIEWED_NAME).is_file(),
                run_id=str(provenance.get("run_id") or "") or None,
            )
        )
    return tuple(found)


def group_runs_by_layout(
    runs: Sequence[CellQuantRunRef],
) -> dict[str, tuple[CellQuantRunRef, ...]]:
    grouped: dict[str, list[CellQuantRunRef]] = {}
    for run in runs:
        grouped.setdefault(run.layout_id, []).append(run)
    return {key: tuple(value) for key, value in grouped.items()}


def _event(
    kind: str,
    run_id: str,
    file_id: str,
    stage: str,
    *,
    current: int | None = None,
    total: int | None = None,
    **details: Any,
) -> PipelineEvent:
    return PipelineEvent(
        kind,
        run_id,
        file_id,
        stage,
        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        current=current,
        total=total,
        details=details,
    )


def classify_cellquant_run(
    run_dir: str | Path,
    recipe: ClassificationRecipe | Mapping[str, Any],
    *,
    output_root: str | Path,
    context: Mapping[str, Any] | None = None,
    cancel=None,
) -> tuple[Path, ClassificationResult, str]:
    """Classify one complete CellQuant run; return (pack_path, result, labels_used)."""

    root = Path(run_dir)
    if not _is_complete_run(root):
        raise ValueError(f"not a complete CellQuant run: {root}")
    provenance = _read_json(root / PROVENANCE_NAME)
    source = Path(str(provenance.get("source") or ""))
    if not source.is_file():
        raise FileNotFoundError(
            f"source image missing for {root.name}: {source} "
            "(keep original ND2/TIFF reachable for coexpression)"
        )
    config = load_config(root / CONFIG_NAME)
    volume = _open_run_volume(source, config)
    analysis = prepare_analysis_volume(volume, config, cancel=cancel)
    labels_path = labels_path_for_run(root)
    labels_used = "reviewed" if labels_path.name == LABELS_REVIEWED_NAME else "original"
    labels_array = read_run_labels(root)
    if labels_array.shape != analysis.data.shape[:3]:
        raise ValueError(
            f"labels shape {labels_array.shape} does not match analysis grid "
            f"{analysis.data.shape[:3]} for {root}"
        )
    labels = LabelVolume(
        labels_array,
        analysis.spacing_um,
        {
            "source_run": str(root),
            "labels_file": labels_path.name,
            "labels_used": labels_used,
            "analysis_volume": dict(analysis.metadata.get("analysis_volume") or {}),
        },
    )
    recipe_obj = (
        recipe if isinstance(recipe, ClassificationRecipe) else ClassificationRecipe(recipe)
    )
    ctx = dict(context or {})
    ctx.setdefault("image_id", source.name)
    ctx.setdefault("specimen_id", root.parent.name)
    ctx.setdefault("region_id", "whole_image")
    pack, result = save_classification(
        output_root,
        analysis,
        labels,
        recipe_obj,
        context=ctx,
        cancel=cancel,
    )
    return pack, result, labels_used


def _threshold_rows(
    recipe: ClassificationRecipe,
    *,
    image_id: str,
    layout_id: str,
    image_source: str,
    threshold_source: str,
) -> list[dict[str, Any]]:
    rows = []
    for marker in recipe.raw["markers"]:
        rows.append(
            {
                "image_id": image_id,
                "layout_id": layout_id,
                "source": image_source,
                "marker": marker["name"],
                "channel": marker.get("channel"),
                "low": marker["low"],
                "high": marker.get("high"),
                "positive_fraction": marker["positive_fraction"],
                "uncertainty_margin": marker.get("uncertainty_margin", 0),
                "threshold_source": threshold_source,
            }
        )
    return rows


def run_classify_batch(
    runs: Sequence[CellQuantRunRef],
    *,
    output_dir: str | Path,
    layout_recipes: Mapping[str, ClassificationRecipe | Mapping[str, Any]],
    image_overrides: Mapping[str, ClassificationRecipe | Mapping[str, Any]] | None = None,
    cancel=None,
    events=null_event_sink,
) -> dict[str, Path]:
    """Score many CellQuant runs; write Fiji-style summary CSVs.

    Cancellation is finalized rather than raised: the interrupted run is recorded
    as ``cancelled``, runs never reached as ``unstarted``, and every CSV plus the
    summary is written so partial results stay openable.
    """

    token = cancel or MutableCancellationToken()
    overrides = {
        str(Path(key).resolve()): (
            value if isinstance(value, ClassificationRecipe) else ClassificationRecipe(value)
        )
        for key, value in dict(image_overrides or {}).items()
    }
    recipes = {
        key: (value if isinstance(value, ClassificationRecipe) else ClassificationRecipe(value))
        for key, value in dict(layout_recipes).items()
    }
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    packs_root = destination / "runs"
    packs_root.mkdir(exist_ok=True)

    run_rows: list[dict[str, Any]] = []
    marker_rows: list[dict[str, Any]] = []
    coexpr_rows: list[dict[str, Any]] = []
    threshold_rows: list[dict[str, Any]] = []
    total = len(runs)
    cancelled_from: int | None = None

    for index, run in enumerate(runs):
        if token.cancelled:
            cancelled_from = index
            break
        resolved = str(run.path.resolve())
        events(
            _event(
                "progress",
                run.run_id or "",
                str(run.path),
                "batch",
                current=index + 1,
                total=total,
                status="running",
                message=f"Classifying {run.path.name}",
            )
        )
        recipe = overrides.get(resolved)
        threshold_source = "image_override"
        if recipe is None:
            recipe = recipes.get(run.layout_id)
            threshold_source = "layout_recipe"
        if recipe is None:
            run_rows.append(
                {
                    "run_path": str(run.path),
                    "layout_id": run.layout_id,
                    "source": run.source,
                    "labels_used": "reviewed" if run.has_reviewed_labels else "original",
                    "status": "failed",
                    "classify_pack": "",
                    "error": f"no recipe for layout {run.layout_id}",
                }
            )
            continue
        try:
            pack, result, labels_used = classify_cellquant_run(
                run.path,
                recipe,
                output_root=packs_root,
                context={
                    "image_id": Path(run.source).name if run.source else run.path.name,
                    "specimen_id": run.path.parent.name,
                    "region_id": "whole_image",
                    "calibration_group": recipe.raw.get("calibration_group") or "",
                },
                cancel=token,
            )
            image_id = Path(run.source).name if run.source else run.path.name
            for row in _threshold_rows(
                recipe,
                image_id=image_id,
                layout_id=run.layout_id,
                image_source=run.source,
                threshold_source=threshold_source,
            ):
                threshold_rows.append(row)
            # Per-marker % positive from single-marker queries.
            for _, query in result.queries.iterrows():
                positives = json.loads(query["positive"]) if isinstance(query["positive"], str) else list(query["positive"])
                negatives = json.loads(query["negative"]) if isinstance(query["negative"], str) else list(query["negative"])
                denom_pos = (
                    json.loads(query["denominator_positive"])
                    if isinstance(query["denominator_positive"], str)
                    else list(query["denominator_positive"])
                )
                if len(positives) == 1 and not negatives and not denom_pos:
                    marker_rows.append(
                        {
                            "image_id": image_id,
                            "layout_id": run.layout_id,
                            "run_path": str(run.path),
                            "marker": positives[0],
                            "percentage": query["percentage"],
                            "numerator": query["numerator"],
                            "denominator": query["denominator"],
                            "evaluable": query["evaluable"],
                            "total_eligible": query["total_eligible"],
                        }
                    )
            wide: dict[str, Any] = {
                "image_id": image_id,
                "layout_id": run.layout_id,
                "run_path": str(run.path),
                "labels_used": labels_used,
            }
            for _, query in result.queries.iterrows():
                name = str(query["name"])
                denom_pos = (
                    json.loads(query["denominator_positive"])
                    if isinstance(query["denominator_positive"], str)
                    else list(query["denominator_positive"])
                )
                # One rate label per query, named after its actual denominator.
                rate = f"{name}_pct_of_{'+'.join(denom_pos)}+" if denom_pos else f"{name}_pct_of_cells"
                wide[rate] = query["percentage"]
                wide[f"{name}_numerator"] = query["numerator"]
                wide[f"{name}_denominator"] = query["denominator"]
                wide[f"{name}_denominator_population"] = query["denominator_population"]
                wide[f"{name}_denominator_coverage_pct"] = query["denominator_coverage_pct"]
            coexpr_rows.append(wide)
            run_rows.append(
                {
                    "run_path": str(run.path),
                    "layout_id": run.layout_id,
                    "source": run.source,
                    "labels_used": labels_used,
                    "status": "completed",
                    "classify_pack": str(pack),
                    "error": "",
                }
            )
            events(
                _event(
                    "progress",
                    run.run_id or "",
                    str(run.path),
                    "batch",
                    current=index + 1,
                    total=total,
                    status="completed",
                )
            )
        except PipelineCancelled:
            # Cancellation is not a failure: record where the batch stopped.
            run_rows.append(
                {
                    "run_path": str(run.path),
                    "layout_id": run.layout_id,
                    "source": run.source,
                    "labels_used": "reviewed" if run.has_reviewed_labels else "original",
                    "status": "cancelled",
                    "classify_pack": "",
                    "error": "cancelled",
                }
            )
            events(
                _event(
                    "progress",
                    run.run_id or "",
                    str(run.path),
                    "batch",
                    current=index + 1,
                    total=total,
                    status="cancelled",
                    message="Cancelled during classification",
                )
            )
            cancelled_from = index + 1
            break
        except Exception as exc:  # noqa: BLE001 - batch must continue
            run_rows.append(
                {
                    "run_path": str(run.path),
                    "layout_id": run.layout_id,
                    "source": run.source,
                    "labels_used": "reviewed" if run.has_reviewed_labels else "original",
                    "status": "failed",
                    "classify_pack": "",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            events(
                _event(
                    "progress",
                    run.run_id or "",
                    str(run.path),
                    "batch",
                    current=index + 1,
                    total=total,
                    status="failed",
                    message=str(exc),
                )
            )

    for run in runs[cancelled_from:] if cancelled_from is not None else ():
        run_rows.append(
            {
                "run_path": str(run.path),
                "layout_id": run.layout_id,
                "source": run.source,
                "labels_used": "reviewed" if run.has_reviewed_labels else "original",
                "status": "unstarted",
                "classify_pack": "",
                "error": "cancelled before this run started",
            }
        )

    runs_path = destination / "runs.csv"
    thresholds_path = destination / "thresholds_used.csv"
    markers_path = destination / "marker_results.csv"
    coexpr_path = destination / "coexpression_summary.csv"
    summary_path = destination / "batch_summary.json"

    pd.DataFrame(run_rows).to_csv(runs_path, index=False, na_rep="NA")
    pd.DataFrame(threshold_rows).to_csv(thresholds_path, index=False, na_rep="NA")
    pd.DataFrame(marker_rows).to_csv(markers_path, index=False, na_rep="NA")
    pd.DataFrame(coexpr_rows).to_csv(coexpr_path, index=False, na_rep="NA")
    counts = {
        "total": len(run_rows),
        "completed": sum(r["status"] == "completed" for r in run_rows),
        "failed": sum(r["status"] == "failed" for r in run_rows),
        "cancelled": sum(r["status"] == "cancelled" for r in run_rows),
        "unstarted": sum(r["status"] == "unstarted" for r in run_rows),
    }
    summary_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "created_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "status": "cancelled" if cancelled_from is not None else "completed",
                **counts,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "runs": runs_path,
        "thresholds_used": thresholds_path,
        "marker_results": markers_path,
        "coexpression_summary": coexpr_path,
        "batch_summary": summary_path,
        "output_dir": destination,
    }


def load_layout_recipes(path: str | Path) -> dict[str, ClassificationRecipe]:
    """Load layout→recipe map or a single recipe applied under key ``*``."""

    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("recipe JSON must be an object")
    if "layouts" in raw and isinstance(raw["layouts"], dict):
        return {
            str(key): ClassificationRecipe(value)
            for key, value in raw["layouts"].items()
        }
    if "markers" in raw:
        return {"*": ClassificationRecipe(raw)}
    # Bare layout_id → recipe mapping
    if raw and all(isinstance(v, dict) and "markers" in v for v in raw.values()):
        return {str(key): ClassificationRecipe(value) for key, value in raw.items()}
    raise ValueError(
        "recipe JSON must be a ClassificationRecipe, {\"layouts\": {...}}, "
        "or a layout_id→recipe mapping"
    )


def load_image_overrides(path: str | Path | None) -> dict[str, ClassificationRecipe]:
    if path is None:
        return {}
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("overrides JSON must map run paths to recipes")
    return {str(key): ClassificationRecipe(value) for key, value in raw.items()}


def resolve_layout_recipes_for_runs(
    runs: Sequence[CellQuantRunRef],
    recipes: Mapping[str, ClassificationRecipe],
) -> dict[str, ClassificationRecipe]:
    """Expand a ``*`` recipe to every layout present in ``runs``."""

    if "*" in recipes and len(recipes) == 1:
        shared = recipes["*"]
        return {run.layout_id: shared for run in runs}
    return dict(recipes)


__all__ = [
    "CellQuantRunRef",
    "LABELS_NAME",
    "LABELS_REVIEWED_NAME",
    "REVIEW_JSON_NAME",
    "classify_cellquant_run",
    "discover_cellquant_runs",
    "group_runs_by_layout",
    "labels_path_for_run",
    "load_image_overrides",
    "load_layout_recipes",
    "read_run_labels",
    "resolve_layout_recipes_for_runs",
    "run_classify_batch",
    "save_reviewed_labels",
]
