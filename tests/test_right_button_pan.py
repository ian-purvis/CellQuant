"""Hold the right mouse button and drag: the view moves, the canvas (and so every tool and the camera's own zoom) never sees the right button."""

from types import SimpleNamespace

import numpy as np
import pytest

from cellquant.gui.app import pan_camera


def test_pan_moves_camera_by_the_cursor_shift():
    viewer = SimpleNamespace(camera=SimpleNamespace(center=(0.0, 100.0, 200.0)))
    pan_camera(viewer, (0, 50.0, 60.0), (0, 40.0, 75.0), [1, 2])
    assert np.allclose(viewer.camera.center, (0.0, 110.0, 185.0))


def test_pan_with_a_two_value_camera_centre():
    viewer = SimpleNamespace(camera=SimpleNamespace(center=(100.0, 200.0)))
    pan_camera(viewer, (3, 50.0, 60.0), (3, 40.0, 75.0), [1, 2])
    assert np.allclose(viewer.camera.center, (110.0, 185.0))


@pytest.fixture
def canvas():
    """A stand-in canvas widget that records what reaches it, with a 1:1 canvas-to-world map that follows the camera."""

    qtwidgets = pytest.importorskip("qtpy.QtWidgets")
    app = qtwidgets.QApplication.instance() or qtwidgets.QApplication([])

    class Canvas(qtwidgets.QWidget):
        def __init__(self):
            super().__init__()
            self.seen = []

        def mousePressEvent(self, event):
            self.seen.append(("press", event.button()))

        def mouseMoveEvent(self, event):
            self.seen.append(("move", event.buttons()))

        def mouseReleaseEvent(self, event):
            self.seen.append(("release", event.button()))

    native = Canvas()
    native.resize(200, 200)
    native.setMouseTracking(True)  # hover moves reach it, as on the real canvas
    vispy_canvas = SimpleNamespace(
        native=native,
        view="view",
        _get_viewbox_at=lambda pos: ("view", (0, 0)),
    )
    viewer = SimpleNamespace(
        camera=SimpleNamespace(center=(0.0, 100.0, 100.0)),
        dims=SimpleNamespace(displayed=(1, 2)),
        window=SimpleNamespace(_qt_viewer=SimpleNamespace(canvas=vispy_canvas)),
    )

    def to_world(pos, view):
        _, y, x = viewer.camera.center
        return (0.0, float(pos[1]) + y - 100.0, float(pos[0]) + x - 100.0)

    vispy_canvas._map_canvas2world = to_world
    yield viewer, native
    native.deleteLater()
    app.processEvents()


def _send(native, kind, x, y, button, held):
    from qtpy.QtCore import QEvent, QPointF, Qt
    from qtpy.QtGui import QMouseEvent
    from qtpy.QtWidgets import QApplication

    point = QPointF(x, y)
    QApplication.sendEvent(native, QMouseEvent(getattr(QEvent.Type, kind), point, point, button, held, Qt.KeyboardModifier.NoModifier))


def test_right_drag_pans_and_the_canvas_never_sees_it(canvas):
    from qtpy.QtCore import Qt

    from cellquant.gui.app import bind_right_button_pan

    viewer, native = canvas
    bind_right_button_pan(viewer)
    right, none = Qt.MouseButton.RightButton, Qt.MouseButton.NoButton
    _send(native, "MouseButtonPress", 50, 60, right, right)
    _send(native, "MouseMove", 70, 65, none, right)
    _send(native, "MouseMove", 75, 40, none, right)
    _send(native, "MouseButtonRelease", 75, 40, right, none)
    assert native.seen == []
    assert np.allclose(viewer.camera.center, (0.0, 120.0, 75.0))  # the point grabbed stays under the cursor

    # A plain right click: nothing reaches the canvas and nothing moves.
    _send(native, "MouseButtonPress", 50, 60, right, right)
    _send(native, "MouseButtonRelease", 50, 60, right, none)
    _send(native, "MouseButtonDblClick", 50, 60, right, right)
    assert native.seen == [] and np.allclose(viewer.camera.center, (0.0, 120.0, 75.0))

    # The left button and plain hovering are untouched.
    left = Qt.MouseButton.LeftButton
    _send(native, "MouseMove", 10, 10, none, none)
    _send(native, "MouseButtonPress", 10, 10, left, left)
    _send(native, "MouseMove", 30, 30, none, left)
    _send(native, "MouseButtonRelease", 30, 30, left, none)
    assert [kind for kind, _ in native.seen] == ["move", "press", "move", "release"]
    assert np.allclose(viewer.camera.center, (0.0, 120.0, 75.0))


def test_binds_once(canvas):
    from cellquant.gui.app import RightButtonPan, bind_right_button_pan

    viewer, native = canvas
    bind_right_button_pan(viewer)
    bind_right_button_pan(viewer)
    assert len(native.findChildren(RightButtonPan)) == 1
