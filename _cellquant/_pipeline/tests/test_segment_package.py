import hashlib
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys

import numpy as np
import pytest

from cellquant.contracts import ImageVolume
from cellquant.segment import ACCEPT_MEASURED_MODEL_HASH, Segmenter, load_model, segment


class FakeModel:
    def __init__(self):
        self.calls = []

    def eval(self, image, **kwargs):
        self.calls.append((np.asarray(image).copy(), kwargs))
        masks = np.zeros(np.asarray(image).shape, dtype=np.uint16)
        if masks.ndim == 2:
            masks[1:4, 2:5] = 9
        else:
            masks[..., 1:4, 2:5] = 9
        return masks, [], np.zeros(1)


def _config(mode="volume_3d"):
    return {
        "runtime": {"seed": 3, "deterministic_torch": False},
        "segment": {
            "engine": "v4", "model": "cpsam_v2", "model_sha256": "a" * 64,
            "mode": mode, "z_index": 1 if mode == "single_plane_2d" else None,
            "diameter_px": 30, "anisotropy": "manifest", "min_size": 200,
            "flow_threshold": 0.4, "cellprob_threshold": 0.0,
            "stitch_threshold": 0.25 if mode == "stitch_2d" else 0.0,
            "channel_axis": None, "z_axis": 0, "tile": True, "tile_overlap": 0.1,
            "batch_size": 8, "augment": False, "resample": True, "normalize": True,
            "device": "cpu", "allow_cpu_fallback": False, "use_bfloat16": True,
            "flow3D_smooth": 0, "max_size_fraction": 0.4, "niter": None,
            "bsize": 256, "compute_masks": True, "channels": None,
            "rescale_factor": None, "progress": None,
            "model_type": None, "diam_mean": None, "nchan": None,
        },
    }


def test_true_3d_matches_reference_notebook_arguments():
    image = ImageVolume(np.ones((3, 8, 9, 1), dtype=np.float32), (1.5, 0.5, 0.5),
                        ("DAPI",), Path("x.tif"))
    fake = FakeModel()
    result = segment(image, _config(), segmenter=Segmenter(fake, "a" * 64, "cpu", "4.2.1.1"))
    assert result.data.dtype == np.uint32 and result.data.shape == (3, 8, 9)
    _, kwargs = fake.calls[0]
    assert kwargs["do_3D"] is True and kwargs["anisotropy"] == 3
    assert kwargs["diameter"] == 30 and kwargs["min_size"] == 200
    assert kwargs["flow_threshold"] == 0.4 and kwargs["cellprob_threshold"] == 0
    assert kwargs["stitch_threshold"] == 0
    assert kwargs["batch_size"] == 8 and kwargs["bsize"] == 256


def test_native_diameter_passes_none_to_cellpose():
    image = ImageVolume(np.ones((2, 6, 6, 1), dtype=np.float32), (1, 1, 1),
                        ("DAPI",), Path("x.tif"))
    fake = FakeModel()
    config = _config()
    config["segment"]["diameter_px"] = None
    segment(image, config, segmenter=Segmenter(fake, "a" * 64, "cpu", "4.2.1.1"))
    assert fake.calls[0][1]["diameter"] is None


def test_stitch_mode_runs_each_plane_then_stitches():
    image = ImageVolume(np.ones((4, 5, 6, 1), dtype=np.float32), (2, 1, 1),
                        ("DAPI",), Path("x.tif"))
    fake = FakeModel()
    result = segment(image, _config("stitch_2d"), segmenter=Segmenter(fake, "a" * 64, "cpu", "4"))
    assert result.data.shape == (4, 5, 6)
    assert len(fake.calls) == 4
    assert all(call[0].shape == (5, 6) for call in fake.calls)
    assert all(call[1]["do_3D"] is False for call in fake.calls)
    assert all(call[1]["stitch_threshold"] == 0.0 for call in fake.calls)
    assert result.provenance["eval_parameters"]["cancellable_stitch_planes"] is True
    assert result.provenance["eval_parameters"]["stitch_threshold"] == 0.25


