"""Crop to the region of interest before segmenting (C1), on synthetic images."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import tifffile

from cellquant import segmentation
from cellquant.crop import crop_channel_warnings, crop_channels, detect_regions
from cellquant.pipeline import process_image
from cellquant.recipe import CropSpec, load_recipe
from tests.test_cellpose_engines import _install_fake

SIZE = 400
PIXEL = 0.5  # µm per pixel
REPORTER = (slice(40, 150), slice(40, 150))  # where the reporter-positive nuclei are


def _image(blob: bool = True) -> np.ndarray:
    """Channel 0: nuclei everywhere (a grid). Channel 1: the reporter, in one area only, plus
    (optionally) a large bright non-nuclear blob. Channel 2: a marker in half of the reporter cells."""

    rng = np.random.default_rng(3)
    yy, xx = np.mgrid[:SIZE, :SIZE]
    nuclei = rng.normal(60, 4, (SIZE, SIZE))
    reporter = rng.normal(40, 4, (SIZE, SIZE))
    marker = rng.normal(40, 4, (SIZE, SIZE))
    index = 0
    for cy in range(20, SIZE, 24):
        for cx in range(20, SIZE, 24):
            disc = (yy - cy) ** 2 + (xx - cx) ** 2 <= 36  # radius 6 px = 6 µm diameter
            nuclei[disc] = 1000
            if REPORTER[0].start <= cy < REPORTER[0].stop and REPORTER[1].start <= cx < REPORTER[1].stop:
                reporter[disc] = 900
                if index % 2:
                    marker[disc] = 800
            index += 1
    if blob:
        reporter[260:360, 250:360] = 700  # bright, solid, not made of nuclei
    return np.clip(np.stack([nuclei, reporter, marker]), 0, 65535).astype(np.uint16)


def _recipe(algorithm: str = "classical", crop: dict | None = None, **parameters) -> dict:
    base = {"threshold_method": "otsu", "sigma": 1.0} if algorithm == "classical" else {}
    return {
        "object_set": {"name": "Nuclei", "segmentation_channel": 0, "algorithm": algorithm, "parameters": {**base, **parameters}},
        "crop": crop,
        "measurements": [
            {"id": "reporter_mean", "channel": 1, "region": {"type": "object"}, "statistic": "mean"},
            {"id": "marker_mean", "channel": 2, "region": {"type": "object"}, "statistic": "mean"},
        ],
        "classifications": [
            {"id": "reporter_pos", "name": "Fluor", "measurement": "reporter_mean", "threshold": 400},
            {"id": "marker_pos", "name": "OTX2", "measurement": "marker_mean", "threshold": 400},
        ],
        "reports": [{"numerator": "Fluor AND OTX2", "denominator": "Fluor"}],
    }


CROP = {"enabled": True, "margin_um": 10.0, "merge_gap_um": 20.0}


def _objects_by_pixels(labels: np.ndarray, keep) -> set[frozenset]:
    """Each object as its set of pixels, so object numbers do not matter."""

    found = set()
    for value in np.unique(labels):
        if value == 0 or not keep(value):
            continue
        coordinates = np.argwhere(labels == value)
        found.add(frozenset(map(tuple, coordinates.tolist())))
    return found


def test_the_region_keeps_every_reporter_cell_and_leaves_out_tissue_without_reporter():
    info: dict = {}
    rectangles = detect_regions([_image()[1]], PIXEL, CropSpec(**CROP), info)
    covered = np.zeros((SIZE, SIZE), bool)
    for y0, y1, x0, x1 in rectangles:
        covered[y0:y1, x0:x1] = True
    # Inclusive: every reporter-positive nucleus (centres 44-140) lies wholly inside, with margin.
    assert covered[30:160, 30:160].all()
    # The bright blob is positive too, so it is kept (safe); nuclei without reporter elsewhere are not.
    assert covered[270:350, 260:350].all()
    assert not covered[200:240, 0:200].any() and not covered[0:200, 220:400].any()
    assert 0 < info["fraction"] < 0.7 and info["skipped"] is False and info["seconds"] >= 0
    assert detect_regions([np.full((50, 50), 7.0)], PIXEL, CropSpec(**CROP)) == []


def test_rectangles_closer_than_the_merge_gap_become_one():
    from cellquant.crop import merge_rectangles

    assert merge_rectangles([(0, 10, 0, 10), (0, 10, 30, 40)], gap=25) == [(0, 10, 0, 40)]
    assert merge_rectangles([(0, 10, 0, 10), (0, 10, 30, 40)], gap=5) == [(0, 10, 0, 10), (0, 10, 30, 40)]


def test_an_image_whose_region_covers_most_of_it_is_not_cropped():
    from cellquant.image import load_image
    from cellquant.crop import regions_for

    everywhere = _image()
    everywhere[1] = everywhere[0]  # every nucleus is reporter-positive
    loaded = load_image(everywhere, pixel_size_x=PIXEL, pixel_size_y=PIXEL, channel_axis=0)
    info: dict = {}
    assert regions_for(loaded, load_recipe(_recipe("classical", CROP)), info) is None
    assert info["skipped"] and info["fraction"] > 0.7 and "segmented in full" in info["note"]
    result = process_image(everywhere, _recipe("classical", CROP), pixel_size_x=PIXEL, pixel_size_y=PIXEL, channel_axis=0)
    assert result.provenance["crop_rectangles"] is None and result.provenance["crop"]["skipped"]


@pytest.mark.parametrize("algorithm", ["classical", "cellpose"])
def test_objects_inside_the_crop_are_identical_with_and_without_cropping(tmp_path, monkeypatch, algorithm):
    if algorithm == "cellpose":
        # A stand-in Cellpose that scales brightness like Cellpose (1st-99th percentile) unless told not to.
        source = """
