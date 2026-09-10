"""npe2/magicgui widget factory for the CellQuant plugin."""

from pathlib import Path

from cellquant.config import load_config

from .controller import PluginController


SEGMENTATION_CHANNEL_WIDGET_OPTIONS = {
    "widget_type": "ComboBox",
    "choices": [],
    "label": "Segmentation channel",
}


def segmentation_channel_choices(image_layer):
    """Return friendly 1-based labels mapped to zero-based channel indices."""
    if image_layer is None:
        return []
    data = getattr(image_layer, "data", None)
    shape = tuple(getattr(data, "shape", ()))
    if len(shape) != 4:
        return []
    metadata = dict(getattr(image_layer, "metadata", {}) or {})
    names = tuple(metadata.get("channel_names", ()))
    if len(names) != shape[-1]:
        names = tuple(f"C{index + 1}" for index in range(shape[-1]))
    return [(f"{index + 1} — {name}", index) for index, name in enumerate(names)]


def bind_segmentation_channel_choices(function_widget):
    """Refresh a segmentation form's channel ComboBox when its Image changes."""

    def refresh(*_args):
        function_widget.channel_index.choices = segmentation_channel_choices(
            function_widget.image.value
        )

    function_widget.image.changed.connect(refresh)
    refresh()
    return refresh


def make_cellquant_widget(viewer=None):
    """Create the dock widget, importing GUI libraries only on demand."""
    import napari
    from magicgui import magicgui
    from napari.layers import Image, Labels
    from qtpy.QtCore import QTimer
    from qtpy.QtWidgets import QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

    viewer = viewer or napari.current_viewer()
    if viewer is None:
        raise RuntimeError("CellQuant requires an active napari viewer")
    controller = PluginController(viewer)
    root = QWidget()
    layout = QVBoxLayout(root)
    status = QLabel("Ready")
    progress = QProgressBar()
    progress.setRange(0, 0)
    progress.hide()

    @magicgui(call_button="Open lazily", path={"mode": "r"})
    def open_image(path: Path, series: int = 0, position: int = 0):
        controller.open_path(path, series=series, position=position)

    @magicgui(
        call_button="Run Cellpose",
        config_path={"mode": "r"},
        channel_index=SEGMENTATION_CHANNEL_WIDGET_OPTIONS,
    )
    def run_segmentation(image: Image, config_path: Path, channel_index: int):
        controller.set_config(load_config(config_path))
        controller.segment(image, channel_index=channel_index)

    bind_segmentation_channel_choices(run_segmentation)

    @magicgui(call_button="Measure edited labels + save", output_dir={"mode": "d"})
    def save_edited(labels: Labels, output_dir: Path):
        controller.measure_and_save(output_dir, labels)

    cancel = QPushButton("Cancel")
    cancel.clicked.connect(controller.cancel)

    def display_event(event):
        if event.current is not None:
            progress.show()
            progress.setRange(0, event.total)
            progress.setValue(event.current)
        if event.kind in {"stage_finished", "cancelled", "failed"}:
            progress.hide()

    def poll_events():
        controller.drain_events(display_event)
        status.setText(controller.status_text)

    timer = QTimer(root)
    timer.timeout.connect(poll_events)
    timer.start(50)
    for native in (open_image.native, run_segmentation.native, save_edited.native):
        layout.addWidget(native)
    layout.addWidget(cancel)
    layout.addWidget(progress)
    layout.addWidget(status)
    # Retain explicit references and expose the controller for scripted QA.
    root.cellquant_controller = controller
    root.cellquant_timer = timer
    return root


def cellquant_widget():
    """Zero-argument npe2 widget entry point."""
    return make_cellquant_widget()


__all__ = [
    "SEGMENTATION_CHANNEL_WIDGET_OPTIONS",
    "bind_segmentation_channel_choices",
    "cellquant_widget",
    "make_cellquant_widget",
    "segmentation_channel_choices",
]
