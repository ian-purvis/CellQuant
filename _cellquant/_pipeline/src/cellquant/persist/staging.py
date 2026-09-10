"""Cloud/sync-folder detection and local staging for durable run stores.

UI folders may live on OneDrive/Dropbox/Google Drive. Frequent atomic replaces
there fail under sync locks. CellQuant writes the hot run store under
``%LOCALAPPDATA%/CellQuant/staging/...`` (or the platform equivalent) and
publishes a copy to the user-selected folder when the run finishes.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from pathlib import Path

from cellquant.persist.atomic import replace_with_retry


_CLOUD_MARKERS = (
    "onedrive",
    "dropbox",
    "google drive",
    "googledrive",
    "box sync",
    "box\\",
    "icloud",
    "sharepoint",
)


def looks_like_cloud_sync_path(path: str | Path) -> bool:
    """Heuristic: path appears to sit under a consumer/cloud sync root."""

    text = str(Path(path)).casefold().replace("/", "\\")
    if any(marker in text for marker in _CLOUD_MARKERS):
        return True
    # Common Windows OneDrive layout: ...\Users\<name>\OneDrive - <tenant>\...
    parts = Path(path).parts
    for part in parts:
        lowered = part.casefold()
        if lowered.startswith("onedrive"):
            return True
    return False


def local_staging_root() -> Path:
    """Root directory for local CellQuant staging mirrors.

    Honours ``CELLQUANT_SCRATCH`` when set so users can point temporary and
    staged run stores at a local non-synced drive.
    """

    override = os.environ.get("CELLQUANT_SCRATCH")
    if override:
        root = Path(override).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        return root.resolve()
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    if base:
        root = Path(base) / "CellQuant" / "staging"
    else:
        root = Path.home() / ".cache" / "cellquant" / "staging"
    root.mkdir(parents=True, exist_ok=True)
    return root


def staging_directory_for(user_output: str | Path) -> Path:
    """Stable local mirror directory for a user-selected output folder."""

    resolved = str(Path(user_output).expanduser().resolve())
    digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]
    path = local_staging_root() / digest
    path.mkdir(parents=True, exist_ok=True)
    # Breadcrumb so operators can map staging ↔ UI folder.
    marker = path / "cellquant_publish_target.txt"
    if not marker.exists():
        marker.write_text(resolved + "\n", encoding="utf-8")
    return path


def resolve_work_directory(user_output: str | Path) -> tuple[Path, Path | None]:
    """Return ``(work_dir, publish_to)``.

    When ``publish_to`` is not None, all hot writes should go to ``work_dir``
    and ``publish_directory(work_dir, publish_to)`` should run after commit.
    """

    user_output = Path(user_output)
    if looks_like_cloud_sync_path(user_output):
        return staging_directory_for(user_output), user_output
    return user_output, None


def publish_directory(source: str | Path, destination: str | Path) -> None:
    """Copy a finished staging tree to the user-selected (possibly cloud) folder."""

    source = Path(source)
    destination = Path(destination)
    if source.resolve() == destination.resolve():
        return
    destination.mkdir(parents=True, exist_ok=True)
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        if path.name.endswith(".tmp") or path.name == "cellquant_publish_target.txt":
            continue
        relative = path.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            with path.open("rb") as reader, temporary.open("xb") as writer:
                shutil.copyfileobj(reader, writer)
                writer.flush()
                os.fsync(writer.fileno())
            # Cloud publish can need longer than local event-log retries.
            replace_with_retry(temporary, target, attempts=20, base_delay_s=0.1)
        finally:
            temporary.unlink(missing_ok=True)


__all__ = [
    "looks_like_cloud_sync_path",
    "local_staging_root",
    "staging_directory_for",
    "resolve_work_directory",
    "publish_directory",
]
