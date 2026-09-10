"""Run Cellpose eval in a subprocess: Cancel is cooperative; Kill terminates."""

from __future__ import annotations

from multiprocessing import get_context
from pathlib import Path
from queue import Empty
import tempfile
from threading import RLock
from typing import Any, Callable, Mapping

import numpy as np

from cellquant.contracts import MutableCancellationToken, PipelineCancelled


def is_killable_cellpose_model(model: Any) -> bool:
    """True for real Cellpose models (not unit-test fakes)."""

    module = type(model).__module__
    return bool(module) and module.startswith("cellpose")


def _sanitize_eval_kwargs(kwargs: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(kwargs)
    # Progress widgets / cancel wrappers are not picklable; Cancel/Kill use a
    # shared Event plus optional process.terminate() instead.
    payload.pop("progress", None)
    return payload


def _drain_progress(progress_queue, on_plane_progress: Callable[[int, int, str], None] | None) -> None:
    while True:
        try:
            current, total, message = progress_queue.get_nowait()
        except Empty:
            return
        if on_plane_progress is not None:
            on_plane_progress(int(current), int(total), str(message))


class _ChildProgress:
    """Cellpose's coarse progress callbacks also honor cooperative Cancel."""
    def __init__(self, event):
        self.event = event

    def setValue(self, value):  # noqa: N802 - Cellpose progress API
        if self.event.is_set():
            raise PipelineCancelled("pipeline run cancelled")

    def setMaximum(self, value):  # noqa: N802
        pass

    def setMinimum(self, value):  # noqa: N802
        pass


def _child_main(
    image_path: str,
    masks_path: str,
    constructor_parameters: dict[str, Any],
    eval_kwargs: dict[str, Any],
    mode: str,
    cancel_event,
    progress_queue,
    result_queue,
) -> None:
    try:
        from cellquant.segment import load_model, require_linking_stitch_threshold, _seed
        if cancel_event.is_set():
            result_queue.put(("cancelled", "cancelled before model load"))
            return
        spec = constructor_parameters["_model_spec"]
        active = load_model(spec)
        model = active.model
        _seed(constructor_parameters["_runtime"])
        model_metadata = dict(model_sha256=active.model_sha256, device=active.device,
                              cellpose_version=active.cellpose_version,
                              constructor_parameters=dict(active.constructor_parameters))
        image = np.load(image_path)
        kwargs = _sanitize_eval_kwargs(eval_kwargs)
        kwargs["progress"] = _ChildProgress(cancel_event)

        if mode == "stitch_2d":
            if image.ndim != 3:
                raise ValueError(f"stitch_2d expects ZYX; received shape {image.shape}")
            plane_kwargs = dict(kwargs)
            # Same policy as the direct path: refuse to run planes we cannot link.
            stitch_threshold = require_linking_stitch_threshold(plane_kwargs["stitch_threshold"])

            from cellpose.utils import stitch3D

            plane_kwargs["stitch_threshold"] = 0.0
            plane_kwargs["do_3D"] = False
            plane_kwargs["z_axis"] = None
            plane_kwargs["anisotropy"] = None
            depth = int(image.shape[0])
            plane_masks: list[np.ndarray] = []
            for index in range(depth):
                if cancel_event.is_set():
                    result_queue.put(("cancelled", "cancelled before plane"))
                    return
                progress_queue.put(
                    (
                        index + 1,
                        depth,
                        (
                            f"Cellpose stitch plane {index + 1}/{depth} — "
                            "Cancel after this plane, or Kill to stop now"
                        ),
                    )
                )
                result = model.eval(image[index], **plane_kwargs)
                masks = np.asarray(result[0])
                if masks.ndim != 2:
                    raise ValueError(f"expected 2D plane masks; received shape {masks.shape}")
                plane_masks.append(masks)
            if cancel_event.is_set():
                result_queue.put(("cancelled", "cancelled after planes"))
                return
            stacked = np.stack(plane_masks, axis=0)
            if depth > 1:
                stacked = np.asarray(stitch3D(stacked, stitch_threshold=stitch_threshold))
            np.save(masks_path, stacked)
            result_queue.put(
                (
                    "ok",
                    {
                        **model_metadata,
                        "cancellable_stitch_planes": True,
                        "force_killable_process": True,
                        "stitch_threshold": stitch_threshold,
                    },
                )
            )
            return

        if cancel_event.is_set():
            result_queue.put(("cancelled", "cancelled before eval"))
            return
        result = model.eval(image, **kwargs)
        masks = np.asarray(result[0])
        np.save(masks_path, masks)
        result_queue.put(("ok", {**model_metadata, "force_killable_process": True}))
    except PipelineCancelled:
        result_queue.put(("cancelled", "pipeline run cancelled"))
    except BaseException as exc:  # noqa: BLE001 - report any child failure to parent
        result_queue.put(("error", f"{type(exc).__name__}: {exc}"))


def run_killable_cellpose_eval(
    *,
    constructor_parameters: Mapping[str, Any],
    image: np.ndarray,
    eval_kwargs: Mapping[str, Any],
    mode: str,
    cancel: MutableCancellationToken,
    on_plane_progress: Callable[[int, int, str], None] | None = None,
    poll_seconds: float = 0.25,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Evaluate Cellpose in a child process.

    Cancel sets a cooperative event (child stops between planes).
    Kill terminates the child immediately mid-eval.
    """

    cancel.raise_if_cancelled()
    ctx = get_context("spawn")
    progress_queue = result_queue = process = None
    registered = False
    started = False
    process_lock = RLock()
    try:
        cancel_event = ctx.Event()
        progress_queue = ctx.Queue()
        result_queue = ctx.Queue()
        with tempfile.TemporaryDirectory(prefix="cellquant-cellpose-") as tmp:
            masks_path = Path(tmp) / "masks.npy"
            image_path = Path(tmp) / "input.npy"
            np.save(image_path, np.asarray(image))
            process = ctx.Process(target=_child_main,
                args=(str(image_path), str(masks_path), dict(constructor_parameters),
                      _sanitize_eval_kwargs(eval_kwargs), mode, cancel_event, progress_queue, result_queue),
                name="cellquant-cellpose-eval", daemon=True)

            def force_stop():
                cancel_event.set()
                with process_lock:
                    if started and process.is_alive():
                        process.terminate()

            cancel.register_force_stop(force_stop)
            registered = True
            try:
                # The lock prevents Kill touching a half-started or closing process.
                with process_lock:
                    cancel.raise_if_cancelled()
                    process.start()
                    started = True
                status, payload = None, None
                while True:
                    if cancel.cancelled:
                        cancel_event.set()
                    if cancel.killed:
                        force_stop()
                        raise PipelineCancelled("pipeline run cancelled")
                    _drain_progress(progress_queue, on_plane_progress)
                    try:
                        status, payload = result_queue.get(timeout=poll_seconds)
                        break
                    except Empty:
                        if not process.is_alive():
                            try:
                                status, payload = result_queue.get(timeout=0.5)
                            except Empty:
                                pass
                            break
                cancel.raise_if_cancelled()
                if status == "cancelled":
                    raise PipelineCancelled("pipeline run cancelled")
                if status == "error":
                    raise RuntimeError(f"Cellpose worker failed: {payload}")
                if status != "ok" or not masks_path.exists():
                    raise RuntimeError(f"Cellpose worker ended without a result (exitcode={process.exitcode})")
                masks = np.load(masks_path)
                cancel.raise_if_cancelled()
                return np.asarray(masks), {**dict(payload or {}), "force_killable_process": True}
            finally:
                # Unregister waits for an in-flight Kill callback before closing handles.
                if registered:
                    cancel.unregister_force_stop(force_stop)
                with process_lock:
                    if started:
                        process.join(timeout=0.2)
                        if process.is_alive():
                            process.terminate()
                            process.join(timeout=2.0)
                        if process.is_alive():
                            process.kill()
                            process.join(timeout=1.0)
                    if not process.is_alive():
                        process.close()
    finally:
        for queue in (progress_queue, result_queue):
            if queue is not None:
                queue.close()
                queue.join_thread()


__all__ = ["is_killable_cellpose_model", "run_killable_cellpose_eval"]
