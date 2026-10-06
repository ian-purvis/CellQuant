"""Experiment manifest.

An experiment is a directory of images, channel names, and metadata. Source
image files stay where the user imported them and are never modified.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from cellquant.__version__ import __version__
from cellquant.errors import ImageLoadError
from cellquant import progress
from cellquant.image import IMAGE_SUFFIXES, ImageInfo, inspect_image

IMAGE_STATUSES = (
    "not_analyzed",
    "analyzed",
    "reviewed",
    "approved",
    "excluded",
    "needs_attention",
)


class ChannelRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel_index: int
    channel_name: str
    display_name: str
    display_settings: dict[str, Any] = Field(default_factory=dict)


class ImageRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_id: str
    source_path: str
    filename: str
    sample_name: str
    # Path inside the folder that was added, e.g. "mCherry cont/Retina 2/image.nd2".
    # Files in different subfolders can share a filename; this keeps them apart.
    relative_path: str = ""
    # Image file relative to the experiment folder (POSIX separators), for experiments that carry
    # their own copies of the images (for example results imported from a cluster). When set and
    # present, it is used instead of source_path, so the folder can be moved.
    source_path_relative: str = ""
    position: int = 0  # stage position inside a multi-position ND2 file
    positions_in_file: int = 1
    z_planes: int = 1
    channel_names: list[str] = Field(default_factory=list)
    # Display colors stored in the file, (r, g, b) in 0-1, or None per channel.
    channel_colors: list[list[float] | None] = Field(default_factory=list)
    objective: str = ""
    include: bool = True
    dimensions: list[int] = Field(default_factory=list)
    number_of_channels: int | None = None
    pixel_size_x: float | None = None
    pixel_size_y: float | None = None
    pixel_size_z: float | None = None
    image_metadata: dict[str, Any] = Field(default_factory=dict)
    user_metadata: dict[str, Any] = Field(default_factory=dict)
    processing_status: str = "not_analyzed"
    last_result: str = ""
    last_message: str = ""
    # Settings fingerprint (and edit count) at approval; a re-run with the same ones keeps the approval.
    approved_settings_sha256: str = ""
    content_hash: str = ""
    file_signature: str = ""


# Review state of one image that belongs to one analysis (see AnalysisRecord.image_states).
IMAGE_STATE_FIELDS = ("processing_status", "last_result", "last_message", "approved_settings_sha256")


class PlanEntry(BaseModel):
    """One image in one analysis's plan. Blank fields follow the defaults."""

    model_config = ConfigDict(extra="forbid")

    # None: run it when the image is included (the default); True/False: chosen for this image.
    run: bool | None = None
    # Channel to find objects in for this image only (index in this image's own channels).
    channel: int | None = None
    # True: segment the full image even when the analysis crops to the region of interest.
    full_image: bool | None = None


class AnalysisRecord(BaseModel):
    """One analysis of the experiment's images: its own settings (recipe) and its own results.

    An experiment can hold several, for example one per channel that objects are found in. The
    active analysis's per-image review state lives on the image records; the others' is kept in
    ``image_states`` until they are made active again.
    """

    model_config = ConfigDict(extra="forbid")

    recipe_id: str
    name: str
    # Where this analysis keeps its open (not yet run) results, relative to the experiment folder.
    working_folder: str = "working"
    latest_run_id: str | None = None
    image_states: dict[str, dict[str, str]] = Field(default_factory=dict)
    # The analysis that holds everything saved before analyses existed (runs without its id among the
    # others, working/, unstamped edits). Only the experiment's original analysis has this.
    owns_legacy: bool = False
    # Taken off the list. Kept so its runs and edits are never mistaken for another analysis's.
    removed: bool = False
    # Which images this analysis runs on, and per-image channel choices (image id -> entry).
    plan: dict[str, PlanEntry] = Field(default_factory=dict)


