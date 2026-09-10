import ast
from copy import deepcopy
import inspect
from pathlib import Path
import sys

import pytest

from cellquant.config import RunConfig, load_config
from cellquant.segment import ACCEPT_MEASURED_MODEL_HASH, apply_engine_segment_defaults, _eval_kwargs


def _config(engine="v3", mode="max_projection_2d"):
    raw = deepcopy(load_config(Path(__file__).parents[1] / "sample_config.yaml").raw)
    raw["segment"].update(engine=engine, mode=mode, anisotropy="manifest")
    # stitch_2d only accepts a threshold that actually links plane-local IDs.
    raw["segment"]["stitch_threshold"] = 0.25 if mode == "stitch_2d" else 0.0
    raw["segment"] = apply_engine_segment_defaults(raw["segment"])
    return raw


def test_apply_engine_defaults_switches_sam_to_nuclei():
    segment = {
        "engine": "v3",
        "model": "cpsam_v2",
        "model_sha256": "a" * 64,
        "model_type": None,
        "tile": True,
        "use_bfloat16": True,
    }
    out = apply_engine_segment_defaults(segment)
    assert out["model"] == "nuclei"
    assert out["model_sha256"] == ACCEPT_MEASURED_MODEL_HASH
    assert out["tile"] is True
    assert out["use_bfloat16"] is False


@pytest.mark.parametrize("engine", ["v3", "v4"])
def test_no_tile_is_rejected_in_config_and_direct_adapter(engine):
    raw = _config(engine)
    raw["segment"]["tile"] = False
    with pytest.raises(ValueError, match="segment.tile must be true"):
        RunConfig(raw)
    with pytest.raises(ValueError, match="segment.tile must be true"):
        _eval_kwargs(raw["segment"], (2.0, 0.5, 0.5))


def test_v3_eval_forwards_supported_settings_without_tile_keyword():
    spec = _config()["segment"]
    spec.update(batch_size=3, resample=False, niter=150, augment=True,
                bsize=224, tile_overlap=0.2, rescale_factor=0.75)
    kwargs = _eval_kwargs(spec, (1.0, 0.5, 0.5))
    assert kwargs["channels"] == [0, 0]
    assert kwargs["do_3D"] is False
    assert "tile" not in kwargs
    assert kwargs["z_axis"] is None
    assert kwargs["channel_axis"] is None
    assert kwargs["anisotropy"] is None
    assert kwargs["batch_size"] == 3
    assert kwargs["resample"] is False
    assert kwargs["niter"] == 150
    assert kwargs["augment"] is True
    assert kwargs["bsize"] == 224
    assert kwargs["tile_overlap"] == 0.2
    assert kwargs["rescale"] == 0.75


# CellposeModel.eval's v3 signature, copied from the installed v3 models.py.
# No **kwargs: this catches invalid adapter parameters without model inference.
def _v3_eval(self, x, batch_size=8, resample=True, channels=None, channel_axis=None,
             z_axis=None, normalize=True, invert=False, rescale=None, diameter=None,
             flow_threshold=0.4, cellprob_threshold=0.0, do_3D=False, anisotropy=None,
             flow3D_smooth=0, stitch_threshold=0.0, min_size=15, max_size_fraction=0.4,
             niter=None, augment=False, tile_overlap=0.1, bsize=224, interp=True,
             compute_masks=True, progress=None):
    pass


@pytest.mark.parametrize("mode", ["volume_3d", "stitch_2d", "single_plane_2d", "max_projection_2d"])
def test_v3_kwargs_bind_to_cellpose_model_eval_signature(mode):
    spec = _config(mode=mode)["segment"]
    kwargs = _eval_kwargs(spec, (2.0, 0.5, 0.5))
    inspect.signature(_v3_eval).bind(None, None, **kwargs)
    assert kwargs["z_axis"] == (None if mode in {"single_plane_2d", "max_projection_2d"} else 0)
    assert kwargs["anisotropy"] == (4.0 if mode == "volume_3d" else None)


def test_contract_matches_installed_v3_source_when_available():
    model_source = Path(sys.executable).parents[1] / "cellquant-napari-v3/Lib/site-packages/cellpose/models.py"
    if not model_source.is_file():
        pytest.skip("separate Cellpose v3 environment is not installed")
    tree = ast.parse(model_source.read_text(encoding="utf-8"))
    model = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "CellposeModel")
    method = next(node for node in model.body if isinstance(node, ast.FunctionDef) and node.name == "eval")
    assert method.args.kwarg is None
    assert [arg.arg for arg in method.args.args] == list(inspect.signature(_v3_eval).parameters)
