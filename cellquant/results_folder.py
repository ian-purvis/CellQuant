"""Find object masks in a results folder and match them to an experiment's images.

A results folder is any folder of label images, whatever made them: CellQuant
itself (an experiment folder, a run or a working folder), a cluster run, or
another tool such as Cellpose. Subfolders are searched too.

* A mask is a TIFF (``.tif``/``.tiff``) of whole numbers: 0 is background and
  every object has its own number. It is 2D (Y, X) for objects found on a max
  projection or one slice, or 3D (Z, Y, X) with one plane per slice for
  stitched or 3D objects.
* It is matched to an image by name: the image's file name without its
  extension, its path in the image folder without the extension (``/`` may
  be written ``__``), or its sample name; ``_p<N>`` names position N of a
  multi-position file. The name may end in ``_masks``, ``_cp_masks``,
  ``_labels``, ``_objects``, ``_final`` or ``_automated``. Matching ignores
  upper and lower case, and characters other than letters, digits, ``.``,
  ``-`` and ``_`` count as ``_``.
* An ``images.csv`` in the folder (or, for a folder named ``results``, beside
  it) with columns ``name``, ``source_file`` and ``position`` (from 1) names
  each mask's image file exactly; masks it lists are matched by it first.
* Masks CellQuant saved (``labels/<id>_final.tif`` next to
  ``provenance/<id>.json``) are matched by the image name saved in that provenance,
  so results from another CellQuant experiment load the same way. The edited
  objects (``_final``) are used over the unedited ones (``_automated``).
* Object tables beside the masks are not read: CellQuant measures every
  marker itself from the masks and the image.
* When one image has more than one mask, the newest file is used.
"""

from __future__ import annotations

import csv
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

MASK_SUFFIXES = ("_cp_masks", "_masks", "_mask", "_labels", "_label", "_objects", "_final", "_automated")
_EXTENSIONS = (".tif", ".tiff")


@dataclass
class FoundMask:
    path: Path
    name: str  # the image name it was matched by (see ``name_key``)
    edited: bool  # CellQuant's edited objects (preferred over its unedited ones)
    modified: float
    cellquant: bool = False  # saved by CellQuant: earlier runs of the same image are older copies


@dataclass
class MaskMatch:
    found: dict[str, FoundMask] = field(default_factory=dict)  # image_id -> mask
    unmatched: list[Path] = field(default_factory=list)  # masks that fit no image
    duplicates: int = 0  # images that had more than one different mask


