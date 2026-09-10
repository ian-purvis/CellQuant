import json

import numpy as np
import pandas as pd

from cellquant.verify import compare_pair, match_labels, render_parity_report
from cellquant.verify import parity


def _volume_discrepancy():
    reference = np.zeros((1, 3, 40), dtype=np.uint32)
    candidate = np.zeros_like(reference)
    starts = [0, 5, 10, 15, 22]
    for label, start in enumerate(starts, 1):
        reference[0, 1, start] = label
        candidate[0, 1, start] = label
    candidate[0, 1, 23:32] = 5
    return reference, candidate


def test_bland_altman_fields_and_likely_causes_are_written():
    reference, candidate = _volume_discrepancy()
    result = compare_pair(reference, candidate, "volumes.tif")
    assert result.bland_altman_mean_difference_voxels == 1.8
    assert result.volume_outlier_count == 1
    assert result.matched_labels.is_volume_outlier.sum() == 1
    assert "outliers" in " ".join(result.likely_causes)


def test_report_adds_performance_outliers_and_source_overlay(tmp_path):
    reference, candidate = _volume_discrepancy()
    result = compare_pair(reference, candidate, "volumes.tif")
    report = render_parity_report(
        [result],
        tmp_path,
        performance={
            "volumes.tif": {
                "baseline": {"runtime_seconds": 4, "ram_bytes": 100, "vram_bytes": None},
                "candidate": {"runtime_seconds": 2, "ram_bytes": 125, "vram_bytes": 50},
            }
        },
        source_images={
            "volumes.tif": {
                "image": np.arange(reference.size, dtype=np.float32).reshape(reference.shape),
                "reference": reference,
                "candidate": candidate,
            }
        },
    )
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["performance"][0]["candidate_to_baseline_runtime_seconds_ratio"] == 0.5
    performance = pd.read_csv(tmp_path / "performance_comparison.csv")
    assert performance.loc[0, "candidate_to_baseline_ram_bytes_ratio"] == 1.25
    assert (tmp_path / "volumes_volume_outliers.csv").is_file()
    assert (tmp_path / "volumes_outlier_overlay.png").is_file()


def test_identity_with_thousands_of_labels_never_builds_dense_matrix(monkeypatch):
    labels = np.arange(1, 10_001, dtype=np.uint32).reshape(1, 100, 100)

    def dense_path_forbidden(*args, **kwargs):
        raise AssertionError("identity comparison entered an O(N*M) path")

    monkeypatch.setattr(parity, "_contingency", dense_path_forbidden)
    monkeypatch.setattr(parity, "linear_sum_assignment", dense_path_forbidden)
    result = compare_pair(labels, labels.copy(), "ten_thousand.tif")
    match = match_labels(labels, labels.copy(), 0.75)
    assert result.reference_count == result.candidate_count == 10_000
    assert len(result.matched_labels) == 10_000
    assert np.all(result.matched_labels.reference_label == result.matched_labels.candidate_label)
    assert np.all(result.matched_labels.iou == 1)
    assert result.volume_wasserstein_voxels == result.volume_ks_statistic == 0
    assert result.volume_outlier_count == 0
    assert match.true_positive == 10_000
    assert match.f1 == 1
