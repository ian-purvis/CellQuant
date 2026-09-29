"""The cluster worker: analyze a package's acquisitions one at a time and publish each result.

The worker never imports Qt or napari and never builds an ``AnalysisController``.
Each acquisition goes through the shared ``process_image``. Every planned
acquisition is always accounted for in ``results.json``; a stop leaves
unstarted ones ``pending`` and never invents a success.
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import threading
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Callable

from cellquant import progress
from cellquant.hpc.common import new_id, pixel_sha256, read_json, sha256_file, utc_now, write_json_atomic
from cellquant.hpc.lease import LeaseHeld, acquire, release
from cellquant.hpc.lineage import lineage_key, segmentation_settings
from cellquant.hpc.models import (
    AcquisitionState,
    AttemptInfo,
    AttemptRecord,
    ErrorDetail,
    LeaseRecord,
    ResultIndex,
    RunAllocation,
    TaskResult,
    load_model,
)
from cellquant.hpc.publish import PublicationError, hash_tree, publish_directory, verified_commit, write_commit
from cellquant.hpc.runtime import compare_runtime, fingerprint, observed_runtime
from cellquant.hpc.validate import Bundle, BundleInvalid, open_bundle

EXIT_OK = 0
EXIT_INVALID = 2
EXIT_RUNTIME = 3
EXIT_UNFINISHED = 4
EXIT_PUBLICATION = 5
EXIT_CANCELLED = 130


class Stop:
    """Stop requests from signals. ``soon`` stops after the current image; ``now`` at the next safe point in it."""

    def __init__(self) -> None:
        self.soon = threading.Event()
        self.now = threading.Event()

    @property
    def requested(self) -> bool:
        return self.soon.is_set() or self.now.is_set()

    def install(self) -> None:
        def later(_signum, _frame) -> None:
            self.soon.set()

        def immediately(_signum, _frame) -> None:
            self.soon.set()
            self.now.set()

        if hasattr(signal, "SIGUSR1"):
            signal.signal(signal.SIGUSR1, later)  # Slurm's warning before the time limit
        signal.signal(signal.SIGTERM, immediately)  # scancel, or the time limit itself
        signal.signal(signal.SIGINT, immediately)


def allocate_run(results_root: Path, bundle_id: str, ready_digest: str, package_name: str) -> tuple[str, Path]:
    run_id = f"run_{utc_now().replace('-', '').replace(':', '').rstrip('Z')}_{new_id(length=6)}"
    run_dir = results_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    allocation = RunAllocation(run_id=run_id, bundle_id=bundle_id, ready_digest=ready_digest, package_name=package_name, created_at=utc_now())
    write_json_atomic(run_dir / "run.json", allocation.model_dump(mode="json"))
    return run_id, run_dir


def allocate_attempt(run_dir: Path, run_id: str, *, resume: bool) -> tuple[str, Path]:
    attempt_id = f"att_{utc_now().replace('-', '').replace(':', '').rstrip('Z')}_{new_id(length=4)}"
    attempt_dir = run_dir / "attempts" / attempt_id
    (attempt_dir / "logs").mkdir(parents=True, exist_ok=False)
    info = AttemptInfo(attempt_id=attempt_id, run_id=run_id, created_at=utc_now(), resume=resume)
    write_json_atomic(attempt_dir / "attempt.json", info.model_dump(mode="json"))
    return attempt_id, attempt_dir


class Runner:
    def __init__(
        self,
        bundle_dir: str | Path,
        scratch_results: str | Path,
        publish_to: str | Path,
        *,
        attempt_id: str | None = None,
        resume: bool = False,
        require_gpu: bool = True,
        observe: Callable[..., dict] | None = None,
        stop: Stop | None = None,
        log: Callable[[str], None] = print,
    ) -> None:
        self.bundle_dir = Path(bundle_dir)
        self.scratch = Path(scratch_results)
        self.run_dir = Path(publish_to)
        self.attempt_id = attempt_id
        self.resume = resume
        self.require_gpu = require_gpu
        self.observe = observe or observed_runtime
        self.stop = stop or Stop()
        self._log = log
        self.index: ResultIndex | None = None
        self.lease: LeaseRecord | None = None

    def log(self, text: str) -> None:
        line = f"{utc_now()} {text}"
        self._log(line)
        if self.attempt_id and (self.run_dir / "attempts" / self.attempt_id).is_dir():
            with (self.run_dir / "attempts" / self.attempt_id / "worker.log").open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    # -- setup ---------------------------------------------------------------------------------------------

    def run(self) -> int:
        try:
            bundle = open_bundle(self.bundle_dir)
        except BundleInvalid as exc:
            self.log(f"The package is not valid: {exc}")
            for issue in exc.report.errors[:20]:
                self.log(f"  {issue.code}: {issue.message}")
            return EXIT_INVALID
        code = self._prepare_run_dir(bundle)
        if code is not None:
            return code
        write_json_atomic(self.run_dir / "attempts" / self.attempt_id / "validation.json", bundle.report.model_dump(mode="json"))  # type: ignore[operator]
        observed = self.observe(bundle.runtime.engine, bundle.runtime.model, models_dir=bundle.runtime.model_directory)
        issues = compare_runtime(bundle.runtime, observed)
        write_json_atomic(
            self.run_dir / "attempts" / str(self.attempt_id) / "runtime_report.json",
            {"checked_at": utc_now(), "observed": observed, "issues": [issue.model_dump() for issue in issues]},
        )
        if issues:
            for issue in issues:
                self.log(f"{issue.code}: {issue.message}")
            self.log("The installed software does not match the package's runtime contract. Nothing was analyzed.")
            return EXIT_RUNTIME
        runtime_fingerprint = fingerprint(observed)
        lease = LeaseRecord(
            lease_token=new_id(length=32),
            ready_digest=bundle.checksums_sha256,
            run_id=self.run_id,
            attempt_id=str(self.attempt_id),
            cluster=os.environ.get("SLURM_CLUSTER_NAME", ""),
            job_id=os.environ.get("SLURM_JOB_ID", ""),
            host=socket.gethostname(),
            pid=os.getpid(),
            acquired_at=utc_now(),
            submitted_at=self._attempt_info().created_at if self._attempt_info() else None,
        )
        try:
            self.lease = acquire(self.run_dir, lease)
        except LeaseHeld as exc:
            self.log(str(exc))
            held = exc.current
            if held is not None:
                self.log(
                    "If that job has ended, recover the run with:\n  python -m cellquant.hpc recover-lease "
                    f"--bundle PACKAGE --results {self.run_dir} --run-id {held.run_id} --attempt-id {held.attempt_id} "
                    f"--job-id {held.job_id or 'JOB_ID'} --expected-lease-token {held.lease_token}"
                )
            return EXIT_INVALID
        try:
            return self._work(bundle, runtime_fingerprint)
        finally:
            if self.lease is not None:
                try:
                    release(self.run_dir, self.lease.lease_token, "worker finished")
                except Exception as exc:  # noqa: BLE001 - reported; the lease then needs recovery
                    self.log(f"The lease could not be released ({exc}); recover it before resuming.")

    def _attempt_info(self) -> AttemptInfo | None:
        path = self.run_dir / "attempts" / str(self.attempt_id) / "attempt.json"
        if not path.is_file():
            return None
        return load_model(AttemptInfo, data=read_json(path))  # type: ignore[return-value]

    def _prepare_run_dir(self, bundle: Bundle) -> int | None:
        allocation_path = self.run_dir / "run.json"
        if allocation_path.is_file():
            allocation: RunAllocation = load_model(RunAllocation, data=read_json(allocation_path))  # type: ignore[assignment]
            if allocation.bundle_id != bundle.manifest.bundle_id or allocation.ready_digest != bundle.checksums_sha256:
                self.log("This results folder belongs to a different package (or a changed copy of it). Nothing was analyzed.")
                return EXIT_INVALID
            started = (self.run_dir / "results.json").is_file() or (self.run_dir / "tasks").exists()
            if started and not self.resume:
                self.log("This run already has results. Use --resume to continue it, or submit a new run.")
                return EXIT_INVALID
            self.run_id = allocation.run_id
        else:
            if self.resume:
                self.log("--resume needs an existing run folder with run.json.")
                return EXIT_INVALID
            if self.run_dir.exists() and any(self.run_dir.iterdir()):
                self.log(f"{self.run_dir} is not empty. A new run needs an empty results folder.")
                return EXIT_INVALID
            self.run_dir.mkdir(parents=True, exist_ok=True)
            self.run_id = self.run_dir.name if self.run_dir.name.startswith("run_") else new_id("run_", 12)
            allocation = RunAllocation(
                run_id=self.run_id,
                bundle_id=bundle.manifest.bundle_id,
                ready_digest=bundle.checksums_sha256,
                package_name=bundle.manifest.package_name,
                created_at=utc_now(),
            )
            write_json_atomic(allocation_path, allocation.model_dump(mode="json"))
        if self.attempt_id is None:
            self.attempt_id, _ = allocate_attempt(self.run_dir, self.run_id, resume=self.resume)
        elif not (self.run_dir / "attempts" / self.attempt_id / "attempt.json").is_file():
            attempt_dir = self.run_dir / "attempts" / self.attempt_id
            (attempt_dir / "logs").mkdir(parents=True, exist_ok=True)
            info = AttemptInfo(attempt_id=self.attempt_id, run_id=self.run_id, created_at=utc_now(), resume=self.resume)
            write_json_atomic(attempt_dir / "attempt.json", info.model_dump(mode="json"))
        return None

    # -- index ---------------------------------------------------------------------------------------------

    def _write_index(self) -> None:
        assert self.index is not None
        self.index.updated_at = utc_now()
        write_json_atomic(self.run_dir / "results.json", self.index.model_dump(mode="json"))

    def _state(self, acquisition_id: str) -> AcquisitionState:
        assert self.index is not None
        return next(item for item in self.index.acquisitions if item.acquisition_id == acquisition_id)

    def _load_or_create_index(self, bundle: Bundle, runtime_fingerprint: str) -> None:
        path = self.run_dir / "results.json"
        planned = [item.acquisition_id for item in bundle.manifest.acquisitions]
        if path.is_file():
            index: ResultIndex = load_model(ResultIndex, data=read_json(path))  # type: ignore[assignment]
            if [item.acquisition_id for item in index.acquisitions] != planned:
                raise PublicationError("results.json does not list this package's acquisitions.")
        else:
            now = utc_now()
            index = ResultIndex(
                bundle_id=bundle.manifest.bundle_id,
                ready_digest=bundle.checksums_sha256,
                run_id=self.run_id,
                created_at=now,
                updated_at=now,
                acquisitions=[AcquisitionState(acquisition_id=item) for item in planned],
            )
        self.index = index
        # Reconcile with what is actually published: only hash-verified successes of this exact task count.
        by_id = {item.acquisition_id: item for item in bundle.manifest.acquisitions}
        for state in index.acquisitions:
            acquisition = by_id[state.acquisition_id]
            found = None
            notes = []
            task_root = self.run_dir / "tasks" / state.acquisition_id
            candidates = sorted(path for path in task_root.iterdir() if path.is_dir()) if task_root.is_dir() else []
            for task_dir in candidates:
                if not (task_dir / "COMMIT.json").is_file():
                    continue
                record, why = verified_commit(task_dir)
                if record is None:
                    notes.append(f"{task_dir.name}: not reusable ({why})")
                    continue
                if record.task_key != acquisition.task_key or record.runtime_sha256 != bundle.manifest.runtime_sha256:
                    notes.append(f"{task_dir.name}: made for different settings or software")
                    continue
                if record.runtime_fingerprint != runtime_fingerprint:
                    notes.append(f"{task_dir.name}: made with a different installed runtime")
                    continue
                found = (task_dir, record)
            if found is not None:
                task_dir, record = found
                state.state = "succeeded"
                state.attempt_id = record.attempt_id
                state.commit_path = (task_dir / "COMMIT.json").relative_to(self.run_dir).as_posix()
                state.commit_sha256 = sha256_file(task_dir / "COMMIT.json")
                state.message = ""
            else:
                if state.state == "running":
                    state.state = "interrupted"
                    notes.append("the previous job ended without finishing this image")
                elif state.state == "succeeded":
                    state.state = "interrupted"
                    notes.append("its published result could not be verified")
                state.commit_path = None
                state.commit_sha256 = None
                state.message = "; ".join(notes)
        index.attempts.append(
            AttemptRecord(
                attempt_id=str(self.attempt_id),
                started_at=utc_now(),
                job_id=os.environ.get("SLURM_JOB_ID") or None,
                host=socket.gethostname(),
            )
        )
        index.compute_outcome = "running"
        index.finalized = False
        self._set_publication_outcome()
        self._write_index()

    def _set_publication_outcome(self, failed: bool = False) -> None:
        assert self.index is not None
        done = [item for item in self.index.acquisitions if item.state == "succeeded"]
        if failed:
            self.index.publication_outcome = "failed"
        elif not done:
            self.index.publication_outcome = "not_published"
        elif len(done) == len(self.index.acquisitions):
            self.index.publication_outcome = "verified_complete"
        else:
            self.index.publication_outcome = "verified_partial"

    # -- the work ------------------------------------------------------------------------------------------

    def _work(self, bundle: Bundle, runtime_fingerprint: str) -> int:
        try:
            self._load_or_create_index(bundle, runtime_fingerprint)
        except (PublicationError, OSError) as exc:
            self.log(f"The run's result index could not be written: {exc}")
            return EXIT_PUBLICATION
        assert self.index is not None
        publication_failed = False
        cancelled = False
        todo = [item for item in bundle.manifest.acquisitions if self._state(item.acquisition_id).state != "succeeded"]
        self.log(f"{len(todo)} of {len(bundle.manifest.acquisitions)} images to analyze in run {self.run_id}, attempt {self.attempt_id}.")
        for number, acquisition in enumerate(todo, start=1):
            if self.stop.requested:
                cancelled = True
                break
            state = self._state(acquisition.acquisition_id)
            state.state = "running"
            state.attempt_id = self.attempt_id
            state.message = ""
            try:
                self._write_index()
            except OSError as exc:
                self.log(f"Writing results.json failed: {exc}")
                publication_failed = True
                break
            self.log(f"[{number}/{len(todo)}] {acquisition.acquisition_id} {acquisition.source_relative_path}")
            started = utc_now()
            try:
                result, local_dir = self._analyze(bundle, acquisition, runtime_fingerprint)
            except progress.AnalysisCancelled:
                state.state = "cancelled"
                state.message = "stopped during this image; nothing from it was kept"
                self._record_failure(bundle, acquisition, runtime_fingerprint, started, "cancelled", "E_CANCELLED", state.message)
                cancelled = True
                break
            except Exception as exc:  # noqa: BLE001 - one image's failure must not stop the others
                code = getattr(exc, "code", None) or "E_ANALYSIS"
                state.state = "failed"
                state.message = str(exc.args[0] if exc.args else exc)
                self.log(f"  failed: {state.message}")
                try:
                    self._record_failure(bundle, acquisition, runtime_fingerprint, started, "failed", code, state.message, traceback.format_exc())
                except PublicationError as publish_error:
                    self.log(f"  the failure record could not be published: {publish_error}")
                    publication_failed = True
                    break
                continue
            try:
                self._publish(bundle, acquisition, result, local_dir, runtime_fingerprint, started, state)
            except PublicationError as exc:
                state.state = "failed"
                state.message = f"analyzed, but publishing failed: {exc}"
                self.log(f"  {state.message}")
                publication_failed = True
                break
            self.log(f"  done: {result.qc.n_objects} objects" + (f"; {result.qc.warnings[0]}" if result.qc.warnings else ""))
        return self._finish(publication_failed, cancelled)

    def _analyze(self, bundle: Bundle, acquisition, runtime_fingerprint: str):
        from cellquant.image import read_stack, reduce_stack
        from cellquant.pipeline import process_image
        from cellquant.storage import persist_image_result

        def cancelled() -> bool:
            return self.stop.now.is_set()

        with progress.reporting(lambda _text, _fraction: None, cancelled):
            path = bundle.input_file(acquisition)
            stack = read_stack(path, position=0)
            czyx = stack.czyx
            if list(czyx.shape) != acquisition.shape_czyx or str(czyx.dtype) != acquisition.dtype:
                raise _Failure("E_PIXELS", f"The prepared image has shape {list(czyx.shape)} {czyx.dtype}, expected {acquisition.shape_czyx} {acquisition.dtype}.")
            if pixel_sha256(czyx) != acquisition.pixel_sha256:
                raise _Failure("E_PIXELS", "The prepared pixels differ from the ones recorded at preparation.")
            x, y, z = acquisition.effective_spacing_xyz_um
            loaded = reduce_stack(
                stack,
                z_mode=bundle.recipe.z_stack,
                z_index=acquisition.effective_z_index,
                pixel_size_x=x,
                pixel_size_y=y,
                pixel_size_z=z,
            )
            if (loaded.z_mode, loaded.z_index) != (acquisition.effective_z_mode, acquisition.effective_z_index):
                raise _Failure("E_Z", f"The image was read as {loaded.z_mode}, but the package says {acquisition.effective_z_mode}.")
            colors = tuple(tuple(color) if color else None for color in acquisition.channel_colors) if acquisition.channel_colors else ()
            loaded = replace(
                loaded,
                source_path=acquisition.input_path,
                channel_names=tuple(acquisition.channel_names),
                channel_colors=colors,  # type: ignore[arg-type]
                objective=acquisition.objective,
            )
            del stack, czyx
            result = process_image(
                loaded,
                bundle.recipe,
                sample_name=acquisition.sample_name,
                image_id=acquisition.acquisition_id,
                experiment_id=bundle.manifest.source_experiment_id,
                run_id=self.run_id,
                filename=Path(acquisition.source_relative_path).name,
                user_metadata=acquisition.user_metadata,
            )
        engine = result.provenance.get("segmentation_engine") or {}
        device = str(engine.get("device") or "")
        if self.require_gpu and device != "cuda":
            raise _Failure("E_DEVICE", f"Cellpose ran on '{device or 'unknown'}', not the GPU. Results made on the CPU are not kept.")
        settings = segmentation_settings(bundle.recipe, acquisition.effective_z_index)
        key = lineage_key(
            input_sha256=acquisition.input_file_sha256,
            position=0,
            calibration=list(acquisition.effective_spacing_xyz_um),
            settings=settings,
            runtime=bundle.manifest.runtime_sha256,
            labels=result.automated_labels,
        )
        result.provenance.update(
            {
                "segmentation_origin": "hpc",
                "segmentation_key": key,
                "segmentation_settings": settings,
                "segmentation_input_sha256": acquisition.input_file_sha256,
                "segmentation_calibration": list(acquisition.effective_spacing_xyz_um),
                "hpc": {
                    "bundle_id": bundle.manifest.bundle_id,
                    "package_name": bundle.manifest.package_name,
                    "run_id": self.run_id,
                    "attempt_id": self.attempt_id,
                    "acquisition_id": acquisition.acquisition_id,
                    "task_key": acquisition.task_key,
                    "source_experiment_id": bundle.manifest.source_experiment_id,
                    "source_image_id": acquisition.source_image_id,
                    "source_relative_path": acquisition.source_relative_path,
                    "source_position": acquisition.source_position,
                    "source_file_sha256": acquisition.source_file_sha256,
                    "input_path": acquisition.input_path,
                    "input_file_sha256": acquisition.input_file_sha256,
                    "pixel_sha256": acquisition.pixel_sha256,
                    "runtime_id": bundle.runtime.runtime_id,
                    "runtime_sha256": bundle.manifest.runtime_sha256,
                    "runtime_fingerprint": runtime_fingerprint,
                    "device": device,
                    "host": socket.gethostname(),
                    "job_id": os.environ.get("SLURM_JOB_ID", ""),
                },
            }
        )
        local_dir = self.scratch / acquisition.acquisition_id / str(self.attempt_id)
        if local_dir.exists():
            shutil.rmtree(local_dir)
        local_dir.mkdir(parents=True)
        persist_image_result(local_dir, result, [])
        self._check_written(local_dir, acquisition.acquisition_id, result)
        return result, local_dir

    def _check_written(self, local_dir: Path, acquisition_id: str, result) -> None:
        from cellquant.storage import read_persisted_result

        again = read_persisted_result(local_dir, acquisition_id)
        if again is None or again.labels.shape != result.labels.shape or len(again.objects) != len(result.objects):
            raise _Failure("E_WRITE", "The saved result could not be read back.")

    def _publish(self, bundle, acquisition, result, local_dir: Path, runtime_fingerprint: str, started: str, state) -> None:
        artifacts = hash_tree(local_dir)
        destination = self.run_dir / "tasks" / acquisition.acquisition_id / str(self.attempt_id)
        publish_directory(local_dir, destination, artifacts)
        from cellquant.storage import _qc_to_json

        record = TaskResult(
            bundle_id=bundle.manifest.bundle_id,
            run_id=self.run_id,
            acquisition_id=acquisition.acquisition_id,
            task_key=acquisition.task_key,
            attempt_id=str(self.attempt_id),
            outcome="succeeded",
            started_at=started,
            ended_at=utc_now(),
            runtime_fingerprint=runtime_fingerprint,
            runtime_sha256=bundle.manifest.runtime_sha256,
            device=str((result.provenance.get("hpc") or {}).get("device") or ""),
            artifacts=artifacts,
            qc=_qc_to_json(result.qc),
            warnings=list(result.qc.warnings),
        )
        commit_sha = write_commit(destination, record)
        if self.index is not None and self.index.attempts and not self.index.attempts[-1].device:
            self.index.attempts[-1].device = record.device
        state.state = "succeeded"
        state.commit_path = (destination / "COMMIT.json").relative_to(self.run_dir).as_posix()
        state.commit_sha256 = commit_sha
        self._set_publication_outcome()
        try:
            self._write_index()
        except OSError as exc:
            raise PublicationError(f"results.json could not be updated: {exc}") from exc

    def _record_failure(self, bundle, acquisition, runtime_fingerprint, started, outcome, code, message, trace: str = "") -> None:
        destination = self.run_dir / "tasks" / acquisition.acquisition_id / str(self.attempt_id)
        record = TaskResult(
            bundle_id=bundle.manifest.bundle_id,
            run_id=self.run_id,
            acquisition_id=acquisition.acquisition_id,
            task_key=acquisition.task_key,
            attempt_id=str(self.attempt_id),
            outcome=outcome,
            started_at=started,
            ended_at=utc_now(),
            runtime_fingerprint=runtime_fingerprint,
            runtime_sha256=bundle.manifest.runtime_sha256,
            error=ErrorDetail(code=code, message=message, traceback=trace[-20000:]),
        )
        try:
            destination.mkdir(parents=True, exist_ok=True)
            write_commit(destination, record)
            self._write_index()
        except OSError as exc:
            raise PublicationError(str(exc)) from exc

    def _finish(self, publication_failed: bool, cancelled: bool) -> int:
        assert self.index is not None
        states = [item.state for item in self.index.acquisitions]
        succeeded = states.count("succeeded")
        failed = states.count("failed")
        unfinished = len(states) - succeeded - failed
        if cancelled:
            outcome = "cancelled"
        elif publication_failed:
            outcome = "interrupted"
        elif unfinished:
            outcome = "interrupted"
        elif failed and succeeded:
            outcome = "completed_with_failures"
        elif failed:
            outcome = "failed"
        else:
            outcome = "completed"
        self.index.compute_outcome = outcome  # type: ignore[assignment]
        self._set_publication_outcome(failed=publication_failed)
        self.index.finalized = True
        attempt = self.index.attempts[-1]
        attempt.ended_at = utc_now()
        attempt.compute_outcome = outcome  # type: ignore[assignment]
        attempt.publication_outcome = self.index.publication_outcome
        try:
            self._write_index()
        except OSError as exc:
            self.log(f"results.json could not be finalized: {exc}")
            publication_failed = True
        self.log(
            f"Finished: {succeeded} succeeded, {failed} failed, {unfinished} not finished. "
            f"Compute {outcome}; publication {self.index.publication_outcome}."
        )
        if publication_failed:
            self.log(f"Publishing failed. Scratch results are kept in {self.scratch}.")
            return EXIT_PUBLICATION
        if cancelled:
            return EXIT_CANCELLED
        if outcome != "completed":
            return EXIT_UNFINISHED
        return EXIT_OK


class _Failure(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
