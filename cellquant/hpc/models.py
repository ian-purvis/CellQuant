"""Versioned records exchanged between preparation, the cluster worker and import.

Every model rejects unknown fields. ``schema_version`` is checked by every
reader, and a file written by a newer version is refused rather than guessed
at. ``write_schemas`` publishes the JSON schemas (``cellquant/hpc/schemas``).
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cellquant.hpc import BUNDLE_KIND, SCHEMA_VERSION

Sha256 = str
Engine = Literal["cellpose3", "cellpose4"]
ZMode = Literal["max_projection", "single_plane", "stitch_slices", "full_3d"]
EffectiveZMode = Literal["none", "max_projection", "single_plane", "stitch_slices", "full_3d"]
TaskState = Literal["pending", "running", "succeeded", "failed", "cancelled", "interrupted"]
ComputeOutcome = Literal["not_started", "running", "completed", "completed_with_failures", "failed", "cancelled", "interrupted"]
PublicationOutcome = Literal["not_published", "verified_partial", "verified_complete", "failed"]
ProfileStatus = Literal["draft", "experimental", "production"]

_SHA = re.compile(r"^[0-9a-f]{64}$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_GRES = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_:.-]{0,127}$")
_HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")
_MODULE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./+-]{0,127}$")
_ACQUISITION = re.compile(r"^a\d{6}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
PLACEHOLDER = re.compile(r"(CHANGE[_ ]?ME|YOUR[_ ]|<[^>]*>|\bUSER\b)")


class HpcModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _check_sha(value: str, label: str) -> str:
    if not isinstance(value, str) or not _SHA.match(value):
        raise ValueError(f"{label} must be a lowercase SHA-256 hex digest")
    return value


def _check_posix_absolute(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.startswith("/"):
        raise ValueError(f"{label} must be an absolute Linux path starting with '/'")
    if any(ord(character) < 32 for character in value) or "\\" in value:
        raise ValueError(f"{label} must not contain control characters or backslashes")
    parts = PurePosixPath(value).parts
    if ".." in parts:
        raise ValueError(f"{label} must not contain '..'")
    return value.rstrip("/") or "/"


def _check_relative(value: str, label: str) -> str:
    from cellquant.hpc.common import safe_relative

    try:
        safe_relative(value)
    except ValueError as exc:
        raise ValueError(f"{label}: {exc}") from exc
    return value


# --- package -----------------------------------------------------------------------------------------------


class Acquisition(HpcModel):
    """One prepared image: where it came from, its exported copy, and how it will be analyzed."""

    acquisition_id: str
    source_image_id: str
    source_relative_path: str
    source_file_sha256: Sha256
    source_position: int = Field(ge=0)
    sample_name: str
    user_metadata: dict[str, Any] = Field(default_factory=dict)
    input_path: str
    input_file_sha256: Sha256
    pixel_sha256: Sha256
    shape_czyx: list[int]
    dtype: str
    channel_names: list[str]
    channel_colors: list[list[float] | None] = Field(default_factory=list)
    file_spacing_xyz_um: list[float | None]
    effective_spacing_xyz_um: list[float | None]
    calibration_source: Literal["file", "user", "file_and_user", "missing"]
    effective_z_mode: EffectiveZMode
    effective_z_index: int | None = None
    objective: str = ""
    channel_layout_confirmed: bool = False
    task_key: Sha256

    @field_validator("acquisition_id")
    @classmethod
    def _acquisition_id(cls, value: str) -> str:
        if not _ACQUISITION.match(value):
            raise ValueError("acquisition_id must look like a000001")
        return value

    @field_validator("source_file_sha256", "input_file_sha256", "pixel_sha256", "task_key")
    @classmethod
    def _digests(cls, value: str, info) -> str:
        return _check_sha(value, info.field_name)

    @field_validator("input_path")
    @classmethod
    def _input_path(cls, value: str) -> str:
        _check_relative(value, "input_path")
        if not value.startswith("inputs/") or not value.endswith(".ome.tif") or value != value.lower():
            raise ValueError("input_path must be inputs/<lowercase name>.ome.tif")
        return value

    @field_validator("shape_czyx")
    @classmethod
    def _shape(cls, value: list[int]) -> list[int]:
        if len(value) != 4 or any(int(item) < 1 for item in value):
            raise ValueError("shape_czyx must be four positive integers (channels, z, y, x)")
        return [int(item) for item in value]

    @field_validator("file_spacing_xyz_um", "effective_spacing_xyz_um")
    @classmethod
    def _spacing(cls, value: list[float | None]) -> list[float | None]:
        if len(value) != 3:
            raise ValueError("spacing must have three entries (x, y, z), each a positive number or null")
        for item in value:
            if item is not None and not float(item) > 0:
                raise ValueError("spacing values must be positive or null")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Acquisition:
        channels = self.shape_czyx[0]
        if len(self.channel_names) != channels:
            raise ValueError("channel_names must have one name per channel")
        if self.channel_colors and len(self.channel_colors) != channels:
            raise ValueError("channel_colors must be empty or have one entry per channel")
        if self.effective_z_mode == "single_plane":
            if self.effective_z_index is None or not 0 <= self.effective_z_index < self.shape_czyx[1]:
                raise ValueError("single_plane needs an effective_z_index inside the stack")
        elif self.effective_z_index is not None:
            raise ValueError("effective_z_index is only used with single_plane")
        return self


class BundleManifest(HpcModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    kind: Literal["cellquant_v2_hpc"] = BUNDLE_KIND
    bundle_id: str
    package_name: str
    created_at: str
    cellquant_version: str
    application_build_sha256: Sha256
    source_experiment_id: str
    source_experiment_name: str
    recipe_path: Literal["recipe.yaml"] = "recipe.yaml"
    recipe_scientific_sha256: Sha256
    runtime_path: Literal["runtime.json"] = "runtime.json"
    runtime_sha256: Sha256
    cluster_path: Literal["cluster.json"] = "cluster.json"
    channel_layout: list[str]
    channel_layout_confirmed: bool = False
    notes: list[str] = Field(default_factory=list)
    acquisitions: list[Acquisition] = Field(min_length=1)

    @field_validator("bundle_id")
    @classmethod
    def _bundle_id(cls, value: str) -> str:
        if not re.match(r"^[0-9a-f]{32}$", value):
            raise ValueError("bundle_id must be 32 lowercase hex characters")
        return value

    @field_validator("package_name")
    @classmethod
    def _package_name(cls, value: str) -> str:
        if not re.match(r"^cq_hpc_[0-9a-f]{8}$", value):
            raise ValueError("package_name must look like cq_hpc_1a2b3c4d")
        return value

    @field_validator("recipe_scientific_sha256", "runtime_sha256", "application_build_sha256")
    @classmethod
    def _digests(cls, value: str, info) -> str:
        return _check_sha(value, info.field_name)

    @model_validator(mode="after")
    def _unique(self) -> BundleManifest:
        for field_name in ("acquisition_id", "input_path", "task_key"):
            values = [getattr(item, field_name) for item in self.acquisitions]
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate {field_name} in acquisitions")
        identities = [(item.source_image_id, item.source_relative_path, item.source_position) for item in self.acquisitions]
        if len(identities) != len(set(identities)):
            raise ValueError("the same source acquisition is listed twice")
        for item in self.acquisitions:
            if len(item.channel_names) != len(self.channel_layout):
                raise ValueError(f"{item.acquisition_id} has a different number of channels from the package")
        return self


class RuntimeContract(HpcModel):
    """The exact cluster software a package is prepared for, exported by ``runtime-inspect``."""

    schema_version: Literal[1] = SCHEMA_VERSION
    runtime_id: str
    python_version: str
    application_build_sha256: Sha256
    dependency_lock_sha256: Sha256
    packages: dict[str, str]
    engine: Engine
    cellpose_version: str
    model: str
    model_files: dict[str, Sha256]
    model_directory: str
    supported_modes: list[ZMode] = Field(default_factory=list)
    validation_notes: dict[str, str] = Field(default_factory=dict)
    runtime_validation_date: str | None = None
    observed: dict[str, Any] = Field(default_factory=dict)

    @field_validator("runtime_id")
    @classmethod
    def _runtime_id(cls, value: str) -> str:
        if not _NAME.match(value):
            raise ValueError("runtime_id may use letters, digits, '.', '_' and '-'")
        return value

    @field_validator("application_build_sha256", "dependency_lock_sha256")
    @classmethod
    def _digests(cls, value: str, info) -> str:
        return _check_sha(value, info.field_name)

    @field_validator("model_files")
    @classmethod
    def _model_files(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            raise ValueError("model_files must list the model's weight files and their SHA-256")
        for name, digest in value.items():
            if "/" in name or "\\" in name or name in ("", ".", ".."):
                raise ValueError("model_files keys are file names inside model_directory")
            _check_sha(digest, f"model_files[{name}]")
        return value

    @field_validator("model_directory")
    @classmethod
    def _model_directory(cls, value: str) -> str:
        return _check_posix_absolute(value, "model_directory")

    @field_validator("python_version")
    @classmethod
    def _python(cls, value: str) -> str:
        parts = value.split(".")
        if len(parts) < 2 or (int(parts[0]), int(parts[1])) < (3, 11):
            raise ValueError("the cluster environment must use Python 3.11 or newer")
        return value


class ClusterProfile(HpcModel):
    """Where and with what resources a package runs. Structured fields only: no shell snippets."""

    schema_version: Literal[1] = SCHEMA_VERSION
    profile_id: str
    display_name: str = ""
    scheduler: Literal["slurm"] = "slurm"
    host: str
    account: str
    partition: str
    qos: str | None = None
    gpu_resource: str
    gpus: Literal[1] = 1
    cpus: int = Field(ge=1, le=256)
    memory_mib: int = Field(ge=512)
    walltime_seconds: int = Field(ge=60, le=14 * 24 * 3600)
    remote_durable_root: str
    scratch_root: str
    python_path: str
    modules: list[str] = Field(default_factory=list)
    runtime_id: str
    runtime_contract_path: str
    runtime_sha256: Sha256
    validation_status: ProfileStatus = "draft"
    profile_verified_date: str | None = None
    live_smoke_date: str | None = None
    notes: list[str] = Field(default_factory=list)

    @field_validator("profile_id", "account", "partition", "runtime_id")
    @classmethod
    def _names(cls, value: str, info) -> str:
        if not _NAME.match(value):
            raise ValueError(f"{info.field_name} may use letters, digits, '.', '_' and '-' only")
        return value

    @field_validator("qos")
    @classmethod
    def _qos(cls, value: str | None) -> str | None:
        if value in (None, ""):
            return None
        if not _NAME.match(value):
            raise ValueError("qos may use letters, digits, '.', '_' and '-' only")
        return value

    @field_validator("gpu_resource")
    @classmethod
    def _gres(cls, value: str) -> str:
        if not _GRES.match(value):
            raise ValueError("gpu_resource must be a Slurm GRES such as gpu:h200:1")
        if not value.startswith("gpu"):
            raise ValueError("gpu_resource must request a GPU (gpu:...)")
        if value.rsplit(":", 1)[-1] != "1":
            raise ValueError("gpu_resource must request exactly one GPU (ending in :1)")
        return value

    @field_validator("host")
    @classmethod
    def _host(cls, value: str) -> str:
        if not _HOST.match(value):
            raise ValueError("host must be a host name such as login.rc.colorado.edu")
        return value

    @field_validator("remote_durable_root", "scratch_root", "python_path")
    @classmethod
    def _absolute(cls, value: str, info) -> str:
        return _check_posix_absolute(value, info.field_name)

    @field_validator("modules")
    @classmethod
    def _modules(cls, value: list[str]) -> list[str]:
        for item in value:
            if not _MODULE.match(item):
                raise ValueError(f"module name '{item}' is not allowed; use names like cuda/12.8")
        return value

    @field_validator("runtime_sha256")
    @classmethod
    def _runtime_sha(cls, value: str) -> str:
        return _check_sha(value, "runtime_sha256")

    @field_validator("runtime_contract_path")
    @classmethod
    def _contract_path(cls, value: str) -> str:
        if not value or "\0" in value or "\n" in value:
            raise ValueError("runtime_contract_path must be a file path")
        return value

    def unresolved_fields(self) -> list[str]:
        """Fields still holding a placeholder (CHANGE_ME, YOUR_..., <...>, USER)."""

        names = ["host", "account", "partition", "qos", "gpu_resource", "remote_durable_root", "scratch_root", "python_path"]
        return [name for name in names if isinstance(getattr(self, name), str) and PLACEHOLDER.search(getattr(self, name))]


# --- worker records ---------------------------------------------------------------------------------------


class ArtifactEntry(HpcModel):
    path: str
    bytes: int = Field(ge=0)
    sha256: Sha256

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        return _check_relative(value, "artifact path")

    @field_validator("sha256")
    @classmethod
    def _sha(cls, value: str) -> str:
        return _check_sha(value, "sha256")


class ErrorDetail(HpcModel):
    code: str
    message: str
    traceback: str = ""


class TaskResult(HpcModel):
    """The record of one acquisition's attempt. For a success it is written last, after its artifacts."""

    schema_version: Literal[1] = SCHEMA_VERSION
    bundle_id: str
    run_id: str
    acquisition_id: str
    task_key: Sha256
    attempt_id: str
    outcome: Literal["succeeded", "failed", "cancelled"]
    started_at: str
    ended_at: str
    runtime_fingerprint: Sha256
    runtime_sha256: Sha256
    device: str = ""
    artifacts: list[ArtifactEntry] = Field(default_factory=list)
    qc: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    error: ErrorDetail | None = None


