"""Positive by the percent of a cell's pixels at or above a pixel level (CellQuant v1's
``positive_fraction``): recipe fields, measurement in 2D and 3D, background, the
"at least" boundary, and re-measuring without segmenting again."""

from __future__ import annotations

import numpy as np
import pytest

from cellquant.errors import RecipeValidationError
from cellquant.quantify import classification_counts, classify_objects, measure_objects
from cellquant.quicksetup import describe_rule, marker_recipe, uses_pixel_level
from cellquant.recipe import ClassificationSpec, MeasurementSpec, Recipe, load_recipe, save_recipe


def _percent(level, *, high=None, background=None, region=None):
    return MeasurementSpec(
        id="p",
        channel=0,
        region=region or {"type": "object"},
        statistic="percent_above",
        pixel_level=level,
        pixel_level_high=high,
        background=background or {"type": "none"},
    )


def _two_objects():
    labels = np.zeros((6, 6), dtype=np.int32)
    labels[0:2, 0:5] = 1  # 10 pixels
    labels[3:6, 3:6] = 2  # 9 pixels
    image = np.zeros((1, 6, 6), dtype=np.float64)
    image[0, 0, 0:3] = 10  # object 1: 3 of 10 pixels
    image[0, 4, 4] = 10  # object 2: 1 of 9 pixels
    return image, labels


def test_percent_of_pixels_at_or_above_the_level():
    image, labels = _two_objects()
    table, warnings = measure_objects(image, labels, [_percent(10)], pixel_size=None)
    assert warnings == []
    assert table["p"].tolist() == [30.0, 100.0 / 9.0]
    # "At or above": a level just over the pixel value counts nothing.
    table, _ = measure_objects(image, labels, [_percent(10.000001)], pixel_size=None)
    assert table["p"].tolist() == [0.0, 0.0]


def test_at_least_includes_the_boundary_and_above_does_not():
    image, labels = _two_objects()
    table, _ = measure_objects(image, labels, [_percent(10)], pixel_size=None)
    at_least = ClassificationSpec(id="c", name="c", measurement="p", threshold=30, comparison="at_least")
    above = ClassificationSpec(id="c", name="c", measurement="p", threshold=30)
    assert classify_objects(table, [at_least])["c"].tolist() == [True, False]
    assert classify_objects(table, [above])["c"].tolist() == [False, False]
    # 1 of 9 pixels is exactly the typed value 100/9 as well.
    exact = ClassificationSpec(id="c", name="c", measurement="p", threshold=100.0 / 9.0, comparison="at_least")
    assert classify_objects(table, [exact])["c"].tolist() == [True, True]
    counts = classification_counts(table["p"].to_numpy(), 30, "at_least")
    assert (counts["positive"], counts["negative"]) == (1, 1)
    assert classification_counts(table["p"].to_numpy(), 30)["positive"] == 0


@pytest.mark.parametrize("pixels,passing", [(7, 3), (100, 7), (3, 1), (49, 7)])
def test_boundary_is_exact_for_decimal_percents(pixels, passing):
    labels = np.zeros((1, pixels), dtype=np.int32)
    labels[:] = 1
    image = np.zeros((1, 1, pixels))
    image[0, 0, :passing] = 5
    table, _ = measure_objects(image, labels, [_percent(5)], pixel_size=None)
    value = float(table["p"].iloc[0])
    typed = float(f"{100 * passing / pixels!r}")
    spec = ClassificationSpec(id="c", name="c", measurement="p", threshold=typed, comparison="at_least")
    assert bool(classify_objects(table, [spec])["c"].iloc[0])
    one_less = ClassificationSpec(id="c", name="c", measurement="p", threshold=np.nextafter(value, np.inf), comparison="at_least")
    assert not bool(classify_objects(table, [one_less])["c"].iloc[0])


def test_upper_level_leaves_out_saturated_pixels():
    image, labels = _two_objects()
    image[0, 1, 0] = 4000  # a saturated pixel in object 1
    table, _ = measure_objects(image, labels, [_percent(10)], pixel_size=None)
    assert table["p"].iloc[0] == 40.0
    table, _ = measure_objects(image, labels, [_percent(10, high=100)], pixel_size=None)
    assert table["p"].iloc[0] == 30.0