def mask_name(path: Path) -> tuple[str, bool, bool]:
    """The image name a mask file names, whether it is CellQuant's edited version, and whether CellQuant saved it."""

    stem = path.name
    for extension in _EXTENSIONS:
        if stem.lower().endswith(extension):
            stem = stem[: -len(extension)]
            break
    edited = False
    if path.parent.name == "labels":
        provenance = path.parent.parent / "provenance"
        for suffix in ("_final", "_automated"):
            if stem.endswith(suffix):
                saved = provenance / f"{stem[: -len(suffix)]}.json"
                if saved.is_file():
                    try:
                        data = json.loads(saved.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        data = {}
                    name = str(data.get("sample_name") or Path(str(data.get("filename") or "")).stem)
                    if name:
                        return name, suffix == "_final", True
    for suffix in MASK_SUFFIXES:
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            edited = suffix == "_final"
            break
    return stem, edited, False


def name_key(name: str) -> str:
    """A name as masks are compared: "/" as "__", other characters as "_", lower case."""

    text = str(name).replace("\\", "/").replace("/", "__")
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._")
    return text[:120].lower()


def image_names(record) -> list[str]:
    """Names a mask may use for this image (see ``name_key``)."""

    names = []
    sample = str(record.sample_name or "").replace("\\", "/")
    if sample:
        names.extend([sample, sample.rsplit("/", 1)[-1]])
    stems = [Path(record.filename).stem]
    relative = str(record.relative_path or "").replace("\\", "/")
    if relative:
        stems.append(relative.rsplit(".", 1)[0] if "." in relative.rsplit("/", 1)[-1] else relative)
    if int(record.positions_in_file or 1) > 1:
        stems = [f"{stem}_p{int(record.position) + 1}" for stem in stems]
    names.extend(stems)
    return list(dict.fromkeys(name_key(name) for name in names if name))


def _manifest(root: Path) -> dict[str, tuple[str, int]]:
    """images.csv rows: mask name -> (image file, position from 0)."""

    places = [root / "images.csv"]
    if root.name.lower() == "results":
        places.append(root.parent / "images.csv")
    rows: dict[str, tuple[str, int]] = {}
    for place in places:
        if not place.is_file():
            continue
        try:
            with place.open(newline="", encoding="utf-8-sig") as handle:
                for row in csv.DictReader(handle):
                    name, source = row.get("name"), row.get("source_file")
                    if not name or not source:
                        continue
                    try:
                        position = max(int(float(row.get("position") or 1)) - 1, 0)
                    except ValueError:
                        position = 0
                    rows.setdefault(name_key(name), (source, position))
        except (OSError, csv.Error, UnicodeDecodeError):
            continue
    return rows


def _same_file(first: str, second: str) -> bool:
    def norm(path: str) -> str:
        try:
            return os.path.normcase(str(Path(path).resolve()))
        except (OSError, ValueError):
            return os.path.normcase(path)

    return norm(first) == norm(second)


def find_masks(folder: str | Path, records, skip: set[str] | None = None) -> MaskMatch:
    """Match the masks in ``folder`` (and subfolders) to ``records``. ``skip``: paths that are images, not masks."""

    root = Path(folder)
    skip = {os.path.normcase(str(Path(item).resolve())) for item in (skip or set())}
    manifest = _manifest(root)
    by_name: dict[str, list] = {}
    for record in records:
        for name in image_names(record):
            by_name.setdefault(name, []).append(record)
    candidates: dict[str, list[FoundMask]] = {}
    match = MaskMatch()
    for directory, folders, files in os.walk(root):
        folders[:] = sorted(item for item in folders if not item.startswith("."))
        for file in sorted(files):
            if not file.lower().endswith(_EXTENSIONS):
                continue
            path = Path(directory) / file
            if os.path.normcase(str(path.resolve())) in skip:
                continue
            name, edited, cellquant = mask_name(path)
            name = name_key(name)
            owners = by_name.get(name, [])
            if name in manifest:
                source, position = manifest[name]
                listed = [item for item in records if item.position == position and _same_file(item.source_path, source)]
                if not listed:
                    # The images were moved since: the same file name and position.
                    listed = [
                        item for item in records
                        if item.position == position and Path(item.source_path).name.lower() == Path(source.replace("\\", "/")).name.lower()
                    ]
                owners = listed if len(listed) == 1 else owners
            if len(owners) != 1:
                match.unmatched.append(path)
                continue
            found = FoundMask(path=path, name=name, edited=edited, modified=path.stat().st_mtime, cellquant=cellquant)
            candidates.setdefault(owners[0].image_id, []).append(found)
    for image_id, masks in candidates.items():
        # CellQuant's edited objects over its unedited ones from the same folder; then the newest.
        folders = {item.path.parent for item in masks if item.edited}
        masks = [item for item in masks if item.edited or item.path.parent not in folders]
        newest = max(masks, key=lambda item: item.modified)
        match.found[image_id] = newest
        others = [item for item in masks if not item.cellquant]
        if len(others) > 1 and len({_content(item.path) for item in others}) > 1:
            match.duplicates += 1
    return match


def _content(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_mask(path: Path) -> np.ndarray:
    """A mask as whole-number labels, (Y, X) or (Z, Y, X). Raises ValueError when it is not one."""

    import tifffile

    data = np.squeeze(np.asarray(tifffile.imread(path)))
    if data.ndim not in (2, 3):
        raise ValueError(f"it has {data.ndim} dimensions; a mask is 2D (Y, X) or 3D (Z, Y, X)")
    if data.dtype.kind == "f":
        if not np.all(np.isfinite(data)) or not np.array_equal(data, np.round(data)):
            raise ValueError("it holds fractions, not object numbers")
    elif data.dtype.kind not in "iub":
        raise ValueError("it does not hold object numbers")
    if data.size and data.min() < 0:
        raise ValueError("it holds negative numbers")
    return data.astype(np.int32)


def mask_shape(path: Path) -> tuple[int, ...]:
    """A mask's shape without reading its pixels, (Y, X) or (Z, Y, X)."""

    import tifffile

    with tifffile.TiffFile(path) as handle:
        shape = tuple(int(value) for value in handle.series[0].shape if int(value) != 1)
    if len(shape) not in (2, 3):
        raise ValueError(f"it has {len(shape)} dimensions; a mask is 2D (Y, X) or 3D (Z, Y, X)")
    return shape


# Z handling named by a cluster package (prep.json / settings.json), as CellQuant names it.
_PACKAGE_Z = {"max": "max_projection", "single": "single_plane", "stitch": "stitch_slices", "3d": "full_3d"}


def _beside(root: Path, name: str) -> list[Path]:
    places = [root / name]
    if root.name.lower() == "results":
        places.append(root.parent / name)
    return [place for place in places if place.is_file()]


def package_settings(folder: str | Path) -> dict:
    """What a cluster package says about how its masks were made: ``channel`` (a name),
    ``z_stack`` (a CellQuant Z mode) and ``image_folder``. Empty for other folders."""

    root = Path(folder)
    settings: dict = {}
    for name in ("settings.json", "prep.json"):  # prep.json wins
        for place in _beside(root, name):
            try:
                data = json.loads(place.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            mode = str(data.get("z_mode") or "").lower()
            mode = _PACKAGE_Z.get(mode, mode)
            if mode in _PACKAGE_Z.values():
                settings["z_stack"] = mode
            if data.get("channel"):
                settings["channel"] = str(data["channel"])
            if data.get("image_folder"):
                settings["image_folder"] = str(data["image_folder"])
    return settings


def listed_originals(folder: str | Path) -> list[str]:
    """Image files the folder names: images.csv's source_file, or the source of each image CellQuant saved."""

    root = Path(folder)
    listed = [source for source, _position in _manifest(root).values()]
    for directory, folders, files in os.walk(root):
        folders[:] = [item for item in folders if not item.startswith(".")]
        if Path(directory).name != "provenance" or not (Path(directory).parent / "labels").is_dir():
            continue
        for file in files:
            if not file.endswith(".json"):
                continue
            try:
                data = json.loads((Path(directory) / file).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and data.get("source_path"):
                listed.append(str(data["source_path"]))
    return list(dict.fromkeys(listed))


def mask_keys(folder: str | Path) -> set[str]:
    """The image names the masks in a folder use (see ``name_key``), with and without a ``_p<N>`` position."""

    keys = set()
    for directory, folders, files in os.walk(Path(folder)):
        folders[:] = [item for item in folders if not item.startswith(".")]
        for file in files:
            if file.lower().endswith(_EXTENSIONS):
                key = name_key(mask_name(Path(directory) / file)[0])
                keys.update({key, re.sub(r"_p\d+$", "", key)})
    return keys


def find_image_files(
    search: str | Path, *, names: set[str] = frozenset(), keys: set[str] = frozenset(), exclude: str | Path | None = None
) -> dict[str, Path]:
    """Image files (ND2, TIFF) under ``search`` whose file name is in ``names`` (lower case),
    or whose name or path in ``search`` matches a mask name in ``keys``. Returns {name or key: file}."""

    root = Path(search)
    skip = Path(exclude).resolve() if exclude is not None else None
    found: dict[str, Path] = {}
    for directory, folders, files in os.walk(root):
        folders[:] = [item for item in folders if not item.startswith(".")]
        if skip is not None and (Path(directory).resolve() == skip or skip in Path(directory).resolve().parents):
            continue  # the results folder itself: masks, not images
        for file in files:
            if not file.lower().endswith((".nd2", *_EXTENSIONS)):
                continue
            path = Path(directory) / file
            if file.lower() in names:
                found.setdefault(file.lower(), path)
                continue
            relative = path.relative_to(root).with_suffix("").as_posix()
            for key in (name_key(path.stem), name_key(relative)):
                if key in keys:
                    found.setdefault(key, path)
                    break
    return found
