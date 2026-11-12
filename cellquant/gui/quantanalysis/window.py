"""The QuantAnalysis window: a navigator of data tables, results and graphs, and the open sheet.

Its own top-level window, separate from the image pipeline. Tables imported from the open
experiment follow it: when approvals, cutoffs or results change there, those tables are marked
out of date at once and imported again (with the same choices) when this window is next
activated, or with Refresh; their results and graphs follow.
"""

from __future__ import annotations

import importlib.util
import hashlib
import json
import logging
import sys
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
from qtpy.QtCore import QEvent, QSettings, Qt, QTimer
from qtpy.QtGui import QFont, QKeySequence
from qtpy.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cellquant.errors import CellQuantError
from cellquant.gui.quantanalysis import graphs
from cellquant.gui.quantanalysis import project_file, tips
from cellquant.gui.quantanalysis.datagrid import DataSheetView
from cellquant.gui.quantanalysis.guide import AnalysisGuideDialog, facts_for
from cellquant.gui.quantanalysis.dialogs import (
    DEFAULT_OPTIONS,
    AnovaDialog,
    BlandAltmanDialog,
    CompareDialog,
    ContingencyDialog,
    CorrelationMatrixDialog,
    CurveFitDialog,
    ImportCsvDialog,
    ImportDialog,
    NestedDialog,
    OutlierDialog,
    PcaDialog,
    TWO_WAY_DEFAULTS,
    ProcessDialog,
    PStackDialog,
    ProjectDefaultsDialog,
    RocDialog,
    SurvivalDialog,
    TwoWayDialog,
    XYDialog,
    suggested_plan,
    summary_text,
)
from cellquant.gui.quantanalysis.project import (
    ANOVA_NAMES,
    COMPARE_TESTS,
    LOG,
    NESTED_NEEDS_REPLICATES,
    OUTLIER_NAMES,
    STAT_DEFAULTS,
    DataSheet,
    GraphSheet,
    LayoutSheet,
    Project,
    ResultSheet,
    table_frame,
    set_grouped,
    set_contingency,
    set_replicates,
    set_xy,
)
from cellquant.gui.quantanalysis.results_view import ResultSheetView
from cellquant.stats import ColumnTable, sources, transforms
from cellquant.stats.contingency import ContingencyTable
from cellquant.stats.curvefit import MODELS as CURVE_MODELS
from cellquant.stats.grouped import GroupedTable
from cellquant.stats.replicates import ReplicateTable
from cellquant.stats.xy import XYTable

INSTALL = 'pip install "cellquant[stats]"'
SHEET_ROLE = Qt.UserRole + 1
RECENT_KEY = "quantanalysis/recent_projects"
RECENT_COUNT = 8
AUTOSAVE_MS = 120_000  # a recovery copy of unsaved work is written this often
FALLBACK_STYLE = """
QWidget { background-color: #262930; color: #f0f1f2; }
QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox, QTableWidget, QTreeWidget, QListWidget, QTextBrowser, QPlainTextEdit {
    background-color: #3b3f46; border: 1px solid #414851; }
QPushButton { background-color: #414851; border-radius: 2px; padding: 4px 10px; }
QPushButton:hover { background-color: #5a626c; }
QHeaderView::section { background-color: #3b3f46; padding: 3px; border: none; }
"""


def dark_stylesheet() -> str:
    """napari's dark theme stylesheet (napari 0.5/0.6), or a close copy when it cannot be loaded."""

    try:
        from qtpy.QtCore import QDir

        if not QDir.searchPaths("theme_dark"):  # napari's own window registers its icons; a window without it does here
            from napari._qt.qt_event_loop import _theme_path

            QDir.addSearchPath("theme_dark", str(_theme_path("dark")))
    except Exception:  # noqa: BLE001 - icons are optional
        pass
    try:
        from napari.qt import get_stylesheet

        return get_stylesheet("dark")
    except Exception:  # noqa: BLE001 - any napari version without it
        pass
    try:
        from napari._qt.qt_resources import get_stylesheet

        return get_stylesheet("dark")
    except Exception:  # noqa: BLE001
        return FALLBACK_STYLE


def recovery_folder() -> Path:
    """Where recovery copies of unsaved projects are kept: beside CellQuant's per-computer settings. Tests point it at a temporary folder."""

    from cellquant.hardware import settings_path

    return settings_path().parent / "recovery"


def missing_packages() -> list[str]:
    return [name for name in ("matplotlib", "statsmodels") if importlib.util.find_spec(name) is None]


class _LogBuffer(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.lines: list[str] = []
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))

    def emit(self, record) -> None:
        self.lines.append(self.format(record))
        del self.lines[:-2000]


