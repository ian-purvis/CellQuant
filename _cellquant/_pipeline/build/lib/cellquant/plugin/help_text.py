"""Hover help copy for novice-facing CellQuant napari controls."""

from __future__ import annotations

TOOLTIPS = {
    "workflow_mode": (
        "Single image: open one file in napari, segment, edit labels, and save.\n"
        "Batch folder: survey many files, pick a channel per layout, then run all.\n"
        "Coexpression: classify nuclear marker positivity and calibrate thresholds."
    ),
    "open_path": "Choose a TIFF or ND2 file. CellQuant opens it lazily so large volumes stay responsive.",
    "series": "Which series inside a multi-series file (usually 0).",
    "position": "Which stage position / well inside a multi-position acquisition (usually 0).",
    "image_layer": "The napari Image layer to segment. Usually 'CellQuant image' after Open lazily.",
    "segmentation_channel": (
        "Which fluorescence channel Cellpose should segment.\n"
        "Typical choice: nuclear DNA (DAPI/Hoechst). You may also choose other nuclear or "
        "cytoplasmic markers — one channel per run."
    ),
    "output_dir": (
        "Folder where labels, measurements, events.jsonl, and status.json are written. "
        "OneDrive/Dropbox/Google Drive folders are OK: CellQuant stages each run locally, "
        "then publishes into this folder when the file finishes. "
        "Mark cloud input files as Always keep on this device so they are fully downloaded."
    ),
    "input_folder": "Root folder of acquisitions to survey and batch-process.",
    "batch_output_folder": (
        "Root output folder. Survey writes under <output>/survey/; each image gets a "
        "*.cellquant folder that mirrors the input tree."
    ),
    "recursive": "If checked, also include images in subfolders of the input folder.",
    "file_type": "Limit discovery to TIFF, ND2, or both. Matching files only are surveyed and batched.",
    "segment_mode": (
        "How Cellpose treats Z:\n"
        "• 3D volume — true 3D segmentation (best for thick stacks when GPU is available).\n"
        "• 2D + Z stitch — segment each plane, then link objects across Z (faster alternative).\n"
        "• Single Z plane — segment only one plane (quick check).\n"
        "• Max Z projection — collapse Z, then 2D segment (good overview, not true 3D volumes)."
    ),
    "cellpose_engine": (
        "Cellpose package / engine that CellQuant can drive in this Napari session.\n"
        "Options come from the Cellpose major version installed in the active env. "
        "Install CellQuant installs both v3 and v4 envs — use Open CellQuant.bat and "
        "pick the matching engine. Classic v3 is lighter if SAM v4 is too heavy."
    ),
    "device": (
        "Where to run Cellpose.\n"
        "Only devices that work in this environment are listed "
        "(GPU appears only when CUDA PyTorch is available).\n"
        "• Auto — prefer GPU when present, otherwise CPU.\n"
        "• GPU (CUDA) — require a working NVIDIA GPU setup.\n"
        "• CPU — slower, but works without a GPU."
    ),
    "allow_cpu_fallback": (
        "If GPU was requested but is unavailable or fails to initialize, continue on CPU "
        "instead of failing the whole batch."
    ),
    "diameter_mode": (
        "Cellpose-SAM (v4) is size-tolerant; classic Cellpose (v3) benefits more from "
        "a measured nuclear diameter.\n"
        "• Native (recommended) — no rescale; usually best starting point.\n"
        "• Manual — set typical object width in pixels (or measure a line in napari).\n"
        "A larger manual diameter downsamples the image (faster; can merge small objects)."
    ),
    "diameter_px": (
        "Typical width of one object in pixels in the XY plane.\n"
        "Only used in Manual mode. Tip: draw a line across a typical nucleus and click "
        "'Use drawn line'."
    ),
    "measure_diameter": (
        "Creates or uses a Shapes layer named 'CellQuant diameter'. Draw one line across a "
        "typical nucleus/cell, then click Use drawn line to fill Diameter (px)."
    ),
    "open_measure_image": (
        "Opens a file browser starting in your batch input folder (or home). Pick one "
        "representative TIFF/ND2, then measure diameter on that image before running the batch."
    ),
    "stitch_threshold": (
        "Only for 2D + Z stitch. Higher values require more overlap between planes to merge "
        "into one 3D object (0–1). Start near 0.25 and adjust if objects split or merge wrongly."
    ),
    "z_index": (
        "Zero-based plane index for Single Z plane mode (0 = first plane). "
        "Use a mid-stack plane for a representative check."
    ),
    "survey_layouts": (
        "Each row is a channel layout (same channel count/names). Include the layouts you want, "
        "pick the channel to segment, then Run batch."
    ),
    "scrollability": (
        "Controls this dropdown’s popup list.\n"
        "Checked: scroll through options when the list is long.\n"
        "Unchecked: show every option at once (no scrollbar)."
    ),
    "run_batch": "Process all included survey layouts with the current segmentation settings.",
    "cancel": (
        "Soft stop: finish the current Z plane / Cellpose checkpoint, then halt. "
        "Prefer this for smaller jobs. Already-finished batch files are kept. "
        "If one plane/image is still running after ~5 minutes, CellQuant asks "
        "Continue / Cancel / Kill (ETA bar stays visible)."
    ),
    "kill": (
        "Hard stop: terminate the Cellpose worker process immediately, even mid-plane. "
        "Use this for large/stuck jobs when Cancel is not enough and Napari feels frozen. "
        "You should not need Task Manager."
    ),
    "open_output": "Open the latest CellQuant output folder in your file browser.",
    "open_failures": "Open failures.csv listing which files failed and why.",
}


def tip(key: str) -> str:
    """Return tooltip text for a named control."""

    try:
        return TOOLTIPS[key]
    except KeyError as exc:
        raise KeyError(f"unknown CellQuant tooltip key {key!r}") from exc
