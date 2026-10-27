"""CellQuant results as column tables: what QuantAnalysis imports.

A source holds one row per image (the per-image summaries, with the image's group columns and
whether it is approved) and, when available, one row per object. It is read from a results folder
(``experiment.json`` with ``working/summaries/*.csv`` and ``working/measurements/*.csv``), a run or
working folder (``summaries/``, ``measurements/``), an export folder (``image_summary.csv``,
``objects.csv``), or an open experiment.

``build`` turns a source into a ``ColumnTable`` for one measure, one dot per image, per unit or per
object, one column per group. Per unit, a report's percent is pooled the way
``storage.unit_summary`` pools it (the unit's counts over its denominators, not a mean of
percents); counts and count areas are summed; per-object values are averaged over every object in
the unit. The per-image values are kept as well, so a SuperPlot can draw both levels.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

import pandas as pd

from cellquant.errors import StatisticsError
from cellquant.stats.tables import ColumnTable
from cellquant.storage import default_unit_column, unit_summary

APPROVED = ("approved", "reviewed")
DOTS = ("image", "unit", "object")
# Per-image columns that are never a measure or a group.
_IMAGE_ID_COLUMNS = {"sample_name", "filename", "image_id", "experiment_id", "run_id", "qc_status", "n_unmeasured", "n_called_by_hand", "summary_note"}
# Per-object columns that are never a measure.
_OBJECT_NOT_MEASURES = {
    "object_id", "centroid_x", "centroid_y", "centroid_z", "z_first", "z_last", "excluded", "unmeasured",
    "in_count_area", "at_crop_edge", "z_flag", "experiment_id", "run_id", "position", "manual_edit_status", "object_set",
}


@dataclass(frozen=True)
class Measure:
    key: str          # the column, e.g. report_1_percent, total_objects, area, af594_mean
    label: str        # shown, e.g. "% AF594+ of all objects"
    level: str        # "image" or "object"
    kind: str         # "percent", "count", "area" or "value"


@dataclass
class Source:
    images: pd.DataFrame                    # one row per image; "approved" is True/False, or missing when unknown
    objects: pd.DataFrame | None = None     # one row per object, or None
    names: dict[str, str] = field(default_factory=dict)          # marker id (af594_pos) -> name (AF594)
    measurements: dict[str, str] = field(default_factory=dict)   # measurement column (af594_mean) -> marker name
    label: str = ""                         # where it came from, for titles and the log
    path: Path | None = None

    @property
    def approvals_known(self) -> bool:
        return "approved" in self.images.columns

    def metadata_columns(self) -> list[str]:
        """Group columns of step 1 (``Folder 1``, ``group``…), in the summaries' order."""

        out = []
        for name in self.images.columns:
            text = str(name)
            if text in _IMAGE_ID_COLUMNS or text == "approved" or text in ("total_objects", "count_area") or text.startswith("report_"):
                continue
            out.append(text)
        return out

    def object_groups(self) -> list[str]:
        """Per-object columns to group by: each marker's +/- call and the phenotype."""

        if self.objects is None:
            return []
        return [name for name in self.objects.columns if str(name).endswith("_pos") or name == "phenotype"]

    def default_unit(self) -> str | None:
        return default_unit_column(self.images)

    def measures(self) -> list[Measure]:
        out = []
        for column in self.images.columns:
            match = re.fullmatch(r"(report_\d+)_percent", str(column))
            if match:
                out.append(Measure(column, report_name(self.images, match.group(1), self.names), "image", "percent"))
        if "total_objects" in self.images.columns:
            out.append(Measure("total_objects", "Objects counted (total_objects)", "image", "count"))
        if "count_area" in self.images.columns and pd.to_numeric(self.images["count_area"], errors="coerce").notna().any():
            out.append(Measure("count_area", "Count area (count_area)", "image", "area"))
        if self.objects is not None:
            for column in self.objects.columns:
                if column in _OBJECT_NOT_MEASURES or str(column).endswith("_pos"):
                    continue
                series = self.objects[column]
                if series.dtype == bool or not pd.api.types.is_numeric_dtype(series) or series.isna().all():
                    continue
                out.append(Measure(str(column), f"{object_measure_name(str(column), self.measurements)} per object", "object", "value"))
        return out

    def measure(self, key: str) -> Measure:
        for item in self.measures():
            if item.key == key:
                return item
        raise StatisticsError(f"These results have no measure {key!r}.")


# --- Names -----------------------------------------------------------------------------------------------------


