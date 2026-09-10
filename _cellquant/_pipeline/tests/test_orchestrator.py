from pathlib import Path

import numpy as np
import pytest

from cellquant.contracts import ImageVolume, LabelVolume, MutableCancellationToken, PipelineCancelled
from cellquant.orchestrator import run_measurements, run_pipeline


def _image():
    return ImageVolume(
        np.arange(32, dtype=np.uint16).reshape(2, 4, 4, 1),
        (2.0, 0.5, 0.5),
        ("DAPI",),
        Path("input.tif"),
        {"run_id": "run", "file_id": "file"},
    )


def _config(mode="volume_3d", z_index=None):
    return {"segment": {"mode": mode, "z_index": z_index}, "measure": {}}


def _patch_stages(monkeypatch, processed, labels):
    monkeypatch.setattr("cellquant.orchestrator.run_preprocess", lambda *args: processed)
    monkeypatch.setattr("cellquant.orchestrator.segment", lambda *args: labels)
    monkeypatch.setattr("cellquant.orchestrator.run_postprocess", lambda value, *args, **kwargs: value)


def test_rescaled_labels_are_restored_for_measurement(monkeypatch):
    image = _image()
    processed = ImageVolume(
        np.zeros((4, 2, 2, 1), dtype=np.float32),
        (1.0, 1.0, 1.0),
        ("DAPI",),
        image.source,
        image.metadata,
    )
    source = np.zeros((4, 2, 2), dtype=np.uint32)
    source[:2, 0, 0] = 7
    source[2:, 1, 1] = 42
    labels = LabelVolume(source, processed.spacing_um, {"model": "fake"})
    _patch_stages(monkeypatch, processed, labels)
    events = []

    result = run_pipeline(image, _config(), MutableCancellationToken(), events.append)

    assert result.data.shape == image.data.shape[:3]
    assert result.spacing_um == image.spacing_um
    assert result.data.dtype == np.uint32
    assert set(np.unique(result.data)) == {0, 7, 42}
    assert result.provenance["original_grid_regrid"] == {
        "source_shape_zyx": [4, 2, 2],
        "source_spacing_um": [1.0, 1.0, 1.0],
        "target_shape_zyx": [2, 4, 4],
        "target_spacing_um": [2.0, 0.5, 0.5],
        "interpolation": "nearest",
    }
    assert any(event.kind == "warning" and event.stage == "restore_grid" for event in events)

    tables = run_measurements(image, result, _config(), MutableCancellationToken())
    assert set(tables.objects["label"]) == {7, 42}


def test_no_rescale_returns_postprocessed_label_object(monkeypatch):
    image = _image()
    processed = ImageVolume(
        image.data.astype(np.float32), image.spacing_um, image.channel_names, image.source, image.metadata
    )
    labels = LabelVolume(np.zeros(image.data.shape[:3], dtype=np.uint32), image.spacing_um)
    _patch_stages(monkeypatch, processed, labels)
    events = []

    result = run_pipeline(image, _config(), MutableCancellationToken(), events.append)

    assert result is labels
    assert not any(event.kind in {"materialized", "warning"} and event.stage == "restore_grid" for event in events)


def test_cancellation_before_regrid_does_not_publish_labels(monkeypatch):
    image = _image()
    processed = ImageVolume(
        np.zeros((4, 2, 2, 1), dtype=np.float32),
        (1.0, 1.0, 1.0),
        image.channel_names,
        image.source,
        image.metadata,
    )
    labels = LabelVolume(np.ones((4, 2, 2), dtype=np.uint32), processed.spacing_um)
    token = MutableCancellationToken()
    monkeypatch.setattr("cellquant.orchestrator.run_preprocess", lambda *args: processed)
    monkeypatch.setattr("cellquant.orchestrator.segment", lambda *args: labels)

    def cancel_after_postprocess(value, *args, **kwargs):
        token.cancel()
        return value

    monkeypatch.setattr("cellquant.orchestrator.run_postprocess", cancel_after_postprocess)
    events = []

    with pytest.raises(PipelineCancelled):
        run_pipeline(image, _config(), token, events.append)

    assert any(event.kind == "cancelled" and event.stage == "restore_grid" for event in events)
    assert not any(event.kind == "warning" and event.stage == "restore_grid" for event in events)


def test_single_plane_pipeline_does_not_expand_mask_across_source_z(monkeypatch):
    image = _image()
    monkeypatch.setattr("cellquant.orchestrator.run_preprocess", lambda volume, *args: volume)
    monkeypatch.setattr(
        "cellquant.orchestrator.segment",
        lambda volume, *args: LabelVolume(
            np.ones(volume.data.shape[:3], dtype=np.uint32), volume.spacing_um
        ),
    )
    monkeypatch.setattr("cellquant.orchestrator.run_postprocess", lambda value, *args, **kwargs: value)

    result = run_pipeline(
        image, _config("single_plane_2d", 1), MutableCancellationToken()
    )

    assert result.data.shape == (1, 4, 4)
    assert result.spacing_um == image.spacing_um


def test_projection_measurements_use_matching_projected_intensities():
    image = ImageVolume(
        np.array([[[[1]]], [[[9]]]], dtype=np.uint16),
        (2, 1, 1),
        ("DAPI",),
        Path("input.tif"),
    )
    labels = LabelVolume(np.ones((1, 1, 1), dtype=np.uint32), image.spacing_um)
    tables = run_measurements(
        image, labels, _config("max_projection_2d"), MutableCancellationToken()
    )
    assert tables.intensities.loc[0, "mean"] == 9
