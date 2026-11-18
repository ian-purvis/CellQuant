"""Markers called by overlap: an object is positive when enough of it lies inside an object another
analysis of the same image found. The cutoff markers are unchanged."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cellquant.controller import AnalysisController
from cellquant.errors import RecipeValidationError
from cellquant.pipeline import labels_digest, process_image
from cellquant.quantify import OVERLAP_NOT_MEASURED, measure_objects, overlap_sources
from cellquant.quicksetup import (
    add_overlap_marker,
    describe_rule,
    marker_recipe,
    merge_marker_recipe,
    overlap_markers,
    remove_overlap_marker,
    uses_overlap,
)
from cellquant.recipe import MeasurementSpec, load_recipe
from cellquant.synthetic_retina import write_retina_set

CLASSICAL = {"sigma": 1.0, "use_watershed": True, "watershed_min_distance_px": 4, "min_area_um2": 5}
OTHER = "recipe_other"


def _overlap_spec(source: str = OTHER, spec_id: str = "in_other") -> dict:
    return {"id": spec_id, "channel": 0, "region": {"type": "object"}, "statistic": "object_overlap", "objects_from": source}


def _recipe(*measurements: dict, cutoff: float = 50.0) -> dict:
    found = list(measurements) or [_overlap_spec()]
    return {
        "recipe_id": "recipe_this",
        "object_set": {"name": "Cells", "segmentation_channel": 0, "algorithm": "classical", "parameters": CLASSICAL},
        "measurements": found,
        "classifications": [
            {"id": "other_pos", "name": "Other objects", "measurement": found[0]["id"], "threshold": cutoff, "comparison": "at_least"}
        ],
        "reports": [{"numerator": "other_pos", "denominator": "all_objects"}],
    }


def _squares() -> tuple[np.ndarray, np.ndarray]:
    """Four 10 x 10 objects, and another analysis's objects covering 100, 50, 30 and 0 % of them."""

    labels = np.zeros((40, 60), dtype=np.int32)
    other = np.zeros_like(labels)
    for index, column in enumerate((2, 16, 30, 44)):
        labels[5:15, column : column + 10] = index + 1
    other[3:17, 0:14] = 7  # all of object 1
    other[5:15, 21:30] = 8  # the right half of object 2 (and beyond)
    other[5:8, 30:40] = 9  # the top 3 rows of object 3
    return labels, other


def test_the_value_is_the_percent_of_the_object_inside_the_other_objects():
    labels, other = _squares()
    image = np.zeros((1, *labels.shape), dtype=np.uint16)
    spec = MeasurementSpec.model_validate(_overlap_spec())
    table, warnings = measure_objects(image, labels, [spec], pixel_size=0.5, object_masks={OTHER: other > 0})
    assert table["in_other"].tolist() == [100.0, 50.0, 30.0, 0.0]
    assert warnings == []


def test_without_the_other_objects_the_marker_is_blank_and_says_why():
    labels, other = _squares()
    image = np.zeros((1, *labels.shape), dtype=np.uint16)
    spec = MeasurementSpec.model_validate(_overlap_spec())
    for masks in (None, {OTHER: None}, {OTHER: np.ones((3, 3), dtype=bool)}, {"someone_else": other > 0}):
        table, warnings = measure_objects(image, labels, [spec], pixel_size=None, object_masks=masks)
        assert table["in_other"].isna().all()
        assert warnings == [OVERLAP_NOT_MEASURED]


def test_in_3d_the_percent_counts_voxels():
    labels = np.zeros((4, 12, 12), dtype=np.int32)
    labels[0:4, 2:6, 2:6] = 1  # 4 slices deep
    other = np.zeros_like(labels)
    other[0:1, 0:12, 0:12] = 5  # only the first slice
    image = np.zeros((1, *labels.shape), dtype=np.uint16)
    spec = MeasurementSpec.model_validate(_overlap_spec())
    table, _warnings = measure_objects(image, labels, [spec], pixel_size=0.5, pixel_size_z=1.0, object_masks={OTHER: other > 0})
    assert table["in_other"].tolist() == [25.0]


