"""Results kept for the session, with their object masks moved out of memory when many are open.

Every measured image keeps its result (tables, checks, settings) for the whole session, but the
mask arrays are the large part (about 8 MB per 1024 x 1024 image, far more for a Z-stack). Only
the masks of the most recently used images stay in memory; older ones are written to a temporary
file and read back, unchanged, the next time they are used. Nothing about the result changes.
"""

from __future__ import annotations

import atexit
import shutil
import tempfile
import threading
import uuid
import weakref
from collections import OrderedDict
from pathlib import Path

import numpy as np

# Masks of the most recently used images kept in memory, and how many are always kept.
LABELS_IN_MEMORY_BYTES = 512 * 1024**2
LABELS_ALWAYS_KEPT = 3

_folder: Path | None = None
_folder_lock = threading.Lock()


def _spill_folder() -> Path:
    global _folder
    with _folder_lock:
        if _folder is None or not _folder.is_dir():
            _folder = Path(tempfile.mkdtemp(prefix="cellquant-masks-"))
            atexit.register(shutil.rmtree, _folder, True)
        return _folder


class SpilledLabels:
    """A mask array written to a temporary file. Shared by a result's two masks when they are the
    same array, so they come back as one array. The file is removed when nothing refers to it."""

    def __init__(self, array: np.ndarray):
        self.path = _spill_folder() / f"{uuid.uuid4().hex}.npy"
        np.save(self.path, array, allow_pickle=False)
        self._back = None
        self._lock = threading.Lock()
        weakref.finalize(self, _remove, self.path)

    def load(self) -> np.ndarray:
        with self._lock:
            array = self._back() if self._back is not None else None
            if array is None:
                array = np.load(self.path, allow_pickle=False)
                self._back = weakref.ref(array)
            return array


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def labels_in_memory(result) -> list[np.ndarray]:
    """The result's mask arrays that are in memory (each array once)."""

    arrays = []
    for name in ("_labels", "_automated_labels"):
        value = result.__dict__.get(name)
        if isinstance(value, np.ndarray) and not any(value is other for other in arrays):
            arrays.append(value)
    return arrays


def spill_labels(result) -> None:
    """Move the result's masks out of memory (see ``SpilledLabels``)."""

    spilled: dict[int, SpilledLabels] = {}
    for name in ("_labels", "_automated_labels"):
        value = result.__dict__.get(name)
        if isinstance(value, np.ndarray):
            if id(value) not in spilled:
                spilled[id(value)] = SpilledLabels(value)
            result.__dict__[name] = spilled[id(value)]


class ResultCache(OrderedDict):
    """Image id -> result, most recently used last. Storing or reading a result marks it used; when
    the masks in memory pass ``LABELS_IN_MEMORY_BYTES``, the least recently used results' masks are
    moved out of memory (the newest ``LABELS_ALWAYS_KEPT`` never are). Safe to use from a worker thread."""

    def __init__(self, *args, **kwargs):
        self._lock = threading.RLock()
        super().__init__(*args, **kwargs)

    def __setitem__(self, key, value) -> None:
        with self._lock:
            super().__setitem__(key, value)
            self.move_to_end(key)
            self._limit()

    def __getitem__(self, key):
        with self._lock:
            value = super().__getitem__(key)
            self.move_to_end(key)
            return value

    def get(self, key, default=None):
        with self._lock:
            if key not in self:
                return default
            return self[key]

    def trim(self) -> None:
        """Move masks out of memory again if reading results brought too many back."""

        with self._lock:
            self._limit()

    def _limit(self) -> None:
        results = list(self.values())
        total = sum(array.nbytes for result in results for array in labels_in_memory(result))
        for result in results[: max(len(results) - LABELS_ALWAYS_KEPT, 0)]:
            if total <= LABELS_IN_MEMORY_BYTES:
                return
            freed = sum(array.nbytes for array in labels_in_memory(result))
            if freed:
                spill_labels(result)
                total -= freed
