"""Compare segmentation modes and quantification thresholds on a set of images.

An experiment is a grid. Each *segmentation run* is one image, one channel to
segment, one method (classical, Cellpose 3, Cellpose-SAM) and one Z-stack mode.
Each run is then quantified with several *configurations*: a Cellpose
cell-probability threshold plus a positivity threshold for every channel.

Runs are independent. Each writes its own folder under ``units/`` and is skipped
when it is already done, so an experiment can be stopped, resumed, and split
across computers (for example, the slow 3D runs on a computer with more memory).
``collate`` merges every finished run into the tables in the output folder.

Source images are only read. Everything is written under the output folder.

    python -m cellquant.sweep run --input IMAGES --output RESULTS --engines classical
    python -m cellquant.sweep collate --output RESULTS
    python -m cellquant.sweep score --output RESULTS --hand-counts counts.csv
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import platform
import re
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

from cellquant import progress
from cellquant.__version__ import __version__
from cellquant.image import load_image
from cellquant.pipeline import process_image, reclassify_result, segment_channel
from cellquant.recipe import (
    BackgroundSpec,
    ClassificationSpec,
    MeasurementSpec,
    ObjectSetSpec,
    Recipe,
    RegionSpec,
    ReportSpec,
)
from cellquant.regions import isotropic_pixel_size

ENGINES = ("classical", "cellpose3", "cellpose4")
ALL_MODES = ("max_projection", "single_plane", "stitch_slices", "full_3d")
# Cellpose-SAM in 3D is too slow for a laptop without a GPU, so it is only run when asked.
SLOW_COMBINATIONS = frozenset({("cellpose4", "stitch_slices"), ("cellpose4", "full_3d")})
STATUS_FILE = "unit.json"

# Threshold levels, in intensity units above the image's own background.
DEFAULT_MARKER_SETS = {"lenient": 30.0, "strict": 120.0}
DEFAULT_CELLPROB_LEVELS = (-2.0, -1.0, 0.0, 1.0, 2.0)
DENSE_DELTAS = (5, 10, 20, 30, 45, 60, 80, 100, 130, 170, 220, 300, 400, 600, 800)
BACKGROUND_PERCENTILE = 25.0


# --- design ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Config:
    config_id: str
    cellprob: float
    marker_set: str
    delta: float  # counts above the image background, same for every channel


@dataclass(frozen=True)
class Design:
    cellprob_levels: tuple[float, ...] = DEFAULT_CELLPROB_LEVELS
    marker_sets: tuple[tuple[str, float], ...] = tuple(DEFAULT_MARKER_SETS.items())
    dense_deltas: tuple[float, ...] = tuple(float(v) for v in DENSE_DELTAS)
    default_cellprob: float = 0.0
    flow_threshold: float = 0.4
    min_size_px: int = 15
    min_area_um2: float = 5.0  # the same physical size floor for every method
    classical_sigma: float = 1.0
    classical_min_separation_um: float = 3.0
    background_percentile: float = BACKGROUND_PERCENTILE

    def configs(self) -> list[Config]:
        """The quantification configurations applied to every run: cell probability x marker set."""

        result = []
        for index, (cellprob, (name, delta)) in enumerate(product(self.cellprob_levels, self.marker_sets), start=1):
            result.append(Config(f"C{index:02d}", float(cellprob), name, float(delta)))
        return result

    def as_dict(self) -> dict:
        return {
            "cellprob_levels": list(self.cellprob_levels),
            "marker_sets": dict(self.marker_sets),
            "dense_deltas": list(self.dense_deltas),
            "flow_threshold": self.flow_threshold,
            "min_size_px": self.min_size_px,
            "min_area_um2": self.min_area_um2,
            "classical_sigma": self.classical_sigma,
            "classical_min_separation_um": self.classical_min_separation_um,
            "background_percentile": self.background_percentile,
            "configs": [config.__dict__ for config in self.configs()],
        }


@dataclass(frozen=True)
class ImageEntry:
    key: str
    path: Path
    relative_path: str


@dataclass(frozen=True)
class Unit:
    image: ImageEntry
    channel: int
    channel_name: str
    engine: str
    mode: str

    @property
    def unit_id(self) -> str:
        return f"{self.image.key}_{_slug(self.channel_name)}_{self.engine}_{self.mode}"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_") or "channel"


def channel_labels(loaded) -> tuple[str, ...]:
    """The file's channel names, or Channel 1, Channel 2, ... when it has none (or repeats one)."""

    names = tuple(str(name) for name in loaded.channel_names)
    if len(names) != loaded.n_channels or len({_slug(name) for name in names}) != len(names):
        return tuple(f"Channel {index + 1}" for index in range(loaded.n_channels))
    return names


# --- planning --------------------------------------------------------------------------------------


def find_images(input_dir: str | Path, suffixes: tuple[str, ...] = (".nd2",)) -> list[ImageEntry]:
    """Every matching file below ``input_dir``, in a stable order, named img01, img02, ..."""

    root = Path(input_dir)
    files = sorted(
        (path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in suffixes),
        key=lambda path: path.relative_to(root).as_posix().lower(),
    )
    width = max(2, len(str(len(files))))
    return [
        ImageEntry(f"img{index:0{width}d}", path, path.relative_to(root).as_posix())
        for index, path in enumerate(files, start=1)
    ]


