import json

import numpy as np
import pytest

from cellquant.verify import compare_pair, compare_reference_set, match_labels, render_parity_report


def _labels():
    reference = np.zeros((2, 6, 6), dtype=np.uint32)
    reference[:, 0:2, 0:2] = 1
    reference[:, 3:5, 3:6] = 2
    candidate = reference.copy()
    return reference, candidate


def test_identity_is_exact():
    reference, candidate = _labels()
    result = compare_pair(reference, candidate, "identity.tif")
    assert result.reference_count == result.candidate_count == 2
    assert result.foreground_iou == 1
    assert result.optimal_matched_mean_iou == 1
    assert result.f1_at_0_50 == result.f1_at_0_75 == 1
    assert result.count_delta == 0
    assert np.all(result.matched_labels.iou == 1)


def test_split_object_changes_counts_and_threshold_f1():
    reference, candidate = _labels()
    candidate[:, 3:5, 4] = 3
    result = compare_pair(reference, candidate)
    assert result.candidate_count == 3
    assert result.count_delta == 1
    assert result.f1_at_0_50 < 1
    assert match_labels(reference, candidate, 0.75).f1 < 1


def test_shape_and_dtype_are_not_coerced():
    reference, candidate = _labels()
    with pytest.raises(ValueError, match="shapes differ"):
        compare_pair(reference, candidate[:, :-1])
    with pytest.raises(TypeError, match="integer"):
        compare_pair(reference.astype(np.float32), candidate)


def test_set_table_and_report_artifacts(tmp_path):
    reference, candidate = _labels()
    result = compare_pair(reference, candidate, "case.tif")
    table = compare_reference_set([("case.tif", reference, candidate)])
    assert list(table.name) == ["case.tif"]
    report = render_parity_report([result], tmp_path)
    assert report.is_file()
    assert (tmp_path / "parity_table.csv").is_file()
    assert (tmp_path / "parity_summary.png").is_file()
    assert json.loads(report.read_text())["aggregate"]["mean_f1_at_0_75"] == 1