def test_global_and_local_ring_background():
    image, labels = _two_objects()
    image = image + 2.0  # every pixel, including background, is 2 brighter
    table, _ = measure_objects(image, labels, [_percent(10)], pixel_size=None)
    assert table["p"].tolist() == [30.0, 100.0 / 9.0]  # 12 >= 10
    table, _ = measure_objects(image, labels, [_percent(10.5)], pixel_size=None)
    assert table["p"].tolist() == [30.0, 100.0 / 9.0]
    table, _ = measure_objects(image, labels, [_percent(10.5, background={"type": "global"})], pixel_size=None)
    assert table["p"].tolist() == [0.0, 0.0]  # 12 - 2 = 10 < 10.5
    table, _ = measure_objects(image, labels, [_percent(10, background={"type": "global", "value": 2})], pixel_size=None)
    assert table["p"].tolist() == [30.0, 100.0 / 9.0]
    ring = {"type": "local_ring", "inner_px": 0, "outer_px": 1}
    table, _ = measure_objects(image, labels, [_percent(10, background=ring)], pixel_size=None)
    assert table["p"].tolist() == [30.0, 100.0 / 9.0]


def test_objects_with_unusable_pixels_are_unmeasured():
    image, labels = _two_objects()
    image[0, 5, 5] = np.nan
    table, _ = measure_objects(image, labels, [_percent(10)], pixel_size=None)
    assert table["p"].iloc[0] == 30.0 and np.isnan(table["p"].iloc[1])
    spec = ClassificationSpec(id="c", name="c", measurement="p", threshold=10, comparison="at_least")
    classified = classify_objects(table, [spec])
    assert classified["unmeasured"].tolist() == [False, True]
    # An eroded region that leaves an object no pixels: blank, with a warning.
    table, warnings = measure_objects(image, labels, [_percent(10, region={"type": "eroded_object", "distance_px": 3})], pixel_size=None)
    assert table["p"].isna().all() and any("no pixels" in item for item in warnings)


def test_three_dimensional_objects_count_every_voxel():
    labels = np.zeros((3, 4, 4), dtype=np.int32)
    labels[:, 1:3, 1:3] = 7  # 12 voxels
    image = np.zeros((1, 3, 4, 4))
    image[0, 0, 1:3, 1:3] = 50  # the first slice only: 4 of 12
    table, _ = measure_objects(image, labels, [_percent(50)], pixel_size=0.5, pixel_size_z=1.0)
    assert table["object_id"].tolist() == [7]
    assert table["p"].iloc[0] == pytest.approx(100 / 3)


def test_recipe_fields_are_checked_and_saved(tmp_path):
    with pytest.raises(ValueError, match="pixel level"):
        MeasurementSpec(id="p", channel=0, region={"type": "object"}, statistic="percent_above")
    with pytest.raises(ValueError, match="only to 'percent_above'"):
        MeasurementSpec(id="m", channel=0, region={"type": "object"}, statistic="mean", pixel_level=3)
    with pytest.raises(ValueError, match="at least pixel_level"):
        _percent(10, high=5)
    recipe = load_recipe(
        {
            "object_set": {"segmentation_channel": 0, "algorithm": "classical"},
            "measurements": [_percent(1200, high=4000).model_dump(mode="json")],
            "classifications": [{"id": "c", "name": "OTX2", "measurement": "p", "threshold": 30, "comparison": "at_least"}],
        }
    )
    save_recipe(recipe, tmp_path / "r.yaml")
    again = load_recipe(tmp_path / "r.yaml")
    assert again.measurements[0].pixel_level == 1200 and again.measurements[0].pixel_level_high == 4000
    assert again.classifications[0].comparison == "at_least"
    assert again.content_hash() == recipe.content_hash()
    changed = again.model_copy(deep=True)
    changed.measurements[0].pixel_level = 1300
    assert changed.content_hash() != recipe.content_hash()
    assert describe_rule(recipe, recipe.classifications[0]) == "at least 30% of pixels 1200–4000"


