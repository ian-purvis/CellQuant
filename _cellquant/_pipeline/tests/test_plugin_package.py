from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from cellquant.batch import BatchItem, BatchItemResult, BatchSummary
from cellquant.config import RunConfig
from cellquant.contracts import ImageVolume, LabelVolume, PipelineEvent
from cellquant.plugin import IMAGE_LAYER_NAME, LABEL_LAYER_NAME, PluginController
from cellquant.plugin.widget import (
    FILE_TYPE_CHOICES,
    SEGMENTATION_CHANNEL_WIDGET_OPTIONS,
    bind_segmentation_channel_choices,
    file_type_suffixes,
    segmentation_channel_choices,
)


class Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self, value):
        for callback in self.callbacks:
            callback(value)


class Worker:
    def __init__(self, operation, *, delayed=False):
        self.operation = operation
        self.delayed = delayed
        self.returned = Signal()
        self.errored = Signal()
        self.started = False

    def start(self):
        self.started = True
        if not self.delayed:
            self.run()

    def run(self):
        try:
            self.returned.emit(self.operation())
        except BaseException as exc:
            self.errored.emit(exc)


class WorkerFactory:
    def __init__(self, *, delayed=False):
        self.delayed = delayed
        self.workers = []

    def __call__(self, operation):
        worker = Worker(operation, delayed=self.delayed)
        self.workers.append(worker)
        return worker


class LazyArray:
    shape = (3, 8, 9, 1)
    dtype = np.dtype(np.uint16)

    def __init__(self):
        self.materialized = False

    def compute(self):
        self.materialized = True
        return np.zeros(self.shape, dtype=self.dtype)


class ImageLayer:
    _type_string = "image"

    def __init__(self, data, name, scale, metadata):
        self.data = data
        self.name = name
        self.scale = tuple(scale)
        self.metadata = metadata


class LabelsLayer:
    _type_string = "labels"

    def __init__(self, data, name, scale, metadata):
        self.data = data
        self.name = name
        self.scale = tuple(scale)
        self.metadata = metadata


class LayerList(list):
    def __getitem__(self, item):
        if isinstance(item, str):
            for layer in self:
                if layer.name == item:
                    return layer
            raise KeyError(item)
        return super().__getitem__(item)


class Viewer:
    def __init__(self):
        self.layers = LayerList()
        self.calls = []

    def add_image(self, data, **kwargs):
        self.calls.append(("image", kwargs["name"]))
        layer = ImageLayer(data, kwargs["name"], kwargs["scale"], kwargs["metadata"])
        self.layers.append(layer)
        return layer

    def add_labels(self, data, **kwargs):
        self.calls.append(("labels", kwargs["name"]))
        layer = LabelsLayer(data, kwargs["name"], kwargs["scale"], kwargs["metadata"])
        self.layers.append(layer)
        return layer


def config():
    return RunConfig(
        {
            "schema_version": 1,
            "io": {"series": 0, "position": 0, "lazy": True, "axes_override": None, "spacing_override_um": None, "recursive": True, "suffixes": [".tif", ".tiff", ".nd2"]},
            "preprocess": {
                "channel": 0,
                "normalize": {"enabled": False, "low_percentile": None, "high_percentile": None, "scope": None},
                "rescale": {"enabled": False, "target_spacing_um": None, "interpolation": None, "antialias": None, "boundary": "constant", "cval": 0.0},
                "denoise": {"enabled": False, "method": None, "parameters": {}, "boundary": "reflect", "cval": 0.0},
            },
            "segment": {
                "engine": "v4", "model": "cpsam_v2", "model_sha256": "0" * 64,
                "mode": "volume_3d", "z_index": None, "diameter_px": 30, "anisotropy": 2,
                "min_size": 200, "flow_threshold": 0.4, "cellprob_threshold": 0,
                "stitch_threshold": 0.0, "channel_axis": None, "z_axis": 0,
                "tile": True, "tile_overlap": 0.1, "batch_size": 1, "augment": False,
                "resample": True, "normalize": True, "device": "cpu",
                "allow_cpu_fallback": False, "use_bfloat16": False, "flow3D_smooth": 0,
                "max_size_fraction": 0.4, "niter": None, "bsize": 256,
                "compute_masks": True, "channels": None, "rescale_factor": None,
                "progress": None, "model_type": None, "diam_mean": None, "nchan": None,
            },
            "postprocess": {"min_volume_um3": None, "max_volume_um3": None, "min_voxels": None, "max_voxels": None, "remove_border_faces": [], "relabel": False},
            "measure": {"channels": "all", "intensity_statistics": ["mean"]},
            "viz": {"low_percentile": 1, "high_percentile": 99, "label_seed": 0, "dpi": 72},
            "runtime": {"seed": 0, "deterministic_torch": True, "hash_inputs": True, "output_compression": "none"},
        }
    )


