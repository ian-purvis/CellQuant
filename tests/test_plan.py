"""The plan: which images each analysis runs, per-image channels, other channel layouts, and grouping
by channel layout or folder."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import tifffile

from cellquant.controller import AnalysisController
from cellquant.errors import CellQuantError
from cellquant.plan import group_images, layout_label
from cellquant.quicksetup import marker_recipe
from cellquant.synthetic_retina import PIXEL_SIZE_UM, Z_STEP_UM, _luts, write_retina_set

CLASSICAL = {"sigma": 1.0, "use_watershed": True, "watershed_min_distance_px": 4, "min_area_um2": 5}


def _reordered_copy(source: Path, target: Path) -> None:
    """The same image with its channels stored as Far Red, Green, Red: another channel layout."""

    with tifffile.TiffFile(source) as handle:
        data = handle.asarray()  # Z, C, Y, X
    target.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(
        target,
        np.ascontiguousarray(data[:, [2, 0, 1]]),
        imagej=True,
        resolution=(1 / PIXEL_SIZE_UM, 1 / PIXEL_SIZE_UM),
        metadata={"axes": "ZCYX", "unit": "um", "spacing": Z_STEP_UM, "Labels": ["Far Red", "Green", "Red"], "LUTs": [_luts()[2], _luts()[0], _luts()[1]]},
    )


def _experiment(tmp_path: Path) -> AnalysisController:
    write_retina_set(tmp_path / "set", size=96, retinas=1, seed=4)
    images = tmp_path / "set" / "images"
    _reordered_copy(images / "Control" / "Retina 1" / "control_r1.tif", images / "Zeiss 40x" / "control_r1_reordered.tif")
    controller = AnalysisController.create(tmp_path / "experiment", "Plan", input_directory=images)
    controller.add_image_paths([images])
    base = controller.recipe.model_dump(mode="json")
    base["object_set"] = {"name": "Nuclei", "segmentation_channel": 2, "algorithm": "classical", "parameters": CLASSICAL}
    controller.set_recipe(marker_recipe(base, [(0, "OTX2"), (1, "Fluor")]))
    controller.save()
    return controller


def _ids(controller):
    return {record.relative_path: record.image_id for record in controller.experiment.images}


def test_grouping_by_layout_folder_or_both(tmp_path: Path):
    controller = _experiment(tmp_path)
    records = controller.experiment.images
    by_layout = group_images(records, "layout")
    assert [group.label for group in by_layout] == [
        layout_label(("Green", "Red", "Far Red")),
        layout_label(("Far Red", "Green", "Red")),
    ]
    assert [len(group.image_ids) for group in by_layout] == [2, 1]
    by_folder = group_images(records, "folder")
    assert sorted(group.label for group in by_folder) == ["CRISPRi/Retina 1", "Control/Retina 1", "Zeiss 40x"]
    both = group_images(records, "layout_folder")
    assert [len(group.children) for group in both] == [2, 1] and both[0].image_ids == []
    assert sorted(both[0].all_image_ids()) == sorted(by_layout[0].image_ids)
    with pytest.raises(ValueError):
        group_images(records, "colour")


def test_other_layouts_use_the_channel_with_the_same_name(tmp_path: Path):
    controller = _experiment(tmp_path)
    ids = _ids(controller)
    reordered = ids["Zeiss 40x/control_r1_reordered.tif"]
    original = ids["Control/Retina 1/control_r1.tif"]
    assert controller.segmentation_channel_for(original) == (2, "analysis")
    assert controller.segmentation_channel_for(reordered) == (0, "same name")
    assert controller.measurement_channels_for(reordered) == {"otx2_mean": (1, "same name"), "fluor_mean": (2, "same name")}
    first = controller.run_image(original)
    second = controller.run_image(reordered)
    # The same pixels in another channel order give the same objects and the same marker values.
    assert first.qc.n_objects == second.qc.n_objects
    np.testing.assert_allclose(np.sort(first.objects["otx2_mean"]), np.sort(second.objects["otx2_mean"]))
    assert second.provenance["recipe"]["object_set"]["segmentation_channel"] == 0
    assert controller.recipe.object_set.segmentation_channel == 2  # the analysis's own settings are unchanged


def test_per_image_channel_and_ticks(tmp_path: Path):
    controller = _experiment(tmp_path)
    ids = _ids(controller)
    control, crispri = ids["Control/Retina 1/control_r1.tif"], ids["CRISPRi/Retina 1/crispri_r1.tif"]
    first = controller.recipe.recipe_id
    second = controller.add_analysis("Objects in Green", activate=False)
    assert controller.planned_images(first) == list(ids.values())  # every included image by default

    controller.set_plan_channel([crispri], 1)  # this image only: find objects in Red
    assert controller.segmentation_channel_for(crispri) == (1, "chosen for this image")
    assert controller.segmentation_channel_for(crispri, second) == (2, "analysis")  # other analyses unchanged
    with pytest.raises(CellQuantError):
        controller.set_plan_channel([crispri], 5)
    result = controller.run_image(crispri)
    assert result.provenance["recipe"]["object_set"]["segmentation_channel"] == 1

    controller.set_planned([control], False, [second])
    assert controller.planned_images(second) == [crispri, ids["Zeiss 40x/control_r1_reordered.tif"]]
    assert controller.plan_status(control, second) == "not planned"
    assert controller.plan_status(crispri, first) == "analyzed"
    assert controller.plan_status(control, first) == "not run"

    reports = controller.run_analyses()
    assert [len(report.jobs) for report in reports] == [3, 2]
    assert controller.plan_status(control, second) == "not planned"
    assert controller.plan_status(crispri, second) == "analyzed"

    # Saved with the experiment.
    reopened = AnalysisController.open(controller.directory)
    assert reopened.segmentation_channel_for(crispri) == (1, "chosen for this image")
    assert not reopened.is_planned(control, second)
    # Ticking a left-out image includes it again; going back to the analysis channel clears the choice.
    reopened.set_included(control, False)
    assert not reopened.is_planned(control, first)
    reopened.set_planned([control], True)
    assert reopened.experiment.image(control).include and reopened.is_planned(control, second)
    reopened.set_plan_channel([crispri], None)
    assert reopened.segmentation_channel_for(crispri) == (2, "analysis")
    assert reopened.active_analysis().plan == {}


def test_images_that_lack_a_channel_name_keep_the_position(tmp_path: Path):
    controller = _experiment(tmp_path)
    record = controller.experiment.images[-1]
    record.channel_names = ["DAPI", "GFP", "RFP"]
    assert controller.segmentation_channel_for(record.image_id) == (2, "not in this image")


def test_changes_after_opening_again_use_the_image_channels_once(tmp_path: Path):
    """Editing an image of another layout after reopening re-measures in the same channels as the run
    (the channels are never mapped twice), and settings saved meanwhile stay the analysis's own."""

    controller = _experiment(tmp_path)
    reordered = _ids(controller)["Zeiss 40x/control_r1_reordered.tif"]
    first = controller.run_image(reordered)
    reopened = AnalysisController.open(controller.directory)
    edited = reopened.delete_object(reordered, int(first.objects["object_id"].iloc[0]))
    assert edited.provenance["recipe"]["object_set"]["segmentation_channel"] == 0
    assert [item["channel"] for item in edited.provenance["recipe"]["measurements"]] == [1, 2]
    assert edited.qc.n_objects == first.qc.n_objects - 1 or int(edited.objects["excluded"].sum()) == 1
    with reopened._image_settings(reopened.experiment.image(reordered)):
        reopened.save()
        with pytest.raises(CellQuantError):
            reopened.set_recipe(reopened.recipe.model_dump(mode="json"))
    saved = AnalysisController.open(controller.directory)
    assert saved.recipe.object_set.segmentation_channel == 2
    assert [item.channel for item in saved.recipe.measurements] == [0, 1]


