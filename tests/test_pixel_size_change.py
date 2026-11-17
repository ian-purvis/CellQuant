"""Changing the pixel size after a run: the image's results are out of date and it must be run again."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from cellquant.controller import AnalysisController
from cellquant.errors import CellQuantError
from cellquant.practice import PIXEL_SIZE_UM, write_practice_images


def _controller(tmp_path: Path) -> AnalysisController:
    raw = write_practice_images(tmp_path / "raw")
    controller = AnalysisController.create(tmp_path / "experiment", "Sizes")
    controller.add_image_paths(raw[:2])
    for record in controller.experiment.images:
        controller.set_pixel_size(record.image_id, PIXEL_SIZE_UM, PIXEL_SIZE_UM)
    controller.set_recipe(
        {"object_set": {"name": "Nuclei", "segmentation_channel": 0, "algorithm": "classical", "parameters": {"threshold_method": "otsu", "sigma": 1.0}}}
    )
    return controller


def _run_and_approve(controller: AnalysisController) -> list[str]:
    ids = [record.image_id for record in controller.experiment.images]
    controller.run_images()
    for image_id in ids:
        controller.set_status(image_id, "approved")
    return ids


def test_a_changed_size_makes_the_result_out_of_date_and_clears_the_approval(tmp_path: Path):
    controller = _controller(tmp_path)
    first, second = _run_and_approve(controller)
    before = controller.recall(first)
    assert before is not None and before.provenance["pixel_size_x_um"] == PIXEL_SIZE_UM
    old_fingerprint = controller.experiment.image(first).approved_settings_sha256

    assert controller.pixel_size_makes_stale(first, 2.0, 2.0)
    controller.set_pixel_size(first, 2.0, 2.0)

    record = controller.experiment.image(first)
    assert (record.pixel_size_x, record.pixel_size_y) == (2.0, 2.0)
    assert record.processing_status == "not_analyzed" and record.approved_settings_sha256 == ""
    assert "run it again" in record.last_message
    assert controller.recall(first) is None and not controller.has_result(first)
    assert first not in controller.last_results
    # The other image is untouched.
    assert controller.experiment.image(second).processing_status == "approved"
    assert controller.recall(second) is not None

    # Run again: measured at the new size, analyzed (not approved again by itself).
    controller.run_image(first)
    after = controller.recall(first)
    assert after.provenance["pixel_size_x_um"] == 2.0
    assert controller.experiment.image(first).processing_status == "analyzed"
    assert controller._approval_fingerprint(first) != old_fingerprint


def test_the_saved_result_is_not_offered_after_reopening(tmp_path: Path):
    controller = _controller(tmp_path)
    first, _ = _run_and_approve(controller)
    controller.save()
    controller.set_pixel_size(first, 2.0, 2.0)
    controller.save()
    reopened = AnalysisController.open(tmp_path / "experiment")
    assert reopened.experiment.image(first).processing_status == "not_analyzed"
    assert reopened.recall(first) is None
    assert reopened.recall(reopened.experiment.images[1].image_id) is not None


def test_export_leaves_out_the_out_of_date_image(tmp_path: Path):
    controller = _controller(tmp_path)
    first, second = _run_and_approve(controller)
    controller.set_pixel_size(first, 2.0, 2.0)
    out = controller.export(tmp_path / "export")
    summary = (out / "image_summary.csv").read_text()
    assert controller.experiment.image(second).filename in summary
    assert controller.experiment.image(first).filename not in summary
    controller.set_pixel_size(second, 2.0, 2.0)
    with pytest.raises(CellQuantError, match="no results"):
        controller.export(tmp_path / "export2")


def test_the_same_value_changes_nothing(tmp_path: Path):
    controller = _controller(tmp_path)
    first, _ = _run_and_approve(controller)
    assert not controller.pixel_size_makes_stale(first, PIXEL_SIZE_UM, PIXEL_SIZE_UM)
    controller.set_pixel_size(first, PIXEL_SIZE_UM, PIXEL_SIZE_UM)
    controller.set_pixel_size(first, PIXEL_SIZE_UM, PIXEL_SIZE_UM, None)  # no Z step given: Z stays as it is
    record = controller.experiment.image(first)
    assert record.processing_status == "approved" and record.approved_settings_sha256
    assert controller.recall(first) is not None


def test_a_z_step_change_counts_and_an_image_without_results_is_simply_updated(tmp_path: Path):
    controller = _controller(tmp_path)
    first, second = [record.image_id for record in controller.experiment.images]
    controller.run_image(first)
    controller.set_status(first, "approved")
    assert not controller.pixel_size_makes_stale(second, 1.0, 1.0, 3.0)  # never run: nothing to be out of date
    controller.set_pixel_size(second, 1.0, 1.0, 3.0)
    assert controller.experiment.image(second).pixel_size_z == 3.0
    assert controller.experiment.image(second).processing_status == "not_analyzed"
    assert controller.experiment.image(second).last_message == ""
    controller.set_pixel_size(first, PIXEL_SIZE_UM, PIXEL_SIZE_UM, 3.0)
    assert controller.experiment.image(first).processing_status == "not_analyzed"


def test_the_approval_fingerprint_covers_the_pixel_sizes(tmp_path: Path):
    controller = _controller(tmp_path)
    first, _ = _run_and_approve(controller)
    approved = controller.experiment.image(first).approved_settings_sha256
    assert approved == controller._approval_fingerprint(first)
    controller.experiment.image(first).pixel_size_x = 3.0  # as if the size had been changed behind the check
    assert approved != controller._approval_fingerprint(first)


def test_running_again_with_unchanged_sizes_keeps_the_approval(tmp_path: Path):
    controller = _controller(tmp_path)
    first, _ = _run_and_approve(controller)
    controller.run_image(first)
    assert controller.experiment.image(first).processing_status == "approved"


def _wait(shell, seconds: float = 120) -> None:
    from qtpy.QtWidgets import QApplication

    end = time.time() + seconds
    while (shell._job is not None or shell._batch is not None or shell._loader is not None) and time.time() < end:
        QApplication.processEvents()
        time.sleep(0.02)
    for _ in range(5):
        QApplication.processEvents()


@pytest.fixture
def shell(tmp_path: Path, monkeypatch):
    napari = pytest.importorskip("napari")
    from cellquant.gui import guide
    from cellquant.gui.app import CellQuantWindow

    monkeypatch.setattr(guide, "choose_practice_folder", lambda parent: tmp_path / "practice")
    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"napari viewer could not start: {exc}")
    window = CellQuantWindow(viewer, None)
    window.start_practice()
    _wait(window)
    window.run_current()
    _wait(window)
    yield window
    viewer.close()


def _change_to_two(window, scope: str = "this") -> None:
    panel = window._experiment_panel
    panel._show_size_controls()
    panel.pixel_x.setValue(2.0)
    panel.pixel_y.setValue(2.0)
    panel.size_scope.setCurrentIndex(panel.size_scope.findData(scope))
    panel._apply_pixel_size()
    _wait(window)


def test_gui_unapproved_result_goes_stale_without_a_question(shell, monkeypatch):
    from qtpy.QtWidgets import QMessageBox

    asked = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: asked.append(a) or QMessageBox.Yes))
    controller = shell.controller
    image_id = shell._nav_ids[shell._nav_index]
    assert controller.recall(image_id) is not None
    said = []
    monkeypatch.setattr(shell, "message", said.append)
    _change_to_two(shell)
    assert asked == []
    assert controller.experiment.image(image_id).processing_status == "not_analyzed"
    assert controller.recall(image_id) is None
    assert said and "1 image needs running again" in said[-1]
    panel = shell._experiment_panel
    row = [panel.table.item(r, 0).data(0x0100) for r in range(panel.table.rowCount())].index(image_id)
    texts = [panel.table.item(row, c).text() for c in range(panel.table.columnCount()) if panel.table.item(row, c)]
    assert "Not run" in texts
    # Every step follows: with the only result out of date, step 3 locks again until the image is run.
    tabs = shell._tabs
    assert not tabs.isTabEnabled(tabs.indexOf(shell._step_pages[2]))


def test_gui_approved_image_asks_first_and_no_changes_nothing(shell, monkeypatch):
    from qtpy.QtWidgets import QMessageBox

    controller = shell.controller
    image_id = shell._nav_ids[shell._nav_index]
    controller.set_status(image_id, "approved")
    asked = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: asked.append(a[2]) or QMessageBox.No))
    _change_to_two(shell)
    assert len(asked) == 1 and "1 approved image" in asked[0]
    record = controller.experiment.image(image_id)
    assert record.processing_status == "approved" and record.pixel_size_x == PIXEL_SIZE_UM
    assert controller.recall(image_id) is not None

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    _change_to_two(shell)
    record = controller.experiment.image(image_id)
    assert record.processing_status == "not_analyzed" and record.pixel_size_x == 2.0
    assert controller.recall(image_id) is None
    assert shell._results_panel is not None
