"""Show working for contingency tables, ROC curves, Bland-Altman agreement, survival curves and a stack of P values:
every step that has an engine value agrees with it."""

from __future__ import annotations

import math

import numpy as np
import pytest

from cellquant.gui.quantanalysis import project as qa
from cellquant.gui.quantanalysis.project import DataSheet
from cellquant.stats import ColumnTable
from cellquant.stats.contingency import ContingencyTable
from cellquant.stats.working import working, to_frame
from cellquant.stats.xy import XYTable

NAN = float("nan")


def _steps(blocks):
    return [step for block in blocks for step in block.steps]


def _check(blocks, minimum: int = 3):
    steps = _steps(blocks)
    bad = [(s.label, s.value, s.engine) for s in steps if s.checked is False]
    assert not bad, bad
    assert sum(s.checked is True for s in steps) >= minimum
    assert not to_frame(blocks).empty
    return steps


def _step(blocks, label: str):
    return next(s for s in _steps(blocks) if s.label == label)


def _contingency(counts, **options):
    counts = np.asarray(counts, float)
    rows = [f"R{i + 1}" for i in range(counts.shape[0])]
    cols = [f"C{j + 1}" for j in range(counts.shape[1])]
    sheet = DataSheet("t", ColumnTable.from_columns({"x": [1.0]}))
    sheet.contingency = ContingencyTable("Counts", rows, cols, counts)
    result = qa.run_contingency(sheet, options)
    return working("contingency", sheet.contingency, options, result), result


@pytest.mark.parametrize("options", [{}, {"test": "chi2"}, {"test": "chi2", "yates": True}, {"test": "fisher", "ci": 0.9}, {"group": 1, "outcome": 1}])
def test_contingency_2x2(options):
    blocks, result = _contingency([[12, 5], [4, 14]], **options)
    _check(blocks, 12)
    assert _step(blocks, "P value (two-sided)").checked is True
    assert math.isclose(_step(blocks, "P value (two-sided)").value, result.values["p"], rel_tol=1e-6)


def test_contingency_fisher_table_and_zero_cell():
    blocks, result = _contingency([[7, 0], [2, 9]])
    _check(blocks, 12)
    fisher = next(b for b in blocks if b.title == "Fisher's exact test")
    assert math.isclose(fisher.table["prob"].sum(), 1.0)
    assert any("added to every cell" in s.label or "shift" in s.label.lower() for s in _steps(blocks))
    _check(_contingency([[0, 5], [3, 9]], test="chi2")[0], 5)


def test_contingency_diagnostic_and_larger_tables():
    blocks, _ = _contingency([[18, 4], [3, 25]], diagnostic=True)
    _check(blocks, 15)
    blocks, result = _contingency([[10, 20, 5], [8, 15, 12], [3, 9, 20]])
    _check(blocks, 8)
    assert _step(blocks, "Cramér's V").checked is True
    blocks, result = _contingency([[5, 15], [9, 11], [14, 6]], trend=True)
    _check(blocks, 6)
    assert "Chi-square test for trend" in [b.title for b in blocks]
    blocks, _ = _contingency([[5, 15, 10], [9, 11, 12]], trend=True)
    assert "Chi-square test for trend" in [b.title for b in blocks]


def _column_sheet(**columns):
    return DataSheet("t", ColumnTable.from_columns(columns, title="Marker"))


CONTROLS = [4.1, 5.2, 3.8, 6.0, 5.5, 4.7, 5.9, 6.4, 4.4, 5.0, NAN]
PATIENTS = [6.2, 7.5, 5.8, 8.1, 6.9, 7.2, 5.4, 8.8, 7.7, 6.5, 5.2]


@pytest.mark.parametrize("options", [{}, {"direction": "<"}, {"controls": "P", "patients": "C", "ci": 0.9}])
def test_roc(options):
    sheet = _column_sheet(C=CONTROLS, P=PATIENTS)
    result = qa.run_roc(sheet, options)
    blocks = working("roc", sheet.table, options, result)
    _check(blocks, 8)
    assert _step(blocks, "Area under the curve A").checked is True
    assert _step(blocks, "P value (two-tailed)").checked is True
    cutoffs = next(b for b in blocks if b.title.startswith("Cutoffs")).table
    curve = result.values["curve"]
    assert np.allclose(cutoffs["cutoff"], curve["cutoff"]) and np.allclose(cutoffs["sens %"], curve["sensitivity"])
    assert np.allclose(cutoffs["spec %"], curve["specificity"])


