from __future__ import annotations

import hashlib
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pytest
from PIL import Image

from cellquant.contracts import ImageVolume, LabelVolume
from cellquant.viz import (
    label_projection,
    make_qc_figures,
    orthogonal_view,
    outline_slice,
    showcase_crop,
)


def _volumes() -> tuple[ImageVolume, LabelVolume]:
    z, y, x = np.indices((5, 18, 24))
    image_data = (z * 40 + y * 3 + x).astype(np.uint16)[..., None]
    labels = np.zeros((5, 18, 24), dtype=np.uint32)
    labels[1:4, 3:10, 4:12] = 1
    labels[2:5, 11:16, 14:21] = 7
    spacing = (2.0, 0.5, 0.25)
    image = ImageVolume(
        image_data,
        spacing,
        ("DAPI",),
        Path("synthetic.tif"),
        {"input_fingerprint": "input-sha256"},
    )
    label_volume = LabelVolume(labels, spacing, {"input_fingerprint": "input-sha256"})
    return image, label_volume


def _config(seed: int = 19) -> dict:
    return {
        "preprocess": {"channel": 0},
        "viz": {
            "low_percentile": 2.0,
            "high_percentile": 98.0,
            "label_seed": seed,
            "dpi": 80,
        },
    }


def test_make_qc_figures_writes_fixed_size_pngs_and_metadata(tmp_path):
    image, labels = _volumes()
    paths = make_qc_figures(image, labels, tmp_path, _config())

    assert set(paths) == {"outline_slice", "orthogonal_view", "label_projection"}
    assert all(path.is_file() for path in paths.values())
    expected_sizes = {
        "outline_slice": (480, 480),
        "orthogonal_view": (960, 320),
        "label_projection": (480, 480),
    }
    for name, path in paths.items():
        with Image.open(path) as png:
            assert png.size == expected_sizes[name]
            assert png.info["input_fingerprint"] == "input-sha256"
            assert png.info["config_fingerprint"] == hashlib.sha256(
                b'{"preprocess":{"channel":0},"viz":{"dpi":80,"high_percentile":98.0,"label_seed":19,"low_percentile":2.0}}'
            ).hexdigest()


def test_output_is_byte_deterministic_and_seed_controls_colors(tmp_path):
    image, labels = _volumes()
    first = make_qc_figures(image, labels, tmp_path / "a", _config(seed=2))
    second = make_qc_figures(image, labels, tmp_path / "b", _config(seed=2))
    different = make_qc_figures(image, labels, tmp_path / "c", _config(seed=3))

    assert {name: path.read_bytes() for name, path in first.items()} == {
        name: path.read_bytes() for name, path in second.items()
    }
    assert first["label_projection"].read_bytes() != different["label_projection"].read_bytes()


def test_low_level_renderers_return_headless_figures_and_crop_showcase(tmp_path):
    image, labels = _volumes()
    common = dict(channel=0, low_percentile=2, high_percentile=98, label_seed=4, dpi=50)
    figures = [
        outline_slice(image, labels, **common),
        orthogonal_view(image, labels, **common),
        label_projection(labels, label_seed=4, dpi=50),
    ]
    try:
        assert [len(figure.axes) for figure in figures] == [1, 3, 1]
    finally:
        for figure in figures:
            plt.close(figure)
    assert all(path.exists() for path in showcase_crop(image, labels, tmp_path, _config()).values())


def test_rejects_shape_dtype_spacing_and_channel_mismatches(tmp_path):
    image, labels = _volumes()
    wrong_shape = LabelVolume(np.zeros((4, 18, 24), dtype=np.uint32), labels.spacing_um)
    wrong_spacing = LabelVolume(labels.data, (1.0, 0.5, 0.25))

    with pytest.raises(ValueError, match="shape must match"):
        make_qc_figures(image, wrong_shape, tmp_path, _config())
    with pytest.raises(ValueError, match="spacing_um must match"):
        make_qc_figures(image, wrong_spacing, tmp_path, _config())
    with pytest.raises(TypeError, match="uint32"):
        LabelVolume(labels.data.astype(np.uint16), labels.spacing_um)
    bad_channel = _config()
    bad_channel["preprocess"]["channel"] = 1
    with pytest.raises(ValueError, match="outside C axis"):
        make_qc_figures(image, labels, tmp_path, bad_channel)


def test_missing_fingerprint_and_invalid_percentiles_fail_explicitly(tmp_path):
    image, labels = _volumes()
    no_fingerprint = ImageVolume(image.data, image.spacing_um, image.channel_names, image.source)
    no_label_fingerprint = LabelVolume(labels.data, labels.spacing_um)
    with pytest.raises(ValueError, match="input_fingerprint"):
        make_qc_figures(no_fingerprint, no_label_fingerprint, tmp_path, _config())
    invalid = _config()
    invalid["viz"]["low_percentile"] = 99.0
    with pytest.raises(ValueError, match="percentiles"):
        make_qc_figures(image, labels, tmp_path, invalid)


def test_projected_qc_uses_matching_singleton_z_multichannel_image(tmp_path):
    image, _ = _volumes()
    projected = np.zeros((1, 18, 24), dtype=np.uint32)
    projected[0, 3:10, 4:12] = 1
    labels = LabelVolume(projected, image.spacing_um, {"input_fingerprint": "input-sha256"})
    config = _config()
    config["segment"] = {"mode": "max_projection_2d", "z_index": None}

    paths = make_qc_figures(image, labels, tmp_path, config)

    assert all(path.is_file() for path in paths.values())
