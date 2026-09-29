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
