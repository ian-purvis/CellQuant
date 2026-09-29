"""Locations shared by the CellQuant launcher and the dependency installer."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def application_directory() -> Path:
    """Folder that contains the exe, or this source tree when run as a script."""

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def find_project_root() -> Path | None:
    """Find the folder that contains pyproject.toml and the cellquant package."""

    seeds = [application_directory(), Path.cwd()]
    seen: set[Path] = set()
    for seed in seeds:
        for candidate in [seed, *seed.parents]:
            if candidate in seen:
                continue
            seen.add(candidate)
            if (candidate / "pyproject.toml").is_file() and (candidate / "cellquant").is_dir():
                return candidate
    return None


def environment_dir() -> Path:
    """Short path outside OneDrive. Deep project paths hit Windows' path limit."""

    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / "CellQuant" / "venv"


def message(text: str) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, text, "CellQuant", 0x40)
    except Exception:
        print(text)