def plan_units(
    images: list[ImageEntry],
    channel_names: tuple[str, ...],
    *,
    engines: tuple[str, ...] = ENGINES,
    modes: tuple[str, ...] = ALL_MODES,
    channels: tuple[int, ...] | None = None,
    include_slow: bool = False,
) -> list[Unit]:
    chosen = tuple(range(len(channel_names))) if channels is None else tuple(channels)
    units = []
    for image, channel, engine, mode in product(images, chosen, engines, modes):
        if (engine, mode) in SLOW_COMBINATIONS and not include_slow:
            continue
        units.append(Unit(image, channel, channel_names[channel], engine, mode))
    return units


# --- one segmentation run --------------------------------------------------------------------------


def background_level(plane: np.ndarray, percentile: float = BACKGROUND_PERCENTILE) -> float:
    """The image's own background: a low percentile of its pixels in one channel."""

    return float(np.percentile(np.asarray(plane), percentile))


def segmentation_parameters(engine: str, design: Design, pixel_size: float | None, cellprob: float | None) -> dict:
    if engine == "classical":
        distance = design.classical_min_separation_um / pixel_size if pixel_size else 5.0
        return {
            "sigma": design.classical_sigma,
            "threshold_method": "otsu",
            "fill_holes": True,
            "use_watershed": True,
            "watershed_min_distance_px": float(max(2.0, round(distance, 1))),
            "min_area_um2": design.min_area_um2,
        }
    return {
        "engine": engine,
        "flow_threshold": design.flow_threshold,
        "cellprob_threshold": float(cellprob if cellprob is not None else design.default_cellprob),
        "min_size": design.min_size_px,
        "min_area_um2": design.min_area_um2,
        "random_seed": 0,
        "gpu": False,
    }


def _report_expressions(ids: list[str]) -> list[tuple[str, str]]:
    """Name and expression for each count: every marker alone, every pair, and all together."""

    names = {identifier: identifier.removesuffix("_pos") for identifier in ids}
    rows = [(names[i], i) for i in ids]
    for first in range(len(ids)):
        for second in range(first + 1, len(ids)):
            rows.append((f"{names[ids[first]]}_and_{names[ids[second]]}", f"{ids[first]} AND {ids[second]}"))
    if len(ids) > 2:
        rows.append(("all_markers", " AND ".join(ids)))
    return rows


def build_recipe(
    unit: Unit,
    design: Design,
    channel_names: tuple[str, ...],
    backgrounds: dict[int, float],
    pixel_size: float | None,
    cellprob: float | None,
    delta: float,
) -> Recipe:
    """The CellQuant recipe for one run at one threshold setting."""

    slugs = [_slug(name) for name in channel_names]
    measurements = [
        MeasurementSpec(
            id=f"{slug}_mean",
            name=f"{name} mean above background",
            channel=index,
            region=RegionSpec(type="object"),
            statistic="mean",
            background=BackgroundSpec(type="global", value=backgrounds[index]),
        )
        for index, (slug, name) in enumerate(zip(slugs, channel_names, strict=True))
    ]
    classifications = [
        ClassificationSpec(id=f"{slug}_pos", name=f"{name} positive", measurement=f"{slug}_mean", threshold=float(delta))
        for slug, name in zip(slugs, channel_names, strict=True)
    ]
    reports = [
        ReportSpec(numerator=expression, denominator="all_objects")
        for _name, expression in _report_expressions([item.id for item in classifications])
    ]
    algorithm = "classical" if unit.engine == "classical" else "cellpose"
    return Recipe(
        recipe_name=f"sweep {unit.unit_id}",
        z_stack=unit.mode,
        object_set=ObjectSetSpec(
            segmentation_channel=unit.channel,
            algorithm=algorithm,
            parameters=segmentation_parameters(unit.engine, design, pixel_size, cellprob),
        ),
        measurements=measurements,
        classifications=classifications,
        reports=reports,
    )


def _with_delta(recipe: Recipe, delta: float) -> Recipe:
    return recipe.model_copy(
        update={"classifications": [item.model_copy(update={"threshold": float(delta)}) for item in recipe.classifications]}
    )


def _count_row(reports: pd.DataFrame, names: list[str]) -> dict:
    """Counts from a CellQuant report table, keyed by the names in ``_report_expressions``."""

    counts = reports["count"].tolist()
    row = {"n_objects": int(reports["denominator_count"].iloc[0]) if len(reports) else 0}
    for name, count in zip(names, counts, strict=True):
        row[f"n_{name}"] = int(count)
        row[f"pct_{name}"] = round(100.0 * int(count) / row["n_objects"], 3) if row["n_objects"] else float("nan")
    row["n_unmeasured"] = int(reports["n_unmeasured"].iloc[0]) if len(reports) else 0
    return row


def curve_rows(objects: pd.DataFrame, mean_columns: dict[str, str], deltas: tuple[float, ...], cellprob: float) -> list[dict]:
    """How many objects are positive at each threshold, for every channel (same rule as the classification)."""

    measured = objects[~objects["unmeasured"].fillna(False).astype(bool)] if "unmeasured" in objects else objects
    rows = []
    for name, column in mean_columns.items():
        values = measured[column].to_numpy(dtype=float)
        for delta in deltas:
            rows.append(
                {
                    "cellprob": cellprob,
                    "channel": name,
                    "delta": float(delta),
                    "n_objects": int(len(values)),
                    "n_positive": int(np.count_nonzero(values > delta)),
                }
            )
    return rows


