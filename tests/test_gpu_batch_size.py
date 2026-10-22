"""Cellpose's batch size follows the NVIDIA GPU's memory; the step line says what a long 3D run is doing."""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from cellquant import engines, hardware, progress, segmentation
from cellquant.segmentation import CellposeBackend
from tests.test_cellpose_engines import _V4, _install_fake

# The stand-in runs on "cuda", records each eval's batch size, returns labels the shape of its input,
# and runs out of memory above OOM_ABOVE when that is set.
_V4_CUDA = _V4.replace(
    'self.device = types.SimpleNamespace(type="cpu")',
    'self.device = types.SimpleNamespace(type="cuda")',
).replace(
    '        CALLS.append(("CellposeModel.eval", dict(diameter=diameter, channels=channels)))\n'
    '        masks = np.zeros(np.asarray(x).shape[-2:], dtype=np.int32)',
    '        CALLS.append(("CellposeModel.eval", dict(batch_size=batch_size, do_3D=do_3D)))\n'
    '        if OOM_ABOVE and batch_size > OOM_ABOVE:\n'
    '            raise RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB")\n'
    '        masks = np.zeros(np.asarray(x).shape, dtype=np.int32)',
).replace("CALLS = []", "CALLS = []\nOOM_ABOVE = 0")


def _torch(memory_gb: float):
    properties = types.SimpleNamespace(total_memory=memory_gb * 1024**3)
    return types.SimpleNamespace(
        cuda=types.SimpleNamespace(
            is_available=lambda: True,
            is_bf16_supported=lambda: False,
            get_device_properties=lambda device: properties,
            get_device_name=lambda device: "RTX 6000 Ada",
            empty_cache=lambda: None,
        ),
        backends=types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: False)),
        set_num_threads=lambda count: None,
        manual_seed=lambda seed: None,
    )


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(segmentation, "_MODELS", {})
    monkeypatch.setattr(hardware, "_BATCH_LIMIT", None)
    monkeypatch.setattr(hardware, "_BATCH_NOTES", [])
    monkeypatch.setattr(hardware, "_BATCH_ANNOUNCED", set())
    monkeypatch.setattr(hardware, "_BF16", {})
    monkeypatch.setattr(hardware, "_THREADS_APPLIED", set())
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    engines.cellpose_engine.cache_clear()
    yield
    engines.cellpose_engine.cache_clear()


@pytest.mark.parametrize(
    ("memory_gb", "size"),
    [(None, 8), (4, 8), (8, 8), (11.9, 8), (12, 16), (16, 16), (24, 32), (32, 32), (40, 64), (49, 64), (80, 64)],
)
def test_batch_size_follows_gpu_memory(memory_gb, size):
    assert hardware.batch_size_for_memory(memory_gb) == size


