"""Validation shared by preparation, the cluster (before submission and before inference) and import.

A package is READY only when ``READY`` names the bundle and the SHA-256 of
``checksums.json``, ``validation.json`` records a passing check of that same
digest, and every listed file has the listed size and hash. Consumers check
the contents and the binding, not only that the files exist.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cellquant.hpc import SCHEMA_VERSION
from cellquant.hpc.common import contained_path, safe_relative, sha256_bytes, sha256_file, sha256_json, utc_now
from cellquant.hpc.compat import check_against_runtime, check_calibration, effective_z
from cellquant.hpc.models import (
    Acquisition,
    BundleManifest,
    ClusterProfile,
    Issue,
    RuntimeContract,
    SchemaVersionError,
    ValidationReport,
    load_model,
)
from cellquant.recipe import Recipe, load_recipe

REQUIRED_FILES = (
    "bundle.json",
    "recipe.yaml",
    "runtime.json",
    "cluster.json",
    "scripts/preflight.sh",
    "scripts/submit.sh",
    "scripts/job.sbatch",
    "README_SUBMIT.md",
)
UNHASHED = ("checksums.json", "validation.json", "READY")


class BundleInvalid(Exception):
    def __init__(self, report: ValidationReport):
        self.report = report
        first = report.errors[0].message if report.errors else "The package is not valid."
        super().__init__(first)


@dataclass
class Bundle:
    root: Path
    manifest: BundleManifest
    recipe: Recipe
    runtime: RuntimeContract
    runtime_bytes: bytes
    profile: ClusterProfile
    checksums: dict[str, dict[str, Any]]
    checksums_sha256: str
    report: ValidationReport
    extra: dict[str, Any] = field(default_factory=dict)

    def input_file(self, acquisition: Acquisition) -> Path:
        return contained_path(self.root, acquisition.input_path)


def task_key(acquisition: dict[str, Any] | Acquisition, recipe_sha256: str, runtime_sha256: str) -> str:
    """What makes one acquisition's result: its pixels and identity, calibration, channels, Z choice, settings, runtime."""

    item = acquisition.model_dump(mode="json") if isinstance(acquisition, Acquisition) else acquisition
    return sha256_json(
        {
            "format": "cellquant-task-v1",
            "pixel_sha256": item["pixel_sha256"],
            "shape_czyx": item["shape_czyx"],
            "dtype": item["dtype"],
            "source": {
                "source_image_id": item["source_image_id"],
                "source_relative_path": item["source_relative_path"],
                "source_file_sha256": item["source_file_sha256"],
                "source_position": item["source_position"],
            },
            "effective_spacing_xyz_um": item["effective_spacing_xyz_um"],
            "channel_names": item["channel_names"],
            "effective_z_mode": item["effective_z_mode"],
            "effective_z_index": item["effective_z_index"],
            "recipe_scientific_sha256": recipe_sha256,
            "runtime_sha256": runtime_sha256,
        }
    )


def checksums_document(root: Path, relative_paths: list[str]) -> dict[str, Any]:
    files = []
    for relative in sorted(relative_paths):
        path = contained_path(root, relative)
        files.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return {"schema_version": SCHEMA_VERSION, "algorithm": "sha256", "files": files}


def ready_document(bundle_id: str, checksums_sha256: str) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "bundle_id": bundle_id, "checksums_sha256": checksums_sha256}