class QuantAnalysisWindow(QMainWindow):
    """``experiment`` returns the open experiment's controller (or None); ``busy`` says when it is running."""

    def __init__(self, parent=None, experiment: Callable[[], object] = lambda: None, busy: Callable[[], bool] = lambda: False):
        super().__init__(parent)
        self.setWindowFlag(Qt.Window, True)
        self.setWindowTitle("QuantAnalysis")
        self.setObjectName("QuantAnalysis")
        self.resize(1320, 820)
        self.setStyleSheet(dark_stylesheet())
        self._experiment = experiment
        self._busy = busy
        self._signature: str | None = None
        self._folder_sources: dict[str, sources.Source] = {}
        self.project = Project()
        self.project.changed.connect(self._project_changed)
        self.path: Path | None = None   # the project file, once saved or opened
        self._saved = project_file.fingerprint(self.project)
        self._last_export: Path | None = None   # the folder of the last export this session
        self._close_confirmed = False           # CellQuant's close already asked about unsaved work: closing now does not ask again
        self._recovery_checked = False
        self._title = QTimer(self)
        self._title.setSingleShot(True)
        self._title.setInterval(300)
        self._title.timeout.connect(self.update_title)
        self.project.modified.connect(self._title.start)
        self.views: dict[str, QWidget] = {}
        self._log = _LogBuffer()
        LOG.addHandler(self._log)
        LOG.setLevel(logging.INFO)

        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.banner.setStyleSheet("QLabel { background: rgba(217, 164, 0, 0.22); padding: 8px; }")
        missing = missing_packages()
        self.banner.setText(
            f"{' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} not installed: "
            + ("graphs cannot be drawn" if "matplotlib" in missing else "")
            + (" and " if len(missing) == 2 else "")
            + ("the Anderson-Darling and Kolmogorov-Smirnov normality tests are left out" if "statsmodels" in missing else "")
            + f". Install with:  {INSTALL}"
        )
        self.banner.setVisible(bool(missing))
        outer.addWidget(self.banner)
        split = QSplitter()
        self.navigator = QTreeWidget()
        self.navigator.setHeaderHidden(True)
        self.navigator.setMinimumWidth(220)
        self.navigator.setContextMenuPolicy(Qt.CustomContextMenu)
        self.navigator.customContextMenuRequested.connect(self._navigator_menu)
        self.navigator.currentItemChanged.connect(lambda current, _previous: self._show(current))
        self.navigator.itemDoubleClicked.connect(lambda item, _column: self._rename(item))
        self.folders = {}
        for key, title, tip in (
            ("data", "Data tables", "Your numbers: one column per group. Edit them and every linked result and graph updates."),
            ("results", "Results", "Analyses of a data table. Each one follows its table."),
            ("graphs", "Graphs", "Graphs of a data table, and of a comparison for the P bracket."),
            ("layouts", "Layouts", "Pages with several graphs on them, for figures. Each one follows its graphs."),
        ):
            folder = QTreeWidgetItem([title])
            folder.setToolTip(0, tip)
            font = QFont()
            font.setBold(True)
            folder.setFont(0, font)
            folder.setFlags(folder.flags() & ~Qt.ItemIsSelectable)
            self.navigator.addTopLevelItem(folder)
            folder.setExpanded(True)
            self.folders[key] = folder
        split.addWidget(self.navigator)
        self.stack = QStackedWidget()
        self.empty = self._welcome()
        self.stack.addWidget(self.empty)
        split.addWidget(self.stack)
        split.setStretchFactor(1, 1)
        split.setSizes([260, 1060])
        outer.addWidget(split, 1)
        self.setCentralWidget(central)
        self._menus()
        self._watch = QTimer(self)
        self._watch.setInterval(1500)
        self._watch.timeout.connect(self._check_experiment)
        self._watch.start()
        self._autosave = QTimer(self)
        self._autosave.setInterval(AUTOSAVE_MS)
        self._autosave.timeout.connect(self.autosave)
        self._autosave.start()
        if parent is not None:
            parent.installEventFilter(self)  # CellQuant closing asks to save an unsaved project

    # --- Building the window ---------------------------------------------------------------------------------------

    def _welcome(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignCenter)
        title = QLabel("<h2>QuantAnalysis</h2>")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        text = QLabel("Analyse and graph CellQuant results the standard column-table way: a data table with one column per group, "
                      "then statistics and graphs that follow it.")
        text.setWordWrap(True)
        text.setAlignment(Qt.AlignCenter)
        text.setMaximumWidth(560)
        layout.addWidget(text, 0, Qt.AlignCenter)
        for label, tip, slot in (
            ("Import from the open experiment…", "Choose a measure, what a dot is, and the groups, from the experiment open in CellQuant.", self.import_experiment),
            ("Open CellQuant results folder…", "A results folder (with experiment.json) or an export folder (with image_summary.csv).", self.open_folder_dialog),
            ("New column table", "An empty table to type or paste values into.", self.new_table),
            ("New grouped table", tips.NEW_GROUPED, self.new_grouped_table),
            ("New XY table", tips.NEW_XY, self.new_xy_table),
            ("New contingency table", tips.NEW_CONTINGENCY, self.new_contingency_table),
            ("New survival table", tips.NEW_SURVIVAL, self.new_survival_table),
            ("Open project…", "A QuantAnalysis project file (.cqstats) saved before: its tables, analyses and graphs.", self.open_project_dialog),
        ):
            button = QPushButton(label)
            button.setToolTip(tip)
            button.setMinimumWidth(300)
            button.setMinimumHeight(32)
            button.clicked.connect(lambda _checked=False, slot=slot: slot())
            layout.addWidget(button, 0, Qt.AlignCenter)
        return page

    def _menus(self) -> None:
        bar = self.menuBar()
        file = bar.addMenu("&File")
        self._action(file, "New project", self.new_project, "Start again with no tables, analyses or graphs.").setShortcut(QKeySequence.New)
        self._action(file, "Open project…", self.open_project_dialog,
                     "A QuantAnalysis project file (.cqstats): its tables, analyses and graphs.").setShortcut(QKeySequence.Open)
        self.recent_menu = file.addMenu("Open recent")
        self.recent_menu.setToolTipsVisible(True)
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        self._action(file, "Save project", self.save_project,
                     "Save every table, analysis and graph in one .cqstats file. Analyses are computed again when it is opened.").setShortcut(QKeySequence.Save)
        self._action(file, "Save project as…", self.save_project_as, "Save the project in a new file.").setShortcut(QKeySequence.SaveAs)
        file.addSeparator()
        self._action(file, "New column table", self.new_table, "An empty table: one column per group.")
        self._action(file, "New table with replicates (SuperPlot)", self.new_replicate_table, tips.NEW_REPLICATE_TABLE)
        self._action(file, "New grouped table", self.new_grouped_table, tips.NEW_GROUPED)
        self._action(file, "New XY table", self.new_xy_table, tips.NEW_XY)
        self._action(file, "New contingency table", self.new_contingency_table, tips.NEW_CONTINGENCY)
        self._action(file, "New survival table", self.new_survival_table, tips.NEW_SURVIVAL)
        self._action(file, "Import CSV…", self.import_csv_dialog, "A CSV file, wide (one column per group) or long (value and group columns).")
        file.addSeparator()
        self._action(file, "Import from the open experiment…", self.import_experiment, "The experiment open in CellQuant, at its current cutoffs.")
        self._action(file, "Open CellQuant results folder…", self.open_folder_dialog,
                     "A results folder (experiment.json, summaries/) or an export folder (image_summary.csv, objects.csv).")
        file.addSeparator()
        self._action(file, "Close", self.close)
        edit = bar.addMenu("&Edit")
        self.rename_action = self._action(edit, "Rename", self.rename_current, "Rename the table, result, graph or layout selected in the list on the left (F2).")
        self.delete_action = self._action(edit, "Delete", self.delete_current,
                                          "Delete the table, result, graph or layout selected in the list on the left. Deleting a table asks first when "
                                          "results or graphs follow it.")
        self.rename_action.setShortcut(QKeySequence("F2"))
        self.delete_action.setShortcuts([QKeySequence("Delete")] + ([QKeySequence("Backspace")] if sys.platform == "darwin" else []))
        for action in (self.rename_action, self.delete_action):
            # Keys act only while the list on the left has the focus: never while a cell or a text field is being edited, and the graph's own
            # Delete (a selected text box or arrow) is left alone.
            action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
            self.navigator.addAction(action)
        edit.aboutToShow.connect(self._update_edit_menu)
        analyze = bar.addMenu("&Analyze")
        self._runnable: dict = {}  # menu action -> (analysis kind, its tooltip): greyed, with the reason as tooltip, when it cannot run
        analyze.aboutToShow.connect(self._update_analyze_menu)
        self._analysis(analyze, "Column statistics", "colstats", "n, mean, SD, SEM, CI, median and percentiles of every column.")
        self._analysis(analyze, "Normality and lognormality tests", "normality",
                     "D'Agostino-Pearson, Shapiro-Wilk, Anderson-Darling and Kolmogorov-Smirnov, on the values and on their logs.")
        self._analysis(analyze, "Compare two columns…", "compare", "t tests and rank tests (the t test dialog).")
        self._analysis(analyze, "Compare three or more columns…", "anova",
                     "One-way ANOVA, Kruskal-Wallis and Friedman, with multiple comparisons against one or two reference groups or all pairs.")
        self._analysis(analyze, "Nested t test / ANOVA…", "nested", tips.NESTED)
        self._analysis(analyze, "Two-way ANOVA…", "twoway", tips.TWO_WAY)
        self._analysis(analyze, "Linear regression…", "linreg", tips.LINREG)
        self._analysis(analyze, "Nonlinear regression (curve fit)…", "curvefit", tips.CURVEFIT)
        self._analysis(analyze, "Correlation (XY)…", "xycorr", tips.XYCORR)
        self._analysis(analyze, "Contingency table…", "contingency", tips.CONTINGENCY)
        self._analysis(analyze, "ROC curve…", "roc", tips.ROC)
        self._analysis(analyze, "Bland-Altman…", "blandaltman", tips.BLAND_ALTMAN)
        self._analysis(analyze, "Survival curves (Kaplan-Meier)…", "survival", tips.SURVIVAL)
        self._analysis(analyze, "Correlation matrix…", "corrmatrix", tips.CORRELATION_MATRIX)
        self._analysis(analyze, "Principal component analysis (PCA)…", "pca", tips.PCA)
        analyze.addSeparator()
        self._analysis(analyze, "Identify outliers…", "outliers",
                     "ROUT, Grubbs' and iterative Grubbs' tests. Flagged values are shaded in the table and hollow on graphs; the table is not changed.")
        self._analysis(analyze, "Analyze a stack of P values…", "pstack",
                     "Paste P values and correct them for multiple testing (FDR, Holm-Sidak, Bonferroni).")
        analyze.addSeparator()
        processing = analyze.addMenu("Data processing")
        processing.setToolTipsVisible(True)
        for kind, name in transforms.PROCESSES.items():
            self._runnable[self._action(processing, f"{name}…", lambda kind=kind: self.process_data(kind), tips.PROCESS[kind])] = ("process", tips.PROCESS[kind])
        analyze.addSeparator()
        self._action(analyze, "Project defaults…", self.project_defaults_dialog,
                     "The alpha, confidence interval and P value style every new analysis starts from. Saved in the project.")
        self._action(analyze, "Help me choose…", self.help_choose, tips.GUIDE)
        graph = bar.addMenu("&Graph")
        for key, (name, tip) in graphs.KINDS.items():
            self._action(graph, name, lambda key=key: self.new_graph(key), tip)
        graph.addSeparator()
        for text, method, tip in (("Make this graph look like…", lambda view: view.magic_dialog(), tips.FORMAT["magic"]),
                                  ("Copy format", lambda view: view.copy_format(), tips.FORMAT["copy_format"]),
                                  ("Paste format", lambda view: view.paste_format(), tips.FORMAT["paste_format"])):
            self._action(graph, text, lambda method=method: self._on_graph(method), tip)
        graph.addSeparator()
        self._action(graph, "New page layout", self.new_layout, tips.NEW_LAYOUT)
        export = bar.addMenu("&Export")
        self._action(export, "Export graph…", self.export_graph, "The graph shown, as SVG, PDF, EPS, PNG, TIFF or JPEG.")
        self._action(export, "Export page layout…", self.export_layout, tips.LAYOUT_EXPORT)
        self._action(export, "Export results CSV…", self.export_results, "The results sheet shown, as a CSV file.")
        self._action(export, "Export data table CSV…", self.export_data_table,
                     "The data table shown (or the one the selected result or graph follows), as a CSV file with the same headers and layout. File → Import CSV… reads it back.")
        self._action(export, "Copy results", self.copy_results, "The results sheet shown, as tab-separated text.")
        help_menu = bar.addMenu("&Help")
        self._action(help_menu, "Statistics reference…", lambda: self.statistics_reference(), tips.REFERENCE)
        self._action(help_menu, "Statistics guide…", self.statistics_guide, "Definitions, equations and when to use each test.")
        self._action(help_menu, "Check the engine…", self.check_engine, tips.CHECK_ENGINE)
        self._action(help_menu, "Help me choose…", self.help_choose, tips.GUIDE)
        self._action(help_menu, "Log…", self.show_log, "What QuantAnalysis did: imports, analyses, exports and any errors.")
        for menu in (file, edit, analyze, graph, export, help_menu):
            menu.setToolTipsVisible(True)
        self._update_edit_menu()
        self._update_analyze_menu()

    def _action(self, menu: QMenu, text: str, slot, tip: str = ""):
        action = menu.addAction(text)
        action.triggered.connect(lambda _checked=False: slot())
        if tip:
            action.setToolTip(tip)
        return action

    def _analysis(self, menu: QMenu, text: str, kind: str, tip: str):
        action = self._action(menu, text, lambda: self.analyze(kind), tip)
        self._runnable[action] = (kind, tip)
        return action

    def blocker(self, kind: str, data: DataSheet | None) -> str | None:
        """Why analysis ``kind`` cannot run on ``data`` (None: it can); the data sheets grey their buttons from it."""

        return analysis_blocker(kind, data)

    def _update_analyze_menu(self) -> None:
        """Grey the analyses that cannot run on the selected table, each with the reason as its tooltip (the same check ``analyze`` makes)."""

        data = self.current_data()
        for action, (kind, tip) in self._runnable.items():
            reason = process_blocker(data) if kind == "process" else analysis_blocker(kind, data)
            action.setEnabled(reason is None)
            action.setToolTip(tip if reason is None else reason)

    def _update_edit_menu(self) -> None:
        on = self.current_id() is not None  # a folder row, or nothing, is not a sheet
        for action in (self.rename_action, self.delete_action):
            action.setEnabled(on)

    def project_defaults_dialog(self, values: dict | None = None) -> bool:
        """Analyze → Project defaults…; ``values`` (scripts and tests) skips the dialog."""

        if values is None:
            dialog = ProjectDefaultsDialog(self, self.project.defaults)
            if not dialog.exec():
                return False
            values = dialog.options()
        self.project.set_defaults(values)
        self.log(f"project defaults: {summary_text(**{key: self.project.defaults[key] for key in ('alpha', 'ci', 'style')})}")
        return True

    def status(self, text: str) -> None:
        self.statusBar().showMessage(text, 8000)
        LOG.info("%s", text)

    def log(self, text: str) -> None:
        LOG.info("%s", text)

    # --- Navigator --------------------------------------------------------------------------------------------------

    def _project_changed(self, sheet_id: str) -> None:
        if not sheet_id:
            self._rebuild_navigator()
            return
        view = self.views.get(sheet_id)
        if view is not None:
            view.fill()
        self._update_analyze_menu()  # a column added or removed can change what the selected table can run

    def _rebuild_navigator(self) -> None:
        current = self.current_id()
        self.navigator.blockSignals(True)
        for key, sheets in (("data", self.project.data), ("results", self.project.results), ("graphs", self.project.graphs),
                            ("layouts", self.project.layouts)):
            folder = self.folders[key]
            folder.takeChildren()
            for sheet in sheets.values():
                item = QTreeWidgetItem([sheet.name])
                item.setData(0, SHEET_ROLE, sheet.id)
                if key == "data":
                    item.setToolTip(0, sheet.description or "Double-click to rename.")
                elif key == "layouts":
                    item.setToolTip(0, f"{sum(1 for graph_id in sheet.graphs if graph_id in self.project.graphs)} graph(s) on one page.")
                elif not sheet.data_id:
                    item.setToolTip(0, "P values pasted into the analysis; no data table.")
                else:
                    data = self.project.data.get(sheet.data_id)
                    item.setToolTip(0, f"From data table: {data.name if data else '?'}")
                folder.addChild(item)
        for sheet_id in list(self.views):
            if self.project.sheet(sheet_id) is None:
                view = self.views.pop(sheet_id)
                self.stack.removeWidget(view)
                view.deleteLater()
        self.navigator.blockSignals(False)
        self.select(current)

    def item_of(self, sheet_id: str | None):
        if sheet_id is None:
            return None
        for folder in self.folders.values():
            for index in range(folder.childCount()):
                if folder.child(index).data(0, SHEET_ROLE) == sheet_id:
                    return folder.child(index)
        return None

    def select(self, sheet_id: str | None) -> None:
        item = self.item_of(sheet_id)
        if item is not None:
            self.navigator.setCurrentItem(item)
        else:
            self._show(None)

    def current_id(self) -> str | None:
        item = self.navigator.currentItem()
        return item.data(0, SHEET_ROLE) if item is not None else None

    def _show(self, item) -> None:
        sheet_id = item.data(0, SHEET_ROLE) if item is not None else None
        sheet = self.project.sheet(sheet_id) if sheet_id else None
        self._mark_linked(sheet)
        self._update_edit_menu()
        self._update_analyze_menu()
        if sheet is None:
            self.stack.setCurrentWidget(self.empty)
            return
        view = self.views.get(sheet_id)
        if view is None:
            from cellquant.gui.quantanalysis.contingency_view import ContingencySheetView
            from cellquant.gui.quantanalysis.graph_view import GraphSheetView
            from cellquant.gui.quantanalysis.grouped_view import GroupedSheetView
            from cellquant.gui.quantanalysis.layout_view import LayoutSheetView
            from cellquant.gui.quantanalysis.xy_view import XYSheetView

            kind = (GroupedSheetView if isinstance(sheet, DataSheet) and sheet.grouped is not None
                    else XYSheetView if isinstance(sheet, DataSheet) and sheet.xy is not None
                    else ContingencySheetView if isinstance(sheet, DataSheet) and sheet.contingency is not None
                    else DataSheetView if isinstance(sheet, DataSheet) else ResultSheetView if isinstance(sheet, ResultSheet)
                    else LayoutSheetView if isinstance(sheet, LayoutSheet) else GraphSheetView)
            view = kind(self, self.project, sheet)
            self.views[sheet_id] = view
            self.stack.addWidget(view)
        self.stack.setCurrentWidget(view)

    def _mark_linked(self, sheet) -> None:
        """Underline the sheets of the selected one's family: its data table, results and graphs."""

        data_id = sheet.id if isinstance(sheet, DataSheet) else getattr(sheet, "data_id", None)
        for folder in self.folders.values():
            for index in range(folder.childCount()):
                child = folder.child(index)
                other = self.project.sheet(child.data(0, SHEET_ROLE))
                family = other is not None and bool(data_id) and (other.id == data_id or getattr(other, "data_id", None) == data_id)
                font = child.font(0)
                font.setUnderline(bool(family))
                child.setFont(0, font)

    def _navigator_menu(self, position) -> None:
        item = self.navigator.itemAt(position)
        sheet_id = item.data(0, SHEET_ROLE) if item is not None else None
        if not sheet_id:
            return
        menu = QMenu(self)
        menu.addAction("Rename…").triggered.connect(lambda: self._rename(item))
        menu.addAction("Delete").triggered.connect(lambda: self._delete(sheet_id))
        menu.exec(self.navigator.viewport().mapToGlobal(position))

    def _rename(self, item) -> None:
        sheet = self.project.sheet(item.data(0, SHEET_ROLE)) if item is not None else None
        if sheet is None:
            return
        name, ok = QInputDialog.getText(self, "Rename", "Name", text=sheet.name)
        if ok and name.strip():
            sheet.name = name.strip()
            self.sheet_renamed(sheet)

    def sheet_renamed(self, sheet) -> None:
        item = self.item_of(sheet.id)
        if item is not None and item.text(0) != sheet.name:
            item.setText(0, sheet.name)
        self.project.touch()

    def rename_current(self) -> None:
        self._rename(self.navigator.currentItem())

    def delete_current(self) -> None:
        sheet_id = self.current_id()
        if sheet_id:
            self._delete(sheet_id)

    def _delete(self, sheet_id: str) -> None:
        sheet = self.project.sheet(sheet_id)
        if isinstance(sheet, DataSheet):
            results, graph_sheets = self.project.linked(sheet_id)
            if results or graph_sheets:
                answer = QMessageBox.question(self, "Delete data table",
                                              f"Delete {sheet.name} and its {len(results)} result(s) and {len(graph_sheets)} graph(s)?")
                if answer != QMessageBox.Yes:
                    return
        self.project.remove(sheet_id)

    # --- Project files ----------------------------------------------------------------------------------------------

    def experiment_dir(self) -> Path | None:
        directory = getattr(self.controller(), "directory", None)
        return Path(directory) if directory else None

    def has_unsaved_changes(self) -> bool:
        try:
            return project_file.fingerprint(self.project) != self._saved
        except (TypeError, ValueError):  # something in the project cannot be written to a file: it cannot match the last save
            return True

    def update_title(self) -> None:
        dirty = "*" if self.has_unsaved_changes() else ""
        if self.path is None:
            self.setWindowTitle(f"QuantAnalysis: Untitled{dirty}" if dirty else "QuantAnalysis")
        else:
            self.setWindowTitle(f"QuantAnalysis: {self.path.name}{dirty}")

    def ask_save(self, action: str) -> str:
        """"save", "discard" or "cancel"."""

        name = self.path.name if self.path is not None else "this untitled project"
        box = QMessageBox(QMessageBox.Question, "QuantAnalysis", f"Save the changes to {name} before {action}?",
                          QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, self)
        box.setInformativeText("Without saving, the tables, analyses and graph formats made since the last save are lost.")
        box.setDefaultButton(QMessageBox.Save)
        return {QMessageBox.Save: "save", QMessageBox.Discard: "discard"}.get(box.exec(), "cancel")

    def maybe_save(self, action: str) -> bool:
        """True when it is fine to go on: nothing unsaved, saved now, or the user chose not to save."""

        if not self.has_unsaved_changes():
            return True
        answer = self.ask_save(action)
        if answer == "save":
            return self.save_project()
        if answer == "discard":
            self.clear_recovery()  # the user chose to lose these changes: no recovery copy of them either
            return True
        return False

    # --- Recovery copies --------------------------------------------------------------------------------------------

    def recovery_path(self, path: Path | None = None) -> Path:
        """The recovery copy of the project saved at ``path`` (the current one by default), or of an unsaved project (``path`` None). Never the
        project's own file."""

        path = self.path if path is None else path
        if path is None:
            return recovery_folder() / f"untitled{project_file.SUFFIX}"
        resolved = str(Path(path).resolve())
        return recovery_folder() / f"{hashlib.sha1(resolved.encode('utf-8')).hexdigest()[:10]}-{Path(path).stem}{project_file.SUFFIX}"

    def clear_recovery(self, *paths: Path | None) -> None:
        """Delete the recovery copy of the current project (and of the projects at ``paths``; None is the unsaved one)."""

        for path in ({self.recovery_path()} | {self.recovery_path(path) for path in paths}):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def autosave(self) -> bool:
        """Write a recovery copy while there are unsaved changes (every ``AUTOSAVE_MS``), with the writer the normal save uses. It never
        touches the project's own file, and a failure is only logged: it must not interrupt work."""

        if not self.has_unsaved_changes():
            return False
        try:
            project_file.save(self.project, self.recovery_path(), self.experiment_dir(), self.current_id())
        except (CellQuantError, TypeError, ValueError) as exc:
            self.log(f"autosave failed: {exc}")
            return False
        return True

    def ask_restore(self, name: str, when: str) -> bool:
        """Offer the recovery copy of ``name`` (saved ``when``); True to restore it."""

        box = QMessageBox(QMessageBox.Question, "QuantAnalysis", f"Restore the unsaved changes to {name}?", QMessageBox.Yes | QMessageBox.No, self)
        box.setInformativeText(f"QuantAnalysis found a recovery copy from {when}, newer than the saved project. It was left behind when QuantAnalysis "
                               "closed without saving. No restores it away.")
        box.setDefaultButton(QMessageBox.Yes)
        return box.exec() == QMessageBox.Yes

    def _newer_recovery(self, path: Path | None) -> Path | None:
        """The recovery copy of the project at ``path`` (None: an unsaved project) when it exists and is newer than the project file."""

        recovery = self.recovery_path(path)
        try:
            stamp = recovery.stat().st_mtime
            if path is not None and stamp <= Path(path).stat().st_mtime:
                recovery.unlink(missing_ok=True)  # the project was saved after it: it is stale
                return None
        except OSError:
            return None
        return recovery

    def _restore(self, recovery: Path, path: Path | None, name: str) -> bool:
        """Offer the recovery copy; when accepted, load it in place of the project (still unsaved), else delete it."""

        from datetime import datetime

        when = datetime.fromtimestamp(recovery.stat().st_mtime).strftime("%d %b %H:%M")
        if not self.ask_restore(name, when):
            self.clear_recovery(path)
            return False
        try:
            content = project_file.read(recovery)
        except CellQuantError as exc:
            self.status(f"The recovery copy could not be read: {exc}")
            return False
        saved = self._saved
        self._drop_views()
        current, notes = project_file.load(recovery, self.project, self.experiment_dir(), content)
        self.path = path
        self._saved = saved  # the restored project differs from what was saved: it stays unsaved
        self.select(current or next(iter(self.project.data), None))
        self.update_title()
        for note in notes:
            self.log(note)
        self.status(f"Restored the unsaved changes to {name}. Save the project to keep them.")
        return True

    def offer_recovery(self) -> bool:
        """When QuantAnalysis starts with an unsaved project's recovery copy left behind: offer it (once)."""

        if self._recovery_checked:
            return False
        self._recovery_checked = True
        if self.path is not None or self.project.data or self.project.results or self.project.graphs or self.project.layouts:
            return False
        recovery = self._newer_recovery(None)
        if recovery is None:
            return False
        return self._restore(recovery, None, "an untitled project")

    def _drop_views(self) -> None:
        for view in self.views.values():
            self.stack.removeWidget(view)
            view.deleteLater()
        self.views.clear()

    def new_project(self) -> bool:
        if not self.maybe_save("starting a new project"):
            return False
        self._drop_views()
        self.project.defaults = dict(STAT_DEFAULTS)
        self.project.replace({}, {}, {})
        self.path = None
        self._saved = project_file.fingerprint(self.project)
        self.update_title()
        self.select(None)
        self.log("new project")
        return True

    def open_project_dialog(self) -> None:
        if not self.maybe_save("opening another project"):
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open a QuantAnalysis project", str(self._project_folder()),
                                              f"QuantAnalysis projects (*{project_file.SUFFIX});;All files (*)")
        if path:
            self.open_project(path, ask=False)

    def open_project(self, path: str | Path, ask: bool = True) -> bool:
        """Open a project file in place of the current project (asking first to save it when ``ask``)."""

        if ask and not self.maybe_save("opening another project"):
            return False
        path = Path(path)
        experiment = self.experiment_dir()
        try:
            content = project_file.read(path)
        except CellQuantError as exc:
            self.status(str(exc))
            QMessageBox.warning(self, "QuantAnalysis", str(exc))
            self._forget_recent(path)
            return False
        self._drop_views()
        current, notes = project_file.load(path, self.project, experiment, content)
        self.path = path
        self._saved = project_file.fingerprint(self.project)
        self._remember_recent(path)
        recovery = self._newer_recovery(path)
        if recovery is not None:
            self._restore(recovery, path, path.name)  # a recovery copy newer than the file: offer it
        self.update_title()
        self.select(current or next(iter(self.project.data), None))
        for note in notes:
            self.log(note)
        self.status(f"Opened {path.name}: {len(self.project.data)} table(s), {len(self.project.results)} analysis(es), "
                    f"{len(self.project.graphs)} graph(s)." + (f" {len(notes)} item(s) could not be read; see Help → Log." if notes else ""))
        linked = [sheet for sheet in self.project.data.values() if sheet.origin == "experiment" and sheet.spec is not None]
        if linked and self.controller() is not None:
            # The experiment may have changed since the save: import its tables again, with the same choices.
            for sheet in linked:
                sheet.stale = True
            self.refresh_imports()
        return True

    def save_project(self) -> bool:
        if self.path is None:
            return self.save_project_as()
        return self._write(self.path)

    def save_project_as(self) -> bool:
        start = self.path if self.path is not None else self._project_folder() / project_file.default_name(self.experiment_dir())
        path, _ = QFileDialog.getSaveFileName(self, "Save the QuantAnalysis project", str(start),
                                              f"QuantAnalysis projects (*{project_file.SUFFIX})")
        return self._write(Path(path)) if path else False

    def _write(self, path: Path) -> bool:
        try:
            written = project_file.save(self.project, path, self.experiment_dir(), self.current_id())
        except CellQuantError as exc:
            self.status(str(exc))
            QMessageBox.warning(self, "QuantAnalysis", str(exc))
            return False
        self.clear_recovery(written)  # saved: the recovery copies (of the unsaved project, and of this file) are done with
        self.path = written
        self._saved = project_file.fingerprint(self.project)
        self._remember_recent(written)
        self.update_title()
        self.status(f"Saved {written}.")
        return True

    def _project_folder(self) -> Path:
        if self.path is not None:
            return self.path.parent
        recent = self.recent_projects()
        if recent:
            return Path(recent[0]).parent
        return self.experiment_dir() or Path.home()

    def recent_projects(self) -> list[str]:
        value = QSettings("CellQuant", "CellQuant").value(RECENT_KEY, "")
        if isinstance(value, str):
            try:  # kept as JSON text: a list, or one path, can come back split at its commas from an .ini file
                value = json.loads(value) if value else []
            except ValueError:
                value = [value]
        if not isinstance(value, (list, tuple)):
            return []
        return [str(item) for item in value if isinstance(item, str) and item]

    def _set_recent(self, paths: list[str]) -> None:
        QSettings("CellQuant", "CellQuant").setValue(RECENT_KEY, json.dumps(list(paths[:RECENT_COUNT])))

    def _remember_recent(self, path: Path) -> None:
        text = str(Path(path).resolve())
        self._set_recent([text, *[item for item in self.recent_projects() if item != text]])

    def _forget_recent(self, path: Path) -> None:
        text = str(Path(path).resolve())
        self._set_recent([item for item in self.recent_projects() if item != text])

    def _fill_recent(self) -> None:
        self.recent_menu.clear()
        recent = self.recent_projects()
        for item in recent:
            action = self.recent_menu.addAction(Path(item).name)
            action.setToolTip(item)
            action.triggered.connect(lambda _checked=False, item=item: self.open_project(item))
        if not recent:
            self.recent_menu.addAction("No recent projects").setEnabled(False)
        else:
            self.recent_menu.addSeparator()
            self.recent_menu.addAction("Clear the list").triggered.connect(lambda: self._set_recent([]))

    def eventFilter(self, watched, event) -> bool:
        if watched is self.parent() and event.type() == QEvent.Close:
            if not self.maybe_save("CellQuant closes"):
                event.ignore()
                return True
            # Asked (or nothing to ask): closing QuantAnalysis along with CellQuant must not ask a second time.
            self._close_confirmed = True
            QTimer.singleShot(3000, lambda: setattr(self, "_close_confirmed", False))
        return False

    # --- Data tables ------------------------------------------------------------------------------------------------

    def current_data(self) -> DataSheet | None:
        sheet = self.project.sheet(self.current_id()) if self.current_id() else None
        if isinstance(sheet, DataSheet):
            return sheet
        if sheet is not None:
            return self.project.data.get(getattr(sheet, "data_id", "") or "")  # a page layout follows several tables: none
        return next(reversed(self.project.data.values()), None) if len(self.project.data) == 1 else None

    def new_table(self) -> DataSheet:
        table = ColumnTable.from_columns({"Group A": [], "Group B": []}, title="")
        sheet = self.project.add_data(DataSheet(f"Data {len(self.project.data) + 1}", table))
        self.select(sheet.id)
        return sheet

    def new_replicate_table(self) -> DataSheet:
        """An empty column table with a Replicate column, to paste every value (for example every cell) into by replicate."""

        sheet = DataSheet(f"Data {len(self.project.data) + 1}", ColumnTable())
        set_replicates(sheet, ReplicateTable("", ["Group A", "Group B"], [np.zeros(0), np.zeros(0)], []))
        self.project.add_data(sheet)
        self.select(sheet.id)
        return sheet

    def new_grouped_table(self) -> DataSheet:
        """An empty grouped table: two rows × two data sets × three replicates, to type or paste into."""

        sheet = DataSheet(f"Data {len(self.project.data) + 1}", ColumnTable())
        set_grouped(sheet, GroupedTable("", ["Row 1", "Row 2"], ["Data set A", "Data set B"], np.full((2, 2, 3), np.nan)))
        self.project.add_data(sheet)
        self.select(sheet.id)
        return sheet

    def new_xy_table(self) -> DataSheet:
        """An empty XY table: five rows, one data set with three replicates, to type or paste into."""

        sheet = DataSheet(f"Data {len(self.project.data) + 1}", ColumnTable())
        set_xy(sheet, XYTable("", "X", np.full(5, np.nan), ["Data set A"], np.full((5, 1, 3), np.nan)))
        self.project.add_data(sheet)
        self.select(sheet.id)
        return sheet

    def new_survival_table(self) -> DataSheet:
        """An empty survival table: an XY table with Time as X and two groups (one replicate each), one row per subject."""

        sheet = DataSheet(f"Data {len(self.project.data) + 1}", ColumnTable())
        set_xy(sheet, XYTable("", "Time", np.full(8, np.nan), ["Group A", "Group B"], np.full((8, 2, 1), np.nan)))
        self.project.add_data(sheet)
        self.select(sheet.id)
        return sheet

    def new_contingency_table(self) -> DataSheet:
        """An empty 2 × 2 contingency table to type counts into."""

        sheet = DataSheet(f"Data {len(self.project.data) + 1}", ColumnTable())
        set_contingency(sheet, ContingencyTable("", ["Group A", "Group B"], ["Outcome", "No outcome"], np.full((2, 2), np.nan)))
        self.project.add_data(sheet)
        self.select(sheet.id)
        return sheet

    def import_csv_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import CSV", str(Path.home()), "CSV files (*.csv *.txt);;All files (*)")
        if path:
            self.import_csv(path)

    def import_csv(self, path: str, shape: str | None = None, value: str | None = None, group: str | None = None,
                   replicate: str | None = None) -> DataSheet | None:
        try:
            frame = pd.read_csv(path, sep=None, engine="python")
        except (OSError, ValueError) as exc:
            self.status(f"{Path(path).name} could not be read: {exc}")
            return None
        dialog = ImportCsvDialog(self, frame, Path(path).name)
        if shape is not None:
            from cellquant.gui.quantanalysis.dialogs import _check

            _check(dialog.shape, shape)
            if value is not None:
                dialog.value.setCurrentIndex(dialog.value.findData(value))
            if group is not None:
                dialog.group.setCurrentIndex(dialog.group.findData(group))
            dialog.replicate.setCurrentIndex(max(0, dialog.replicate.findData(replicate)))
        elif not dialog.exec():
            return None
        try:
            table = dialog.table(Path(path).stem)
            replicates = dialog.replicates(Path(path).stem)
        except CellQuantError as exc:
            self.status(str(exc))
            return None
        sheet = DataSheet(Path(path).stem, table, description=f"From {path}")
        if replicates is not None:
            set_replicates(sheet, replicates)
        sheet = self.project.add_data(sheet)
        self.select(sheet.id)
        return sheet

    def controller(self):
        try:
            return self._experiment()
        except Exception:  # noqa: BLE001 - the experiment window was closed
            return None

    def import_experiment(self, spec: sources.ImportSpec | None = None, accept: bool = False, make_suggested: bool | None = None) -> DataSheet | None:
        """Import from the open experiment. ``accept`` (with ``spec``) skips the dialog, for scripts and tests; ``make_suggested`` then
        stands for the dialog's checkbox (default: made when no ``spec`` is given)."""

        controller = self.controller()
        if controller is None:
            self.status("No experiment is open in CellQuant. Open one there, or use File → Open CellQuant results folder….")
            return None
        try:
            source = sources.from_controller(controller)
        except CellQuantError as exc:
            self.status(str(exc))
            return None
        self._signature = sources.controller_signature(controller)
        return self._import(source, "experiment", spec, accept, **({} if make_suggested is None else {"make_suggested": make_suggested}))

    def open_folder_dialog(self) -> None:
        from cellquant.gui.app import choose_existing_directory

        folder = choose_existing_directory(self, "Open a CellQuant results folder")
        if folder:
            self.open_folder(folder)

    def open_folder(self, folder: str | Path, spec: sources.ImportSpec | None = None, accept: bool = False,
                    make_suggested: bool | None = None) -> DataSheet | None:
        try:
            source = sources.read_results_folder(folder)
        except (CellQuantError, OSError, ValueError) as exc:
            self.status(str(exc))
            QMessageBox.warning(self, "QuantAnalysis", str(exc)) if not accept else None
            return None
        self._folder_sources[str(folder)] = source
        return self._import(source, str(folder), spec, accept, **({} if make_suggested is None else {"make_suggested": make_suggested}))

    def import_dialog(self, source: sources.Source, spec: sources.ImportSpec | None = None) -> ImportDialog:
        return ImportDialog(self, source, spec)

    def _import(self, source: sources.Source, origin: str, spec, accept: bool, make_suggested: bool | None = None) -> DataSheet | None:
        dialog = self.import_dialog(source, spec)
        if accept:
            if spec is None:
                dialog._use_suggested()
            dialog.make_suggested.setChecked(spec is None if make_suggested is None else make_suggested)  # scripts: the checkbox's answer
            dialog._preview()
        elif not dialog.exec():
            return None
        imported = dialog.imported
        if imported is None:
            self.status("Nothing to import with these choices.")
            return None
        sheet = DataSheet(imported.table.title or "Imported", imported.table)
        self._apply_import(sheet, imported, dialog.spec(), origin)
        self.project.add_data(sheet)
        self.select(sheet.id)
        self.log(f"imported {sheet.name} from {source.label}: {imported.description}")
        if dialog.make_suggested.isChecked():  # the checkbox alone decides whether graphs and a test are made
            self.suggested_analysis(sheet)
        return sheet

    def _apply_import(self, sheet: DataSheet, imported: sources.Imported, spec, origin: str) -> None:
        sheet.table, sheet.row_labels, sheet.grouped = imported.table, imported.row_labels, None
        sheet.images, sheet.units = imported.images, imported.units
        if imported.grouped is not None:  # cells of a grouped table: no per-image or per-replicate frames (SuperPlots are per column)
            set_grouped(sheet, imported.grouped)
            sheet.images, sheet.units, sheet.row_labels = None, None, []
        sheet.notes, sheet.warnings = imported.notes, imported.warnings
        sheet.pseudoreplicated, sheet.description = imported.pseudoreplicated, imported.description
        sheet.spec, sheet.origin, sheet.stale = spec, origin, False

    def suggested_analysis(self, sheet: DataSheet) -> None:
        """The image-data preset (see ``suggested_plan``): a SuperPlot (mean ± SD) and, for two groups, Welch's t test with an estimation plot.
        A grouped table gets a two-way ANOVA comparing the data sets within each row, and an interleaved bar graph showing it."""

        plan = suggested_plan(len(sheet.table), sheet.grouped is not None, sheet.units is not None or sheet.replicates is not None)
        stats = {key: self.project.defaults[key] for key in ("alpha", "ci", "style")}
        if plan["test"] == "twoway":
            result = self.project.add_result(ResultSheet(f"Two-way ANOVA: {sheet.name}", sheet.id, "twoway", {**TWO_WAY_DEFAULTS, **stats}))
            self.project.add_graph(GraphSheet(f"{graphs.KINDS['bar'][0]}: {sheet.name}", sheet.id, "bar", {"error": "sd", "legend": {"show": True, "position": "master"}},
                                              result.id))
            return
        result = None
        if plan["test"] == "welch_t":
            result = self.project.add_result(ResultSheet(f"Welch's t test: {sheet.name}", sheet.id, "compare",
                                                         {"test": "welch_t", "first": 0, "second": 1, "alternative": "two-sided", **stats}))
        kind = plan["graphs"][0]
        graph = self.project.add_graph(GraphSheet(f"{graphs.KINDS[kind][0]}: {sheet.name}", sheet.id, kind,
                                                  {"error": "sd", **({"legend": {"show": True, "position": "right"}} if kind == "superplot" else {})},
                                                  result.id if result else None))
        if "estimation" in plan["graphs"]:
            self.project.add_graph(GraphSheet(f"Estimation plot: {sheet.name}", sheet.id, "estimation", {"error": "sd"}, result.id))
        if plan["test"] == "kruskal_wallis":
            reference = sheet.table.names[0]
            options = {"test": "kruskal_wallis", "comparisons": "control", "method": "dunn", "control": [reference], "family": "per_reference",
                       "alpha": stats["alpha"], "ci": stats["ci"], "style": stats["style"]}
            result = self.project.add_result(ResultSheet(f"Kruskal-Wallis test: {sheet.name}", sheet.id, "anova", options))
            graph.result_id = result.id
            self.project.changed.emit(graph.id)
            self.status(f"Three or more groups: Kruskal-Wallis with Dunn's tests against {reference}. Change the reference group(s) "
                        "with Change options….")

    # --- Following the open experiment ---------------------------------------------------------------------------

    def _check_experiment(self) -> None:
        linked = [sheet for sheet in self.project.data.values() if sheet.origin == "experiment" and sheet.spec is not None]
        controller = self.controller()
        if not linked or controller is None or self._busy():
            return
        try:
            signature = sources.controller_signature(controller)
        except Exception:  # noqa: BLE001 - mid-change; try again on the next tick
            return
        if self._signature is not None and signature != self._signature:
            for sheet in linked:
                if not sheet.stale:
                    sheet.stale = True
                    self.project.changed.emit(sheet.id)
            self.log("the open experiment changed; imported tables are out of date")
            if self.isActiveWindow():
                self.refresh_imports()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.ActivationChange and self.isActiveWindow():
            self._check_experiment()
            self.refresh_imports()

    def refresh_imports(self, force: bool = False) -> None:
        """Import the out-of-date tables again with their choices; their results and graphs follow."""

        stale = [sheet for sheet in self.project.data.values() if sheet.spec is not None and (sheet.stale or force)
                 and sheet.origin == "experiment"]
        if force:
            stale += [sheet for sheet in self.project.data.values() if sheet.spec is not None and sheet.origin not in ("", "experiment")]
        if not stale or self._busy():
            return
        cache: dict[str, sources.Source] = {}
        for sheet in stale:
            try:
                if sheet.origin == "experiment":
                    controller = self.controller()
                    if controller is None:
                        continue
                    if "experiment" not in cache:
                        cache["experiment"] = sources.from_controller(controller)
                        self._signature = sources.controller_signature(controller)
                else:
                    cache.setdefault(sheet.origin, sources.read_results_folder(sheet.origin))
                imported = sources.build(cache[sheet.origin], sheet.spec)
            except CellQuantError as exc:
                sheet.warnings = [f"Could not import again: {exc}"]
                sheet.stale = False
                self.project.data_changed(sheet.id)
                continue
            self._apply_import(sheet, imported, sheet.spec, sheet.origin)
            self.project.data_changed(sheet.id)
            self.log(f"{sheet.name} imported again: {imported.description}")
        self.status("Imported tables are up to date with the experiment.")

    # --- Analyses and graphs ----------------------------------------------------------------------------------------

    def analyze(self, kind: str, options: dict | None = None) -> ResultSheet | None:
        """Run an analysis on the current data table; ``options`` (from a dialog) skips the dialog, for scripts and tests."""

        if kind == "pstack":
            return self._add_result(None, "pstack", options, lambda: PStackDialog(self))
        data = self.current_data()
        reason = analysis_blocker(kind, data)  # the same check that greys the Analyze menu item
        if reason:
            self.status(reason)
            return None
        if kind == "compare":
            dialog = lambda: CompareDialog(self, data.table, sheet=data)  # noqa: E731
        elif kind == "anova":
            dialog = lambda: AnovaDialog(self, data.table, sheet=data)  # noqa: E731
        elif kind == "nested":
            dialog = lambda: NestedDialog(self, data.table, data.replicates)  # noqa: E731
        elif kind == "outliers":
            dialog = lambda: OutlierDialog(self, data.table)  # noqa: E731
        elif kind == "twoway":
            dialog = lambda: TwoWayDialog(self, data.grouped)  # noqa: E731
        elif kind in ("roc", "blandaltman"):
            dialog = (lambda: RocDialog(self, data.table)) if kind == "roc" else (lambda: BlandAltmanDialog(self, data.table))
        elif kind == "survival":
            dialog = lambda: SurvivalDialog(self, data.xy)  # noqa: E731
        elif kind in ("corrmatrix", "pca"):
            dialog = (lambda: CorrelationMatrixDialog(self, data.table)) if kind == "corrmatrix" else (lambda: PcaDialog(self, data.table))
        elif kind == "contingency":
            dialog = lambda: ContingencyDialog(self, data.contingency)  # noqa: E731
        elif kind in ("linreg", "xycorr", "curvefit"):
            dialog = (lambda: CurveFitDialog(self, data.xy)) if kind == "curvefit" else (lambda: XYDialog(self, data.xy, kind))
        else:
            dialog = None
        sheet = self._add_result(data, kind, options, dialog)
        if sheet is not None and kind in ("roc", "blandaltman") and not sheet.error:
            # An ROC curve or Bland-Altman plot is its own graph; the table's column graphs stay as they are.
            self.project.add_graph(GraphSheet(f"{sheet.name} (graph)", data.id, "scatter", {}, sheet.id))
        if sheet is not None and kind == "corrmatrix" and not sheet.error:
            # The correlation matrix is its own graph, a heat map; the table's column graphs stay as they are.
            self.project.add_graph(GraphSheet(f"{sheet.name} (graph)", data.id, "scatter", {}, sheet.id))
        if sheet is not None and kind == "pca" and not sheet.error:
            # A PCA makes two graphs: the scree plot (how many components) and the scores with the loadings (what they mean).
            self.project.add_graph(GraphSheet(f"{sheet.name} (scree)", data.id, "scatter", {"pca_view": "scree"}, sheet.id))
            self.project.add_graph(GraphSheet(f"{sheet.name} (scores)", data.id, "scatter", {"pca_view": "scores"}, sheet.id))
        if sheet is not None and kind == "survival" and not sheet.error:
            # The survival curves are their own graph, with the legend on (the groups are told apart by colour).
            self.project.add_graph(GraphSheet(f"{sheet.name} (graph)", data.id, "xy", {"legend": {"show": True}}, sheet.id))
        if sheet is not None and kind in ("compare", "anova", "nested", "linreg", "curvefit", "contingency"):
            for graph in self.project.graphs.values():
                if graph.data_id == data.id and graph.result_id is None:
                    graph.result_id = sheet.id
                    self.project.changed.emit(graph.id)
        return sheet

    def _add_result(self, data: DataSheet | None, kind: str, options: dict | None, dialog: Callable | None) -> ResultSheet | None:
        if options is None and dialog is not None:
            box = dialog()
            if not box.exec():
                return None
            options = box.options()
        name = result_name(kind, options or {}, data)
        sheet = self.project.add_result(ResultSheet(name, data.id if data is not None else "", kind, options or {}))
        self.select(sheet.id)
        self.log(f"{name}: {'error: ' + sheet.error if sheet.error else 'done'}")
        return sheet

    def change_options(self, sheet: ResultSheet, options: dict | None = None) -> None:
        data = self.project.data.get(sheet.data_id)
        if data is None and sheet.kind != "pstack":
            return
        if options is None and sheet.kind == "nested" and data.replicates is None:
            self.status(NESTED_NEEDS_REPLICATES)
            return
        if options is None:
            box = {"compare": lambda: CompareDialog(self, data.table, sheet.options, sheet=data),
                   "anova": lambda: AnovaDialog(self, data.table, sheet.options, sheet=data),
                   "nested": lambda: NestedDialog(self, data.table, data.replicates, sheet.options),
                   "outliers": lambda: OutlierDialog(self, data.table, sheet.options), "pstack": lambda: PStackDialog(self, sheet.options),
                   "twoway": lambda: TwoWayDialog(self, data.grouped, sheet.options),
                   "linreg": lambda: XYDialog(self, data.xy, "linreg", sheet.options),
                   "xycorr": lambda: XYDialog(self, data.xy, "xycorr", sheet.options),
                   "curvefit": lambda: CurveFitDialog(self, data.xy, sheet.options),
                   "contingency": lambda: ContingencyDialog(self, data.contingency, sheet.options),
                   "roc": lambda: RocDialog(self, data.table, sheet.options),
                   "blandaltman": lambda: BlandAltmanDialog(self, data.table, sheet.options),
                   "survival": lambda: SurvivalDialog(self, data.xy, sheet.options),
                   "corrmatrix": lambda: CorrelationMatrixDialog(self, data.table, sheet.options),
                   "pca": lambda: PcaDialog(self, data.table, sheet.options)}[sheet.kind]()
            if not box.exec():
                return
            # The guide's answer that the values are not independent stays with the analysis.
            options = {**box.options(), **{key: sheet.options[key] for key in ("not_independent",) if key in sheet.options}}
        sheet.options = options
        sheet.name = result_name(sheet.kind, options, data)
        self.sheet_renamed(sheet)
        self.project.rerun(sheet)

    def process_data(self, kind: str, options: dict | None = None) -> DataSheet | None:
        """A new data table computed from the current one (transform, normalize…); ``options`` skips the dialog."""

        data = self.current_data()
        reason = process_blocker(data)
        if reason:
            self.status(reason)
            return None
        if options is None:
            dialog = ProcessDialog(self, data.table, kind)
            if not dialog.exec():
                return None
            options = dialog.options()
        try:
            sheet = self.project.add_processed(data, {**options, "kind": kind})
        except CellQuantError as exc:
            self.status(str(exc))
            return None
        self.select(sheet.id)
        self.log(f"{sheet.name}: {' '.join(sheet.notes)}")
        return sheet

    def change_process(self, sheet: DataSheet, options: dict | None = None) -> None:
        source = self.project.data.get(sheet.processed_from or "")
        if source is None:
            return
        if options is None:
            dialog = ProcessDialog(self, source.table, sheet.process["kind"], sheet.process)
            if not dialog.exec():
                return
            options = dialog.options()
        try:
            self.project.change_process(sheet, {**options, "kind": sheet.process["kind"]})
        except CellQuantError as exc:
            self.status(str(exc))
            return
        self.log(f"{sheet.name}: {' '.join(sheet.notes)}")

    def create_cleaned_table(self, sheet: ResultSheet) -> DataSheet | None:
        """A new data table that follows the original with the flagged outliers blank. The original is not changed."""

        if sheet.result is None or not sheet.result.values.get("n_outliers"):
            self.status("No outliers were flagged, so a cleaned table would equal the original.")
            return None
        source = self.project.data.get(sheet.data_id)
        if source is not None and source.grouped is not None:
            self.status("A cleaned copy keeps a column table's layout; for a grouped table, blank the flagged values in the table itself.")
            return None
        cleaned = self.project.add_cleaned(sheet)
        if cleaned is not None:
            self.select(cleaned.id)
            self.log(f"{cleaned.name} created from {sheet.name}: {sheet.result.values['n_outliers']} value(s) blank")
        return cleaned

    def help_choose(self) -> AnalysisGuideDialog | None:
        """The analysis guide on the selected table; Run this analysis opens the recommended analysis filled in."""

        data = self.current_data()
        dialog = AnalysisGuideDialog(self, facts_for(data))
        if not dialog.exec():
            return dialog
        rec, options = dialog.recommendation(), dialog.options()
        kind = rec.kind
        if kind == "survival":  # the survival dialog reads an XY table (File → New survival table)
            if data is None or data.xy is None:
                self.status(f"{rec.name} needs an XY table with one row per subject: File → New survival table. Select or make one, then run it.")
                return dialog
            box = SurvivalDialog(self, data.xy)
            if box.exec():
                self.analyze("survival", box.options())
            return dialog
        need = {"compare": (lambda d: d.xy is None and len(d.table) >= 2, "a data table with two or more columns"),
                "anova": (lambda d: d.xy is None and len(d.table) >= 3, "a data table with three or more columns"),
                "twoway": (lambda d: d.grouped is not None, "a grouped table (File → New grouped table)"),
                "linreg": (lambda d: d.xy is not None, "an XY table (File → New XY table)"),
                "xycorr": (lambda d: d.xy is not None, "an XY table (File → New XY table)")}[kind]
        if data is None or not need[0](data):
            self.status(f"{rec.name} needs {need[1]}. Select or make one, then run it.")
            return dialog
        if kind == "compare":
            box = CompareDialog(self, data.table, {**DEFAULT_OPTIONS, **self.project.defaults, **options}, sheet=data)
        elif kind == "anova":
            box = AnovaDialog(self, data.table, {**self.project.defaults, **options}, sheet=data)
        elif kind == "twoway":
            box = TwoWayDialog(self, data.grouped, {**TWO_WAY_DEFAULTS, **self.project.defaults, **options})
        else:
            box = XYDialog(self, data.xy, kind, {**self.project.defaults, **options})
        if box.exec():
            chosen = box.options()
            if rec.pseudoreplicated and not data.pseudoreplicated:
                chosen["not_independent"] = "images" if dialog.answers().dot == "image" else "cells"
            self.analyze(kind, chosen)
        return dialog

    def new_graph(self, kind: str) -> GraphSheet | None:
        data = self.current_data()
        if data is None:
            self.status("Select a data table first.")
            return None
        result = next((item for item in reversed(list(self.project.results.values()))
                       if item.data_id == data.id and item.kind in ("compare", "anova", "nested", "twoway", "linreg", "curvefit", "contingency")), None)
        sheet = self.project.add_graph(GraphSheet(f"{graphs.KINDS[kind][0]}: {data.name}", data.id, kind, self.graph_defaults(kind, data),
                                                  result.id if result else None))
        self.select(sheet.id)
        return sheet

    @staticmethod
    def graph_defaults(kind: str, data: DataSheet | None) -> dict:
        """The format a new graph of ``kind`` starts from (and Reset graph puts back): the standard format, with the legend on where colours need naming."""

        if data is None:
            return {}
        # A grouped graph colours its cells by data set (and an XY graph its data sets), so its legend is on from the start.
        several = data.grouped is not None or data.contingency is not None or (data.xy is not None and len(data.xy.sets) > 1)
        # A SuperPlot colours by biological replicate: its legend names them.
        return ({"legend": {"show": True, "position": "master"}} if several else
                {"legend": {"show": True, "position": "right"}} if kind == "superplot" and graphs.superplot_levels(data) is not None else {})

    def new_layout(self) -> LayoutSheet:
        """A new page with the project's graphs in a grid just large enough for them (the graph shown first)."""

        graph_ids = list(self.project.graphs)
        current = self.current_id()
        if current in graph_ids:
            graph_ids.remove(current)
            graph_ids.insert(0, current)
        count = max(1, min(len(graph_ids), 36))
        columns = 1 if count == 1 else 2 if count <= 4 else 3 if count <= 9 else 4 if count <= 16 else 6
        rows = min(6, -(-count // columns))
        sheet = self.project.add_layout(LayoutSheet("Layout", {"rows": rows, "columns": columns}, graph_ids[:rows * columns]))
        self.select(sheet.id)
        return sheet

    def _data_folder(self, sheet) -> Path | None:
        """The CellQuant results folder ``sheet``'s data came from (its table's, or the table it was processed or cleaned from), if any."""

        data = sheet if isinstance(sheet, DataSheet) else None
        if data is None and isinstance(sheet, LayoutSheet):
            sheet = next((self.project.graphs[graph_id] for graph_id in sheet.graphs if graph_id in self.project.graphs), None)
        if data is None and sheet is not None:
            data = self.project.data.get(getattr(sheet, "data_id", "") or "")
        for _step in range(6):
            if data is None:
                return None
            if data.origin == "experiment":
                folder = self.experiment_dir()
            else:
                folder = Path(data.origin) if data.origin else None
            if folder is not None and folder.is_dir():
                return folder
            data = self.project.data.get(data.processed_from or data.cleaned_from or "")
        return None

    def export_folder(self, sheet=None) -> Path:
        """Where an export starts: the folder of the saved project, else the results folder the data came from, else the last folder an
        export used this session, else the home folder."""

        if self.path is not None and self.path.parent.is_dir():
            return self.path.parent
        sheet = sheet if sheet is not None else self.project.sheet(self.current_id() or "")
        folder = self._data_folder(sheet)
        if folder is not None:
            return folder
        if self._last_export is not None and self._last_export.is_dir():
            return self._last_export
        return Path.home()

    def export_path(self, filename: str, sheet=None) -> str:
        """``filename`` (made safe for a file name) in ``export_folder``: the start of an export dialog."""

        safe = "".join(" " if character in '\\/:*?"<>|' else character for character in filename).strip()
        return str(self.export_folder(sheet) / " ".join(safe.split()))

    def remember_export(self, path) -> None:
        """The next export this session starts in the folder of this one."""

        self._last_export = Path(path).parent

    def export_data_table(self, path: str | None = None, sheet: DataSheet | None = None) -> str | None:
        """Write the data table shown (or ``sheet``) as CSV, with the same headers and layout as its grid; ``path`` skips the dialog."""

        data = sheet or self.current_data()
        if data is None:
            self.status("Select a data table first.")
            return None
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, "Export data table", self.export_path(f"{data.name}.csv", data), "CSV (*.csv)")
            if not path:
                return None
        try:
            table_frame(data).to_csv(path, index=False)
        except OSError as exc:
            self.status(f"The data table could not be saved to {path}: {exc.strerror or exc}.")
            return None
        self.remember_export(path)
        self.status(f"Saved {path}")
        self.log(f"data table {data.name} exported to {path}")
        return str(path)

    def export_layout(self) -> None:
        view = self.stack.currentWidget()
        if hasattr(view, "export_dialog") and isinstance(self.project.sheet(self.current_id() or ""), LayoutSheet):
            view.export_dialog()
        else:
            self.status("Select a page layout first.")

    def _on_graph(self, method) -> None:
        view = self.stack.currentWidget()
        if hasattr(view, "magic_dialog"):
            method(view)
        else:
            self.status("Select a graph first.")

    def export_graph(self) -> None:
        view = self.stack.currentWidget()
        if hasattr(view, "export_dialog"):
            view.export_dialog()
        else:
            self.status("Select a graph first.")

    def export_results(self) -> None:
        view = self.stack.currentWidget()
        if isinstance(view, ResultSheetView):
            view.export_csv()
        else:
            self.status("Select a results sheet first.")

    def copy_results(self) -> None:
        view = self.stack.currentWidget()
        if isinstance(view, ResultSheetView):
            view.copy()
        else:
            self.status("Select a results sheet first.")

    # --- Help -------------------------------------------------------------------------------------------------------

    def statistics_guide(self) -> QDialog:
        from cellquant.gui.guide import GuideDialog

        dialog = GuideDialog(self, statistics_text(), title="QuantAnalysis: statistics guide", modal=False)
        dialog.show()
        return dialog

    def statistics_reference(self, sheet: ResultSheet | None = None, term: str = ""):
        """The statistics reference, at the page for ``sheet`` (its analysis) or ``term`` when given. Made once and reused."""

        from cellquant.gui.quantanalysis import reference
        from cellquant.gui.quantanalysis.reference_window import ReferenceWindow

        window = getattr(self, "_reference", None)
        if window is None:
            window = self._reference = ReferenceWindow(self, reference.text(), statistics_text(), self.open_example)
        if sheet is not None:
            options = dict(sheet.options)
            if sheet.result is not None and sheet.result.analysis.startswith("Mixed-effects"):
                options["test"] = "mixed"  # repeated measures with blanks is fitted as a mixed model
            window.show_sheet(sheet.kind, options)
        elif term:
            window.show_name(term)
        window.show()
        window.raise_()
        window.activateWindow()
        return window

    def check_engine(self):
        """Help → Check the engine…: the engine on published reference data sets. Made once and reused."""

        from cellquant.gui.quantanalysis.engine_check import EngineCheckDialog

        dialog = getattr(self, "_engine_check", None)
        if dialog is None:
            dialog = self._engine_check = EngineCheckDialog(self)
        dialog.show()
        dialog.raise_()
        return dialog

    def open_example(self, name: str, data: dict, paired: bool = False) -> DataSheet:
        """A worked example from the statistics reference as a new data table, to analyze and compare."""

        sheet = self.project.add_data(DataSheet(name, ColumnTable.from_columns(data, title=name, paired=paired)))
        self.select(sheet.id)
        self.log(f"{name} added from the statistics reference")
        return sheet

    def show_log(self) -> QDialog:
        dialog = QDialog(self)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.setWindowTitle("QuantAnalysis log")
        dialog.resize(760, 480)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit("\n".join(self._log.lines) or "Nothing logged yet.")
        text.setReadOnly(True)
        layout.addWidget(text)
        row = QHBoxLayout()
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(text.toPlainText()))
        row.addStretch(1)
        row.addWidget(copy)
        layout.addLayout(row)
        dialog.show()
        return dialog

    def closeEvent(self, event) -> None:
        """Closing asks to save unsaved work (Save, Discard or Cancel). Opened from CellQuant, closing only hides the window and
        Analyze… shows it again, so Discard also puts the project back to its last save; CellQuant closing asks once (``eventFilter``)."""

        discarded = False
        if self.has_unsaved_changes() and not self._close_confirmed:
            answer = self.ask_save("closing QuantAnalysis")
            if answer == "save":
                if not self.save_project():
                    event.ignore()
                    return
            elif answer == "discard":
                discarded = True
            else:
                event.ignore()
                return
        self._close_confirmed = False
        self.clear_recovery()  # saved, discarded or nothing to save: a recovery copy is only for work lost without an answer (a crash)
        self._watch.stop()
        if discarded and self.parent() is not None:
            self._revert_to_saved()
        super().closeEvent(event)

    def _revert_to_saved(self) -> None:
        """The window is only hidden (it belongs to CellQuant and is shown again later): put the project back to its last save, or empty."""

        self._drop_views()
        if self.path is not None and self.path.is_file():
            try:
                project_file.load(self.path, self.project, self.experiment_dir())
            except CellQuantError:
                self.project.replace({}, {}, {})
                self.path = None
        else:
            self.project.defaults = dict(STAT_DEFAULTS)
            self.project.replace({}, {}, {})
            self.path = None
        self._saved = project_file.fingerprint(self.project)
        self.update_title()
        self.select(next(iter(self.project.data), None))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._watch.start()
        if not self._recovery_checked:
            QTimer.singleShot(0, self.offer_recovery)  # after the window is up: a recovery copy of unsaved work is offered


