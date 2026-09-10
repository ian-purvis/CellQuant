import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
import tifffile
import yaml

from cellquant.cli import main
from cellquant.classify.store import reopen_classification


ROOT = Path(__file__).resolve().parents[1]


def inputs(tmp_path, mode="volume_3d"):
    image = np.array([[[[0], [8]], [[8], [0]]], [[[8], [8]], [[8], [8]]]], dtype=np.uint16)
    tifffile.imwrite(tmp_path / "image.tif", image, metadata={"axes": "ZYXC"}, photometric="minisblack")
    labels = np.ones((2 if mode == "volume_3d" else 1, 2, 2), np.uint32)
    tifffile.imwrite(tmp_path / "labels.tif", labels, metadata={"axes": "ZYX"}, photometric="minisblack")
    raw = yaml.safe_load((ROOT / "sample_config.yaml").read_text())
    raw["io"]["axes_override"] = "ZYXC"
    raw["io"]["spacing_override_um"] = [2, 1, 1]
    raw["segment"]["mode"] = mode
    raw["segment"]["z_index"] = 1 if mode == "single_plane_2d" else None
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(raw))
    recipe = {"schema_version": 1, "name": "trial", "markers": [
        {"name": "marker", "channel": 0, "low": 5, "positive_fraction": 0.9}]}
    (tmp_path / "recipe.json").write_text(json.dumps(recipe))
    return image, ["classify", str(tmp_path / "image.tif"), str(tmp_path / "labels.tif"),
        str(tmp_path / "out"), "--config", str(tmp_path / "config.yaml"),
        "--recipe", str(tmp_path / "recipe.json")]


@pytest.mark.parametrize("mode", ["volume_3d", "single_plane_2d", "max_projection_2d"])
def test_cli_uses_original_analysis_grid_and_persists_config(tmp_path, capsys, mode):
    image, args = inputs(tmp_path, mode)
    assert main(args) == 0
    payload = json.loads(capsys.readouterr().out)
    saved = reopen_classification(payload["output_dir"])
    expected = image if mode == "volume_3d" else image[1:] if mode == "single_plane_2d" else image.max(axis=0, keepdims=True)
    np.testing.assert_array_equal(saved.image.data, expected)
    assert saved.image.metadata["analysis_volume"]["mode"] == mode
    assert saved.image.metadata["run_config"]["segment"]["mode"] == mode
    assert main(["reclassify", payload["output_dir"], str(tmp_path / "out")]) == 0
    rescored = json.loads(capsys.readouterr().out)
    assert rescored["output_dir"] != payload["output_dir"]
    assert rescored["metadata"] == payload["metadata"]


def test_cli_subprocess_does_not_import_cellpose(tmp_path):
    _, args = inputs(tmp_path)
    code = """import sys
class NoCellpose:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'cellpose' or fullname.startswith('cellpose.'):
            raise RuntimeError('Classification must not import Cellpose')
sys.meta_path.insert(0, NoCellpose())
from cellquant.cli import main
raise SystemExit(main(sys.argv[1:]))
"""
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    completed = subprocess.run([sys.executable, "-c", code, *args], env=env,
                               capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stderr
    path = json.loads(completed.stdout)["output_dir"]
    repeated = subprocess.run([sys.executable, "-c", code, "reclassify", path, str(tmp_path / "new")],
                              env=env, capture_output=True, text=True, timeout=60)
    assert repeated.returncode == 0, repeated.stderr


@pytest.mark.parametrize("bad", [-1, 2**32])
def test_cli_rejects_wrapping_label_ids(tmp_path, capsys, bad):
    _, args = inputs(tmp_path)
    tifffile.imwrite(tmp_path / "labels.tif", np.full((2, 2, 2), bad, dtype=np.int64),
                     metadata={"axes": "ZYX"}, photometric="minisblack")
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2
    assert "uint32" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_cli_missing_input_gives_parser_error_without_traceback(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["reclassify", str(tmp_path / "missing"), str(tmp_path / "out")])
    assert exc.value.code == 2
    assert "error:" in capsys.readouterr().err
