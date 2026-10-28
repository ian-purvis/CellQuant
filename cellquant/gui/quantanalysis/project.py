"""The QuantAnalysis project: data tables, the results computed from them, and graphs.

Every result and graph is linked to one data table. Editing the table (or a
fresh import of the experiment it came from) recomputes its results and redraws its graphs. No Qt
widgets here; the window listens to ``Project.changed``.
"""

from __future__ import annotations

import itertools
import logging
import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from qtpy.QtCore import QObject, QTimer, Signal
from scipy import special

from cellquant.errors import CellQuantError
from cellquant.gui.quantanalysis.graph_format import GraphFormat, as_format
from cellquant.stats import (
    AnalysisResult,
    ColumnTable,
    column_statistics_table,
    compare,
    identify_outliers,
    process,
    lognormality,
    normality,
    one_way,
    p_value_stack,
)
from cellquant.stats import transforms
from cellquant.stats.contingency import ContingencyTable, contingency
from cellquant.stats.curvefit import nonlinear_regression
from cellquant.stats.grouped import GroupedTable, two_way_anova
from cellquant.stats.normality import TESTS as NORMALITY_TESTS
from cellquant.stats.replicates import ReplicateTable
from cellquant.stats.xy import XYTable, linear_regression, xy_correlation

LOG = logging.getLogger("cellquant.quantanalysis")

_ids = itertools.count(1)

# key: (name in menus, paired, family)
COMPARE_TESTS = {
    "unpaired_t": ("Unpaired t test", False, "parametric"),
    "welch_t": ("Welch's t test", False, "parametric"),
    "lognormal_t": ("Unpaired t test on logs (lognormal)", False, "parametric"),
    "mann_whitney": ("Mann-Whitney test", False, "nonparametric"),
    "kolmogorov_smirnov": ("Kolmogorov-Smirnov test", False, "nonparametric"),
    "paired_t": ("Paired t test", True, "parametric"),
    "ratio_paired_t": ("Ratio paired t test", True, "parametric"),
    "wilcoxon": ("Wilcoxon matched-pairs test", True, "nonparametric"),
}
# key: (name in menus, paired, family); the keys of cellquant.stats.ANOVA_TESTS that the dialog offers
ANOVA_NAMES = {
    "ordinary": ("Ordinary one-way ANOVA", False, "parametric"),
    "welch": ("Welch and Brown-Forsythe ANOVA", False, "parametric"),
    "repeated_measures": ("Repeated-measures ANOVA", True, "parametric"),
    "kruskal_wallis": ("Kruskal-Wallis test", False, "nonparametric"),
    "friedman": ("Friedman test", True, "nonparametric"),
}
OUTLIER_NAMES = {"rout": "ROUT", "grubbs": "Grubbs' test", "iterative_grubbs": "Iterative Grubbs' test"}
# Tests whose difference and CI (B - A, in the data's units) an estimation plot can show.
ESTIMATION_TESTS = {"unpaired_t", "welch_t", "paired_t"}

PSEUDO_WARNING = (
    "Pseudoreplication: each value is one object (technical replicate n), not one biological replicate (N). "
    "The P value treats every cell as independent, so it is far too small for a claim about conditions."
)


def next_id(prefix: str) -> str:
    return f"{prefix}{next(_ids)}"


def reserve_ids(ids) -> None:
    """New sheets get ids after these (sheets read from a project file keep theirs)."""

    global _ids
    numbers = [int(match.group(1)) for value in ids if (match := re.search(r"(\d+)$", str(value)))]
    if numbers:
        following = next(_ids)
        _ids = itertools.count(max(following, max(numbers) + 1))


@dataclass
class DataSheet:
    name: str
    table: ColumnTable
    row_labels: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: next_id("data"))
    # Set when the table was imported from CellQuant results; dropped when the table is edited by hand.
    spec: object | None = None          # sources.ImportSpec
    origin: str = ""                    # "experiment" (the open experiment: follows it) or a folder path
    images: pd.DataFrame | None = None  # per-image values (group, unit, image, value), for SuperPlots
    units: pd.DataFrame | None = None   # per-replicate values (group, dot, value): what the tests use
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    pseudoreplicated: bool = False
    description: str = ""
    stale: bool = False                 # the experiment changed since the import
    cleaned_from: str | None = None     # the data table this one is a cleaned copy of (flagged outliers blank); follows it
    cleaned_options: dict = field(default_factory=dict)  # the outlier method and its Q or alpha
    processed_from: str | None = None   # the data table this one is computed from (transform, normalize…); follows it
    process: dict = field(default_factory=dict)          # the processing: "kind" and its options (cellquant.stats.transforms)
    grouped: GroupedTable | None = None  # a grouped table (two factors); ``table`` is then its cells as columns ("Row: Data set")
    xy: XYTable | None = None           # an XY table (X and Y data sets); ``table`` is then X and each data set's row means
    replicates: ReplicateTable | None = None  # every value with its biological replicate; ``table`` is then each replicate's mean (N)
    contingency: ContingencyTable | None = None  # counts in rows × columns; ``table`` is then one column of counts per outcome


