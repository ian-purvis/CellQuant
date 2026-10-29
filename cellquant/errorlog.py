"""The CellQuant log: a rotating file, a catch-all for unexpected errors, and the report a person can send.

Nothing here may hide the original problem, so every function swallows its own failures. The module has no
GUI code: the window registers a function with ``set_problem_handler`` to be told about unexpected errors.
"""

from __future__ import annotations

import datetime as _dt
import atexit
import errno
import faulthandler
import importlib.metadata
import logging
import logging.handlers
import os
import platform
import sys
import threading
import traceback
import time
import zipfile
from pathlib import Path

LOG_NAME = "cellquant.log"
CRASH_LOG_NAME = "cellquant_crash.log"
LOG_BYTES = 2 * 1024 * 1024
LOG_BACKUPS = 5
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
UNEXPECTED_SUFFIX = "Details saved to the CellQuant log."
LOG_POINTER = "Details are in the CellQuant log (Help → Open log folder)."

_logger = logging.getLogger("cellquant")
_state: dict = {
    "handler": None,
    "folder": None,
    "crash_handle": None,
    "environment": [],
    "gpu": "",
    "last_traceback": "",
    "problem_handler": None,
    "hooks": None,
    "busy": False,
}


# -- where the log lives -------------------------------------------------------------------------------------------


def log_folder() -> Path:
    """Beside the per-computer install record on Windows (%LOCALAPPDATA%\\CellQuant); ~/.cellquant elsewhere."""

    local = os.environ.get("LOCALAPPDATA")
    return Path(local) / "CellQuant" if local else Path.home() / ".cellquant"


# -- the environment header ----------------------------------------------------------------------------------------


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except Exception:  # noqa: BLE001 - not installed, or metadata unreadable
        return "not installed"


def environment_lines() -> list[str]:
    """What the log header says about this computer. Never imports PyTorch, so it is quick."""

    lines: list[str] = []
    try:
        from cellquant.__version__ import __version__

        lines.append(f"CellQuant: {__version__}")
    except Exception:  # noqa: BLE001
        lines.append("CellQuant: unknown")
    for label, produce in (
        ("OS", platform.platform),
        ("Python", lambda: f"{platform.python_version()} ({platform.python_implementation()})"),
    ):
        try:
            lines.append(f"{label}: {produce()}")
        except Exception:  # noqa: BLE001
            lines.append(f"{label}: unknown")
    for package in ("napari", "cellpose", "torch"):
        lines.append(f"{package}: {_package_version(package)}")
    lines.append(f"GPU: {_state['gpu'] or 'not checked yet'}")
    return lines


def environment_text() -> str:
    return "\n".join(environment_lines())


def note_gpu(status: dict) -> None:
    """Record what PyTorch reported about CUDA (the window checks this in the background anyway)."""

    try:
        if status.get("available"):
            text = f"PyTorch can use CUDA: {status.get('name') or 'GPU'}"
        else:
            reason = status.get("reason") or "no CUDA device"
            extra = f"; NVIDIA GPU present: {status['nvidia_gpu']}" if status.get("nvidia_gpu") else ""
            text = f"PyTorch cannot use CUDA ({reason}){extra}"
        _record_gpu(text)
    except Exception:  # noqa: BLE001
        pass


def _record_gpu(text: str) -> None:
    previous = _state["gpu"]
    _state["gpu"] = f"{previous}; {text}" if previous and text not in previous else (previous or text)
    _logger.info("GPU: %s", text)


