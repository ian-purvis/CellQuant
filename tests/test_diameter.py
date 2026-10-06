"""Nucleus diameter in µm, converted with each image's own pixel size."""

from __future__ import annotations

import numpy as np
import pytest

from cellquant import segmentation
from cellquant.pipeline import process_image
from tests.test_cellpose_engines import _V4, _install_fake


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    from cellquant import engines

    monkeypatch.setattr(segmentation, "_MODELS", {})
    yield
    engines.cellpose_engine.cache_clear()


def _evals(models) -> list[dict]:
    return [options for name, options in models.CALLS if name == "CellposeModel.eval"]


def _recipe(parameters: dict) -> dict:
    return {"object_set": {"segmentation_channel": 0, "algorithm": "cellpose", "parameters": parameters}}


def test_a_diameter_in_micrometres_is_converted_with_each_images_own_pixel_size(tmp_path, monkeypatch):
    models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", _V4)
    # Saved by 2.1 with diameter_px baked from the first image; diameter_um now wins, per image.
    recipe = _recipe({"diameter_um": 6.3, "diameter_px": 10.0})
    image = np.zeros((1, 20, 20), dtype=np.uint16)
    used = []
    for pixel_size in (0.575, 0.281):  # a 20x and a 40x image
        result = process_image(image, recipe, pixel_size_x=pixel_size, pixel_size_y=pixel_size, channel_axis=0)
        used.append(result.provenance["diameter_px_used"])
    assert [item["diameter"] for item in _evals(models)] == pytest.approx([6.3 / 0.575, 6.3 / 0.281])
    assert used == pytest.approx([6.3 / 0.575, 6.3 / 0.281])


def test_without_a_pixel_size_cellpose_decides_and_pixels_are_used_as_is(tmp_path, monkeypatch):
    models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", _V4)
    image = np.zeros((1, 20, 20), dtype=np.uint16)
    process_image(image, _recipe({"diameter_um": 6.3}), channel_axis=0)
    assert _evals(models)[-1]["diameter"] is None
    result = process_image(image, _recipe({"diameter_px": 17.0}), pixel_size_x=0.5, pixel_size_y=0.5, channel_axis=0)
    assert _evals(models)[-1]["diameter"] == pytest.approx(17.0) and result.provenance["diameter_px_used"] == pytest.approx(17.0)