def readable(expression: str, names: dict[str, str]) -> str:
    """``af594_pos AND NOT af647_pos`` as ``AF594+ and AF647−``."""

    text = str(expression).strip()
    if not text or text.casefold() in {"all_objects", "all_measured_objects", "nan"}:
        return "all objects"
    words = []
    for part in re.split(r"\s+AND\s+", text, flags=re.IGNORECASE):
        negated = part.casefold().startswith("not ")
        token = (part.split(None, 1)[1] if negated else part).strip()
        name = names.get(token) or re.sub(r"_pos$", "", token).upper()
        words.append(f"{name}−" if negated else f"{name}+")
    return " and ".join(words)


def report_name(images: pd.DataFrame, prefix: str, names: dict[str, str]) -> str:
    """``% AF594+ of all objects`` from the report's expressions in the summary."""

    def first(column: str) -> str:
        if column not in images.columns:
            return ""
        values = images[column].dropna()
        return str(values.iloc[0]) if len(values) else ""

    numerator = first(f"{prefix}_numerator")
    if not numerator:
        return f"{prefix}_percent"
    return f"% {readable(numerator, names)} of {readable(first(f'{prefix}_denominator_expression'), names)}"


def object_measure_name(column: str, measurements: dict[str, str]) -> str:
    if column in measurements:
        suffix = column.rsplit("_", 1)[-1]
        what = {"mean": "mean intensity", "pct": "% of pixels bright", "median": "median intensity", "max": "max intensity",
                "integrated": "integrated intensity", "sum": "integrated intensity"}.get(suffix, suffix)
        return f"{measurements[column]} {what}"
    return {"area": "Area", "volume": "Volume", "z_slices": "Z slices"}.get(column, column)


def group_label(column: str, value, names: dict[str, str]) -> str:
    """A group's name in the table: ``AF594+`` / ``AF594−`` for a marker call, else the value."""

    if column.endswith("_pos"):
        name = names.get(column) or column[: -len("_pos")].upper()
        return f"{name}+" if _truthy(value) else f"{name}−"
    if column == "phenotype":
        return str(value).replace("|", " ").replace("-", "−")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _truthy(value) -> bool:
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "1", "yes"}
    return bool(value)


# --- Reading ---------------------------------------------------------------------------------------------------


def read_results_folder(path: str | Path) -> Source:
    """A results folder, an export folder, or a run / working folder."""

    folder = Path(path).expanduser()
    if not folder.is_dir():
        raise StatisticsError(f"{folder} is not a folder.")
    if (folder / "experiment.json").is_file():
        return _read_experiment(folder)
    if (folder / "image_summary.csv").is_file():
        images = pd.read_csv(folder / "image_summary.csv")
        objects = pd.read_csv(folder / "objects.csv") if (folder / "objects.csv").is_file() else None
        names, measurements = _recipe_names(folder / "recipe.yaml")
        return Source(images, objects, names, measurements, label=f"export folder {folder.name}", path=folder)
    if (folder / "summaries").is_dir():
        experiment = next((parent for parent in (folder.parent, folder.parent.parent, folder.parent.parent.parent)
                           if (parent / "experiment.json").is_file()), None)
        statuses = _statuses(experiment / "experiment.json", folder) if experiment else None
        names, measurements = _recipe_names(folder / "recipe_snapshot.yaml")
        return _read_tables(folder, statuses, names, measurements, label=folder.name, path=folder)
    raise StatisticsError(
        f"{folder} is not a CellQuant results or export folder: it has no experiment.json, image_summary.csv or summaries folder."
    )


def _read_experiment(folder: Path) -> Source:
    document = json.loads((folder / "experiment.json").read_text(encoding="utf-8"))
    active = document.get("recipe_id")
    analysis = next((item for item in document.get("analyses") or [] if item.get("recipe_id") == active), None)
    working = folder / ((analysis or {}).get("working_folder") or "working")
    if not (working / "summaries").is_dir():
        raise StatisticsError(f"{folder} has no saved results yet (no {working.name}/summaries folder). Run some images first.")
    statuses = {record.get("image_id"): record.get("processing_status") for record in document.get("images", []) if record.get("include", True)}
    names, measurements = _recipe_names(folder / "recipes" / f"{active}.yaml")
    return _read_tables(working, statuses, names, measurements, label=document.get("experiment_name") or folder.name, path=folder)


