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
