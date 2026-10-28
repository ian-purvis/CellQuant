"""QuantAnalysis dialogs: import CellQuant results, import a CSV, compare two columns, compare three or
more columns, identify outliers, a stack of P values, data processing, and the "Help me choose" test guide."""

from __future__ import annotations

import html

import pandas as pd
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QTextBrowser,
    QVBoxLayout,
)

from cellquant.errors import CellQuantError
from cellquant.gui.quantanalysis import tips
from cellquant.gui.quantanalysis.datagrid import DANGER_STYLE, NOTE_STYLE, WARNING_STYLE
from cellquant.gui.quantanalysis.project import ANOVA_NAMES, COMPARE_TESTS, OUTLIER_NAMES, parse_p_values
from cellquant.stats import STYLES, ColumnTable
from cellquant.stats import sources, transforms
from cellquant.stats.grouped import COMPARE, FAMILIES, GroupedTable, two_way_anova
from cellquant.stats.xy import XYTable, linear_regression, xy_correlation
from cellquant.stats.replicates import ReplicateTable, looks_like_replicate
from cellquant.stats.multcomp import (
    ALL_PAIRS_ONLY,
    EQUAL_SD_METHODS,
    FDR_METHODS,
    METHOD_NAMES,
    RANK_METHODS,
    STACK_METHODS,
    UNEQUAL_SD_METHODS,
    default_method,
)

DOT_NAMES = {
    "unit": "Biological replicate (N)",
    "image": "Image (technical replicate, n)",
    "object": "Object / cell (technical replicate, n)",
}


def _radio(group: QButtonGroup, layout, text: str, key, tip: str = "") -> QRadioButton:
    button = QRadioButton(text)
    button.setProperty("key", key)
    if tip:
        button.setToolTip(tip)
    group.addButton(button)
    layout.addWidget(button)
    return button


def _checked(group: QButtonGroup):
    button = group.checkedButton()
    return button.property("key") if button is not None else None


def _check(group: QButtonGroup, key) -> None:
    for button in group.buttons():
        if button.property("key") == key:
            button.setChecked(True)


# --- Import CellQuant results ---------------------------------------------------------------------------------


def suggested_spec(source: sources.Source) -> tuple[sources.ImportSpec, str]:
    """The suggested setup for image data, and a sentence saying what was chosen."""

    measures = [item for item in source.measures() if item.kind == "percent"]
    target = [item for item in measures if not item.label.endswith("of all objects")]
    measure = (target or measures or source.measures())[0]
    unit = source.default_unit()
    others = [name for name in source.metadata_columns() if name != unit]
    group_by = others[0] if others else None
    dot = "unit" if unit else "image"
    text = (f"{measure.label}; one dot per biological replicate ({unit}), pooled from its images; "
            if unit else f"{measure.label}; one dot per image (no replicate column found: put each replicate's images in its own folder); ")
    text += f"grouped by {group_by}." if group_by else "no group column found."
    return sources.ImportSpec(measure.key, dot=dot, unit=unit, group_by=group_by, approved_only=source.approvals_known), text


