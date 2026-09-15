"""Canonical TIFF export and HPC bundle assembly (no Cellpose load)."""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import tifffile

from cellquant import __version__ as CELLQUANT_VERSION
from cellquant.config import RunConfig
from cellquant.contracts import CancellationToken, MutableCancellationToken, PipelineEvent, null_event_sink
from cellquant.hpc.acquisitions import AcquisitionRef
from cellquant.hpc.contract import (
    ACQUISITION_NAME_FMT,
    BUNDLE_KIND,
    BUNDLE_NAME,
    BUNDLE_SCHEMA_VERSION,
    CHECKSUMS_NAME,
    EXPORT_AXES,
    EXPORT_REPORT_NAME,
    READY_NAME,
    README_NAME,
    require_supported,
)
from cellquant.hpc.cluster_profiles import AlpineProfile, validate_user_profile_fields
from cellquant.hpc.templates import (
    UserClusterSettings,
    generate_scripts,
    ood_guide_source_path,
    render_readme,
)
from cellquant.io import open_volume
from cellquant.persist.atomic import write_bytes_atomic, write_text_atomic


@dataclass(frozen=True)
class ExportResult:
    bundle_dir: Path
    bundle_id: str
    ready: bool
    acquisition_count: int
    incomplete_reason: str | None
    artifacts: Mapping[str, Path]


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _short_id() -> str:
    return uuid.uuid4().hex[:10]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def estimate_export_bytes(shape: Sequence[int], dtype_name: str) -> int | None:
    """Estimate payload size from metadata only (no pixel materialization)."""

    try:
        itemsize = int(np.dtype(dtype_name).itemsize)
    except Exception:
        return None
    if not shape or any(int(v) < 1 for v in shape):
        return None
    total = itemsize
    for dim in shape:
        total *= int(dim)
    return int(total)


