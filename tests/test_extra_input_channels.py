"""Cellpose-SAM reads extra input channels together with the source channel (step 2, Advanced)."""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest
import tifffile

from cellquant import engines, segmentation
from cellquant.controller import AnalysisController, extra_channels_log_line
from cellquant.errors import SegmentationError
from cellquant.pipeline import process_image
from cellquant.recipe import Recipe
from cellquant.segmentation import extra_channel_indices, segment_objects, segment_regions
from cellquant.storage import CHANNEL_LIST_FILE, input_tag, label_paths, run_channel_tag
from cellquant.volume import segment_volume
from tests.test_cellpose_engines import _V3, _V4, _install_fake


def _seeing(source: str) -> str:
    """Make the fake record what Cellpose was given: the array's shape, where its channels are, and
    each channel's brightest value. Masks are shaped like the image without its channel axis."""

    return source.replace(
        '''        CALLS.append(("CellposeModel.eval", dict(diameter=diameter, channels=channels)))
        masks = np.zeros(np.asarray(x).shape[-2:], dtype=np.int32)
        masks[2:8, 2:8] = 1
        masks[12:18, 12:18] = 2''',
        '''        data = np.asarray(x)
        spatial = data.shape if channel_axis is None else data.shape[:-1]
        peaks = None if channel_axis is None else [float(data[..., index].max()) for index in range(data.shape[-1])]
        CALLS.append(("CellposeModel.eval", dict(shape=data.shape, channel_axis=channel_axis, channels=channels,
                      normalize=normalize, do_3D=do_3D, z_axis=z_axis, peaks=peaks)))
        masks = np.zeros(spatial if do_3D else spatial[-2:], dtype=np.int32)
        masks[..., 2:8, 2:8] = 1
        masks[..., 12:18, 12:18] = 2''',
    )


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(segmentation, "_MODELS", {})
    engines.cellpose_engine.cache_clear()
    yield
    engines.cellpose_engine.cache_clear()


def _evals(models) -> list[dict]:
    return [options for name, options in models.CALLS if name == "CellposeModel.eval"]


def _recipe(parameters: dict, channel: int = 0, algorithm: str = "cellpose") -> dict:
    return {"object_set": {"segmentation_channel": channel, "algorithm": algorithm, "parameters": parameters}}


def _image(channels: int = 3) -> np.ndarray:
    image = np.zeros((channels, 20, 20), dtype=np.uint16)
    for index in range(channels):
        image[index, 2:8, 2:8] = 100 * (index + 1)
    return image


def test_extra_channel_indices_reads_only_channel_positions():
    assert extra_channel_indices(None) == []
    assert extra_channel_indices({}) == []
    assert extra_channel_indices({"extra_channels": [2, 1, 2, -1, "x", None]}) == [2, 1]


def test_one_channel_calls_cellpose_exactly_as_before(tmp_path, monkeypatch):
    models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", _seeing(_V4))
    result = process_image(_image(), _recipe({}), channel_axis=0)
    call = _evals(models)[-1]
    assert call["shape"] == (20, 20) and call["channel_axis"] is None
    assert "segmentation_extra_channels" not in result.provenance
    assert "input_channels" not in result.provenance["segmentation_engine"]


@pytest.mark.parametrize(("extra", "count"), [([2], 2), ([2, 1], 3)])
def test_extra_channels_reach_cellpose_sam_after_the_source_channel(tmp_path, monkeypatch, extra, count):
    models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", _seeing(_V4))
    result = process_image(_image(), _recipe({"extra_channels": extra}), channel_axis=0)
    call = _evals(models)[-1]
    assert call["shape"] == (20, 20, count) and call["channel_axis"] == -1
    assert call["peaks"] == [100.0, *[100.0 * (index + 1) for index in extra]]  # the source channel first
    assert call["normalize"] is True  # Cellpose scales each channel itself
    assert result.provenance["segmentation_extra_channels"] == extra
    assert result.provenance["segmentation_engine"]["input_channels"] == count
    assert result.labels.shape == (20, 20) and result.qc.n_objects == 2


