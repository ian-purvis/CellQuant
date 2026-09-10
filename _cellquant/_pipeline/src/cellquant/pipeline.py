from __future__ import annotations

from dataclasses import asdict
import numpy as np
from .cellpose_adapter import CellposeAdapter, CellposeSettings
from .quantify import quantify, overlap_metrics, object_table, coexpression_summary
from .zmode import parse_z_selection, prepare_z


def calibrated_diameter_px(diameter_um, pixel_size_um_yx):
    y, x = pixel_size_um_yx
    if not y or not x:
        raise ValueError("A physical diameter requires calibrated X/Y pixel sizes")
    return float(diameter_um) / float(np.sqrt(y * x))


def analysis_images(image_czyx, selection):
    if selection.mode in {"single", "max"}:
        return np.stack([prepare_z(channel, selection) for channel in image_czyx], axis=0)[:, None]
    return image_czyx[:, selection.slice]


def segment(image_czyx, seg_channel_zero_based, z_spec, settings, adapter=None):
    if not 0 <= seg_channel_zero_based < image_czyx.shape[0]:
        raise ValueError("Segmentation channel is 0-based and outside the image")
    selection = parse_z_selection(z_spec, image_czyx.shape[1])
    substrate = prepare_z(image_czyx[seg_channel_zero_based], selection)
    return (adapter or CellposeAdapter(settings)).segment(substrate, selection.mode), selection


def measure(image_czyx, labels, channel_names, thresholds, selection):
    substrate = analysis_images(image_czyx, selection)
    measured = quantify(substrate, labels, channel_names, thresholds)
    overlaps = overlap_metrics(substrate, labels, channel_names, thresholds)
    objects = object_table(measured, overlaps)
    return {"objects": objects, "measurements_long": measured, "overlaps": overlaps,
            "coexpression_summary": coexpression_summary(objects)}

