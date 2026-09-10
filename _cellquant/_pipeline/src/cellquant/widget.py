from pathlib import Path
import numpy as np
import napari

from .cellpose_adapter import CellposeSettings
from .pipeline import calibrated_diameter_px, measure, segment
from .results import save_result


def make_widget():
    from magicgui import magicgui
    from napari.layers import Image, Labels
    from qtpy.QtWidgets import QWidget, QVBoxLayout, QLabel

    class CellQuantWidget(QWidget):
        def __init__(self, napari_viewer):
            super().__init__()
            self.viewer = napari_viewer
            self.selection = None
            self.source_image = None
            layout = QVBoxLayout(); self.setLayout(layout)
            layout.addWidget(QLabel("Channel numbers are 1-based here; arrays/API use 0-based channels."))

            @magicgui(call_button="Run Cellpose", image={"label": "CZYX image layer"},
                      seg_channel={"min": 1}, diameter_px={"min": 0.1},
                      diameter_um={"min": 0.0}, voxel_xy_um={"min": 0.0},
                      anisotropy={"min": 0.0},
                      gpu_mode={"choices": ["auto", "on", "off"]},
                      stitch_threshold={"min": 0.0}, min_size={"min": 0})
            def run(image: Image, seg_channel: int = 1, z_selection: str = "stitch:1-1",
                    engine: str = "auto", model: str = "nuclei", diameter_px: float = 30.0,
                    diameter_um: float = 0.0, voxel_xy_um: float = 0.0,
                    anisotropy: float = 0.0, gpu_mode: str = "auto",
                    cellprob_threshold: float = 0.0, flow_threshold: float = 0.4,
                    stitch_threshold: float = 0.25, min_size: int = 200):
                if image is None:
                    raise ValueError("Choose an Image layer")
                data = np.asarray(image.data)
                if data.ndim != 4:
                    raise ValueError("Selected image layer must use CZYX axes")
                effective_diameter = diameter_px
                if diameter_um > 0:
                    if voxel_xy_um <= 0:
                        raise ValueError("voxel_xy_um must be positive when diameter_um is used")
                    effective_diameter = calibrated_diameter_px(diameter_um, (voxel_xy_um, voxel_xy_um))
                settings = CellposeSettings(
                    engine=engine, model=model, diameter_px=effective_diameter,
                    cellprob_threshold=cellprob_threshold, flow_threshold=flow_threshold,
                    stitch_threshold=stitch_threshold, min_size=min_size,
                    anisotropy=anisotropy or None, gpu=gpu_mode)
                labels, self.selection = segment(data, seg_channel - 1, z_selection, settings)
                self.source_image = image
                old = next((x for x in self.viewer.layers if x.name == "CellQuant labels"), None)
                if old is not None:
                    self.viewer.layers.remove(old)
                self.viewer.add_labels(labels, name="CellQuant labels")

            @magicgui(call_button="Recalculate + Save", labels={"label": "Edited labels"},
                      output_dir={"mode": "d"})
            def save(labels: Labels, output_dir: Path, thresholds_json: str = "{}"):
                if labels is None or self.source_image is None or self.selection is None:
                    raise ValueError("Run segmentation first and choose the edited Labels layer")
                import json
                thresholds = json.loads(thresholds_json)
                names = list(self.source_image.metadata.get("channel_names", [])) or [f"C{i+1}" for i in range(self.source_image.data.shape[0])]
                tables = measure(np.asarray(self.source_image.data), np.asarray(labels.data), names,
                                 thresholds, self.selection)
                scale = tuple(self.source_image.scale[-3:])
                save_result(output_dir, np.asarray(labels.data), tables,
                            {"fingerprint": None, "source": self.source_image.name,
                             "edited_in_napari": True}, scale)

            layout.addWidget(run.native); layout.addWidget(save.native)

    return CellQuantWidget


def cellquant_widget():
    """npe2 entry point that binds the widget to the active viewer."""
    napari_viewer = napari.current_viewer()
    if napari_viewer is None:
        raise RuntimeError("CellQuant requires an active napari viewer")
    return make_widget()(napari_viewer)