def write_canonical_tiff(
    destination: Path,
    array: np.ndarray,
    *,
    spacing_um: tuple[float, float, float],
    channel_names: Sequence[str],
) -> None:
    """Write lossless multichannel OME-TIFF with explicit calibration metadata.

    In-memory CellQuant volumes are ZYXC. OME-TIFF transport stores ZCYX (the
    layout tifffile/OME accept reliably); :func:`cellquant.io.open_volume`
    normalizes back to ZYXC. Channel names and spacing are recorded in OME-XML.
    """

    data = np.asarray(array)
    if data.ndim != 4:
        raise ValueError(f"exported array must be ZYXC; received shape {data.shape}")
    if data.shape[-1] != len(channel_names):
        raise ValueError("channel_names must match C axis")
    destination.parent.mkdir(parents=True, exist_ok=True)
    from cellquant.persist.atomic import replace_with_retry

    z, y, x = (float(v) for v in spacing_um)
    # BigTIFF when payload may exceed classic TIFF limits (~4 GiB).
    nbytes = int(data.nbytes)
    bigtiff = nbytes >= (2 * 1024**3)
    # OME-TIFF: ZCYX matches existing CellQuant I/O fixtures and tifffile OME writer.
    ome_data = np.moveaxis(data, -1, 1)
    metadata = {
        "axes": "ZCYX",
        "PhysicalSizeZ": z,
        "PhysicalSizeZUnit": "um",
        "PhysicalSizeY": y,
        "PhysicalSizeYUnit": "um",
        "PhysicalSizeX": x,
        "PhysicalSizeXUnit": "um",
        "Channel": {"Name": list(channel_names)},
    }
    temporary = destination.with_name(f".{destination.stem}.{uuid.uuid4().hex}.tif")
    write_kwargs: dict[str, Any] = {
        "bigtiff": bigtiff,
        "ome": True,
        "photometric": "minisblack",
        "metadata": metadata,
        "compression": None,
    }
    try:
        tifffile.imwrite(temporary, ome_data, **write_kwargs)
        # Round-trip through CellQuant reader (canonical ZYXC).
        volume = open_volume(temporary, lazy=False)
        read = np.asarray(volume.data)
        if tuple(read.shape) != tuple(data.shape):
            raise OSError(
                f"TIFF round-trip shape mismatch: wrote {data.shape}, read {read.shape}"
            )
        if read.dtype != data.dtype:
            raise OSError(
                f"TIFF round-trip dtype mismatch: wrote {data.dtype}, read {read.dtype}"
            )
        if not np.array_equal(read, data):
            raise OSError("TIFF round-trip intensity mismatch")
        if tuple(volume.channel_names) != tuple(channel_names):
            raise OSError(
                f"channel name mismatch: wrote {list(channel_names)}, "
                f"read {list(volume.channel_names)}"
            )
        if any(
            abs(float(a) - float(b)) > 1e-6
            for a, b in zip(volume.spacing_um, spacing_um, strict=True)
        ):
            raise OSError(
                f"spacing mismatch: wrote {spacing_um}, read {volume.spacing_um}"
            )
        replace_with_retry(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _effective_config_for_export(
    template: RunConfig,
    acquisition: AcquisitionRef,
    *,
    export_relative: str,
) -> dict[str, Any]:
    """Adapt RunConfig I/O to the exported TIFF while preserving provenance."""

    raw = deepcopy(dict(template.raw))
    io_spec = dict(raw["io"])
    # Exported TIFF is already one acquisition; selection indices are provenance only.
    io_spec["series"] = 0
    io_spec["position"] = 0
    io_spec["lazy"] = True
    # Exported OME-TIFF stores ZCYX; open_volume normalizes to ZYXC. Do not
    # force axes_override=ZYXC on the on-disk ZCYX representation.
    io_spec["axes_override"] = None
    if acquisition.segment_channel is not None:
        raw["preprocess"] = {
            **dict(raw["preprocess"]),
            "channel": int(acquisition.segment_channel),
        }
    # Preserve measured vs override spacing provenance on the export config.
    if io_spec.get("spacing_override_um") is None and acquisition.spacing_um is not None:
        if all(v is not None and float(v) > 0 for v in acquisition.spacing_um):
            # Prefer source calibration stamped into the TIFF; keep override null
            # so remote open_volume uses TIFF metadata unless the user overrode.
            pass
    raw["io"] = io_spec
    raw["hpc_export"] = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "export_relative": export_relative.replace("\\", "/"),
        "export_axes_memory": EXPORT_AXES,
        "export_axes_ome": "ZCYX",
        "source_relative": acquisition.relative_source,
        "source_series": int(acquisition.series),
        "source_position": int(acquisition.position),
        "acquisition_id": acquisition.acquisition_id,
        "channel_names": list(acquisition.channel_names),
        "preprocessing_applied_locally": False,
        "projection_applied_locally": False,
    }
    # Validate through RunConfig (strips unknown? — hpc_export is extra top-level)
    # RunConfig only requires known sections; extra keys are retained in raw.
    validated = RunConfig(raw)
    return dict(validated.raw)


def _check_disk_space(path: Path, needed_bytes: int | None) -> None:
    if needed_bytes is None:
        return
    usage = shutil.disk_usage(path if path.exists() else path.parent)
    # Require a modest headroom factor.
    if usage.free < int(needed_bytes * 1.1) + 64 * 1024 * 1024:
        raise OSError(
            f"Insufficient disk space under {path}: need ~{needed_bytes} bytes, "
            f"have {usage.free} free"
        )


