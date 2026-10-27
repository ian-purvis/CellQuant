"""Grouped tables and two-way ANOVA, checked against the classical formulas and an independent least-squares fit."""

from __future__ import annotations

import itertools

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from cellquant.errors import StatisticsError
from cellquant.stats.grouped import GroupedTable, fit_two_way, two_way_anova

# 2 rows × 3 data sets × 3 replicates (balanced)
BALANCED = {
    ("Control", "WT"): [4.0, 5.0, 6.0], ("Control", "KO1"): [6.0, 7.0, 8.0], ("Control", "KO2"): [5.0, 6.5, 8.0],
    ("Treated", "WT"): [7.0, 8.0, 9.5], ("Treated", "KO1"): [12.0, 13.0, 15.0], ("Treated", "KO2"): [8.0, 9.0, 11.0],
}


def _classical(cells: dict) -> dict:
    """Balanced two-way ANOVA by the textbook sums of squares."""

    rows = list(dict.fromkeys(key[0] for key in cells))
    columns = list(dict.fromkeys(key[1] for key in cells))
    y = np.array([[cells[(r, c)] for c in columns] for r in rows])  # rows × columns × n
    a, b, n = y.shape
    grand = y.mean()
    row_means, column_means, cell_means = y.mean(axis=(1, 2)), y.mean(axis=(0, 2)), y.mean(axis=2)
    ss_row = b * n * ((row_means - grand) ** 2).sum()
    ss_column = a * n * ((column_means - grand) ** 2).sum()
    ss_cells = n * ((cell_means - grand) ** 2).sum()
    ss_interaction = ss_cells - ss_row - ss_column
    ss_resid = ((y - cell_means[..., None]) ** 2).sum()
    df_resid = a * b * (n - 1)
    ms_resid = ss_resid / df_resid
    out = {"resid": (ss_resid, df_resid)}
    for name, ss, df in (("row", ss_row, a - 1), ("column", ss_column, b - 1), ("interaction", ss_interaction, (a - 1) * (b - 1))):
        f = ss / df / ms_resid
        out[name] = (ss, df, f, stats.f.sf(f, df, df_resid))
    return out


def test_balanced_two_way_matches_the_classical_formulas():
    table = GroupedTable.from_cells(BALANCED, title="Response")
    result = two_way_anova(table)
    expected = _classical(BALANCED)
    by_term = {row["term"]: row for row in result.values["anova"]}
    for name in ("row", "column", "interaction"):
        ss, df, f, p = expected[name]
        assert by_term[name]["ss"] == pytest.approx(ss) and by_term[name]["df"] == df
        assert by_term[name]["f"] == pytest.approx(f) and by_term[name]["p"] == pytest.approx(p)
    assert result.values["ss_resid"] == pytest.approx(expected["resid"][0]) and result.values["df_resid"] == expected["resid"][1]
    # % of total variation: the four parts add up to 100% when the table is balanced
    total = sum(row["percent"] for row in result.values["anova"]) + 100 * result.values["ss_resid"] / result.values["ss_total"]
    assert total == pytest.approx(100.0)
    assert "Type III" in result.methods and not result.warnings


def test_unbalanced_sums_of_squares_are_type_iii():
    rng = np.random.default_rng(3)
    cells = {(r, c): rng.normal(1.0 * (c == "B") + 0.8 * (r == "R3") * (c == "B"), 1.0, 3 + n)
             for n, (r, c) in enumerate(itertools.product(["R1", "R2", "R3"], ["A", "B"]))}
    table = GroupedTable.from_cells(cells)
    result = two_way_anova(table)
    smf = pytest.importorskip("statsmodels.formula.api")
    sm = pytest.importorskip("statsmodels.api")
    fit = smf.ols("value ~ C(row, Sum) * C(column, Sum)", table.to_long()).fit()
    reference = sm.stats.anova_lm(fit, typ=3)
    by_term = {row["term"]: row for row in result.values["anova"]}
    for term, label in (("row", "C(row, Sum)"), ("column", "C(column, Sum)"), ("interaction", "C(row, Sum):C(column, Sum)")):
        assert by_term[term]["ss"] == pytest.approx(reference.loc[label, "sum_sq"])
        assert by_term[term]["p"] == pytest.approx(reference.loc[label, "PR(>F)"])
    assert any("Type III" in text for text in result.warnings)


def test_additive_model_empty_cells_and_bad_tables():
    one_each = GroupedTable.from_cells({(r, c): [float(i + 2 * j + (i * j == 1))] for i, r in enumerate("ab") for j, c in enumerate("xyz")})
    result = two_way_anova(one_each)
    assert not result.values["interaction"] and "additive" in result.methods and "one value" in result.warnings[0]
    holed = GroupedTable.from_cells({("a", "x"): [1, 2], ("a", "y"): [3, 4], ("b", "x"): [2, 3], ("b", "y"): []})
    with pytest.raises(StatisticsError, match="empty cells"):
        two_way_anova(holed)
    additive = two_way_anova(holed, interaction=False)
    assert additive.values["df_resid"] == 6 - 3
    with pytest.raises(StatisticsError, match="at least 2 rows"):
        two_way_anova(GroupedTable.from_cells({("a", "x"): [1, 2], ("a", "y"): [3, 4]}))
    with pytest.raises(StatisticsError, match="unique"):
        GroupedTable("", ["a", "a"], ["x"], np.zeros((2, 1, 1)))
    with pytest.raises(StatisticsError, match="shaped"):
        GroupedTable("", ["a"], ["x"], np.zeros((2, 1, 1)))
    with pytest.raises(StatisticsError, match="goal"):
        two_way_anova(GroupedTable.from_cells(BALANCED), compare="within_rows", goal="selected")


