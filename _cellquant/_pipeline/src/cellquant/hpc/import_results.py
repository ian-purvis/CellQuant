"""Import HPC result packages back into CellQuant review workflows."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from cellquant.hpc.validate import validate_bundle
from cellquant.persist.atomic import write_text_atomic


@dataclass(frozen=True)
class ImportRow:
    acquisition_id: str
    export_name: str
    status: str
    run_dir: Path | None
    source_relative: str | None
    message: str | None = None


@dataclass(frozen=True)
class ImportSummary:
    result_root: Path
    destination: Path
    rows: tuple[ImportRow, ...]
    complete: int
    failed: int
    unfinished: int
    mapping_path: Path


def discover_result_package(path: str | Path) -> Path:
    """Accept a result root or a parent folder containing result_manifest.json."""

    root = Path(path)
    if (root / "result_manifest.json").is_file():
        return root
    # Nested common layout: results/<bundle>/result_manifest.json
    matches = sorted(root.rglob("result_manifest.json"))
    if len(matches) == 1:
        return matches[0].parent
    if not matches:
        raise FileNotFoundError(f"no result_manifest.json under {root}")
    raise ValueError(
        f"multiple result packages under {root}; select the specific result folder"
    )


def resolve_imported_run_dir(
    result_root: Path,
    export_name: str,
    recorded: str | None,
) -> Path | None:
    """Locate a run store after cluster → local transfer.

    Prefer the downloaded result tree. Absolute cluster paths are used only
    when they still exist (importing on the same machine).
    """

    portable = result_root / "runs" / f"{export_name}.cellquant"
    candidates: list[Path] = [portable]
    if recorded:
        rec = Path(str(recorded))
        if rec.is_absolute():
            candidates.append(rec)
            if len(rec.parts) >= 2:
                candidates.append(result_root / rec.parts[-2] / rec.parts[-1])
            candidates.append(result_root / rec.name)
        else:
            candidates.insert(0, result_root / rec)
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.is_dir():
            return path
    return None


def import_hpc_results(
    result_path: str | Path,
    destination: str | Path,
    *,
    source_bundle: str | Path | None = None,
    copy_runs: bool = True,
) -> ImportSummary:
    """Validate and stage HPC results for local open/review.

    Completed runs remain ordinary ``*.cellquant`` stores. Import never marks
    segmentations as manually reviewed.
    """

    result_root = discover_result_package(result_path)
    manifest = json.loads((result_root / "result_manifest.json").read_text(encoding="utf-8"))
    dest = Path(destination)
    dest.mkdir(parents=True, exist_ok=True)

    identity = {
        row.get("acquisition_id"): row for row in (manifest.get("identity_map") or [])
    }
    # Optional: cross-check against the original export bundle if provided.
    if source_bundle is not None:
        validation = validate_bundle(source_bundle)
        if validation.bundle is not None:
            expected_ids = {
                row["acquisition_id"] for row in validation.bundle.get("acquisitions") or []
            }
            seen = {row.get("acquisition_id") for row in manifest.get("acquisitions") or []}
            missing = expected_ids - seen
            if missing:
                raise ValueError(
                    "result package is missing acquisitions from the source bundle: "
                    + ", ".join(sorted(missing)[:8])
                )

    rows: list[ImportRow] = []
    complete = failed = unfinished = 0
    for entry in manifest.get("acquisitions") or []:
        acquisition_id = str(entry.get("acquisition_id"))
        export_name = str(entry.get("export_name"))
        status = str(entry.get("status") or "unfinished")
        recorded = entry.get("run_dir")
        run_dir = resolve_imported_run_dir(
            result_root,
            export_name,
            str(recorded) if recorded else None,
        )

        identity_row = identity.get(acquisition_id) or {}
        source_relative = entry.get("source_relative") or identity_row.get("source_relative")

        if status in {"completed", "resumed"} and run_dir is not None and run_dir.is_dir():
            target = dest / f"{export_name}.cellquant"
            if copy_runs:
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(run_dir, target)
                staged = target
            else:
                staged = run_dir
            # Never invent a reviewed marker.
            reviewed = staged / "labels_reviewed.tif"
            if reviewed.exists():
                # Leave as-is if the user curated remotely (unlikely); do not create.
                pass
            rows.append(
                ImportRow(
                    acquisition_id=acquisition_id,
                    export_name=export_name,
                    status="complete",
                    run_dir=staged,
                    source_relative=source_relative,
                )
            )
            complete += 1
        elif status == "failed":
            rows.append(
                ImportRow(
                    acquisition_id=acquisition_id,
                    export_name=export_name,
                    status="failed",
                    run_dir=run_dir,
                    source_relative=source_relative,
                    message=entry.get("error"),
                )
            )
            failed += 1
        else:
            rows.append(
                ImportRow(
                    acquisition_id=acquisition_id,
                    export_name=export_name,
                    status="unfinished",
                    run_dir=run_dir,
                    source_relative=source_relative,
                    message=entry.get("error") or "acquisition did not finish on the cluster",
                )
            )
            unfinished += 1

    mapping = {
        "schema_version": 1,
        "result_root": str(result_root),
        "destination": str(dest),
        "bundle_id": manifest.get("bundle_id"),
        "imported": [
            {
                "acquisition_id": row.acquisition_id,
                "export_name": row.export_name,
                "status": row.status,
                "run_dir": str(row.run_dir) if row.run_dir else None,
                "source_relative": row.source_relative,
                "message": row.message,
                "manually_reviewed": False,
            }
            for row in rows
        ],
    }
    mapping_path = dest / "hpc_import_mapping.json"
    write_text_atomic(mapping_path, json.dumps(mapping, indent=2, sort_keys=True) + "\n")
    return ImportSummary(
        result_root=result_root,
        destination=dest,
        rows=tuple(rows),
        complete=complete,
        failed=failed,
        unfinished=unfinished,
        mapping_path=mapping_path,
    )


__all__ = [
    "ImportRow",
    "ImportSummary",
    "discover_result_package",
    "import_hpc_results",
    "resolve_imported_run_dir",
]