class Experiment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    experiment_name: str
    created_at: str
    modified_at: str
    recipe_id: str | None = None
    directory: str
    input_directory: str = ""
    images: list[ImageRecord] = Field(default_factory=list)
    channels: list[ChannelRecord] = Field(default_factory=list)
    metadata_columns: list[str] = Field(default_factory=list)
    # File types last chosen for folder searches ("nd2", "tiff").
    import_file_types: list[str] = Field(default_factory=lambda: ["nd2", "tiff"])
    software_version: str = __version__
    latest_run_id: str | None = None
    # Analyses of these images; empty in experiments saved before analyses existed (one analysis).
    analyses: list[AnalysisRecord] = Field(default_factory=list)

    def image(self, image_id: str) -> ImageRecord:
        for record in self.images:
            if record.image_id == image_id:
                return record
        raise KeyError(image_id)

    def touch(self) -> None:
        self.modified_at = _now()
        self.software_version = __version__


def create_experiment(
    directory: str | Path,
    name: str,
    input_directory: str | Path | None = None,
) -> Experiment:
    """Create the results folder. Image files are not copied or modified.

    ``directory`` is where runs, recipes, and exports are written.
    ``input_directory`` is the folder of source images, when there is one.
    """

    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    for relative in ("recipes", "runs", "exports", "edits", ".cache"):
        (root / relative).mkdir(exist_ok=True)
    now = _now()
    images = ""
    if input_directory:
        images = str(Path(input_directory).resolve())
    experiment = Experiment(
        experiment_id=f"exp_{uuid.uuid4().hex[:8]}",
        experiment_name=name or "Experiment",
        created_at=now,
        modified_at=now,
        directory=str(root.resolve()),
        input_directory=images,
    )
    save_experiment(experiment)
    return experiment


