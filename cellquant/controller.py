"""Analysis controller.

The viewer calls this object. Segmentation, measurement, classification, and
summary each cache on their own inputs, and manual edits stay out of the recipe.
"""

from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from cellquant.__version__ import __version__
from cellquant.cache import StageCache, stage_key
from cellquant.edits import EditOperation, apply_edits, operations_from_diff
from cellquant.errors import CellQuantError, ImageLoadError
from cellquant.experiment import (
    IMAGE_STATE_FIELDS,
    AnalysisRecord,
    Experiment,
    PlanEntry,
    add_images,
    add_metadata_column,
    channel_warnings,
    create_experiment,
    load_experiment,
    save_experiment,
    set_channel_name,
)
from cellquant.image import LoadedImage
from cellquant.inputs import load_record_image, resolve_source_path
from cellquant.hpc.lineage import segmentation_settings
from cellquant import progress
from cellquant.progress import AnalysisCancelled
from cellquant.pipeline import (
    ImageResult,
    assemble_result,
    carry_segmentation_provenance,
    measurement_values,
    process_image,
    reclassify_result,
    remeasure_persisted_result,
    segmentation_details_of,
)
from cellquant.quantify import classification_counts
from cellquant.recipe import Recipe, load_recipe, save_recipe
from cellquant.segmentation import engine_signature
from cellquant.storage import (
    RunLog,
    RunRecord,
    export_run_tables,
    file_fingerprint,
    finish_run,
    grouped_summary,
    load_edits,
    persist_image_result,
    read_persisted_result,
    save_edits,
    start_run,
)


@dataclass
class ImageJobResult:
    image_id: str
    filename: str
    status: str
    message: str
    qc_status: str | None = None


@dataclass
class BatchReport:
    run_id: str
    run_dir: str
    jobs: list[ImageJobResult] = field(default_factory=list)
    analysis: str = ""  # the analysis's name, when several analyses were run
    cancelled: bool = False

    @property
    def completed(self) -> int:
        return sum(job.status == "Success" for job in self.jobs)

    @property
    def warnings(self) -> int:
        return sum(job.status == "Warning" for job in self.jobs)

    @property
    def failed(self) -> int:
        return sum(job.status == "Failure" for job in self.jobs)


# Loaded images kept for fast re-measuring after an edit. Only the most recent
# few are kept so a long batch does not hold every image in memory.
SESSION_IMAGE_LIMIT = 2


