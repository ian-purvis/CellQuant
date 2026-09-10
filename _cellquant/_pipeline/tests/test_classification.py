from pathlib import Path

import numpy as np
import pytest

from cellquant.classify import ClassificationRecipe, classify_labels
from cellquant.contracts import ImageVolume, LabelVolume, MutableCancellationToken, PipelineCancelled


def volumes(values, ids=None):
    data = np.asarray(values, dtype=float)
    if data.ndim == 2:
        data = data[None, None, :, :]
    if ids is None:
        ids = np.arange(1, data.shape[2]+1)[None, None, :]
    return (ImageVolume(data, (1, 1, 1), tuple(f"ch{i}" for i in range(data.shape[-1])), Path("test.tif")),
            LabelVolume(np.asarray(ids, dtype=np.uint32), (1, 1, 1)))


def recipe(n=3, **updates):
    raw = dict(markers=[dict(name=chr(65+i), channel=i, low=1, positive_fraction=.5) for i in range(n)])
    raw.update(updates)
    return raw


@pytest.mark.parametrize("n", [3, 4])
def test_inclusive_and_exact_patterns(n):
    image, labels = volumes([[1]*n, [1, 1]+[0]*(n-2), [0]*n])
    result = classify_labels(image, labels, recipe(n))
    queries = result.queries.set_index("name")
    assert queries.loc["A+ & B+", "numerator"] == 2
    assert len(queries) == 2**n-1
    assert result.patterns.numerator.sum() == 3
    assert result.patterns.set_index("pattern").loc[" ".join(chr(65+i)+"+" for i in range(n)), "numerator"] == 1


def test_missing_unrelated_and_missing_precedes_uncertain():
    image, labels = volumes([[1, 1], [0, 0]])
    raw = recipe()
    raw["markers"][1]["uncertainty_margin"] = .1
    raw["markers"][2]["channel"] = None
    # Make B exactly on the uncertain boundary for both objects.
    image, labels = volumes([[1, 1], [1, 0], [0, 1], [0, 0]], [[[1, 1, 2, 2]]])
    result = classify_labels(image, labels, raw)
    q = result.queries.set_index("name")
    assert q.loc["A+", "evaluable"] == 2
    assert q.loc["A+ & B+", "uncertain"] == 2
    assert q.loc["A+ & B+ & C+", "missing"] == 2
    assert q.loc["A+ & B+ & C+", "uncertain"] == 0
    assert np.isnan(q.loc["C+", "percentage"])


def test_negative_queries_conditional_denominator():
    image, labels = volumes([[1, 1], [1, 0], [0, 0]])
    raw = recipe(2, queries=[dict(name="B negative among A", negative=["B"], denominator_positive=["A"])])
    row = classify_labels(image, labels, raw).queries.iloc[0]
    assert (row.total_eligible, row.evaluable, row.denominator, row.numerator, row.percentage) == (3, 3, 2, 1, 50)


def test_exact_bounds_cutoff_nonfinite_and_sparse_ids():
    image, labels = volumes([[1], [2], [3], [np.nan]], [[[7, 7, 4000000000, 4000000000]]])
    raw = recipe(1)
    raw["markers"][0].update(low=1, high=1, positive_fraction=.5)
    before = image.data.copy()
    result = classify_labels(image, labels, raw)
    assert result.calls.label.tolist() == [7, 4000000000]
    assert result.calls.call.tolist() == ["positive", "missing"]
    assert result.calls.fraction.iloc[0] == .5
    assert result.calls.reason.iloc[1] == "nonfinite_pixels"
    np.testing.assert_array_equal(image.data, before)


def test_uncertainty_endpoints():
    image, labels = volumes([[1], [1], [1], [0], [0], [1], [1], [0], [0], [0]], [[ [1]*5 + [2]*5 ]])
    raw = recipe(1)
    raw["markers"][0]["uncertainty_margin"] = .1
    assert classify_labels(image, labels, raw).calls.call.tolist() == ["positive", "uncertain"]


def test_uncertainty_decimal_upper_boundary_is_positive():
    # 3/10 == 0.3 must be positive for cutoff 0.2 and margin 0.1 (upper ∈ positive).
    values = [[1], [1], [1], [0], [0], [0], [0], [0], [0], [0]]
    image, labels = volumes(values, [[[1] * 10]])
    raw = recipe(1)
    raw["markers"][0].update(low=1, positive_fraction=0.2, uncertainty_margin=0.1)
    assert classify_labels(image, labels, raw).calls.call.tolist() == ["positive"]


def test_uncertainty_exact_lower_and_zero_margin():
    # 4/10 == 0.4 is the lower endpoint for 0.5±0.1 → uncertain (lower ∈ uncertainty).
    image, labels = volumes([[1]] * 4 + [[0]] * 6, [[[1] * 10]])
    raw = recipe(1)
    raw["markers"][0].update(low=1, positive_fraction=0.5, uncertainty_margin=0.1)
    assert classify_labels(image, labels, raw).calls.call.tolist() == ["uncertain"]
    raw["markers"][0]["uncertainty_margin"] = 0
    assert classify_labels(image, labels, raw).calls.call.tolist() == ["negative"]


