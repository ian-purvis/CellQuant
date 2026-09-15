"""Local validation of HPC bundles before transfer/submit."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cellquant.hpc.contract import BUNDLE_NAME, CHECKSUMS_NAME, READY_NAME, matrix_status


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    bundle: dict[str, Any] | None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_bundle(bundle_dir: str | Path, *, require_ready: bool = False) -> ValidationResult:
    """Validate manifests, checksums, and supported matrix membership."""

    root = Path(bundle_dir)
    errors: list[str] = []
    warnings: list[str] = []
    bundle_payload: dict[str, Any] | None = None

    if not root.is_dir():
        return ValidationResult(False, (f"bundle directory not found: {root}",), (), None)

    bundle_path = root / BUNDLE_NAME
    checksum_path = root / CHECKSUMS_NAME
    ready_path = root / READY_NAME

    if not bundle_path.is_file():
        errors.append("bundle.json missing")
    else:
        try:
            bundle_payload = json.loads(bundle_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"bundle.json invalid JSON: {exc}")

    if not checksum_path.is_file():
        errors.append("checksums.json missing")
    else:
        try:
            checksum_payload = json.loads(checksum_path.read_text(encoding="utf-8"))
            files = checksum_payload.get("files", {})
            if not isinstance(files, dict) or not files:
                errors.append("checksums.json has no files")
            else:
                for rel, expected in files.items():
                    path = root / str(rel)
                    if not path.is_file():
                        errors.append(f"missing checksummed file: {rel}")
                        continue
                    actual = _sha256(path)
                    if actual != expected:
                        errors.append(f"checksum mismatch: {rel}")
        except json.JSONDecodeError as exc:
            errors.append(f"checksums.json invalid JSON: {exc}")

    if require_ready and not ready_path.is_file():
        errors.append("READY.json missing — package is incomplete and cannot be submitted")
    elif not ready_path.is_file():
        warnings.append("READY.json missing — package is not marked ready")

    if bundle_payload is not None:
        engine = str(bundle_payload.get("engine", ""))
        mode = str(bundle_payload.get("mode", ""))
        profile_id = str(bundle_payload.get("profile_id", ""))
        status, reason = matrix_status(engine, mode, profile_id)
        if status != "enabled":
            errors.append(reason or "unsupported engine/mode/profile")
        if not bundle_payload.get("cluster_smoke_verified"):
            warnings.append(
                "profile has no recorded Alpine smoke verification; "
                "do not treat local validation as cluster readiness"
            )
        if bundle_payload.get("submit_ready") is False:
            warnings.append(
                "profile is not submit-ready (Requires validation); "
                "submit.sh will refuse until environment/parity gates pass"
            )
        acquisitions = bundle_payload.get("acquisitions") or []
        for row in acquisitions:
            export_rel = row.get("export_relative")
            config_rel = row.get("config_relative")
            if export_rel and not (root / export_rel).is_file():
                errors.append(f"missing export: {export_rel}")
            if config_rel and not (root / config_rel).is_file():
                errors.append(f"missing config: {config_rel}")

    for script in (
        "scripts/validate_bundle.py",
        "scripts/prepare_submission.sh",
        "scripts/submit.sh",
        "scripts/run.sbatch",
        "scripts/run_jobcomposer.sbatch",
        "scripts/setup_environment.sh",
    ):
        path = root / script
        if not path.is_file():
            errors.append(f"missing {script}")
        else:
            data = path.read_bytes()
            if b"\r" in data:
                errors.append(f"{script} contains CR characters; scripts must use LF endings")
    if not (root / "SUBMIT_THROUGH_OPEN_ONDEMAND.md").is_file():
        warnings.append(
            "SUBMIT_THROUGH_OPEN_ONDEMAND.md missing — Open OnDemand Job Composer guide not packaged"
        )
    if (root / "scripts" / "run_jobcomposer.sbatch").is_file():
        composer = (root / "scripts" / "run_jobcomposer.sbatch").read_text(encoding="utf-8")
        if "submit.sh" in composer and "Do not paste" not in composer:
            warnings.append(
                "run_jobcomposer.sbatch should not be a submit.sh wrapper payload"
            )
        if "BUNDLE_DIR=" not in composer:
            errors.append("run_jobcomposer.sbatch missing explicit BUNDLE_DIR assignment")

    return ValidationResult(
        ok=not errors,
        errors=tuple(errors),
        warnings=tuple(warnings),
        bundle=bundle_payload,
    )


__all__ = ["ValidationResult", "validate_bundle"]