def load_experiment(directory: str | Path) -> Experiment:
    root = Path(directory)
    path = root / "experiment.json" if root.is_dir() else root
    if not path.is_file():
        raise FileNotFoundError(f"Experiment file could not be opened: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    # The folder the experiment is opened from, not the one it was saved in: a moved or
    # copied experiment then writes into its own folder.
    data["directory"] = str(path.parent.resolve())
    return Experiment.model_validate(data)


def save_experiment(experiment: Experiment) -> Path:
    root = Path(experiment.directory)
    root.mkdir(parents=True, exist_ok=True)
    experiment.touch()
    path = root / "experiment.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(experiment.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(path)
    return path


# CellQuant's own folders inside an experiment. They are never imported as images.
_EXPERIMENT_FOLDERS = {"runs", "working", ".cache", "exports", "edits", "recipes", "analyses"}


@dataclass
class ImportReport:
    """What a folder import found, for the user to check before analysing."""

    added: int = 0
    already_listed: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    folders_with_images: list[str] = field(default_factory=list)
    folders_without_images: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    skipped_experiment_files: int = 0
    # Files of a type that was not chosen, e.g. {"TIFF": 12} when only ND2 was chosen.
    skipped_types: dict[str, int] = field(default_factory=dict)
    # Paths that resolved outside the chosen folder (junctions / links).
    skipped_outside: int = 0
    search_roots: list[str] = field(default_factory=list)
    file_types: tuple[str, ...] = ("nd2", "tiff")

    def lines(self) -> list[str]:
        out = []
        for root in self.search_roots:
            out.append(f"Looking in: {root}")
        if self.added or self.already_listed:
            kinds = ", ".join(f"{count} {kind}" for kind, count in sorted(self.by_type.items()))
            where = len(self.folders_with_images)
            text = f"Found {self.added + self.already_listed} image{'s' if self.added + self.already_listed != 1 else ''}"
            if kinds:
                text += f" ({kinds})"
            if where > 1:
                text += f" in {where} folders"
            text += "."
            if self.already_listed:
                text += f" {self.already_listed} were already listed."
            out.append(text)
        else:
            chosen = " or ".join(kind.upper() for kind in self.file_types) or "TIFF or ND2"
            out.append(f"No {chosen} images were found.")
        if self.skipped_outside:
            out.append(
                f"{self.skipped_outside} file{'s were' if self.skipped_outside != 1 else ' was'} skipped "
                "because they are outside the chosen folder."
            )
        for kind, count in sorted(self.skipped_types.items()):
            out.append(
                f"{count} {kind} file{'s were' if count != 1 else ' was'} not added, because only "
                f"{' and '.join(item.upper() for item in self.file_types)} files were chosen."
            )
        if self.folders_without_images:
            shown = self.folders_without_images[:8]
            more = len(self.folders_without_images) - len(shown)
            out.append(
                "Folders with no images: " + "; ".join(shown) + (f"; and {more} more" if more > 0 else "") + "."
            )
        for name in self.unreadable:
            out.append(f"{name}: File could not be opened.")
        if self.skipped_experiment_files:
            out.append("CellQuant's own result folders were skipped.")
        return out


FILE_TYPES = {"nd2": (".nd2",), "tiff": (".tif", ".tiff")}


def add_images(
    experiment: Experiment,
    paths: list[str | Path],
    *,
    file_types: tuple[str, ...] | list[str] | None = None,
) -> list[str]:
    """Import images, searching folders and their subfolders.

    ``file_types`` ("nd2", "tiff") limits which files are taken from folders;
    by default both. Files named one by one are always added.
    Returns user-facing notices: what was found, empty folders, and anything
    that may need attention (channels, pixel sizes, Z-stacks).
    """

    chosen = tuple(item for item in (file_types or ("nd2", "tiff")) if item in FILE_TYPES)
    if not chosen:
        raise ValueError("Choose at least one file type: ND2 or TIFF.")
    report = ImportReport(file_types=chosen)
    known = {(record.source_path, record.position) for record in experiment.images}
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_dir():
            root = path
            report.search_roots.append(_resolved(root))
            files = _find_images(root, report, chosen)
        else:
            root = path.parent
            files = [path]
        for number, file in enumerate(files, start=1):
            if not _is_under(file, root):
                report.skipped_outside += 1
                continue
            relative = _relative(file, root)
            if relative is None:
                report.skipped_outside += 1
                continue
            progress.update(f"Reading file {number} of {len(files)}: {relative}", number - 1, len(files))
            try:
                infos = inspect_image(file)
            except ImageLoadError:
                report.unreadable.append(relative)
                experiment.images.append(
                    ImageRecord(
                        image_id=f"img_{uuid.uuid4().hex[:8]}",
                        source_path=_resolved(file),
                        filename=file.name,
                        sample_name=_sample_name(relative, 0, 1),
                        relative_path=relative,
                        processing_status="needs_attention",
                        last_result="Failure",
                        last_message="File could not be opened.",
                    )
                )
                continue
            kind = "ND2" if file.suffix.lower() == ".nd2" else "TIFF"
            report.by_type[kind] = report.by_type.get(kind, 0) + len(infos)
            for info in infos:
                source = _resolved(file)
                if (source, info.position) in known:
                    report.already_listed += 1
                    continue
                record = _record_from_info(file, relative, info)
                experiment.images.append(record)
                known.add((source, info.position))
                report.added += 1
                _ensure_channels(experiment, info)
            _add_folder_columns(experiment, relative)
    for record in experiment.images:
        for column in experiment.metadata_columns:
            record.user_metadata.setdefault(column, "")
    notices = report.lines() + image_notices(experiment)
    experiment.touch()
    return _unique(notices)


def _find_images(root: Path, report: ImportReport, file_types: tuple[str, ...] = ("nd2", "tiff")) -> list[Path]:
    """Every image of the chosen types under root, skipping CellQuant's own folders."""

    wanted = {suffix for kind in file_types for suffix in FILE_TYPES[kind]}
    root_resolved = root.resolve()

    found: list[Path] = []
    folders: dict[str, int] = {}
    for folder, subfolders, names in _walk(root):
        if not _is_under(folder, root_resolved):
            subfolders[:] = []
            report.skipped_outside += 1
            continue
        if (folder / "experiment.json").is_file():
            skipped = [name for name in subfolders if name in _EXPERIMENT_FOLDERS]
            if skipped:
                report.skipped_experiment_files += 1
            subfolders[:] = [name for name in subfolders if name not in _EXPERIMENT_FOLDERS]
        subfolders.sort()
        every = sorted(folder / name for name in names if Path(name).suffix.lower() in IMAGE_SUFFIXES)
        images = []
        for path in every:
            if not _is_under(path, root_resolved):
                report.skipped_outside += 1
                continue
            if path.suffix.lower() not in wanted:
                kind = "ND2" if path.suffix.lower() == ".nd2" else "TIFF"
                report.skipped_types[kind] = report.skipped_types.get(kind, 0) + 1
                continue
            images.append(path)
        relative_folder = _relative(folder, root) or "."
        # A folder holding only files of an unchosen type is not reported as empty.
        folders[relative_folder] = len(every)
        if images:
            report.folders_with_images.append(relative_folder)
        found.extend(images)
    empty = []
    for name, count in folders.items():
        if count:
            continue
        elif name != "." and not any(other.startswith(name + "/") and n for other, n in folders.items()):
            # Only folders that hold no images anywhere below them.
            empty.append(name)
    # Name an empty folder once, not again for each of its empty subfolders.
    for name in empty:
        if any(name.startswith(parent + "/") for parent in empty):
            continue
        below = sum(1 for other in empty if other.startswith(name + "/"))
        report.folders_without_images.append(f"{name} (and its {below} subfolder{'s' if below != 1 else ''})" if below else name)
    return found


def _walk(root: Path):
    import os

    for folder, subfolders, names in os.walk(root):
        yield Path(folder), subfolders, names


def _is_under(path: Path, root: Path) -> bool:
    """True when path resolves inside root (rejects sibling folders reached via junctions)."""

    try:
        path.resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError):
        return False