def counts_from_objects(objects: pd.DataFrame, channels: list[str], deltas: dict[str, float]) -> dict:
    """The same counts CellQuant reports, computed from a saved object table.

    ``objects`` has one ``mean_<channel>`` column per channel (mean above background). An
    object is positive when its value is greater than the threshold; an object with a missing
    value in any channel is left out of every count. Used to re-threshold a finished
    experiment without segmenting again, and checked against the pipeline in the tests.
    """

    frame = objects[[f"mean_{name}" for name in channels]].to_numpy(dtype=float)
    measured = np.isfinite(frame).all(axis=1)
    if "unmeasured" in objects:
        measured &= ~objects["unmeasured"].fillna(False).astype(bool).to_numpy()
    positive = {name: frame[:, index] > float(deltas[name]) for index, name in enumerate(channels)}
    total = int(measured.sum())
    expressions = _report_expressions([f"{name}_pos" for name in channels])
    row = {"n_objects": total}
    for name, expression in expressions:
        mask = np.ones(len(frame), dtype=bool)
        for part in expression.split(" AND "):
            mask &= positive[part.removesuffix("_pos")]
        count = int((mask & measured).sum())
        row[f"n_{name}"] = count
        row[f"pct_{name}"] = round(100.0 * count / total, 3) if total else float("nan")
    row["n_unmeasured"] = int((~measured).sum())
    return row



_OBJECT_COLUMNS = ["object_id", "centroid_x", "centroid_y", "centroid_z", "area", "volume", "z_slices", "z_first", "z_last", "z_flag"]


def _object_table(objects: pd.DataFrame, mean_columns: dict[str, str], cellprob: float) -> pd.DataFrame:
    keep = [column for column in _OBJECT_COLUMNS if column in objects.columns]
    table = objects[keep].copy()
    for name, column in mean_columns.items():
        table[f"mean_{name}"] = objects[column].to_numpy(dtype=float)
    table["unmeasured"] = objects["unmeasured"].fillna(False).astype(bool).to_numpy() if "unmeasured" in objects else False
    table.insert(0, "cellprob", cellprob)
    return table


def _engine_matches(engine: str) -> str | None:
    """None when this environment can run the engine, else the reason it cannot."""

    if engine == "classical":
        return None
    from cellquant.engines import cellpose_engine

    installed = cellpose_engine()
    if not installed.installed:
        return "Cellpose is not installed in this environment"
    if installed.key != engine:
        return f"this environment has {installed.key} ({installed.version}), not {engine}"
    return None


