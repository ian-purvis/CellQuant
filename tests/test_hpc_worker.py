"""The cluster worker: headless, transport parity, state accounting, cancel, leases, resume, publication (AC06-AC11)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cellquant.hpc import lease as lease_module
from cellquant.hpc.common import new_id, read_json, utc_now
from cellquant.hpc.import_results import ImportFailed, import_results
from cellquant.hpc.models import LeaseRecord
from cellquant.hpc.publish import PublicationError
from cellquant.hpc.runner import EXIT_CANCELLED, EXIT_INVALID, EXIT_OK, EXIT_PUBLICATION, EXIT_RUNTIME, EXIT_UNFINISHED, Runner, Stop
from cellquant.hpc.validate import open_bundle
from cellquant.inputs import load_record_image
from cellquant.pipeline import process_image
from cellquant.storage import read_persisted_result
from tests.hpc_helpers import install_fake_cellpose, matching_observer, prepared_package

ROOT = Path(__file__).resolve().parents[1]


def _run(package: Path, run_dir: Path, runtime_bytes: bytes, **options) -> int:
    runner = Runner(package, run_dir.parent / "scratch" / run_dir.name, run_dir, observe=matching_observer(runtime_bytes), log=lambda _text: None, **options)
    return runner.run()


def _index(run_dir: Path) -> dict:
    return read_json(run_dir / "results.json")


def _states(run_dir: Path) -> list[str]:
    return [item["state"] for item in _index(run_dir)["acquisitions"]]


# --- AC06: headless -------------------------------------------------------------------------------------


def test_the_worker_imports_without_a_display():
    code = textwrap.dedent(
        """
        import sys
        import cellquant.hpc.runner, cellquant.hpc.preflight, cellquant.hpc.__main__
        from cellquant.hpc.runner import Runner
        import cellquant.pipeline
        bad = [m for m in sys.modules if m.split('.')[0] in ('napari', 'qtpy', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'cellquant.gui')]
        bad += [m for m in sys.modules if m.startswith('cellquant.gui') or m == 'cellquant.controller']
        print(bad)
        assert sys.version_info >= (3, 11)
        """
    )
    output = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, check=True)
    assert output.stdout.strip() == "[]"


# --- AC07: the prepared copy gives the same results as the original -------------------------------------


@pytest.mark.parametrize("mode", ["max_projection", "single_plane", "stitch_slices", "full_3d"])
def test_transport_parity_in_every_mode(tmp_path: Path, monkeypatch, mode: str):
    install_fake_cellpose(tmp_path, monkeypatch)
    controller, package, runtime_bytes = prepared_package(tmp_path, z_stack=mode)
    run_dir = tmp_path / "results" / "run_parity"
    assert _run(package, run_dir, runtime_bytes) == EXIT_OK
    for record in controller.experiment.images:
        original = load_record_image(record, z_mode=controller.recipe.z_stack, z_index=controller.recipe.z_index, experiment_dir=controller.directory)
        local = process_image(original, controller.recipe, sample_name=record.sample_name, image_id="x", filename=record.filename, user_metadata=record.user_metadata)
        acquisition_id = next(
            item.acquisition_id for item in open_bundle(package).manifest.acquisitions if item.source_image_id == record.image_id
        )
        task = next((run_dir / "tasks" / acquisition_id).iterdir())
        remote = read_persisted_result(task, acquisition_id)
        assert np.array_equal(local.automated_labels, remote.automated_labels)
        assert np.array_equal(local.labels, remote.labels)
        columns = [column for column in local.objects.columns if column not in ("image_id", "run_id", "experiment_id")]
        pd.testing.assert_frame_equal(
            local.objects[columns].reset_index(drop=True), remote.objects[columns].reset_index(drop=True), check_exact=False, rtol=1e-6, atol=1e-8
        )
        pd.testing.assert_frame_equal(local.reports.reset_index(drop=True), remote.reports.reset_index(drop=True), check_exact=False, rtol=1e-6)
        pd.testing.assert_frame_equal(local.combination_counts, remote.combination_counts)
        assert remote.provenance["segmentation_origin"] == "hpc" and remote.provenance["hpc"]["device"] == "cuda"


# --- the worker's own rules ----------------------------------------------------------------------------


def test_a_runtime_mismatch_stops_before_any_analysis(tmp_path: Path, monkeypatch):
    install_fake_cellpose(tmp_path, monkeypatch)
    _controller, package, runtime_bytes = prepared_package(tmp_path)
    contract = json.loads(runtime_bytes)
    contract["packages"]["numpy"] = "1.0"
    run_dir = tmp_path / "results" / "run_bad_runtime"
    assert _run(package, run_dir, json.dumps(contract).encode()) == EXIT_RUNTIME
    assert not (run_dir / "results.json").exists() and not (run_dir / "tasks").exists()
    report = read_json(next((run_dir / "attempts").iterdir()) / "runtime_report.json")
    assert report["issues"][0]["code"] == "E_RUNTIME_PACKAGES"


def test_results_made_on_the_cpu_are_not_kept(tmp_path: Path, monkeypatch):
    install_fake_cellpose(tmp_path, monkeypatch, device="cpu")
    _controller, package, runtime_bytes = prepared_package(tmp_path)
    run_dir = tmp_path / "results" / "run_cpu"
    assert _run(package, run_dir, runtime_bytes) == EXIT_UNFINISHED
    index = _index(run_dir)
    assert _states(run_dir) == ["failed", "failed"] and index["compute_outcome"] == "failed"
    failed = read_json(next((run_dir / "tasks" / "a000001").iterdir()) / "FAILED.json")
    assert failed["error"]["code"] == "E_DEVICE"


def test_a_damaged_scratch_copy_is_refused(tmp_path: Path, monkeypatch):
    install_fake_cellpose(tmp_path, monkeypatch)
    _controller, package, runtime_bytes = prepared_package(tmp_path)
    path = package / "inputs" / "a000002.ome.tif"
    path.write_bytes(path.read_bytes()[:-10])
    run_dir = tmp_path / "results" / "run_damaged"
    assert _run(package, run_dir, runtime_bytes) == EXIT_INVALID
    assert not (run_dir / "results.json").exists()


def test_a_fresh_run_needs_an_empty_folder_and_resume_needs_the_same_package(tmp_path: Path, monkeypatch):
    install_fake_cellpose(tmp_path, monkeypatch)
    _controller, package, runtime_bytes = prepared_package(tmp_path)
    run_dir = tmp_path / "results" / "run_a"
    assert _run(package, run_dir, runtime_bytes) == EXIT_OK
    assert _run(package, run_dir, runtime_bytes) == EXIT_INVALID  # already has results; --resume is needed
    other = tmp_path / "results" / "junk"
    other.mkdir()
    (other / "file.txt").write_text("x")
    assert _run(package, other, runtime_bytes) == EXIT_INVALID
    _controller2, package2, runtime2 = prepared_package(tmp_path / "second")
    assert _run(package2, run_dir, runtime2, resume=True) == EXIT_INVALID
    assert _run(package, run_dir, runtime_bytes, resume=True) == EXIT_OK  # nothing left to do
    assert len(_index(run_dir)["attempts"]) == 2


# --- AC09: cancelling -------------------------------------------------------------------------------------


def test_cancel_before_the_first_image(tmp_path: Path, monkeypatch):
    install_fake_cellpose(tmp_path, monkeypatch)
    _controller, package, runtime_bytes = prepared_package(tmp_path)
    stop = Stop()
    stop.soon.set()
    run_dir = tmp_path / "results" / "run_c0"
    assert _run(package, run_dir, runtime_bytes, stop=stop) == EXIT_CANCELLED
    index = _index(run_dir)
    assert _states(run_dir) == ["pending", "pending"]
    assert index["compute_outcome"] == "cancelled" and index["publication_outcome"] == "not_published" and index["finalized"]


def test_cancel_during_an_image_keeps_nothing_from_it(tmp_path: Path, monkeypatch):
    install_fake_cellpose(tmp_path, monkeypatch)
    _controller, package, runtime_bytes = prepared_package(tmp_path)
    stop = Stop()
    import cellquant.pipeline as pipeline

    real = pipeline.segment_channel

    def segment_then_stop(*args, **kwargs):
        labels = real(*args, **kwargs)
        stop.now.set()  # scancel arrives while this image is being analyzed
        stop.soon.set()
        return labels

    monkeypatch.setattr(pipeline, "segment_channel", segment_then_stop)
    run_dir = tmp_path / "results" / "run_c1"
    assert _run(package, run_dir, runtime_bytes, stop=stop) == EXIT_CANCELLED
    assert _states(run_dir) == ["cancelled", "pending"]
    assert not any((run_dir / "tasks" / "a000001").glob("*/COMMIT.json"))
    assert _index(run_dir)["compute_outcome"] == "cancelled"


def test_cancel_after_one_image_and_partial_import(tmp_path: Path, monkeypatch):
    install_fake_cellpose(tmp_path, monkeypatch)
    _controller, package, runtime_bytes = prepared_package(tmp_path, count=3)
    stop = Stop()
    import cellquant.hpc.runner as runner_module

    real = runner_module.write_commit

    def commit_then_stop(task_dir, record):
        digest = real(task_dir, record)
        stop.soon.set()
        return digest

    monkeypatch.setattr(runner_module, "write_commit", commit_then_stop)
    run_dir = tmp_path / "results" / "run_c2"
    assert _run(package, run_dir, runtime_bytes, stop=stop) == EXIT_CANCELLED
    assert _states(run_dir) == ["succeeded", "pending", "pending"]
    assert _index(run_dir)["compute_outcome"] == "cancelled"
    assert _index(run_dir)["publication_outcome"] == "verified_partial"
    with pytest.raises(ImportFailed) as refused:
        import_results(package, run_dir, tmp_path / "imported")
    assert refused.value.exit_code == 4 and "a000002 (pending)" in str(refused.value)
    outcome = import_results(package, run_dir, tmp_path / "imported", allow_partial=True)
    assert [item.imported_result for item in outcome.record.acquisitions] == [True, False, False] and outcome.record.partial


# --- AC10: hard termination, leases, resume ---------------------------------------------------------


class FakeScheduler:
    def __init__(self, accounting, queue):
        self.accounting, self.queue = accounting, queue

    def accounting_states(self, job_id):
        return self.accounting

    def queue_states(self, job_id):
        return self.queue


def _killed_run(tmp_path: Path, monkeypatch):
    """A job killed after publishing the second image's files but before its commit record."""

    install_fake_cellpose(tmp_path, monkeypatch)
    _controller, package, runtime_bytes = prepared_package(tmp_path, count=3)
    import cellquant.hpc.runner as runner_module

    real = runner_module.write_commit
    calls = {"n": 0}

    def killed(task_dir, record):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt("SIGKILL")  # the process is gone; nothing else runs
        return real(task_dir, record)

    monkeypatch.setattr(runner_module, "write_commit", killed)
    monkeypatch.setattr(runner_module, "release", lambda *args, **kwargs: None)  # a killed job cannot release
    monkeypatch.setenv("SLURM_JOB_ID", "4242")
    run_dir = tmp_path / "results" / "run_k"
    with pytest.raises(KeyboardInterrupt):
        _run(package, run_dir, runtime_bytes)
    monkeypatch.setattr(runner_module, "write_commit", real)
    return package, runtime_bytes, run_dir


