"""The QuantAnalysis project: data tables, the results computed from them, and graphs.

Like a Prism project, every result and graph is linked to one data table. Editing the table (or a
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
from qtpy.QtCore import QObject, Signal
from scipy import special

from cellquant.errors import CellQuantError
from cellquant.gui.quantanalysis.graph_format import GraphFormat, as_format
from cellquant.stats import (
    AnalysisResult,
    ColumnTable,
    column_statistics_table,
    compare,
    identify_outliers,
    lognormality,
    normality,
    one_way,
    p_value_stack,
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

    def remove(self, sheet_id: str) -> None:
        """Remove a sheet; a data table takes its results and graphs with it."""

        if sheet_id in self.data:
            for result in [item for item in self.results.values() if item.data_id == sheet_id]:
                self.results.pop(result.id)
            for graph in [item for item in self.graphs.values() if item.data_id == sheet_id]:
                self.graphs.pop(graph.id)
            self.data.pop(sheet_id)
            for other in self.data.values():
                if other.cleaned_from == sheet_id:
                    other.cleaned_from = None
        removed = self.results.pop(sheet_id, None)
        self.graphs.pop(sheet_id, None)
        for graph in self.graphs.values():
            if graph.result_id == sheet_id:
                graph.result_id = None
        self.changed.emit("")
        if removed is not None:
            self._flags_changed(removed)

    def linked(self, data_id: str) -> tuple[list[ResultSheet], list[GraphSheet]]:
        return ([item for item in self.results.values() if item.data_id == data_id],
                [item for item in self.graphs.values() if item.data_id == data_id])

    def data_changed(self, data_id: str) -> None:
        """The table changed: recompute its results, redraw its graphs (Prism's live links)."""

        results, graphs = self.linked(data_id)
        for result in results:
            self.compute(result)
        self.changed.emit(data_id)
        for result in results:
            self.changed.emit(result.id)
        for graph in graphs:
            self.changed.emit(graph.id)
        for copy in [item for item in self.data.values() if item.cleaned_from == data_id]:
            self.refresh_cleaned(copy)

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
            if sheet.kind in ("compare", "anova", "outliers"):
                run = {"compare": run_compare, "anova": run_anova, "outliers": run_outliers}[sheet.kind]
                sheet.result = run(data, sheet.options)
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


ANOVA_KEYS = ("alpha", "ci", "style", "comparisons", "method", "family", "pairs", "geisser_greenhouse")


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
    if goal != "control":
        kwargs.pop("family", None)
    return one_way(table, test, **kwargs)


def _as_list(value) -> list:
    return [] if value is None else [value] if isinstance(value, str) else list(value)


def run_outliers(data: DataSheet, options: dict) -> AnalysisResult:
    columns = [name for name in options.get("columns") or data.table.names if name in data.table.names]
    return identify_outliers(data.table, options.get("method", "rout"), q=options.get("q", 0.01), alpha=options.get("alpha", 0.05),
                             columns=columns, style=options.get("style", "GP"))


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
    return p_value_stack(values, options.get("method", "bky"), alpha=options.get("alpha", 0.05), labels=labels, style=options.get("style", "GP"))


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