def test_long_tables_and_cells():
    frame = pd.DataFrame({"t": ["c", "c", "d", "d", "c"], "g": ["x", "y", "x", "y", "x"], "v": [1.0, 2.0, 3.0, 4.0, 5.0]})
    table = GroupedTable.from_long(frame, "v", "t", "g")
    assert table.rows == ["c", "d"] and table.columns == ["x", "y"] and table.replicates == 2
    assert list(table.cell("c", "x")) == [1.0, 5.0] and table.cell(1, 1).tolist() == [4.0]
    assert table.to_long()["value"].tolist() == [1.0, 5.0, 2.0, 3.0, 4.0]


def test_comparisons_within_rows_and_of_main_effects():
    table = GroupedTable.from_cells(BALANCED)
    fit = fit_two_way(table)
    ms, df = fit["ms_resid"], fit["df_resid"]
    # Fisher's LSD within each row is a t test with the pooled SD: by hand
    result = two_way_anova(table, compare="within_rows", method="fisher_lsd")
    rows = result.values["comparisons"]
    assert len(rows) == 2 * 3 and rows[0]["label"] == "Control: WT vs. KO1"
    t = (5.0 - 7.0) / np.sqrt(ms * (1 / 3 + 1 / 3))
    assert rows[0]["difference"] == pytest.approx(-2.0) and rows[0]["p_adjusted"] == pytest.approx(2 * stats.t.sf(abs(t), df))
    # One family for the whole table: Šidák over all 6 comparisons
    combined = two_way_anova(table, compare="within_rows", family="combined", method="sidak").values["comparisons"]
    raw = np.array([row["p"] for row in combined])
    assert [row["p_adjusted"] for row in combined] == pytest.approx(1 - (1 - raw) ** 6)
    # Dunnett against a reference data set, within each data set comparing rows
    control = two_way_anova(table, compare="within_rows", goal="control", control="WT").values["comparisons"]
    assert {row["label"] for row in control} == {"Control: WT vs. KO1", "Control: WT vs. KO2", "Treated: WT vs. KO1", "Treated: WT vs. KO2"}
    rows_within = two_way_anova(table, compare="within_columns").values["comparisons"]
    assert [row["label"] for row in rows_within] == ["WT: Control vs. Treated", "KO1: Control vs. Treated", "KO2: Control vs. Treated"]
    # Main column effect: least-squares means = the average of the cell means, Tukey over 3 data sets
    main = two_way_anova(table, compare="column_means")
    first = main.values["comparisons"][0]
    wt, ko1 = np.mean([5.0, 8.1666667]), np.mean([7.0, 13.3333333])
    assert first["estimate_1"] == pytest.approx(wt) and first["estimate_2"] == pytest.approx(ko1)
    assert first["se"] == pytest.approx(np.sqrt(ms * 2 / 6))
    with pytest.raises(StatisticsError):
        two_way_anova(table, compare="within_rows", goal="control", control="nobody", family="combined")


def test_disconnected_designs_empty_cells_and_constant_data():
    # Without the interaction, cells that link no row with any shared data set cannot separate the two effects.
    split = GroupedTable.from_cells({("A", "x"): [1.0, 2.0, 3.0], ("B", "y"): [5.0, 6.0, 8.0], ("A", "y"): [], ("B", "x"): []})
    with pytest.raises(StatisticsError, match="do not link every row"):
        two_way_anova(split, interaction=False, compare="column_means")
    # Connected but with an empty cell: comparisons within a row skip the empty cell instead of predicting it.
    holed = GroupedTable.from_cells({("a", "x"): [1, 2], ("a", "y"): [3, 4], ("a", "z"): [5, 7],
                                     ("b", "x"): [2, 3], ("b", "y"): [4, 6], ("b", "z"): []})
    rows = two_way_anova(holed, interaction=False, compare="within_rows").values["comparisons"]
    assert {row["label"] for row in rows} == {"a: x vs. y", "a: x vs. z", "a: y vs. z", "b: x vs. y"}
    combined = two_way_anova(holed, interaction=False, compare="within_rows", family="combined").values["comparisons"]
    assert len(combined) == 4 and all("z" not in row["label"] or row["label"].startswith("a:") for row in combined)
    control = two_way_anova(holed, interaction=False, compare="within_rows", goal="control", control="x").values["comparisons"]
    assert {row["label"] for row in control} == {"a: x vs. y", "a: x vs. z", "b: x vs. y"}
    assert not any("[ref:" in row["label"] for row in control)
    # No scatter within cells: F and P are undefined, not huge.
    flat = GroupedTable.from_cells({(r, c): [1.0 + i + 2 * j] * 2 for i, r in enumerate("ab") for j, c in enumerate("xy")})
    result = two_way_anova(flat)
    assert all(np.isnan(row["f"]) and np.isnan(row["p"]) for row in result.values["anova"])
    # The residual's share of the variation is listed with the factors'.
    sections = {section.title: section for section in two_way_anova(GroupedTable.from_cells(BALANCED)).sections}
    assert "Residual" in [row.label for row in sections["Source of variation"].rows]