class ImportDialog(QDialog):
    """Choose the measure, what a dot is, the groups and their order; the table is previewed live."""

    def __init__(self, parent, source: sources.Source, spec: sources.ImportSpec | None = None):
        super().__init__(parent)
        self.setWindowTitle("Import CellQuant results")
        self.source = source
        self.suggested = False
        self.imported: sources.Imported | None = None
        self.resize(640, 640)
        layout = QVBoxLayout(self)
        count = len(source.images)
        where = QLabel(f"From <b>{html.escape(source.label)}</b> · {count} image{'s' if count != 1 else ''}"
                       + (f" · {len(source.objects)} objects" if source.objects is not None else ""))
        where.setToolTip(str(source.path or ""))
        head = QHBoxLayout()
        head.addWidget(where, 1)
        suggest = QPushButton("Use suggested setup")
        suggest.setToolTip(tips.SUGGESTED)
        suggest.clicked.connect(self._use_suggested)
        head.addWidget(suggest)
        layout.addLayout(head)
        form = QFormLayout()
        self.measure = QComboBox()
        self.measure.setToolTip(tips.MEASURE)
        for item in source.measures():
            self.measure.addItem(item.label, item.key)
            self.measure.setItemData(self.measure.count() - 1, item.key, Qt.ToolTipRole)
        form.addRow("Measure", self.measure)
        self.dot = QComboBox()
        self.dot.setToolTip(tips.DOT)
        for key, name in DOT_NAMES.items():
            self.dot.addItem(name, key)
        form.addRow("Dot =", self.dot)
        self.unit = QComboBox()
        self.unit.setToolTip(tips.UNIT)
        for name in source.metadata_columns():
            self.unit.addItem(name, name)
        if not self.unit.count():
            self.unit.addItem("none (the images are not in replicate folders)", None)
        form.addRow("Replicate column", self.unit)
        self.group_by = QComboBox()
        self.group_by.setToolTip(tips.GROUP_BY)
        form.addRow("Group by", self.group_by)
        self.column_by = QComboBox()
        self.column_by.setToolTip(tips.COLUMN_BY)
        form.addRow("Data sets by", self.column_by)
        self.approved = QCheckBox("Approved images only")
        self.approved.setToolTip(tips.APPROVED if source.approvals_known else "This folder does not record approvals (an export folder), so every image is used.")
        self.approved.setEnabled(source.approvals_known)
        self.approved.setChecked(source.approvals_known)
        form.addRow("", self.approved)
        layout.addLayout(form)
        order_title = QLabel("Columns, in order (drag to reorder, untick to leave out)")
        order_title.setToolTip(tips.ORDER)
        layout.addWidget(order_title)
        self.order = QListWidget()
        self.order.setDragDropMode(QAbstractItemView.InternalMove)
        self.order.setToolTip(tips.ORDER)
        self.order.setMaximumHeight(150)
        self.order.model().rowsMoved.connect(lambda *_args: self._preview())
        self.order.itemChanged.connect(lambda _item: self._preview())
        layout.addWidget(self.order)
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        self.preview.setStyleSheet(NOTE_STYLE)
        layout.addWidget(self.preview)
        self.warning = QLabel()
        self.warning.setWordWrap(True)
        layout.addWidget(self.warning)
        layout.addStretch(1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Import")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.measure.currentIndexChanged.connect(lambda _index: self._measure_changed())
        self.dot.currentIndexChanged.connect(lambda _index: self._dot_changed())
        self.unit.currentIndexChanged.connect(lambda _index: self._preview())
        self.group_by.currentIndexChanged.connect(lambda _index: self._fill_order())
        self.column_by.currentIndexChanged.connect(lambda _index: self._preview())
        self.approved.toggled.connect(lambda _on: self._preview())
        self._quiet = False
        self.set_spec(spec or self._default_spec())

    def _default_spec(self) -> sources.ImportSpec:
        spec, _text = suggested_spec(self.source)
        if not self.source.default_unit():
            spec.dot = "image"
        return spec

    def set_spec(self, spec: sources.ImportSpec) -> None:
        self._quiet = True
        self.measure.setCurrentIndex(max(0, self.measure.findData(spec.measure)))
        self._fill_groups()
        self.dot.setCurrentIndex(max(0, self.dot.findData(spec.dot)))
        if spec.unit:
            self.unit.setCurrentIndex(max(0, self.unit.findData(spec.unit)))
        self.group_by.setCurrentIndex(max(0, self.group_by.findData(spec.group_by)))
        self.column_by.setCurrentIndex(max(0, self.column_by.findData(spec.column_by)))
        if self.approved.isEnabled():
            self.approved.setChecked(spec.approved_only)
        self._quiet = False
        self._fill_order(spec.order)
        self._dot_changed()

    def _use_suggested(self) -> None:
        spec, text = suggested_spec(self.source)
        self.set_spec(spec)
        self.suggested = True
        self.preview.setToolTip(text)

    def _level(self) -> str:
        key = self.measure.currentData()
        return self.source.measure(key).level if key else "image"

    def _measure_changed(self) -> None:
        self._fill_groups()
        self._dot_changed()

    def _fill_groups(self) -> None:
        keep = self.group_by.currentData()
        self.group_by.blockSignals(True)
        self.group_by.clear()
        self.group_by.addItem("no grouping (one column)", None)
        for name in self.source.metadata_columns():
            self.group_by.addItem(name, name)
        if self._level() == "object":
            for name in self.source.object_groups():
                label = f"{self.source.names.get(name, name)} +/− (per object)" if name.endswith("_pos") else f"{name} (per object)"
                self.group_by.addItem(label, name)
        self.group_by.setCurrentIndex(max(0, self.group_by.findData(keep)))
        self.group_by.blockSignals(False)
        keep_column = self.column_by.currentData()
        self.column_by.blockSignals(True)
        self.column_by.clear()
        self.column_by.addItem("none (a column table)", None)
        for index in range(1, self.group_by.count()):
            self.column_by.addItem(self.group_by.itemText(index), self.group_by.itemData(index))
        self.column_by.setCurrentIndex(max(0, self.column_by.findData(keep_column)))
        self.column_by.blockSignals(False)
        self._fill_order()

    def _dot_changed(self) -> None:
        model = self.dot.model()
        object_row = self.dot.findData("object")
        model.item(object_row).setEnabled(self._level() == "object")
        unit_row = self.dot.findData("unit")
        has_unit = self.unit.findData(None) < 0
        model.item(unit_row).setEnabled(has_unit)
        if self.dot.currentData() == "object" and self._level() != "object" or self.dot.currentData() == "unit" and not has_unit:
            self.dot.setCurrentIndex(self.dot.findData("image"))
        self.unit.setEnabled(self.dot.currentData() == "unit")
        self._preview()

    def _fill_order(self, order: list[str] | None = None) -> None:
        names = sources.groups(self.source, self.group_by.currentData())
        if order:
            names = [name for name in order if name in names] + [name for name in names if name not in order]
        self.order.blockSignals(True)
        self.order.clear()
        for name in names:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
            item.setCheckState(Qt.Checked if not order or name in order else Qt.Unchecked)
            self.order.addItem(item)
        self.order.blockSignals(False)
        self._preview()

    def spec(self) -> sources.ImportSpec:
        order = [self.order.item(row).text() for row in range(self.order.count()) if self.order.item(row).checkState() == Qt.Checked]
        return sources.ImportSpec(
            measure=self.measure.currentData(), dot=self.dot.currentData(),
            unit=self.unit.currentData() if self.dot.currentData() == "unit" else (self.unit.currentData() or None),
            group_by=self.group_by.currentData(), order=order or None, approved_only=self.approved.isChecked(),
            column_by=self.column_by.currentData() if self.group_by.currentData() else None,
        )

    def _preview(self) -> None:
        if self._quiet:
            return
        ok = self.buttons.button(QDialogButtonBox.Ok)
        try:
            self.imported = sources.build(self.source, self.spec())
        except CellQuantError as exc:
            self.imported = None
            self.preview.setText("")
            self.warning.setText(f"⚠ {html.escape(str(exc))}")
            self.warning.setStyleSheet(WARNING_STYLE)
            self.warning.setVisible(True)
            ok.setEnabled(False)
            return
        ok.setEnabled(True)
        imported = self.imported
        self.preview.setText(html.escape(imported.description) + "<br>" + html.escape(" ".join(imported.notes)))
        self.warning.setText("<br>".join(f"⚠ {html.escape(text)}" for text in imported.warnings))
        self.warning.setStyleSheet(DANGER_STYLE if imported.pseudoreplicated else WARNING_STYLE)
        self.warning.setVisible(bool(imported.warnings))


# --- Import a CSV ---------------------------------------------------------------------------------------------


class ImportCsvDialog(QDialog):
    """Wide (one column per group) or long (a value column and a group column)."""

    def __init__(self, parent, frame: pd.DataFrame, name: str):
        super().__init__(parent)
        self.setWindowTitle(f"Import {name}")
        self.frame = frame
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"{len(frame)} rows, {len(frame.columns)} columns."))
        self.shape = QButtonGroup(self)
        _radio(self.shape, layout, "Wide: each column is a group", "wide", "Every numeric column becomes a column of the table.")
        _radio(self.shape, layout, "Long: one row per value, with a group column", "long",
               "One column holds the values and another names each row's group, as in objects.csv.")
        form = QFormLayout()
        self.value = QComboBox()
        self.group = QComboBox()
        for name in frame.columns:
            self.value.addItem(str(name), name)
            self.group.addItem(str(name), name)
        numeric = [name for name in frame.columns if pd.api.types.is_numeric_dtype(frame[name])]
        text = [name for name in frame.columns if name not in numeric]
        if numeric:
            self.value.setCurrentIndex(self.value.findData(numeric[0]))
        if text:
            self.group.setCurrentIndex(self.group.findData(text[0]))
        form.addRow("Values", self.value)
        form.addRow("Group", self.group)
        self.replicate = QComboBox()
        self.replicate.addItem("(none)", None)
        for name in frame.columns:
            self.replicate.addItem(str(name), name)
        self.replicate.setToolTip(tips.CSV_REPLICATE)
        guess = next((name for name in frame.columns if looks_like_replicate(name)), None)
        if guess is not None:
            self.replicate.setCurrentIndex(self.replicate.findData(guess))
            if guess in text:
                text.remove(guess)
                if text:
                    self.group.setCurrentIndex(self.group.findData(text[0]))
        form.addRow("Replicate", self.replicate)
        layout.addLayout(form)
        _check(self.shape, "long" if text and len(numeric) <= 2 else "wide")
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def table(self, title: str) -> ColumnTable:
        if _checked(self.shape) == "long":
            return ColumnTable.from_long(self.frame, value=self.value.currentData(), group=self.group.currentData(), title=title)
        numeric = {str(name): pd.to_numeric(self.frame[name], errors="coerce").to_numpy() for name in self.frame.columns
                   if pd.to_numeric(self.frame[name], errors="coerce").notna().any()}
        if not numeric:
            raise CellQuantError("The file has no column of numbers.")
        trimmed = {}
        for name, values in numeric.items():
            filled = [index for index, value in enumerate(values) if value == value]
            trimmed[name] = values[: filled[-1] + 1] if filled else values[:0]
        return ColumnTable.from_columns(trimmed, title=title)

    def replicates(self, title: str) -> ReplicateTable | None:
        """With a replicate column: every value with its replicate (the table's analyses then use the replicate means)."""

        replicate = self.replicate.currentData()
        if replicate is None:
            return None
        if _checked(self.shape) == "long":
            if replicate in (self.value.currentData(), self.group.currentData()):
                raise CellQuantError("Choose a replicate column other than the values and the group.")
            return ReplicateTable.from_long(self.frame, value=self.value.currentData(), group=self.group.currentData(),
                                            replicate=replicate, title=title)
        return ReplicateTable.from_wide(self.frame, replicate, title=title)


# --- Compare two columns --------------------------------------------------------------------------------------

TEST_TIPS = {
    "welch_t": tips.WELCH, "unpaired_t": tips.POOLED, "lognormal_t": tips.LOGNORMAL, "mann_whitney": tips.MANN_WHITNEY,
    "kolmogorov_smirnov": tips.KS, "paired_t": "Paired t test: is the mean of the within-replicate differences zero?",
    "ratio_paired_t": tips.RATIO, "wilcoxon": tips.WILCOXON,
}
DEFAULT_OPTIONS = {"test": "welch_t", "first": 0, "second": 1, "alternative": "two-sided", "ci": 0.95, "alpha": 0.05, "style": "classic"}