def _nvidia_smi_info() -> str:
    """The GPU name and driver nvidia-smi reports, or "". Can be slow, so only called off the main thread."""

    import shutil
    import subprocess

    from cellquant.engines import _NVIDIA_SMI_FALLBACKS

    candidates = [shutil.which("nvidia-smi")]
    if sys.platform.startswith("win"):
        candidates += list(_NVIDIA_SMI_FALLBACKS)
    for path in candidates:
        if not path or not Path(path).exists():
            continue
        try:
            output = subprocess.run(
                [path, "--query-gpu=name,driver_version", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout
        except Exception:  # noqa: BLE001
            continue
        rows = [line.strip() for line in output.splitlines() if line.strip()]
        if rows:
            name, _, driver = rows[0].partition(",")
            return f"{name.strip()}, driver {driver.strip()}" if driver.strip() else name.strip()
    return ""


def _detect_gpu() -> None:
    try:
        info = _nvidia_smi_info()
        _record_gpu(f"NVIDIA {info}" if info else "no NVIDIA GPU found by nvidia-smi")
    except Exception:  # noqa: BLE001
        pass


# -- setting up ----------------------------------------------------------------------------------------------------


def setup_logging(folder: str | Path | None = None, *, crash_log: bool = True, detect_gpu: bool = True) -> Path | None:
    """Start the rotating log, write the session header and install the catch-all hooks. Safe to call again.

    Returns the log folder, or None when the log file could not be opened (CellQuant still runs).
    """

    try:
        shutdown_logging()
        target = Path(folder) if folder is not None else log_folder()
        target.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            target / LOG_NAME, maxBytes=LOG_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=None))
        handler.formatter.default_time_format = "%Y-%m-%dT%H:%M:%S"
        handler.formatter.default_msec_format = "%s.%03d"
        _logger.setLevel(logging.INFO)
        _logger.addHandler(handler)
        warnings_logger = logging.getLogger("py.warnings")
        warnings_logger.addHandler(handler)
        logging.captureWarnings(True)
        _state.update(handler=handler, folder=target, gpu="", last_traceback="")
        _logger.info("---- CellQuant session started ----")
        _state["environment"] = environment_lines()
        for line in _state["environment"]:
            _logger.info("%s", line)
        if crash_log:
            _enable_faulthandler(target)
        install_hooks()
        if detect_gpu:
            threading.Thread(target=_detect_gpu, name="cellquant-gpu-info", daemon=True).start()
        return target
    except Exception:  # noqa: BLE001 - logging must never stop CellQuant opening
        return None


def _enable_faulthandler(folder: Path) -> None:
    try:
        handle = (folder / CRASH_LOG_NAME).open("a", encoding="utf-8")
        handle.write(f"---- session started {_dt.datetime.now().isoformat(timespec='seconds')} ----\n")
        handle.flush()
        faulthandler.enable(file=handle, all_threads=True)
        _state["crash_handle"] = handle  # kept open for the life of the process
    except Exception:  # noqa: BLE001
        pass


def shutdown_logging() -> None:
    """Undo setup_logging (used when it runs again, and by tests)."""

    try:
        remove_hooks()
        handler = _state["handler"]
        if handler is not None:
            _logger.removeHandler(handler)
            logging.getLogger("py.warnings").removeHandler(handler)
            handler.close()
        if _state["crash_handle"] is not None:
            try:
                faulthandler.disable()
                _state["crash_handle"].close()
            except Exception:  # noqa: BLE001
                pass
        logging.captureWarnings(False)
    except Exception:  # noqa: BLE001
        pass
    _state.update(handler=None, folder=None, crash_handle=None, environment=[], gpu="")


def current_folder() -> Path:
    """The folder the log is written to now (the default one before setup_logging)."""

    return _state["folder"] or log_folder()


# -- logging errors ------------------------------------------------------------------------------------------------


def log_exception(exc: BaseException, context: str = "") -> None:
    """Write the traceback to the log and keep it for Copy details. Never raises."""

    try:
        text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        _state["last_traceback"] = text
        _logger.error("%s%s", f"{context}: " if context else "", text.rstrip())
    except Exception:  # noqa: BLE001 - logging must never hide the original problem
        pass


def log_problem(text: str) -> None:
    """Record a message the Problem box showed."""

    try:
        logging.getLogger("cellquant.gui").error("Problem shown: %s", text)
    except Exception:  # noqa: BLE001
        pass


def last_traceback() -> str:
    return _state["last_traceback"]


def problem_details(problem: str) -> str:
    """What Copy details puts on the clipboard: the problem, the last traceback logged, and the environment."""

    parts = [f"Problem: {problem}"]
    if _state["last_traceback"]:
        parts.append("Last traceback:\n" + _state["last_traceback"].rstrip())
    parts.append("Environment:\n" + environment_text())
    return "\n\n".join(parts) + "\n"