def _relative(path: Path, root: Path) -> str | None:
    """Path relative to root using resolved paths, or None when outside root."""

    try:
        return path.resolve().relative_to(Path(root).resolve()).as_posix()
    except (ValueError, OSError):
        return None


def _resolved(path: Path) -> str:
    return str(path.resolve()) if path.exists() else str(path)


def _sample_name(relative: str, position: int, positions: int) -> str:
    name = relative.rsplit(".", 1)[0] if "." in Path(relative).name else relative
    return f"{name} [position {position + 1}]" if positions > 1 else name


def _record_from_info(file: Path, relative: str, info: ImageInfo) -> ImageRecord:
    return ImageRecord(
        image_id=f"img_{uuid.uuid4().hex[:8]}",
        source_path=_resolved(file),
        filename=file.name,
        sample_name=_sample_name(relative, info.position, info.positions),
        relative_path=relative,
        position=info.position,
        positions_in_file=info.positions,
        z_planes=info.z_planes,
        channel_names=list(info.channel_names),
        channel_colors=[list(color) if color else None for color in info.channel_colors],
        objective=info.objective,
        number_of_channels=info.n_channels,
        dimensions=[info.height, info.width],
        pixel_size_x=info.pixel_size_x,
        pixel_size_y=info.pixel_size_y,
        pixel_size_z=info.pixel_size_z,
        image_metadata={"axes": info.axes, "dtype": info.dtype, "notes": list(info.notes)},
    )


def _add_folder_columns(experiment: Experiment, relative: str) -> None:
    """Subfolder names become columns (Folder 1, Folder 2, ...) for grouping results."""

    parts = relative.split("/")[:-1]
    for index, part in enumerate(parts, start=1):
        column = f"Folder {index}"
        if column not in experiment.metadata_columns:
            experiment.metadata_columns.append(column)
    for record in experiment.images:
        if record.relative_path == relative:
            for index, part in enumerate(parts, start=1):
                record.user_metadata.setdefault(f"Folder {index}", part)


