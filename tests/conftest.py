import pytest


@pytest.fixture(autouse=True)
def _no_save_prompts(monkeypatch):
    """Closing CellQuant with an unsaved QuantAnalysis project asks to save it; in tests the answer is "don't save"."""

    try:
        from cellquant.gui.quantanalysis.window import QuantAnalysisWindow
    except Exception:  # noqa: BLE001 - no Qt or no stats libraries here
        return
    monkeypatch.setattr(QuantAnalysisWindow, "ask_save", lambda self, action: "discard")
