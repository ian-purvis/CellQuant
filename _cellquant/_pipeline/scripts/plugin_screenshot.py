"""Capture the plugin dock in a bounded offscreen Qt session."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("NAPARI_ASYNC", "0")


def main() -> None:
    from qtpy.QtWidgets import QApplication

    from cellquant.plugin import make_cellquant_widget

    output = Path(__file__).resolve().parents[1] / "artifacts" / "wave3_plugin"
    output.mkdir(parents=True, exist_ok=True)
    application = QApplication.instance() or QApplication([])
    # Widget construction does not require the heavyweight vispy canvas. The
    # controller interaction suite separately exercises layer publication with
    # a viewer-compatible test double.
    viewer = SimpleNamespace(layers=[])
    widget = make_cellquant_widget(viewer)
    widget.resize(560, 720)
    widget.show()
    application.processEvents()
    image = widget.grab()
    target = output / "plugin_widget.png"
    if image.isNull() or not image.save(str(target)):
        raise RuntimeError("Qt did not produce a widget screenshot")
    widget.cellquant_timer.stop()
    widget.close()
    application.processEvents()
    print(target)


if __name__ == "__main__":
    main()
