"""One owner per run.

A worker takes the run's lease before writing anything and gives it back when
it stops. Taking it is atomic: a hard link to ``lease.json`` succeeds for
exactly one contender (links are atomic on shared cluster filesystems, where
exclusive-create may not be). A job that was killed leaves its lease behind;
``recover`` retires it only when Slurm's accounting shows that job has ended
and nothing is queued or running under it. Every change is appended to
``lease_history.jsonl``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Protocol

from cellquant.hpc.common import read_json, utc_now
from cellquant.hpc.models import LeaseRecord, load_model

LEASE = "lease.json"
HISTORY = "lease_history.jsonl"
TERMINAL_STATES = frozenset(
    {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "NODE_FAIL", "OUT_OF_MEMORY", "BOOT_FAIL", "DEADLINE", "PREEMPTED"}
)
ACTIVE_STATES = frozenset(
    {"PENDING", "RUNNING", "REQUEUED", "REQUEUE_HOLD", "REQUEUE_FED", "RESIZING", "SUSPENDED", "CONFIGURING", "COMPLETING", "STOPPED", "SIGNALING", "STAGE_OUT", "RESV_DEL_HOLD", "SPECIAL_EXIT"}
)


class LeaseHeld(Exception):
    def __init__(self, current: LeaseRecord | None, message: str):
        super().__init__(message)
        self.current = current


class RecoveryRefused(Exception):
    pass


class Scheduler(Protocol):
    def accounting_states(self, job_id: str) -> list[str] | None:
        """Every accounting state recorded for the job's allocation (requeues included), or None if unknown."""

    def queue_states(self, job_id: str) -> list[str] | None:
        """States of the job in the live queue ([] when it is not queued), or None if the queue cannot be read."""


