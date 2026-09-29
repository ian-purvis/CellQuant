"""Cellpose 3 and Cellpose 4 adapters.

The stand-in packages below copy the constructor and ``eval`` signatures of the
real Cellpose 3.1.1.3 and 4.2.1.1 releases. Real ``CellposeModel.eval`` takes
no ``**kwargs``, so a misspelled or removed argument fails here the same way it
would fail with the real package. No network or model weights are used.
"""

from __future__ import annotations

import importlib
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest

from cellquant import engines
from cellquant.errors import SegmentationError
from cellquant.pipeline import process_image
from cellquant.segmentation import engine_signature, segment_objects

_V3 = '''
import numpy as np, types
CALLS = []
MODEL_NAMES = ["cyto3", "nuclei", "cyto2_cp3", "tissuenet_cp3", "livecell_cp3", "cyto2", "cyto", "CPx"]

class CellposeModel:
    def __init__(self, gpu=False, pretrained_model=False, model_type=None, mkldnn=True, diam_mean=30.0,
                 device=None, nchan=2, pretrained_model_ortho=None, backbone="default"):
        self.device = types.SimpleNamespace(type="cpu")

    def eval(self, x, batch_size=8, resample=True, channels=None, channel_axis=None, z_axis=None,
             normalize=True, invert=False, rescale=None, diameter=None, flow_threshold=0.4,
             cellprob_threshold=0.0, do_3D=False, anisotropy=None, flow3D_smooth=0, stitch_threshold=0.0,
             min_size=15, max_size_fraction=0.4, niter=None, augment=False, tile_overlap=0.1, bsize=224,
             interp=True, compute_masks=True, progress=None):
        CALLS.append(("CellposeModel.eval", dict(diameter=diameter, channels=channels)))
        masks = np.zeros(np.asarray(x).shape[-2:], dtype=np.int32)
        masks[2:8, 2:8] = 1
        masks[12:18, 12:18] = 2
        return masks, None, None

class Cellpose:
    def __init__(self, gpu=False, model_type="cyto3", nchan=2, device=None, backbone="default"):
        CALLS.append(("Cellpose.__init__", dict(gpu=gpu, model_type=model_type)))
        self.cp = CellposeModel(gpu=gpu, model_type=model_type)
        self.device = self.cp.device

    def eval(self, x, batch_size=8, channels=[0, 0], channel_axis=None, invert=False, normalize=True,
             diameter=30.0, do_3D=False, **kwargs):
        # The real class forwards **kwargs to CellposeModel.eval, which rejects unknown names.
        masks, flows, styles = self.cp.eval(x, channels=channels, channel_axis=channel_axis, batch_size=batch_size,
                                            normalize=normalize, invert=invert, diameter=diameter, do_3D=do_3D, **kwargs)
        return masks, flows, styles, 17.0
'''

_V4 = '''
import numpy as np, types
CALLS = []
MODEL_NAMES = ["cpsam_v2", "cpdino", "cpdino-vitb", "cpsam"]

class CellposeModel:
    def __init__(self, gpu=False, pretrained_model="cpsam_v2", model_type=None,
                 diam_mean=None, device=None, nchan=None, use_bfloat16=True):
        CALLS.append(("CellposeModel.__init__", dict(gpu=gpu, pretrained_model=pretrained_model, model_type=model_type)))
        self.device = types.SimpleNamespace(type="cpu")

    def eval(self, x, batch_size=8, resample=True, channels=None, channel_axis=None, z_axis=None,
             normalize=True, rescale=None, diameter=None, flow_threshold=0.4, cellprob_threshold=0.0,
             do_3D=False, anisotropy=None, flow3D_smooth=0, stitch_threshold=0.0, min_size=15,
             max_size_fraction=0.4, niter=None, augment=False, tile_overlap=0.1, bsize=256,
             compute_masks=True, progress=None):
        CALLS.append(("CellposeModel.eval", dict(diameter=diameter, channels=channels)))
        masks = np.zeros(np.asarray(x).shape[-2:], dtype=np.int32)
        masks[2:8, 2:8] = 1
        masks[12:18, 12:18] = 2
        return masks, None, None
'''


