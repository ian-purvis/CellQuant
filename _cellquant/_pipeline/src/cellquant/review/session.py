"""``review_session.json``: resumable folder/batch queue progress.

Per-run ``review.json`` remains authoritative for review state; the session only
records *which* items are in the queue, their order, exclusions, and where the
reviewer stopped. Resuming reconciles item status from each run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Mapping, Sequence
import uuid

from cellquant.persist.atomic import replace_with_retry
from cellquant.review.constants import (
    REVIEW_SESSION_NAME,
    SESSION_SCHEMA_VERSION,
)
from cellquant.review.state import utc_now


class ReviewSessionError(ValueError):
    """Raised when a session file cannot be interpreted."""


@dataclass(frozen=True)
class SessionItem:
    """One queue member, identified by its resolved bundle/run path."""

    item_id: str
    path: str
    source: str | None = None
    excluded: bool = False
    blocked_reason: str | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "path": self.path,
            "source": self.source,
            "excluded": bool(self.excluded),
            "blocked_reason": self.blocked_reason,
        }


@dataclass(frozen=True)
class ReviewSession:
    """Scope, ordered membership, and active item for a review queue."""

    scope: str
    selection: str
    items: tuple[SessionItem, ...] = ()
    active_item_id: str | None = None
    layout: str = "side_by_side"
    recursive: bool = True
    created_utc: str = field(default_factory=utc_now)
    updated_utc: str = field(default_factory=utc_now)
    discovery_snapshot: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = SESSION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.scope not in {"single", "folder", "batch"}:
            raise ReviewSessionError(
                f"scope must be single, folder, or batch; received {self.scope!r}"
            )

    @property
    def active_items(self) -> tuple[SessionItem, ...]:
        return tuple(item for item in self.items if not item.excluded)

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": int(self.schema_version),
            "scope": self.scope,
            "selection": self.selection,
            "recursive": bool(self.recursive),
            "layout": self.layout,
            "items": [item.payload() for item in self.items],
            "exclusions": [item.item_id for item in self.items if item.excluded],
            "active_item_id": self.active_item_id,
            "created_utc": self.created_utc,
            "updated_utc": self.updated_utc,
            "discovery_snapshot": dict(self.discovery_snapshot),
        }


def parse_session(payload: Mapping[str, Any]) -> ReviewSession:
    if not isinstance(payload, Mapping):
        raise ReviewSessionError("review_session.json must contain a JSON object")
    version = int(payload.get("schema_version") or 0)
    if version != SESSION_SCHEMA_VERSION:
        raise ReviewSessionError(
            f"review_session.json schema_version {version} is not supported "
            f"(expected {SESSION_SCHEMA_VERSION})"
        )
    raw_items = payload.get("items") or ()
    if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
        raise ReviewSessionError("items must be a list")
    exclusions = set(str(v) for v in (payload.get("exclusions") or ()))
    items = tuple(
        SessionItem(
            item_id=str(entry.get("item_id") or entry.get("path") or ""),
            path=str(entry.get("path") or ""),
            source=entry.get("source") or None,
            excluded=bool(entry.get("excluded"))
            or str(entry.get("item_id") or "") in exclusions,
            blocked_reason=entry.get("blocked_reason") or None,
        )
        for entry in raw_items
        if isinstance(entry, Mapping)
    )
    return ReviewSession(
        scope=str(payload.get("scope") or "folder"),
        selection=str(payload.get("selection") or ""),
        items=items,
        active_item_id=payload.get("active_item_id") or None,
        layout=str(payload.get("layout") or "side_by_side"),
        recursive=bool(payload.get("recursive", True)),
        created_utc=str(payload.get("created_utc") or utc_now()),
        updated_utc=str(payload.get("updated_utc") or utc_now()),
        discovery_snapshot=dict(payload.get("discovery_snapshot") or {}),
    )


def session_path(directory: str | Path) -> Path:
    return Path(directory) / REVIEW_SESSION_NAME


def save_session(directory: str | Path, session: ReviewSession) -> Path:
    """Write the session atomically into a writable session directory."""

    destination = session_path(directory)
    destination.parent.mkdir(parents=True, exist_ok=True)
    body = dict(session.payload())
    body["updated_utc"] = utc_now()
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        replace_with_retry(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def load_session(directory: str | Path) -> ReviewSession | None:
    """Load a saved session, or ``None`` when the directory has none."""

    path = session_path(directory)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewSessionError(f"unreadable {path}: {exc}") from exc
    return parse_session(raw)


def item_id_for(path: str | Path) -> str:
    """Stable identity for a queue item based on its resolved path."""

    import hashlib

    resolved = str(Path(path).resolve()).casefold()
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "ReviewSession",
    "ReviewSessionError",
    "SessionItem",
    "item_id_for",
    "load_session",
    "parse_session",
    "save_session",
    "session_path",
]
