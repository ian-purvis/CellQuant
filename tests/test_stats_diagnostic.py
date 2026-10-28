"""ROC curve and Bland-Altman agreement, checked against scipy, statsmodels and hand counts."""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import stats

from cellquant.errors import StatisticsError
from cellquant.stats import bland_altman as exported_bland_altman
from cellquant.stats import roc_curve as exported_roc_curve
from cellquant.stats.diagnostic import bland_altman, roc_curve

CONTROLS = np.array([1.2, 2.3, 2.9, 3.1, 3.8, 4.4, 2.3, 5.0])
PATIENTS = np.array([3.5, 4.4, 5.2, 6.1, 4.9, 7.3, 5.5])


def test_exported_from_package():
    assert exported_roc_curve is roc_curve and exported_bland_altman is bland_altman


def test_auc_matches_mann_whitney():
    result = roc_curve(CONTROLS, PATIENTS)
    u = stats.mannwhitneyu(PATIENTS, CONTROLS, alternative="two-sided").statistic
    assert result.values["auc"] == pytest.approx(u / (CONTROLS.size * PATIENTS.size))
    assert result.values["direction"] == ">"
    assert result.values["n_controls"] == 8 and result.values["n_patients"] == 7


def test_auc_matches_sklearn_when_available():
    metrics = pytest.importorskip("sklearn.metrics")
    labels = np.r_[np.zeros(CONTROLS.size), np.ones(PATIENTS.size)]
    expected = metrics.roc_auc_score(labels, np.r_[CONTROLS, PATIENTS])
    assert roc_curve(CONTROLS, PATIENTS).values["auc"] == pytest.approx(expected)


def test_hanley_mcneil_se_by_hand():
    controls, patients = [1, 2, 3, 4], [3, 5, 6, 7]
    # Patient > control pairs: 3 beats 1,2 (+tie with 3 = 0.5); 5,6,7 beat all 4 -> 2.5 + 12 = 14.5 of 16
    auc = 14.5 / 16
    q1, q2 = auc / (2 - auc), 2 * auc**2 / (1 + auc)
    se = math.sqrt((auc * (1 - auc) + 3 * (q1 - auc**2) + 3 * (q2 - auc**2)) / 16)
    result = roc_curve(controls, patients)
    assert result.values["auc"] == pytest.approx(auc)
    assert result.values["se"] == pytest.approx(se)
    z = stats.norm.ppf(0.975)
    assert result.values["ci_low"] == pytest.approx(auc - z * se)
    assert result.values["ci_high"] == pytest.approx(min(auc + z * se, 1.0))
    # P value uses the null standard error (area = 0.5: Q1 = Q2 = 1/3)
    se0 = math.sqrt((0.25 + 6 * (1 / 3 - 0.25)) / 16)
    assert result.values["p"] == pytest.approx(2 * stats.norm.sf((auc - 0.5) / se0))


def test_ci_clipped_to_unit_interval():
    result = roc_curve([1, 2, 3], [10, 11, 12])
    assert result.values["auc"] == 1.0
    assert result.values["ci_high"] == 1.0 and result.values["ci_low"] <= 1.0


def test_sensitivity_specificity_at_cutoff_by_hand():
    curve = roc_curve(CONTROLS, PATIENTS).values["curve"]
    row = curve.iloc[(curve["cutoff"] - 4.65).abs().argmin()]
    assert row["cutoff"] == pytest.approx(4.65)  # midpoint of 4.4 and 4.9
    # patients above 4.65: 5.2, 6.1, 4.9, 7.3, 5.5 = 5 of 7; controls at or below: all but 5.0 = 7 of 8
    assert row["sensitivity"] == pytest.approx(100 * 5 / 7)
    assert row["specificity"] == pytest.approx(100 * 7 / 8)
    assert row["fpr_percent"] == pytest.approx(12.5)
    assert row["likelihood_ratio"] == pytest.approx((5 / 7) / (1 / 8))