def test_the_settings_need_the_other_analysis_and_never_compare_with_themselves():
    with pytest.raises(RecipeValidationError, match="objects_from"):
        load_recipe(_recipe({**_overlap_spec(), "objects_from": None}))
    with pytest.raises(RecipeValidationError, match="only to 'object_overlap'"):
        load_recipe(_recipe({"id": "m", "channel": 0, "region": {"type": "object"}, "statistic": "mean", "objects_from": OTHER}))
    with pytest.raises(RecipeValidationError, match="themselves"):
        load_recipe(_recipe(_overlap_spec("recipe_this")))
    with pytest.raises(RecipeValidationError, match="Background"):
        load_recipe(_recipe({**_overlap_spec(), "background": {"type": "global", "value": 3.0}}))
    recipe = load_recipe(_recipe())
    assert overlap_sources(recipe.measurements) == [OTHER]
    # Settings without such a marker are written, and hashed, as before.
    plain = load_recipe(marker_recipe(_recipe(), [(0, "A")]))
    assert all("objects_from" not in item for item in plain.model_dump(mode="json")["measurements"])


def test_one_image_is_called_by_overlap_and_remembers_what_it_was_compared_with():
    labels, other = _squares()
    image = np.zeros((1, *labels.shape), dtype=np.uint16)
    result = process_image(image, _recipe(), automated_labels=labels, other_objects={OTHER: other})
    assert result.objects["in_other"].tolist() == [100.0, 50.0, 30.0, 0.0]
    assert result.objects["other_pos"].tolist() == [True, True, False, False]
    assert result.provenance["object_overlap"] == {OTHER: labels_digest(other)}
    named = process_image(image, _recipe(), automated_labels=labels, other_objects={OTHER: other}, other_digests={OTHER: "abc"})
    assert named.provenance["object_overlap"] == {OTHER: "abc"}
    alone = process_image(image, _recipe(), automated_labels=labels)
    assert alone.objects["in_other"].isna().all() and alone.provenance["object_overlap"] == {OTHER: ""}
    assert OVERLAP_NOT_MEASURED in alone.qc.warnings
    assert bool(alone.objects["unmeasured"].all())
    # A recipe without such markers records nothing new.
    plain = process_image(image, marker_recipe(_recipe(), [(0, "A")]), automated_labels=labels)
    assert "object_overlap" not in plain.provenance


def test_an_edit_measures_only_what_changed_until_the_other_objects_change():
    from cellquant.edits import EditOperation

    labels, other = _squares()
    image = np.zeros((1, *labels.shape), dtype=np.uint16)
    first = process_image(image, _recipe(), automated_labels=labels, other_objects={OTHER: other})
    delete = [EditOperation(image_id="image", object_id=4, operation="delete", timestamp="2026-01-01T00:00:00+00:00")]
    edited = process_image(image, _recipe(), automated_labels=labels, other_objects={OTHER: other}, manual_edits=delete, previous=first)
    assert edited.objects.loc[~edited.objects["excluded"], "in_other"].tolist() == [100.0, 50.0, 30.0]
    # The other analysis lost the object over object 1: every overlap is measured again, not only near the edit.
    changed = np.where(other == 7, 0, other)
    again = process_image(image, _recipe(), automated_labels=labels, other_objects={OTHER: changed}, manual_edits=delete, previous=edited)
    assert again.objects.loc[~again.objects["excluded"], "in_other"].tolist() == [0.0, 50.0, 30.0]