def test_the_source_channel_a_missing_channel_and_a_third_extra_are_left_out(tmp_path, monkeypatch):
    models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", _seeing(_V4))
    result = process_image(_image(4), _recipe({"extra_channels": [1, 3, 7, 2, 0]}, channel=1), channel_axis=0)
    assert _evals(models)[-1]["shape"] == (20, 20, 3)
    assert result.provenance["segmentation_extra_channels"] == [3, 2]
    assert any("Channel 8 is not in this image" in text for text in result.qc.warnings)


def test_skipped_channel_names_are_a_warning_on_the_result(tmp_path, monkeypatch):
    _install_fake(tmp_path, monkeypatch, "4.2.1.1", _seeing(_V4))
    result = process_image(_image(), _recipe({"extra_channels_skipped": ["AF594", "DAPI"]}), channel_axis=0)
    assert "Extra input channels AF594, DAPI are not in this image, so its objects were found without them." in result.qc.warnings


def test_the_classical_method_finds_objects_in_one_channel():
    with pytest.raises(SegmentationError, match="finds objects in one channel"):
        segment_objects(_image()[0], "classical", {"threshold_method": "manual", "threshold": 50}, extra_channels=[_image()[1]])


def test_classic_cellpose_refuses_extra_channels_before_loading_a_model(tmp_path, monkeypatch):
    models = _install_fake(tmp_path, monkeypatch, "3.1.1.3", _V3)
    with pytest.raises(SegmentationError, match="Extra input channels need Cellpose-SAM"):
        process_image(_image(), _recipe({"extra_channels": [1]}), channel_axis=0)
    assert models.CALLS == []


def test_more_than_two_extra_channels_and_other_shapes_are_refused(tmp_path, monkeypatch):
    _install_fake(tmp_path, monkeypatch, "4.2.1.1", _seeing(_V4))
    image = _image(4)
    with pytest.raises(SegmentationError, match="at most 3 channels"):
        segment_objects(image[0], "cellpose", {}, extra_channels=[image[1], image[2], image[3]])
    with pytest.raises(SegmentationError, match="does not match the shape"):
        segment_objects(image[0], "cellpose", {}, extra_channels=[image[1][:10]])


def test_the_setting_changes_the_recipe_hash_only_when_present():
    old = Recipe.model_validate(_recipe({"diameter_um": 6.0}))
    again = Recipe.model_validate(_recipe({"diameter_um": 6.0}))
    new = Recipe.model_validate(_recipe({"diameter_um": 6.0, "extra_channels": [1]}))
    assert old.content_hash() == again.content_hash() != new.content_hash()


def test_cropped_regions_scale_each_channel_on_the_whole_image(tmp_path, monkeypatch):
    models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", _seeing(_V4))
    rng = np.random.default_rng(0)
    source = rng.random((40, 40)).astype(np.float32)
    extra = (rng.random((40, 40)) * 5000).astype(np.float32)  # a far brighter channel
    labels = segment_regions(source, "cellpose", {}, [(0, 20, 0, 20), (20, 40, 20, 40)], extra_channels=[extra])
    calls = _evals(models)
    assert [call["shape"] for call in calls] == [(20, 20, 2), (20, 20, 2)]
    assert all(call["channel_axis"] == -1 and call["normalize"] is False for call in calls)
    # Both channels arrive on the same 0-1 scale, whatever their own brightness.
    assert all(peak < 1.2 for call in calls for peak in call["peaks"])
    assert labels.shape == (40, 40) and len(np.unique(labels)) == 5  # two objects per rectangle, numbered apart