def test_a_killed_job_leaves_no_false_success_and_blocks_the_run(tmp_path: Path, monkeypatch):
    package, runtime_bytes, run_dir = _killed_run(tmp_path, monkeypatch)
    index = _index(run_dir)
    assert _states(run_dir) == ["succeeded", "running", "pending"] and not index["finalized"]
    task = next((run_dir / "tasks" / "a000002").iterdir())
    assert (task / "labels").is_dir() and not (task / "COMMIT.json").exists()
    import cellquant.hpc.runner as runner_module

    monkeypatch.setattr(runner_module, "release", lease_module.release)
    assert _run(package, run_dir, runtime_bytes, resume=True) == EXIT_INVALID  # the old job still owns the run


@pytest.mark.parametrize(
    "accounting, queue, allowed",
    [
        (["RUNNING"], ["RUNNING"], False),
        (["REQUEUED"], [], False),
        (None, [], False),
        (["TIMEOUT"], None, False),
        ([], [], False),
        (["PREEMPTED"], [], False),
        (["CANCELLED"], ["PENDING"], False),
        (["TIMEOUT"], [], True),
        (["NODE_FAIL", "CANCELLED"], [], True),
    ],
)
def test_recovery_needs_proof_that_the_job_ended(tmp_path: Path, accounting, queue, allowed):
    run_dir = tmp_path / "run"
    record = LeaseRecord(lease_token=new_id(length=32), ready_digest="a" * 64, run_id="run_1", attempt_id="att_1", job_id="77", acquired_at=utc_now())
    lease_module.acquire(run_dir, record)
    arguments = dict(run_id="run_1", attempt_id="att_1", job_id="77", expected_token=record.lease_token, ready_digest="a" * 64, scheduler=FakeScheduler(accounting, queue))
    if allowed:
        evidence = lease_module.recover(run_dir, **arguments)
        assert evidence["accounting_states"] == accounting and lease_module.current(run_dir) is None
        history = [json.loads(line) for line in (run_dir / "lease_history.jsonl").read_text().splitlines()]
        assert [item["event"] for item in history] == ["acquired", "recovered"]
    else:
        with pytest.raises(lease_module.RecoveryRefused):
            lease_module.recover(run_dir, **arguments)
        assert lease_module.current(run_dir) is not None


