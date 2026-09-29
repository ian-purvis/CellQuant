"""Freeze an experiment's settings and export its images into a checksummed cluster package.

Preparation reads images and writes a new folder; it never segments, never
changes the recipe, and never writes into the image folders. A package is
published under its final name only after validation passes and ``READY`` is
written. A cancelled or failed attempt is left as ``<name>.incomplete``.
"""

from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from cellquant import progress
from cellquant.__version__ import __version__
from cellquant.errors import CellQuantError, ImageLoadError
from cellquant.experiment import Experiment
from cellquant.hpc.common import (
    pixel_sha256,
    sha256_bytes,
    sha256_file,
    utc_now,
    write_bytes_atomic,
    write_json_atomic,
)
from cellquant.hpc.compat import check_against_runtime, check_calibration, effective_z
from cellquant.hpc.models import Acquisition, BundleManifest, Issue, LocalSidecar
from cellquant.hpc.profiles import ResolvedProfile, bundled_profile
from cellquant.hpc.runtime import application_build_sha256
from cellquant.hpc.templates import write_scripts
from cellquant.hpc.validate import checksums_document, ready_document, task_key, validate_bundle
from cellquant.image import ImageInfo, inspect_image, read_stack
from cellquant.inputs import resolve_source_path
from cellquant.recipe import Recipe, save_recipe

# Windows refuses most paths of 260 characters or more; stay well below.
MAX_WINDOWS_PATH = 240
NOTE_NO_EDITS = (
    "Manual object edits, deleted objects and approvals from the source experiment are not applied to the new "
    "segmentation. Image selection, sample names and metadata are kept."
)


class PreparationError(CellQuantError):
    def __init__(self, message: str, issues: list[Issue] | None = None):
        super().__init__(message)
        self.issues = issues or []


@dataclass
class PlannedAcquisition:
    acquisition_id: str
    record: Any
    path: Path
    info: ImageInfo | None
    effective_spacing: list[float | None]
    file_spacing: list[float | None]
    calibration_source: str
    effective_z_mode: str = "none"
    effective_z_index: int | None = None
    channel_names: list[str] = field(default_factory=list)
    bytes: int = 0
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)

    @property
    def label(self) -> str:
        return self.record.relative_path or self.record.filename


@dataclass
class PreparationPlan:
    experiment: Experiment
    recipe: Recipe
    profile: ResolvedProfile
    acquisitions: list[PlannedAcquisition]
    channel_layout: list[str]
    channel_layout_confirmed: bool
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    input_bytes: int = 0
    peak_memory_bytes: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors and all(not item.errors for item in self.acquisitions)

    def all_errors(self) -> list[Issue]:
        return [*self.errors, *[issue for item in self.acquisitions for issue in item.errors]]

    def all_warnings(self) -> list[Issue]:
        return [*self.warnings, *[issue for item in self.acquisitions for issue in item.warnings]]


@dataclass
class PreparationResult:
    package_dir: Path
    sidecar: Path
    manifest: BundleManifest
    warnings: list[Issue]


