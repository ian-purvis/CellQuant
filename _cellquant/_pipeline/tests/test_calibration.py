from pathlib import Path

import numpy as np
import pytest

from cellquant.classify import ClassificationRecipe, classify_labels
from cellquant.classify.calibration import (analyze_calibration, propose_calibration,
    preview_calibration, calibration_record)
from cellquant.classify.store import save_classification, reopen_classification
from cellquant.contracts import ImageVolume, LabelVolume, MutableCancellationToken, PipelineCancelled


def inputs(values=(0, 1, 2, 3, 10, 11, 12, 13)):
    image = ImageVolume(np.array(values, dtype=float).reshape(1, 2, 4, 1), (1., 1., 1.), ('A',), Path('raw.tif'))
    labels = LabelVolume(np.array([1, 1, 1, 1, 2, 2, 2, 2], dtype=np.uint32).reshape(1, 2, 4), (1., 1., 1.))
    return image, labels


def analysis(**kwargs):
    return analyze_calibration(*inputs(), marker_name='A', channel=0, **kwargs)


def test_two_populations_histogram_exact_preview_and_positive_feedback():
    a = analysis(bins=13)
    assert a.histogram_counts.sum() == 8
    p = propose_calibration(a, negative_ids=[1], positive_ids=[2])
    assert 3 < p['low'] <= 10
    result = preview_calibration(a, low=p['low'], positive_fraction=.5, negative_ids=[1], positive_ids=[2])
    assert result.fraction.tolist() == [0, 1]
    assert result.call.tolist() == ['negative', 'positive']
    assert not result.disagreement.any()
    assert result.centroid_x.tolist() == [1.5, 1.5]
    assert result.centroid_y.tolist() == [0, 1]
    assert propose_calibration(a, positive_ids=[1])['low'] == p['low']
    assert preview_calibration(a, low=p['low'], positive_fraction=.5, positive_ids=[1]).disagreement.tolist() == [True, False]


def test_negative_percentile_inclusive_bounds_and_cutoff():
    a = analysis()
    p = propose_calibration(a, method='negative_percentile', negative_ids=[1], percentile=100)
    assert p['low'] == 3
    table = preview_calibration(a, low=3, high=10, positive_fraction=.25)
    assert table.fraction.tolist() == [.25, .25]
    assert table.call.tolist() == ['positive', 'positive']
    expected = classify_labels(a.image, a.labels, dict(markers=[dict(name='A', channel=0, low=3, high=10, positive_fraction=.25)])).calls
    assert table[expected.columns].equals(expected)


@pytest.mark.parametrize('kwargs, match', [
    ({'negative_ids': [1, 1]}, 'unique'), ({'negative_ids': [5]}, 'exist'),
    ({'negative_ids': [1], 'positive_ids': [1]}, 'overlap'),
    ({'positive_ids': [0]}, 'positive integers'),
    ({'method': 'negative_percentile'}, 'Select at least'),
    ({'method': 'negative_percentile', 'negative_ids': [1], 'percentile': 101}, 'between'),
])
def test_invalid_examples(kwargs, match):
    with pytest.raises(ValueError, match=match):
        propose_calibration(analysis(), **kwargs)


def test_invalid_channel_region_and_empty():
    im, lab = inputs()
    for channel, match in [(None, 'not acquired'), (1, 'out of range')]:
        with pytest.raises(ValueError, match=match):
            analyze_calibration(im, lab, marker_name='A', channel=channel)
    with pytest.raises(ValueError, match='region_id'):
        analysis(region=np.ones(lab.data.shape, dtype=bool))
    with pytest.raises(ValueError, match='boolean mask'):
        analysis(region=np.ones((2, 4), dtype=bool), context={'region_id': 'test'})
    with pytest.raises(ValueError, match='No eligible nuclei'):
        analyze_calibration(im, LabelVolume(np.zeros(lab.data.shape, np.uint32), lab.spacing_um), marker_name='A', channel=0)
    roi = np.ones(lab.data.shape, bool)
    roi[0, 0, 0] = False
    whole = analysis(region=roi, context={'region_id': 'test'})
    centroid = analysis(region=roi, context={'region_id': 'test'}, region_policy='centroid')
    assert whole.objects.label.tolist() == [2]
    assert centroid.objects.label.tolist() == [1, 2]
    with pytest.raises(ValueError, match='eligible'):
        propose_calibration(whole, negative_ids=[1])


