"""Build CellQuant.exe and Install_CellQuant.exe next to the project."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = Path(__file__).resolve().parent
WORK = Path(os.environ.get("TEMP", ".")) / "CellQuant-pyinstaller"
DIST = WORK / "dist"


def main() -> int:
    subprocess.check_call([sys.executable, "-m", "PyInstaller", "--version"])
    _build("CellQuant", PACKAGING / "cellquant_launcher.py", windowed=True)
    _build("Install_CellQuant", PACKAGING / "cellquant_installer.py", windowed=False)
    for name in ("CellQuant.exe", "Install_CellQuant.exe"):
        source = DIST / name
        target = ROOT / name
        shutil.copy2(source, target)
        print(f"Wrote {target}")
    return 0


def _build(name: str, script: Path, *, windowed: bool) -> None:
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        name,
        "--distpath",
        str(DIST),
        "--workpath",
        str(WORK / name),
        "--specpath",
        str(WORK),
        "--paths",
        str(PACKAGING),
        str(script),
    ]
    if windowed:
        command.insert(6, "--windowed")
    subprocess.check_call(command)


if __name__ == "__main__":
    raise SystemExit(main())
