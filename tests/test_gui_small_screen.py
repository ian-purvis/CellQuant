"""On a small screen the CellQuant panel scrolls instead of growing taller than the window: Approve and Next stay in view.

Needs napari and Qt; skipped without napari.
"""

from __future__ import annotations

from pathlib import Path

import pytest

napari = pytest.importorskip("napari")

from tests.gui_support import _texture_sizes  # noqa: E402,F401
from tests.test_gui_crop import _events, _wait, open_window  # noqa: E402,F401


def _covering(shell, widget):
    """The widget at the centre of ``widget`` on screen (the widget itself when nothing covers it)."""

    from qtpy.QtWidgets import QApplication

    return QApplication.widgetAt(widget.mapToGlobal(widget.rect().center()))


def _is_or_inside(widget, found) -> bool:
    while found is not None:
        if found is widget:
            return True
        found = found.parent()
    return False


@pytest.mark.parametrize("size", [(1366, 768), (1280, 720)])
def test_approve_and_next_stay_in_view_on_a_small_screen(tmp_path: Path, open_window, size):  # noqa: F811
    from cellquant.practice import create_practice_experiment

    controller = create_practice_experiment(tmp_path / "practice")
    controller.save()
    shell = open_window(controller.directory)
    window = shell.viewer.window._qt_window
    window.show()
    window.resize(*size)
    _events(0.8)
    shell.run_current()
    assert _wait(shell, 120)
    shell._marker_setup.button.click()
    assert _wait(shell, 120)

    assert shell._dock.mapToGlobal(shell._dock.rect().bottomLeft()).y() <= shell._footer.mapToGlobal(shell._footer.rect().topLeft()).y() + 1
    footer_top = shell._footer.mapToGlobal(shell._footer.rect().topLeft()).y()
    for step in range(6):
        shell.go_to_step(step)
        _events(0.5)
        page = shell._step_pages[step]
        buttons = [page.next, page.back] + ([shell._review_panel.approve_button, shell._review_panel.edit_cells] if step == 4 else [])
        for button in buttons:
            assert button.isVisibleTo(page)
            bottom = button.mapToGlobal(button.rect().bottomLeft()).y()
            assert bottom < footer_top, f"step {step + 1}: {button.text()} is under the footer"
            top = button.mapToGlobal(button.rect().topLeft()).y()
            assert top >= shell._dock.mapToGlobal(shell._dock.rect().topLeft()).y()
            found = _covering(shell, button)  # None when this virtual screen is smaller than the window
            assert found is None or _is_or_inside(button, found), f"step {step + 1}: {button.text()} is covered by {found}"
        assert not page.panel_scroll.horizontalScrollBar().isVisible()  # only up and down at the normal dock width

    # The Start tab scrolls as well, so it does not set the panel's height.
    shell.go_to_start()
    _events(0.3)
    assert shell._dock.minimumSizeHint().height() < 500