def plan_preparation(
    experiment: Experiment,
    recipe: Recipe,
    profile: ResolvedProfile,
    *,
    image_ids: list[str] | None = None,
    confirm_channel_layout: bool = False,
) -> PreparationPlan:
    """Check everything that can be checked without reading pixels: images, channels, calibration, settings, profile."""

    experiment_dir = Path(experiment.directory)
    errors: list[Issue] = list(profile.errors)
    warnings: list[Issue] = list(profile.warnings)
    if profile.runtime is not None:
        errors.extend(check_against_runtime(recipe, profile.runtime))
        if application_build_sha256() != profile.runtime.application_build_sha256:
            errors.append(
                Issue(
                    code="E_RUNTIME_BUILD",
                    message="This copy of CellQuant is not the release installed on the cluster (the build digests differ).",
                    fix="Update CellQuant on this computer or on the cluster so both run the same release, then export a new runtime contract.",
                )
            )
    if image_ids is None:
        records = [record for record in experiment.images if record.include]
    else:
        wanted = list(dict.fromkeys(image_ids))
        known = {record.image_id: record for record in experiment.images}
        unknown = [image_id for image_id in wanted if image_id not in known]
        if unknown:
            errors.append(Issue(code="E_SELECTION", message=f"Unknown image IDs: {', '.join(unknown)}."))
        records = [known[image_id] for image_id in wanted if image_id in known]
    if not records:
        errors.append(Issue(code="E_SELECTION", message="No images are selected.", fix="Include at least one image."))
    planned: list[PlannedAcquisition] = []
    identities: dict[tuple[str, int], str] = {}
    for index, record in enumerate(records, start=1):
        acquisition_id = f"a{index:06d}"
        path = resolve_source_path(record, experiment_dir)
        item = PlannedAcquisition(
            acquisition_id=acquisition_id,
            record=record,
            path=path,
            info=None,
            effective_spacing=[record.pixel_size_x, record.pixel_size_y, record.pixel_size_z],
            file_spacing=[None, None, None],
            calibration_source="missing",
        )
        planned.append(item)
        progress.update(f"Checking image {index} of {len(records)}: {item.label}", index - 1, len(records))
        key = (str(path.resolve()) if path.exists() else str(path), int(record.position))
        if key in identities:
            item.errors.append(
                Issue(code="E_DUPLICATE", message=f"{item.label} (position {record.position + 1}) is selected twice.", acquisition_id=acquisition_id)
            )
            continue
        identities[key] = acquisition_id
        problem = _unreadable_reason(path)
        if problem:
            item.errors.append(Issue(code="E_INPUT", message=f"{item.label}: {problem}", acquisition_id=acquisition_id, fix=_UNREADABLE_FIX))
            continue
        try:
            infos = inspect_image(path)
        except ImageLoadError as exc:
            item.errors.append(Issue(code="E_INPUT", message=f"{item.label}: {exc}", acquisition_id=acquisition_id))
            continue
        if not 0 <= record.position < len(infos):
            item.errors.append(Issue(code="E_INPUT", message=f"{item.label}: position {record.position + 1} is not in the file.", acquisition_id=acquisition_id))
            continue
        info = infos[record.position]
        item.info = info
        for note in info.notes:
            if "time series" in note:
                item.errors.append(Issue(code="E_UNSUPPORTED", message=f"{item.label}: {note}.", acquisition_id=acquisition_id))
        item.file_spacing = [info.pixel_size_x, info.pixel_size_y, info.pixel_size_z]
        item.calibration_source = _calibration_source(item.file_spacing, item.effective_spacing)
        names = list(info.channel_names) if len(info.channel_names) == info.n_channels and all(info.channel_names) else []
        if not names:
            names = [
                channel.channel_name for channel in sorted(experiment.channels, key=lambda channel: channel.channel_index)
            ][: info.n_channels]
            if len(names) != info.n_channels:
                names = [f"Channel {number + 1}" for number in range(info.n_channels)]
        item.channel_names = names
        mode, z_index, z_issues = effective_z(recipe, info.z_planes)
        item.effective_z_mode, item.effective_z_index = mode, z_index
        for issue in z_issues:
            item.errors.append(issue.model_copy(update={"acquisition_id": acquisition_id, "message": f"{item.label}: {issue.message}"}))
        if mode == "none" and recipe.z_stack in ("stitch_slices", "full_3d"):
            item.warnings.append(
                Issue(code="W_SINGLE_PLANE", message=f"{item.label} has one slice, so it is analyzed in 2D.", acquisition_id=acquisition_id)
            )
        calibration_errors, calibration_warnings = check_calibration(
            item.effective_spacing, recipe=recipe, z_planes=info.z_planes, acquisition_label=item.label
        )
        item.errors.extend(issue.model_copy(update={"acquisition_id": acquisition_id}) for issue in calibration_errors)
        item.warnings.extend(issue.model_copy(update={"acquisition_id": acquisition_id}) for issue in calibration_warnings)
        itemsize = np.dtype(info.dtype).itemsize if info.dtype else 2
        item.bytes = int(info.n_channels * max(info.z_planes, 1) * info.height * info.width * itemsize)
    readable = [item for item in planned if item.info is not None]
    layout: list[str] = readable[0].channel_names if readable else []
    confirmed = bool(confirm_channel_layout)
    for item in readable:
        if len(item.channel_names) != len(layout):
            item.errors.append(
                Issue(
                    code="E_CHANNELS",
                    message=f"{item.label} has {len(item.channel_names)} channels; the first image has {len(layout)}. "
                    "Images with a different channel layout need their own package.",
                    acquisition_id=item.acquisition_id,
                )
            )
        elif item.channel_names != layout and not confirmed:
            item.errors.append(
                Issue(
                    code="E_CHANNEL_NAMES",
                    message=f"{item.label} names its channels {', '.join(item.channel_names)}; the first image uses {', '.join(layout)}.",
                    acquisition_id=item.acquisition_id,
                    fix="If the channels are in the same order and mean the same thing, confirm the channel layout. Channels are never reordered.",
                )
            )
    if layout:
        used = [recipe.object_set.segmentation_channel, *[measurement.channel for measurement in recipe.measurements]]
        if max(used) >= len(layout):
            errors.append(
                Issue(code="E_CHANNELS", message=f"The settings use channel {max(used) + 1}, but the images have {len(layout)} channels.")
            )
    warnings.append(Issue(code="W_EDITS_NOT_REPLAYED", message=NOTE_NO_EDITS))
    input_bytes = sum(item.bytes for item in readable)
    peak = 0
    for item in readable:
        file_size = item.path.stat().st_size if item.path.is_file() else 0
        # ND2 files are read whole before a position is chosen; then the stack is copied once for writing.
        peak = max(peak, (file_size if item.path.suffix.lower() == ".nd2" else 0) + 3 * item.bytes)
    return PreparationPlan(experiment, recipe, profile, planned, layout, confirmed, errors, warnings, input_bytes, peak)


