"""User-reviewed nuclear threshold proposals; never an accuracy optimizer.

Histograms exclude entire objects with nonfinite pixels, matching the scoring
contract. Positive examples provide disagreement feedback only.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from numbers import Integral

import numpy as np
import pandas as pd

from cellquant.classify import ClassificationRecipe, classify_labels, _json_safe, _number
from cellquant.contracts import ImageVolume, LabelVolume


def _checkpoint(cancel):
    if cancel is not None:
        cancel.raise_if_cancelled()


def _hash_array(array, cancel=None):
    array = np.ascontiguousarray(array)
    digest = hashlib.sha256(json.dumps({'shape': list(array.shape), 'dtype': array.dtype.str},
                                      sort_keys=True).encode())
    view = memoryview(array).cast('B')
    for start in range(0, len(view), 1024 * 1024):
        _checkpoint(cancel)
        digest.update(view[start:start + 1024 * 1024])
    return digest.hexdigest()


@dataclass(frozen=True)
class CalibrationAnalysis:
    image: ImageVolume
    labels: LabelVolume
    region: np.ndarray | None
    marker_name: str
    channel: int
    region_policy: str
    context: dict
    histogram_counts: np.ndarray
    histogram_edges: np.ndarray
    objects: pd.DataFrame
    evidence: dict


def _recipe(marker_name, channel, region_policy, low, high, positive_fraction, uncertainty_margin):
    return ClassificationRecipe(dict(region_policy=region_policy, markers=[dict(
        name=marker_name, channel=channel, low=low, high=high,
        positive_fraction=positive_fraction, uncertainty_margin=uncertainty_margin)]))


def analyze_calibration(image, labels, *, marker_name, channel, region=None,
                        region_policy='whole_object', context=None, bins=256, cancel=None):
    """Snapshot raw evidence and pool pixels from finite, region-eligible nuclei."""
    _checkpoint(cancel)
    if channel is None:
        raise ValueError('Marker was not acquired; select an acquired marker channel to calibrate')
    if isinstance(bins, bool) or not isinstance(bins, Integral) or not 2 <= bins <= 65536:
        raise ValueError('histogram bins must be an integer from 2 to 65536')
    recipe = _recipe(marker_name, channel, region_policy, 0, None, .5, 0)
    image = ImageVolume(np.array(image.data, copy=True), tuple(image.spacing_um),
                        tuple(image.channel_names), image.source, _json_safe(dict(image.metadata)))
    labels = LabelVolume(np.array(labels.data, copy=True), tuple(labels.spacing_um))
    region = None if region is None else np.array(region, copy=True)
    context = _json_safe(dict(context or {}))
    result = classify_labels(image, labels, recipe, region=region, context=context, cancel=cancel)
    objects = result.calls.copy()
    if objects.empty:
        raise ValueError('No eligible nuclei; load reviewed labels and check the counting region')
    ids = objects.loc[objects.reason == '', 'label'].to_numpy()
    pixels = image.data[..., channel][np.isin(labels.data, ids)]
    if not pixels.size:
        raise ValueError('No finite eligible nuclei; remove or repair nuclei containing nonfinite image pixels')
    if pixels.min() == pixels.max():
        raise ValueError('Constant nuclear intensities cannot support a threshold proposal; check the raw channel and labels')
    counts, edges = np.histogram(pixels, bins=int(bins))
    positions = np.flatnonzero(labels.data.ravel())
    all_ids, groups = np.unique(labels.data.ravel()[positions], return_inverse=True)
    sizes = np.bincount(groups)
    centers = pd.DataFrame({'label': all_ids})
    for name, coords in zip(('centroid_z', 'centroid_y', 'centroid_x'), np.unravel_index(positions, labels.data.shape)):
        centers[name] = np.bincount(groups, weights=coords) / sizes
    objects = objects.merge(centers, on='label', validate='one_to_one')
    evidence = dict(image_sha256=_hash_array(image.data, cancel), labels_sha256=_hash_array(labels.data, cancel),
                    region_sha256=None if region is None else _hash_array(region, cancel),
                    shape=list(image.data.shape), image_dtype=image.data.dtype.str,
                    labels_dtype=labels.data.dtype.str, spacing_um=list(image.spacing_um),
                    channel_names=list(image.channel_names), channel=int(channel), marker_name=marker_name,
                    source=str(image.source), analysis_metadata=dict(image.metadata),
                    measurement_grid='ZYXC', region_policy=region_policy,
                    region_kind='whole_image' if region is None else 'explicit_mask', context=context,
                    nonfinite_policy='exclude_whole_object', finite_eligible_objects=int(len(ids)),
                    pooled_pixel_count=int(pixels.size))
    evidence['fingerprint'] = hashlib.sha256(json.dumps(evidence, sort_keys=True, allow_nan=False).encode()).hexdigest()
    for array in (image.data, labels.data, region, counts, edges):
        if array is not None:
            array.flags.writeable = False
    _checkpoint(cancel)
    return CalibrationAnalysis(image, labels, region, marker_name, int(channel), region_policy,
                               context, counts, edges, objects, evidence)


def _examples(analysis, negative_ids, positive_ids):
    eligible = set(int(v) for v in analysis.objects.loc[analysis.objects.reason == '', 'label'])
    result = []
    for name, values in (('negative', negative_ids), ('positive', positive_ids)):
        values = list(values)
        if any(isinstance(v, bool) or not isinstance(v, Integral) or v <= 0 for v in values):
            raise ValueError(f'{name} example IDs must be positive integers')
        values = [int(v) for v in values]
        if len(set(values)) != len(values):
            raise ValueError(f'{name} example IDs must be unique')
        if set(values) - eligible:
            raise ValueError(f'{name} examples must exist and be eligible nuclei with finite pixels')
        result.append(sorted(values))
    if set(result[0]) & set(result[1]):
        raise ValueError('Positive and negative examples must not overlap')
    return result


def propose_calibration(analysis, *, method='otsu', negative_ids=(), positive_ids=(),
                        percentile=99.0, cancel=None):
    """Return a raw-intensity starting point; acceptance is a separate action."""
    _checkpoint(cancel)
    negative, positive = _examples(analysis, negative_ids, positive_ids)
    settings = dict(histogram_bins=int(len(analysis.histogram_counts)),
                    histogram_source='pooled_finite_eligible_nuclear_pixels')
    if method == 'otsu':
        counts = analysis.histogram_counts.astype(float)
        centers = (analysis.histogram_edges[:-1] + analysis.histogram_edges[1:]) / 2
        weights = np.cumsum(counts)
        moments = np.cumsum(counts * centers)
        valid = (weights[:-1] > 0) & (weights[:-1] < weights[-1])
        score = np.full(len(counts)-1, -np.inf)
        score[valid] = ((moments[-1] * weights[:-1][valid] - moments[:-1][valid] * weights[-1]) ** 2
                        / (weights[:-1][valid] * (weights[-1] - weights[:-1][valid])))
        if not valid.any():
            raise ValueError('Histogram has no separable populations; check channel or increase histogram bins')
        # Use the separating edge: inclusive low must exclude the lower bin,
        # including its darkest constant population (unlike a bin-center rule).
        low = float(analysis.histogram_edges[int(np.argmax(score)) + 1])
        settings.update(algorithm='numpy_otsu_between_class_variance_v1', threshold='upper_edge_of_lower_class', tie_break='first_maximum')
    elif method == 'negative_percentile':
        percentile = _number(percentile, 'negative percentile')
        if not 0 <= percentile <= 100:
            raise ValueError('negative percentile must be between 0 and 100')
        if not negative:
            raise ValueError('Select at least one explicitly negative example nucleus')
        pixels = analysis.image.data[..., analysis.channel][np.isin(analysis.labels.data, negative)]
        if pixels.min() == pixels.max():
            raise ValueError('Negative examples have constant intensities; select representative varying negative nuclei')
        low = float(np.percentile(pixels, percentile, method='linear'))
        settings.update(percentile=percentile, percentile_method='linear', control_pixel_count=int(pixels.size))
    else:
        raise ValueError('method must be otsu or negative_percentile')
    _checkpoint(cancel)
    return dict(low=low, high=None, method=method, settings=settings,
                negative_ids=negative, positive_ids=positive,
                evidence_fingerprint=analysis.evidence['fingerprint'])


def preview_calibration(analysis, *, low, high=None, positive_fraction,
                        uncertainty_margin=0, negative_ids=(), positive_ids=(), cancel=None):
    """Score one reviewed setting with the exact shared classification engine."""
    negative, positive = _examples(analysis, negative_ids, positive_ids)
    recipe = _recipe(analysis.marker_name, analysis.channel, analysis.region_policy,
                     low, high, positive_fraction, uncertainty_margin)
    result = classify_labels(analysis.image, analysis.labels, recipe, region=analysis.region,
                             context=analysis.context, cancel=cancel)
    table = result.calls.merge(analysis.objects[['label', 'centroid_z', 'centroid_y', 'centroid_x']], on='label')
    table['example'] = ['negative' if v in negative else 'positive' if v in positive else '' for v in table.label]
    table['disagreement'] = (table.example != '') & (table.example != table.call)
    return table


def calibration_record(analysis, proposal, *, low, high=None, positive_fraction,
                       uncertainty_margin=0, reviewer='', review_status='current'):
    """Record explicit acceptance, preserving the original proposal separately.

    Call only following a user acceptance action. Historical records remain
    evidence about their original image, not validation of a new image.
    """
    if review_status not in ('current', 'historical'):
        raise ValueError('review_status must be current or historical')
    if not isinstance(reviewer, str):
        raise ValueError('reviewer must be a string')
    if proposal.get('evidence_fingerprint') != analysis.evidence['fingerprint']:
        raise ValueError('Proposal belongs to different evidence; generate a new proposal before accepting')
    accepted = _recipe(analysis.marker_name, analysis.channel, analysis.region_policy,
                       low, high, positive_fraction, uncertainty_margin).raw['markers'][0]
    negative, positive = _examples(analysis, proposal['negative_ids'], proposal['positive_ids'])
    return _json_safe(dict(schema_version=1, method=proposal['method'], settings=proposal['settings'],
        proposal=proposal, accepted=accepted, negative_ids=negative, positive_ids=positive,
        evidence=analysis.evidence, histogram=dict(counts=analysis.histogram_counts.tolist(),
                                                  edges=analysis.histogram_edges.tolist()),
        review=dict(status=review_status, reviewer=reviewer, accepted=True,
                    interpretation='User-reviewed starting settings; examples are not biological ground truth')))
