"""Napari-facing controller with no import-time dependency on napari or Qt.

The controller is intentionally a thin adapter.  All scientific work is
delegated to the shared core and all potentially slow operations are submitted
through a worker factory.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from queue import Empty, SimpleQueue
from time import monotonic
from typing import Any, Callable, Mapping, Sequence
import uuid

import numpy as np

from cellquant.batch import BatchSummary, build_queue, run_batch
from cellquant.config import RunConfig, load_config
from cellquant.contracts import (
    ImageVolume,
    LabelVolume,
    MutableCancellationToken,
    PipelineCancelled,
    PipelineEvent,
)
from cellquant.io import normalize_suffixes, open_volume, resolve_file_type_preset
from cellquant.measure import write_measurements
from cellquant.orchestrator import run_measurements, run_pipeline
from cellquant.persist import RunStore
from cellquant.plugin.messages import (
    UserMessage,
    explain_batch_summary,
    explain_exception,
    failed_result_paths,
    format_eta_seconds,
    format_failed_event,
    format_pipeline_status,
    notify_napari,
)
from cellquant.survey import (
    SurveyResult,
    SurveyRunResult,
    materialize_run_config,
    run_survey_batches,
    survey_folder,
    with_assignments,
    write_layout_configs,
    write_survey,
)
from cellquant.viz import make_qc_figures


IMAGE_LAYER_NAME = "CellQuant image"
LABEL_LAYER_NAME = "CellQuant labels"


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _input_fingerprint(path: Path, cancel=None) -> tuple[str, dict[str, int | str]]:
    """Fingerprint a source using the same descriptor semantics as batch."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            if cancel is not None:
                cancel.raise_if_cancelled()
            digest.update(block)
    stat = path.stat()
    descriptor: dict[str, int | str] = {
        "content_sha256": digest.hexdigest(),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }
    canonical = json.dumps(descriptor, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("ascii")).hexdigest(), descriptor


def _validated_label_array(value: Any) -> np.ndarray:
    """Validate napari-edited IDs before conversion to the core uint32 type."""
    data = np.asarray(value)
    if data.ndim != 3:
        raise ValueError(f"edited labels must be ZYX; received shape {data.shape}")
    if not (np.issubdtype(data.dtype, np.integer) or np.issubdtype(data.dtype, np.floating)):
        raise TypeError(f"edited labels must contain real integer IDs; received {data.dtype}")
    if np.issubdtype(data.dtype, np.floating):
        if not np.all(np.isfinite(data)):
            raise ValueError("edited labels contain nonfinite values")
        if not np.all(data == np.floor(data)):
            raise ValueError("edited labels contain non-integral values")
    if data.size:
        if np.min(data) < 0:
            raise ValueError("edited labels contain negative IDs")
        if np.max(data) > np.iinfo(np.uint32).max:
            raise ValueError("edited labels exceed the uint32 ID range")
    return data.astype(np.uint32, copy=False)


def _default_worker_factory(operation: Callable[[], Any]):
    # Import only when an operation is dispatched.  Core imports and controller
    # tests therefore do not require a Qt event loop.
    from napari.qt.threading import thread_worker

    return thread_worker(operation)()


def _connect(signal: Any, callback: Callable) -> None:
    if signal is not None:
        signal.connect(callback)


def _layer_kind(layer: Any) -> str | None:
    value = getattr(layer, "_type_string", None)
    if isinstance(value, str):
        return value.lower()
    name = type(layer).__name__.lower()
    if "label" in name:
        return "labels"
    if "image" in name:
        return "image"
    return getattr(layer, "layer_type", None)


def _find_layer(viewer: Any, name: str) -> Any | None:
    layers = viewer.layers
    try:
        return layers[name]
    except (KeyError, TypeError, IndexError, ValueError):
        return next((layer for layer in layers if getattr(layer, "name", None) == name), None)


