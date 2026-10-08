"""Recording drawn edits measures again only the objects the drawing can change."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from cellquant.edits import EditOperation, operations_from_diff
from cellquant.image import LoadedImage
from cellquant.pipeline import measurement_values, process_image
import cellquant.pipeline as pipeline


def _recipe(background: dict | None = None) -> dict:
    ring = background or {"type": "local_ring", "inner_px": 1, "outer_px": 4}
    return {
        "recipe_name": "edits",
        "object_set": {"name": "Objects", "segmentation_channel": 0, "algorithm": "classical", "parameters": {}},
        "measurements": [
            {"id": "a_mean", "channel": 1, "region": {"type": "object"}, "statistic": "mean", "background": ring},
            {"id": "a_grown", "channel": 1, "region": {"type": "expanded_object", "distance_px": 3}, "statistic": "integrated"},
            {"id": "a_ring", "channel": 1, "region": {"type": "ring", "inner_px": 1, "outer_px": 5}, "statistic": "median"},
            {"id": "a_core", "channel": 1, "region": {"type": "eroded_object", "distance_px": 1}, "statistic": "max"},
            {"id": "a_x", "channel": 1, "region": {"type": "object"}, "statistic": "centroid_x"},
            {"id": "a_pct", "channel": 1, "region": {"type": "object"}, "statistic": "percent_above", "pixel_level": 500, "background": ring},
        ],
        "classifications": [{"id": "pos", "name": "A", "measurement": "a_mean", "threshold": 300}],
        "reports": [{"numerator": "A", "denominator": "all_objects"}],
    }


def _scene(volume: bool = False):
    rng = np.random.default_rng(3)
    shape = (3, 120, 140) if volume else (120, 140)
    labels = np.zeros(shape, dtype=np.int32)
    number = 0
    for top in range(6, 110, 16):
        for left in range(6, 130, 17):
            number += 1
            labels[..., top : top + 9, left : left + 10] = number
    data = rng.integers(0, 1000, size=(2, *shape)).astype(np.float32)
    axes = "CZYX" if volume else "CYX"
    image = LoadedImage(
        data=data,
        pixel_size_x=0.5,
        pixel_size_y=0.5,
        pixel_size_z=2.0 if volume else None,
        axes=axes,
        z_planes=3 if volume else 1,
        z_mode="full_3d" if volume else "none",
    )
    return image, labels


def _edits(labels: np.ndarray) -> list[EditOperation]:
    drawn = labels.copy()
    drawn[..., 60:70, 60:66] = int(labels.max()) + 1  # a new object drawn between others
    drawn[..., 6:10, 6:16] = 0  # part of object 1 erased
    drawn[drawn == 20] = 0  # object 20 erased completely
    drawn[..., 30:33, 40:50] = 9  # object 9 painted larger
    return operations_from_diff(labels, drawn, "img")


@pytest.mark.parametrize("volume", [False, True])
def test_recorded_edits_match_a_full_measurement(volume, monkeypatch):
    image, labels = _scene(volume)
    recipe = _recipe()
    if volume:
        recipe["z_stack"] = "full_3d"
    before = process_image(image, recipe, image_id="img", automated_labels=labels)
    edits = _edits(labels)
    calls = []
    real = pipeline.measure_objects

    def counting(image_part, labels_part, *args, **kwargs):
        calls.append(labels_part.shape)
        return real(image_part, labels_part, *args, **kwargs)

    full = process_image(image, recipe, image_id="img", automated_labels=labels, manual_edits=edits)
    monkeypatch.setattr(pipeline, "measure_objects", counting)
    partial = process_image(
        image, recipe, image_id="img", automated_labels=labels, manual_edits=edits, previous=before
    )
    # Only windows around the drawn objects were measured, never the whole image.
    assert calls and all(shape != labels.shape for shape in calls)
    pd.testing.assert_frame_equal(measurement_values(partial, recipe), measurement_values(full, recipe))
    assert partial.objects["excluded"].tolist() == full.objects["excluded"].tolist()
    assert partial.qc.n_objects == full.qc.n_objects
    assert np.array_equal(partial.labels, full.labels)


def test_global_background_measures_everything(monkeypatch):
    image, labels = _scene()
    recipe = _recipe({"type": "global"})
    before = process_image(image, recipe, image_id="img", automated_labels=labels)
    calls = []
    real = pipeline.measure_objects
    monkeypatch.setattr(pipeline, "measure_objects", lambda i, l, *a, **k: calls.append(l.shape) or real(i, l, *a, **k))
    process_image(image, recipe, image_id="img", automated_labels=labels, manual_edits=_edits(labels), previous=before)
    # Every object's background moves when objects change: the whole image is measured.
    assert labels.shape in calls