def prepare_bundle(
    acquisitions: Sequence[AcquisitionRef],
    output_root: str | Path,
    config: RunConfig,
    profile: AlpineProfile,
    user_settings: UserClusterSettings,
    *,
    cancel: CancellationToken | None = None,
    events: Callable[[PipelineEvent], None] = null_event_sink,
    open_volume_fn=open_volume,
    bundle_id: str | None = None,
) -> ExportResult:
    """Export a portable HPC package. Never loads a Cellpose model."""

    token = cancel or MutableCancellationToken()
    segment = config.raw["segment"]
    require_supported(str(segment["engine"]), str(segment["mode"]), profile.profile_id)

    field_errors = validate_user_profile_fields(
        profile,
        account=user_settings.account,
        qos=user_settings.qos,
        gres=user_settings.gres,
        walltime=user_settings.walltime,
        project_root=user_settings.project_root,
        scratch_root=user_settings.scratch_root,
        env_location=user_settings.env_location,
        email=user_settings.email,
    )
    if field_errors:
        raise ValueError("; ".join(field_errors))

    selected = [item for item in acquisitions if item.included and item.error is None]
    if not selected:
        raise ValueError("no included acquisitions to export")

    # Calibration gate for physical diameter.
    diameter = segment.get("diameter_px")
    if diameter is not None:
        for item in selected:
            spacing = item.spacing_um
            override = config.raw["io"].get("spacing_override_um")
            has_spacing = (
                override is not None
                and all(v is not None and float(v) > 0 for v in override)
            ) or (
                spacing is not None
                and all(v is not None and float(v) > 0 for v in spacing)
            )
            if not has_spacing:
                raise ValueError(
                    f"{item.display_name}: physical/manual diameter requires valid "
                    "per-acquisition spacing (or an explicit spacing override). "
                    "Native/no-rescale diameter_px=null does not estimate size automatically."
                )

    for item in selected:
        if item.segment_channel is None:
            raise ValueError(
                f"{item.display_name}: segment channel is unset for layout {item.layout_id!r}"
            )
        if not (0 <= int(item.segment_channel) < len(item.channel_names)):
            raise ValueError(
                f"{item.display_name}: segment channel {item.segment_channel} "
                f"out of range for {list(item.channel_names)}"
            )

    short = bundle_id or _short_id()
    bundle_name = f"cellquant_hpc_{short}"
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    bundle_dir = root / bundle_name
    if bundle_dir.exists():
        raise FileExistsError(f"bundle directory already exists: {bundle_dir}")

    estimated = 0
    for item in selected:
        size = estimate_export_bytes(item.shape, item.dtype)
        if size is not None:
            estimated += size
    _check_disk_space(root, estimated if estimated > 0 else None)

    incomplete_reason: str | None = None
    artifacts: dict[str, Path] = {}
    export_rows: list[dict[str, Any]] = []
    checksums: dict[str, str] = {}

    try:
        bundle_dir.mkdir(parents=True, exist_ok=False)
        inputs_dir = bundle_dir / "inputs"
        configs_dir = bundle_dir / "configs"
        profiles_dir = bundle_dir / "profiles"
        scripts_dir = bundle_dir / "scripts"
        runtime_dir = bundle_dir / "runtime"
        models_dir = bundle_dir / "models"
        for path in (inputs_dir, configs_dir, profiles_dir, scripts_dir, runtime_dir, models_dir):
            path.mkdir(parents=True, exist_ok=True)

        profile_path = profiles_dir / "alpine.json"
        write_text_atomic(
            profile_path,
            json.dumps(profile.to_dict(), indent=2, sort_keys=True) + "\n",
        )
        artifacts["profile"] = profile_path

        total = len(selected)
        for index, item in enumerate(selected, start=1):
            token.raise_if_cancelled()
            stem = ACQUISITION_NAME_FMT.format(index=index)
            export_name = f"{stem}.tif"
            export_rel = f"inputs/{export_name}"
            config_rel = f"configs/{stem}.json"
            events(
                PipelineEvent(
                    kind="progress",
                    run_id=bundle_name,
                    file_id=item.acquisition_id,
                    stage="hpc_export",
                    timestamp_utc=_utc(),
                    current=index,
                    total=total,
                    details={
                        "relative_source": item.relative_source,
                        "export_name": export_name,
                    },
                )
            )

            volume = open_volume_fn(
                item.source,
                series=item.series,
                position=item.position,
                lazy=False,
                axes_override=config.raw["io"].get("axes_override"),
                spacing_override_um=(
                    tuple(float(v) for v in config.raw["io"]["spacing_override_um"])
                    if config.raw["io"].get("spacing_override_um") is not None
                    else None
                ),
            )
            array = np.asarray(volume.data)
            dest = bundle_dir / export_rel
            write_canonical_tiff(
                dest,
                array,
                spacing_um=volume.spacing_um,
                channel_names=volume.channel_names,
            )
            effective = _effective_config_for_export(
                config, item, export_relative=export_rel
            )
            # Force device from profile expectations for remote run.
            if profile.require_gpu:
                effective["segment"] = {
                    **dict(effective["segment"]),
                    "device": "cuda",
                    "allow_cpu_fallback": False,
                }
            config_path = bundle_dir / config_rel
            write_text_atomic(
                config_path,
                json.dumps(effective, indent=2, sort_keys=True, allow_nan=False) + "\n",
            )
            file_hash = _sha256_file(dest)
            config_hash = _sha256_file(config_path)
            checksums[export_rel] = file_hash
            checksums[config_rel] = config_hash
            export_rows.append(
                {
                    "acquisition_id": item.acquisition_id,
                    "export_name": stem,
                    "export_relative": export_rel,
                    "config_relative": config_rel,
                    "source_relative": item.relative_source,
                    "source_series": item.series,
                    "source_position": item.position,
                    "shape_zyxc": list(array.shape),
                    "dtype": str(array.dtype),
                    "axes": EXPORT_AXES,
                    "channel_names": list(volume.channel_names),
                    "spacing_um": list(volume.spacing_um),
                    "calibration_status": volume.metadata.get("calibration_status"),
                    "spacing_source": volume.metadata.get("spacing_source"),
                    "segment_channel": item.segment_channel,
                    "segment_channel_name": volume.channel_names[int(item.segment_channel)],
                    "byte_size": int(dest.stat().st_size),
                    "sha256": file_hash,
                    "config_sha256": config_hash,
                    "config_fingerprint": RunConfig(effective).fingerprint,
                }
            )

        # Runtime marker: pin backend identity; full wheel bundling is optional later.
        runtime_meta = {
            "backend": "cellquant_core",
            "cellquant_version": CELLQUANT_VERSION,
            "profile_id": profile.profile_id,
            "notes": [
                "Cluster jobs must install/activate a compatible CellQuant environment "
                "via scripts/setup_environment.sh before submit.",
                "Do not depend on a developer Windows checkout path.",
            ],
        }
        runtime_path = runtime_dir / "backend.json"
        write_text_atomic(
            runtime_path,
            json.dumps(runtime_meta, indent=2, sort_keys=True) + "\n",
        )
        checksums["runtime/backend.json"] = _sha256_file(runtime_path)

        settings_path = bundle_dir / "configs" / "cluster_user.json"
        write_text_atomic(
            settings_path,
            json.dumps(user_settings.to_dict(), indent=2, sort_keys=True) + "\n",
        )
        checksums["configs/cluster_user.json"] = _sha256_file(settings_path)

        script_paths = generate_scripts(
            scripts_dir,
            profile=profile,
            user_settings=user_settings,
            bundle_name=bundle_name,
        )
        for rel, path in script_paths.items():
            checksums[rel] = _sha256_file(path)
            artifacts[Path(rel).stem] = path

        ood_src = ood_guide_source_path()
        if ood_src is not None:
            ood_dest = bundle_dir / "SUBMIT_THROUGH_OPEN_ONDEMAND.md"
            ood_dest.write_bytes(ood_src.read_bytes())
            # Normalize to LF for packaged docs on Windows checkouts.
            text = ood_dest.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
            if not text.endswith("\n"):
                text += "\n"
            write_text_atomic(ood_dest, text)
            checksums["SUBMIT_THROUGH_OPEN_ONDEMAND.md"] = _sha256_file(ood_dest)
            artifacts["ood_guide"] = ood_dest

        readme = render_readme(
            profile=profile,
            user_settings=user_settings,
            bundle_name=bundle_name,
            acquisition_count=len(export_rows),
        )
        readme_path = bundle_dir / README_NAME
        write_text_atomic(readme_path, readme)
        checksums[README_NAME] = _sha256_file(readme_path)
        artifacts["readme"] = readme_path

        config_digest = config.fingerprint
        bundle_payload = {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "kind": BUNDLE_KIND,
            "bundle_id": short,
            "bundle_name": bundle_name,
            "created_utc": _utc(),
            "cellquant_version": CELLQUANT_VERSION,
            "backend": "cellquant_core",
            "profile_id": profile.profile_id,
            "profile_version": profile.documentation_checked_utc,
            "documentation_checked_utc": profile.documentation_checked_utc,
            "smoke_verified_utc": profile.smoke_verified_utc,
            "submit_ready": bool(profile.submit_ready),
            "config_fingerprint": config_digest,
            "engine": segment["engine"],
            "mode": segment["mode"],
            "model": segment.get("model"),
            "model_sha256": segment.get("model_sha256"),
            "acquisition_count": len(export_rows),
            "acquisitions": export_rows,
            "image_export_policy": "lossless_all_channels_source_grid",
            "preprocessing_boundary": "remote_shared_core",
            "cluster_smoke_verified": bool(profile.smoke_verified_utc),
        }
        bundle_path = bundle_dir / BUNDLE_NAME
        write_text_atomic(
            bundle_path,
            json.dumps(bundle_payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        )
        checksums[BUNDLE_NAME] = _sha256_file(bundle_path)
        artifacts["bundle"] = bundle_path

        checksum_path = bundle_dir / CHECKSUMS_NAME
        write_text_atomic(
            checksum_path,
            json.dumps({"schema_version": 1, "files": checksums}, indent=2, sort_keys=True)
            + "\n",
        )
        artifacts["checksums"] = checksum_path

        report = {
            "created_utc": _utc(),
            "bundle_dir": str(bundle_dir),
            "acquisition_count": len(export_rows),
            "estimated_bytes": estimated if estimated > 0 else None,
            "ready": True,
            "status": "Package ready for transfer",
            "cluster_smoke_verified": bool(profile.smoke_verified_utc),
            "submit_ready": bool(profile.submit_ready),
        }
        report_path = bundle_dir / EXPORT_REPORT_NAME
        write_text_atomic(
            report_path,
            json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        )
        artifacts["export_report"] = report_path

        # Validate in-process before READY.
        from cellquant.hpc.validate import validate_bundle

        validation = validate_bundle(bundle_dir)
        if not validation.ok:
            incomplete_reason = "; ".join(validation.errors)
            raise RuntimeError(f"bundle validation failed: {incomplete_reason}")

        ready_payload = {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "bundle_id": short,
            "ready_utc": _utc(),
            "config_fingerprint": config_digest,
            "checksum_of_checksums": _sha256_file(checksum_path),
        }
        ready_path = bundle_dir / READY_NAME
        # Atomic READY publish.
        write_text_atomic(
            ready_path,
            json.dumps(ready_payload, indent=2, sort_keys=True) + "\n",
        )
        artifacts["ready"] = ready_path

        events(
            PipelineEvent(
                kind="stage_finished",
                run_id=bundle_name,
                file_id="*",
                stage="hpc_export",
                timestamp_utc=_utc(),
                details={"bundle_dir": str(bundle_dir), "ready": True},
            )
        )
        return ExportResult(
            bundle_dir=bundle_dir,
            bundle_id=short,
            ready=True,
            acquisition_count=len(export_rows),
            incomplete_reason=None,
            artifacts=artifacts,
        )
    except Exception as exc:
        incomplete_reason = str(exc)
        if bundle_dir.exists():
            marker = {
                "ready": False,
                "incomplete": True,
                "reason": incomplete_reason,
                "updated_utc": _utc(),
            }
            try:
                write_text_atomic(
                    bundle_dir / EXPORT_REPORT_NAME,
                    json.dumps(marker, indent=2) + "\n",
                )
                # Ensure READY is absent for incomplete packages.
                ready = bundle_dir / READY_NAME
                ready.unlink(missing_ok=True)
            except Exception:
                pass
        events(
            PipelineEvent(
                kind="failed",
                run_id=bundle_name,
                file_id="*",
                stage="hpc_export",
                timestamp_utc=_utc(),
                details={"error": incomplete_reason},
            )
        )
        return ExportResult(
            bundle_dir=bundle_dir if bundle_dir.exists() else root / bundle_name,
            bundle_id=short,
            ready=False,
            acquisition_count=len(export_rows),
            incomplete_reason=incomplete_reason,
            artifacts=artifacts,
        )


__all__ = [
    "ExportResult",
    "estimate_export_bytes",
    "prepare_bundle",
    "write_canonical_tiff",
]