_UNREADABLE_FIX = "Make the file available on this computer (for OneDrive: 'Always keep on this device'), then check again."


def _unreadable_reason(path: Path) -> str:
    if not path.is_file():
        return "the file was not found."
    try:
        attributes = getattr(os.stat(path), "st_file_attributes", 0)
    except OSError as exc:
        return f"the file cannot be read ({exc})."
    # Windows cloud placeholders (OneDrive "online-only" files).
    if attributes & (0x400000 | 0x1000):
        return "the file is stored online only and is not on this computer."
    try:
        with path.open("rb") as handle:
            handle.read(1)
    except OSError as exc:
        return f"the file cannot be read ({exc})."
    return ""


def _calibration_source(file_values: list[float | None], effective: list[float | None]) -> str:
    if all(value is None for value in effective):
        return "missing"
    same = [
        (a is None and b is None) or (a is not None and b is not None and abs(a - b) <= 1e-9 * max(abs(a), abs(b), 1.0))
        for a, b in zip(file_values, effective, strict=True)
    ]
    if all(same):
        return "file"
    if all(value is None for value in file_values):
        return "user"
    return "file_and_user"


def check_output_root(output_root: Path, plan: PreparationPlan, package_name: str) -> list[Issue]:
    """The output folder must not be inside an image folder, and the package's paths must stay short enough for Windows."""

    issues = []
    root = output_root.resolve()
    sources = {item.path.resolve().parent for item in plan.acquisitions if item.path.exists()}
    if plan.experiment.input_directory:
        sources.add(Path(plan.experiment.input_directory).resolve())
    for folder in sources:
        if root == folder or folder in root.parents:
            issues.append(
                Issue(
                    code="E_OUTPUT",
                    message=f"The output folder {output_root} is inside the image folder {folder}.",
                    fix="Choose a short folder elsewhere, for example D:\\CellQuant_HPC.",
                )
            )
            break
    longest = max(
        [
            len(str(root / f"{package_name}.incomplete" / "inputs" / "a000000.ome.tif")),
            len(str(root / f"{package_name}.local.json")),
            len(str(root / package_name / "scripts" / "preflight.sh")),
        ]
    )
    if longest >= MAX_WINDOWS_PATH:
        issues.append(
            Issue(
                code="E_PATH_TOO_LONG",
                message=f"Paths in the package would be {longest} characters long; Windows allows fewer than {MAX_WINDOWS_PATH} here.",
                fix="Choose a shorter output folder, for example D:\\CellQuant_HPC.",
            )
        )
    return issues


