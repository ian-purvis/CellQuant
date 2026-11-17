"""Choosing Discard and then cancelling the next step keeps the recovery copy: it is deleted only when the unsaved project is really replaced
or closed. Needs Qt (xvfb-run on Linux) and matplotlib."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("matplotlib")


@pytest.fixture
def window(qtbot):
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    window = QuantAnalysisWindow()
    qtbot.addWidget(window)
    window.show()
    return window


def _recovery_files() -> list[Path]:
    from cellquant.gui.quantanalysis import window as window_module

    folder = window_module.recovery_folder()
    return sorted(folder.glob("*.cqstats")) if folder.is_dir() else []


def _unsaved(window):
    window.new_table()
    assert window.autosave() and len(_recovery_files()) == 1


def test_discard_then_cancelling_the_file_chooser_keeps_the_recovery_copy(window, monkeypatch):
    from cellquant.gui.quantanalysis import window as window_module
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    _unsaved(window)
    asked = []
    monkeypatch.setattr(QuantAnalysisWindow, "ask_save", lambda self, action: asked.append(action) or "discard")
    monkeypatch.setattr(window_module.QFileDialog, "getOpenFileName", lambda *args, **kwargs: ("", ""))
    window.open_project_dialog()
    assert asked == [] and window.project.data and window.has_unsaved_changes() and len(_recovery_files()) == 1


def test_discard_then_opening_a_file_replaces_the_project_and_removes_the_copy(window, monkeypatch, tmp_path):
    from cellquant.gui.quantanalysis import window as window_module
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    other = window_module.QuantAnalysisWindow()
    other.new_table()
    saved = tmp_path / "saved.cqstats"
    assert other._write(saved)
    other.close()
    _unsaved(window)
    monkeypatch.setattr(QuantAnalysisWindow, "ask_save", lambda self, action: "discard")
    monkeypatch.setattr(window_module.QFileDialog, "getOpenFileName", lambda *args, **kwargs: (str(saved), ""))
    window.open_project_dialog()
    assert window.path == saved and _recovery_files() == []


def test_discard_then_a_file_that_cannot_be_read_keeps_the_copy(window, monkeypatch, tmp_path):
    from cellquant.gui.quantanalysis import window as window_module
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    bad = tmp_path / "bad.cqstats"
    bad.write_text("not a project")
    _unsaved(window)
    monkeypatch.setattr(QuantAnalysisWindow, "ask_save", lambda self, action: "discard")
    monkeypatch.setattr(window_module.QMessageBox, "warning", staticmethod(lambda *args, **kwargs: None))
    assert not window.open_project(bad)  # also what opening a recent project does
    assert window.project.data and len(_recovery_files()) == 1


def test_cancel_in_the_question_keeps_the_project_and_the_copy(window, monkeypatch, tmp_path):
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    _unsaved(window)
    monkeypatch.setattr(QuantAnalysisWindow, "ask_save", lambda self, action: "cancel")
    assert not window.new_project() and not window.open_project(tmp_path / "x.cqstats")
    assert window.project.data and len(_recovery_files()) == 1


def test_discard_in_new_project_replaces_the_project_and_removes_the_copy(window, monkeypatch):
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    _unsaved(window)
    monkeypatch.setattr(QuantAnalysisWindow, "ask_save", lambda self, action: "discard")
    assert window.new_project() and not window.project.data and _recovery_files() == []


def test_discard_when_the_window_closes_removes_the_copy_but_cancel_keeps_it(window, monkeypatch):
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    _unsaved(window)
    monkeypatch.setattr(QuantAnalysisWindow, "ask_save", lambda self, action: "cancel")
    assert not window.close() and len(_recovery_files()) == 1
    monkeypatch.setattr(QuantAnalysisWindow, "ask_save", lambda self, action: "discard")
    assert window.close() and _recovery_files() == []


def test_cellquant_closing_keeps_the_copy_until_the_window_closes(window, monkeypatch):
    from qtpy.QtCore import QEvent
    from qtpy.QtGui import QCloseEvent

    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    _unsaved(window)
    monkeypatch.setattr(QuantAnalysisWindow, "ask_save", lambda self, action: "discard")
    monkeypatch.setattr(window, "parent", lambda: window)  # the filter only reacts to its parent's close
    event = QCloseEvent()
    assert not window.eventFilter(window, event) and event.type() == QEvent.Close
    assert len(_recovery_files()) == 1  # a later veto by CellQuant would leave the project open
    window.close()
    assert _recovery_files() == []