def test_stitch_mode_can_cancel_between_planes():
    from cellquant.contracts import MutableCancellationToken, PipelineCancelled

    image = ImageVolume(np.ones((4, 5, 6, 1), dtype=np.float32), (2, 1, 1),
                        ("DAPI",), Path("x.tif"))
    fake = FakeModel()
    token = MutableCancellationToken()

    class CancelAfterFirst(FakeModel):
        def eval(self, image, **kwargs):
            out = super().eval(image, **kwargs)
            token.cancel()
            return out

    model = CancelAfterFirst()
    with pytest.raises(PipelineCancelled):
        segment(
            image,
            _config("stitch_2d"),
            cancel=token,
            segmenter=Segmenter(model, "a" * 64, "cpu", "4"),
        )
    assert len(model.calls) == 1


def test_unvalidated_volume_mapping_cannot_send_an_ineffective_stitch_threshold():
    image = ImageVolume(np.ones((3, 5, 6, 1), dtype=np.float32), (2, 1, 1),
                        ("DAPI",), Path("x.tif"))
    raw = _config()
    raw["segment"]["stitch_threshold"] = 0.25
    with pytest.raises(ValueError, match="must be 0.0 unless"):
        segment(image, raw, segmenter=Segmenter(FakeModel(), "a" * 64, "cpu", "4"))


@pytest.mark.parametrize("threshold", [0, 0.0, -0.25, 1.5, float("nan")])
def test_stitch_mode_rejects_thresholds_that_cannot_link_planes_before_inference(threshold):
    image = ImageVolume(np.ones((3, 5, 6, 1), dtype=np.float32), (2, 1, 1),
                        ("DAPI",), Path("x.tif"))
    raw = _config("stitch_2d")
    raw["segment"]["stitch_threshold"] = threshold
    fake = FakeModel()
    with pytest.raises(ValueError, match="stitch_threshold"):
        segment(image, raw, segmenter=Segmenter(fake, "a" * 64, "cpu", "4"))
    assert fake.calls == []


def test_killable_worker_applies_the_same_stitch_threshold_policy(monkeypatch, tmp_path):
    from queue import Queue
    from threading import Event

    from cellquant.segment import killable

    monkeypatch.setattr(
        "cellquant.segment.load_model",
        lambda spec, *args, **kwargs: Segmenter(FakeModel(), "a" * 64, "cpu", "4"),
    )
    image_path = tmp_path / "input.npy"
    np.save(image_path, np.ones((3, 5, 6), dtype=np.float32))
    results = Queue()

    killable._child_main(
        str(image_path),
        str(tmp_path / "masks.npy"),
        {"_model_spec": _config("stitch_2d")["segment"], "_runtime": {"seed": 0}},
        {"stitch_threshold": 0.0},
        "stitch_2d",
        Event(),
        Queue(),
        results,
    )

    status, payload = results.get_nowait()
    assert status == "error" and "stitch_threshold" in payload
    assert not (tmp_path / "masks.npy").exists()


@pytest.mark.parametrize("mode", ["single_plane_2d", "max_projection_2d"])
def test_singleton_2d_modes_send_yx_and_return_singleton_zyx(mode):
    image = ImageVolume(
        np.ones((1, 5, 6, 1), dtype=np.float32),
        (2, 1, 1),
        ("DAPI",),
        Path("x.tif"),
        {"analysis_volume": {"mode": mode, "original_z_depth": 4}},
    )
    fake = FakeModel()
    result = segment(
        image,
        _config(mode),
        segmenter=Segmenter(fake, "a" * 64, "cpu", "4"),
    )
    sent, kwargs = fake.calls[0]
    assert sent.shape == (5, 6)
    assert result.data.shape == (1, 5, 6)
    assert kwargs["do_3D"] is False
    assert kwargs["anisotropy"] is None
    assert kwargs["z_axis"] is None
    assert kwargs["stitch_threshold"] == 0
    assert result.provenance["analysis_volume"]["mode"] == mode


def test_multichannel_input_is_rejected_not_guessed():
    image = ImageVolume(np.ones((2, 3, 4, 2), dtype=np.float32), (1, 1, 1),
                        ("a", "b"), Path("x"))
    with pytest.raises(ValueError, match="single-channel"):
        segment(image, _config(), segmenter=Segmenter(FakeModel(), "a" * 64, "cpu", "4"))


class LazyArray:
    def __init__(self, data, calls=None):
        self._data = data
        self.shape = data.shape
        self.dtype = data.dtype
        self.calls = calls if calls is not None else []

    def __getitem__(self, item):
        return LazyArray(self._data[item], self.calls)

    def compute(self):
        self.calls.append(self.shape)
        return self._data


