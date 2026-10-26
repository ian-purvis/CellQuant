"""QuantAnalysis: a commercial package-style column statistics and graphs of CellQuant results.

Its own top-level window (``QuantAnalysisWindow``), opened from step 6 (**Analyze…**, the open
experiment) or from the Start page and step 1 (**Analyze a results folder…**). One window is made
on first use and reused. The statistics are ``cellquant.stats``; graphs need matplotlib
(``pip install "cellquant[stats]"``), imported only when a graph is drawn.
"""

from __future__ import annotations


def open_window(parent=None, experiment=lambda: None, busy=lambda: False, existing=None):
    """Show ``existing`` (a QuantAnalysisWindow) or a new one, raised and active."""

    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    window = existing if existing is not None else QuantAnalysisWindow(parent, experiment, busy)
    window.show()
    window.raise_()
    window.activateWindow()
    return window


__all__ = ["open_window"]
