"""Download the default Cellpose model now, so the first analysis does not stall.

Usage: python download_models.py

Cellpose-SAM weights are over 1 GB. Classic Cellpose downloads the nuclei model
and its size model. Cellpose caches them in the user's .cellpose folder.
"""

from __future__ import annotations

import sys


def main() -> int:
    from cellquant.engines import CELLPOSE_CLASSIC, cellpose_engine

    engine = cellpose_engine()
    if not engine.installed:
        print("Cellpose is not installed; nothing to download.")
        return 1
    from cellpose import models

    print(f"Downloading the {engine.default_model} model for {engine.label}...")
    if engine.key == CELLPOSE_CLASSIC:
        models.Cellpose(gpu=False, model_type=engine.default_model)
    else:
        models.CellposeModel(gpu=False, pretrained_model=engine.default_model)
    print("Model ready.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - the installer treats this as a warning
        print(f"Model download failed: {exc}")
        sys.exit(1)