def run_unit(
    unit: Unit,
    design: Design,
    output_dir: str | Path,
    *,
    log=print,
    force: bool = False,
) -> dict:
    """Run one segmentation run and quantify it at every configuration. Skips finished runs."""

    from cellquant import segmentation

    folder = Path(output_dir) / "units" / unit.unit_id
    status_path = folder / STATUS_FILE
    if status_path.exists() and not force:
        previous = json.loads(status_path.read_text(encoding="utf-8"))
        if previous.get("status") == "done":
            return previous
    reason = _engine_matches(unit.engine)
    if reason:
        return {"unit_id": unit.unit_id, "status": "not_run", "reason": reason}
    folder.mkdir(parents=True, exist_ok=True)
    started = time.time()
    record: dict = {
        "unit_id": unit.unit_id,
        "image_key": unit.image.key,
        "image_path": unit.image.relative_path,
        "segmentation_channel": unit.channel,
        "segmentation_channel_name": unit.channel_name,
        "engine": unit.engine,
        "mode": unit.mode,
        "cellquant_version": __version__,
        "host": platform.node(),
        "started": datetime.fromtimestamp(started, timezone.utc).isoformat(),
    }
    try:
        loaded = load_image(unit.image.path, z_mode=unit.mode)
        pixel_size = isotropic_pixel_size(loaded.pixel_size_x, loaded.pixel_size_y)
        backgrounds = {
            index: background_level(loaded.data[index], design.background_percentile) for index in range(loaded.n_channels)
        }
        names = channel_labels(loaded)
        slugs = {name: _slug(name) for name in names}
        mean_columns = {slugs[name]: f"{slugs[name]}_mean" for name in names}
        expression_names = [name for name, _expression in _report_expressions([f"{slugs[n]}_pos" for n in names])]
        applies = unit.engine != "classical"
        levels = list(design.cellprob_levels) if applies else [None]
        configs = design.configs()
        objects_tables, curves, config_rows, per_level = [], [], [], {}
        default_labels = None
        with segmentation.keep_network_output():
            for level in levels:
                label = f"cellprob {level:g}" if level is not None else "classical"
                progress.update(f"{unit.unit_id}: {label}")
                base = build_recipe(unit, design, names, backgrounds, pixel_size, level, design.marker_sets[0][1])
                details: dict = {}
                labels = segment_channel(loaded, base, details, record_timing=False)
                result = process_image(
                    loaded,
                    base,
                    sample_name=unit.image.key,
                    image_id=unit.unit_id,
                    filename=Path(unit.image.relative_path).name,
                    automated_labels=labels,
                    segmentation_details=details,
                )
                shown = float(level) if level is not None else float("nan")
                per_level[label] = {
                    "n_objects": int(result.qc.n_objects),
                    "segmentation_seconds": round(float(details.get("segmentation_seconds") or 0.0), 2),
                    "warnings": list(result.qc.warnings),
                }
                objects_tables.append(_object_table(result.objects, mean_columns, shown))
                curves.extend(curve_rows(result.objects, mean_columns, design.dense_deltas, shown))
                if level is None or float(level) == design.default_cellprob:
                    default_labels = result.labels
                for config in configs:
                    if applies and float(level) != config.cellprob:
                        continue
                    reclassified = reclassify_result(result, _with_delta(base, config.delta))
                    row = {
                        "unit_id": unit.unit_id,
                        "config_id": config.config_id,
                        "cellprob": config.cellprob if applies else float("nan"),
                        "marker_set": config.marker_set,
                        "delta": config.delta,
                    }
                    row.update(_count_row(reclassified.reports, expression_names))
                    config_rows.append(row)
            segmentation.clear_network_output()
        if not applies:
            # Cell probability does not apply to the classical method: every cell-probability
            # setting gives the same objects, so each marker set is reported once and copied.
            by_marker = {row["marker_set"]: row for row in config_rows}
            config_rows = [
                {**by_marker[config.marker_set], "config_id": config.config_id, "cellprob": config.cellprob}
                for config in configs
            ]
            for row in config_rows:
                row["cellprob_applies"] = False
        else:
            for row in config_rows:
                row["cellprob_applies"] = True
        pd.concat(objects_tables, ignore_index=True).to_csv(folder / "objects.csv.gz", index=False)
        pd.DataFrame(config_rows).to_csv(folder / "configs.csv", index=False)
        pd.DataFrame(curves).to_csv(folder / "curves.csv", index=False)
        if default_labels is not None:
            dtype = np.uint16 if int(default_labels.max(initial=0)) < 65535 else np.int32
            np.savez_compressed(folder / "labels.npz", labels=np.asarray(default_labels).astype(dtype))
        record.update(
            {
                "status": "done",
                "image_shape_zyx": list(loaded.spatial_shape),
                "z_planes_in_file": loaded.z_planes,
                "z_index_used": loaded.z_index,
                "pixel_size_xy_um": pixel_size,
                "pixel_size_z_um": loaded.pixel_size_z,
                "objective": loaded.objective,
                "channel_names": list(names),
                "background_by_channel": {slugs[name]: backgrounds[i] for i, name in enumerate(names)},
                "engine_details": _engine_details(unit.engine),
                "levels": per_level,
                "segmentation_seconds_total": round(sum(v["segmentation_seconds"] for v in per_level.values()), 2),
                "threads": _thread_count(),
            }
        )
    except progress.AnalysisCancelled:
        raise
    except Exception as exc:  # noqa: BLE001 - one failed run must not stop the experiment
        record.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()})
        log(f"  FAILED {unit.unit_id}: {exc}")
    record["finished"] = datetime.now(timezone.utc).isoformat()
    record["wall_seconds"] = round(time.time() - started, 2)
    temporary = status_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    temporary.replace(status_path)  # readers never see a half-written file
    return record


def _engine_details(engine: str) -> dict:
    if engine == "classical":
        return {"algorithm": "classical"}
    from cellquant.segmentation import engine_signature

    return engine_signature("cellpose", {"engine": engine})


def _thread_count() -> int | None:
    try:
        import torch

        return int(torch.get_num_threads())
    except Exception:  # noqa: BLE001 - PyTorch is optional
        return None


# --- running many ----------------------------------------------------------------------------------


def run_units(units: list[Unit], design: Design, output_dir: str | Path, *, log=print, force: bool = False) -> list[dict]:
    records = []
    started = time.time()
    for index, unit in enumerate(units, start=1):
        one = time.time()
        record = run_unit(unit, design, output_dir, log=log, force=force)
        records.append(record)
        state = record.get("status")
        note = record.get("reason") or ""
        log(
            f"[{index}/{len(units)}] {unit.unit_id}: {state}"
            + (f" ({note})" if note else "")
            + f" {time.time() - one:.0f}s (total {time.time() - started:.0f}s)"
        )
    return records


# --- collate ---------------------------------------------------------------------------------------

DOMAIN_OF_MODE = {"max_projection": "projection", "single_plane": "plane", "stitch_slices": "stack", "full_3d": "stack"}
# Which method's objects set the natural thresholds, in order of preference.
REFERENCE_ENGINES = ("cellpose4", "cellpose3", "classical")


