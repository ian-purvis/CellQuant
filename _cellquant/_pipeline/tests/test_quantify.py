import numpy as np
from cellquant.quantify import quantify, overlap_metrics, object_table, coexpression_summary


def test_per_label_channel_quantification_and_overlap():
    labels = np.array([[[1, 1], [2, 2]]], dtype=np.uint16)
    image = np.array([
        [[[0, 10], [10, 10]]],
        [[[10, 10], [0, 10]]],
    ])
    thresholds = {"A": 5, "B": 5}
    long = quantify(image, labels, ["A", "B"], thresholds)
    a1 = long[(long.label == 1) & (long.channel == "A")].iloc[0]
    assert a1["mean"] == 5
    assert a1["median"] == 5
    assert a1["integrated_intensity"] == 10
    assert a1["positive_pixel_fraction"] == 0.5
    overlap = overlap_metrics(image, labels, ["A", "B"], thresholds)
    pair1 = overlap[(overlap.label == 1) & (overlap.channel_a == "A")].iloc[0]
    assert pair1["intersection_pixels"] == 1
    assert pair1["jaccard"] == 0.5
    objects = object_table(long, overlap)
    assert set(objects.label) == {1, 2}
    assert set(objects.coexpression_order) == {2}
    summary = coexpression_summary(objects)
    assert summary.object_count.sum() == 2