# -- the catch-all -------------------------------------------------------------------------------------------------


def set_problem_handler(handler) -> None:
    """The window registers a function(text) that shows the Problem box. It may be called from any thread."""

    _state["problem_handler"] = handler


def unexpected_error_text(exc: BaseException) -> str:
    return f"Unexpected error ({type(exc).__name__}: {str(exc) or 'no details'}). {UNEXPECTED_SUFFIX}"


def _report_unexpected(exc: BaseException) -> None:
    if _state["busy"]:  # an error while reporting an error must not loop
        return
    _state["busy"] = True
    try:
        log_exception(exc, "Uncaught exception")
        handler = _state["problem_handler"]
        if handler is not None and not isinstance(exc, (KeyboardInterrupt, SystemExit)):
            handler(unexpected_error_text(exc))
    except Exception:  # noqa: BLE001
        pass
    finally:
        _state["busy"] = False


def install_hooks() -> None:
    """Chain sys.excepthook and threading.excepthook to the log. If another hook was installed since, chain to that."""

    try:
        if sys.excepthook is (_state["hooks"] or {}).get("sys_hook"):
            return
        previous_sys = sys.excepthook
        previous_thread = threading.excepthook

        def sys_hook(kind, value, tb):
            if value is not None:
                if value.__traceback__ is None and tb is not None:
                    value = value.with_traceback(tb)
                _report_unexpected(value)
            previous_sys(kind, value, tb)

        def thread_hook(args):
            if args.exc_value is not None:
                _report_unexpected(args.exc_value)
            previous_thread(args)

        sys.excepthook = sys_hook
        threading.excepthook = thread_hook
        _state["hooks"] = {
            "sys_hook": sys_hook,
            "thread_hook": thread_hook,
            "previous_sys": previous_sys,
            "previous_thread": previous_thread,
        }
    except Exception:  # noqa: BLE001
        pass


def remove_hooks() -> None:
    """Put back the hooks that were there before install_hooks (only those still ours)."""

    hooks = _state["hooks"]
    if not hooks:
        return
    try:
        if sys.excepthook is hooks["sys_hook"]:
            sys.excepthook = hooks["previous_sys"]
        if threading.excepthook is hooks["thread_hook"]:
            threading.excepthook = hooks["previous_thread"]
    except Exception:  # noqa: BLE001
        pass
    _state["hooks"] = None


# -- plain-English file errors -------------------------------------------------------------------------------------


def os_error_text(exc: OSError) -> str:
    """A sentence for the Problem box about a file error, with the path when there is one."""

    code = getattr(exc, "errno", None)
    win = getattr(exc, "winerror", None)
    filename = getattr(exc, "filename", None)
    if filename is not None and getattr(exc, "filename2", None) is not None:
        filename = f"{filename} → {exc.filename2}"
    path = str(filename) if filename is not None else ""
    if code is None and win is None:
        # Not a system error: a sentence somebody wrote.
        return str(exc.args[0] if exc.args else exc) or "The analysis failed."
    if win == 32:
        return f"{path or 'The file'} is in use by another program. Close it or wait, then try again."
    if win == 5 or (win is None and code in (errno.EACCES,)):
        where = f": {path}" if path else ""
        return (
            f"Permission denied{where}. The file may be open in another program, read-only, or still syncing "
            "(for example in a cloud-synced folder). Close it or wait, then try again."
        )
    if win == 112 or code == errno.ENOSPC:
        where = f" ({path})" if path else ""
        return f"The disk is full{where}. Free some space, then try again."
    if win in (2, 3) or (win is None and code == errno.ENOENT):
        return f"Not found: {path}. Check that it was not moved, renamed or deleted." if path else "A file or folder was not found."
    if win == 206 or code == errno.ENAMETOOLONG:
        return (
            f"The path is too long: {path}. Move the folder closer to the top of the drive, or use shorter names."
            if path
            else "A path is too long. Move the folder closer to the top of the drive, or use shorter names."
        )
    reason = getattr(exc, "strerror", None)
    if reason and path:
        return f"{reason}: {path}"
    return str(reason or exc)