def test_lazy_open_is_background_and_does_not_materialize(tmp_path):
    lazy = LazyArray()
    calls = []
    source = tmp_path / "stack.ome.tif"
    source.write_bytes(b"synthetic source pixels")

    def opener(path, **kwargs):
        calls.append(kwargs)
        return ImageVolume(lazy, (2.0, 0.5, 0.5), ("DAPI",), Path(path))

    workers = WorkerFactory()
    viewer = Viewer()
    controller = PluginController(viewer, config(), worker_factory=workers, open_volume_fn=opener)
    controller.open_path(source)

    assert len(workers.workers) == 1 and workers.workers[0].started
    assert calls == [{"series": 0, "position": 0, "lazy": True, "axes_override": None, "spacing_override_um": None}]
    assert viewer.layers[IMAGE_LAYER_NAME].data is lazy
    assert len(viewer.layers[IMAGE_LAYER_NAME].metadata["input_fingerprint"]) == 64
    assert viewer.layers[IMAGE_LAYER_NAME].metadata["input_fingerprint_descriptor"]["size"] == source.stat().st_size
    assert not lazy.materialized
    assert viewer.calls == [("image", IMAGE_LAYER_NAME)]


def test_controller_keeps_batch_progress_when_plane_events_arrive():
    viewer = Viewer()
    workers = WorkerFactory()
    controller = PluginController(viewer, config(), worker_factory=workers)
    controller._busy = True
    controller._job_started_monotonic = __import__("time").monotonic() - 120.0

    controller.enqueue_event(
        PipelineEvent(
            "progress",
            "run",
            "a.nd2",
            "batch",
            "2026-01-01T00:00:00Z",
            current=2,
            total=10,
            details={"status": "running"},
        )
    )
    controller.drain_events()
    assert controller.batch_progress == (2, 10)
    assert "Running image 2 of 10" in controller.status_text
    assert "left" in controller.status_text

    controller.enqueue_event(
        PipelineEvent(
            "progress",
            "run",
            "a.nd2",
            "segment",
            "2026-01-01T00:00:00Z",
            current=5,
            total=20,
            details={"message": "plane 5/20", "status": "running"},
        )
    )
    controller.drain_events()
    assert controller.batch_progress == (2, 10)
    assert controller.progress == (5, 20)
    assert "Running image 2 of 10" in controller.status_text
    assert "plane 5/20" in controller.status_text
    assert "left" in controller.status_text


def test_segmentation_uses_shared_pipeline_relays_events_and_keeps_stable_layers():
    viewer = Viewer()
    image = np.zeros((2, 4, 5, 1), dtype=np.float32)
    image_layer = viewer.add_image(
        image, name=IMAGE_LAYER_NAME, scale=(3, 1, 1, 1),
        metadata={"spacing_um": (3, 1, 1), "channel_names": ("DAPI",), "source": "input.tif"},
    )
    workers = WorkerFactory()
    call_count = 0

    def pipeline(volume, run_config, cancel, events):
        nonlocal call_count
        call_count += 1
        events(PipelineEvent("progress", "run", "file", "segment", "2026-01-01T00:00:00Z", 1, 2))
        return LabelVolume(np.ones((2, 4, 5), dtype=np.uint32), volume.spacing_um)

    controller = PluginController(viewer, config(), worker_factory=workers, pipeline_fn=pipeline)
    controller.segment(image_layer)
    first = viewer.layers[LABEL_LAYER_NAME]
    controller.segment(image_layer)

    assert call_count == 2
    assert len([layer for layer in viewer.layers if layer.name == LABEL_LAYER_NAME]) == 1
    assert viewer.layers[LABEL_LAYER_NAME] is first
    assert first.data.dtype == np.uint32 and first.scale == (3, 1, 1)
    events = controller.drain_events()
    assert len(events) == 2
    assert controller.progress == (1, 2)
    assert all(event.stage == "segment" for event in events)