class PluginController:
    """Coordinate viewer layers, background work, cancellation, and events."""

    def __init__(
        self,
        viewer: Any,
        config: RunConfig | None = None,
        *,
        worker_factory: Callable[[Callable[[], Any]], Any] | None = None,
        open_volume_fn: Callable[..., ImageVolume] = open_volume,
        pipeline_fn: Callable[..., LabelVolume] = run_pipeline,
        measurements_fn: Callable[..., Any] = run_measurements,
        persist_fn: Callable[..., Path] | None = None,
        batch_fn: Callable[..., BatchSummary] | None = None,
        build_queue_fn: Callable[..., list] | None = None,
        notify_fn: Callable[[str, str, str], None] | None = None,
        survey_fn: Callable[..., SurveyResult] | None = None,
        survey_run_fn: Callable[..., tuple[SurveyRunResult, ...]] | None = None,
    ) -> None:
        self.viewer = viewer
        self.config = config
        self._worker_factory = worker_factory or _default_worker_factory
        self._open_volume = open_volume_fn
        self._pipeline = pipeline_fn
        self._measurements = measurements_fn
        self._persist = persist_fn or self._persist_edited
        self._batch = batch_fn or run_batch
        self._build_queue = build_queue_fn or build_queue
        self._notify_fn = notify_fn
        self._survey = survey_fn or survey_folder
        self._survey_run = survey_run_fn or run_survey_batches
        self._events: SimpleQueue[PipelineEvent] = SimpleQueue()
        self._workers: list[Any] = []
        self._busy = False
        self.cancel_token = MutableCancellationToken()
        self.image_volume: ImageVolume | None = None
        self.label_volume: LabelVolume | None = None
        self.batch_summary: BatchSummary | None = None
        self.survey_result: SurveyResult | None = None
        self.survey_runs: tuple[SurveyRunResult, ...] | None = None
        self.survey_dir: Path | None = None
        self.last_config_path: Path | None = None
        self.status_text = "Ready"
        self.progress: tuple[int, int] | None = None
        self.last_error: BaseException | None = None
        self.last_user_message: UserMessage | None = None
        self.last_output_dir: Path | None = None
        self.last_failures_path: Path | None = None
        self.last_summary_path: Path | None = None
        self._job_started_monotonic: float | None = None

    @property
    def is_busy(self) -> bool:
        return bool(self._busy)

    def _publish_user_message(self, message: UserMessage) -> None:
        self.last_user_message = message
        self.status_text = message.status_line()
        notify_napari(message, self._notify_fn)

    def report_exception(self, exc: BaseException) -> UserMessage:
        """Publish a user-facing failure for synchronous UI validation errors."""

        self.last_error = exc
        message = explain_exception(exc)
        self._publish_user_message(message)
        return message

    def set_config(self, config: RunConfig) -> None:
        if not isinstance(config, RunConfig):
            raise TypeError("config must be a RunConfig")
        self.config = config

    def enqueue_event(self, event: PipelineEvent) -> None:
        """Worker-safe event sink.  It deliberately performs no Qt calls."""
        if not isinstance(event, PipelineEvent):
            raise TypeError("plugin event sink accepts PipelineEvent records")
        self._events.put(event)

    def drain_events(self, callback: Callable[[PipelineEvent], None] | None = None) -> list[PipelineEvent]:
        """Drain events on the caller (GUI) thread and update display state."""
        drained: list[PipelineEvent] = []
        while True:
            try:
                event = self._events.get_nowait()
            except Empty:
                break
            drained.append(event)
            if event.current is not None:
                self.progress = (event.current, event.total)
            if event.kind == "failed":
                explained = format_failed_event(event)
                if explained is not None:
                    self.last_user_message = explained
                    self.status_text = explained.status_line()
            else:
                eta = None
                if (
                    event.stage == "batch"
                    and event.current is not None
                    and event.total is not None
                    and self._job_started_monotonic is not None
                ):
                    status = str(event.details.get("status") or "")
                    completed = int(event.current) if status in {"completed", "resumed"} else max(
                        int(event.current) - 1, 0
                    )
                    eta = format_eta_seconds(
                        monotonic() - self._job_started_monotonic,
                        int(event.current),
                        int(event.total),
                        completed=completed,
                    )
                self.status_text = format_pipeline_status(event, eta=eta)
            if callback is not None:
                callback(event)
        return drained

    def cancel(self) -> None:
        """Request a cooperative stop at the next plane / Cellpose checkpoint."""
        self.cancel_token.cancel()
        self.status_text = (
            "Cancellation requested — will stop after the current Z plane / Cellpose checkpoint. "
            "Use Kill if this mid-plane run is stuck and Napari feels frozen."
        )

    def kill(self) -> None:
        """Force-terminate the Cellpose worker process immediately."""
        kill = getattr(self.cancel_token, "kill", None)
        if callable(kill):
            kill()
        else:
            self.cancel_token.cancel()
        self.status_text = (
            "Kill requested — terminating the Cellpose worker now so Napari can respond again"
        )

    def _dispatch(self, operation: Callable[[], Any], returned: Callable[[Any], None]) -> Any:
        if self._busy:
            raise RuntimeError("A CellQuant background operation is already running")
        self.cancel_token = MutableCancellationToken()
        self.last_error = None
        self.last_user_message = None
        self.progress = None
        self._job_started_monotonic = monotonic()
        worker = self._worker_factory(operation)
        self._busy = True
        self._workers.append(worker)

        def on_return(value):
            try:
                before = self.status_text
                returned(value)
                if self.status_text == before:
                    self.status_text = "Complete"
            finally:
                self._busy = False
                self._job_started_monotonic = None

        def on_error(error):
            self.last_error = error if isinstance(error, BaseException) else RuntimeError(str(error))
            self._publish_user_message(explain_exception(self.last_error))
            self._busy = False
            self._job_started_monotonic = None

        _connect(getattr(worker, "returned", None), on_return)
        _connect(getattr(worker, "errored", None), on_error)
        self.status_text = "Running in background…"
        try:
            worker.start()
        except BaseException as exc:
            self._busy = False
            self._job_started_monotonic = None
            self.last_error = exc
            self._publish_user_message(explain_exception(exc))
            raise
        return worker

    def _require_config(self) -> RunConfig:
        if self.config is None:
            raise ValueError("Load a validated CellQuant configuration first")
        return self.config

    def _with_io_overrides(
        self,
        *,
        recursive: bool | None = None,
        suffixes: Sequence[str] | None = None,
        channel_index: int | None = None,
    ) -> RunConfig:
        config = self._require_config()
        if recursive is None and suffixes is None and channel_index is None:
            return config
        raw = deepcopy(dict(config.raw))
        if recursive is not None:
            raw["io"]["recursive"] = bool(recursive)
        if suffixes is not None:
            raw["io"]["suffixes"] = list(normalize_suffixes(suffixes))
        if channel_index is not None:
            if not isinstance(channel_index, int) or isinstance(channel_index, bool):
                raise TypeError("segmentation channel index must be an integer")
            if channel_index < 0:
                raise IndexError("segmentation channel index must be non-negative")
            raw["preprocess"]["channel"] = channel_index
        config = RunConfig(raw)
        self.config = config
        return config

    def run_batch(
        self,
        input_dir: str | Path,
        output_dir: str | Path,
        *,
        recursive: bool | None = None,
        suffixes: Sequence[str] | None = None,
        file_type: str | None = None,
        channel_index: int | None = None,
    ) -> Any:
        """Discover and process an input folder using the shared batch core."""

        if suffixes is not None and file_type is not None:
            raise ValueError("pass suffixes or file_type, not both")
        resolved_suffixes = (
            list(resolve_file_type_preset(file_type)) if file_type is not None else suffixes
        )
        config = self._with_io_overrides(
            recursive=recursive,
            suffixes=resolved_suffixes,
            channel_index=channel_index,
        )
        source_root = Path(input_dir)
        destination = Path(output_dir)
        if not source_root.exists():
            raise FileNotFoundError(source_root)

        def operation() -> BatchSummary:
            queue = self._build_queue([source_root], destination, config)
            if not queue:
                raise FileNotFoundError(
                    f"No matching image files under {source_root} "
                    f"(recursive={config.raw['io']['recursive']}, "
                    f"suffixes={config.raw['io']['suffixes']})"
                )
            return self._batch(queue, config, self.cancel_token, self.enqueue_event)

        def returned(summary: BatchSummary) -> None:
            self.batch_summary = summary
            self.last_output_dir = Path(output_dir)
            paths = failed_result_paths(summary)
            self.last_failures_path = next(
                (path for path in paths if path.name == "failures.csv"),
                Path(summary.failures_path) if summary.failures_path else None,
            )
            self.last_summary_path = next(
                (path for path in paths if path.name == "batch_summary.json"),
                Path(summary.summary_path) if summary.summary_path else None,
            )
            self._publish_user_message(explain_batch_summary(summary))

        return self._dispatch(operation, returned)

    def apply_segment_overrides(self, overrides: Mapping[str, Any] | None) -> RunConfig:
        """Merge UI segment options into the active config and return it."""

        config = self._require_config()
        if not overrides:
            return config
        raw = deepcopy(dict(config.raw))
        segment = dict(raw["segment"])
        for key, value in overrides.items():
            if key not in segment:
                raise KeyError(f"unknown segment override {key!r}")
            segment[key] = value
        mode = segment.get("mode")
        if mode != "stitch_2d":
            segment["stitch_threshold"] = 0.0
        if mode != "single_plane_2d":
            segment["z_index"] = None
        elif segment.get("z_index") is None:
            segment["z_index"] = 0
        raw["segment"] = segment
        config = RunConfig(raw)
        self.config = config
        return config

    def run_survey(
        self,
        input_dir: str | Path,
        output_dir: str | Path,
        *,
        recursive: bool = True,
        suffixes: Sequence[str] | None = None,
        file_type: str | None = None,
        segment_overrides: Mapping[str, Any] | None = None,
    ) -> Any:
        """Scan inputs, cluster layouts, write survey + auto-load a run config."""

        if suffixes is not None and file_type is not None:
            raise ValueError("pass suffixes or file_type, not both")
        resolved_suffixes = (
            list(resolve_file_type_preset(file_type)) if file_type is not None else suffixes
        )
        source_root = Path(input_dir)
        destination = Path(output_dir)
        if not source_root.is_dir():
            raise FileNotFoundError(source_root)
        survey_dir = destination / "survey"
        overrides = dict(segment_overrides or {})

        def operation() -> tuple[SurveyResult, dict[str, Path], RunConfig, Path]:
            survey = self._survey(
                source_root,
                recursive=recursive,
                suffixes=resolved_suffixes,
            )
            artifacts = write_survey(survey, survey_dir)
            config, config_path = materialize_run_config(
                survey_dir / "cellquant_run_config.yaml",
                survey=survey,
                segment_overrides=overrides or None,
            )
            artifacts = {**artifacts, "run_config": config_path}
            return survey, artifacts, config, config_path

        def returned(payload: tuple[SurveyResult, dict[str, Path], RunConfig, Path]) -> None:
            survey, artifacts, config, config_path = payload
            self.survey_result = survey
            self.survey_dir = survey_dir
            self.last_output_dir = destination
            self.last_config_path = config_path
            self.set_config(config)
            self._publish_user_message(
                UserMessage(
                    "info",
                    "Survey complete",
                    f"{survey.file_count} files → {len(survey.layouts)} channel layouts "
                    f"({survey.error_count} inspect errors)",
                    "Pick a segmentation channel per layout below, then Run batch. "
                    f"Run config auto-loaded from {config_path.name}.",
                )
            )

        return self._dispatch(operation, returned)

    def run_survey_batches(
        self,
        output_dir: str | Path,
        *,
        assignments: Mapping[str, int] | None = None,
        survey: SurveyResult | None = None,
        segment_overrides: Mapping[str, Any] | None = None,
    ) -> Any:
        """Rewrite per-layout configs, auto-load the first, and run batches."""

        planned = survey or self.survey_result
        if planned is None:
            raise ValueError("Survey the input folder first")
        if assignments is not None:
            planned = with_assignments(planned, assignments)
            self.survey_result = planned
        destination = Path(output_dir)
        survey_dir = self.survey_dir or (destination / "survey")
        config_dir = survey_dir / "configs"
        overrides = dict(segment_overrides or {})

        def operation() -> tuple[tuple[SurveyRunResult, ...], Path]:
            assigned = [layout for layout in planned.layouts if layout.segment_channel is not None]
            if not assigned:
                raise ValueError(
                    "Include at least one layout and choose its segmentation channel"
                )
            active, active_path = materialize_run_config(
                survey_dir / "cellquant_run_config.yaml",
                template=self.config,
                survey=planned,
                segment_channel=assigned[0].segment_channel,
                segment_overrides=overrides or None,
            )
            write_layout_configs(
                planned,
                config_dir,
                template=active,
                assignments=None,
                segment_overrides=None,
            )
            write_survey(planned, survey_dir)
            runs = self._survey_run(
                planned,
                destination,
                active,
                cancel=self.cancel_token,
                events=self.enqueue_event,
            )
            return runs, active_path

        def returned(payload: tuple[tuple[SurveyRunResult, ...], Path]) -> None:
            runs, config_path = payload
            self.survey_runs = runs
            self.last_output_dir = destination
            self.last_config_path = Path(config_path)
            self.set_config(load_config(self.last_config_path))
            failed = sum(run.summary.failed for run in runs)
            completed = sum(run.summary.completed for run in runs)
            failure_paths: list[Path] = []
            first_error: str | None = None
            for run in runs:
                for path in failed_result_paths(run.summary):
                    if path.name == "failures.csv":
                        failure_paths.append(path)
                if first_error is None:
                    for result in getattr(run.summary, "results", ()) or ():
                        if getattr(result, "status", None) != "failed":
                            continue
                        message = getattr(result, "message", None) or getattr(result, "error", None)
                        if message:
                            first_error = str(message)
                            break
            self.last_failures_path = failure_paths[0] if failure_paths else None
            severity = "warning" if failed else "info"
            hint_parts = [
                f"Run config kept at {self.last_config_path.name}; per-layout YAMLs under survey/configs/."
            ]
            if first_error:
                hint_parts.insert(0, f"First error: {first_error}")
            if failure_paths:
                hint_parts.append("Use Open failures.csv for the full list.")
            self._publish_user_message(
                UserMessage(
                    severity,
                    "Batch finished",
                    f"{len(runs)} layout(s): {completed} completed, {failed} failed",
                    " ".join(hint_parts),
                )
            )

        return self._dispatch(operation, returned)

    def open_path(
        self,
        path: str | Path,
        *,
        series: int = 0,
        position: int = 0,
        axes_override: str | None = None,
        spacing_override_um: tuple[float, float, float] | None = None,
    ) -> Any:
        """Open calibrated data lazily on a worker and publish an Image layer."""
        source = Path(path)

        def operation() -> ImageVolume:
            volume = self._open_volume(
                source,
                series=series,
                position=position,
                lazy=True,
                axes_override=axes_override,
                spacing_override_um=spacing_override_um,
            )
            fingerprint, descriptor = _input_fingerprint(source, self.cancel_token)
            return replace(
                volume,
                metadata={
                    **dict(volume.metadata),
                    "input_fingerprint": fingerprint,
                    "input_fingerprint_descriptor": descriptor,
                },
            )

        return self._dispatch(operation, self._publish_image)

    def _publish_image(self, volume: ImageVolume) -> None:
        self.image_volume = volume
        metadata = dict(volume.metadata)
        metadata.update(
            {
                "source": str(volume.source),
                "spacing_um": tuple(volume.spacing_um),
                "channel_names": tuple(volume.channel_names),
                "canonical_axes": "ZYXC",
            }
        )
        scale = (*volume.spacing_um, 1.0)
        existing = _find_layer(self.viewer, IMAGE_LAYER_NAME)
        if existing is not None:
            if _layer_kind(existing) != "image":
                raise TypeError(f"{IMAGE_LAYER_NAME!r} is not an Image layer")
            existing.data = volume.data
            existing.scale = scale
            existing.metadata = metadata
        else:
            self.viewer.add_image(
                volume.data,
                name=IMAGE_LAYER_NAME,
                rgb=False,
                visible=False,
                scale=scale,
                metadata=metadata,
            )

        # Canonical ZYXC is a measurement source, not a spatial display plane.
        # Display each channel on ZYX so labels and fluorescence share a grid.
        canonical = _find_layer(self.viewer, IMAGE_LAYER_NAME)
        canonical.visible = False
        for stale in list(self.viewer.layers):
            if getattr(stale, "metadata", {}).get("cellquant_display_channel"):
                self.viewer.layers.remove(stale)
        for index, channel_name in enumerate(volume.channel_names):
            self.viewer.add_image(
                volume.data[..., index], rgb=False,
                name=f"CellQuant channel - {channel_name}", scale=volume.spacing_um,
                blending="additive", metadata={"cellquant_display_channel": True},
            )

    def _volume_from_layer(self, layer: Any) -> ImageVolume:
        if _layer_kind(layer) != "image":
            raise TypeError("segmentation input must be a napari Image layer")
        data = layer.data
        if len(tuple(data.shape)) != 4:
            raise ValueError("segmentation input must use canonical ZYXC axes")
        metadata = dict(getattr(layer, "metadata", {}) or {})
        spacing = tuple(metadata.get("spacing_um", tuple(layer.scale[:3])))
        names = tuple(metadata.get("channel_names", ()))
        if not names:
            names = tuple(f"C{index + 1}" for index in range(int(data.shape[-1])))
        source = Path(metadata.get("source", getattr(layer, "name", "interactive-image")))
        return ImageVolume(data, spacing, names, source, metadata)

    def segment(
        self,
        image_layer: Any | None = None,
        channel_index: int | None = None,
        *,
        segment_overrides: Mapping[str, Any] | None = None,
    ) -> Any:
        config = self._require_config()
        if segment_overrides:
            config = self.apply_segment_overrides(segment_overrides)
        layer = image_layer or _find_layer(self.viewer, IMAGE_LAYER_NAME)
        if layer is None:
            raise ValueError("Open or select an Image layer first")
        volume = self._volume_from_layer(layer)
        if channel_index is not None:
            if not isinstance(channel_index, int) or isinstance(channel_index, bool):
                raise TypeError("segmentation channel index must be an integer")
            if not 0 <= channel_index < int(volume.data.shape[-1]):
                raise IndexError(
                    f"segmentation channel {channel_index} is outside C axis of length "
                    f"{volume.data.shape[-1]}"
                )
            raw = deepcopy(dict(config.raw))
            raw["preprocess"]["channel"] = channel_index
            config = RunConfig(raw)
            self.config = config
        self.image_volume = volume
        return self._dispatch(
            lambda: self._pipeline(volume, config, self.cancel_token, self.enqueue_event),
            self._publish_labels,
        )

    def _publish_labels(self, labels: LabelVolume) -> None:
        self.label_volume = labels
        existing = _find_layer(self.viewer, LABEL_LAYER_NAME)
        metadata = {"spacing_um": tuple(labels.spacing_um), **dict(labels.provenance)}
        if existing is not None:
            if _layer_kind(existing) != "labels":
                raise TypeError(f"{LABEL_LAYER_NAME!r} is not a Labels layer")
            existing.data = labels.data
            existing.scale = labels.spacing_um
            existing.metadata = metadata
        else:
            self.viewer.add_labels(
                labels.data,
                name=LABEL_LAYER_NAME,
                scale=labels.spacing_um,
                metadata=metadata,
            )

    def measure_and_save(self, output_dir: str | Path, labels_layer: Any | None = None) -> Any:
        """Re-measure the currently edited Labels layer and persist a full run."""
        config = self._require_config()
        if self.image_volume is None:
            image_layer = _find_layer(self.viewer, IMAGE_LAYER_NAME)
            if image_layer is None:
                raise ValueError("No source Image layer is available")
            self.image_volume = self._volume_from_layer(image_layer)
        layer = labels_layer or _find_layer(self.viewer, LABEL_LAYER_NAME)
        if layer is None or _layer_kind(layer) != "labels":
            raise ValueError("Select the edited CellQuant Labels layer")
        # Keep conversion/copying off the GUI thread: an edited Labels layer can
        # itself be a many-gigabyte volume.
        layer_data = layer.data
        spacing_um = self.image_volume.spacing_um
        provenance = {
            **(dict(self.label_volume.provenance) if self.label_volume is not None else {}),
            **dict(getattr(layer, "metadata", {}) or {}),
            "edited_in_napari": True,
        }

        def operation() -> Path:
            labels = LabelVolume(
                _validated_label_array(layer_data),
                spacing_um,
                provenance,
            )
            tables = self._measurements(
                self.image_volume, labels, config, self.cancel_token, self.enqueue_event
            )
            return self._persist(Path(output_dir), self.image_volume, labels, tables, config)

        return self._dispatch(operation, self._on_saved)

    def _on_saved(self, path: Path) -> None:
        self.last_output_dir = Path(path)
        self._publish_user_message(
            UserMessage(
                "info",
                "Saved",
                f"Run store written to {path}",
                "Open the output folder for labels, measurements, events.jsonl, and status.json.",
            )
        )

    def _persist_edited(self, output_dir, image, labels, tables, config) -> Path:
        source_fingerprint = str(image.metadata.get("input_fingerprint", ""))
        if not source_fingerprint and image.source.is_file():
            source_fingerprint, _ = _input_fingerprint(image.source, self.cancel_token)
        if not source_fingerprint:
            # Interactive layers may not have a file.  Make that limitation
            # explicit in provenance while retaining a stable local run key.
            payload = f"interactive:{image.source}:{image.data.shape}:{image.data.dtype}"
            source_fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        run_id = str(uuid.uuid4())
        store = RunStore.create_for_user_output(
            output_dir, source_fingerprint, config.fingerprint, run_id=run_id
        )

        def sink(event: PipelineEvent) -> None:
            self.enqueue_event(event)
            store.append_event(event)

        try:
            # Measurement events were already relayed live.  Record the
            # explicit persistence boundary in the durable event stream.
            sink(PipelineEvent("stage_started", run_id, image.source.name, "persist", _utc()))
            store.write_config(config)
            store.write_labels(labels)
            store.write_provenance(
                {
                    **dict(labels.provenance),
                    "edited_in_napari": True,
                    "source": str(image.source),
                    "input_fingerprint": source_fingerprint,
                    "config_fingerprint": config.fingerprint,
                }
            )
            for path in write_measurements(tables, store.directory):
                store.register_measurement(path)
            for path in make_qc_figures(image, labels, store.directory, config).values():
                store.register_qc_artifact(path)
            sink(PipelineEvent("stage_finished", run_id, image.source.name, "persist", _utc()))
            store.commit()
        except BaseException as exc:
            terminal = "cancelled" if isinstance(exc, PipelineCancelled) else "failed"
            try:
                sink(PipelineEvent(
                    terminal,
                    run_id,
                    image.source.name,
                    "persist",
                    _utc(),
                    details={"exception_type": type(exc).__name__, "message": str(exc)},
                ))
                store.commit(terminal)
            except BaseException:
                # Preserve the causal exception. If even the terminal marker
                # cannot be written, the incomplete store still cannot resume.
                pass
            raise
        return Path(output_dir)


__all__ = ["IMAGE_LAYER_NAME", "LABEL_LAYER_NAME", "PluginController"]