def test_export_does_not_call_per_image_channels_mixed_settings(tmp_path: Path):
    controller = _experiment(tmp_path)
    ids = _ids(controller)
    controller.set_plan_channel([ids["CRISPRi/Retina 1/crispri_r1.tif"]], 1)
    controller.run_images()
    out = controller.export(tmp_path / "export")
    assert not (out / "mixed_settings.txt").exists()
    # A real change of settings partway through is still reported.
    data = controller.recipe.model_dump(mode="json")
    data["object_set"]["parameters"] = {**data["object_set"]["parameters"], "sigma": 2.0}
    controller.set_recipe(data)
    controller.run_image(ids["Control/Retina 1/control_r1.tif"])
    out = controller.export(tmp_path / "export2")
    assert (out / "mixed_settings.txt").exists()


def test_each_file_is_shown_in_its_own_colors(tmp_path: Path):
    """Never false color: every image is displayed with the colors stored in its own file, in its own
    channel order, even when an analysis maps its channels by name."""

    pytest.importorskip("napari")
    from cellquant.gui.app import channel_colormaps
    from cellquant.synthetic_retina import CHANNEL_COLORS

    controller = _experiment(tmp_path)
    ids = _ids(controller)
    expected = {
        "Control/Retina 1/control_r1.tif": [CHANNEL_COLORS[0], CHANNEL_COLORS[1], CHANNEL_COLORS[2]],
        "Zeiss 40x/control_r1_reordered.tif": [CHANNEL_COLORS[2], CHANNEL_COLORS[0], CHANNEL_COLORS[1]],
    }
    for name, colors in expected.items():
        record = controller.experiment.image(ids[name])
        with controller._image_settings(record):
            loaded = controller._load_record(record)
        maps = channel_colormaps(loaded)
        for colormap, color in zip(maps, colors, strict=True):
            assert np.allclose(colormap.colors[-1][:3], np.asarray(color) / 255, atol=1 / 255)
            assert np.allclose(colormap.colors[0][:3], 0)
