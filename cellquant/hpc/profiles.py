"""Cluster profiles and their runtime contracts.

A profile is a JSON file (``ClusterProfile``). Its ``runtime_contract_path``
is resolved against the profile file's own folder when relative, never the
current working folder. Preparation embeds the contract's exact bytes as
``runtime.json`` and rewrites the bundled profile to point at it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from cellquant.hpc.common import sha256_bytes
from cellquant.hpc.models import ClusterProfile, Issue, RuntimeContract, load_model


@dataclass
class ResolvedProfile:
    profile: ClusterProfile
    path: Path | None
    runtime: RuntimeContract | None
    runtime_bytes: bytes | None
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)

    @property
    def submission_ready(self) -> bool:
        return not self.errors and self.runtime is not None

    @property
    def status_text(self) -> str:
        if self.errors:
            return "Draft: " + "; ".join(issue.message for issue in self.errors[:2])
        if self.profile.validation_status == "production" and self.profile.live_smoke_date:
            return f"Production-verified (cluster smoke test {self.profile.live_smoke_date})."
        return "Experimental: no recorded smoke test on the cluster for this profile yet."


def examples_dir() -> Path:
    return Path(__file__).resolve().parent / "profiles"


def load_profile(path: str | Path) -> ResolvedProfile:
    source = Path(path)
    profile = load_model(ClusterProfile, source)
    return resolve_profile(profile, source)  # type: ignore[arg-type]


def resolve_profile(profile: ClusterProfile, path: Path | None) -> ResolvedProfile:
    """Find and check the runtime contract; list what keeps the profile from making a READY package."""

    errors: list[Issue] = []
    warnings: list[Issue] = []
    runtime = None
    runtime_bytes = None
    contract_path = Path(profile.runtime_contract_path)
    if not contract_path.is_absolute():
        if path is None:
            errors.append(Issue(code="E_PROFILE", message="The runtime contract path is relative, but the profile has no file location."))
            contract_path = None  # type: ignore[assignment]
        else:
            contract_path = path.resolve().parent / contract_path
    if contract_path is not None:
        if not contract_path.is_file():
            errors.append(
                Issue(
                    code="E_RUNTIME_MISSING",
                    message=f"The runtime contract {contract_path} was not found.",
                    fix="Ask the maintainer for the runtime.json made with 'python -m cellquant.hpc runtime-inspect' and put it beside the profile.",
                )
            )
        else:
            runtime_bytes = contract_path.read_bytes()
            try:
                runtime = load_model(RuntimeContract, data=json.loads(runtime_bytes))  # type: ignore[assignment]
            except Exception as exc:  # noqa: BLE001
                errors.append(Issue(code="E_RUNTIME_INVALID", message=f"The runtime contract could not be read: {exc}"))
                runtime_bytes = None
    if runtime is not None and runtime_bytes is not None:
        if runtime.runtime_id != profile.runtime_id:
            errors.append(
                Issue(
                    code="E_RUNTIME_MISMATCH",
                    message=f"The profile names runtime '{profile.runtime_id}', but the contract is '{runtime.runtime_id}'.",
                )
            )
        if sha256_bytes(runtime_bytes) != profile.runtime_sha256:
            errors.append(
                Issue(
                    code="E_RUNTIME_MISMATCH",
                    message="The runtime contract's SHA-256 does not match the profile's runtime_sha256.",
                    fix="Use the runtime.json the profile was made for, or update runtime_sha256 after checking the new contract.",
                )
            )
        if not runtime.runtime_validation_date:
            errors.append(
                Issue(
                    code="E_RUNTIME_UNVERIFIED",
                    message=f"Runtime '{runtime.runtime_id}' has no validation date, so it is still a draft.",
                    fix="The maintainer records runtime_validation_date after the preflight and parity checks pass.",
                )
            )
        if not runtime.supported_modes:
            errors.append(Issue(code="E_MODE", message=f"Runtime '{runtime.runtime_id}' has no Z modes enabled yet."))
    unresolved = profile.unresolved_fields()
    if unresolved:
        errors.append(
            Issue(
                code="E_PROFILE_PLACEHOLDER",
                message="Fill in the profile fields that still hold placeholders: " + ", ".join(unresolved) + ".",
                fix="Enter your Slurm account and your own project and scratch folders.",
            )
        )
    if not profile.profile_verified_date:
        warnings.append(
            Issue(
                code="W_PROFILE_UNVERIFIED",
                message="The profile's partition, GPU request and limits have not been checked against current cluster documentation.",
                fix="Check them against the cluster's current documentation and record profile_verified_date.",
            )
        )
    if not profile.live_smoke_date:
        warnings.append(
            Issue(code="W_NO_SMOKE", message="No cluster smoke test is recorded for this profile; treat it as experimental.")
        )
    return ResolvedProfile(profile, path, runtime, runtime_bytes, errors, warnings)


def bundled_profile(profile: ClusterProfile) -> ClusterProfile:
    """The copy stored in a package: it points at the embedded runtime.json and nothing on this computer."""

    return profile.model_copy(update={"runtime_contract_path": "runtime.json"})


def save_profile(profile: ClusterProfile, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(profile.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return target