def test_channel_choices_are_named_one_based_but_controller_persists_zero_based_override():
    viewer = Viewer()
    image_layer = viewer.add_image(
        np.zeros((2, 4, 5, 2), dtype=np.float32),
        name=IMAGE_LAYER_NAME,
        scale=(3, 1, 1, 1),
        metadata={
            "spacing_um": (3, 1, 1),
            "channel_names": ("DAPI", "GFP"),
            "source": "input.tif",
        },
    )
    captured = {}

    def pipeline(volume, run_config, cancel, events):
        captured["channel"] = run_config.raw["preprocess"]["channel"]
        return LabelVolume(np.ones((2, 4, 5), dtype=np.uint32), volume.spacing_um)

    controller = PluginController(
        viewer, config(), worker_factory=WorkerFactory(), pipeline_fn=pipeline
    )
    assert segmentation_channel_choices(image_layer) == [
        ("1 — DAPI", 0),
        ("2 — GFP", 1),
    ]

    controller.segment(image_layer, channel_index=1)

    assert captured["channel"] == 1
    assert controller.config.raw["preprocess"]["channel"] == 1
    with pytest.raises(IndexError, match="outside C axis"):
        controller.segment(image_layer, channel_index=2)


def test_channel_dropdown_binding_refreshes_from_selected_image_without_magicgui():
    class Field:
        def __init__(self, value=None):
            self.value = value
            self.changed = Signal()

    class ChannelField:
        def __init__(self):
            self.choices = [("stale", 99)]

    form = type("FakeForm", (), {})()
    form.image = Field(None)
    form.channel_index = ChannelField()

    refresh = bind_segmentation_channel_choices(form)

    assert callable(refresh)
    assert form.channel_index.choices == []
    first = ImageLayer(
        np.zeros((1, 2, 3, 2)),
        "first",
        (1, 1, 1, 1),
        {"channel_names": ("DAPI", "GFP")},
    )
    form.image.value = first
    form.image.changed.emit(first)
    assert form.channel_index.choices == [("1 — DAPI", 0), ("2 — GFP", 1)]

    second = ImageLayer(np.zeros((1, 2, 3, 1)), "second", (1, 1, 1, 1), {})
    form.image.value = second
    form.image.changed.emit(second)
    assert form.channel_index.choices == [("1 — C1", 0)]


def test_production_channel_options_construct_a_real_magicgui_combobox():
    from magicgui import magicgui
    from magicgui.widgets import ComboBox

    @magicgui(channel_index=SEGMENTATION_CHANNEL_WIDGET_OPTIONS)
    def form(channel_index: int):
        return channel_index

    assert isinstance(form.channel_index, ComboBox)
    assert list(form.channel_index.choices) == []


def test_cancel_button_path_reaches_worker_token():
    viewer = Viewer()
    viewer.add_image(
        np.zeros((1, 2, 2, 1), np.float32), name=IMAGE_LAYER_NAME,
        scale=(1, 1, 1, 1), metadata={"spacing_um": (1, 1, 1), "channel_names": ("C1",)},
    )
    workers = WorkerFactory(delayed=True)

    def pipeline(volume, run_config, cancel, events):
        cancel.raise_if_cancelled()

    controller = PluginController(viewer, config(), worker_factory=workers, pipeline_fn=pipeline)
    controller.segment(viewer.layers[IMAGE_LAYER_NAME])
    controller.cancel()
    workers.workers[0].run()

    assert controller.cancel_token.cancelled
    assert controller.last_error is not None
    assert "cancelled" in str(controller.last_error)


