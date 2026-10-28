"""Replicate tables: every value with its biological replicate, analyzed as one value per replicate."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from cellquant.errors import StatisticsError
from cellquant.stats.replicates import ReplicateTable, looks_like_replicate

NAN = np.nan


def _table(**changes) -> ReplicateTable:
    options = {"title": "Area", "names": ["Control", "Treated"],
               "values": [[1, 2, 3, 10, 11, NAN, 20], [4, 5, NAN, 30, 31, 32, NAN]],
               "labels": ["Exp 1", "", "", "Exp 2", "", "", "Exp 3"]}
    return ReplicateTable(**{**options, **changes})


def test_blank_names_continue_the_replicate_above():
    table = _table()
    assert table.row_replicates() == ["Exp 1"] * 3 + ["Exp 2"] * 3 + ["Exp 3"]
    assert table.replicates() == ["Exp 1", "Exp 2", "Exp 3"]
    assert table.counts() == {"Control": 6, "Treated": 5} and table.unassigned() == 0


def test_analyses_use_one_value_per_replicate():
    table = _table()
    summary = table.summary_table()
    assert summary.names == ["Control", "Treated"] and summary.title == "Area"
    np.testing.assert_allclose(summary["Control"].values, [2, 10.5, 20])
    np.testing.assert_allclose(summary["Treated"].values, [4.5, 31, NAN])  # Exp 3 has no Treated value: blank
    frame = table.summaries()
    assert list(frame.columns) == ["group", "dot", "value", "n"] and frame["n"].tolist() == [3, 2, 1, 2, 3]
    median = _table(values=[[1, 2, 30, 10, 11, NAN, 20], [4, 5, NAN, 30, 31, 32, NAN]], summary="median").summary_table()
    np.testing.assert_allclose(median["Control"].values, [2, 10.5, 20])
    assert _table(paired=True).summary_table().paired


def test_rows_above_the_first_name_are_left_out():
    table = _table(labels=["", "", "Exp 1"])
    assert table.unassigned() == 4 and table.replicates() == ["Exp 1"]
    np.testing.assert_allclose(table.summary_table()["Control"].values, [(3 + 10 + 11 + 20) / 4])
    assert _table(labels=[]).summary_table()["Control"].values.size == 0


def test_from_long_makes_one_block_per_replicate():
    frame = pd.DataFrame({"condition": ["c", "c", "t", "t", "t", "c", "t", None],
                          "replicate": ["r1", "r1", "r1", "r2", "r2", "r2", None, "r2"],
                          "value": [1, 2, 3, 4, 5, 6, 7, 8]})
    table = ReplicateTable.from_long(frame, "value", "condition", "replicate")
    assert table.names == ["c", "t"] and table.labels == ["r1", "", "r2", ""]
    np.testing.assert_allclose(table.values[0], [1, 2, 6, NAN])
    np.testing.assert_allclose(table.values[1], [3, NAN, 4, 5])
    np.testing.assert_allclose(table.summary_table()["t"].values, [3, 4.5])
    with pytest.raises(StatisticsError):
        ReplicateTable.from_long(frame, "value", "condition", "missing")


def test_from_wide_and_checks():
    frame = pd.DataFrame({"Experiment": ["A", None, "B"], "Control": [1, 2, 3], "Treated": [4, None, 6]})
    table = ReplicateTable.from_wide(frame, "Experiment")
    assert table.names == ["Control", "Treated"] and table.labels == ["A", "", "B"]
    np.testing.assert_allclose(table.summary_table()["Control"].values, [1.5, 3])
    with pytest.raises(StatisticsError):
        ReplicateTable("", ["A", "A"], [[1], [2]], [])
    with pytest.raises(StatisticsError):
        ReplicateTable("", ["A"], [["x"]], [])
    assert ReplicateTable("", ["A"], [[1]], [], summary="mode").summary == "mean"
    assert looks_like_replicate("Biological replicate") and looks_like_replicate("Experiment") and not looks_like_replicate("Area")