def test_quick_setup_adds_and_removes_the_marker_and_leaves_it_alone_otherwise():
    base = marker_recipe(_recipe(), [(1, "AF594")])
    plan = add_overlap_marker(base, OTHER, 2, "AF647")
    assert plan.added == ["AF647 objects"] and plan.new_ids == []  # its cutoff is not guessed from the image
    recipe = load_recipe(plan.data)
    marker = recipe.classifications[-1]
    assert (marker.id, marker.threshold, marker.comparison) == ("af647_objects_pos", 50.0, "at_least")
    assert uses_overlap(recipe, marker) and not uses_overlap(recipe, recipe.classifications[0])
    assert "50% of the object inside" in describe_rule(recipe, marker)
    assert {"numerator": "af594_pos AND af647_objects_pos", "denominator": "all_objects"} in plan.data["reports"]
    assert list(overlap_markers(plan.data)) == [OTHER]
    assert not add_overlap_marker(plan.data, OTHER, 2, "AF647").changes  # once per analysis
    # Ticking and unticking channels never touches it, even the channel its objects are found in.
    merged = merge_marker_recipe(plan.data, [(1, "AF594"), (2, "AF647")], {0, 1, 2})
    assert merged.added == ["AF647"] and list(overlap_markers(merged.data)) == [OTHER]
    unticked = merge_marker_recipe(merged.data, [(1, "AF594")], {0, 1, 2})
    assert unticked.removed == ["AF647"] and list(overlap_markers(unticked.data)) == [OTHER]
    gone = remove_overlap_marker(unticked.data, OTHER)
    assert gone.removed == ["AF647 objects"] and overlap_markers(gone.data) == {}
    assert load_recipe(gone.data).content_hash() == load_recipe(base).content_hash()


# -- two analyses of the same images ---------------------------------------------------------------


def _experiment(tmp_path: Path, run: bool = True) -> tuple[AnalysisController, str, str]:
    """An experiment with an analysis of the PAX6 channel (active) and one of the OTX2 channel, both run."""

    write_retina_set(tmp_path / "images", size=96, retinas=1, seed=4)
    controller = AnalysisController.create(tmp_path / "experiment", "Overlap", input_directory=tmp_path / "images" / "images")
    controller.add_image_paths([tmp_path / "images" / "images"])
    for index, name in enumerate(("OTX2", "Fluor", "PAX6")):
        controller.set_channel_name(index, name)
    base = controller.recipe.model_dump(mode="json")
    base["object_set"] = {"name": "Nuclei", "segmentation_channel": 2, "algorithm": "classical", "parameters": CLASSICAL}
    controller.set_recipe(base)
    controller.save()
    here = controller.recipe.recipe_id
    other = controller.analyses_for_channels([0])[0]
    if run:
        controller.run_analyses()
    assert controller.recipe.recipe_id == here
    return controller, here, other


def _add_marker(controller: AnalysisController, other: str) -> str:
    plan = controller.overlap_marker_plan(other, True)
    controller.set_recipe(plan.data)
    controller.save()
    return controller.overlap_choices()[0]["marker"]


def test_a_marker_compares_with_the_other_analysis_objects(tmp_path: Path):
    controller, here, other = _experiment(tmp_path)
    choices = controller.overlap_choices()
    assert [(item["id"], item["name"], item["channel_name"], item["marker"]) for item in choices] == [(other, "Objects in OTX2", "OTX2", "")]
    marker = _add_marker(controller, other)
    assert marker == "otx2_objects_pos" and controller.overlap_analyses() == [other]
    image_id = controller.experiment.images[0].image_id
    assert controller.lacks_markers(image_id) or controller.recall(image_id) is not None
    result = controller.measure_markers(image_id)
    values = result.objects["otx2_objects_overlap"]
    theirs = controller.objects_of(other, image_id)
    assert theirs is not None and theirs.shape == result.labels.shape
    expected = [100 * np.count_nonzero(theirs[result.labels == object_id]) / np.count_nonzero(result.labels == object_id) for object_id in result.objects["object_id"]]
    assert np.allclose(values.to_numpy(dtype=float), expected)
    assert 0 < int(result.objects[marker].astype(bool).sum()) < len(result.objects)  # some inside, some not
    assert result.provenance["object_overlap"] == {other: controller.objects_digest(other, image_id)}
    assert not controller.overlap_changed(image_id)
    # A run measures it too, and the run's tables carry the column.
    run = controller.run_image(image_id)
    assert np.allclose(run.objects["otx2_objects_overlap"].to_numpy(dtype=float), expected)
    reopened = AnalysisController.open(controller.directory)
    again = reopened.recall(image_id)
    assert np.allclose(again.objects["otx2_objects_overlap"].to_numpy(dtype=float), expected)


