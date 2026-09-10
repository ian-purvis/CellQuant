"""Resource preflight helpers for usability review item 10."""

from pathlib import Path

import numpy as np

from cellquant.contracts import ImageVolume, LabelVolume
from cellquant.plugin.resources import estimate_preparation


def test_estimate_preparation_flags_oversized_snapshot(monkeypatch):
    image = ImageVolume(
        np.zeros((2, 64, 64, 1), dtype=np.float32),
        (1.0, 0.5, 0.5),
        ("DAPI",),
        Path("x.tif"),
    )
    labels = LabelVolume(np.ones((2, 64, 64), dtype=np.uint32), (1.0, 0.5, 0.5))

    class _Mem:
        available = 1024  # tiny

    monkeypatch.setattr("cellquant.plugin.resources.psutil.virtual_memory", lambda: _Mem())
    monkeypatch.setattr(
        "cellquant.plugin.resources.psutil.disk_usage",
        lambda _path: type("D", (), {"free": 10 * 1024 ** 3})(),
    )
    estimate = estimate_preparation(image=image, labels=labels)
    assert estimate.blocking_errors
    assert "RAM" in estimate.blocking_errors[0]
    assert estimate.total_preparation_bytes > 0


def test_estimate_preparation_reports_scratch_path(tmp_path, monkeypatch):
    monkeypatch.setenv("CELLQUANT_SCRATCH", str(tmp_path / "scratch"))
    from cellquant.persist.staging import local_staging_root

    root = local_staging_root()
    assert root == (tmp_path / "scratch").resolve()
    estimate = estimate_preparation()
    assert estimate.scratch_root == root


def test_estimate_preparation_does_not_materialize_lazy_array():
    class Lazy:
        shape = (2, 4, 4, 1)
        dtype = np.float32
        calls = 0

        def __array__(self, *_a, **_k):
            type(self).calls += 1
            return np.zeros(self.shape, self.dtype)

    lazy = Lazy()
    estimate = estimate_preparation(image=lazy)
    assert Lazy.calls == 0
    assert estimate.image_bytes == 2 * 4 * 4 * 1 * 4
    assert "Not estimated" not in "\n".join(estimate.as_preflight_lines())


def test_unknown_array_size_is_not_reported_as_zero():
    class Mystery:
        pass

    estimate = estimate_preparation(image=Mystery())
    assert estimate.image_bytes is None
    lines = "\n".join(estimate.as_preflight_lines())
    assert "Not estimated" in lines
    assert "image 0 B" not in lines


def test_numpy_array_nbytes_uses_shape_not_buffer():
    array = np.zeros((2, 8, 8, 1), dtype=np.float32)
    estimate = estimate_preparation(image=array)
    assert estimate.image_bytes == 2 * 8 * 8 * 1 * 4
    assert "Not estimated" not in "\n".join(estimate.as_preflight_lines())
