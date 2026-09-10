"""Atomic filesystem helpers with Windows/OneDrive-friendly retries."""

from __future__ import annotations

import os
from pathlib import Path
from time import sleep
import uuid


def _retryable_replace_error(exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):
        return True
    if not isinstance(exc, OSError):
        return False
    winerror = getattr(exc, "winerror", None)
    if winerror in {5, 32}:  # ACCESS_DENIED, SHARING_VIOLATION
        return True
    return exc.errno in {11, 13, 16}  # EAGAIN / EACCES / EBUSY (POSIX)


def replace_with_retry(
    source: str | Path,
    destination: str | Path,
    *,
    attempts: int = 12,
    base_delay_s: float = 0.05,
) -> None:
    """Replace ``destination`` with ``source``, retrying brief lock conflicts.

    OneDrive and antivirus on Windows often deny ``os.replace`` for a short
    window while syncing ``events.jsonl`` / status files. Retrying avoids
    failing an otherwise successful segmentation run.
    """

    source = Path(source)
    destination = Path(destination)
    last: BaseException | None = None
    for attempt in range(max(attempts, 1)):
        try:
            os.replace(source, destination)
            return
        except OSError as exc:
            if not _retryable_replace_error(exc):
                raise
            last = exc
            if attempt + 1 >= attempts:
                break
            sleep(base_delay_s * (2 ** min(attempt, 5)))
    assert last is not None
    raise PermissionError(
        f"Could not update {destination} (temp {source.name}). "
        "Windows reported the file was locked — often cloud sync (OneDrive) or another "
        "program holding the file. CellQuant stages run stores locally for cloud output "
        "folders; if this still appears, pause sync briefly or free the file and re-run. "
        f"Last error: {last}"
    ) from last


def write_bytes_atomic(path: str | Path, payload: bytes, *, attempts: int = 12) -> None:
    """Write bytes via temp + replace_with_retry (safe on cloud-synced folders)."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(payload)
        replace_with_retry(temporary, path, attempts=attempts)
    finally:
        temporary.unlink(missing_ok=True)


def write_text_atomic(path: str | Path, text: str, *, encoding: str = "utf-8", attempts: int = 12) -> None:
    write_bytes_atomic(path, text.encode(encoding), attempts=attempts)


__all__ = ["replace_with_retry", "write_bytes_atomic", "write_text_atomic"]
