"""Single-image preflight should follow the selected image, not a stale empty spec."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from qtpy import QtCore, QtWidgets
from qtpy.QtWidgets import QApplication

from cellquant.plugin.capabilities import detect_runtime_capabilities
from cellquant.plugin.widget import make_segment_options_panel


class _Viewer:
    layers = []


class _Layer:
    def __init__(self):
        self.metadata = {"spacing_um": (1.0, 0.5, 0.5), "spacing_source": "file"}
        self.scale = (1.0, 0.5, 0.5)
        self.data = np.zeros((2, 8, 8, 1), dtype=np.float32)


def _caps():
    return detect_runtime_capabilities(
        cellpose_version_fn=lambda: "4.2.1.1",
        cuda_fn=lambda: (False, "2.14.0+cpu", "CPU"),
        torch_ready_fn=lambda: (True, "2.14.0+cpu"),
    )


def test_preflight_uses_selected_image_and_retains_it_on_option_change():
    app = QApplication.instance() or QApplication([])
    panel = make_segment_options_panel(_Viewer(), QtWidgets, QtCore, capabilities=_caps())
    assert "Not estimated" in panel.cellquant_preflight.text()
    panel.cellquant_refresh_preflight(image_layer=_Layer(), channel_label="DAPI")
    text = panel.cellquant_preflight.text()
    assert "Image shape: (2, 8, 8, 1)" in text
    assert "Channel: DAPI" in text
    assert "Calibration: file" in text
    assert "image 512 B" in text
    assert "image 0 B" not in text
    assert "Not estimated" not in text
    panel.cellquant_diameter.valueChanged.emit(panel.cellquant_diameter.value())
    again = panel.cellquant_preflight.text()
    assert "Image shape: (2, 8, 8, 1)" in again
    assert "Channel: DAPI" in again
    panel.close()
    app.processEvents()
