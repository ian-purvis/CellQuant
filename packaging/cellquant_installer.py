"""Install_CellQuant.exe: run "Install CellQuant.bat" from the project folder.

All install logic is in packaging/windows/install_windows.ps1, which the .bat
file starts. It checks the hardware, installs the Cellpose engines the user
chooses into conda environments, and adds GPU support where possible.
"""

from __future__ import annotations

import os
import sys

from cellquant_paths import find_project_root, message


def main() -> int:
    root = find_project_root()
    if root is None:
        message("Install_CellQuant.exe needs to sit in the CellQuant project folder, next to Install CellQuant.bat.")
        return 1
    script = root / "Install CellQuant.bat"
    if not script.is_file():
        message(f"Install CellQuant.bat was not found in {root}.")
        return 1
    os.startfile(str(script))  # noqa: S606
    return 0


if __name__ == "__main__":
    sys.exit(main())