def _install_fake(tmp_path: Path, monkeypatch, version: str, source: str):
    root = tmp_path / f"cellpose_{version}"
    package = root / "cellpose"
    package.mkdir(parents=True)
    # Like the real package, importing cellpose would pull in PyTorch; the adapter must not need to.
    (package / "__init__.py").write_text("")
    (package / "models.py").write_text(textwrap.dedent(source))
    info = root / f"cellpose-{version}.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: cellpose\nVersion: {version}\n")
    (info / "RECORD").write_text("")
    for name in [name for name in sys.modules if name == "cellpose" or name.startswith("cellpose.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.syspath_prepend(str(root))
    importlib.invalidate_caches()
    engines.cellpose_engine.cache_clear()
    return importlib.import_module("cellpose.models")


@pytest.fixture(autouse=True)
def _clear_engine_cache():
    engines.cellpose_engine.cache_clear()
    yield
    engines.cellpose_engine.cache_clear()


def _image() -> np.ndarray:
    return np.zeros((20, 20), dtype=np.float32)


def test_classic_cellpose_uses_the_cellpose_class_and_nuclei_model(tmp_path, monkeypatch):
    models = _install_fake(tmp_path, monkeypatch, "3.1.1.3", _V3)
    engine = engines.cellpose_engine()
    assert (engine.key, engine.default_model) == ("cellpose3", "nuclei")
    details: dict = {}
    labels = segment_objects(_image(), "cellpose", {"diameter_px": 12, "flow_threshold": 0.5}, details=details)
    assert labels.max() == 2
    assert models.CALLS[0] == ("Cellpose.__init__", {"gpu": False, "model_type": "nuclei"})
    assert models.CALLS[1] == ("CellposeModel.eval", {"diameter": 12.0, "channels": [0, 0]})
    assert details["engine"]["engine"] == "cellpose3"
    assert details["engine"]["cellpose_version"] == "3.1.1.3"


def test_cellpose_sam_uses_its_own_default_model_and_no_channels(tmp_path, monkeypatch):
    models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", _V4)
    engine = engines.cellpose_engine()
    assert (engine.key, engine.default_model) == ("cellpose4", "cpsam_v2")
    assert engine.models[0] == "cpsam_v2"
    labels = segment_objects(_image(), "cellpose", {"diameter_px": 0})
    assert labels.max() == 2
    init = models.CALLS[0][1]
    assert init["pretrained_model"] == "cpsam_v2" and init["model_type"] is None
    assert models.CALLS[1] == ("CellposeModel.eval", {"diameter": None, "channels": None})


def test_engine_is_found_without_importing_cellpose_or_torch(tmp_path, monkeypatch):
    _install_fake(tmp_path, monkeypatch, "4.2.1.1", _V4)
    for name in [name for name in sys.modules if name == "cellpose" or name.startswith("cellpose.")]:
        monkeypatch.delitem(sys.modules, name)
    engines.cellpose_engine.cache_clear()
    engines.cellpose_engine()
    assert "cellpose" not in sys.modules


@pytest.mark.parametrize(
    ("version", "source", "parameters", "message"),
    [
        ("4.2.1.1", _V4, {"engine": "cellpose3"}, "made for Classic Cellpose"),
        ("4.2.1.1", _V4, {"model": "nuclei"}, "that is a classic Cellpose model"),
        ("3.1.1.3", _V3, {"engine": "cellpose4"}, "made for Cellpose-SAM"),
        ("3.1.1.3", _V3, {"model": "cpsam"}, "Classic Cellpose has no model named 'cpsam'"),
    ],
)
def test_settings_for_the_other_engine_are_refused(tmp_path, monkeypatch, version, source, parameters, message):
    models = _install_fake(tmp_path, monkeypatch, version, source)
    with pytest.raises(SegmentationError, match=message):
        segment_objects(_image(), "cellpose", parameters)
    assert models.CALLS == []  # refused before any model was loaded


def test_gpu_request_that_falls_back_to_cpu_is_reported(tmp_path, monkeypatch):
    _install_fake(tmp_path, monkeypatch, "4.2.1.1", _V4)
    image = np.zeros((2, 20, 20), dtype=np.uint16)
    image[0, 2:8, 2:8] = 1000
    recipe = {
        "object_set": {"segmentation_channel": 0, "algorithm": "cellpose", "parameters": {"gpu": True}},
    }
    result = process_image(image, recipe)
    assert result.provenance["segmentation_engine"]["device"] == "cpu"
    assert result.provenance["segmentation_engine"]["model"] == "cpsam_v2"
    assert any("GPU was requested" in warning for warning in result.qc.warnings)


def test_cache_signature_differs_between_engines(tmp_path, monkeypatch):
    _install_fake(tmp_path, monkeypatch, "3.1.1.3", _V3)
    classic = engine_signature("cellpose", {})
    _install_fake(tmp_path / "v4", monkeypatch, "4.2.1.1", _V4)
    sam = engine_signature("cellpose", {})
    assert classic != sam
    assert engine_signature("classical", {}) == {"algorithm": "classical"}


def test_missing_cellpose_gives_an_install_hint(monkeypatch):
    monkeypatch.setattr(engines, "cellpose_engine", lambda: engines.CellposeEngine(installed=False))
    with pytest.raises(SegmentationError, match="Install CellQuant.bat"):
        segment_objects(_image(), "cellpose", {})
