"""Tests for cloud-path staging and publish."""

from pathlib import Path

from cellquant.persist.staging import (
    looks_like_cloud_sync_path,
    publish_directory,
    resolve_work_directory,
    staging_directory_for,
)


def test_onedrive_paths_are_detected():
    assert looks_like_cloud_sync_path(
        Path(r"C:\Users\me\OneDrive - Contoso\Lab\Outputs")
    )
    assert looks_like_cloud_sync_path(Path.home() / "Dropbox" / "data")
    assert not looks_like_cloud_sync_path(Path(r"C:\CellQuant\Outputs"))


def test_resolve_work_directory_stages_cloud_outputs(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localapp"))
    cloud = tmp_path / "OneDrive - Contoso" / "Outputs" / "run.cellquant"
    work, publish = resolve_work_directory(cloud)
    assert publish == cloud
    assert work != cloud
    assert "CellQuant" in str(work)
    assert "staging" in str(work)
    marker = work / "cellquant_publish_target.txt"
    assert marker.is_file()
    assert "OneDrive" in marker.read_text(encoding="utf-8")


def test_resolve_work_directory_keeps_local_outputs(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localapp"))
    local = tmp_path / "local_outputs" / "run.cellquant"
    work, publish = resolve_work_directory(local)
    assert work == local
    assert publish is None


def test_publish_directory_copies_artifacts(tmp_path):
    source = tmp_path / "stage"
    destination = tmp_path / "OneDrive - Contoso" / "published"
    source.mkdir()
    (source / "events.jsonl").write_text('{"ok": true}\n', encoding="utf-8")
    (source / "nested").mkdir()
    (source / "nested" / "objects.csv").write_text("label\n1\n", encoding="utf-8")
    (source / "cellquant_publish_target.txt").write_text("ignore\n", encoding="utf-8")

    publish_directory(source, destination)
    assert (destination / "events.jsonl").read_text(encoding="utf-8").startswith("{")
    assert (destination / "nested" / "objects.csv").is_file()
    assert not (destination / "cellquant_publish_target.txt").exists()


def test_staging_directory_is_stable(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localapp"))
    target = tmp_path / "OneDrive" / "a.cellquant"
    first = staging_directory_for(target)
    second = staging_directory_for(target)
    assert first == second
