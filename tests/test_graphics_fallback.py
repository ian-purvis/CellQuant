"""Software-graphics fallback after a graphics driver error (cellquant/gui/graphics.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cellquant.gui import graphics

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "packaging" / "windows"


def _raise_from(filename: str, exc: BaseException) -> BaseException:
    code = compile("def draw(exc):\n    raise exc\n", filename, "exec")
    scope: dict = {}
    exec(code, scope)
    try:
        scope["draw"](exc)
    except BaseException as caught:  # noqa: BLE001
        return caught
    raise AssertionError("not raised")


def test_access_violation_in_vispy_is_a_graphics_error():
    exc = _raise_from(r"C:\env\Lib\site-packages\vispy\gloo\gl\gl2.py", OSError("exception: access violation reading 0x34"))
    assert graphics.is_graphics_error(exc)


def test_other_errors_are_not_graphics_errors():
    assert not graphics.is_graphics_error(_raise_from("/env/cellquant/segmentation.py", OSError("file not found")))
    assert not graphics.is_graphics_error(_raise_from("/env/cellquant/gui/app.py", ValueError("bad value")))


def test_remember_writes_the_launcher_file(tmp_path, monkeypatch):
    target = tmp_path / "CellQuant" / "graphics.txt"
    monkeypatch.setenv("CELLQUANT_GRAPHICS_FILE", str(target))
    monkeypatch.delenv("CELLQUANT_SOFTWARE_GRAPHICS", raising=False)
    assert graphics.remember_software_graphics()
    assert target.read_text(encoding="ascii").strip() == "software"


@pytest.mark.parametrize("software", ["1", None])
def test_remember_does_nothing_without_the_launcher_or_when_already_on(tmp_path, monkeypatch, software):
    if software:
        monkeypatch.setenv("CELLQUANT_GRAPHICS_FILE", str(tmp_path / "graphics.txt"))
        monkeypatch.setenv("CELLQUANT_SOFTWARE_GRAPHICS", software)
    else:
        monkeypatch.delenv("CELLQUANT_GRAPHICS_FILE", raising=False)
    assert not graphics.remember_software_graphics()
    assert not (tmp_path / "graphics.txt").exists()


def test_one_launcher_that_reopens_with_software_graphics():
    assert not (ROOT / "Open CellQuant (software graphics).bat").exists()
    launcher = (WINDOWS / "launch_cellquant.ps1").read_text(encoding="ascii")
    assert f"$GraphicsExitCodes = @({graphics.GRAPHICS_EXIT_CODE}," in launcher
    assert "CELLQUANT_GRAPHICS_FILE" in launcher and "VISPY_GL_LIB" in launcher
    assert "Clear-CellQuantSoftwareGraphics" in (WINDOWS / "install_windows.ps1").read_text(encoding="ascii")


def test_vispy_logged_drawing_errors_are_graphics_errors():
    logged = (
        'Traceback (most recent call last):\n  File "C:\\env\\Lib\\site-packages\\vispy\\gloo\\glir.py", line 752, in _parse\n'
        "RuntimeError: Using glBindFramebuffer with no OpenGL context."
    )
    assert graphics.is_graphics_error_text(logged)
    assert graphics.is_graphics_error_text(logged.replace("RuntimeError: Using glBindFramebuffer with no OpenGL context.", "OSError: exception: access violation reading 0x34"))
    assert not graphics.is_graphics_error_text("Error drawing visual <Image>")


def test_one_error_mid_session_only_warns_and_repeats_switch(tmp_path, monkeypatch):
    pytest.importorskip("napari")
    import logging
    import time

    import napari.utils.notifications as notifications

    target = tmp_path / "graphics.txt"
    monkeypatch.setenv("CELLQUANT_GRAPHICS_FILE", str(target))
    monkeypatch.delenv("CELLQUANT_SOFTWARE_GRAPHICS", raising=False)
    warnings: list[str] = []
    monkeypatch.setattr(notifications, "show_warning", warnings.append)
    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    class _Signal:
        def connect(self, callback):
            self.callback = callback

    class _Manager:
        notification_ready = _Signal()

    monkeypatch.setattr(notifications, "notification_manager", _Manager())
    vispy_logger = logging.getLogger("vispy")
    handlers = list(vispy_logger.handlers)
    try:
        graphics.watch_viewer_errors(app=None)
        report = _Manager.notification_ready.callback
        error = type("Notification", (), {"exception": _raise_from(r"C:\env\Lib\site-packages\vispy\gloo\gl\_gl2.py", OSError("exception: access violation reading 0x34"))})

        clock[0] += 600  # well after startup, during a run
        report(error)
        report(error)  # the same paint reported twice
        assert not target.exists() and not graphics.restart_requested
        assert len(warnings) == 1 and "keeps going" in warnings[0]

        clock[0] += 60
        report(error)
        assert not target.exists() and len(warnings) == 1
        clock[0] += 60
        report(error)
        assert target.read_text(encoding="ascii").strip() == "software"
        assert len(warnings) == 2 and "software graphics from the next start" in warnings[1]
        assert not graphics.restart_requested
    finally:
        vispy_logger.handlers = handlers
