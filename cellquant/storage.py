"""Run folders and per-image artifacts.

Source images are not written. The run snapshot of the recipe stays unchanged
after the run starts. Later threshold edits update the image provenance, which
records the recipe that produced that table.
"""

from __future__ import annotations

import hashlib
import json
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import tifffile

from cellquant.__version__ import __version__
from cellquant.edits import EditOperation
from cellquant.experiment import Experiment, ImageRecord
from cellquant.pipeline import ImageResult
from cellquant.recipe import Recipe, save_recipe


@dataclass
class RunRecord:
    run_id: str
    experiment_id: str
    recipe_id: str | None
    software_version: str
    start_timestamp: str
    completion_timestamp: str | None = None
    input_files: list[str] = field(default_factory=list)
    input_hashes: dict[str, str] = field(default_factory=dict)
    processing_status: str = "running"
    manual_edits: dict[str, list] = field(default_factory=dict)
    recipe_hashes: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "experiment_id": self.experiment_id,
            "recipe_id": self.recipe_id,
            "software_version": self.software_version,
            "start_timestamp": self.start_timestamp,
            "completion_timestamp": self.completion_timestamp,
            "input_files": self.input_files,
            "input_hashes": self.input_hashes,
            "processing_status": self.processing_status,
            "manual_edits": self.manual_edits,
            "recipe_hashes": self.recipe_hashes,
            "warnings": self.warnings,
            "errors": self.errors,
        }


class RunLog:
    """Human-readable run log, plus a developer log that may contain tracebacks."""

    def __init__(self, run_dir: Path):
        log_dir = run_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.path = log_dir / "run.log"
        self.developer_path = log_dir / "developer.log"

    def write(self, image: str, stage: str, message: str, exc: BaseException | None = None) -> None:
        line = f"{_now()}\t{image}\t{stage}\t{message}\n"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)
        if exc is not None:
            detail = "".join(traceback.format_exception(exc))
            with self.developer_path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.write(detail)
                if not detail.endswith("\n"):
                    handle.write("\n")


def start_run(experiment: Experiment, recipe: Recipe, images: list[ImageRecord]) -> tuple[RunRecord, Path]:
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    run_dir = Path(experiment.directory) / "runs" / run_id
    for relative in ("logs", "labels", "measurements", "classifications", "summaries", "provenance", "edits"):
        (run_dir / relative).mkdir(parents=True, exist_ok=True)
    record = RunRecord(
        run_id=run_id,
        experiment_id=experiment.experiment_id,
        recipe_id=recipe.recipe_id,
        software_version=__version__,
        start_timestamp=_now(),
        input_files=[image.source_path for image in images],
        input_hashes={image.image_id: image.content_hash for image in images},
    )
    save_recipe(recipe, run_dir / "recipe_snapshot.yaml")
    _write_run(run_dir, record)
    return record, run_dir


def finish_run(run_dir: Path, record: RunRecord, status: str) -> None:
    record.completion_timestamp = _now()
    record.processing_status = status
    _write_run(run_dir, record)


