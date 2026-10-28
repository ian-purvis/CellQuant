"""Show working for column tables: every step that the analysis also reports must agree with it."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("qtpy")

from cellquant.gui.quantanalysis.project import (  # noqa: E402
    DataSheet,
    run_anova,
    run_column_statistics,
    run_compare,
    run_nested,
    run_normality,
    run_outliers,
)
from cellquant.stats import ColumnTable  # noqa: E402
from cellquant.stats import compare as engine_compare  # noqa: E402
from cellquant.stats.replicates import ReplicateTable  # noqa: E402
from cellquant.stats.steps import to_frame, working  # noqa: E402

RNG = np.random.default_rng(11)
A = [4.1, 5.3, 6.2, 5.8, 4.9, 6.6, 5.1, 5.5]
B = [6.4, 7.9, 6.8, 8.3, 7.2, 9.1, 6.9]
C = [5.0, 5.9, 7.2, 6.1, 6.6, 7.7, 5.4]
D = [9.5, 8.1, 9.9, 10.4, 8.8, 9.2, 10.9]


def sheet(**columns) -> DataSheet:
    return DataSheet("data", ColumnTable.from_columns(columns, title="Data", paired=True))


def check(kind, data, options, result, minimum=5):
    """Run the working; no step may disagree, and at least ``minimum`` steps are checked."""

    blocks = working(kind, data, options, result)
    assert blocks
    checks = [step for block in blocks for step in block.steps if step.checked is not None]
    bad = [f"{block.title}: {step.label} = {step.value!r} vs {step.engine!r}" for block in blocks for step in block.steps if step.checked is False]
    assert not bad, "\n".join(bad)
    assert len(checks) >= minimum
    assert not to_frame(blocks).empty
    return blocks


def checked(blocks, label, title=None):
    """The steps with this label; each must be checked and agree."""

    found = [s for b in blocks if title is None or b.title.startswith(title) for s in b.steps if s.label == label]
    assert found, label
    assert all(s.checked is True for s in found), label
    return found[0]


def compare(data, **options):
    options = {"first": 0, "second": 1, **options}
    result = run_compare(data, options)
    return check("compare", data.table, options, result), result


@pytest.mark.parametrize("test", ["unpaired_t", "welch_t"])
@pytest.mark.parametrize("alternative", ["two-sided", "greater", "less"])
def test_unpaired(test, alternative):
    data = sheet(a=A, b=B + [np.nan])
    blocks, result = compare(data, test=test, alternative=alternative, ci=0.9)
    checked(blocks, "t")
    checked(blocks, "P value", "Unpaired t test" if test == "unpaired_t" else "Welch")
    checked(blocks, "Lower limit of the CI")
    checked(blocks, "Hedges' g")
    assert (test == "welch_t") == (not any(b.title.startswith("F test") for b in blocks))


def test_unpaired_unequal_n_and_advanced():
    data = sheet(a=A, b=B[:5])
    compare(data, test="unpaired_t", df=3, difference=0.5)
    compare(data, test="welch_t", df="conservative")
    compare(data, test="unpaired_t", difference=-1)


def test_lognormal_t():
    data = sheet(a=A, b=B)
    blocks, result = compare(data, test="lognormal_t", alternative="greater")
    checked(blocks, "Ratio of geometric means, B/A")
    checked(blocks, "Upper limit of the CI of the ratio")
    compare(data, test="lognormal_t", df=9)


@pytest.mark.parametrize("test", ["paired_t", "ratio_paired_t"])
def test_paired_with_missing_row(test):
    data = sheet(a=[4.1, 5.3, np.nan, 5.8, 4.9, 6.6, 5.1, 5.5], b=[6.4, 7.9, 6.8, np.nan, 7.2, 9.1, 6.9])
    blocks, result = compare(data, test=test, alternative="less")
    checked(blocks, "t")
    checked(blocks, "Number of pairs, n", "Data used")
    checked(blocks, "Correlation coefficient, r")
    checked(blocks, "P value (one tailed)")
    compare(data, test=test, df=3)


def test_paired_difference_and_effects():
    data = sheet(a=A[:7], b=B)
    blocks, _ = compare(data, test="paired_t", difference=0.4)
    checked(blocks, "Hedges' g_z")


@pytest.mark.parametrize("alternative", ["two-sided", "greater", "less"])
def test_mann_whitney_exact(alternative):
    data = sheet(a=A, b=B)
    blocks, result = compare(data, test="mann_whitney", alternative=alternative)
    assert result.values["exact"]
    checked(blocks, "U")
    checked(blocks, "P value", "P value")
    checked(blocks, "Hodges-Lehmann difference")
    checked(blocks, "Lower limit of the CI")


def test_mann_whitney_ties_and_blanks():
    data = sheet(a=[1, 2, 2, 3, 4, np.nan], b=[2, 3, 3, 5, 6, 7, 7])
    blocks, result = compare(data, test="mann_whitney")
    checked(blocks, "Sum of ranks of b, R_B")
    checked(blocks, "P value", "P value")


def test_mann_whitney_exact_without_ties_and_large_n():
    rng = np.random.default_rng(2)
    data = sheet(a=rng.normal(5, 1, 30), b=rng.normal(5.8, 1, 40))
    blocks, result = compare(data, test="mann_whitney")
    assert result.values["exact"]
    checked(blocks, "P value", "P value")


def test_mann_whitney_approximate():
    rng = np.random.default_rng(2)
    data = sheet(a=np.round(rng.normal(5, 1, 30), 1), b=np.round(rng.normal(5.8, 1, 40), 1))
    for options in ({"exact": False}, {"exact": False, "continuity": True, "alternative": "greater"}):
        result = engine_compare(data.table, "mann_whitney", 0, 1, **options)
        assert not result.values["exact"]
        blocks = check("compare", data.table, {"test": "mann_whitney"}, result)
        checked(blocks, "z")


@pytest.mark.parametrize("alternative", ["two-sided", "greater", "less"])
def test_kolmogorov_smirnov(alternative):
    data = sheet(a=A, b=B)
    blocks, result = compare(data, test="kolmogorov_smirnov", alternative=alternative)
    checked(blocks, "Kolmogorov-Smirnov D")
    checked(blocks, "P value")


def test_kolmogorov_smirnov_ties():
    data = sheet(a=[1, 2, 2, 3, 4, np.nan], b=[2, 3, 3, 5, 6, 7, 7])
    blocks, _ = compare(data, test="kolmogorov_smirnov")
    checked(blocks, "Kolmogorov-Smirnov D")


@pytest.mark.parametrize("zero_method", ["exclude", "pratt"])
def test_wilcoxon(zero_method):
    data = sheet(a=[4.1, 5.3, 6.2, 5.8, 4.9, 6.6, np.nan, 5.5, 5.0], b=[6.4, 7.9, 6.2, 8.3, 7.2, 9.1, 6.9, 5.5, 4.4])
    options = {"first": 0, "second": 1, "test": "wilcoxon"}
    result = engine_compare(data.table, "wilcoxon", 0, 1, zero_method=zero_method)  # the dialog does not pass zero_method
    assert result.values["n_zero"] == 2
    blocks = check("compare", data.table, options, result)
    checked(blocks, "Sum of signed ranks, W")
    checked(blocks, "P value", "P value")
    checked(blocks, "Hodges-Lehmann pseudo-median")
    checked(blocks, "Spearman correlation, rs")


def test_wilcoxon_approximate_and_one_tail():
    rng = np.random.default_rng(5)
    data = sheet(a=np.round(rng.normal(5, 1, 60), 1), b=np.round(rng.normal(5.4, 1, 60), 1))
    for alternative in ("two-sided", "greater"):
        compare(data, test="wilcoxon", alternative=alternative)
    result = engine_compare(data.table, "wilcoxon", 0, 1, continuity=True, zero_method="pratt", exact=False)
    assert not result.values["exact"]
    blocks = check("compare", data.table, {"test": "wilcoxon"}, result)
    checked(blocks, "z")


def anova(data, **options):
    options = {"test": "ordinary", **options}
    result = run_anova(data, options)
    return check("anova", data.table, options, result), result


def test_ordinary_anova_with_comparisons():
    data = sheet(a=A, b=B, c=C, d=D + [np.nan])
    for method, goal in (("tukey", "all"), ("bonferroni", "all"), ("sidak", "all"), ("fisher_lsd", "all"), ("dunnett", "control"), ("holm_sidak", "all")):
        blocks, result = anova(data, comparisons=goal, method=method, control="a")
        checked(blocks, "F")
        checked(blocks, "P value", "ANOVA table")
        checked(blocks, "K² (corrected)")
        assert any(b.title.startswith("Multiple comparisons") for b in blocks)
    blocks, _ = anova(data)
    checked(blocks, "F", "Brown-Forsythe test of equal SDs")


def test_ordinary_anova_overrides():
    data = sheet(a=A, b=B, c=C)
    anova(data, df=10)
    anova(data, comparisons="all", method="tukey", sd=1.1)
    anova(data, comparisons="all", method="bonferroni", m=6)


def test_welch_anova():
    data = sheet(a=A, b=B, c=C, d=D)
    blocks, _ = anova(data, test="welch")
    checked(blocks, "W")
    checked(blocks, "F*")
    for method in ("games_howell", "tamhane_t2", "dunnett_t3"):
        anova(data, test="welch", comparisons="all", method=method)
    anova(data, test="welch", df=8)


def test_repeated_measures():
    rng = np.random.default_rng(4)
    base = rng.normal(10, 2, 8)
    data = sheet(a=base, b=base + rng.normal(1, 0.5, 8), c=base + rng.normal(2, 1.0, 8), d=base + rng.normal(2.5, 1.5, 8))
    for gg in (True, False):
        blocks, result = anova(data, test="repeated_measures", geisser_greenhouse=gg)
        checked(blocks, "ε")
        checked(blocks, "F (treatments)")
        checked(blocks, "P value", "ANOVA table")
    for method in ("tukey", "bonferroni"):
        anova(data, test="repeated_measures", geisser_greenhouse=True, comparisons="all", method=method)
        anova(data, test="repeated_measures", geisser_greenhouse=False, comparisons="all", method=method)
    anova(data, test="repeated_measures", epsilon=0.6)
    anova(data, test="repeated_measures", df=15)


def test_repeated_measures_two_columns():
    anova_two = sheet(a=A, b=B[:7] + [6.1], c=C + [5.2])
    anova(anova_two, test="repeated_measures")


def test_repeated_measures_with_a_blank_uses_the_mixed_model():
    pytest.importorskip("statsmodels")
    data = sheet(a=A, b=B + [np.nan], c=C + [5.2], d=[9.5, 8.1, np.nan, 10.4, 8.8, 9.2, 10.9, 9.9])
    options = {"test": "repeated_measures", "comparisons": "all", "method": "tukey"}
    result = run_anova(data, options)
    assert result.analysis.startswith("Mixed-effects")
    blocks = working("anova", data.table, options, result)
    assert blocks
    assert all(step.checked is not False for block in blocks for step in block.steps)
    assert any("computed by" in step.formula for block in blocks for step in block.steps)


@pytest.mark.parametrize("comparisons", [None, "all"])
def test_kruskal_wallis(comparisons):
    data = sheet(a=A, b=B, c=C + [np.nan], d=D)
    options = {"test": "kruskal_wallis"}
    if comparisons:
        options.update(comparisons="all", method="dunn")
    result = run_anova(data, options)
    blocks = check("anova", data.table, options, result)
    checked(blocks, "H")
    checked(blocks, "P value (chi-square approximation)")
    if comparisons:
        assert any(b.title.startswith("Multiple comparisons") for b in blocks)


def test_kruskal_wallis_ties_exact_and_dunn_variants():
    data = sheet(a=[1, 2, 2, 3], b=[3, 4, 5, 5], c=[5, 6, 7, 8])
    options = {"test": "kruskal_wallis", "comparisons": "control", "control": "a", "method": "dunn_uncorrected"}
    result = run_anova(data, options)
    blocks = check("anova", data.table, options, result)
    checked(blocks, "H")


def test_friedman():
    data = sheet(a=[1.2, 2.3, 3.1, 2.2, np.nan, 1.8, 2.9], b=[2.2, 2.3, 4.1, 3.5, 3.0, 2.8, 3.9], c=[3.5, 3.9, 4.0, 4.4, 4.1, 3.2, 5.0])
    for comparisons in (None, "all"):
        options = {"test": "friedman"}
        if comparisons:
            options.update(comparisons="all", method="dunn")
        result = run_anova(data, options)
        blocks = check("anova", data.table, options, result)
        checked(blocks, "Q")
        checked(blocks, "P value (chi-square approximation)")


def test_friedman_chi_square_when_not_exact():
    rng = np.random.default_rng(1)
    data = sheet(**{f"c{j}": rng.normal(j * 0.3, 1, 30) for j in range(4)})
    options = {"test": "friedman", "exact": False}
    result = run_anova(data, options)
    check("anova", data.table, options, result)


def replicates(unbalanced=False) -> DataSheet:
    labels, columns = [], {"Control": [], "Treated": [], "Rescue": []}
    rng = np.random.default_rng(8)
    for unit, shift in zip("ABCDEF", (0, 0.3, 0.1, 1.0, 1.4, 1.1)):
        size = 4 if not unbalanced else 3 + (ord(unit) % 3)
        labels += [unit] + [""] * (size - 1)
        for index, name in enumerate(columns):
            columns[name] += list(np.round(rng.normal(10 + shift * (index + 1), 1, size), 2))
    table = ReplicateTable("Cells", list(columns), [np.asarray(v) for v in columns.values()], labels)
    sheet_ = DataSheet("rep", table.summary_table())
    sheet_.replicates = table
    return sheet_


def test_nested_anova_balanced_and_unbalanced():
    for unbalanced in (False, True):
        data = replicates(unbalanced)
        for options in ({}, {"comparisons": "all", "method": "tukey"}, {"comparisons": "control", "method": "dunnett", "control": "Control"}):
            result = run_nested(data, options)
            blocks = check("nested", data.replicates, options, result, minimum=8)
            checked(blocks, "F (groups)")
            checked(blocks, "Intraclass correlation, ICC")


def test_nested_two_groups_and_overrides():
    data = replicates(True)
    table = data.replicates
    keep = [0, 1]
    two = ReplicateTable("Cells", [table.names[i] for i in keep], [table.values[i] for i in keep], table.labels)
    two_sheet = DataSheet("rep", two.summary_table())
    two_sheet.replicates = two
    for options in ({}, {"alternative": "greater"}, {"df": 3}, {"df": "conservative"}):
        result = run_nested(two_sheet, options)
        blocks = check("nested", two, options, result, minimum=8)
        checked(blocks, "t")
        checked(blocks, "P value", "Nested t test")
    result = run_nested(data, {"df": 5, "comparisons": "all", "method": "bonferroni"})
    check("nested", data.replicates, {"df": 5, "comparisons": "all", "method": "bonferroni"}, result)


def outlier_sheet() -> DataSheet:
    return sheet(a=[5.1, 5.3, 4.9, 5.0, 5.2, 9.8, 5.1, 4.8], b=[7.1, 7.3, 6.9, 7.0, 7.2, 7.1, 7.0, np.nan], c=[1.0, 2.0, 3.0, 2.5, 2.2, 40.0, 38.0, 2.4])


@pytest.mark.parametrize("method", ["grubbs", "iterative_grubbs", "rout"])
def test_outliers(method):
    data = outlier_sheet()
    options = {"method": method, "alpha": 0.05, "q": 0.05}
    result = run_outliers(data, options)
    blocks = check("outliers", data.table, options, result, minimum=3)
    if method != "rout":
        checked(blocks, "critical G" if method == "grubbs" else "round 1: critical G")
    assert any(step.label == "Outliers found" or step.label == "Number of outliers" for block in blocks for step in block.steps)


def test_outliers_selected_columns():
    data = outlier_sheet()
    options = {"method": "grubbs", "columns": ["a"]}
    result = run_outliers(data, options)
    blocks = check("outliers", data.table, options, result, minimum=3)
    assert len(blocks) == 1


def test_column_statistics():
    data = sheet(a=A + [np.nan], b=B)
    options = {"ci": 0.9}
    frame, _, _ = run_column_statistics(data, options)
    blocks = check("colstats", data.table, options, frame)
    checked(blocks, "Mean, x̄")
    checked(blocks, "Standard error of the mean, SEM")
    checked(blocks, "Lower 90% limit of the mean")
    checked(blocks, "Median")


def test_normality():
    data = sheet(a=A + [np.nan], b=B)
    options = {"alpha": 0.05}
    frame, _, _ = run_normality(data, options)
    blocks = working("normality", data.table, options, frame)
    assert blocks
    assert any(step.label.endswith("P value") for block in blocks for step in block.steps)
    assert any(b.title.startswith("Lognormality") for b in blocks)


def test_anova_without_comparisons_says_so_and_brown_forsythe_df_are_both_shown():
    from cellquant.gui.quantanalysis import project as qa
    from cellquant.gui.quantanalysis.project import DataSheet
    from cellquant.stats import ColumnTable
    from cellquant.stats.steps import working

    sheet = DataSheet("t", ColumnTable.from_columns({"C": [4.1, 5.0, 5.9, 5.2], "A": [6.2, 7.1, 8.3, 7.0], "B": [5.0, 6.1, 5.5, 6.6]}))
    result = qa.run_anova(sheet, {"test": "ordinary"})
    blocks = working("anova", sheet.table, {"test": "ordinary"}, result)
    note = next(b for b in blocks if b.title == "Multiple comparisons")
    assert not note.steps and "None were asked for" in note.note
    bf = next(b for b in blocks if b.title.startswith("Brown-Forsythe"))
    df = {step.label: step for step in bf.steps if step.label.startswith("df")}
    assert df["df between"].value == 2 and df["df between"].checked and df["df within"].value == 9 and df["df within"].checked
