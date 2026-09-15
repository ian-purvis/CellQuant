"""Per-acquisition expansion for HPC prep (series × position rows)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterable, Sequence

from cellquant.io import ImageMetadata, inspect_volume, iter_supported_files, normalize_suffixes


@dataclass(frozen=True)
class AcquisitionRef:
    """One exportable source/series/position selection."""

    acquisition_id: str
    source: Path
    relative_source: str
    series: int
    position: int
    series_count: int
    position_count: int
    timepoint_count: int
    channel_names: tuple[str, ...]
    shape: tuple[int, ...]
    axes: str
    spacing_um: tuple[float | None, float | None, float | None]
    dtype: str
    format: str
    layout_id: str | None
    included: bool = True
    error: str | None = None
    segment_channel: int | None = None

    @property
    def display_name(self) -> str:
        base = self.relative_source or self.source.name
        return f"{base} [series={self.series}, position={self.position}]"


def acquisition_id_for(source: Path, series: int, position: int) -> str:
    """Stable content identity for one acquisition selection."""

    payload = f"{source.resolve().as_posix()}|s={int(series)}|p={int(position)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def reject_multi_timepoint(meta: ImageMetadata, *, relative: str) -> None:
    """Fail closed when a source has more than one timepoint."""

    count = int(getattr(meta, "timepoint_count", 1) or 1)
    if count > 1:
        raise ValueError(
            f"{relative}: time-series export is unsupported ({count} timepoints). "
            "Save a single timepoint or wait for explicit timepoint selection."
        )


def expand_acquisitions(
    sources: Sequence[str | Path],
    *,
    root: str | Path | None = None,
    recursive: bool = True,
    suffixes: Sequence[str] | None = None,
    inspect_fn: Callable[[Path], ImageMetadata] = inspect_volume,
    include_errors: bool = True,
) -> tuple[AcquisitionRef, ...]:
    """Expand files into one row per series × position.

    Multi-timepoint sources raise before any row is emitted for that file.
    """

    paths: list[Path] = []
    if root is not None:
        base = Path(root)
        paths.extend(
            iter_supported_files(base, recursive=recursive, suffixes=suffixes)
        )
    for item in sources:
        path = Path(item)
        if path.is_dir():
            paths.extend(
                iter_supported_files(path, recursive=recursive, suffixes=suffixes)
            )
        else:
            paths.append(path.resolve())

    # Stable unique order
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = str(path.resolve()).casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path.resolve())
    unique.sort(key=lambda item: str(item).casefold())

    base_root = Path(root).resolve() if root is not None else None
    records: list[AcquisitionRef] = []
    for path in unique:
        if base_root is not None:
            try:
                relative = path.relative_to(base_root).as_posix()
            except ValueError:
                relative = path.name
        else:
            relative = path.name
        try:
            meta = inspect_fn(path)
            reject_multi_timepoint(meta, relative=relative)
            series_count = max(1, int(meta.series_count or 1))
            position_count = max(1, int(meta.position_count or 1))
            names = tuple(str(name) for name in meta.channel_names)
            from cellquant.survey import layout_id_for

            layout = layout_id_for(names) if names else None
            for series in range(series_count):
                for position in range(position_count):
                    records.append(
                        AcquisitionRef(
                            acquisition_id=acquisition_id_for(path, series, position),
                            source=path,
                            relative_source=relative,
                            series=series,
                            position=position,
                            series_count=series_count,
                            position_count=position_count,
                            timepoint_count=int(meta.timepoint_count or 1),
                            channel_names=names,
                            shape=tuple(int(v) for v in meta.shape),
                            axes=str(meta.axes),
                            spacing_um=tuple(meta.spacing_um),
                            dtype=str(np_dtype_name(meta.dtype)),
                            format=str(meta.format),
                            layout_id=layout,
                            included=True,
                            error=None,
                        )
                    )
        except Exception as exc:  # noqa: BLE001 - surface per-file; continue when allowed
            if not include_errors:
                raise
            records.append(
                AcquisitionRef(
                    acquisition_id=acquisition_id_for(path, 0, 0),
                    source=path,
                    relative_source=relative,
                    series=0,
                    position=0,
                    series_count=1,
                    position_count=1,
                    timepoint_count=1,
                    channel_names=(),
                    shape=(),
                    axes="",
                    spacing_um=(None, None, None),
                    dtype="",
                    format="",
                    layout_id=None,
                    included=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
    return tuple(records)


def np_dtype_name(dtype) -> str:
    try:
        import numpy as np

        return np.dtype(dtype).name
    except Exception:  # pragma: no cover
        return str(dtype)


def with_inclusion(
    acquisitions: Iterable[AcquisitionRef],
    included_ids: set[str] | None,
) -> tuple[AcquisitionRef, ...]:
    """Return acquisitions with inclusion flags applied by id."""

    if included_ids is None:
        return tuple(acquisitions)
    return tuple(
        replace(item, included=item.acquisition_id in included_ids and item.error is None)
        for item in acquisitions
    )


def with_segment_channels(
    acquisitions: Iterable[AcquisitionRef],
    layout_channels: dict[str, int] | None,
) -> tuple[AcquisitionRef, ...]:
    """Stamp per-layout segment channel indices onto acquisitions."""

    if not layout_channels:
        return tuple(acquisitions)
    updated: list[AcquisitionRef] = []
    for item in acquisitions:
        channel = layout_channels.get(item.layout_id) if item.layout_id else None
        updated.append(replace(item, segment_channel=channel))
    return tuple(updated)


__all__ = [
    "AcquisitionRef",
    "acquisition_id_for",
    "expand_acquisitions",
    "reject_multi_timepoint",
    "with_inclusion",
    "with_segment_channels",
]
