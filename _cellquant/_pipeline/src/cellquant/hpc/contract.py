"""Portable HPC bundle schema constants and supported matrix."""

from __future__ import annotations

from typing import Mapping

BUNDLE_SCHEMA_VERSION = 1
BUNDLE_KIND = "cellquant_hpc"
READY_NAME = "READY.json"
BUNDLE_NAME = "bundle.json"
CHECKSUMS_NAME = "checksums.json"
EXPORT_REPORT_NAME = "export_report.json"
README_NAME = "README_SUBMIT.md"

# Canonical transport axes for exported acquisitions (matches ImageVolume).
EXPORT_AXES = "ZYXC"
LABEL_AXES = "ZYX"

# Short collision-safe input names mapped in the manifest.
ACQUISITION_NAME_FMT = "a{index:06d}"

# Documentation-informed default (CURC §12, 2026-09-11): full H200.
DEFAULT_PROFILE_ID = "alpine_ah200"

# UI presentation order for GPU profile choices.
PROFILE_UI_ORDER: tuple[str, ...] = (
    "alpine_ah200",
    "alpine_artxpro6000",
    "alpine_aa100",
    "alpine_al40",
)

_V4_MODES = (
    "volume_3d",
    "stitch_2d",
    "single_plane_2d",
    "max_projection_2d",
)

# First-release matrix. Unsupported combinations must fail closed with a reason.
# Status values: "enabled" | "unsupported"
# RTX Pro 6000 is matrix-enabled for package prep and Alpine comparative trials.
# Prefer H200 until a matched smoke + parity result is recorded on the profile.
SUPPORTED_MATRIX: tuple[Mapping[str, str], ...] = tuple(
    {
        "engine": "v4",
        "mode": mode,
        "profile_id": profile_id,
        "status": "enabled",
        "reason": "",
    }
    for profile_id in PROFILE_UI_ORDER
    for mode in _V4_MODES
) + (
    {
        "engine": "v3",
        "mode": "*",
        "profile_id": "*",
        "status": "unsupported",
        "reason": "v3 engine/mode combinations require parity tests against the shared core before HPC enablement",
    },
)


def matrix_status(engine: str, mode: str, profile_id: str) -> tuple[str, str]:
    """Return ``(status, reason)`` for an engine/mode/profile combination."""

    for row in SUPPORTED_MATRIX:
        engine_ok = row["engine"] == engine or row["engine"] == "*"
        mode_ok = row["mode"] == mode or row["mode"] == "*"
        profile_ok = row["profile_id"] == profile_id or row["profile_id"] == "*"
        if engine_ok and mode_ok and profile_ok and row["status"] == "enabled":
            return "enabled", ""
    for row in SUPPORTED_MATRIX:
        engine_ok = row["engine"] == engine or row["engine"] == "*"
        mode_ok = row["mode"] == mode or row["mode"] == "*"
        profile_ok = row["profile_id"] == profile_id or row["profile_id"] == "*"
        if engine_ok and mode_ok and profile_ok and row["status"] == "unsupported":
            return "unsupported", str(row["reason"])
    return (
        "unsupported",
        f"combination engine={engine!r} mode={mode!r} profile={profile_id!r} "
        "is not in the published HPC supported matrix",
    )


def require_supported(engine: str, mode: str, profile_id: str) -> None:
    status, reason = matrix_status(engine, mode, profile_id)
    if status != "enabled":
        raise ValueError(reason or "unsupported HPC combination")
