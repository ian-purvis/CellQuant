"""The analysis guide's logic: answers and table facts → the recommended analysis, its degrees of freedom and warnings."""

import numpy as np
import pytest
from scipy import stats

from cellquant.stats import ColumnTable
from cellquant.stats.advisor import Answers, Facts, facts_of, recommend, smallest_p, suggested, welch_anova_df, welch_df
from cellquant.stats.grouped import GroupedTable
from cellquant.stats.xy import XYTable

A = [12.0, 15.0, 11.0, 14.0, 13.0]
B = [18.0, 25.0, 16.0, 22.0, 30.0, 19.0]


def _table(paired=False, **columns):
    return ColumnTable.from_columns(columns or {"Control": A, "Treated": B}, paired=paired)


def test_welch_df_matches_the_t_test():
    from cellquant.stats.ttests import welch_t

    facts = facts_of(_table())
    assert welch_df(facts.sizes, facts.sds) == pytest.approx(welch_t(A, B).values["df"])
    rec = recommend(Answers(), facts)
    assert rec.kind == "compare" and rec.options == {"test": "welch_t", "alternative": "two-sided"}
    assert rec.df == pytest.approx(welch_t(A, B).values["df"]) and "Welch-Satterthwaite" in rec.dof[0][1]
    assert "N = 5 and 6" in rec.unit and "t(" in rec.report


def test_two_group_tests_and_their_df():
    facts = facts_of(_table())
    pooled = recommend(Answers(equal_sd=True), facts)
    assert pooled.options["test"] == "unpaired_t" and pooled.df == 9 and "5 + 6 − 2 = 9" in pooled.dof[0][1]
    assert recommend(Answers(gaussian="no"), facts).options["test"] == "mann_whitney"
    assert recommend(Answers(gaussian="log"), facts).options["test"] == "lognormal_t"
    paired = facts_of(_table(paired=True, Before=A, After=B[:5]))
    rec = recommend(Answers(paired=True), paired)
    assert rec.options["test"] == "paired_t" and rec.df == 4 and not any("not marked Paired" in w for w in rec.warnings)
    assert recommend(Answers(paired=True, gaussian="log"), paired).options["test"] == "ratio_paired_t"
    unmarked = recommend(Answers(paired=True), facts_of(_table(Before=A, After=B[:5])))
    assert any("not marked Paired" in w for w in unmarked.warnings)


def test_rank_tests_warn_when_p_cannot_reach_alpha():
    small = facts_of(_table(Control=[1, 2, 3], Treated=[4, 5, 6]))
    rec = recommend(Answers(gaussian="no"), small)
    assert rec.df is None and any("0.1" in w and "cannot reach P < 0.05" in w for w in rec.warnings)
    assert smallest_p("mann_whitney", 3, 3) == pytest.approx(0.1)
    assert smallest_p("wilcoxon", 5) == pytest.approx(0.0625)
    pairs = facts_of(_table(paired=True, Before=[1, 2, 3, 4, 5], After=[2, 3, 5, 4, 7]))
    assert any("6 pairs" in w for w in recommend(Answers(paired=True, gaussian="no"), pairs).warnings)


def test_three_or_more_groups():
    table = _table(First=[1, 2, 3, 4], Second=[2, 3, 4, 6, 7], Third=[5, 6, 9])
    facts = facts_of(table)
    ordinary = recommend(Answers(groups=3, equal_sd=True), facts)
    assert ordinary.kind == "anova" and ordinary.options["test"] == "ordinary" and ordinary.df == 12 - 3
    assert ("Between groups", "k − 1 = 3 − 1 = 2") in ordinary.dof and "Dunnett" in ordinary.then
    welch = recommend(Answers(groups=3), facts)
    from cellquant.stats.anova import welch_anova_core

    samples = [column.finite for column in table.columns]
    assert welch.options["test"] == "welch" and welch.df == pytest.approx(welch_anova_core(samples)["welch_dfd"])
    assert welch_anova_df(facts.sizes, facts.sds) == pytest.approx(welch.df)
    assert recommend(Answers(groups=3, gaussian="no"), facts).options["test"] == "kruskal_wallis"
    paired = facts_of(_table(paired=True, First=[1, 2, 3, 4], Second=[2, 3, 4, 6], Third=[5, 6, 9, 9]))
    rm = recommend(Answers(groups=3, paired=True), paired)
    assert rm.options["test"] == "repeated_measures" and rm.df == (3 - 1) * (4 - 1) and any("Geisser" in what for what, _ in rm.dof)
    assert recommend(Answers(groups=3, paired=True, gaussian="no"), paired).options["test"] == "friedman"
    none = recommend(Answers(groups=3, comparisons="none"), facts)
    assert none.options["comparisons"] is None


