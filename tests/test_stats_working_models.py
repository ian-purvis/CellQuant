"""Show working for models: two-way ANOVA, linear regression, XY correlation, curve fitting, correlation matrix and PCA.
Every step with an analysis value must agree with it."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PyQt6")

from cellquant.gui.quantanalysis import project as qp  # noqa: E402
from cellquant.stats import Column, ColumnTable  # noqa: E402
from cellquant.stats.grouped import GroupedTable  # noqa: E402
from cellquant.stats.steps import to_frame, working  # noqa: E402
from cellquant.stats.xy import XYTable  # noqa: E402

NAN = np.nan


def _steps(blocks):
    return [step for block in blocks for step in block.steps]


def _assert_agree(blocks, minimum=3):
    steps = _steps(blocks)
    checked = [s for s in steps if s.checked is not None]
    assert len(checked) >= minimum
    bad = [(s.label, s.value, s.engine) for s in checked if not s.checked]
    assert not bad, bad
    assert not to_frame(blocks).empty
    return {s.label: s for s in steps}


def _grouped(unbalanced=False):
    cells = {("Ctl", "WT"): [4.1, 3.9, 4.4, 4.0], ("Ctl", "KO"): [5.2, 5.6, 5.1, 5.9],
             ("Drug", "WT"): [6.3, 6.8, 6.1, 6.6], ("Drug", "KO"): [5.0, 4.6, 4.9, 5.3],
             ("High", "WT"): [8.1, 7.7, 8.4, 7.9], ("High", "KO"): [5.5, 5.8, 6.1, 5.2]}
    if unbalanced:
        cells[("Ctl", "WT")] = [4.1, 3.9, 4.4, NAN]
        cells[("High", "KO")] = [5.5, 5.8, NAN, NAN]
        cells[("Drug", "WT")] = [6.3, 6.8, 6.1, 6.6, 6.0]
    return GroupedTable.from_cells(cells, title="g")


def _sheet(**kwargs):
    sheet = qp.DataSheet("d", ColumnTable(title="d"))
    if "grouped" in kwargs:
        qp.set_grouped(sheet, kwargs["grouped"])
    if "xy" in kwargs:
        qp.set_xy(sheet, kwargs["xy"])
    if "table" in kwargs:
        sheet.table = kwargs["table"]
    return sheet


@pytest.mark.parametrize("unbalanced", [False, True])
@pytest.mark.parametrize("options", [{}, {"interaction": False}, {"df": 12}])
def test_twoway(unbalanced, options):
    sheet = _sheet(grouped=_grouped(unbalanced))
    result = qp.run_two_way(sheet, options)
    blocks = working("twoway", sheet.grouped, options, result)
    got = _assert_agree(blocks, 8)
    for term in ("Row factor", "Column factor"):
        assert got[f"{term}: P"].checked
    if "interaction" not in options or options["interaction"]:
        assert got["Interaction: F"].checked and got["Interaction: P"].checked
    assert any(b.title.startswith("ANOVA table") and b.table is not None for b in blocks)
    if not unbalanced:
        assert any(b.title.startswith("Equal numbers") for b in blocks)


def test_twoway_one_value_per_cell_is_additive():
    cells = {(r, c): [v] for (r, c), v in zip([(r, c) for r in "ab c".replace(" ", "") for c in "xyz"], [1, 2, 4, 2, 5, 5, 3, 3, 8])}
    sheet = _sheet(grouped=GroupedTable.from_cells(cells))
    result = qp.run_two_way(sheet, {})
    assert result.values["interaction"] is False
    _assert_agree(working("twoway", sheet.grouped, {}, result), 6)


X = np.array([0.0, 1.0, 2.0, 4.0, 8.0, np.nan])
VALUES = np.array([
    [[1.1, 0.9, 1.3], [2.0, 2.4, 1.8], [0.5, 0.7, 0.4]],
    [[2.0, 2.3, 1.8], [2.9, 3.1, 3.3], [1.2, 1.1, NAN]],
    [[3.2, 2.9, 3.0], [4.1, NAN, 3.8], [1.9, 2.2, 2.0]],
    [[5.1, 4.8, 5.3], [5.2, 5.6, 5.0], [3.1, 2.8, 3.0]],
    [[9.2, 8.8, 9.1], [8.1, 7.7, 8.4], [5.0, 5.2, 4.8]],
    [[3.0, 3.1, 3.2], [3.0, 3.3, 3.1], [2.0, 2.1, 1.9]],
])


def _xy():
    return XYTable("Response", "Dose", X, ["A", "B", "C"], VALUES)


@pytest.mark.parametrize("options", [{}, {"replicates": "mean"}, {"through_origin": True}, {"ci": 0.9}, {"df": 5}])
def test_linreg(options):
    sheet = _sheet(xy=_xy())
    result = qp.run_linear_regression(sheet, options)
    blocks = working("linreg", sheet.xy, options, result)
    got = _assert_agree(blocks, 30)
    assert got["F, are the slopes equal?"].checked and got["P for the slopes"].checked
    if not options.get("through_origin"):
        assert got["F, are the intercepts equal?"].checked
    assert sum(b.title.startswith("Data set") for b in blocks) == 3
    assert all(b.table is not None for b in blocks if b.title.startswith("Data set"))


def test_linreg_single_set_and_skipped_set():
    xy = XYTable("T", "x", [1, 2, 3, 4, 5], ["a", "b"], np.array([[2, NAN], [4, NAN], [5, NAN], [4, 7], [5, NAN]], float).reshape(5, 2, 1))
    sheet = _sheet(xy=xy)
    result = qp.run_linear_regression(sheet, {})
    blocks = working("linreg", xy, {}, result)
    assert len(blocks) == 1
    got = _assert_agree(blocks, 15)
    assert got["Slope a"].value == pytest.approx(0.6)


@pytest.mark.parametrize("options", [{}, {"method": "spearman"}, {"replicates": "mean"}, {"df": 8}, {"method": "spearman", "ci": 0.99}])
def test_xycorr(options):
    sheet = _sheet(xy=_xy())
    result = qp.run_xy_correlation(sheet, options)
    blocks = working("xycorr", sheet.xy, options, result)
    got = _assert_agree(blocks, 12)
    assert "P (two-tailed)" in got
    if options.get("method") == "spearman":
        assert "rank x" in blocks[0].table.columns


def test_xycorr_ties_spearman():
    xy = XYTable("T", "x", [1, 2, 2, 3, 4, 5], ["a"], np.array([1, 2, 2, 5, 4, 4.0]).reshape(6, 1, 1))
    sheet = _sheet(xy=xy)
    result = qp.run_xy_correlation(sheet, {"method": "spearman"})
    _assert_agree(working("xycorr", xy, {"method": "spearman"}, result), 6)


DOSE = np.arange(-9, -3.5, 0.5)


def _curve_xy():
    rng = np.random.default_rng(3)
    clean = 5 + 95 / (1 + 10 ** ((-6.5 - DOSE) * 1.1))
    values = np.stack([clean + rng.normal(0, 2.5, DOSE.size), clean * 0.9 + rng.normal(0, 2.5, DOSE.size)], axis=1)
    reps = np.stack([values, values + rng.normal(0, 1.5, values.shape)], axis=2)
    reps[2, 0, 1] = NAN
    return XYTable("Resp", "log dose", DOSE, ["A", "B"], reps)


@pytest.mark.parametrize("options", [{"model": "log_dose_response"}, {"model": "log_dose_response", "replicates": "mean", "ci": 0.9},
                                     {"model": "log_dose_response", "fixed": {"Bottom": 0.0}},
                                     {"model": "line"}, {"model": "polynomial_2", "df": 10}])
def test_curvefit(options):
    xy = _curve_xy()
    sheet = _sheet(xy=xy)
    result = qp.run_curve_fit(sheet, options)
    blocks = working("curvefit", xy, options, result)
    got = _assert_agree(blocks, 20)
    assert got["Sy.x"].checked and got["R²"].checked and got["AICc"].checked
    assert any(b.table is not None and any(c.startswith("∂Y/∂") for c in b.table.columns) for b in blocks)


def test_curvefit_other_models():
    x = np.array([0.5, 1, 2, 4, 8, 16, 32.0])
    rng = np.random.default_rng(1)
    for model, y in (("michaelis_menten", 10 * x / (3 + x)), ("one_phase", 2 + 8 * np.exp(-0.2 * x)), ("exponential_growth", 2 * np.exp(0.1 * x)),
                     ("dose_response", 1 + 9 / (1 + (6 / x) ** 1.3))):
        xy = XYTable("t", "x", x, ["a"], (y + rng.normal(0, 0.05 * y.mean(), y.size)).reshape(-1, 1, 1))
        sheet = _sheet(xy=xy)
        result = qp.run_curve_fit(sheet, {"model": model})
        _assert_agree(working("curvefit", xy, {"model": model}, result), 10)
    xg = np.linspace(-4, 4, 15)
    xy = XYTable("t", "x", xg, ["a"], (3 * np.exp(-0.5 * ((xg - 0.5) / 1.2) ** 2) + rng.normal(0, 0.05, 15)).reshape(-1, 1, 1))
    sheet = _sheet(xy=xy)
    result = qp.run_curve_fit(sheet, {"model": "gaussian"})
    _assert_agree(working("curvefit", xy, {"model": "gaussian"}, result), 10)


def test_curvefit_model_comparison_and_datasets():
    xy = _curve_xy()
    sheet = _sheet(xy=xy)
    options = {"model": "polynomial_2", "compare": "model", "compare_model": "line"}
    result = qp.run_curve_fit(sheet, options)
    got = _assert_agree(working("curvefit", xy, options, result), 20)
    assert got["F (extra sum of squares)"].checked and got["P"].checked
    options = {"model": "log_dose_response", "compare": "model", "compare_model": "log_dose_response", "compare_fixed": {"HillSlope": 1.0}}
    result = qp.run_curve_fit(sheet, options)
    got = _assert_agree(working("curvefit", xy, options, result), 20)
    assert got["AICc difference (full − simple)"].checked
    for parameter in (None, "LogEC50"):
        options = {"model": "log_dose_response", "compare": "datasets", "compare_parameter": parameter}
        result = qp.run_curve_fit(sheet, options)
        blocks = working("curvefit", xy, options, result)
        got = _assert_agree(blocks, 20)
        assert result.values["datasets"] is not None and got["F"].checked


def _cases():
    rng = np.random.default_rng(7)
    a = rng.normal(10, 2, 14)
    b = a * 0.6 + rng.normal(0, 1.5, 14)
    c = rng.normal(0, 1, 14) - 0.3 * a
    d = np.round(rng.normal(5, 1, 14), 0)  # ties
    a[3] = NAN
    c[9] = NAN
    return ColumnTable.from_columns({"A": a, "B": b, "C": c, "D": d}, title="cases")


@pytest.mark.parametrize("options", [{}, {"method": "spearman"}, {"missing": "listwise"}, {"adjust": "holm"}, {"ci": 0.9, "variables": ["A", "B", "D"]},
                                     {"method": "spearman", "adjust": "bh", "missing": "listwise"}])
def test_corrmatrix(options):
    table = _cases()
    sheet = _sheet(table=table)
    result = qp.run_correlation_matrix(sheet, options)
    blocks = working("corrmatrix", table, options, result)
    got = _assert_agree(blocks, 12)
    pairs = len(result.values["names"]) * (len(result.values["names"]) - 1) // 2
    assert len(blocks) == pairs + 1 and len(blocks[0].table) == pairs
    if options.get("adjust"):
        assert any(s.label == "Adjusted P" for s in _steps(blocks))
    assert "t" in got


@pytest.mark.parametrize("options", [{}, {"standardize": False}, {"select": "kaiser"}, {"select": "variance", "variance": 0.6},
                                     {"select": "number", "number": 3}, {"simulations": 200, "seed": 4, "variables": ["A", "B", "C"]}])
def test_pca(options):
    table = _cases()
    sheet = _sheet(table=table)
    result = qp.run_pca(sheet, options)
    blocks = working("pca", table, options, result)
    got = _assert_agree(blocks, 10)
    assert got["Components kept"].checked
    assert got["Largest difference of the scores from the analysis"].checked
    titles = [b.title for b in blocks]
    assert "Scores" in titles and any(t.startswith(("Correlation matrix", "Covariance matrix")) for t in titles)
    scores = [b for b in blocks if b.title == "Scores"][0].table
    assert len(scores) == result.values["n"]


def test_unknown_source_gives_nothing():
    assert working("pca", None, {}, None) == []