def test_wilson_intervals_match_statsmodels():
    proportion = pytest.importorskip("statsmodels.stats.proportion")
    curve = roc_curve(CONTROLS, PATIENTS, ci=0.9).values["curve"]
    row = curve.iloc[(curve["cutoff"] - 4.65).abs().argmin()]
    low, high = proportion.proportion_confint(5, 7, alpha=0.1, method="wilson")
    assert row["sens_low"] == pytest.approx(100 * low) and row["sens_high"] == pytest.approx(100 * high)
    low, high = proportion.proportion_confint(7, 8, alpha=0.1, method="wilson")
    assert row["spec_low"] == pytest.approx(100 * low) and row["spec_high"] == pytest.approx(100 * high)


def test_curve_runs_from_origin_to_corner():
    curve = roc_curve(CONTROLS, PATIENTS).values["curve"]
    assert list(curve.columns) == ["cutoff", "sensitivity", "sens_low", "sens_high", "specificity", "spec_low", "spec_high",
                                   "likelihood_ratio", "fpr_percent"]
    assert curve["fpr_percent"].iloc[0] == 0 and curve["sensitivity"].iloc[0] == 0
    assert curve["fpr_percent"].iloc[-1] == 100 and curve["sensitivity"].iloc[-1] == 100
    assert curve["fpr_percent"].is_monotonic_increasing and curve["sensitivity"].is_monotonic_increasing
    assert len(curve) == np.unique(np.r_[CONTROLS, PATIENTS]).size + 1
    assert math.isnan(curve["likelihood_ratio"].iloc[0])  # no false positives


def test_trapezoid_area_equals_auc():
    result = roc_curve(CONTROLS, PATIENTS)
    curve = result.values["curve"]
    area = np.trapezoid(curve["sensitivity"] / 100, curve["fpr_percent"] / 100)
    assert area == pytest.approx(result.values["auc"])


def test_direction_auto_flips_when_patients_are_lower():
    high = roc_curve(CONTROLS, PATIENTS)
    low = roc_curve(PATIENTS, CONTROLS)  # now the "patients" tend lower
    assert low.values["direction"] == "<"
    assert low.values["auc"] == pytest.approx(high.values["auc"])
    assert low.values["wide"].loc[("Data", "Positive when")].iloc[0] == "the value is below the cutoff"
    curve = low.values["curve"]
    assert curve["fpr_percent"].iloc[0] == 0 and curve["sensitivity"].iloc[-1] == 100
    forced = roc_curve(CONTROLS, PATIENTS, direction="<")
    assert forced.values["auc"] == pytest.approx(1 - high.values["auc"])


def test_nan_dropped_and_wide_layout():
    result = roc_curve(np.r_[CONTROLS, np.nan], np.r_[np.nan, PATIENTS], style="APA")
    assert result.values["n_controls"] == 8 and result.values["n_patients"] == 7
    wide = result.values["wide"]
    assert wide.index.names == ["section", "statistic"]
    assert list(wide.loc["Area under the ROC curve"].index) == ["Area", "Std. Error", "95% CI", "P value", "Significant?"]
    assert list(wide.loc["Data"].index) == ["Controls (controls) n", "Patients (patients) n", "Positive when"]
    assert len(wide.loc[wide.index.get_level_values(0).str.startswith("Sensitivity and specificity")]) == len(result.values["curve"])
    assert result.analysis == "ROC curve" and result.methods and "Hanley" in result.methods
    assert not result.warnings


def test_small_groups_warn():
    assert any("Fewer than 5" in text for text in roc_curve([1, 2, 3], [2, 3, 4, 5, 6]).warnings)


def test_roc_refusals():
    with pytest.raises(StatisticsError):
        roc_curve([np.nan], [1, 2])
    with pytest.raises(StatisticsError):
        roc_curve([1, 2], [])
    with pytest.raises(StatisticsError):
        roc_curve([1, 2], [3, 4], direction="up")
    with pytest.raises(StatisticsError):
        roc_curve([1, 2], [3, 4], ci=1.5)
    with pytest.raises(StatisticsError):
        roc_curve([1, 2], [3, 4], alpha=0)


