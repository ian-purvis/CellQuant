"""Two CellQuant windows at once: one window per experiment folder, safe settings saves, shared GPU and CPU."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

from cellquant import hardware, instances

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(instances, "_REGISTERED", {})
    monkeypatch.setattr(hardware, "_BATCH_LIMITS", {})
    monkeypatch.setattr(hardware, "_SHARED_BATCH_LIMITS", {})
    monkeypatch.setattr(hardware, "_BATCH_NOTES", [])
    monkeypatch.setattr(hardware, "_BATCH_ANNOUNCED", set())
    monkeypatch.setattr(hardware, "_THREADS_APPLIED", set())
    monkeypatch.setattr(hardware, "_THREADS_WINDOWS", {})
    monkeypatch.setattr(hardware, "_DEFAULT_THREADS", [])


def _other_program(code: str, local: Path) -> subprocess.Popen:
    """Another CellQuant program: runs ``code``, prints "ready", and waits until it is killed."""

    script = textwrap.dedent(code) + "\nprint('ready', flush=True)\nimport time\ntime.sleep(600)\n"
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        text=True,
        env={**__import__("os").environ, "LOCALAPPDATA": str(local)},
    )
    assert process.stdout.readline().strip() == "ready"
    return process


def _crash(process: subprocess.Popen) -> None:
    process.kill()
    process.wait(10)


def test_a_folder_opens_in_one_window_and_frees_after_a_crash(tmp_path):
    folder = tmp_path / "experiment"
    folder.mkdir()
    other = _other_program(f"from cellquant.instances import lock_folder\nlock = lock_folder({str(folder)!r})", tmp_path)
    try:
        assert instances.lock_folder(folder) is None
    finally:
        _crash(other)  # no clean exit: the system frees the lock
    lock = instances.lock_folder(folder)
    assert lock is not None and lock.covers(folder)
    assert instances.lock_folder(folder) is None  # a second window in this program too
    lock.release()
    again = instances.lock_folder(folder)
    assert again is not None
    again.release()


def test_a_missing_folder_is_not_made_unless_asked(tmp_path):
    missing = tmp_path / "not here"
    lock = instances.lock_folder(missing)
    assert lock is not None and not lock.covers(missing) and not missing.exists()
    made = instances.lock_folder(missing, create=True)
    assert made.covers(missing) and (missing / instances.FOLDER_LOCK_NAME).is_file()
    made.release()


def test_other_windows_are_counted_and_ended_ones_forgotten(tmp_path):
    local = tmp_path / "local"
    assert instances.other_instances() == 0
    other = _other_program("from cellquant.instances import other_instances\nother_instances()", local)
    try:
        assert instances.other_instances() == 1
    finally:
        _crash(other)
    assert instances.other_instances() == 0
    assert len(list(instances.instances_folder().glob("*.lock"))) == 1  # only this program's


def test_two_programs_saving_settings_keep_each_others_keys(tmp_path):
    local = tmp_path / "local"
    code = textwrap.dedent(
        """
        import sys
        from cellquant.hardware import update_settings
        name = sys.argv[1]
        for index in range(40):
            update_settings(lambda data: data.setdefault(name, []).append(index))
        """
    )
    env = {**__import__("os").environ, "LOCALAPPDATA": str(local)}
    programs = [subprocess.Popen([sys.executable, "-c", code, name], cwd=ROOT, env=env) for name in ("a", "b")]
    for program in programs:
        assert program.wait(120) == 0
    data = json.loads(hardware.settings_path().read_text(encoding="utf-8"))
    assert data["a"] == list(range(40)) and data["b"] == list(range(40))
    assert not list(hardware.settings_path().parent.glob("*.tmp"))


def _torch(total_gb: float, free_gb: float, threads: int, state: dict):
    gib = 1024**3
    return types.SimpleNamespace(
        cuda=types.SimpleNamespace(
            get_device_properties=lambda device: types.SimpleNamespace(total_memory=total_gb * gib),
            get_device_name=lambda device: "RTX 6000 Ada",
            mem_get_info=lambda device: (free_gb * gib, total_gb * gib),
            memory_reserved=lambda device: 0,
            empty_cache=lambda: state.__setitem__("emptied", state.get("emptied", 0) + 1),
        ),
        get_num_threads=lambda: threads,
        set_num_threads=lambda count: state.__setitem__("threads", count),
    )


CUDA = types.SimpleNamespace(type="cuda")


@pytest.mark.parametrize(("others", "free_gb", "expected"), [(0, 5, 64), (1, 44, 64), (1, 30, 32), (1, 13, 16), (1, 5, 8)])
def test_batch_size_follows_free_memory_only_with_another_window(monkeypatch, others, free_gb, expected):
    monkeypatch.setitem(sys.modules, "torch", _torch(48, free_gb, 16, {}))
    monkeypatch.setattr(hardware, "_other_windows", lambda: others)
    assert hardware.cellpose_batch_size(CUDA) == expected
    if others:
        assert "shared with 1 other CellQuant window" in hardware.batch_notes()[0]


def test_running_out_of_memory_beside_another_window_is_not_saved(monkeypatch):
    state: dict = {}
    monkeypatch.setitem(sys.modules, "torch", _torch(48, 44, 16, state))
    monkeypatch.setattr(hardware, "_other_windows", lambda: 1)
    assert hardware.lower_batch_size(64, CUDA) == 32
    assert hardware.cellpose_batch_size(CUDA) == 32
    assert not hardware.settings_path().exists()
    assert "while another CellQuant window" in " ".join(hardware.batch_notes())
    monkeypatch.setattr(hardware, "_other_windows", lambda: 0)
    assert hardware.cellpose_batch_size(CUDA) == 64  # alone again: the full size
    assert hardware.lower_batch_size(64, CUDA) == 32
    assert json.loads(hardware.settings_path().read_text())["gpu_batch_size"] == {"RTX 6000 Ada": 32}


def test_gpu_cache_is_released_only_beside_another_window(monkeypatch):
    state: dict = {}
    monkeypatch.setitem(sys.modules, "torch", _torch(48, 44, 16, state))
    monkeypatch.setattr(hardware, "_other_windows", lambda: 0)
    hardware.release_gpu_cache()
    assert "emptied" not in state
    monkeypatch.setattr(hardware, "_other_windows", lambda: 1)
    hardware.release_gpu_cache()
    assert state["emptied"] == 1


def test_cpu_threads_are_shared_between_windows(monkeypatch):
    state: dict = {}
    windows = {"others": 0}
    monkeypatch.setitem(sys.modules, "torch", _torch(48, 44, 16, state))
    monkeypatch.setattr(hardware, "_other_windows", lambda: windows["others"])
    assert hardware.apply_threads("cellpose4") is None and "threads" not in state  # alone, nothing measured: as before
    windows["others"] = 1
    assert hardware.apply_threads("cellpose4") == 8 and state["threads"] == 8
    assert hardware.apply_threads("cellpose4") is None  # already set for two windows
    windows["others"] = 0
    assert hardware.apply_threads("cellpose4") == 16 and state["threads"] == 16  # the other window closed
    hardware.update_settings(lambda data: data.setdefault("cpu_threads", {}).update({"cellpose3": {"threads": 6}}))
    windows["others"] = 2
    assert hardware.apply_threads("cellpose3") == 2


def test_cellquant_run_refuses_a_folder_open_in_a_window(tmp_path, capsys):
    from cellquant.__main__ import main

    folder = tmp_path / "experiment"
    folder.mkdir()
    lock = instances.lock_folder(folder)
    try:
        with pytest.raises(SystemExit) as stop:
            main(["run", "--experiment", str(folder)])
    finally:
        lock.release()
    assert stop.value.code == 2 and "open in a CellQuant window" in capsys.readouterr().err