class SlurmScheduler:
    def __init__(self, timeout: float = 60):
        self.timeout = timeout

    def _run(self, command: list[str]) -> str | None:
        if shutil.which(command[0]) is None:
            return None
        try:
            output = subprocess.run(command, capture_output=True, text=True, timeout=self.timeout, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if output.returncode != 0:
            return None
        return output.stdout

    def accounting_states(self, job_id: str) -> list[str] | None:
        text = self._run(["sacct", "-j", str(job_id), "-X", "-D", "-n", "-P", "-o", "JobIDRaw,State"])
        if text is None:
            return None
        states = []
        for line in text.splitlines():
            parts = line.strip().split("|")
            if len(parts) >= 2 and parts[0].split("_")[0] == str(job_id):
                states.append(parts[1].split()[0].upper())
        return states

    def queue_states(self, job_id: str) -> list[str] | None:
        text = self._run(["squeue", "-h", "-j", str(job_id), "-o", "%T"])
        if text is None:
            # squeue exits non-zero for a job that has left the queue on some versions.
            listing = self._run(["squeue", "-h", "-o", "%i"])
            if listing is None:
                return None
            return [] if str(job_id) not in listing.split() else None
        return [line.strip().upper() for line in text.splitlines() if line.strip()]


def _history(run_dir: Path, event: dict) -> None:
    with (run_dir / HISTORY).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": utc_now(), **event}, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def current(run_dir: Path) -> LeaseRecord | None:
    path = run_dir / LEASE
    if not path.is_file():
        return None
    return load_model(LeaseRecord, data=read_json(path))  # type: ignore[return-value]


def acquire(run_dir: Path, record: LeaseRecord) -> LeaseRecord:
    """Take the run's lease, or raise LeaseHeld without writing anything else."""

    run_dir.mkdir(parents=True, exist_ok=True)
    temporary = run_dir / f".lease.{record.lease_token}.tmp"
    temporary.write_text(record.model_dump_json(indent=2), encoding="utf-8")
    target = run_dir / LEASE
    try:
        try:
            os.link(temporary, target)
        except (AttributeError, NotImplementedError, PermissionError):  # filesystems without hard links
            descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(record.model_dump_json(indent=2))
    except FileExistsError:
        held = None
        try:
            held = current(run_dir)
        except Exception:  # noqa: BLE001
            held = None
        owner = f"job {held.job_id or '?'} (attempt {held.attempt_id})" if held else "another worker"
        raise LeaseHeld(held, f"This run is owned by {owner}. Only one job may write to a run.") from None
    finally:
        temporary.unlink(missing_ok=True)
    _history(run_dir, {"event": "acquired", "lease": record.model_dump(mode="json")})
    return record


def _retire(run_dir: Path, token: str, event: dict) -> None:
    """Move the lease aside atomically, and only if it is still the one with this token."""

    claim = run_dir / f".lease.claim.{uuid.uuid4().hex[:8]}"
    try:
        os.rename(run_dir / LEASE, claim)
    except FileNotFoundError:
        raise RecoveryRefused("The run has no lease to release.") from None
    try:
        held = load_model(LeaseRecord, data=read_json(claim))
    except Exception:  # noqa: BLE001
        held = None
    if held is None or held.lease_token != token:  # type: ignore[union-attr]
        # Not ours: put it back unless someone has taken the run meanwhile.
        try:
            os.link(claim, run_dir / LEASE)
        except FileExistsError:
            pass
        claim.unlink(missing_ok=True)
        raise RecoveryRefused("The lease changed hands; nothing was released.")
    retired = run_dir / "lease_retired"
    retired.mkdir(exist_ok=True)
    os.rename(claim, retired / f"{token}.json")
    _history(run_dir, {**event, "lease": held.model_dump(mode="json")})  # type: ignore[union-attr]


def release(run_dir: Path, token: str, reason: str) -> None:
    _retire(run_dir, token, {"event": "released", "reason": reason})


def recover(
    run_dir: Path,
    *,
    run_id: str,
    attempt_id: str,
    job_id: str,
    expected_token: str,
    ready_digest: str,
    scheduler: Scheduler,
) -> dict:
    """Retire a dead job's lease after Slurm confirms the job has ended. Outputs are never touched."""

    held = current(run_dir)
    if held is None:
        raise RecoveryRefused("This run has no lease; nothing needs recovering.")
    mismatches = [
        name
        for name, found, wanted in (
            ("run ID", held.run_id, run_id),
            ("attempt ID", held.attempt_id, attempt_id),
            ("job ID", held.job_id, str(job_id)),
            ("lease token", held.lease_token, expected_token),
            ("package", held.ready_digest, ready_digest),
        )
        if found != wanted
    ]
    if mismatches:
        raise RecoveryRefused("The lease does not match: " + ", ".join(mismatches) + ". Check the values printed by submit.sh.")
    accounting = scheduler.accounting_states(str(job_id))
    queue = scheduler.queue_states(str(job_id))
    evidence = {"job_id": str(job_id), "accounting_states": accounting, "queue_states": queue, "checked_at": utc_now()}
    if accounting is None or queue is None:
        raise RecoveryRefused(f"Slurm could not report on job {job_id}, so the lease was kept. Try again later.")
    if queue:
        raise RecoveryRefused(f"Job {job_id} is still in the queue ({', '.join(queue)}). Wait for it to end or cancel it first.")
    if not accounting:
        raise RecoveryRefused(f"Slurm has no accounting record for job {job_id}, so its end cannot be confirmed.")
    if any(state in ACTIVE_STATES or state not in TERMINAL_STATES for state in accounting):
        raise RecoveryRefused(f"Job {job_id} is not confirmed ended (states: {', '.join(accounting)}).")
    if accounting[-1] == "PREEMPTED":
        raise RecoveryRefused(f"Job {job_id} was preempted and may be requeued; wait until Slurm shows it ended.")
    _retire(run_dir, expected_token, {"event": "recovered", "evidence": evidence})
    return evidence