def test_recovery_refuses_a_wrong_token_or_job(tmp_path: Path):
    run_dir = tmp_path / "run"
    record = LeaseRecord(lease_token="t" * 32, ready_digest="a" * 64, run_id="run_1", attempt_id="att_1", job_id="77", acquired_at=utc_now())
    lease_module.acquire(run_dir, record)
    for change in ({"expected_token": "x" * 32}, {"job_id": "78"}, {"attempt_id": "att_2"}, {"ready_digest": "b" * 64}):
        arguments = dict(run_id="run_1", attempt_id="att_1", job_id="77", expected_token="t" * 32, ready_digest="a" * 64, scheduler=FakeScheduler(["COMPLETED"], []))
        arguments.update(change)
        with pytest.raises(lease_module.RecoveryRefused):
            lease_module.recover(run_dir, **arguments)
    assert lease_module.current(run_dir).lease_token == "t" * 32


def test_two_owners_cannot_share_a_run(tmp_path: Path):
    run_dir = tmp_path / "run"
    first = LeaseRecord(lease_token="1" * 32, ready_digest="a" * 64, run_id="r", attempt_id="a1", acquired_at=utc_now())
    second = first.model_copy(update={"lease_token": "2" * 32, "attempt_id": "a2"})
    lease_module.acquire(run_dir, first)
    with pytest.raises(lease_module.LeaseHeld):
        lease_module.acquire(run_dir, second)
    assert lease_module.current(run_dir).lease_token == "1" * 32
    with pytest.raises(lease_module.RecoveryRefused):
        lease_module.release(run_dir, "2" * 32, "not the owner")
    lease_module.release(run_dir, "1" * 32, "done")
    lease_module.acquire(run_dir, second)