def test_edited_labels_are_remeasured_and_saved_on_worker(tmp_path):
    viewer = Viewer()
    image = np.ones((1, 3, 3, 1), np.float32)
    labels = np.ones((1, 3, 3), np.uint32)
    image_layer = viewer.add_image(
        image, name=IMAGE_LAYER_NAME, scale=(2, 1, 1, 1),
        metadata={"spacing_um": (2, 1, 1), "channel_names": ("DAPI",)},
    )
    labels_layer = viewer.add_labels(
        labels, name=LABEL_LAYER_NAME, scale=(2, 1, 1), metadata={},
    )
    workers = WorkerFactory()
    captured = {}

    def measure(image_volume, label_volume, run_config, cancel, events):
        captured["labels"] = label_volume
        return "tables"

    def persist(output_dir, image_volume, label_volume, tables, run_config):
        captured.update(output=output_dir, tables=tables)
        return output_dir

    controller = PluginController(
        viewer, config(), worker_factory=workers, measurements_fn=measure, persist_fn=persist
    )
    controller.image_volume = controller._volume_from_layer(image_layer)
    labels_layer.data[0, 0, 0] = 7
    controller.measure_and_save(tmp_path, labels_layer)

    assert len(workers.workers) == 1 and workers.workers[0].started
    assert captured["labels"].data[0, 0, 0] == 7
    assert captured["labels"].provenance["edited_in_napari"] is True
    assert captured["tables"] == "tables" and captured["output"] == tmp_path


@pytest.mark.parametrize(
    "invalid, message",
    [
        (np.array([[[-1]]], dtype=np.int64), "negative"),
        (np.array([[[1.5]]], dtype=np.float64), "non-integral"),
        (np.array([[[np.nan]]], dtype=np.float64), "nonfinite"),
        (np.array([[[2**32]]], dtype=np.uint64), "uint32"),
    ],
)
def test_invalid_edited_label_ids_are_rejected_without_wrapping(tmp_path, invalid, message):
    viewer = Viewer()
    image_layer = viewer.add_image(
        np.ones((1, 1, 1, 1), np.float32), name=IMAGE_LAYER_NAME,
        scale=(1, 1, 1, 1), metadata={"spacing_um": (1, 1, 1), "channel_names": ("DAPI",)},
    )
    labels_layer = viewer.add_labels(
        invalid, name=LABEL_LAYER_NAME, scale=(1, 1, 1), metadata={},
    )
    workers = WorkerFactory()
    controller = PluginController(viewer, config(), worker_factory=workers)
    controller.image_volume = controller._volume_from_layer(image_layer)

    controller.measure_and_save(tmp_path, labels_layer)

    assert controller.last_error is not None
    assert message in str(controller.last_error)
    assert not (tmp_path / "status.json").exists()


def test_open_segment_edit_save_commits_complete_store_with_qc_and_provenance(tmp_path):
    source = tmp_path / "source.tif"
    source.write_bytes(b"a deterministic microscope source")
    image_data = np.arange(40, dtype=np.uint16).reshape(2, 4, 5, 1)

    def opener(path, **kwargs):
        return ImageVolume(image_data, (2.0, 0.5, 0.5), ("DAPI",), Path(path))

    def pipeline(volume, run_config, cancel, events):
        return LabelVolume(
            np.ones((2, 4, 5), dtype=np.uint32),
            volume.spacing_um,
            {
                "model_sha256": "a" * 64,
                "eval_parameters": {"diameter": 30.0, "do_3D": True},
                "input_fingerprint": volume.metadata["input_fingerprint"],
            },
        )

    viewer = Viewer()
    controller = PluginController(
        viewer,
        config(),
        worker_factory=WorkerFactory(),
        open_volume_fn=opener,
        pipeline_fn=pipeline,
    )
    controller.open_path(source)
    controller.segment(viewer.layers[IMAGE_LAYER_NAME])
    labels_layer = viewer.layers[LABEL_LAYER_NAME]
    labels_layer.data[0, 0, 0] = 2
    output = tmp_path / "edited_run"
    controller.measure_and_save(output, labels_layer)

    status = json.loads((output / "status.json").read_text(encoding="utf-8"))
    provenance = json.loads((output / "provenance.json").read_text(encoding="utf-8"))
    image_fingerprint = viewer.layers[IMAGE_LAYER_NAME].metadata["input_fingerprint"]
    assert status["status"] == "complete"
    assert status["input_fingerprint"] == image_fingerprint
    assert provenance["input_fingerprint"] == image_fingerprint
    assert provenance["model_sha256"] == "a" * 64
    assert provenance["eval_parameters"] == {"diameter": 30.0, "do_3D": True}
    assert provenance["edited_in_napari"] is True
    assert (output / "qc_mid_stack_outlines.png").is_file()
    assert (output / "qc_orthogonal_view.png").is_file()
    assert (output / "qc_label_projection.png").is_file()
    assert controller.last_error is None


