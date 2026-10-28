"""The analysis guide (Analyze → Help me choose…): questions about the experiment, the analysis they recommend with
its degrees of freedom worked out from the selected table, and a button that opens that analysis filled in.

The logic is cellquant.stats.advisor; this module asks the questions and shows the answer."""

from __future__ import annotations

import html

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QScrollArea,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from cellquant.gui.quantanalysis import tips
from cellquant.gui.quantanalysis.dialogs import AdvancedBox, _check, _checked, _radio
from cellquant.stats.advisor import COMPARISONS, DOTS, GAUSSIAN, GOALS, REPORTING, Answers, Facts, Recommendation, facts_of, recommend, suggested

GUIDE_CSS = ("<style>p { margin: 4px 0; } .muted { color: #9aa3ad; } h3 { margin: 8px 0 2px 0; } .warn { color: #f0b429; } "
             "td { padding: 1px 8px 1px 0; vertical-align: top; }</style>")
COMPARISON_TEXT = {
    "control": "Each group vs one or two reference groups (controls)",
    "all": "Every pair of groups",
    "selected": "Only some pairs, chosen beforehand",
    "trend": "A trend across ordered groups (e.g. doses)",
    "none": "None: only whether the groups differ at all",
}


def facts_for(sheet) -> Facts:
    """What a data sheet tells the guide: its table, how it was imported (what a dot is, the replicates behind each
    group's images or cells) and whether it is a table of replicates (its replicate means are analyzed)."""

    if sheet is None:
        return Facts()
    spec = getattr(sheet, "spec", None)
    dot = getattr(spec, "dot", None)
    replicates = []
    images = getattr(sheet, "images", None)
    if dot in ("image", "object") and images is not None and {"group", "unit"} <= set(images.columns):
        counts = images.dropna(subset=["unit"]).groupby("group", sort=False)["unit"].nunique()
        replicates = [int(counts.get(name, 0)) for name in sheet.table.names] if sheet.table is not None else list(counts)
    replicate_means = getattr(sheet, "replicates", None) is not None
    return facts_of(sheet.table if sheet.xy is None and sheet.grouped is None else None, sheet.grouped, sheet.xy, dot=dot,
                    replicates=replicates if any(replicates) else [], replicate_means=replicate_means)


