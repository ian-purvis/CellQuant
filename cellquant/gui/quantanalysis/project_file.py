"""QuantAnalysis project files (.cqstats): every data table, analysis and graph, saved and opened again.

A project file is a zip archive holding one ``project.json``. Data tables are saved with their values, their import
choices and where they came from; analyses are saved as their options and computed again when the file is opened, so
a result always matches its table. Graphs keep their whole format. No Qt widgets here.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from cellquant.errors import CellQuantError
from cellquant.gui.quantanalysis.project import (
    DataSheet,
    GraphSheet,
    LayoutSheet,
    Project,
    ResultSheet,
    reserve_ids,
    set_contingency,
    set_grouped,
    set_replicates,
    set_xy,
)
from cellquant.stats.replicates import ReplicateTable
from cellquant.stats import ColumnTable, transforms
from cellquant.stats.contingency import ContingencyTable
from cellquant.stats.grouped import GroupedTable
from cellquant.stats.xy import XYTable
from cellquant.stats.sources import ImportSpec
from cellquant.stats.tables import Column

SUFFIX = ".cqstats"
FORMAT = "cellquant-quantanalysis-project"
VERSION = 1
MEMBER = "project.json"
RESULT_KINDS = {"compare", "anova", "colstats", "normality", "outliers", "pstack", "twoway", "linreg", "xycorr", "curvefit", "contingency", "roc", "blandaltman", "survival"}


# --- Writing -------------------------------------------------------------------------------------------------------------


def _plain(value):
    """``json.dumps`` default: numpy numbers and arrays, paths and tuples as plain JSON values."""

    if isinstance(value, np.generic):
        value = value.item()
        return None if isinstance(value, float) and not np.isfinite(value) else value
    if isinstance(value, np.ndarray):
        return [_plain(item) if isinstance(item, np.generic) else item for item in value.tolist()]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    raise TypeError(f"{type(value).__name__} cannot be saved in a project file")


def _numbers(values) -> list:
    return [float(value) if np.isfinite(value) else None for value in np.asarray(values, dtype=float)]


def _frame(frame: pd.DataFrame | None) -> dict | None:
    """A data frame column by column, with each column's kind so that it reads back the same."""

    if frame is None:
        return None
    columns = []
    for name in frame.columns:
        series = frame[name]
        if pd.api.types.is_bool_dtype(series):
            kind, values = "b", [bool(value) for value in series]
        elif pd.api.types.is_integer_dtype(series):
            kind, values = "i", [int(value) for value in series]
        elif pd.api.types.is_float_dtype(series):
            kind, values = "f", _numbers(series)
        else:
            kind = "s" if isinstance(series.dtype, pd.StringDtype) else "O"
            values = [None if value is None or (isinstance(value, float) and not np.isfinite(value)) else
                      _plain(value) if isinstance(value, np.generic) else value if isinstance(value, (str, int, float, bool)) else str(value)
                      for value in series]
        columns.append({"name": str(name), "kind": kind, "values": values})
    return {"columns": columns}