@dataclass
class ResultSheet:
    name: str
    data_id: str
    kind: str                           # "compare", "anova", "colstats", "normality", "outliers" or "pstack" (no data table)
    options: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: next_id("result"))
    result: AnalysisResult | None = None   # compare, anova, outliers, pstack
    wide: pd.DataFrame | None = None       # colstats / normality: rows (section, statistic), one column per data column
    warnings: list[str] = field(default_factory=list)
    methods: str = ""
    error: str = ""


@dataclass
class GraphSheet:
    name: str
    data_id: str
    kind: str                           # a key of graphs.KINDS
    format: GraphFormat = field(default_factory=GraphFormat)  # every format setting; a plain dict (older code, a file) is read into one
    result_id: str | None = None        # the comparison (two columns or ANOVA) whose P brackets / difference it shows
    id: str = field(default_factory=lambda: next_id("graph"))

    def __post_init__(self):
        self.format = as_format(self.format)


@dataclass
class LayoutSheet:
    name: str
    page: dict = field(default_factory=dict)   # paper, orientation, grid, margins, panel letters, title (layouts.DEFAULT_PAGE)
    graphs: list = field(default_factory=list)  # a graph id (or None: empty) per cell, row by row
    id: str = field(default_factory=lambda: next_id("layout"))


FIT_DELAY_MS = 400  # how long typing must pause before curve fits are refitted


