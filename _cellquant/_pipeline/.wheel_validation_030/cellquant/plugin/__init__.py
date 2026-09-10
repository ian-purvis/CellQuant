"""Napari plugin adapters for the shared CellQuant core."""

from .controller import IMAGE_LAYER_NAME, LABEL_LAYER_NAME, PluginController
from .widget import (
    bind_segmentation_channel_choices,
    cellquant_widget,
    make_cellquant_widget,
    segmentation_channel_choices,
)

__all__ = [
    "IMAGE_LAYER_NAME",
    "LABEL_LAYER_NAME",
    "PluginController",
    "bind_segmentation_channel_choices",
    "cellquant_widget",
    "make_cellquant_widget",
    "segmentation_channel_choices",
]
