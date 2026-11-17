"""A failed Cellpose model download says what to do; other Cellpose failures keep their message."""

from __future__ import annotations

import socket
import ssl
import urllib.error

import numpy as np
import pytest

pytest.importorskip("cellpose")

from cellquant import hardware, segmentation
from cellquant.errors import SegmentationError
from cellquant.segmentation import CellposeBackend

PLAIN = "Cellpose could not download its model. Check the internet connection and run again, or choose the classical method."


def _backend_with_loader(monkeypatch, error: BaseException) -> CellposeBackend:
    def loader(*args, **kwargs):
        raise error

    monkeypatch.setattr(segmentation, "_cached_model", loader)
    return CellposeBackend()


def _run(backend: CellposeBackend, mode: str):
    if mode == "2d":
        return backend.segment_with_details(np.zeros((32, 32), dtype=np.float32), {})
    return backend.segment_volume(np.zeros((2, 32, 32), dtype=np.float32), {}, mode="stitch_slices", scale="stack", anisotropy=1.0)


@pytest.mark.parametrize("mode", ["2d", "3d"])
@pytest.mark.parametrize(
    "error",
    [
        urllib.error.URLError(OSError("Tunnel connection failed: 403 Forbidden")),
        urllib.error.HTTPError("https://example.org/model", 403, "Forbidden", {}, None),
        socket.gaierror(-2, "Name or service not known"),
        ConnectionResetError("reset"),
        ssl.SSLError("certificate verify failed"),
    ],
)
def test_a_failed_download_gets_a_plain_message(monkeypatch, error, mode):
    hardware.batch_notes()
    backend = _backend_with_loader(monkeypatch, error)
    with pytest.raises(SegmentationError) as caught:
        _run(backend, mode)
    assert str(caught.value) == PLAIN
    assert "Tunnel" not in str(caught.value) and "urlopen" not in str(caught.value)
    # The detail is kept for the run log.
    notes = hardware.batch_notes()
    assert any("could not download its model" in note and type(error).__name__ in note for note in notes)


def test_a_download_error_inside_another_error_counts(monkeypatch):
    try:
        try:
            raise urllib.error.URLError("no route")
        except urllib.error.URLError as inner:
            raise RuntimeError("could not fetch weights") from inner
    except RuntimeError as outer:
        backend = _backend_with_loader(monkeypatch, outer)
    with pytest.raises(SegmentationError) as caught:
        _run(backend, "2d")
    assert str(caught.value) == PLAIN


def test_other_cellpose_failures_keep_their_message(monkeypatch):
    backend = _backend_with_loader(monkeypatch, RuntimeError("CUDA error: out of cheese"))
    with pytest.raises(SegmentationError, match="Cellpose failed: CUDA error: out of cheese"):
        _run(backend, "2d")
