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
from cellquant.io import open_volume
from cellquant.verify import compare_pair, render_parity_report


def output_directory(output_root: str | Path, relative_input: str | Path) -> Path:
    """Return the stable, collision-safe per-file run directory."""

    relative = Path(relative_input)
    return Path(output_root) / relative.parent / f"{relative.name}.cellquant"


def _configured(path: str | Path, *, recursive: bool | None = None) -> RunConfig:
    config = load_config(path)
    if recursive is None:
        return config
    raw = copy.deepcopy(dict(config.raw))
    raw["io"] = {**raw["io"], "recursive": recursive}
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

    batch_parser = commands.add_parser("batch", help="run a failure-isolated calibrated batch")
    batch_parser.add_argument("input")
    batch_parser.add_argument("output")
    batch_parser.add_argument("--config", required=True)
    batch_parser.add_argument("--recursive", action="store_true")

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

    args = parser.parse_args(argv)
    if args.command == "batch":
        config = _configured(args.config, recursive=args.recursive)
        summary = batch(args.input, args.output, config)
        print(json.dumps({"total": summary.total, "completed": summary.completed, "resumed": summary.resumed, "failed": summary.failed, "cancelled": summary.cancelled}))
        return 1 if summary.failed else 0
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
