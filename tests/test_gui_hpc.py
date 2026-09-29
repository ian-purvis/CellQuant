"""The HPC prep panel in the live window: feature flag, background preparation, READY gating, import and review.

Needs napari, Qt and OpenGL (xvfb-run with Mesa). Skipped when napari is missing.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

napari = pytest.importorskip("napari")

from tests.hpc_helpers import cellpose_recipe, install_fake_cellpose, make_experiment, matching_observer, write_profile, write_runtime  # noqa: E402


def _wait(shell, seconds: float = 60) -> None:
    from qtpy.QtWidgets import QApplication

    end = time.time() + seconds
    while (shell._job is not None or shell._batch is not None or shell._loader is not None) and time.time() < end:
        QApplication.processEvents()
        time.sleep(0.02)
    for _ in range(5):
        QApplication.processEvents()


def _until(shell, condition, seconds: float = 60) -> None:
    from qtpy.QtWidgets import QApplication

    end = time.time() + seconds
    while not condition() and time.time() < end:
        QApplication.processEvents()
        time.sleep(0.02)
    _wait(shell)


@pytest.fixture
def window(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CELLQUANT_ENABLE_HPC", "1")
    from cellquant.gui.app import CellQuantWindow

    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"napari viewer could not start: {exc}")
    shell = CellQuantWindow(viewer, None)
    yield shell
    _wait(shell)
    viewer.close()


def test_hpc_prep_is_hidden_unless_switched_on(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CELLQUANT_ENABLE_HPC", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    from cellquant.gui.app import CellQuantWindow

    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"napari viewer could not start: {exc}")
    shell = CellQuantWindow(viewer, None)
    try:
        assert shell._start_page.hpc_button is None
    finally:
        viewer.close()


def test_prepare_submit_and_import_in_the_window(window, tmp_path: Path, monkeypatch):
    install_fake_cellpose(tmp_path, monkeypatch)
    shell = window
    assert shell._start_page.hpc_button is not None and not shell._start_page.hpc_button.isEnabled()
    controller = make_experiment(tmp_path, 2, recipe=cellpose_recipe(gpu=False))
    shell.open_experiment(controller.directory)
    _wait(shell)
    assert shell._start_page.hpc_button.isEnabled()
    shell.open_hpc_prep()
    panel = shell._hpc_panel
    assert shell._tabs.currentWidget() is panel and len(panel.selected_ids()) == 2
    # Settings: the GPU is off, and the panel offers to turn it on (an explicit, visible change).
    panel.refresh_settings()
    assert not panel.gpu_button.isHidden() and "CPU" in panel.settings_issues.text()
    panel.gpu_button.click()
    assert panel.package_recipe().object_set.parameters["gpu"] is True
    assert shell.controller.recipe.object_set.parameters["gpu"] is False  # the experiment's own settings are unchanged
    assert "package only" in panel.override_label.text()
    # Cluster profile.
    runtime_path, runtime_bytes = write_runtime(tmp_path / "profiles")
    panel.load_profile(write_profile(tmp_path / "profiles", runtime_path, runtime_bytes))
    assert "Ready for packages" in panel.profile_status.text()
    if panel.package_recipe().object_set.parameters.get("engine") != "cellpose4":
        assert not panel.engine_button.isHidden()
        panel.engine_button.click()
    assert panel.package_recipe().object_set.parameters["engine"] == "cellpose4"
    panel.cpus.setValue(4)
    panel._apply_profile_fields()
    assert panel.profile.profile.cpus == 4
    # Prepare, in the background.
    panel.output.setText(str(tmp_path / "packages"))
    panel.check_plan()
    assert not panel.prepare_button.isEnabled()
    _wait(shell)
    assert panel.plan is not None and panel.plan.ok and panel.prepare_button.isEnabled(), [i.message for i in panel.plan.all_errors()]
    panel.prepare()
    assert shell.is_busy() and not panel.prepare_button.isEnabled()
    _until(shell, lambda: panel.commands.isEnabled())
    assert panel.package_dir is not None and (panel.package_dir / "READY").is_file()
    assert panel.commands.isEnabled() and "bash scripts/submit.sh" in panel.commands.toPlainText()
    assert panel.tabs.currentIndex() == 4
    # A package that is not READY shows no commands.
    broken = panel.package_dir.parent / "copy"
    import shutil

    shutil.copytree(panel.package_dir, broken)
    (broken / "READY").unlink()
    panel.package_field.setText(str(broken))
    panel.validate_package()
    _wait(shell)
    assert not panel.commands.isEnabled() and "not a READY package" in panel.submit_status.text()
    # The cluster run (the worker, as the job would run it).
    from cellquant.hpc.runner import Runner

    run_dir = tmp_path / "downloaded" / "run_1"
    assert Runner(panel.package_dir, tmp_path / "scratch", run_dir, observe=matching_observer(runtime_bytes), log=lambda _t: None).run() == 0
    # Import.
    panel.import_package.setText(str(panel.package_dir))
    panel.import_results_field.setText(str(run_dir))
    panel.import_destination.setText(str(tmp_path / "imported experiment"))
    panel.check_results()
    _wait(shell)
    assert panel.preview is not None and panel.preview.complete and panel.partial.isHidden()
    assert panel.import_button.isEnabled() and panel.results_table.rowCount() == 2
    panel.run_import()
    _wait(shell)
    assert not panel.open_imported.isHidden(), panel.import_status.text()
    panel.open_imported.click()
    _wait(shell)
    assert shell.controller.directory == (tmp_path / "imported experiment").resolve()
    record = shell.controller.experiment.images[0]
    assert record.processing_status != "approved" and shell.controller.recall(record.image_id) is not None


def test_a_failed_preparation_is_explained(window, tmp_path: Path, monkeypatch):
    shell = window
    controller = make_experiment(tmp_path, 1, recipe=cellpose_recipe())
    shell.open_experiment(controller.directory)
    _wait(shell)
    shell.open_hpc_prep()
    panel = shell._hpc_panel
    runtime_path, runtime_bytes = write_runtime(tmp_path / "profiles", modes=("single_plane",))
    panel.load_profile(write_profile(tmp_path / "profiles", runtime_path, runtime_bytes))
    panel.output.setText(str(tmp_path / "packages"))
    panel.check_plan()
    _wait(shell)
    assert not panel.prepare_button.isEnabled() and "not enabled" in panel.plan_text.text()
    from cellquant.hpc import prepare
    from cellquant.progress import AnalysisCancelled

    runtime_path, runtime_bytes = write_runtime(tmp_path / "profiles")
    panel.load_profile(write_profile(tmp_path / "profiles", runtime_path, runtime_bytes))
    if not panel.gpu_button.isHidden():  # a computer without a GPU unticks 'Use GPU' in step 2
        panel.gpu_button.click()
    panel.check_plan()
    _wait(shell)
    assert panel.prepare_button.isEnabled(), panel.plan_text.text()

    def cancelled(plan, output):
        raise AnalysisCancelled("Stopped.")

    monkeypatch.setattr(prepare, "prepare_package", cancelled)
    panel.prepare()
    _wait(shell)
    assert "stopped" in panel.plan_text.text() and panel.prepare_button.isEnabled()