def test_editing_the_other_analysis_measures_the_marker_again_and_clears_the_approval(tmp_path: Path):
    controller, here, other = _experiment(tmp_path)
    marker = _add_marker(controller, other)
    image_id = controller.experiment.images[0].image_id
    before = controller.run_image(image_id)
    controller.set_status(image_id, "approved")
    controller.save()
    inside = before.objects.loc[before.objects["otx2_objects_overlap"] > 0, "object_id"].astype(int).tolist()
    assert inside
    # In the other analysis: delete every object that touches the first overlapping object here.
    controller.switch_analysis(other)
    theirs = controller.recall(image_id)
    touching = sorted(set(np.unique(theirs.labels[before.labels == inside[0]]).tolist()) - {0})
    controller.delete_objects(image_id, touching)
    controller.switch_analysis(here)
    assert controller.overlap_changed(image_id, controller.recall(image_id, measure=False))
    after = controller.recall(image_id)
    row = after.objects.loc[after.objects["object_id"] == inside[0]].iloc[0]
    assert row["otx2_objects_overlap"] == 0.0 and not bool(row[marker])
    assert not controller.overlap_changed(image_id)
    record = controller.experiment.image(image_id)
    assert record.processing_status == "analyzed" and "compared with" in record.last_message
    # Nothing is measured again while the other objects stay as they are.
    assert controller.recall(image_id) is after


def test_no_objects_or_another_z_handling_leave_the_marker_unmeasured(tmp_path: Path):
    controller, here, other = _experiment(tmp_path)
    _add_marker(controller, other)
    image_id = controller.experiment.images[0].image_id
    assert controller.objects_of(here, image_id) is None  # never itself
    controller.remove_analysis(other)
    assert controller.objects_of(other, image_id) is None
    result = controller.run_image(image_id)
    assert result.objects["otx2_objects_overlap"].isna().all()
    assert OVERLAP_NOT_MEASURED in result.qc.warnings


def test_a_batch_finds_the_compared_objects_first(tmp_path: Path):
    controller, here, other = _experiment(tmp_path, run=False)  # nothing has objects yet
    _add_marker(controller, other)
    assert [recipe_id for recipe_id, _images in controller._sources_first([(here, ["a"]), (other, ["a"])])] == [other, here]
    # Each compared with the other: the first to run is measured again once the second has its objects.
    assert controller.add_reverse_overlap_marker(other) and not controller.add_reverse_overlap_marker(other)
    before = {path.name for path in (controller.directory / "runs").iterdir()}
    reports = controller.run_analyses()
    assert len(reports) == 2
    image_id = controller.experiment.images[0].image_id
    # Nothing is left saying the first one was not measured: its report, its run folder, its status.
    made = sorted({path.name for path in (controller.directory / "runs").iterdir()} - before)
    assert len(made) == 2 and sorted(report.run_id for report in reports) == made
    for report, column in zip(reports, ("otx2_objects_overlap", "pax6_objects_overlap")):
        assert report.jobs and all((job.status, job.message) == ("Success", "") for job in report.jobs)
        for job in report.jobs:
            saved = pd.read_csv(Path(report.run_dir) / "measurements" / f"{job.image_id}.csv")
            assert saved[column].notna().all()
            assert json.loads((Path(report.run_dir) / "qc" / f"{job.image_id}.json").read_text())["warnings"] == []
        run = json.loads((Path(report.run_dir) / "run.json").read_text())
        assert run["processing_status"] == "completed" and not run["warnings"]
    for recipe_id, column in ((here, "otx2_objects_overlap"), (other, "pax6_objects_overlap")):
        controller.switch_analysis(recipe_id)
        record = controller.experiment.image(image_id)
        assert (record.processing_status, record.last_result) == ("analyzed", "Success")
        result = controller.recall(image_id)
        assert result.objects[column].notna().all() and not controller.overlap_changed(image_id)
        theirs = controller.objects_of(here if recipe_id == other else other, image_id)
        first = int(result.objects["object_id"].iloc[0])
        expected = 100 * np.count_nonzero(theirs[result.labels == first]) / np.count_nonzero(result.labels == first)
        assert result.objects[column].iloc[0] == pytest.approx(expected)


# -- Z-stacks --------------------------------------------------------------------------------------

PIXEL = 0.5  # µm per pixel in X and Y
STEP = 1.5  # µm between slices