@dataclass(frozen=True)
class Quantification:
    """How positive nuclei are counted, in terms of each channel's natural threshold.

    The natural threshold of a channel is where the intensities of nuclei split into a dim and a
    bright group (``natural_thresholds``). The ten official configurations are every cell-probability
    level with a lenient and a strict multiple of it. The extended table adds the whole ladder.
    """

    cellprob_levels: tuple[float, ...] = DEFAULT_CELLPROB_LEVELS
    official: tuple[tuple[str, float], ...] = (("lenient", 0.7), ("strict", 1.4))
    ladder: tuple[float, ...] = (0.5, 0.7, 1.0, 1.4, 2.0)
    reference_cellprob: float = 0.0

    def official_configs(self) -> list[tuple[str, float, str, float]]:
        return [
            (f"C{index:02d}", float(cellprob), name, float(multiplier))
            for index, (cellprob, (name, multiplier)) in enumerate(product(self.cellprob_levels, self.official), start=1)
        ]

    def extended_configs(self) -> list[tuple[str, float, str, float]]:
        return [
            (f"X{index:02d}", float(cellprob), f"{multiplier:g}x", float(multiplier))
            for index, (cellprob, multiplier) in enumerate(product(self.cellprob_levels, self.ladder), start=1)
        ]

    def as_dict(self) -> dict:
        return {
            "cellprob_levels": list(self.cellprob_levels),
            "official_marker_sets": dict(self.official),
            "extended_ladder": list(self.ladder),
            "official_configs": [dict(zip(("config_id", "cellprob", "marker_set", "multiplier"), c)) for c in self.official_configs()],
        }


def _log_otsu(values: np.ndarray) -> float | None:
    """The intensity that best separates dim from bright nuclei, found on a log scale."""

    from skimage.filters import threshold_otsu

    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) < 50 or float(values.max()) <= float(values.min()):
        return None
    return float(10 ** threshold_otsu(np.log10(np.clip(values, 1.0, None))))


def natural_thresholds(root: Path, records: list[dict], quantification: Quantification, override: dict[str, float] | None = None) -> pd.DataFrame:
    """A threshold for every channel in every Z domain (projection, one slice, stack).

    Intensities differ between a projection, a single slice and a stack, so each domain gets its
    own. They come from the nuclei that the best available method found at the default cell
    probability, pooled over all images and all segmented channels.
    """

    rows = []
    for domain in ("projection", "plane", "stack"):
        for engine in REFERENCE_ENGINES:
            chosen = [
                r for r in records
                if r.get("status") == "done" and r["engine"] == engine and DOMAIN_OF_MODE[r["mode"]] == domain
            ]
            if chosen:
                break
        else:
            continue
        frames = []
        for record in chosen:
            objects = pd.read_csv(root / "units" / record["unit_id"] / "objects.csv.gz")
            if engine != "classical":
                objects = objects[np.isclose(objects["cellprob"], quantification.reference_cellprob)]
            frames.append(objects)
        pooled = pd.concat(frames, ignore_index=True)
        for name in chosen[0]["channel_names"]:
            slug = _slug(name)
            if override and slug in override:
                value, how = float(override[slug]), "set by hand"
            else:
                value, how = _log_otsu(pooled[f"mean_{slug}"].to_numpy()), "log-Otsu of nuclear mean intensity"
                if value is None:
                    value, how = 100.0, "default (too few nuclei)"
            rows.append(
                {
                    "domain": domain,
                    "channel": slug,
                    "natural_threshold": round(value, 1),
                    "how": how,
                    "reference_engine": engine,
                    "n_units": len(chosen),
                    "n_nuclei": int(len(pooled)),
                }
            )
    return pd.DataFrame(rows)


def counts_for_unit(
    unit_folder: Path,
    record: dict,
    configs: list[tuple[str, float, str, float]],
    natural: pd.DataFrame,
) -> pd.DataFrame:
    """Counts at each configuration, from a run's saved object table."""

    objects = pd.read_csv(unit_folder / "objects.csv.gz")
    channels = [_slug(name) for name in record["channel_names"]]
    domain = DOMAIN_OF_MODE[record["mode"]]
    limits = natural[natural["domain"] == domain].set_index("channel")["natural_threshold"].to_dict()
    applies = record["engine"] != "classical"
    rows = []
    for config_id, cellprob, name, multiplier in configs:
        subset = objects[np.isclose(objects["cellprob"], cellprob)] if applies else objects
        deltas = {channel: multiplier * float(limits[channel]) for channel in channels}
        row = {
            "unit_id": record["unit_id"],
            "config_id": config_id,
            "cellprob": cellprob,
            "cellprob_applies": applies,
            "marker_set": name,
            "multiplier": multiplier,
        }
        row.update({f"delta_{channel}": round(deltas[channel], 1) for channel in channels})
        row.update(counts_from_objects(subset, channels, deltas))
        rows.append(row)
    return pd.DataFrame(rows)


def _matches_pipeline(unit_folder: Path, record: dict) -> bool | None:
    """Do counts from the saved object table equal the counts CellQuant made while running?"""

    path = unit_folder / "configs.csv"
    if not path.exists():
        return None
    made = pd.read_csv(path)
    objects = pd.read_csv(unit_folder / "objects.csv.gz")
    channels = [_slug(name) for name in record["channel_names"]]
    applies = record["engine"] != "classical"
    for _index, row in made.iterrows():
        if applies and not np.isclose(row["cellprob"], objects["cellprob"]).any():
            continue
        subset = objects[np.isclose(objects["cellprob"], row["cellprob"])] if applies else objects
        again = counts_from_objects(subset, channels, {channel: float(row["delta"]) for channel in channels})
        for key, value in again.items():
            if key in made.columns and not (pd.isna(row[key]) and pd.isna(value)) and abs(float(row[key]) - float(value)) > 1e-6:
                return False
    return True


