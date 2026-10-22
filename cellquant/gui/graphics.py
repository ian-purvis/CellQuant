"""Switch the viewer to software graphics after a graphics driver error.

"Open CellQuant.bat" sets CELLQUANT_GRAPHICS_FILE. When the viewer hits an
OpenGL error (for example an NVIDIA "access violation" in glDrawArrays, or a
computer with no OpenGL 2 driver), CellQuant writes "software" to that file,
and the launcher starts the viewer with software OpenGL from then on. Later in a
session, a single error is only reported; repeated errors switch.
Running Install CellQuant.bat again clears the file.
"""

from __future__ import annotations

import os
import traceback
from pathlib import Path

# Exit code that tells the launcher to reopen CellQuant with software graphics.
GRAPHICS_EXIT_CODE = 75

_FILE_VARIABLE = "CELLQUANT_GRAPHICS_FILE"
_SOFTWARE_VARIABLE = "CELLQUANT_SOFTWARE_GRAPHICS"
# A graphics error this soon after the window opens restarts CellQuant at once.
_STARTUP_SECONDS = 60
# Later, this many separate errors switch the next start to software graphics.
_REPEATED_ERRORS = 3
_SAME_ERROR_SECONDS = 2.0
restart_requested = False


def software_graphics_on() -> bool:
    return os.environ.get(_SOFTWARE_VARIABLE) == "1"


def is_graphics_error(exc: BaseException) -> bool:
    """True for an error raised while the viewer draws with OpenGL."""

    files = [frame.filename.replace("\\", "/").lower() for frame in traceback.extract_tb(exc.__traceback__)]
    in_opengl = any("/vispy/gloo/" in name or "/vispy/app/backends/" in name or "/opengl/" in name for name in files)
    text = str(exc).lower()
    return (in_opengl and (isinstance(exc, OSError) or "gl" in text)) or "opengl" in text


def is_graphics_error_text(text: str) -> bool:
    """True for a traceback that vispy logged after an OpenGL error while drawing."""

    text = text.replace("\\", "/").lower()
    return "/vispy/gloo/" in text and ("access violation" in text or "opengl" in text)


def remember_software_graphics() -> bool:
    """Ask the launcher for software graphics next time. False when it cannot (not started by it, or already on)."""

    path = os.environ.get(_FILE_VARIABLE)
    if not path or software_graphics_on():
        return False
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("software\n", encoding="ascii")
    except OSError:
        return False
    return True


def drawing_works(viewer) -> bool:
    """Draw a small test image once. False when OpenGL cannot draw it (for example no OpenGL 2 driver)."""

    import logging
    import sys

    import numpy as np

    failed = []

    class _VispyErrors(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if is_graphics_error_text(record.getMessage()):
                failed.append(record)

    def hook(kind, exc, tb) -> None:
        # Qt calls this for errors while painting; the default hook would end the program.
        exc.__traceback__ = tb
        if is_graphics_error(exc):
            failed.append(exc)
        else:
            sys.__excepthook__(kind, exc, tb)

    handler = _VispyErrors(logging.WARNING)
    logging.getLogger("vispy").addHandler(handler)
    previous_hook, sys.excepthook = sys.excepthook, hook
    layer = None
    try:
        layer = viewer.add_image(np.zeros((8, 8), dtype=np.uint8), name="graphics check")
        viewer.screenshot(canvas_only=True, flash=False)
    except Exception as exc:  # noqa: BLE001
        if not is_graphics_error(exc):
            raise
        failed.append(exc)
    finally:
        sys.excepthook = previous_hook
        logging.getLogger("vispy").removeHandler(handler)
        if layer is not None and layer in viewer.layers:
            viewer.layers.remove(layer)
    return not failed


def watch_viewer_errors(app) -> None:
    """Act on graphics errors after the window opens.

    Shortly after opening, nothing can be lost yet: CellQuant closes, and the
    launcher reopens it with software graphics. Later, one error is often a
    driver hiccup while Cellpose keeps the same GPU busy, and drawing recovers:
    the user is told the run goes on. Only repeated errors switch the next
    start to software graphics.
    """

    import logging
    import time

    from napari.utils.notifications import notification_manager, show_warning
    from qtpy.QtCore import QTimer

    global restart_requested
    opened = time.monotonic()
    errors: list[float] = []
    warned = False
    done = False

    def handle() -> None:
        nonlocal warned, done
        global restart_requested
        if done:
            return
        now = time.monotonic()
        if now - opened < _STARTUP_SECONDS:
            if not remember_software_graphics():
                return
            done = True
            print("CellQuant: graphics error at startup. Reopening with software graphics.", flush=True)
            restart_requested = True
            QTimer.singleShot(0, app.quit)
            return
        # vispy logs an error and napari reports it too: one paint counts once.
        if errors and now - errors[-1] < _SAME_ERROR_SECONDS:
            return
        errors.append(now)
        if len(errors) >= _REPEATED_ERRORS:
            done = True
            if remember_software_graphics():
                print("CellQuant: repeated graphics driver errors. The next start will use software graphics.", flush=True)
                show_warning(
                    "Graphics driver errors keep happening. CellQuant will use software graphics from the next start. "
                    "A run keeps going and saves each image as it finishes. When it is done, record your edits, then close and reopen CellQuant."
                )
            else:
                show_warning(
                    "Graphics driver errors keep happening. A run keeps going and saves each image as it finishes. "
                    "When it is done, record your edits, then close and reopen CellQuant."
                )
            return
        if not warned:
            warned = True
            print("CellQuant: graphics driver error while drawing the viewer.", flush=True)
            show_warning(
                "The graphics driver failed to draw the viewer once. This can happen while Cellpose keeps the same GPU busy. "
                "A run keeps going and saves each image as it finishes."
            )

    def check_notification(notification) -> None:
        exc = getattr(notification, "exception", None)
        if exc is not None and is_graphics_error(exc):
            handle()

    class _VispyErrors(logging.Handler):
        # vispy logs drawing errors instead of raising them.
        def emit(self, record: logging.LogRecord) -> None:
            try:
                if is_graphics_error_text(record.getMessage()):
                    handle()
            except Exception:  # noqa: BLE001
                pass

    restart_requested = False
    notification_manager.notification_ready.connect(check_notification)
    logging.getLogger("vispy").addHandler(_VispyErrors(logging.WARNING))