def test_roc_ties():
    sheet = _column_sheet(C=[1, 2, 2, 3, 3], P=[2, 3, 3, 4, 5, 5])
    result = qa.run_roc(sheet, {})
    blocks = working("roc", sheet.table, {}, result)
    _check(blocks, 6)
    assert _step(blocks, "Pairs tied").value > 0


@pytest.mark.parametrize("method", ["difference", "ratio", "percent"])
def test_bland_altman(method):
    a = [10.2, 11.5, 9.8, 12.1, 10.9, 13.0, 11.1, NAN, 12.4, 10.0]
    b = [10.0, 11.9, 9.1, 12.9, 10.2, 12.2, 11.5, 9.9, NAN, 10.6, 8.8]  # longer than a: the extra row has no pair
    sheet = _column_sheet(A=a, B=b)
    options = {"method": method, "ci": 0.9}
    result = qa.run_bland_altman(sheet, options)
    blocks = working("blandaltman", sheet.table, options, result)
    _check(blocks, 10)
    assert _step(blocks, "Number of pairs n").value == result.values["n"] == 8
    assert np.allclose(blocks[0].table.iloc[:, 4], result.values["measure"])


SUBJECTS = [(2, 0, 1), (3, 0, 1), (4, 0, 0), (5, 0, 1), (7, 0, 1), (9, 0, 0), (3, 0, 1),
            (4, 1, 1), (6, 1, 0), (8, 1, 1), (10, 1, 1), (12, 1, 0), (15, 1, 1), (4, 1, 1),
            (8, 2, 1), (11, 2, 0), (14, 2, 1), (17, 2, 0), (20, 2, 1), (24, 2, 0)]


def _survival(groups: int, blank_rows: int = 0):
    subjects = [s for s in SUBJECTS if s[1] < groups]
    x = np.array([s[0] for s in subjects] + [NAN] * blank_rows, float)
    values = np.full((x.size, groups, 1), NAN)
    for i, (_t, g, status) in enumerate(subjects):
        values[i, g, 0] = status
    sheet = DataSheet("t", ColumnTable.from_columns({"x": [1.0]}))
    sheet.xy = XYTable("Trial", "Time", x, ["Control", "Low", "High"][:groups], values)
    return sheet


@pytest.mark.parametrize("groups, options", [(1, {}), (2, {}), (2, {"ci": 0.9}), (3, {"trend": True})])
def test_survival(groups, options):
    sheet = _survival(groups, blank_rows=1)
    result = qa.run_survival(sheet, options)
    blocks = working("survival", sheet.xy, options, result)
    _check(blocks, 6 * groups)
    for name, curve in result.values["curves"].items():
        km = next(b for b in blocks if b.title == f"Kaplan-Meier table: {name}").table
        assert np.allclose(km["S"], curve["survival"]) and np.array_equal(km["at risk"], curve["at_risk"])
        assert np.allclose(km["SE"], curve["se"], equal_nan=True)
    if groups >= 2:
        assert _step(blocks, "P value").checked is True
        titles = [b.title for b in blocks]
        assert "Gehan-Breslow-Wilcoxon test" in titles
    if groups == 2:
        assert _step(blocks, "Hazard ratio, Mantel-Haenszel").checked is True
        assert _step(blocks, "Hazard ratio, log-rank (O/E)").checked is True
    if options.get("trend"):
        assert "Log-rank test for trend" in [b.title for b in blocks]


@pytest.mark.parametrize("method", ["bonferroni", "sidak", "holm", "holm_sidak", "bh", "by", "bky", "none"])
def test_pstack(method):
    options = {"text": "0.001\nsecond  0.02\n0.04\n0.04\nP5 = 0.30\n0,5\n", "method": method, "alpha": 0.05}
    result = qa.run_pstack(options)
    blocks = working("pstack", options, options, result)
    steps = _check(blocks, 7)
    assert _step(blocks, "Number of P values m").value == 6
    assert sum(s.checked is True for s in steps if s.label.startswith(("Adjusted P", "q of"))) == 6
    table = blocks[0].table
    assert list(table["rank"]) == [1, 2, 3, 4, 5, 6]
    assert np.allclose(sorted(table["adjusted"]), sorted(result.values["adjusted"]))


def test_pstack_bky_with_no_stage_one_discovery():
    options = {"text": "0.4\n0.6\n0.9", "method": "bky", "alpha": 0.05}
    blocks = working("pstack", options, options, qa.run_pstack(options))
    _check(blocks, 3)


def test_no_result_gives_working_without_engine():
    sheet = _column_sheet(C=CONTROLS, P=PATIENTS)
    blocks = working("roc", sheet.table, {}, None)
    assert blocks and all(s.checked is None for s in _steps(blocks))
