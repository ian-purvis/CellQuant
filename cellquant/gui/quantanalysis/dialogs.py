"""QuantAnalysis dialogs: import CellQuant results, import a CSV, compare two columns, and the
"Help me choose" test guide."""

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
    QPushButton,
    QRadioButton,
    QTextBrowser,
    QVBoxLayout,
)

from cellquant.errors import CellQuantError
from cellquant.gui.quantanalysis import tips
from cellquant.gui.quantanalysis.datagrid import DANGER_STYLE, NOTE_STYLE, WARNING_STYLE
from cellquant.gui.quantanalysis.project import COMPARE_TESTS
from cellquant.stats import STYLES, ColumnTable
from cellquant.stats import sources

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
        _radio(self.shape, layout, "Wide: each column is a group", "wide", "Every numeric column becomes a column of the table, as in Prism.")
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


# --- Compare two columns --------------------------------------------------------------------------------------

TEST_TIPS = {
    "welch_t": tips.WELCH, "unpaired_t": tips.POOLED, "lognormal_t": tips.LOGNORMAL, "mann_whitney": tips.MANN_WHITNEY,
    "kolmogorov_smirnov": tips.KS, "paired_t": "Paired t test: is the mean of the within-replicate differences zero?",
    "ratio_paired_t": tips.RATIO, "wilcoxon": tips.WILCOXON,
}
DEFAULT_OPTIONS = {"test": "welch_t", "first": 0, "second": 1, "alternative": "two-sided", "ci": 0.95, "alpha": 0.05, "style": "GP"}


class CompareDialog(QDialog):
    """Prism's t test dialog: columns, design, distribution, test, tails and P value options."""

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
        if dialog.exec() and dialog.choice():
            self.set_options({**self.options(), **dialog.choice()})

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


# --- Help me choose -------------------------------------------------------------------------------------------

GUIDE_CSS = "<style>p { margin: 4px 0; } .muted { color: #9aa3ad; } h3 { margin: 8px 0 2px 0; }</style>"


class ChooseTestDialog(QDialog):
    """Prism's questions, a recommended test with its reason and a worked example, and the image-data preset."""

    def __init__(self, parent, paired: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Help me choose a test")
        self.resize(720, 720)
        layout = QVBoxLayout(self)
        self.groups = self._question(layout, "1. How many groups (columns) do you compare?", [
            ("Two", 2, "Two conditions, for example control and treated."),
            ("Three or more (ANOVA, Kruskal-Wallis: coming in the next update)", 3,
             "Comparing three or more groups needs one-way ANOVA or Kruskal-Wallis with multiple comparisons. "
             "Coming in the next update; testing every pair with t tests instead inflates false positives."),
        ])
        self.paired = self._question(layout, "2. Was the same biological replicate measured in both conditions?", [
            ("No: different replicates in each group (unpaired)", False, tips.PAIRED),
            ("Yes: each replicate gives a value in both groups (paired)", True, tips.PAIRED),
        ])
        self.gaussian = self._question(layout, "3. Can you assume the values come from a Gaussian (normal) distribution?", [
            ("Yes", "yes", "Typical for percentages that are not near 0 or 100, and for means of many cells."),
            ("No", "no", "For example ranks or scores, or values bounded near 0 with a long tail."),
            ("Not sure", "unsure", "Look at the graph and think about the measurement. With N below about 10, a normality test cannot tell."),
        ])
        self.equal = self._question(layout, "4. Unpaired and Gaussian: can you assume both groups have the same SD?", [
            ("Don't assume it (Welch's t test)", False, tips.WELCH),
            ("Assume equal SDs (Student's t test)", True, tips.POOLED),
        ])
        self.tails = self._question(layout, "5. One or two tails?", [
            ("Two-tailed (recommended)", "two-sided", tips.TAILS),
            ("One-tailed: I predicted B > A before collecting the data", "greater", tips.TAILS),
        ])
        _check(self.groups, 2)
        _check(self.paired, paired)
        _check(self.gaussian, "yes")
        _check(self.equal, False)
        _check(self.tails, "two-sided")
        self.answer = QTextBrowser()
        self.answer.setOpenExternalLinks(False)
        layout.addWidget(self.answer, 1)
        buttons = QDialogButtonBox()
        self.use = buttons.addButton("Use this test", QDialogButtonBox.AcceptRole)
        buttons.addButton(QDialogButtonBox.Close)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        for group in (self.groups, self.paired, self.gaussian, self.equal, self.tails):
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

    def choice(self) -> dict | None:
        groups, paired, gaussian, equal = _checked(self.groups), _checked(self.paired), _checked(self.gaussian), _checked(self.equal)
        if groups != 2:
            return None
        if paired:
            test = "paired_t" if gaussian != "no" else "wilcoxon"
        elif gaussian == "no":
            test = "mann_whitney"
        else:
            test = "unpaired_t" if equal else "welch_t"
        return {"test": test, "alternative": _checked(self.tails)}

    def _update(self) -> None:
        self.equal.property("box").setEnabled(not _checked(self.paired) and _checked(self.gaussian) != "no")
        choice = self.choice()
        self.use.setEnabled(choice is not None)
        parts = [GUIDE_CSS]
        if choice is None:
            parts.append("<h3>Three or more groups: coming in the next update</h3><p>One-way ANOVA (Gaussian) or Kruskal-Wallis with "
                         "Dunn's multiple comparisons against a reference group (not Gaussian). Until then, do not run a t test for every "
                         "pair: with three groups the chance of at least one false positive is about 14%, not 5%.</p>")
        else:
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
            parts.append(f"<h3>Recommended: {name}</h3><p>{reason}</p><p class='muted'>{example}</p>")
            if _checked(self.gaussian) == "unsure":
                parts.append("<p><b>Not sure about Gaussian?</b> Look at the dots: roughly symmetric, no long tail, no values piled at 0 or 100%? "
                             "Means of many cells per replicate are usually close to Gaussian. With small N a normality test has almost no "
                             "power, and with large N it flags tiny departures that do not matter, so do not rely on it alone.</p>")
            if choice["alternative"] != "two-sided":
                parts.append("<p><b>One-tailed:</b> only if the direction was predicted before the experiment, and a change the other way "
                             "would be reported as no effect.</p>")
        parts.append(
            "<h3>Suggested analysis for image data</h3>"
            "<p>Measure: % of target, or % of segmented cells. Dot: one per <b>biological replicate (N)</b>, pooled from its images "
            "(technical replicates, n). Graph: a SuperPlot with mean ± SD of the replicates. Two groups: Welch's t test with the difference "
            "and 95% CI and an estimation plot, or Mann-Whitney (watch the small-N warning). Three or more groups (next update): "
            "Kruskal-Wallis with Dunn's tests against chosen reference groups.</p>"
            "<p><b>Why the replicate?</b> A claim about a condition is a claim about independent samples. Images and cells from one sample share "
            "its biology and handling, so counting them as independent makes n large and P falsely small (pseudoreplication). "
            "Import CellQuant results → <b>Use suggested setup</b> sets this up.</p>"
            "<p class='muted'>Definitions and equations: Help → Statistics guide…</p>"
        )
        self.answer.setHtml("".join(parts))
