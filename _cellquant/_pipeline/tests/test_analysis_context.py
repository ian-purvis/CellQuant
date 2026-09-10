"""Analysis-context binding for classification measurement grids."""

from pathlib import Path

import numpy as np
import pytest

from cellquant.analysis import (
    analysis_context_from_config,
    analysis_context_from_labels,
    analysis_context_summary,
    resolve_measurement_image,
)
from cellquant.classify import ClassificationRecipe, classify_labels
from cellquant.contracts import ImageVolume, LabelVolume


def _raw_stack():
    # Plane 0 dark, plane 1 bright — same XY shape so spacing/shape checks alone cannot distinguish.
    data = np.zeros((2, 4, 4, 1), dtype=np.float32)
    data[1, ...] = 100
    return ImageVolume(data, (1.0, 1.0, 1.0), ("ch0",), Path("stack.tif"))


def test_mask_provenance_binds_scoring_plane_not_live_config():
    image = _raw_stack()
    labels = LabelVolume(
        np.ones((1, 4, 4), dtype=np.uint32),
        (1.0, 1.0, 1.0),
        {
            "analysis_volume": {
                "mode": "single_plane_2d",
                "original_z_depth": 2,
                "z_selection": {"kind": "single_plane", "z_index": 0},
            }
        },
    )
    # Live config would wrongly pick plane 1.
    wrong = {"segment": {"mode": "single_plane_2d", "z_index": 1}}
    declared = analysis_context_from_config(
        wrong, original_z_depth=2, spacing_um=(1.0, 1.0, 1.0), shape_yx=(4, 4)
    )
    analysis, context = resolve_measurement_image(image, labels, declared=declared)
    assert context.z_selection == {"kind": "single_plane", "z_index": 0}
    assert float(analysis.data.max()) == 0.0
    recipe = ClassificationRecipe(
        dict(markers=[dict(name="A", channel=0, low=50, positive_fraction=0.5)])
    )
    calls = classify_labels(analysis, labels, recipe).calls.call.tolist()
    assert calls == ["negative"]


def test_max_projection_provenance_prepares_projection():
    image = _raw_stack()
    labels = LabelVolume(
        np.ones((1, 4, 4), dtype=np.uint32),
        (1.0, 1.0, 1.0),
        {
            "analysis_volume": {
                "mode": "max_projection_2d",
                "original_z_depth": 2,
                "z_selection": {
                    "kind": "maximum_projection",
                    "z_start": 0,
                    "z_stop_exclusive": 2,
                },
            }
        },
    )
    analysis, context = resolve_measurement_image(image, labels)
    assert "max projection" in analysis_context_summary(context)
    assert float(analysis.data.max()) == 100.0
    recipe = ClassificationRecipe(
        dict(markers=[dict(name="A", channel=0, low=50, positive_fraction=0.5)])
    )
    assert classify_labels(analysis, labels, recipe).calls.call.tolist() == ["positive"]


def test_missing_provenance_requires_declaration():
    image = _raw_stack()
    labels = LabelVolume(np.ones((1, 4, 4), dtype=np.uint32), (1.0, 1.0, 1.0))
    with pytest.raises(ValueError, match="provenance"):
        resolve_measurement_image(image, labels)
    with pytest.raises(ValueError, match="provenance"):
        analysis_context_from_labels(labels)
    declared = analysis_context_from_config(
        {"segment": {"mode": "single_plane_2d", "z_index": 1}},
        original_z_depth=2,
        spacing_um=(1.0, 1.0, 1.0),
        shape_yx=(4, 4),
    )
    analysis, _ = resolve_measurement_image(image, labels, declared=declared)
    assert float(analysis.data.max()) == 100.0
