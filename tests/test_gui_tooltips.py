"""Every button, check box, menu, number box and slider on the six step pages, the Start tab, the Plan and Measure windows and
the New experiment dialog has a hover tip.

Needs napari and Qt; skipped without napari.
"""

from __future__ import annotations

from pathlib import Path

import pytest

napari = pytest.importorskip("napari")

from tests.gui_support import _texture_sizes  # noqa: E402,F401
from tests.test_gui_crop import _events, _wait, open_window  # noqa: E402,F401


def _without_tip(root, where: str) -> list[str]:
    from qtpy.QtWidgets import QAbstractButton, QAbstractSlider, QAbstractSpinBox, QComboBox, QScrollBar, QWidget

    missing = []
    for widget in root.findChildren(QWidget):
        if not isinstance(widget, (QAbstractButton, QComboBox, QAbstractSpinBox, QAbstractSlider)) or isinstance(widget, QScrollBar):
            continue
        if widget.objectName() == "qt_tableview_cornerbutton" or type(widget.parent()).__name__ in ("QTabBar", "QScrollBar"):
            continue  # Qt's own parts
        if not widget.toolTip().strip():
            text = getattr(widget, "text", lambda: "")()
            if isinstance(widget, QComboBox):
                text = widget.currentText()
            missing.append(f"{where}: {type(widget).__name__} {text!r}")
    return missing


def test_every_control_has_a_tooltip(tmp_path: Path, open_window):  # noqa: F811
    from cellquant.gui import guide
    from cellquant.gui.app import NewExperimentDialog
    from cellquant.practice import create_practice_experiment

    controller = create_practice_experiment(tmp_path / "practice")
    controller.save()
    shell = open_window(controller.directory)
    shell.run_current()
    assert _wait(shell, 120)
    shell._marker_setup.button.click()  # markers exist, so step 4 lists them and step 5 and 6 have their controls
    assert _wait(shell, 120)
    shell.show_plan()
    shell.show_measure()
    _events(0.5)

    missing = _without_tip(shell._start_page, "Start")
    for number, page in enumerate(shell._step_pages, start=1):
        shell.go_to_step(number - 1)
        _events(0.2)
        missing += _without_tip(page, f"step {number}")
    missing += _without_tip(shell._plan_dock, "Plan")
    missing += _without_tip(shell._measure_dock, "Measure")
    dialog = NewExperimentDialog(shell._tabs)
    missing += _without_tip(dialog, "New experiment")
    missing += _without_tip(guide.GuideDialog(shell._tabs, "text"), "Guide")
    assert missing == [], "\n".join(missing)
