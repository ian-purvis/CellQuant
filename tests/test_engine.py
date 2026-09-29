"""Synthetic-image checks for the analysis engine.

The ten-object image is the acceptance fixture from the architecture notes:
10 objects, 5 A+, 3 B+, 2 A+B+.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import tifffile

from cellquant import (
    CalibrationError,
    ChannelMismatchError,
    RecipeValidationError,
    SegmentationError,
    export_image_result,
    load_image,
    load_recipe,
    process_image,
    save_recipe,
)
from cellquant.quantify import classification_counts, classify_objects
from cellquant.regions import erode_labels, expand_labels, ring_labels
from cellquant.segmentation import register_segmentation_backend, segment_objects


def _squares() -> tuple[np.ndarray, list[tuple[int, int]]]:
    image = np.zeros((4, 180, 220), dtype=np.uint16)
    origins: list[tuple[int, int]] = []
    for row in range(2):
        for col in range(5):
            origins.append((20 + row * 50, 15 + col * 40))
    a_positive = {0, 1, 2, 3, 4}
    b_positive = {0, 1, 5}
    c_positive = {0, 1}
    for index, (y, x) in enumerate(origins):
        image[0, y : y + 10, x : x + 10] = 1000
        image[1, y : y + 10, x : x + 10] = 800 if index in a_positive else 20
        image[2, y : y + 10, x : x + 10] = 900 if index in b_positive else 30
        image[3, y : y + 10, x : x + 10] = 700 if index in c_positive else 40
    return image, origins


def _recipe() -> dict:
    return {
        "recipe_version": 1,
        "recipe_name": "synthetic",
        "object_set": {
            "name": "Objects",
            "segmentation_channel": 0,
            "algorithm": "classical",
            "parameters": {
                "sigma": 0,
                "threshold_method": "manual",
                "threshold": 100,
                "fill_holes": True,
                "use_watershed": False,
            },
        },
        "measurements": [
            {
                "id": "marker_a_mean",
                "channel": 1,
                "region": {"type": "object"},
                "statistic": "mean",
            },
            {
                "id": "marker_b_mean",
                "channel": 2,
                "region": {"type": "object"},
                "statistic": "mean",
            },
            {
                "id": "marker_c_mean",
                "channel": 3,
                "region": {"type": "object"},
                "statistic": "median",
            },
        ],
        "classifications": [
            {
                "id": "class_a",
                "name": "A",
                "measurement": "marker_a_mean",
                "method": "threshold",
                "threshold": 200,
            },
            {
                "id": "class_b",
                "name": "B",
                "measurement": "marker_b_mean",
                "threshold": 200,
            },
            {
                "id": "class_c",
                "name": "C",
                "measurement": "marker_c_mean",
                "threshold": 200,
            },
        ],
        "reports": [
            {"numerator": "A", "denominator": "all_objects"},
            {"numerator": "A AND B", "denominator": "A"},
            {"numerator": "A AND B AND C", "denominator": "A"},
        ],
    }


def test_synthetic_population_is_exact():
    image, origins = _squares()
    result = process_image(image, _recipe(), sample_name="sample_001", image_id="img")

    assert result.qc.status == "success"
    assert result.qc.n_objects == 10
    assert result.spatial_unit == "px"
    objects = result.objects
    assert list(objects["object_id"]) == list(range(1, 11))
    assert objects["area"].tolist() == [100] * 10
    for object_id, (y, x) in zip(objects["object_id"], origins, strict=True):
        row = objects.loc[objects["object_id"] == object_id].iloc[0]
        assert row["centroid_x"] == pytest.approx(x + 4.5)
        assert row["centroid_y"] == pytest.approx(y + 4.5)

    assert int(objects["class_a"].sum()) == 5
    assert int(objects["class_b"].sum()) == 3
    assert int((objects["class_a"] & objects["class_b"]).sum()) == 2
    assert int((objects["class_a"] & objects["class_b"] & objects["class_c"]).sum()) == 2
    assert objects.loc[objects["object_id"] == 1, "phenotype"].item() == "A+|B+|C+"
    assert objects.loc[objects["object_id"] == 3, "phenotype"].item() == "A+|B-|C-"
    assert objects.loc[objects["object_id"] == 6, "phenotype"].item() == "A-|B+|C-"
    assert objects.loc[objects["object_id"] == 10, "phenotype"].item() == "A-|B-|C-"
    assert objects.loc[objects["object_id"] == 1, "marker_a_mean"].item() == 800
    assert objects.loc[objects["object_id"] == 10, "marker_b_mean"].item() == 30

    by_report = {row.numerator: row for row in result.reports.itertuples()}
    assert by_report["A"].count == 5
    assert by_report["A"].percent == pytest.approx(50)
    assert by_report["A AND B"].count == 2
    assert by_report["A AND B"].denominator_count == 5
    assert by_report["A AND B"].percent == pytest.approx(40)
    assert by_report["A AND B AND C"].count == 2
    assert by_report["A AND B AND C"].percent == pytest.approx(40)

    exclusive = result.phenotype_counts.set_index("phenotype")
    assert exclusive.loc["A+|B+|C+", "count"] == 2
    assert exclusive.loc["A+|B-|C-", "count"] == 3
    assert exclusive.loc["A-|B+|C-", "count"] == 1
    assert exclusive.loc["A-|B-|C-", "count"] == 4
    assert int(exclusive["count"].sum()) == 10

    combinations = result.combination_counts.set_index("phenotype")
    assert combinations.loc["A+", "count"] == 5
    assert combinations.loc["A+|B+", "count"] == 2
    assert combinations.loc["A+|B+|C+", "count"] == 2


def test_repeat_run_reproduces_the_object_table():
    image, _origins = _squares()
    recipe = load_recipe(_recipe())
    first = process_image(image, recipe, sample_name="sample_001")
    second = process_image(image, recipe, sample_name="sample_001")
    assert first.provenance["recipe_sha256"] == second.provenance["recipe_sha256"]
    assert first.provenance["recipe_sha256"] == recipe.content_hash()
    pd.testing.assert_frame_equal(first.objects, second.objects)
    assert np.array_equal(first.labels, second.labels)


def test_threshold_change_does_not_alter_measurements():
    image, _origins = _squares()
    result = process_image(image, _recipe())
    measurements = result.objects[
        ["object_id", "marker_a_mean", "marker_b_mean", "marker_c_mean"]
    ].copy()
    low = classify_objects(
        measurements,
        load_recipe(_recipe()).classifications,
    )
    stricter = load_recipe(_recipe()).classifications
    stricter[0] = stricter[0].model_copy(update={"threshold": 1000})
    high = classify_objects(measurements, stricter)
    pd.testing.assert_series_equal(low["marker_a_mean"], high["marker_a_mean"])
    assert int(low["class_a"].sum()) == 5
    assert int(high["class_a"].sum()) == 0
    assert np.array_equal(result.automated_labels, result.labels)


def test_threshold_equality_is_negative():
    counts = classification_counts(np.array([10, 20, 20, 30], dtype=float), 20)
    assert counts["positive"] == 1
    assert counts["negative"] == 3
    assert counts["percent_positive"] == pytest.approx(25)


def test_regions_have_exact_geometry():
    labels = np.zeros((21, 21), dtype=np.int32)
    labels[8:13, 8:13] = 1
    eroded = erode_labels(labels, 1)
    assert int((eroded == 1).sum()) == 9
    assert eroded[10, 10] == 1
    assert eroded[8, 8] == 0

    grown = expand_labels(labels, 1)
    assert np.array_equal(grown[8:13, 8:13], labels[8:13, 8:13])
    assert grown[7, 10] == 1
    assert grown[6, 10] == 0

    labels[8:13, 16:21] = 2
    wide = expand_labels(labels, 5)
    overlap = (wide == 1) & (wide == 2)
    assert not overlap.any()
    assert wide[10, 14] in (1, 2)
    assert wide[10, 11] == 1
    assert wide[10, 18] == 2

    single = np.zeros((21, 21), dtype=np.int32)
    single[10, 10] = 1
    ring = ring_labels(single, 0, 1)
    assert ring[10, 10] == 0
    assert ring[10, 11] == 1
    assert ring[9, 11] == 0
    assert int((ring == 1).sum()) == 4


def test_background_correction():
    image = np.zeros((2, 40, 40), dtype=np.float64)
    image[0, 10:20, 10:20] = 1000
    image[1, :, :] = 10
    image[1, 10:20, 10:20] = 100
    recipe = {
        "object_set": {
            "segmentation_channel": 0,
            "algorithm": "classical",
            "parameters": {"threshold_method": "manual", "threshold": 100},
        },
        "measurements": [
            {
                "id": "raw_mean",
                "channel": 1,
                "region": {"type": "object"},
                "statistic": "mean",
            },
            {
                "id": "global_mean",
                "channel": 1,
                "region": {"type": "object"},
                "statistic": "mean",
                "background": {"type": "global"},
            },
            {
                "id": "ring_mean",
                "channel": 1,
                "region": {"type": "object"},
                "statistic": "mean",
                "background": {
                    "type": "local_ring",
                    "inner_px": 1,
                    "outer_px": 3,
                },
            },
            {
                "id": "global_integrated",
                "channel": 1,
                "region": {"type": "object"},
                "statistic": "integrated",
                "background": {"type": "global", "value": 10},
            },
        ],
    }
    result = process_image(image, recipe)
    row = result.objects.iloc[0]
    assert row["raw_mean"] == pytest.approx(100)
    assert row["global_mean"] == pytest.approx(90)
    assert row["ring_mean"] == pytest.approx(90)
    assert row["global_integrated"] == pytest.approx(9000)


def test_physical_units_scale_area_and_centroid():
    image, origins = _squares()
    result = process_image(image, _recipe(), pixel_size_x=0.5, pixel_size_y=0.5)
    y, x = origins[0]
    row = result.objects.iloc[0]
    assert result.spatial_unit == "um"
    assert row["area"] == pytest.approx(25)
    assert row["centroid_x"] == pytest.approx((x + 4.5) * 0.5)
    assert row["centroid_y"] == pytest.approx((y + 4.5) * 0.5)
    assert result.qc.median_area == pytest.approx(25)


def test_micrometre_distance_without_pixel_size_is_rejected():
    image, _origins = _squares()
    recipe = _recipe()
    recipe["measurements"][0]["region"] = {
        "type": "expanded_object",
        "distance_um": 2,
    }
    with pytest.raises(CalibrationError, match="no pixel size"):
        process_image(image, recipe)


def test_channel_mismatch_message():
    image, _origins = _squares()
    recipe = _recipe()
    recipe["measurements"][0]["channel"] = 9
    with pytest.raises(ChannelMismatchError, match="has 4 channels"):
        process_image(image, recipe)


def test_empty_segmentation_is_a_warning():
    image = np.zeros((2, 30, 30), dtype=np.uint16)
    recipe = {
        "object_set": {
            "segmentation_channel": 0,
            "algorithm": "classical",
            "parameters": {"threshold_method": "manual", "threshold": 1},
        }
    }
    result = process_image(image, recipe)
    assert result.qc.status == "warning"
    assert result.qc.n_objects == 0
    assert result.objects.empty
    assert "Segmentation returned no objects." in result.qc.warnings


def test_size_filter_keeps_surviving_ids():
    channel = np.zeros((40, 40), dtype=np.uint16)
    channel[2:4, 2:4] = 100
    channel[10:20, 10:20] = 100
    labels = segment_objects(
        channel,
        "classical",
        {"threshold_method": "manual", "threshold": 10, "min_area_px": 50},
    )
    assert sorted(np.unique(labels).tolist()) == [0, 2]


def test_watershed_splits_a_dumbbell():
    channel = np.zeros((60, 80), dtype=np.float64)
    yy, xx = np.ogrid[:60, :80]
    left = (yy - 30) ** 2 + (xx - 25) ** 2 <= 10**2
    right = (yy - 30) ** 2 + (xx - 52) ** 2 <= 10**2
    bridge = (np.abs(yy - 30) <= 0) & (xx >= 25) & (xx <= 52)
    channel[left | right | bridge] = 100
    labels = segment_objects(
        channel,
        "classical",
        {
            "threshold_method": "manual",
            "threshold": 10,
            "use_watershed": True,
            "watershed_min_distance_px": 8,
        },
    )
    assert len(np.unique(labels)) - 1 == 2


def test_recipe_yaml_roundtrip(tmp_path: Path):
    path = tmp_path / "recipe.yaml"
    original = load_recipe(_recipe())
    save_recipe(original, path)
    loaded = load_recipe(path)
    assert loaded.content_hash() == original.content_hash()
    with pytest.raises(RecipeValidationError, match="invalid"):
        load_recipe({"recipe_name": "missing object set"})


def test_invalid_measurement_reference_is_rejected():
    recipe = _recipe()
    recipe["classifications"][0]["measurement"] = "missing"
    with pytest.raises(RecipeValidationError, match="unknown measurement"):
        load_recipe(recipe)


def test_registered_backend_is_used():
    class BoxBackend:
        def segment(self, image, parameters):
            labels = np.zeros(image.shape, dtype=np.int32)
            labels[4:9, 4:9] = 7
            return labels

    register_segmentation_backend("box-test", BoxBackend())
    try:
        image = np.zeros((1, 20, 20), dtype=np.uint16)
        result = process_image(
            image,
            {
                "object_set": {
                    "segmentation_channel": 0,
                    "algorithm": "box-test",
                    "parameters": {},
                }
            },
        )
    finally:
        from cellquant.segmentation import _BACKENDS

        _BACKENDS.pop("box-test", None)
    assert result.qc.n_objects == 1
    assert result.objects.iloc[0]["object_id"] == 7
    assert result.objects.iloc[0]["area"] == 25


def test_unknown_algorithm():
    with pytest.raises(SegmentationError, match="Unknown segmentation algorithm"):
        segment_objects(np.zeros((8, 8)), "not-a-method", {})


def test_tiff_roundtrip_reads_axes_and_pixel_size(tmp_path: Path):
    path = tmp_path / "sample.tif"
    data = np.arange(2 * 6 * 8, dtype=np.uint16).reshape(6, 8, 2)
    # 0.5 µm/pixel => 20000 pixels per centimetre.
    tifffile.imwrite(
        path,
        data,
        photometric="minisblack",
        metadata={"axes": "YXC"},
        resolution=(20000, 20000),
        resolutionunit="CENTIMETER",
    )
    loaded = load_image(path)
    assert loaded.data.shape == (2, 6, 8)
    assert loaded.pixel_size_x == pytest.approx(0.5)
    assert loaded.pixel_size_y == pytest.approx(0.5)
    assert loaded.channel_axis_source == "tiff_axes"
    assert np.array_equal(loaded.data[1], data[:, :, 1])


def test_export_writes_object_and_image_tables(tmp_path: Path):
    image, _origins = _squares()
    result = process_image(
        image,
        _recipe(),
        sample_name="sample_001",
        image_id="img1",
        experiment_id="exp",
        run_id="run",
        filename="sample_001.tif",
    )
    export_image_result(result, tmp_path / "out")
    objects = pd.read_csv(tmp_path / "out" / "objects.csv")
    summary = pd.read_csv(tmp_path / "out" / "image_summary.csv")
    assert list(objects.columns[:8]) == [
        "experiment_id",
        "run_id",
        "sample_name",
        "image_id",
        "filename",
        "object_set",
        "object_id",
        "centroid_x",
    ]
    assert "phenotype" in objects.columns
    assert len(objects) == 10
    assert summary.iloc[0]["total_objects"] == 10
    assert summary.iloc[0]["sample_name"] == "sample_001"
    assert (tmp_path / "out" / "provenance.json").is_file()
    labels = tifffile.imread(tmp_path / "out" / "labels.tif")
    assert labels.max() == 10
