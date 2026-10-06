"""Crop to the region of interest in the window (C1): step 2 settings, the warning, and the rectangles in review.

Needs napari and Qt; skipped without napari.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

napari = pytest.importorskip("napari")

import tifffile  # noqa: E402

from tests.test_crop import CROP, PIXEL, _image, _recipe  # noqa: E402


def _events(seconds: float = 0.2) -> None:
    from qtpy.QtWidgets import QApplication

    end = time.time() + seconds
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.01)


def _wait(shell, seconds: float = 120) -> bool:
    """Wait for the window's work to finish; returns whether it did."""

    from qtpy.QtWidgets import QApplication

    end = time.time() + seconds
    while (shell._job is not None or shell._batch is not None or shell._loader is not None) and time.time() < end:
        QApplication.processEvents()
        time.sleep(0.02)
    _events(0.1)
    return shell._job is None and shell._batch is None


@pytest.fixture
def open_window():
    windows = []

    def make(directory):
        from cellquant.gui.app import CellQuantWindow

        try:
            viewer = napari.Viewer(show=False)
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"napari viewer could not start: {exc}")
        shell = CellQuantWindow(viewer, directory)
        windows.append((shell, viewer))
        _wait(shell)
        return shell

    yield make
    for shell, viewer in windows:
        _wait(shell, 30)
        _events(0.5)
        shell._batch = None
        shell._job = None
        viewer.close()
        _events(0.2)


def test_crop_settings_warning_and_rectangles_in_the_window(tmp_path: Path, open_window):
    from qtpy.QtCore import Qt

    from cellquant.controller import AnalysisController

    path = tmp_path / "retina.tif"
    tifffile.imwrite(path, _image(), photometric="minisblack")
    controller = AnalysisController.create(tmp_path / "experiment", "Crop")
    controller.add_image_paths([path])
    image_id = controller.experiment.images[0].image_id
    controller.set_pixel_size(image_id, PIXEL, PIXEL)
    controller.set_recipe(_recipe("classical", CROP))
    controller.save()
    shell = open_window(controller.directory)
    panel = shell._objects_panel
    assert panel.crop.isChecked() and not panel.crop_warning.text()
    # Requiring the numerator-only channel (index 2) is warned about.
    panel.crop_channels.item(2).setCheckState(Qt.Checked)
    _events(0.2)
    assert "only in the numerator" in panel.crop_warning.text()
    panel.crop_channels.item(2).setCheckState(Qt.Unchecked)
    _events(0.2)
    assert not panel.crop_warning.text()
    shell.run_current()
    assert _wait(shell, 120)
    assert "Crop regions" in shell.viewer.layers
    assert len(shell.viewer.layers["Crop regions"].data) == len(shell.controller.recall(image_id).provenance["crop_rectangles"])