class Project(QObject):
    """Every sheet, and the links between them. ``changed(id)`` names the sheet that changed ("" for the list)."""

    changed = Signal(str)
    modified = Signal()  # anything that would be saved may have changed (also a graph's format, a name)

    def __init__(self):
        super().__init__()
        self.data: dict[str, DataSheet] = {}
        self.results: dict[str, ResultSheet] = {}
        self.graphs: dict[str, GraphSheet] = {}
        self.layouts: dict[str, LayoutSheet] = {}
        self._refreshing: set[str] = set()
        self._renamed_from: dict[str, str] = {}  # sheet id -> its name before a rename that its processed tables follow
        self.changed.connect(lambda _sheet_id: self.modified.emit())
        # Curve fits are refitted once typing pauses, not on every cell edit.
        self._pending_fits: set[str] = set()
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(FIT_DELAY_MS)
        self._fit_timer.timeout.connect(self.flush_fits)

    def touch(self) -> None:
        self.modified.emit()

    def replace(self, data: dict, results: dict, graphs: dict, layouts: dict | None = None) -> None:
        """Every sheet at once (a project file opened, or a new project): analyses are computed again."""

        self.data, self.results, self.graphs, self.layouts = dict(data), dict(results), dict(graphs), dict(layouts or {})
        for sheet in self.results.values():
            self.compute(sheet)
        self.changed.emit("")

    def sheet(self, sheet_id: str):
        return self.data.get(sheet_id) or self.results.get(sheet_id) or self.graphs.get(sheet_id) or self.layouts.get(sheet_id)

    def unique_name(self, base: str) -> str:
        names = {sheet.name for sheet in (*self.data.values(), *self.results.values(), *self.graphs.values(), *self.layouts.values())}
        if base not in names:
            return base
        number = 2
        while f"{base} ({number})" in names:
            number += 1
        return f"{base} ({number})"

    def add_data(self, sheet: DataSheet) -> DataSheet:
        sheet.name = self.unique_name(sheet.name)
        self.data[sheet.id] = sheet
        LOG.info("data table %s added: %s", sheet.name, sheet.description or "")
        self.changed.emit("")
        return sheet

    def add_result(self, sheet: ResultSheet) -> ResultSheet:
        sheet.name = self.unique_name(sheet.name)
        self.results[sheet.id] = sheet
        self.compute(sheet)
        self.changed.emit("")
        self._flags_changed(sheet)
        return sheet

    def add_cleaned(self, result: ResultSheet) -> DataSheet | None:
        """A new data table that follows ``result.data_id`` with the outliers it flagged left blank. The original is not changed."""

        source = self.data.get(result.data_id)
        if source is None or result.result is None:
            return None
        sheet = DataSheet(f"{source.name} (cleaned)", result.result.values["cleaned"], row_labels=list(source.row_labels),
                          cleaned_from=source.id, cleaned_options=dict(result.options), pseudoreplicated=source.pseudoreplicated,
                          warnings=list(source.warnings), units=None, images=None)
        sheet.description = f"{source.name} with the values flagged by {self.cleaned_label(result.options)} left blank. Follows {source.name}."
        return self.add_data(sheet)

    def add_processed(self, source: DataSheet, options: dict) -> DataSheet:
        """A new data table computed from ``source`` (transformed, normalized…) that follows it; ``source`` is not changed.
        Raises CellQuantError when the options do not fit the table."""

        table, notes = process(source.table, options)
        sheet = DataSheet(f"{transforms.label(options)}: {source.name}", table, row_labels=list(source.row_labels),
                          processed_from=source.id, process=dict(options), pseudoreplicated=source.pseudoreplicated,
                          warnings=list(source.warnings), notes=notes)
        sheet.description = self.processed_description(sheet, source)
        return self.add_data(sheet)

    @staticmethod
    def processed_description(sheet: DataSheet, source: DataSheet) -> str:
        kind = transforms.PROCESSES.get(sheet.process.get("kind"), "Processing")
        return f"{kind} of {source.name}: {sheet.notes[0] if sheet.notes else ''} Follows {source.name}."

    def refresh_processed(self, sheet: DataSheet, renamed: dict[str, str] | None = None) -> None:
        """A processed table follows its source: compute it again with the same options. ``renamed`` (old column
        name -> new) renames the source columns its options name. When it cannot be computed again (a column it
        uses was removed, for example) its values are blanked and a warning says why, so nothing looks current."""

        source = self.data.get(sheet.processed_from or "")
        if source is None or sheet.id in self._refreshing:  # a loop of tables computed from each other stops here
            return
        self._refreshing.add(sheet.id)
        try:
            if renamed:
                source_name = self._renamed_from.get(source.id, source.name)
                named = sheet.name == f"{transforms.label(sheet.process)}: {source_name}"
                sheet.process = transforms.renamed(sheet.process, renamed)
                if named:
                    self._renamed_from[sheet.id] = sheet.name
                    sheet.name = ""
                    sheet.name = self.unique_name(f"{transforms.label(sheet.process)}: {source.name}")
            before = sheet.table.names
            failed = ""
            try:
                sheet.table, sheet.notes = process(source.table, sheet.process)
            except CellQuantError as exc:
                failed = str(exc)
                sheet.table = table_with(sheet.table, before, [np.full(len(column.values), np.nan) for column in sheet.table.columns],
                                         sheet.table.paired)
                sheet.notes = [f"Could not be computed again: {exc}"]
                LOG.info("%s: %s", sheet.name, exc)
            sheet.row_labels = list(source.row_labels)
            sheet.pseudoreplicated, sheet.warnings = source.pseudoreplicated, list(source.warnings)
            if failed:
                sheet.warnings.append(f"Out of date: could not be computed again from {source.name} ({failed}) and is blank. "
                                      "Use Change processing… or undo the change to it.")
            sheet.description = self.processed_description(sheet, source)
            after = sheet.table.names
            self.data_changed(sheet.id, {old: new for old, new in zip(before, after) if old != new} if len(before) == len(after) else None)
        finally:
            self._refreshing.discard(sheet.id)
            self._renamed_from.pop(sheet.id, None)

    def change_process(self, sheet: DataSheet, options: dict) -> None:
        """New processing options for a processed table (raises CellQuantError when they do not fit its source)."""

        source = self.data[sheet.processed_from]
        process(source.table, options)
        named = sheet.name == f"{transforms.label(sheet.process)}: {source.name}"
        sheet.process = dict(options)
        if named:  # still the name it was given: name it after the new processing
            sheet.name = ""
            sheet.name = self.unique_name(f"{transforms.label(options)}: {source.name}")
        self.refresh_processed(sheet)
        self.changed.emit("")

    @staticmethod
    def cleaned_label(options: dict) -> str:
        return OUTLIER_NAMES[options.get("method", "rout")]

    def flags(self, data_id: str) -> dict[str, np.ndarray]:
        """The outliers of the newest Identify outliers result for this table: column name -> mask over the column's cells."""

        for sheet in reversed(list(self.results.values())):
            if sheet.data_id == data_id and sheet.kind == "outliers" and sheet.result is not None:
                return sheet.result.values["masks"]
        return {}

    def _flags_changed(self, sheet: ResultSheet) -> None:
        """Flagged values show in the data grid and on graphs of the table: they redraw at once."""

        if sheet.kind != "outliers":
            return
        self.changed.emit(sheet.data_id)
        for graph in self.graphs.values():
            if graph.data_id == sheet.data_id:
                self.changed.emit(graph.id)

    def add_graph(self, sheet: GraphSheet) -> GraphSheet:
        sheet.name = self.unique_name(sheet.name)
        self.graphs[sheet.id] = sheet
        self.changed.emit("")
        return sheet

    def add_layout(self, sheet: LayoutSheet) -> LayoutSheet:
        sheet.name = self.unique_name(sheet.name)
        self.layouts[sheet.id] = sheet
        self.changed.emit("")
        return sheet

    def remove(self, sheet_id: str) -> None:
        """Remove a sheet; a data table takes its results and graphs with it, and a graph leaves its layout cells empty."""

        if sheet_id in self.data:
            for result in [item for item in self.results.values() if item.data_id == sheet_id]:
                self.results.pop(result.id)
            for graph in [item for item in self.graphs.values() if item.data_id == sheet_id]:
                self.graphs.pop(graph.id)
            self.data.pop(sheet_id)
            for other in self.data.values():
                if other.cleaned_from == sheet_id:
                    other.cleaned_from = None
                if other.processed_from == sheet_id:
                    other.processed_from = None
        removed = self.results.pop(sheet_id, None)
        self.graphs.pop(sheet_id, None)
        self.layouts.pop(sheet_id, None)
        for layout in self.layouts.values():
            layout.graphs = [graph_id if graph_id in self.graphs else None for graph_id in layout.graphs]
        for graph in self.graphs.values():
            if graph.result_id == sheet_id:
                graph.result_id = None
        self.changed.emit("")
        if removed is not None:
            self._flags_changed(removed)

    def linked(self, data_id: str) -> tuple[list[ResultSheet], list[GraphSheet]]:
        return ([item for item in self.results.values() if item.data_id == data_id],
                [item for item in self.graphs.values() if item.data_id == data_id])

    def data_changed(self, data_id: str, renamed: dict[str, str] | None = None) -> None:
        """The table changed: recompute its results, redraw its graphs (live links). ``renamed``: its columns renamed,
        old name -> new, so the tables computed from it follow the new names."""

        results, graphs = self.linked(data_id)
        for result in results:
            if result.kind == "curvefit" and result.result is not None:
                self._pending_fits.add(result.id)
                self._fit_timer.start()
            else:
                self.compute(result)
        self.changed.emit(data_id)
        for result in results:
            self.changed.emit(result.id)
        for graph in graphs:
            self.changed.emit(graph.id)
        for copy in [item for item in self.data.values() if item.cleaned_from == data_id]:
            self.refresh_cleaned(copy)
        for copy in [item for item in self.data.values() if item.processed_from == data_id]:
            self.refresh_processed(copy, renamed)

    def flush_fits(self) -> None:
        """Refit the curve fits whose table changed since they were last fitted, and redraw what shows them."""

        self._fit_timer.stop()
        pending, self._pending_fits = self._pending_fits, set()
        for result_id in pending:
            result = self.results.get(result_id)
            if result is None:
                continue
            self.compute(result)
            self.changed.emit(result.id)
            for graph in self.graphs.values():
                if graph.result_id == result.id:
                    self.changed.emit(graph.id)

    def refresh_cleaned(self, sheet: DataSheet) -> None:
        """The cleaned copy follows its source: flag the outliers again and blank them."""

        source = self.data.get(sheet.cleaned_from or "")
        if source is None:
            return
        options = sheet.cleaned_options
        try:
            result = run_outliers(source, options)
        except CellQuantError as exc:
            LOG.info("%s: %s", sheet.name, exc)
            return
        sheet.table = result.values["cleaned"]
        sheet.row_labels = list(source.row_labels)
        sheet.pseudoreplicated = source.pseudoreplicated
        self.data_changed(sheet.id)

    def rerun(self, result: ResultSheet) -> None:
        self.compute(result)
        self.changed.emit(result.id)
        for graph in self.graphs.values():
            if graph.result_id == result.id:
                self.changed.emit(graph.id)
        self._flags_changed(result)

    def compute(self, sheet: ResultSheet) -> None:
        data = self.data.get(sheet.data_id)
        sheet.result, sheet.wide, sheet.warnings, sheet.methods, sheet.error = None, None, [], "", ""
        try:
            if sheet.kind in ("compare", "anova", "outliers", "twoway"):
                run = {"compare": run_compare, "anova": run_anova, "outliers": run_outliers, "twoway": run_two_way}[sheet.kind]
                sheet.result = run(data, sheet.options)
                sheet.warnings = list(sheet.result.warnings)
                sheet.methods = sheet.result.methods
            elif sheet.kind == "contingency":
                sheet.result = run_contingency(data, sheet.options)
                sheet.wide = sheet.result.values["wide"]
                sheet.warnings = list(sheet.result.warnings)
                sheet.methods = sheet.result.methods
            elif sheet.kind in ("linreg", "xycorr", "curvefit"):
                run = {"linreg": run_linear_regression, "xycorr": run_xy_correlation, "curvefit": run_curve_fit}[sheet.kind]
                sheet.result = run(data, sheet.options)
                sheet.wide = sheet.result.values["wide"]
                sheet.warnings = list(sheet.result.warnings)
                sheet.methods = sheet.result.methods
            elif sheet.kind == "pstack":
                sheet.result = run_pstack(sheet.options)
                sheet.warnings = list(sheet.result.warnings)
                sheet.methods = sheet.result.methods
            elif sheet.kind == "colstats":
                sheet.wide, sheet.warnings, sheet.methods = run_column_statistics(data, sheet.options)
            elif sheet.kind == "normality":
                sheet.wide, sheet.warnings, sheet.methods = run_normality(data, sheet.options)
            if data is not None and data.pseudoreplicated and PSEUDO_WARNING not in sheet.warnings:
                sheet.warnings.insert(0, PSEUDO_WARNING)
            if data is not None and data.replicates is not None and sheet.kind != "pstack":
                sheet.methods = f"{replicate_methods(data)} {sheet.methods}".strip()
        except CellQuantError as exc:
            sheet.error = str(exc)
        except Exception as exc:  # noqa: BLE001 - kept in the log; the sheet says it could not be computed
            LOG.exception("analysis %s failed", sheet.name)
            sheet.error = f"This analysis could not be computed ({type(exc).__name__}). Details are in Help → Log."
        if sheet.error:
            LOG.info("%s: %s", sheet.name, sheet.error)