def test_cpu_and_apple_gpu_keep_cellposes_default(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", _torch(49))
    assert hardware.cellpose_batch_size(types.SimpleNamespace(type="cpu")) == 8
    assert hardware.cellpose_batch_size(types.SimpleNamespace(type="mps")) == 8
    assert hardware.cellpose_batch_size(None) == 8
    assert hardware.cellpose_batch_size(types.SimpleNamespace(type="cuda")) == 64


def test_unknown_gpu_memory_keeps_the_default(monkeypatch):
    broken = _torch(49)
    broken.cuda.get_device_properties = lambda device: (_ for _ in ()).throw(RuntimeError("no driver"))
    monkeypatch.setitem(sys.modules, "torch", broken)
    assert hardware.cellpose_batch_size(types.SimpleNamespace(type="cuda")) == 8


def _segment_2d(parameters=None):
    return CellposeBackend().segment_with_details(np.zeros((20, 20), np.float32), {"gpu": True, "diameter_px": 10, **(parameters or {})})


def test_a_big_gpu_sends_larger_batches(tmp_path, monkeypatch):
    models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", _V4_CUDA)
    monkeypatch.setitem(sys.modules, "torch", _torch(49))
    _labels, details = _segment_2d()
    assert [call[1]["batch_size"] for call in models.CALLS if call[0] == "CellposeModel.eval"] == [64]
    assert details["batch_size"] == 64 and details["device"] == "cuda"


def test_a_small_gpu_calls_cellpose_as_before(tmp_path, monkeypatch):
    models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", _V4_CUDA)
    monkeypatch.setitem(sys.modules, "torch", _torch(6))
    calls = []
    original = models.CellposeModel.eval
    monkeypatch.setattr(models.CellposeModel, "eval", lambda self, x, **kw: calls.append(kw) or original(self, x, **kw))
    _labels, details = _segment_2d()
    assert "batch_size" not in calls[0] and details["batch_size"] == 8


def test_out_of_memory_halves_the_batch_and_remembers_it(tmp_path, monkeypatch):
    models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", _V4_CUDA)
    monkeypatch.setitem(sys.modules, "torch", _torch(49))
    models.OOM_ABOVE = 16
    _labels, details = _segment_2d()
    assert [call[1]["batch_size"] for call in models.CALLS if call[0] == "CellposeModel.eval"] == [64, 32, 16]
    assert details["batch_size"] == 16
    models.CALLS.clear()
    _segment_2d()  # the next image starts at the size that fitted
    assert [call[1]["batch_size"] for call in models.CALLS if call[0] == "CellposeModel.eval"] == [16]
    notes = hardware.batch_notes()
    assert notes[0] == "Cellpose batch size 64 on RTX 6000 Ada (49 GB)."
    assert "ran out of memory at Cellpose batch size 64; trying the image again at 32" in notes[1]
    assert "ran out of memory at Cellpose batch size 32; trying the image again at 16" in notes[2]
    assert len(notes) == 3 and hardware.batch_notes() == []  # each line is logged once
    assert hardware.load_settings()["gpu_batch_size"] == {"RTX 6000 Ada": 16}


def test_the_size_that_fitted_is_kept_on_this_computer(tmp_path, monkeypatch):
    hardware.save_settings({"gpu_batch_size": {"RTX 6000 Ada": 32}})
    monkeypatch.setitem(sys.modules, "torch", _torch(49))  # a new session: nothing lowered yet
    assert hardware.cellpose_batch_size(types.SimpleNamespace(type="cuda")) == 32
    assert hardware.batch_notes() == ["Cellpose batch size 32 on RTX 6000 Ada (49 GB) (the largest that fitted on this computer before)."]
    hardware.save_settings({"gpu_batch_size": {"Another GPU": 8}})  # saved for a different GPU: not used
    assert hardware.cellpose_batch_size(types.SimpleNamespace(type="cuda")) == 64


def test_out_of_memory_at_the_default_is_reported(tmp_path, monkeypatch):
    models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", _V4_CUDA)
    monkeypatch.setitem(sys.modules, "torch", _torch(49))
    models.OOM_ABOVE = 4
    with pytest.raises(segmentation.SegmentationError, match="out of memory"):
        _segment_2d()


def test_other_errors_are_not_retried(tmp_path, monkeypatch):
    models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", _V4_CUDA)
    monkeypatch.setitem(sys.modules, "torch", _torch(49))
    monkeypatch.setattr(models.CellposeModel, "eval", lambda self, x, **kw: (_ for _ in ()).throw(ValueError("bad input")))
    with pytest.raises(segmentation.SegmentationError, match="bad input"):
        _segment_2d()


def test_full_3d_says_loading_then_segmenting_on_the_gpu(tmp_path, monkeypatch):
    models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", _V4_CUDA)
    monkeypatch.setitem(sys.modules, "torch", _torch(49))
    steps = []
    volume = np.zeros((7, 20, 20), np.float32)
    with progress.reporting(lambda text, fraction: steps.append(text)):
        labels, details = CellposeBackend().segment_volume(
            volume, {"gpu": True, "diameter_px": 10}, mode="full_3d", scale="stack", anisotropy=2.0
        )
        CellposeBackend().segment_volume(volume, {"gpu": True, "diameter_px": 10}, mode="full_3d", scale="stack", anisotropy=2.0)
    assert labels.shape == volume.shape
    assert steps[0].startswith("Loading Cellpose-SAM")
    assert steps[1] == "Finding objects with Cellpose-SAM in the whole volume on the GPU (cannot be stopped part-way)"
    assert sum(text.startswith("Loading") for text in steps) == 1  # the model is loaded once per session
    assert details["batch_size"] == 64
    assert [call[1] for call in models.CALLS if call[0] == "CellposeModel.eval"][0] == {"batch_size": 64, "do_3D": True}


def test_time_left_reports_image_times():
    now = [0.0]
    clock = progress.TimeLeft(clock=lambda: now[0])
    now[0] = 75.0
    assert clock.image_seconds() == 75.0 and clock.last_image_seconds() is None
    clock.image_finished(1, 2)
    assert clock.last_image_seconds() == 75.0 and clock.image_seconds() == 0.0


@pytest.mark.parametrize(("seconds", "text"), [(4.6, "4 s"), (59, "59 s"), (252, "4 min 12 s"), (3780, "1 h 3 min")])
def test_elapsed_text(seconds, text):
    assert progress.elapsed_text(seconds) == text
