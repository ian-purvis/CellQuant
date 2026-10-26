"""The QuantAnalysis project: data tables, the results computed from them, and graphs.

Like a a commercial package project, every result and graph is linked to one data table. Editing the table (or a
fresh import of the experiment it came from) recomputes its results and redraws its graphs. No Qt
widgets here; the window listens to ``Project.changed``.
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from qtpy.QtCore import QObject, Signal
from scipy import special

from cellquant.errors import CellQuantError
from cellquant.stats import (
    AnalysisResult,
    ColumnTable,
    column_statistics_table,
    compare,
    lognormality,
    normality,
)
from cellquant.stats.normality import TESTS as NORMALITY_TESTS

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
# Tests whose difference and CI (B - A, in the data's units) an estimation plot can show.
ESTIMATION_TESTS = {"unpaired_t", "welch_t", "paired_t"}

PSEUDO_WARNING = (
    "Pseudoreplication: each value is one object (technical replicate n), not one biological replicate (N). "
    "The P value treats every cell as independent, so it is far too small for a claim about conditions."
)


def next_id(prefix: str) -> str:
    return f"{prefix}{next(_ids)}"


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


@dataclass
class ResultSheet:
    name: str
    data_id: str
    kind: str                           # "compare", "colstats" or "normality"
    options: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: next_id("result"))
    result: AnalysisResult | None = None   # compare
    wide: pd.DataFrame | None = None       # colstats / normality: rows (section, statistic), one column per data column
    warnings: list[str] = field(default_factory=list)
    methods: str = ""
    error: str = ""


@dataclass
class GraphSheet:
    name: str
    data_id: str
    kind: str                           # a key of graphs.KINDS
    format: dict = field(default_factory=dict)
    result_id: str | None = None        # the comparison whose P bracket / difference it shows
    id: str = field(default_factory=lambda: next_id("graph"))


class Project(QObject):
    """Every sheet, and the links between them. ``changed(id)`` names the sheet that changed ("" for the list)."""

    changed = Signal(str)

    def __init__(self):
        super().__init__()
        self.data: dict[str, DataSheet] = {}
        self.results: dict[str, ResultSheet] = {}
        self.graphs: dict[str, GraphSheet] = {}

    def sheet(self, sheet_id: str):
        return self.data.get(sheet_id) or self.results.get(sheet_id) or self.graphs.get(sheet_id)

    def unique_name(self, base: str) -> str:
        names = {sheet.name for sheet in (*self.data.values(), *self.results.values(), *self.graphs.values())}
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
        return sheet

    def add_graph(self, sheet: GraphSheet) -> GraphSheet:
        sheet.name = self.unique_name(sheet.name)
        self.graphs[sheet.id] = sheet
        self.changed.emit("")
        return sheet

    def remove(self, sheet_id: str) -> None:
        """Remove a sheet; a data table takes its results and graphs with it."""

        if sheet_id in self.data:
            for result in [item for item in self.results.values() if item.data_id == sheet_id]:
                self.results.pop(result.id)
            for graph in [item for item in self.graphs.values() if item.data_id == sheet_id]:
                self.graphs.pop(graph.id)
            self.data.pop(sheet_id)
        self.results.pop(sheet_id, None)
        self.graphs.pop(sheet_id, None)
        for graph in self.graphs.values():
            if graph.result_id == sheet_id:
                graph.result_id = None
        self.changed.emit("")

    def linked(self, data_id: str) -> tuple[list[ResultSheet], list[GraphSheet]]:
        return ([item for item in self.results.values() if item.data_id == data_id],
                [item for item in self.graphs.values() if item.data_id == data_id])

    def data_changed(self, data_id: str) -> None:
        """The table changed: recompute its results, redraw its graphs (a commercial package's live links)."""

        results, graphs = self.linked(data_id)
        for result in results:
            self.compute(result)
        self.changed.emit(data_id)
        for result in results:
            self.changed.emit(result.id)
        for graph in graphs:
            self.changed.emit(graph.id)

    def rerun(self, result: ResultSheet) -> None:
        self.compute(result)
        self.changed.emit(result.id)
        for graph in self.graphs.values():
            if graph.result_id == result.id:
                self.changed.emit(graph.id)

    def compute(self, sheet: ResultSheet) -> None:
        data = self.data[sheet.data_id]
        sheet.result, sheet.wide, sheet.warnings, sheet.methods, sheet.error = None, None, [], "", ""
        try:
            if sheet.kind == "compare":
                sheet.result = run_compare(data, sheet.options)
                sheet.warnings = list(sheet.result.warnings)
                sheet.methods = sheet.result.methods
            elif sheet.kind == "colstats":
                sheet.wide, sheet.warnings, sheet.methods = run_column_statistics(data, sheet.options)
            elif sheet.kind == "normality":
                sheet.wide, sheet.warnings, sheet.methods = run_normality(data, sheet.options)
            if data.pseudoreplicated and PSEUDO_WARNING not in sheet.warnings:
                sheet.warnings.insert(0, PSEUDO_WARNING)
        except CellQuantError as exc:
            sheet.error = str(exc)
        except Exception as exc:  # noqa: BLE001 - kept in the log; the sheet says it could not be computed
            LOG.exception("analysis %s failed", sheet.name)
            sheet.error = f"This analysis could not be computed ({type(exc).__name__}). Details are in Help → Log."
        if sheet.error:
            LOG.info("%s: %s", sheet.name, sheet.error)


# --- Running analyses ------------------------------------------------------------------------------------------


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
    result = compare(table, test, first, second, **kwargs)
    _small_n_warning(result, table, test, first, second, kwargs.get("alpha", 0.05))
    return result


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
    style = options.get("style", "GP")
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


def table_with(table: ColumnTable, names: list[str], values: list[np.ndarray], paired: bool) -> ColumnTable:
    from cellquant.stats import Column

    return ColumnTable(title=table.title, columns=[Column(name, column) for name, column in zip(names, values)], paired=paired)
