"""Keep the computer from sleeping while an analysis runs.

Sleep pauses every program, so a long batch would stop until someone wakes the
computer. While held, the system and the screen stay on: on many Windows laptops
(Modern Standby) the computer sleeps as soon as the screen turns off, so keeping
only the system awake is not enough. Windows uses SetThreadExecutionState, macOS
runs ``caffeinate``; elsewhere this does nothing.
"""

from __future__ import annotations

import os
import subprocess
import sys

_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_DISPLAY_REQUIRED = 0x00000002


def supported() -> bool:
    return sys.platform == "win32" or sys.platform == "darwin"


class KeepAwake:
    """``hold()`` blocks system sleep until ``release()``. Both are safe to call repeatedly.

    On Windows the request belongs to the calling thread, so call both from the same one
    (the interface thread).
    """

    def __init__(self) -> None:
        self._held = False
        self._process: subprocess.Popen | None = None

    @property
    def held(self) -> bool:
        return self._held

    def hold(self) -> None:
        if self._held:
            return
        try:
            if sys.platform == "win32":
                import ctypes

                # Returns the previous state, which is 0 the first time: not a failure.
                ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_DISPLAY_REQUIRED)
            elif sys.platform == "darwin":
                # -i: no idle sleep; -d: screen stays on; -w: ends by itself if CellQuant closes.
                self._process = subprocess.Popen(["caffeinate", "-i", "-d", "-w", str(os.getpid())])
            else:
                return
        except Exception:  # noqa: BLE001 - never let this stop a run
            return
        self._held = True

    def release(self) -> None:
        if not self._held:
            return
        self._held = False
        try:
            if sys.platform == "win32":
                import ctypes

                ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
            elif self._process is not None:
                self._process.terminate()
                self._process.wait(timeout=5)
        except Exception:  # noqa: BLE001, S110 - the run is over either way
            pass
        self._process = None
