"""Mac and Linux installer and launcher scripts (packaging/unix)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
UNIX = ROOT / "packaging" / "unix"
ENTRY_POINTS = [ROOT / f"{name}.{ext}" for name in ("Install CellQuant", "Open CellQuant") for ext in ("command", "sh")]
SCRIPTS = sorted(UNIX.glob("*.sh"))


@pytest.mark.parametrize("path", ENTRY_POINTS + SCRIPTS, ids=lambda path: path.name)
def test_scripts_are_executable_lf_and_parse(path: Path):
    data = path.read_bytes()
    assert data.isascii() and b"\r" not in data, path.name
    if path.name != "common.sh":  # sourced, not run
        assert data.startswith(b"#!/usr/bin/env bash\n"), path.name
        assert os.access(path, os.X_OK), f"{path.name} is not executable"
    assert subprocess.run(["bash", "-n", str(path)], capture_output=True).returncode == 0


def test_entry_points_start_scripts_that_exist():
    for path in ENTRY_POINTS:
        for relative in re.findall(r'bash "(packaging/unix/[^"]+)"', path.read_text()):
            assert (ROOT / relative).is_file(), relative


def test_installer_installs_the_statistics_extra_like_windows():
    unix = (UNIX / "install_unix.sh").read_text()
    windows = (ROOT / "packaging" / "windows" / "install_windows.ps1").read_text(encoding="ascii")
    assert 'requirement="${PROJECT_ROOT}[gui,stats,$extra]"' in unix
    assert '"${ProjectRoot}[gui,stats,$Extra]"' in windows
    common = (UNIX / "common.sh").read_text()
    launcher = (UNIX / "launch_cellquant.sh").read_text()
    assert "CQ_STATS_PACKAGES='statsmodels matplotlib'" in common
    assert launcher.index('cq_install_stats_packages "$CQ_CONDA" "$PREFIX"') < launcher.index('exec "$CQ_CONDA" run')


def test_stats_packages_are_added_only_when_missing(tmp_path: Path):
    """The launcher's check: exit 3 means a library is missing, and only then is pip run."""

    conda = tmp_path / "conda"
    calls = tmp_path / "calls"
    conda.write_text(f"#!/usr/bin/env bash\necho \"$@\" >> {calls}\ncase \"$*\" in *find_spec*) exit ${{CHECK:-0}};; esac\n")
    conda.chmod(0o755)
    script = f'. "{UNIX / "common.sh"}"; cq_install_stats_packages "{conda}" /env'
    for check, pip in (("0", False), ("3", True)):
        calls.write_text("")
        result = subprocess.run(["bash", "-c", script], env={**os.environ, "CHECK": check}, capture_output=True, text=True)
        assert result.returncode == 0
        assert ("pip install" in calls.read_text()) is pip
        assert ("statsmodels matplotlib" in calls.read_text()) is pip


def test_miniforge_is_pinned_like_windows():
    common = (UNIX / "common.sh").read_text()
    windows = (ROOT / "packaging" / "windows" / "resolve_conda.ps1").read_text()
    version = re.search(r"CQ_MINIFORGE_VERSION='([^']+)'", common).group(1)
    assert f"$script:CellQuantMiniforgeVersion = '{version}'" in windows
    hashes = dict(re.findall(r"(\w+-\w+)\) echo '([0-9a-f]{64})'", common))
    assert set(hashes) == {"Linux-x86_64", "Linux-aarch64", "MacOSX-arm64", "MacOSX-x86_64"}


def _record(tmp_path: Path, *args: str, stdin: str = "") -> str:
    env = dict(os.environ, HOME=str(tmp_path), XDG_DATA_HOME=str(tmp_path / "data"))
    result = subprocess.run(
        [sys.executable, str(UNIX / "env_record.py"), *args], input=stdin, capture_output=True, text=True, env=env, check=True
    )
    return result.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="Mac and Linux record")
def test_env_record_round_trip(tmp_path: Path):
    prefix = tmp_path / "envs" / "cellquant2-cellpose3"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin" / "python").write_text("")
    _record(tmp_path, "save", "cellpose3", str(prefix), "3.1.1", "", "", "cellpose4", "cellpose3", str(tmp_path / "envs"), str(ROOT))
    _record(tmp_path, "save", "cellpose4", str(tmp_path / "gone"), "4.0.6", "", "", "cellpose4", "cellpose3", str(tmp_path / "envs"), str(ROOT))
    assert _record(tmp_path, "installed") == f"cellpose3\t{prefix}\n"
    assert _record(tmp_path, "recorded").splitlines()[0].startswith("cellpose4\t")
    assert _record(tmp_path, "get", "default_engine").strip() == "cellpose4"
    record = json.loads(Path(_record(tmp_path, "path").strip()).read_text())
    assert record["engines"]["cellpose3"]["cellpose_version"] == "3.1.1"
    _record(tmp_path, "forget")
    assert _record(tmp_path, "installed") == ""


def test_env_record_reads_smoke_test_output(tmp_path: Path):
    good = 'noise\nCELLQUANT_SMOKE {"ok": true, "test_analysis": "2 objects, 50% positive", "default_model": "nuclei", "gpu": {"available": false}}\n'
    assert _record(tmp_path, "smoke", stdin=good).startswith("ok\tTest passed: 2 objects, 50% positive.")
    bad = 'CELLQUANT_SMOKE {"ok": false, "error": "Cellpose is not installed in this environment."}\n'
    assert _record(tmp_path, "smoke", stdin=bad).strip() == "fail\tCellpose is not installed in this environment."
