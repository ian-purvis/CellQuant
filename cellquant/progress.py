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