def test_pseudoreplication_is_flagged_and_replicate_tables_are_not():
    cells = facts_of(_table(), dot="object", replicates=[3, 3])
    assert suggested(cells).dot == "cell"
    rec = recommend(suggested(cells), cells)
    assert rec.warnings[0].startswith("Pseudoreplication") and "3 and 3 biological replicates" in rec.warnings[0]
    assert any("nested" in text for text in rec.alternatives)
    own = recommend(Answers(dot="cell", independent=True), cells)
    assert not any(w.startswith("Pseudoreplication") for w in own.warnings)
    means = facts_of(_table(), replicate_means=True)
    rec = recommend(suggested(means), means)
    assert "replicate's mean" in rec.unit and any("answer Paired" in w for w in rec.warnings)
    assert not any(w.startswith("Pseudoreplication") for w in rec.warnings)


def test_too_few_replicates():
    facts = facts_of(_table(Control=[1, 2], Treated=[3, 4, 5]))
    assert any("practical minimum" in w for w in recommend(Answers(), facts).warnings)


def test_two_factors_and_xy():
    grouped = GroupedTable("t", ["WT", "KO"], ["Vehicle", "Drug"], np.arange(2 * 2 * 3, dtype=float).reshape(2, 2, 3))
    facts = facts_of(grouped=grouped)
    answers = suggested(facts)
    assert answers.goal == "two_factors"
    rec = recommend(answers, facts)
    assert rec.kind == "twoway" and rec.options == {"interaction": True} and rec.df == 12 - 4
    assert recommend(Answers(goal="two_factors", interaction=False), facts).df == 12 - 2 - 2 + 1
    one = facts_of(grouped=GroupedTable("t", ["a", "b"], ["c", "d"], np.ones((2, 2, 1))))
    assert recommend(Answers(goal="two_factors"), one).options["interaction"] is False
    xy = XYTable("y", "x", np.array([1, 2, 3, 4, 5.0]), ["Y"], np.arange(10, dtype=float).reshape(5, 1, 2))
    facts = facts_of(xy=xy)
    assert facts.points == 5 and facts.points_each == 10 and suggested(facts).goal == "xy"
    line = recommend(Answers(goal="xy"), facts)
    assert line.kind == "linreg" and line.df == 8 and any("fit their mean" in w for w in line.warnings)
    assert recommend(Answers(goal="xy", xy_replicates="mean"), facts).df == 3
    corr = recommend(Answers(goal="xy", xy="correlation", gaussian="no"), facts)
    assert corr.kind == "xycorr" and corr.options["method"] == "spearman"


def test_without_a_table():
    rec = recommend(Answers(), Facts())
    assert rec.df is None and rec.dof and rec.report
    for goal in ("groups", "two_factors", "xy"):
        for gaussian in ("yes", "no", "log", "unsure"):
            for paired in (False, True):
                assert recommend(Answers(goal=goal, gaussian=gaussian, paired=paired, groups=3), Facts()).name


def test_normality_hint():
    rng = np.random.default_rng(1)
    skewed = rng.lognormal(0, 1.2, 40)
    facts = facts_of(_table(Control=skewed, Treated=skewed * 2))
    assert facts.normality["Control"] == pytest.approx(stats.shapiro(skewed).pvalue)
    assert any("Shapiro-Wilk P < 0.05" in w for w in recommend(Answers(), facts).warnings)


def test_replicates_behind_cells_count_for_the_minimum_and_summary():
    cells = facts_of(_table(), dot="object", replicates=[2, 4])
    assert any("practical minimum" in w for w in recommend(suggested(cells), cells).warnings)
    medians = facts_of(_table(), replicate_means=True, replicate_summary="median")
    assert "replicate's median" in recommend(suggested(medians), medians).unit
    mw = recommend(Answers(dot="cell", gaussian="no"), facts_of(_table(Control=[1, 2, 3], Treated=[4, 5, 6]), dot="object"))
    assert any("With n = 3 and 3" in w for w in mw.warnings)


def test_three_groups_on_a_two_column_table_and_empty_grouped_cells():
    rec = recommend(Answers(groups=3), facts_of(_table()))
    assert rec.df is None and any("2 column(s)" in w for w in rec.warnings)
    values = np.arange(12, dtype=float).reshape(2, 2, 3)
    values[1, 1, :] = np.nan
    facts = facts_of(grouped=GroupedTable("t", ["WT", "KO"], ["Vehicle", "Drug"], values))
    rec = recommend(Answers(goal="two_factors"), facts)
    assert rec.df == 9 - 3 and any("empty" in w for w in rec.warnings)
