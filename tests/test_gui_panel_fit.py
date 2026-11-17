"""Docked panels are never drawn over each other: the Plan opens in a window of its own, and the panels beside the
image (Plan, Measure, View, then napari's layer list and layer controls) scroll when the window is too short for them.

Needs napari and Qt; skipped without napari.
"""

from __future__ import annotations

from pathlib import Path

import pytest

napari = pytest.importorskip("napari")

from tests.gui_support import _texture_sizes  # noqa: E402,F401
from tests.test_gui_crop import _events, _wait, open_window  # noqa: E402,F401


def _docked(window):
    from qtpy.QtWidgets import QDockWidget

    return [dock for dock in window.findChildren(QDockWidget) if dock.isVisible() and not dock.isFloating()]


def _overlaps(window) -> list[str]:
    """Pairs of docked panels drawn over each other, and panels reaching outside the window."""

    docks = _docked(window)
    found = [f"{dock.windowTitle()} outside the window" for dock in docks if not window.rect().contains(dock.geometry())]
    for index, first in enumerate(docks):
        for second in docks[index + 1:]:
            if first.geometry().intersects(second.geometry()):
                found.append(f"{first.windowTitle()} / {second.windowTitle()}")
    return found


def _open(tmp_path: Path, open_window, size):  # noqa: F811
    from cellquant.practice import create_practice_experiment

    controller = create_practice_experiment(tmp_path / "practice")
    controller.save()
    shell = open_window(controller.directory)
    window = shell.viewer.window._qt_window
    window.show()
    _events(0.8)  # the window is free to shrink below what its panels ask for only after it is first shown
    window.resize(*size)
    _events(0.8)
    return shell, window


def _long_run_status(shell) -> None:
    """The bar at the bottom as it is during a whole-volume 3D step: long lines that wrap."""

    name = "7_Rd1_Batch 9_D35+2W_plAA54_RO1_slide 1_sect 3_DAPI_GFP_mCherry_panBRN3_240613.nd2"
    footer = shell._footer
    footer.set_position(0, 90, "ATOH7 CRISPR/" + name)
    footer.start_busy(True)
    footer.update_progress(2, 90, name, "running")
    footer.show_step("Finding objects: whole volume on the GPU (cannot be stopped part-way)", -1)


def test_plan_opens_in_its_own_window(tmp_path: Path, open_window):  # noqa: F811
    shell, window = _open(tmp_path, open_window, (1700, 900))
    before = {dock.windowTitle(): dock.geometry() for dock in _docked(window)}
    shell.show_plan()
    _events(0.8)
    dock = shell._plan_dock_widget
    assert dock.isVisible() and dock.isFloating()
    assert dock.height() >= 480 and dock.width() >= 560  # room for the list, not Qt's smallest size
    assert {panel.windowTitle(): panel.geometry() for panel in _docked(window)} == before  # the image keeps its room
    # Docked by the user, closed and opened again: it stays where it was put.
    dock.setFloating(False)
    _events(0.5)
    shell.show_plan()
    _events(0.3)
    assert not dock.isFloating()
    try:
        _long_run_status(shell)
        _events(0.5)
        assert _overlaps(window) == []
    finally:
        shell._footer.end_busy()


@pytest.mark.parametrize("size", [(1700, 900), (1366, 768), (1280, 720)])
def test_docked_panels_scroll_instead_of_overlapping(tmp_path: Path, open_window, size):  # noqa: F811
    shell, window = _open(tmp_path, open_window, size)
    assert _overlaps(window) == []
    shell.show_plan()
    plan, view = shell._plan_dock_widget, next(dock for dock in _docked(window) if dock.windowTitle() == "View")
    plan.setFloating(False)  # as it opened before, in the column left of the image
    _events(0.8)
    try:
        _long_run_status(shell)
        _events(0.8)
        assert _overlaps(window) == []
        # The Plan is shorter than its controls need, so it scrolls; it gives way before the other panels.
        assert plan.widget().squeezed() and plan.widget().verticalScrollBar().isVisible()
        assert plan.widget().height() < plan.widget().full_height()
        if size[1] >= 900:
            assert not view.widget().squeezed() and not view.widget().verticalScrollBar().isVisible()
        # Measure docked under the CellQuant panel as well.
        shell.show_measure()
        shell._measure_dock_widget.setFloating(False)
        _events(0.8)
        assert _overlaps(window) == []
    finally:
        shell._footer.end_busy()
    # Popped out again, the Plan has its full size and the panels left behind are not squeezed for it.
    plan.setFloating(True)
    _events(0.8)
    assert not plan.widget().squeezed() and plan.widget().height() >= plan.widget().full_height()
    assert _overlaps(window) == []


def test_panels_keep_their_full_height_when_the_window_has_room(tmp_path: Path, open_window):  # noqa: F811
    shell, window = _open(tmp_path, open_window, (1700, 900))
    view = next(dock for dock in _docked(window) if dock.windowTitle() == "View")
    assert not view.widget().squeezed() and view.widget().height() >= view.widget().full_height()
    assert not view.widget().verticalScrollBar().isVisible()
    # Made too short for the layer panels and the View panel together, View scrolls; made tall again, it does not.
    window.resize(1280, 700)
    _events(0.8)
    assert view.widget().squeezed() and _overlaps(window) == []
    window.resize(1700, 900)
    _events(0.8)
    assert not view.widget().squeezed() and view.widget().height() >= view.widget().full_height()
    assert _overlaps(window) == []
