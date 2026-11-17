"""The run log says how long the finished image took, not the zero seconds of the clock restarted for the next one."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("qtpy")

from cellquant.progress import TimeLeft


def _text(clock) -> str:
    from cellquant.gui.app import Footer

    return Footer.run_time_text(SimpleNamespace(_clock=clock))


def test_the_time_is_the_finished_images_even_though_the_clock_restarted():
    now = [100.0]
    clock = TimeLeft(lambda: now[0])
    now[0] = 153.0
    clock.image_finished(0, 1)  # as the run ends: the clock starts for a next image that never comes
    assert _text(clock) == " in 53 s"
    now[0] = 400.0
    assert _text(clock) == " in 53 s"  # and keeps saying so


def test_a_long_image_and_no_run():
    now = [0.0]
    clock = TimeLeft(lambda: now[0])
    now[0] = 252.0
    clock.image_finished(0, 1)
    assert _text(clock) == " in 4 min 12 s"
    assert _text(None) == ""


def test_before_any_image_has_finished_it_is_the_running_time():
    now = [10.0]
    clock = TimeLeft(lambda: now[0])
    now[0] = 17.0
    assert _text(clock) == " in 7 s"


def test_a_quick_image_is_not_reported_as_zero_seconds():
    now = [10.0]
    clock = TimeLeft(lambda: now[0])
    now[0] = 10.26
    assert _text(clock) == " in under 1 s"
    now[0] = 11.0
    assert _text(clock) == " in 1 s"
