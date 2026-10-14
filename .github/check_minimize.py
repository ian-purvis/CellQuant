"""Windows check: a popped-out CellQuant panel stays visible when the napari window is minimized."""
import ctypes, sys, time, tempfile
from pathlib import Path

import napari
from qtpy.QtWidgets import QApplication

from cellquant.controller import AnalysisController
import cellquant.gui.app as app

control = "--control" in sys.argv
if control:
    app.own_window = lambda dock: None  # old behaviour, to show the bug reproduces here

user32 = ctypes.windll.user32
user32.GetWindow.restype = ctypes.c_void_p
GW_OWNER = 4


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.02)


tmp = Path(tempfile.mkdtemp())
AnalysisController.create(tmp / "experiment", "Check").save()
viewer = napari.Viewer()
shell = app.CellQuantWindow(viewer, tmp / "experiment")
main = viewer.window._qt_window
pump(2)
header = shell._floating_headers[0]
dock = header.dock
dock.setFloating(True)
pump(2)
hwnd = int(dock.winId())
print("header applied:", dock.titleBarWidget() is header)
print("owner:", user32.GetWindow(hwnd, GW_OWNER))
print("visible before:", bool(user32.IsWindowVisible(hwnd)))
main.showMinimized()
pump(2)
print("napari minimized:", bool(user32.IsIconic(int(main.winId()))))
stays = bool(user32.IsWindowVisible(hwnd)) and not user32.IsIconic(hwnd)
print("panel visible while napari minimized:", stays)
main.showNormal()
pump(2)
print("panel visible after napari restored:", bool(user32.IsWindowVisible(hwnd)))
header.buttons["reset"].click()
pump(1)
docked = not dock.isFloating() and dock.titleBarWidget() is not header
print("Reset docks it back:", docked)
dock.setFloating(True)
pump(2)
again = dock.titleBarWidget() is header and user32.GetWindow(int(dock.winId()), GW_OWNER) in (None, 0)
print("floats again unowned:", again)
viewer.close()
ok = stays and docked and (control or again)
if control:
    sys.exit(0 if not stays else 1)  # control must reproduce the bug
sys.exit(0 if ok else 1)
