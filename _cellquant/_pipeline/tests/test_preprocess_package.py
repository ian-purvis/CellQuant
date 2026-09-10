from pathlib import Path

import numpy as np
import pytest

from cellquant.contracts import ImageVolume, MutableCancellationToken, PipelineCancelled
from cellquant.preprocess import (
    normalize,
    prepare_analysis_volume,
    rescale,
    run_preprocess,
    select_channel,
)


def _volume():
    data = np.arange(4 * 8 * 10 * 2, dtype=np.uint16).reshape(4, 8, 10, 2)
    return ImageVolume(data, (2.0, 0.5, 0.5), ("DAPI", "GFP"), Path("x.tif"))


class LazyArray:
    def __init__(self, data, calls=None):
        self._data = data
        self.shape = data.shape
        self.dtype = data.dtype
        self.calls = calls if calls is not None else []

    def __getitem__(self, item):
        return LazyArray(self._data[item], self.calls)

    def compute(self):
        self.calls.append(self.shape)
        return self._data


def test_reference_noop_is_value_preserving_float32_single_channel():
    volume = _volume()
    config = {"preprocess": {"channel": 0, "normalize": {"enabled": False},
                              "rescale": {"enabled": False}, "denoise": {"enabled": False}}}
    result = run_preprocess(volume, config)
    assert result.data.shape == (4, 8, 10, 1)
    assert result.data.dtype == np.float32
    np.testing.assert_array_equal(result.data[..., 0], volume.data[..., 0])


def test_normalization_is_explicit_and_bounded():
    result = normalize(select_channel(_volume(), 0), {"enabled": True, "low_percentile": 0,
                                                       "high_percentile": 100, "scope": "volume"})
    assert result.data.min() == 0 and result.data.max() == 1
    with pytest.raises(ValueError, match="scope"):
        normalize(select_channel(_volume(), 0), {"enabled": True, "low_percentile": 1,
                                                  "high_percentile": 99, "scope": None})


def test_rescale_updates_shape_and_spacing_without_channel_mixing():
    one = select_channel(_volume(), 1)
    result = rescale(one, {"enabled": True, "target_spacing_um": [1, 1, 1],
                           "interpolation": "nearest", "antialias": False,
                           "boundary": "constant", "cval": 0.0})
    assert result.data.shape == (8, 4, 5, 1)
    assert result.spacing_um == (1, 1, 1)


def test_channel_errors_are_not_clamped():
    with pytest.raises(IndexError):
        select_channel(_volume(), 2)


def test_lazy_reference_preprocess_emits_materialization_contract():
    original = _volume()
    lazy = LazyArray(original.data)
    volume = ImageVolume(
        lazy,
        original.spacing_um,
        original.channel_names,
        original.source,
        {"run_id": "r1", "file_id": "f1"},
    )
    config = {"preprocess": {"channel": 0, "normalize": {"enabled": False},
                              "rescale": {"enabled": False}, "denoise": {"enabled": False}}}
    events = []
    result = run_preprocess(volume, config, events=events.append)
    assert result.data.dtype == np.float32
    assert lazy.calls == [(4, 8, 10, 1)]
    assert len(events) == 1 and events[0].kind == "materialized"
    assert events[0].run_id == "r1" and events[0].file_id == "f1"
    assert events[0].details["shape"] == [4, 8, 10, 1]
    assert events[0].details["dtype"] == "uint16"
    assert "eager" in events[0].details["reason"]


def test_rescale_boundary_is_explicit_and_cancellation_precedes_work():
    one = select_channel(_volume(), 0)
    with pytest.raises(ValueError, match="boundary"):
        rescale(one, {"enabled": True, "target_spacing_um": [1, 1, 1],
                      "interpolation": "nearest", "antialias": False,
                      "boundary": None, "cval": 0.0})
    token = MutableCancellationToken()
    token.cancel()
    config = {"preprocess": {"channel": 0, "normalize": {"enabled": False},
                              "rescale": {"enabled": False}, "denoise": {"enabled": False}}}
    with pytest.raises(PipelineCancelled):
        run_preprocess(_volume(), config, cancel=token)


def test_single_plane_analysis_keeps_all_channels_and_records_zero_based_plane():
    result = prepare_analysis_volume(
        _volume(), {"segment": {"mode": "single_plane_2d", "z_index": 2}}
    )
    assert result.data.shape == (1, 8, 10, 2)
    np.testing.assert_array_equal(result.data[0], _volume().data[2])
    assert result.metadata["analysis_volume"] == {
        "mode": "single_plane_2d",
        "original_z_depth": 4,
        "z_selection": {"kind": "single_plane", "z_index": 2},
    }
    with pytest.raises(IndexError, match="outside Z axis"):
        prepare_analysis_volume(
            _volume(), {"segment": {"mode": "single_plane_2d", "z_index": 4}}
        )


def test_max_projection_analysis_projects_every_channel_without_channel_mixing():
    result = prepare_analysis_volume(
        _volume(), {"segment": {"mode": "max_projection_2d", "z_index": None}}
    )
    assert result.data.shape == (1, 8, 10, 2)
    np.testing.assert_array_equal(result.data[0], np.max(_volume().data, axis=0))
    assert result.metadata["analysis_volume"]["z_selection"] == {
        "kind": "maximum_projection",
        "z_start": 0,
        "z_stop_exclusive": 4,
    }


def test_2d_analysis_rescale_changes_only_yx_and_retains_source_plane_thickness():
    analysis = prepare_analysis_volume(
        _volume(), {"segment": {"mode": "single_plane_2d", "z_index": 1}}
    )
    result = rescale(
        analysis,
        {
            "enabled": True,
            "target_spacing_um": [0.5, 1.0, 1.0],
            "interpolation": "nearest",
            "antialias": False,
            "boundary": "constant",
            "cval": 0.0,
        },
    )
    assert result.data.shape == (1, 4, 5, 2)
    assert result.spacing_um == (2.0, 1.0, 1.0)
