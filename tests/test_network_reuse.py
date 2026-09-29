"""Reusing Cellpose models and network output.

The stand-in Cellpose 4 below has a slow ``_run_net`` (the network) and a cheap
mask step that depends on ``cellprob_threshold``, like the real package.
"""

from __future__ import annotations

import numpy as np
import pytest

from cellquant import engines, segmentation
from cellquant.segmentation import clear_network_output, keep_network_output, segment_objects
from tests.test_cellpose_engines import _install_fake

_V4_WITH_NETWORK = '''
import numpy as np, types
CALLS = {"init": 0, "net": 0}
MODEL_NAMES = ["cpsam_v2", "cpsam"]

class CellposeModel:
    def __init__(self, gpu=False, pretrained_model="cpsam_v2", model_type=None, diam_mean=None,
                 device=None, nchan=None, use_bfloat16=True):
        CALLS["init"] += 1
        self.device = types.SimpleNamespace(type="cpu")

    def _run_net(self, x, rescale=1.0, resample=True, augment=False, batch_size=8, tile_overlap=0.1,
                 bsize=None, anisotropy=1.0, do_3D=False):
        CALLS["net"] += 1
        cellprob = np.asarray(x, dtype=np.float32) / max(float(np.max(x)), 1.0) * 6.0 - 3.0
        return np.zeros((2,) + cellprob.shape, np.float32), cellprob, np.zeros(4, np.float32)

    def eval(self, x, batch_size=8, resample=True, channels=None, channel_axis=None, z_axis=None,
             normalize=True, rescale=None, diameter=None, flow_threshold=0.4, cellprob_threshold=0.0,
             do_3D=False, anisotropy=None, flow3D_smooth=0, stitch_threshold=0.0, min_size=15,
             max_size_fraction=0.4, niter=None, augment=False, tile_overlap=0.1, bsize=256,
             compute_masks=True, progress=None):
        dP, cellprob, styles = self._run_net(np.asarray(x), resample=resample, rescale=1.0,
                                             augment=augment, batch_size=batch_size,
                                             tile_overlap=tile_overlap, bsize=bsize, do_3D=do_3D,
                                             anisotropy=anisotropy)
        masks = (cellprob > cellprob_threshold).astype(np.int32)
        cellprob += 100.0   # a caller must never be able to change what is remembered
        return masks, [dP, dP, cellprob], styles
'''


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    engines.cellpose_engine.cache_clear()
    segmentation._MODELS.clear()
    yield
    engines.cellpose_engine.cache_clear()
    segmentation._MODELS.clear()
    segmentation._NETWORK_MEMO = None


def _params(cellprob: float) -> dict:
    return {"engine": "cellpose4", "cellprob_threshold": cellprob, "min_size": 0}


def _image(seed: int = 0) -> np.ndarray:
    return np.random.default_rng(seed).uniform(1, 100, (24, 24)).astype(np.float32)


def test_network_runs_once_for_several_thresholds(tmp_path, monkeypatch):
    models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", _V4_WITH_NETWORK)
    image = _image()
    counts = []
    with keep_network_output():
        for value in (-2.0, 0.0, 2.0, -2.0):
            counts.append(int(np.count_nonzero(segment_objects(image, "cellpose", _params(value)))))
    assert models.CALLS["net"] == 1
    # A stricter threshold keeps fewer pixels, and repeating a value repeats the result.
    assert counts[0] > counts[1] > counts[2]
    assert counts[3] == counts[0]


def test_reused_output_matches_a_fresh_run(tmp_path, monkeypatch):
    models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", _V4_WITH_NETWORK)
    image = _image()
    fresh = segment_objects(image, "cellpose", _params(1.0))
    with keep_network_output():
        segment_objects(image, "cellpose", _params(-1.0))
        reused = segment_objects(image, "cellpose", _params(1.0))
    assert np.array_equal(fresh, reused)
    assert models.CALLS["net"] == 2  # fresh run, plus one network run inside the block


def test_a_different_image_or_a_run_outside_the_block_runs_the_network(tmp_path, monkeypatch):
    models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", _V4_WITH_NETWORK)
    with keep_network_output():
        segment_objects(_image(0), "cellpose", _params(0.0))
        segment_objects(_image(1), "cellpose", _params(0.0))
        assert models.CALLS["net"] == 2
        clear_network_output()
        segment_objects(_image(1), "cellpose", _params(0.0))
        assert models.CALLS["net"] == 3
    segment_objects(_image(1), "cellpose", _params(0.0))
    segment_objects(_image(1), "cellpose", _params(0.0))
    assert models.CALLS["net"] == 5  # nothing is remembered outside the block


def test_the_model_is_loaded_once(tmp_path, monkeypatch):
    models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", _V4_WITH_NETWORK)
    for seed in range(3):
        segment_objects(_image(seed), "cellpose", _params(0.0))
    assert models.CALLS["init"] == 1


def test_models_without_a_network_step_still_work(tmp_path, monkeypatch):
    # The other stand-ins have no _run_net; reuse simply does nothing for them.
    from tests.test_cellpose_engines import _V4

    models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", _V4)
    with keep_network_output():
        labels = segment_objects(_image(), "cellpose", {"engine": "cellpose4"})
    assert labels.max() == 2
    assert models is not None
