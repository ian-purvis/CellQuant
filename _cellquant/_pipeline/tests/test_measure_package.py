from pathlib import Path

import numpy as np
import pytest

from cellquant.contracts import ImageVolume, LabelVolume
from cellquant.measure import MEASURE_SCHEMA_VERSION, measure_labels, write_measurements


def _data(empty=False):
    image = np.arange(2 * 4 * 5 * 2, dtype=np.uint16).reshape(2, 4, 5, 2)
    labels = np.zeros((2, 4, 5), dtype=np.uint32)
    if not empty:
        labels[:, 1:3, 2:5] = 7
    spacing = (2.0, 0.5, 0.25)
    return (LabelVolume(labels, spacing),
            ImageVolume(image, spacing, ("DAPI", "GFP"), Path("x.tif")))


def _plane_data(mode, z_spacing, z_depth=4):
    """A four-pixel interior mask on a singleton-Z analysis grid."""

    mask = np.zeros((1, 4, 4), dtype=np.uint32)
    mask[0, 1:3, 1:3] = 5
    spacing = (z_spacing, 0.5, 0.5)
    z_selection = (
        {"kind": "maximum_projection", "z_start": 0, "z_stop_exclusive": z_depth}
        if mode == "max_projection_2d"
        else {"kind": "single_plane", "z_index": 0}
    )
    provenance = {
        "analysis_volume": {
            "mode": mode,
            "original_z_depth": z_depth,
            "z_selection": z_selection,
        }
    }
    return (
        LabelVolume(mask, spacing, provenance),
        ImageVolume(np.ones((1, 4, 4, 1), dtype=np.uint16), spacing, ("DAPI",), Path("p.tif")),
    )


def test_morphometrics_use_physical_units_and_stable_ids():
    labels, image = _data()
    tables = measure_labels(labels, image)
    row = tables.objects.iloc[0]
    assert row.label == 7 and row.voxel_count == 12
    assert row.volume_um3 == 12 * 2 * 0.5 * 0.25
    assert np.isnan(row.area_um2)
    assert tables.objects.attrs["schema_version"] == MEASURE_SCHEMA_VERSION
    assert tables.objects.attrs["extent_metric"] == "volume_um3"
    assert row.centroid_z_um == 0.5 * 2
    assert row.bbox_z0_px == 0 and row.bbox_z0_um == 0
    assert row.bbox_y0_px == 1 and row.bbox_y0_um == 0.5
    assert row.bbox_x1_px == 5 and row.bbox_x1_um == 1.25
    assert set(tables.intensities.channel) == {"DAPI", "GFP"}
    assert set(tables.intensities.label) == {7}


def test_empty_labels_produce_header_only_valid_tables_and_warning(tmp_path):
    labels, image = _data(empty=True)
    seen = []
    tables = measure_labels(labels, image, events=seen.append)
    assert tables.objects.empty and tables.intensities.empty
    assert len(seen) == 1
    assert seen[0].kind == "warning" and seen[0].stage == "measure"
    assert "header-only" in seen[0].details["message"]
    paths = write_measurements(tables, tmp_path)
    assert all(path.read_text().strip() for path in paths)


def test_config_selects_channels_and_statistics():
    labels, image = _data()
    tables = measure_labels(labels, image, {
        "channels": ["GFP"],
        "intensity_statistics": ["mean", "integrated_intensity"],
    })
    assert set(tables.intensities.channel) == {"GFP"}
    assert list(tables.intensities.columns) == [
        "label", "channel", "voxel_count", "mean", "integrated_intensity"
    ]


@pytest.mark.parametrize("mode", ["single_plane_2d", "max_projection_2d"])
def test_two_dimensional_modes_report_area_independent_of_source_z_spacing(mode):
    areas = []
    for z_spacing in (1.0, 5.0):
        labels, image = _plane_data(mode, z_spacing)
        tables = measure_labels(labels, image)
        row = tables.objects.iloc[0]
        assert row.voxel_count == 4
        assert np.isnan(row.volume_um3)
        assert tables.objects.attrs["extent_metric"] == "area_um2"
        areas.append(row.area_um2)
    assert areas == [4 * 0.5 * 0.5, 4 * 0.5 * 0.5]


def test_declared_context_overrides_mask_provenance_for_dimensionality():
    from cellquant.contracts import AnalysisContext

    labels, image = _data()
    context = AnalysisContext(
        mode="max_projection_2d",
        z_selection={"kind": "maximum_projection", "z_start": 0, "z_stop_exclusive": 2},
        original_z_depth=2,
        spacing_um=labels.spacing_um,
        shape_zyx=labels.data.shape,
    )
    row = measure_labels(labels, image, context=context).objects.iloc[0]
    assert row.area_um2 == 12 * 0.5 * 0.25
    assert np.isnan(row.volume_um3)


class _FakeLazy:
    def __init__(self, array, seen):
        self._array = array
        self.shape = array.shape
        self.dtype = array.dtype
        self.compute_calls = 0
        self.events_seen_at_compute = None
        self._seen = seen

    def compute(self):
        self.compute_calls += 1
        self.events_seen_at_compute = len(self._seen)
        return self._array


def test_lazy_image_and_labels_emit_once_before_each_materialization():
    seen = []
    lazy_labels = _FakeLazy(np.ones((1, 2, 2), dtype=np.uint32), seen)
    lazy_image = _FakeLazy(np.arange(4, dtype=np.uint16).reshape(1, 2, 2, 1), seen)
    spacing = (2.0, 0.5, 0.25)
    labels = LabelVolume(lazy_labels, spacing)
    image = ImageVolume(
        lazy_image,
        spacing,
        ("DAPI",),
        Path("lazy.tif"),
        {"run_id": "run-7", "file_id": "file-9"},
    )

    tables = measure_labels(labels, image, events=seen.append)

    assert not tables.objects.empty
    row = tables.objects.iloc[0]
    assert row.voxel_count == 4
    assert row.area_um2 == 4 * 0.5 * 0.25
    assert np.isnan(row.volume_um3)
    assert tables.objects.attrs["extent_metric"] == "area_um2"
    assert lazy_labels.compute_calls == lazy_image.compute_calls == 1
    assert lazy_labels.events_seen_at_compute == 1
    assert lazy_image.events_seen_at_compute == 2
    assert [event.kind for event in seen] == ["materialized", "materialized"]
    assert [event.details["shape"] for event in seen] == [[1, 2, 2], [1, 2, 2, 1]]
    assert [event.details["dtype"] for event in seen] == ["uint32", "uint16"]
    assert all(event.run_id == "run-7" and event.file_id == "file-9" for event in seen)
    assert "label data" in seen[0].details["reason"]
    assert "image data" in seen[1].details["reason"]
