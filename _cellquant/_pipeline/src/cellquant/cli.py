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

    classify_batch_parser = commands.add_parser(
        "classify-batch",
        help="nuclear coexpression over complete *.cellquant runs (prefers labels_reviewed.tif)",
    )
    classify_batch_parser.add_argument("root", help="folder containing complete *.cellquant runs")
    classify_batch_parser.add_argument("output", help="parent folder for classify_batch_<timestamp>/")
    classify_batch_parser.add_argument(
        "--recipe",
        required=True,
        help="recipe JSON, {\"layouts\": {layout_id: recipe}}, or layout_id→recipe map",
    )
    classify_batch_parser.add_argument(
        "--overrides",
        help="JSON map of *.cellquant path → recipe for per-image threshold overrides",
    )
    classify_batch_parser.add_argument(
        "--survey",
        help="survey.json used to group runs by channel layout",
    )

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

    hpc_parser = commands.add_parser(
        "hpc",
        help="prepare, validate, and import portable Alpine segmentation packages",
    )
    hpc_commands = hpc_parser.add_subparsers(dest="hpc_command", required=True)

    hpc_prepare = hpc_commands.add_parser(
        "prepare",
        help="export a portable HPC bundle without loading Cellpose",
    )
    hpc_prepare.add_argument("input", help="image file or folder")
    hpc_prepare.add_argument("output", help="directory that will contain cellquant_hpc_<id>/")
    hpc_prepare.add_argument("--config", required=True)
    hpc_prepare.add_argument("--profile", default=None, help="Alpine profile id (default: alpine_ah200)")
    hpc_prepare.add_argument("--recursive", action=argparse.BooleanOptionalAction, default=True)
    hpc_prepare.add_argument(
        "--file-type",
        choices=sorted(("all", "tiff", "nd2")),
        default="all",
    )
    hpc_prepare.add_argument("--suffixes", nargs="+", default=None, metavar="EXT")
    hpc_prepare.add_argument("--project-root", required=True, help="Alpine /projects path for durable results")
    hpc_prepare.add_argument("--scratch-root", required=True, help="Alpine /scratch path for compute staging")
    hpc_prepare.add_argument("--env-location", required=True, help="CellQuant conda/env path on Alpine")
    hpc_prepare.add_argument("--account", default=None)
    hpc_prepare.add_argument("--qos", default=None)
    hpc_prepare.add_argument("--gres", default=None)
    hpc_prepare.add_argument("--walltime", default=None)
    hpc_prepare.add_argument("--email", default=None)
    hpc_prepare.add_argument(
        "--segment-channel",
        type=int,
        default=None,
        help="0-based channel index applied to every acquisition (required unless set in config)",
    )

    hpc_validate = hpc_commands.add_parser("validate", help="validate a local HPC bundle")
    hpc_validate.add_argument("bundle")
    hpc_validate.add_argument(
        "--require-ready",
        action=argparse.BooleanOptionalAction,
        default=True,
    )

    hpc_import = hpc_commands.add_parser(
        "import-results",
        help="import cluster result_manifest.json packages into *.cellquant runs",
    )
    hpc_import.add_argument("results", help="result folder containing result_manifest.json")
    hpc_import.add_argument("destination", help="local folder for imported *.cellquant runs")
    hpc_import.add_argument("--source-bundle", default=None, help="optional original export bundle")
    hpc_import.add_argument(
        "--copy",
        dest="copy_runs",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="copy run stores into destination (default) or --no-copy to reference in place",
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
    if args.command == "classify-batch":
        from datetime import datetime, timezone

        from cellquant.classify.batch import (
            discover_cellquant_runs,
            load_image_overrides,
            load_layout_recipes,
            resolve_layout_recipes_for_runs,
            run_classify_batch,
        )

        try:
            runs = discover_cellquant_runs(args.root, survey_json=args.survey)
            if not runs:
                raise ValueError(f"no complete *.cellquant runs under {args.root}")
            recipes = resolve_layout_recipes_for_runs(runs, load_layout_recipes(args.recipe))
            overrides = load_image_overrides(args.overrides)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            destination = Path(args.output) / f"classify_batch_{stamp}"
            artifacts = run_classify_batch(
                runs,
                output_dir=destination,
                layout_recipes=recipes,
                image_overrides=overrides,
            )
            summary = json.loads(Path(artifacts["batch_summary"]).read_text(encoding="utf-8"))
        except (ValueError, TypeError, OSError, IndexError, KeyError, json.JSONDecodeError) as exc:
            parser.error(str(exc))
        print(
            json.dumps(
                {
                    "output_dir": str(artifacts["output_dir"]),
                    "total": summary.get("total"),
                    "completed": summary.get("completed"),
                    "failed": summary.get("failed"),
                    "artifacts": {name: str(path) for name, path in artifacts.items() if name != "output_dir"},
                },
                allow_nan=False,
            )
        )
        return 1 if summary.get("failed") else 0
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
    if args.command == "hpc":
        return _hpc_command(parser, args)
    raise AssertionError(args.command)


def _hpc_command(parser: argparse.ArgumentParser, args) -> int:
    from cellquant.hpc.acquisitions import expand_acquisitions, with_segment_channels
    from cellquant.hpc.export import prepare_bundle
    from cellquant.hpc.import_results import import_hpc_results
    from cellquant.hpc.cluster_profiles import load_profile
    from cellquant.hpc.templates import UserClusterSettings, submission_command, transfer_instructions
    from cellquant.hpc.validate import validate_bundle

    if args.hpc_command == "validate":
        try:
            result = validate_bundle(args.bundle, require_ready=bool(args.require_ready))
        except (ValueError, TypeError, OSError) as exc:
            parser.error(str(exc))
        print(
            json.dumps(
                {
                    "ok": result.ok,
                    "errors": list(result.errors),
                    "warnings": list(result.warnings),
                    "bundle_id": (result.bundle or {}).get("bundle_id"),
                },
                indent=2,
            )
        )
        return 0 if result.ok else 1

    if args.hpc_command == "import-results":
        try:
            summary = import_hpc_results(
                args.results,
                args.destination,
                source_bundle=args.source_bundle,
                copy_runs=bool(args.copy_runs),
            )
        except (ValueError, TypeError, OSError, KeyError, json.JSONDecodeError) as exc:
            parser.error(str(exc))
        print(
            json.dumps(
                {
                    "destination": str(summary.destination),
                    "complete": summary.complete,
                    "failed": summary.failed,
                    "unfinished": summary.unfinished,
                    "mapping": str(summary.mapping_path),
                },
                indent=2,
            )
        )
        return 0 if summary.failed == 0 else 1

    if args.hpc_command == "prepare":
        try:
            profile = load_profile(args.profile)
            config = _configured(args.config)
            if args.segment_channel is not None:
                raw = dict(config.raw)
                raw["preprocess"] = {**dict(raw["preprocess"]), "channel": int(args.segment_channel)}
                config = RunConfig(raw)
            suffixes = list(args.suffixes) if args.suffixes is not None else list(
                resolve_file_type_preset(args.file_type)
            )
            input_path = Path(args.input)
            acquisitions = expand_acquisitions(
                [input_path] if input_path.is_file() else [],
                root=None if input_path.is_file() else input_path,
                recursive=bool(args.recursive),
                suffixes=suffixes,
            )
            channel = int(config.raw["preprocess"]["channel"])
            layout_channels = {
                item.layout_id: channel for item in acquisitions if item.layout_id
            }
            acquisitions = with_segment_channels(acquisitions, layout_channels)
            settings = UserClusterSettings(
                account=args.account,
                qos=args.qos or profile.default_qos,
                gres=args.gres or profile.default_gres,
                walltime=args.walltime or profile.default_walltime,
                project_root=args.project_root,
                scratch_root=args.scratch_root,
                env_location=args.env_location,
                email=args.email,
            )
            result = prepare_bundle(
                acquisitions,
                args.output,
                config,
                profile,
                settings,
            )
        except (ValueError, TypeError, OSError, FileExistsError, KeyError) as exc:
            parser.error(str(exc))
        payload = {
            "ready": result.ready,
            "bundle_dir": str(result.bundle_dir),
            "bundle_id": result.bundle_id,
            "acquisition_count": result.acquisition_count,
            "incomplete_reason": result.incomplete_reason,
            "status": (
                "Package ready for transfer"
                if result.ready
                else "Incomplete — not submittable"
            ),
        }
        if result.ready:
            remote = f"{settings.project_root.rstrip('/')}/{result.bundle_dir.name}"
            payload["transfer_command"] = transfer_instructions(
                local_bundle=result.bundle_dir,
                remote_parent=settings.project_root,
            )
            payload["submission_command"] = submission_command(remote_bundle=remote)
        print(json.dumps(payload, indent=2))
        return 0 if result.ready else 1

    raise AssertionError(args.hpc_command)


if __name__ == "__main__":
    raise SystemExit(main())
