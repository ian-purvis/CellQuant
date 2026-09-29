"""Make a new, self-contained experiment from a prepared package and its downloaded results.

Everything is checked before anything is published: the package's READY
binding, the result index, every task's commit record, artifact hashes, label
shapes and object tables. The experiment is built in a hidden folder beside
the destination and renamed into place only after it opens and reads back
correctly. The original experiment and the original images are never used.
"""

from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from cellquant import progress
from cellquant.errors import CellQuantError
from cellquant.experiment import ChannelRecord, ImageRecord, create_experiment, save_experiment
from cellquant.hpc.common import contained_path, sha256_file, utc_now, write_json_atomic
from cellquant.hpc.models import (
    ImportedAcquisition,
    ImportRecord,
    Issue,
    ResultIndex,
    RunAllocation,
    TaskResult,
    load_model,
)
from cellquant.hpc.publish import verify_artifacts
from cellquant.hpc.validate import Bundle, BundleInvalid, open_bundle
from cellquant.recipe import save_recipe

MAX_WINDOWS_PATH = 240
IMPORT_RECORD = "hpc_import.json"


class ImportFailed(CellQuantError):
    def __init__(self, message: str, issues: list[Issue] | None = None, exit_code: int = 2):
        super().__init__(message)
        self.issues = issues or []
        self.exit_code = exit_code


@dataclass
class PreviewRow:
    acquisition_id: str
    sample_name: str
    source_relative_path: str
    state: str
    message: str
    importable: bool
    warnings: list[str] = field(default_factory=list)


@dataclass
class ImportPreview:
    bundle: Bundle
    index: ResultIndex
    index_sha256: str
    rows: list[PreviewRow]
    records: dict[str, tuple[Path, TaskResult]]

    @property
    def complete(self) -> bool:
        return (
            self.index.finalized
            and self.index.compute_outcome == "completed"
            and self.index.publication_outcome == "verified_complete"
            and all(row.importable for row in self.rows)
        )

    @property
    def importable_count(self) -> int:
        return sum(row.importable for row in self.rows)

    def status_text(self) -> str:
        counts: dict[str, int] = {}
        for row in self.rows:
            counts[row.state] = counts.get(row.state, 0) + 1
        parts = ", ".join(f"{count} {state}" for state, count in sorted(counts.items()))
        snapshot = "" if self.index.finalized else " The job had not finished writing its record (it may still be running, or it was killed)."
        return f"Run {self.index.run_id}: {parts}. Compute {self.index.compute_outcome}, publication {self.index.publication_outcome}.{snapshot}"


@dataclass
class ImportOutcome:
    destination: Path
    record: ImportRecord
    already_imported: bool = False

    def summary_text(self) -> str:
        imported = sum(item.imported_result for item in self.record.acquisitions)
        total = len(self.record.acquisitions)
        prefix = "Already imported: " if self.already_imported else "Imported "
        text = f"{prefix}{imported} of {total} images into {self.destination}."
        missing = [item for item in self.record.acquisitions if not item.imported_result]
        if missing:
            text += " Without results: " + ", ".join(f"{item.acquisition_id} ({item.state})" for item in missing) + "."
        return text


def _expected_shape(acquisition) -> tuple[int, ...]:
    _channels, planes, height, width = acquisition.shape_czyx
    if planes > 1 and acquisition.effective_z_mode in ("stitch_slices", "full_3d"):
        return (planes, height, width)
    return (height, width)


