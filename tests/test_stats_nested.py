"""Nested t test and nested one-way ANOVA, checked against the tests on replicate means (equal values per replicate),
hand-computed expected mean squares and the variance components."""

from __future__ import annotations

import numpy as np
import pytest
from scipy import stats

from cellquant.errors import StatisticsError
from cellquant.stats import nested as exported_nested
from cellquant.stats.nested import nested, nested_core
from cellquant.stats.replicates import ReplicateTable


def _table(k: int, replicates: int, sizes, shift: float = 0.6, seed: int = 1, paired: bool = False) -> ReplicateTable:
    rng = np.random.default_rng(seed)
    names = [f"G{i + 1}" for i in range(k)]
    columns: list[list[float]] = [[] for _ in names]
    labels: list[str] = []
    for r in range(replicates):
        counts = [sizes(r, i) for i in range(k)]
        depth = max(counts)
        for i in range(k):
            base = shift * i + rng.normal(0, 1)
            columns[i] += list(base + rng.normal(0, 2, counts[i])) + [np.nan] * (depth - counts[i])
        labels += [f"Replicate {r + 1}"] + [""] * (depth - 1)
    return ReplicateTable("Cells", names, [np.array(c) for c in columns], labels, paired=paired)


def test_exported_from_package():
    assert exported_nested is nested


def test_balanced_two_groups_equal_t_test_on_replicate_means():
    table = _table(2, 5, lambda r, i: 8)
    result = nested(table)
    means = table.summary_table()
    reference = stats.ttest_ind(means.columns[1].values, means.columns[0].values)
    assert result.analysis == "Nested t test"
    assert result.values["t"] == pytest.approx(reference.statistic) and result.values["p"] == pytest.approx(reference.pvalue)
    assert result.values["df"] == 8 and result.values["balanced"]
    assert result.values["t"] ** 2 == pytest.approx(result.values["f"])
    low, high = result.values["ci_low"], result.values["ci_high"]
    assert low < result.values["difference"] < high


def test_balanced_three_groups_equal_anova_on_replicate_means():
    table = _table(3, 4, lambda r, i: 6)
    result = nested(table, comparisons="all")
    means = table.summary_table()
    reference = stats.f_oneway(*[column.values for column in means.columns])
    assert result.analysis == "Nested one-way ANOVA"
    assert result.values["f"] == pytest.approx(reference.statistic) and result.values["p"] == pytest.approx(reference.pvalue)
    assert any(section.title.startswith("Tukey") or "comparisons" in section.title.lower() for section in result.sections)


def test_unbalanced_uses_expected_mean_squares():
    table = _table(2, 5, lambda r, i: 3 + (r * 3 + i * 5) % 9)
    core = nested_core([[np.asarray(u) for u in g] for g in _groups(table)])
    assert not core["balanced"]
    # Hand-built: E(MS) coefficients and the synthesized denominator
    n_gr = [np.array([u.size for u in g], float) for g in _groups(table)]
    big_n = sum(n.sum() for n in n_gr)
    within = sum((n ** 2).sum() / n.sum() for n in n_gr)
    c_r = (big_n - within) / (10 - 2)
    c_g = (within - sum((n ** 2).sum() for n in n_gr) / big_n) / 1
    ms_den = c_g / c_r * core["ms_r"] + (1 - c_g / c_r) * core["ms_e"]
    assert core["ms_den"] == pytest.approx(ms_den)
    result = nested(table)
    assert result.values["t"] ** 2 == pytest.approx(result.values["f"])
    assert "Satterthwaite" in result.methods


def _groups(table: ReplicateTable):
    long = table.long()
    return [[long[(long.group == name) & (long.unit == unit)].value.to_numpy() for unit in dict.fromkeys(long[long.group == name].unit)]
            for name in table.names]


def test_variance_components_and_icc():
    table = _table(2, 6, lambda r, i: 10)
    result = nested(table)
    core = nested_core(_groups(table))
    assert result.values["var_replicates"] == pytest.approx(max((core["ms_r"] - core["ms_e"]) / 10, 0))
    assert result.values["var_residual"] == pytest.approx(core["ms_e"])
    assert 0 <= result.values["icc"] <= 1
    assert "N is the number of replicates" in result.methods


def test_df_override_is_recorded():
    table = _table(2, 5, lambda r, i: 8)
    assert nested(table, df="conservative").values["dfd"] == 8  # balanced: the conservative df is the computed one
    custom = nested(table, df=20)
    assert custom.values["df"] == 20 and "Denominator df" in custom.values["overrides"]
    assert any("Set by hand" in w for w in custom.warnings)


def test_refusals():
    with pytest.raises(StatisticsError, match="Paired"):
        nested(_table(2, 4, lambda r, i: 5, paired=True))
    with pytest.raises(StatisticsError, match="one value"):
        nested(_table(2, 4, lambda r, i: 1))
    with pytest.raises(StatisticsError, match="more replicates than groups"):
        nested(_table(2, 1, lambda r, i: 5))
    with pytest.raises(StatisticsError, match="two groups"):
        nested(_table(3, 4, lambda r, i: 5), alternative="greater")


def test_few_replicates_warn():
    result = nested(_table(2, 2, lambda r, i: 6))
    assert any("Add biological replicates" in w for w in result.warnings)
