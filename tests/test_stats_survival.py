"""Survival analysis (Kaplan-Meier, log-rank, median, hazard ratio), checked against statsmodels and hand counts."""

from __future__ import annotations

import math

import numpy as np
import pytest

from cellquant.errors import StatisticsError
from cellquant.stats import SurvivalTable as ExportedTable
from cellquant.stats import survival as exported_survival
from cellquant.stats.survival import SurvivalTable, kaplan_meier, median_survival, survival

RNG = np.random.default_rng(3)
TA, EA = np.round(RNG.exponential(10, 30), 1), (RNG.random(30) < 0.8).astype(int)
TB, EB = np.round(RNG.exponential(16, 25), 1), (RNG.random(25) < 0.7).astype(int)
TC, EC = np.round(RNG.exponential(22, 20), 1), (RNG.random(20) < 0.7).astype(int)


def _two() -> SurvivalTable:
    return SurvivalTable("Trial", ["A", "B"], [TA, TB], [EA, EB])


def test_exported_from_package():
    assert ExportedTable is SurvivalTable and exported_survival is survival


def test_hand_counted_curve():
    # times 1, 2+, 3, 3, 5+: S = 4/5 after 1, then 4/5 × (1 − 2/3) after 3
    curve = kaplan_meier([1, 2, 3, 3, 5], [1, 0, 1, 1, 0])
    assert curve["time"].tolist() == [1, 2, 3, 5]
    assert curve["at_risk"].tolist() == [5, 4, 3, 1]
    assert curve["survival"].to_numpy() == pytest.approx([0.8, 0.8, 0.8 / 3, 0.8 / 3])
    se = 0.8 / 3 * math.sqrt(1 / (5 * 4) + 2 / (3 * 1))  # Greenwood
    assert curve["se"].iloc[2] == pytest.approx(se)


def test_curve_median_and_ci_match_statsmodels():
    survfunc = pytest.importorskip("statsmodels.duration.survfunc")
    reference = survfunc.SurvfuncRight(TA, EA)
    curve = kaplan_meier(TA, EA)
    events = curve[curve["events"] > 0]
    assert events["survival"].to_numpy() == pytest.approx(reference.surv_prob)
    assert events["se"].to_numpy()[:-1] == pytest.approx(reference.surv_prob_se[:-1])
    median, low, high = median_survival(curve)
    assert median == pytest.approx(reference.quantile(0.5))
    with np.errstate(all="ignore"):
        assert (low, high) == pytest.approx(reference.quantile_ci(0.5))


def test_logrank_and_gehan_match_statsmodels():
    survfunc = pytest.importorskip("statsmodels.duration.survfunc")
    result = survival(_two())
    times, status, group = np.r_[TA, TB], np.r_[EA, EB], np.r_[[0] * TA.size, [1] * TB.size]
    chi, p = survfunc.survdiff(times, status, group)
    assert result.values["logrank"]["chi2"] == pytest.approx(chi) and result.values["logrank"]["p"] == pytest.approx(p)
    chi, p = survfunc.survdiff(times, status, group, weight_type="gb")
    assert result.values["gehan"]["chi2"] == pytest.approx(chi) and result.values["gehan"]["p"] == pytest.approx(p)
    three = survival(SurvivalTable("", ["A", "B", "C"], [TA, TB, TC], [EA, EB, EC]))
    chi, p = survfunc.survdiff(np.r_[TA, TB, TC], np.r_[EA, EB, EC], np.r_[[0] * 30, [1] * 25, [2] * 20])
    assert three.values["logrank"]["chi2"] == pytest.approx(chi) and three.values["logrank"]["df"] == 2


def test_hazard_ratio_close_to_cox():
    sm = pytest.importorskip("statsmodels.api")
    result = survival(_two())
    hr, low, high = result.values["hazard_ratio"]["mantel_haenszel"]
    cox = sm.PHReg(np.r_[TA, TB], np.r_[[1.0] * TA.size, [0.0] * TB.size], status=np.r_[EA, EB]).fit()
    assert hr == pytest.approx(math.exp(cox.params[0]), rel=0.02) and low < hr < high
    o, e = result.values["logrank"]["observed"], result.values["logrank"]["expected"]
    assert result.values["hazard_ratio"]["logrank"][0] == pytest.approx((o[0] / e[0]) / (o[1] / e[1]))
    assert ("Hazard ratio (A / B)", "Mantel-Haenszel") in result.values["wide"].index


def test_trend_only_for_three_or_more_groups():
    three = survival(SurvivalTable("", ["Low", "Mid", "High"], [TA, TB, TC], [EA, EB, EC]), trend=True)
    assert three.values["trend"]["df"] == 1 and 0 <= three.values["trend"]["p"] <= 1
    assert "scores 1, 2, 3" in three.methods
    two = survival(_two(), trend=True)
    assert "trend" not in two.values and any("three or more" in w for w in two.warnings)


def test_table_layout_counts_blank_rows():
    time = [1, 2, 3, np.nan, 4, 5, 6]
    a = [1, 0, np.nan, 1, np.nan, np.nan, np.nan]
    b = [np.nan, np.nan, 1, np.nan, 0, 1, np.nan]
    table = SurvivalTable.from_columns(time, {"A": a, "B": b})
    assert table.dropped == 2  # row 4 has no time, row 7 no status
    assert table.times[0].tolist() == [1, 2] and table.events[1].tolist() == [1, 0, 1]
    result = survival(table)
    assert any("2 rows with a blank time or status were left out" in w for w in result.warnings)
    assert "one subject" in result.methods and "5 in all" in result.methods
    with pytest.raises(StatisticsError, match="more than one group"):
        SurvivalTable.from_columns([1], {"A": [1], "B": [0]})


def test_bad_statuses_and_times_are_refused():
    with pytest.raises(StatisticsError, match="status other than 1"):
        survival(SurvivalTable("", ["A"], [[1, 2]], [[1, 2]]))
    with pytest.raises(StatisticsError, match="negative time"):
        survival(SurvivalTable("", ["A"], [[-1, 2]], [[1, 0]]))


def test_undefined_median_and_no_events():
    result = survival(SurvivalTable("", ["A", "B"], [[1, 2, 3, 4, 5], [1, 2, 3, 4, 5]], [[0, 0, 0, 0, 0], [1, 1, 1, 0, 0]]))
    assert math.isnan(result.values["medians"]["A"][0])
    assert "Undefined" in result.row("Median survival", "A").formatted
    assert any("A has no events" in w for w in result.warnings)
    assert "Undefined" in result.row("Hazard ratio (A / B)", "Log-rank (O/E)").formatted
