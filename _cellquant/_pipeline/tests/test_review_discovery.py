"""Review queue discovery for single, folder, and batch scopes."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import tifffile
import yaml

from cellquant.review import (
    LABELS_DRAFT_NAME,
    LABELS_NAME,
    LABELS_REVIEWED_NAME,
    DiscoveryError,
    discover,
    discover_batch,
    discover_folder,
    discover_single,
    filter_items,
    is_review_artifact,
    owning_run,
    publish_approved,
    queue_counts,
    reject_review,
    save_draft,
    set_queue_status,
)
from cellquant.review.state import load_review

ROOT = Path(__file__).resolve().parents[1]


def _write_image(path: Path, data: np.ndarray) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(path, data, photometric="minisblack", metadata={"axes": "ZYXC"})
    return path


def _write_mask(path: Path, labels: np.ndarray) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(
        path,
        labels.astype(np.uint16),
        photometric="minisblack",
        metadata={"axes": "ZYX"},
    )
    return path


def _labels() -> np.ndarray:
    data = np.zeros((1, 4, 4), np.uint32)
    data[0, 0, 0] = 1
    data[0, 2, 2] = 2
    return data


def _run(
    parent: Path,
    *,
    name: str,
    source: Path,
    labels: np.ndarray | None = None,
    status: str = "complete",
) -> Path:
    run = parent / f"{name}.cellquant"
    run.mkdir(parents=True)
    raw = yaml.safe_load((ROOT / "sample_config.yaml").read_text())
    raw["io"]["axes_override"] = "ZYXC"
    raw["io"]["spacing_override_um"] = [1, 1, 1]
    (run / "config.json").write_text(json.dumps(raw), encoding="utf-8")
    (run / "provenance.json").write_text(
        json.dumps({"schema_version": 1, "run_id": name, "source": str(source)}),
        encoding="utf-8",
    )
    (run / "status.json").write_text(json.dumps({"status": status}), encoding="utf-8")
    _write_mask(run / LABELS_NAME, _labels() if labels is None else labels)
    return run


# -- single scope ------------------------------------------------------


def test_single_accepts_run_dir_and_resolves_masks_inside_a_run(tmp_path):
    source = _write_image(tmp_path / "img.tif", np.zeros((1, 4, 4, 1), np.uint16))
    run = _run(tmp_path, name="a", source=source)
    publish_approved(run, _labels(), note="ok")

    direct = discover_single(run)
    assert direct.kind == "run"
    assert direct.run_dir == run
    assert direct.review_status == "approved"
    assert direct.revision == 1
    assert direct.source == str(source)

    # Selecting any mask belonging to the run reuses the run's review record
    # instead of importing the mask as a brand-new image.
    for inside in (
        run / LABELS_NAME,
        run / LABELS_REVIEWED_NAME,
        run / "reviews" / "r000001.tif",
    ):
        item = discover_single(inside)
        assert item.kind == "run"
        assert item.run_dir == run
        assert item.revision == 1
    assert owning_run(run / "reviews" / "r000001.tif") == run


def test_single_accepts_standalone_label_tiff_and_marks_source_needed(tmp_path):
    mask = _write_mask(tmp_path / "loose" / "cells_masks.tif", _labels())
    item = discover_single(mask)
    assert item.kind == "standalone_mask"
    assert item.run_dir is None
    assert item.needs_source is True
    assert item.is_reviewable is False
    assert item.review_status == "pending"


def test_single_rejects_unsupported_mask_exports(tmp_path):
    npy = tmp_path / "cells_seg.npy"
    npy.write_bytes(b"\x93NUMPY fake")
    with pytest.raises(DiscoveryError, match="_seg.npy"):
        discover_single(npy)

    rgb = tmp_path / "overlay.tif"
    tifffile.imwrite(rgb, np.zeros((4, 4, 3), np.uint8), photometric="rgb")
    with pytest.raises(DiscoveryError, match="RGB rendering"):
        discover_single(rgb)

    intensity = tmp_path / "raw.tif"
    tifffile.imwrite(intensity, np.zeros((4, 4), np.float32), photometric="minisblack")
    with pytest.raises(DiscoveryError, match="integer"):
        discover_single(intensity)

    with pytest.raises(DiscoveryError, match="does not exist"):
        discover_single(tmp_path / "nope.tif")

    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(DiscoveryError, match="not a .cellquant run"):
        discover_single(plain)


def test_run_without_source_is_visible_but_blocked(tmp_path):
    run = _run(tmp_path, name="orphan", source=tmp_path / "gone.tif")
    item = discover_single(run)
    assert item.blocked_reason is not None
    assert "source image missing" in item.blocked_reason
    assert item.is_reviewable is False


def test_incomplete_run_reports_its_reason(tmp_path):
    source = _write_image(tmp_path / "img.tif", np.zeros((1, 4, 4, 1), np.uint16))
    failed = _run(tmp_path, name="failed", source=source, status="failed")
    assert "not complete" in (discover_single(failed).blocked_reason or "")

    bare = tmp_path / "bare.cellquant"
    bare.mkdir()
    assert f"no {LABELS_NAME}" in (discover_single(bare).blocked_reason or "")


# -- folder scope ------------------------------------------------------


def test_folder_includes_the_selected_run_itself(tmp_path):
    source = _write_image(tmp_path / "img.tif", np.zeros((1, 4, 4, 1), np.uint16))
    run = _run(tmp_path, name="solo", source=source)
    items = discover_folder(run)
    assert [item.run_dir for item in items] == [run]


def test_folder_dedupes_runs_and_ignores_generated_review_outputs(tmp_path):
    source = _write_image(tmp_path / "img.tif", np.zeros((1, 4, 4, 1), np.uint16))
    run = _run(tmp_path, name="a", source=source)
    publish_approved(run, _labels())
    save_draft(run, _labels(), loaded_record=load_review(run))
    loose = _write_mask(tmp_path / "masks" / "b_masks.tif", _labels())

    items = discover_folder(tmp_path)
    paths = [item.path for item in items]
    # The run appears once even though it holds labels.tif, labels_draft.tif,
    # labels_reviewed.tif and reviews/r000001.tif.
    assert paths.count(run) == 1
    assert loose in paths
    assert len(items) == 2
    assert is_review_artifact(run / LABELS_DRAFT_NAME)
    assert is_review_artifact(run / LABELS_REVIEWED_NAME)
    assert is_review_artifact(run / "reviews" / "r000001.tif")
    assert not is_review_artifact(run / LABELS_NAME)


def test_folder_recursion_toggle(tmp_path):
    source = _write_image(tmp_path / "img.tif", np.zeros((1, 4, 4, 1), np.uint16))
    _run(tmp_path, name="top", source=source)
    _run(tmp_path / "nested" / "deeper", name="low", source=source)
    _write_mask(tmp_path / "nested" / "loose_masks.tif", _labels())

    recursive = discover_folder(tmp_path, recursive=True)
    assert len(recursive) == 3
    shallow = discover_folder(tmp_path, recursive=False)
    assert [item.name for item in shallow] == ["top.cellquant"]
    with pytest.raises(DiscoveryError, match="not a directory"):
        discover_folder(tmp_path / "img.tif")


def test_folder_reports_review_state_for_the_queue(tmp_path):
    source = _write_image(tmp_path / "img.tif", np.zeros((1, 4, 4, 1), np.uint16))
    approved = _run(tmp_path, name="approved", source=source)
    publish_approved(approved, _labels())
    skipped = _run(tmp_path, name="skipped", source=source)
    publish_approved(skipped, _labels())
    set_queue_status(skipped, "skipped", loaded_record=load_review(skipped))
    drafted = _run(tmp_path, name="drafted", source=source)
    save_draft(drafted, _labels())
    rejected = _run(tmp_path, name="rejected", source=source)
    reject_review(rejected, reason="over-segmented")
    _run(tmp_path, name="pending", source=source)
    blocked = _run(tmp_path, name="blocked", source=tmp_path / "gone.tif")

    items = discover_folder(tmp_path)
    counts = queue_counts(items)
    assert counts["total"] == 6
    assert counts["approved"] == 2
    assert counts["draft"] == 1
    assert counts["rejected"] == 1
    assert counts["pending"] == 2  # pending + the blocked run
    assert counts["skipped"] == 1
    assert counts["blocked"] == 1

    # Filters use the queue vocabulary; skipped and blocked are tags, not statuses.
    assert {i.name for i in filter_items(items, statuses=["approved"])} == {
        "approved.cellquant",
        "skipped.cellquant",
    }
    assert {i.name for i in filter_items(items, statuses=["skipped"])} == {
        "skipped.cellquant"
    }
    assert {i.path for i in filter_items(items, statuses=["blocked"])} == {blocked}
    assert filter_items(items, statuses=None) == items
    assert {i.name for i in filter_items(items, statuses=["draft", "rejected"])} == {
        "drafted.cellquant",
        "rejected.cellquant",
    }


# -- batch scope -------------------------------------------------------


def _batch(tmp_path: Path, results: list[dict]) -> Path:
    out = tmp_path / "batch"
    out.mkdir(parents=True, exist_ok=True)
    (out / "batch_summary.json").write_text(
        json.dumps({"schema_version": 1, "results": results}), encoding="utf-8"
    )
    return out


def test_batch_membership_comes_from_the_manifest_only(tmp_path):
    source = _write_image(tmp_path / "img.tif", np.zeros((1, 4, 4, 1), np.uint16))
    member = _run(tmp_path / "batch", name="member", source=source)
    # A sibling run that was not part of the batch must not join the queue.
    _run(tmp_path / "batch", name="neighbor", source=source)
    out = _batch(
        tmp_path,
        [
            {
                "status": "completed",
                "output_dir": str(member),
                "source": str(source),
                "run_id": "m1",
            }
        ],
    )
    items = discover_batch(out)
    assert [item.run_dir for item in items] == [member]
    assert items[0].batch_status == "completed"
    # The directory and the manifest path are interchangeable selections.
    assert discover_batch(out / "batch_summary.json") == items
    assert discover("batch", out) == items


def test_batch_keeps_failed_cancelled_and_missing_entries_visible(tmp_path):
    source = _write_image(tmp_path / "img.tif", np.zeros((1, 4, 4, 1), np.uint16))
    ok = _run(tmp_path / "batch", name="ok", source=source)
    out = _batch(
        tmp_path,
        [
            {"status": "completed", "output_dir": str(ok), "source": str(source)},
            {
                "status": "failed",
                "output_dir": str(tmp_path / "batch" / "bad.cellquant"),
                "source": str(tmp_path / "bad.tif"),
                "message": "cellpose ran out of memory",
            },
            {
                "status": "cancelled",
                "output_dir": str(tmp_path / "batch" / "stopped.cellquant"),
                "source": str(tmp_path / "stopped.tif"),
            },
            {"status": "missing", "source": str(tmp_path / "never.tif")},
        ],
    )
    items = discover_batch(out)
    assert len(items) == 4
    by_status = {item.batch_status: item for item in items}
    assert by_status["completed"].is_reviewable is True
    assert "out of memory" in (by_status["failed"].blocked_reason or "")
    assert "run directory missing" in (by_status["failed"].blocked_reason or "")
    assert "cancelled" in (by_status["cancelled"].blocked_reason or "")
    assert by_status["missing"].blocked_reason is not None
    # Totals stay honest: the batch had four members, one of which is reviewable.
    counts = queue_counts(items)
    assert counts["total"] == 4 and counts["blocked"] == 3


def test_batch_requires_a_membership_manifest(tmp_path):
    with pytest.raises(DiscoveryError, match="batch manifest not found"):
        discover_batch(tmp_path)
    out = tmp_path / "batch"
    out.mkdir()
    (out / "batch_summary.json").write_text(json.dumps({"completed": 3}), encoding="utf-8")
    with pytest.raises(DiscoveryError, match="no per-file 'results' membership"):
        discover_batch(out)


def test_unknown_scope_is_rejected(tmp_path):
    with pytest.raises(DiscoveryError, match="unknown review scope"):
        discover("everything", tmp_path)