def test_existing_recipes_keep_their_settings_fingerprint():
    """Settings saved before this option existed hash exactly as before (no new keys appear)."""

    data = {
        "object_set": {"segmentation_channel": 0, "algorithm": "classical"},
        "measurements": [{"id": "m", "channel": 1, "region": {"type": "object"}, "statistic": "mean"}],
        "classifications": [{"id": "c", "name": "A", "measurement": "m", "threshold": 12.5}],
    }
    recipe = Recipe.model_validate(data)
    dumped = recipe.scientific_dict()
    assert "pixel_level" not in dumped["measurements"][0] and "pixel_level_high" not in dumped["measurements"][0]
    assert "comparison" not in dumped["classifications"][0]
    # Fingerprint recorded with the release before this option (2026-09-29).
    assert recipe.content_hash() == "ce7a9fcf9bfb7bf38d075b90a440b773d90f98fc277face2ef429aa4d93d89e1"


def test_quick_setup_percent_rule():
    base = {"object_set": {"segmentation_channel": 0, "algorithm": "classical"}}
    data = marker_recipe(base, [(1, "OTX2"), (2, "GFP")], rule="percent_above", min_percent=30)
    recipe = load_recipe(data)
    assert [m.id for m in recipe.measurements] == ["otx2_mean", "otx2_pct", "gfp_mean", "gfp_pct"]
    assert all(uses_pixel_level(recipe, item) for item in recipe.classifications)
    assert [(c.measurement, c.threshold, c.comparison) for c in recipe.classifications] == [
        ("otx2_pct", 30.0, "at_least"),
        ("gfp_pct", 30.0, "at_least"),
    ]
    mean = load_recipe(marker_recipe(base, [(1, "OTX2")]))
    assert [m.id for m in mean.measurements] == ["otx2_mean"] and not uses_pixel_level(mean, mean.classifications[0])
    assert describe_rule(mean, mean.classifications[0]) == "mean > 0"


def test_changing_the_pixel_level_measures_again_without_segmenting(tmp_path, monkeypatch):
    from cellquant import pipeline
    from cellquant.practice import create_practice_experiment

    controller = create_practice_experiment(tmp_path)
    base = controller.recipe.model_dump(mode="json")
    names = [(c.channel_index, c.channel_name) for c in controller.experiment.channels if c.channel_index != controller.recipe.object_set.segmentation_channel]
    controller.set_recipe(marker_recipe(base, names, rule="percent_above", min_percent=50))
    image_id = controller.experiment.images[0].image_id
    first = controller.run_image(image_id)
    measurement = controller.recipe.measurements[1].id
    assert first.objects[measurement].eq(100.0).all()  # level 0: every pixel passes

    def no_segmentation(*args, **kwargs):
        raise AssertionError("segmentation must not run again")

    monkeypatch.setattr(pipeline, "segment_channel", no_segmentation)
    controller.delete_object(image_id, int(first.objects["object_id"].iloc[0]))
    after = controller.update_pixel_levels(image_id, {measurement: (1e9, None)})
    assert after is not None and after.objects[measurement].fillna(0).eq(0.0).all()
    assert controller.recipe.measurements[1].pixel_level == 1e9
    assert np.array_equal(after.automated_labels, first.automated_labels)
    assert bool(after.objects["excluded"].iloc[0])  # the edit is kept
    with pytest.raises(Exception, match="does not use a pixel level"):
        controller.update_pixel_levels(image_id, {controller.recipe.measurements[0].id: (5, None)})


def test_unknown_statistic_keys_are_rejected():
    with pytest.raises(RecipeValidationError):
        load_recipe(
            {
                "object_set": {"segmentation_channel": 0, "algorithm": "classical"},
                "classifications": [],
                "measurements": [{"id": "m", "channel": 0, "region": {"type": "object"}, "statistic": "percent_above", "pixel_levels": 3}],
            }
        )
