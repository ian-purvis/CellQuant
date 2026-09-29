"""CellQuant.exe: open "Open CellQuant.bat" from the project folder.

The .bat file asks which Cellpose engine to use and starts the matching
environment. This exe only exists so there is a double-clickable program icon.
"""

from __future__ import annotations

import os
import sys

from cellquant_paths import find_project_root, message


def main() -> int:
    root = find_project_root()
    if root is None:
        message("CellQuant.exe needs to sit in the CellQuant project folder, next to Open CellQuant.bat.")
        return 1
    script = root / "Open CellQuant.bat"
    if not script.is_file():
        message(f"Open CellQuant.bat was not found in {root}.")
        return 1
    os.startfile(str(script))  # noqa: S606 - opens a console window for the engine question
    return 0


if __name__ == "__main__":
    sys.exit(main())