A = np.array([10.2, 11.5, 9.8, 12.1, 13.0, 8.7, 10.9, 11.1, 12.6, 9.4, 10.0, 11.8])
B = np.array([10.0, 11.9, 9.1, 12.5, 12.2, 8.9, 10.1, 11.6, 12.0, 9.9, 10.3, 11.2])


def test_difference_bias_sd_and_limits():
    result = bland_altman(A, B, names=("New", "Old"))
    diff = A - B
    v = result.values
    assert v["bias"] == pytest.approx(diff.mean()) and v["sd"] == pytest.approx(diff.std(ddof=1))
    assert v["low"] == pytest.approx(diff.mean() - 1.96 * diff.std(ddof=1))
    assert v["high"] == pytest.approx(diff.mean() + 1.96 * diff.std(ddof=1))
    np.testing.assert_allclose(v["average"], (A + B) / 2)
    np.testing.assert_allclose(v["measure"], diff)
    assert v["n"] == 12
    assert result.analysis == "Bland-Altman"
    assert result.values["wide"].index.names == ["section", "statistic"]
    assert {"Bias", "Limits of agreement", "Data"} <= set(result.values["wide"].index.get_level_values("section"))


def test_confidence_intervals_use_t():
    result = bland_altman(A, B, ci=0.9)
    v = result.values
    n, sd = v["n"], v["sd"]
    t = stats.t.ppf(0.95, n - 1)
    assert v["bias_ci"][0] == pytest.approx(v["bias"] - t * sd / math.sqrt(n))
    assert v["bias_ci"][1] == pytest.approx(v["bias"] + t * sd / math.sqrt(n))
    half = t * math.sqrt(3 * sd**2 / n)
    assert v["low_ci"] == pytest.approx((v["low"] - half, v["low"] + half))
    assert v["high_ci"] == pytest.approx((v["high"] - half, v["high"] + half))
    # the limits keep the 1.96 multiplier whatever ci is
    assert v["high"] - v["bias"] == pytest.approx(1.96 * sd)
    ref = stats.ttest_1samp(A - B, 0).confidence_interval(0.9)
    assert v["bias_ci"] == pytest.approx((ref.low, ref.high))


def test_ratio_and_percent_methods():
    ratio = bland_altman(A, B, method="ratio").values
    assert ratio["bias"] == pytest.approx((A / B).mean())
    percent = bland_altman(A, B, method="percent").values
    expected = 100 * (A - B) / ((A + B) / 2)
    assert percent["bias"] == pytest.approx(expected.mean()) and percent["sd"] == pytest.approx(expected.std(ddof=1))


def test_ratio_refuses_non_positive():
    with pytest.raises(StatisticsError):
        bland_altman([1.0, 0.0, 3.0], [1.0, 2.0, 3.0], method="ratio")
    with pytest.raises(StatisticsError):
        bland_altman([1.0, 2.0, 3.0], [1.0, -2.0, 3.0], method="ratio")


def test_nan_pairs_dropped_and_counted():
    a = np.r_[A, np.nan, 5.0]
    b = np.r_[B, 5.0, np.nan]
    result = bland_altman(a, b)
    assert result.values["n"] == 12
    assert result.values["bias"] == pytest.approx((A - B).mean())
    assert any("2 pairs" in text for text in result.warnings)


def test_bland_altman_refusals():
    with pytest.raises(StatisticsError):
        bland_altman([1.0], [2.0])
    with pytest.raises(StatisticsError):
        bland_altman([1.0, np.nan, 3.0], [np.nan, 2.0, 3.0])
    with pytest.raises(StatisticsError):
        bland_altman([1.0, 2.0, 3.0], [1.0, 2.0])
    with pytest.raises(StatisticsError):
        bland_altman([1.0, 2.0], [2.0, 3.0], method="log")
    with pytest.raises(StatisticsError):
        bland_altman([1.0, 2.0], [-1.0, 1.0], method="percent")
    with pytest.raises(StatisticsError):
        bland_altman([1.0, 2.0], [2.0, 3.0], ci=0)
