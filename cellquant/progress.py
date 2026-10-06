"""Progress and cancelling for long analysis steps.

Analysis code calls ``update`` at each step ("Finding objects: slice 3 of
7"). The window's worker thread installs a handler with ``reporting`` to show
it, and a check that raises ``AnalysisCancelled`` when the user clicks
Cancel. Outside a worker (scripts, tests) both do nothing.

Handlers are per thread, so a batch in one thread never reports into another.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from cellquant.errors import CellQuantError


class AnalysisCancelled(CellQuantError):
    """The user clicked Cancel."""


_local = threading.local()


@contextmanager
def reporting(
    on_update: Callable[[str, float | None], None],
    is_cancelled: Callable[[], bool] | None = None,
) -> Iterator[None]:
    """Send updates from this thread to ``on_update(text, fraction)``.

    ``fraction`` is 0-1 within the current step, or None when the step's
    length is unknown (shown as a busy bar).
    """

    previous = getattr(_local, "handler", None)
    _local.handler = (on_update, is_cancelled)
    try:
        yield
    finally:
        _local.handler = previous


def update(text: str, done: float | None = None, total: float | None = None) -> None:
    """Report a step, then stop here if the user cancelled."""

    handler = getattr(_local, "handler", None)
    if handler is None:
        return
    on_update, _is_cancelled = handler
    fraction = None if done is None or not total else max(0.0, min(1.0, float(done) / float(total)))
    on_update(text, fraction)
    check_cancelled()


def check_cancelled() -> None:
    handler = getattr(_local, "handler", None)
    if handler is None:
        return
    _on_update, is_cancelled = handler
    if is_cancelled is not None and is_cancelled():
        raise AnalysisCancelled("Stopped. Nothing from the unfinished image was saved.")


class TimeLeft:
    """Estimates the time left in a run from timings measured during it.

    Finished images give the typical time per image. Inside the running
    image, a counted step ("slice 3 of 40") gives the time per slice, and
    finished images give the time spent after it (linking slices,
    measuring, saving). Estimates sharpen as the run goes.
    """

    def __init__(self, clock: Callable[[], float] | None = None):
        import time

        self._clock = clock or time.monotonic
        self.index = 1
        self.total = 1
        self._durations: list[float] = []  # seconds per finished image
        self._tails: list[float] = []  # seconds after the counted step, per finished image
        self._paused_at: float | None = None
        self.unit = "step"
        self._units = 0  # how many the counted step has ("slice 3 of 40": 40)
        self._start_image(self._clock())

    def _start_image(self, now: float) -> None:
        self._image_start = now
        self._counted: list | None = None  # [key, first time, first fraction, last time, last fraction]
        self._counted_end: float | None = None

    def image_started(self, index: int, total: int) -> None:
        self.index, self.total = index, max(total, 1)
        self._start_image(self._clock())

    def image_finished(self, index: int, total: int) -> None:
        now = self._clock()
        self.index, self.total = index + 1, max(total, 1)  # the next image starts now
        self._durations.append(now - self._image_start)
        if self._counted_end is not None:
            self._tails.append(now - self._counted_end)
        self._start_image(now)

    def step(self, text: str, fraction: float | None) -> None:
        """A step of the running image; ``fraction`` is None (or negative) when it is not counted."""

        import re

        now = self._clock()
        if fraction is None or fraction < 0:
            if self._counted is not None and self._counted_end is None:
                self._counted_end = now
            return
        key = re.sub(r"\d+", "#", text)
        unit = re.search(r"(\w+) \d+ of (\d+)", text)
        self.unit, self._units = (unit.group(1), int(unit.group(2))) if unit else ("step", 0)
        if self._counted is None or self._counted[0] != key or self._counted_end is not None:
            self._counted = [key, now, fraction, now, fraction]
            self._counted_end = None
        else:
            self._counted[3:] = [now, fraction]

    def pause(self) -> None:
        self._paused_at = self._clock()

    def resume(self) -> None:
        """Paused time is not counted as working time."""

        if self._paused_at is None:
            return
        gap = self._clock() - self._paused_at
        self._paused_at = None
        self._image_start += gap
        if self._counted is not None:
            self._counted[1] += gap
            self._counted[3] += gap
        if self._counted_end is not None:
            self._counted_end += gap

    def per_image(self) -> float | None:
        return sum(self._durations) / len(self._durations) if self._durations else None

    def _step_seconds(self) -> float | None:
        """Seconds the whole counted step takes in the running image, once two units have been timed."""

        counted = self._counted
        if counted is None or counted[4] <= counted[2] or counted[3] <= counted[1]:
            return None
        return (counted[3] - counted[1]) / (counted[4] - counted[2])

    def _image_left(self, now: float) -> float | None:
        typical = self.per_image()
        elapsed = now - self._image_start
        tail = sum(self._tails) / len(self._tails) if self._tails else None
        counted = self._counted
        if counted is not None and self._counted_end is None:
            rate = self._step_seconds()
            if rate is None:
                return None if typical is None else max(typical - elapsed, 0.0)
            end = counted[1] + rate * (1.0 - counted[2])
            if tail is None:
                # First image: the time after the counted step is not known yet; the time before it is the best guess.
                tail = counted[1] - self._image_start
            return max(end - now, 0.0) + tail
        if self._counted_end is not None and tail is not None:
            return max(tail - (now - self._counted_end), 0.0)
        return None if typical is None else max(typical - elapsed, 0.0)

    def seconds_left(self) -> float | None:
        """Seconds until the run finishes, or None while nothing has been timed yet."""

        if self.index > self.total:
            return 0.0
        now = self._paused_at if self._paused_at is not None else self._clock()
        left = self._image_left(now)
        if left is None:
            return None
        later = self.total - self.index
        if later > 0:
            typical = self.per_image()
            left += later * (typical if typical is not None else now - self._image_start + left)
        return left

    def detail(self) -> str:
        """How the estimate is made, for a tooltip."""

        lines = ["Time left, measured from this run; it sharpens as the run goes."]
        typical = self.per_image()
        if typical is not None:
            count = len(self._durations)
            lines.append(f"{_seconds(typical)} per image ({count} finished).")
        rate = self._step_seconds()
        if rate is not None and self._units:
            lines.append(f"{_seconds(rate / self._units)} per {self.unit} in this image.")
        if rate is not None and not self._tails and self._counted_end is None:
            lines.append(f"Time after the last {self.unit} (linking, measuring, saving) is a guess until the first image finishes.")
        return "\n".join(lines)


def _seconds(seconds: float) -> str:
    return f"{seconds:.1f} s" if seconds < 10 else f"{seconds:.0f} s" if seconds < 90 else f"{seconds / 60:.1f} min"