class CompareDialog(QDialog):
    """The t test dialog: columns, design, distribution, test, tails and P value options."""

    def __init__(self, parent, table: ColumnTable, options: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Compare two columns")
        self.table = table
        options = {**DEFAULT_OPTIONS, **({"test": "paired_t"} if table.paired and not options else {}), **(options or {})}
        layout = QVBoxLayout(self)
        columns = QGroupBox("Columns")
        columns_form = QFormLayout(columns)
        self.first = QComboBox()
        self.second = QComboBox()
        for box in (self.first, self.second):
            for name in table.names:
                box.addItem(name, name)
            box.setToolTip(tips.COLUMNS)
        columns_form.addRow("A (control, first)", self.first)
        columns_form.addRow("B (compared with A)", self.second)
        layout.addWidget(columns)
        design = QGroupBox("Experimental design")
        design_layout = QVBoxLayout(design)
        self.design = QButtonGroup(self)
        _radio(self.design, design_layout, "Unpaired: different replicates in each group", False, tips.PAIRED)
        _radio(self.design, design_layout, "Paired: the same replicate measured in both", True, tips.PAIRED)
        layout.addWidget(design)
        gaussian = QGroupBox("Assume a Gaussian distribution?")
        gaussian_layout = QVBoxLayout(gaussian)
        self.family = QButtonGroup(self)
        _radio(self.family, gaussian_layout, "Yes: parametric test (t test)", "parametric", tips.PARAMETRIC)
        _radio(self.family, gaussian_layout, "No: nonparametric test (ranks)", "nonparametric", tips.PARAMETRIC)
        layout.addWidget(gaussian)
        form = QFormLayout()
        self.test = QComboBox()
        form.addRow("Test", self.test)
        self.tails = QComboBox()
        self.tails.addItem("Two-tailed", "two-sided")
        self.tails.addItem("One-tailed, predicting B > A", "greater")
        self.tails.addItem("One-tailed, predicting B < A", "less")
        self.tails.setToolTip(tips.TAILS)
        form.addRow("P value", self.tails)
        self.alpha = QDoubleSpinBox()
        self.alpha.setRange(0.0001, 0.5)
        self.alpha.setDecimals(4)
        self.alpha.setSingleStep(0.01)
        self.alpha.setToolTip(tips.ALPHA)
        form.addRow("Alpha", self.alpha)
        self.ci = QComboBox()
        for value in (0.90, 0.95, 0.99):
            self.ci.addItem(f"{100 * value:g}%", value)
        self.ci.setToolTip(tips.CI_LEVEL)
        form.addRow("Confidence interval", self.ci)
        self.style = QComboBox()
        for name in STYLES:
            self.style.addItem(name, name)
        self.style.setToolTip(tips.P_STYLE)
        form.addRow("P value style", self.style)
        layout.addLayout(form)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet(WARNING_STYLE)
        layout.addWidget(self.note)
        bottom = QHBoxLayout()
        guide = QPushButton("Help me choose…")
        guide.setToolTip("Answer a few questions about your experiment; the guide picks the test and fills this dialog.")
        guide.clicked.connect(self._guide)
        bottom.addWidget(guide)
        bottom.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Run")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        bottom.addWidget(buttons)
        layout.addLayout(bottom)
        self.design.buttonToggled.connect(lambda *_args: self._fill_tests())
        self.family.buttonToggled.connect(lambda *_args: self._fill_tests())
        self.test.currentIndexChanged.connect(lambda _index: self._test_changed())
        self.set_options(options)

    def set_options(self, options: dict) -> None:
        for box, key in ((self.first, options["first"]), (self.second, options["second"])):
            index = box.findData(key) if isinstance(key, str) else int(key)
            box.setCurrentIndex(max(0, min(box.count() - 1, index)))
        if self.first.currentIndex() == self.second.currentIndex() and self.second.count() > 1:
            self.second.setCurrentIndex(1 if self.first.currentIndex() == 0 else 0)
        _name, paired, family = COMPARE_TESTS[options["test"]]
        _check(self.design, paired)
        _check(self.family, family)
        self._fill_tests()
        self.test.setCurrentIndex(max(0, self.test.findData(options["test"])))
        self.tails.setCurrentIndex(max(0, self.tails.findData(options["alternative"])))
        self.alpha.setValue(float(options["alpha"]))
        self.ci.setCurrentIndex(max(0, self.ci.findData(options["ci"])))
        self.style.setCurrentIndex(max(0, self.style.findData(options["style"])))

    def _fill_tests(self) -> None:
        paired, family = _checked(self.design), _checked(self.family)
        keep = self.test.currentData()
        self.test.blockSignals(True)
        self.test.clear()
        order = ["welch_t", "unpaired_t", "lognormal_t", "mann_whitney", "kolmogorov_smirnov", "paired_t", "ratio_paired_t", "wilcoxon"]
        for key in order:
            name, test_paired, test_family = COMPARE_TESTS[key]
            if test_paired == paired and test_family == family:
                self.test.addItem(name + (" (recommended)" if key == "welch_t" else ""), key)
                self.test.setItemData(self.test.count() - 1, TEST_TIPS[key], Qt.ToolTipRole)
        self.test.setCurrentIndex(max(0, self.test.findData(keep)))
        self.test.blockSignals(False)
        self._test_changed()

    def _test_changed(self) -> None:
        key = self.test.currentData()
        self.test.setToolTip(TEST_TIPS.get(key, ""))
        self.ci.setEnabled(key != "kolmogorov_smirnov")
        notes = []
        if _checked(self.design) and not self.table.paired:
            notes.append("The table is not marked Paired. A paired test lines up row i of each column; tick Paired on the data table if that is right.")
        if key in ("mann_whitney", "wilcoxon"):
            from cellquant.gui.quantanalysis.project import smallest_p

            a, b = self.table[self.first.currentIndex()].finite.size, self.table[self.second.currentIndex()].finite.size
            floor = smallest_p(key, min(a, b) if key == "wilcoxon" else a, b)
            if floor > self.alpha.value():
                notes.append(f"With n = {a} and {b}, this test cannot give P below {floor:.3g}.")
        self.note.setText(" ".join(notes))
        self.note.setVisible(bool(notes))

    def _guide(self) -> None:
        dialog = ChooseTestDialog(self, paired=bool(_checked(self.design)))
        choice = dialog.choice() if dialog.exec() else None
        if choice and choice["kind"] == "compare":
            self.set_options({**self.options(), **{key: value for key, value in choice.items() if key != "kind"}})
        elif choice:
            self.note.setText("Three or more groups use Analyze → Compare three or more columns….")
            self.note.setVisible(True)

    def options(self) -> dict:
        return {
            "test": self.test.currentData(), "first": self.first.currentData(), "second": self.second.currentData(),
            "alternative": self.tails.currentData(), "alpha": round(self.alpha.value(), 6), "ci": self.ci.currentData(),
            "style": self.style.currentData(),
        }

    def accept(self) -> None:
        if self.first.currentIndex() == self.second.currentIndex():
            self.note.setText("Choose two different columns.")
            self.note.setVisible(True)
            return
        super().accept()


# --- Compare three or more columns ----------------------------------------------------------------------------

ANOVA_TIPS = {"ordinary": tips.ANOVA_ORDINARY, "welch": tips.ANOVA_WELCH, "repeated_measures": tips.ANOVA_RM,
              "kruskal_wallis": tips.KRUSKAL, "friedman": tips.FRIEDMAN}
ANOVA_DEFAULTS = {"test": "ordinary", "geisser_greenhouse": True, "comparisons": "all", "method": None, "control": [], "pairs": [],
                  "family": "per_reference", "alpha": 0.05, "ci": 0.95, "style": "classic"}
GOALS = (("none", "None: only the overall test"), ("all", "Every pair of groups"), ("control", "Each group vs one or two reference groups"),
         ("selected", "Selected pairs"), ("trend", "Test for a linear trend across the columns"))


def methods_for(test: str, goal: str, combined: bool = False) -> list[str]:
    """The multiple comparisons methods valid after ``test`` for this goal, the recommended one first."""

    kind = "rank" if test in ("kruskal_wallis", "friedman") else "welch" if test == "welch" else "pooled"
    allowed = {"pooled": EQUAL_SD_METHODS, "welch": UNEQUAL_SD_METHODS, "rank": RANK_METHODS}[kind]
    methods = [name for name in allowed if not (name in ALL_PAIRS_ONLY and goal != "all") and not (name == "dunnett" and (goal != "control" or combined))]
    best = default_method(goal, kind)
    return ([best] if best in methods else []) + [name for name in methods if name != best]


class AnovaDialog(QDialog):
    """The one-way ANOVA dialog: design, Gaussian or not, the test, multiple comparisons (with one or two
    reference groups), alpha, CI level and P value style."""

    def __init__(self, parent, table: ColumnTable, options: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Compare three or more columns")
        self.table = table
        options = {**ANOVA_DEFAULTS, **({"test": "repeated_measures"} if table.paired and not options else {}), **(options or {})}
        layout = QVBoxLayout(self)
        columns = QHBoxLayout()
        left, right = QVBoxLayout(), QVBoxLayout()
        design = QGroupBox("Experimental design")
        design_layout = QVBoxLayout(design)
        self.design = QButtonGroup(self)
        _radio(self.design, design_layout, "Unpaired: different replicates in each group", False, tips.ANOVA_PAIRED)
        _radio(self.design, design_layout, "Paired: the same replicate measured in every group", True, tips.ANOVA_PAIRED)
        self.greenhouse = QCheckBox("Don't assume sphericity (Geisser-Greenhouse)")
        self.greenhouse.setToolTip(tips.GREENHOUSE)
        design_layout.addWidget(self.greenhouse)
        left.addWidget(design)
        gaussian = QGroupBox("Assume a Gaussian distribution?")
        gaussian_layout = QVBoxLayout(gaussian)
        self.family = QButtonGroup(self)
        _radio(self.family, gaussian_layout, "Yes: ANOVA (compares means)", "parametric", tips.ANOVA_GAUSSIAN)
        _radio(self.family, gaussian_layout, "No: Kruskal-Wallis or Friedman (compares ranks)", "nonparametric", tips.ANOVA_GAUSSIAN)
        left.addWidget(gaussian)
        form = QFormLayout()
        self.test = QComboBox()
        form.addRow("Test", self.test)
        self.alpha = QDoubleSpinBox()
        self.alpha.setRange(0.0001, 0.5)
        self.alpha.setDecimals(4)
        self.alpha.setSingleStep(0.01)
        self.alpha.setToolTip(tips.ANOVA_ALPHA)
        form.addRow("Alpha", self.alpha)
        self.ci = QComboBox()
        for value in (0.90, 0.95, 0.99):
            self.ci.addItem(f"{100 * value:g}%", value)
        self.ci.setToolTip(tips.ANOVA_CI)
        form.addRow("Confidence interval", self.ci)
        self.style = QComboBox()
        for name in STYLES:
            self.style.addItem(name, name)
        self.style.setToolTip(tips.ANOVA_STYLE)
        form.addRow("P value style", self.style)
        left.addLayout(form)
        left.addStretch(1)
        compare = QGroupBox("Multiple comparisons")
        compare_layout = QVBoxLayout(compare)
        self.goal = QButtonGroup(self)
        for key, text in GOALS:
            _radio(self.goal, compare_layout, text, key, tips.COMPARISONS_GOAL[key])
        compare_form = QFormLayout()
        self.method = QComboBox()
        self.method.setToolTip(tips.METHOD)
        compare_form.addRow("Method", self.method)
        self.control = QComboBox()
        self.control.setToolTip(tips.CONTROL)
        self.control2 = QComboBox()
        self.control2.setToolTip(tips.CONTROL2)
        compare_form.addRow("Reference group", self.control)
        compare_form.addRow("Second reference", self.control2)
        self.family_mode = QComboBox()
        self.family_mode.addItem("Each reference is its own family", "per_reference")
        self.family_mode.addItem("Both references as one family", "combined")
        self.family_mode.setToolTip(tips.FAMILY)
        compare_form.addRow("Families", self.family_mode)
        compare_layout.addLayout(compare_form)
        self.pairs = QListWidget()
        self.pairs.setToolTip(tips.PAIRS)
        self.pairs.setMaximumHeight(110)
        names = table.names
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                item = QListWidgetItem(f"{a} vs. {b}")
                item.setData(Qt.UserRole, (a, b))
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Unchecked)
                self.pairs.addItem(item)
        compare_layout.addWidget(self.pairs)
        right.addWidget(compare)
        columns.addLayout(left, 1)
        columns.addLayout(right, 1)
        layout.addLayout(columns)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet(WARNING_STYLE)
        layout.addWidget(self.note)
        bottom = QHBoxLayout()
        guide = QPushButton("Help me choose…")
        guide.setToolTip("Answer a few questions about your experiment; the guide picks the test and the comparisons and fills this dialog.")
        guide.clicked.connect(self._guide)
        bottom.addWidget(guide)
        bottom.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Run")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        bottom.addWidget(buttons)
        layout.addLayout(bottom)
        self.design.buttonToggled.connect(lambda *_args: self._fill_tests())
        self.family.buttonToggled.connect(lambda *_args: self._fill_tests())
        self.test.currentIndexChanged.connect(lambda _index: self._test_changed())
        self.goal.buttonToggled.connect(lambda *_args: self._goal_changed())
        self.control.currentIndexChanged.connect(lambda _index: self._fill_second())
        self.control2.currentIndexChanged.connect(lambda _index: self._goal_changed())
        self.family_mode.currentIndexChanged.connect(lambda _index: self._fill_methods())
        self._quiet = False
        self.set_options(options)

    def set_options(self, options: dict) -> None:
        options = {**ANOVA_DEFAULTS, **options}
        self._quiet = True
        _name, paired, family = ANOVA_NAMES[options["test"]]
        _check(self.design, paired)
        _check(self.family, family)
        self.greenhouse.setChecked(bool(options["geisser_greenhouse"]))
        self.control.clear()
        for name in self.table.names:
            self.control.addItem(name, name)
        control = [name for name in options["control"] if name in self.table.names]
        goal = options["comparisons"] or "none"
        if goal == "control" and not control and self.table.names:
            control = [self.table.names[0]]
        self.control.setCurrentIndex(max(0, self.control.findData(control[0] if control else None)))
        self._fill_second(control[1] if len(control) > 1 else None)
        self.family_mode.setCurrentIndex(max(0, self.family_mode.findData(options["family"])))
        _check(self.goal, goal)
        chosen = {tuple(pair) for pair in options["pairs"]}
        for row in range(self.pairs.count()):
            item = self.pairs.item(row)
            item.setCheckState(Qt.Checked if item.data(Qt.UserRole) in chosen else Qt.Unchecked)
        self.alpha.setValue(float(options["alpha"]))
        self.ci.setCurrentIndex(max(0, self.ci.findData(options["ci"])))
        self.style.setCurrentIndex(max(0, self.style.findData(options["style"])))
        self._quiet = False
        self._fill_tests(options["test"], options["method"])

    def _fill_tests(self, keep: str | None = None, method: str | None = None) -> None:
        if self._quiet:
            return
        paired, family = _checked(self.design), _checked(self.family)
        keep = keep or self.test.currentData()
        self.test.blockSignals(True)
        self.test.clear()
        for key, (name, test_paired, test_family) in ANOVA_NAMES.items():
            if test_paired == paired and test_family == family:
                self.test.addItem(name, key)
                self.test.setItemData(self.test.count() - 1, ANOVA_TIPS[key], Qt.ToolTipRole)
        self.test.setCurrentIndex(max(0, self.test.findData(keep)))
        self.test.blockSignals(False)
        self._test_changed(method)

    def _test_changed(self, method: str | None = None) -> None:
        if self._quiet:
            return
        key = self.test.currentData()
        self.test.setToolTip(ANOVA_TIPS.get(key, ""))
        self.greenhouse.setEnabled(key == "repeated_measures")
        self.ci.setEnabled(key not in ("kruskal_wallis", "friedman"))
        trend = self.goal.buttons()[-1]
        trend.setEnabled(key in ("ordinary", "repeated_measures"))
        if not trend.isEnabled() and trend.isChecked():
            _check(self.goal, "all")
        self._goal_changed(method)

    def _fill_second(self, keep: str | None = None) -> None:
        keep = keep if keep is not None else self.control2.currentData()
        self.control2.blockSignals(True)
        self.control2.clear()
        self.control2.addItem("none", None)
        for name in self.table.names:
            if name != self.control.currentData():
                self.control2.addItem(name, name)
        self.control2.setCurrentIndex(max(0, self.control2.findData(keep)))
        self.control2.blockSignals(False)
        self._goal_changed()

    def _goal_changed(self, method: str | None = None) -> None:
        if self._quiet:
            return
        goal = _checked(self.goal)
        for widget in (self.control, self.control2):
            widget.setEnabled(goal == "control")
        self.family_mode.setEnabled(goal == "control" and self.control2.currentData() is not None)
        self.pairs.setEnabled(goal == "selected")
        self.method.setEnabled(goal in ("all", "control", "selected"))
        self._fill_methods(method)
        self._notes()

    def _fill_methods(self, keep: str | None = None) -> None:
        if self._quiet:
            return
        goal, test = _checked(self.goal), self.test.currentData()
        keep = keep or self.method.currentData()
        combined = self.family_mode.isEnabled() and self.family_mode.currentData() == "combined"
        methods = methods_for(test, goal, combined) if goal in ("all", "control", "selected") else []
        self.method.blockSignals(True)
        self.method.clear()
        for number, name in enumerate(methods):
            self.method.addItem(METHOD_NAMES[name] + (" (recommended)" if number == 0 else ""), name)
            self.method.setItemData(self.method.count() - 1, tips.METHOD_TIPS[name], Qt.ToolTipRole)
        self.method.setCurrentIndex(max(0, self.method.findData(keep)))
        self.method.blockSignals(False)
        self.method.setToolTip(tips.METHOD_TIPS.get(self.method.currentData(), tips.METHOD))

    def _notes(self) -> None:
        notes = []
        if _checked(self.design) and not self.table.paired:
            notes.append("The table is not marked Paired. Repeated measures line up row i of every column; tick Paired on the data table if that is right.")
        if len(self.table) < 3:
            notes.append("This table has fewer than three columns; use Compare two columns….")
        self.note.setText(" ".join(notes))
        self.note.setVisible(bool(notes))

    def _guide(self) -> None:
        dialog = ChooseTestDialog(self, paired=bool(_checked(self.design)), groups=3)
        choice = dialog.choice() if dialog.exec() else None
        if choice and choice["kind"] == "anova":
            self.set_options({**self.options(), **{key: value for key, value in choice.items() if key != "kind"}})

    def options(self) -> dict:
        goal = _checked(self.goal)
        control = [box.currentData() for box in (self.control, self.control2) if box.currentData() is not None]
        return {
            "test": self.test.currentData(), "geisser_greenhouse": self.greenhouse.isChecked(),
            "comparisons": None if goal == "none" else goal,
            "method": self.method.currentData() if goal in ("all", "control", "selected") else None,
            "control": control if goal == "control" else [],
            "pairs": [list(self.pairs.item(row).data(Qt.UserRole)) for row in range(self.pairs.count())
                      if self.pairs.item(row).checkState() == Qt.Checked] if goal == "selected" else [],
            "family": self.family_mode.currentData(), "alpha": round(self.alpha.value(), 6), "ci": self.ci.currentData(),
            "style": self.style.currentData(),
        }

    def accept(self) -> None:
        goal, options = _checked(self.goal), self.options()
        if len(self.table) < 3:
            self._notes()
            return
        if goal == "selected" and not options["pairs"]:
            self.note.setText("Tick at least one pair to compare.")
            self.note.setVisible(True)
            return
        super().accept()


# --- Two-way ANOVA --------------------------------------------------------------------------------------------

TWO_WAY_DEFAULTS = {"interaction": True, "compare": "within_rows", "goal": "all", "method": "tukey", "control": None,
                    "family": "per_group", "alpha": 0.05, "ci": 0.95, "style": "classic"}
TWO_WAY_SIMPLE = ("within_rows", "within_columns")


def two_way_methods(goal: str, family: str) -> list[str]:
    """Methods offered for a two-way follow-up, the default first."""

    if family == "combined":
        return ["sidak", "holm_sidak", "bonferroni", "fisher_lsd", "bky", "bh", "by"]
    first = "dunnett" if goal == "control" else "tukey"
    return [first, "sidak", "holm_sidak", "bonferroni", "fisher_lsd", "bky", "bh", "by"]


class TwoWayDialog(QDialog):
    """Two-way ANOVA of a grouped table: the model (with or without interaction) and which means to compare."""

    def __init__(self, parent, grouped: GroupedTable, options: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Two-way ANOVA")
        self.grouped = grouped
        options = {**TWO_WAY_DEFAULTS, **(options or {})}
        layout = QVBoxLayout(self)
        intro = QLabel(f"{len(grouped.rows)} rows × {len(grouped.columns)} data sets. {tips.TWO_WAY}")
        intro.setWordWrap(True)
        intro.setStyleSheet(NOTE_STYLE)
        layout.addWidget(intro)
        form = QFormLayout()
        self.interaction = QCheckBox("Fit the interaction (row × data set)")
        self.interaction.setToolTip(tips.TWO_WAY_INTERACTION)
        form.addRow("Model", self.interaction)
        self.compare = QComboBox()
        self.compare.addItem("No multiple comparisons", None)
        for key, name in COMPARE.items():
            self.compare.addItem(name, key)
        self.compare.setToolTip(tips.TWO_WAY_COMPARE)
        form.addRow("Compare", self.compare)
        self.goal = QComboBox()
        self.goal.addItem("Every pair", "all")
        self.goal.addItem("Each with a reference (control)", "control")
        form.addRow("Which pairs", self.goal)
        self.control = QComboBox()
        self.control.setToolTip("The reference group every other group is compared with.")
        form.addRow("Reference", self.control)
        self.family = QComboBox()
        for key, name in FAMILIES.items():
            self.family.addItem(name, key)
        self.family.setToolTip(tips.TWO_WAY_FAMILY)
        form.addRow("Family", self.family)
        self.method = QComboBox()
        self.method.setToolTip(tips.TWO_WAY_METHOD)
        form.addRow("Test", self.method)
        self.alpha = QDoubleSpinBox()
        self.alpha.setRange(0.0001, 0.5)
        self.alpha.setDecimals(4)
        self.alpha.setSingleStep(0.01)
        self.alpha.setToolTip(tips.ANOVA_ALPHA)
        form.addRow("Alpha", self.alpha)
        self.ci = QComboBox()
        for value in (0.90, 0.95, 0.99):
            self.ci.addItem(f"{100 * value:g}%", value)
        self.ci.setToolTip(tips.ANOVA_CI)
        form.addRow("Confidence interval", self.ci)
        self.style = QComboBox()
        for name in STYLES:
            self.style.addItem(name, name)
        self.style.setToolTip(tips.ANOVA_STYLE)
        form.addRow("P value style", self.style)
        layout.addLayout(form)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet(WARNING_STYLE)
        self.note.setVisible(False)
        layout.addWidget(self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.interaction.setChecked(bool(options["interaction"]))
        self.compare.setCurrentIndex(max(0, self.compare.findData(options["compare"])))
        self.goal.setCurrentIndex(max(0, self.goal.findData(options["goal"])))
        self.family.setCurrentIndex(max(0, self.family.findData(options["family"])))
        self.alpha.setValue(float(options["alpha"]))
        self.ci.setCurrentIndex(max(0, self.ci.findData(options["ci"])))
        self.style.setCurrentIndex(max(0, self.style.findData(options["style"])))
        for box in (self.compare, self.goal, self.family):
            box.currentIndexChanged.connect(lambda _index: self._update())
        self._update(options.get("control"), options.get("method"))

    def _inner(self) -> list[str]:
        """The groups compared: data sets (within rows, main column effect) or rows."""

        return list(self.grouped.rows if self.compare.currentData() in ("within_columns", "row_means") else self.grouped.columns)

    def _update(self, control: str | None = None, method: str | None = None) -> None:
        compare = self.compare.currentData()
        on = compare is not None
        for widget in (self.goal, self.method):
            widget.setEnabled(on)
        self.family.setEnabled(compare in TWO_WAY_SIMPLE)
        goal = self.goal.currentData()
        keep = control or self.control.currentData()
        self.control.clear()
        for name in self._inner():
            self.control.addItem(name, name)
        self.control.setCurrentIndex(max(0, self.control.findData(keep)))
        self.control.setEnabled(on and goal == "control")
        family = self.family.currentData() if compare in TWO_WAY_SIMPLE else "per_group"
        keep_method = method or self.method.currentData()
        self.method.clear()
        for name in two_way_methods(goal, family):
            self.method.addItem(METHOD_NAMES[name], name)
        self.method.setCurrentIndex(max(0, self.method.findData(keep_method)))

    def options(self) -> dict:
        compare = self.compare.currentData()
        return {
            "interaction": self.interaction.isChecked(), "compare": compare, "goal": self.goal.currentData(),
            "method": self.method.currentData() if compare else None,
            "control": self.control.currentData() if compare and self.goal.currentData() == "control" else None,
            "family": self.family.currentData() if compare in TWO_WAY_SIMPLE else "per_group",
            "alpha": round(self.alpha.value(), 6), "ci": self.ci.currentData(), "style": self.style.currentData(),
        }

    def accept(self) -> None:
        try:
            two_way_anova(self.grouped, **{key: value for key, value in self.options().items() if value is not None})
        except CellQuantError as exc:
            self.note.setText(str(exc))
            self.note.setVisible(True)
            return
        super().accept()


# --- XY tables: linear regression and correlation -------------------------------------------------------------

LINREG_DEFAULTS = {"through_origin": False, "replicates": "each", "ci": 0.95, "alpha": 0.05, "style": "classic"}
XYCORR_DEFAULTS = {"method": "pearson", "replicates": "each", "ci": 0.95, "alpha": 0.05, "style": "classic"}


class XYDialog(QDialog):
    """Linear regression (``kind`` "linreg") or correlation ("xycorr") of an XY table's data sets with X."""

    def __init__(self, parent, xy: XYTable, kind: str, options: dict | None = None):
        super().__init__(parent)
        self.xy, self.kind = xy, kind
        self.setWindowTitle("Linear regression" if kind == "linreg" else "Correlation")
        options = {**(LINREG_DEFAULTS if kind == "linreg" else XYCORR_DEFAULTS), **(options or {})}
        layout = QVBoxLayout(self)
        intro = QLabel(f"{len(xy.sets)} data set(s) against {xy.x_title}. {tips.LINREG if kind == 'linreg' else tips.XYCORR}")
        intro.setWordWrap(True)
        intro.setStyleSheet(NOTE_STYLE)
        layout.addWidget(intro)
        form = QFormLayout()
        if kind == "linreg":
            self.origin = QCheckBox("Force the line through X = 0, Y = 0")
            self.origin.setToolTip(tips.LINREG_ORIGIN)
            self.origin.setChecked(bool(options["through_origin"]))
            form.addRow("Model", self.origin)
        else:
            self.method = QComboBox()
            self.method.addItem("Pearson r (Gaussian data)", "pearson")
            self.method.addItem("Spearman r (ranks, no Gaussian assumption)", "spearman")
            self.method.setToolTip(tips.XYCORR_METHOD)
            self.method.setCurrentIndex(max(0, self.method.findData(options["method"])))
            form.addRow("Method", self.method)
        self.replicates = QComboBox()
        self.replicates.addItem("Each replicate is a point", "each")
        self.replicates.addItem("Only the mean of each row", "mean")
        self.replicates.setToolTip(tips.XY_REPLICATES)
        self.replicates.setCurrentIndex(max(0, self.replicates.findData(options["replicates"])))
        self.replicates.setEnabled(xy.replicates > 1)
        form.addRow("Replicates", self.replicates)
        self.alpha = QDoubleSpinBox()
        self.alpha.setRange(0.0001, 0.5)
        self.alpha.setDecimals(4)
        self.alpha.setSingleStep(0.01)
        self.alpha.setToolTip(tips.ANOVA_ALPHA)
        self.alpha.setValue(float(options["alpha"]))
        form.addRow("Alpha", self.alpha)
        self.ci = QComboBox()
        for value in (0.90, 0.95, 0.99):
            self.ci.addItem(f"{100 * value:g}%", value)
        self.ci.setToolTip(tips.ANOVA_CI)
        self.ci.setCurrentIndex(max(0, self.ci.findData(options["ci"])))
        form.addRow("Confidence interval", self.ci)
        self.style = QComboBox()
        for name in STYLES:
            self.style.addItem(name, name)
        self.style.setToolTip(tips.ANOVA_STYLE)
        self.style.setCurrentIndex(max(0, self.style.findData(options["style"])))
        form.addRow("P value style", self.style)
        layout.addLayout(form)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet(WARNING_STYLE)
        self.note.setVisible(False)
        layout.addWidget(self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def options(self) -> dict:
        out = {"replicates": self.replicates.currentData(), "ci": self.ci.currentData(), "alpha": round(self.alpha.value(), 6),
               "style": self.style.currentData()}
        if self.kind == "linreg":
            out["through_origin"] = self.origin.isChecked()
        else:
            out["method"] = self.method.currentData()
        return out

    def accept(self) -> None:
        try:
            (linear_regression if self.kind == "linreg" else xy_correlation)(self.xy, **self.options())
        except CellQuantError as exc:
            self.note.setText(str(exc))
            self.note.setVisible(True)
            return
        super().accept()


# --- Identify outliers ----------------------------------------------------------------------------------------

OUTLIER_TIPS = {"rout": tips.ROUT, "grubbs": tips.GRUBBS, "iterative_grubbs": tips.ITERATIVE_GRUBBS}
OUTLIER_DEFAULTS = {"method": "rout", "q": 0.01, "alpha": 0.05, "columns": None, "style": "classic"}


class OutlierDialog(QDialog):
    """Identify outliers: ROUT (Q), Grubbs' or iterative Grubbs' (alpha), for each ticked column."""

    def __init__(self, parent, table: ColumnTable, options: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Identify outliers")
        self.table = table
        options = {**OUTLIER_DEFAULTS, **(options or {})}
        layout = QVBoxLayout(self)
        method = QGroupBox("Method")
        method_layout = QVBoxLayout(method)
        self.method = QButtonGroup(self)
        for key, name in (("rout", "ROUT (recommended)"), ("grubbs", "Grubbs' test"), ("iterative_grubbs", "Iterative Grubbs' test")):
            _radio(self.method, method_layout, name, key, OUTLIER_TIPS[key])
        method.setToolTip(tips.OUTLIER_METHOD)
        layout.addWidget(method)
        form = QFormLayout()
        self.q = QDoubleSpinBox()
        self.q.setRange(0.1, 25)
        self.q.setDecimals(1)
        self.q.setSuffix(" %")
        self.q.setToolTip(tips.OUTLIER_Q)
        form.addRow("ROUT: Q", self.q)
        self.alpha = QDoubleSpinBox()
        self.alpha.setRange(0.001, 0.2)
        self.alpha.setDecimals(3)
        self.alpha.setSingleStep(0.01)
        self.alpha.setToolTip(tips.OUTLIER_ALPHA)
        form.addRow("Grubbs: alpha", self.alpha)
        layout.addLayout(form)
        title = QLabel("Columns to test")
        title.setToolTip(tips.OUTLIER_COLUMNS)
        layout.addWidget(title)
        self.columns = QListWidget()
        self.columns.setToolTip(tips.OUTLIER_COLUMNS)
        self.columns.setMaximumHeight(130)
        chosen = options["columns"]
        for name in table.names:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if chosen is None or name in chosen else Qt.Unchecked)
            self.columns.addItem(item)
        layout.addWidget(self.columns)
        shown = QLabel(tips.OUTLIERS_SHOWN)
        shown.setWordWrap(True)
        shown.setStyleSheet(NOTE_STYLE)
        layout.addWidget(shown)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet(WARNING_STYLE)
        self.note.setVisible(False)
        layout.addWidget(self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Run")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.method.buttonToggled.connect(lambda *_args: self._method_changed())
        self.style_value = options["style"]
        _check(self.method, options["method"])
        self.q.setValue(100 * float(options["q"]))
        self.alpha.setValue(float(options["alpha"]))
        self._method_changed()

    def _method_changed(self) -> None:
        rout = _checked(self.method) == "rout"
        self.q.setEnabled(rout)
        self.alpha.setEnabled(not rout)

    def options(self) -> dict:
        ticked = [self.columns.item(row).text() for row in range(self.columns.count()) if self.columns.item(row).checkState() == Qt.Checked]
        return {"method": _checked(self.method), "q": round(self.q.value() / 100, 6), "alpha": round(self.alpha.value(), 6),
                "columns": None if len(ticked) == self.columns.count() else ticked, "style": self.style_value}

    def accept(self) -> None:
        if not self.options()["columns"] and self.options()["columns"] is not None:
            self.note.setText("Tick at least one column.")
            self.note.setVisible(True)
            return
        super().accept()


# --- Data processing ------------------------------------------------------------------------------------------

PROCESS_DEFAULTS = {
    "transform": {"function": "log10", "k": 100.0, "columns": None},
    "normalize": {"zero": "smallest", "hundred": "largest", "zero_value": 0.0, "hundred_value": 100.0, "result": "percent", "columns": None},
    "fraction": {"total": "column", "result": "percent", "columns": None},
    "baseline": {"baseline": None, "operation": "percent", "by": "mean", "keep_baseline": True},
    "column_math": {"a": None, "b": None, "operation": "difference"},
}


class ProcessDialog(QDialog):
    """Transform, normalize, fraction of total, remove baseline or column math: the options of one kind of processing."""

    def __init__(self, parent, table: ColumnTable, kind: str, options: dict | None = None):
        super().__init__(parent)
        self.kind = kind
        self.table = table
        self.setWindowTitle(transforms.PROCESSES[kind])
        options = {**PROCESS_DEFAULTS[kind], **(options or {}), "kind": kind}
        layout = QVBoxLayout(self)
        intro = QLabel(tips.PROCESS[kind])
        intro.setWordWrap(True)
        intro.setStyleSheet(NOTE_STYLE)
        layout.addWidget(intro)
        form = QFormLayout()
        layout.addLayout(form)
        names = table.names
        self.columns = None
        if kind == "transform":
            self.function = QComboBox()
            for key, (label, _needs_k) in transforms.TRANSFORMS.items():
                self.function.addItem(label, key)
                self.function.setItemData(self.function.count() - 1, tips.FUNCTION[key], Qt.ToolTipRole)
            self.function.setCurrentIndex(max(0, self.function.findData(options["function"])))
            self.function.currentIndexChanged.connect(lambda _index: self._function_changed())
            form.addRow("Function", self.function)
            self.k = QDoubleSpinBox()
            self.k.setRange(-1e9, 1e9)
            self.k.setDecimals(6)
            self.k.setValue(float(options["k"] if options.get("k") is not None else 100.0))
            self.k.setToolTip(tips.K_VALUE)
            form.addRow("K", self.k)
            self.function_note = QLabel()
            self.function_note.setWordWrap(True)
            self.function_note.setStyleSheet(NOTE_STYLE)
            form.addRow(self.function_note)
            self._function_changed()
        elif kind == "normalize":
            self.zero = self._combo(transforms.NORMALIZE_ZERO, options["zero"], tips.NORMALIZE_ZERO)
            self.zero_value = self._number(options["zero_value"], tips.NORMALIZE_ZERO)
            self.hundred = self._combo(transforms.NORMALIZE_HUNDRED, options["hundred"], tips.NORMALIZE_HUNDRED)
            self.hundred_value = self._number(options["hundred_value"], tips.NORMALIZE_HUNDRED)
            form.addRow("0% is", self.zero)
            form.addRow("  number", self.zero_value)
            form.addRow("100% is", self.hundred)
            form.addRow("  number", self.hundred_value)
            self.result = self._combo({"percent": "Percentages (0 to 100)", "fraction": "Fractions (0 to 1)"}, options["result"], tips.RESULT)
            form.addRow("Results as", self.result)
            self.zero.currentIndexChanged.connect(lambda _index: self._values_enabled())
            self.hundred.currentIndexChanged.connect(lambda _index: self._values_enabled())
            self._values_enabled()
        elif kind == "fraction":
            self.total = self._combo(transforms.FRACTION_TOTALS, options["total"], tips.FRACTION_TOTAL)
            form.addRow("Divide by", self.total)
            self.result = self._combo({"percent": "Percentages (0 to 100)", "fraction": "Fractions (0 to 1)"}, options["result"], tips.RESULT)
            form.addRow("Results as", self.result)
        elif kind == "baseline":
            self.baseline = self._combo({name: name for name in names}, options["baseline"] or (names[0] if names else None), tips.BASELINE)
            form.addRow("Baseline column", self.baseline)
            self.operation = self._combo(transforms.BASELINE_OPERATIONS, options["operation"], tips.BASELINE_OPERATION)
            form.addRow("Compute", self.operation)
            self.by = self._combo(transforms.BASELINE_BY, options["by"], tips.BASELINE_BY)
            form.addRow("Baseline value", self.by)
            self.keep = QCheckBox("Keep the baseline column")
            self.keep.setChecked(bool(options["keep_baseline"]))
            self.keep.setToolTip(tips.KEEP_BASELINE)
            form.addRow(self.keep)
        elif kind == "column_math":
            self.a = self._combo({name: name for name in names}, options["a"] or (names[1] if len(names) > 1 else names[0] if names else None),
                                 tips.COLUMN_A)
            self.b = self._combo({name: name for name in names}, options["b"] or (names[0] if names else None), tips.COLUMN_B)
            self.operation = self._combo({key: text.format(a="A", b="B") for key, (text, _function) in transforms.COLUMN_MATH.items()},
                                         options["operation"], tips.MATH_OPERATION)
            form.addRow("Column A", self.a)
            form.addRow("Column B", self.b)
            form.addRow("Compute", self.operation)
        if kind in ("transform", "normalize", "fraction"):
            title = QLabel("Columns")
            title.setToolTip(tips.PROCESS_COLUMNS)
            layout.addWidget(title)
            self.columns = QListWidget()
            self.columns.setToolTip(tips.PROCESS_COLUMNS)
            self.columns.setMaximumHeight(130)
            chosen = options.get("columns")
            for name in names:
                item = QListWidgetItem(name)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if chosen is None or name in chosen else Qt.Unchecked)
                self.columns.addItem(item)
            layout.addWidget(self.columns)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet(WARNING_STYLE)
        self.note.setVisible(False)
        layout.addWidget(self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Create table")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _combo(self, choices: dict, current, tip: str) -> QComboBox:
        combo = QComboBox()
        for key, text in choices.items():
            combo.addItem(text, key)
        combo.setCurrentIndex(max(0, combo.findData(current)))
        combo.setToolTip(tip)
        return combo

    def _number(self, value, tip: str) -> QDoubleSpinBox:
        box = QDoubleSpinBox()
        box.setRange(-1e12, 1e12)
        box.setDecimals(6)
        box.setValue(float(value))
        box.setToolTip(tip)
        return box

    def _function_changed(self) -> None:
        key = self.function.currentData()
        self.k.setEnabled(transforms.TRANSFORMS[key][1])
        self.function_note.setText(tips.FUNCTION[key])

    def _values_enabled(self) -> None:
        self.zero_value.setEnabled(self.zero.currentData() == "value")
        self.hundred_value.setEnabled(self.hundred.currentData() == "value")

    def options(self) -> dict:
        options = {"kind": self.kind}
        if self.kind == "transform":
            options.update(function=self.function.currentData(), k=round(self.k.value(), 6) if self.k.isEnabled() else None)
        elif self.kind == "normalize":
            options.update(zero=self.zero.currentData(), hundred=self.hundred.currentData(), zero_value=self.zero_value.value(),
                           hundred_value=self.hundred_value.value(), result=self.result.currentData())
        elif self.kind == "fraction":
            options.update(total=self.total.currentData(), result=self.result.currentData())
        elif self.kind == "baseline":
            options.update(baseline=self.baseline.currentData(), operation=self.operation.currentData(), by=self.by.currentData(),
                           keep_baseline=self.keep.isChecked())
        else:
            options.update(a=self.a.currentData(), b=self.b.currentData(), operation=self.operation.currentData())
        if self.columns is not None:
            ticked = [self.columns.item(row).text() for row in range(self.columns.count())
                      if self.columns.item(row).checkState() == Qt.Checked]
            options["columns"] = None if len(ticked) == self.columns.count() else ticked
        return options

    def accept(self) -> None:
        try:
            transforms.process(self.table, self.options())
        except CellQuantError as exc:
            self.note.setText(str(exc))
            self.note.setVisible(True)
            return
        super().accept()


# --- Analyze a stack of P values ------------------------------------------------------------------------------

PSTACK_DEFAULTS = {"text": "", "method": "bky", "alpha": 0.05, "style": "classic"}


class PStackDialog(QDialog):
    """Analyze a stack of P values: paste P values, correct them for multiple testing."""

    def __init__(self, parent, options: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Analyze a stack of P values")
        self.resize(520, 520)
        options = {**PSTACK_DEFAULTS, **(options or {})}
        layout = QVBoxLayout(self)
        intro = QLabel(tips.PSTACK_TEXT)
        intro.setWordWrap(True)
        intro.setStyleSheet(NOTE_STYLE)
        layout.addWidget(intro)
        self.text = QPlainTextEdit(options["text"])
        self.text.setPlaceholderText("0.012\n0.0004\nComparison 3  0.21")
        self.text.setToolTip(tips.PSTACK_TEXT)
        layout.addWidget(self.text, 1)
        form = QFormLayout()
        self.method = QComboBox()
        for key, name in STACK_METHODS.items():
            if key in ("none",):
                continue
            self.method.addItem(("FDR: " if key in FDR_METHODS else "") + name + (" (recommended)" if key == "bky" else ""), key)
            self.method.setItemData(self.method.count() - 1, tips.METHOD_TIPS.get(key, tips.PSTACK_METHOD), Qt.ToolTipRole)
        self.method.setToolTip(tips.PSTACK_METHOD)
        self.method.setCurrentIndex(max(0, self.method.findData(options["method"])))
        form.addRow("Correction", self.method)
        self.alpha = QDoubleSpinBox()
        self.alpha.setRange(0.0001, 0.5)
        self.alpha.setDecimals(4)
        self.alpha.setSingleStep(0.01)
        self.alpha.setValue(float(options["alpha"]))
        self.alpha.setToolTip(tips.PSTACK_ALPHA)
        form.addRow("Q or alpha", self.alpha)
        self.style = QComboBox()
        for name in STYLES:
            self.style.addItem(name, name)
        self.style.setToolTip(tips.P_STYLE)
        self.style.setCurrentIndex(max(0, self.style.findData(options["style"])))
        form.addRow("P value style", self.style)
        layout.addLayout(form)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet(WARNING_STYLE)
        self.note.setVisible(False)
        layout.addWidget(self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Run")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def options(self) -> dict:
        return {"text": self.text.toPlainText(), "method": self.method.currentData(), "alpha": round(self.alpha.value(), 6),
                "style": self.style.currentData()}

    def accept(self) -> None:
        try:
            parse_p_values(self.text.toPlainText())
        except CellQuantError as exc:
            self.note.setText(str(exc))
            self.note.setVisible(True)
            return
        if not self.text.toPlainText().strip():
            self.note.setText("Paste at least one P value.")
            self.note.setVisible(True)
            return
        super().accept()


# --- Help me choose -------------------------------------------------------------------------------------------

GUIDE_CSS = "<style>p { margin: 4px 0; } .muted { color: #9aa3ad; } h3 { margin: 8px 0 2px 0; }</style>"


class ChooseTestDialog(QDialog):
    """A few questions, a recommended test with its reason and a worked example, and the image-data preset.
    Two groups fill Compare two columns; three or more fill Compare three or more columns."""

    def __init__(self, parent, paired: bool = False, groups: int = 2):
        super().__init__(parent)
        self.setWindowTitle("Help me choose a test")
        self.resize(740, 800)
        layout = QVBoxLayout(self)
        self.groups = self._question(layout, "1. How many groups (columns) do you compare?", [
            ("Two", 2, "Two conditions, for example control and treated."),
            ("Three or more", 3,
             "Three or more conditions, for example a negative control, a positive control and two treatments. "
             "One ANOVA or Kruskal-Wallis test asks whether the groups differ; multiple comparisons then say which. "
             "Testing every pair with t tests instead inflates false positives."),
        ])
        self.paired = self._question(layout, "2. Was the same biological replicate measured in every condition?", [
            ("No: different replicates in each group (unpaired)", False, tips.PAIRED),
            ("Yes: each replicate gives a value in every group (paired)", True, tips.PAIRED),
        ])
        self.gaussian = self._question(layout, "3. Can you assume the values come from a Gaussian (normal) distribution?", [
            ("Yes", "yes", "Typical for percentages that are not near 0 or 100, and for means of many cells."),
            ("No", "no", "For example ranks or scores, or values bounded near 0 with a long tail."),
            ("Not sure", "unsure", "Look at the graph and think about the measurement. With N below about 10, a normality test cannot tell."),
        ])
        self.equal = self._question(layout, "4. Unpaired and Gaussian: can you assume every group has the same SD?", [
            ("Don't assume it (Welch's t test, or Welch and Brown-Forsythe ANOVA)", False, tips.WELCH),
            ("Assume equal SDs (Student's t test, or ordinary ANOVA)", True, tips.POOLED),
        ])
        self.tails = self._question(layout, "5. Two groups: one or two tails?", [
            ("Two-tailed (recommended)", "two-sided", tips.TAILS),
            ("One-tailed: I predicted B > A before collecting the data", "greater", tips.TAILS),
        ])
        self.goal = self._question(layout, "6. Three or more groups: which comparisons?", [
            ("Each group against one or two reference groups (for example a negative and a positive control)", "control",
             tips.COMPARISONS_GOAL["control"]),
            ("Every pair of groups", "all", tips.COMPARISONS_GOAL["all"]),
            ("None: only whether the groups differ at all", "none", tips.COMPARISONS_GOAL["none"]),
        ])
        _check(self.groups, groups)
        _check(self.paired, paired)
        _check(self.gaussian, "yes")
        _check(self.equal, False)
        _check(self.tails, "two-sided")
        _check(self.goal, "control")
        self.answer = QTextBrowser()
        self.answer.setOpenExternalLinks(False)
        layout.addWidget(self.answer, 1)
        buttons = QDialogButtonBox()
        self.use = buttons.addButton("Use this test", QDialogButtonBox.AcceptRole)
        buttons.addButton(QDialogButtonBox.Close)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        for group in (self.groups, self.paired, self.gaussian, self.equal, self.tails, self.goal):
            group.buttonToggled.connect(lambda *_args: self._update())
        self._update()

    def _question(self, layout, title: str, choices) -> QButtonGroup:
        box = QGroupBox(title)
        row = QVBoxLayout(box)
        row.setSpacing(2)
        group = QButtonGroup(self)
        for text, key, tip in choices:
            _radio(group, row, text, key, tip)
        layout.addWidget(box)
        group.setProperty("box", box)
        return group

    def choice(self) -> dict:
        """What the answers recommend. ``kind`` is "compare" (options of Compare two columns) or "anova"
        (options of Compare three or more columns)."""

        groups, paired, gaussian, equal = _checked(self.groups), _checked(self.paired), _checked(self.gaussian), _checked(self.equal)
        if groups != 2:
            goal = _checked(self.goal)
            if paired:
                test = "repeated_measures" if gaussian != "no" else "friedman"
            elif gaussian == "no":
                test = "kruskal_wallis"
            else:
                test = "ordinary" if equal else "welch"
            return {"kind": "anova", "test": test, "comparisons": None if goal == "none" else goal, "method": None}
        if paired:
            test = "paired_t" if gaussian != "no" else "wilcoxon"
        elif gaussian == "no":
            test = "mann_whitney"
        else:
            test = "unpaired_t" if equal else "welch_t"
        return {"kind": "compare", "test": test, "alternative": _checked(self.tails)}

    def _update(self) -> None:
        many = _checked(self.groups) != 2
        self.equal.property("box").setEnabled(not _checked(self.paired) and _checked(self.gaussian) != "no")
        self.tails.property("box").setEnabled(not many)
        self.goal.property("box").setEnabled(many)
        choice = self.choice()
        parts = [GUIDE_CSS]
        if many:
            parts.append(self._many_text(choice))
        else:
            parts.append(self._two_text(choice))
        parts.append(
            "<h3>Suggested analysis for image data</h3>"
            "<p>Measure: % of target, or % of segmented cells. Dot: one per <b>biological replicate (N)</b>, pooled from its images "
            "(technical replicates, n). Graph: a SuperPlot with mean ± SD of the replicates. Two groups: Welch's t test with the difference "
            "and 95% CI and an estimation plot, or Mann-Whitney (watch the small-N warning). Three or more groups: "
            "Kruskal-Wallis with Dunn's tests against the chosen reference group(s), for example a negative and a positive control; "
            "with Gaussian values and enough replicates, ANOVA with Dunnett's tests.</p>"
            "<p><b>Why the replicate?</b> A claim about a condition is a claim about independent samples. Images and cells from one sample share "
            "its biology and handling, so counting them as independent makes n large and P falsely small (pseudoreplication). "
            "Import CellQuant results → <b>Use suggested setup</b> sets this up.</p>"
            "<p class='muted'>Definitions and equations: Help → Statistics guide…</p>"
        )
        self.answer.setHtml("".join(parts))

    def _two_text(self, choice: dict) -> str:
        test = choice["test"]
        name = COMPARE_TESTS[test][0]
        reason = {
            "welch_t": "Two unpaired groups, Gaussian values, and no need to assume equal SDs. Welch's test stays correct when the SDs differ "
                       "and loses almost nothing when they are equal.",
            "unpaired_t": "Two unpaired groups, Gaussian values with the same SD in both.",
            "mann_whitney": "Two unpaired groups, and you cannot assume a Gaussian distribution: the test compares ranks.",
            "paired_t": "The same replicate was measured in both conditions, so the test looks at each replicate's difference.",
            "wilcoxon": "Paired data that you cannot assume are Gaussian: the test ranks each replicate's difference.",
        }[test]
        example = {
            "welch_t": "Example: % of target in 5 control and 6 treated replicates. Welch's test gives the difference of the means with "
                       "its 95% CI; report “difference 8.2 percentage points (95% CI 2.1 to 14.3), Welch's t test, P = 0.014, N = 5 and 6”.",
            "unpaired_t": "Example: % of segmented cells in 6 replicates per group, with similar SDs.",
            "mann_whitney": "Example: a score from 6 control and 7 treated replicates. At 3 vs 3 the smallest possible two-tailed P is 0.10, "
                            "so it needs N ≥ 4 per group to reach P < 0.05.",
            "paired_t": "Example: each of 5 replicates measured untreated and treated (or left and right half); the test asks whether the mean "
                        "within-replicate difference is 0.",
            "wilcoxon": "Example: 7 replicates, each scored before and after. With 5 pairs or fewer, it cannot reach P < 0.05.",
        }[test]
        text = f"<h3>Recommended: {name}</h3><p>{reason}</p><p class='muted'>{example}</p>"
        if _checked(self.gaussian) == "unsure":
            text += ("<p><b>Not sure about Gaussian?</b> Look at the dots: roughly symmetric, no long tail, no values piled at 0 or 100%? "
                     "Means of many cells per replicate are usually close to Gaussian. With small N a normality test has almost no "
                     "power, and with large N it flags tiny departures that do not matter, so do not rely on it alone.</p>")
        if choice["alternative"] != "two-sided":
            text += ("<p><b>One-tailed:</b> only if the direction was predicted before the experiment, and a change the other way "
                     "would be reported as no effect.</p>")
        return text

    def _many_text(self, choice: dict) -> str:
        test, goal = choice["test"], choice["comparisons"]
        name = ANOVA_NAMES[test][0]
        reason = {
            "ordinary": "Three or more unpaired groups, Gaussian values with similar SDs. One test asks whether any group mean differs, "
                        "instead of a t test for every pair (with three groups the chance of at least one false positive is about 14%, not 5%).",
            "welch": "Three or more unpaired groups, Gaussian values, and the SDs may differ between groups. Welch's and Brown-Forsythe ANOVA "
                     "do not assume equal SDs, and lose little when they are equal.",
            "repeated_measures": "The same replicate was measured in every condition, so the analysis compares conditions within each replicate. "
                                 "Keep the Geisser-Greenhouse correction on unless you know the differences between conditions vary equally.",
            "kruskal_wallis": "Three or more unpaired groups, and you cannot assume a Gaussian distribution: the test compares ranks.",
            "friedman": "Matched replicates, and you cannot assume a Gaussian distribution: each replicate's values are ranked across the conditions.",
        }[test]
        example = {
            "ordinary": "Example: % of target in a negative control, a positive control and two treatments, 6 replicates each.",
            "welch": "Example: % of segmented cells where the control varies little (near 0%) and a treated group varies a lot.",
            "repeated_measures": "Example: each of 5 replicates measured in 4 conditions; the replicates are the rows.",
            "kruskal_wallis": "Example: a score from 5 replicates in each of 4 conditions. With groups of 2, no Dunn comparison can reach P < 0.05.",
            "friedman": "Example: 6 replicates each scored in 3 conditions.",
        }[test]
        follow = {
            "control": {"rank": "Dunn's tests compare each group with the reference group(s). With two references, such as a negative and a positive control, "
                                "each reference is its own family by default and its stars get their own colour on a graph.",
                        "ordinary": "Dunnett's test compares each group with the reference group(s). With two references, such as a negative and a positive control, "
                                    "each reference is its own family by default and its stars get their own colour on a graph."},
            "all": {"rank": "Dunn's tests compare every pair; a compact letter display summarises them on a graph.",
                    "ordinary": "Tukey's test compares every pair (Games-Howell when SDs differ); a compact letter display summarises them on a graph."},
            "none": {"rank": "Only the overall test; it does not say which groups differ.", "ordinary": "Only the overall test; it does not say which groups differ."},
        }[goal or "none"]["rank" if test in ("kruskal_wallis", "friedman") else "ordinary"]
        text = f"<h3>Recommended: {name}</h3><p>{reason}</p><p class='muted'>{example}</p><p><b>Then:</b> {follow}</p>"
        if _checked(self.gaussian) == "unsure":
            text += ("<p><b>Not sure about Gaussian?</b> With few replicates per group a normality test cannot tell, so Kruskal-Wallis is the safe choice, "
                     "but it needs more replicates to reach a small P; with 3 per group Dunn's tests can rarely be significant.</p>")
        return text