def _data(sheet: DataSheet, experiment_dir: str | None) -> dict:
    return {
        "id": sheet.id,
        "name": sheet.name,
        "title": sheet.table.title,
        "paired": bool(sheet.table.paired),
        "columns": [{"name": column.name, "values": _numbers(column.values)} for column in sheet.table.columns],
        "row_labels": [str(label) for label in sheet.row_labels],
        "spec": asdict(sheet.spec) if sheet.spec is not None else None,
        "origin": sheet.origin,
        "experiment": experiment_dir if sheet.origin == "experiment" else None,
        "images": _frame(sheet.images),
        "units": _frame(sheet.units),
        "notes": list(sheet.notes),
        "warnings": list(sheet.warnings),
        "pseudoreplicated": bool(sheet.pseudoreplicated),
        "description": sheet.description,
        "cleaned_from": sheet.cleaned_from,
        "cleaned_options": dict(sheet.cleaned_options),
        "processed_from": sheet.processed_from,
        "process": dict(sheet.process),
        "grouped": None if sheet.grouped is None else {
            "rows": list(sheet.grouped.rows), "columns": list(sheet.grouped.columns),
            "values": [[_numbers(cell) for cell in row] for row in sheet.grouped.values]},
        "contingency": None if sheet.contingency is None else {
            "rows": list(sheet.contingency.rows), "columns": list(sheet.contingency.columns),
            "counts": [_numbers(row) for row in sheet.contingency.counts]},
        "xy": None if sheet.xy is None else {
            "x_title": sheet.xy.x_title, "x": _numbers(sheet.xy.x), "sets": list(sheet.xy.sets),
            "values": [[_numbers(cell) for cell in row] for row in sheet.xy.values]},
        "replicates": None if sheet.replicates is None else {
            "names": list(sheet.replicates.names), "values": [_numbers(column) for column in sheet.replicates.values],
            "labels": list(sheet.replicates.labels), "summary": sheet.replicates.summary},
    }


def to_dict(project: Project, experiment_dir: str | Path | None = None, current: str | None = None) -> dict:
    """The project as plain data. ``experiment_dir`` is the folder of the experiment open in CellQuant, so that tables
    imported from it can find it again."""

    folder = str(experiment_dir) if experiment_dir else None
    return {
        "format": FORMAT,
        "version": VERSION,
        "data": [_data(sheet, folder) for sheet in project.data.values()],
        "results": [{"id": sheet.id, "name": sheet.name, "data_id": sheet.data_id, "kind": sheet.kind, "options": dict(sheet.options)}
                    for sheet in project.results.values()],
        "graphs": [{"id": sheet.id, "name": sheet.name, "data_id": sheet.data_id, "kind": sheet.kind, "format": sheet.format.to_dict(),
                    "result_id": sheet.result_id} for sheet in project.graphs.values()],
        "layouts": [{"id": sheet.id, "name": sheet.name, "page": dict(sheet.page), "graphs": list(sheet.graphs)}
                    for sheet in project.layouts.values()],
        "current": current,
    }


def _dumps(content: dict) -> str:
    return json.dumps(content, default=_plain, allow_nan=False, sort_keys=True, ensure_ascii=False)


def fingerprint(project: Project, experiment_dir: str | Path | None = None) -> str:
    """Changes when anything that would be saved changes: compare it with the one taken at the last save."""

    return hashlib.sha1(_dumps(to_dict(project, experiment_dir)).encode("utf-8")).hexdigest()


