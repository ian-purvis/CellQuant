"""Shared helpers for the window tests of the step panels (not a test file)."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _texture_sizes(monkeypatch):
    """napari reads the graphics card's texture limits when it draws object numbers; offscreen there is no card, so give it some."""

    try:
        import napari._vispy.layers.labels as labels
    except Exception:  # noqa: BLE001 - no napari here
        return
    monkeypatch.setattr(labels, "get_max_texture_sizes", lambda: (4096, 256), raising=False)
