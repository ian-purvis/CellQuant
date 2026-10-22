"""Let two or more CellQuant windows run on one computer at the same time.

An experiment folder is open in one window at a time: the window holds a lock on a small file in
the folder. Every CellQuant program that runs Cellpose holds a lock on its own file in
%LOCALAPPDATA%\\CellQuant\\instances, so the others can count it and share the GPU memory and CPU
cores with it. The operating system releases these locks when a program ends, even after a crash,
so a lock never outlives its window.
"""

from __future__ import annotations

import errno
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

FOLDER_LOCK_NAME = ".cellquant.lock"
# The errors that mean another program holds the lock; anything else means this disk cannot lock.
_HELD = {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK, errno.EDEADLK}
# This program's own file in each instances folder it has used.
_REGISTERED: dict[Path, tuple[Path, int]] = {}


def _try_lock(descriptor: int) -> bool | None:
    """Lock an open file without waiting: True when locked, False when another program holds it,
    None when the disk cannot lock files (some network drives)."""

    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        return False if exc.errno in _HELD else None
    return True


def _unlock(descriptor: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError:
        pass
    os.close(descriptor)


def _open(path: Path) -> int | None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError:
        return None


class FolderLock:
    """This window's hold on an experiment folder; released by ``release()`` or when the program ends."""

    def __init__(self, directory: Path, descriptor: int | None):
        self.directory = directory
        self._descriptor = descriptor

    def covers(self, directory: str | Path) -> bool:
        return self._descriptor is not None and _same(self.directory, Path(directory))

    def release(self) -> None:
        if self._descriptor is not None:
            _unlock(self._descriptor)
            self._descriptor = None


def _same(first: Path, second: Path) -> bool:
    try:
        return os.path.normcase(str(first.resolve())) == os.path.normcase(str(second.resolve()))
    except OSError:
        return False


def lock_folder(directory: str | Path, create: bool = False) -> FolderLock | None:
    """Hold ``directory`` for this window, or None when another CellQuant window has it open.

    ``create`` makes the folder when it does not exist yet. A folder that is missing or cannot hold
    a lock file (read-only, or a drive without file locks) is not refused: the lock then holds nothing.
    """

    directory = Path(directory)
    if not create and not directory.is_dir():
        return FolderLock(directory, None)
    descriptor = _open(directory / FOLDER_LOCK_NAME)
    if descriptor is None:
        return FolderLock(directory, None)
    locked = _try_lock(descriptor)
    if locked is False:
        os.close(descriptor)
        return None
    if locked is None:
        os.close(descriptor)
        return FolderLock(directory, None)
    return FolderLock(directory, descriptor)


def instances_folder() -> Path:
    from cellquant.hardware import settings_path

    return settings_path().parent / "instances"


def other_instances() -> int:
    """How many other CellQuant programs on this computer run Cellpose (this one counts itself from now on).

    Files left by programs that ended are removed.
    """

    folder = instances_folder()
    own = _REGISTERED.get(folder)
    if own is None:
        path = folder / f"{os.getpid()}-{uuid.uuid4().hex[:8]}.lock"
        descriptor = _open(path)
        if descriptor is None or not _try_lock(descriptor):
            if descriptor is not None:
                os.close(descriptor)
                try:
                    path.unlink()
                except OSError:
                    pass
            return 0  # cannot take part: assume this program is alone, as before
        own = _REGISTERED[folder] = (path, descriptor)
    others = 0
    try:
        entries = list(folder.glob("*.lock"))
    except OSError:
        return 0
    for path in entries:
        if path == own[0]:
            continue
        descriptor = _open(path)
        if descriptor is None:
            continue
        locked = _try_lock(descriptor)
        if locked is False:
            others += 1
            os.close(descriptor)
            continue
        if locked:
            _unlock(descriptor)
            try:
                path.unlink()  # its program has ended
            except OSError:
                pass
        else:
            os.close(descriptor)
    return others


@contextmanager
def file_lock(path: Path, wait: float = 5.0):
    """Hold ``path`` locked while the block runs, waiting up to ``wait`` seconds for another program.

    After that (or on a disk without file locks) the block runs anyway: a slow window must not stop this one.
    """

    descriptor = _open(path)
    locked = False
    if descriptor is not None:
        deadline = time.monotonic() + wait
        while True:
            state = _try_lock(descriptor)
            if state is not False or time.monotonic() >= deadline:
                locked = bool(state)
                break
            time.sleep(0.05)
    try:
        yield
    finally:
        if locked:
            _unlock(descriptor)
        elif descriptor is not None:
            os.close(descriptor)
