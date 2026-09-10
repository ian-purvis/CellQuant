"""Deterministic label-volume postprocessing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np

from cellquant.contracts import LabelVolume


def _copy(labels: LabelVolume) -> np.ndarray:
    return np.asarray(labels.data).copy()


def relabel(labels: LabelVolume) -> LabelVolume:
    data = np.asarray(labels.data)
    ids = np.unique(data)
    ids = ids[ids != 0]
    output = np.zeros(data.shape, dtype=np.uint32)
    if ids.size:
        locations = np.searchsorted(ids, data)
        foreground = data != 0
        output[foreground] = locations[foreground].astype(np.uint32) + 1
    return LabelVolume(output, labels.spacing_um, {**labels.provenance, "relabelled": True})


def filter_size(labels: LabelVolume, spec: Mapping) -> LabelVolume:
    data = np.asarray(labels.data)
    ids, counts = np.unique(data, return_counts=True)
    voxel_um3 = float(np.prod(labels.spacing_um))
    min_um3, max_um3 = spec.get("min_volume_um3"), spec.get("max_volume_um3")
    min_voxels, max_voxels = spec.get("min_voxels"), spec.get("max_voxels")
    keep = ids != 0
    if min_um3 is not None:
        keep &= counts * voxel_um3 >= float(min_um3)
    if max_um3 is not None:
        keep &= counts * voxel_um3 <= float(max_um3)
    if min_voxels is not None:
        keep &= counts >= int(min_voxels)
    if max_voxels is not None:
        keep &= counts <= int(max_voxels)
    keep_ids = ids[keep]
    output = np.where(np.isin(data, keep_ids), data, 0).astype(np.uint32, copy=False)
    return LabelVolume(output, labels.spacing_um, {**labels.provenance, "size_filter": dict(spec)})


def remove_border_labels(labels: LabelVolume, faces: Iterable[str]) -> LabelVolume:
    faces = tuple(faces)
    allowed = {"z0", "z1", "y0", "y1", "x0", "x1"}
    unknown = set(faces) - allowed
    if unknown:
        raise ValueError(f"unknown border face(s): {sorted(unknown)}")
    data = np.asarray(labels.data)
    touched: set[int] = set()
    planes = {
        "z0": data[0], "z1": data[-1], "y0": data[:, 0],
        "y1": data[:, -1], "x0": data[:, :, 0], "x1": data[:, :, -1],
    }
    for face in faces:
        touched.update(int(v) for v in np.unique(planes[face]) if v != 0)
    output = data.copy()
    if touched:
        output[np.isin(output, tuple(touched))] = 0
    return LabelVolume(output.astype(np.uint32, copy=False), labels.spacing_um,
                       {**labels.provenance, "removed_border_faces": faces})


def merge_labels(labels: LabelVolume, pairs: Iterable[tuple[int, int]]) -> LabelVolume:
    pairs = tuple(pairs)
    output = _copy(labels)
    existing = set(int(value) for value in np.unique(output))
    for target, source in pairs:
        if target <= 0 or source <= 0 or target == source:
            raise ValueError("merge pairs require distinct positive (target, source) IDs")
        if target not in existing or source not in existing:
            raise ValueError(f"cannot merge absent labels {target}, {source}")
        output[output == source] = target
        existing.discard(source)
    return LabelVolume(output.astype(np.uint32, copy=False), labels.spacing_um,
                       {**labels.provenance, "merge_pairs": [list(pair) for pair in pairs]})


def split_label(labels: LabelVolume, request: Mapping) -> LabelVolume:
    source = int(request["label"])
    parts = np.asarray(request["parts"])
    data = np.asarray(labels.data)
    if parts.shape != data.shape or not np.issubdtype(parts.dtype, np.integer):
        raise ValueError("split parts must be an integer array matching the label volume")
    source_mask = data == source
    part_ids = np.unique(parts[source_mask])
    part_ids = part_ids[part_ids != 0]
    if part_ids.size < 2 or np.any(parts[~source_mask] != 0) or np.any(parts[source_mask] == 0):
        raise ValueError("split parts must partition the complete source label into at least two parts")
    output = data.copy()
    output[source_mask] = 0
    next_id = int(output.max(initial=0)) + 1
    for index, part in enumerate(part_ids):
        output[parts == part] = source if index == 0 else next_id
        if index:
            next_id += 1
    return LabelVolume(output.astype(np.uint32, copy=False), labels.spacing_um,
                       {**labels.provenance, "split_label": source, "part_count": int(part_ids.size)})


def run_postprocess(labels: LabelVolume, config, cancel=None, events=None) -> LabelVolume:
    raw = config.raw if hasattr(config, "raw") else config
    spec = raw["postprocess"]
    if cancel is not None:
        cancel.raise_if_cancelled()
    result = filter_size(labels, spec)
    result = remove_border_labels(result, spec.get("remove_border_faces", []))
    if spec.get("relabel", False):
        result = relabel(result)
    if cancel is not None:
        cancel.raise_if_cancelled()
    return result


def showcase_crop(value, config, output_dir: str | Path):
    labels = value[1] if isinstance(value, tuple) else value
    result = run_postprocess(labels, config)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    labels_path = output / "postprocess_labels.npy"
    np.save(labels_path, result.data)
    summary = output / "postprocess_summary.json"
    summary.write_text(json.dumps({"input_count": int(np.unique(labels.data).size - (0 in labels.data)),
                                   "output_count": int(np.unique(result.data).size - (0 in result.data))}, indent=2),
                       encoding="utf-8")
    return {"labels": labels_path, "summary": summary}


__all__ = ["filter_size", "merge_labels", "relabel", "remove_border_labels", "run_postprocess", "split_label", "showcase_crop"]