def _ball(shape, center, radius_um):
    z, y, x = np.ogrid[: shape[0], : shape[1], : shape[2]]
    cz, cy, cx = center
    return ((z - cz) * STEP) ** 2 + ((y - cy) * PIXEL) ** 2 + ((x - cx) * PIXEL) ** 2 <= radius_um**2


def _stack_experiment(tmp_path: Path, z_stack: str) -> tuple[AnalysisController, str, str, str]:
    """Three nuclei in channel 1, two of them stacked in Z; channel 2 shows only the upper stacked one.

    One analysis finds the objects of each channel, with the same Z handling.
    """

    import tifffile

    shape = (9, 64, 64)
    nuclear = np.full(shape, 50, dtype=np.uint16)
    marker = np.full(shape, 20, dtype=np.uint16)
    upper = _ball(shape, (6, 20, 20), 2.8)
    for mask in (_ball(shape, (2, 20, 20), 2.8), upper, _ball(shape, (4, 44, 44), 2.9)):
        nuclear[mask] = 1000
    marker[upper] = 800
    path = tmp_path / "data" / "stack.tif"
    path.parent.mkdir(parents=True)
    tifffile.imwrite(
        path,
        np.stack([nuclear, marker], axis=1),
        imagej=True,
        resolution=(1 / PIXEL, 1 / PIXEL),
        metadata={"axes": "ZCYX", "unit": "um", "spacing": STEP},
    )
    controller = AnalysisController.create(tmp_path / "results", "3D overlap", input_directory=path.parent)
    controller.add_image_paths([path])
    for index, name in enumerate(("Nuclei", "OTX2")):
        controller.set_channel_name(index, name)
    controller.set_recipe(
        {
            "z_stack": z_stack,
            "object_set": {
                "segmentation_channel": 0,
                "algorithm": "classical",
                "parameters": {"threshold_method": "manual", "threshold": 300},
            },
            "measurements": [],
            "classifications": [],
            "reports": [],
        }
    )
    controller.save()
    here = controller.recipe.recipe_id
    other = controller.analyses_for_channels([1])[0]
    controller.run_analyses()
    return controller, here, other, controller.experiment.images[0].image_id


@pytest.mark.parametrize("mode", ["stitch_slices", "full_3d"])
def test_in_a_z_stack_only_the_object_at_the_same_depth_is_inside(tmp_path: Path, mode: str):
    controller, here, other, image_id = _stack_experiment(tmp_path, mode)
    marker = _add_marker(controller, other)
    result = controller.run_image(image_id)

    theirs = controller.objects_of(other, image_id)
    assert result.labels.shape == theirs.shape == (9, 64, 64)
    assert len(np.unique(theirs)) - 1 == 1  # the other analysis found the upper nucleus only
    objects = result.objects.sort_values("centroid_z").reset_index(drop=True)
    stacked = objects.loc[(objects["centroid_x"] - 10).abs() < 1]
    side = objects.loc[(objects["centroid_x"] - 10).abs() >= 1]
    # Same XY position, different depths: the lower nucleus is outside, the upper one inside.
    assert stacked["otx2_objects_overlap"].tolist() == [0.0, 100.0]
    assert stacked[marker].tolist() == [False, True]
    assert side["otx2_objects_overlap"].tolist() == [0.0] and side[marker].tolist() == [False]
    assert result.reports.loc[0, "count"] == 1 and result.reports.loc[0, "denominator_count"] == 3
    assert result.provenance["object_overlap"] == {other: controller.objects_digest(other, image_id)}


def test_a_projection_cannot_tell_the_stacked_objects_apart(tmp_path: Path):
    """Why the Z handling matters here: projected, the two stacked nuclei are one object, and it is inside."""

    controller, here, other, image_id = _stack_experiment(tmp_path, "max_projection")
    marker = _add_marker(controller, other)
    result = controller.run_image(image_id)
    assert result.labels.ndim == 2 and result.qc.n_objects == 2
    assert sorted(result.objects["otx2_objects_overlap"].tolist()) == [0.0, 100.0]
    assert int(result.objects[marker].astype(bool).sum()) == 1


