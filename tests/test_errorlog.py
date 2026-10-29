"""The CellQuant log, plain-English file errors, the catch-all hooks and the problem report."""

from __future__ import annotations

import errno
import logging
import sys
import threading
import zipfile

import pytest

from cellquant import errorlog


@pytest.fixture
def logs(tmp_path, monkeypatch):
    folder = tmp_path / "CellQuant"
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    yield folder
    errorlog.shutdown_logging()


def _flush():
    for handler in logging.getLogger("cellquant").handlers:
        handler.flush()


def test_log_folder_follows_the_platform_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert errorlog.log_folder() == tmp_path / "CellQuant"
    monkeypatch.delenv("LOCALAPPDATA")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert errorlog.log_folder() == tmp_path / ".cellquant"


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (errno.EACCES, "Permission denied: C:/data/a.csv. The file may be open in another program, read-only, or still syncing"),
        (errno.ENOSPC, "disk is full"),
        (errno.ENOENT, "Not found: C:/data/a.csv"),
        (errno.ENAMETOOLONG, "path is too long: C:/data/a.csv"),
    ],
)
def test_os_error_text_for_errno(code, expected):
    text = errorlog.os_error_text(OSError(code, "whatever", "C:/data/a.csv"))
    assert expected in text
    if code != errno.EACCES:
        assert "C:/data/a.csv" in text
    assert "Close it or wait, then try again." in errorlog.os_error_text(PermissionError(13, "x", "p"))


def test_os_error_text_defaults_and_plain_sentences():
    assert errorlog.os_error_text(OSError(errno.EIO, "Input/output error", "d.tif")) == "Input/output error: d.tif"
    assert errorlog.os_error_text(OSError(errno.EIO, "Input/output error")) == "Input/output error"
    assert errorlog.os_error_text(OSError("The image could not be read.")) == "The image could not be read."
    assert errorlog.os_error_text(PermissionError(errno.EACCES, "Permission denied")).startswith("Permission denied. The file may")


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="winerror exists on Windows only")
@pytest.mark.parametrize(
    ("winerror", "expected"),
    [(5, "Permission denied: f.csv"), (32, "f.csv is in use by another program"), (112, "disk is full"), (2, "Not found: f.csv"), (206, "too long: f.csv")],
)
def test_os_error_text_for_winerror(winerror, expected):
    assert expected in errorlog.os_error_text(OSError(None, "message", "f.csv", winerror))


