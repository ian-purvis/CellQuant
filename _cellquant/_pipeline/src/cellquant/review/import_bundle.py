"""Import adapter turning a standalone label TIFF into a ``.cellquant`` bundle.

The imported original is never modified. A bundle is a short, uniquely named
directory holding a copy of the labels plus the honest metadata an import can
supply: how to reopen the source, the analysis grid the mask lives on, and
nullable segmentation provenance. Cellpose settings are never fabricated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
import uuid

import numpy as np

from cellquant.config import ImportedRunConfig, load_imported_config
from cellquant.review.constants import (
    CONFIG_NAME,
    IMPORTED_RUN_KIND,
    IMPORT_CONFIG_SCHEMA_VERSION,
    IMPORT_ORIGIN_CELLPOSE_TIFF,
    LABELS_NAME,
    PROVENANCE_NAME,
    REVIEWED_MASKS_DIR_NAME,
    STATUS_NAME,
)
from cellquant.review.labels import (
    label_array_sha256,
    read_label_tiff,
    sha256_of_file,
    write_label_tiff,
)

#: Windows tolerates long paths poorly; bundles stay short by construction.
MAX_STEM_CHARS = 24
_MAX_PATH = 240


class MaskImportError(ValueError):
    """Raised when a standalone mask cannot be imported as a review bundle."""


@dataclass(frozen=True)
class ImportedBundle:
    """A created review-import bundle."""

    run_dir: Path
    labels_path: Path
    config: ImportedRunConfig
    imported_from: Path
    source: Path


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def bundle_name(mask_path: str | Path, *, salt: str = "") -> str:
    """Short, collision-resistant bundle directory name for ``mask_path``."""

    path = Path(mask_path)
    stem = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in path.stem)
    stem = stem[:MAX_STEM_CHARS].strip("_") or "mask"
    identity = hashlib.sha256(
        (str(path.resolve()).casefold() + salt).encode("utf-8")
    ).hexdigest()[:8]
    return f"{stem}-{identity}.cellquant"


def default_output_root(mask_path: str | Path) -> Path:
    """Sibling ``reviewed_masks`` directory next to the imported mask."""

    return Path(mask_path).parent / REVIEWED_MASKS_DIR_NAME


def derive_analysis_declaration(
    labels: np.ndarray,
    *,
    spacing_um: tuple[float, float, float],
    original_z_depth: int | None = None,
    mode: str | None = None,
    z_index: int = 0,
) -> dict[str, Any]:
    """Build the ``analysis`` grid declaration for an imported mask.

    Multi-Z masks declare ``volume_3d`` over all planes. Singleton-Z masks are
    ambiguous by nature, so they declare an explicit single plane; the caller can
    override ``mode`` when the mask is a maximum projection instead.
    """

    shape = tuple(int(v) for v in labels.shape)
    if len(shape) != 3:
        raise MaskImportError(f"imported labels must be ZYX; received shape {shape}")
    depth = int(original_z_depth if original_z_depth is not None else shape[0])
    if depth < 1:
        raise MaskImportError("original_z_depth must be a positive integer")
    resolved_mode = mode or ("volume_3d" if shape[0] > 1 else "single_plane_2d")
    if resolved_mode in {"volume_3d", "stitch_2d"}:
        z_selection: Any = "all_planes"
        depth = max(depth, shape[0])
    elif resolved_mode == "single_plane_2d":
        z_selection = {"kind": "single_plane", "z_index": int(z_index)}
    elif resolved_mode == "max_projection_2d":
        z_selection = {"kind": "maximum_projection", "z_start": 0, "z_stop_exclusive": depth}
    else:
        raise MaskImportError(f"unsupported analysis mode {resolved_mode!r}")
    return {
        "mode": resolved_mode,
        "z_selection": z_selection,
        "original_z_depth": depth,
        "spacing_um": [float(v) for v in spacing_um],
        "shape_zyx": [int(v) for v in shape],
        "preprocess_channel": 0,
    }


def build_imported_config(
    *,
    source: Path,
    analysis: Mapping[str, Any],
    imported_from: Path,
    series: int = 0,
    position: int = 0,
    lazy: bool = True,
    axes_override: str | None = None,
    spacing_override_um: tuple[float, float, float] | None = None,
    engine: str | None = None,
    model: str | None = None,
    model_sha256: str | None = None,
    settings: Mapping[str, Any] | None = None,
    origin: str = IMPORT_ORIGIN_CELLPOSE_TIFF,
) -> dict[str, Any]:
    """Assemble the imported configuration body. Unknown Cellpose fields stay null."""

    return {
        "schema_version": IMPORT_CONFIG_SCHEMA_VERSION,
        "run_kind": IMPORTED_RUN_KIND,
        "io": {
            "series": int(series),
            "position": int(position),
            "lazy": bool(lazy),
            "axes_override": axes_override,
            "spacing_override_um": (
                [float(v) for v in spacing_override_um]
                if spacing_override_um is not None
                else None
            ),
            "recursive": False,
            "suffixes": [source.suffix.lower()] if source.suffix else [".tif"],
        },
        "analysis": dict(analysis),
        "segmentation_provenance": {
            "origin": str(origin),
            "engine": engine,
            "model": model,
            "model_sha256": model_sha256,
            "settings": dict(settings) if settings is not None else None,
            "imported_from": str(imported_from),
        },
    }


def create_import_bundle(
    mask_path: str | Path,
    *,
    source: str | Path,
    spacing_um: tuple[float, float, float],
    output_root: str | Path | None = None,
    analysis: Mapping[str, Any] | None = None,
    original_z_depth: int | None = None,
    analysis_mode: str | None = None,
    z_index: int = 0,
    series: int = 0,
    position: int = 0,
    axes_override: str | None = None,
    engine: str | None = None,
    model: str | None = None,
    model_sha256: str | None = None,
    settings: Mapping[str, Any] | None = None,
    note: str = "",
) -> ImportedBundle:
    """Create a ``.cellquant`` review-import bundle for a standalone mask.

    The mask is validated, normalized to ZYX, and copied in as ``labels.tif``.
    The imported file itself is left untouched.
    """

    mask = Path(mask_path).expanduser()
    if not mask.is_file():
        raise FileNotFoundError(mask)
    image = Path(source).expanduser()
    if not image.is_file():
        raise MaskImportError(
            f"source image for {mask.name} not found at {image}; resolve the pairing "
            "before importing so the mask keeps a reconstructable grid"
        )
    labels = read_label_tiff(mask)
    declaration = dict(
        analysis
        if analysis is not None
        else derive_analysis_declaration(
            labels,
            spacing_um=spacing_um,
            original_z_depth=original_z_depth,
            mode=analysis_mode,
            z_index=z_index,
        )
    )
    root = Path(output_root) if output_root is not None else default_output_root(mask)
    root = root.expanduser()
    salt = ""
    run_dir = root / bundle_name(mask)
    while run_dir.exists():
        salt = uuid.uuid4().hex[:6]
        run_dir = root / bundle_name(mask, salt=salt)
    if len(str(run_dir / "reviews" / "r000001.tif")) > _MAX_PATH:
        raise MaskImportError(
            f"review bundle path is too long for Windows ({run_dir}); choose a shorter "
            "output folder"
        )
    run_dir.mkdir(parents=True)

    body = build_imported_config(
        source=image,
        analysis=declaration,
        imported_from=mask,
        series=series,
        position=position,
        axes_override=axes_override,
        spacing_override_um=tuple(float(v) for v in spacing_um),
        engine=engine,
        model=model,
        model_sha256=model_sha256,
        settings=settings,
    )
    # Validate before anything downstream can read a half-formed bundle.
    config = ImportedRunConfig(body)
    labels_path = write_label_tiff(run_dir / LABELS_NAME, labels, spacing_um=spacing_um)
    (run_dir / CONFIG_NAME).write_text(
        json.dumps(config.raw, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (run_dir / PROVENANCE_NAME).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_kind": IMPORTED_RUN_KIND,
                "run_id": f"import-{uuid.uuid4().hex[:12]}",
                "source": str(image),
                "imported_from": str(mask),
                "imported_sha256": sha256_of_file(mask),
                "imported_labels_sha256": label_array_sha256(labels),
                "imported_utc": _utc(),
                "config_fingerprint": config.fingerprint,
                "label_provenance": {
                    "analysis_volume": {
                        "mode": declaration["mode"],
                        "z_selection": declaration["z_selection"],
                        "original_z_depth": int(declaration["original_z_depth"]),
                    },
                    "spacing_um": [float(v) for v in spacing_um],
                },
                "segmentation_provenance": dict(config.raw["segmentation_provenance"]),
                "note": str(note or ""),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / STATUS_NAME).write_text(
        json.dumps(
            {"schema_version": 1, "status": "complete", "completed_utc": _utc()},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return ImportedBundle(
        run_dir=run_dir,
        labels_path=labels_path,
        config=config,
        imported_from=mask,
        source=image,
    )


def is_imported_run(run_dir: str | Path) -> bool:
    config = Path(run_dir) / CONFIG_NAME
    if not config.is_file():
        return False
    try:
        raw = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(raw, Mapping) and str(raw.get("run_kind") or "") == IMPORTED_RUN_KIND


def load_bundle_config(run_dir: str | Path) -> ImportedRunConfig:
    return load_imported_config(Path(run_dir) / CONFIG_NAME)


def relink_source(run_dir: str | Path, source: str | Path) -> Path:
    """Point an imported bundle at a moved source image without changing its grid.

    Only the recorded source path changes; axes, spacing, series/position, and the
    analysis declaration are preserved so the grid interpretation cannot shift
    silently.
    """

    root = Path(run_dir)
    if not is_imported_run(root):
        raise MaskImportError(f"{root} is not an imported review bundle")
    image = Path(source).expanduser()
    if not image.is_file():
        raise FileNotFoundError(image)
    provenance_path = root / PROVENANCE_NAME
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["source"] = str(image)
    provenance["relinked_utc"] = _utc()
    provenance_path.write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    config_path = root / CONFIG_NAME
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    io = dict(raw.get("io") or {})
    io["suffixes"] = [image.suffix.lower()] if image.suffix else io.get("suffixes") or [".tif"]
    raw["io"] = io
    config = ImportedRunConfig(raw)
    config_path.write_text(
        json.dumps(config.raw, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return image


__all__ = [
    "ImportedBundle",
    "MaskImportError",
    "build_imported_config",
    "bundle_name",
    "create_import_bundle",
    "default_output_root",
    "derive_analysis_declaration",
    "is_imported_run",
    "load_bundle_config",
    "relink_source",
]