def test_objects_found_with_another_z_handling_are_not_compared(tmp_path: Path):
    controller, here, other, image_id = _stack_experiment(tmp_path, "stitch_slices")
    _add_marker(controller, other)
    controller.switch_analysis(other)
    projected = controller.recipe.model_dump(mode="json")
    projected["z_stack"] = "max_projection"
    controller.set_recipe(projected)
    controller.save()
    controller.run_image(image_id)
    controller.switch_analysis(here)
    result = controller.run_image(image_id)
    assert result.labels.ndim == 3 and controller.objects_of(other, image_id).ndim == 2
    assert result.objects["otx2_objects_overlap"].isna().all()
    assert OVERLAP_NOT_MEASURED in result.qc.warnings


# -- kept up to date everywhere ---------------------------------------------------------------------


def test_an_export_measures_the_marker_again_when_the_other_objects_changed(tmp_path: Path):
    controller, here, other = _experiment(tmp_path)
    marker = _add_marker(controller, other)
    image_id = controller.experiment.images[0].image_id
    before = controller.run_image(image_id)
    inside = before.objects.loc[before.objects["otx2_objects_overlap"] > 0, "object_id"].astype(int).tolist()
    controller.switch_analysis(other)
    theirs = controller.recall(image_id)
    controller.delete_objects(image_id, sorted(set(np.unique(theirs.labels[before.labels == inside[0]]).tolist()) - {0}))
    controller.switch_analysis(here)
    # Straight to the export, without showing the image first.
    exported = controller._results_for_export()[0]
    row = exported.objects.loc[exported.objects["object_id"] == inside[0]].iloc[0]
    assert row["otx2_objects_overlap"] == 0.0 and not bool(row[marker])
    assert exported.provenance["object_overlap"] == {other: controller.objects_digest(other, image_id)}


def test_a_marker_compared_with_a_removed_analysis_stays_listed_until_it_is_taken_away(tmp_path: Path):
    from cellquant.errors import CellQuantError

    controller, here, other = _experiment(tmp_path)
    _add_marker(controller, other)
    image_id = controller.experiment.images[0].image_id
    assert [item["removed"] for item in controller.overlap_choices()] == [False]
    controller.remove_analysis(other)
    listed = [(item["id"], item["name"], item["channel_name"], item["marker"], item["removed"]) for item in controller.overlap_choices()]
    assert listed == [(other, "Objects in OTX2", "OTX2", "otx2_objects_pos", True)]
    # The reason for the blank marker is said every time: a first run, a run from the saved table, and after an edit.
    first = controller.run_image(image_id)
    again = controller.run_image(image_id)
    controller.delete_objects(image_id, [int(first.objects["object_id"].iloc[0])])
    edited = controller.recall(image_id)
    for result in (first, again, edited):
        assert result.objects["otx2_objects_overlap"].isna().all() and OVERLAP_NOT_MEASURED in result.qc.warnings
    with pytest.raises(CellQuantError):
        controller.overlap_marker_plan(other, True)  # nothing to compare with any more
    plan = controller.overlap_marker_plan(other, False)
    assert plan.removed == ["OTX2 objects"]
    controller.set_recipe(plan.data)
    assert controller.overlap_choices() == [] and controller.overlap_analyses() == []


def test_several_markers_are_planned_as_one_change(tmp_path: Path):
    from cellquant.quicksetup import join_merges

    controller, here, other = _experiment(tmp_path)
    third = controller.analyses_for_channels([1])[0]
    first = controller.overlap_marker_plan(other, True, 40)
    second = controller.overlap_marker_plan(third, True, 40, first.data)
    plan = join_merges(first, second)
    assert plan.added == ["OTX2 objects", "Fluor objects"] and not plan.loses_something
    controller.set_recipe(plan.data)
    assert controller.overlap_analyses() == [other, third]
    assert {item["marker"] for item in controller.overlap_choices()} == {"otx2_objects_pos", "fluor_objects_pos"}
    assert [item.threshold for item in controller.recipe.classifications] == [40.0, 40.0]
    back = join_merges(controller.overlap_marker_plan(other, False), controller.overlap_marker_plan(third, False, None, controller.overlap_marker_plan(other, False).data))
    assert back.removed == ["OTX2 objects", "Fluor objects"] and not back.data["classifications"]
