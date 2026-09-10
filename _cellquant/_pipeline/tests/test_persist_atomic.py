"""Tests for atomic replace retries under brief Windows/OneDrive locks."""

from pathlib import Path

import pytest

from cellquant.persist.atomic import replace_with_retry


def test_replace_with_retry_succeeds_after_transient_permission_errors(tmp_path, monkeypatch):
    source = tmp_path / "src.tmp"
    destination = tmp_path / "dest.txt"
    source.write_text("ok", encoding="utf-8")
    destination.write_text("old", encoding="utf-8")

    calls = {"n": 0}
    real_replace = __import__("os").replace

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(5, "Access is denied")
        return real_replace(src, dst)

    monkeypatch.setattr("cellquant.persist.atomic.os.replace", flaky_replace)
    monkeypatch.setattr("cellquant.persist.atomic.sleep", lambda *_: None)

    replace_with_retry(source, destination, attempts=5, base_delay_s=0.01)
    assert destination.read_text(encoding="utf-8") == "ok"
    assert calls["n"] == 3


def test_replace_with_retry_raises_clear_onedrive_hint(tmp_path, monkeypatch):
    source = tmp_path / "src.tmp"
    destination = tmp_path / "events.jsonl"
    source.write_text("x", encoding="utf-8")

    def always_locked(src, dst):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr("cellquant.persist.atomic.os.replace", always_locked)
    monkeypatch.setattr("cellquant.persist.atomic.sleep", lambda *_: None)

    with pytest.raises(PermissionError, match="OneDrive"):
        replace_with_retry(source, destination, attempts=3, base_delay_s=0.01)