def test_after_recovery_resume_keeps_verified_work_and_redoes_the_rest(tmp_path: Path, monkeypatch):
    package, runtime_bytes, run_dir = _killed_run(tmp_path, monkeypatch)
    import cellquant.hpc.runner as runner_module

    monkeypatch.setattr(runner_module, "release", lease_module.release)
    held = lease_module.current(run_dir)
    bundle = open_bundle(package)
    lease_module.recover(
        run_dir, run_id=held.run_id, attempt_id=held.attempt_id, job_id=held.job_id, expected_token=held.lease_token,
        ready_digest=bundle.checksums_sha256, scheduler=FakeScheduler(["CANCELLED"], []),
    )
    first_commit = next((run_dir / "tasks" / "a000001").iterdir()) / "COMMIT.json"
    stamp = first_commit.stat().st_mtime_ns
    analyzed = []
    import cellquant.hpc.runner as runner_module

    real = runner_module.Runner._analyze

    def counting(self, bundle, acquisition, fingerprint):
        analyzed.append(acquisition.acquisition_id)
        return real(self, bundle, acquisition, fingerprint)

    monkeypatch.setattr(runner_module.Runner, "_analyze", counting)
    assert _run(package, run_dir, runtime_bytes, resume=True) == EXIT_OK
    assert analyzed == ["a000002", "a000003"] and first_commit.stat().st_mtime_ns == stamp
    assert _states(run_dir) == ["succeeded"] * 3 and _index(run_dir)["compute_outcome"] == "completed"
    assert len(list((run_dir / "tasks" / "a000002").iterdir())) == 2  # the unfinished attempt is kept for diagnosis


