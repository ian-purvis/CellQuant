import pytest


@pytest.fixture(autouse=True)
def _no_save_prompts(monkeypatch):
    """Closing CellQuant with an unsaved QuantAnalysis project asks to save it; in tests the answer is "don't save"."""

    try:
        from cellquant.gui.quantanalysis.window import QuantAnalysisWindow
    except Exception:  # noqa: BLE001 - no Qt or no stats libraries here
        return
    monkeypatch.setattr(QuantAnalysisWindow, "ask_save", lambda self, action: "discard")


@pytest.fixture(autouse=True)
def _no_run_prompts(monkeypatch):
    """A window never stops a test with a question about runs: closing during a run exits, and an
    unfinished run from an earlier test is left for later."""

    try:
        from cellquant.gui.app import CellQuantWindow
    except Exception:  # noqa: BLE001 - no Qt here
        return
    monkeypatch.setattr(CellQuantWindow, "_confirm_exit_during_run", lambda self: True)
    monkeypatch.setattr(CellQuantWindow, "_ask_unfinished", lambda self, *args, **kwargs: (None, False))
