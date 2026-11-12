"""Step 3: drawing in the Objects layer is recorded by itself (moving on, leaving the tab, running, exporting)
and is never dropped silently (closing, opening another experiment).

Needs napari and Qt; skipped without napari.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

napari = pytest.importorskip("napari")

from tests.test_gui_crop import _events, _wait, open_window  # noqa: E402,F401
from tests.test_workflow import _recipe, _write_squares  # noqa: E402


def _shell(tmp_path: Path, open_window, images: int = 2):  # noqa: F811
    from cellquant.controller import AnalysisController

    paths = []
    for index in range(images):
        path = tmp_path / f"{'ab'[index]}.tif"
        _write_squares(path)
        paths.append(path)
    controller = AnalysisController.create(tmp_path / "experiment", "Drawing")
    controller.add_image_paths(paths)
    controller.set_recipe(_recipe())
    controller.save()
    shell = open_window(controller.directory)
    shell.run_current()
    assert _wait(shell, 120)
    return shell


def _paint(shell, value: int = 7) -> np.ndarray:
    layer = shell.viewer.layers["Objects"]
    painted = np.array(layer.data, copy=True)
    painted[60:65, 60:65] = value
    layer.data = painted
    shell._edit_panel._show_unrecorded()
    return painted


def test_indicator_follows_unrecorded_drawing(tmp_path: Path, open_window):  # noqa: F811
    shell = _shell(tmp_path, open_window)
    panel = shell._edit_panel
    shell.go_to_step(2)
    _events(0.2)
    assert not panel.unrecorded_note.isVisibleTo(panel)
    _paint(shell)
    assert panel.unrecorded_note.isVisibleTo(panel) and "Unrecorded drawing on this image" in panel.unrecorded_note.text()
    # The record button and the indicator are neighbours: the note sits just above the record row.
    record_row = panel._buttons["Record drawn edits"].parentWidget()
    assert panel.layout().indexOf(panel.unrecorded_note) == panel.layout().indexOf(record_row) - 1
    panel._commit()
    assert _wait(shell)
    _events(0.3)
    assert not panel.unrecorded_note.isVisibleTo(panel) and not panel._pending


def test_leaving_the_tab_records_the_drawing(tmp_path: Path, open_window):  # noqa: F811
    shell = _shell(tmp_path, open_window)
    controller, panel = shell.controller, shell._edit_panel
    image_id = controller.experiment.images[0].image_id
    shell.go_to_step(2)
    _events(0.2)
    _paint(shell)
    before = controller.edit_count(image_id)
    shell.go_to_step(3)  # step 4
    _events(0.3)
    assert controller.edit_count(image_id) > before
    assert not panel._pending and not panel.unrecorded_note.isVisibleTo(panel)
    assert int(np.asarray(shell.viewer.layers["Objects"].data)[62, 62]) != 0


def test_a_run_and_an_export_record_the_drawing_first(tmp_path: Path, open_window, monkeypatch):  # noqa: F811
    from qtpy.QtWidgets import QFileDialog

    shell = _shell(tmp_path, open_window)
    controller, panel = shell.controller, shell._edit_panel
    image_id = controller.experiment.images[0].image_id
    _paint(shell)
    before = controller.edit_count(image_id)
    shell.run_current()
    assert _wait(shell, 120)
    assert controller.edit_count(image_id) > before and not panel._pending

    _paint(shell, 9)
    before = controller.edit_count(image_id)
    chosen = tmp_path / "chosen"
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *args, **kwargs: str(chosen)))
    shell.export_dialog()
    assert _wait(shell, 120)
    assert controller.edit_count(image_id) > before and not panel._pending
    # The export went into a new dated folder inside the chosen one.
    assert shell._exported_to.parent == chosen and shell._exported_to != chosen
    assert (shell._exported_to / "objects.csv").is_file() and not (chosen / "objects.csv").exists()
    import re

    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{4}_" + controller.active_analysis().name.replace(" ", "_"), shell._exported_to.name)


def test_a_failed_recording_keeps_the_drawing_and_says_why(tmp_path: Path, open_window, monkeypatch):  # noqa: F811
    from cellquant.errors import CellQuantError

    shell = _shell(tmp_path, open_window)
    controller, panel = shell.controller, shell._edit_panel
    shell.go_to_step(2)
    painted = _paint(shell)

    def refuse(image_id, drawn):
        raise CellQuantError("the file is read-only")

    monkeypatch.setattr(controller, "commit_drawn_labels", refuse)
    assert panel.record_unrecorded() is False
    assert "Not recorded: the file is read-only" in panel.record_status.text()
    assert len(panel._pending) == 1 and panel.unrecorded_note.isVisibleTo(panel)
    assert np.array_equal(np.asarray(shell.viewer.layers["Objects"].data), painted)


def test_closing_with_unrecorded_drawing_asks_record_discard_cancel(tmp_path: Path, open_window, monkeypatch):  # noqa: F811
    from cellquant.gui.app import CellQuantWindow

    shell = _shell(tmp_path, open_window)
    controller, panel = shell.controller, shell._edit_panel
    image_id = controller.experiment.images[0].image_id
    asked: list[int] = []
    answers = ["cancel", "discard", "record"]

    def ask(self, images):
        asked.append(images)
        return answers.pop(0)

    monkeypatch.setattr(CellQuantWindow, "_ask_unrecorded", ask)
    assert shell._allow_close() is True and not asked  # nothing drawn: no question

    original = np.array(shell.viewer.layers["Objects"].data, copy=True)
    _paint(shell)
    before = controller.edit_count(image_id)
    assert shell._allow_close() is False and asked == [1]  # Cancel: the window stays open, drawing kept
    assert controller.edit_count(image_id) == before and panel.unrecorded_count() == 1
    assert shell._allow_close() is True  # Discard: gone, nothing recorded
    assert controller.edit_count(image_id) == before and panel.unrecorded_count() == 0
    assert np.array_equal(np.asarray(shell.viewer.layers["Objects"].data), original)

    _paint(shell)
    assert shell._allow_close() is True  # Record: kept
    assert controller.edit_count(image_id) > before and panel.unrecorded_count() == 0


def test_opening_another_experiment_asks_first(tmp_path: Path, open_window, monkeypatch):  # noqa: F811
    from cellquant.controller import AnalysisController
    from cellquant.gui.app import CellQuantWindow

    shell = _shell(tmp_path, open_window)
    other = tmp_path / "other"
    other_image = tmp_path / "c.tif"
    _write_squares(other_image)
    other_controller = AnalysisController.create(other, "Other")
    other_controller.add_image_paths([other_image])
    other_controller.save()
    first = shell.controller
    _paint(shell)
    monkeypatch.setattr(CellQuantWindow, "_ask_unrecorded", lambda self, images: "cancel")
    shell.open_experiment(other)
    assert shell.controller is first  # cancelled: still the same experiment, drawing still there
    assert shell._edit_panel.unrecorded_count() == 1
    monkeypatch.setattr(CellQuantWindow, "_ask_unrecorded", lambda self, images: "discard")
    shell.open_experiment(other)
    _events(0.3)
    assert shell.controller is not first