def _statuses(experiment_json: Path, working: Path) -> dict[str, str] | None:
    """Image statuses, for a working folder of the active analysis only (a run's approvals may differ)."""

    try:
        document = json.loads(experiment_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    active = document.get("recipe_id")
    analysis = next((item for item in document.get("analyses") or [] if item.get("recipe_id") == active), {})
    if working.resolve() != (experiment_json.parent / (analysis.get("working_folder") or "working")).resolve():
        return None
    return {record.get("image_id"): record.get("processing_status") for record in document.get("images", []) if record.get("include", True)}


def _read_tables(folder: Path, statuses: dict[str, str] | None, names, measurements, label: str, path: Path) -> Source:
    images = _concat(folder / "summaries")
    if images.empty:
        raise StatisticsError(f"{folder / 'summaries'} has no image summaries.")
    objects = _concat(folder / "measurements")
    if statuses is not None and "image_id" in images.columns:
        images = images.loc[images["image_id"].isin(statuses)].reset_index(drop=True)
        images["approved"] = images["image_id"].map(lambda image_id: statuses.get(image_id) in APPROVED)
        if not objects.empty and "image_id" in objects.columns:
            objects = objects.loc[objects["image_id"].isin(statuses)].reset_index(drop=True)
    return Source(images, None if objects.empty else objects, names, measurements, label=label, path=path)


def _concat(directory: Path) -> pd.DataFrame:
    frames = [pd.read_csv(path) for path in sorted(directory.glob("*.csv"))] if directory.is_dir() else []
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _recipe_names(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Marker names by classification id, and by the measurement column each one reads."""

    try:
        import yaml

        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError, ImportError, AttributeError):
        return {}, {}
    return _names_from(document)


def _names_from(document: dict) -> tuple[dict[str, str], dict[str, str]]:
    names, measurements = {}, {}
    try:
        for item in document.get("classifications") or []:
            names[str(item["id"])] = str(item.get("name") or item["id"])
            if item.get("measurement"):
                measurements[str(item["measurement"])] = names[str(item["id"])]
        channels = {}
        for item in document.get("measurements") or []:
            channels.setdefault(item.get("channel"), []).append(str(item.get("id")))
        # A marker's other statistics on the same channel (af594_pct next to af594_mean) take its name too.
        for ids in channels.values():
            named = next((measurements[key] for key in ids if key in measurements), None)
            if named:
                for key in ids:
                    measurements.setdefault(key, named)
    except (KeyError, TypeError, AttributeError):
        pass
    return names, measurements


def from_controller(controller) -> Source:
    """The open experiment's active analysis, exactly as Export would write it (current cutoffs)."""

    results = controller._results_for_export()
    if not results:
        raise StatisticsError("This experiment has no results yet. Run some images first (step 6).")
    images = pd.concat([item.summary for item in results], ignore_index=True)
    objects = pd.concat([item.objects for item in results], ignore_index=True)
    statuses = {record.image_id: record.processing_status for record in controller.experiment.images if record.include}
    if "image_id" in images.columns:
        images["approved"] = images["image_id"].map(lambda image_id: statuses.get(image_id) in APPROVED)
    names, measurements = _names_from(controller.recipe.model_dump(mode="json"))
    return Source(images, objects if not objects.empty else None, names, measurements,
                  label=controller.experiment.experiment_name, path=controller.directory)


def controller_signature(controller) -> str:
    """Changes when anything that changes the imported numbers changes: approvals, cutoffs, edits, results."""

    experiment = controller.experiment
    parts = [
        [(record.image_id, record.processing_status, record.include, record.approved_settings_sha256,
          json.dumps(record.user_metadata, sort_keys=True, default=str), str(getattr(record, "count_area", None)))
         for record in experiment.images],
        controller.recipe.model_dump_json(exclude={"modified_at", "created_at"}),
        str(experiment.recipe_id),
        # dict.items: the raw entries, without reading back masks a result cache moved to disk.
        sorted((key, id(value)) for key, value in dict.items(controller.last_results)),
    ]
    return json.dumps(parts, default=str)


# --- Building a table ------------------------------------------------------------------------------------------


@dataclass
class ImportSpec:
    measure: str
    dot: str = "image"                  # "image", "unit" or "object"
    unit: str | None = None             # the unit column (dot = unit)
    group_by: str | None = None         # a group column of step 1, or a per-object column
    order: list[str] | None = None      # group names in table order; groups left out are dropped
    approved_only: bool = True

    def with_(self, **changes) -> "ImportSpec":
        return replace(self, **changes)


@dataclass
class Imported:
    table: ColumnTable
    row_labels: list[str]               # one per table row (the subject of a paired table, else blank)
    images: pd.DataFrame                # group, unit, image, value: one row per image and group, for SuperPlots
    units: pd.DataFrame | None          # group, dot, value: the replicate values tested (dot = unit), else None
    notes: list[str]                    # what was done, for the data sheet
    warnings: list[str]                 # shown prominently
    pseudoreplicated: bool              # each dot is one object
    description: str                    # one line for the sheet


def groups(source: Source, group_by: str | None, spec_measure: str | None = None) -> list[str]:
    """The groups a column makes, in order (a marker's − before its +)."""

    if not group_by:
        return ["All"]
    if group_by in source.images.columns:
        values = source.images[group_by].dropna()
    elif source.objects is not None and group_by in source.objects.columns:
        values = source.objects[group_by].dropna()
    else:
        return []
    labels = list(dict.fromkeys(group_label(group_by, value, source.names) for value in values))
    if group_by.endswith("_pos"):
        labels.sort(key=lambda text: text.endswith("+"))
    else:
        labels.sort(key=_natural)
    return labels


def _natural(text: str):
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", str(text))]


def counted_objects(objects: pd.DataFrame) -> pd.DataFrame:
    """Objects that count: not deleted, inside the count area, measured (as in every percent)."""

    keep = pd.Series(True, index=objects.index)
    for column, bad in (("excluded", True), ("unmeasured", True), ("in_count_area", False)):
        if column in objects.columns:
            values = objects[column].map(lambda value: _truthy(value) if pd.notna(value) else (not bad))
            keep &= values != bad
    return objects.loc[keep]


def build(source: Source, spec: ImportSpec) -> Imported:
    measure = source.measure(spec.measure)
    if spec.dot not in DOTS:
        raise StatisticsError(f"Dot must be one of {', '.join(DOTS)}.")
    if spec.dot == "object" and measure.level != "object":
        raise StatisticsError("One dot per object needs a per-object measure.")
    if spec.dot == "unit" and not spec.unit:
        raise StatisticsError("Choose the unit column (the biological replicate, for example Folder 2).")
    group_by = spec.group_by
    object_group = bool(group_by) and group_by not in source.images.columns
    if object_group and measure.level != "object":
        raise StatisticsError(f"{group_by} is a per-object column; group a per-image measure by a column of step 1.")
    notes, warnings = [], []
    images = source.images.copy()
    if spec.approved_only:
        if source.approvals_known:
            approved = images["approved"].astype(bool)
            notes.append(f"Approved images only: {int(approved.sum())} of {len(images)}.")
            images = images.loc[approved]
            if images.empty:
                raise StatisticsError("No image is approved yet. Approve images in step 5, or untick Approved images only.")
        else:
            notes.append("This folder does not record approvals, so every image is used.")
    images = images.reset_index(drop=True)
    images["_image"] = images["sample_name"].astype(str) if "sample_name" in images.columns else images.index.astype(str)
    images["_unit"] = images[spec.unit].map(lambda value: group_label(spec.unit, value, {})) if spec.unit and spec.unit in images.columns else ""
    images["_group"] = images[group_by].map(lambda value: group_label(group_by, value, source.names)) if group_by and not object_group else "All"
    if spec.dot == "unit" and spec.unit not in images.columns:
        raise StatisticsError(f"The summaries have no column {spec.unit!r}.")

    if measure.level == "image":
        images = images.loc[images["_group"].notna() & (images["_group"] != "nan")]
        values = pd.to_numeric(images[measure.key], errors="coerce")
        per_image = pd.DataFrame({"group": images["_group"], "unit": images["_unit"], "image": images["_image"], "value": values})
        if spec.dot == "image":
            long = per_image.rename(columns={"image": "dot"})
        else:
            long = _units_from_images(images, measure, spec.unit, group_by)
            notes.append(_pool_note(measure, spec.unit))
    else:
        if source.objects is None:
            raise StatisticsError("These results have no per-object table (objects.csv or measurements/).")
        objects = counted_objects(source.objects)
        keep = ["image_id", "_image", "_unit", "_group"] if "image_id" in images.columns else []
        if not keep:
            raise StatisticsError("The summaries have no image_id column, so objects cannot be matched to images.")
        objects = objects.merge(images[keep], on="image_id", how="inner", suffixes=("", "_img"))
        if object_group:
            if group_by not in objects.columns:
                raise StatisticsError(f"The object table has no column {group_by!r}.")
            objects["_group"] = objects[group_by].map(lambda value: group_label(group_by, value, source.names))
        objects["_value"] = pd.to_numeric(objects[measure.key], errors="coerce")
        objects = objects.loc[objects["_group"].notna()]
        per_image = (objects.groupby(["_group", "_unit", "_image"], sort=False)["_value"].mean().reset_index()
                     .rename(columns={"_group": "group", "_unit": "unit", "_image": "image", "_value": "value"}))
        if spec.dot == "object":
            long = pd.DataFrame({"group": objects["_group"], "dot": objects["_image"] + " #" + objects["object_id"].astype(str), "value": objects["_value"]})
            warnings.append(
                "Pseudoreplication: each dot is one object (cell), so n is the number of cells. Cells of one image or "
                "biological replicate are not independent, and a test on them gives P values that are far too small. For a claim about "
                "conditions, use one dot per biological replicate (N)."
            )
        elif spec.dot == "image":
            long = per_image.rename(columns={"image": "dot"})
            notes.append("Each image's value is the mean of its counted objects.")
        else:
            long = (objects.groupby(["_group", "_unit"], sort=False)["_value"].mean().reset_index()
                    .rename(columns={"_group": "group", "_unit": "dot", "_value": "value"}))
            notes.append(f"Each unit's value is the mean of every counted object in its images ({spec.unit}).")

    available = groups(source, group_by) if group_by else ["All"]
    order = [name for name in (spec.order or available) if name in set(long["group"])]
    if not order:
        raise StatisticsError("No values for the chosen groups.")
    long = long.loc[long["group"].isin(order)]
    paired = object_group and spec.dot != "object"
    title = measure.label.replace(" per object", "")
    if paired:
        table = ColumnTable.from_long(long, value="value", group="group", order=order, subject="dot", title=title)
        row_labels = list(pd.unique(long["dot"]))
        notes.append(f"Paired: row i of each column is the same {'image' if spec.dot == 'image' else 'unit'}.")
    else:
        table = ColumnTable.from_columns({name: long.loc[long["group"] == name, "value"].to_numpy() for name in order}, title=title)
        row_labels = []
    per_image = per_image.loc[per_image["group"].isin(order)].reset_index(drop=True)
    sizes = ", ".join(f"{column.name} n={column.finite.size}" for column in table.columns)
    what = {"image": "one dot per image", "unit": f"one dot per biological replicate ({spec.unit})", "object": "one dot per object"}[spec.dot]
    group_name = f"{source.names.get(group_by, group_by)} +/−" if group_by and group_by.endswith("_pos") else group_by
    description = f"{title}, {what}" + (f", grouped by {group_name}" if group_by else "") + f" ({sizes}). From {source.label}."
    if spec.dot in ("unit", "image") and any(column.finite.size < 3 for column in table.columns) and len(table.columns) > 1:
        warnings.append("A group has fewer than 3 values: tests have very little power, and a rank test cannot reach P < 0.05.")
    units = long[["group", "dot", "value"]].reset_index(drop=True) if spec.dot == "unit" else None
    return Imported(table, row_labels, per_image, units, notes, warnings, spec.dot == "object", description)


def _units_from_images(images: pd.DataFrame, measure: Measure, unit: str, group_by: str | None) -> pd.DataFrame:
    keys = ["_group", "_unit"]
    if measure.kind == "percent":
        prefix = measure.key[: -len("_percent")]
        # The same pooling as units_by_<unit>.csv: the unit's counts over its denominators.
        pooled = unit_summary(images, "_unit", "_group")
        return pd.DataFrame({"group": pooled["_group"], "dot": pooled["_unit"].astype(str), "value": pooled[f"{prefix}_unit_pooled_percent"]})
    values = images.assign(_value=pd.to_numeric(images[measure.key], errors="coerce"))
    summed = values.groupby(keys, sort=False)["_value"].sum(min_count=1).reset_index()
    return summed.rename(columns={"_group": "group", "_unit": "dot", "_value": "value"})


def _pool_note(measure: Measure, unit: str) -> str:
    if measure.kind == "percent":
        return (f"Each unit ({unit}) pools its images: the sum of their counts over the sum of their denominators, "
                "as in units_by_<unit>.csv, so an image with more cells weighs more.")
    return f"Each unit ({unit}) is the sum over its images."


__all__ = [
    "APPROVED", "ImportSpec", "Imported", "Measure", "Source", "build", "controller_signature", "counted_objects",
    "from_controller", "group_label", "groups", "read_results_folder", "readable", "report_name",
]
