"""Hold the right mouse button and drag: the view moves, left clicks and the data are untouched."""

from types import SimpleNamespace

import numpy as np

from cellquant.gui.app import bind_right_button_pan, pan_with_right_button


def _drag(viewer, button, positions, dims=(1, 2)):
    event = SimpleNamespace(button=button, type="mouse_press", position=positions[0], dims_displayed=list(dims))
    gen = pan_with_right_button(viewer, event)
    try:
        next(gen)
    except StopIteration:
        return
    for pos in positions[1:]:
        event.type, event.position = "mouse_move", pos
        next(gen)
    event.type = "mouse_release"
    try:
        next(gen)
    except StopIteration:
        pass


def test_right_drag_moves_camera_by_the_cursor_shift():
    viewer = SimpleNamespace(camera=SimpleNamespace(center=(0.0, 100.0, 200.0)))
    _drag(viewer, 2, [(0, 50.0, 60.0), (0, 40.0, 75.0)])
    assert np.allclose(viewer.camera.center, (0.0, 110.0, 185.0))


def test_left_button_and_plain_right_click_do_not_move():
    viewer = SimpleNamespace(camera=SimpleNamespace(center=(0.0, 100.0, 200.0)))
    _drag(viewer, 1, [(0, 50.0, 60.0), (0, 40.0, 75.0)])
    _drag(viewer, 2, [(0, 50.0, 60.0)])
    assert viewer.camera.center == (0.0, 100.0, 200.0)


def test_binds_once():
    viewer = SimpleNamespace(mouse_drag_callbacks=[])
    bind_right_button_pan(viewer)
    bind_right_button_pan(viewer)
    assert viewer.mouse_drag_callbacks == [pan_with_right_button]