# --- Running analyses ------------------------------------------------------------------------------------------


def table_signature(table) -> list[int]:
    """What Advanced settings are chosen for: the values per group of a column table (per cell of a grouped table, per data
    set of an XY table). Saved with the settings, so a later change of the table is flagged."""

    if table is None:
        return []
    if isinstance(table, GroupedTable):
        return [int(np.isfinite(table.values[i, j]).sum()) for i in range(len(table.rows)) for j in range(len(table.columns))]
    if isinstance(table, XYTable):
        return [int(table.points(j)[0].size) for j in range(len(table.sets))]
    return [int(column.finite.size) for column in table.columns]


def _hand_set_notes(result: AnalysisResult, table, options: dict) -> None:
    """Warnings the options carry: settings set by hand for a different table, and the guide's answer that the values are
    images or cells of shared samples."""

    was = options.get("advanced_for")
    if was is not None and any(options.get(key) is not None for key in ("df", "difference", "epsilon", "sd", "m")):
        now = table_signature(table)
        if list(was) != now:
            result.warnings.insert(0, f"The settings set by hand under Advanced were chosen for a different table (values per group "
                                      f"{', '.join(map(str, was))}; now {', '.join(map(str, now))}). Check them with Change options….")
    what = options.get("not_independent")
    if what in ("images", "cells") and not any(text.startswith("Pseudoreplication") for text in result.warnings):
        result.warnings.insert(0, f"Pseudoreplication: each value is one of the {what} of shared samples (your answer in Help me choose…), "
                                  "so the test counts them as independent and P is too small. Use one value per biological replicate.")


