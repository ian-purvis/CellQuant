"""Deterministic, failure-isolated batch execution for CellQuant."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from cellquant.config import RunConfig
from cellquant.contracts import (
    CancellationToken,
    PipelineCancelled,
    PipelineEvent,
    null_event_sink,
)
from cellquant.io import iter_supported_files, normalize_suffixes, open_volume
from cellquant.measure import write_measurements
from cellquant.orchestrator import run_measurements, run_pipeline
from cellquant.persist import RunStore
from cellquant.persist.atomic import replace_with_retry
from cellquant.persist.staging import publish_directory, resolve_work_directory
from cellquant.viz import make_qc_figures


@dataclass(frozen=True)
class BatchItem:
    source: Path
    output_dir: Path
    output_root: Path
    input_fingerprint: str
    file_id: str


@dataclass(frozen=True)
class BatchItemResult:
    source: str
    output_dir: str
    status: str
    run_id: str | None
    message: str | None = None


@dataclass(frozen=True)
class BatchSummary:
    total: int
    completed: int
    resumed: int
    failed: int
    cancelled: int
    results: tuple[BatchItemResult, ...]
    summary_path: Path | None = None
    failures_path: Path | None = None


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _raw_config(config: RunConfig | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(config, RunConfig):
        return config.raw
    if not isinstance(config, Mapping):
        raise TypeError("config must be a RunConfig or mapping")
    return config


def _config_fingerprint(config: RunConfig | Mapping[str, Any]) -> str:
    if isinstance(config, RunConfig):
        return config.fingerprint
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _input_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    stat = path.stat()
    descriptor = {
        "content_sha256": digest.hexdigest(),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }
    canonical = json.dumps(descriptor, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def _collision_name(relative: Path, source: Path) -> Path:
    token = hashlib.sha256(str(source.resolve()).casefold().encode("utf-8")).hexdigest()[:10]
    return relative.with_name(f"{relative.stem}__{token}{relative.suffix}")


def _run_directory(relative: Path) -> Path:
    """Append, rather than replace, the acquisition file's full extension."""

    return relative.with_name(f"{relative.name}.cellquant")


def _is_generated_output(source: Path, output_root: Path) -> bool:
    """Identify only the configured output tree and explicit run directories."""

    resolved = source.resolve()
    try:
        resolved.relative_to(output_root)
        return True
    except ValueError:
        pass
    return any(parent.name.casefold().endswith(".cellquant") for parent in resolved.parents)


def build_queue(
    inputs: Iterable[str | Path],
    output_root: str | Path,
    config: RunConfig | Mapping[str, Any],
) -> list[BatchItem]:
    """Build a stable queue, preserving source-relative paths and extensions.

    Directory inputs receive a root-name prefix, so equal relative paths from
    different acquisition roots cannot overwrite one another. Exact duplicate
    source paths are processed once. Any remaining case-insensitive collision
    receives a stable source-path suffix before its original extension.
    """

    raw = _raw_config(config)
    io_spec = raw.get("io", {})
    if not isinstance(io_spec, Mapping):
        raise TypeError("config.io must be a mapping")
    recursive = bool(io_spec.get("recursive", True))
    suffixes = normalize_suffixes(io_spec.get("suffixes"))
    destination_root = Path(output_root).resolve()
    discovered: list[tuple[Path, Path]] = []
    seen_sources: set[str] = set()
    for supplied in inputs:
        root = Path(supplied).resolve()
        if not root.exists():
            raise FileNotFoundError(root)
        if root.is_file():
            candidates = list(iter_supported_files(root, recursive=False, suffixes=suffixes))
            relative_values = [(path, Path(path.name)) for path in candidates]
        else:
            candidates = list(iter_supported_files(root, recursive=recursive, suffixes=suffixes))
            relative_values = [(path, Path(root.name) / path.relative_to(root)) for path in candidates]
        for source, relative in relative_values:
            source = source.resolve()
            # A recursive input may contain its own output root from a prior
            # batch. Never enqueue persisted labels or any other generated TIFF
            # as a new acquisition. The exact resolved-tree check deliberately
            # does not reject similarly named directories elsewhere.
            if _is_generated_output(source, destination_root):
                continue
            key = str(source).casefold()
            if key not in seen_sources:
                seen_sources.add(key)
                discovered.append((source, relative))

    discovered.sort(key=lambda value: str(value[0]).casefold())
    occupied: set[str] = set()
    queue: list[BatchItem] = []
    for source, relative in discovered:
        candidate = destination_root / _run_directory(relative)
        key = str(candidate).casefold()
        if key in occupied:
            relative = _collision_name(relative, source)
            candidate = destination_root / _run_directory(relative)
            key = str(candidate).casefold()
            if key in occupied:  # Cryptographically implausible, but fail closed.
                raise RuntimeError(f"could not derive a unique output path for {source}")
        occupied.add(key)
        queue.append(
            BatchItem(
                source=source,
                output_dir=candidate,
                output_root=destination_root,
                input_fingerprint=_input_fingerprint(source),
                file_id=relative.as_posix(),
            )
        )
    return queue