def persist_image_result(run_dir: Path, result: ImageResult, edits: list[EditOperation]) -> None:
    """Save every part of a result so ``read_persisted_result`` gives the same result back.

    Tables are CSV, with a small ``tables/<image>.json`` that records each
    column's type and which text cells were missing, so booleans, missing
    classifications and blank text survive a reload. QC, requested reports and
    marker combinations are saved as well.
    """

    image_id = _safe(str(result.provenance.get("image_id") or "image"))
    for relative in ("labels", "measurements", "classifications", "summaries", "provenance", "edits", "reports", "qc", "tables"):
        (run_dir / relative).mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(run_dir / "labels" / f"{image_id}_automated.tif", result.automated_labels.astype(np.int32))
    tifffile.imwrite(run_dir / "labels" / f"{image_id}_final.tif", result.labels.astype(np.int32))
    tables = {
        "objects": (result.objects, run_dir / "measurements" / f"{image_id}.csv"),
        "summary": (result.summary, run_dir / "summaries" / f"{image_id}.csv"),
        "phenotype_counts": (result.phenotype_counts, run_dir / "classifications" / f"{image_id}_phenotypes.csv"),
        "combination_counts": (result.combination_counts, run_dir / "classifications" / f"{image_id}_combinations.csv"),
        "reports": (result.reports, run_dir / "reports" / f"{image_id}.csv"),
    }
    described = {}
    for name, (frame, path) in tables.items():
        frame.to_csv(path, index=False)
        described[name] = describe_table(frame)
    (run_dir / "tables" / f"{image_id}.json").write_text(
        json.dumps({"format": 1, "tables": described}, indent=1, default=_json_default),
        encoding="utf-8",
    )
    (run_dir / "qc" / f"{image_id}.json").write_text(
        json.dumps(_qc_to_json(result.qc), indent=2, default=_json_default),
        encoding="utf-8",
    )
    (run_dir / "provenance" / f"{image_id}.json").write_text(
        json.dumps(result.provenance, indent=2, default=_json_default),
        encoding="utf-8",
    )
    (run_dir / "edits" / f"{image_id}.json").write_text(
        json.dumps([item.model_dump(mode="json") for item in edits], indent=2),
        encoding="utf-8",
    )


def describe_table(frame: pd.DataFrame) -> dict:
    """Column order, each column's type, and the rows where a text column was missing."""

    columns = []
    for name in frame.columns:
        series = frame[name]
        entry: dict[str, Any] = {"name": str(name), "dtype": str(series.dtype)}
        if not pd.api.types.is_numeric_dtype(series.dtype) or pd.api.types.is_bool_dtype(series.dtype):
            missing = np.flatnonzero(series.isna().to_numpy())
            if len(missing):
                entry["missing_rows"] = [int(value) for value in missing]
        columns.append(entry)
    return {"columns": columns, "rows": int(len(frame))}


def read_table(path: Path, description: dict | None) -> pd.DataFrame:
    """Read a table saved by ``persist_image_result``, restoring column types when they were recorded."""

    if not path.is_file():
        return pd.DataFrame()
    if not description:
        return pd.read_csv(path)
    columns = description.get("columns") or []
    if not columns:
        return pd.DataFrame(index=range(int(description.get("rows") or 0)))
    raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    restored = {}
    for entry in columns:
        name = entry["name"]
        text = raw[name] if name in raw.columns else pd.Series([""] * len(raw), dtype=object)
        restored[name] = _restore_column(text.to_numpy(dtype=object), entry)
    return pd.DataFrame(restored, index=range(len(raw)))


def _restore_column(values: np.ndarray, entry: dict) -> pd.Series:
    dtype_name = str(entry.get("dtype") or "object")
    missing = set(entry.get("missing_rows") or [])
    try:
        dtype = pd.api.types.pandas_dtype(dtype_name)
    except TypeError:
        dtype = None
    if dtype is not None and pd.api.types.is_bool_dtype(dtype):
        flags = [
            None if (index in missing or value == "") else str(value).strip().lower() in ("true", "1")
            for index, value in enumerate(values)
        ]
        if str(dtype) == "bool" and None not in flags:
            return pd.Series(flags, dtype=bool)
        return pd.Series(flags, dtype="boolean")
    if dtype is not None and pd.api.types.is_numeric_dtype(dtype):
        numbers = pd.to_numeric(pd.Series(values, dtype=object).replace("", np.nan), errors="coerce")
        try:
            return numbers.astype(dtype)
        except (TypeError, ValueError):
            return numbers
    items = [None if index in missing else value for index, value in enumerate(values)]
    if dtype is not None and dtype_name not in ("object",):
        try:
            return pd.Series(items, dtype=dtype)
        except (TypeError, ValueError):
            pass
    return pd.Series(items, dtype=object)


def _qc_to_json(qc) -> dict:
    return {
        "status": qc.status,
        "n_objects": int(qc.n_objects),
        "median_area": None if qc.median_area is None else float(qc.median_area),
        "fraction_touching_border": _finite_or_none(qc.fraction_touching_border),
        "percent_excluded_by_size": _finite_or_none(qc.percent_excluded_by_size),
        "warnings": list(qc.warnings),
        "errors": list(qc.errors),
    }