def test_a_corrupted_success_is_reported_and_redone(tmp_path: Path, monkeypatch):
    install_fake_cellpose(tmp_path, monkeypatch)
    _controller, package, runtime_bytes = prepared_package(tmp_path)
    run_dir = tmp_path / "results" / "run_corrupt"
    assert _run(package, run_dir, runtime_bytes) == EXIT_OK
    task = next((run_dir / "tasks" / "a000001").iterdir())
    (task / "measurements" / "a000001.csv").write_text("damaged")
    assert _run(package, run_dir, runtime_bytes, resume=True) == EXIT_OK
    index = _index(run_dir)
    assert index["acquisitions"][0]["state"] == "succeeded" and index["acquisitions"][0]["attempt_id"] != task.name
    assert task.is_dir()  # kept for diagnosis


# --- AC11: publication failures ----------------------------------------------------------------------------


def test_a_failed_copy_keeps_scratch_and_earlier_results(tmp_path: Path, monkeypatch):
    install_fake_cellpose(tmp_path, monkeypatch)
    _controller, package, runtime_bytes = prepared_package(tmp_path, count=3)
    import cellquant.hpc.runner as runner_module

    real = runner_module.publish_directory
    calls = {"n": 0}

    def disk_full(source, destination, expected):
        calls["n"] += 1
        if calls["n"] == 2:
            raise PublicationError("No space left on device")
        return real(source, destination, expected)

    monkeypatch.setattr(runner_module, "publish_directory", disk_full)
    run_dir = tmp_path / "results" / "run_full"
    assert _run(package, run_dir, runtime_bytes) == EXIT_PUBLICATION
    index = _index(run_dir)
    assert _states(run_dir) == ["succeeded", "failed", "pending"]
    assert index["publication_outcome"] == "failed" and index["compute_outcome"] != "completed"
    scratch = tmp_path / "results" / "scratch" / "run_full" / "a000002"
    assert any(scratch.rglob("*.csv")), "scratch results must be kept"
    outcome = import_results(package, run_dir, tmp_path / "imported", allow_partial=True)
    assert [item.imported_result for item in outcome.record.acquisitions] == [True, False, False]


def test_the_log_is_in_the_durable_run_folder(tmp_path: Path, monkeypatch):
    install_fake_cellpose(tmp_path, monkeypatch)
    _controller, package, runtime_bytes = prepared_package(tmp_path)
    run_dir = tmp_path / "results" / "run_log"
    assert _run(package, run_dir, runtime_bytes) == EXIT_OK
    attempt = next((run_dir / "attempts").iterdir())
    assert "Finished: 2 succeeded" in (attempt / "worker.log").read_text()
    assert (attempt / "runtime_report.json").is_file() and (attempt / "validation.json").is_file()


# --- signals, in a real process -------------------------------------------------------------------------


def test_sigterm_stops_the_worker_cleanly(tmp_path: Path, monkeypatch):
    import signal
    import time

    install_fake_cellpose(tmp_path, monkeypatch)
    _controller, package, runtime_bytes = prepared_package(tmp_path, count=4)
    fake_root = next(path for path in tmp_path.iterdir() if path.name.startswith("cellpose_"))
    run_dir = tmp_path / "results" / "run_signal"
    script = tmp_path / "worker.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import sys, time, json
            sys.path.insert(0, {str(fake_root)!r}); sys.path.insert(0, {str(ROOT)!r})
            import cellpose.models as models
            real = models.CellposeModel.eval
            def slow(self, *args, **kwargs):
                time.sleep(1.5)
                return real(self, *args, **kwargs)
            models.CellposeModel.eval = slow
            from tests.hpc_helpers import matching_observer
            from cellquant.hpc.runner import Runner, Stop
            stop = Stop(); stop.install()
            runtime = open({str(package / 'runtime.json')!r}, 'rb').read()
            code = Runner({str(package)!r}, {str(tmp_path / 'scratch')!r}, {str(run_dir)!r}, observe=matching_observer(runtime), stop=stop).run()
            sys.exit(code)
            """
        )
    )
    process = subprocess.Popen([sys.executable, str(script)], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env={**os.environ, "FAKE_CELLPOSE_DEVICE": "cuda"})
    deadline = time.time() + 60
    while time.time() < deadline:
        if list(run_dir.glob("tasks/a000001/*/COMMIT.json")):
            break
        time.sleep(0.2)
    process.send_signal(signal.SIGTERM)
    output, _ = process.communicate(timeout=60)
    assert process.returncode == EXIT_CANCELLED, output
    states = _states(run_dir)
    assert states[0] == "succeeded" and "succeeded" not in states[2:] and set(states[1:]) <= {"cancelled", "pending", "succeeded"}
    assert _index(run_dir)["finalized"] and lease_module.current(run_dir) is None