def _event(
    kind: str,
    run_id: str,
    item: BatchItem,
    stage: str,
    *,
    current: int | None = None,
    total: int | None = None,
    **details: Any,
) -> PipelineEvent:
    return PipelineEvent(
        kind,
        run_id,
        item.file_id,
        stage,
        _utc(),
        current=current,
        total=total,
        details=details,
    )


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        replace_with_retry(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_root_reports(root: Path, results: Sequence[BatchItemResult]) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    failures_path = root / "failures.csv"
    temporary = failures_path.with_name(f".{failures_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["source", "output_dir", "status", "run_id", "message"])
            writer.writeheader()
            for result in results:
                if result.status == "failed":
                    writer.writerow(asdict(result))
        replace_with_retry(temporary, failures_path)
    finally:
        temporary.unlink(missing_ok=True)
    summary_path = root / "batch_summary.json"
    counts = {name: sum(result.status == name for result in results) for name in ("completed", "resumed", "failed", "cancelled")}
    _atomic_json(summary_path, {"schema_version": 1, "total": len(results), **counts, "results": [asdict(result) for result in results]})
    return summary_path, failures_path


def run_batch(
    queue: Sequence[BatchItem],
    config: RunConfig,
    cancel: CancellationToken,
    events=null_event_sink,
    *,
    write_root_reports: bool = True,
) -> BatchSummary:
    """Run files serially with exact resume checks and per-file isolation."""

    if not isinstance(config, RunConfig):
        raise TypeError("run_batch requires a validated RunConfig")
    items = list(queue)
    roots = {item.output_root.resolve() for item in items}
    if len(roots) > 1:
        raise ValueError("all BatchItems in one run must share output_root")
    results: list[BatchItemResult] = []
    stop_for_cancellation = False

    for index, item in enumerate(items):
        if stop_for_cancellation or cancel.cancelled:
            results.append(BatchItemResult(str(item.source), str(item.output_dir), "cancelled", None, "batch cancelled before file started"))
            continue

        run_id = uuid.uuid4().hex
        store = None
        try:
            # Resume must be checked before create(), because create intentionally
            # invalidates an earlier completion marker and starts a new event log.
            existing = RunStore(item.output_dir, item.input_fingerprint, config.fingerprint)
            if existing.is_resumable(item.input_fingerprint, config.fingerprint):
                result = BatchItemResult(str(item.source), str(item.output_dir), "resumed", None)
                results.append(result)
                events(_event("progress", "resume", item, "batch", current=index + 1, total=len(items), status="resumed"))
                continue

            # Cloud folders: finish a prior staged complete run that never published.
            work_dir, publish_to = resolve_work_directory(item.output_dir)
            if publish_to is not None:
                staged = RunStore(work_dir, item.input_fingerprint, config.fingerprint)
                if staged.is_resumable(item.input_fingerprint, config.fingerprint):
                    publish_directory(work_dir, item.output_dir)
                    result = BatchItemResult(str(item.source), str(item.output_dir), "resumed", None)
                    results.append(result)
                    events(
                        _event(
                            "progress",
                            "resume",
                            item,
                            "batch",
                            current=index + 1,
                            total=len(items),
                            status="resumed",
                        )
                    )
                    continue

            store = RunStore.create_for_user_output(
                item.output_dir, item.input_fingerprint, config.fingerprint, run_id=run_id
            )

            def emit(value: PipelineEvent) -> None:
                store.append_event(value)
                events(value)

            # Emit before long Cellpose work so the UI can show N/M while eval runs.
            events(
                _event(
                    "progress",
                    run_id,
                    item,
                    "batch",
                    current=index + 1,
                    total=len(items),
                    status="running",
                    message=f"Processing {Path(item.source).name} ({index + 1}/{len(items)})",
                )
            )
            emit(_event("stage_started", run_id, item, "file"))
            cancel.raise_if_cancelled()
            io_spec = config.raw["io"]
            emit(_event("stage_started", run_id, item, "io"))
            image = open_volume(
                item.source,
                series=int(io_spec.get("series", 0)),
                position=int(io_spec.get("position", 0)),
                lazy=bool(io_spec.get("lazy", True)),
                axes_override=io_spec.get("axes_override"),
                spacing_override_um=io_spec.get("spacing_override_um"),
            )
            image = replace(image, metadata={
                **image.metadata,
                "run_id": run_id,
                "file_id": item.file_id,
                "input_fingerprint": item.input_fingerprint,
            })
            emit(_event("stage_finished", run_id, item, "io"))
            # Configuration determines label compression and therefore must be
            # persisted before the label writer is called.
            store.write_config(config)
            labels = run_pipeline(image, config, cancel, emit)
            tables = run_measurements(image, labels, config, cancel, emit)
            # Write side-car CSVs/figures into the working store directory (local
            # staging when the UI output folder is cloud-synced).
            for path in write_measurements(tables, store.directory):
                stored = store.register_measurement(path)
                emit(_event("artifact_written", run_id, item, "measure", path=stored.name))
            for path in make_qc_figures(image, labels, store.directory, config).values():
                stored = store.register_qc_artifact(path)
                emit(_event("artifact_written", run_id, item, "viz", path=stored.name))
            store.write_provenance({
                "schema_version": 1,
                "run_id": run_id,
                "source": str(item.source),
                "input_fingerprint": item.input_fingerprint,
                "config_fingerprint": config.fingerprint,
                "model_sha256": config.raw["segment"]["model_sha256"],
                "label_provenance": dict(labels.provenance),
                # Reuse the harness' comprehensive, read-only inventory. The
                # import is intentionally lazy: harness has no dependency on
                # batch, and ordinary queue construction stays lightweight.
                "environment": _environment_inventory(),
            })
            store.write_labels(labels)
            emit(_event("artifact_written", run_id, item, "persist", path=store.labels_path.name))
            emit(_event("stage_finished", run_id, item, "file"))
            store.commit("complete")
            results.append(BatchItemResult(str(item.source), str(item.output_dir), "completed", run_id))
            events(_event("progress", run_id, item, "batch", current=index + 1, total=len(items), status="completed"))
        except PipelineCancelled as exc:
            message = str(exc)
            try:
                if store is not None:
                    store.append_event(_event("cancelled", run_id, item, "file", message=message))
                    store.commit("cancelled")
            except Exception:
                pass
            results.append(BatchItemResult(str(item.source), str(item.output_dir), "cancelled", run_id, message))
            stop_for_cancellation = True
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            try:
                if store is not None:
                    store.append_event(
                        _event(
                            "failed",
                            run_id,
                            item,
                            "file",
                            exception_type=type(exc).__name__,
                            message=str(exc),
                        )
                    )
                    store.commit("failed")
            except Exception as secondary:
                message = f"{message} (also failed while recording status: {type(secondary).__name__}: {secondary})"
            results.append(BatchItemResult(str(item.source), str(item.output_dir), "failed", run_id, message))

    root = next(iter(roots), None)
    summary_path = failures_path = None
    if write_root_reports and root is not None:
        summary_path, failures_path = _write_root_reports(root, results)
    return BatchSummary(
        total=len(items),
        completed=sum(result.status == "completed" for result in results),
        resumed=sum(result.status == "resumed" for result in results),
        failed=sum(result.status == "failed" for result in results),
        cancelled=sum(result.status == "cancelled" for result in results),
        results=tuple(results),
        summary_path=summary_path,
        failures_path=failures_path,
    )


def _environment_inventory() -> Mapping[str, Any]:
    from cellquant.harness.runner import _environment_provenance

    return _environment_provenance()


__all__ = ["BatchItem", "BatchItemResult", "BatchSummary", "build_queue", "run_batch"]
