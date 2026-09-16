"""Cluster-side sequential runner over an HPC bundle (shared CellQuant core)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cellquant.batch import BatchItem, run_batch
from cellquant.config import load_config
from cellquant.contracts import MutableCancellationToken
from cellquant.hpc.validate import validate_bundle
from cellquant.persist.atomic import write_text_atomic
from cellquant.persist.store import RunStore


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _completion_path(result_root: Path, export_name: str) -> Path:
    return result_root / "completions" / f"{export_name}.json"


def portable_run_dir(export_name: str) -> str:
    """Return the result-relative store path used in published manifests."""

    return f"runs/{export_name}.cellquant"


def run_bundle(
    bundle_dir: str | Path,
    result_dir: str | Path,
    *,
    cancel: MutableCancellationToken | None = None,
) -> dict[str, Any]:
    """Run acquisitions sequentially via the shared batch core; resume safely."""

    token = cancel or MutableCancellationToken()
    bundle_root = Path(bundle_dir)
    result_root = Path(result_dir)
    result_root.mkdir(parents=True, exist_ok=True)
    (result_root / "completions").mkdir(parents=True, exist_ok=True)
    (result_root / "runs").mkdir(parents=True, exist_ok=True)
    (result_root / "logs").mkdir(parents=True, exist_ok=True)

    validation = validate_bundle(bundle_root, require_ready=True)
    if not validation.ok:
        raise RuntimeError("bundle validation failed: " + "; ".join(validation.errors))
    assert validation.bundle is not None
    bundle = validation.bundle

    lock = result_root / ".runner.lock"
    if lock.exists():
        raise RuntimeError(
            f"another runner appears active ({lock}); refuse to share mutable resume state"
        )
    write_text_atomic(lock, json.dumps({"pid": os.getpid(), "started_utc": _utc()}) + "\n")

    summary: dict[str, Any] = {
        "schema_version": 1,
        "bundle_id": bundle.get("bundle_id"),
        "bundle_name": bundle.get("bundle_name"),
        "started_utc": _utc(),
        "completed": 0,
        "resumed": 0,
        "failed": 0,
        "cancelled": 0,
        "total": len(bundle.get("acquisitions") or []),
        "acquisitions": [],
    }
    try:
        # GPU gate once before any segmentation (no silent CPU fallback).
        first_cfg = None
        acquisitions = list(bundle.get("acquisitions") or [])
        if acquisitions:
            first_cfg = load_config(bundle_root / acquisitions[0]["config_relative"])
            if first_cfg.raw["segment"].get("device") == "cuda":
                try:
                    import torch
                except ImportError as exc:
                    raise RuntimeError("torch unavailable for GPU check") from exc
                from cellquant.runtime.cuda_compat import check_cuda_device
                import os

                status = check_cuda_device(
                    torch_module=torch,
                    allocate_smoke=True,
                    platform="alpine",
                    env_prefix=os.environ.get("CONDA_PREFIX")
                    or os.environ.get("ENV_LOCATION"),
                )
                if not status.ok:
                    message = (
                        "GPU required by profile but CUDA is not usable; "
                        f"refusing silent CPU fallback: {status.detail}"
                    )
                    if status.remediation:
                        message = f"{message}\n{status.remediation}"
                    raise RuntimeError(message)

        for row in acquisitions:
            token.raise_if_cancelled()
            export_name = row["export_name"]
            image_path = bundle_root / row["export_relative"]
            config = load_config(bundle_root / row["config_relative"])
            # Prefer manifest checksum; fall back to live hash.
            input_fp = str(row.get("sha256") or _sha256(image_path))
            rel_run = portable_run_dir(export_name)
            run_dir = result_root / Path(rel_run)
            record: dict[str, Any] = {
                "acquisition_id": row["acquisition_id"],
                "export_name": export_name,
                "source_relative": row.get("source_relative"),
                "run_dir": rel_run,
                "status": "pending",
            }

            existing = RunStore(run_dir, input_fp, config.fingerprint)
            if existing.is_resumable(input_fp, config.fingerprint):
                # Existing mask filename alone is insufficient; is_resumable checks
                # fingerprints + artifact hashes.
                completion = {
                    "status": "completed",
                    "resumed": True,
                    "completed_utc": _utc(),
                    "acquisition_id": row["acquisition_id"],
                    "export_name": export_name,
                    "export_sha256": input_fp,
                    "config_fingerprint": config.fingerprint,
                    "model_sha256": config.raw["segment"].get("model_sha256"),
                    "run_dir": rel_run,
                }
                write_text_atomic(
                    _completion_path(result_root, export_name),
                    json.dumps(completion, indent=2, sort_keys=True) + "\n",
                )
                record["status"] = "resumed"
                summary["resumed"] += 1
                summary["acquisitions"].append(record)
                continue

            item = BatchItem(
                source=image_path,
                output_dir=run_dir,
                output_root=result_root / "runs",
                input_fingerprint=input_fp,
                file_id=export_name,
            )
            batch_summary = run_batch(
                [item],
                config,
                token,
                write_root_reports=False,
            )
            result = batch_summary.results[0] if batch_summary.results else None
            status = result.status if result is not None else "failed"
            record["status"] = status
            if status == "completed":
                summary["completed"] += 1
                write_text_atomic(
                    _completion_path(result_root, export_name),
                    json.dumps(
                        {
                            "status": "completed",
                            "completed_utc": _utc(),
                            "acquisition_id": row["acquisition_id"],
                            "export_name": export_name,
                            "export_sha256": input_fp,
                            "config_fingerprint": config.fingerprint,
                            "model_sha256": config.raw["segment"].get("model_sha256"),
                            "run_dir": rel_run,
                        },
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                )
            elif status == "resumed":
                summary["resumed"] += 1
            elif status == "cancelled":
                summary["cancelled"] += 1
                record["error"] = result.message if result else "cancelled"
                break
            else:
                summary["failed"] += 1
                record["error"] = result.message if result else "failed"
                write_text_atomic(
                    _completion_path(result_root, export_name),
                    json.dumps(
                        {
                            "status": "failed",
                            "failed_utc": _utc(),
                            "acquisition_id": row["acquisition_id"],
                            "export_name": export_name,
                            "error": record.get("error"),
                        },
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                )
            summary["acquisitions"].append(record)
    finally:
        summary["finished_utc"] = _utc()
        # Result publication is separate from compute status.
        write_text_atomic(
            result_root / "result_manifest.json",
            json.dumps(
                {
                    **summary,
                    "source_bundle_id": bundle.get("bundle_id"),
                    "source_bundle_name": bundle.get("bundle_name"),
                    "identity_map": [
                        {
                            "acquisition_id": row.get("acquisition_id"),
                            "export_name": row.get("export_name"),
                            "source_relative": row.get("source_relative"),
                            "source_series": row.get("source_series"),
                            "source_position": row.get("source_position"),
                            "channel_names": row.get("channel_names"),
                            "spacing_um": row.get("spacing_um"),
                            "shape_zyxc": row.get("shape_zyxc"),
                        }
                        for row in acquisitions
                    ],
                },
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n",
        )
        lock.unlink(missing_ok=True)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m cellquant.hpc.runner")
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args(argv)
    summary = run_bundle(args.bundle, args.result)
    print(json.dumps(summary, indent=2))
    return 1 if summary.get("failed") or summary.get("cancelled") else 0


if __name__ == "__main__":
    raise SystemExit(main())