def run_compare(data: DataSheet, options: dict) -> AnalysisResult:
    table = data.table
    first, second = options.get("first", 0), options.get("second", 1)
    names = table.names
    if isinstance(first, str) and first not in names or isinstance(second, str) and second not in names:
        raise CellQuantError("A column this comparison used is gone; choose the columns again (Change options…).")
    if len(table) < 2:
        raise CellQuantError("Comparing two columns needs a table with at least two columns.")
    test = options.get("test", "unpaired_t")
    kwargs = {key: options[key] for key in ("alternative", "ci", "alpha", "style") if key in options}
    # Advanced settings set by hand: the t tests take df, and the difference tested against.
    if test in ("welch_t", "unpaired_t", "lognormal_t", "paired_t", "ratio_paired_t") and options.get("df") is not None:
        kwargs["df"] = options["df"]
    if test in ("welch_t", "unpaired_t", "paired_t") and options.get("difference") not in (None, 0):
        kwargs["difference"] = options["difference"]
    result = compare(table, test, first, second, **kwargs)
    _small_n_warning(result, table, test, first, second, kwargs.get("alpha", 0.05))
    _hand_set_notes(result, table, options)
    return result


ANOVA_KEYS = ("alpha", "ci", "style", "comparisons", "method", "family", "pairs", "geisser_greenhouse", "df", "sd", "m", "epsilon")
# The Advanced settings each one-way test takes (others are dropped: a saved option the test cannot use is ignored).
ANOVA_OVERRIDES = {"ordinary": ("df", "sd", "m"), "welch": ("df", "m"), "repeated_measures": ("df", "sd", "m", "epsilon"),
                   "kruskal_wallis": ("m",), "friedman": ("m",)}


