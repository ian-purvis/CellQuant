import numpy as np
from cellquant.cellpose_adapter import CellposeSettings
from cellquant.pipeline import calibrated_diameter_px, segment


class FakeAdapter:
    def __init__(self): self.call = None
    def segment(self, image, mode):
        self.call = (image.shape, mode)
        return np.zeros(image.shape, dtype=np.uint16)


def test_stitch_passes_whole_stack_once():
    image = np.zeros((2, 5, 8, 9), dtype=np.uint16)
    fake = FakeAdapter()
    labels, selection = segment(image, 1, "stitch:2-4", CellposeSettings(), fake)
    assert fake.call == ((3, 8, 9), "stitch")
    assert labels.shape == (3, 8, 9)
    assert selection.first == 2


def test_calibrated_diameter_uses_geometric_mean_xy():
    assert calibrated_diameter_px(10, (0.25, 1.0)) == 20