def validate_bundle(
    package: str | Path,
    *,
    require_ready: bool = True,
    deep: bool = False,
    cancel=None,
) -> tuple[ValidationReport, Bundle | None]:
    """Check a package. ``deep`` also reads every input and checks its pixel digest (slow)."""

    root = Path(package)
    errors: list[Issue] = []
    warnings: list[Issue] = []

    def fail(code: str, message: str, **extra) -> None:
        errors.append(Issue(code=code, message=message, **extra))

    def done(bundle: Bundle | None = None, digest: str | None = None, bundle_id: str | None = None):
        report = ValidationReport(
            ok=not errors, bundle_id=bundle_id, checked_at=utc_now(), checksums_sha256=digest, errors=errors, warnings=warnings
        )
        if bundle is not None:
            bundle.report = report
        return report, (bundle if not errors else None)

    if not root.is_dir():
        fail("E_MISSING_FILE", f"The package folder {root} does not exist.")
        return done()
    # 1. checksums.json and every file it lists
    checksum_path = root / "checksums.json"
    if not checksum_path.is_file():
        fail("E_MISSING_FILE", "checksums.json is missing, so the package cannot be checked.", path="checksums.json")
        return done()
    checksum_bytes = checksum_path.read_bytes()
    digest = sha256_bytes(checksum_bytes)
    try:
        document = json.loads(checksum_bytes)
        if document.get("schema_version") != SCHEMA_VERSION or document.get("algorithm") != "sha256":
            raise SchemaVersionError("checksums.json has an unsupported format or schema version.")
        entries = {}
        for entry in document["files"]:
            relative = str(entry["path"])
            safe_relative(relative)
            if relative in entries:
                raise ValueError(f"{relative} is listed twice.")
            if relative in UNHASHED:
                raise ValueError(f"{relative} must not be listed in checksums.json.")
            entries[relative] = {"bytes": int(entry["bytes"]), "sha256": str(entry["sha256"])}
    except (ValueError, KeyError, TypeError) as exc:
        fail("E_SCHEMA", f"checksums.json could not be read: {exc}", path="checksums.json")
        return done(digest=digest)
    for required in REQUIRED_FILES:
        if required not in entries:
            fail("E_MISSING_FILE", f"{required} is not listed in checksums.json.", path=required)
    for relative, expected in entries.items():
        if cancel is not None:
            cancel()
        try:
            path = contained_path(root, relative)
        except ValueError as exc:
            fail("E_PATH", str(exc), path=relative)
            continue
        if path.is_symlink():
            fail("E_PATH", f"{relative} is a link. Packages must contain real files.", path=relative)
            continue
        if not path.is_file():
            fail("E_MISSING_FILE", f"{relative} is missing.", path=relative, fix="Transfer the whole package again.")
            continue
        size = path.stat().st_size
        if size != expected["bytes"]:
            fail(
                "E_CHECKSUM",
                f"{relative} has {size} bytes, but {expected['bytes']} were written. The transfer is incomplete or the file was changed.",
                path=relative,
                fix="Transfer the package again. Do not edit files inside a package; prepare a new one instead.",
            )
            continue
        if sha256_file(path, cancel=cancel) != expected["sha256"]:
            fail(
                "E_CHECKSUM",
                f"{relative} does not match its checksum. It was changed or damaged after preparation.",
                path=relative,
                fix="Transfer the package again. Do not edit files inside a package; prepare a new one instead.",
            )
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            if relative not in entries:
                fail("E_PATH", f"{relative} is a link inside the package.", path=relative)
            continue
        if path.is_file() and relative not in entries and relative not in UNHASHED:
            warnings.append(Issue(code="W_EXTRA_FILE", message=f"{relative} is not part of the package and is ignored.", path=relative))
    if errors:
        return done(digest=digest)
    # 2. the records
    try:
        manifest: BundleManifest = load_model(BundleManifest, root / "bundle.json")  # type: ignore[assignment]
        runtime_bytes = (root / "runtime.json").read_bytes()
        runtime: RuntimeContract = load_model(RuntimeContract, data=json.loads(runtime_bytes))  # type: ignore[assignment]
        profile: ClusterProfile = load_model(ClusterProfile, root / "cluster.json")  # type: ignore[assignment]
        recipe = load_recipe(root / "recipe.yaml")
    except SchemaVersionError as exc:
        fail("E_UNSUPPORTED_SCHEMA", str(exc))
        return done(digest=digest)
    except Exception as exc:  # noqa: BLE001 - reported to the user
        fail("E_SCHEMA", f"A package record could not be read: {exc}")
        return done(digest=digest)
    bundle = Bundle(root, manifest, recipe, runtime, runtime_bytes, profile, entries, digest, report=None)  # type: ignore[arg-type]
    # 3. READY binding
    ready_path = root / "READY"
    validation_path = root / "validation.json"
    if require_ready:
        if not ready_path.is_file():
            fail("E_READY", "READY is missing: the package was not finished, or preparation failed.", fix="Prepare the package again.")
        else:
            try:
                ready = json.loads(ready_path.read_text(encoding="utf-8"))
            except ValueError:
                ready = {}
            if ready != ready_document(manifest.bundle_id, digest):
                fail("E_READY", "READY does not match this package's bundle and checksums.", fix="Prepare the package again.")
        if not validation_path.is_file():
            fail("E_READY", "validation.json is missing.")
        else:
            try:
                validation = json.loads(validation_path.read_text(encoding="utf-8"))
            except ValueError:
                validation = {}
            if not validation.get("ok") or validation.get("checksums_sha256") != digest or validation.get("bundle_id") != manifest.bundle_id:
                fail("E_READY", "validation.json does not record a passing check of these files.")
    # 4. identities and hashes between records
    if recipe.content_hash() != manifest.recipe_scientific_sha256:
        fail("E_RECIPE", "recipe.yaml differs from the settings the package was prepared with.", path="recipe.yaml")
    if sha256_bytes(runtime_bytes) != manifest.runtime_sha256:
        fail("E_RUNTIME", "runtime.json differs from the runtime the package was prepared for.", path="runtime.json")
    if profile.runtime_contract_path != "runtime.json" or profile.runtime_sha256 != manifest.runtime_sha256:
        fail("E_RUNTIME", "cluster.json does not point at this package's runtime.json.", path="cluster.json")
    if profile.runtime_id != runtime.runtime_id:
        fail("E_RUNTIME", f"cluster.json names runtime '{profile.runtime_id}', but runtime.json is '{runtime.runtime_id}'.")
    if manifest.application_build_sha256 != runtime.application_build_sha256:
        fail(
            "E_RUNTIME",
            "The package was prepared by a different CellQuant build from the one the runtime contract describes.",
            fix="Prepare packages with the CellQuant release that is installed on the cluster.",
        )
    unresolved = profile.unresolved_fields()
    if unresolved:
        fail("E_PROFILE_PLACEHOLDER", "cluster.json still has placeholders: " + ", ".join(unresolved) + ".")
    for issue in check_against_runtime(recipe, runtime):
        errors.append(issue)
    for script in ("scripts/preflight.sh", "scripts/submit.sh", "scripts/job.sbatch"):
        if b"\r" in (root / script).read_bytes():
            fail("E_SCRIPT", f"{script} has Windows line endings, which bash cannot run.", path=script, fix="Prepare the package again.")
    # 5. every acquisition
    channel_count = len(manifest.channel_layout)
    used = [recipe.object_set.segmentation_channel, *[item.channel for item in recipe.measurements]]
    if any(index >= channel_count for index in used):
        fail("E_CHANNELS", f"The settings use channel {max(used) + 1}, but the images have {channel_count} channels.")
    for acquisition in manifest.acquisitions:
        label = f"{acquisition.acquisition_id} ({acquisition.source_relative_path})"
        entry = entries.get(acquisition.input_path)
        if entry is None:
            fail("E_MISSING_FILE", f"{label}: {acquisition.input_path} is not in checksums.json.", acquisition_id=acquisition.acquisition_id)
        elif entry["sha256"] != acquisition.input_file_sha256:
            fail("E_CHECKSUM", f"{label}: the input file hash differs from bundle.json.", acquisition_id=acquisition.acquisition_id)
        if acquisition.channel_names != manifest.channel_layout and not (manifest.channel_layout_confirmed and acquisition.channel_layout_confirmed):
            fail(
                "E_CHANNELS",
                f"{label}: channel names {acquisition.channel_names} differ from the package layout {manifest.channel_layout} and were not confirmed.",
                acquisition_id=acquisition.acquisition_id,
            )
        mode, index, z_issues = effective_z(recipe, acquisition.shape_czyx[1])
        errors.extend(z_issues)
        if (mode, index) != (acquisition.effective_z_mode, acquisition.effective_z_index):
            fail("E_Z", f"{label}: the recorded Z handling does not follow from the settings.", acquisition_id=acquisition.acquisition_id)
        calibration_errors, _calibration_warnings = check_calibration(
            acquisition.effective_spacing_xyz_um, recipe=recipe, z_planes=acquisition.shape_czyx[1], acquisition_label=label
        )
        errors.extend(calibration_errors)
        if task_key(acquisition, manifest.recipe_scientific_sha256, manifest.runtime_sha256) != acquisition.task_key:
            fail("E_TASK_KEY", f"{label}: its task key does not match its contents.", acquisition_id=acquisition.acquisition_id)
        if deep and not errors:
            from cellquant.hpc.common import pixel_sha256
            from cellquant.image import read_stack

            stack = read_stack(bundle.input_file(acquisition))
            czyx = stack.czyx
            if list(czyx.shape) != acquisition.shape_czyx or str(czyx.dtype) != acquisition.dtype:
                fail("E_PIXELS", f"{label}: the prepared image has shape {list(czyx.shape)} {czyx.dtype}, expected {acquisition.shape_czyx} {acquisition.dtype}.")
            elif pixel_sha256(czyx) != acquisition.pixel_sha256:
                fail("E_PIXELS", f"{label}: the prepared pixels differ from the ones recorded at preparation.")
            del stack, czyx
    return done(bundle, digest, manifest.bundle_id)


def open_bundle(package: str | Path, *, require_ready: bool = True, deep: bool = False, cancel=None) -> Bundle:
    report, bundle = validate_bundle(package, require_ready=require_ready, deep=deep, cancel=cancel)
    if bundle is None:
        raise BundleInvalid(report)
    return bundle
