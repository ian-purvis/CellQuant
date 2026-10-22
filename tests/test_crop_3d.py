"""Crop to the region of interest in Z-stacks: found on the maximum projection, all slices kept."""

from __future__ import annotations

import numpy as np
import pytest
import tifffile

from cellquant import segmentation
from cellquant.crop import crop_log_line, regions_for
from cellquant.hardware import batch_notes
from cellquant.image import load_image
from cellquant.pipeline import process_image
from cellquant.recipe import load_recipe
from cellquant.volume import segment_volume
from tests.test_cellpose_engines import _install_fake
from tests.test_crop import CROP, PIXEL, _image, _objects_by_pixels, _recipe

SLICES = 3

_FAKE = """
import numpy as np, types
from skimage.measure import label
CALLS = []
MODEL_NAMES = ["cpsam_v2"]

class CellposeModel:
    def __init__(self, gpu=False, pretrained_model="cpsam_v2", model_type=None, diam_mean=None, device=None, nchan=None, use_bfloat16=True):
        self.device = types.SimpleNamespace(type="cpu")

    def eval(self, x, normalize=True, diameter=None, do_3D=False, flow_threshold=0.4, cellprob_threshold=0.0, min_size=15, **kwargs):
        data = np.asarray(x, dtype=np.float32)
        CALLS.append(dict(normalize=normalize, shape=data.shape, do_3D=do_3D, max=float(data.max())))
        return label(data > 0.5).astype(np.int32), None, None
"""


def _stack_file(tmp_path):
    """The 2D test picture repeated in SLICES slices (Z, C, Y, X), saved with its pixel sizes."""

    plane = _image()
    data = np.stack([plane] * SLICES, axis=0)
    path = tmp_path / "stack.tif"
    tifffile.imwrite(
        path, data, imagej=True, resolution=(1 / PIXEL, 1 / PIXEL), metadata={"axes": "ZCYX", "unit": "um", "spacing": 1.5}
    )
    return path


def _install(tmp_path, monkeypatch):
    models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", _FAKE)
    monkeypatch.setattr(segmentation, "_MODELS", {})
    return models


@pytest.mark.parametrize("mode", ["full_3d", "stitch_slices"])
def test_a_stack_is_cropped_in_xy_only_and_agrees_with_the_whole_stack(tmp_path, monkeypatch, mode):
    models = _install(tmp_path, monkeypatch)
    path = _stack_file(tmp_path)
    loaded = load_image(path, z_mode=mode)
    assert loaded.is_3d
    recipe = _recipe("cellpose", CROP)
    recipe["z_stack"] = mode
    full = process_image(loaded, {**recipe, "crop": None})
    models.CALLS.clear()
    cropped = process_image(loaded, recipe)
    rectangles = cropped.provenance["crop_rectangles"]
    assert rectangles and full.provenance["crop_rectangles"] is None
    assert cropped.labels.shape == full.labels.shape == (SLICES, 400, 400)
    # Every part saw all the slices, scaled like the whole stack (not again), in one pass each.
    calls = models.CALLS
    assert all(len(call["shape"]) == (3 if mode == "full_3d" else 2) for call in calls)
    assert all(call["shape"][0] == SLICES for call in calls if mode == "full_3d")
    assert not any(call["normalize"] for call in calls)
    assert len(calls) == len(rectangles) * (1 if mode == "full_3d" else SLICES)
    covered = np.zeros((400, 400), bool)
    for y0, y1, x0, x1 in rectangles:
        covered[y0:y1, x0:x1] = True
    assert not np.any(cropped.labels[:, ~covered])
    edge = set(cropped.objects.loc[cropped.objects["at_crop_edge"], "object_id"])

    def inside(value):
        rows, cols = np.nonzero((full.labels == value).any(axis=0))
        return any(
            rows.min() > y0 and rows.max() < y1 - 1 and cols.min() > x0 and cols.max() < x1 - 1 for y0, y1, x0, x1 in rectangles
        )

    expected = _objects_by_pixels(full.labels, inside)
    assert expected and _objects_by_pixels(cropped.labels, lambda value: value not in edge) == expected
    assert cropped.provenance["crop"]["fraction"] < 0.7


def test_object_numbers_are_unique_across_regions_in_a_stack(tmp_path, monkeypatch):
    _install(tmp_path, monkeypatch)
    volume = np.zeros((3, 60, 100), dtype=np.float32)
    volume[:, 5:15, 5:15] = 1
    volume[:, 40:50, 70:80] = 1
    rectangles = [(0, 30, 0, 30), (30, 60, 60, 100)]
    labels = segment_volume(volume, "cellpose", {"diameter_px": 10}, "full_3d", rectangles=rectangles)
    assert sorted(np.unique(labels)) == [0, 1, 2]
    assert labels[1, 10, 10] != labels[1, 45, 75]


def test_the_region_is_found_on_the_projection_even_when_the_signal_sits_in_one_slice(tmp_path):
    plane = _image(blob=False)
    data = np.zeros((SLICES,) + plane.shape, dtype=plane.dtype)
    data[1] = plane  # the reporter is in the middle slice only
    path = tmp_path / "one_slice.tif"
    tifffile.imwrite(path, data, imagej=True, resolution=(1 / PIXEL, 1 / PIXEL), metadata={"axes": "ZCYX", "unit": "um", "spacing": 1.5})
    loaded = load_image(path, z_mode="full_3d")
    projected = load_image(plane, pixel_size_x=PIXEL, pixel_size_y=PIXEL, channel_axis=0)
    recipe = load_recipe({**_recipe("cellpose", CROP), "z_stack": "full_3d"})
    assert regions_for(loaded, recipe) == regions_for(projected, recipe)


def test_the_classical_method_is_not_cropped_in_a_stack_and_says_so(tmp_path):
    loaded = load_image(_stack_file(tmp_path), z_mode="full_3d")
    recipe = load_recipe({**_recipe("classical", CROP), "z_stack": "full_3d"})
    info: dict = {}
    assert regions_for(loaded, recipe, info) is None and "needs Cellpose" in info["warning"]
    assert crop_log_line(None, info).startswith("Crop: skipped.")


def test_the_crop_is_logged_once_per_image_for_a_stack(tmp_path, monkeypatch):
    _install(tmp_path, monkeypatch)
    batch_notes()
    loaded = load_image(_stack_file(tmp_path), z_mode="full_3d")
    recipe = {**_recipe("cellpose", CROP), "z_stack": "full_3d"}
    process_image(loaded, recipe)
    lines = [line for line in batch_notes() if line.startswith("Crop:")]
    assert len(lines) == 1 and "of the frame" in lines[0] and "to find" in lines[0]


def test_crop_log_lines():
    assert crop_log_line([(0, 1, 0, 1)] * 3, {"fraction": 0.381, "seconds": 0.43}) == "Crop: 38% of the frame (3 regions), 0.4 s to find"
    assert crop_log_line([(0, 1, 0, 1)], {"fraction": 0.5, "seconds": 1.0}) == "Crop: 50% of the frame (1 region), 1.0 s to find"
    assert crop_log_line(None, {"fraction": 0.82, "skipped": True}) == "Crop: skipped, regions cover 82% of the frame"
    assert crop_log_line([], {"fraction": 0.0}) == "Crop: no positive region found, whole frame used"