def test_lazy_input_and_blocking_inference_emit_truthful_events():
    lazy = LazyArray(np.ones((3, 8, 9, 1), dtype=np.float32))
    image = ImageVolume(lazy, (1.5, 0.5, 0.5), ("DAPI",), Path("x.tif"),
                        {"run_id": "r1", "file_id": "f1"})
    fake = FakeModel()
    events = []
    result = segment(
        image,
        _config(),
        events=events.append,
        segmenter=Segmenter(
            fake,
            "a" * 64,
            "cpu",
            "4.2.1.1",
            {"model_type": None, "diam_mean": None, "nchan": None},
        ),
    )
    assert result.provenance["constructor_parameters"]["model_type"] is None
    assert lazy.calls == [(3, 8, 9)]
    assert [event.kind for event in events] == ["materialized", "warning"]
    assert events[0].details["shape"] == [3, 8, 9]
    assert events[0].details["dtype"] == "float32"
    assert "requires" in events[0].details["reason"]
    assert "Cancel" in events[1].details["message"]
    assert events[1].details["cancellation_scope"] == "cellpose_progress_checkpoints"


def test_loader_passes_and_records_explicit_v4_constructor_defaults(monkeypatch, tmp_path):
    weight = tmp_path / "weights"
    weight.write_bytes(b"cellpose-test-weight")
    captured = {}

    class FakeCellposeModel:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.pretrained_model = kwargs["pretrained_model"]

    fake_cellpose = ModuleType("cellpose")
    fake_cellpose.models = SimpleNamespace(CellposeModel=FakeCellposeModel)
    fake_torch = ModuleType("torch")
    fake_torch.cuda = SimpleNamespace(is_available=lambda: False)
    fake_torch.device = lambda name: f"device:{name}"
    monkeypatch.setitem(sys.modules, "cellpose", fake_cellpose)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr("cellquant.segment.importlib.metadata.version", lambda name: "4.2.1.1")

    spec = _config()["segment"]
    spec["model"] = str(weight)
    spec["model_sha256"] = hashlib.sha256(weight.read_bytes()).hexdigest()
    loaded = load_model(spec)

    assert captured["model_type"] is None
    assert captured["diam_mean"] is None
    assert captured["nchan"] is None
    assert captured["use_bfloat16"] is True
    assert loaded.constructor_parameters["pretrained_model"] == str(weight)
    assert loaded.constructor_parameters["requested_model"] == str(weight)
    assert loaded.constructor_parameters["resolved_model_path"] == str(weight)
    assert loaded.constructor_parameters["device"] == "cpu"


def _fake_loader_runtime(monkeypatch, tmp_path, *, cuda_available=False):
    weight = tmp_path / "weights"
    weight.write_bytes(b"cellpose-device-policy-test")
    constructor_calls = []

    class FakeCellposeModel:
        def __init__(self, **kwargs):
            constructor_calls.append(kwargs)
            self.pretrained_model = kwargs["pretrained_model"]

    fake_cellpose = ModuleType("cellpose")
    fake_cellpose.models = SimpleNamespace(CellposeModel=FakeCellposeModel)
    fake_torch = ModuleType("torch")
    fake_torch.cuda = SimpleNamespace(is_available=lambda: cuda_available)
    fake_torch.device = lambda name: f"device:{name}"
    monkeypatch.setitem(sys.modules, "cellpose", fake_cellpose)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr("cellquant.segment.importlib.metadata.version", lambda name: "4.2.1.1")
    spec = _config()["segment"]
    spec["model"] = str(weight)
    spec["model_sha256"] = hashlib.sha256(weight.read_bytes()).hexdigest()
    return spec, constructor_calls


def test_cuda_unavailable_uses_and_records_only_explicitly_permitted_cpu_fallback(
    monkeypatch, tmp_path
):
    spec, constructor_calls = _fake_loader_runtime(monkeypatch, tmp_path)
    spec["device"] = "cuda"
    spec["allow_cpu_fallback"] = True
    events = []

    loaded = load_model(spec, events=events.append)

    assert loaded.device == "cpu"
    assert loaded.constructor_parameters["device"] == "cpu"
    assert constructor_calls[0]["gpu"] is False
    assert constructor_calls[0]["device"] == "device:cpu"
    assert len(events) == 1 and events[0].kind == "warning"
    assert events[0].details["requested_device"] == "cuda"
    assert events[0].details["effective_device"] == "cpu"


