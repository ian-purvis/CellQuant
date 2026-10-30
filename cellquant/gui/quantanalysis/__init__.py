"""QuantAnalysis: Column-table statistics and graphs of CellQuant results.

Its own top-level window (``QuantAnalysisWindow``), opened from step 6 (**Analyze…**, the open
experiment) or from the Start page and step 1 (**Analyze a results folder…**). One window is made
on first use and reused. ``cellquant analyze`` (the launcher's "QuantAnalysis only" choice) opens it on
its own, without napari's viewer or Cellpose. The statistics are ``cellquant.stats``; graphs need matplotlib
(``pip install "cellquant[stats]"``), imported only when a graph is drawn.
"""

from __future__ import annotations


def open_window(parent=None, experiment=lambda: None, busy=lambda: False, existing=None):
    """Show ``existing`` (a QuantAnalysisWindow) or a new one, raised and active. A window whose C++ object is gone
    (its parent was deleted) is replaced."""

    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    window = existing
    try:
        if window is not None:
            window.isVisible()
    except RuntimeError:
        window = None
    if window is None:
        window = QuantAnalysisWindow(parent, experiment, busy)
    window.show()
    window.raise_()
    window.activateWindow()
    return window


def launch_alone(folder: str | None = None) -> None:
    """QuantAnalysis as the only window: ``cellquant analyze``. Closing it ends the program. With ``folder``, that
    results or export folder is offered for import (the user asked for it on the command line)."""

    import sys

    from cellquant import errorlog

    errorlog.setup_logging(detect_gpu=False)  # no Cellpose here
    from qtpy.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("CellQuant")
    window = open_window()
    errorlog.install_hooks()
    if folder:
        window.open_folder(folder)
    app.exec()


__all__ = ["launch_alone", "open_window"]
