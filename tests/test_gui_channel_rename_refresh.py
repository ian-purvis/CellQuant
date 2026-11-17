"""After a channel is renamed, every panel shows the new name at once (steps 1 to 6 and the Start page).

Needs napari and Qt; skipped without napari.
"""

from __future__ import annotations

from pathlib import Path

import pytest

napari = pytest.importorskip("napari")

from tests.gui_support import _texture_sizes  # noqa: E402,F401
from tests.test_gui_crop import _events, _wait, open_window  # noqa: E402,F401


def _texts(root) -> list[str]:
    """Everything a person can read in this part of the window: labels, buttons, list items and table cells."""

    from qtpy.QtWidgets import QAbstractButton, QComboBox, QLabel, QTableWidget

    found = []
    for widget in root.findChildren(object):
        if isinstance(widget, (QLabel, QAbstractButton)):
            found.append(widget.text())
        elif isinstance(widget, QComboBox):
            found.extend(widget.itemText(index) for index in range(widget.count()))
        elif isinstance(widget, QTableWidget):
            for row in range(widget.rowCount()):
                for column in range(widget.columnCount()):
                    item = widget.item(row, column)
                    if item is not None:
                        found.append(item.text())
                    cell = widget.cellWidget(row, column)
                    if isinstance(cell, QComboBox):
                        found.extend(cell.itemText(index) for index in range(cell.count()))
            for column in range(widget.columnCount()):
                header = widget.horizontalHeaderItem(column)
                if header is not None:
                    found.append(header.text())
    return found


def _stale(shell, old: str) -> list[str]:
    roots = [shell._start_page, *shell._step_pages, shell._marker_setup, shell._results_summary]
    return [text for root in roots for text in _texts(root) if old in text]


def test_renaming_a_channel_leaves_no_old_name_on_screen(tmp_path: Path, open_window):  # noqa: F811
    from cellquant.practice import create_practice_experiment

    controller = create_practice_experiment(tmp_path / "practice")
    controller.save()
    shell = open_window(controller.directory)
    shell.run_current()
    assert _wait(shell, 120)
    shell._marker_setup.button.click()
    assert _wait(shell, 120)
    _events(0.3)
    assert any("Marker A" in text for text in _texts(shell._experiment_panel)), "the old name is on screen before the rename"
    assert any("Marker A" in text for text in _texts(shell._measurements_panel))

    layer = next(item for item in shell.viewer.layers if item.name.startswith("Channel 2"))
    layer.name = "Zeta"  # what typing a name in napari's layer list does
    _events(0.6)

    stale = _stale(shell, "Marker A")
    assert stale == [], stale
    assert "Zeta" in shell._experiment_panel.channel_order.text()
    assert any("Zeta" in text for text in _texts(shell._measurements_panel))
    assert any("Zeta" in text for text in _texts(shell._results_panel))
    assert "Zeta" in shell._marker_setup.current.text()

    # The settings keep the name the marker was made with: nothing is unapproved or measured again, and
    # the name in the Markers table is not written back over it.
    stored = [item.name for item in shell.controller.recipe.classifications]
    shell._measurements_panel.write_recipe()
    assert [item.name for item in shell.controller.recipe.classifications] == stored
    assert "Marker A" in stored and "Zeta" in shell.controller.marker_names().values()