def preview_import(bundle_dir: str | Path, results_dir: str | Path, cancel=None) -> ImportPreview:
    """Validate a package and its downloaded results. Raises ImportFailed on any integrity problem."""

    try:
        bundle = open_bundle(bundle_dir, cancel=cancel)
    except BundleInvalid as exc:
        raise ImportFailed(f"The prepared package is not valid: {exc}", exc.report.errors, 2) from exc
    results = Path(results_dir)
    if not (results / "results.json").is_file() or not (results / "run.json").is_file():
        raise ImportFailed(
            f"{results} is not a downloaded run folder (results.json and run.json are missing).",
            [Issue(code="E_RESULTS", message="Choose the folder named run_... inside results/<package name>/ on the cluster.")],
        )
    try:
        allocation: RunAllocation = load_model(RunAllocation, results / "run.json")  # type: ignore[assignment]
        index: ResultIndex = load_model(ResultIndex, results / "results.json")  # type: ignore[assignment]
    except Exception as exc:  # noqa: BLE001
        raise ImportFailed(f"The run's records could not be read: {exc}", exit_code=5) from exc
    manifest = bundle.manifest
    if allocation.bundle_id != manifest.bundle_id or index.bundle_id != manifest.bundle_id:
        raise ImportFailed("These results were made from a different package.", [Issue(code="E_IDENTITY", message="Bundle IDs differ.")])
    if allocation.ready_digest != bundle.checksums_sha256 or index.ready_digest != bundle.checksums_sha256:
        raise ImportFailed("These results were made from a different copy of this package (its checksums differ).")
    if allocation.run_id != index.run_id:
        raise ImportFailed("run.json and results.json name different runs.", exit_code=5)
    planned = [item.acquisition_id for item in manifest.acquisitions]
    listed = [item.acquisition_id for item in index.acquisitions]
    unknown = sorted(set(listed) - set(planned))
    missing = sorted(set(planned) - set(listed))
    if unknown or missing or len(listed) != len(set(listed)):
        raise ImportFailed(
            "The result index does not account for exactly the package's images.",
            [Issue(code="E_INDEX", message=f"Unknown: {unknown or 'none'}; missing: {missing or 'none'}.")],
            5,
        )
    by_id = {item.acquisition_id: item for item in manifest.acquisitions}
    rows: list[PreviewRow] = []
    records: dict[str, tuple[Path, TaskResult]] = {}
    problems: list[Issue] = []
    from cellquant.storage import read_persisted_result

    for number, state in enumerate(index.acquisitions, start=1):
        if cancel is not None:
            cancel()
        progress.update(f"Checking result {number} of {len(index.acquisitions)}", number - 1, len(index.acquisitions))
        acquisition = by_id[state.acquisition_id]
        row = PreviewRow(
            acquisition_id=state.acquisition_id,
            sample_name=acquisition.sample_name,
            source_relative_path=acquisition.source_relative_path,
            state=state.state if index.finalized or state.state != "running" else "running (unfinished record)",
            message=state.message,
            importable=False,
        )
        rows.append(row)
        if state.state != "succeeded":
            continue
        try:
            commit = contained_path(results, state.commit_path or "")
        except ValueError as exc:
            problems.append(Issue(code="E_PATH", message=f"{state.acquisition_id}: {exc}", acquisition_id=state.acquisition_id))
            continue
        if not commit.is_file() or state.commit_sha256 != sha256_file(commit):
            problems.append(
                Issue(code="E_CHECKSUM", message=f"{state.acquisition_id}: its commit record is missing or changed.", acquisition_id=state.acquisition_id)
            )
            continue
        record: TaskResult = load_model(TaskResult, commit)  # type: ignore[assignment]
        task_dir = commit.parent
        identity = (record.bundle_id, record.run_id, record.acquisition_id, record.task_key, record.runtime_sha256, record.outcome)
        wanted = (manifest.bundle_id, index.run_id, acquisition.acquisition_id, acquisition.task_key, manifest.runtime_sha256, "succeeded")
        if identity != wanted:
            problems.append(
                Issue(code="E_IDENTITY", message=f"{state.acquisition_id}: its result was made for different settings, software or package.", acquisition_id=state.acquisition_id)
            )
            continue
        broken = verify_artifacts(task_dir, record.artifacts)
        if broken:
            problems.append(
                Issue(code="E_CHECKSUM", message=f"{state.acquisition_id}: " + "; ".join(broken[:3]), acquisition_id=state.acquisition_id)
            )
            continue
        result = read_persisted_result(task_dir, acquisition.acquisition_id)
        if result is None:
            problems.append(Issue(code="E_RESULTS", message=f"{state.acquisition_id}: its result files are incomplete.", acquisition_id=state.acquisition_id))
            continue
        expected = _expected_shape(acquisition)
        if tuple(result.labels.shape) != expected or tuple(result.automated_labels.shape) != expected:
            problems.append(
                Issue(code="E_LABELS", message=f"{state.acquisition_id}: labels are {result.labels.shape}, expected {expected}.", acquisition_id=state.acquisition_id)
            )
            continue
        if result.labels.dtype.kind not in "iu":
            problems.append(Issue(code="E_LABELS", message=f"{state.acquisition_id}: labels are not integers.", acquisition_id=state.acquisition_id))
            continue
        present = set(np.unique(result.automated_labels).tolist()) - {0}
        ids = set(int(value) for value in result.objects["object_id"].tolist()) if "object_id" in result.objects else set()
        if ids != present or len(result.objects) != len(ids):
            problems.append(
                Issue(code="E_OBJECTS", message=f"{state.acquisition_id}: the object table does not match the labels.", acquisition_id=state.acquisition_id)
            )
            continue
        if (result.provenance.get("hpc") or {}).get("task_key") != acquisition.task_key:
            problems.append(Issue(code="E_IDENTITY", message=f"{state.acquisition_id}: its provenance names another task.", acquisition_id=state.acquisition_id))
            continue
        row.importable = True
        row.warnings = list(record.warnings)
        records[state.acquisition_id] = (task_dir, record)
    if problems:
        raise ImportFailed("Some returned results failed their integrity checks. Nothing was imported.", problems, 5)
    return ImportPreview(bundle, index, sha256_file(results / "results.json"), rows, records)


