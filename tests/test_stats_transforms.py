"""Data processing of column tables, checked against values worked out by hand."""

from __future__ import annotations

import numpy as np
import pytest

from cellquant.errors import StatisticsError
from cellquant.stats import ColumnTable, column_math, fraction_of_total, normalize, process, remove_baseline, transform
from cellquant.stats.transforms import TRANSFORMS, label


def _table(paired: bool = False) -> ColumnTable:
    return ColumnTable.from_columns({"A": [1.0, 2.0, 4.0], "B": [2.0, 6.0, 8.0, np.nan], "C": [0.0, -1.0, 10.0]}, title="t", paired=paired)


def test_transform_each_function():
    table = _table()
    out, notes = transform(table, "log10", columns=["A", "C"])
    assert out["A"].values == pytest.approx(np.log10([1, 2, 4]))
    assert np.isnan(out["C"].values[:2]).all() and out["C"].values[2] == pytest.approx(1.0)  # log of 0 and -1: blank
    assert out["B"].values[:3] == pytest.approx([2, 6, 8])  # not ticked: copied
    assert any("2 value(s) had no result" in note for note in notes)
    assert table["C"].values[0] == 0.0  # the source is unchanged
    assert transform(table, "multiply", 2.5)[0]["A"].values == pytest.approx([2.5, 5, 10])
    assert transform(table, "power", 3)[0]["A"].values == pytest.approx([1, 8, 64])
    assert transform(table, "reciprocal")[0]["C"].values[1:] == pytest.approx([-1.0, 0.1])
    assert transform(table, "sqrt")[0]["A"].values == pytest.approx(np.sqrt([1, 2, 4]))
    assert transform(table, "exp10")[0]["A"].values == pytest.approx([10, 100, 10000])
    z = transform(table, "zscore")[0]["A"].values
    assert z == pytest.approx((np.array([1, 2, 4]) - 7 / 3) / np.std([1, 2, 4], ddof=1))
    fractions = ColumnTable.from_columns({"P": [0.25, 0.5, 1.0]})
    assert transform(fractions, "logit")[0]["P"].values[:2] == pytest.approx([np.log(1 / 3), 0.0])
    assert np.isnan(transform(fractions, "logit")[0]["P"].values[2])
    assert transform(fractions, "arcsine")[0]["P"].values == pytest.approx(np.arcsin(np.sqrt([0.25, 0.5, 1.0])))
    with pytest.raises(StatisticsError, match="needs a number for K"):
        transform(table, "multiply")
    with pytest.raises(StatisticsError, match="no column"):
        transform(table, "ln", columns=["Z"])
    for key in TRANSFORMS:  # every function runs on blanks, zeros and negatives
        transform(table, key, 2.0)


def test_normalize():
    table = _table()
    out, notes = normalize(table)  # 0% = smallest, 100% = largest
    assert out["A"].values == pytest.approx([0, 100 / 3, 100])
    assert out["C"].values == pytest.approx([100 / 11, 0, 100])
    assert "0% = smallest value in each column" in notes[0]
    out, _ = normalize(table, zero="first", hundred="last", result="fraction", columns=["B"])
    assert out["B"].values[:3] == pytest.approx([0, 4 / 6, 1]) and out["A"].values == pytest.approx([1, 2, 4])
    out, _ = normalize(table, zero="value", zero_value=0, hundred="sum")
    assert out["A"].values == pytest.approx(np.array([1, 2, 4]) / 7 * 100)
    flat = ColumnTable.from_columns({"F": [3.0, 3.0]})
    out, notes = normalize(flat)
    assert np.isnan(out["F"].values).all() and "cannot be normalized" in notes[-1]


def test_fraction_of_total():
    table = _table()
    out, _ = fraction_of_total(table, columns=["A", "B"])
    assert out["A"].values == pytest.approx(np.array([1, 2, 4]) / 7 * 100)
    out, _ = fraction_of_total(table, "grand", "fraction", columns=["A", "B"])
    assert out["B"].values[:3] == pytest.approx(np.array([2, 6, 8]) / 23)
    out, _ = fraction_of_total(table, "row", columns=["A", "B"])
    assert out["A"].values == pytest.approx([100 / 3, 25, 100 / 3]) and np.isnan(out["B"].values[3])


def test_remove_baseline():
    table = _table()
    out, notes = remove_baseline(table, "A", "percent", "mean")  # % of the control mean
    mean = 7 / 3
    assert out["A"].values == pytest.approx(np.array([1, 2, 4]) / mean * 100)
    assert out["B"].values[:3] == pytest.approx(np.array([2, 6, 8]) / mean * 100)
    assert "the mean of A" in notes[0]
    out, notes = remove_baseline(_table(paired=True), "A", "subtract", "row", keep_baseline=False)
    assert out.names == ["B", "C"] and out["B"].values[:3] == pytest.approx([1, 4, 4]) and np.isnan(out["B"].values[3])
    assert "A left out" in notes[0]
    out, notes = remove_baseline(table, "A", "percent_change", "row")
    assert out["C"].values == pytest.approx([-100, -150, 150]) and "paired table" in notes[-1]
    with pytest.raises(StatisticsError):
        remove_baseline(table, "Z")


def test_column_math_and_process():
    table = _table(paired=True)
    out, notes = column_math(table, "B", "A", "difference")
    assert out.names == ["B − A"] and out["B − A"].values[:3] == pytest.approx([1, 4, 4]) and np.isnan(out["B − A"].values[3])
    assert column_math(table, "B", "A", "log2_ratio")[0].columns[0].values[:3] == pytest.approx(np.log2([2, 3, 2]))
    assert column_math(table, "B", "A", "percent_change")[0].columns[0].values[:3] == pytest.approx([100, 200, 100])
    same, _ = process(table, {"kind": "baseline", "baseline": "A", "operation": "divide", "by": "row"})
    assert same["B"].values[:3] == pytest.approx([2, 3, 2])
    assert label({"kind": "baseline", "baseline": "ctrl", "operation": "percent"}) == "% of ctrl"
    assert label({"kind": "transform", "function": "multiply", "k": 100}) == "Y × 100"
    with pytest.raises(StatisticsError, match="Unknown data processing"):
        process(table, {"kind": "nothing"})
