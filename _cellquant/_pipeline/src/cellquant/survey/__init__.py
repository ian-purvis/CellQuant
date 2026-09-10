"""Fiji-style input survey: cluster acquisitions by channel layout."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from cellquant.batch import BatchSummary, run_batch
from cellquant.config import RunConfig, load_config
from cellquant.contracts import CancellationToken, MutableCancellationToken, null_event_sink
from cellquant.io import ImageMetadata, inspect_volume, iter_supported_files, normalize_suffixes
from cellquant.persist.atomic import write_text_atomic


def default_template_config_path() -> Path:
    """Return the installed template, independent of the current directory."""

    candidate = Path(__file__).resolve().parents[1] / "sample_config.yaml"
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(
        "sample_config.yaml was not found next to the CellQuant install; "
        "reinstall CellQuant to restore its packaged configuration."
    )


def materialize_run_config(
    output_path: str | Path,
    *,
    template: RunConfig | str | Path | None = None,
    survey: SurveyResult | None = None,
    segment_channel: int | None = None,
    recursive: bool | None = None,
    suffixes: Sequence[str] | None = None,
    segment_overrides: Mapping[str, Any] | None = None,
) -> tuple[RunConfig, Path]:
    """Write a concrete run config YAML and return the loaded ``RunConfig``.

    Uses ``sample_config.yaml`` when ``template`` is omitted. Survey results
    stamp ``io.recursive`` / ``io.suffixes`` so users do not hand-edit discovery.
    ``segment_overrides`` may set mode, device, diameter, and related fields.
    """

    import yaml

    if template is None:
        base = load_config(default_template_config_path())
    elif isinstance(template, RunConfig):
        base = template
    else:
        base = load_config(template)

    raw = deepcopy(dict(base.raw))
    if survey is not None:
        raw["io"] = {
            **dict(raw["io"]),
            "recursive": bool(survey.recursive),
            "suffixes": list(survey.suffixes),
        }
    if recursive is not None:
        raw["io"]["recursive"] = bool(recursive)
    if suffixes is not None:
        raw["io"]["suffixes"] = list(normalize_suffixes(suffixes))
    if segment_channel is not None:
        raw["preprocess"] = {**dict(raw["preprocess"]), "channel": int(segment_channel)}
    if segment_overrides:
        from cellquant.segment import apply_engine_segment_defaults

        segment = dict(raw["segment"])
        for key, value in segment_overrides.items():
            if key not in segment:
                raise KeyError(f"unknown segment override {key!r}")
            segment[key] = value
        mode = segment.get("mode")
        if mode != "stitch_2d":
            segment["stitch_threshold"] = 0.0
        if mode != "single_plane_2d":
            segment["z_index"] = None
        elif segment.get("z_index") is None:
            segment["z_index"] = 0
        raw["segment"] = apply_engine_segment_defaults(segment)

    # Prefer a complete spacing from the first successful survey record when the
    # template still has a null override and every inspected file agrees.
    if survey is not None and raw["io"].get("spacing_override_um") is None:
        spacings = [
            tuple(record.spacing_um)
            for record in survey.records
            if record.error is None and record.spacing_um is not None
        ]
        if spacings and all(item == spacings[0] for item in spacings):
            if all(value is not None and float(value) > 0 for value in spacings[0]):
                raw["io"]["spacing_override_um"] = [float(value) for value in spacings[0]]

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(
        destination,
        yaml.safe_dump(raw, sort_keys=False, default_flow_style=False),
    )
    config = RunConfig(raw)
    return config, destination


def write_layout_configs(
    survey: SurveyResult,
    output_dir: str | Path,
    *,
    template: RunConfig | str | Path | None = None,
    assignments: Mapping[str, int] | None = None,
    segment_overrides: Mapping[str, Any] | None = None,
) -> dict[str, Path]:
    """Write one run config YAML per included layout assignment."""

    planned = with_assignments(survey, assignments) if assignments is not None else survey
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for layout in planned.layouts:
        if layout.segment_channel is None:
            continue
        path = root / f"{layout.layout_id}.yaml"
        _, written_path = materialize_run_config(
            path,
            template=template,
            survey=planned,
            segment_channel=layout.segment_channel,
            segment_overrides=segment_overrides,
        )
        written[layout.layout_id] = written_path
    return written


# Soft UI defaults only. Users may segment ANY channel; these hints never
# gate discovery, config writing, or Cellpose mode selection.
_SEGMENTATION_HINT_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "nuclear_dna",
        (
            "dapi",
            "hoechst",
            "h33342",
            "draq5",
            "draq7",
            "sytox",
            "propidium",
            "pi ",
            "nuclei",
            "nucleus",
            "nuclear",
            "dna",
            "405",
        ),
    ),
    (
        "nuclear_marker",
        (
            "histone",
            "h2b",
            "lamina",
            "lamin",
            "ki67",
            "ki-67",
            "pcna",
            "neun",
            "sox2",
            "pax6",
            "otx2",
            "foxg1",
        ),
    ),
    (
        "cytoplasmic_or_structure",
        (
            "phalloidin",
            "actin",
            "tubulin",
            "tub",
            "cytokeratin",
            "vimentin",
            "membrane",
            "wga",
            "wheat germ",
            "cyto",
            "cytosol",
            "cytoplasm",
            "mito",
            "lysosome",
            "gfp",
            "egfp",
            "mcherry",
            "tdtomato",
            "rfp",
            "yfp",
            "cfp",
        ),
    ),
)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_channel_label(name: str) -> str:
    """Collapse channel labels for layout identity (case/spacing tolerant)."""

    cleaned = re.sub(r"\s+", " ", str(name).strip().casefold())
    return cleaned or "unnamed"


def layout_signature(channel_names: Sequence[str]) -> tuple[str, ...]:
    return tuple(normalize_channel_label(name) for name in channel_names)


def layout_id_for(channel_names: Sequence[str]) -> str:
    signature = layout_signature(channel_names)
    payload = json.dumps(
        {"n": len(signature), "channels": list(signature)},
        separators=(",", ":"),
        ensure_ascii=True,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    return f"L{len(signature)}-{digest}"


def suggest_segmentation_channel(
    channel_names: Sequence[str],
) -> tuple[int | None, str | None]:
    """Return ``(0-based index, hint_group)`` for a soft UI default.

    Preference order is nuclear DNA dyes → other nuclear markers →
    cytoplasmic/structural labels. If nothing matches, returns
    ``(None, None)`` and the UI should leave the choice to the user (any
    channel remains valid for Cellpose nuclei or cyto-style runs).
    """

    normalized = [normalize_channel_label(name) for name in channel_names]
    for group_name, hints in _SEGMENTATION_HINT_GROUPS:
        for index, name in enumerate(normalized):
            if any(hint in name for hint in hints):
                return index, group_name
    return None, None


def suggest_nuclear_channel(channel_names: Sequence[str]) -> int | None:
    """Compatibility alias for :func:`suggest_segmentation_channel`."""

    index, _group = suggest_segmentation_channel(channel_names)
    return index


@dataclass(frozen=True)
class SurveyRecord:
    source: str
    relative: str
    channel_count: int | None
    channel_names: tuple[str, ...]
    layout_id: str | None
    spacing_um: tuple[float | None, float | None, float | None] | None
    error: str | None = None


@dataclass(frozen=True)
class ChannelLayout:
    layout_id: str
    channel_count: int
    channel_names: tuple[str, ...]
    file_count: int
    sources: tuple[str, ...]
    suggested_channel: int | None
    segment_channel: int | None = None


@dataclass(frozen=True)
class SurveyResult:
    schema_version: int
    surveyed_utc: str
    input_root: str
    recursive: bool
    suffixes: tuple[str, ...]
    records: tuple[SurveyRecord, ...]
    layouts: tuple[ChannelLayout, ...]
    error_count: int

    @property
    def file_count(self) -> int:
        return len(self.records)


def survey_folder(
    input_root: str | Path,
    *,
    recursive: bool = True,
    suffixes: Sequence[str] | None = None,
    inspect_fn: Callable[[Path], ImageMetadata] = inspect_volume,
) -> SurveyResult:
    """Walk an acquisition tree and cluster files by channel metadata."""

    root = Path(input_root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"survey input folder not found: {root}")
    allowed = normalize_suffixes(suffixes)
    files = list(iter_supported_files(root, recursive=recursive, suffixes=allowed))
    if not files:
        raise FileNotFoundError(
            f"No matching image files under {root} "
            f"(recursive={recursive}, suffixes={list(allowed)})"
        )

    records: list[SurveyRecord] = []
    buckets: dict[str, dict[str, Any]] = {}
    errors = 0
    for path in files:
        relative = path.relative_to(root).as_posix()
        try:
            meta = inspect_fn(path)
            names = tuple(str(name) for name in meta.channel_names)
            layout_id = layout_id_for(names)
            record = SurveyRecord(
                source=str(path.resolve()),
                relative=relative,
                channel_count=len(names),
                channel_names=names,
                layout_id=layout_id,
                spacing_um=tuple(meta.spacing_um),
                error=None,
            )
            bucket = buckets.setdefault(
                layout_id,
                {
                    "channel_count": len(names),
                    "channel_names": names,
                    "sources": [],
                },
            )
            bucket["sources"].append(str(path.resolve()))
        except Exception as exc:  # noqa: BLE001 - survey must continue past bad files
            errors += 1
            record = SurveyRecord(
                source=str(path.resolve()),
                relative=relative,
                channel_count=None,
                channel_names=(),
                layout_id=None,
                spacing_um=None,
                error=f"{type(exc).__name__}: {exc}",
            )
        records.append(record)

    layouts: list[ChannelLayout] = []
    for layout_id, bucket in sorted(
        buckets.items(),
        key=lambda item: (-len(item[1]["sources"]), item[1]["channel_names"], item[0]),
    ):
        names = tuple(bucket["channel_names"])
        sources = tuple(bucket["sources"])
        suggested, _group = suggest_segmentation_channel(names)
        layouts.append(
            ChannelLayout(
                layout_id=layout_id,
                channel_count=int(bucket["channel_count"]),
                channel_names=names,
                file_count=len(sources),
                sources=sources,
                suggested_channel=suggested,
                # Pre-fill only when a soft hint matched; otherwise leave unset
                # so users explicitly choose (nuclear, cytoplasmic, or other).
                segment_channel=suggested,
            )
        )

    return SurveyResult(
        schema_version=1,
        surveyed_utc=_utc(),
        input_root=str(root),
        recursive=bool(recursive),
        suffixes=tuple(allowed),
        records=tuple(records),
        layouts=tuple(layouts),
        error_count=errors,
    )


def _survey_to_mapping(survey: SurveyResult) -> dict[str, Any]:
    return {
        "schema_version": survey.schema_version,
        "surveyed_utc": survey.surveyed_utc,
        "input_root": survey.input_root,
        "recursive": survey.recursive,
        "suffixes": list(survey.suffixes),
        "error_count": survey.error_count,
        "file_count": survey.file_count,
        "layouts": [
            {
                "layout_id": layout.layout_id,
                "channel_count": layout.channel_count,
                "channel_names": list(layout.channel_names),
                "file_count": layout.file_count,
                "sources": list(layout.sources),
                "suggested_channel": layout.suggested_channel,
                "segment_channel": layout.segment_channel,
            }
            for layout in survey.layouts
        ],
        "records": [
            {
                "source": record.source,
                "relative": record.relative,
                "channel_count": record.channel_count,
                "channel_names": list(record.channel_names),
                "layout_id": record.layout_id,
                "spacing_um": list(record.spacing_um) if record.spacing_um is not None else None,
                "error": record.error,
            }
            for record in survey.records
        ],
    }


def survey_from_mapping(raw: Mapping[str, Any]) -> SurveyResult:
    layouts = tuple(
        ChannelLayout(
            layout_id=str(item["layout_id"]),
            channel_count=int(item["channel_count"]),
            channel_names=tuple(item["channel_names"]),
            file_count=int(item["file_count"]),
            sources=tuple(item.get("sources", ())),
            suggested_channel=item.get("suggested_channel"),
            segment_channel=item.get("segment_channel"),
        )
        for item in raw["layouts"]
    )
    records = tuple(
        SurveyRecord(
            source=str(item["source"]),
            relative=str(item["relative"]),
            channel_count=item.get("channel_count"),
            channel_names=tuple(item.get("channel_names") or ()),
            layout_id=item.get("layout_id"),
            spacing_um=tuple(item["spacing_um"]) if item.get("spacing_um") is not None else None,
            error=item.get("error"),
        )
        for item in raw["records"]
    )
    return SurveyResult(
        schema_version=int(raw.get("schema_version", 1)),
        surveyed_utc=str(raw["surveyed_utc"]),
        input_root=str(raw["input_root"]),
        recursive=bool(raw["recursive"]),
        suffixes=tuple(raw["suffixes"]),
        records=records,
        layouts=layouts,
        error_count=int(raw.get("error_count", 0)),
    )


def write_survey(survey: SurveyResult, output_dir: str | Path) -> dict[str, Path]:
    """Persist survey JSON/CSV artifacts for review and assignment."""

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    survey_json = root / "survey.json"
    layouts_csv = root / "layouts.csv"
    files_csv = root / "files.csv"
    assignments_csv = root / "assignments.csv"

    write_text_atomic(
        survey_json,
        json.dumps(_survey_to_mapping(survey), indent=2, sort_keys=True) + "\n",
    )

    with layouts_csv.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "layout_id",
                "file_count",
                "channel_count",
                "channel_names",
                "suggested_channel",
                "segment_channel",
                "segment_channel_1based",
            ],
        )
        writer.writeheader()
        for layout in survey.layouts:
            writer.writerow(
                {
                    "layout_id": layout.layout_id,
                    "file_count": layout.file_count,
                    "channel_count": layout.channel_count,
                    "channel_names": " | ".join(layout.channel_names),
                    "suggested_channel": layout.suggested_channel,
                    "segment_channel": layout.segment_channel,
                    "segment_channel_1based": (
                        None
                        if layout.segment_channel is None
                        else int(layout.segment_channel) + 1
                    ),
                }
            )

    with files_csv.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "relative",
                "layout_id",
                "channel_count",
                "channel_names",
                "error",
                "source",
            ],
        )
        writer.writeheader()
        for record in survey.records:
            writer.writerow(
                {
                    "relative": record.relative,
                    "layout_id": record.layout_id,
                    "channel_count": record.channel_count,
                    "channel_names": " | ".join(record.channel_names),
                    "error": record.error,
                    "source": record.source,
                }
            )

    with assignments_csv.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "layout_id",
                "channel_names",
                "file_count",
                "segment_channel",
                "segment_channel_1based",
                "include",
            ],
        )
        writer.writeheader()
        for layout in survey.layouts:
            writer.writerow(
                {
                    "layout_id": layout.layout_id,
                    "channel_names": " | ".join(layout.channel_names),
                    "file_count": layout.file_count,
                    "segment_channel": layout.segment_channel
                    if layout.segment_channel is not None
                    else "",
                    "segment_channel_1based": (
                        ""
                        if layout.segment_channel is None
                        else int(layout.segment_channel) + 1
                    ),
                    "include": "yes" if layout.segment_channel is not None else "no",
                }
            )

    return {
        "survey_json": survey_json,
        "layouts_csv": layouts_csv,
        "files_csv": files_csv,
        "assignments_csv": assignments_csv,
    }


def load_survey(path: str | Path) -> SurveyResult:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("survey.json root must be a mapping")
    return survey_from_mapping(raw)


def read_assignments_csv(path: str | Path) -> dict[str, int]:
    """Read ``layout_id -> 0-based segment_channel`` from assignments.csv."""

    assignments: dict[str, int] = {}
    with Path(path).open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        for row in reader:
            include = str(row.get("include", "yes")).strip().casefold()
            if include in {"no", "false", "0", "n"}:
                continue
            layout_id = str(row["layout_id"]).strip()
            value = row.get("segment_channel")
            if value in (None, ""):
                one_based = row.get("segment_channel_1based")
                if one_based in (None, ""):
                    continue
                channel = int(one_based) - 1
            else:
                channel = int(value)
            if channel < 0:
                raise ValueError(f"segment_channel must be >= 0 for {layout_id}")
            assignments[layout_id] = channel
    return assignments


def with_assignments(
    survey: SurveyResult,
    assignments: Mapping[str, int],
    *,
    complete: bool = True,
) -> SurveyResult:
    """Return a copy of ``survey`` with ``segment_channel`` filled from assignments.

    When ``complete`` is True (default), the assignments map is the entire selected
    set: layouts absent from the map are excluded (``segment_channel=None``).
    When ``complete`` is False, only listed layouts are updated and others keep
    their previous ``segment_channel`` (partial update).
    """

    layouts = []
    known = {layout.layout_id: layout for layout in survey.layouts}
    for layout_id, channel in assignments.items():
        if layout_id not in known:
            raise KeyError(f"unknown layout_id {layout_id!r}")
        if channel < 0 or channel >= known[layout_id].channel_count:
            raise IndexError(
                f"segment channel {channel} is outside layout {layout_id} "
                f"with {known[layout_id].channel_count} channels"
            )
    for layout in survey.layouts:
        if layout.layout_id in assignments:
            channel = assignments[layout.layout_id]
        elif complete:
            channel = None
        else:
            channel = layout.segment_channel
        layouts.append(
            ChannelLayout(
                layout_id=layout.layout_id,
                channel_count=layout.channel_count,
                channel_names=layout.channel_names,
                file_count=layout.file_count,
                sources=layout.sources,
                suggested_channel=layout.suggested_channel,
                segment_channel=channel,
            )
        )
    return SurveyResult(
        schema_version=survey.schema_version,
        surveyed_utc=survey.surveyed_utc,
        input_root=survey.input_root,
        recursive=survey.recursive,
        suffixes=survey.suffixes,
        records=survey.records,
        layouts=tuple(layouts),
        error_count=survey.error_count,
    )


def config_for_layout(base: RunConfig, *, segment_channel: int) -> RunConfig:
    raw = deepcopy(dict(base.raw))
    raw["preprocess"] = {**raw["preprocess"], "channel": int(segment_channel)}
    return RunConfig(raw)


@dataclass(frozen=True)
class SurveyRunResult:
    layout_id: str
    channel_names: tuple[str, ...]
    segment_channel: int
    summary: BatchSummary


def _relative_under_survey(source: Path, input_root: Path | None) -> Path:
    source = source.resolve()
    if input_root is not None:
        try:
            return source.relative_to(Path(input_root).resolve())
        except ValueError:
            pass
    return Path(source.parent.name) / source.name


def plan_survey_batch_items(
    survey: SurveyResult,
    output_root: str | Path,
) -> list[tuple[str, int, BatchItem]]:
    """Plan unique output dirs for every included layout source in one pass.

    Paths are rooted at the survey input root when possible so same basenames in
    different folders do not collide. Layout order does not change identities.
    """

    from cellquant.batch import BatchItem, _collision_name, _input_fingerprint, _run_directory

    destination_root = Path(output_root).resolve()
    input_root = Path(survey.input_root).resolve() if survey.input_root else None
    pairs: list[tuple[str, int, Path, Path]] = []
    for layout in survey.layouts:
        if layout.segment_channel is None:
            continue
        for source in layout.sources:
            path = Path(source).resolve()
            relative = _relative_under_survey(path, input_root)
            pairs.append((layout.layout_id, int(layout.segment_channel), path, relative))
    pairs.sort(key=lambda value: (value[2].as_posix().casefold(), value[0]))
    occupied: set[str] = set()
    planned: list[tuple[str, int, BatchItem]] = []
    for layout_id, channel, source, relative in pairs:
        candidate = destination_root / _run_directory(relative)
        key = str(candidate).casefold()
        if key in occupied:
            relative = _collision_name(relative, source)
            candidate = destination_root / _run_directory(relative)
            key = str(candidate).casefold()
            if key in occupied:
                raise RuntimeError(f"could not derive a unique output path for {source}")
        occupied.add(key)
        planned.append(
            (
                layout_id,
                channel,
                BatchItem(
                    source=source,
                    output_dir=candidate,
                    output_root=destination_root,
                    input_fingerprint=_input_fingerprint(source),
                    file_id=relative.as_posix(),
                ),
            )
        )
    return planned


def run_survey_batches(
    survey: SurveyResult,
    output_root: str | Path,
    base_config: RunConfig,
    *,
    assignments: Mapping[str, int] | None = None,
    cancel: CancellationToken | None = None,
    events=null_event_sink,
) -> tuple[SurveyRunResult, ...]:
    """Run one batch queue per assigned channel layout with a shared output plan."""

    from cellquant.batch import BatchItemResult, run_batch, _write_root_reports

    planned = with_assignments(survey, assignments) if assignments is not None else survey
    token = cancel or MutableCancellationToken()
    destination = Path(output_root)
    planned_items = plan_survey_batch_items(planned, destination)
    if not planned_items:
        raise ValueError(
            "No layout has a segment_channel assignment. "
            "Set channels in the Survey UI or fill assignments.csv."
        )

    by_layout: dict[str, list] = {}
    channel_by_layout: dict[str, int] = {}
    names_by_layout: dict[str, tuple[str, ...]] = {
        layout.layout_id: layout.channel_names for layout in planned.layouts
    }
    for layout_id, channel, item in planned_items:
        by_layout.setdefault(layout_id, []).append(item)
        channel_by_layout[layout_id] = channel

    results: list[SurveyRunResult] = []
    ledger: list[BatchItemResult] = []
    for layout_id, queue in by_layout.items():
        if token.cancelled:
            for item in queue:
                ledger.append(
                    BatchItemResult(
                        str(item.source),
                        str(item.output_dir),
                        "cancelled",
                        None,
                        "batch cancelled before layout started",
                    )
                )
            continue
        config = config_for_layout(base_config, segment_channel=channel_by_layout[layout_id])
        summary = run_batch(queue, config, token, events, write_root_reports=False)
        ledger.extend(summary.results)
        layout_summary_path, layout_failures_path = _write_root_reports(
            destination / "layouts" / layout_id, summary.results
        )
        results.append(
            SurveyRunResult(
                layout_id=layout_id,
                channel_names=names_by_layout.get(layout_id, ()),
                segment_channel=channel_by_layout[layout_id],
                summary=BatchSummary(
                    total=summary.total,
                    completed=summary.completed,
                    resumed=summary.resumed,
                    failed=summary.failed,
                    cancelled=summary.cancelled,
                    results=summary.results,
                    summary_path=layout_summary_path,
                    failures_path=layout_failures_path,
                ),
            )
        )
        # Checkpoint consolidated ledger after each layout.
        _write_root_reports(destination, ledger)

    summary_path, failures_path = _write_root_reports(destination, ledger)
    if results:
        # Expose consolidated totals/paths on the last entry for UI discovery.
        last = results[-1]
        results[-1] = SurveyRunResult(
            layout_id=last.layout_id,
            channel_names=last.channel_names,
            segment_channel=last.segment_channel,
            summary=BatchSummary(
                total=len(ledger),
                completed=sum(r.status == "completed" for r in ledger),
                resumed=sum(r.status == "resumed" for r in ledger),
                failed=sum(r.status == "failed" for r in ledger),
                cancelled=sum(r.status == "cancelled" for r in ledger),
                results=tuple(ledger),
                summary_path=summary_path,
                failures_path=failures_path,
            ),
        )
    return tuple(results)


__all__ = [
    "ChannelLayout",
    "SurveyRecord",
    "SurveyResult",
    "SurveyRunResult",
    "config_for_layout",
    "default_template_config_path",
    "layout_id_for",
    "load_survey",
    "materialize_run_config",
    "normalize_channel_label",
    "plan_survey_batch_items",
    "read_assignments_csv",
    "run_survey_batches",
    "suggest_nuclear_channel",
    "suggest_segmentation_channel",
    "survey_folder",
    "with_assignments",
    "write_layout_configs",
    "write_survey",
]
