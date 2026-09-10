import numpy as np
import pytest

from cellquant.contracts import LabelVolume
from cellquant.postprocess import filter_size, merge_labels, relabel, remove_border_labels, split_label


def _labels():
    data = np.zeros((3, 6, 7), dtype=np.uint32)
    data[0:2, 0:2, 0:2] = 2
    data[1:3, 3:6, 3:7] = 9
    data[1, 2, 2] = 20
    return LabelVolume(data, (2, 0.5, 0.5))


def _plane_labels(mode, z_spacing):
    """A four-pixel interior mask on a singleton-Z analysis grid (area 1 um^2)."""

    data = np.zeros((1, 4, 4), dtype=np.uint32)
    data[0, 1:3, 1:3] = 5
    provenance = {
        "analysis_volume": {
            "mode": mode,
            "original_z_depth": 4,
            "z_selection": {"kind": "single_plane", "z_index": 0},
        }
    }
    return LabelVolume(data, (z_spacing, 0.5, 0.5), provenance)


def test_volume_and_voxel_filters_are_distinct():
    result = filter_size(_labels(), {"min_volume_um3": 2, "max_volume_um3": None,
                                     "min_voxels": None, "max_voxels": None})
    assert set(np.unique(result.data)) == {0, 2, 9}
    assert result.provenance["size_filter_metric"] == "volume_um3"


@pytest.mark.parametrize("mode", ["single_plane_2d", "max_projection_2d"])
@pytest.mark.parametrize(
    ("threshold", "survives"), [(2, False), (0.5, True)]
)
def test_two_dimensional_size_filter_ignores_source_z_spacing(mode, threshold, survives):
    decisions = []
    for z_spacing in (1.0, 5.0):
        result = filter_size(
            _plane_labels(mode, z_spacing),
            {"min_volume_um3": threshold, "max_volume_um3": None,
             "min_voxels": None, "max_voxels": None},
        )
        assert result.provenance["size_filter_metric"] == "area_um2"
        decisions.append(5 in np.unique(result.data))
    assert decisions == [survives, survives]


def test_explicit_area_thresholds_win_over_legacy_volume_keys():
    result = filter_size(
        _plane_labels("max_projection_2d", 5.0),
        {"min_volume_um3": 2, "max_volume_um3": None, "min_area_um2": 0.5,
         "max_area_um2": None, "min_voxels": None, "max_voxels": None},
    )
    assert 5 in np.unique(result.data)


def test_border_faces_are_explicit():
    result = remove_border_labels(_labels(), ["z0"])
    assert 2 not in np.unique(result.data) and 9 in np.unique(result.data)
    with pytest.raises(ValueError, match="unknown"):
        remove_border_labels(_labels(), ["all"])


@pytest.mark.parametrize("face", ["z0", "z1"])
def test_z_border_faces_are_rejected_for_two_dimensional_masks(face):
    labels = _plane_labels("max_projection_2d", 2.0)
    with pytest.raises(ValueError, match="remove_border_faces"):
        remove_border_labels(labels, [face, "y0"])
    assert 5 in np.unique(labels.data)


def test_z_border_faces_are_rejected_for_masks_with_a_single_plane():
    labels = LabelVolume(np.ones((1, 3, 3), dtype=np.uint32), (2.0, 0.5, 0.5))
    with pytest.raises(ValueError, match="single Z plane"):
        remove_border_labels(labels, ["z1"])
    assert remove_border_labels(labels, ["y0"]).data.max() == 0


def test_relabel_merge_and_split_preserve_uint32():
    compact = relabel(_labels())
    assert set(np.unique(compact.data)) == {0, 1, 2, 3}
    merged = merge_labels(compact, [(1, 3)])
    assert set(np.unique(merged.data)) == {0, 1, 2}
    parts = np.zeros_like(merged.data)
    coords = np.argwhere(merged.data == 1)
    for index, coord in enumerate(coords):
        parts[tuple(coord)] = 1 if index < len(coords) // 2 else 2
    split = split_label(merged, {"label": 1, "parts": parts})
    assert split.data.dtype == np.uint32
    assert len(np.unique(split.data[split.data > 0])) == 3

