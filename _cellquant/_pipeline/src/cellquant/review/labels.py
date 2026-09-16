"""Integer instance-label validation, hashing, and lossless TIFF round-trips.

Review must never renumber IDs, binarize instance masks, or narrow a mask to a
dtype that cannot hold its largest ID. Everything here works on canonical ZYX
arrays; 2D input is normalized to ``(1, Y, X)``.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any
import uuid

import numpy as np
import tifffile

_UINT16_MAX = int(np.iinfo(np.uint16).max)
_UINT32_MAX = int(np.iinfo(np.uint32).max)


class LabelValidationError(ValueError):
    """Raised when candidate labels are not a usable integer instance mask."""


@dataclass(frozen=True)
class LabelSummary:
    """Everything review persistence records about one label array."""

    shape: tuple[int, int, int]
    dtype: str
    label_count: int
    max_id: int
    sha256: str

    @property
    def is_empty(self) -> bool:
        return self.label_count == 0


def normalize_label_array(value: Any) -> np.ndarray:
    """Return a canonical ZYX view of ``value`` without altering IDs.

    A 2D ``(Y, X)`` mask becomes ``(1, Y, X)``; higher dimensions are rejected
    because review has no way to guess which axis carries Z.
    """

    data = np.asarray(value)
    if data.ndim == 2:
        return data.reshape((1, *data.shape))
    if data.ndim == 3:
        return data
    raise LabelValidationError(
        f"labels must be 2D (Y, X) or 3D (Z, Y, X); received shape {data.shape}"
    )


def validate_label_array(value: Any) -> np.ndarray:
    """Validate integer instance labels and return a fresh ``uint32`` ZYX copy.

    Rejects floating-point values that are not whole numbers, negative IDs, and
    IDs beyond ``uint32``. Whole-number floats are accepted because napari
    Labels layers and some external exports hand back float arrays.
    """

    data = normalize_label_array(value)
    if np.issubdtype(data.dtype, np.bool_):
        raise LabelValidationError(
            "labels must contain integer instance IDs, not a boolean mask; "
            "review does not convert instance masks to binary"
        )
    if np.issubdtype(data.dtype, np.floating):
        if not np.all(np.isfinite(data)):
            raise LabelValidationError("labels contain nonfinite values")
        if not np.all(data == np.floor(data)):
            raise LabelValidationError(
                "labels contain fractional values; instance IDs must be whole numbers"
            )
    elif not np.issubdtype(data.dtype, np.integer):
        raise LabelValidationError(
            f"labels must be an integer array; received dtype {data.dtype}"
        )
    if data.size:
        minimum = float(np.min(data))
        maximum = float(np.max(data))
        if minimum < 0:
            raise LabelValidationError("labels contain negative IDs")
        if maximum > _UINT32_MAX:
            raise LabelValidationError("labels exceed the uint32 ID range")
    return np.ascontiguousarray(data.astype(np.uint32, copy=True))


def label_disk_dtype(labels: np.ndarray) -> type[np.unsignedinteger]:
    """Smallest lossless unsigned dtype for the largest ID present."""

    maximum = int(np.max(labels, initial=0))
    return np.uint16 if maximum <= _UINT16_MAX else np.uint32


def sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_of_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def label_array_sha256(labels: np.ndarray) -> str:
    """Content digest of label *values*, independent of on-disk dtype.

    Canonicalizing to little-endian ``uint32`` keeps the digest stable when the
    same mask is stored as ``uint16`` in one revision and ``uint32`` in another.
    """

    canonical = np.ascontiguousarray(np.asarray(labels, dtype="<u4"))
    digest = hashlib.sha256()
    digest.update(repr(canonical.shape).encode("ascii"))
    digest.update(canonical.tobytes())
    return digest.hexdigest()


def count_labels(labels: np.ndarray) -> int:
    """Number of distinct positive object IDs (background 0 excluded)."""

    if labels.size == 0:
        return 0
    unique = np.unique(labels)
    return int(unique[unique > 0].size)


def summarize_labels(labels: np.ndarray) -> LabelSummary:
    validated = labels if labels.dtype == np.uint32 else validate_label_array(labels)
    return LabelSummary(
        shape=tuple(int(v) for v in validated.shape),  # type: ignore[arg-type]
        dtype=str(label_disk_dtype(validated)().dtype),
        label_count=count_labels(validated),
        max_id=int(np.max(validated, initial=0)),
        sha256=label_array_sha256(validated),
    )


def read_label_tiff(path: str | Path) -> np.ndarray:
    """Read an integer label TIFF as a validated ``uint32`` ZYX array."""

    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    try:
        raw = np.asarray(tifffile.imread(source))
    except Exception as exc:  # noqa: BLE001 - corrupt files must be reportable
        raise LabelValidationError(f"cannot read label TIFF {source}: {exc}") from exc
    if not np.issubdtype(raw.dtype, np.integer):
        raise LabelValidationError(
            f"{source} is not an integer label TIFF (dtype {raw.dtype}); "
            "export integer instance labels rather than an intensity or RGB rendering"
        )
    return validate_label_array(raw)


def looks_like_label_tiff(path: str | Path) -> bool:
    """Cheap metadata-only test used by folder discovery.

    Reads the TIFF header, not the pixels, so scanning a folder stays bounded.
    RGB renderings and floating-point images are rejected here rather than being
    silently reinterpreted as instance masks.
    """

    source = Path(path)
    if source.suffix.lower() not in {".tif", ".tiff"}:
        return False
    try:
        with tifffile.TiffFile(source) as handle:
            series = handle.series[0]
            dtype = np.dtype(series.dtype)
            shape = tuple(int(v) for v in series.shape)
            axes = str(series.axes or "").upper()
            page = handle.pages[0]
            samples = int(getattr(page, "samplesperpixel", 1) or 1)
            photometric = int(getattr(page, "photometric", 1) or 1)
    except Exception:  # noqa: BLE001 - discovery stays best-effort
        return False
    if not np.issubdtype(dtype, np.integer) or np.issubdtype(dtype, np.signedinteger):
        return False
    if samples > 1 or "S" in axes or photometric in {2, 3, 6}:
        # RGB/RGBA, palette, or YCbCr: a rendering of a mask, not the mask. The
        # sample axis is used rather than a shape heuristic so a genuine 3- or
        # 4-plane Z-stack of labels is not mistaken for a colour image.
        return False
    return len(shape) in (2, 3)


def write_label_tiff(
    path: str | Path,
    labels: np.ndarray,
    *,
    spacing_um: tuple[float, float, float] | None = None,
) -> Path:
    """Write ``labels`` losslessly, then reopen and verify the bytes round-trip.

    The write goes to a sibling temporary file that is renamed only after the
    reopen check passes, so a torn or truncated write never appears under the
    destination name.
    """

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    validated = labels if labels.dtype == np.uint32 else validate_label_array(labels)
    payload = validated.astype(label_disk_dtype(validated), copy=False)
    metadata: dict[str, Any] = {"axes": "ZYX"}
    kwargs: dict[str, Any] = {}
    if spacing_um is not None:
        z, y, x = (float(v) for v in spacing_um)
        if z > 0:
            metadata["spacing"] = z
            metadata["unit"] = "um"
        if y > 0 and x > 0:
            kwargs["resolution"] = (1.0 / x, 1.0 / y)
            kwargs["resolutionunit"] = "NONE"
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        tifffile.imwrite(
            temporary,
            payload,
            photometric="minisblack",
            metadata=metadata,
            **kwargs,
        )
        reopened = read_label_tiff(temporary)
        if reopened.shape != validated.shape or not np.array_equal(reopened, validated):
            raise LabelValidationError(
                f"reopen check failed for {destination.name}: written labels do not "
                "match the array in memory"
            )
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


__all__ = [
    "LabelSummary",
    "LabelValidationError",
    "count_labels",
    "label_array_sha256",
    "label_disk_dtype",
    "looks_like_label_tiff",
    "normalize_label_array",
    "read_label_tiff",
    "sha256_of_bytes",
    "sha256_of_file",
    "summarize_labels",
    "validate_label_array",
    "write_label_tiff",
]
