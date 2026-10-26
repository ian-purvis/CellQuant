"""Column tables, P value text and stars, and the result layout."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from cellquant.errors import StatisticsError
from cellquant.stats import AnalysisResult, ColumnTable, format_p, p_summary, p_text, unpaired_t


def test_a_table_keeps_column_order_blanks_and_unequal_lengths():
    table = ColumnTable.from_columns({"WT": [1, 2, None, 4], "KO": [5.0, np.nan], "Het": ["7", ""]}, title="Counts")
    assert table.names == ["WT", "KO", "Het"]
    assert np.array_equal(table["WT"].values, [1, 2, np.nan, 4], equal_nan=True)
    assert np.array_equal(table[1].finite, [5.0])
    assert np.array_equal(table["Het"].values, [7, np.nan], equal_nan=True)
    long = table.to_long()
    assert long.to_dict("list") == {"group": ["WT", "WT", "WT", "KO", "Het"], "row": [1, 2, 4, 1, 1], "value": [1.0, 2.0, 4.0, 5.0, 7.0]}
    with pytest.raises(StatisticsError, match="not a number"):
        ColumnTable.from_columns({"A": [1, "x"]})
    with pytest.raises(StatisticsError, match="infinite"):
        ColumnTable.from_columns({"A": [1, np.inf]})
    with pytest.raises(StatisticsError, match="unique"):
        ColumnTable(columns=[table["WT"], table["WT"]])


def test_a_long_table_becomes_one_column_per_group_and_lines_up_subjects_when_paired():
    df = pd.DataFrame({"g": ["b", "a", "b", "a", None, "c"], "v": [1, 2, 3, np.nan, 9, 5], "s": ["m1", "m1", "m2", "m2", "m3", "m1"]})
    table = ColumnTable.from_long(df, "v", "g")
    assert table.names == ["b", "a", "c"] and table.title == "v"
    assert np.array_equal(table["a"].values, [2, np.nan], equal_nan=True)
    assert ColumnTable.from_long(df, "v", "g", order=["a", "b"]).names == ["a", "b"]
    with pytest.raises(StatisticsError, match="No rows"):
        ColumnTable.from_long(df, "v", "g", order=["z"])
    # Rows in a different order are lined up by subject; a missing subject is a blank.
    paired = ColumnTable.from_long(df.iloc[[2, 0, 1, 3, 5]], "v", "g", order=["a", "b", "c"], subject="s")
    assert paired.paired
    assert np.array_equal(paired["a"].values, [np.nan, 2], equal_nan=True)  # m2 first (first seen), then m1
    assert np.array_equal(paired["b"].values, [3, 1])
    assert np.array_equal(paired["c"].values, [np.nan, 5], equal_nan=True)
    with pytest.raises(StatisticsError, match="more than once"):
        ColumnTable.from_long(pd.concat([df, df]), "v", "g", subject="s")


@pytest.mark.parametrize(
    "p, stars",
    [
        (0.5, "ns"), (0.0332, "*"), (0.05000001, "ns"), (0.05, "*"), (0.0100001, "*"), (0.01, "**"), (0.0010001, "**"), (0.001, "***"),
        (0.00010001, "***"), (0.0001, "****"), (0.0, "****"),
    ],
)
def test_gp_style_stars_use_less_or_equal_on_the_unrounded_p(p, stars):
    assert p_summary(p, "GP") == stars


@pytest.mark.parametrize(
    "style, p, stars",
    [
        ("APA", 0.05000001, "ns"), ("APA", 0.05, "*"), ("APA", 0.01, "**"), ("APA", 0.001, "***"), ("APA", 1e-9, "***"),
        ("NEJM", 0.0500001, "ns"), ("NEJM", 0.05, "*"), ("NEJM", 0.0099, "**"), ("NEJM", 0.0001, "***"),
    ],
)
def test_apa_and_nejm_stop_at_three_stars(style, p, stars):
    assert p_summary(p, style) == stars


def test_p_values_are_written_with_the_styles_digits_and_a_less_than_floor():
    assert format_p(0.03216) == "0.0322"
    assert format_p(0.00009) == "<0.0001"
    assert format_p(0.0001) == "0.0001"
    assert format_p(0.99996) == ">0.9999" and format_p(1.0) == ">0.9999"
    assert format_p(0.03216, digits=2) == "0.03"
    assert format_p(0.004, digits=2) == "<0.01"
    assert format_p(0.0234, style="APA") == ".023" and format_p(0.0002, style="APA") == "<.001"
    assert format_p(0.0234, style="NEJM") == "0.023" and format_p(0.0002, style="NEJM") == "<0.001"
    assert format_p(np.nan) == "" and p_summary(np.nan) == ""
    assert p_text(0.00001) == "P < 0.0001" and p_text(0.0123) == "P = 0.0123" and p_text(0.2, "APA") == "p = .200"
    with pytest.raises(ValueError, match="Unknown P value style"):
        p_summary(0.1, "older")  # a commercial package's older scheme is the GP scheme
    assert format_p(0.05000001) == "0.0500" and p_summary(0.05000001) == "ns"  # stars use the unrounded P


def test_a_result_lists_its_sections_rows_warnings_and_methods_for_export():
    result = unpaired_t([1, 2, 3, np.nan], [4, 5, 7], names=("Ctrl", "Drug"), table_title="Expt 1")
    assert isinstance(result, AnalysisResult)
    assert [section.title for section in result.sections] == [
        "Table analyzed", "Unpaired t test", "How big is the difference?", "Effect size", "F test to compare variances", "Data analyzed"]
    frame = result.to_frame()
    assert list(frame.columns) == ["section", "label", "value", "formatted"]
    assert frame.iloc[-1]["section"] == "Methods" and "Drug with Ctrl" in frame.iloc[-1]["formatted"]
    assert (frame["section"] == "Warnings").sum() == len(result.warnings) == 1  # the blank cell
    assert result.row("How big is the difference?", "Difference between means (Drug - Ctrl) ± SEM").value[0] == pytest.approx(10 / 3)
    for key in ("difference", "ci_low", "ci_high", "p", "t", "df"):
        assert key in result.values
    frame.to_csv(index=False)  # exportable as is
