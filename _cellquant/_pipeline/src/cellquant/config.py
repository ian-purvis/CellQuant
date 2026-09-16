"""Versioned configuration loading and canonical serialization."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from cellquant.io._common import normalize_suffixes


REQUIRED_SEGMENT_KEYS = {
    "engine",
    "model",
    "model_sha256",
    "mode",
    "z_index",
    "diameter_px",
    "anisotropy",
    "min_size",
    "flow_threshold",
    "cellprob_threshold",
    "stitch_threshold",
    "channel_axis",
    "z_axis",
    "tile",
    "tile_overlap",
    "batch_size",
    "augment",
    "resample",
    "normalize",
    "device",
    "allow_cpu_fallback",
    "use_bfloat16",
    "flow3D_smooth",
    "max_size_fraction",
    "niter",
    "bsize",
    "compute_masks",
    "channels",
    "rescale_factor",
    "progress",
    "model_type",
    "diam_mean",
    "nchan",
}

REQUIRED_PREPROCESS_KEYS = {"channel", "normalize", "rescale", "denoise"}
REQUIRED_IO_KEYS = {
    "series",
    "position",
    "lazy",
    "axes_override",
    "spacing_override_um",
    "recursive",
    "suffixes",
}
REQUIRED_POSTPROCESS_KEYS = {
    "min_volume_um3",
    "max_volume_um3",
    "min_voxels",
    "max_voxels",
    "remove_border_faces",
    "relabel",
}
OPTIONAL_POSTPROCESS_KEYS = frozenset({"min_area_um2", "max_area_um2"})
REQUIRED_MEASURE_KEYS = {"intensity_statistics", "channels"}
REQUIRED_VIZ_KEYS = {"low_percentile", "high_percentile", "label_seed", "dpi"}
REQUIRED_RUNTIME_KEYS = {
    "seed",
    "deterministic_torch",
    "hash_inputs",
    "output_compression",
}
REQUIRED_NORMALIZE_KEYS = {"enabled", "low_percentile", "high_percentile", "scope"}
REQUIRED_RESCALE_KEYS = {
    "enabled",
    "target_spacing_um",
    "interpolation",
    "antialias",
    "boundary",
    "cval",
}
REQUIRED_DENOISE_KEYS = {"enabled", "method", "parameters", "boundary", "cval"}


@dataclass(frozen=True)
class RunConfig:
    raw: Mapping[str, Any]

    def __post_init__(self) -> None:
        if int(self.raw.get("schema_version", -1)) != 1:
            raise ValueError("schema_version must be 1")
        for section in ("io", "preprocess", "segment", "postprocess", "measure", "viz", "runtime"):
            if not isinstance(self.raw.get(section), Mapping):
                raise ValueError(f"missing mapping section {section!r}")
        for section in ("preprocess", "measure", "viz"):
            if not self.raw[section]:
                raise ValueError(f"section {section!r} cannot be empty")
        for section, required in (
            ("io", REQUIRED_IO_KEYS),
            ("postprocess", REQUIRED_POSTPROCESS_KEYS),
            ("measure", REQUIRED_MEASURE_KEYS),
            ("viz", REQUIRED_VIZ_KEYS),
            ("runtime", REQUIRED_RUNTIME_KEYS),
        ):
            missing_section = required - set(self.raw[section])
            if missing_section:
                raise ValueError(
                    f"{section} config omits explicit parameter(s): {sorted(missing_section)}"
                )
        try:
            # Persist the normalized form so fingerprints and batch discovery agree.
            object.__setattr__(
                self,
                "raw",
                {
                    **dict(self.raw),
                    "io": {
                        **dict(self.raw["io"]),
                        "suffixes": list(normalize_suffixes(self.raw["io"]["suffixes"])),
                    },
                },
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"io.suffixes is invalid: {exc}") from exc
        preprocess = self.raw["preprocess"]
        missing_preprocess = REQUIRED_PREPROCESS_KEYS - set(preprocess)
        if missing_preprocess:
            raise ValueError(
                f"preprocess config omits explicit parameter(s): {sorted(missing_preprocess)}"
            )
        channel = preprocess["channel"]
        if not isinstance(channel, int) or isinstance(channel, bool) or channel < 0:
            raise ValueError("preprocess.channel must be a non-negative integer")
        for name, required in (
            ("normalize", REQUIRED_NORMALIZE_KEYS),
            ("rescale", REQUIRED_RESCALE_KEYS),
            ("denoise", REQUIRED_DENOISE_KEYS),
        ):
            subsection = preprocess[name]
            if not isinstance(subsection, Mapping):
                raise ValueError(f"preprocess.{name} must be a mapping")
            missing_subsection = required - set(subsection)
            if missing_subsection:
                raise ValueError(
                    f"preprocess.{name} omits explicit parameter(s): {sorted(missing_subsection)}"
                )
        postprocess = self.raw["postprocess"]
        unknown_postprocess = set(postprocess) - REQUIRED_POSTPROCESS_KEYS - OPTIONAL_POSTPROCESS_KEYS
        if unknown_postprocess:
            raise ValueError(
                f"postprocess config contains unknown parameter(s): {sorted(unknown_postprocess)}"
            )
        for name in ("min_volume_um3", "max_volume_um3", "min_area_um2", "max_area_um2"):
            value = postprocess.get(name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not np.isfinite(float(value))
                or float(value) < 0
            ):
                raise ValueError(
                    f"postprocess.{name} must be null or a non-negative finite number"
                )
        missing = REQUIRED_SEGMENT_KEYS - set(self.raw["segment"])
        if missing:
            raise ValueError(f"segment config omits explicit parameter(s): {sorted(missing)}")
        segment = self.raw["segment"]
        if segment["engine"] not in {"v3", "v4"}:
            raise ValueError("segment.engine must be v3 or v4")
        engine = segment["engine"]
        modes = {"volume_3d", "stitch_2d", "single_plane_2d", "max_projection_2d"}
        if segment["mode"] not in modes:
            raise ValueError(f"segment.mode must be one of {sorted(modes)}")
        if not bool(segment.get("tile", True)):
            raise ValueError(f"Cellpose {segment['engine']} always tiles; segment.tile must be true (tile=False is unsupported)")
        if segment["mode"] != "stitch_2d" and float(segment["stitch_threshold"]) != 0.0:
            raise ValueError(
                "segment.stitch_threshold must be 0.0 unless mode is stitch_2d"
            )
        if segment["mode"] == "stitch_2d":
            stitch = float(segment["stitch_threshold"])
            if not np.isfinite(stitch) or not 0.0 < stitch <= 1.0:
                raise ValueError(
                    "segment.stitch_threshold must satisfy 0 < stitch_threshold <= 1 "
                    "for stitch_2d (zero is rejected because plane-local IDs would collide)"
                )
        z_index = segment["z_index"]
        if segment["mode"] == "single_plane_2d":
            if not isinstance(z_index, int) or isinstance(z_index, bool) or z_index < 0:
                raise ValueError(
                    "segment.z_index must be a non-negative integer for single_plane_2d"
                )
        elif z_index is not None:
            raise ValueError("segment.z_index must be null unless mode is single_plane_2d")
        if segment["device"] not in {"cuda", "cpu", "auto"}:
            raise ValueError("segment.device must be cuda, cpu, or auto")
        diameter = segment["diameter_px"]
        if diameter is not None:
            if isinstance(diameter, bool) or not isinstance(diameter, (int, float)):
                raise ValueError("segment.diameter_px must be null (native size) or a finite positive number")
            if not np.isfinite(float(diameter)) or float(diameter) <= 0:
                raise ValueError("segment.diameter_px must be null (native size) or positive")
        if int(segment["min_size"]) < 0:
            raise ValueError("segment.min_size cannot be negative")
        if segment.get("compute_masks") is False:
            raise ValueError("segment.compute_masks must be true; CellQuant requires masks for measurement")
        channel_axis = segment.get("channel_axis", None)
        z_axis = segment.get("z_axis", None)
        # Adapter always supplies single-channel YX/ZYX; reject contradictory overrides.
        if channel_axis not in (None,):
            raise ValueError(
                "segment.channel_axis must be null; CellQuant owns channel selection via preprocess.channel"
            )
        # Modes that evaluate a Z stack need z_axis=0 (canonical ZYX). Normalize
        # null → 0 so hand-edited YAML cannot reach Cellpose as int(None).
        if segment["mode"] in {"volume_3d", "stitch_2d"}:
            if z_axis not in (None, 0):
                raise ValueError(
                    f"segment.z_axis must be null or 0 for {segment['mode']} (canonical ZYX)"
                )
            if z_axis is None:
                segment = {**dict(segment), "z_axis": 0}
                object.__setattr__(
                    self,
                    "raw",
                    {**dict(self.raw), "segment": segment},
                )
        elif z_axis not in (None, 0):
            raise ValueError(
                "segment.z_axis must be null (or 0, ignored) unless mode is volume_3d or stitch_2d"
            )
        if engine == "v4" and segment.get("rescale_factor") not in (None, 1, 1.0):
            # Installed Cellpose v4 resets rescale and derives it from diameter.
            raise ValueError(
                "segment.rescale_factor is ignored by Cellpose v4; leave it null/1 and set diameter_px instead"
            )
        model_hash = segment["model_sha256"]
        if not isinstance(model_hash, str) or len(model_hash) != 64 or any(
            character not in "0123456789abcdefABCDEF" for character in model_hash
        ):
            raise ValueError("segment.model_sha256 must be a 64-character SHA-256")
        seed = self.raw["runtime"].get("seed")
        if not isinstance(seed, int) or seed < 0:
            raise ValueError("runtime.seed must be a non-negative integer")

    def canonical_json(self) -> str:
        return json.dumps(self.raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


NATIVE_RUN_KIND = "native"
IMPORTED_RUN_KIND = "imported_labels"
REQUIRED_IMPORT_PROVENANCE_KEYS = {"origin", "engine", "model", "model_sha256", "settings"}


@dataclass(frozen=True)
class ImportedRunConfig:
    """Configuration of a run whose masks came from an external label TIFF.

    External masks cannot truthfully supply Cellpose engine/model settings, so
    this contract validates only what an import can honestly declare: how to
    reopen the source (``io``), the analysis grid the mask lives on
    (``analysis``), and nullable ``segmentation_provenance``. It is deliberately
    *not* a :class:`RunConfig`; segmentation execution paths must reject it.
    """

    raw: Mapping[str, Any]

    def __post_init__(self) -> None:
        if int(self.raw.get("schema_version", -1)) != 1:
            raise ValueError("imported configuration schema_version must be 1")
        if str(self.raw.get("run_kind") or "") != IMPORTED_RUN_KIND:
            raise ValueError(f"run_kind must be {IMPORTED_RUN_KIND!r}")
        for section in ("io", "analysis", "segmentation_provenance"):
            if not isinstance(self.raw.get(section), Mapping):
                raise ValueError(f"missing mapping section {section!r}")
        missing_io = REQUIRED_IO_KEYS - set(self.raw["io"])
        if missing_io:
            raise ValueError(f"io config omits explicit parameter(s): {sorted(missing_io)}")
        try:
            object.__setattr__(
                self,
                "raw",
                {
                    **dict(self.raw),
                    "io": {
                        **dict(self.raw["io"]),
                        "suffixes": list(normalize_suffixes(self.raw["io"]["suffixes"])),
                    },
                },
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"io.suffixes is invalid: {exc}") from exc
        provenance = self.raw["segmentation_provenance"]
        missing_provenance = REQUIRED_IMPORT_PROVENANCE_KEYS - set(provenance)
        if missing_provenance:
            raise ValueError(
                "segmentation_provenance omits explicit parameter(s): "
                f"{sorted(missing_provenance)}"
            )
        if not str(provenance.get("origin") or "").strip():
            raise ValueError("segmentation_provenance.origin is required")
        # Validate the declared grid with the shared analysis-context checks so
        # imported masks reconstruct exactly like native runs.
        from cellquant.analysis import analysis_context_from_dict

        analysis = dict(self.raw["analysis"])
        spacing = analysis.get("spacing_um")
        shape = analysis.get("shape_zyx")
        if not isinstance(spacing, Sequence) or len(tuple(spacing)) != 3:
            raise ValueError("analysis.spacing_um must be a three-element list")
        if not isinstance(shape, Sequence) or len(tuple(shape)) != 3:
            raise ValueError("analysis.shape_zyx must be a three-element list")
        analysis_context_from_dict(
            analysis,
            spacing_um=tuple(float(v) for v in spacing),
            shape_zyx=tuple(int(v) for v in shape),
        )

    @property
    def run_kind(self) -> str:
        return IMPORTED_RUN_KIND

    def canonical_json(self) -> str:
        return json.dumps(self.raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def _read_raw_config(path: str | Path) -> tuple[Path, Mapping[str, Any]]:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        raw = json.loads(text)
    else:
        import yaml

        raw = yaml.safe_load(text)
    if not isinstance(raw, Mapping):
        raise ValueError("configuration root must be a mapping")
    return path, raw


def read_run_kind(path: str | Path) -> str:
    """Read ``run_kind`` without validating either configuration contract."""

    _, raw = _read_raw_config(path)
    return str(raw.get("run_kind") or NATIVE_RUN_KIND)


def load_config(path: str | Path) -> RunConfig:
    """Load and validate a native segmentation configuration."""

    _, raw = _read_raw_config(path)
    kind = str(raw.get("run_kind") or NATIVE_RUN_KIND)
    if kind != NATIVE_RUN_KIND:
        raise ValueError(
            f"{path} declares run_kind {kind!r}; use load_run_config to load "
            "non-native runs. Native segmentation settings remain mandatory for "
            "actual segmentation."
        )
    return RunConfig(raw)


def load_imported_config(path: str | Path) -> ImportedRunConfig:
    """Load and validate an imported-labels configuration."""

    _, raw = _read_raw_config(path)
    return ImportedRunConfig(raw)


def load_run_config(path: str | Path) -> RunConfig | ImportedRunConfig:
    """Branch on ``run_kind`` before applying either validator.

    Native runs keep the existing loader unchanged; imported-label runs are
    validated against the imported contract instead of the segmentation one.
    """

    _, raw = _read_raw_config(path)
    kind = str(raw.get("run_kind") or NATIVE_RUN_KIND)
    if kind == IMPORTED_RUN_KIND:
        return ImportedRunConfig(raw)
    if kind != NATIVE_RUN_KIND:
        raise ValueError(f"unknown run_kind {kind!r} in {path}")
    return RunConfig(raw)


def reject_imported_config(config: Any, *, action: str = "segmentation") -> None:
    """Refuse imported-label configurations in segmentation execution paths.

    Native segmentation settings stay mandatory for actual segmentation. This
    only rejects imports; it deliberately does not tighten the duck-typed
    configuration mappings that internal stages accept.
    """

    raw = config.raw if hasattr(config, "raw") else config
    kind = ""
    if isinstance(raw, Mapping):
        kind = str(raw.get("run_kind") or "")
    if isinstance(config, ImportedRunConfig) or kind == IMPORTED_RUN_KIND:
        raise ValueError(
            f"{action} requires native Cellpose settings, but this run was created by "
            "importing external labels (run_kind=imported_labels). Re-run segmentation "
            "from the source image instead."
        )


def require_segmentation_config(config: Any, *, action: str = "segmentation") -> RunConfig:
    """Require a validated native :class:`RunConfig` for ``action``."""

    reject_imported_config(config, action=action)
    if not isinstance(config, RunConfig):
        raise TypeError(f"{action} requires a validated RunConfig")
    return config
