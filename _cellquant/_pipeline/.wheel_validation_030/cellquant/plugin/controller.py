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
from typing import Any, Callable
import uuid

import numpy as np

from cellquant.config import RunConfig
from cellquant.contracts import (
    ImageVolume,
    LabelVolume,
    MutableCancellationToken,
    PipelineCancelled,
    PipelineEvent,
)
from cellquant.io import open_volume
from cellquant.measure import write_measurements
from cellquant.orchestrator import run_measurements, run_pipeline
from cellquant.persist import RunStore
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
    ) -> None:
        self.viewer = viewer
        self.config = config
        self._worker_factory = worker_factory or _default_worker_factory
        self._open_volume = open_volume_fn
        self._pipeline = pipeline_fn
        self._measurements = measurements_fn
        self._persist = persist_fn or self._persist_edited
        self._events: SimpleQueue[PipelineEvent] = SimpleQueue()
        self._workers: list[Any] = []
        self._busy = False
        self.cancel_token = MutableCancellationToken()
        self.image_volume: ImageVolume | None = None
        self.label_volume: LabelVolume | None = None
        self.status_text = "Ready"
        self.progress: tuple[int, int] | None = None
        self.last_error: BaseException | None = None

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
            message = event.details.get("message")
            self.status_text = str(message or f"{event.stage}: {event.kind.replace('_', ' ')}")
            if callback is not None:
                callback(event)
        return drained

    def cancel(self) -> None:
        """Request cooperative cancellation of the active core operation."""
        self.cancel_token.cancel()
        self.status_text = "Cancellation requested"

    def _require_config(self) -> RunConfig:
        if self.config is None:
            raise ValueError("Load a validated CellQuant configuration first")
        return self.config

    def _dispatch(self, operation: Callable[[], Any], returned: Callable[[Any], None]) -> Any:
        if self._busy:
            raise RuntimeError("A CellQuant background operation is already running")
        self.cancel_token = MutableCancellationToken()
        self.last_error = None
        worker = self._worker_factory(operation)
        self._busy = True
        self._workers.append(worker)

        def on_return(value):
            try:
                returned(value)
                self.status_text = "Complete"
            finally:
                self._busy = False

        def on_error(error):
            self.last_error = error if isinstance(error, BaseException) else RuntimeError(str(error))
            self.status_text = f"Failed: {self.last_error}"
            self._busy = False

        _connect(getattr(worker, "returned", None), on_return)
        _connect(getattr(worker, "errored", None), on_error)
        self.status_text = "Running in background"
        try:
            worker.start()
        except BaseException:
            self._busy = False
            raise
        return worker

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
                scale=scale,
                metadata=metadata,
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

    def segment(self, image_layer: Any | None = None, channel_index: int | None = None) -> Any:
        config = self._require_config()
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

        return self._dispatch(operation, lambda path: None)

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
        store = RunStore.create(
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
            for path in write_measurements(tables, output_dir):
                store.register_measurement(path)
            for path in make_qc_figures(image, labels, output_dir, config).values():
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
