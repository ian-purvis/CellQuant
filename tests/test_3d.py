"""3D analysis of Z-stacks: linked slices and whole-volume segmentation."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import tifffile

from cellquant import engines
from cellquant.controller import AnalysisController
from cellquant.edits import apply_edits, operations_from_diff
from cellquant.image import load_image
from cellquant.recipe import load_recipe
from cellquant.regions import expand_labels, ring_labels
from cellquant.volume import scale_brightness, segment_volume, stitch_slices
from tests.test_cellpose_engines import _V3, _V4, _install_fake

PIXEL = 0.5  # µm per pixel in X and Y
STEP = 1.5  # µm between slices


def _ball(shape, center, radius_um):
    z, y, x = np.ogrid[: shape[0], : shape[1], : shape[2]]
    cz, cy, cx = center
    return ((z - cz) * STEP) ** 2 + ((y - cy) * PIXEL) ** 2 + ((x - cx) * PIXEL) ** 2 <= radius_um**2


def _stack(path: Path | None = None, *, dim_top: bool = False) -> np.ndarray:
    """Three nuclei in a 9-slice stack; two are stacked in Z at the same XY position.

    Channel 0: nuclear stain. Channel 1: a marker in the upper stacked nucleus only.
    """

    shape = (9, 64, 64)
    nuclear = np.full(shape, 50, dtype=np.uint16)
    marker = np.full(shape, 20, dtype=np.uint16)
    # Radii below 2 slice steps (3 µm): each ball is 3 slices deep, with an empty slice between the stacked two.
    lower = _ball(shape, (2, 20, 20), 2.8)
    upper = _ball(shape, (6, 20, 20), 2.8)
    side = _ball(shape, (4, 44, 44), 2.9)
    for mask in (lower, upper, side):
        nuclear[mask] = 1000
    marker[upper] = 800
    if dim_top:
        nuclear[6:] = (nuclear[6:] * 0.3).astype(np.uint16)
    data = np.stack([nuclear, marker], axis=1)  # Z, C, Y, X
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tifffile.imwrite(
            path,
            data,
            imagej=True,
            resolution=(1 / PIXEL, 1 / PIXEL),
            metadata={"axes": "ZCYX", "unit": "um", "spacing": STEP},
        )
    return data


def _recipe(z_stack: str, **extra) -> dict:
    return {
        "z_stack": z_stack,
        "object_set": {
            "segmentation_channel": 0,
            "algorithm": "classical",
            "parameters": {"threshold_method": "manual", "threshold": 300},
        },
        "measurements": [{"id": "marker_mean", "channel": 1, "region": {"type": "object"}, "statistic": "mean"}],
        "classifications": [{"id": "marker_pos", "name": "Marker", "measurement": "marker_mean", "threshold": 400}],
        "reports": [{"numerator": "Marker", "denominator": "all_objects"}],
        **extra,
    }


# --- linking -------------------------------------------------------------------------------


def test_linking_joins_overlapping_outlines_and_keeps_separate_ones_apart():
    planes = np.zeros((4, 20, 20), dtype=np.int32)
    planes[0, 2:8, 2:8] = 5  # object A, slices 1-3, numbered differently in each slice
    planes[1, 3:9, 3:9] = 1
    planes[2, 3:8, 3:8] = 9
    planes[1, 12:18, 12:18] = 2  # object B, slices 2-4
    planes[2, 12:18, 12:18] = 3
    planes[3, 13:18, 13:18] = 7
    planes[3, 2:4, 17:19] = 4  # object C, one slice, no overlap
    linked = stitch_slices(planes, 0.25)
    a = {int(linked[z, 5, 5]) for z in range(3)}
    b = {int(linked[z, 15, 15]) for z in range(1, 4)}
    assert len(a) == 1 and len(b) == 1 and a != b
    assert len(np.unique(linked)) - 1 == 3


def test_a_high_threshold_splits_what_a_low_one_links():
    planes = np.zeros((2, 20, 20), dtype=np.int32)
    planes[0, 2:12, 2:12] = 1  # 100 px
    planes[1, 2:7, 2:7] = 1  # 25 px, inside: overlap 0.25
    assert len(np.unique(stitch_slices(planes, 0.2))) - 1 == 1
    assert len(np.unique(stitch_slices(planes, 0.5))) - 1 == 2


def test_one_outline_below_links_to_only_one_above():
    planes = np.zeros((2, 20, 20), dtype=np.int32)
    planes[0, 2:18, 2:10] = 1  # one wide outline
    planes[1, 2:18, 2:9] = 1  # below it: two outlines, one a much better match
    planes[1, 2:18, 9:10] = 2
    linked = stitch_slices(planes, 0.05)
    assert linked[0, 5, 5] == linked[1, 5, 5]
    assert linked[1, 5, 9] not in (0, linked[0, 5, 5])


def test_stack_scaling_keeps_a_dim_slice_dim():
    stack = np.stack([np.linspace(0, 1000, 400).reshape(20, 20), np.linspace(0, 100, 400).reshape(20, 20)])
    whole = scale_brightness(stack, "stack")
    per_slice = scale_brightness(stack, "slice")
    assert whole[1].max() < 0.2  # still dim compared with the bright slice
    assert per_slice[1].max() == pytest.approx(per_slice[0].max(), rel=0.05)  # stretched to match


# --- classical 3D on a file ------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["stitch_slices", "full_3d"])
def test_stacked_nuclei_are_counted_separately_in_3d(tmp_path: Path, mode: str):
    path = tmp_path / "data" / "stack.tif"
    _stack(path)
    controller = AnalysisController.create(tmp_path / "results", "3D", input_directory=path.parent)
    controller.add_image_paths([path])
    controller.set_recipe(_recipe(mode))
    image_id = controller.experiment.images[0].image_id
    result = controller.run_image(image_id)

    assert result.labels.shape == (9, 64, 64)
    assert result.qc.n_objects == 3, result.qc.warnings
    objects = result.objects.sort_values("centroid_z").reset_index(drop=True)
    assert {"centroid_z", "volume", "z_slices", "z_first", "z_last", "z_flag"} <= set(objects.columns)
    # Upper stacked nucleus is marker-positive; the lower one, at the same XY position, is not.
    stacked = objects.loc[(objects["centroid_x"] - 10).abs() < 1]
    assert len(stacked) == 2
    assert stacked["marker_pos"].tolist() == [False, True]
    assert result.reports.loc[0, "count"] == 1 and result.reports.loc[0, "denominator_count"] == 3
    # A 2.8 µm-radius ball: about 92 µm³ (1.5 µm slices make it approximate).
    assert stacked["volume"].iloc[0] == pytest.approx(4 / 3 * np.pi * 2.8**3, rel=0.25)
    assert stacked["z_slices"].tolist() == [3, 3]
    assert result.provenance["analysis_dimensions"] == 3
    assert result.provenance["z_description"].startswith("3D, 9 slices")


def test_max_projection_merges_the_stacked_nuclei(tmp_path: Path):
    """Why 3D matters: in a projection the two stacked nuclei become one."""

    path = tmp_path / "stack.tif"
    _stack(path)
    controller = AnalysisController.create(tmp_path / "results", "2D")
    controller.add_image_paths([path])
    controller.set_recipe(_recipe("max_projection"))
    result = controller.run_image(controller.experiment.images[0].image_id)
    assert result.labels.ndim == 2 and result.qc.n_objects == 2


def test_z_settings_change_the_recipe_hash_only_in_3d():
    base = load_recipe(_recipe("max_projection"))
    changed = load_recipe(_recipe("max_projection", z_stitch_threshold=0.5))
    assert base.content_hash() == changed.content_hash()  # unused in 2D: no false "mixed settings"
    assert load_recipe(_recipe("stitch_slices")).content_hash() != load_recipe(
        _recipe("stitch_slices", z_stitch_threshold=0.5)
    ).content_hash()


def test_minimum_slices_removes_one_slice_objects():
    volume = np.zeros((5, 30, 30), dtype=np.float32)
    volume[1:4, 5:12, 5:12] = 1000  # three slices
    volume[2, 20:26, 20:26] = 1000  # one slice
    parameters = {"threshold_method": "manual", "threshold": 500}
    kept = segment_volume(volume, "classical", parameters, "stitch_slices")
    assert len(np.unique(kept)) - 1 == 2
    filtered = segment_volume(volume, "classical", parameters, "stitch_slices", min_slices=2)
    assert len(np.unique(filtered)) - 1 == 1


def test_one_slice_and_merged_objects_are_flagged(tmp_path: Path):
    shape = (9, 64, 64)
    nuclear = np.full(shape, 50, dtype=np.uint16)
    # Two nuclei stacked in Z whose tips touch: linked into one object twice as deep as it is wide.
    for center in ((2, 20, 20), (6, 20, 20)):
        nuclear[_ball(shape, center, 3.0)] = 1000
    nuclear[_ball(shape, (4, 44, 44), 2.9)] = 1000  # a normal nucleus
    nuclear[4, 55:60, 5:10] = 1000  # a one-slice speck
    data = np.stack([nuclear, nuclear], axis=1)
    path = tmp_path / "stack.tif"
    tifffile.imwrite(path, data, imagej=True, resolution=(1 / PIXEL, 1 / PIXEL), metadata={"axes": "ZCYX", "unit": "um", "spacing": STEP})
    controller = AnalysisController.create(tmp_path / "results", "Flags")
    controller.add_image_paths([path])
    controller.set_recipe(_recipe("stitch_slices", z_stitch_threshold=0.01))
    result = controller.run_image(controller.experiment.images[0].image_id)
    flags = result.objects.set_index("z_slices")["z_flag"].to_dict()
    assert flags[9] == "possibly_merged"
    assert flags[1] == "one_slice"
    assert any("only one slice" in text for text in result.qc.warnings)
    assert any("deeper than they are wide" in text for text in result.qc.warnings)


def test_size_limits_use_the_largest_cross_section():
    volume = np.zeros((5, 40, 40), dtype=np.float32)
    volume[0:5, 5:15, 5:15] = 1000  # 100 px in every slice, 500 voxels
    volume[2, 25:29, 25:29] = 1000  # 16 px
    labels = segment_volume(volume, "classical", {"threshold_method": "manual", "threshold": 500, "min_area_px": 50}, "full_3d")
    assert len(np.unique(labels)) - 1 == 1


def test_rings_in_3d_respect_the_slice_spacing():
    labels = np.zeros((7, 21, 21), dtype=np.int32)
    labels[3, 10, 10] = 1
    grown = expand_labels(labels, 2.5, z_scale=3.0)  # slices 3 px apart: 2.5 px does not reach the next slice
    assert grown[3].sum() > 1 and grown[2].sum() == 0
    ring = ring_labels(labels, 0.5, 3.5, z_scale=3.0)
    assert ring[2, 10, 10] == 1 and ring[3, 10, 10] == 0


def test_3d_edits_replay_on_the_right_slice():
    before = np.zeros((3, 10, 10), dtype=np.int32)
    before[1, 2:5, 2:5] = 4
    after = before.copy()
    after[1, 2, 2] = 0
    after[2, 7, 7] = 4
    operations = operations_from_diff(before, after, "img")
    assert all("planes" in item.parameters for item in operations)
    assert np.array_equal(apply_edits(before, operations), after)


def test_edits_follow_the_segmentation_they_were_made_on(tmp_path: Path):
    """Object numbers differ between 2D and 3D; a deletion must not hit a different object."""

    path = tmp_path / "stack.tif"
    _stack(path)
    controller = AnalysisController.create(tmp_path / "results", "Edits")
    controller.add_image_paths([path])
    image_id = controller.experiment.images[0].image_id
    controller.set_recipe(_recipe("max_projection"))
    first = controller.run_image(image_id)
    victim = int(first.objects["object_id"].iloc[0])
    deleted = controller.delete_object(image_id, victim)
    assert deleted.qc.n_objects == 1
    controller.set_recipe(_recipe("stitch_slices"))
    in_3d = controller.run_image(image_id)
    assert in_3d.qc.n_objects == 3 and not in_3d.objects["excluded"].any()
    controller.set_recipe(_recipe("max_projection"))
    back = controller.run_image(image_id)
    assert back.qc.n_objects == 1  # the 2D deletion applies again to the 2D segmentation


def test_3d_results_survive_reopening(tmp_path: Path):
    path = tmp_path / "stack.tif"
    _stack(path)
    controller = AnalysisController.create(tmp_path / "results", "Reopen")
    controller.add_image_paths([path])
    controller.set_recipe(_recipe("full_3d"))
    image_id = controller.experiment.images[0].image_id
    controller.run_images()
    again = AnalysisController.open(tmp_path / "results")
    recalled = again.recall(image_id)
    assert recalled is not None and recalled.labels.shape == (9, 64, 64)
    exported = again.export(tmp_path / "export")
    objects = (Path(exported) / "objects.csv").read_text()
    assert "z_slices" in objects.splitlines()[0]


def test_loading_keeps_the_volume_for_3d_modes(tmp_path: Path):
    path = tmp_path / "stack.tif"
    _stack(path)
    assert load_image(path).data.shape == (2, 64, 64)
    volume = load_image(path, z_mode="stitch_slices")
    assert volume.data.shape == (2, 9, 64, 64) and volume.is_3d
    assert volume.spatial_shape == (9, 64, 64)
    assert volume.pixel_size_z == pytest.approx(STEP)


# --- Cellpose in 3D, with stand-ins for both engines -------------------------------------------


def _recording(source: str) -> str:
    """Make the fake record every eval call and return 3D masks for do_3D."""

    return source.replace(
        '''        CALLS.append(("CellposeModel.eval", dict(diameter=diameter, channels=channels)))
        masks = np.zeros(np.asarray(x).shape[-2:], dtype=np.int32)''',
        '''        CALLS.append(("CellposeModel.eval", dict(diameter=diameter, channels=channels, normalize=normalize,
                      do_3D=do_3D, z_axis=z_axis, anisotropy=anisotropy, max=float(np.asarray(x).max()))))
        shape = np.asarray(x).shape if do_3D else np.asarray(x).shape[-2:]
        masks = np.zeros(shape, dtype=np.int32)''',
    ).replace(
        '''        masks[2:8, 2:8] = 1
        masks[12:18, 12:18] = 2''',
        '''        masks[..., 2:8, 2:8] = 1
        masks[..., 12:18, 12:18] = 2''',
    )


@pytest.fixture(autouse=True)
def _clear_engine_cache():
    engines.cellpose_engine.cache_clear()
    yield
    engines.cellpose_engine.cache_clear()


@pytest.mark.parametrize("version,source", [("3.1.1.3", _V3), ("4.2.1.1", _V4)])
def test_cellpose_linked_slices_scale_the_whole_stack_once(tmp_path, monkeypatch, version, source):
    models = _install_fake(tmp_path, monkeypatch, version, _recording(source))
    volume = np.stack([np.full((20, 20), value, dtype=np.float32) for value in (100, 1000, 100)])
    volume[:, 0, 0] = 0
    details: dict = {}
    labels = segment_volume(volume, "cellpose", {"diameter_px": 10}, "stitch_slices", details=details)
    calls = [item[1] for item in models.CALLS if item[0] == "CellposeModel.eval"]
    assert len(calls) == 3  # one per slice
    assert all(call["normalize"] is False and call["do_3D"] is False for call in calls)
    assert calls[0]["max"] < 0.2 < calls[1]["max"]  # the dim slices stay dim
    assert len(np.unique(labels)) - 1 == 2  # the same two outlines in every slice, linked
    assert details["engine"]["engine"] == ("cellpose3" if version.startswith("3") else "cellpose4")


@pytest.mark.parametrize("version,source", [("3.1.1.3", _V3), ("4.2.1.1", _V4)])
def test_cellpose_full_3d_passes_the_slice_spacing(tmp_path, monkeypatch, version, source):
    models = _install_fake(tmp_path, monkeypatch, version, _recording(source))
    volume = np.random.default_rng(0).random((5, 20, 20)).astype(np.float32)
    labels = segment_volume(
        volume, "cellpose", {"diameter_px": 10}, "full_3d", pixel_size_x=0.5, pixel_size_y=0.5, pixel_size_z=1.5
    )
    call = next(item[1] for item in models.CALLS if item[0] == "CellposeModel.eval")
    assert call["do_3D"] is True and call["z_axis"] == 0 and call["anisotropy"] == pytest.approx(3.0)
    assert call["normalize"] is False
    assert labels.shape == (5, 20, 20)


# --- same rule as Cellpose -----------------------------------------------------------------
# Reference: the loop in cellpose 4.2.1.1 utils.stitch3D (BSD-3), with its IoU computed densely.


def _iou_cp(a, b):  # Cellpose metrics._intersection_over_union, dense
    ov = np.zeros((a.max()+1, b.max()+1)); np.add.at(ov, (a.ravel(), b.ravel()), 1)
    n0 = ov.sum(0, keepdims=True); n1 = ov.sum(1, keepdims=True)
    return ov / (n0 + n1 - ov)

def _cellpose_stitch3d(masks, thr):  # Cellpose 4.2.1.1 utils.stitch3D, loop body copied
    masks = masks.copy(); mmax = masks[0].max(); empty = 0
    for i in range(len(masks) - 1):
        iou = _iou_cp(masks[i + 1], masks[i])[1:, 1:]
        if not iou.size and empty == 0:
            masks[i + 1] = masks[i + 1]; mmax = masks[i + 1].max()
        elif not iou.size and not empty == 0:
            icount = masks[i + 1].max()
            istitch = np.arange(mmax + 1, mmax + icount + 1, 1, masks.dtype); mmax += icount
            istitch = np.append(np.array(0), istitch); masks[i + 1] = istitch[masks[i + 1]]
        else:
            iou[iou < thr] = 0.0; iou[iou < iou.max(axis=0)] = 0.0
            istitch = iou.argmax(axis=1) + 1
            ino = np.nonzero(iou.max(axis=1) == 0.0)[0]
            istitch[ino] = np.arange(mmax + 1, mmax + len(ino) + 1, 1, masks.dtype)
            mmax += len(ino); istitch = np.append(np.array(0), istitch)
            masks[i + 1] = istitch[masks[i + 1]]; empty = 1
    return masks

def _same_partition(a, b):
    pairs = set(zip(a.ravel().tolist(), b.ravel().tolist()))
    left = {}; right = {}
    for x, y in pairs:
        if (x == 0) != (y == 0): return False
        if left.setdefault(x, y) != y or right.setdefault(y, x) != x: return False
    return True



def test_linking_groups_outlines_exactly_like_cellpose_stitch3d():
    from scipy import ndimage

    rng = np.random.default_rng(1)
    for _trial in range(60):
        z = int(rng.integers(2, 7))
        field = ndimage.gaussian_filter(rng.random((z, 48, 48)), (0.8, 2.5, 2.5))
        planes = np.stack([ndimage.label(p > np.percentile(p, rng.integers(55, 80)))[0] for p in field]).astype(np.int64)
        threshold = float(rng.choice([0.05, 0.25, 0.5]))
        assert _same_partition(stitch_slices(planes, threshold), _cellpose_stitch3d(planes, threshold))