def analysis_blocker(kind: str, data: DataSheet | None) -> str | None:
    """Why analysis ``kind`` cannot run on ``data`` (the sentence shown in the status bar and as the tooltip of the greyed menu item),
    or None when it can. The one place that knows: Analyze → … and the data sheet's buttons grey themselves from it, and ``analyze`` refuses with it."""

    if kind == "pstack":
        return None
    if data is None:
        return "Select a data table first (or import one: File → Open CellQuant results folder…)."
    if data.contingency is not None and kind != "contingency":
        return "A contingency table is analyzed with Contingency table…; the other analyses need a column, grouped or XY table."
    if data.xy is not None and kind in ("compare", "anova", "outliers", "twoway", "roc", "blandaltman", "corrmatrix", "pca"):
        return ("An XY table is analyzed with Linear regression…, Nonlinear regression… or Correlation (XY)…; the column analyses need a "
                "column table.")
    if kind == "compare" and len(data.table) < 2:
        return "Comparing needs at least two columns."
    if kind == "anova" and len(data.table) < 3:
        return "Comparing three or more columns needs at least three columns. For two, use Compare two columns…."
    if kind == "nested":
        if data.grouped is not None:
            return "A nested analysis needs a column table with replicates; a grouped table has two factors."
        if data.replicates is None:
            return NESTED_NEEDS_REPLICATES
        if len(data.table) < 2:
            return "A nested analysis compares groups: it needs at least two columns."
    if kind == "twoway" and data.grouped is None:
        return "Two-way ANOVA needs a grouped table (rows × data sets): File → New grouped table, or import results with a data set factor."
    if kind in ("roc", "blandaltman"):
        name = "An ROC curve" if kind == "roc" else "A Bland-Altman analysis"
        if data.grouped is not None:
            return f"{name} needs a column table; a grouped table has two factors. Make a column table with one column per group."
        if len(data.table) < 2:
            return f"{name} needs at least two columns: {'the controls and the patients' if kind == 'roc' else 'method A and method B'}."
    if kind == "survival" and data.xy is None:
        return "Survival curves need an XY table with one row per subject: File → New survival table."
    if kind in ("corrmatrix", "pca"):
        name = "A correlation matrix" if kind == "corrmatrix" else "A principal component analysis"
        if data.grouped is not None:
            return f"{name} needs a column table with a variable in each column and a case in each row; a grouped table has two factors."
        if len(data.table) < 2:
            return f"{name} needs at least two columns (variables)."
    if kind == "contingency" and data.contingency is None:
        return "Contingency analyses need a contingency table of counts: File → New contingency table."
    if kind in ("linreg", "xycorr", "curvefit") and data.xy is None:
        return "Regression, curve fits and correlation of X with Y need an XY table: File → New XY table."
    return None