def run_anova(data: DataSheet, options: dict) -> AnalysisResult:
    """One-way ANOVA, Kruskal-Wallis or Friedman by the options of the Compare three or more columns dialog."""

    table = data.table
    if len(table) < 3:
        raise CellQuantError("Comparing three or more columns needs a table with at least three columns.")
    test = options.get("test", "ordinary")
    names = table.names
    control = [name for name in _as_list(options.get("control")) if name]
    gone = [name for name in [*control, *(name for pair in options.get("pairs") or [] for name in pair)] if name not in names]
    if gone:
        raise CellQuantError(f"A column this comparison used is gone ({', '.join(gone)}); choose the groups again (Change options…).")
    rank = test in ("kruskal_wallis", "friedman")
    kwargs = {key: options[key] for key in ANOVA_KEYS if key in options and options[key] is not None}
    for key in ("df", "sd", "m", "epsilon"):
        if key not in ANOVA_OVERRIDES.get(test, ()):
            kwargs.pop(key, None)
    if test != "repeated_measures":
        kwargs.pop("geisser_greenhouse", None)
    if rank:
        kwargs.pop("ci", None)
    goal = kwargs.get("comparisons")
    if goal == "control":
        kwargs["control"] = control if len(control) != 1 else control[0]
    if goal == "selected":
        kwargs["pairs"] = [tuple(pair) for pair in options.get("pairs") or []]
    else:
        kwargs.pop("pairs", None)
    if goal in (None, "none"):
        kwargs.pop("comparisons", None)
        kwargs.pop("method", None)
    if goal in (None, "none", "trend"):
        kwargs.pop("sd", None)
        kwargs.pop("m", None)
    if goal != "control":
        kwargs.pop("family", None)
    result = one_way(table, test, **kwargs)
    _hand_set_notes(result, table, options)
    return result


TWO_WAY_KEYS = ("interaction", "compare", "goal", "method", "control", "family", "alpha", "ci", "style", "df")


def run_two_way(data: DataSheet, options: dict) -> AnalysisResult:
    """Two-way ANOVA of a grouped table by the options of the Two-way ANOVA dialog."""

    if data is None or data.grouped is None:
        raise CellQuantError("Two-way ANOVA needs a grouped table (rows × data sets). Make one with File → New grouped table, "
                             "or import results with a data set factor.")
    grouped = data.grouped
    kwargs = {key: options[key] for key in TWO_WAY_KEYS if key in options and options[key] is not None}
    if kwargs.get("compare") in (None, "none"):
        for key in ("compare", "goal", "method", "control", "family"):
            kwargs.pop(key, None)
    elif kwargs.get("goal") != "control":
        kwargs.pop("control", None)
    else:
        inner = grouped.rows if kwargs["compare"] in ("within_columns", "row_means") else grouped.columns
        if kwargs.get("control") not in inner:
            raise CellQuantError(f"The reference {kwargs.get('control')!r} is gone; choose it again (Change options…).")
    result = two_way_anova(grouped, **kwargs)
    _hand_set_notes(result, grouped, options)
    return result


LINREG_KEYS = ("through_origin", "replicates", "ci", "alpha", "style", "df")
XYCORR_KEYS = ("method", "replicates", "ci", "alpha", "style", "df")
CURVEFIT_KEYS = ("model", "fixed", "replicates", "ci", "alpha", "style", "compare", "compare_model", "compare_fixed", "compare_parameter",
                 "interpolate")


def _xy_of(data: DataSheet | None, what: str) -> XYTable:
    if data is None or data.xy is None:
        raise CellQuantError(f"{what} needs an XY table (X and Y data sets). Make one with File → New XY table.")
    return data.xy


def run_linear_regression(data: DataSheet, options: dict) -> AnalysisResult:
    kwargs = {key: options[key] for key in LINREG_KEYS if key in options and options[key] is not None}
    result = linear_regression(_xy_of(data, "Linear regression"), **kwargs)
    _hand_set_notes(result, data.xy, options)
    return result


def run_curve_fit(data: DataSheet, options: dict) -> AnalysisResult:
    kwargs = {key: options[key] for key in CURVEFIT_KEYS if key in options and options[key] is not None}
    for key in ("fixed", "compare_fixed"):  # a saved project may hold anything here
        if key in kwargs and not isinstance(kwargs[key], dict):
            kwargs.pop(key)
    for key in ("ci", "alpha"):
        try:
            kwargs[key] = float(kwargs[key])
        except (KeyError, TypeError, ValueError):
            kwargs.pop(key, None)
    for key in ("model", "compare_model", "compare_parameter", "compare", "replicates", "style"):
        if key in kwargs and not isinstance(kwargs[key], str):
            kwargs.pop(key)
    kwargs["interpolate"] = bool(kwargs.get("interpolate", False))
    return nonlinear_regression(_xy_of(data, "Nonlinear regression"), **kwargs)


def run_xy_correlation(data: DataSheet, options: dict) -> AnalysisResult:
    kwargs = {key: options[key] for key in XYCORR_KEYS if key in options and options[key] is not None}
    result = xy_correlation(_xy_of(data, "Correlation of X with Y"), **kwargs)
    _hand_set_notes(result, data.xy, options)
    return result


CONTINGENCY_KEYS = ("test", "yates", "diagnostic", "trend", "group", "outcome", "ci", "alpha", "style")


