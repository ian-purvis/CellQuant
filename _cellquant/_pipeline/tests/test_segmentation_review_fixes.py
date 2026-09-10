"""Segmentation survey planning, inclusion, and diameter regressions."""

from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest

from cellquant.config import RunConfig, load_config
from cellquant.contracts import LabelVolume
from cellquant.io._common import ImageMetadata
from cellquant.measure import measure_labels
from cellquant.plugin.diameter import line_length_px
from cellquant.postprocess import filter_size, remove_border_labels
from cellquant.segment import require_linking_stitch_threshold
from cellquant.survey import (
    ChannelLayout,
    SurveyResult,
    plan_survey_batch_items,
    read_assignments_csv,
    survey_folder,
    with_assignments,
)


def _meta(names, path=Path("x.nd2"), spacing=(1.0, 0.5, 0.5)):
    return ImageMetadata(
        source=path,
        format="nd2",
        shape=(2, 8, 8, len(names)),
        dtype=np.dtype(np.uint16),
        axes="ZYXC",
        spacing_um=spacing,
        channel_names=tuple(names),
    )


def test_with_assignments_complete_excludes_omitted_layouts(tmp_path):
    root = tmp_path / "in"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir(parents=True)
    (root / "a" / "one.nd2").write_bytes(b"x")
    (root / "b" / "two.nd2").write_bytes(b"y")

    def inspect(path):
        names = ("DAPI", "488") if "one" in path.name else ("DAPI", "488", "647")
        return _meta(names, path=path)

    survey = survey_folder(root, suffixes=[".nd2"], inspect_fn=inspect)
    assert len(survey.layouts) == 2
    both = {layout.layout_id: 0 for layout in survey.layouts}
    assigned = with_assignments(survey, both)
    assert all(layout.segment_channel == 0 for layout in assigned.layouts)
    keep = assigned.layouts[0].layout_id
    partial = with_assignments(assigned, {keep: 0})
    channels = {layout.layout_id: layout.segment_channel for layout in partial.layouts}
    assert channels[keep] == 0
    assert all(value is None for key, value in channels.items() if key != keep)


def test_read_assignments_csv_include_no_excludes_when_applied(tmp_path):
    root = tmp_path / "in"
    root.mkdir()
    (root / "one.nd2").write_bytes(b"x")

    def inspect(path):
        return _meta(("DAPI", "488"), path=path)

    survey = survey_folder(root, suffixes=[".nd2"], inspect_fn=inspect)
    layout_id = survey.layouts[0].layout_id
    pre = with_assignments(survey, {layout_id: 0})
    path = tmp_path / "assignments.csv"
    path.write_text(
        "layout_id,channel_names,file_count,segment_channel,segment_channel_1based,include\n"
        f"{layout_id},DAPI | 488,1,,1,no\n",
        encoding="utf-8",
    )
    assignments = read_assignments_csv(path)
    assert assignments == {}
    excluded = with_assignments(pre, assignments)
    assert excluded.layouts[0].segment_channel is None


def test_plan_survey_batch_items_distinct_for_same_basename(tmp_path):
    a = tmp_path / "specimen_A" / "image.tif"
    b = tmp_path / "specimen_B" / "image.tif"
    a.parent.mkdir(parents=True)
    b.parent.mkdir(parents=True)
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    survey = SurveyResult(
        schema_version=1,
        surveyed_utc="2026-01-01T00:00:00Z",
        input_root=str(tmp_path),
        recursive=True,
        suffixes=(".tif",),
        records=(),
        layouts=(
            ChannelLayout(
                layout_id="L2-a",
                channel_count=1,
                channel_names=("C1",),
                file_count=1,
                sources=(str(a),),
                suggested_channel=0,
                segment_channel=0,
            ),
            ChannelLayout(
                layout_id="L2-b",
                channel_count=1,
                channel_names=("C1",),
                file_count=1,
                sources=(str(b),),
                suggested_channel=0,
                segment_channel=0,
            ),
        ),
        error_count=0,
    )
    forward = plan_survey_batch_items(survey, tmp_path / "out")
    reversed_survey = SurveyResult(
        schema_version=survey.schema_version,
        surveyed_utc=survey.surveyed_utc,
        input_root=survey.input_root,
        recursive=survey.recursive,
        suffixes=survey.suffixes,
        records=survey.records,
        layouts=tuple(reversed(survey.layouts)),
        error_count=0,
    )
    backward = plan_survey_batch_items(reversed_survey, tmp_path / "out")
    forward_dirs = {item.output_dir for _, _, item in forward}
    backward_dirs = {item.output_dir for _, _, item in backward}
    assert len(forward_dirs) == 2
    assert forward_dirs == backward_dirs
    assert all(path.name.endswith("image.tif.cellquant") for path in forward_dirs)


