"""Linking slices keeps memory proportional to the overlapping outlines, with the results the dense matrix gave."""

from __future__ import annotations

import subprocess
import sys
import textwrap

import numpy as np
import pytest

from cellquant.volume import _compact, stitch_slices


def _dense_iou(current, n_current, previous, n_previous):
    width = n_previous + 1
    pairs = np.bincount((current * width + previous).ravel(), minlength=(n_current + 1) * width)
    overlap = pairs.reshape(n_current + 1, width).astype(np.float64)
    area_current = overlap.sum(axis=1, keepdims=True)
    area_previous = overlap.sum(axis=0, keepdims=True)
    union = area_current + area_previous - overlap
    with np.errstate(divide="ignore", invalid="ignore"):
        iou = np.where(union > 0, overlap / union, 0.0)
    return iou[1:, 1:]


def _dense_stitch(planes, threshold=0.25):
    """The linking as it was before: a dense (current + 1) x (previous + 1) overlap matrix."""

    planes = np.asarray(planes)
    output = np.zeros(planes.shape, dtype=np.int32)
    next_id = 1
    for z in range(planes.shape[0]):
        current, n_current = _compact(planes[z])
        if n_current == 0:
            continue
        previous_ids = np.unique(output[z - 1]) if z > 0 else np.array([0])
        previous_ids = previous_ids[previous_ids != 0]
        assigned = np.zeros(n_current + 1, dtype=np.int32)
        if previous_ids.size:
            lookup = np.zeros(int(previous_ids.max()) + 1, dtype=np.int64)
            lookup[previous_ids] = np.arange(1, previous_ids.size + 1)
            previous = lookup[output[z - 1]]
            iou = _dense_iou(current, n_current, previous, previous_ids.size)
            iou[iou < threshold] = 0.0
            iou[iou < iou.max(axis=0, keepdims=True)] = 0.0
            best = iou.argmax(axis=1)
            linked = iou.max(axis=1) > 0
            assigned[1:][linked] = previous_ids[best[linked]]
        new = np.nonzero(assigned[1:] == 0)[0] + 1
        assigned[new] = np.arange(next_id, next_id + new.size, dtype=np.int32)
        next_id += int(new.size)
        output[z] = assigned[current]
    return output


def _random_stack(rng, depth, size, blocks, ids):
    """Blocky labels (so overlaps tie often), non-contiguous ids, some empty slices."""

    planes = np.zeros((depth, size, size), dtype=np.int32)
    for z in range(depth):
        if rng.random() < 0.15:
            continue
        step = int(rng.integers(2, 5))
        grid = rng.integers(0, blocks + 1, size=(size // step + 1, size // step + 1))
        plane = np.kron(grid, np.ones((step, step), dtype=int))[:size, :size]
        if rng.random() < 0.5:
            plane = np.roll(plane, int(rng.integers(-2, 3)), axis=int(rng.integers(0, 2)))
        planes[z] = ids[plane % len(ids)] * (plane > 0)
    return planes


def test_same_result_as_the_dense_matrix_on_many_random_stacks():
    rng = np.random.default_rng(7)
    for trial in range(300):
        ids = rng.permutation(np.arange(1, 400))[: int(rng.integers(2, 40))] * int(rng.integers(1, 4))
        planes = _random_stack(rng, int(rng.integers(1, 6)), int(rng.integers(6, 24)), int(rng.integers(1, 12)), ids)
        threshold = float(rng.choice([0.0, 0.1, 0.25, 0.5, 0.9, 1.0]))
        assert np.array_equal(stitch_slices(planes, threshold), _dense_stitch(planes, threshold)), (trial, threshold)


def test_same_result_with_ties_and_edge_cases():
    # Two upper outlines overlap one lower outline equally, and one upper outline overlaps two lower ones equally.
    lower = np.zeros((1, 4, 8), dtype=np.int32)
    lower[0, :, :4], lower[0, :, 4:] = 5, 9
    upper = np.zeros((1, 4, 8), dtype=np.int32)
    upper[0, :2, :], upper[0, 2:, :] = 3, 7
    stack = np.concatenate([lower, upper])
    assert np.array_equal(stitch_slices(stack, 0.25), _dense_stitch(stack, 0.25))
    for stack in (np.zeros((3, 5, 5), dtype=np.int32), np.zeros((1, 5, 5), dtype=np.int32), np.ones((3, 5, 5), dtype=np.int32)):
        assert np.array_equal(stitch_slices(stack), _dense_stitch(stack))
    gap = np.concatenate([lower, np.zeros_like(lower), lower])  # an empty slice in the middle starts new objects
    assert np.array_equal(stitch_slices(gap), _dense_stitch(gap))


def test_tens_of_thousands_of_labels_per_slice_link_within_a_small_memory_bound():
    # A dense matrix of this size would need (30000 + 1) x (30000 + 1) x 8 bytes = 7 GB. Run in a fresh process whose
    # address space is limited far below that: finishing there is the proof. (The limit needs a Unix system.)
    pytest.importorskip("resource")
    code = textwrap.dedent(
        """
        import resource, numpy as np
        resource.setrlimit(resource.RLIMIT_AS, (3_000_000_000, 3_000_000_000))
        from cellquant.volume import stitch_slices
        n = 30000
        ids = np.arange(1, n + 1, dtype=np.int32).reshape(150, 200)  # 30,000 outlines of one pixel
        plane = ids
        planes = np.zeros((3, 150, 200), dtype=np.int32)
        planes[0], planes[1], planes[2] = plane, plane, np.roll(plane, 1, axis=1)  # the third slice is shifted: other labels, same places
        out = stitch_slices(planes, 0.25)
        assert out[0].max() == n and np.array_equal(out[0], out[1])
        assert len(np.unique(out)) == n  # every outline overlaps the one at its position below: nothing starts anew
        """
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=600)
    assert done.returncode == 0, done.stderr[-2000:]