def test_conditional_denominator_coverage_incomplete_measurements():
    # Four A+ cells; only one has B measured positive; three B missing → 100% of evaluable A+, 25% coverage.
    data = np.array(
        [[[[1.0, 1.0], [1.0, np.nan], [1.0, np.nan], [1.0, np.nan]]]],
        dtype=float,
    )  # ZYXC = 1x1x4x2
    labels = np.array([[[1, 2, 3, 4]]], dtype=np.uint32)
    image = ImageVolume(data, (1, 1, 1), ("ch0", "ch1"), Path("test.tif"))
    labels = LabelVolume(labels, (1, 1, 1))
    raw = recipe(
        2,
        queries=[dict(name="B among A", positive=["B"], denominator_positive=["A"])],
    )
    row = classify_labels(image, labels, raw).queries.iloc[0]
    assert row.numerator == 1 and row.denominator == 1 and row.percentage == 100
    assert row.denominator_population == 4
    assert row.denominator_coverage_pct == 25
    assert row.denominator_missing == 3


def test_region_full_object_and_rounded_centroid_measure_complete_object():
    image, labels = volumes([[0], [1], [0]], [[[10, 10, 20]]])
    roi = np.array([[[False, True, False]]])
    whole = classify_labels(image, labels, recipe(1), region=roi, context={"region_id": "retina"})
    assert whole.calls.empty and len(whole.exclusions) == 2
    centroid = classify_labels(image, labels, recipe(1, region_policy="centroid"), region=roi, context={"region_id": "retina"})
    assert centroid.calls.label.tolist() == [10]
    assert centroid.calls.fraction.tolist() == [.5]
    assert centroid.metadata["context"]["region_id"] == "retina"
    with pytest.raises(ValueError, match="region_id"):
        classify_labels(image, labels, recipe(1), region=roi)
    with pytest.raises(ValueError, match="ZYX"):
        classify_labels(image, labels, recipe(1), region=roi[0], context={"region_id": "retina"})
    with pytest.raises(ValueError, match="spacing"):
        classify_labels(image, LabelVolume(labels.data, (2, 1, 1)), recipe(1))


def test_empty_and_cancellation():
    image, labels = volumes([[1]], [[[0]]])
    result = classify_labels(image, labels, recipe(1))
    assert result.calls.empty and result.exclusions.empty
    assert result.queries.denominator.tolist() == [0]
    assert result.queries.percentage.isna().all()
    token = MutableCancellationToken()
    token.cancel()
    with pytest.raises(PipelineCancelled):
        classify_labels(image, labels, recipe(1), cancel=token)


def test_equal_summaries_do_not_determine_pixel_fraction():
    # Both have mean=median=2, min=0, max=4, sum=12, but different counts >=3.
    image, labels = volumes([[v] for v in [0, 1, 1, 3, 3, 4, 0, 0, 2, 2, 4, 4]], [[ [1]*6 + [2]*6 ]])
    raw = recipe(1)
    raw["markers"][0]["low"] = 3
    result = classify_labels(image, labels, raw)
    assert result.calls.fraction.tolist() == [.5, 1/3]
    assert result.calls.call.tolist() == ["positive", "negative"]


@pytest.mark.parametrize("field,value", [("low", True), ("low", "1"), ("high", float("inf")),
    ("positive_fraction", 0), ("positive_fraction", 1.1), ("uncertainty_margin", -.1),
    ("channel", True), ("channel", -1), ("channel", 1.5), ("compartment", "cytoplasm")])
def test_invalid_marker_rules(field, value):
    raw = recipe(1)
    raw["markers"][0][field] = value
    with pytest.raises(ValueError):
        ClassificationRecipe(raw)


@pytest.mark.parametrize("query", [dict(name="bad", positive=["A"], negative=["A"]),
    dict(name="bad", negative=["A"], denominator_positive=["A"]), dict(name="bad", positive=["Z"])])
def test_invalid_queries(query):
    with pytest.raises(ValueError):
        ClassificationRecipe(recipe(1, queries=[query]))


def test_recipe_defensive_copy_and_invalid_channel():
    raw = recipe(1)
    parsed = ClassificationRecipe(raw)
    fingerprint = parsed.fingerprint
    parsed.raw["markers"][0]["low"] = 99
    raw["markers"][0]["low"] = 99
    assert parsed.fingerprint == fingerprint and parsed.raw["markers"][0]["low"] == 1
    image, labels = volumes([[1]])
    with pytest.raises(ValueError, match="channel index"):
        classify_labels(image, labels, recipe(2))