def image_notices(experiment: Experiment) -> list[str]:
    """Things to check about the listed images, in plain words."""

    notices = list(channel_warnings(experiment))
    images = [record for record in experiment.images if record.include and record.number_of_channels]
    sizes = sorted({round(record.pixel_size_x, 3) for record in images if record.pixel_size_x})
    if len(sizes) > 1:
        shown = ", ".join(f"{value:g}" for value in sizes[:4])
        notices.append(
            f"The images have {len(sizes)} different pixel sizes ({shown} µm/pixel), probably from different "
            "objectives. Set object sizes in µm², not pixels, so they mean the same in every image."
        )
    missing = [record for record in images if not record.pixel_size_x]
    if missing:
        notices.append(f"{len(missing)} image{'s have' if len(missing) != 1 else ' has'} no pixel size, so sizes will be in pixels. Enter it in step 1.")
    stacks = [record.z_planes for record in images if record.z_planes > 1]
    if stacks:
        low, high = min(stacks), max(stacks)
        span = f"{low}" if low == high else f"{low}-{high}"
        notices.append(
            f"{len(stacks)} image{'s are' if len(stacks) != 1 else ' is'} Z-stacks ({span} slices). Choose 2D "
            "(a projection or one slice) or 3D in step 2; it recommends one for this computer."
        )
    return notices


def channel_warnings(experiment: Experiment) -> list[str]:
    expected = len(experiment.channels)
    if expected == 0:
        return []
    messages = []
    for record in experiment.images:
        label = record.relative_path or record.filename
        if record.number_of_channels is not None and record.number_of_channels != expected:
            messages.append(
                f"{label}: This image has {record.number_of_channels} channels, but the experiment's channel list has "
                f"{expected}. Channels are matched by the names stored in the file; check this image (⚠ in the Plan), "
                "or set its channel in the Plan."
            )
    named = [record for record in experiment.images if record.channel_names]
    if named:
        first = named[0].channel_names
        different = [record for record in named if record.channel_names != first]
        if different:
            messages.append(
                f"{len(different)} image{'s have' if len(different) != 1 else ' has'} different channel names in the file "
                f"from {named[0].relative_path or named[0].filename} ({', '.join(first)}). Channels are matched by name, so "
                "another order is fine; an image without a channel of that name is marked ⚠ in the Plan."
            )
    return messages


def set_channel_name(experiment: Experiment, channel_index: int, name: str) -> None:
    for channel in experiment.channels:
        if channel.channel_index == channel_index:
            channel.channel_name = name
            channel.display_name = name
            experiment.touch()
            return
    raise KeyError(channel_index)


def add_metadata_column(experiment: Experiment, name: str) -> None:
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("Metadata column name must not be empty.")
    if cleaned not in experiment.metadata_columns:
        experiment.metadata_columns.append(cleaned)
    for record in experiment.images:
        record.user_metadata.setdefault(cleaned, "")
    experiment.touch()


def _ensure_channels(experiment: Experiment, info: ImageInfo | int) -> None:
    """Create the channel list from the first image, using the file's channel names when it has them."""

    if experiment.channels:
        return
    count = info if isinstance(info, int) else info.n_channels
    names = [] if isinstance(info, int) else list(info.channel_names)
    experiment.channels = [
        ChannelRecord(
            channel_index=index,
            channel_name=names[index] if index < len(names) and names[index] else f"Channel {index + 1}",
            display_name=names[index] if index < len(names) and names[index] else f"Channel {index + 1}",
        )
        for index in range(count)
    ]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _unique(messages: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered = []
    for message in messages:
        if message in seen:
            continue
        seen.add(message)
        ordered.append(message)
    return ordered
