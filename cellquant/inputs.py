"""Finding and reading an experiment's image files.

The controller, the cluster worker and the tests all load an image record
through ``load_record_image``, so they apply the same position, pixel sizes
and Z handling.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path, PurePosixPath

import numpy as np

from cellquant.errors import ImageLoadError
from cellquant.image import LoadedImage, load_image, read_stack, reduce_stack


def resolve_source_path(record, experiment_dir: str | Path | None = None) -> Path:
    """The image file for a record.

    A record with ``source_path_relative`` names a file inside the experiment
    folder; it is used when present, so an experiment that carries its own
    images keeps working after the folder is moved. Otherwise ``source_path``.
    """

    relative = str(getattr(record, "source_path_relative", "") or "")
    if relative and experiment_dir is not None:
        pure = PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts:
            raise ImageLoadError(f"The image path '{relative}' must stay inside the experiment folder.")
        candidate = Path(experiment_dir).joinpath(*pure.parts)
        if candidate.is_file():
            return candidate
    return Path(record.source_path)


def load_record_image(
    record,
    *,
    z_mode: str,
    z_index: int | None,
    experiment_dir: str | Path | None = None,
) -> LoadedImage:
    """Load a record's image with its position, its pixel sizes and the analysis Z handling."""

    path = resolve_source_path(record, experiment_dir)
    if not path.is_file():
        raise ImageLoadError("File could not be opened.")
    return load_image(
        path,
        pixel_size_x=record.pixel_size_x,
        pixel_size_y=record.pixel_size_y,
        position=record.position,
        z_mode=z_mode,
        z_index=z_index,
        pixel_size_z=record.pixel_size_z,
    )


def load_record_display(
    record,
    *,
    z_mode: str,
    z_index: int | None,
    experiment_dir: str | Path | None = None,
) -> LoadedImage:
    """Load a record's image for the viewer: every Z slice, so the stack can be scrolled.

    Pixel sizes, channel names and the Z description follow the analysis Z
    handling, as in ``load_record_image``; only the pixels keep all slices,
    as ``(channels, z, y, x)``. A single-plane file stays ``(channels, y, x)``.
    """

    path = resolve_source_path(record, experiment_dir)
    if not path.is_file():
        raise ImageLoadError("File could not be opened.")
    stack = read_stack(path, position=record.position)
    loaded = reduce_stack(
        stack,
        z_mode=z_mode,
        z_index=z_index,
        pixel_size_x=record.pixel_size_x,
        pixel_size_y=record.pixel_size_y,
        pixel_size_z=record.pixel_size_z,
    )
    if stack.zcyx.shape[0] > 1 and not loaded.is_3d:
        loaded = replace(loaded, data=np.ascontiguousarray(stack.czyx))
    return loaded
