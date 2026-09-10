"""Deterministic, headless quality-control figures.

All display scaling in this module is visualization-only.  Input image and
label arrays are never modified or rescaled in place.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import matplotlib

matplotlib.use("Agg", force=True)

from matplotlib import pyplot as plt
from matplotlib.colors import ListedColormap
from matplotlib.figure import Figure
import numpy as np

from cellquant.config import RunConfig
from cellquant.contracts import ImageVolume, LabelVolume
from cellquant.preprocess import prepare_analysis_volume


QC_FILENAMES = {
    "outline_slice": "qc_mid_stack_outlines.png",
    "orthogonal_view": "qc_orthogonal_view.png",
    "label_projection": "qc_label_projection.png",
}


def _validate_volumes(
    image: ImageVolume, labels: LabelVolume
) -> tuple[np.ndarray, np.ndarray]:
    if not isinstance(image, ImageVolume):
        raise TypeError("image must be an ImageVolume with ZYXC axes")
    if not isinstance(labels, LabelVolume):
        raise TypeError("labels must be a LabelVolume with ZYX axes")
    image_data = np.asarray(image.data)
    label_data = np.asarray(labels.data)
    if image_data.ndim != 4:
        raise ValueError(f"image data must be ZYXC; received shape {image_data.shape}")
    if label_data.ndim != 3:
        raise ValueError(f"label data must be ZYX; received shape {label_data.shape}")
    if label_data.dtype != np.uint32:
        raise TypeError(f"labels must be uint32; received {label_data.dtype}")
    if image_data.shape[:3] != label_data.shape:
        raise ValueError(
            "image ZYX shape must match labels; received "
            f"{image_data.shape[:3]} and {label_data.shape}"
        )
    if image.spacing_um != labels.spacing_um:
        raise ValueError("image and label spacing_um must match")
    return image_data, label_data


def _display_limits(data: np.ndarray, low_percentile: float, high_percentile: float) -> tuple[float, float]:
    low = float(low_percentile)
    high = float(high_percentile)
    if not (0.0 <= low < high <= 100.0):
        raise ValueError("display percentiles must satisfy 0 <= low < high <= 100")
    finite = np.asarray(data)[np.isfinite(data)]
    if finite.size == 0:
        raise ValueError("image channel contains no finite intensities")
    vmin, vmax = np.percentile(finite, (low, high)).astype(float)
    if vmin == vmax:
        vmax = vmin + max(1.0, abs(vmin) * np.finfo(float).eps)
    return vmin, vmax


def _label_colors(max_label: int, seed: int) -> ListedColormap:
    if seed < 0:
        raise ValueError("label_seed must be non-negative")
    rng = np.random.default_rng(seed)
    colors = np.ones((max_label + 1, 4), dtype=float)
    colors[0] = (0.0, 0.0, 0.0, 0.0)
    if max_label:
        colors[1:, :3] = rng.uniform(0.15, 0.95, size=(max_label, 3))
    return ListedColormap(colors)


def _boundaries(labels_2d: np.ndarray) -> np.ndarray:
    boundary = np.zeros(labels_2d.shape, dtype=bool)
    vertical = labels_2d[1:, :] != labels_2d[:-1, :]
    horizontal = labels_2d[:, 1:] != labels_2d[:, :-1]
    boundary[1:, :] |= vertical
    boundary[:-1, :] |= vertical
    boundary[:, 1:] |= horizontal
    boundary[:, :-1] |= horizontal
    return boundary & (labels_2d != 0)


def _outline_rgba(labels_2d: np.ndarray, seed: int) -> np.ndarray:
    rgba = _label_colors(int(labels_2d.max(initial=0)), seed)(labels_2d)
    rgba[..., 3] = _boundaries(labels_2d).astype(float)
    return rgba


def outline_slice(
    image: ImageVolume,
    labels: LabelVolume,
    *,
    channel: int,
    low_percentile: float,
    high_percentile: float,
    label_seed: int,
    dpi: int,
) -> Figure:
    """Render the middle-Z intensity plane with deterministic label outlines."""

    image_data, label_data = _validate_volumes(image, labels)
    if not 0 <= channel < image_data.shape[3]:
        raise ValueError(f"channel {channel} is outside C axis of length {image_data.shape[3]}")
    z_index = image_data.shape[0] // 2
    intensity = image_data[z_index, :, :, channel]
    vmin, vmax = _display_limits(image_data[..., channel], low_percentile, high_percentile)
    z_um, y_um, x_um = image.spacing_um

    fig, ax = plt.subplots(figsize=(6, 6), dpi=dpi, layout="constrained")
    extent = (0.0, image_data.shape[2] * x_um, image_data.shape[1] * y_um, 0.0)
    ax.imshow(intensity, cmap="gray", vmin=vmin, vmax=vmax, extent=extent)
    ax.imshow(_outline_rgba(label_data[z_index], label_seed), extent=extent, interpolation="nearest")
    ax.set(title=f"Mid-stack outlines (z={z_index}, {z_index * z_um:.3g} um)", xlabel="X (um)", ylabel="Y (um)")
    ax.set_aspect("equal")
    return fig


def orthogonal_view(
    image: ImageVolume,
    labels: LabelVolume,
    *,
    channel: int,
    low_percentile: float,
    high_percentile: float,
    label_seed: int,
    dpi: int,
) -> Figure:
    """Render calibrated XY, XZ, and YZ central sections with outlines."""

    image_data, label_data = _validate_volumes(image, labels)
    if not 0 <= channel < image_data.shape[3]:
        raise ValueError(f"channel {channel} is outside C axis of length {image_data.shape[3]}")
    z_mid, y_mid, x_mid = (size // 2 for size in label_data.shape)
    volume = image_data[..., channel]
    vmin, vmax = _display_limits(volume, low_percentile, high_percentile)
    z_um, y_um, x_um = image.spacing_um
    views = (
        (volume[z_mid], label_data[z_mid], (0, label_data.shape[2] * x_um, label_data.shape[1] * y_um, 0), "XY", "X (um)", "Y (um)", "equal"),
        (volume[:, y_mid, :], label_data[:, y_mid, :], (0, label_data.shape[2] * x_um, label_data.shape[0] * z_um, 0), "XZ (Z display expanded)", "X (um)", "Z (um)", "auto"),
        (volume[:, :, x_mid], label_data[:, :, x_mid], (0, label_data.shape[1] * y_um, label_data.shape[0] * z_um, 0), "YZ (Z display expanded)", "Y (um)", "Z (um)", "auto"),
    )
    fig, axes = plt.subplots(1, 3, figsize=(12, 4), dpi=dpi, layout="constrained")
    for ax, (plane, mask, extent, title, xlabel, ylabel, aspect) in zip(axes, views, strict=True):
        ax.imshow(plane, cmap="gray", vmin=vmin, vmax=vmax, extent=extent)
        ax.imshow(_outline_rgba(mask, label_seed), extent=extent, interpolation="nearest")
        ax.set(title=title, xlabel=xlabel, ylabel=ylabel)
        ax.set_aspect(aspect)
    return fig


def label_projection(labels: LabelVolume, *, label_seed: int, dpi: int) -> Figure:
    """Render a deterministic categorical maximum-Z label projection."""

    if not isinstance(labels, LabelVolume):
        raise TypeError("labels must be a LabelVolume with ZYX axes")
    label_data = np.asarray(labels.data)
    if label_data.ndim != 3:
        raise ValueError(f"label data must be ZYX; received shape {label_data.shape}")
    if label_data.dtype != np.uint32:
        raise TypeError(f"labels must be uint32; received {label_data.dtype}")
    projection = np.max(label_data, axis=0)
    _, y_um, x_um = labels.spacing_um
    extent = (0.0, projection.shape[1] * x_um, projection.shape[0] * y_um, 0.0)
    fig, ax = plt.subplots(figsize=(6, 6), dpi=dpi, layout="constrained")
    ax.imshow(
        projection,
        cmap=_label_colors(int(label_data.max(initial=0)), label_seed),
        interpolation="nearest",
        vmin=0,
        vmax=max(1, int(label_data.max(initial=0))),
        extent=extent,
    )
    ax.set(title="Maximum-Z label projection", xlabel="X (um)", ylabel="Y (um)")
    ax.set_aspect("equal")
    return fig


def _raw_config(config: RunConfig | Mapping[str, Any]) -> Mapping[str, Any]:
    return config.raw if isinstance(config, RunConfig) else config


def _config_fingerprint(config: RunConfig | Mapping[str, Any]) -> str:
    if isinstance(config, RunConfig):
        return config.fingerprint
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _figure_options(config: RunConfig | Mapping[str, Any], channel_count: int) -> dict[str, int | float]:
    raw = _raw_config(config)
    try:
        viz = raw["viz"]
        channel = viz["channel"] if "channel" in viz else raw["preprocess"]["channel"]
        options = {
            "channel": int(channel),
            "low_percentile": float(viz["low_percentile"]),
            "high_percentile": float(viz["high_percentile"]),
            "label_seed": int(viz["label_seed"]),
            "dpi": int(viz["dpi"]),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("config must explicitly define viz percentiles, label_seed, dpi, and a channel") from exc
    if options["dpi"] <= 0:
        raise ValueError("viz.dpi must be positive")
    if not 0 <= options["channel"] < channel_count:
        raise ValueError(f"visualization channel {options['channel']} is outside C axis of length {channel_count}")
    _display_limits(np.array([0.0, 1.0]), options["low_percentile"], options["high_percentile"])
    if options["label_seed"] < 0:
        raise ValueError("viz.label_seed must be non-negative")
    return options


def _input_fingerprint(image: ImageVolume, labels: LabelVolume) -> str:
    value = image.metadata.get("input_fingerprint") or labels.provenance.get("input_fingerprint")
    if not isinstance(value, str) or not value:
        raise ValueError("image metadata or label provenance must contain input_fingerprint")
    return value


def make_qc_figures(
    image: ImageVolume,
    labels: LabelVolume,
    output_dir: str | Path,
    config: RunConfig | Mapping[str, Any],
) -> dict[str, Path]:
    """Write the three contract QC PNGs and return their stable paths."""

    raw = _raw_config(config)
    if "segment" in raw:
        image = prepare_analysis_volume(image, config)
    image_data, _ = _validate_volumes(image, labels)
    options = _figure_options(config, image_data.shape[3])
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "Software": "cellquant",
        "input_fingerprint": _input_fingerprint(image, labels),
        "config_fingerprint": _config_fingerprint(config),
    }
    figures = {
        "outline_slice": outline_slice(image, labels, **options),
        "orthogonal_view": orthogonal_view(image, labels, **options),
        "label_projection": label_projection(labels, label_seed=int(options["label_seed"]), dpi=int(options["dpi"])),
    }
    paths: dict[str, Path] = {}
    try:
        for name, figure in figures.items():
            path = output_dir / QC_FILENAMES[name]
            figure.savefig(path, dpi=int(options["dpi"]), metadata={**metadata, "Title": name})
            paths[name] = path
    finally:
        for figure in figures.values():
            plt.close(figure)
    return paths


def showcase_crop(
    image: ImageVolume,
    labels: LabelVolume,
    output_dir: str | Path,
    config: RunConfig | Mapping[str, Any],
) -> dict[str, Path]:
    """Stage the visualization subsystem on an already-recorded crop."""

    return make_qc_figures(image, labels, output_dir, config)


__all__ = [
    "QC_FILENAMES",
    "label_projection",
    "make_qc_figures",
    "orthogonal_view",
    "outline_slice",
    "showcase_crop",
]
