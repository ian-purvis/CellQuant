"""The bundled "Check the engine" benchmark set: every benchmark agrees with its published (or live reference) values."""

from __future__ import annotations

import pytest

from cellquant.stats import benchmarks
from cellquant.stats.benchmarks import BENCHMARKS, agrees, run_benchmarks, summarize, tolerance

ROWS = run_benchmarks()


def test_the_set_is_the_expected_size_and_names_are_unique():
    names = [b.name for b in BENCHMARKS]
    assert 15 <= len(names) <= 45 and len(names) == len(set(names))


@pytest.mark.parametrize("benchmark", BENCHMARKS, ids=lambda b: b.name)
def test_every_benchmark_has_a_source_data_and_expected_values(benchmark):
    assert benchmark.source.strip() and benchmark.analysis.strip() and benchmark.expected
    assert callable(benchmark.run)
    assert (benchmark.reference is not None) == any(item.published is None for item in benchmark.expected)
    for item in benchmark.expected:
        if item.published is not None:
            tolerance(item.published)  # a well-formed published number


def test_every_benchmark_passes():
    assert ROWS
    wrong = [f"{r.name}: {r.key}: published {r.published}, engine {r.engine}" for r in ROWS if not r.ok]
    assert not wrong, wrong
    summary = summarize(ROWS)
    assert summary.agreeing == summary.values == len(ROWS) and not summary.disagreeing
    assert summary.benchmarks == len(BENCHMARKS) - len(summary.skipped)


def test_rows_carry_the_fields_the_window_reads():
    row = ROWS[0]
    assert [row.name, row.analysis, row.source, row.key] and row.published is not None and isinstance(row.ok, bool)
    assert run_benchmarks([BENCHMARKS[0].name]) == [r for r in ROWS if r.name == BENCHMARKS[0].name]


def test_agreement_is_to_the_published_digits():
    assert agrees("0.0159", 0.01591) and not agrees("0.0159", 0.01596)
    assert agrees("-4.0621", -4.06214) and not agrees("-4.0621", 4.0621)
    assert agrees("9.085e-05", 9.0853e-05) and not agrees("9.085e-05", 9.09e-05)
    assert agrees("<0.01", 0.004) and not agrees("<0.01", 0.02)
    assert not agrees("1", None) and not agrees("1", float("nan"))


def test_a_wrong_engine_value_is_reported_not_hidden():
    bad = benchmarks.Benchmark("Bad", "x", "source", {}, lambda d: {"v": 2.0}, (benchmarks.Expected("v", "1.0"), benchmarks.Expected("w", "1.0")))
    rows = benchmarks.run_benchmark(bad)
    assert [r.ok for r in rows] == [False, False] and rows[1].engine is None