def run_contingency(data: DataSheet, options: dict) -> AnalysisResult:
    if data is None or data.contingency is None:
        raise CellQuantError("This analysis needs a contingency table (counts): File → New contingency table.")
    kwargs = {key: options[key] for key in CONTINGENCY_KEYS if key in options and options[key] is not None}
    for key in ("ci", "alpha"):
        try:
            kwargs[key] = float(kwargs[key])
        except (KeyError, TypeError, ValueError):
            kwargs.pop(key, None)
    for key in ("yates", "diagnostic", "trend"):
        kwargs[key] = bool(kwargs.get(key, False))
    for key in ("group", "outcome"):
        if not isinstance(kwargs.get(key, 0), int) or isinstance(kwargs.get(key), bool):
            kwargs.pop(key)
    for key in ("test", "style"):
        if key in kwargs and not isinstance(kwargs[key], str):
            kwargs.pop(key)
    return contingency(data.contingency, **kwargs)


def _as_list(value) -> list:
    return [] if value is None else [value] if isinstance(value, str) else list(value)


def run_outliers(data: DataSheet, options: dict) -> AnalysisResult:
    columns = [name for name in options.get("columns") or data.table.names if name in data.table.names]
    return identify_outliers(data.table, options.get("method", "rout"), q=options.get("q", 0.01), alpha=options.get("alpha", 0.05),
                             columns=columns, style=options.get("style", "classic"))


