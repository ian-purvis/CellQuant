"""User-facing error and status copy for the napari plugin.

Durable logging remains in run stores (`events.jsonl`, `status.json`,
`failures.csv`). This module only translates those outcomes into short
operator-facing text and remediation hints.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class UserMessage:
    """GUI-ready status / notification payload."""

    severity: str  # "info" | "warning" | "error"
    title: str
    body: str
    hint: str | None = None

    def status_line(self) -> str:
        parts = [f"{self.title}: {self.body}"]
        if self.hint:
            parts.append(self.hint)
        return " — ".join(parts)

    def notification_text(self) -> str:
        if self.hint:
            return f"{self.body}\n\n{self.hint}"
        return self.body


def _match(text: str, *needles: str) -> bool:
    lowered = text.casefold()
    return any(needle.casefold() in lowered for needle in needles)


def explain_exception(exc: BaseException) -> UserMessage:
    """Map a pipeline/plugin exception to a short remediation hint."""

    name = type(exc).__name__
    text = str(exc).strip() or name

    if _match(name, "PipelineCancelled") or (
        _match(text, "cancel") and not _match(text, "permission")
    ):
        return UserMessage(
            "warning",
            "Cancelled",
            text,
            "Re-run when ready. Cancel stops after the current plane/checkpoint; "
            "Kill force-stops the Cellpose worker immediately if Napari feels stuck.",
        )
    if _match(name, "PermissionError") or _match(
        text, "Permission denied", "permission denied", "Access is denied", "WinError 5"
    ):
        if _match(
            text,
            "Could not update",
            "events.jsonl",
            "OneDrive",
            "Access is denied",
            "WinError 5",
            "locked",
        ):
            return UserMessage(
                "error",
                "Output folder locked",
                text,
                "A cloud sync client or other program locked a file during publish. "
                "CellQuant stages runs locally under %LOCALAPPDATA%\\CellQuant\\staging; "
                "pause OneDrive briefly and re-run — completed staged runs will publish/resume.",
            )
        return UserMessage(
            "error",
            "Path not chosen or not writable",
            text,
            "Pick a real file/folder in the widget (magicgui defaults to '.'). "
            "For outputs, use a writable folder; OneDrive-locked paths often deny access.",
        )
    if _match(text, "Choose a path first", "still points at"):
        return UserMessage(
            "error",
            "Path not chosen",
            text,
            "Use the browse button and select an image, config YAML, or folder before running.",
        )
    if _match(text, "spacing_override", "spacing_um", "calibrat"):
        return UserMessage(
            "error",
            "Missing calibration",
            text,
            "Set voxel spacing in the config (`io.spacing_override_um`) or ensure the file carries XY/Z metadata.",
        )
    if _match(text, "Unsupported image type", "unsupported image suffix", "suffixes"):
        return UserMessage(
            "error",
            "Unsupported or filtered file type",
            text,
            "Use TIFF/OME-TIFF/ND2, or change Mode → Batch → File type / `io.suffixes`.",
        )
    if _match(text, "No matching image files"):
        return UserMessage(
            "error",
            "No images found",
            text,
            "Check the input folder, recursive setting, and file-type filter.",
        )
    if _match(text, "cuda", "GPU", "out of memory", "CUDNN"):
        return UserMessage(
            "error",
            "GPU / device problem",
            text,
            "Confirm the CUDA PyTorch build and driver, or set `segment.device: cpu` / allow CPU fallback in the config.",
        )
    if _match(text, "model_sha256", "weight", "hash"):
        return UserMessage(
            "error",
            "Model weights rejected",
            text,
            "Point `segment.model` at the expected weights and match `segment.model_sha256`.",
        )
    if _match(text, "channel", "outside C axis", "preprocess.channel"):
        return UserMessage(
            "error",
            "Channel selection invalid",
            text,
            "Pick a channel that exists in the image (0-based in config/batch; 1-based labels in single-image mode).",
        )
    if _match(text, "schema_version", "omits explicit", "must be"):
        return UserMessage(
            "error",
            "Invalid configuration",
            text,
            "Open `sample_config.yaml`, fix the reported field, and reload the config path.",
        )
    if _match(text, "A CellQuant background operation is already running"):
        return UserMessage(
            "warning",
            "Busy",
            text,
            "Wait for the current job to finish or press Cancel, then try again.",
        )
    return UserMessage(
        "error",
        "CellQuant failed",
        f"{name}: {text}",
        "Check the dock status and, for saved runs, `events.jsonl` / `status.json` in the output folder.",
    )


def explain_batch_summary(summary: Any) -> UserMessage:
    """Summarize a finished batch for the dock and notifications."""

    failed = int(getattr(summary, "failed", 0) or 0)
    cancelled = int(getattr(summary, "cancelled", 0) or 0)
    completed = int(getattr(summary, "completed", 0) or 0)
    resumed = int(getattr(summary, "resumed", 0) or 0)
    total = int(getattr(summary, "total", 0) or 0)
    body = (
        f"{completed} done, {resumed} resumed, {failed} failed, "
        f"{cancelled} cancelled (of {total})"
    )

    results = tuple(getattr(summary, "results", ()) or ())
    failed_names = [
        Path(str(result.source)).name
        for result in results
        if getattr(result, "status", None) == "failed"
    ]
    failures_path = getattr(summary, "failures_path", None)
    summary_path = getattr(summary, "summary_path", None)

    if failed:
        preview = ", ".join(failed_names[:5])
        if len(failed_names) > 5:
            preview = f"{preview}, …"
        hint_parts = []
        if preview:
            hint_parts.append(f"Failed files: {preview}.")
        if failures_path:
            hint_parts.append(f"Open failures.csv: {failures_path}")
        elif summary_path:
            hint_parts.append(f"See batch_summary.json: {summary_path}")
        else:
            hint_parts.append("Inspect each *.cellquant/events.jsonl for details.")
        return UserMessage("warning", "Batch finished with failures", body, " ".join(hint_parts))

    if cancelled and not completed and not resumed:
        return UserMessage(
            "warning",
            "Batch cancelled",
            body,
            "Re-run to continue; completed files with matching fingerprints will resume.",
        )

    hint = None
    if summary_path:
        hint = f"Summary: {summary_path}"
    return UserMessage("info", "Batch complete", body, hint)


def failed_result_paths(summary: Any) -> tuple[Path, ...]:
    """Return existing failure/summary artifact paths from a batch summary."""

    paths: list[Path] = []
    for attr in ("failures_path", "summary_path"):
        value = getattr(summary, attr, None)
        if value:
            path = Path(value)
            if path.exists():
                paths.append(path)
    return tuple(paths)


def format_failed_event(event: Any) -> UserMessage | None:
    """Turn a streamed `failed` PipelineEvent into dock copy when useful."""

    if getattr(event, "kind", None) != "failed":
        return None
    details = dict(getattr(event, "details", {}) or {})
    exception_type = str(details.get("exception_type") or "Error")
    message = str(details.get("message") or event.kind)
    explained = explain_exception(RuntimeError(f"{exception_type}: {message}"))
    stage = getattr(event, "stage", None)
    file_id = getattr(event, "file_id", None)
    prefix_bits = [bit for bit in (stage, file_id) if bit]
    title = explained.title if not prefix_bits else f"{explained.title} ({', '.join(map(str, prefix_bits))})"
    return UserMessage(explained.severity, title, explained.body, explained.hint)


def format_eta_seconds(
    elapsed_s: float,
    current: int,
    total: int,
    *,
    completed: int | None = None,
) -> str | None:
    """Estimate remaining time from average seconds per finished step."""

    if total < 1 or elapsed_s <= 0:
        return None
    finished = completed if completed is not None else max(int(current) - 1, 0)
    if finished < 1:
        # No completed images yet — rough projection from time on the first item.
        avg = elapsed_s
        remaining_steps = max(total - max(current, 1) + 1, 0)
    else:
        avg = elapsed_s / finished
        remaining_steps = max(total - finished, 0)
    remaining = remaining_steps * avg
    if remaining < 1:
        return "under a minute left"
    if remaining < 90:
        return f"~{int(round(remaining))}s left"
    minutes = remaining / 60.0
    if minutes < 90:
        return f"~{minutes:.0f} min left"
    return f"~{minutes / 60.0:.1f} h left"


def format_pipeline_status(event: Any, *, eta: str | None = None) -> str:
    """Turn a PipelineEvent into a short dock status line."""

    details = dict(getattr(event, "details", {}) or {})
    message = details.get("message")
    kind = getattr(event, "kind", "")
    stage = getattr(event, "stage", "")
    file_id = getattr(event, "file_id", "") or ""
    name = Path(str(file_id)).name if file_id else ""
    current = getattr(event, "current", None)
    total = getattr(event, "total", None)

    if kind == "warning" and message and _match(
        str(message), "Cellpose is running", "blocking library call"
    ):
        base = str(message)
        if name:
            base = f"{name}: {base}"
        return base

    if stage == "batch" and current is not None and total is not None:
        status = str(details.get("status") or kind)
        if status == "running":
            verb = "Running"
        elif status == "completed":
            verb = "Finished"
        elif status == "resumed":
            verb = "Resumed"
        else:
            verb = status.capitalize()
        parts = [f"{verb} image {current} of {total}"]
        if name:
            parts.append(name)
        if eta:
            parts.append(eta)
        return " — ".join(parts)

    if message:
        return str(message)
    label = f"{stage}: {str(kind).replace('_', ' ')}"
    if name:
        return f"{name}: {label}"
    return label


def open_path_in_os(path: str | Path) -> None:
    """Open a file or folder with the platform file browser / default app."""

    import os
    import subprocess
    import sys

    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(target)
    if sys.platform.startswith("win"):
        os.startfile(target)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", str(target)], check=False)
    else:
        subprocess.run(["xdg-open", str(target)], check=False)


def notify_napari(message: UserMessage, notify_fn: Any | None = None) -> None:
    """Show a napari notification when available; never raise into the dock."""

    text = message.notification_text()
    if notify_fn is not None:
        notify_fn(message.severity, message.title, text)
        return
    try:
        from napari.utils.notifications import (
            show_error,
            show_info,
            show_warning,
        )
    except Exception:
        return
    try:
        if message.severity == "error":
            show_error(text)
        elif message.severity == "warning":
            show_warning(text)
        else:
            show_info(text)
    except Exception:
        return


__all__ = [
    "UserMessage",
    "explain_batch_summary",
    "explain_exception",
    "failed_result_paths",
    "format_eta_seconds",
    "format_failed_event",
    "format_pipeline_status",
    "notify_napari",
    "open_path_in_os",
]
