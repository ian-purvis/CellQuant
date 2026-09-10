"""Usability review regressions for measurement context, ND2, and z_axis."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from cellquant.analysis import analysis_context_from_dict
from cellquant.config import RunConfig, load_config
from cellquant.contracts import ImageVolume, LabelVolume, MutableCancellationToken
from cellquant.io._nd2 import _axes_calibrated, _spacing
from cellquant.measure import measure_labels
from cellquant.orchestrator import run_measurements


REFERENCE = Path(__file__).resolve().parents[1] / "src" / "cellquant" / "sample_config.yaml"


def _config(**segment_updates):
    raw = deepcopy(load_config(REFERENCE).raw)
    raw["segment"].update(segment_updates)
    return RunConfig(raw)


class _FakeND2:
    """Minimal ND2 handle: only what the reader actually touches."""

    def __init__(self, *, sizes, data, voxel=(1.0, 1.0, 1.0), calibrated=None):
        self.sizes = dict(sizes)
        self._data = data
        self._voxel = voxel
        self.dtype = np.dtype(data.dtype)
        if calibrated is not None:
            self.axesCalibrated = list(calibrated)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def voxel_size(self):
        z, y, x = self._voxel
        return SimpleNamespace(z=z, y=y, x=x)

    @property
    def metadata(self):
        return SimpleNamespace(
            channels=[SimpleNamespace(channel=SimpleNamespace(name="DAPI"))]
        )

    def to_dask(self):
        return self._data

    def asarray(self):
        return self._data


def _install_nd2(monkeypatch, handle):
    import sys
    import types

    module = types.ModuleType("nd2")
    module.ND2File = lambda path: handle
    monkeypatch.setitem(sys.modules, "nd2", module)


def _nd2_path(tmp_path):
    path = tmp_path / "positions.nd2"
    path.write_bytes(b"mocked reader only")
    return path


def test_null_z_axis_normalizes_for_volume_and_stitch():
    for mode, stitch in (("volume_3d", 0.0), ("stitch_2d", 0.25)):
        raw = deepcopy(load_config(REFERENCE).raw)
        raw["segment"].update(mode=mode, z_index=None, z_axis=None, stitch_threshold=stitch)
        config = RunConfig(raw)
        assert config.raw["segment"]["z_axis"] == 0


def test_run_measurements_uses_label_plane_not_live_config():
    image = np.zeros((2, 4, 5, 1), dtype=np.float32)
    image[0] = 10
    image[1] = 90
    volume = ImageVolume(image, (1.0, 0.5, 0.5), ("DAPI",), Path("plane.tif"))
    labels = np.zeros((1, 4, 5), dtype=np.uint32)
    labels[0, 1:3, 1:4] = 1
    context = analysis_context_from_dict(
        {
            "mode": "single_plane_2d",
            "original_z_depth": 2,
            "z_selection": {"kind": "single_plane", "z_index": 0},
        },
        spacing_um=(1.0, 0.5, 0.5),
        shape_zyx=(1, 4, 5),
    )
    mask = LabelVolume(
        labels,
        (1.0, 0.5, 0.5),
        {"analysis_volume": {
            "mode": context.mode,
            "original_z_depth": context.original_z_depth,
            "z_selection": context.z_selection,
        }},
    )
    # Live config asks for plane 1; labels were from plane 0.
    config = _config(mode="single_plane_2d", z_index=1, z_axis=None, stitch_threshold=0.0)
    tables = run_measurements(volume, mask, config, MutableCancellationToken())
    assert tables.intensities.iloc[0]["mean"] == pytest.approx(10.0)


def test_uncalibrated_nd2_spacing_is_unknown():
    handle = SimpleNamespace(
        voxel_size=lambda: SimpleNamespace(z=1.0, y=1.0, x=1.0),
        axesCalibrated=[False, False, False],
    )
    assert _axes_calibrated(handle) is False
    assert _spacing(handle) == (None, None, None)


def test_ambiguous_unit_spacing_without_flag_is_unknown():
    handle = SimpleNamespace(
        voxel_size=lambda: SimpleNamespace(z=1.0, y=1.0, x=1.0),
    )
    assert _spacing(handle) == (None, None, None)


def test_calibrated_nd2_spacing_is_trusted():
    handle = SimpleNamespace(
        voxel_size=lambda: SimpleNamespace(z=2.0, y=0.5, x=0.5),
        axesCalibrated=[True, True, True],
    )
    assert _spacing(handle) == (2.0, 0.5, 0.5)


def test_read_nd2_details_include_position(monkeypatch):
    from cellquant.io import _nd2

    class _Handle:
        dtype = np.dtype(np.uint16)
        sizes = {"P": 2, "Z": 1, "Y": 2, "X": 2, "C": 1}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def voxel_size(self):
            return SimpleNamespace(z=1.0, y=0.5, x=0.5)

        @property
        def axesCalibrated(self):
            return [True, True, True]

        @property
        def metadata(self):
            return SimpleNamespace(channels=[SimpleNamespace(channel=SimpleNamespace(name="DAPI"))])

        def to_dask(self):
            return np.zeros((2, 1, 2, 2, 1), dtype=np.uint16)

        def asarray(self):
            return np.zeros((2, 1, 2, 2, 1), dtype=np.uint16)

    monkeypatch.setattr(_nd2, "nd2", SimpleNamespace(ND2File=lambda path: _Handle()), raising=False)
    # Bypass import nd2 inside function by patching the module attribute after import.
    import sys
    import types

    fake = types.ModuleType("nd2")
    fake.ND2File = lambda path: _Handle()
    monkeypatch.setitem(sys.modules, "nd2", fake)
    data, axes, spacing, names, details = _nd2.read_nd2(Path("x.nd2"), position=1, lazy=False)
    assert details["position"] == 1
    assert details["position_count"] == 2
    assert details["series"] == 0
    assert spacing == (1.0, 0.5, 0.5)


def test_two_dimensional_modes_keep_null_z_axis():
    for mode, z_index in (("single_plane_2d", 0), ("max_projection_2d", None)):
        config = _config(mode=mode, z_index=z_index, z_axis=None, stitch_threshold=0.0)
        assert config.raw["segment"]["z_axis"] is None


def test_channel_volume_calibration_flag_is_read():
    # Shape reported by the installed nd2 package for uncalibrated files.
    handle = SimpleNamespace(
        voxel_size=lambda: SimpleNamespace(z=1.0, y=1.0, x=1.0),
        metadata=SimpleNamespace(
            channels=[
                SimpleNamespace(volume=SimpleNamespace(axesCalibrated=[False, False, False]))
            ]
        ),
    )
    assert _axes_calibrated(handle) is False
    assert _spacing(handle) == (None, None, None)


def test_uncalibrated_nd2_requires_override_and_keeps_reader_values(monkeypatch, tmp_path):
    from cellquant.io import open_volume

    path = _nd2_path(tmp_path)
    pixels = np.zeros((1, 2, 2, 1), dtype=np.uint16)
    handle = _FakeND2(
        sizes={"Z": 1, "Y": 2, "X": 2, "C": 1},
        data=pixels,
        calibrated=[False, False, False],
    )
    _install_nd2(monkeypatch, handle)

    with pytest.raises(ValueError, match="spacing_override_um"):
        open_volume(path, lazy=False)

    volume = open_volume(path, lazy=False, spacing_override_um=(2.0, 0.5, 0.5))
    assert volume.spacing_um == (2.0, 0.5, 0.5)
    assert volume.metadata["calibration_status"] == "explicit_override"
    assert volume.metadata["spacing_source"] == "override"
    # The untrusted reader values stay visible without being treated as physical.
    assert volume.metadata["reader_voxel_size_um"] == (1.0, 1.0, 1.0)
    assert volume.metadata["detected_spacing_um"] == (None, None, None)


def test_calibrated_nd2_is_labeled_as_metadata(monkeypatch, tmp_path):
    from cellquant.io import open_volume

    path = _nd2_path(tmp_path)
    handle = _FakeND2(
        sizes={"Z": 1, "Y": 2, "X": 2, "C": 1},
        data=np.zeros((1, 2, 2, 1), dtype=np.uint16),
        voxel=(2.0, 0.5, 0.5),
        calibrated=[True, True, True],
    )
    _install_nd2(monkeypatch, handle)

    volume = open_volume(path, lazy=False)
    assert volume.spacing_um == (2.0, 0.5, 0.5)
    assert volume.metadata["calibration_status"] == "calibrated"
    assert volume.metadata["spacing_source"] == "metadata"


def test_two_positions_are_self_identifying(monkeypatch, tmp_path):
    from cellquant.io import open_volume

    path = _nd2_path(tmp_path)
    pixels = np.zeros((2, 2, 2), dtype=np.uint16)
    pixels[1] = 99
    handle = _FakeND2(
        sizes={"P": 2, "Y": 2, "X": 2},
        data=pixels,
        voxel=(2.0, 0.5, 0.5),
        calibrated=[True, True, True],
    )
    _install_nd2(monkeypatch, handle)

    first = open_volume(path, position=0, lazy=False)
    second = open_volume(path, position=1, lazy=False)

    assert float(np.asarray(first.data).mean()) == 0.0
    assert float(np.asarray(second.data).mean()) == 99.0
    assert first.metadata["position"] == 0
    assert second.metadata["position"] == 1
    assert first.metadata["position_count"] == second.metadata["position_count"] == 2
    assert first.metadata["series"] == second.metadata["series"] == 0
    assert first.metadata["series_count"] == 1
    assert first.metadata != second.metadata


def test_unavailable_position_fails_clearly(monkeypatch, tmp_path):
    from cellquant.io import open_volume

    path = _nd2_path(tmp_path)
    handle = _FakeND2(
        sizes={"P": 2, "Y": 2, "X": 2},
        data=np.zeros((2, 2, 2), dtype=np.uint16),
        voxel=(2.0, 0.5, 0.5),
        calibrated=[True, True, True],
    )
    _install_nd2(monkeypatch, handle)

    with pytest.raises(IndexError, match="position 5 is unavailable"):
        open_volume(path, position=5, lazy=False)


def _plane_zero_labels():
    labels = np.zeros((1, 4, 5), dtype=np.uint32)
    labels[0, 1:3, 1:4] = 1
    return LabelVolume(
        labels,
        (1.0, 0.5, 0.5),
        {
            "analysis_volume": {
                "mode": "single_plane_2d",
                "original_z_depth": 2,
                "z_selection": {"kind": "single_plane", "z_index": 0},
            }
        },
    )


def _two_plane_volume():
    image = np.zeros((2, 4, 5, 1), dtype=np.float32)
    image[0] = 10
    image[1] = 90
    return ImageVolume(image, (1.0, 0.5, 0.5), ("DAPI",), Path("plane.tif"))


def test_measurement_tables_record_the_measured_grid():
    config = _config(mode="single_plane_2d", z_index=1, z_axis=None, stitch_threshold=0.0)
    tables = run_measurements(
        _two_plane_volume(), _plane_zero_labels(), config, MutableCancellationToken()
    )
    for table in (tables.objects, tables.intensities):
        identity = table.attrs["analysis_context"]
        assert identity["mode"] == "single_plane_2d"
        assert identity["z_selection"] == {"kind": "single_plane", "z_index": 0}
        assert identity["shape_zyx"] == [1, 4, 5]
        assert "single plane Z=0" in identity["summary"]


def test_label_grid_that_cannot_come_from_the_image_is_rejected():
    labels = LabelVolume(
        np.ones((1, 4, 5), dtype=np.uint32),
        (1.0, 0.5, 0.5),
        {
            "analysis_volume": {
                "mode": "single_plane_2d",
                "original_z_depth": 7,
                "z_selection": {"kind": "single_plane", "z_index": 6},
            }
        },
    )
    config = _config(mode="single_plane_2d", z_index=0, z_axis=None, stitch_threshold=0.0)
    with pytest.raises(ValueError, match="original_z_depth"):
        run_measurements(_two_plane_volume(), labels, config, MutableCancellationToken())


def test_measure_panel_summary_prefers_mask_provenance():
    from cellquant.plugin.controller import _measurement_grid_summary

    config = _config(mode="single_plane_2d", z_index=1, z_axis=None, stitch_threshold=0.0)
    summary = _measurement_grid_summary(_two_plane_volume(), _plane_zero_labels(), config)
    assert "single plane Z=0" in summary

    unprovenanced = LabelVolume(np.ones((1, 4, 5), dtype=np.uint32), (1.0, 0.5, 0.5))
    assumed = _measurement_grid_summary(_two_plane_volume(), unprovenanced, config)
    assert "single plane Z=1" in assumed


def test_measurements_without_provenance_warn_that_the_grid_is_assumed():
    config = _config(mode="single_plane_2d", z_index=1, z_axis=None, stitch_threshold=0.0)
    labels = LabelVolume(np.ones((1, 4, 5), dtype=np.uint32), (1.0, 0.5, 0.5))
    events = []
    tables = run_measurements(
        _two_plane_volume(), labels, config, MutableCancellationToken(), events.append
    )
    assert tables.intensities.iloc[0]["mean"] == pytest.approx(90.0)
    assert any(
        event.kind == "warning" and "provenance" in str(event.details.get("message", ""))
        for event in events
    )
