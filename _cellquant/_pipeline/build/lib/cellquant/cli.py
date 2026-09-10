"""Headless entry points for the same calibrated core used by napari."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Iterable

import numpy as np
import tifffile

from cellquant.batch import build_queue, run_batch
from cellquant.config import RunConfig, load_config
from cellquant.contracts import ImageVolume, LabelVolume, MutableCancellationToken
from cellquant.harness import HarnessCase, run_case
from cellquant.io import normalize_suffixes, open_volume, resolve_file_type_preset
from cellquant.survey import (
    load_survey,
    read_assignments_csv,
    run_survey_batches,
    survey_folder,
    write_survey,
)
from cellquant.verify import compare_pair, render_parity_report


def output_directory(output_root: str | Path, relative_input: str | Path) -> Path:
    """Return the stable, collision-safe per-file run directory."""

    relative = Path(relative_input)
    return Path(output_root) / relative.parent / f"{relative.name}.cellquant"


def _configured(
    path: str | Path,
    *,
    recursive: bool | None = None,
    suffixes: list[str] | None = None,
) -> RunConfig:
    config = load_config(path)
    if recursive is None and suffixes is None:
        return config
    raw = copy.deepcopy(dict(config.raw))
    io_updates = dict(raw["io"])
    if recursive is not None:
        io_updates["recursive"] = recursive
    if suffixes is not None:
        io_updates["suffixes"] = list(normalize_suffixes(suffixes))
    raw["io"] = io_updates
    return RunConfig(raw)


def batch(input_root: str | Path, output_root: str | Path, config: RunConfig):
    """Compatibility callable backing the ``cellquant batch`` command."""

    queue = build_queue([input_root], output_root, config)
    return run_batch(queue, config, MutableCancellationToken())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_label(path: Path) -> np.ndarray:
    value = np.asarray(tifffile.imread(path))
    if value.ndim != 3 or not np.issubdtype(value.dtype, np.integer):
        raise ValueError(f"label file must be an integer ZYX TIFF: {path}")
    return value.astype(np.uint32, copy=False)


def _classification_grid_tiff(path: Path, shape, *, region=False) -> np.ndarray:
    value = np.asarray(tifffile.imread(path))
    if value.ndim == 2 and shape[0] == 1:
        value = value[np.newaxis, ...]
    if value.shape != tuple(shape):
        raise ValueError(f"{'region' if region else 'labels'} must match the prepared analysis grid {tuple(shape)}; received {value.shape}")
    if region:
        if not np.all((value == 0) | (value == 1)):
            raise ValueError("region TIFF must contain only 0 (outside) and 1 (inside)")
        return value.astype(bool)
    if (not np.issubdtype(value.dtype, np.integer)
            or np.any(value < 0) or np.any(value > np.iinfo(np.uint32).max)):
        raise ValueError("labels TIFF must contain nonnegative integer IDs within uint32 range")
    return value.astype(np.uint32)


def _classify_command(args):
    from cellquant.classify import ClassificationRecipe
    from cellquant.classify.store import save_classification
    from cellquant.preprocess import prepare_analysis_volume

    config = load_config(args.config)
    io_spec = config.raw["io"]
    source = Path(args.image)
    image = open_volume(source, series=int(io_spec["series"]),
                        position=int(io_spec["position"]), lazy=bool(io_spec["lazy"]),
                        axes_override=io_spec["axes_override"],
                        spacing_override_um=io_spec["spacing_override_um"])
    image = prepare_analysis_volume(image, config)
    image = ImageVolume(image.data, image.spacing_um, image.channel_names, image.source,
                        {**image.metadata, "run_config": dict(config.raw)})
    shape = tuple(image.data.shape[:3])
    labels = LabelVolume(_classification_grid_tiff(Path(args.labels), shape), image.spacing_um,
                         {"source": str(Path(args.labels).resolve()), "review_status": "user_supplied"})
    region = _classification_grid_tiff(Path(args.region), shape, region=True) if args.region else None
    context = json.loads(Path(args.context).read_text(encoding="utf-8")) if args.context else {}
    recipe = ClassificationRecipe(json.loads(Path(args.recipe).read_text(encoding="utf-8")))
    return save_classification(args.output, image, labels, recipe, region=region, context=context)


def _candidate_map(root: Path) -> dict[str, Path]:
    if root.is_file():
        return {root.name: root}
    values: dict[str, Path] = {}
    for path in sorted(root.rglob("labels.tif")):
        name = path.parent.name
        if name.endswith(".cellquant"):
            values[name[: -len(".cellquant")]] = path
    for path in sorted(root.rglob("*.tif")):
        if not path.name.endswith("_overlay.tif") and path.name != "labels.tif":
            values.setdefault(path.name, path)
    return values


def _parity_pairs(reference: Path, candidate: Path) -> Iterable[tuple[str, np.ndarray, np.ndarray]]:
    if reference.is_file() and candidate.is_file():
        yield reference.name, _load_label(reference), _load_label(candidate)
        return
    if not reference.is_dir() or not candidate.is_dir():
        raise ValueError("reference and candidate must both be files or both be directories")
    candidates = _candidate_map(candidate)
    references = {
        path.name: path
        for path in sorted(reference.rglob("*.tif"))
        if not path.name.endswith("_overlay.tif") and path.name != "labels.tif"
    }
    missing = sorted(set(references) - set(candidates))
    if missing:
        raise FileNotFoundError(f"candidate labels missing for {len(missing)} stack(s): {missing[:5]}")
    for name, path in references.items():
        yield name, _load_label(path), _load_label(candidates[name])


def _parse_crop(value: str | None, shape: tuple[int, int, int]) -> tuple[slice, slice, slice]:
    if value is None:
        return tuple(slice(0, size) for size in shape)  # type: ignore[return-value]
    parts = value.split(",")
    if len(parts) != 3:
        raise ValueError("crop must be Z0:Z1,Y0:Y1,X0:X1 with exclusive upper bounds")
    result = []
    for text, size in zip(parts, shape, strict=True):
        endpoints = text.split(":")
        if len(endpoints) != 2:
            raise ValueError("each crop axis must be START:STOP")
        start = int(endpoints[0]) if endpoints[0] else 0
        stop = int(endpoints[1]) if endpoints[1] else size
        if not 0 <= start < stop <= size:
            raise ValueError(f"crop {start}:{stop} is outside axis length {size}")
        result.append(slice(start, stop))
    return tuple(result)  # type: ignore[return-value]


def _showcase(
    module: str,
    input_path: Path,
    labels_path: Path | None,
    output: Path,
    config: RunConfig,
    crop_text: str | None,
):
    io_spec = config.raw["io"]
    image = open_volume(
        input_path,
        series=int(io_spec.get("series", 0)),
        position=int(io_spec.get("position", 0)),
        lazy=bool(io_spec.get("lazy", True)),
        axes_override=io_spec.get("axes_override"),
        spacing_override_um=io_spec.get("spacing_override_um"),
    )
    crop = _parse_crop(crop_text, tuple(image.data.shape[:3]))
    image = ImageVolume(
        image.data[crop + (slice(None),)],
        image.spacing_um,
        image.channel_names,
        image.source,
        {**image.metadata, "input_fingerprint": _sha256(input_path)},
    )
    if module == "preprocess":
        from cellquant.preprocess import showcase_crop

        return showcase_crop(image, config, output)
    if module == "segment":
        from cellquant.segment import showcase_crop

        return showcase_crop(image, config, output)
    if labels_path is None:
        raise ValueError(f"showcase module {module!r} requires --labels")
    labels = LabelVolume(
        _load_label(labels_path)[crop],
        image.spacing_um,
        {"input_fingerprint": _sha256(input_path)},
    )
    if module == "viz":
        from cellquant.viz import showcase_crop

        return showcase_crop(image, labels, output, config)
    if module == "measure":
        from cellquant.measure import showcase_crop

        return showcase_crop((image, labels), config, output)
    if module == "postprocess":
        from cellquant.postprocess import showcase_crop

        return showcase_crop((image, labels), config, output)
    raise ValueError("showcase module must be preprocess, segment, postprocess, measure, or viz")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="cellquant")
    commands = parser.add_subparsers(dest="command", required=True)

    classify_parser = commands.add_parser("classify", help="score reviewed nuclear labels without running Cellpose")
    classify_parser.add_argument("image")
    classify_parser.add_argument("labels", help="reviewed integer TIFF on the configured analysis grid")
    classify_parser.add_argument("output")
    classify_parser.add_argument("--config", required=True)
    classify_parser.add_argument("--recipe", required=True, help="classification recipe JSON")
    classify_parser.add_argument("--region", help="binary 0/1 TIFF matching the analysis grid")
    classify_parser.add_argument("--context", help="sample identity JSON; region_id required with --region")

    reclassify_parser = commands.add_parser("reclassify", help="verify and rescore saved classification evidence")
    reclassify_parser.add_argument("run")
    reclassify_parser.add_argument("output")
    reclassify_parser.add_argument("--recipe", help="replacement recipe JSON (default: saved recipe)")

    batch_parser = commands.add_parser("batch", help="run a failure-isolated calibrated batch")
    batch_parser.add_argument("input")
    batch_parser.add_argument("output")
    batch_parser.add_argument("--config", required=True)
    batch_parser.add_argument(
        "--recursive",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="include subfolders when discovering images (default: config.io.recursive)",
    )
    batch_parser.add_argument(
        "--file-type",
        choices=sorted(("all", "tiff", "nd2")),
        default=None,
        help="restrict discovery to a filetype preset (default: config.io.suffixes)",
    )
    batch_parser.add_argument(
        "--suffixes",
        nargs="+",
        default=None,
        metavar="EXT",
        help="explicit extensions such as .tif .nd2 (overrides --file-type)",
    )

    harness_parser = commands.add_parser("harness", help="run one full verification case")
    harness_parser.add_argument("input")
    harness_parser.add_argument("output")
    harness_parser.add_argument("--config", required=True)
    harness_parser.add_argument("--name")
    harness_parser.add_argument("--axes")
    harness_parser.add_argument("--spacing", nargs=3, type=float, metavar=("Z_UM", "Y_UM", "X_UM"))

    parity_parser = commands.add_parser("parity", help="compare candidate labels with reference labels")
    parity_parser.add_argument("reference")
    parity_parser.add_argument("candidate")
    parity_parser.add_argument("output")
    parity_parser.add_argument("--title", default="CellQuant parity report")

    showcase_parser = commands.add_parser("showcase", help="stage one subsystem on a bounded crop")
    showcase_parser.add_argument("module")
    showcase_parser.add_argument("input")
    showcase_parser.add_argument("output")
    showcase_parser.add_argument("--config", required=True)
    showcase_parser.add_argument("--labels")
    showcase_parser.add_argument("--crop")

    survey_parser = commands.add_parser(
        "survey",
        help="scan inputs and cluster files by channel layout (Fiji-style survey)",
    )
    survey_parser.add_argument("input")
    survey_parser.add_argument("output")
    survey_parser.add_argument(
        "--recursive",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    survey_parser.add_argument(
        "--file-type",
        choices=sorted(("all", "tiff", "nd2")),
        default="nd2",
    )
    survey_parser.add_argument("--suffixes", nargs="+", default=None, metavar="EXT")

    survey_run_parser = commands.add_parser(
        "survey-run",
        help="run batch queues from a survey.json + assignments.csv",
    )
    survey_run_parser.add_argument("survey_json")
    survey_run_parser.add_argument("output")
    survey_run_parser.add_argument("--config", required=True)
    survey_run_parser.add_argument(
        "--assignments",
        help="assignments.csv (default: beside survey.json)",
    )

    args = parser.parse_args(argv)
    if args.command in {"classify", "reclassify"}:
        try:
            if args.command == "classify":
                path, result = _classify_command(args)
            else:
                from cellquant.classify.store import reclassify_run

                recipe = json.loads(Path(args.recipe).read_text(encoding="utf-8")) if args.recipe else None
                path, result = reclassify_run(args.run, args.output, recipe)
        except (ValueError, TypeError, OSError, IndexError, KeyError) as exc:
            parser.error(str(exc))
        print(json.dumps({"output_dir": str(path), "metadata": result.metadata}, allow_nan=False))
        return 0
    if args.command == "batch":
        suffixes = None
        if args.suffixes is not None:
            suffixes = list(args.suffixes)
        elif args.file_type is not None:
            suffixes = list(resolve_file_type_preset(args.file_type))
        config = _configured(args.config, recursive=args.recursive, suffixes=suffixes)
        summary = batch(args.input, args.output, config)
        print(json.dumps({"total": summary.total, "completed": summary.completed, "resumed": summary.resumed, "failed": summary.failed, "cancelled": summary.cancelled}))
        return 1 if summary.failed else 0
    if args.command == "survey":
        suffixes = list(args.suffixes) if args.suffixes is not None else list(
            resolve_file_type_preset(args.file_type)
        )
        result = survey_folder(
            args.input,
            recursive=bool(args.recursive),
            suffixes=suffixes,
        )
        artifacts = write_survey(result, args.output)
        print(
            json.dumps(
                {
                    "file_count": result.file_count,
                    "layout_count": len(result.layouts),
                    "error_count": result.error_count,
                    "artifacts": {name: str(path) for name, path in artifacts.items()},
                },
                indent=2,
            )
        )
        return 0
    if args.command == "survey-run":
        survey_path = Path(args.survey_json)
        survey = load_survey(survey_path)
        assignments_path = (
            Path(args.assignments)
            if args.assignments
            else survey_path.with_name("assignments.csv")
        )
        assignments = read_assignments_csv(assignments_path)
        runs = run_survey_batches(
            survey,
            args.output,
            load_config(args.config),
            assignments=assignments,
        )
        payload = [
            {
                "layout_id": run.layout_id,
                "segment_channel": run.segment_channel,
                "channel_names": list(run.channel_names),
                "total": run.summary.total,
                "completed": run.summary.completed,
                "failed": run.summary.failed,
                "resumed": run.summary.resumed,
                "cancelled": run.summary.cancelled,
            }
            for run in runs
        ]
        print(json.dumps({"layouts": payload}, indent=2))
        return 1 if any(run.summary.failed for run in runs) else 0
    if args.command == "harness":
        path = Path(args.input)
        result = run_case(
            HarnessCase(
                args.name or path.name,
                path,
                tuple(args.spacing) if args.spacing else None,
                args.axes,
            ),
            _configured(args.config),
            args.output,
        )
        print(json.dumps({"success": result.success, "label_count": result.label_count, "output_dir": str(result.output_dir)}))
        return 0 if result.success else 1
    if args.command == "parity":
        results = [
            compare_pair(ref, cand, name)
            for name, ref, cand in _parity_pairs(Path(args.reference), Path(args.candidate))
        ]
        report = render_parity_report(results, args.output, title=args.title)
        print(report)
        return 0
    if args.command == "showcase":
        artifacts = _showcase(
            args.module,
            Path(args.input),
            Path(args.labels) if args.labels else None,
            Path(args.output),
            _configured(args.config),
            args.crop,
        )
        print(json.dumps({name: str(path) for name, path in artifacts.items()}, indent=2))
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