def test_linked_slices_and_true_3d_get_the_channels_last(tmp_path, monkeypatch):
    models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", _seeing(_V4))
    rng = np.random.default_rng(1)
    stack = rng.random((3, 20, 20)).astype(np.float32)
    extra = (rng.random((3, 20, 20)) * 900).astype(np.float32)
    linked = segment_volume(stack, "cellpose", {}, "stitch_slices", extra_channels=[extra])
    calls = _evals(models)
    assert [call["shape"] for call in calls] == [(20, 20, 2)] * 3
    assert all(call["channel_axis"] == -1 and call["normalize"] is False and all(peak < 1.2 for peak in call["peaks"]) for call in calls)
    assert linked.shape == (3, 20, 20)

    details: dict = {}
    whole = segment_volume(stack, "cellpose", {"min_voxels_3d": 1}, "full_3d", extra_channels=[extra], details=details)
    call = _evals(models)[-1]
    assert call["shape"] == (3, 20, 20, 2) and call["channel_axis"] == -1 and call["z_axis"] == 0 and call["do_3D"] is True
    assert whole.shape == (3, 20, 20)
    assert details["engine"]["input_channels"] == 2


def test_cropped_stacks_keep_the_channels(tmp_path, monkeypatch):
    models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", _seeing(_V4))
    rng = np.random.default_rng(2)
    stack = rng.random((2, 40, 40)).astype(np.float32)
    extra = rng.random((2, 40, 40)).astype(np.float32)
    labels = segment_volume(stack, "cellpose", {}, "stitch_slices", rectangles=[(0, 20, 0, 20)], extra_channels=[extra])
    assert [call["shape"] for call in _evals(models)] == [(20, 20, 2)] * 2
    assert labels.shape == (2, 40, 40) and not labels[:, 20:, :].any()


# --- names of the outputs ---------------------------------------------------------------------------


def test_input_tag_joins_the_channels_with_a_plus():
    assert input_tag("AF 594") == "AF_594"
    assert input_tag(["AF594", "AF647"]) == "AF594+AF647"
    assert input_tag(["AF594", "??", "DAPI"]) == "AF594+DAPI"
    assert input_tag(None) == "" and input_tag([]) == ""


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        ([["AF594", "AF647"]], "AF594+AF647"),
        ([["AF594", "AF647"], ["AF594", "AF647"]], "AF594+AF647"),
        ([["AF594", "AF647"], "DAPI"], "mixed"),
        ([["AF594", "AF647"], ["AF488", "AF647"]], "mixed"),
        ([["a" * 12, "b" * 12, "c" * 12]], "mixed"),  # too long for a folder name
        (["b", "A"], "A+b"),
    ],
)
def test_run_channel_tag_with_several_input_channels(names, expected):
    assert run_channel_tag(names) == expected


# --- an experiment: per-image channels, files and the list --------------------------------------------


def _write(path: Path, channels: int = 3) -> None:
    image = np.zeros((channels, 40, 40), dtype=np.uint16)
    for index in range(channels):
        image[index, 4:14, 4:14] = 500 + 100 * index
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(path, image, photometric="minisblack")


def _experiment(tmp_path: Path, extra: list[int], channels: int = 3) -> AnalysisController:
    _write(tmp_path / "images" / "a.tif", channels)
    controller = AnalysisController.create(tmp_path / "exp", "exp")
    controller.add_image_paths([tmp_path / "images"])
    for index, name in enumerate(["AF488", "AF594", "AF647"][:channels]):
        controller.rename_channel(index, name)
    controller.set_recipe(
        {
            "recipe_name": "extra",
            "object_set": {"name": "Objects", "segmentation_channel": 1, "algorithm": "cellpose", "parameters": {"extra_channels": extra}},
            "measurements": [],
            "classifications": [],
            "reports": [],
        }
    )
    return controller


