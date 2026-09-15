"""Discover classification and segmentation analyses without loading pixels."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Callable

from cellquant.classify.batch import discover_cellquant_runs


ProgressCallback = Callable[[str, int, int | None], None]


@dataclass(frozen=True)
class AnalysisCandidate:
    """Lightweight discovery record; arrays are not loaded."""

    path: Path
    kind: str  # classification | segmentation | incomplete | incompatible
    status: str  # available | incomplete | missing_dependencies | incompatible | unavailable
    sample_name: str
    image_id: str
    created_utc: str
    markers: tuple[str, ...]
    segmentation_mode: str
    details: dict


def _read_json_file(path: Path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON field: {key}")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError(f"nonfinite JSON number: {value}")

    return json.loads(path.read_bytes(), object_pairs_hook=unique, parse_constant=invalid)


def _is_reparse_point(path: Path) -> bool:
    try:
        return path.is_symlink()
    except OSError:
        return True


def _looks_like_classify_dir(path: Path) -> bool:
    return path.is_dir() and path.name.startswith("classify_") and len(path.name) > len("classify_")


def _skim_classification(path: Path) -> AnalysisCandidate:
    complete = path / "complete.json"
    if not complete.is_file():
        return AnalysisCandidate(
            path=path, kind="incomplete", status="incomplete",
            sample_name=path.name, image_id="", created_utc="",
            markers=(), segmentation_mode="unavailable",
            details={"reason": "missing complete.json"},
        )
    try:
        completion = _read_json_file(complete)
        if not isinstance(completion, dict) or "manifest_sha256" not in completion:
            raise ValueError("invalid completion marker")
        manifest = _read_json_file(path / "manifest.json")
        if not isinstance(manifest, dict) or manifest.get("kind") != "cellquant_classification":
            raise ValueError("unsupported manifest")
        recipe = _read_json_file(path / "recipe.json")
        context = _read_json_file(path / "context.json") if (path / "context.json").is_file() else {}
        inputs = _read_json_file(path / "inputs.json") if (path / "inputs.json").is_file() else {}
        markers = tuple(
            str(m.get("name")) for m in (recipe.get("markers") or [])
            if isinstance(m, dict) and m.get("name")
        )
        image_meta = (inputs.get("image") or {}) if isinstance(inputs, dict) else {}
        analysis_meta = image_meta.get("metadata") if isinstance(image_meta, dict) else {}
        volume = {}
        if isinstance(analysis_meta, dict):
            volume = analysis_meta.get("analysis_volume") or {}
        mode = volume.get("mode") if isinstance(volume, dict) else None
        sample = ""
        image_id = ""
        if isinstance(context, dict):
            sample = str(context.get("specimen_id") or context.get("image_id") or "")
            image_id = str(context.get("image_id") or "")
        if not sample:
            source = image_meta.get("source") if isinstance(image_meta, dict) else None
            sample = Path(str(source)).name if source else path.name
        created = str(manifest.get("created_utc") or "")
        source_path = image_meta.get("source") if isinstance(image_meta, dict) else None
        source_missing = False
        if isinstance(source_path, str) and source_path.strip():
            try:
                source_missing = not Path(source_path).expanduser().exists()
            except OSError:
                source_missing = True
        # Embedded classify packs remain openable even when the recorded source
        # path is offline; surface that as a detail, not as unavailable.
        status = "available"
        return AnalysisCandidate(
            path=path, kind="classification", status=status,
            sample_name=sample or path.name, image_id=image_id,
            created_utc=created, markers=markers,
            segmentation_mode=str(mode) if mode else "unavailable",
            details={
                "recipe_name": recipe.get("name") if isinstance(recipe, dict) else None,
                "source": source_path,
                "source_missing": source_missing,
                "manifest_created_utc": created,
                "run_id": path.name,
            },
        )
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        return AnalysisCandidate(
            path=path, kind="incompatible", status="incompatible",
            sample_name=path.name, image_id="", created_utc="",
            markers=(), segmentation_mode="unavailable",
            details={"reason": str(exc)},
        )


def _skim_segmentation(run) -> AnalysisCandidate:
    sample = Path(run.source).name if run.source else run.path.name
    return AnalysisCandidate(
        path=run.path, kind="segmentation", status="available",
        sample_name=sample, image_id=sample, created_utc="",
        markers=(), segmentation_mode="unavailable",
        details={
            "source": run.source,
            "layout_id": run.layout_id,
            "channel_names": list(run.channel_names),
            "has_reviewed_labels": run.has_reviewed_labels,
            "run_id": run.run_id,
        },
    )


def discover_classification_analyses(
    root,
    *,
    cancel=None,
    progress: ProgressCallback | None = None,
    include_segmentation: bool = True,
) -> tuple[AnalysisCandidate, ...]:
    """Find classification runs (and optional segmentation runs) under ``root``.

    Accepts an outer folder or a direct ``classify_*`` directory. Does not load
    ``.npy`` arrays. Skips symlink/reparse-point loops.
    """
    base = Path(root).expanduser().resolve()
    if cancel is not None:
        cancel.raise_if_cancelled()
    if not base.exists():
        raise FileNotFoundError(base)
    if not base.is_dir():
        raise NotADirectoryError(base)

    found: list[AnalysisCandidate] = []
    seen: set[Path] = set()

    def report(message: str, current: int, total: int | None = None):
        if progress is not None:
            progress(message, current, total)

    if _looks_like_classify_dir(base):
        report(f"Inspecting {base.name}", 0, 1)
        found.append(_skim_classification(base))
        report(f"Inspected {base.name}", 1, 1)
        return tuple(found)

    # Prefer shallow classify_* children, then a bounded recursive walk.
    classify_dirs: list[Path] = []
    stack = [base]
    visited_dirs: set[Path] = set()
    while stack:
        if cancel is not None:
            cancel.raise_if_cancelled()
        current = stack.pop()
        try:
            resolved = current.resolve()
        except OSError:
            continue
        if resolved in visited_dirs or _is_reparse_point(current):
            continue
        visited_dirs.add(resolved)
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if cancel is not None:
                cancel.raise_if_cancelled()
            if _is_reparse_point(entry):
                continue
            if _looks_like_classify_dir(entry):
                classify_dirs.append(entry)
            elif entry.is_dir() and entry.suffix != ".cellquant":
                # Stay under the selected root; do not follow cloud/junction loops.
                try:
                    if base in entry.resolve().parents or entry.resolve() == base:
                        stack.append(entry)
                except OSError:
                    continue

    total = len(classify_dirs)
    for index, path in enumerate(sorted(classify_dirs, key=lambda p: str(p).lower())):
        if cancel is not None:
            cancel.raise_if_cancelled()
        report(f"Inspecting {path.name}", index, total)
        if path.resolve() in seen:
            continue
        seen.add(path.resolve())
        found.append(_skim_classification(path))
    report("Classification scan complete", total, total)

    if include_segmentation:
        if cancel is not None:
            cancel.raise_if_cancelled()
        report("Scanning segmentation runs", 0, None)
        try:
            for run in discover_cellquant_runs(base):
                if cancel is not None:
                    cancel.raise_if_cancelled()
                candidate = _skim_segmentation(run)
                if candidate.path.resolve() in seen:
                    continue
                seen.add(candidate.path.resolve())
                found.append(candidate)
        except FileNotFoundError:
            pass
        report("Segmentation scan complete", 0, None)

    return tuple(found)


def default_population_label() -> str:
    return "All eligible objects in saved segmentation"
