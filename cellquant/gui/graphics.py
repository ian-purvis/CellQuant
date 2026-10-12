"""Switch the viewer to software graphics after a graphics driver error.

"Open CellQuant.bat" sets CELLQUANT_GRAPHICS_FILE. When the viewer hits an
OpenGL error (for example an NVIDIA "access violation" in glDrawArrays, or a
computer with no OpenGL 2 driver), CellQuant writes "software" to that file,
and the launcher starts the viewer with software OpenGL from then on.
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


def watch_viewer_errors(app) -> None:
    """Act on the first graphics error after the window opens.

    Shortly after opening, nothing can be lost yet: CellQuant closes, and the
    launcher reopens it with software graphics. Later, the user is asked to
    record their edits and reopen CellQuant.
    """

    import logging
    import time

    from napari.utils.notifications import notification_manager, show_warning
    from qtpy.QtCore import QTimer

    global restart_requested
    opened = time.monotonic()
    done = False

    def handle() -> None:
        nonlocal done
        global restart_requested
        if done or not remember_software_graphics():
            return
        done = True
        if time.monotonic() - opened < _STARTUP_SECONDS:
            print("CellQuant: graphics error at startup. Reopening with software graphics.", flush=True)
            restart_requested = True
            QTimer.singleShot(0, app.quit)
            return
        print("CellQuant: graphics driver error. The next start will use software graphics.", flush=True)
        show_warning(
            "Graphics driver error. CellQuant will use software graphics from the next start. "
            "Record your edits, then close and reopen CellQuant."
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