def collate(
    output_dir: str | Path,
    *,
    quantification: Quantification | None = None,
    natural: dict[str, float] | None = None,
) -> dict[str, Path]:
    """Merge every finished run into the tables in the output folder.

    Counts come from each run's saved object table, so thresholds can be changed at any time
    without segmenting again. ``natural`` sets a channel's natural threshold by hand
    (``{"far_red": 300}``); otherwise it is found from the data.
    """

    root = Path(output_dir)
    quant = quantification or Quantification()
    records = []
    for path in sorted((root / "units").glob(f"*/{STATUS_FILE}")):
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue  # being written by another process; the next collate picks it up
    written: dict[str, Path] = {}
    manifests, curves = [], []
    done = [record for record in records if record.get("status") == "done"]
    for record in records:
        flat = {key: value for key, value in record.items() if key not in {"levels", "background_by_channel", "engine_details", "traceback", "channel_names"}}
        details = record.get("engine_details") or {}
        flat["engine_version"] = details.get("cellpose_version", "")
        flat["model"] = details.get("model", "")
        levels = record.get("levels") or {}
        flat["n_objects_default"] = next((v["n_objects"] for k, v in levels.items() if k in ("classical", "cellprob 0")), None)
        if record.get("status") == "done":
            folder = root / "units" / record["unit_id"]
            flat["counts_match_pipeline"] = _matches_pipeline(folder, record)
            curve = pd.read_csv(folder / "curves.csv")
            curve.insert(0, "unit_id", record["unit_id"])
            curves.append(curve)
        manifests.append(flat)
    if manifests:
        written["runs_manifest"] = _write(root / "runs_manifest.csv", pd.DataFrame(manifests))
    if not done:
        return written
    thresholds = natural_thresholds(root, done, quant, natural)
    written["natural_thresholds"] = _write(root / "natural_thresholds.csv", thresholds)
    lead = ["image_key", "image_path", "segmentation_channel_name", "engine", "mode", "config_id", "cellprob", "cellprob_applies", "marker_set", "multiplier"]
    for name, configs in (("results_long", quant.official_configs()), ("results_extended", quant.extended_configs())):
        frames = []
        for record in done:
            table = counts_for_unit(root / "units" / record["unit_id"], record, configs, thresholds)
            for column in ("image_key", "image_path", "engine", "mode"):
                table[column] = record[column]
            table["segmentation_channel_name"] = record["segmentation_channel_name"]
            frames.append(table)
        table = pd.concat(frames, ignore_index=True)
        table = table[lead + [c for c in table.columns if c not in lead]]
        written[name] = _write(root / f"{name}.csv", table)
    written["threshold_curves"] = _write(root / "threshold_curves.csv", pd.concat(curves, ignore_index=True))
    written["quantification"] = root / "quantification.json"
    written["quantification"].write_text(json.dumps(quant.as_dict(), indent=2), encoding="utf-8")
    return written


def _write(path: Path, frame: pd.DataFrame) -> Path:
    frame.to_csv(path, index=False)
    return path


# --- a folder to hand over ---------------------------------------------------------------------------------


_BUNDLE_TABLES = (
    "images.csv",
    "hand_counts_template.csv",
    "design.json",
    "quantification.json",
    "runs_manifest.csv",
    "natural_thresholds.csv",
    "results_long.csv",
    "results_extended.csv",
    "threshold_curves.csv",
)


