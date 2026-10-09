"""Keep the computer awake during a run: held while busy, released when the run ends."""

from __future__ import annotations

import sys

from cellquant import keep_awake


def test_hold_and_release_are_safe_to_repeat(monkeypatch):
    calls = []

    class Kernel:
        def SetThreadExecutionState(self, flags):
            calls.append(flags)
            return 0  # the previous state: 0 on a thread that never asked before

    class Windll:
        kernel32 = Kernel()

    import ctypes

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", Windll(), raising=False)
    guard = keep_awake.KeepAwake()
    guard.hold()
    guard.hold()
    assert guard.held and calls == [0x80000003]
    guard.release()
    guard.release()
    assert not guard.held and calls == [0x80000003, 0x80000000]


def test_does_nothing_where_unsupported(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    guard = keep_awake.KeepAwake()
    guard.hold()
    assert not guard.held and not keep_awake.supported()
    guard.release()


def test_window_holds_while_busy(tmp_path, monkeypatch):
    napari = __import__("pytest").importorskip("napari")
    from cellquant.gui import app
    from cellquant.gui.app import CellQuantWindow

    monkeypatch.setattr(app, "save_keep_awake", lambda on: None)  # leave this computer's settings alone
    monkeypatch.setattr(keep_awake.KeepAwake, "hold", lambda self: setattr(self, "_held", True))
    monkeypatch.setattr(keep_awake.KeepAwake, "release", lambda self: setattr(self, "_held", False))
    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        __import__("pytest").skip(f"napari viewer could not start: {exc}")
    try:
        shell = CellQuantWindow(viewer)
        box = shell._footer.keep_awake
        box.setChecked(True)
        shell._job = object()  # a run is going
        shell.set_busy(True)
        assert shell._keep_awake.held
        box.setChecked(False)
        assert not shell._keep_awake.held
        box.setChecked(True)
        assert shell._keep_awake.held
        shell._job = None
        shell.set_busy(False)
        assert not shell._keep_awake.held
    finally:
        viewer.close()