def prepare_package(plan: PreparationPlan, output_root: str | Path) -> PreparationResult:
    """Export the planned images and publish a READY package. Honors progress cancellation between steps."""

    if not plan.ok:
        raise PreparationError("The package cannot be prepared until the listed problems are fixed.", plan.all_errors())
    profile = plan.profile
    assert profile.runtime is not None and profile.runtime_bytes is not None
    output = Path(output_root)
    output.mkdir(parents=True, exist_ok=True)
    bundle_id = uuid.uuid4().hex
    package_name = f"cq_hpc_{bundle_id[:8]}"
    issues = check_output_root(output, plan, package_name)
    if issues:
        raise PreparationError(issues[0].message, issues)
    final = output / package_name
    work = output / f"{package_name}.preparing"
    if final.exists() or work.exists():
        raise PreparationError(f"{final} already exists.")
    work.mkdir()
    try:
        manifest = _build(plan, work, bundle_id, package_name)
        progress.update("Publishing the package")
        os.replace(work, final)
    except BaseException:
        failed = output / f"{package_name}.incomplete"
        try:
            os.replace(work, failed)
        except OSError:
            pass
        raise
    sidecar_path = output / f"{package_name}.local.json"
    sidecar = LocalSidecar(
        bundle_id=bundle_id,
        package_name=package_name,
        created_at=manifest.created_at,
        source_experiment_dir=str(Path(plan.experiment.directory)),
        acquisitions={
            item.acquisition_id: {
                "source_image_id": item.record.image_id,
                "source_path": str(item.path),
                "position": int(item.record.position),
                "sample_name": item.record.sample_name,
            }
            for item in plan.acquisitions
        },
    )
    write_json_atomic(sidecar_path, sidecar.model_dump(mode="json"))
    return PreparationResult(final, sidecar_path, manifest, plan.all_warnings())