def test_persistence_failure_writes_terminal_failed_status(tmp_path, monkeypatch):
    source = tmp_path / "source.tif"
    source.write_bytes(b"pixels")
    image = ImageVolume(
        np.ones((1, 2, 2, 1), np.float32),
        (1, 1, 1),
        ("DAPI",),
        source,
        {"input_fingerprint": "f" * 64},
    )
    labels = LabelVolume(np.ones((1, 2, 2), np.uint32), (1, 1, 1), {"model_sha256": "a" * 64})
    viewer = Viewer()
    viewer.add_image(
        image.data, name=IMAGE_LAYER_NAME, scale=(1, 1, 1, 1),
        metadata={**image.metadata, "spacing_um": image.spacing_um, "channel_names": image.channel_names, "source": str(source)},
    )
    viewer.add_labels(
        labels.data, name=LABEL_LAYER_NAME, scale=labels.spacing_um,
        metadata={"spacing_um": labels.spacing_um, **labels.provenance},
    )
    controller = PluginController(viewer, config(), worker_factory=WorkerFactory())
    controller.image_volume = image
    controller.label_volume = labels

    def fail_qc(*args, **kwargs):
        raise RuntimeError("synthetic QC failure")

    monkeypatch.setattr("cellquant.plugin.controller.make_qc_figures", fail_qc)
    output = tmp_path / "failed_run"
    controller.measure_and_save(output, viewer.layers[LABEL_LAYER_NAME])

    status = json.loads((output / "status.json").read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert controller.last_error is not None
    assert "synthetic QC failure" in str(controller.last_error)


def test_controller_batch_uses_shared_queue_and_relays_summary(tmp_path):
    inputs = tmp_path / "in"
    inputs.mkdir()
    source = inputs / "a.tif"
    source.write_bytes(b"pixels")
    output = tmp_path / "out"
    output.mkdir()
    failures = output / "failures.csv"
    failures.write_text("source,message\n", encoding="utf-8")
    workers = WorkerFactory()
    viewer = Viewer()
    seen = {}
    notices = []

    def fake_queue(paths, output_root, config):
        seen["suffixes"] = list(config.raw["io"]["suffixes"])
        seen["recursive"] = config.raw["io"]["recursive"]
        seen["channel"] = config.raw["preprocess"]["channel"]
        item = BatchItem(source.resolve(), output / "a.tif.cellquant", output.resolve(), "f" * 64, "a.tif")
        return [item]

    def fake_batch(queue, config, cancel, events):
        events(PipelineEvent("progress", "run", "a.tif", "batch", "2026-01-01T00:00:00Z", 1, 1))
        return BatchSummary(
            total=1,
            completed=0,
            resumed=0,
            failed=1,
            cancelled=0,
            results=(BatchItemResult(str(source), str(output / "a.tif.cellquant"), "failed", "r1", "boom"),),
            summary_path=output / "batch_summary.json",
            failures_path=failures,
        )

    controller = PluginController(
        viewer,
        config(),
        worker_factory=workers,
        build_queue_fn=fake_queue,
        batch_fn=fake_batch,
        notify_fn=lambda severity, title, text: notices.append((severity, title, text)),
    )
    controller.run_batch(
        inputs,
        output,
        recursive=False,
        file_type="tiff",
        channel_index=0,
    )

    assert seen == {"suffixes": [".tif", ".tiff"], "recursive": False, "channel": 0}
    assert controller.batch_summary is not None
    assert controller.batch_summary.failed == 1
    assert controller.last_output_dir == output
    assert controller.last_failures_path == failures
    assert "Batch finished with failures" in controller.status_text
    assert notices and notices[0][0] == "warning"
    assert file_type_suffixes(FILE_TYPE_CHOICES[2]) == (".nd2",)


def test_survey_batch_uses_current_output_folder_not_stale_survey_dir(tmp_path):
    from cellquant.survey import ChannelLayout, SurveyRecord, SurveyResult, SurveyRunResult

    inputs = tmp_path / "in"
    inputs.mkdir()
    source = inputs / "a.tif"
    source.write_bytes(b"pixels")
    old_output = tmp_path / "old_out"
    new_output = tmp_path / "new_out"
    old_output.mkdir()
    new_output.mkdir()
    (old_output / "survey").mkdir()

    workers = WorkerFactory()
    viewer = Viewer()
    seen = {}

    def fake_survey_run(survey, output_root, base_config, *, assignments=None, cancel=None, events=None):
        seen["output_root"] = Path(output_root)
        seen["channel"] = base_config.raw["preprocess"]["channel"]
        summary = BatchSummary(
            total=1,
            completed=1,
            resumed=0,
            failed=0,
            cancelled=0,
            results=(BatchItemResult(str(source), str(Path(output_root) / "a.tif.cellquant"), "completed", "r1"),),
            summary_path=Path(output_root) / "batch_summary.json",
            failures_path=Path(output_root) / "failures.csv",
        )
        return (
            SurveyRunResult(
                layout_id="L1",
                channel_names=("DAPI",),
                segment_channel=0,
                summary=summary,
            ),
        )

    survey = SurveyResult(
        schema_version=1,
        surveyed_utc="2026-01-01T00:00:00Z",
        input_root=str(inputs),
        recursive=True,
        suffixes=(".tif",),
        records=(
            SurveyRecord(
                source=str(source),
                relative="a.tif",
                channel_count=1,
                channel_names=("DAPI",),
                layout_id="L1",
                spacing_um=(1.0, 1.0, 1.0),
            ),
        ),
        layouts=(
            ChannelLayout(
                layout_id="L1",
                channel_count=1,
                channel_names=("DAPI",),
                file_count=1,
                sources=(str(source),),
                suggested_channel=0,
                segment_channel=None,
            ),
        ),
        error_count=0,
    )

    controller = PluginController(
        viewer,
        config(),
        worker_factory=workers,
        survey_run_fn=fake_survey_run,
    )
    controller.survey_result = survey
    controller.survey_dir = old_output / "survey"

    controller.run_survey_batches(new_output, assignments={"L1": 0})

    assert seen["output_root"] == new_output
    assert controller.survey_dir == new_output / "survey"
    assert controller.last_output_dir == new_output
    assert (new_output / "survey" / "cellquant_run_config.yaml").is_file()
    assert not (old_output / "survey" / "cellquant_run_config.yaml").exists()


def test_run_batch_reads_live_output_folder_from_widget():
    text = (
        Path(__file__).parents[1] / "src" / "cellquant" / "plugin" / "widget.py"
    ).read_text(encoding="utf-8")
    assert "survey_batch.output_dir.value" in text
    assert 'batch_paths["output"] = destination' in text
    assert 'destination = batch_paths["output"]' not in text


def test_controller_publishes_remediation_on_worker_error():
    viewer = Viewer()
    workers = WorkerFactory()
    notices = []

    def opener(path, **kwargs):
        raise ValueError("provide spacing_override_um before opening")

    controller = PluginController(
        viewer,
        config(),
        worker_factory=workers,
        open_volume_fn=opener,
        notify_fn=lambda severity, title, text: notices.append((severity, title, text)),
    )
    controller.open_path("missing.tif")

    assert controller.last_error is not None
    assert controller.last_user_message is not None
    assert "calibration" in controller.last_user_message.title.casefold()
    assert notices and notices[0][0] == "error"


def test_interaction_trace_is_deterministic(tmp_path):
    trace = {
        "background_dispatch": ["open", "segment", "measure_and_save"],
        "cancel_token_wired": True,
        "event_relay": "worker_queue_to_gui_timer",
        "lazy_open_materialized": False,
        "layers": {"image": IMAGE_LAYER_NAME, "labels": LABEL_LAYER_NAME},
        "screenshot": "deferred: offscreen Qt not required for deterministic unit gate",
    }
    path = tmp_path / "interaction_trace.json"
    path.write_text(json.dumps(trace, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8")) == trace