class AnalysisController:
    def __init__(self, experiment: Experiment):
        self.experiment = experiment
        self.directory = Path(experiment.directory)
        self.cache = StageCache(self.directory / ".cache")
        self.recipe = self._load_bound_recipe()
        self.edits: dict[str, list[EditOperation]] = {
            record.image_id: load_edits(self.directory, record.image_id) for record in experiment.images
        }
        self.current_image_id: str | None = experiment.images[0].image_id if experiment.images else None
        self.run_record: RunRecord | None = None
        self.run_dir: Path | None = None
        self.run_log: RunLog | None = None
        self.last_results: dict[str, ImageResult] = {}
        self._session_images: OrderedDict[str, LoadedImage] = OrderedDict()
        self._segmentation_ids: dict[str, str] = {}
        self._ensure_analyses()

    @classmethod
    def create(cls, directory: str | Path, name: str, input_directory: str | Path | None = None) -> AnalysisController:
        return cls(create_experiment(directory, name, input_directory=input_directory))

    @classmethod
    def open(cls, directory: str | Path) -> AnalysisController:
        return cls(load_experiment(directory))

    def save(self) -> None:
        if getattr(self, "_unswapped_recipe", None) is not None:
            # Saving while one image is analyzed with its own channels: keep the analysis's settings.
            swapped, original = self.recipe, self._unswapped_recipe
            self.recipe, self._unswapped_recipe = original, None
            try:
                self.save()
            finally:
                self.recipe, self._unswapped_recipe = swapped, original
            return
        self._touch_recipe()
        self.recipe.recipe_name = self.active_analysis().name
        recipe_path = self._recipe_path()
        save_recipe(self.recipe, recipe_path)
        self.experiment.recipe_id = self.recipe.recipe_id
        active = self.active_analysis()
        active.latest_run_id = self.experiment.latest_run_id
        save_experiment(self.experiment)
        for image_id, edits in self.edits.items():
            save_edits(self.directory, image_id, edits)
        self._persist_open_results()

    def recall(self, image_id: str) -> ImageResult | None:
        """Return the in-memory result or the last saved result for this image."""

        if image_id in self.last_results:
            return self.last_results[image_id]
        for folder in self._result_folders():
            result = read_persisted_result(folder, image_id)
            if result is not None:
                self.last_results[image_id] = result
                return result
        return None

    def _result_folders(self) -> list[Path]:
        """Where saved results are looked for: open work, the latest run, then earlier runs, newest first.

        A run of one image does not hide the saved results of the others (for example, results
        imported from a cluster run after one image is run again here).
        """

        folders = [self._working_dir()]
        latest = self._latest_run_dir()
        if latest is not None and self._run_belongs_here(latest):
            folders.append(latest)
        earlier = []
        for run in (self.directory / "runs").glob("*/run.json"):
            if latest is not None and run.parent == latest:
                continue
            try:
                import json

                data = json.loads(run.read_text(encoding="utf-8"))
                started = str(data.get("start_timestamp") or "")
                recipe_id = data.get("recipe_id")
            except (OSError, ValueError):
                started, recipe_id = "", None
            if not self._recipe_belongs_here(recipe_id):
                continue  # another analysis's run
            earlier.append((started, run.parent))
        folders.extend(folder for _started, folder in sorted(earlier, key=lambda item: item[0], reverse=True))
        return folders

    def _latest_run_dir(self) -> Path | None:
        if self.run_dir is not None and self.run_dir.is_dir():
            return self.run_dir
        if self.experiment.latest_run_id:
            path = self.directory / "runs" / self.experiment.latest_run_id
            if path.is_dir():
                return path
        return None

    def _persist_open_results(self) -> None:
        """Save interactive results separately from the finished run snapshot."""

        destination = self._working_dir()
        for relative in ("labels", "measurements", "classifications", "summaries", "provenance", "edits"):
            (destination / relative).mkdir(parents=True, exist_ok=True)
        for image_id, result in self.last_results.items():
            persist_image_result(destination, result, self._active_edits(image_id))

    def add_image_paths(self, paths: list[str | Path], file_types: list[str] | tuple[str, ...] | None = None) -> list[str]:
        """Add files, or every image of the chosen types in folders and their subfolders."""

        if file_types is not None:
            if not list(file_types):
                raise ValueError("Choose at least one file type: ND2 or TIFF.")
            self.experiment.import_file_types = list(file_types)
        notices = add_images(self.experiment, paths, file_types=file_types or self.experiment.import_file_types)
        for record in self.experiment.images:
            self.edits.setdefault(record.image_id, [])
        if self.current_image_id is None and self.experiment.images:
            self.current_image_id = self.experiment.images[0].image_id
        self.save()
        return notices

    def channel_notices(self) -> list[str]:
        """Things to check about the listed images: channels, pixel sizes, Z-stacks."""

        from cellquant.experiment import image_notices

        return image_notices(self.experiment)

    def set_sample_name(self, image_id: str, name: str) -> None:
        self.experiment.image(image_id).sample_name = name

    def set_included(self, image_id: str, include: bool) -> None:
        record = self.experiment.image(image_id)
        record.include = include
        if not include:
            record.processing_status = "excluded"
        elif record.processing_status == "excluded":
            # Ticked again: back to what it was before it was left out.
            record.processing_status = "analyzed" if self.recall(image_id) is not None else "not_analyzed"

    def add_metadata_column(self, name: str) -> None:
        add_metadata_column(self.experiment, name)

    def set_metadata(self, image_id: str, column: str, value: str) -> None:
        if column not in self.experiment.metadata_columns:
            self.add_metadata_column(column)
        self.experiment.image(image_id).user_metadata[column] = value

    def set_channel_name(self, channel_index: int, name: str) -> None:
        set_channel_name(self.experiment, channel_index, name)

    def set_pixel_size(
        self,
        image_id: str,
        pixel_size_x: float | None,
        pixel_size_y: float | None,
        pixel_size_z: float | None = None,
    ) -> None:
        """Set µm per pixel in X and Y, and optionally the Z step in µm."""

        record = self.experiment.image(image_id)
        record.pixel_size_x = pixel_size_x
        record.pixel_size_y = pixel_size_y
        if pixel_size_z is not None:
            record.pixel_size_z = pixel_size_z or None

    def set_status(self, image_id: str, status: str) -> None:
        record = self.experiment.image(image_id)
        record.processing_status = status
        record.approved_settings_sha256 = self._approval_fingerprint(image_id) if status == "approved" else ""

    def _approval_fingerprint(self, image_id: str) -> str:
        """Settings plus the number of edits: what an approval vouches for."""

        result = self.last_results.get(image_id)
        if result is not None:
            settings = result.provenance.get("recipe_sha256", "")
        else:
            with self._image_settings(self.experiment.image(image_id)):
                settings = self.recipe.content_hash()
        return f"{settings}:{len(self._active_edits(image_id))}"

    def set_recipe(self, recipe: Recipe | dict | str | Path) -> None:
        """Replace the active analysis's settings. The analysis keeps its identity (recipe id)."""

        if getattr(self, "_unswapped_recipe", None) is not None:
            raise CellQuantError("Wait for the current analysis to finish before changing the settings.")
        current = self.recipe.recipe_id if hasattr(self, "recipe") else None
        self.recipe = load_recipe(recipe)
        if current and self.experiment.analyses:
            self.recipe.recipe_id = current
        if not self.recipe.recipe_id:
            self.recipe.recipe_id = self.experiment.recipe_id or f"recipe_{self.experiment.experiment_id[-8:]}"
        self.recipe.software_version = self.recipe.software_version or __version__

    def duplicate_recipe(self, name: str) -> Recipe:
        """A new analysis with a copy of these settings, made active. The current analysis is kept."""

        self.add_analysis(name or f"{self.active_analysis().name} copy", activate=True)
        return self.recipe

    def import_recipe(self, path: str | Path) -> Recipe:
        self.set_recipe(path)
        self.save()
        return self.recipe

    def export_recipe(self, path: str | Path) -> None:
        self._touch_recipe()
        save_recipe(self.recipe, path)

    def set_included_many(self, image_ids: list[str], include: bool) -> None:
        for image_id in image_ids:
            self.set_included(image_id, include)

    def included_ids(self) -> list[str]:
        return [record.image_id for record in self.experiment.images if record.include]

    def run_image(self, image_id: str, *, persist: bool = True, new_run: bool = True) -> ImageResult:
        record = self.experiment.image(image_id)
        was_approved = record.processing_status == "approved"
        result = self._analyze(record)
        status, message = _job_status(result)
        self.last_results[image_id] = result
        unchanged = (
            was_approved
            and status == "Success"
            and record.approved_settings_sha256 == self._approval_fingerprint(image_id)
        )
        if not unchanged:
            if was_approved:
                record.last_message = "Approval was cleared: this image was analyzed again with different settings."
                record.approved_settings_sha256 = ""
            record.processing_status = "needs_attention" if status != "Success" else "analyzed"
        record.last_result = status
        record.last_message = message or record.last_message
        self.last_results[image_id] = result
        if persist:
            progress.update("Saving results")
            self._ensure_run([record], fresh=new_run)
            assert self.run_dir is not None and self.run_record is not None and self.run_log is not None
            persist_image_result(self.run_dir, result, self._active_edits(image_id))
            self._record_recipe_use(image_id, result)
            self.run_record.manual_edits[image_id] = [item.model_dump(mode="json") for item in self._active_edits(image_id)]
            self.run_record.input_hashes[image_id] = record.content_hash
            if message:
                self.run_log.write(record.filename, "result", message)
                if status == "Warning":
                    self.run_record.warnings.append(f"{record.filename}: {message}")
            self.experiment.latest_run_id = self.run_record.run_id
            finish_run(self.run_dir, self.run_record, "completed")
            self.save()
        return result

    def run_images(
        self,
        image_ids: list[str] | None = None,
        *,
        on_progress=None,
        should_continue=None,
    ) -> BatchReport:
        selected = list(image_ids) if image_ids is not None else self.included_ids()
        records = [self.experiment.image(image_id) for image_id in selected]
        self._ensure_run(records, fresh=True)
        assert self.run_dir is not None and self.run_record is not None and self.run_log is not None
        jobs: list[ImageJobResult] = []
        total = len(records)
        cancelled = False
        for index, record in enumerate(records, start=1):
            if should_continue is not None and not should_continue():
                self.run_log.write(record.filename, "batch", "Cancelled.")
                cancelled = True
                break
            if on_progress is not None:
                on_progress(index, total, record.filename, "running")
            try:
                result = self.run_image(record.image_id, persist=True, new_run=False)
            except AnalysisCancelled:
                # Stopped part-way through this image: nothing from it is kept; earlier images are.
                self.run_log.write(record.filename, "batch", "Cancelled during this image.")
                cancelled = True
                break
            except Exception as exc:
                message = exc.args[0] if exc.args else "Analysis failed."
                if not isinstance(exc, CellQuantError):
                    message = "Analysis failed."
                record.processing_status = "needs_attention"
                record.last_result = "Failure"
                record.last_message = str(message)
                self.run_log.write(record.filename, "batch", str(message), exc)
                self.run_record.errors.append(f"{record.filename}: {message}")
                jobs.append(ImageJobResult(record.image_id, record.filename, "Failure", str(message)))
            else:
                status, message = _job_status(result)
                jobs.append(
                    ImageJobResult(
                        record.image_id,
                        record.filename,
                        status,
                        message,
                        result.qc.status,
                    )
                )
            if on_progress is not None:
                on_progress(index, total, record.filename, jobs[-1].status)
        if cancelled:
            # Stopped early: never recorded as a finished run, whatever the finished images did.
            batch_status = "cancelled"
        elif jobs and all(job.status == "Failure" for job in jobs):
            batch_status = "failed"
        elif any(job.status == "Failure" for job in jobs):
            batch_status = "completed_with_failures"
        else:
            batch_status = "completed"
        finish_run(self.run_dir, self.run_record, batch_status)
        self.save()
        return BatchReport(
            self.run_record.run_id, str(self.run_dir), jobs, analysis=self.active_analysis().name, cancelled=cancelled
        )

    def preview(self, image_id: str, crop: tuple[int, int, int, int] | None = None) -> np.ndarray:
        """Segment a crop. The result is not cached and is not a saved run."""

        record = self.experiment.image(image_id)
        loaded = self._load_record(record)
        height, width = loaded.shape_yx
        y0, y1, x0, x1 = crop or (0, height, 0, width)
        y0, y1 = _clamp(y0, y1, height)
        x0, x1 = _clamp(x0, x1, width)
        from dataclasses import replace

        from cellquant.pipeline import segment_channel

        # A 3D preview segments every slice of the visible area, so linking can be checked too.
        cropped = replace(loaded, data=np.ascontiguousarray(loaded.data[..., y0:y1, x0:x1]))
        with self._image_settings(record):
            labels = segment_channel(cropped, self.recipe, record_timing=False)
        full = np.zeros(loaded.spatial_shape, dtype=np.int32)
        full[..., y0:y1, x0:x1] = labels
        return full

    def update_thresholds(self, image_id: str, thresholds: dict[str, float]) -> ImageResult | None:
        """Reclassify the current measurements. Segmentation is not repeated."""

        data = self.recipe.model_dump(mode="json")
        known = {item["id"]: item for item in data["classifications"]}
        for classification_id, threshold in thresholds.items():
            if classification_id not in known:
                raise KeyError(classification_id)
            known[classification_id]["threshold"] = float(threshold)
        self.set_recipe(data)
        prior = self.recall(image_id)
        if prior is None:
            return None
        result = reclassify_result(prior, self.recipe)
        self.last_results[image_id] = result
        return result

    def delete_object(self, image_id: str, object_id: int) -> ImageResult:
        self._edit_and_remeasure(image_id, EditOperation.create(image_id, int(object_id), "delete"))
        return self.last_results[image_id]

    def restore_object(self, image_id: str, object_id: int) -> ImageResult:
        self._edit_and_remeasure(image_id, EditOperation.create(image_id, int(object_id), "restore"))
        return self.last_results[image_id]

    def undo(self, image_id: str) -> ImageResult | None:
        edits = self.edits.get(image_id, [])
        active = self._active_edits(image_id)
        if not active:
            return self.last_results.get(image_id)
        last = active[-1]
        # Remove that exact edit; edits made on other segmentations stay.
        for index in range(len(edits) - 1, -1, -1):
            if edits[index] is last:
                del edits[index]
                break
        save_edits(self.directory, image_id, edits)
        self._remeasure_cached(image_id)
        return self.last_results.get(image_id)

    def commit_drawn_labels(self, image_id: str, drawn: np.ndarray) -> ImageResult:
        current = self.last_results.get(image_id)
        if current is None:
            current = self.run_image(image_id, persist=False, new_run=False)
        operations = operations_from_diff(current.labels, np.asarray(drawn), image_id)
        stamp = self._current_segmentation(image_id)
        for operation in operations:
            operation.segmentation = stamp
            operation.analysis = self.recipe.recipe_id or ""
        self.edits.setdefault(image_id, []).extend(operations)
        save_edits(self.directory, image_id, self.edits[image_id])
        self._remeasure_cached(image_id)
        return self.last_results[image_id]

    def _current_segmentation(self, image_id: str) -> str:
        """Cache key of the segmentation on screen for this image, or blank when unknown."""

        if image_id in self._segmentation_ids:
            return self._segmentation_ids[image_id]
        result = self.recall(image_id)
        key = str(result.provenance.get("segmentation_key") or "") if result is not None else ""
        if key:
            self._segmentation_ids[image_id] = key
        return key

    def _active_edits(self, image_id: str, segmentation: str | None = None) -> list[EditOperation]:
        """Edits made on the current segmentation (and older edits with no segmentation recorded)."""

        current = self._segmentation_ids.get(image_id, "") if segmentation is None else segmentation
        analysis = self.recipe.recipe_id
        legacy = self._owns_legacy()
        # An edit belongs to the analysis it was made in; edits from before analyses existed belong
        # to the original analysis.
        edits = [item for item in self.edits.get(image_id, []) if item.analysis == analysis or (not item.analysis and legacy)]
        if not current:
            return edits
        return [item for item in edits if item.segmentation in (current, "")]

    def _edit_and_remeasure(self, image_id: str, operation: EditOperation) -> None:
        operation.segmentation = self._current_segmentation(image_id)
        operation.analysis = self.recipe.recipe_id or ""
        self.edits.setdefault(image_id, []).append(operation)
        save_edits(self.directory, image_id, self.edits[image_id])
        record = self.experiment.image(image_id)
        if record.processing_status == "approved":
            record.processing_status = "analyzed"
            record.last_message = "Approval was cleared because an object was edited."
        self._remeasure_cached(image_id)

    def _remeasure_cached(self, image_id: str) -> ImageResult:
        """Re-measure from the image already in memory. Does not hash the file."""

        with self._image_settings(self.experiment.image(image_id)):
            return self._remeasure_cached_here(image_id)

    def _remeasure_cached_here(self, image_id: str) -> ImageResult:
        record = self.experiment.image(image_id)
        prior = self.last_results.get(image_id)
        loaded = self._session_images.get(image_id)
        if loaded is not None:
            self._session_images.move_to_end(image_id)
        if prior is None or loaded is None:
            saved = prior or self.recall(image_id)
            # An edit never segments again when the objects came from elsewhere (a cluster run):
            # it re-measures the saved labels, or explains why it cannot.
            from_elsewhere = saved is not None and saved.provenance.get("segmentation_origin") == "hpc"
            result = self._analyze(record, allow_segmentation=not from_elsewhere)
            self.last_results[image_id] = result
            self._write_working(image_id, result)
            return result
        result = process_image(
            loaded,
            self.recipe,
            sample_name=record.sample_name,
            image_id=record.image_id,
            experiment_id=self.experiment.experiment_id,
            run_id=None,
            filename=record.filename,
            user_metadata=record.user_metadata,
            manual_edits=self._active_edits(image_id),
            automated_labels=prior.automated_labels,
            segmentation_details=_details_to_keep(prior),
        )
        carry_segmentation_provenance(prior, result)
        self.last_results[image_id] = result
        self._write_working(image_id, result)
        return result

    def _remember_image(self, image_id: str, loaded: LoadedImage) -> None:
        self._session_images[image_id] = loaded
        self._session_images.move_to_end(image_id)
        while len(self._session_images) > SESSION_IMAGE_LIMIT:
            self._session_images.popitem(last=False)

    def _write_working(self, image_id: str, result: ImageResult) -> None:
        destination = self._working_dir()
        for relative in ("labels", "measurements", "classifications", "summaries", "provenance", "edits"):
            (destination / relative).mkdir(parents=True, exist_ok=True)
        persist_image_result(destination, result, self._active_edits(image_id))

    def _record_recipe_use(self, image_id: str, result: ImageResult) -> None:
        if self.run_record is None:
            return
        used = getattr(self.run_record, "recipe_hashes", None)
        if used is None:
            self.run_record.recipe_hashes = {}
        self.run_record.recipe_hashes[image_id] = result.provenance.get("recipe_sha256", "")

    def update_pixel_levels(
        self,
        image_id: str | None,
        levels: dict[str, tuple[float, float | None]],
    ) -> ImageResult | None:
        """Change the pixel level (and optional upper level) of 'percent_above' measurements.

        ``levels`` maps a measurement id to ``(pixel_level, pixel_level_high or None)``.
        The image on screen is measured again from its current objects; segmentation is
        not repeated and manual edits are kept. Other images use the new level when they
        are next run or opened.
        """

        data = self.recipe.model_dump(mode="json")
        known = {item["id"]: item for item in data["measurements"]}
        for measurement_id, (low, high) in levels.items():
            item = known.get(measurement_id)
            if item is None:
                raise KeyError(measurement_id)
            if item.get("statistic") != "percent_above":
                raise CellQuantError(f"Measurement '{measurement_id}' does not use a pixel level.")
            item["pixel_level"] = float(low)
            item["pixel_level_high"] = None if high is None else float(high)
        self.set_recipe(data)
        if image_id is None or (image_id not in self.last_results and self.recall(image_id) is None):
            return None
        return self._remeasure_cached(image_id)

    def histogram(self, image_id: str, measurement_id: str, threshold: float, comparison: str = "above") -> dict[str, float]:
        result = self.last_results.get(image_id)
        if result is None or measurement_id not in result.objects.columns:
            return {"positive": 0, "negative": 0, "percent_positive": float("nan"), "n_missing": 0}
        active = result.objects
        if "excluded" in active.columns:
            active = active.loc[~active["excluded"].astype(bool)]
        return classification_counts(active[measurement_id].to_numpy(dtype=float), threshold, comparison)

    def export(self, directory: str | Path, *, group_by: str | None = None) -> Path:
        destination = Path(directory)
        results = self._results_for_export()
        if not results:
            raise CellQuantError("There are no results to export yet.")
        destination.mkdir(parents=True, exist_ok=True)
        objects = pd.concat([item.objects for item in results], ignore_index=True)
        summaries = pd.concat([item.summary for item in results], ignore_index=True)
        objects.to_csv(destination / "objects.csv", index=False)
        summaries.to_csv(destination / "image_summary.csv", index=False)
        index_rows = [
            {
                "image_id": item.provenance.get("image_id"),
                "filename": item.provenance.get("filename"),
                "recipe_sha256": item.provenance.get("recipe_sha256"),
            }
            for item in results
        ]
        pd.DataFrame(index_rows).to_csv(destination / "settings_index.csv", index=False)
        hashes = self._settings_versions(results)
        self.export_recipe(destination / "recipe.yaml")
        mixed_note = destination / "mixed_settings.txt"
        if len(hashes) <= 1 and mixed_note.is_file():
            mixed_note.unlink()
        if len(hashes) > 1:
            (destination / "mixed_settings.txt").write_text(
                "These results were produced with more than one analysis settings version. "
                "settings_index.csv lists the settings hash for each image. "
                "recipe.yaml is the current analysis settings, not a single label for every row.\n",
                encoding="utf-8",
            )
        run_dir = self._latest_run_dir()
        if run_dir is not None and (run_dir / "run.json").is_file():
            (destination / "run.json").write_text((run_dir / "run.json").read_text(encoding="utf-8"), encoding="utf-8")
        if group_by:
            grouped_summary(summaries, group_by).to_csv(destination / f"grouped_by_{group_by}.csv", index=False)
        return destination

    def _settings_versions(self, results: list[ImageResult]) -> set:
        """Distinct settings among these results. Channels that differ only because an image uses its own
        channels (a per-image choice, or another channel layout) do not count as different settings."""

        versions = set()
        for result in results:
            recipe = result.provenance.get("recipe")
            image_id = result.provenance.get("image_id")
            if not isinstance(recipe, dict) or image_id is None:
                versions.add(("hash", result.provenance.get("recipe_sha256")))
                continue
            try:
                record = self.experiment.image(str(image_id))
            except KeyError:
                versions.add(("hash", result.provenance.get("recipe_sha256")))
                continue
            used = (
                int(recipe.get("object_set", {}).get("segmentation_channel", -1)),
                tuple(int(item.get("channel", -1)) for item in recipe.get("measurements", [])),
            )
            with self._image_settings(record):
                expected = (
                    int(self.recipe.object_set.segmentation_channel),
                    tuple(int(item.channel) for item in self.recipe.measurements),
                )
            try:
                normalized = load_recipe(
                    {
                        **recipe,
                        "object_set": {**recipe.get("object_set", {}), "segmentation_channel": 0},
                        "measurements": [{**item, "channel": 0} for item in recipe.get("measurements", [])],
                    }
                )
            except Exception:  # noqa: BLE001 - settings saved by another version: compare them as they are
                versions.add(("hash", result.provenance.get("recipe_sha256")))
                continue
            versions.add((normalized.content_hash(), used == expected))
        return versions

    def _results_for_export(self) -> list[ImageResult]:
        """Results for every included image: open ones first, then saved ones.

        Saved results are read without keeping them in memory, so exporting a
        large experiment does not load every label image at once.
        """

        folders = self._result_folders()
        results: list[ImageResult] = []
        for record in self.experiment.images:
            if not record.include:
                continue
            result = self.last_results.get(record.image_id)
            for folder in folders:
                if result is not None:
                    break
                result = read_persisted_result(folder, record.image_id)
            if result is not None:
                results.append(result)
        return results

    def objects_for_export(self) -> pd.DataFrame:
        frames = [result.objects for result in self.last_results.values()]
        if not frames and self.experiment.latest_run_id:
            folder = self.directory / "runs" / self.experiment.latest_run_id / "measurements"
            frames = [pd.read_csv(path) for path in sorted(folder.glob("*.csv"))]
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    def remeasure_persisted_result(self, image_id: str) -> ImageResult:
        """Measure the saved objects of this image again, with the current edits. Never segments."""

        record = self.experiment.image(image_id)
        result = self._analyze(record, allow_segmentation=False)
        self.last_results[image_id] = result
        self._write_working(image_id, result)
        return result

    def _persisted_segmentation(self, record, loaded: LoadedImage, digest: str) -> ImageResult | None:
        """A saved segmentation made elsewhere (a cluster run) that still fits this image and these settings."""

        prior = self.last_results.get(record.image_id) or self.recall(record.image_id)
        if prior is None or prior.provenance.get("segmentation_origin") != "hpc":
            return None
        provenance = prior.provenance
        wanted = segmentation_settings(self.recipe, loaded.z_index)
        same_settings = _canonical(provenance.get("segmentation_settings")) == _canonical(wanted)
        calibration = [record.pixel_size_x, record.pixel_size_y, record.pixel_size_z]
        same_calibration = _same_numbers(provenance.get("segmentation_calibration"), calibration)
        same_image = provenance.get("segmentation_input_sha256") == digest
        same_shape = tuple(prior.automated_labels.shape) == tuple(loaded.spatial_shape)
        return prior if same_settings and same_calibration and same_image and same_shape else None

    def _analyze(self, record, *, allow_segmentation: bool = True) -> ImageResult:
        with self._image_settings(record):
            analyzed = self._cached_analysis(record, remeasure=True, allow_segmentation=allow_segmentation)
        if analyzed is None:
            raise ImageLoadError("File could not be opened.")
        result, loaded = analyzed
        self._remember_image(record.image_id, loaded)
        return result

    def _cached_analysis(self, record, *, remeasure: bool, allow_segmentation: bool = True) -> tuple[ImageResult, LoadedImage] | None:
        progress.update("Reading the image file")
        try:
            loaded = self._load_record(record)
        except ImageLoadError:
            raise
        progress.update("Checking whether the file changed since the last run")
        signature, digest = self._fingerprint(record)
        record.file_signature = signature
        record.content_hash = digest
        persisted = self._persisted_segmentation(record, loaded, digest)
        if persisted is not None:
            # Objects from a cluster run: measured again here without the segmentation engine.
            key = str(persisted.provenance.get("segmentation_key") or "")
            self._segmentation_ids[record.image_id] = key
            progress.update("Measuring markers in each object")
            result = remeasure_persisted_result(
                loaded,
                self.recipe,
                persisted,
                manual_edits=self._active_edits(record.image_id, key),
                sample_name=record.sample_name,
                image_id=record.image_id,
                experiment_id=self.experiment.experiment_id,
                run_id=self.run_record.run_id if self.run_record else self.experiment.latest_run_id,
                filename=record.filename,
                user_metadata=record.user_metadata,
            )
            return result, loaded
        if not allow_segmentation:
            raise CellQuantError(
                "These objects came from the cluster run, but the image, its pixel sizes or the segmentation "
                "settings have changed since, so the saved objects no longer apply. Editing never segments again: "
                "restore the settings, or click Run to segment this image again on purpose."
            )
        segmentation_payload = {
            "image_sha256": digest,
            "position": record.position,
            "z_stack": self.recipe.z_stack,
            "z_index": self.recipe.z_index,
            "z_settings": {
                key: value
                for key, value in self.recipe.scientific_dict().items()
                if key in ("z_stitch_threshold", "z_scale_brightness", "z_min_slices")
            },
            "pixel_size_z": loaded.pixel_size_z if loaded.is_3d else None,
            "object_set": self.recipe.object_set.model_dump(mode="json"),
            # Engine, version and model: labels from Cellpose 3 are never reused under Cellpose 4.
            "engine": engine_signature(self.recipe.object_set.algorithm, self.recipe.object_set.parameters),
            "pixel_size_x": loaded.pixel_size_x,
            "pixel_size_y": loaded.pixel_size_y,
        }
        segmentation_id = stage_key("segmentation", segmentation_payload)
        automated = self.cache.get_labels(segmentation_id)
        details = self.cache.get_meta(segmentation_id) or {}
        if automated is None or automated.shape != loaded.spatial_shape:
            if not remeasure:
                return None
            automated = None
        self._segmentation_ids[record.image_id] = segmentation_id
        edits = self._active_edits(record.image_id, segmentation_id)
        measurement_payload = {
            "segmentation": segmentation_id,
            "measurements": [item.model_dump(mode="json") for item in self.recipe.measurements],
            "edits": [item.model_dump(mode="json") for item in edits],
            "pixel_size_x": loaded.pixel_size_x,
            "pixel_size_y": loaded.pixel_size_y,
            "pixel_size_z": loaded.pixel_size_z if loaded.is_3d else None,
        }
        measurement_id = stage_key("measurement", measurement_payload)
        measured = self.cache.get_table(measurement_id)
        if automated is not None and measured is not None:
            final_labels = apply_edits(automated, edits)
            result = assemble_result(
                loaded=loaded,
                recipe=self.recipe,
                automated_labels=automated,
                labels=final_labels,
                measured=measured,
                measure_warnings=[],
                edits=edits,
                segmentation_details=details.get("segmentation_details", details),
                sample_name=record.sample_name,
                image_id=record.image_id,
                experiment_id=self.experiment.experiment_id,
                run_id=self.run_record.run_id if self.run_record else self.experiment.latest_run_id,
                filename=record.filename,
                user_metadata=record.user_metadata,
            )
            result.provenance["segmentation_key"] = segmentation_id
            return result, loaded
        if not remeasure:
            return None
        result = process_image(
            loaded,
            self.recipe,
            sample_name=record.sample_name,
            image_id=record.image_id,
            experiment_id=self.experiment.experiment_id,
            run_id=self.run_record.run_id if self.run_record else None,
            filename=record.filename,
            user_metadata=record.user_metadata,
            manual_edits=edits,
            automated_labels=automated,
            segmentation_details=details.get("segmentation_details"),
        )
        self.cache.put_labels(segmentation_id, result.automated_labels)
        stored_details = _details_to_keep(result)
        self.cache.put_meta(segmentation_id, {"segmentation_details": stored_details})
        result.provenance["segmentation_key"] = segmentation_id
        self.cache.put_table(measurement_id, measurement_values(result, self.recipe))
        return result, loaded

    def _load_record(self, record) -> LoadedImage:
        return load_record_image(
            record,
            z_mode=self.recipe.z_stack,
            z_index=self.recipe.z_index,
            experiment_dir=self.directory,
        )

    def source_path(self, record) -> Path:
        """The image file this record reads (its own copy inside the experiment, when it has one)."""

        return resolve_source_path(record, self.directory)

    def _fingerprint(self, record) -> tuple[str, str]:
        path = self.source_path(record)
        stat = path.stat()
        signature = f"{stat.st_mtime_ns}:{stat.st_size}"
        if record.file_signature == signature and record.content_hash:
            return signature, record.content_hash
        return file_fingerprint(path)

    def _ensure_run(self, records, fresh: bool = False) -> None:
        if self.run_dir is not None and not fresh:
            return
        self._touch_recipe()
        record, run_dir = start_run(self.experiment, self.recipe, records)
        self.run_record = record
        self.run_dir = run_dir
        self.run_log = RunLog(run_dir)
        self.experiment.latest_run_id = record.run_id

    def _load_bound_recipe(self) -> Recipe:
        path = self._recipe_path()
        if path.is_file():
            recipe = load_recipe(path)
        else:
            recipe = Recipe(
                recipe_id=f"recipe_{self.experiment.experiment_id[-8:]}",
                recipe_name="Recipe",
                software_version=__version__,
                object_set={
                    "name": "Objects",
                    "segmentation_channel": 0,
                    "algorithm": "classical",
                    "parameters": {"threshold_method": "otsu"},
                },
            )
        if not recipe.recipe_id:
            recipe.recipe_id = f"recipe_{self.experiment.experiment_id[-8:]}"
        return recipe

    def _recipe_path(self) -> Path:
        recipe_id = self.recipe.recipe_id if hasattr(self, "recipe") and self.recipe.recipe_id else None
        if recipe_id is None and self.experiment.recipe_id:
            recipe_id = self.experiment.recipe_id
        name = recipe_id or "recipe"
        return self.directory / "recipes" / f"{name}.yaml"

    def _touch_recipe(self) -> None:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        if not self.recipe.created_at:
            self.recipe.created_at = now
        self.recipe.modified_at = now
        self.recipe.software_version = __version__
        if not self.recipe.recipe_id:
            self.recipe.recipe_id = f"recipe_{self.experiment.experiment_id[-8:]}"

    # -- analyses: several settings (for example one per channel) on the same images ----------------

    def analyses(self) -> list[AnalysisRecord]:
        """The analyses on the list (removed ones are kept on file but not listed)."""

        return [item for item in self.experiment.analyses if not item.removed]

    def active_analysis(self) -> AnalysisRecord:
        self._ensure_analyses()
        for item in self.experiment.analyses:
            if item.recipe_id == self.recipe.recipe_id:
                return item
        # The settings were given a new identity from outside (an old-style recipe file): adopt it.
        item = self.experiment.analyses[0] if len(self.experiment.analyses) == 1 and not self.experiment.analyses[0].removed else None
        if item is not None:
            item.recipe_id = self.recipe.recipe_id or item.recipe_id
            return item
        item = AnalysisRecord(recipe_id=self.recipe.recipe_id, name=self._unique_name("Analysis"), working_folder=self._new_working_folder(self.recipe.recipe_id))
        self.experiment.analyses.append(item)
        return item

    def add_analysis(self, name: str, recipe: Recipe | dict | None = None, *, activate: bool = True) -> str:
        """Add an analysis of the same images: a copy of the current settings, or the settings given.

        Returns its id. Its results are kept apart from every other analysis's.
        """

        import uuid

        source = load_recipe(recipe) if recipe is not None else self.recipe
        new_id = f"recipe_{uuid.uuid4().hex[:8]}"
        data = source.model_dump(mode="json")
        name = self._unique_name(name.strip() or "Analysis")
        data.update(recipe_id=new_id, recipe_name=name, created_at=None, modified_at=None)
        created = load_recipe(data)
        save_recipe(created, self.directory / "recipes" / f"{new_id}.yaml")
        self.experiment.analyses.append(
            AnalysisRecord(recipe_id=new_id, name=name, working_folder=self._new_working_folder(new_id))
        )
        if activate:
            self.switch_analysis(new_id)
        else:
            self.save()
        return new_id

    def switch_analysis(self, recipe_id: str) -> None:
        """Make another analysis active: its settings, its results and its review state."""

        target = self._analysis(recipe_id)
        if recipe_id == self.recipe.recipe_id:
            return
        self.save()
        current = self.active_analysis()
        current.image_states = {
            record.image_id: {name: str(getattr(record, name) or "") for name in IMAGE_STATE_FIELDS}
            for record in self.experiment.images
        }
        current.latest_run_id = self.experiment.latest_run_id
        path = self.directory / "recipes" / f"{recipe_id}.yaml"
        restored_excluded = []
        self.recipe = load_recipe(path) if path.is_file() else self.recipe.model_copy(update={"recipe_id": recipe_id})
        self.recipe.recipe_id = recipe_id
        for record in self.experiment.images:
            state = target.image_states.get(record.image_id, {})
            record.processing_status = state.get("processing_status") or "not_analyzed"
            record.last_result = state.get("last_result", "")
            record.last_message = state.get("last_message", "")
            record.approved_settings_sha256 = state.get("approved_settings_sha256", "")
            if not record.include:
                record.processing_status = "excluded"
            elif record.processing_status == "excluded":
                restored_excluded.append(record)  # included again while another analysis was shown
        self.experiment.latest_run_id = target.latest_run_id
        self.experiment.recipe_id = recipe_id
        self.last_results = {}
        self._session_images.clear()
        self._segmentation_ids = {}
        self.run_record = None
        self.run_dir = None
        self.run_log = None
        for record in restored_excluded:
            record.processing_status = "analyzed" if self.recall(record.image_id) is not None else "not_analyzed"
        self.save()

    def rename_analysis(self, recipe_id: str, name: str) -> None:
        item = self._analysis(recipe_id)
        name = name.strip()
        if not name:
            raise CellQuantError("An analysis needs a name.")
        if name != item.name:
            item.name = self._unique_name(name)
        if recipe_id == self.recipe.recipe_id:
            self.recipe.recipe_name = item.name
        self.save()

    def remove_analysis(self, recipe_id: str) -> None:
        """Take an analysis off the list. Its settings and results stay in the experiment folder."""

        if len(self.analyses()) <= 1:
            raise CellQuantError("An experiment keeps at least one analysis.")
        item = self._analysis(recipe_id)
        if recipe_id == self.recipe.recipe_id:
            other = next(entry for entry in self.analyses() if entry.recipe_id != recipe_id)
            self.switch_analysis(other.recipe_id)
        item.removed = True
        self.save()

    def analyses_for_channels(self, channels: list[int] | None = None) -> list[str]:
        """One analysis per channel: the current settings, finding objects in each channel in turn.

        A channel the current analysis (or another with the same settings) already finds objects in
        is not added again. Returns the ids of the analyses for these channels, in channel order.
        The active analysis does not change.
        """

        wanted = [channel.channel_index for channel in self.experiment.channels] if channels is None else list(channels)
        names = {channel.channel_index: channel.channel_name for channel in self.experiment.channels}
        base = self.recipe.model_dump(mode="json")
        ids = []
        for channel in wanted:
            data = dict(base)
            data["object_set"] = {**base["object_set"], "segmentation_channel": int(channel)}
            existing = self._analysis_with_settings(load_recipe({**data, "recipe_id": None}))
            if existing is not None:
                ids.append(existing)
                continue
            ids.append(self.add_analysis(f"Objects in {names.get(channel, f'channel {channel + 1}')}", data, activate=False))
        return ids

    def run_analyses(
        self,
        recipe_ids: list[str] | None = None,
        image_ids: list[str] | None = None,
        *,
        on_progress=None,
        should_continue=None,
        on_analysis=None,
    ) -> list[BatchReport]:
        """Run each analysis over the same images, one after another. The active analysis is restored.

        ``on_progress(index, total, filename, status)`` counts across all analyses; ``filename`` is
        prefixed with the analysis name. ``on_analysis(index, total, name)`` is called as each starts.
        Stopping ends the current analysis's run (finished images are kept) and skips the rest.
        """

        chosen = [item.recipe_id for item in self.analyses()] if recipe_ids is None else list(recipe_ids)
        for recipe_id in chosen:
            self._analysis(recipe_id)
        original = self.recipe.recipe_id
        # Without a list of images, each analysis runs the images its plan ticks.
        work = [(recipe_id, list(image_ids) if image_ids is not None else self.planned_images(recipe_id)) for recipe_id in chosen]
        work = [(recipe_id, images) for recipe_id, images in work if images]
        total = sum(len(images) for _recipe_id, images in work)
        reports: list[BatchReport] = []
        offset = 0
        try:
            for position, (recipe_id, images) in enumerate(work):
                if should_continue is not None and not should_continue():
                    break
                self.switch_analysis(recipe_id)
                name = self.active_analysis().name
                if on_analysis is not None:
                    on_analysis(position + 1, len(work), name)

                def progress_across(index, _count, filename, status, offset=offset, label=name):
                    if on_progress is not None:
                        on_progress(offset + index, total, f"{label}: {filename}", status)

                offset += len(images)
                report = self.run_images(images, on_progress=progress_across, should_continue=should_continue)
                reports.append(report)
                if report.cancelled:
                    break
        finally:
            if original and original != self.recipe.recipe_id and any(item.recipe_id == original for item in self.analyses()):
                self.switch_analysis(original)
        return reports

    def export_all(self, directory: str | Path, *, group_by: str | None = None) -> Path:
        """Export every analysis that has results, one folder each, and one table of all image summaries."""

        import re

        destination = Path(directory)
        destination.mkdir(parents=True, exist_ok=True)
        original = self.recipe.recipe_id
        summaries, skipped, used = [], [], set()
        try:
            for item in self.analyses():
                self.switch_analysis(item.recipe_id)
                slug = re.sub(r"[^A-Za-z0-9._-]+", "_", item.name).strip("_") or item.recipe_id
                while slug in used:
                    slug += "_"
                used.add(slug)
                if not self._results_for_export():
                    skipped.append(item.name)
                    continue
                folder = self.export(destination / slug, group_by=group_by)
                table = pd.read_csv(folder / "image_summary.csv")
                table.insert(0, "segmentation_channel", self._channel_name(self.recipe.object_set.segmentation_channel))
                table.insert(0, "analysis", item.name)
                summaries.append(table)
        finally:
            if original != self.recipe.recipe_id:
                self.switch_analysis(original)
        if not summaries:
            raise CellQuantError("There are no results to export yet.")
        combined = pd.concat(summaries, ignore_index=True)
        combined.to_csv(destination / "all_analyses_image_summary.csv", index=False)
        if group_by:
            grouped = [grouped_summary(frame.drop(columns=["analysis", "segmentation_channel"]), group_by).assign(analysis=frame["analysis"].iloc[0]) for frame in summaries]
            pd.concat(grouped, ignore_index=True).to_csv(destination / f"all_analyses_grouped_by_{group_by}.csv", index=False)
        note = destination / "analyses_not_exported.txt"
        if skipped:
            note.write_text("These analyses have no results yet, so they were not exported:\n" + "\n".join(skipped) + "\n", encoding="utf-8")
        elif note.is_file():
            note.unlink()
        return destination

    # -- plan: which images each analysis runs on, and per-image channels ------------------------

    def is_planned(self, image_id: str, recipe_id: str | None = None) -> bool:
        """Whether this analysis runs this image: included, and not unticked in its plan."""

        record = self.experiment.image(image_id)
        entry = self._analysis(recipe_id or self.recipe.recipe_id).plan.get(image_id)
        return bool(record.include) and not (entry is not None and entry.run is False)

    def planned_images(self, recipe_id: str | None = None) -> list[str]:
        return [record.image_id for record in self.experiment.images if self.is_planned(record.image_id, recipe_id)]

    def set_planned(self, image_ids: list[str], run: bool, recipe_ids: list[str] | None = None) -> None:
        """Tick (run=True) or untick these images in these analyses (all listed ones when not given).

        Ticking an image that is left out of the experiment also includes it again.
        """

        targets = [self._analysis(recipe_id) for recipe_id in (recipe_ids or [item.recipe_id for item in self.analyses()])]
        for image_id in image_ids:
            record = self.experiment.image(image_id)
            if run and not record.include:
                self.set_included(image_id, True)
            for item in targets:
                entry = item.plan.get(image_id) or PlanEntry()
                entry.run = None if run else False
                self._store_entry(item, image_id, entry)
        self.save()

    def set_plan_channel(self, image_ids: list[str], channel: int | None, recipe_ids: list[str] | None = None) -> None:
        """Find objects in ``channel`` (an index in each image's own channels) for these images only.

        ``None`` goes back to the analysis's channel. Applies to the active analysis unless others are given.
        """

        targets = [self._analysis(recipe_id) for recipe_id in (recipe_ids or [self.recipe.recipe_id])]
        for image_id in image_ids:  # check every image before changing any
            record = self.experiment.image(image_id)
            count = record.number_of_channels or len(record.channel_names) or len(self.experiment.channels)
            if channel is not None and not 0 <= int(channel) < max(count, 1):
                label = record.relative_path or record.filename
                raise CellQuantError(f"{label} has {count} channels; channel {int(channel) + 1} does not exist.")
        for image_id in image_ids:
            for item in targets:
                entry = item.plan.get(image_id) or PlanEntry()
                entry.channel = None if channel is None else int(channel)
                self._store_entry(item, image_id, entry)
        self.save()

    def segmentation_channel_for(self, image_id: str, recipe_id: str | None = None) -> tuple[int, str]:
        """The channel this analysis finds objects in for this image, and why.

        Reasons: ``"chosen for this image"``; ``"analysis"`` (the analysis's channel); ``"same name"``
        (the image lists that channel's name at another position); ``"not in this image"`` (its
        layout has no channel of that name, so the position is used: worth checking).
        """

        recipe_id = recipe_id or self.recipe.recipe_id
        record = self.experiment.image(image_id)
        entry = self._analysis(recipe_id).plan.get(image_id)
        if entry is not None and entry.channel is not None:
            return int(entry.channel), "chosen for this image"
        return self._map_channel(record, int(self._recipe_of(recipe_id).object_set.segmentation_channel))

    def _map_channel(self, record, index: int) -> tuple[int, str]:
        """A channel of the experiment's channel list, found in this image by its name in the file.

        Images with another channel layout (the same channels in another order) then use the right
        channel. When the image has no channel of that name, the position is kept.
        """

        reference = self._reference_channel_names()
        names = list(record.channel_names)
        if reference and names and names != reference and index < len(reference):
            wanted = reference[index]
            if wanted in names:
                found = names.index(wanted)
                return found, ("same name" if found != index else "analysis")
            return index, "not in this image"
        return index, "analysis"

    def display_channels(self, record, n_channels: int) -> list[int | None]:
        """For each channel position in this image's file: the experiment channel it is, or None.

        Matched by the names stored in the file, as for analysis, so an image with another channel
        layout shows each layer under the right name. None: the file names a channel the experiment's
        list does not have.
        """

        reference = self._reference_channel_names()
        names = list(record.channel_names)
        known = {channel.channel_index for channel in self.experiment.channels}
        if not reference or not names or names == reference:
            return [index if index in known else None for index in range(n_channels)]
        owners: list[int | None] = []
        for position in range(n_channels):
            name = names[position] if position < len(names) else None
            owners.append(reference.index(name) if name in reference else None)
        return owners

    def measurement_channels_for(self, image_id: str, recipe_id: str | None = None) -> dict[str, tuple[int, str]]:
        """For each measurement of this analysis: the channel it reads in this image, and why."""

        record = self.experiment.image(image_id)
        recipe = self._recipe_of(recipe_id or self.recipe.recipe_id)
        return {item.id: self._map_channel(record, int(item.channel)) for item in recipe.measurements}

    def plan_status(self, image_id: str, recipe_id: str | None = None) -> str:
        """For the Plan dock: 'not planned', 'not run', 'analyzed', 'reviewed', 'approved', 'needs attention' or 'failed'."""

        recipe_id = recipe_id or self.recipe.recipe_id
        if not self.is_planned(image_id, recipe_id):
            return "not planned"
        record = self.experiment.image(image_id)
        if recipe_id == self.recipe.recipe_id:
            status, last = record.processing_status, record.last_result
        else:
            state = self._analysis(recipe_id).image_states.get(image_id, {})
            status, last = state.get("processing_status", "not_analyzed"), state.get("last_result", "")
        if last == "Failure":
            return "failed"
        return {"not_analyzed": "not run", "excluded": "not run", "needs_attention": "needs attention"}.get(status, status)

    def _store_entry(self, item: AnalysisRecord, image_id: str, entry: PlanEntry) -> None:
        if entry.run is None and entry.channel is None:
            item.plan.pop(image_id, None)
        else:
            item.plan[image_id] = entry

    def _recipe_of(self, recipe_id: str) -> Recipe:
        if recipe_id == self.recipe.recipe_id:
            return self.recipe
        path = self.directory / "recipes" / f"{recipe_id}.yaml"
        if not path.is_file():
            return self.recipe
        stamp = path.stat().st_mtime_ns
        cache = self.__dict__.setdefault("_recipe_files", {})
        if cache.get(recipe_id, (None,))[0] != stamp:
            cache[recipe_id] = (stamp, load_recipe(path))
        return cache[recipe_id][1]

    def _reference_channel_names(self) -> list[str]:
        """File channel names of the layout the experiment's channel list was made from."""

        expected = len(self.experiment.channels)
        for record in self.experiment.images:
            if record.channel_names and len(record.channel_names) == expected:
                return list(record.channel_names)
        return []

    @contextmanager
    def _image_settings(self, record):
        """Analyze this image with its own channels: the segmentation channel chosen for it (or the
        channel of the same name), and each marker read from the channel of the same name."""

        if getattr(self, "_unswapped_recipe", None) is not None:
            yield  # already analyzing this image with its own channels: never map twice
            return
        channel, _reason = self.segmentation_channel_for(record.image_id)
        measured = {item.id: self._map_channel(record, int(item.channel))[0] for item in self.recipe.measurements}
        same = channel == self.recipe.object_set.segmentation_channel and all(
            measured[item.id] == item.channel for item in self.recipe.measurements
        )
        if same:
            yield
            return
        saved = self.recipe
        changed = saved.model_copy(deep=True)
        changed.object_set.segmentation_channel = channel
        for item in changed.measurements:
            item.channel = measured[item.id]
        self.recipe = changed
        self._unswapped_recipe = saved
        try:
            yield
        finally:
            self.recipe = saved
            self._unswapped_recipe = None

    def _channel_name(self, index: int) -> str:
        for channel in self.experiment.channels:
            if channel.channel_index == index:
                return channel.channel_name
        return f"Channel {index + 1}"

    def _ensure_analyses(self) -> None:
        """Experiments saved before analyses existed have one: the current settings and results."""

        if self.experiment.analyses or not hasattr(self, "recipe"):
            return
        name = self.recipe.recipe_name if self.recipe.recipe_name and self.recipe.recipe_name != "Recipe" else "Analysis 1"
        self.experiment.analyses.append(
            AnalysisRecord(
                recipe_id=self.recipe.recipe_id,
                name=name,
                working_folder="working",
                latest_run_id=self.experiment.latest_run_id,
                owns_legacy=True,
            )
        )

    def _analysis(self, recipe_id: str) -> AnalysisRecord:
        self._ensure_analyses()
        for item in self.analyses():
            if item.recipe_id == recipe_id:
                return item
        raise KeyError(recipe_id)

    def _analysis_with_settings(self, recipe: Recipe) -> str | None:
        wanted = recipe.content_hash()
        if self.recipe.content_hash() == wanted:
            return self.recipe.recipe_id
        for item in self.analyses():
            path = self.directory / "recipes" / f"{item.recipe_id}.yaml"
            if item.recipe_id != self.recipe.recipe_id and path.is_file() and load_recipe(path).content_hash() == wanted:
                return item.recipe_id
        return None

    def _owns_legacy(self) -> bool:
        """Whether the active analysis holds what was saved before analyses existed."""

        if not self.experiment.analyses:
            return True
        return self.active_analysis().owns_legacy

    def _recipe_belongs_here(self, recipe_id) -> bool:
        """Whether a saved run with this recipe id holds results of the active analysis."""

        if recipe_id == self.recipe.recipe_id:
            return True
        if not self._owns_legacy():
            return False
        # The original analysis also owns runs made before analyses existed (other or missing ids),
        # but never a run of another analysis, listed or removed.
        others = {item.recipe_id for item in self.experiment.analyses if item.recipe_id != self.recipe.recipe_id}
        return recipe_id not in others

    def _run_belongs_here(self, run_dir: Path) -> bool:
        import json

        try:
            recipe_id = json.loads((run_dir / "run.json").read_text(encoding="utf-8")).get("recipe_id")
        except (OSError, ValueError):
            return self._owns_legacy()
        return self._recipe_belongs_here(recipe_id)

    def _working_dir(self) -> Path:
        if not self.experiment.analyses:
            return self.directory / "working"
        return self.directory / Path(self.active_analysis().working_folder)

    @staticmethod
    def _new_working_folder(recipe_id: str) -> str:
        return f"analyses/{recipe_id}/working"

    def _unique_name(self, name: str) -> str:
        taken = {item.name for item in self.analyses()}
        if name not in taken:
            return name
        counter = 2
        while f"{name} ({counter})" in taken:
            counter += 1
        return f"{name} ({counter})"


