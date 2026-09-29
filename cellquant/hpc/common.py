"""Hashing, canonical JSON and atomic writes shared by preparation, the worker and import."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import numpy as np

CHUNK = 4 * 1024 * 1024


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_id(prefix: str = "", length: int = 12) -> str:
    return f"{prefix}{uuid.uuid4().hex[:length]}"


def canonical_json(value: Any) -> bytes:
    """One byte string per value: sorted keys, no spaces, UTF-8. Used for every hash of JSON data."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False, default=_default).encode(
        "utf-8"
    )


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json(value))


def sha256_file(path: str | Path, cancel=None) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            if cancel is not None:
                cancel()
            digest.update(chunk)
    return digest.hexdigest()


def pixel_sha256(czyx: np.ndarray) -> str:
    """Digest of an image's values: a header (shape, dtype, little-endian) then the pixels in C order.

    The same pixels give the same digest whatever file format or byte order they were stored in.
    """

    array = np.asarray(czyx)
    if array.ndim != 4:
        raise ValueError("Pixel digests are taken over (channels, z, y, x) arrays.")
    little = array.dtype.newbyteorder("<") if array.dtype.byteorder not in ("|",) else array.dtype
    header = canonical_json({"format": "cellquant-pixels-v1", "shape": [int(v) for v in array.shape], "dtype": little.str})
    digest = hashlib.sha256()
    digest.update(header)
    digest.update(b"\0")
    for channel in range(array.shape[0]):
        for plane in range(array.shape[1]):
            digest.update(np.ascontiguousarray(array[channel, plane], dtype=little).tobytes())
    return digest.hexdigest()


def write_bytes_atomic(path: str | Path, data: bytes) -> None:
    """Write to a temporary file beside the target, flush it to disk, then rename over the target."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex[:8]}.tmp")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)


def write_json_atomic(path: str | Path, value: Any) -> None:
    write_bytes_atomic(path, (json.dumps(value, indent=2, sort_keys=False, default=_default) + "\n").encode("utf-8"))


def write_text_lf(path: str | Path, text: str, *, executable: bool = False) -> None:
    """Text with LF line endings on every platform (shell scripts must not have CRLF)."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text.replace("\r\n", "\n"))
    if executable:
        try:
            target.chmod(0o755)
        except OSError:  # pragma: no cover - Windows ignores the mode
            pass


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def safe_relative(value: str) -> PurePosixPath:
    """A relative POSIX path that stays inside its root. Raises ValueError otherwise."""

    if not isinstance(value, str) or not value or "\\" in value or "\0" in value:
        raise ValueError(f"'{value}' is not a relative POSIX path.")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in ("..", "") for part in pure.parts) or value.startswith("./"):
        raise ValueError(f"'{value}' must be a relative path inside the package.")
    if len(pure.parts) and ":" in pure.parts[0]:
        raise ValueError(f"'{value}' looks like a drive path.")
    return pure


def contained_path(root: str | Path, relative: str) -> Path:
    """Resolve a relative package path under root, refusing anything that escapes it (including links)."""

    pure = safe_relative(relative)
    base = Path(root).resolve()
    candidate = base.joinpath(*pure.parts)
    resolved = candidate.resolve()
    if resolved != base and base not in resolved.parents:
        raise ValueError(f"'{relative}' points outside the package.")
    return candidate


def _default(value: Any):
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
