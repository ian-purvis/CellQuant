"""Publishing results to durable storage, and checking what was published.

A task's files are copied into a hidden temporary folder beside their final
place, on the destination filesystem, checked against their hashes, and then
renamed into place. The task's commit record is written last, with every
artifact's hash. Renames across filesystems are never relied on.
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

from cellquant.hpc.common import read_json, sha256_file, write_json_atomic
from cellquant.hpc.models import ArtifactEntry, TaskResult, load_model

COMMIT = "COMMIT.json"
FAILED = "FAILED.json"


class PublicationError(Exception):
    """Copying to durable storage failed or could not be verified. Scratch is kept."""


def hash_tree(root: Path, *, skip: tuple[str, ...] = (COMMIT, FAILED)) -> list[ArtifactEntry]:
    entries = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name not in skip:
            relative = path.relative_to(root).as_posix()
            entries.append(ArtifactEntry(path=relative, bytes=path.stat().st_size, sha256=sha256_file(path)))
    return entries


def verify_artifacts(root: Path, artifacts: list[ArtifactEntry]) -> list[str]:
    """Problems with the listed files under root (missing, wrong size, wrong hash)."""

    from cellquant.hpc.common import contained_path

    problems = []
    for entry in artifacts:
        try:
            path = contained_path(root, entry.path)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        if not path.is_file():
            problems.append(f"{entry.path} is missing")
        elif path.stat().st_size != entry.bytes:
            problems.append(f"{entry.path} has the wrong size")
        elif sha256_file(path) != entry.sha256:
            problems.append(f"{entry.path} does not match its hash")
    return problems


def publish_directory(source: Path, destination: Path, expected: list[ArtifactEntry]) -> Path:
    """Copy a finished task folder to its final durable place, verified, then renamed into place."""

    if destination.exists():
        raise PublicationError(f"{destination} already exists; published results are never overwritten.")
    parent = destination.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        staging = parent / f".{destination.name}.partial-{uuid.uuid4().hex[:8]}"
        for entry in expected:
            target = staging / entry.path
            target.parent.mkdir(parents=True, exist_ok=True)
            with (source / entry.path).open("rb") as reader, target.open("wb") as writer:
                shutil.copyfileobj(reader, writer, 4 * 1024 * 1024)
                writer.flush()
                os.fsync(writer.fileno())
        problems = verify_artifacts(staging, expected)
        if problems:
            raise PublicationError("The copy in durable storage does not match: " + "; ".join(problems[:3]))
        os.rename(staging, destination)
    except PublicationError:
        raise
    except OSError as exc:
        raise PublicationError(f"Copying results to {parent} failed: {exc}") from exc
    return destination


def write_commit(task_dir: Path, result: TaskResult) -> str:
    """Write the task's record (COMMIT.json for a success, FAILED.json otherwise). Returns its SHA-256."""

    name = COMMIT if result.outcome == "succeeded" else FAILED
    path = task_dir / name
    try:
        write_json_atomic(path, result.model_dump(mode="json"))
    except OSError as exc:
        raise PublicationError(f"Writing {path} failed: {exc}") from exc
    return sha256_file(path)


def read_commit(task_dir: Path) -> TaskResult | None:
    path = task_dir / COMMIT
    if not path.is_file():
        return None
    return load_model(TaskResult, data=read_json(path))  # type: ignore[return-value]


def verified_commit(task_dir: Path) -> tuple[TaskResult | None, str]:
    """A success record whose artifacts are all present and unchanged, or (None, why not)."""

    try:
        record = read_commit(task_dir)
    except Exception as exc:  # noqa: BLE001
        return None, f"its commit record cannot be read ({exc})"
    if record is None:
        return None, "no commit record"
    if record.outcome != "succeeded":
        return None, f"outcome {record.outcome}"
    problems = verify_artifacts(task_dir, record.artifacts)
    if problems:
        return None, "; ".join(problems[:3])
    return record, ""