class AcquisitionState(HpcModel):
    acquisition_id: str
    state: TaskState = "pending"
    attempt_id: str | None = None
    commit_path: str | None = None
    commit_sha256: Sha256 | None = None
    message: str = ""


class AttemptRecord(HpcModel):
    attempt_id: str
    started_at: str
    ended_at: str | None = None
    job_id: str | None = None
    host: str = ""
    device: str = ""
    compute_outcome: ComputeOutcome | None = None
    publication_outcome: PublicationOutcome | None = None
    notes: list[str] = Field(default_factory=list)


class ResultIndex(HpcModel):
    """Every planned acquisition and its state. ``results.json`` in the durable run folder."""

    schema_version: Literal[1] = SCHEMA_VERSION
    bundle_id: str
    ready_digest: Sha256
    run_id: str
    created_at: str
    updated_at: str
    compute_outcome: ComputeOutcome = "not_started"
    publication_outcome: PublicationOutcome = "not_published"
    finalized: bool = False
    acquisitions: list[AcquisitionState]
    attempts: list[AttemptRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> ResultIndex:
        ids = [item.acquisition_id for item in self.acquisitions]
        if len(ids) != len(set(ids)):
            raise ValueError("an acquisition appears twice in the result index")
        return self


class RunAllocation(HpcModel):
    """``run.json``: made by submit.sh before the job is queued."""

    schema_version: Literal[1] = SCHEMA_VERSION
    run_id: str
    bundle_id: str
    ready_digest: Sha256
    package_name: str
    created_at: str

    @field_validator("run_id")
    @classmethod
    def _run_id(cls, value: str) -> str:
        if not _ID.match(value):
            raise ValueError("run_id may use letters, digits, '_' and '-'")
        return value


class AttemptInfo(HpcModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    attempt_id: str
    run_id: str
    created_at: str
    resume: bool = False
    job_id: str | None = None
    cluster: str | None = None


class LeaseRecord(HpcModel):
    """Who owns a run. Only one worker at a time may publish into a run folder."""

    schema_version: Literal[1] = SCHEMA_VERSION
    lease_token: str
    ready_digest: Sha256
    run_id: str
    attempt_id: str
    cluster: str = ""
    job_id: str = ""
    host: str = ""
    pid: int = 0
    acquired_at: str
    submitted_at: str | None = None


# --- reports -----------------------------------------------------------------------------------------------


class Issue(HpcModel):
    code: str
    message: str
    acquisition_id: str | None = None
    path: str | None = None
    fix: str | None = None


class ValidationReport(HpcModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    ok: bool
    bundle_id: str | None = None
    checked_at: str
    checksums_sha256: Sha256 | None = None
    errors: list[Issue] = Field(default_factory=list)
    warnings: list[Issue] = Field(default_factory=list)


class PreflightReport(HpcModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    ok: bool
    checked_at: str
    host: str = ""
    job_id: str = ""
    bundle_id: str | None = None
    runtime_id: str | None = None
    runtime_fingerprint: str | None = None
    gpu: dict[str, Any] = Field(default_factory=dict)
    inference: dict[str, Any] = Field(default_factory=dict)
    errors: list[Issue] = Field(default_factory=list)


class ImportedAcquisition(HpcModel):
    acquisition_id: str
    state: TaskState
    imported_result: bool
    image_id: str
    source_image_id: str
    source_relative_path: str
    sample_name: str
    attempt_id: str | None = None


class ImportRecord(HpcModel):
    """``hpc_import.json`` in an imported experiment: where it came from and what was imported."""

    schema_version: Literal[1] = SCHEMA_VERSION
    bundle_id: str
    package_name: str
    ready_digest: Sha256
    run_id: str
    results_index_sha256: Sha256
    imported_at: str
    experiment_id: str
    source_experiment_id: str
    source_experiment_name: str
    runtime_id: str
    runtime_sha256: Sha256
    compute_outcome: ComputeOutcome
    publication_outcome: PublicationOutcome
    partial: bool
    acquisitions: list[ImportedAcquisition]


class LocalSidecar(HpcModel):
    """Beside a prepared package, never inside it: the Windows source files, for the user's reference."""

    schema_version: Literal[1] = SCHEMA_VERSION
    bundle_id: str
    package_name: str
    created_at: str
    source_experiment_dir: str
    acquisitions: dict[str, dict[str, Any]]


MODELS: dict[str, type[HpcModel]] = {
    "bundle": BundleManifest,
    "acquisition": Acquisition,
    "runtime": RuntimeContract,
    "cluster_profile": ClusterProfile,
    "task_result": TaskResult,
    "result_index": ResultIndex,
    "run_allocation": RunAllocation,
    "attempt": AttemptInfo,
    "lease": LeaseRecord,
    "validation_report": ValidationReport,
    "preflight_report": PreflightReport,
    "import_record": ImportRecord,
    "local_sidecar": LocalSidecar,
}


class SchemaVersionError(ValueError):
    pass


def load_model(model: type[HpcModel], path: str | Path | None = None, *, data: Any = None) -> HpcModel:
    """Read a record, refusing unknown schema versions with a clear message."""

    if data is None:
        data = json.loads(Path(path).read_text(encoding="utf-8"))  # type: ignore[arg-type]
    if isinstance(data, dict) and "schema_version" in data and data["schema_version"] != SCHEMA_VERSION:
        raise SchemaVersionError(
            f"{Path(path).name if path else model.__name__} uses schema version {data['schema_version']}; "
            f"this CellQuant reads version {SCHEMA_VERSION}. Use the CellQuant release that wrote it."
        )
    if isinstance(data, dict) and data.get("kind") not in (None, BUNDLE_KIND):
        raise SchemaVersionError(
            "This is not a CellQuant V2 HPC package (kind "
            f"'{data.get('kind')}'). Packages made by the older CellQuant pipeline cannot be read; prepare a new package."
        )
    return model.model_validate(data)


def write_schemas(directory: str | Path) -> list[Path]:
    """Write one JSON schema per record type."""

    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for name, model in MODELS.items():
        path = target / f"{name}.schema.json"
        schema = model.model_json_schema()
        schema["$id"] = f"https://cellquant.local/hpc/v{SCHEMA_VERSION}/{name}.schema.json"
        path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        written.append(path)
    return written