def process_blocker(data: DataSheet | None) -> str | None:
    """Why Analyze → Data processing cannot run on ``data``, or None."""

    if data is None:
        return "Select a data table first (in the list on the left)."
    if data.grouped is not None or data.xy is not None or data.contingency is not None:
        return "Data processing works on column tables; a grouped, XY or contingency table cannot be processed yet."
    return None


def result_name(kind: str, options: dict, data: DataSheet | None) -> str:
    """The name of a results sheet: the test and its data table."""

    where = f": {data.name}" if data is not None else ""
    if kind == "compare":
        return f"{COMPARE_TESTS[options['test']][0]}{where}"
    if kind == "anova":
        return f"{ANOVA_NAMES[options['test']][0]}{where}"
    if kind == "nested":
        return f"{'Nested t test' if data is not None and len(data.table) == 2 else 'Nested one-way ANOVA'}{where}"
    if kind == "outliers":
        return f"Outliers ({OUTLIER_NAMES[options.get('method', 'rout')]}){where}"
    if kind == "pstack":
        return "Stack of P values"
    if kind == "twoway":
        return f"Two-way ANOVA{where}"
    if kind == "linreg":
        return f"Linear regression{where}"
    if kind == "contingency":
        return f"Contingency{where}"
    if kind == "roc":
        return f"ROC curve{where}"
    if kind == "blandaltman":
        return f"Bland-Altman{where}"
    if kind == "survival":
        return f"Survival{where}"
    if kind == "corrmatrix":
        return f"Correlation matrix{where}"
    if kind == "pca":
        return f"PCA{where}"
    if kind == "curvefit":
        return f"Nonlinear regression ({CURVE_MODELS.get(str(options.get('model')), CURVE_MODELS['log_dose_response']).name}){where}"
    if kind == "xycorr":
        return f"Correlation ({'Spearman' if options.get('method') == 'spearman' else 'Pearson'}){where}"
    return {"colstats": "Column statistics", "normality": "Normality tests"}[kind] + where


def statistics_text() -> str:
    path = Path(__file__).resolve().parents[1] / "STATISTICS.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return "The statistics guide is missing. See docs/STATISTICS.md in the CellQuant folder."
