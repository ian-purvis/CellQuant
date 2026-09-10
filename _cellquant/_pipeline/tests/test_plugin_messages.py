from pathlib import Path

import pytest

from cellquant.batch import BatchItemResult, BatchSummary
from cellquant.contracts import PipelineCancelled, PipelineEvent
from cellquant.plugin.messages import (
    explain_batch_summary,
    explain_exception,
    format_eta_seconds,
    format_failed_event,
)
from cellquant.plugin.paths import require_existing_file


@pytest.mark.parametrize(
    ("exc", "title_part", "hint_part"),
    [
        (ValueError("provide spacing_override_um"), "calibration", "spacing"),
        (ValueError("Unsupported image type '.png'"), "file type", "TIFF"),
        (FileNotFoundError("No matching image files under /data"), "No images", "recursive"),
        (RuntimeError("CUDA out of memory"), "GPU", "device"),
        (ValueError("segment.model_sha256 must be a 64-character SHA-256"), "Model", "model_sha256"),
        (PipelineCancelled("cancelled by user"), "Cancelled", "Kill"),
        (PermissionError("[Errno 13] Permission denied: '.'"), "Path not chosen", "magicgui"),
        (
            PermissionError(
                "Could not update C:/out/events.jsonl (temp .events.jsonl.abc.tmp). "
                "Windows reported the file was locked — often OneDrive sync"
            ),
            "Output folder locked",
            "staging",
        ),
        (ValueError("Choose a path first (the widget still points at '.' by default)."), "Path not chosen", "browse"),
    ],
)
def test_explain_exception_maps_common_failures(exc, title_part, hint_part):
    message = explain_exception(exc)
    assert title_part.casefold() in message.title.casefold() or title_part.casefold() in message.status_line().casefold()
    assert hint_part.casefold() in (message.hint or "").casefold() or hint_part.casefold() in message.status_line().casefold()


def test_require_existing_file_rejects_magicgui_dot_default():
    with pytest.raises(ValueError, match="Choose a path first"):
        require_existing_file(".")
    with pytest.raises(ValueError, match="Choose a path first"):
        require_existing_file(Path("."))


def test_batch_summary_lists_failed_files_and_failures_csv(tmp_path):
    failures = tmp_path / "failures.csv"
    failures.write_text("source,message\n", encoding="utf-8")
    summary = BatchSummary(
        total=2,
        completed=1,
        resumed=0,
        failed=1,
        cancelled=0,
        results=(
            BatchItemResult(str(tmp_path / "good.tif"), str(tmp_path / "good"), "completed", "r1"),
            BatchItemResult(str(tmp_path / "bad.tif"), str(tmp_path / "bad"), "failed", "r2", "boom"),
        ),
        summary_path=tmp_path / "batch_summary.json",
        failures_path=failures,
    )
    message = explain_batch_summary(summary)
    assert message.severity == "warning"
    assert "bad.tif" in (message.hint or "")
    assert "failures.csv" in (message.hint or "")


def test_failed_event_formatting_includes_stage_context():
    event = PipelineEvent(
        "failed",
        "run",
        "stack.tif",
        "segment",
        "2026-01-01T00:00:00Z",
        details={"exception_type": "RuntimeError", "message": "CUDA out of memory"},
    )
    message = format_failed_event(event)
    assert message is not None
    assert "segment" in message.title
    assert "stack.tif" in message.title
    assert "GPU" in message.title or "device" in (message.hint or "").casefold()


def test_format_eta_uses_completed_count_while_an_image_is_running():
    # 10 minutes elapsed, image 2 of 5 still running → one finished @ 10 min average.
    eta = format_eta_seconds(600.0, current=2, total=5, completed=1)
    assert eta is not None
    assert "min" in eta
    # After finishing image 2, four remain? completed=2 → remaining 3 steps.
    eta_done = format_eta_seconds(600.0, current=2, total=5, completed=2)
    assert eta_done is not None


def test_format_pipeline_status_keeps_batch_eta_during_stitch_plane_progress():
    from cellquant.plugin.messages import format_pipeline_status

    event = PipelineEvent(
        "progress",
        "run",
        r"C:\data\sample.nd2",
        "segment",
        "2026-01-01T00:00:00Z",
        current=12,
        total=40,
        details={"message": "plane 12/40", "status": "running"},
    )
    text = format_pipeline_status(
        event,
        eta="~25 min left",
        batch_progress=(3, 37),
        batch_status="running",
    )
    assert "Running image 3 of 37" in text
    assert "sample.nd2" in text
    assert "plane 12/40" in text
    assert "~25 min left" in text