class AnalysisGuideDialog(QDialog):
    """Questions on the left (only those that apply), the recommendation on the right: the test and why, what N is, its
    degrees of freedom from the selected table (editable under Advanced), warnings, alternatives and how to report it."""

    def __init__(self, parent, facts: Facts | None = None, answers: Answers | None = None):
        super().__init__(parent)
        self.setWindowTitle("Help me choose an analysis")
        self.resize(1080, 760)
        self.facts = facts or Facts()
        start = suggested(self.facts, answers)
        outer = QVBoxLayout(self)
        columns = QHBoxLayout()
        questions = QWidget()
        self._layout = QVBoxLayout(questions)
        self.goal = self._question("What do you want to know?", [(text, key, tips.GUIDE_GOAL) for key, text in GOALS.items()])
        self.dot = self._question("What is one value (one dot) in your table?", [(text, key, tips.GUIDE_DOT) for key, text in DOTS.items()])
        self.independent = QCheckBox("Each was treated or assigned on its own")
        self.independent.setToolTip(tips.GUIDE_INDEPENDENT)
        self.dot.property("box").layout().addWidget(self.independent)
        self.groups = self._question("How many groups (conditions) do you compare?", [
            ("Two", 2, "Two conditions, for example control and treated."),
            ("Three or more", 3, "One ANOVA or Kruskal-Wallis test, then multiple comparisons; a t test for every pair inflates false positives."),
        ])
        self.paired = self._question("Did each replicate give a value in every group?", [
            ("No: different replicates in each group (unpaired)", False, tips.GUIDE_PAIRED),
            ("Yes: same replicate, or same-day experiment (paired)", True, tips.GUIDE_PAIRED),
        ])
        self.xy = self._question("What do you want from X and Y?", [
            ("A line: predict Y from X, compare slopes", "line", tips.LINREG),
            ("Only whether they go together (correlation)", "correlation", tips.XYCORR),
        ])
        self.xy_replicates = self._question("Are the Y values at each X independent samples?", [
            ("Yes: each is its own replicate", "each", tips.XY_REPLICATES),
            ("No: images or cells of one sample (use their mean)", "mean", tips.XY_REPLICATES),
        ])
        self.gaussian = self._question("Can you assume a Gaussian (normal) distribution?",
                                       [(text, key, tips.GUIDE_GAUSSIAN) for key, text in GAUSSIAN.items()])
        self.equal = self._question("Can you assume every group has the same SD?", [
            ("Don't assume it (recommended: Welch)", False, tips.WELCH),
            ("Assume equal SDs", True, tips.POOLED),
        ])
        self.comparisons = self._question("Which comparisons?", [(COMPARISON_TEXT[key], key, tips.COMPARISONS_GOAL[key]) for key in COMPARISONS])
        self.tails = self._question("Was the direction predicted before the experiment?", [
            ("No, or either way matters: two-tailed (recommended)", "two-sided", tips.TAILS),
            ("Yes, the other way counts as no effect: one-tailed", "greater", tips.TAILS),
        ])
        self.interaction = self._question("Could the effect of one factor depend on the other?", [
            ("Yes, or not sure: fit the interaction", True, tips.TWO_WAY_INTERACTION),
            ("No: the effects simply add up", False, tips.TWO_WAY_INTERACTION),
        ])
        self._layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(questions.sizeHint().width() + 24)
        scroll.setWidget(questions)
        columns.addWidget(scroll, 0)
        right = QVBoxLayout()
        self.answer = QTextBrowser()
        self.answer.setOpenExternalLinks(False)
        right.addWidget(self.answer, 1)
        self.advanced = AdvancedBox(self, right)
        self.advanced.add("df", "Degrees of freedom", tips.OVERRIDE_DF, 0.01, 1e6, 2, modes=(("conservative", "Conservative (smaller N − 1)"),),
                          value=10)
        columns.addLayout(right, 1)
        outer.addLayout(columns, 1)
        buttons = QDialogButtonBox()
        self.use = buttons.addButton("Run this analysis…", QDialogButtonBox.AcceptRole)
        self.use.setToolTip(tips.GUIDE_RUN)
        buttons.addButton(QDialogButtonBox.Close)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.set_answers(start)
        for group in self._groups():
            group.buttonToggled.connect(lambda *_args: self._update())
        self.independent.toggled.connect(lambda _checked: self._update())
        for mode, spin, *_rest in self.advanced.fields.values():
            mode.currentIndexChanged.connect(lambda _index: self._update())
            spin.valueChanged.connect(lambda _value: self._update())
        self.advanced.toggle.toggled.connect(lambda _checked: self._update())
        self._update()

    def _question(self, title: str, choices) -> QButtonGroup:
        box = QGroupBox(title)
        row = QVBoxLayout(box)
        row.setSpacing(2)
        group = QButtonGroup(self)
        for text, key, tip in choices:
            _radio(group, row, text, key, tip)
        self._layout.addWidget(box)
        group.setProperty("box", box)
        return group

    def _groups(self) -> list[QButtonGroup]:
        return [self.goal, self.dot, self.groups, self.paired, self.xy, self.xy_replicates, self.gaussian, self.equal, self.comparisons,
                self.tails, self.interaction]

    def set_answers(self, answers: Answers) -> None:
        for group, key in ((self.goal, answers.goal), (self.dot, answers.dot), (self.groups, 2 if answers.groups == 2 else 3),
                           (self.paired, answers.paired), (self.xy, answers.xy), (self.xy_replicates, answers.xy_replicates),
                           (self.gaussian, answers.gaussian), (self.equal, answers.equal_sd), (self.comparisons, answers.comparisons),
                           (self.tails, answers.tails), (self.interaction, answers.interaction)):
            _check(group, key)
        self.independent.setChecked(answers.independent)

    def answers(self) -> Answers:
        return Answers(goal=_checked(self.goal), dot=_checked(self.dot), independent=self.independent.isChecked() and _checked(self.dot) != "replicate",
                       groups=_checked(self.groups), paired=bool(_checked(self.paired)), gaussian=_checked(self.gaussian),
                       equal_sd=bool(_checked(self.equal)), comparisons=_checked(self.comparisons), tails=_checked(self.tails),
                       interaction=bool(_checked(self.interaction)), xy=_checked(self.xy), xy_replicates=_checked(self.xy_replicates))

    def recommendation(self) -> Recommendation:
        return recommend(self.answers(), self.facts)

    def options(self) -> dict:
        """The recommended analysis's options, with any degrees of freedom set by hand under Advanced."""

        rec = self.recommendation()
        return {**rec.options, **self.advanced.values()}

    def _show(self, group: QButtonGroup, visible: bool) -> None:
        group.property("box").setVisible(visible)

    def _update(self) -> None:
        a = self.answers()
        groups, xy, two = a.goal == "groups", a.goal == "xy", a.goal == "two_factors"
        self.independent.setVisible(a.dot != "replicate")
        self._show(self.groups, groups)
        self._show(self.paired, groups or two)
        self._show(self.xy, xy)
        self._show(self.xy_replicates, xy and self.facts.y_per_x > 1)
        self._show(self.gaussian, not xy or a.xy == "correlation")
        self._show(self.equal, groups and not a.paired and a.gaussian in ("yes", "unsure"))
        self._show(self.comparisons, groups and a.groups != 2)
        self._show(self.tails, groups and a.groups == 2)
        self._show(self.interaction, two)
        self.gaussian.buttons()[1].setVisible(not xy)
        rec = self.recommendation()
        conservative = rec.kind == "compare" and rec.options["test"] in ("welch_t", "unpaired_t", "lognormal_t")
        df_mode = self.advanced.fields["df"][0]
        df_mode.model().item(df_mode.findData("conservative")).setEnabled(conservative)
        if not conservative and df_mode.currentData() == "conservative":
            df_mode.setCurrentIndex(0)
        has_df = rec.kind in ("compare", "anova", "twoway", "linreg", "xycorr") and rec.options.get("test") not in (
            "mann_whitney", "wilcoxon", "kruskal_wallis", "friedman")
        self.advanced.show_field("df", has_df)
        self.advanced.set_default_text("df", f"Default ({rec.df:.4g})" if rec.df is not None else "Default (computed)")
        mode, spin, *_rest = self.advanced.fields["df"]
        if mode.currentData() is None and rec.df is not None:  # "Set by hand" starts from the computed df
            spin.blockSignals(True)
            spin.setValue(round(rec.df, 2))
            spin.blockSignals(False)
        self.answer.setHtml(self._html(rec))

    def _html(self, rec: Recommendation) -> str:
        e = html.escape
        parts = [GUIDE_CSS, f"<h3>Recommended: {e(rec.name)}</h3><p>{e(rec.why)}</p>"]
        if rec.then:
            parts.append(f"<p><b>Then:</b> {e(rec.then)}</p>")
        if rec.unit:
            parts.append(f"<h3>What N is</h3><p>{e(rec.unit)}</p>")
        for text in rec.warnings:
            parts.append(f"<p class='warn'>⚠ {e(text)}</p>")
        if rec.dof:
            rows = "".join(f"<tr><td>{e(what)}</td><td>{e(value)}</td></tr>" for what, value in rec.dof)
            parts.append(f"<h3>Degrees of freedom</h3><table>{rows}</table>")
            manual = self.advanced.values().get("df")
            if manual is not None:
                parts.append(f"<p class='warn'>Set by hand under Advanced: {e(str(manual))}. The results sheet and methods text will "
                             "say so; report it with the reason.</p>")
            else:
                parts.append("<p class='muted'>To use other degrees of freedom (for example counted from the biological replicates of a "
                             "nested design), tick Advanced.</p>")
        if rec.alternatives:
            parts.append("<h3>Alternatives</h3><ul>" + "".join(f"<li>{e(text)}</li>" for text in rec.alternatives) + "</ul>")
        parts.append(f"<h3>How to report it</h3><p>{e(rec.report)}</p><p class='muted'>{e(REPORTING)}</p>")
        parts.append(self._data_html())
        parts.append("<p class='muted'>Definitions and equations: Help → Statistics guide…</p>")
        return "".join(parts)

    def _data_html(self) -> str:
        f, e = self.facts, html.escape
        if not f.shape:
            return "<p class='muted'>Select a data table first and the guide works out N and the degrees of freedom from it.</p>"
        if f.shape == "xy":
            return f"<h3>Your table</h3><p>XY table: {len(f.names)} data set(s), {f.points} X values with data, {f.y_per_x} Y column(s) per X.</p>"
        if f.shape == "grouped":
            return (f"<h3>Your table</h3><p>Grouped table: {f.rows} rows × {f.data_sets} data sets, {min(f.sizes, default=0)} to "
                    f"{max(f.sizes, default=0)} values per cell.</p>")
        rows = []
        for i, name in enumerate(f.names):
            sd = f.sds[i] if i < len(f.sds) else float("nan")
            p = f.normality.get(name)
            reps = f" from {f.replicates[i]} replicates" if i < len(f.replicates) and f.replicates[i] else ""
            rows.append(f"<tr><td>{e(name)}</td><td>{f.sizes[i]} values{reps}</td><td>SD {sd:.4g}</td>"
                        f"<td>{'Shapiro-Wilk P ' + format(p, '.3g') if p is not None else ''}</td></tr>")
        paired = f" Marked Paired: {f.pairs} complete rows." if f.paired_table else ""
        return (f"<h3>Your table</h3><table>{''.join(rows)}</table><p class='muted'>{paired} With N below about 8 a normality test "
                "cannot tell; with large N it flags departures that do not matter.</p>")
