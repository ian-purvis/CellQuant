import numpy as np
import pytest
from cellquant.zmode import parse_z_selection, prepare_z


def test_modes_are_one_based_and_ranges_inclusive():
    assert parse_z_selection("single:2", 4).slice == slice(1, 2)
    assert parse_z_selection("stitch:2-4", 4).slice == slice(1, 4)
    data = np.arange(4 * 2 * 2).reshape(4, 2, 2)
    assert np.array_equal(prepare_z(data, parse_z_selection("max:2-3", 4)), data[1:3].max(0))


@pytest.mark.parametrize("spec", ["single:0", "single:2-3", "max:2", "stitch:4-2", "volume:1-5"])
def test_invalid_z_specs_fail(spec):
    with pytest.raises(ValueError):
        parse_z_selection(spec, 4)