def save(project: Project, path: str | Path, experiment_dir: str | Path | None = None, current: str | None = None) -> Path:
    """Write the project to ``path`` (``.cqstats`` is added when missing). The old file is replaced only once the new
    one is complete."""

    from cellquant import __version__

    project.flush_fits()  # a fit still waiting for typing to pause is saved as it will be shown

    path = Path(path)
    if path.suffix.lower() != SUFFIX:
        path = path.with_name(path.name + SUFFIX)
    content = to_dict(project, experiment_dir, current)
    content["saved_with"] = f"CellQuant {__version__}"
    content["saved_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    text = _dumps(content)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(MEMBER, text)
        temporary.replace(path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise CellQuantError(f"The project could not be saved to {path}: {exc.strerror or exc}.") from exc
    return path


# --- Reading -------------------------------------------------------------------------------------------------------------


def _values(values) -> np.ndarray:
    return np.array([np.nan if value is None else value for value in values or []], dtype=float)


def _read_frame(data: dict | None) -> pd.DataFrame | None:
    if data is None:
        return None
    columns = {}
    for column in data.get("columns", []):
        kind, values = column.get("kind", "O"), column.get("values", [])
        if kind == "f":
            columns[column["name"]] = _values(values)
        elif kind == "i":
            columns[column["name"]] = np.array(values, dtype=np.int64)
        elif kind == "b":
            columns[column["name"]] = np.array(values, dtype=bool)
        else:
            series = pd.Series(values, dtype=object)
            if kind == "s":
                series = series.astype(_STRING)
            columns[column["name"]] = series
    return pd.DataFrame(columns)


def _string_dtype():
    try:
        return pd.StringDtype(na_value=np.nan)  # pandas' default for text (pandas 3)
    except TypeError:
        return object


_STRING = _string_dtype()


def _spec(data: dict | None) -> ImportSpec | None:
    if not data:
        return None
    known = {item.name for item in fields(ImportSpec)}
    return ImportSpec(**{key: value for key, value in data.items() if key in known})


def read(path: str | Path) -> dict:
    """The plain data of a project file; a clear message when it is not one or comes from a newer CellQuant."""

    path = Path(path)
    try:
        with zipfile.ZipFile(path) as archive:
            text = archive.read(MEMBER).decode("utf-8")
        content = json.loads(text)
    except FileNotFoundError as exc:
        raise CellQuantError(f"{path} does not exist.") from exc
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
        raise CellQuantError(f"{path.name} is not a QuantAnalysis project file, or it is damaged.") from exc
    if not isinstance(content, dict) or content.get("format") != FORMAT:
        raise CellQuantError(f"{path.name} is not a QuantAnalysis project file.")
    if int(content.get("version", 0)) > VERSION:
        raise CellQuantError(f"{path.name} was saved by a newer CellQuant ({content.get('saved_with', 'unknown version')}). "
                             "Update CellQuant to open it.")
    return content


def load(path: str | Path, project: Project, experiment_dir: str | Path | None = None,
         content: dict | None = None) -> tuple[str | None, list[str]]:
    """Replace ``project``'s sheets with the file's and compute every analysis again. Tables imported from an experiment
    keep following it when that experiment is the one open (``experiment_dir``); otherwise they follow its results
    folder. Returns the sheet that was shown when saved, and notes on anything that could not be read. ``content`` is
    the file's data when it was already read (``read``)."""

    content = read(path) if content is None else content
    notes: list[str] = []
    open_folder = _same_folder(experiment_dir)
    data: dict[str, DataSheet] = {}
    for item in content.get("data", []):
        try:
            table = ColumnTable(title=item.get("title", ""), paired=bool(item.get("paired")),
                                columns=[Column(column["name"], _values(column.get("values"))) for column in item.get("columns", [])])
            origin = item.get("origin", "")
            if origin == "experiment":
                saved = item.get("experiment")
                origin = "experiment" if saved and _same_folder(saved) == open_folder and open_folder else (saved or "")
            sheet = DataSheet(item.get("name", "Data"), table, row_labels=list(item.get("row_labels", [])), id=item["id"],
                              spec=_spec(item.get("spec")), origin=origin, images=_read_frame(item.get("images")),
                              units=_read_frame(item.get("units")), notes=list(item.get("notes", [])),
                              warnings=list(item.get("warnings", [])), pseudoreplicated=bool(item.get("pseudoreplicated")),
                              description=item.get("description", ""), cleaned_from=item.get("cleaned_from"),
                              cleaned_options=dict(item.get("cleaned_options") or {}), processed_from=item.get("processed_from"),
                              process=dict(item.get("process") or {}))
            grouped = item.get("grouped")
            if isinstance(grouped, dict):
                set_grouped(sheet, GroupedTable(table.title, list(grouped["rows"]), list(grouped["columns"]),
                                                np.array([[_values(cell) for cell in row] for row in grouped["values"]], dtype=float)
                                                .reshape(len(grouped["rows"]), len(grouped["columns"]), -1)))
            counts = item.get("contingency")
            if isinstance(counts, dict):
                set_contingency(sheet, ContingencyTable(table.title, list(counts["rows"]), list(counts["columns"]),
                                                        np.array([_values(row) for row in counts["counts"]], dtype=float)))
            xy = item.get("xy")
            if isinstance(xy, dict):
                x = _values(xy["x"])
                set_xy(sheet, XYTable(table.title, str(xy.get("x_title") or "X"), x, list(xy["sets"]),
                                      np.array([[_values(cell) for cell in row] for row in xy["values"]], dtype=float)
                                      .reshape(x.size, len(xy["sets"]), -1)))
            replicates = item.get("replicates")
            if isinstance(replicates, dict):
                try:
                    set_replicates(sheet, ReplicateTable(table.title, [str(name) for name in replicates["names"]],
                                                         [_values(column) for column in replicates["values"]],
                                                         ["" if label is None else str(label) for label in replicates.get("labels") or []],
                                                         str(replicates.get("summary") or "mean"), table.paired))
                except (KeyError, TypeError, ValueError, OverflowError, CellQuantError) as exc:  # keep the replicate means
                    notes.append(f"The values of {item.get('name', '?')} could not be read ({exc}); it keeps one value per replicate.")
        except (KeyError, TypeError, ValueError, OverflowError, CellQuantError) as exc:
            notes.append(f"A data table ({item.get('name', '?')}) could not be read: {exc}")
            continue
        data[sheet.id] = sheet
    results: dict[str, ResultSheet] = {}
    for item in content.get("results", []):
        if item.get("kind") not in RESULT_KINDS or (item.get("data_id") and item.get("data_id") not in data):
            notes.append(f"The analysis {item.get('name', '?')} was left out: its data table or kind is missing.")
            continue
        sheet = ResultSheet(item.get("name", "Results"), item.get("data_id") or "", item["kind"], dict(item.get("options") or {}), id=item["id"])
        results[sheet.id] = sheet
    graph_sheets: dict[str, GraphSheet] = {}
    from cellquant.gui.quantanalysis import graphs

    for item in content.get("graphs", []):
        if item.get("kind") not in graphs.KINDS or item.get("data_id") not in data:
            notes.append(f"The graph {item.get('name', '?')} was left out: its data table or kind is missing.")
            continue
        result_id = item.get("result_id") if item.get("result_id") in results else None
        sheet = GraphSheet(item.get("name", "Graph"), item["data_id"], item["kind"], item.get("format") or {}, result_id, id=item["id"])
        graph_sheets[sheet.id] = sheet
    for sheet in data.values():
        if sheet.cleaned_from not in data:
            sheet.cleaned_from = None
        if sheet.processed_from not in data or sheet.process.get("kind") not in transforms.PROCESSES:
            sheet.processed_from = None
    layouts: dict[str, LayoutSheet] = {}
    items = content.get("layouts")
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            notes.append("A page layout could not be read.")
            continue
        try:
            graph_ids = item.get("graphs") if isinstance(item.get("graphs"), list) else []
            sheet = LayoutSheet(str(item.get("name", "Layout")), dict(item["page"]) if isinstance(item.get("page"), dict) else {},
                                [graph_id if isinstance(graph_id, str) and graph_id in graph_sheets else None for graph_id in graph_ids],
                                id=str(item["id"]))
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            notes.append(f"A page layout ({item.get('name', '?')}) could not be read: {exc}")
            continue
        layouts[sheet.id] = sheet
    reserve_ids([*data, *results, *graph_sheets, *layouts])
    project.replace(data, results, graph_sheets, layouts)
    current = content.get("current")
    return (current if project.sheet(current or "") is not None else None), notes


def _same_folder(folder) -> str | None:
    if not folder:
        return None
    try:
        return os.path.normcase(str(Path(folder).resolve()))
    except OSError:
        return os.path.normcase(str(folder))


def default_name(experiment_dir: str | Path | None) -> str:
    """A file name for a new project: the experiment's folder name, else "QuantAnalysis project"."""

    stem = Path(experiment_dir).name if experiment_dir else ""
    stem = re.sub(r'[<>:"/\\|?*]+', "_", stem).strip(" .") or "QuantAnalysis project"
    return stem + SUFFIX
