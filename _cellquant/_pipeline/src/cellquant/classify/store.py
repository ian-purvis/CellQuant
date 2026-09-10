"""Immutable, checksummed classification evidence; never reload summary CSVs as pixels.

Checksums detect accidental modification, not an attacker able to replace the
entire run. A completed directory is published only after every artifact exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import uuid

import numpy as np
import pandas as pd

from cellquant import __version__
from cellquant.classify import ClassificationRecipe, classify_labels
from cellquant.contracts import ImageVolume, LabelVolume


_BASE_FILES = frozenset({
    "image.npy", "labels.npy", "inputs.json", "recipe.json", "context.json",
    "metadata.json", "calls.csv", "queries.csv", "patterns.csv", "exclusions.csv",
})
_MAX_PATH = 240


@dataclass(frozen=True)
class ReopenedClassification:
    """Verified saved inputs, ready for independent rescoring without Cellpose.

    ``metadata`` is the original result metadata. Image metadata retains the
    analysis-grid transformation and optional run configuration. Arrays are new
    writable copies: edits only affect a future save, never the original run.
    """

    image: ImageVolume
    labels: LabelVolume
    recipe: ClassificationRecipe
    region: np.ndarray | None
    context: dict
    metadata: dict


def _json_bytes(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def _json_copy(value):
    return json.loads(_json_bytes(value))


def _read_json(data: bytes):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON field: {key}")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError(f"nonfinite JSON number: {value}")

    return json.loads(data, object_pairs_hook=unique, parse_constant=invalid)


def _checkpoint(cancel):
    if cancel is not None:
        cancel.raise_if_cancelled()


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write(path: Path, data: bytes):
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _array_spec(array):
    return {"shape": list(array.shape), "dtype": array.dtype.str}


def save_classification(output_root, image, labels, recipe, *, region=None,
                        context=None, cancel=None):
    """Snapshot, classify and publish a unique run; return ``(path, result)``.

    Failed/cancelled writes remain visibly incomplete without ``complete.json``.
    No existing run is overwritten, and all filenames are fixed by this module.
    """
    _checkpoint(cancel)
    recipe = ClassificationRecipe(recipe.raw if isinstance(recipe, ClassificationRecipe) else recipe)
    context = _json_copy({} if context is None else context)
    if not isinstance(context, dict):
        raise ValueError("classification context must be a JSON object")
    image = ImageVolume(np.array(image.data, copy=True), tuple(image.spacing_um),
                        tuple(image.channel_names), Path(image.source),
                        _json_copy(dict(image.metadata)))
    labels = LabelVolume(np.array(labels.data, copy=True), tuple(labels.spacing_um),
                         _json_copy(dict(labels.provenance)))
    region = None if region is None else np.array(region, copy=True)
    result = classify_labels(image, labels, recipe, region=region, context=context, cancel=cancel)
    _checkpoint(cancel)
    inputs = {
        "schema_version": 1,
        "image": {**_array_spec(image.data), "spacing_um": list(image.spacing_um),
                  "channel_names": list(image.channel_names), "source": str(image.source),
                  "metadata": dict(image.metadata)},
        "labels": {**_array_spec(labels.data), "spacing_um": list(labels.spacing_um),
                   "provenance": dict(labels.provenance)},
        "region": None if region is None else _array_spec(region),
    }
    # Validate serialization before creating a partial run.
    json_artifacts = {"inputs.json": inputs, "recipe.json": recipe.raw,
                      "context.json": context, "metadata.json": result.metadata}
    payloads = {name: _json_bytes(value) for name, value in json_artifacts.items()}
    root = Path(output_root).expanduser().resolve()
    run = root / ("classify_" + uuid.uuid4().hex[:12])
    if len(str(run / "complete.pending")) > _MAX_PATH:
        raise ValueError("classification output path is too long; choose a shorter output folder")
    root.mkdir(parents=True, exist_ok=True)
    run.mkdir(exist_ok=False)
    for name, value in (("image.npy", image.data), ("labels.npy", labels.data),
                        ("region.npy", region)):
        if value is None:
            continue
        _checkpoint(cancel)
        with (run / name).open("xb") as stream:
            np.save(stream, value, allow_pickle=False)
            stream.flush()
            os.fsync(stream.fileno())
    for name, value in payloads.items():
        _checkpoint(cancel)
        _write(run / name, value)
    for name in ("calls", "queries", "patterns", "exclusions"):
        _checkpoint(cancel)
        _write(run / (name + ".csv"), getattr(result, name).to_csv(index=False, na_rep="NA").encode("utf-8"))
    names = _BASE_FILES | ({"region.npy"} if region is not None else set())
    files = {}
    for name in sorted(names):
        _checkpoint(cancel)
        files[name] = {"sha256": _hash_file(run / name), "size": (run / name).stat().st_size}
    manifest = {
        "schema_version": 1, "kind": "cellquant_classification",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "software": {"cellquant": __version__, "numpy": np.__version__, "pandas": pd.__version__,
                     "scoring": "nuclear_pixel_fraction_v1"},
        "recipe_fingerprint": recipe.fingerprint, "files": files,
    }
    manifest_data = _json_bytes(manifest)
    _write(run / "manifest.json", manifest_data)
    _checkpoint(cancel)
    _write(run / "complete.pending", _json_bytes({"schema_version": 1,
           "manifest_sha256": _digest(manifest_data)}))
    _checkpoint(cancel)
    # Destination cannot exist in this uniquely created directory. os.rename is
    # atomic on the same filesystem; readers never see half a completion marker.
    os.rename(run / "complete.pending", run / "complete.json")
    return run, result


def _regular_file(root: Path, name: str) -> Path:
    path = root / name
    if path.is_symlink() or path.resolve().parent != root or not path.is_file():
        raise ValueError(f"classification artifact is missing or is not a regular local file: {name}")
    return path


def _validate_array(array, spec, name):
    if not isinstance(spec, dict) or spec.get("shape") != list(array.shape) or spec.get("dtype") != array.dtype.str:
        raise ValueError(f"classification {name} array does not match its recorded shape/dtype")


def reopen_classification(path) -> ReopenedClassification:
    """Verify completion, required artifacts, checksums and grid metadata.

    No filenames supplied by a manifest are ever followed until their exact set
    matches this schema. NPY data are parsed from the same bytes that were hashed.
    """
    root = Path(path).expanduser().resolve()
    try:
        completion = _read_json(_regular_file(root, "complete.json").read_bytes())
        if not isinstance(completion, dict) or set(completion) != {"schema_version", "manifest_sha256"} or completion["schema_version"] != 1:
            raise ValueError("invalid classification completion marker")
        manifest_bytes = _regular_file(root, "manifest.json").read_bytes()
        if completion["manifest_sha256"] != _digest(manifest_bytes):
            raise ValueError("classification manifest checksum mismatch")
        manifest = _read_json(manifest_bytes)
        if not isinstance(manifest, dict) or set(manifest) != {"schema_version", "kind", "created_utc", "software", "recipe_fingerprint", "files"}:
            raise ValueError("invalid classification manifest schema")
        if manifest["schema_version"] != 1 or manifest["kind"] != "cellquant_classification":
            raise ValueError("unsupported classification manifest")
        software = manifest["software"]
        if (not isinstance(software, dict) or set(software) != {"cellquant", "numpy", "pandas", "scoring"}
                or not all(isinstance(v, str) and v for v in software.values())
                or software["scoring"] != "nuclear_pixel_fraction_v1"):
            raise ValueError("invalid or unsupported classification software provenance")
        files = manifest["files"]
        if not isinstance(files, dict) or set(files) not in (_BASE_FILES, _BASE_FILES | {"region.npy"}):
            raise ValueError("classification manifest has missing or unexpected artifact names")
        payloads = {}
        for name, spec in files.items():
            if (not isinstance(spec, dict) or set(spec) != {"sha256", "size"}
                    or type(spec["size"]) is not int or spec["size"] < 0
                    or not isinstance(spec["sha256"], str) or len(spec["sha256"]) != 64):
                raise ValueError(f"invalid artifact checksum record: {name}")
            payload = _regular_file(root, name).read_bytes()
            if len(payload) != spec["size"] or _digest(payload) != spec["sha256"]:
                raise ValueError(f"classification artifact checksum mismatch: {name}")
            # Summary CSVs are verified but are never used to classify cells.
            if not name.endswith(".csv"):
                payloads[name] = payload
        inputs = _read_json(payloads["inputs.json"])
        if not isinstance(inputs, dict) or set(inputs) != {"schema_version", "image", "labels", "region"} or inputs["schema_version"] != 1:
            raise ValueError("invalid classification input schema")
        im, lab = inputs["image"], inputs["labels"]
        if set(im) != {"shape", "dtype", "spacing_um", "channel_names", "source", "metadata"} or set(lab) != {"shape", "dtype", "spacing_um", "provenance"}:
            raise ValueError("invalid classification image/label metadata schema")
        if not isinstance(im["metadata"], dict) or not isinstance(lab["provenance"], dict) or not isinstance(im["source"], str):
            raise ValueError("invalid classification image/label metadata")
        if not isinstance(im["channel_names"], list) or not all(isinstance(n, str) for n in im["channel_names"]):
            raise ValueError("invalid classification channel names")
        image_data = np.load(io.BytesIO(payloads["image.npy"]), allow_pickle=False)
        labels_data = np.load(io.BytesIO(payloads["labels.npy"]), allow_pickle=False)
        _validate_array(image_data, im, "image")
        _validate_array(labels_data, lab, "labels")
        image = ImageVolume(image_data, tuple(im["spacing_um"]), tuple(im["channel_names"]), Path(im["source"]), im["metadata"])
        labels = LabelVolume(labels_data, tuple(lab["spacing_um"]), lab["provenance"])
        if image.data.shape[:3] != labels.data.shape or image.spacing_um != labels.spacing_um:
            raise ValueError("classification image and labels use different grids")
        region = None
        if inputs["region"] is not None:
            if "region.npy" not in payloads or set(inputs["region"]) != {"shape", "dtype"}:
                raise ValueError("classification region is missing or invalid")
            region = np.load(io.BytesIO(payloads["region.npy"]), allow_pickle=False)
            _validate_array(region, inputs["region"], "region")
            if region.dtype != np.bool_ or region.shape != labels.data.shape:
                raise ValueError("classification region must be boolean on the label grid")
        elif "region.npy" in payloads:
            raise ValueError("unrecorded classification region")
        recipe = ClassificationRecipe(_read_json(payloads["recipe.json"]))
        if recipe.fingerprint != manifest["recipe_fingerprint"]:
            raise ValueError("classification recipe fingerprint mismatch")
        context = _read_json(payloads["context.json"])
        metadata = _read_json(payloads["metadata.json"])
        if not isinstance(context, dict) or not isinstance(metadata, dict):
            raise ValueError("classification context/metadata must be objects")
        return ReopenedClassification(image, labels, recipe, region, context, metadata)
    except (OSError, KeyError, TypeError, AttributeError, EOFError) as exc:
        raise ValueError(f"cannot reopen classification: {exc}") from exc


def reclassify_run(path, output_root, recipe=None, *, cancel=None):
    """Verify saved evidence and create a new independent classification run."""
    _checkpoint(cancel)
    saved = reopen_classification(path)
    _checkpoint(cancel)
    return save_classification(output_root, saved.image, saved.labels,
                               saved.recipe if recipe is None else recipe,
                               region=saved.region, context=saved.context, cancel=cancel)