def process_experiment(
    experiment_dir: str | Path,
    recipe: Recipe | dict | str | Path | None = None,
    image_ids: list[str] | None = None,
    on_progress=None,
) -> BatchReport:
    """Headless batch entry point. One image failure does not stop the rest."""

    controller = AnalysisController.open(experiment_dir)
    if recipe is not None:
        controller.set_recipe(recipe)
    return controller.run_images(image_ids, on_progress=on_progress)


def _canonical(value) -> str:
    import json

    return json.dumps(value, sort_keys=True, default=str)


def _same_numbers(stored, current) -> bool:
    if not isinstance(stored, list) or len(stored) != len(current):
        return False
    for first, second in zip(stored, current, strict=True):
        if (first is None) != (second is None):
            return False
        if first is not None and abs(float(first) - float(second)) > 1e-9 * max(abs(float(first)), 1.0):
            return False
    return True


def _job_status(result: ImageResult) -> tuple[str, str]:
    if result.qc.status == "success":
        return "Success", ""
    message = "; ".join(result.qc.warnings) or "Analysis completed with a warning."
    return "Warning", message


def _clamp(start: int, stop: int, limit: int) -> tuple[int, int]:
    start = max(0, min(int(start), limit))
    stop = max(start + 1, min(int(stop), limit)) if limit else start
    return start, stop


def _details_to_keep(result: ImageResult) -> dict:
    """Segmentation details that later measurements of the same labels still report."""

    return segmentation_details_of(result)