def test_setup_creates_a_rotating_log_with_a_header(logs):
    folder = errorlog.setup_logging(crash_log=False, detect_gpu=False)
    assert folder == logs
    handlers = [h for h in logging.getLogger("cellquant").handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert len(handlers) == 1
    handler = handlers[0]
    assert (handler.maxBytes, handler.backupCount, handler.encoding) == (2 * 1024 * 1024, 5, "utf-8")
    assert handler.baseFilename == str(logs / "cellquant.log")
    errorlog.setup_logging(crash_log=False, detect_gpu=False)  # again: still one handler
    assert len([h for h in logging.getLogger("cellquant").handlers if isinstance(h, logging.handlers.RotatingFileHandler)]) == 1
    _flush()
    text = (logs / "cellquant.log").read_text(encoding="utf-8")
    from cellquant import __version__

    assert f"CellQuant: {__version__}" in text and "Python: " in text and "OS: " in text
    assert "napari: " in text and "cellpose: " in text and "torch: " in text and "GPU: " in text
    assert " INFO cellquant: " in text and "T" in text.split()[0]  # ISO timestamp, level, logger name


def test_header_text_never_raises(monkeypatch):
    monkeypatch.setattr(errorlog.platform, "platform", lambda: 1 / 0)
    assert "OS: unknown" in errorlog.environment_text()


def test_log_exception_writes_the_traceback_and_keeps_it(logs):
    errorlog.setup_logging(crash_log=False, detect_gpu=False)
    try:
        raise KeyError("missing_column")
    except KeyError as exc:
        errorlog.log_exception(exc, "Run")
    _flush()
    text = (logs / "cellquant.log").read_text(encoding="utf-8")
    assert "Traceback (most recent call last)" in text and "KeyError: 'missing_column'" in text and "Run: " in text
    details = errorlog.problem_details("It broke")
    assert "Problem: It broke" in details and "KeyError: 'missing_column'" in details and "CellQuant: " in details


def test_logging_failures_are_swallowed(tmp_path):
    errorlog.shutdown_logging()
    blocked = tmp_path / "file"
    blocked.write_text("not a folder")
    assert errorlog.setup_logging(blocked / "inside", crash_log=False, detect_gpu=False) is None
    errorlog.log_exception(ValueError("x"))
    errorlog.append_session_line(blocked, "line")
    errorlog.shutdown_logging()


def test_excepthooks_log_notify_and_chain_to_the_previous_hooks(logs, monkeypatch):
    seen_sys, seen_thread, shown = [], [], []
    monkeypatch.setattr(sys, "excepthook", lambda *args: seen_sys.append(args[1]))
    monkeypatch.setattr(threading, "excepthook", lambda args: seen_thread.append(args.exc_value))
    errorlog.setup_logging(crash_log=False, detect_gpu=False)
    errorlog.set_problem_handler(shown.append)
    assert sys.excepthook is not seen_sys

    error = RuntimeError("boom")
    sys.excepthook(RuntimeError, error, None)
    assert seen_sys == [error]
    assert shown == ["Unexpected error (RuntimeError: boom). Details saved to the CellQuant log."]

    def work():
        raise ValueError("in a thread")

    thread = threading.Thread(target=work)
    thread.start()
    thread.join()
    assert [str(e) for e in seen_thread] == ["in a thread"]
    assert any("in a thread" in text for text in shown)
    _flush()
    assert "RuntimeError: boom" in (logs / "cellquant.log").read_text(encoding="utf-8")

    before = sys.excepthook
    errorlog.install_hooks()  # already ours: not wrapped twice
    assert sys.excepthook is before
    errorlog.set_problem_handler(None)


def test_a_failing_problem_handler_does_not_loop_or_hide_the_error(logs, monkeypatch):
    seen = []
    monkeypatch.setattr(sys, "excepthook", lambda *args: seen.append(args[1]))
    errorlog.setup_logging(crash_log=False, detect_gpu=False)

    def broken(_text):
        raise RuntimeError("cannot show")

    errorlog.set_problem_handler(broken)
    error = ValueError("original")
    sys.excepthook(ValueError, error, None)
    assert seen == [error]
    errorlog.set_problem_handler(None)


def test_hooks_are_put_back_on_shutdown(logs, monkeypatch):
    def previous(*_args):
        pass

    monkeypatch.setattr(sys, "excepthook", previous)
    errorlog.setup_logging(crash_log=False, detect_gpu=False)
    assert sys.excepthook is not previous
    errorlog.shutdown_logging()
    assert sys.excepthook is previous


def test_crash_log_is_opened_for_native_crashes(logs):
    import faulthandler

    errorlog.setup_logging(crash_log=True, detect_gpu=False)
    assert (logs / "cellquant_crash.log").exists() and faulthandler.is_enabled()


def test_report_zip_has_logs_and_no_data(tmp_path):
    folder = tmp_path / "logs"
    folder.mkdir()
    for name in ("cellquant.log", "cellquant.log.1", "cellquant_crash.log", "unrelated.txt"):
        (folder / name).write_text(name)
    experiment = tmp_path / "exp"
    (experiment / "logs").mkdir(parents=True)
    (experiment / "logs" / "session_2026-01-02.log").write_text("session")
    (experiment / "logs" / "notes.txt").write_text("not a log")
    (experiment / "runs" / "run_1" / "logs").mkdir(parents=True)
    (experiment / "runs" / "run_1" / "logs" / "run.log").write_text("run")
    (experiment / "runs" / "run_1" / "labels").mkdir()
    (experiment / "runs" / "run_1" / "labels" / "a_final.tif").write_bytes(b"image")
    (experiment / "experiment.json").write_text("{}")

    destination = tmp_path / "report.zip"
    names = errorlog.build_report(destination, folder, experiment, environment="CellQuant: test")
    with zipfile.ZipFile(destination) as archive:
        assert sorted(archive.namelist()) == sorted(names)
        assert set(names) == {
            "environment.txt",
            "logs/cellquant.log",
            "logs/cellquant.log.1",
            "logs/cellquant_crash.log",
            "experiment/logs/session_2026-01-02.log",
            "experiment/runs/run_1/logs/run.log",
        }
        assert archive.read("environment.txt").decode().strip() == "CellQuant: test"

    without = tmp_path / "plain.zip"
    assert "experiment/logs/session_2026-01-02.log" not in errorlog.build_report(without, folder, None, environment="x")
    assert errorlog.report_name().startswith("cellquant_report_") and errorlog.report_name().endswith(".zip")


def test_session_log_lines_are_timestamped_and_lazy(tmp_path):
    errorlog._session.update(dir=None, lines=[])
    errorlog.append_session_line(None, "ignored")
    errorlog.append_session_line(tmp_path, "first\nsecond")
    errorlog.flush_session_log()
    files = list((tmp_path / "logs").glob("session_*.log"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").splitlines()
    assert [line.split("\t")[1] for line in lines] == ["first", "second"]
    assert lines[0].split("\t")[0][:4].isdigit()


def test_session_log_is_buffered_and_written_at_once_for_a_problem(tmp_path, monkeypatch):
    errorlog._session.update(dir=None, lines=[])
    monkeypatch.setattr(errorlog.time, "monotonic", lambda: 1000.0)
    errorlog.append_session_line(tmp_path, "opened")
    errorlog.flush_session_log()
    errorlog.append_session_line(tmp_path, "Step 2: image 1 of 9")  # within a few seconds: kept in memory
    log = next((tmp_path / "logs").glob("session_*.log"))
    assert "Step 2" not in log.read_text(encoding="utf-8")
    errorlog.append_session_line(tmp_path, "Problem: it broke")
    text = log.read_text(encoding="utf-8")
    assert "Step 2: image 1 of 9" in text and "Problem: it broke" in text
    assert not errorlog._session["lines"]


def test_session_log_timer_writes_lines_while_the_window_is_quiet(tmp_path, monkeypatch):
    import time

    errorlog._session.update(dir=None, lines=[], timer=None)
    monkeypatch.setattr(errorlog, "_SESSION_FLUSH_SECONDS", 0.05)
    errorlog.append_session_line(tmp_path, "Step 2: image 3 of 9")
    deadline = time.monotonic() + 5
    while not list((tmp_path / "logs").glob("session_*.log")) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert "image 3 of 9" in next((tmp_path / "logs").glob("session_*.log")).read_text(encoding="utf-8")
    errorlog._session.update(dir=None, lines=[], timer=None)


def test_session_log_keeps_lines_when_the_folder_cannot_be_written(tmp_path):
    blocked = tmp_path / "not_a_folder"
    blocked.write_text("x")
    errorlog.append_session_line(blocked, "Problem: kept")
    assert errorlog._session["lines"]  # still buffered for the next try
    errorlog.append_session_line(tmp_path, "elsewhere")
    errorlog.flush_session_log()
    assert "kept" not in next((tmp_path / "logs").glob("session_*.log")).read_text(encoding="utf-8")
    errorlog._session.update(dir=None, lines=[])


def test_logs_folder_counts_as_a_cellquant_folder(tmp_path):
    from cellquant.experiment import experiment_files

    (tmp_path / "logs").mkdir()
    assert tmp_path / "logs" in experiment_files(tmp_path)