def _build(plan: PreparationPlan, work: Path, bundle_id: str, package_name: str) -> BundleManifest:
    profile = plan.profile
    runtime = profile.runtime
    assert runtime is not None and profile.runtime_bytes is not None
    recipe = plan.recipe.model_copy(deep=True)
    recipe_sha = recipe.content_hash()
    runtime_sha = sha256_bytes(profile.runtime_bytes)
    (work / "inputs").mkdir()
    acquisitions: list[Acquisition] = []
    total = len(plan.acquisitions)
    for number, item in enumerate(plan.acquisitions, start=1):
        prefix = f"Image {number} of {total} ({item.label})"
        progress.update(f"{prefix}: checking the source file", number - 1, total)
        before = sha256_file(item.path, cancel=progress.check_cancelled)
        progress.update(f"{prefix}: reading every channel and slice", number - 1, total)
        stack = read_stack(item.path, position=item.record.position)
        czyx = np.ascontiguousarray(stack.czyx)
        info = item.info
        assert info is not None
        expected = (info.n_channels, max(info.z_planes, 1), info.height, info.width)
        if tuple(czyx.shape) != expected:
            raise PreparationError(f"{item.label}: the file holds {czyx.shape}, but its metadata says {expected}.")
        digest = pixel_sha256(czyx)
        relative = f"inputs/{item.acquisition_id}.ome.tif"
        target = work / relative
        progress.update(f"{prefix}: writing the lossless copy", number - 1, total)
        _write_ome(target, czyx, item)
        progress.update(f"{prefix}: reading the copy back to compare", number - 1, total)
        _verify_copy(target, czyx, item)
        del stack, czyx
        after = sha256_file(item.path, cancel=progress.check_cancelled)
        if after != before:
            raise PreparationError(
                f"{item.label} changed while it was being exported. Nothing was published; prepare the package again.",
                [Issue(code="E_SOURCE_CHANGED", message=f"{item.label} changed during export.", acquisition_id=item.acquisition_id)],
            )
        colors = list(item.record.channel_colors or [])
        if len(colors) != len(item.channel_names):
            colors = [list(color) if color else None for color in (info.channel_colors or ())]
            if len(colors) != len(item.channel_names):
                colors = []
        fields = {
            "acquisition_id": item.acquisition_id,
            "source_image_id": item.record.image_id,
            "source_relative_path": item.record.relative_path or item.record.filename,
            "source_file_sha256": before,
            "source_position": int(item.record.position),
            "sample_name": item.record.sample_name,
            "user_metadata": dict(item.record.user_metadata),
            "input_path": relative,
            "input_file_sha256": sha256_file(target),
            "pixel_sha256": digest,
            "shape_czyx": list(expected),
            "dtype": str(np.dtype(info.dtype)) if info.dtype else "uint16",
            "channel_names": item.channel_names,
            "channel_colors": colors,
            "file_spacing_xyz_um": item.file_spacing,
            "effective_spacing_xyz_um": item.effective_spacing,
            "calibration_source": item.calibration_source,
            "effective_z_mode": item.effective_z_mode,
            "effective_z_index": item.effective_z_index,
            "objective": info.objective or item.record.objective or "",
            "channel_layout_confirmed": plan.channel_layout_confirmed,
        }
        fields["task_key"] = task_key(fields, recipe_sha, runtime_sha)
        acquisitions.append(Acquisition.model_validate(fields))
    progress.update("Writing the settings, scripts and checksums", total, total)
    manifest = BundleManifest(
        bundle_id=bundle_id,
        package_name=package_name,
        created_at=utc_now(),
        cellquant_version=__version__,
        application_build_sha256=application_build_sha256(),
        source_experiment_id=plan.experiment.experiment_id,
        source_experiment_name=plan.experiment.experiment_name,
        recipe_scientific_sha256=recipe_sha,
        runtime_sha256=runtime_sha,
        channel_layout=plan.channel_layout,
        channel_layout_confirmed=plan.channel_layout_confirmed,
        notes=[NOTE_NO_EDITS],
        acquisitions=acquisitions,
    )
    save_recipe(recipe, work / "recipe.yaml")
    write_bytes_atomic(work / "runtime.json", profile.runtime_bytes)
    cluster = bundled_profile(profile.profile)
    write_bytes_atomic(work / "cluster.json", (cluster.model_dump_json(indent=2) + "\n").encode("utf-8"))
    write_bytes_atomic(work / "bundle.json", (manifest.model_dump_json(indent=2) + "\n").encode("utf-8"))
    write_scripts(work, manifest, cluster, runtime)
    listed = sorted(
        path.relative_to(work).as_posix()
        for path in work.rglob("*")
        if path.is_file() and path.relative_to(work).as_posix() not in ("checksums.json", "validation.json", "READY")
    )
    document = checksums_document(work, listed)
    import json

    checksum_bytes = (json.dumps(document, indent=2) + "\n").encode("utf-8")
    write_bytes_atomic(work / "checksums.json", checksum_bytes)
    progress.update("Validating the finished package", total, total)
    report, _bundle = validate_bundle(work, require_ready=False, deep=False, cancel=progress.check_cancelled)
    write_json_atomic(work / "validation.json", report.model_dump(mode="json"))
    if not report.ok:
        raise PreparationError("The package failed validation: " + report.errors[0].message, report.errors)
    write_json_atomic(work / "READY", ready_document(bundle_id, sha256_bytes(checksum_bytes)))
    return manifest