def _rows(folder: Path) -> list[dict[str, str]]:
    with (folder / CHANNEL_LIST_FILE).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_a_run_names_folder_masks_and_list_after_every_input_channel(tmp_path, monkeypatch):
    models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", _seeing(_V4))
    controller = _experiment(tmp_path, [2])
    image_id = controller.experiment.images[0].image_id
    assert controller.extra_channel_names() == ["AF647"]
    assert controller.extra_channels_for(image_id) == ([2], [])
    assert controller.input_channel_names(image_id) == ["AF594", "AF647"]

    report = controller.run_images()

    assert _evals(models)[-1]["shape"] == (40, 40, 2)
    assert report.run_id.endswith("_AF594+AF647")
    run_dir = controller.directory / "runs" / report.run_id
    final, automated = label_paths(run_dir, image_id)
    assert final.name == f"{image_id}_AF594+AF647_final.tif" and final.is_file() and automated.is_file()
    row = _rows(run_dir)[0]
    assert row["segmented_channel"] == "AF594" and row["extra_input_channels"] == "AF647"
    result = controller.last_results[image_id]
    assert result.provenance["segmentation_extra_channels"] == [2]
    assert controller.input_channel_names(image_id, result) == ["AF594", "AF647"]

    reopened = AnalysisController.open(controller.directory)
    assert reopened.recall(image_id).qc.n_objects == result.qc.n_objects  # the masks are found again under their name


def test_without_extra_channels_the_list_has_no_extra_column(tmp_path, monkeypatch):
    _install_fake(tmp_path, monkeypatch, "4.2.1.1", _seeing(_V4))
    controller = _experiment(tmp_path, [])
    report = controller.run_images()
    assert report.run_id.endswith("_AF594")
    assert "extra_input_channels" not in _rows(controller.directory / "runs" / report.run_id)[0]


def test_an_image_chosen_to_use_an_extra_channel_as_its_source_does_not_read_it_twice(tmp_path, monkeypatch):
    models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", _seeing(_V4))
    controller = _experiment(tmp_path, [2, 0])
    image_id = controller.experiment.images[0].image_id
    controller.set_plan_channel([image_id], 2)
    assert controller.extra_channels_for(image_id) == ([0], [])
    assert controller.input_channel_names(image_id) == ["AF647", "AF488"]
    controller.run_images()
    assert _evals(models)[-1]["shape"] == (40, 40, 2)
    assert controller.last_results[image_id].provenance["segmentation_extra_channels"] == [0]


def test_extra_channels_are_found_by_name_and_a_missing_one_is_skipped_with_a_warning(tmp_path, monkeypatch):
    models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", _seeing(_V4))
    controller = _experiment(tmp_path, [2, 0])
    full = controller.experiment.images[0]
    full.channel_names = ["AF488", "AF594", "AF647"]
    # A second image without AF488, its channels in another order.
    _write(tmp_path / "more" / "b.tif", 2)
    controller.add_image_paths([tmp_path / "more"])
    other = next(record for record in controller.experiment.images if record.image_id != full.image_id)
    other.channel_names = ["AF647", "AF594"]
    controller.save()

    assert controller.segmentation_channel_for(other.image_id)[0] == 1
    assert controller.extra_channels_for(other.image_id) == ([0], ["AF488"])
    assert controller.input_channel_names(other.image_id) == ["AF594", "AF647"]

    controller.run_images([other.image_id])
    assert _evals(models)[-1]["shape"] == (40, 40, 2)
    result = controller.last_results[other.image_id]
    assert result.provenance["segmentation_extra_channels"] == [0]
    assert any("AF488 is not in this image" in text for text in result.qc.warnings)
    # The analysis's own settings are untouched.
    assert controller.recipe.object_set.parameters["extra_channels"] == [2, 0]


def test_the_run_log_line_names_the_extra_channels():
    assert extra_channels_log_line(["AF647"], "Analysis 1") == (
        "Extra input channel for this run (Analysis 1): AF647. Cellpose-SAM reads it together with each image's source channel."
    )
    assert "AF647 and DAPI" in extra_channels_log_line(["AF647", "DAPI"], "A")
