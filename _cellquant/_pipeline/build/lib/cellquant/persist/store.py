"""Persistence primitives for reproducible and resumable pipeline runs.

The completion marker is deliberately written last.  It contains the digest
and label metadata needed to validate every artifact without trusting file
names or a previous process' in-memory state.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import tifffile

from cellquant.config import RunConfig
from cellquant.contracts import LabelVolume, PipelineEvent
from cellquant.persist.atomic import replace_with_retry
from cellquant.persist.staging import publish_directory, resolve_work_directory


_STATUS_NAME = "status.json"
_LABELS_NAME = "labels.tif"
_CONFIG_NAME = "config.json"
_PROVENANCE_NAME = "provenance.json"
_EVENTS_NAME = "events.jsonl"
_REQUIRED_SINGLETON_ROLES = {"labels", "config", "provenance", "event_log"}
_REQUIRED_COLLECTION_ROLES = {"measurement", "qc"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        replace_with_retry(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _json_bytes(value: Any, *, canonical: bool = False) -> bytes:
    if canonical:
        text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    else:
        text = json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False)
    return (text + "\n").encode("utf-8")


def _event_value(event: PipelineEvent) -> dict[str, Any]:
    if not isinstance(event, PipelineEvent):
        raise TypeError("event must be a PipelineEvent")
    return dataclasses.asdict(event)


class RunStore:
    """One output directory whose completed state is independently verifiable."""

    def __init__(
        self,
        directory: str | Path,
        input_fingerprint: str,
        config_fingerprint: str,
        run_id: str | None = None,
        *,
        publish_to: str | Path | None = None,
    ) -> None:
        if not input_fingerprint or not config_fingerprint:
            raise ValueError("input and config fingerprints must be non-empty")
        self.directory = Path(directory)
        self.input_fingerprint = str(input_fingerprint)
        self.config_fingerprint = str(config_fingerprint)
        self.run_id = run_id
        self.publish_to = Path(publish_to) if publish_to is not None else None
        self._artifacts: dict[str, str] = {}
        self._event_lock = threading.Lock()
        # RunConfig may opt into TIFF compression. Until a config is written,
        # the documented compatibility behavior is an uncompressed TIFF.
        self._output_compression: str | None = None

    @classmethod
    def create(
        cls,
        directory: str | Path,
        input_fingerprint: str,
        config_fingerprint: str,
        *,
        run_id: str | None = None,
        publish_to: str | Path | None = None,
    ) -> "RunStore":
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        store = cls(
            directory,
            input_fingerprint,
            config_fingerprint,
            run_id,
            publish_to=publish_to,
        )
        # Invalidate any prior completion marker before touching its event log.
        # A caller may deliberately reuse an output directory for a new run;
        # stale completion metadata must never make that new run resumable.
        _atomic_bytes(
            store.status_path,
            _json_bytes(
                {
                    "schema_version": 1,
                    "status": "running",
                    "run_id": store.run_id,
                    "input_fingerprint": store.input_fingerprint,
                    "config_fingerprint": store.config_fingerprint,
                }
            ),
        )
        # Every create starts a new atomic JSONL stream. Preserving a previous
        # run's events would mix run IDs and make provenance ambiguous.
        _atomic_bytes(store.events_path, b"")
        store._artifacts["event_log"] = _EVENTS_NAME
        return store

    @classmethod
    def create_for_user_output(
        cls,
        user_output: str | Path,
        input_fingerprint: str,
        config_fingerprint: str,
        *,
        run_id: str | None = None,
    ) -> "RunStore":
        """Create a store, staging locally when ``user_output`` is cloud-synced."""

        work_dir, publish_to = resolve_work_directory(user_output)
        return cls.create(
            work_dir,
            input_fingerprint,
            config_fingerprint,
            run_id=run_id,
            publish_to=publish_to,
        )

    @property
    def labels_path(self) -> Path:
        return self.directory / _LABELS_NAME

    @property
    def events_path(self) -> Path:
        return self.directory / _EVENTS_NAME

    @property
    def status_path(self) -> Path:
        return self.directory / _STATUS_NAME

    def write_labels(self, labels: LabelVolume) -> Path:
        if not isinstance(labels, LabelVolume):
            raise TypeError("labels must be a LabelVolume")
        array = np.asarray(labels.data)
        maximum = int(array.max(initial=0))
        disk_dtype = np.uint16 if maximum <= np.iinfo(np.uint16).max else np.uint32
        destination = self.labels_path
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            # Explicit photometric metadata prevents short Z stacks from being
            # interpreted as RGB samples by TIFF readers.
            tifffile.imwrite(
                temporary,
                array.astype(disk_dtype, copy=False),
                photometric="minisblack",
                metadata={"axes": "ZYX"},
                compression=self._output_compression,
            )
            readback = np.asarray(tifffile.imread(temporary))
            if (
                tuple(readback.shape) != tuple(array.shape)
                or readback.dtype != np.dtype(disk_dtype)
                or int(readback.max(initial=0)) != maximum
                or not np.array_equal(readback.astype(np.uint32, copy=False), array)
            ):
                raise OSError("label TIFF failed lossless write/readback validation")
            replace_with_retry(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        self._artifacts["labels"] = _LABELS_NAME
        return destination

    def read_labels(self) -> np.ndarray:
        """Read persisted labels in the canonical uint32 representation."""
        value = np.asarray(tifffile.imread(self.labels_path))
        if value.ndim != 3 or value.dtype not in (np.dtype(np.uint16), np.dtype(np.uint32)):
            raise ValueError("persisted labels must be a 3D uint16 or uint32 TIFF")
        return value.astype(np.uint32, copy=False)

    def write_config(self, config: RunConfig) -> Path:
        if not isinstance(config, RunConfig):
            raise TypeError("config must be a RunConfig")
        if config.fingerprint != self.config_fingerprint:
            raise ValueError("RunConfig fingerprint does not match this run")
        compression = config.raw["runtime"].get("output_compression")
        if compression == "none":
            compression = None
        if compression is not None and not isinstance(compression, str):
            raise ValueError("runtime.output_compression must be a TIFF compression name or null")
        if compression is not None and "labels" in self._artifacts:
            raise RuntimeError(
                "write_config must precede write_labels when output compression is configured"
            )
        self._output_compression = compression
        path = self.directory / _CONFIG_NAME
        _atomic_bytes(path, (config.canonical_json() + "\n").encode("utf-8"))
        self._artifacts["config"] = _CONFIG_NAME
        return path

    def write_provenance(self, provenance: Mapping[str, Any]) -> Path:
        if not isinstance(provenance, Mapping):
            raise TypeError("provenance must be a mapping")
        path = self.directory / _PROVENANCE_NAME
        _atomic_bytes(path, _json_bytes(dict(provenance)))
        self._artifacts["provenance"] = _PROVENANCE_NAME
        return path

    def append_event(self, event: PipelineEvent) -> Path:
        encoded = _json_bytes(_event_value(event), canonical=True)
        # Rewrite-and-replace makes each append atomic. Runs normally have a
        # modest event stream, so durability is preferable to in-place append.
        with self._event_lock:
            previous = self.events_path.read_bytes() if self.events_path.exists() else b""
            _atomic_bytes(self.events_path, previous + encoded)
        self._artifacts["event_log"] = _EVENTS_NAME
        return self.events_path

    def register_artifact(
        self,
        source: str | Path,
        *,
        role: str,
        name: str | None = None,
    ) -> Path:
        """Atomically place and register a measurement or QC artifact."""
        if role not in _REQUIRED_COLLECTION_ROLES:
            raise ValueError("registered artifact role must be 'measurement' or 'qc'")
        source = Path(source)
        if not source.is_file() or source.stat().st_size <= 0:
            raise ValueError("registered artifact must be a non-empty file")
        filename = name or source.name
        if Path(filename).name != filename or filename in {
            _STATUS_NAME, _LABELS_NAME, _CONFIG_NAME, _PROVENANCE_NAME, _EVENTS_NAME
        }:
            raise ValueError("artifact name must be a safe, non-reserved basename")
        destination = self.directory / filename
        if source.resolve() != destination.resolve():
            temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
            try:
                with source.open("rb") as reader, temporary.open("xb") as writer:
                    shutil.copyfileobj(reader, writer)
                    writer.flush()
                    os.fsync(writer.fileno())
                replace_with_retry(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        self._artifacts[f"{role}:{filename}"] = filename
        return destination

    def register_measurement(self, source: str | Path, *, name: str | None = None) -> Path:
        return self.register_artifact(source, role="measurement", name=name)

    def register_qc_artifact(self, source: str | Path, *, name: str | None = None) -> Path:
        return self.register_artifact(source, role="qc", name=name)

    def commit(self, status: str = "complete") -> Path:
        if status != "complete":
            _atomic_bytes(
                self.status_path,
                _json_bytes(
                    {
                        "schema_version": 1,
                        "status": status,
                        "run_id": self.run_id,
                        "input_fingerprint": self.input_fingerprint,
                        "config_fingerprint": self.config_fingerprint,
                    }
                ),
            )
            # Best-effort publish of failed/cancelled markers to the UI folder.
            try:
                self._publish_if_needed()
            except OSError:
                pass
            return self.status_path

        self._validate_before_commit()
        artifacts = []
        for key, filename in sorted(self._artifacts.items()):
            role = key.split(":", 1)[0]
            path = self.directory / filename
            artifacts.append(
                {"name": filename, "role": role, "size": path.stat().st_size, "sha256": _sha256(path)}
            )
        labels = self.read_labels()
        marker = {
            "schema_version": 1,
            "status": "complete",
            "run_id": self.run_id,
            "input_fingerprint": self.input_fingerprint,
            "config_fingerprint": self.config_fingerprint,
            "label_shape": list(labels.shape),
            "label_max": int(labels.max(initial=0)),
            "label_disk_dtype": np.asarray(tifffile.imread(self.labels_path)).dtype.name,
            "artifacts": artifacts,
        }
        _atomic_bytes(self.status_path, _json_bytes(marker))
        self._publish_if_needed()
        return self.status_path

    def _publish_if_needed(self) -> None:
        if self.publish_to is None:
            return
        publish_directory(self.directory, self.publish_to)

    def _validate_before_commit(self) -> None:
        roles = {key.split(":", 1)[0] for key in self._artifacts}
        missing = (_REQUIRED_SINGLETON_ROLES | _REQUIRED_COLLECTION_ROLES) - roles
        if missing:
            raise RuntimeError(f"cannot complete run; missing artifact role(s): {sorted(missing)}")
        if self._temporary_files():
            raise RuntimeError("cannot complete run while temporary files exist")
        for filename in self._artifacts.values():
            path = self.directory / filename
            if not path.is_file():
                raise RuntimeError(f"cannot complete run; missing artifact {filename!r}")
        self.read_labels()
        json.loads((self.directory / _CONFIG_NAME).read_text(encoding="utf-8"))
        json.loads((self.directory / _PROVENANCE_NAME).read_text(encoding="utf-8"))
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                json.loads(line)

    def _temporary_files(self) -> list[Path]:
        return [path for path in self.directory.rglob("*") if path.is_file() and path.name.endswith(".tmp")]

    def is_resumable(self, input_fingerprint: str, config_fingerprint: str) -> bool:
        """Return true only when the committed run validates completely."""
        try:
            if self._temporary_files() or not self.status_path.is_file():
                return False
            marker = json.loads(self.status_path.read_text(encoding="utf-8"))
            if (
                marker.get("schema_version") != 1
                or marker.get("status") != "complete"
                or marker.get("input_fingerprint") != input_fingerprint
                or marker.get("config_fingerprint") != config_fingerprint
            ):
                return False
            entries = marker.get("artifacts")
            if not isinstance(entries, list):
                return False
            roles: set[str] = set()
            names: set[str] = set()
            for entry in entries:
                if not isinstance(entry, dict):
                    return False
                name, role = entry.get("name"), entry.get("role")
                if not isinstance(name, str) or Path(name).name != name or name in names:
                    return False
                path = self.directory / name
                if (
                    not path.is_file()
                    or path.stat().st_size != entry.get("size")
                    or _sha256(path) != entry.get("sha256")
                ):
                    return False
                names.add(name)
                roles.add(role)
            if not (_REQUIRED_SINGLETON_ROLES | _REQUIRED_COLLECTION_ROLES).issubset(roles):
                return False
            labels = self.read_labels()
            disk_labels = np.asarray(tifffile.imread(self.labels_path))
            return (
                list(labels.shape) == marker.get("label_shape")
                and int(labels.max(initial=0)) == marker.get("label_max")
                and disk_labels.dtype.name == marker.get("label_disk_dtype")
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError, tifffile.TiffFileError):
            return False
