"""Z-stack recommendations for the computer CellQuant runs on."""

from __future__ import annotations

import pytest

from cellquant import hardware
from cellquant.hardware import Hardware, recommend

GPU_12GB = Hardware(gpu_available=True, gpu_name="NVIDIA GeForce RTX 5060 Ti", gpu_memory_gb=16, ram_gb=32, cpu_count=12)
LAPTOP = Hardware(gpu_available=False, ram_gb=16, cpu_count=8)
# The E14.5_E17.5 ND2 files: 7 slices of 1024 x 1024, 20x (0.575 µm/px), 1.5 µm steps.
RETINA = [(7, 1024, 1024)] * 9
RETINA_ANISOTROPY = 1.5 / 0.575


@pytest.fixture(autouse=True)
def _no_measured_speeds():
    hardware.forget_speeds()
    yield
    hardware.forget_speeds()


def test_gpu_with_coarse_z_steps_recommends_linked_slices():
    suggestion = recommend(method="cellpose", engine="cellpose4", use_gpu=True, stacks=RETINA, anisotropy=RETINA_ANISOTROPY, hardware=GPU_12GB)
    assert suggestion.mode == "stitch_slices"
    assert "too coarse" in suggestion.reason
    assert set(suggestion.estimates) == {"max_projection", "single_plane", "stitch_slices", "full_3d"}


def test_gpu_with_fine_z_steps_recommends_the_whole_volume():
    suggestion = recommend(method="cellpose", engine="cellpose4", use_gpu=True, stacks=[(30, 1024, 1024)], anisotropy=1.5, hardware=GPU_12GB)
    assert suggestion.mode == "full_3d"


def test_cellpose_sam_without_a_gpu_recommends_a_projection_first_and_says_why():
    suggestion = recommend(method="cellpose", engine="cellpose4", use_gpu=False, stacks=RETINA, anisotropy=RETINA_ANISOTROPY, hardware=LAPTOP)
    assert suggestion.mode == "max_projection"
    assert "Classic Cellpose is much faster" in suggestion.reason
    assert suggestion.estimates["stitch_slices"].seconds_per_image > suggestion.estimates["max_projection"].seconds_per_image


def test_classic_cellpose_on_a_laptop_can_still_do_3d():
    suggestion = recommend(method="cellpose", engine="cellpose3", use_gpu=False, stacks=RETINA, anisotropy=RETINA_ANISOTROPY, hardware=LAPTOP)
    assert suggestion.mode == "stitch_slices"


def test_an_unused_gpu_is_pointed_out():
    suggestion = recommend(method="cellpose", engine="cellpose4", use_gpu=False, stacks=RETINA, anisotropy=RETINA_ANISOTROPY, hardware=GPU_12GB)
    assert suggestion.use_gpu is True and "Use GPU" in suggestion.reason


def test_measured_speed_replaces_the_starting_estimate():
    before = recommend(method="classical", engine=None, use_gpu=False, stacks=RETINA, anisotropy=RETINA_ANISOTROPY, hardware=LAPTOP)
    hardware.record_speed("classical", None, "cpu", "stitch_slices", seconds=70.0, slice_megapixels=7 * 1.048576)
    after = recommend(method="classical", engine=None, use_gpu=False, stacks=RETINA, anisotropy=RETINA_ANISOTROPY, hardware=LAPTOP)
    assert not before.estimates["stitch_slices"].measured
    assert after.estimates["stitch_slices"].measured
    assert after.estimates["stitch_slices"].seconds_per_image == pytest.approx(70.0)


def test_memory_warning_for_a_huge_volume():
    small = Hardware(gpu_available=True, gpu_memory_gb=8, ram_gb=8, cpu_count=4)
    suggestion = recommend(method="cellpose", engine="cellpose4", use_gpu=True, stacks=[(60, 4096, 4096)], anisotropy=3.0, hardware=small)
    assert suggestion.estimates["full_3d"].warning
    assert suggestion.mode != "full_3d"


def test_no_recommendation_without_z_stacks():
    assert recommend(method="classical", engine=None, use_gpu=False, stacks=[(1, 512, 512)], anisotropy=None, hardware=LAPTOP) is None


def test_hardware_description_is_readable():
    assert GPU_12GB.describe() == "GPU: NVIDIA GeForce RTX 5060 Ti, 16 GB, 32 GB memory, 12 CPU cores"
    assert hardware.format_seconds(45) == "45 s" and hardware.format_seconds(600) == "10 min"


def test_a_real_run_records_its_speed(tmp_path):
    from tests.test_3d import _recipe, _stack

    from cellquant.controller import AnalysisController

    path = tmp_path / "stack.tif"
    _stack(path)
    controller = AnalysisController.create(tmp_path / "results", "Speed")
    controller.add_image_paths([path])
    controller.set_recipe(_recipe("stitch_slices"))
    result = controller.run_image(controller.experiment.images[0].image_id)
    assert result.provenance["segmentation_seconds"] > 0
    assert ("classical", "cpu", "stitch_slices") in hardware._MEASURED


def test_a_gpu_that_pytorch_cannot_use_is_named_with_the_fix(monkeypatch):
    from cellquant import engines

    monkeypatch.setattr(engines, "_torch_gpu_status", lambda: {"available": False, "name": "", "reason": "This PyTorch build has no GPU support."})
    monkeypatch.setattr(engines, "nvidia_gpu_name", lambda: "NVIDIA GeForce RTX 4090")
    status = engines.gpu_status()
    assert status["nvidia_gpu"] == "NVIDIA GeForce RTX 4090"
    assert "Install CellQuant.bat" in status["reason"] and "[U] Update" in status["reason"]
    monkeypatch.setattr(engines, "nvidia_gpu_name", lambda: "")
    assert "nvidia_gpu" not in engines.gpu_status()


def test_nd2_files_that_cannot_be_memory_mapped_are_read_from_an_open_file(tmp_path, monkeypatch):
    """OneDrive files not yet downloaded fail to memory-map with [Errno 22]."""

    import io
    import types

    from cellquant import image

    path = tmp_path / "online only.nd2"
    path.write_bytes(b"nd2")
    opened = []

    class FakeND2File:
        def __init__(self, source):
            if not isinstance(source, io.BufferedReader):
                raise OSError(22, "Invalid argument")
            opened.append(source)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    fake = types.SimpleNamespace(ND2File=FakeND2File)
    with image._open_nd2(fake, path) as handle:
        assert isinstance(handle, FakeND2File) and not opened[0].closed
    assert opened[0].closed
    assert "Always keep on this device" in image._nd2_failure(tmp_path / "OneDrive - Lab" / "a.nd2", OSError(22, "Invalid argument"))