def _existing_import(destination: Path, preview: ImportPreview) -> ImportRecord | None:
    path = destination / IMPORT_RECORD
    if not path.is_file():
        return None
    record: ImportRecord = load_model(ImportRecord, path)  # type: ignore[assignment]
    same = (
        record.bundle_id == preview.bundle.manifest.bundle_id
        and record.ready_digest == preview.bundle.checksums_sha256
        and record.run_id == preview.index.run_id
        and record.results_index_sha256 == preview.index_sha256
    )
    return record if same else None


def import_results(
    bundle_dir: str | Path,
    results_dir: str | Path,
    destination: str | Path,
    *,
    allow_partial: bool = False,
    preview: ImportPreview | None = None,
) -> ImportOutcome:
    target = Path(destination)
    preview = preview or preview_import(bundle_dir, results_dir, cancel=progress.check_cancelled)
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        existing = _existing_import(target, preview) if target.is_dir() else None
        if existing is not None:
            _check_opens(target, existing)
            return ImportOutcome(target, existing, already_imported=True)
        raise ImportFailed(
            f"{target} is not empty. Import into a new, empty folder; existing experiments and runs are never replaced.",
            [Issue(code="E_DESTINATION", message="Choose a new folder, for example next to your other experiments.")],
        )
    if not preview.complete:
        if not allow_partial:
            waiting = [f"{row.acquisition_id} ({row.state})" for row in preview.rows if not row.importable]
            raise ImportFailed(
                "Not every image finished: " + (", ".join(waiting) or preview.status_text()) + ". "
                "Import the finished images only by choosing 'Import completed results only' (--allow-partial), "
                "or resume the run on the cluster first.",
                exit_code=4,
            )
        if preview.importable_count == 0:
            raise ImportFailed("No image has a verified result to import.", exit_code=4)
    _check_path_lengths(target, preview)
    staging = target.parent / f".{target.name}.importing-{uuid.uuid4().hex[:8]}"
    try:
        record = _build(staging, target, preview, partial=not preview.complete)
        progress.update("Checking the imported experiment opens")
        _check_opens(staging, record)
        if target.exists():
            target.rmdir()
        os.rename(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return ImportOutcome(target, record)


def _check_path_lengths(target: Path, preview: ImportPreview) -> None:
    root = target.resolve()
    staging = root.parent / f".{root.name}.importing-00000000"
    longest = 0
    for acquisition in preview.bundle.manifest.acquisitions:
        for base in (root, staging):
            longest = max(
                longest,
                len(str(base / "inputs" / f"{acquisition.acquisition_id}.ome.tif")),
                len(str(base / "runs" / preview.index.run_id / "classifications" / f"{acquisition.acquisition_id}_combinations.csv")),
                len(str(base / "runs" / preview.index.run_id / "hpc" / "tasks" / acquisition.acquisition_id / "COMMIT.json")),
            )
    if longest >= MAX_WINDOWS_PATH:
        raise ImportFailed(
            f"The imported experiment's paths would be {longest} characters long; Windows allows fewer than {MAX_WINDOWS_PATH} here.",
            [Issue(code="E_PATH_TOO_LONG", message="Choose a shorter destination folder, for example D:\\CellQuant_imports\\run1.")],
        )


def _build(staging: Path, target: Path, preview: ImportPreview, *, partial: bool) -> ImportRecord:
    from cellquant.storage import RunRecord, _write_run, persist_image_result, read_persisted_result

    bundle = preview.bundle
    manifest = bundle.manifest
    index = preview.index
    run_id = index.run_id
    experiment = create_experiment(staging, f"{manifest.source_experiment_name} (cluster {run_id})")
    # experiment.json records the folder it was written in; opening it from its final folder uses that folder.
    experiment_id = experiment.experiment_id
    recipe = bundle.recipe.model_copy(deep=True)
    recipe.recipe_id = recipe.recipe_id or f"recipe_{experiment_id[-8:]}"
    save_recipe(recipe, staging / "recipes" / f"{recipe.recipe_id}.yaml")
    experiment.recipe_id = recipe.recipe_id
    experiment.channels = [
        ChannelRecord(channel_index=index_, channel_name=name, display_name=name) for index_, name in enumerate(manifest.channel_layout)
    ]
    columns: list[str] = []
    for acquisition in manifest.acquisitions:
        for key in acquisition.user_metadata:
            if key not in columns:
                columns.append(str(key))
    experiment.metadata_columns = columns
    run_dir = staging / "runs" / run_id
    for relative in ("logs", "labels", "measurements", "classifications", "summaries", "provenance", "edits", "hpc"):
        (run_dir / relative).mkdir(parents=True, exist_ok=True)
    (staging / "inputs").mkdir()
    states = {item.acquisition_id: item for item in index.acquisitions}
    imported: list[ImportedAcquisition] = []
    warnings: list[str] = []
    errors: list[str] = []
    total = len(manifest.acquisitions)
    for number, acquisition in enumerate(manifest.acquisitions, start=1):
        progress.update(f"Importing image {number} of {total}: {acquisition.sample_name}", number - 1, total)
        state = states[acquisition.acquisition_id]
        relative_input = f"inputs/{acquisition.acquisition_id}.ome.tif"
        copied = staging / relative_input
        shutil.copyfile(bundle.input_file(acquisition), copied)
        if sha256_file(copied) != acquisition.input_file_sha256:
            raise ImportFailed(f"{acquisition.acquisition_id}: the copied image does not match its checksum.", exit_code=5)
        stat = copied.stat()
        has_result = acquisition.acquisition_id in preview.records
        if has_result:
            task_dir, task_record = preview.records[acquisition.acquisition_id]
            result = read_persisted_result(task_dir, acquisition.acquisition_id)
            assert result is not None
            result.provenance["experiment_id"] = experiment_id
            result.provenance["source_path"] = relative_input
            result.provenance.setdefault("hpc", {})["imported_at"] = utc_now()
            if "experiment_id" in result.objects.columns:
                result.objects["experiment_id"] = experiment_id
            persist_image_result(run_dir, result, [])
            commit_copy = run_dir / "hpc" / "tasks" / acquisition.acquisition_id
            commit_copy.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(task_dir / "COMMIT.json", commit_copy / "COMMIT.json")
            status = "needs_attention" if result.qc.warnings or result.qc.status != "success" else "analyzed"
            message = "; ".join(result.qc.warnings[:2])
            warnings.extend(f"{acquisition.sample_name}: {text}" for text in result.qc.warnings)
        else:
            if state.state == "pending":
                status, message = "not_analyzed", "Not analyzed on the cluster yet."
            else:
                status = "needs_attention"
                message = f"Cluster run: {state.state}" + (f" ({state.message})" if state.message else "")
                errors.append(f"{acquisition.sample_name}: {message}")
        record = ImageRecord(
            image_id=acquisition.acquisition_id,
            source_path=str(target.resolve() / "inputs" / f"{acquisition.acquisition_id}.ome.tif"),
            source_path_relative=relative_input,
            filename=Path(acquisition.source_relative_path).name,
            sample_name=acquisition.sample_name,
            relative_path=acquisition.source_relative_path,
            position=0,
            positions_in_file=1,
            z_planes=acquisition.shape_czyx[1],
            channel_names=list(acquisition.channel_names),
            channel_colors=[list(color) if color else None for color in acquisition.channel_colors],
            objective=acquisition.objective,
            include=True,
            dimensions=acquisition.shape_czyx[2:],
            number_of_channels=acquisition.shape_czyx[0],
            pixel_size_x=acquisition.effective_spacing_xyz_um[0],
            pixel_size_y=acquisition.effective_spacing_xyz_um[1],
            pixel_size_z=acquisition.effective_spacing_xyz_um[2],
            image_metadata={
                "hpc": {
                    "bundle_id": manifest.bundle_id,
                    "run_id": run_id,
                    "acquisition_id": acquisition.acquisition_id,
                    "source_image_id": acquisition.source_image_id,
                    "source_relative_path": acquisition.source_relative_path,
                    "source_position": acquisition.source_position,
                    "source_file_sha256": acquisition.source_file_sha256,
                    "file_spacing_xyz_um": acquisition.file_spacing_xyz_um,
                    "calibration_source": acquisition.calibration_source,
                },
                "dtype": acquisition.dtype,
            },
            user_metadata={key: acquisition.user_metadata.get(key, "") for key in columns},
            processing_status=status,
            last_result="" if has_result or state.state == "pending" else "Failure",
            last_message=message,
            content_hash=acquisition.input_file_sha256,
            file_signature=f"{stat.st_mtime_ns}:{stat.st_size}",
        )
        experiment.images.append(record)
        imported.append(
            ImportedAcquisition(
                acquisition_id=acquisition.acquisition_id,
                state=state.state,
                imported_result=has_result,
                image_id=acquisition.acquisition_id,
                source_image_id=acquisition.source_image_id,
                source_relative_path=acquisition.source_relative_path,
                sample_name=acquisition.sample_name,
                attempt_id=state.attempt_id,
            )
        )
    status_word = {
        "completed": "completed",
        "completed_with_failures": "completed_with_failures",
        "failed": "failed",
    }.get(index.compute_outcome, "incomplete")
    run_record = RunRecord(
        run_id=run_id,
        experiment_id=experiment_id,
        recipe_id=recipe.recipe_id,
        software_version=manifest.cellquant_version,
        start_timestamp=index.created_at,
        completion_timestamp=index.updated_at,
        input_files=[f"inputs/{item.acquisition_id}.ome.tif" for item in manifest.acquisitions],
        input_hashes={item.acquisition_id: item.input_file_sha256 for item in manifest.acquisitions},
        processing_status=status_word,
        recipe_hashes={item: manifest.recipe_scientific_sha256 for item in preview.records},
        warnings=warnings,
        errors=errors,
    )
    save_recipe(recipe, run_dir / "recipe_snapshot.yaml")
    _write_run(run_dir, run_record)
    write_json_atomic(run_dir / "hpc" / "results.json", index.model_dump(mode="json"))
    write_json_atomic(run_dir / "hpc" / "bundle.json", manifest.model_dump(mode="json"))
    experiment.latest_run_id = run_id
    save_experiment(experiment)
    record = ImportRecord(
        bundle_id=manifest.bundle_id,
        package_name=manifest.package_name,
        ready_digest=bundle.checksums_sha256,
        run_id=run_id,
        results_index_sha256=preview.index_sha256,
        imported_at=utc_now(),
        experiment_id=experiment_id,
        source_experiment_id=manifest.source_experiment_id,
        source_experiment_name=manifest.source_experiment_name,
        runtime_id=bundle.runtime.runtime_id,
        runtime_sha256=manifest.runtime_sha256,
        compute_outcome=index.compute_outcome,
        publication_outcome=index.publication_outcome,
        partial=partial,
        acquisitions=imported,
    )
    write_json_atomic(staging / IMPORT_RECORD, record.model_dump(mode="json"))
    return record


def _check_opens(folder: Path, record: ImportRecord) -> None:
    """Open the experiment the way the window does and read every imported result back."""

    from cellquant.controller import AnalysisController

    controller = AnalysisController.open(folder)
    for item in record.acquisitions:
        image = controller.experiment.image(item.image_id)
        if not controller.source_path(image).is_file():
            raise ImportFailed(f"{item.image_id}: its image copy is missing from the imported experiment.", exit_code=5)
        if item.imported_result:
            result = controller.recall(item.image_id)
            if result is None:
                raise ImportFailed(f"{item.image_id}: its result could not be read back after import.", exit_code=5)
