"""Live napari window: threshold changes must not rebuild layers or reload the image.

Needs napari, a Qt binding, and OpenGL (a desktop, or a virtual display such as
xvfb-run with Mesa). Skipped when napari is not installed.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest

napari = pytest.importorskip("napari")

from tests.test_workflow import _recipe, _write_squares  # noqa: E402


@pytest.fixture
def window(tmp_path: Path):
    from cellquant.controller import AnalysisController
    from cellquant.gui.app import CellQuantWindow

    paths = []
    for index in range(2):
        path = tmp_path / f"s{index}.tif"
        _write_squares(path)
        paths.append(path)
    controller = AnalysisController.create(tmp_path / "experiment", "GUI")
    controller.add_image_paths(paths)
    controller.set_recipe(_recipe())
    controller.save()
    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001 - no OpenGL or display here
        pytest.skip(f"napari viewer could not start: {exc}")
    shell = CellQuantWindow(viewer, tmp_path / "experiment")
    yield shell
    viewer.close()


def _wait(shell, seconds: float = 30) -> None:
    from qtpy.QtWidgets import QApplication

    end = time.time() + seconds
    while (shell._job is not None or shell._batch is not None or shell._loader is not None) and time.time() < end:
        QApplication.processEvents()
        time.sleep(0.02)
    QApplication.processEvents()


def test_threshold_change_recolors_without_rebuilding_layers(window, monkeypatch):
    shell = window
    shell.run_current()
    _wait(shell)
    panel = shell._review_panel
    panel.display.setCurrentIndex(panel.display.findData("class_a"))
    layers_before = {name: id(shell.viewer.layers[name]) for name in [layer.name for layer in shell.viewer.layers]}
    assert "Classification" in layers_before

    def forbidden(*_args, **_kwargs):
        raise AssertionError("a threshold change must not read the image file")

    monkeypatch.setattr(shell.controller, "_load_record", forbidden)
    started = time.time()
    # Call the handler directly: an exception inside a Qt signal aborts the process under PyQt6.
    panel._threshold_moved(10_000.0)
    elapsed = time.time() - started

    layers_after = {layer.name: id(layer) for layer in shell.viewer.layers}
    assert layers_after == layers_before  # same layer objects, nothing added or removed
    overlay = shell.viewer.layers["Classification"].data
    assert set(np.unique(overlay).tolist()) <= {0, 1}  # nothing is positive at 10,000
    assert "Positive: 0" in panel.counts.text()
    assert elapsed < 2.0, f"threshold update took {elapsed:.2f} s"


def test_moving_to_another_image_reloads_it(window):
    shell = window
    shell.run_current()
    _wait(shell)
    first = shell._shown_image_id
    shell._footer._step(1)
    _wait(shell)
    assert shell._shown_image_id != first


def test_switching_images_does_not_block_the_window(window, monkeypatch):
    """A slow file read (ND2 stacks on OneDrive) must not freeze the interface thread."""

    shell = window
    _wait(shell)
    real = shell.controller._load_record

    def slow(record):
        time.sleep(1.0)
        return real(record)

    monkeypatch.setattr(shell.controller, "_load_record", slow)
    started = time.time()
    shell._footer._step(1)
    assert time.time() - started < 0.5, "switching images waited for the file on the interface thread"
    assert shell.image_loading()
    _wait(shell)
    assert shell._shown_image_id == shell._nav_ids[1]


def test_new_experiment_keeps_results_out_of_the_image_folder(tmp_path: Path, monkeypatch):
    from qtpy.QtWidgets import QDialog

    from cellquant.gui import app as gui_app

    images = tmp_path / "images" / "Retina 1"
    images.mkdir(parents=True)
    _write_squares(images / "a.tif")
    results = tmp_path / "images - CellQuant results"

    class Chosen:
        def __init__(self, parent=None):
            pass

        def exec(self):
            return QDialog.Accepted

        def experiment_name(self):
            return "Separate folders"

        def images_folder(self):
            return str(tmp_path / "images")

        def results_folder(self):
            return str(results)

    monkeypatch.setattr(gui_app, "NewExperimentDialog", Chosen)
    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"napari viewer could not start: {exc}")
    try:
        shell = gui_app.CellQuantWindow(viewer)
        shell.new_experiment()
        _wait(shell)
        assert [record.relative_path for record in shell.controller.experiment.images] == ["Retina 1/a.tif"]
        assert (results / "experiment.json").is_file()
        assert sorted(path.name for path in (tmp_path / "images").rglob("*")) == ["Retina 1", "a.tif"]
    finally:
        viewer.close()


def test_3d_results_show_as_a_volume_with_a_recommendation(tmp_path: Path):
    from cellquant.controller import AnalysisController
    from cellquant.gui.app import CellQuantWindow
    from tests.test_3d import _recipe as recipe_3d
    from tests.test_3d import _stack

    path = tmp_path / "stack.tif"
    _stack(path)
    controller = AnalysisController.create(tmp_path / "results", "3D window")
    controller.add_image_paths([path])
    controller.set_recipe(recipe_3d("max_projection"))
    controller.save()
    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"napari viewer could not start: {exc}")
    try:
        shell = CellQuantWindow(viewer, tmp_path / "results")
        _wait(shell)
        panel = shell._objects_panel
        panel.show_gpu_status({"available": False, "reason": "test"})
        assert panel.z_recommend_box.isVisibleTo(panel)
        assert "Recommended here" in panel.z_recommend.text()
        assert any("★ recommended" in panel.z_stack.itemText(index) for index in range(panel.z_stack.count()))
        panel.use_recommendation()
        assert panel.z_stack.currentData() == panel._recommended == "stitch_slices"
        assert panel.z3d_box.isVisibleTo(panel)
        shell.run_current()
        _wait(shell)
        assert shell.viewer.layers["Objects"].data.shape == (9, 64, 64)
        assert shell.viewer.dims.ndim == 3  # a slice slider under the image
        assert shell.controller.last_results[shell._shown_image_id].qc.n_objects == 3
        assert "3D, 9 slices linked" in shell._footer.units.text()
        # After a real run, the estimate for that option is measured on this computer.
        panel.update_recommendation()
        index = panel.z_stack.findData("stitch_slices")
        assert "measured" in panel.z_stack.itemText(index)
    finally:
        viewer.close()


def _stack_window(tmp_path: Path, count: int = 1, mode: str = "stitch_slices"):
    from cellquant.controller import AnalysisController
    from cellquant.gui.app import CellQuantWindow
    from tests.test_3d import _recipe as recipe_3d
    from tests.test_3d import _stack

    for index in range(count):
        _stack(tmp_path / "data" / f"s{index}.tif")
    controller = AnalysisController.create(tmp_path / "results", "Window")
    controller.add_image_paths([tmp_path / "data"])
    controller.set_recipe(recipe_3d(mode))
    controller.save()
    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"napari viewer could not start: {exc}")
    shell = CellQuantWindow(viewer, tmp_path / "results")
    _wait(shell)
    return viewer, shell


def test_a_run_shows_its_progress_and_can_be_cancelled_part_way(tmp_path: Path, monkeypatch):
    from qtpy.QtWidgets import QApplication, QPushButton

    import cellquant.segmentation as segmentation

    real = segmentation.classical_segment

    def slow(channel, parameters):
        time.sleep(0.25)
        return real(channel, parameters)

    monkeypatch.setattr(segmentation, "classical_segment", slow)
    viewer, shell = _stack_window(tmp_path)
    try:
        footer = shell._footer
        run_buttons = [button for button in shell._tabs.findChildren(QPushButton) if button.text() == "Run"]
        assert footer.cancel.isEnabled() is False
        shell.run_current()
        end = time.time() + 20
        while "slice 3 of 9" not in footer.status.text() and time.time() < end:
            QApplication.processEvents()
            time.sleep(0.02)
        assert "Finding objects: slice 3 of 9" in footer.status.text()
        assert 0 < footer.progress.value() < 1000
        assert not any(button.isEnabled() for button in run_buttons)
        assert footer.cancel.isEnabled()
        footer._cancel()
        _wait(shell)
        assert "Stopped" in footer.status.text()
        assert shell.controller.recall(shell._nav_ids[0]) is None
        assert all(button.isEnabled() for button in run_buttons)
        assert not footer.cancel.isEnabled()
    finally:
        viewer.close()


def test_batch_progress_counts_images_and_slices(tmp_path: Path):
    from qtpy.QtWidgets import QApplication

    viewer, shell = _stack_window(tmp_path, count=2)
    try:
        seen = set()
        shell.start_batch(None)
        assert shell._footer.pause.isEnabled() and shell._footer.cancel.isEnabled()
        end = time.time() + 60
        while shell._batch is not None and time.time() < end:
            QApplication.processEvents()
            seen.add(shell._footer.status.text())
            time.sleep(0.005)
        QApplication.processEvents()
        assert any(text.startswith("Image 1 of 2") and "slice" in text for text in seen), sorted(seen)[:10]
        # The second file has the same pixels, so its saved segmentation is reused: no slices to report.
        assert any(text.startswith("Image 2 of 2") for text in seen)
        assert "Completed 2" in shell._footer.status.text()
        assert shell._footer.progress.value() == 1000
    finally:
        viewer.close()


def test_include_buttons_choose_a_subset(tmp_path: Path):
    viewer, shell = _stack_window(tmp_path, count=3, mode="max_projection")
    try:
        panel = shell._experiment_panel
        assert "3 of 3 images included" in panel.included_label.text()
        panel.filter_box.setText("s1")
        panel._include_rows(panel._shown_rows(), False)
        assert "2 of 3 images included" in panel.included_label.text()
        assert len(shell._nav_ids) == 2
        panel.filter_box.setText("")
        panel.table.selectRow(0)
        panel._include_only_selected()
        assert shell.controller.included_ids() == [shell.controller.experiment.images[0].image_id]
        panel.show_type.setCurrentIndex(panel.show_type.findData("nd2"))
        assert panel._shown_rows() == []  # no ND2 files in this folder
    finally:
        viewer.close()


def test_channels_use_the_file_colors(tmp_path: Path):
    from cellquant.gui.app import CellQuantWindow
    from cellquant.practice import create_practice_experiment

    create_practice_experiment(tmp_path / "practice")
    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"napari viewer could not start: {exc}")
    try:
        shell = CellQuantWindow(viewer, tmp_path / "practice" / "experiment")
        _wait(shell)
        top = {layer.name: tuple(np.round(layer.colormap.colors[-1][:3], 3)) for layer in viewer.layers if layer.name in ("Nuclei", "Marker A", "Marker B")}
        assert top == {"Nuclei": (0, 0, 1), "Marker A": (0, 1, 0), "Marker B": (1, 0, 0)}
    finally:
        viewer.close()