import numpy as np, types
from skimage.measure import label
CALLS = []
MODEL_NAMES = ["cpsam_v2"]

class CellposeModel:
    def __init__(self, gpu=False, pretrained_model="cpsam_v2", model_type=None, diam_mean=None, device=None, nchan=None, use_bfloat16=True):
        self.device = types.SimpleNamespace(type="cpu")

    def eval(self, x, normalize=True, diameter=None, do_3D=False, flow_threshold=0.4, cellprob_threshold=0.0, min_size=15, **kwargs):
        data = np.asarray(x, dtype=np.float32)
        CALLS.append(dict(normalize=normalize, shape=data.shape))
        if normalize:
            low, high = np.percentile(data, [1, 99])
            data = (data - low) / (high - low)
        return label(data > 0.5).astype(np.int32), None, None
"""
        models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", source)
        monkeypatch.setattr(segmentation, "_MODELS", {})
    image = _image()
    full = process_image(image, _recipe(algorithm), pixel_size_x=PIXEL, pixel_size_y=PIXEL, channel_axis=0)
    cropped = process_image(image, _recipe(algorithm, CROP), pixel_size_x=PIXEL, pixel_size_y=PIXEL, channel_axis=0)
    rectangles = cropped.provenance["crop_rectangles"]
    assert rectangles and full.provenance["crop_rectangles"] is None
    if algorithm == "cellpose":
        calls = [call["normalize"] for call in models.CALLS]
        assert calls[0] is True and calls[1:] and not any(calls[1:])  # every crop was scaled like the whole image
    edge = set(cropped.objects.loc[cropped.objects["at_crop_edge"], "object_id"])

    def inside(labels):
        def keep(value):
            rows, cols = np.nonzero(labels == value)
            return any(
                rows.min() > y0 and rows.max() < y1 - 1 and cols.min() > x0 and cols.max() < x1 - 1 for y0, y1, x0, x1 in rectangles
            )
        return keep

    expected = _objects_by_pixels(full.labels, inside(full.labels))
    assert expected and _objects_by_pixels(cropped.labels, lambda value: value not in edge) == expected
    covered = np.zeros(full.labels.shape, bool)
    for y0, y1, x0, x1 in rectangles:
        covered[y0:y1, x0:x1] = True
    assert not np.any(cropped.labels[~covered])  # nothing outside the rectangles
    # Positions are in full-image coordinates.
    kept = cropped.objects.loc[~cropped.objects["at_crop_edge"]]
    assert set(map(tuple, full.objects[["centroid_x", "centroid_y"]].round(6).values.tolist())) >= set(
        map(tuple, kept[["centroid_x", "centroid_y"]].round(6).values.tolist())
    )


def test_objects_cut_by_a_crop_edge_are_flagged():
    from cellquant.image import load_image
    from cellquant.pipeline import assemble_result, segment_channel
    from cellquant.quantify import measure_objects

    loaded = load_image(_image(), pixel_size_x=PIXEL, pixel_size_y=PIXEL, channel_axis=0)
    recipe = load_recipe(_recipe("classical", CROP))
    details = {"crop_rectangles_given": [(0, 50, 0, 50)]}  # the nucleus at (44, 44) reaches row and column 50
    labels = segment_channel(loaded, recipe, details, record_timing=False)
    assert details["crop_rectangles"] == [[0, 50, 0, 50]] and len(details["crop_edge_objects"]) == 3
    assert not np.any(labels[50:, :]) and not np.any(labels[:, 50:])
    measured, warnings = measure_objects(loaded.data, labels, recipe.measurements, pixel_size=PIXEL)
    result = assemble_result(
        loaded=loaded, recipe=recipe, automated_labels=labels, labels=labels, measured=measured,
        measure_warnings=warnings, segmentation_details=details,
    )
    assert int(result.objects["at_crop_edge"].sum()) == 3 and result.provenance["crop_rectangles"] == [[0, 50, 0, 50]]
    assert any("3 objects touch a crop edge" in warning for warning in result.qc.warnings)


def test_requiring_a_numerator_only_channel_is_warned():
    recipe = load_recipe(_recipe("classical", CROP))
    assert crop_channels(recipe) == [1]  # the denominator's channel (the reporter)
    assert crop_channel_warnings(recipe) == []
    both = load_recipe(_recipe("classical", {**CROP, "channels": [1, 2]}))
    warnings = crop_channel_warnings(both, {1: "Red", 2: "OTX2"})
    assert len(warnings) == 1 and "OTX2 is only in the numerator" in warnings[0] and "inflates" in warnings[0]


def test_the_fingerprint_and_cache_key_follow_the_crop(tmp_path):
    from cellquant.controller import AnalysisController

    plain = load_recipe(_recipe()).content_hash()
    assert load_recipe(_recipe(crop={**CROP, "enabled": False})).content_hash() == plain  # off: unchanged
    on = load_recipe(_recipe(crop=CROP)).content_hash()
    assert on != plain and load_recipe(_recipe(crop={**CROP, "margin_um": 25.0})).content_hash() != on
    path = tmp_path / "retina.tif"
    tifffile.imwrite(path, _image(), photometric="minisblack")
    controller = AnalysisController.create(tmp_path / "experiment", "Crop")
    controller.add_image_paths([path])
    image_id = controller.experiment.images[0].image_id
    controller.set_pixel_size(image_id, PIXEL, PIXEL)
    controller.set_recipe(_recipe("classical", CROP))
    cropped = controller.run_image(image_id)
    key = cropped.provenance["segmentation_key"]
    assert cropped.provenance["crop_rectangles"]
    controller.set_full_image([image_id], True)  # this image: the full frame
    full = controller.run_image(image_id)
    assert full.provenance["crop_rectangles"] is None and full.provenance["segmentation_key"] != key
    assert full.qc.n_objects > cropped.qc.n_objects
    controller.set_full_image([image_id], False)
    assert controller.run_image(image_id).provenance["segmentation_key"] == key


# --- real classic Cellpose (skipped where it is not installed) -------------------------------------------


def _real_cellpose3():
    import importlib
    import sys

    from cellquant import engines

    for name in [name for name in sys.modules if name == "cellpose" or name.startswith("cellpose.")]:
        del sys.modules[name]  # stand-ins from other tests
    importlib.invalidate_caches()
    engines.cellpose_engine.cache_clear()
    if engines.cellpose_engine().key != "cellpose3":
        pytest.skip("needs classic Cellpose (cellpose3) installed")


def _dense_nuclei(seed: int = 3, size: int = 512) -> np.ndarray:
    rng = np.random.default_rng(seed)
    image = rng.normal(100, 8, (size, size))
    yy, xx = np.mgrid[:size, :size]
    for cy in range(12, size - 6, 18):
        for cx in range(12, size - 6, 18):
            cy2, cx2 = cy + rng.integers(-3, 4), cx + rng.integers(-3, 4)
            distance = (yy - cy2) ** 2 + (xx - cx2) ** 2
            inside = distance <= 36
            image[inside] += 900 * np.exp(-distance[inside] / 80)
    return np.clip(image, 0, 65535).astype(np.uint16)


def _iou_agreement(full: np.ndarray, cropped: np.ndarray, box, margin: int) -> tuple[int, int, int]:
    """Objects of the full run lying at least ``margin`` px inside the crop's inner edges: how many,
    how many have a cropped object with IoU >= 0.9, and how many are pixel-identical."""

    y0, y1, x0, x1 = box
    height, width = full.shape
    total = good = identical = 0
    for value in np.unique(full):
        if value == 0:
            continue
        rows, cols = np.nonzero(full == value)
        if not (
            (rows.min() >= y0 + (margin if y0 else 0)) and (rows.max() < y1 - (margin if y1 < height else 0))
            and (cols.min() >= x0 + (margin if x0 else 0)) and (cols.max() < x1 - (margin if x1 < width else 0))
        ):
            continue
        total += 1
        found = cropped[rows, cols]
        found = found[found > 0]
        if not found.size:
            continue
        best = np.bincount(found).argmax()
        other = cropped == best
        intersection = int(np.count_nonzero(other[rows, cols]))
        iou = intersection / (rows.size + int(other.sum()) - intersection)
        good += iou >= 0.9
        identical += iou == 1.0
    return total, good, identical


def test_real_classic_cellpose_crops_agree_with_the_full_image_at_object_level():
    """Measured, not hidden behind a stand-in: resizing by the diameter moves edges by a pixel here and
    there, so masks inside a crop are not pixel-identical, but objects one diameter inside the crop edge
    must match the full-image objects at IoU >= 0.9 (>= 99% of them)."""

    _real_cellpose3()
    from cellquant.segmentation import segment_objects, segment_regions

    image = _dense_nuclei()
    parameters = {"engine": "cellpose3", "model": "nuclei", "diameter_px": 12}
    full = segment_objects(image, "cellpose", parameters)
    details: dict = {}
    box = (0, 263, 0, 263)
    cropped = segment_regions(image, "cellpose", parameters, [box], details=details)
    total, good, identical = _iou_agreement(full, cropped, box, margin=12)
    print(f"objects >= 1 diameter inside the crop: {total}; IoU >= 0.9: {good}; pixel-identical: {identical}")
    assert total > 100
    assert good >= 0.99 * total
    assert {"normalize", "network", "masks_and_rest"} <= set(details["timings"])


def test_real_classic_cellpose_automatic_diameter_is_unchanged_by_timing_the_size_model():
    """CellQuant runs classic Cellpose's size model itself (to time it); the masks must equal Cellpose's own."""

    _real_cellpose3()
    from cellpose import models

    from cellquant.segmentation import segment_objects

    image = _dense_nuclei(size=256)
    details: dict = {}
    ours = segment_objects(image, "cellpose", {"engine": "cellpose3", "model": "nuclei"}, details=details)
    theirs = models.Cellpose(gpu=False, model_type="nuclei").eval(image, diameter=None, channels=[0, 0])[0]
    assert np.array_equal(ours, np.asarray(theirs, dtype=np.int32))
    assert details["timings"]["size_estimate"] > 0 and details["engine"]["estimated_diameter_px"] > 0
