"""Typed Alpine cluster profiles and target capability resolution."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Mapping, Sequence

from cellquant.hpc.contract import (
    DEFAULT_PROFILE_ID,
    PROFILE_UI_ORDER,
    SUPPORTED_MATRIX,
    matrix_status,
)


@dataclass(frozen=True)
class AlpineProfile:
    """Administrator-maintained cluster profile snapshot."""

    profile_id: str
    display_name: str
    cluster: str
    partition: str
    qos_choices: tuple[str, ...]
    default_qos: str
    gres_choices: tuple[str, ...]
    default_gres: str
    cpus_per_task: int
    mem: str
    default_walltime: str
    max_walltime_by_qos: Mapping[str, str]
    modules: tuple[str, ...]
    scratch_root_template: str
    project_root_hint: str
    require_account: bool
    require_gpu: bool
    allow_cpu_fallback: bool
    backend: str
    cellquant_version_pin: str
    environment_spec: Mapping[str, Any]
    documentation: Mapping[str, Any]
    raw: Mapping[str, Any]
    role: str = "alternative"
    submit_ready: bool = True
    architecture: str = ""
    vram_gb: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(dict(self.raw))

    @property
    def documentation_checked_utc(self) -> str | None:
        docs = self.documentation
        return docs.get("documentation_checked_utc") or docs.get("validated_utc")

    @property
    def smoke_verified_utc(self) -> str | None:
        return self.documentation.get("smoke_verified_utc")


@dataclass(frozen=True)
class SupportedMatrix:
    rows: tuple[Mapping[str, str], ...]

    def status(self, engine: str, mode: str, profile_id: str) -> tuple[str, str]:
        return matrix_status(engine, mode, profile_id)


@dataclass(frozen=True)
class TargetCapabilities:
    """Segmentation capabilities implied by the *cluster* profile, not the laptop."""

    profile_id: str
    engines: tuple[str, ...]
    modes: tuple[str, ...]
    devices: tuple[str, ...]
    require_gpu: bool
    allow_cpu_fallback: bool
    summary: str


def packaged_profiles_dir() -> Path:
    """Return the packaged profiles directory when present on disk."""

    return Path(__file__).resolve().parent / "profiles"


def list_profile_ids() -> tuple[str, ...]:
    root = packaged_profiles_dir()
    if not root.is_dir():
        return ()
    found = {path.stem for path in root.glob("*.json")}
    ordered = [pid for pid in PROFILE_UI_ORDER if pid in found]
    ordered.extend(sorted(found - set(PROFILE_UI_ORDER)))
    return tuple(ordered)


def load_profile(profile_id: str | Path | None = None) -> AlpineProfile:
    """Load a packaged or filesystem Alpine profile (default: recommended H200)."""

    if profile_id is None:
        profile_id = DEFAULT_PROFILE_ID
    path = Path(profile_id)
    if path.suffix.lower() == ".json" and path.is_file():
        raw = json.loads(path.read_text(encoding="utf-8"))
    else:
        candidate = packaged_profiles_dir() / f"{profile_id}.json"
        if not candidate.is_file():
            # Fallback: importlib.resources for installed wheels
            try:
                package = resources.files("cellquant.hpc.profiles")
                candidate_res = package.joinpath(f"{profile_id}.json")
                raw = json.loads(candidate_res.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                raise FileNotFoundError(f"HPC profile not found: {profile_id}") from exc
        else:
            raw = json.loads(candidate.read_text(encoding="utf-8"))
    return _parse_profile(raw)


def _parse_profile(raw: Mapping[str, Any]) -> AlpineProfile:
    required = {
        "profile_id",
        "display_name",
        "cluster",
        "partition",
        "qos_choices",
        "default_qos",
        "gres_choices",
        "default_gres",
        "cpus_per_task",
        "mem",
        "default_walltime",
        "modules",
        "scratch_root_template",
        "require_gpu",
        "backend",
    }
    missing = required - set(raw)
    if missing:
        raise ValueError(f"profile missing fields: {sorted(missing)}")
    qos_choices = tuple(str(v) for v in raw["qos_choices"])
    gres_choices = tuple(str(v) for v in raw["gres_choices"])
    if raw["default_qos"] not in qos_choices:
        raise ValueError("default_qos must be one of qos_choices")
    if raw["default_gres"] not in gres_choices:
        raise ValueError("default_gres must be one of gres_choices")
    # H200 / RTX Pro 6000 must never offer gpu-testing (CURC §12).
    if str(raw["partition"]) in {"ah200", "artxpro6000"} and "gpu-testing" in qos_choices:
        raise ValueError(
            f"profile {raw['profile_id']!r}: partition {raw['partition']} "
            "must not include qos gpu-testing"
        )
    # Full-GPU profiles must not silently offer MIG GRES in the same profile.
    if any("mig" in gres.casefold() or "_3g." in gres for gres in gres_choices):
        raise ValueError(
            f"profile {raw['profile_id']!r}: MIG GRES must live in a separate advanced profile"
        )
    vram = raw.get("vram_gb")
    return AlpineProfile(
        profile_id=str(raw["profile_id"]),
        display_name=str(raw["display_name"]),
        cluster=str(raw["cluster"]),
        partition=str(raw["partition"]),
        qos_choices=qos_choices,
        default_qos=str(raw["default_qos"]),
        gres_choices=gres_choices,
        default_gres=str(raw["default_gres"]),
        cpus_per_task=int(raw["cpus_per_task"]),
        mem=str(raw["mem"]),
        default_walltime=str(raw["default_walltime"]),
        max_walltime_by_qos={
            str(k): str(v) for k, v in dict(raw.get("max_walltime_by_qos", {})).items()
        },
        modules=tuple(str(v) for v in raw["modules"]),
        scratch_root_template=str(raw["scratch_root_template"]),
        project_root_hint=str(raw.get("project_root_hint", "")),
        require_account=bool(raw.get("require_account", False)),
        require_gpu=bool(raw["require_gpu"]),
        allow_cpu_fallback=bool(raw.get("allow_cpu_fallback", False)),
        backend=str(raw["backend"]),
        cellquant_version_pin=str(raw.get("cellquant_version_pin", "")),
        environment_spec=dict(raw.get("environment_spec", {})),
        documentation=dict(raw.get("documentation", {})),
        raw=dict(raw),
        role=str(raw.get("role", "alternative")),
        submit_ready=bool(raw.get("submit_ready", True)),
        architecture=str(raw.get("architecture", "")),
        vram_gb=int(vram) if vram is not None else None,
    )


def resolve_capabilities(profile: AlpineProfile) -> TargetCapabilities:
    """Derive UI capability lists from the target profile and supported matrix."""

    engines: list[str] = []
    modes: list[str] = []
    for row in SUPPORTED_MATRIX:
        if row["status"] != "enabled":
            continue
        if row["profile_id"] not in {profile.profile_id, "*"}:
            continue
        if row["engine"] != "*" and row["engine"] not in engines:
            engines.append(row["engine"])
        if row["mode"] != "*" and row["mode"] not in modes:
            modes.append(row["mode"])
    devices = ("cuda",) if profile.require_gpu else ("cuda", "cpu", "auto")
    smoke = profile.smoke_verified_utc
    docs = profile.documentation_checked_utc
    smoke_note = (
        f"Cluster smoke verified {smoke}."
        if smoke
        else "Cluster smoke test not yet recorded — packages are locally preparable only."
    )
    ready_note = (
        "Submit-ready."
        if profile.submit_ready
        else "NOT submit-ready — Requires validation (environment/parity)."
    )
    vram_note = f" VRAM {profile.vram_gb} GB." if profile.vram_gb else ""
    summary = (
        f"Target {profile.display_name} ({profile.partition}/{profile.default_qos}, "
        f"{profile.default_gres}).{vram_note} Docs checked {docs or 'unknown'}. "
        f"{ready_note} Engines: {', '.join(engines) or 'none'}. {smoke_note}"
    )
    return TargetCapabilities(
        profile_id=profile.profile_id,
        engines=tuple(engines),
        modes=tuple(modes),
        devices=tuple(devices),
        require_gpu=profile.require_gpu,
        allow_cpu_fallback=profile.allow_cpu_fallback,
        summary=summary,
    )


def validate_user_profile_fields(
    profile: AlpineProfile,
    *,
    account: str | None,
    qos: str,
    gres: str,
    walltime: str,
    project_root: str,
    scratch_root: str,
    env_location: str,
    email: str | None = None,
) -> list[str]:
    """Return blocking validation errors for user-supplied profile fields."""

    errors: list[str] = []
    account_text = (account or "").strip()
    if profile.require_account and not account_text:
        errors.append(
            "allocation/account is required (Slurm --account, e.g. amc-general — "
            "not your login email/username)"
        )
    elif account_text:
        if "\n" in account_text or "\r" in account_text or " " in account_text:
            errors.append("account must be a single Slurm account name with no spaces")
        if "@" in account_text:
            errors.append(
                "account looks like an email/login; use your Slurm allocation "
                "(e.g. amc-general from: sacctmgr show associations user=$USER)"
            )
    if qos not in profile.qos_choices:
        errors.append(f"qos must be one of {list(profile.qos_choices)}")
    if gres not in profile.gres_choices:
        errors.append(f"gres must be one of {list(profile.gres_choices)}")
    if qos == "gpu-testing" and profile.partition in {"ah200", "artxpro6000"}:
        errors.append(
            f"{profile.partition} is not available under qos gpu-testing; use gpu-normal"
        )
    if not _valid_walltime(walltime):
        errors.append("walltime must look like HH:MM:SS with no line breaks")
    path_labels = {
        "project_root": "Project root (packages + results)",
        "scratch_root": "Scratch root (job temp workspace)",
        "env_location": "CellQuant env on Alpine",
    }
    for name, value in (
        ("project_root", project_root),
        ("scratch_root", scratch_root),
        ("env_location", env_location),
    ):
        label = path_labels[name]
        text = (value or "").strip()
        if not text:
            errors.append(f"{label} is required ({name})")
            continue
        if "\n" in text or "\r" in text:
            errors.append(f"{label} must not contain line breaks ({name})")
        if any(ch in text for ch in ("`", "$(")):
            errors.append(
                f"{label} contains disallowed shell metacharacter sequences ({name})"
            )
        if "\\" in text or re.match(r"^[A-Za-z]:/", text.replace("\\", "/")):
            errors.append(
                f"{label} must be an Alpine POSIX path, not a Windows path ({name})"
            )
        if "${USER}" in text or re.search(r"(?:^|/)USER(?:/|$)", text):
            errors.append(
                f"{name} still contains a USER placeholder — replace with your Alpine username"
            )
        if name == "env_location":
            normalized = text.replace("\\", "/")
            if "build/lib" in normalized or normalized.endswith("/cellquant") and "envs/" not in normalized:
                errors.append(
                    "CellQuant env on Alpine must be a conda/prefix env on the cluster "
                    "(example: /projects/<user>/cellquant/envs/cellquant-hpc), "
                    "not a local CellQuant source/build folder (env_location)"
                )
            if not normalized.startswith("/projects/") and not normalized.startswith("/scratch/"):
                errors.append(
                    "CellQuant env on Alpine should live under /projects/.../envs/... "
                    "(env_location)"
                )
    if email:
        if "\n" in email or "\r" in email or "@" not in email:
            errors.append("email must be a single-line address or empty")
    return errors


def _valid_walltime(value: str) -> bool:
    parts = str(value).strip().split(":")
    if len(parts) != 3:
        return False
    try:
        hours, minutes, seconds = (int(p) for p in parts)
    except ValueError:
        return False
    return hours >= 0 and 0 <= minutes < 60 and 0 <= seconds < 60


def target_runtime_capabilities(profile: AlpineProfile) -> "RuntimeCapabilities":
    """Build UI RuntimeCapabilities from the cluster profile (not the laptop)."""

    from cellquant.plugin.capabilities import (
        DeviceOption,
        EngineOption,
        RuntimeCapabilities,
    )

    caps = resolve_capabilities(profile)
    engines = []
    for engine_id in ("v4", "v3"):
        available = engine_id in caps.engines
        reason = None
        if not available:
            reason = (
                f"{engine_id} is not enabled for profile {profile.profile_id} "
                "in the published HPC supported matrix"
            )
        label = "Cellpose-SAM (v4)" if engine_id == "v4" else "Cellpose classic (v3)"
        engines.append(
            EngineOption(
                engine_id=engine_id,
                label=label,
                description=f"Target cluster engine ({profile.profile_id})",
                min_cellpose_major=4 if engine_id == "v4" else 3,
                max_cellpose_major=4 if engine_id == "v4" else 3,
                available=available,
                unavailable_reason=reason,
            )
        )
    devices = [
        DeviceOption("cuda", "GPU (CUDA) — cluster", True, None),
        DeviceOption(
            "cpu",
            "CPU",
            not profile.require_gpu,
            "CPU disabled: this Alpine profile requires a GPU" if profile.require_gpu else None,
        ),
        DeviceOption(
            "auto",
            "Auto",
            not profile.require_gpu,
            "Auto disabled: this Alpine profile requires CUDA" if profile.require_gpu else None,
        ),
    ]
    return RuntimeCapabilities(
        cellpose_version="cluster-target",
        cellpose_major=4,
        cellpose_error=None,
        torch_version="cluster-target",
        torch_cuda_build="cluster-target",
        cuda_available=True,
        cuda_detail=f"Target profile {profile.profile_id} requires GPU"
        if profile.require_gpu
        else None,
        system_gpu_detected=True,
        system_gpu_name=profile.default_gres,
        engines=tuple(engines),
        devices=tuple(devices),
        summary=caps.summary,
    )


__all__ = [
    "AlpineProfile",
    "SupportedMatrix",
    "TargetCapabilities",
    "list_profile_ids",
    "load_profile",
    "resolve_capabilities",
    "target_runtime_capabilities",
    "validate_user_profile_fields",
]