# -- the report a person can send ----------------------------------------------------------------------------------


def report_name(now: _dt.datetime | None = None) -> str:
    return f"cellquant_report_{(now or _dt.datetime.now()).strftime('%Y%m%d_%H%M%S')}.zip"


def build_report(
    destination: str | Path,
    folder: str | Path,
    experiment_dir: str | Path | None = None,
    environment: str | None = None,
) -> list[str]:
    """Write a zip of the logs: cellquant.log*, the crash log, environment.txt, and the open experiment's logs.

    No images or data go in. Returns the names stored in the zip.
    """

    flush_session_log()
    folder = Path(folder)
    sources: list[tuple[Path, str]] = []
    sources += [(path, f"logs/{path.name}") for path in sorted(folder.glob(LOG_NAME + "*")) if path.is_file()]
    crash = folder / CRASH_LOG_NAME
    if crash.is_file():
        sources.append((crash, f"logs/{CRASH_LOG_NAME}"))
    if experiment_dir is not None:
        root = Path(experiment_dir)
        sources += [(path, f"experiment/logs/{path.name}") for path in sorted((root / "logs").glob("session_*.log"))]
        for path in sorted((root / "runs").glob("*/logs/*.log")):
            sources.append((path, f"experiment/runs/{path.parent.parent.name}/logs/{path.name}"))
    stored: list[str] = []
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("environment.txt", (environment if environment is not None else environment_text()) + "\n")
        stored.append("environment.txt")
        for path, name in sources:
            try:
                archive.write(path, name)
                stored.append(name)
            except OSError:  # a log that is locked or gone is left out
                continue
    return stored


# -- the session log in the experiment folder ----------------------------------------------------------------------


_SESSION_FLUSH_SECONDS = 5.0
_SESSION_MAX_LINES = 2000
_session: dict = {"dir": None, "lines": [], "last_flush": 0.0, "warned": False}


def append_session_line(experiment_dir: str | Path | None, text: str, now: _dt.datetime | None = None) -> None:
    """Add a timestamped line to <experiment>/logs/session_<date>.log. Skipped without an experiment. Never raises.

    Lines are buffered and written at most every few seconds (and at once for a Problem), with the file closed in
    between, so a cloud-synced folder is not touched on every progress message and Overwrite can delete logs/.
    """

    if not experiment_dir:
        return
    try:
        now = now or _dt.datetime.now()
        directory = Path(experiment_dir)
        if _session["dir"] != directory:
            flush_session_log()
            _session["lines"].clear()  # lines the old folder would not take must not land in the new one
            _session["dir"] = directory
            _logger.info("Experiment folder: %s", directory)
        stamp = now.strftime("%Y-%m-%d %H:%M:%S")
        day = now.strftime("%Y-%m-%d")
        lines = str(text).splitlines() or [""]
        _session["lines"].extend((day, f"{stamp}\t{line}\n") for line in lines)
        del _session["lines"][:-_SESSION_MAX_LINES]
        if str(text).startswith("Problem:") or time.monotonic() - _session["last_flush"] >= _SESSION_FLUSH_SECONDS:
            flush_session_log()
    except Exception:  # noqa: BLE001
        pass


def flush_session_log() -> None:
    """Write the buffered session lines. On failure they stay buffered for the next try. Never raises."""

    directory, pending = _session["dir"], _session["lines"]
    _session["last_flush"] = time.monotonic()
    if directory is None or not pending:
        return
    try:
        folder = directory / "logs"
        folder.mkdir(exist_ok=True)
        by_day: dict[str, list[str]] = {}
        for day, line in pending:
            by_day.setdefault(day, []).append(line)
        for day, lines in by_day.items():
            with (folder / f"session_{day}.log").open("a", encoding="utf-8") as handle:
                handle.write("".join(lines))
        pending.clear()
        _session["warned"] = False
    except Exception as exc:  # noqa: BLE001
        if not _session["warned"]:
            _session["warned"] = True
            _logger.warning("The session log in %s could not be written (%s); will try again.", directory, exc)


atexit.register(flush_session_log)
