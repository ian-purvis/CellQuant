from pathlib import Path

import numpy as np
import pytest
import tifffile

from cellquant.io import inspect_volume, iter_supported_files, open_volume
from cellquant.io._common import normalize_axes


def test_ome_tiff_is_normalized_to_zyxc_with_calibration(tmp_path):
    path = tmp_path / "two-channel.ome.tif"
    source = np.arange(3 * 2 * 8 * 9, dtype=np.uint16).reshape(3, 2, 8, 9)
    tifffile.imwrite(
        path,
        source,
        ome=True,
        photometric="minisblack",
        metadata={
            "axes": "ZCYX",
            "PhysicalSizeZ": 1.5,
            "PhysicalSizeZUnit": "um",
            "PhysicalSizeY": 0.4,
            "PhysicalSizeYUnit": "um",
            "PhysicalSizeX": 400,
            "PhysicalSizeXUnit": "nm",
            "Channel": {"Name": ["DAPI", "GFP"]},
        },
    )
    metadata = inspect_volume(path)
    volume = open_volume(path, lazy=False)
    assert metadata.axes == "ZCYX"
    assert volume.data.shape == (3, 8, 9, 2)
    assert volume.data.dtype == np.uint16
    assert volume.spacing_um == pytest.approx((1.5, 0.4, 0.4))
    assert volume.channel_names == ("DAPI", "GFP")
    np.testing.assert_array_equal(volume.data, np.moveaxis(source, 1, -1))


def test_uncalibrated_tiff_fails_without_override_and_preserves_dtype(tmp_path):
    path = tmp_path / "stack.tif"
    source = np.zeros((4, 7, 9), dtype=np.uint16)
    tifffile.imwrite(path, source, photometric="minisblack", metadata={"axes": "ZYX"})
    with pytest.raises(ValueError, match="provide spacing_override_um"):
        open_volume(path, lazy=False)
    volume = open_volume(path, lazy=True, spacing_override_um=(2, 0.5, 0.5))
    assert volume.data.shape == (4, 7, 9, 1)
    assert volume.data.dtype == source.dtype
    assert volume.metadata["lazy_requested"] is True


def test_axis_contract_is_strict_and_position_is_explicit():
    data = np.zeros((2, 3, 4, 5, 1), dtype=np.uint8)
    normalized, _ = normalize_axes(data, "PZYXC", position=1)
    assert normalized.shape == (3, 4, 5, 1)
    with pytest.raises(ValueError, match="time points"):
        normalize_axes(np.zeros((2, 4, 5)), "TYX")
    with pytest.raises(ValueError, match="unknown"):
        normalize_axes(np.zeros((2, 4, 5)), "QYX")
    with pytest.raises(IndexError, match="no position axis"):
        normalize_axes(np.zeros((4, 5)), "YX", position=1)


def test_supported_file_iteration_is_stable_and_filters_other_files(tmp_path):
    (tmp_path / "b.TIF").write_bytes(b"x")
    (tmp_path / "A.nd2").write_bytes(b"x")
    (tmp_path / "notes.csv").write_text("x")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "c.ome.tiff").write_bytes(b"x")
    assert [p.name for p in iter_supported_files(tmp_path)] == ["A.nd2", "b.TIF"]
    assert [p.name for p in iter_supported_files(tmp_path, recursive=True)] == ["A.nd2", "b.TIF", "c.ome.tiff"]
    assert [p.name for p in iter_supported_files(tmp_path, recursive=True, suffixes=[".nd2"])] == ["A.nd2"]
    assert [p.name for p in iter_supported_files(tmp_path, recursive=True, suffixes=[".tif", ".tiff"])] == [
        "b.TIF",
        "c.ome.tiff",
    ]
