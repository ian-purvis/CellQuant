"""Cellpose-SAM without resizing (the step 2 default), and settings saved before it keep resizing."""

from __future__ import annotations

import numpy as np
import pytest

from cellquant import engines, segmentation
from cellquant.pipeline import process_image
from cellquant.recipe import Recipe
from cellquant.volume import segment_volume
from tests.test_3d import _recording
from tests.test_cellpose_engines import _V3, _V4, _install_fake


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(segmentation, "_MODELS", {})
    engines.cellpose_engine.cache_clear()
    yield
    engines.cellpose_engine.cache_clear()


def _evals(models) -> list[dict]:
    return [options for name, options in models.CALLS if name == "CellposeModel.eval"]


def _recipe(parameters: dict) -> dict:
    return {"object_set": {"segmentation_channel": 0, "algorithm": "cellpose", "parameters": parameters}}


def test_without_resizing_cellpose_sam_gets_no_diameter(tmp_path, monkeypatch):
    models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", _V4)
    image = np.zeros((1, 20, 20), dtype=np.uint16)
    result = process_image(
        image, _recipe({"diameter_um": 6.0, "resize_to_diameter": False}), pixel_size_x=0.57, pixel_size_y=0.57, channel_axis=0
    )
    assert _evals(models)[-1]["diameter"] is None
    assert result.provenance["diameter_px_used"] is None
    assert result.provenance["segmentation_engine"]["resized_to_diameter"] is False


def test_settings_saved_without_the_key_still_resize(tmp_path, monkeypatch):
    models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", _V4)
    image = np.zeros((1, 20, 20), dtype=np.uint16)
    result = process_image(image, _recipe({"diameter_um": 6.0}), pixel_size_x=0.5, pixel_size_y=0.5, channel_axis=0)
    assert _evals(models)[-1]["diameter"] == pytest.approx(12.0)
    assert result.provenance["diameter_px_used"] == pytest.approx(12.0)
    assert result.provenance["segmentation_engine"]["resized_to_diameter"] is True


def test_the_key_changes_the_recipe_hash_only_when_present():
    old = Recipe.model_validate(_recipe({"diameter_um": 6.0}))
    again = Recipe.model_validate(_recipe({"diameter_um": 6.0}))
    new = Recipe.model_validate(_recipe({"diameter_um": 6.0, "resize_to_diameter": False}))
    assert old.content_hash() == again.content_hash() != new.content_hash()


def test_classic_cellpose_always_resizes(tmp_path, monkeypatch):
    models = _install_fake(tmp_path, monkeypatch, "3.1.1.3", _V3)
    image = np.zeros((1, 20, 20), dtype=np.uint16)
    process_image(
        image, _recipe({"diameter_px": 17.0, "resize_to_diameter": False}), pixel_size_x=0.5, pixel_size_y=0.5, channel_axis=0
    )
    assert _evals(models)[-1]["diameter"] == pytest.approx(17.0)


def test_full_3d_without_resizing_keeps_the_scaled_voxel_cutoff(tmp_path, monkeypatch):
    models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", _recording(_V4))
    volume = np.random.default_rng(0).random((5, 20, 20)).astype(np.float32)
    details: dict = {}
    segment_volume(
        volume, "cellpose", {"diameter_px": 21.4, "resize_to_diameter": False}, "full_3d",
        pixel_size_x=0.28, pixel_size_y=0.28, pixel_size_z=1.5, details=details,
    )
    call = _evals(models)[0]
    assert call["diameter"] is None
    # The cutoff still follows the nucleus size, as with resizing.
    assert call["min_size"] == round(1000 * (21.4 / 30) ** 3 / (1.5 / 0.28))
    assert details["engine"]["resized_to_diameter"] is False


def test_linked_slices_without_resizing_get_no_diameter(tmp_path, monkeypatch):
    models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", _recording(_V4))
    volume = np.random.default_rng(0).random((3, 20, 20)).astype(np.float32)
    segment_volume(volume, "cellpose", {"diameter_px": 10, "resize_to_diameter": False}, "stitch_slices")
    assert [call["diameter"] for call in _evals(models)] == [None, None, None]
