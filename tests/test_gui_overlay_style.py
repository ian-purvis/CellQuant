"""Step 5 overlay style: positive/negative objects filled or outlined, remembered between images.

Needs napari, Qt and OpenGL (a desktop, or xvfb-run with Mesa). Skipped when napari is missing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

napari = pytest.importorskip("napari")

from tests.test_gui_guided_flow import _wait  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path: Path, monkeypatch):
    from qtpy.QtCore import QSettings

    from cellquant.gui import app

    path = str(tmp_path / "settings.ini")
    monkeypatch.setattr(app, "_settings", lambda: QSettings(path, QSettings.Format.IniFormat))


def test_default_is_fill_and_saved_choice_is_read():
    from cellquant.gui.app import classification_outline, save_classification_style

    assert classification_outline() is False
    save_classification_style(True)
    assert classification_outline() is True
    save_classification_style(False)
    assert classification_outline() is False


def test_overlay_style_applies_and_persists(tmp_path: Path, monkeypatch):
    from cellquant.gui import guide
    from cellquant.gui.app import CellQuantWindow, classification_outline

    monkeypatch.setattr(guide, "choose_practice_folder", lambda parent: tmp_path / "practice")
    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"napari viewer could not start: {exc}")
    shell = CellQuantWindow(viewer, None)
    monkeypatch.setattr(shell, "message", lambda _text: None)
    try:
        shell.start_practice()
        _wait(shell)
        shell.run_current()
        _wait(shell)
        shell._marker_setup.button.click()
        _wait(shell)
        panel = shell._review_panel
        assert "Classification" in viewer.layers
        assert panel.overlay_style.currentData() == "fill"
        assert viewer.layers["Classification"].contour == 0

        panel.overlay_style.setCurrentIndex(panel.overlay_style.findData("outline"))
        panel._overlay_style_chosen()
        assert viewer.layers["Classification"].contour > 0
        assert classification_outline()

        # Showing the image again (a fresh layer) keeps the chosen style.
        viewer.layers.remove("Classification")
        shell.show_current()
        _wait(shell)
        assert viewer.layers["Classification"].contour > 0

        panel.overlay_style.setCurrentIndex(panel.overlay_style.findData("fill"))
        panel._overlay_style_chosen()
        assert viewer.layers["Classification"].contour == 0
        assert not classification_outline()
        _wait(shell)
    finally:
        viewer.close()
