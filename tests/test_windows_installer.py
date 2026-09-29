"""Windows installer and launcher scripts.

PowerShell cannot run here, so these tests check the scripts statically (see
tests/ps_lint.py) and run the Python helpers the installer calls.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from cellquant import engines
from tests import ps_lint

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "packaging" / "windows"
BATCH_FILES = [ROOT / "Install CellQuant.bat", ROOT / "Open CellQuant.bat"]
SCRIPTS = sorted(WINDOWS.glob("*.ps1"))


def test_expected_files_exist():
    names = {path.name for path in SCRIPTS}
    assert names == {"install_windows.ps1", "launch_cellquant.ps1", "cellquant_env.ps1", "hardware.ps1", "resolve_conda.ps1"}
    for helper in ("probe_torch_cuda.py", "smoke_test.py", "download_models.py"):
        assert (WINDOWS / helper).is_file()
    for path in BATCH_FILES:
        assert path.is_file()


@pytest.mark.parametrize("path", BATCH_FILES + SCRIPTS, ids=lambda path: path.name)
def test_scripts_are_ascii_with_windows_line_endings(path: Path):
    data = path.read_bytes()
    # Windows PowerShell 5.1 reads BOM-less files as ANSI. Non-ASCII text such as an
    # em dash turns into characters PowerShell may treat as quotes.
    assert data.isascii(), f"{path.name} contains non-ASCII characters"
    assert b"\n" not in data.replace(b"\r\n", b""), f"{path.name} has LF-only line endings"


def test_batch_files_start_scripts_that_exist():
    for path in BATCH_FILES:
        text = path.read_text(encoding="ascii")
        referenced = re.findall(r'%~dp0([^"]+\.ps1)', text)
        assert referenced, path.name
        for relative in referenced:
            assert (ROOT / relative.replace("\\", "/")).is_file(), relative


def test_dot_sourced_scripts_exist():
    for path in SCRIPTS:
        for name in re.findall(r"\(Join-Path \$ScriptDir '([^']+)'\)", path.read_text(encoding="ascii")):
            assert (WINDOWS / name).is_file(), f"{path.name} loads missing {name}"


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda path: path.name)
def test_powershell_syntax_checks(path: Path):
    assert ps_lint.check_script(path) == []


def test_powershell_calls_use_declared_parameters():
    assert ps_lint.check_calls(SCRIPTS) == []


def test_the_checker_catches_planted_mistakes(tmp_path: Path):
    bad = tmp_path / "bad.ps1"
    bad.write_text(
        "function Get-CellQuantThing {\n"
        "    param([string]$Name)\n"
        "    $x = $a ?? 'b'\n"
        "    if ($x { Write-Host \"a $(Get-CellQuantThing -Name 'q') b\" }\n"
        "}\n"
        "Get-CellQuantThing -Nmae 'z'\n"
        "Get-CellQuantMissing\n",
        encoding="ascii",
    )
    problems = ps_lint.check_script(bad) + ps_lint.check_calls([bad])
    text = "\n".join(problems)
    assert "'??' needs PowerShell 7" in text
    assert "is never closed" in text
    assert "has no parameter -Nmae" in text
    assert "undefined function Get-CellQuantMissing" in text


def test_engine_keys_and_extras_match_python_and_pyproject():
    env_script = (WINDOWS / "cellquant_env.ps1").read_text(encoding="ascii")
    extras = dict(re.findall(r"(cellpose[34]) = '(cellpose-v[34])'", env_script))
    assert extras == {"cellpose4": "cellpose-v4", "cellpose3": "cellpose-v3"}
    assert set(extras) == {engines.CELLPOSE_SAM, engines.CELLPOSE_CLASSIC}
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    optional = project["project"]["optional-dependencies"]
    assert optional["cellpose-v4"] == ["cellpose>=4.2,<5"]
    assert optional["cellpose-v3"] == ["cellpose>=3.1,<4"]
    assert any(item.startswith("napari[pyqt6]") for item in optional["gui"]), "napari needs a Qt backend"


def test_cuda_wheel_table_matches_cellquant_v1():
    """The CUDA tag table is copied from v1, which works on the lab's computers."""

    table = re.findall(r"Min = \[version\]'([\d.]+)'; Tag = '(cu\d+)'", (WINDOWS / "hardware.ps1").read_text(encoding="ascii"))
    assert table == [
        ("13.0", "cu130"), ("12.9", "cu129"), ("12.8", "cu128"), ("12.6", "cu126"),
        ("12.4", "cu124"), ("12.1", "cu121"), ("11.8", "cu118"),
    ]


def test_installer_installs_gpu_pytorch_before_cellpose():
    text = (WINDOWS / "install_windows.ps1").read_text(encoding="ascii")
    body = text[text.index("function Install-CellQuantEngine") :]
    assert body.index("--index-url', $IndexUrl") < body.index('"${ProjectRoot}[gui,$Extra]"')
    assert "'torch', 'torchvision'" in body  # Cellpose 4 needs a matching torchvision
    assert "--override-channels', '--channel', 'conda-forge'" in body


@pytest.mark.parametrize("name", ["probe_torch_cuda.py", "smoke_test.py", "download_models.py"])
def test_python_helpers_compile(name: str):
    compile((WINDOWS / name).read_text(encoding="utf-8"), name, "exec")


@pytest.mark.skipif(engines.cellpose_engine().installed, reason="Cellpose is installed here")
def test_smoke_test_reports_missing_cellpose_and_fails():
    result = subprocess.run(
        [sys.executable, str(WINDOWS / "smoke_test.py"), "cellpose4"],
        capture_output=True, text=True, cwd=ROOT, timeout=120,
    )
    last = result.stdout.strip().splitlines()[-1]
    assert last.startswith("CELLQUANT_SMOKE ")
    assert result.returncode == 1
