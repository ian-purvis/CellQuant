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
