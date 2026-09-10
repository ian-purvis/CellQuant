from types import SimpleNamespace

import numpy as np
import pytest

from cellquant.plugin.diameter import diameter_from_shapes_layer, line_length_px


def test_line_length_px_uses_xy_and_ignores_z():
    # z,y,x vertices of a 30-px-wide line in YX
    vertices = np.array([[2.0, 10.0, 0.0], [2.0, 10.0, 30.0]])
    assert line_length_px(vertices) == pytest.approx(30.0)


def test_diameter_from_shapes_prefers_last_line():
    layer = SimpleNamespace(
        name="CellQuant diameter",
        data=[
            np.array([[0.0, 0.0], [0.0, 10.0]]),
            np.array([[1.0, 5.0], [1.0, 45.0]]),
        ],
        shape_type=["line", "line"],
    )
    assert diameter_from_shapes_layer(layer) == pytest.approx(40.0)


def test_diameter_from_shapes_requires_a_drawn_line():
    with pytest.raises(ValueError, match="Draw a line"):
        diameter_from_shapes_layer(None)
    with pytest.raises(ValueError, match="No shapes"):
        diameter_from_shapes_layer(SimpleNamespace(name="x", data=[], shape_type=[]))
