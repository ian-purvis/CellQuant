"""Stage cache.

Segmentation and measurement are stored separately. A bad cache file is
removed so the next request recomputes that stage.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from cellquant.__version__ import __version__


def stage_key(stage: str, payload: dict[str, Any]) -> str:
    body = {
        "stage": stage,
        "software_version": __version__,
        "payload": payload,
    }
    text = json.dumps(body, sort_keys=True, default=_json_default, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class StageCache:
    def __init__(self, root: str | Path | None):
        self.root = None if root is None else Path(root)
        self._memory_labels: dict[str, np.ndarray] = {}
        self._memory_tables: dict[str, pd.DataFrame] = {}
        if self.root is not None:
            (self.root / "labels").mkdir(parents=True, exist_ok=True)
            (self.root / "tables").mkdir(parents=True, exist_ok=True)
            (self.root / "meta").mkdir(parents=True, exist_ok=True)

    def get_labels(self, key: str) -> np.ndarray | None:
        if key in self._memory_labels:
            return self._memory_labels[key].copy()
        path = self._label_path(key)
        if path is None or not path.is_file():
            return None
        try:
            labels = np.load(path)
        except Exception:
            _discard(path)
            return None
        if labels.ndim not in (2, 3) or not np.issubdtype(labels.dtype, np.integer):
            _discard(path)
            return None
        self._memory_labels[key] = labels
        return labels.copy()

    def put_labels(self, key: str, labels: np.ndarray) -> None:
        stored = np.array(labels, dtype=np.int32, copy=True)
        self._memory_labels[key] = stored
        path = self._label_path(key)
        if path is None:
            return
        _atomic_numpy(path, stored)

    def get_table(self, key: str) -> pd.DataFrame | None:
        if key in self._memory_tables:
            return self._memory_tables[key].copy()
        path = self._table_path(key)
        if path is None or not path.is_file():
            return None
        try:
            table = pd.read_csv(path)
        except Exception:
            _discard(path)
            return None
        if "object_id" not in table.columns:
            _discard(path)
            return None
        self._memory_tables[key] = table
        return table.copy()

    def get_meta(self, key: str) -> dict | None:
        path = self._meta_path(key)
        if path is None or not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            _discard(path)
            return None

    def put_meta(self, key: str, payload: dict) -> None:
        path = self._meta_path(key)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")
        temporary.replace(path)

    def put_table(self, key: str, table: pd.DataFrame) -> None:
        stored = table.copy()
        self._memory_tables[key] = stored
        path = self._table_path(key)
        if path is None:
            return
        _atomic_csv(path, stored)

    def _label_path(self, key: str) -> Path | None:
        if self.root is None:
            return None
        return self.root / "labels" / f"{key}.npy"

    def _table_path(self, key: str) -> Path | None:
        if self.root is None:
            return None
        return self.root / "tables" / f"{key}.csv"

    def _meta_path(self, key: str) -> Path | None:
        if self.root is None:
            return None
        return self.root / "meta" / f"{key}.json"


def _atomic_numpy(path: Path, labels: np.ndarray) -> None:
    temporary = path.parent / f".{path.stem}.tmp.npy"
    try:
        np.save(temporary, labels)
        temporary.replace(path)
    except Exception:
        _discard(temporary)
        raise


def _atomic_csv(path: Path, table: pd.DataFrame) -> None:
    temporary = path.with_suffix(".csv.tmp")
    try:
        table.to_csv(temporary, index=False)
        temporary.replace(path)
    except Exception:
        _discard(temporary)
        raise


def _discard(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return


def _json_default(value: Any):
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
