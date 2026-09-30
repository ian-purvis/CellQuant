"""Cellpose-SAM precision chosen from the hardware (N2) and the CPU thread benchmark (N3)."""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from cellquant import hardware, segmentation
from cellquant.segmentation import engine_signature, segment_objects
from tests.test_cellpose_engines import _V4, _install_fake

_V4_PRECISION = _V4.replace(
    'CALLS.append(("CellposeModel.__init__", dict(gpu=gpu, pretrained_model=pretrained_model, model_type=model_type)))',
    'CALLS.append(("CellposeModel.__init__", dict(gpu=gpu, pretrained_model=pretrained_model, use_bfloat16=use_bfloat16)))',
)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    from cellquant import engines

    monkeypatch.setattr(segmentation, "_MODELS", {})
    monkeypatch.setattr(hardware, "_BF16", {})
    monkeypatch.setattr(hardware, "_THREADS_APPLIED", set())
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    engines.cellpose_engine.cache_clear()
    yield
    engines.cellpose_engine.cache_clear()


def _image() -> np.ndarray:
    return np.zeros((20, 20), dtype=np.float32)


def _inits(models) -> list[dict]:
    return [options for name, options in models.CALLS if name == "CellposeModel.__init__"]


@pytest.mark.parametrize("native, expected", [(False, "float32"), (True, "bfloat16")])
def test_cellpose_sam_uses_bfloat16_only_where_the_device_supports_it(tmp_path, monkeypatch, native, expected):
    models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", _V4_PRECISION)
    monkeypatch.setattr(hardware, "bfloat16_native", lambda gpu: native)
    details: dict = {}
    segment_objects(_image(), "cellpose", {}, details=details)
    assert _inits(models)[-1]["use_bfloat16"] is (expected == "bfloat16")
    assert details["engine"]["precision"] == expected  # recorded in provenance


def test_a_recipe_can_choose_the_precision_and_it_is_part_of_the_cache_key(tmp_path, monkeypatch):
    models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", _V4_PRECISION)
    monkeypatch.setattr(hardware, "bfloat16_native", lambda gpu: False)
    segment_objects(_image(), "cellpose", {"precision": "bfloat16"})
    segment_objects(_image(), "cellpose", {"precision": "float32"})
    assert [item["use_bfloat16"] for item in _inits(models)] == [True, False]  # one model per precision
    assert engine_signature("cellpose", {"precision": "bfloat16"}) != engine_signature("cellpose", {"precision": "float32"})
    assert engine_signature("cellpose", {}) == engine_signature("cellpose", {"precision": "float32"})
    with pytest.raises(segmentation.SegmentationError, match="Unknown precision"):
        segment_objects(_image(), "cellpose", {"precision": "half"})


def test_native_bfloat16_is_read_from_pytorch(monkeypatch):
    fake = types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: True, is_bf16_supported=lambda: True),
        ops=types.SimpleNamespace(mkldnn=types.SimpleNamespace(_is_mkldnn_bf16_supported=lambda: False)),
    )
    monkeypatch.setitem(sys.modules, "torch", fake)
    assert hardware.bfloat16_native(gpu=True) is True
    assert hardware.bfloat16_native(gpu=False) is False  # an AVX2 laptop CPU: emulated, so float32
    assert hardware.cellpose_precision({}, "cellpose3") == "float32"


def _fake_torch(monkeypatch):
    state = {"threads": 8, "set": []}

    def set_num_threads(count):
        state["threads"] = count
        state["set"].append(count)

    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(get_num_threads=lambda: state["threads"], set_num_threads=set_num_threads))
    return state


def test_the_thread_benchmark_keeps_the_fastest_count_and_applies_it(monkeypatch):
    import time

    state = _fake_torch(monkeypatch)
    monkeypatch.setattr(hardware.os, "cpu_count", lambda: 8)
    delays = {2: 0.03, 4: 0.005, 6: 0.02, 8: 0.04}

    def run(image):
        time.sleep(delays[state["threads"]])
        return (image > 400).astype(np.int32)

    result = hardware.benchmark_threads("cellpose3", run=run)
    assert result["threads"] == 4 and result["identical"] and set(result["seconds"]) == {"2", "4", "6", "8"}
    assert state["threads"] == 8  # the count before the benchmark is restored
    assert hardware.best_threads("cellpose3") == 4
    assert hardware.settings_path().is_file()
    monkeypatch.setattr(hardware, "_THREADS_APPLIED", set())
    assert hardware.apply_threads("cellpose3") == 4 and state["threads"] == 4


def test_results_that_change_with_the_thread_count_are_refused(monkeypatch):
    state = _fake_torch(monkeypatch)
    monkeypatch.setattr(hardware.os, "cpu_count", lambda: 4)
    result = hardware.benchmark_threads("cellpose3", counts=(2, 4), run=lambda image: np.full((2, 2), state["threads"]))
    assert not result["identical"] and "error" in result
    assert hardware.best_threads("cellpose3") is None


def test_real_classic_cellpose_gives_identical_masks_at_two_thread_counts():
    """One real Cellpose 3 run at 1 and at 4 threads on the benchmark image (skipped without Cellpose 3)."""

    import importlib

    from cellquant.engines import cellpose_engine

    for name in [name for name in sys.modules if name == "cellpose" or name.startswith("cellpose.")]:
        del sys.modules[name]  # stand-ins from other tests; import the installed Cellpose
    importlib.invalidate_caches()
    cellpose_engine.cache_clear()
    if cellpose_engine().key != "cellpose3":
        pytest.skip("needs classic Cellpose (cellpose3) installed")
    torch = pytest.importorskip("torch")
    image = hardware._benchmark_image()
    before = torch.get_num_threads()
    try:
        torch.set_num_threads(1)
        one = segment_objects(image, "cellpose", {"diameter_px": 20})
        torch.set_num_threads(4)
        four = segment_objects(image, "cellpose", {"diameter_px": 20})
    finally:
        torch.set_num_threads(before)
    assert int(one.max()) >= 30
    assert np.array_equal(one, four)