REFERENCE = Path(__file__).parents[1] / "reference_config.yaml"


def _plane_mask(mode, z_spacing):
    data = np.zeros((1, 4, 4), dtype=np.uint32)
    data[0, 1:3, 1:3] = 5
    provenance = {
        "analysis_volume": {
            "mode": mode,
            "original_z_depth": 4,
            "z_selection": (
                {"kind": "maximum_projection", "z_start": 0, "z_stop_exclusive": 4}
                if mode == "max_projection_2d"
                else {"kind": "single_plane", "z_index": 0}
            ),
        }
    }
    return LabelVolume(data, (z_spacing, 0.5, 0.5), provenance)


@pytest.mark.parametrize("threshold", [0, 0.0, -0.25, 1.5])
def test_stitch_threshold_zero_and_out_of_range_rejected_by_config(threshold):
    raw = deepcopy(load_config(REFERENCE).raw)
    raw["segment"].update(mode="stitch_2d", stitch_threshold=threshold)
    with pytest.raises(ValueError, match="stitch_threshold"):
        RunConfig(raw)


@pytest.mark.parametrize("threshold", [0, 0.0, -0.25, 1.5])
def test_require_linking_stitch_threshold_rejects_non_linking_values(threshold):
    with pytest.raises(ValueError, match="stitch_threshold"):
        require_linking_stitch_threshold(threshold)


@pytest.mark.parametrize("mode", ["single_plane_2d", "max_projection_2d"])
def test_two_dimensional_measurements_ignore_source_z_spacing(mode):
    from cellquant.contracts import ImageVolume

    areas = []
    for z_spacing in (1.0, 5.0):
        labels = _plane_mask(mode, z_spacing)
        image = ImageVolume(
            np.ones((1, 4, 4, 1), dtype=np.uint16), labels.spacing_um, ("DAPI",), Path("p.tif")
        )
        row = measure_labels(labels, image).objects.iloc[0]
        assert row.voxel_count == 4
        assert np.isnan(row.volume_um3)
        areas.append(row.area_um2)
    assert areas == [1.0, 1.0]


@pytest.mark.parametrize("mode", ["single_plane_2d", "max_projection_2d"])
def test_two_dimensional_size_filter_treats_volume_keys_as_area(mode):
    decisions = []
    for z_spacing in (1.0, 5.0):
        result = filter_size(
            _plane_mask(mode, z_spacing),
            {"min_volume_um3": 0.5, "max_volume_um3": None,
             "min_voxels": None, "max_voxels": None},
        )
        assert result.provenance["size_filter_metric"] == "area_um2"
        decisions.append(5 in np.unique(result.data))
    assert decisions == [True, True]


@pytest.mark.parametrize("face", ["z0", "z1"])
def test_z_border_faces_rejected_for_two_dimensional_masks(face):
    with pytest.raises(ValueError, match="remove_border_faces"):
        remove_border_labels(_plane_mask("max_projection_2d", 2.0), [face])


def test_z_border_faces_rejected_for_singleton_z_without_provenance():
    labels = LabelVolume(np.ones((1, 3, 3), dtype=np.uint32), (2.0, 0.5, 0.5))
    with pytest.raises(ValueError, match="single Z plane"):
        remove_border_labels(labels, ["z0"])


def test_diameter_converts_world_coords_through_image_scale():
    # 10-pixel object at 0.5 µm/px spans 5 world units.
    vertices = [[0.0, 0.0], [0.0, 5.0]]
    assert line_length_px(vertices, scale_xy=(0.5, 0.5)) == pytest.approx(10.0)
    assert line_length_px(vertices, scale_xy=(1.0, 1.0)) == pytest.approx(5.0)
    assert line_length_px([[0.0, 0.0], [2.0, 4.0]], scale_xy=(0.5, 1.0)) == pytest.approx(
        np.hypot(2.0 / 0.5, 4.0 / 1.0)
    )