def _finite_or_none(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _none_to_nan(value) -> float:
    return float("nan") if value is None else float(value)


def export_run_tables(run_dir: Path, destination: Path) -> tuple[Path, Path]:
    destination.mkdir(parents=True, exist_ok=True)
    objects = _concat_csv(run_dir / "measurements", ".csv")
    summaries = _concat_csv(run_dir / "summaries", ".csv")
    object_path = destination / "objects.csv"
    summary_path = destination / "image_summary.csv"
    objects.to_csv(object_path, index=False)
    summaries.to_csv(summary_path, index=False)
    return object_path, summary_path


def grouped_summary(image_summary: pd.DataFrame, column: str) -> pd.DataFrame:
    """Per-group mean of each image's percent, and the pooled percent from counts.

    These are different numbers and are stored in differently named columns.
    The SD is the sample SD (n - 1) and is blank for a group with one image.
    The pooled percent is blank when an image's summary was written by an older
    version that stored the denominator as text.
    """

    if column not in image_summary.columns:
        raise KeyError(column)
    note = (
        "mean_of_per_image_percent is the average of each image's percent. "
        "pooled_percent is the sum of counts divided by the sum of denominators."
    )
    percent_columns = [name for name in image_summary.columns if name.endswith("_percent")]
    older_note = (
        " Pooled values are blank where an image's summary came from an older version "
        "that did not store denominator counts."
    )
    rows = []
    for name, subset in image_summary.groupby(column, dropna=False):
        row: dict[str, object] = {column: name, "n_images": int(len(subset)), "summary_note": note}
        for percent_column in percent_columns:
            prefix = percent_column[: -len("_percent")]
            values = pd.to_numeric(subset[percent_column], errors="coerce")
            row[f"{prefix}_mean_of_per_image_percent"] = float(values.mean()) if values.notna().any() else float("nan")
            row[f"{prefix}_sd_of_per_image_percent"] = (
                float(values.std(ddof=1)) if values.notna().sum() >= 2 else float("nan")
            )
            count_column = f"{prefix}_count"
            denominator_column = f"{prefix}_denominator"
            if count_column in subset.columns and denominator_column in subset.columns:
                count_values = pd.to_numeric(subset[count_column], errors="coerce")
                denominator_values = pd.to_numeric(subset[denominator_column], errors="coerce")
                if count_values.isna().any() or denominator_values.isna().any():
                    row[f"{prefix}_pooled_count"] = float("nan")
                    row[f"{prefix}_pooled_denominator"] = float("nan")
                    row[f"{prefix}_pooled_percent"] = float("nan")
                    row["summary_note"] = note + older_note
                    continue
                counts = count_values.sum()
                denominators = denominator_values.sum()
                row[f"{prefix}_pooled_count"] = float(counts)
                row[f"{prefix}_pooled_denominator"] = float(denominators)
                row[f"{prefix}_pooled_percent"] = (
                    float("nan") if denominators == 0 else 100.0 * float(counts) / float(denominators)
                )
        rows.append(row)
    return pd.DataFrame(rows)


def file_fingerprint(path: Path) -> tuple[str, str]:
    stat = path.stat()
    signature = f"{stat.st_mtime_ns}:{stat.st_size}"
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return signature, digest.hexdigest()


def load_edits(experiment_dir: Path, image_id: str) -> list[EditOperation]:
    path = experiment_dir / "edits" / f"{_safe(image_id)}.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [EditOperation.model_validate(item) for item in data]


def save_edits(experiment_dir: Path, image_id: str, edits: list[EditOperation]) -> None:
    directory = experiment_dir / "edits"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{_safe(image_id)}.json"
    path.write_text(
        json.dumps([item.model_dump(mode="json") for item in edits], indent=2),
        encoding="utf-8",
    )


def read_persisted_result(run_dir: Path, image_id: str):
    """Rebuild a result from a saved run. Returns None when files are absent.

    Results saved by this version come back complete (QC, reports, marker
    combinations and column types). Older saves are read as before: QC is
    rebuilt from the table and reports and combinations are empty.
    """

    from cellquant.pipeline import QCReport

    safe_id = _safe(image_id)
    objects_path = run_dir / "measurements" / f"{safe_id}.csv"
    labels_path = run_dir / "labels" / f"{safe_id}_final.tif"
    automated_path = run_dir / "labels" / f"{safe_id}_automated.tif"
    provenance_path = run_dir / "provenance" / f"{safe_id}.json"
    if not objects_path.is_file() or not labels_path.is_file() or not provenance_path.is_file():
        return None
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    labels = tifffile.imread(labels_path)
    automated = tifffile.imread(automated_path) if automated_path.is_file() else labels
    summary_path = run_dir / "summaries" / f"{safe_id}.csv"
    phenotype_path = run_dir / "classifications" / f"{safe_id}_phenotypes.csv"
    tables_path = run_dir / "tables" / f"{safe_id}.json"
    qc_path = run_dir / "qc" / f"{safe_id}.json"
    if tables_path.is_file() and qc_path.is_file():
        described = json.loads(tables_path.read_text(encoding="utf-8")).get("tables", {})
        objects = read_table(objects_path, described.get("objects"))
        summary = read_table(summary_path, described.get("summary"))
        phenotypes = read_table(phenotype_path, described.get("phenotype_counts"))
        combinations = read_table(run_dir / "classifications" / f"{safe_id}_combinations.csv", described.get("combination_counts"))
        reports = read_table(run_dir / "reports" / f"{safe_id}.csv", described.get("reports"))
        stored = json.loads(qc_path.read_text(encoding="utf-8"))
        qc = QCReport(
            status=str(stored["status"]),
            n_objects=int(stored["n_objects"]),
            median_area=stored.get("median_area"),
            fraction_touching_border=_none_to_nan(stored.get("fraction_touching_border")),
            percent_excluded_by_size=_none_to_nan(stored.get("percent_excluded_by_size")),
            warnings=list(stored.get("warnings") or []),
            errors=list(stored.get("errors") or []),
        )
    else:
        objects = pd.read_csv(objects_path)
        for flag in ("excluded", "unmeasured"):
            if flag in objects.columns:
                objects[flag] = objects[flag].astype(str).str.lower().isin(["true", "1"])
        summary = pd.read_csv(summary_path) if summary_path.is_file() else pd.DataFrame()
        phenotypes = pd.read_csv(phenotype_path) if phenotype_path.is_file() else pd.DataFrame()
        combinations = pd.DataFrame()
        reports = pd.DataFrame()
        active = objects
        if len(objects):
            counted = np.ones(len(objects), dtype=bool)
            for flag in ("excluded", "unmeasured"):
                if flag in objects.columns:
                    counted &= ~objects[flag].astype(bool).to_numpy()
            active = objects.loc[counted]
        warnings = list(provenance.get("warnings") or [])
        qc = QCReport(
            status="warning" if warnings else "success",
            n_objects=int(len(active)),
            median_area=None if active.empty else float(active["area"].median()),
            fraction_touching_border=float("nan"),
            percent_excluded_by_size=float("nan"),
            warnings=warnings,
        )
    return ImageResult(
        labels=np.asarray(labels, dtype=np.int32),
        automated_labels=np.asarray(automated, dtype=np.int32),
        objects=objects,
        summary=summary,
        phenotype_counts=phenotypes,
        combination_counts=combinations,
        reports=reports,
        qc=qc,
        provenance=provenance,
        spatial_unit=str(provenance.get("spatial_unit") or "px"),
    )


def _write_run(run_dir: Path, record: RunRecord) -> None:
    (run_dir / "run.json").write_text(json.dumps(record.to_json(), indent=2), encoding="utf-8")


def _concat_csv(directory: Path, suffix: str) -> pd.DataFrame:
    frames = [pd.read_csv(path) for path in sorted(directory.glob(f"*{suffix}")) if path.suffix == suffix]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _safe(value: str) -> str:
    cleaned = "".join(character if character.isalnum() or character in "-_" else "_" for character in value)
    return cleaned or "image"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(value: Any):
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