def export_bundle(output_dir: str | Path, destination: str | Path) -> Path:
    """Copy the tables and report, and gather the per-run files into a few large ones.

    ``objects/objects_<engine>_<mode>.csv.gz`` holds every nucleus of every run with that method
    (column ``unit_id`` says which run); ``labels/labels_<engine>_<mode>_<channel>.npz`` holds the
    label images at the default cell probability, one array per run, named by ``unit_id``.
    """

    import shutil

    root, dest = Path(output_dir), Path(destination)
    (dest / "objects").mkdir(parents=True, exist_ok=True)
    (dest / "labels").mkdir(parents=True, exist_ok=True)
    for name in _BUNDLE_TABLES:
        if (root / name).exists():
            shutil.copy2(root / name, dest / name)
    if (root / "report").exists():
        shutil.copytree(root / "report", dest / "report", dirs_exist_ok=True)
    if (dest / "design.json").exists():
        design = json.loads((dest / "design.json").read_text(encoding="utf-8"))
        design["note"] = (
            "marker_sets and configs are the fixed thresholds used while segmenting, as a check that the saved "
            "tables reproduce CellQuant's own counts. The 10 official configurations are in quantification.json."
        )
        (dest / "design.json").write_text(json.dumps(design, indent=2), encoding="utf-8")
    records = []
    for path in sorted((root / "units").glob(f"*/{STATUS_FILE}")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("status") == "done":
            records.append(record)
    by_method: dict[tuple[str, str], list[dict]] = {}
    for record in records:
        by_method.setdefault((record["engine"], record["mode"]), []).append(record)
    for (engine, mode), items in sorted(by_method.items()):
        frames = []
        for record in items:
            table = pd.read_csv(root / "units" / record["unit_id"] / "objects.csv.gz")
            table.insert(0, "segmentation_channel", record["segmentation_channel_name"])
            table.insert(0, "image_key", record["image_key"])
            table.insert(0, "unit_id", record["unit_id"])
            frames.append(table)
        pd.concat(frames, ignore_index=True).to_csv(dest / "objects" / f"objects_{engine}_{mode}.csv.gz", index=False)
        by_channel: dict[str, dict[str, np.ndarray]] = {}
        for record in items:
            labels = root / "units" / record["unit_id"] / "labels.npz"
            if labels.exists():
                by_channel.setdefault(_slug(record["segmentation_channel_name"]), {})[record["unit_id"]] = np.load(labels)["labels"]
        for channel, arrays in by_channel.items():
            np.savez_compressed(dest / "labels" / f"labels_{engine}_{mode}_{channel}.npz", **arrays)
    if (root / "runs_manifest.csv").exists() and (root / "images.csv").exists():
        from cellquant.sweep_report import write_readme

        write_readme(root, dest)
    return dest


# --- images table and hand-count template --------------------------------------------------------------


def write_image_tables(images: list[ImageEntry], output_dir: str | Path) -> None:
    """``images.csv`` (what is in each file) and ``hand_counts_template.csv`` (to fill in by hand)."""

    from cellquant.image import inspect_image

    root = Path(output_dir)
    rows = []
    slugs: list[str] = []
    for entry in images:
        info = inspect_image(entry.path)[0]
        parts = Path(entry.relative_path).parts
        names = tuple(info.channel_names)
        if len(names) != info.n_channels or len({_slug(name) for name in names}) != len(names):
            names = tuple(f"Channel {index + 1}" for index in range(info.n_channels))
        slugs = slugs or [_slug(name) for name in names]
        rows.append(
            {
                "image_key": entry.key,
                "relative_path": entry.relative_path,
                "folder": "/".join(parts[:-1]),
                "file": parts[-1],
                "channels": ", ".join(names),
                "z_planes": info.z_planes,
                "pixel_size_xy_um": info.pixel_size_x,
                "pixel_size_z_um": info.pixel_size_z,
                "objective": info.objective,
                "shape": f"{info.n_channels} channels x {info.height} x {info.width}",
                "bit_depth": info.dtype,
            }
        )
    frame = pd.DataFrame(rows)
    root.mkdir(parents=True, exist_ok=True)
    frame.to_csv(root / "images.csv", index=False)
    template = frame[["image_key", "relative_path"]].copy()
    template["total_nuclei"] = ""
    for slug in slugs:
        template[f"{slug}_positive"] = ""
    for slug in slugs:
        template[f"{slug}_percent"] = ""
    template.to_csv(root / "hand_counts_template.csv", index=False)


# --- scoring against hand counts ------------------------------------------------------------------------

def _hand_columns(results: pd.DataFrame) -> dict[str, str]:
    """Which hand-count column matches which result column.

    ``total_nuclei`` is ``n_objects``; ``<channel>_positive`` is ``n_<channel>``; ``<a>_and_<b>_positive``
    is ``n_<a>_and_<b>``; ``<channel>_percent`` is ``pct_<channel>`` (percent of all nuclei).
    """

    mapping = {"total_nuclei": "n_objects"}
    for column in results.columns:
        if column.startswith("n_") and column not in ("n_objects", "n_unmeasured"):
            mapping[f"{column[2:]}_positive"] = column
        elif column.startswith("pct_"):
            mapping[f"{column[4:]}_percent"] = column
    return mapping


def score(output_dir: str | Path, hand_counts: str | Path, *, table: str = "results_long") -> dict[str, Path]:
    """Compare every run and configuration with hand counts (one row per image, blanks skipped)."""

    root = Path(output_dir)
    results = pd.read_csv(root / f"{table}.csv")
    hand = pd.read_csv(hand_counts)
    if "image_key" not in hand.columns:
        raise ValueError("The hand-count file needs an image_key column (see hand_counts_template.csv).")
    mapping = _hand_columns(results)
    columns = [name for name in mapping if name in hand.columns and hand[name].notna().any()]
    if not columns:
        raise ValueError(
            "The hand-count file has no filled-in counts. Use the column names in hand_counts_template.csv "
            "(total_nuclei, <channel>_positive, <channel>_percent)."
        )
    merged = results.merge(hand[["image_key", *columns]], on="image_key", how="inner", suffixes=("", "_hand"))
    if merged.empty:
        raise ValueError("None of the image_key values in the hand-count file match this experiment.")
    keys = ["segmentation_channel_name", "engine", "mode", "config_id", "cellprob", "marker_set", "multiplier"]
    counts = [name for name in columns if not name.endswith("_percent")]
    percents = [name for name in columns if name.endswith("_percent")]
    for name in columns:
        merged[f"err_{name}"] = merged[mapping[name]] - merged[name]
        merged[f"abserr_{name}"] = merged[f"err_{name}"].abs()
    for name in counts:
        merged[f"relerr_{name}"] = merged[f"abserr_{name}"] / merged[name].where(merged[name] > 0)
    suffix = "" if table == "results_long" else "_extended"
    per_image = _write(root / f"score_by_image{suffix}.csv", merged)
    grouped = merged.groupby(keys, dropna=False)
    value_columns = [f"abserr_{n}" for n in columns] + [f"relerr_{n}" for n in counts]
    summary = grouped[value_columns].mean().reset_index()
    summary["n_images"] = grouped.size().to_numpy()
    if counts:
        summary["mean_relative_error"] = summary[[f"relerr_{n}" for n in counts]].mean(axis=1)
    if percents:
        summary["mean_error_percentage_points"] = summary[[f"abserr_{n}" for n in percents]].mean(axis=1)
    rank_by = "mean_relative_error" if counts else "mean_error_percentage_points"
    summary = summary.sort_values(rank_by).reset_index(drop=True)
    ranked = _write(root / f"score_by_configuration{suffix}.csv", summary)
    best = summary.groupby(["segmentation_channel_name", "engine", "mode"], dropna=False).head(1)
    return {"by_image": per_image, "by_configuration": ranked, "best_per_mode": _write(root / f"score_best_per_mode{suffix}.csv", best)}


# --- command line ---------------------------------------------------------------------------------------


def _csv_ints(text: str | None) -> tuple[int, ...] | None:
    return None if not text else tuple(int(part) for part in text.split(",") if part.strip())


def _csv_words(text: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    return default if not text else tuple(part.strip() for part in text.split(",") if part.strip())


def _configure_threads(threads: int | None) -> None:
    if not threads:
        return
    os.environ.setdefault("OMP_NUM_THREADS", str(threads))
    try:
        import torch

        torch.set_num_threads(int(threads))
    except Exception:  # noqa: BLE001 - PyTorch is optional
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cellquant.sweep", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run segmentation runs and quantify them")
    run.add_argument("--input", required=True, help="folder of images (searched recursively)")
    run.add_argument("--output", required=True, help="results folder (must not be inside the input folder)")
    run.add_argument("--engines", help=f"comma list from {', '.join(ENGINES)} (default: all)")
    run.add_argument("--modes", help=f"comma list from {', '.join(ALL_MODES)} (default: all)")
    run.add_argument("--channels", help="channel numbers to segment, from 0 (default: every channel)")
    run.add_argument("--images", help="image numbers to run, from 1 (default: all)")
    run.add_argument("--types", default="nd2", help="file types: nd2, tiff, or nd2,tiff")
    run.add_argument("--include-cellpose4-3d", action="store_true", help="also run Cellpose-SAM in 3D (very slow without a GPU)")
    run.add_argument("--threads", type=int, help="CPU threads for PyTorch")
    run.add_argument("--force", action="store_true", help="redo runs that are already finished")
    run.add_argument("--dry-run", action="store_true", help="list the runs and stop")
    for name in ("collate", "score", "report", "export"):
        other = sub.add_parser(name)
        other.add_argument("--output", required=True)
        if name == "export":
            other.add_argument("--to", required=True, help="folder to write the hand-over copy into")
        if name == "collate":
            other.add_argument("--natural", help="set natural thresholds by hand, e.g. green=350,red=270,far_red=300")
            other.add_argument("--lenient", type=float, default=0.7, help="lenient multiple of the natural threshold")
            other.add_argument("--strict", type=float, default=1.4, help="strict multiple of the natural threshold")
        if name == "score":
            other.add_argument("--hand-counts", required=True)
            other.add_argument("--extended", action="store_true", help="score the extended table (all multiples)")
    args = parser.parse_args(argv)

    if args.command == "collate":
        by_hand = None
        if args.natural:
            by_hand = {part.split("=")[0].strip(): float(part.split("=")[1]) for part in args.natural.split(",")}
        quantification = Quantification(official=(("lenient", args.lenient), ("strict", args.strict)))
        for name, path in collate(args.output, quantification=quantification, natural=by_hand).items():
            print(f"{name}: {path}")
        return 0
    if args.command == "export":
        print(f"written: {export_bundle(args.output, args.to)}")
        return 0
    if args.command == "report":
        from cellquant.sweep_report import build_report

        for name, path in build_report(args.output).items():
            print(f"{name}: {path}")
        return 0
    if args.command == "score":
        table = "results_extended" if args.extended else "results_long"
        for name, path in score(args.output, args.hand_counts, table=table).items():
            print(f"{name}: {path}")
        return 0

    input_dir, output_dir = Path(args.input), Path(args.output)
    try:
        output_dir.resolve().relative_to(input_dir.resolve())
    except ValueError:
        pass
    else:
        print("The results folder is inside the image folder. Choose a different results folder.", file=sys.stderr)
        return 2
    _configure_threads(args.threads)
    suffixes = tuple(
        suffix for kind in _csv_words(args.types, ("nd2",)) for suffix in ((".nd2",) if kind == "nd2" else (".tif", ".tiff"))
    )
    images = find_images(input_dir, suffixes)
    if not images:
        print("No images found.", file=sys.stderr)
        return 2
    wanted = _csv_ints(args.images)
    if wanted:
        images = [image for index, image in enumerate(images, start=1) if index in wanted]
    channel_names = channel_labels(load_image(images[0].path, z_mode="max_projection"))
    units = plan_units(
        images,
        channel_names,
        engines=_csv_words(args.engines, ENGINES),
        modes=_csv_words(args.modes, ALL_MODES),
        channels=_csv_ints(args.channels),
        include_slow=args.include_cellpose4_3d,
    )
    design = Design()
    print(f"{len(images)} images, {len(units)} segmentation runs, {len(design.configs())} configurations each.")
    if args.dry_run:
        for unit in units:
            print(unit.unit_id)
        return 0
    output_dir.mkdir(parents=True, exist_ok=True)
    write_image_tables(find_images(input_dir, suffixes), output_dir)
    (output_dir / "design.json").write_text(json.dumps(design.as_dict(), indent=2), encoding="utf-8")
    run_units(units, design, output_dir, force=args.force)
    collate(output_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