def _ome_color(color) -> int | None:
    if not color or len(color) < 3:
        return None
    red, green, blue = (max(0, min(255, int(round(float(value) * 255)))) for value in color[:3])
    value = (red << 24) | (green << 16) | (blue << 8) | 255
    return value - (1 << 32) if value >= 1 << 31 else value


def _write_ome(target: Path, czyx: np.ndarray, item: PlannedAcquisition) -> None:
    import tifffile

    zcyx = np.moveaxis(czyx, 0, 1)
    x, y, z = item.effective_spacing
    metadata: dict[str, Any] = {"axes": "ZCYX", "Channel": {"Name": list(item.channel_names)}}
    colors = [_ome_color(color) for color in (item.record.channel_colors or [])]
    if len(colors) == czyx.shape[0] and all(color is not None for color in colors):
        metadata["Channel"]["Color"] = colors
    if x is not None and y is not None:
        metadata.update({"PhysicalSizeX": float(x), "PhysicalSizeXUnit": "µm", "PhysicalSizeY": float(y), "PhysicalSizeYUnit": "µm"})
    if z is not None:
        metadata.update({"PhysicalSizeZ": float(z), "PhysicalSizeZUnit": "µm"})
    bigtiff = zcyx.nbytes > 3_500_000_000
    temporary = target.with_name(target.name + ".tmp")
    tifffile.imwrite(temporary, np.ascontiguousarray(zcyx), ome=True, bigtiff=bigtiff, photometric="minisblack", metadata=metadata)
    os.replace(temporary, target)


def _verify_copy(target: Path, czyx: np.ndarray, item: PlannedAcquisition) -> None:
    """Read the written file the way the worker will, and require identical pixels, order and calibration."""

    import tifffile

    copy = read_stack(target, position=0)
    if copy.czyx.shape != czyx.shape or copy.czyx.dtype != czyx.dtype or not np.array_equal(copy.czyx, czyx):
        raise PreparationError(f"{item.label}: the written copy does not match the source pixels exactly.")
    with tifffile.TiffFile(target) as tif:
        parsed = tifffile.xml2dict(tif.ome_metadata or "<OME/>")
    image = parsed.get("OME", {}).get("Image", {})
    pixels = (image[0] if isinstance(image, list) else image).get("Pixels", {})
    channels = pixels.get("Channel", [])
    channels = channels if isinstance(channels, list) else [channels]
    names = [str(channel.get("Name", "")) for channel in channels]
    if names != list(item.channel_names):
        raise PreparationError(f"{item.label}: the channel names were not written in order ({names}).")
    for axis, value in zip("XYZ", item.effective_spacing, strict=True):
        stored = pixels.get(f"PhysicalSize{axis}")
        if (value is None) != (stored is None) or (value is not None and abs(float(stored) - float(value)) > 1e-9 * float(value)):
            raise PreparationError(f"{item.label}: the {axis} pixel size was not written correctly.")


def preparation_summary(plan: PreparationPlan) -> dict[str, Any]:
    """Numbers for the preparation screen and the CLI."""

    return {
        "images": len(plan.acquisitions),
        "input_gib": round(plan.input_bytes / 1024**3, 3),
        "peak_memory_gib": round(plan.peak_memory_bytes / 1024**3, 2),
        "channels": plan.channel_layout,
        "errors": [issue.model_dump() for issue in plan.all_errors()],
        "warnings": [issue.model_dump() for issue in plan.all_warnings()],
        "python": sys.version.split()[0],
    }