def parse_p_values(text: str) -> tuple[list[float], list[str]]:
    """P values from pasted text: one per line, with an optional label before it. Returns (values, labels)."""

    values, labels = [], []
    for number, line in enumerate(text.replace(";", "\n").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        parts = re.sub(r",\s+", " ", line.replace("\t", " ")).replace(",", ".").rsplit(None, 1)  # "0,012" is a decimal comma
        token = parts[-1].lstrip("Pp=<≤ ").strip() if parts else ""
        try:
            value = float(token)
        except ValueError:
            raise CellQuantError(f"Line {number} does not end in a P value: “{line}”.") from None
        values.append(value)
        label = re.sub(r"[=<≤\s]+$", "", parts[0]).strip() if len(parts) > 1 else ""  # "P < 0.01": no label
        labels.append(label if label.lower() not in ("", "p", "q") else f"P{len(values)}")
    return values, labels


def run_pstack(options: dict) -> AnalysisResult:
    values, labels = parse_p_values(options.get("text", ""))
    if not values:
        raise CellQuantError("Paste at least one P value.")
    if not all(0 <= value <= 1 for value in values):
        raise CellQuantError("A P value must be between 0 and 1.")
    return p_value_stack(values, options.get("method", "bky"), alpha=options.get("alpha", 0.05), labels=labels, style=options.get("style", "classic"))


def smallest_p(test: str, n_a: int, n_b: int) -> float:
    """The smallest two-tailed P an exact rank test can give with these sample sizes (no ties)."""

    if test == "mann_whitney":
        return min(1.0, 2.0 / float(special.comb(n_a + n_b, n_a)))
    if test == "wilcoxon":
        return min(1.0, 2.0 / 2.0**n_a)
    return 0.0


def _small_n_warning(result: AnalysisResult, table: ColumnTable, test: str, first, second, alpha: float) -> None:
    if test not in ("mann_whitney", "wilcoxon"):
        return
    a, b = table[first].values, table[second].values
    if test == "wilcoxon":
        rows = min(a.size, b.size)
        n = int((np.isfinite(a[:rows]) & np.isfinite(b[:rows])).sum())
        floor, sizes = smallest_p(test, n, n), f"{n} pairs"
    else:
        n_a, n_b = int(np.isfinite(a).sum()), int(np.isfinite(b).sum())
        floor, sizes = smallest_p(test, n_a, n_b), f"n = {n_a} vs {n_b}"
    if floor <= alpha:
        return
    if any("smallest" in text.casefold() or "cannot reach" in text.casefold() for text in result.warnings):
        return
    result.warnings.insert(0, f"Small n: with {sizes}, the smallest two-tailed P this test can give is {floor:.3g}, so it "
                              f"cannot reach P < {alpha:g} whatever the data. Add biological replicates, or use a t test if its "
                              "assumptions hold.")


def run_column_statistics(data: DataSheet, options: dict) -> tuple[pd.DataFrame, list[str], str]:
    from cellquant.stats import column_statistics

    ci = options.get("ci", 0.95)
    frame = column_statistics_table(data.table, ci=ci)
    warnings = []
    for column in data.table.columns:
        result = column_statistics(column.values, ci, name=column.name)
        warnings += [f"{column.name}: {text}" for text in result.warnings]
        methods = result.methods
    return frame, warnings, methods if data.table.columns else ""


def statsmodels_available() -> bool:
    try:
        import statsmodels  # noqa: F401
    except ImportError:
        return False
    return True


def run_normality(data: DataSheet, options: dict) -> tuple[pd.DataFrame, list[str], str]:
    alpha = options.get("alpha", 0.05)
    style = options.get("style", "classic")
    tests = list(NORMALITY_TESTS)
    warnings = []
    if not statsmodels_available():
        tests = [key for key in tests if key in ("dagostino", "shapiro")]
        warnings.append('The Anderson-Darling and Kolmogorov-Smirnov tests need statsmodels: pip install "cellquant[stats]"')
    out: dict[str, dict] = {}
    methods = ""
    for column in data.table.columns:
        cells = {}
        for function, prefix in ((normality, "Normality"), (lognormality, "Lognormality")):
            try:
                result = function(column.values, alpha, tests, style=style)
            except CellQuantError as exc:
                cells[(prefix, "Not run")] = str(exc)
                continue
            for section in result.sections:
                title = section.title if section.title == "Data analyzed" else f"{prefix}: {section.title}"
                for row in section.rows:
                    cells[(title, row.label)] = row.formatted
            warnings += [f"{column.name}: {text}" for text in result.warnings]
            if prefix == "Normality":
                methods = result.methods
        out[column.name] = cells
    frame = pd.DataFrame(out)
    if not frame.empty:
        frame.index.names = ["section", "statistic"]
    return frame, warnings, (methods + " Lognormality: the same tests on the natural logs.") if methods else ""


def cells_table(grouped: GroupedTable) -> ColumnTable:
    """A grouped table's cells as columns ("Row: Data set"), for the analyses and graphs of column tables."""

    from cellquant.stats import Column

    columns, names = [], set()
    for i, row in enumerate(grouped.rows):
        for j, column in enumerate(grouped.columns):
            values = grouped.values[i, j]
            filled = np.nonzero(np.isfinite(values))[0]
            name = base = f"{row}: {column}"
            number = 2
            while name in names:  # "a: b" + "c" and "a" + "b: c" read the same
                name, number = f"{base} ({number})", number + 1
            names.add(name)
            columns.append(Column(name, values[: filled[-1] + 1] if filled.size else values[:0]))
    return ColumnTable(title=grouped.title, columns=columns)


def set_grouped(sheet: DataSheet, grouped: GroupedTable) -> None:
    sheet.grouped = grouped
    sheet.table = cells_table(grouped)


def xy_columns(xy: XYTable) -> ColumnTable:
    """An XY table as columns: X, then each data set's mean per row (blank rows dropped from the end)."""

    from cellquant.stats import Column

    with np.errstate(invalid="ignore"):
        counts = np.isfinite(xy.values).sum(axis=2)
        means = np.where(counts > 0, np.nansum(xy.values, axis=2) / np.maximum(counts, 1), np.nan)
    names = [xy.x_title] + [name if name != xy.x_title else f"{name} (Y)" for name in xy.sets]
    return ColumnTable(title=xy.title, columns=[Column(name, values) for name, values in zip(names, [xy.x, *means.T])])


def set_contingency(sheet: DataSheet, table: ContingencyTable) -> None:
    from cellquant.stats import Column

    sheet.contingency = table
    sheet.table = ColumnTable(title=table.title, columns=[Column(name, table.counts[:, j]) for j, name in enumerate(table.columns)])
    sheet.row_labels = list(table.rows)


def set_xy(sheet: DataSheet, xy: XYTable) -> None:
    sheet.xy = xy
    sheet.table = xy_columns(xy)


def replicate_methods(data: DataSheet) -> str:
    """The sentence a results sheet of a replicate table starts its methods with: what one value is, N and n."""

    replicates = data.replicates
    counts = replicates.counts()
    sizes = "; ".join(f"{column.name} N={column.finite.size}, n={counts.get(column.name, 0)}" for column in data.table.columns)
    return (f"Analyzed on one value per biological replicate (each replicate's {replicates.summary} of its values), "
            f"not on the individual values ({sizes}).")


def set_replicates(sheet: DataSheet, replicates: ReplicateTable | None, values: list | None = None) -> None:
    """Give a column table a replicate per row (its analyses then use one value per replicate), or take it away
    (``None``: the table holds every value again, ``values`` when given, else the replicate table's)."""

    from cellquant.stats import Column

    if replicates is None:
        old = sheet.replicates
        sheet.replicates = None
        if old is not None:
            sheet.table = ColumnTable(title=old.title, paired=old.paired,
                                      columns=[Column(name, column) for name, column in zip(old.names, values if values is not None else old.values)])
            sheet.row_labels = []
        return
    sheet.replicates = replicates
    sheet.table = replicates.summary_table()
    sheet.row_labels = replicates.replicates()


def table_with(table: ColumnTable, names: list[str], values: list[np.ndarray], paired: bool) -> ColumnTable:
    from cellquant.stats import Column

    return ColumnTable(title=table.title, columns=[Column(name, column) for name, column in zip(names, values)], paired=paired)