def test_cuda_unavailable_is_fatal_when_cpu_fallback_is_forbidden(monkeypatch, tmp_path):
    spec, constructor_calls = _fake_loader_runtime(monkeypatch, tmp_path)
    spec["device"] = "cuda"
    spec["allow_cpu_fallback"] = False
    events = []

    with pytest.raises(RuntimeError, match="CUDA was required"):
        load_model(spec, events=events.append)

    assert constructor_calls == []
    assert events == []


def test_auto_device_resolution_is_explicitly_recorded(monkeypatch, tmp_path):
    spec, constructor_calls = _fake_loader_runtime(monkeypatch, tmp_path)
    spec["device"] = "auto"
    spec["allow_cpu_fallback"] = False
    events = []

    loaded = load_model(spec, events=events.append)

    assert loaded.device == "cpu"
    assert loaded.constructor_parameters["device"] == "cpu"
    assert loaded.constructor_parameters["gpu"] is False
    assert constructor_calls[0]["device"] == "device:cpu"
    assert events == []


def test_auto_device_records_cuda_when_available(monkeypatch, tmp_path):
    spec, constructor_calls = _fake_loader_runtime(
        monkeypatch, tmp_path, cuda_available=True
    )
    spec["device"] = "auto"
    spec["allow_cpu_fallback"] = False

    loaded = load_model(spec)

    assert loaded.device == "cuda"
    assert loaded.constructor_parameters["device"] == "cuda"
    assert loaded.constructor_parameters["gpu"] is True
    assert constructor_calls[0]["device"] == "device:cuda"


def test_segment_provenance_records_requested_and_resolved_model():
    image = ImageVolume(np.ones((2, 6, 6, 1), dtype=np.float32), (1, 1, 1),
                        ("DAPI",), Path("x.tif"))
    fake = FakeModel()
    constructor_parameters = {
        "requested_model": "cpsam_v2",
        "resolved_model_path": "/weights/cpsam_v2",
        "engine": "v4",
    }
    result = segment(
        image,
        _config(),
        segmenter=Segmenter(fake, "a" * 64, "cpu", "4.2.1.1", constructor_parameters),
    )
    assert result.provenance["requested_model"] == "cpsam_v2"
    assert result.provenance["resolved_model_path"] == "/weights/cpsam_v2"
    assert result.provenance["model_sha256"] == "a" * 64
    assert result.provenance["constructor_parameters"]["requested_model"] == "cpsam_v2"


def test_v3_rejects_misspelled_builtin(monkeypatch, tmp_path):
    fake_cellpose = ModuleType("cellpose")
    fake_cellpose.models = SimpleNamespace(CellposeModel=lambda **kwargs: None)
    fake_torch = ModuleType("torch")
    fake_torch.cuda = SimpleNamespace(is_available=lambda: False)
    fake_torch.device = lambda name: f"device:{name}"
    monkeypatch.setitem(sys.modules, "cellpose", fake_cellpose)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr("cellquant.segment.importlib.metadata.version", lambda name: "3.0.11")

    spec = _config()["segment"]
    spec["engine"] = "v3"
    spec["model"] = "nucleii"
    spec["model_sha256"] = ACCEPT_MEASURED_MODEL_HASH

    with pytest.raises(ValueError, match="unknown Cellpose v3 model"):
        load_model(spec)


def test_v3_rejects_missing_custom_weight_path(monkeypatch, tmp_path):
    fake_cellpose = ModuleType("cellpose")
    fake_cellpose.models = SimpleNamespace(CellposeModel=lambda **kwargs: None)
    fake_torch = ModuleType("torch")
    fake_torch.cuda = SimpleNamespace(is_available=lambda: False)
    fake_torch.device = lambda name: f"device:{name}"
    monkeypatch.setitem(sys.modules, "cellpose", fake_cellpose)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr("cellquant.segment.importlib.metadata.version", lambda name: "3.0.11")

    missing = tmp_path / "missing" / "custom.pth"
    spec = _config()["segment"]
    spec["engine"] = "v3"
    spec["model"] = str(missing)
    spec["model_sha256"] = ACCEPT_MEASURED_MODEL_HASH

    with pytest.raises(FileNotFoundError, match="custom Cellpose v3 model path does not exist"):
        load_model(spec)