def test_nonfinite_objects_excluded_whole_and_constant_errors():
    im, lab = inputs((np.nan, 1, 2, 3, 10, 11, 12, 13))
    a = analyze_calibration(im, lab, marker_name='A', channel=0)
    assert a.histogram_counts.sum() == 4
    assert preview_calibration(a, low=11, positive_fraction=.5).call.tolist() == ['missing', 'positive']
    with pytest.raises(ValueError, match='finite pixels'):
        propose_calibration(a, negative_ids=[1])
    for values, match in [([np.nan]*8, 'No finite'), ([1]*8, 'Constant')]:
        with pytest.raises(ValueError, match=match):
            analyze_calibration(*inputs(values), marker_name='A', channel=0)
    a = analyze_calibration(*inputs([1,1,1,1,10,11,12,13]), marker_name='A', channel=0)
    with pytest.raises(ValueError, match='constant'):
        propose_calibration(a, method='negative_percentile', negative_ids=[1])


def test_content_hashes_snapshot_and_display_independence():
    im, lab = inputs()
    a = analyze_calibration(im, lab, marker_name='A', channel=0)
    im.data[0, 0, 0, 0] = 2
    b = analyze_calibration(im, lab, marker_name='A', channel=0)
    assert a.evidence['image_sha256'] != b.evidence['image_sha256']
    assert a.image.data[0, 0, 0, 0] == 0
    lab.data[0, 0, 0] = 2
    c = analyze_calibration(im, lab, marker_name='A', channel=0)
    assert b.evidence['labels_sha256'] != c.evidence['labels_sha256']
    display_im = ImageVolume(im.data, im.spacing_um, im.channel_names, im.source, {'display_contrast': [0, 1], 'normalization': [1, 99]})
    d = analyze_calibration(display_im, lab, marker_name='A', channel=0)
    assert propose_calibration(d)['low'] == propose_calibration(c)['low']
    assert d.evidence['image_sha256'] == c.evidence['image_sha256']
    roi = np.ones(lab.data.shape, bool)
    e = analyze_calibration(im, lab, marker_name='A', channel=0, region=roi, context={'region_id':'R'})
    roi[0,0,0] = False
    f = analyze_calibration(im, lab, marker_name='A', channel=0, region=roi, context={'region_id':'R'}, region_policy='centroid')
    assert e.evidence['region_sha256'] != f.evidence['region_sha256']


def test_record_defensive_roundtrip_store_and_optional_backward_compatibility(tmp_path):
    a = analysis()
    p = propose_calibration(a, negative_ids=[1], positive_ids=[2])
    record = calibration_record(a, p, low=5, positive_fraction=.5, reviewer='Ian')
    assert record['proposal']['low'] != record['accepted']['low']
    marker = dict(name='A', channel=0, low=5, positive_fraction=.5)
    old = ClassificationRecipe(dict(markers=[marker]))
    assert 'calibration' not in old.raw['markers'][0]
    recipe = ClassificationRecipe(dict(markers=[dict(marker, calibration=record)]))
    assert recipe.fingerprint != old.fingerprint
    record['review']['reviewer'] = 'edited'
    assert recipe.raw['markers'][0]['calibration']['review']['reviewer'] == 'Ian'
    run, _ = save_classification(tmp_path, a.image, a.labels, recipe)
    saved = reopen_classification(run)
    assert saved.recipe.raw == recipe.raw
    assert saved.recipe.fingerprint == recipe.fingerprint
    historical = calibration_record(a, p, low=5, positive_fraction=.5, review_status='historical')
    assert historical['review']['status'] == 'historical'


@pytest.mark.parametrize('value', [float('nan'), float('inf'), object(), {2: 'bad'}])
def test_metadata_rejects_non_json_nested_values(value):
    with pytest.raises(ValueError):
        ClassificationRecipe(dict(markers=[dict(name='A', channel=0, low=0, positive_fraction=.5, calibration={'nested': [value]})]))


def test_cancellation():
    token = MutableCancellationToken()
    token.cancel()
    with pytest.raises(PipelineCancelled):
        analysis(cancel=token)
    a = analysis()
    with pytest.raises(PipelineCancelled):
        propose_calibration(a, cancel=token)
    with pytest.raises(PipelineCancelled):
        preview_calibration(a, low=5, positive_fraction=.5, cancel=token)


def test_acceptance_rejects_proposal_from_other_evidence():
    a = analysis()
    p = propose_calibration(a)
    b = analyze_calibration(*inputs([1,2,3,4,10,11,12,13]), marker_name='A', channel=0)
    with pytest.raises(ValueError, match='different evidence'):
        calibration_record(b, p, low=5, positive_fraction=.5)
