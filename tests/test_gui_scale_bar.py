"""The scale bar reads in µm when the image has a pixel size (px when not); Measure sits under the layer controls."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest


def _shell(tmp_path: Path, pixel_size):
    pytest.importorskip("napari")
    import napari

    from cellquant.controller import AnalysisController
    from cellquant.gui.app import CellQuantWindow
    from tests.test_gui_crop import _wait
    from tests.test_workflow import _write_squares

    image = tmp_path / "a.tif"
    _write_squares(image)
    controller = AnalysisController.create(tmp_path / "experiment", "Scale")
    controller.add_image_paths([image])
    image_id = controller.experiment.images[0].image_id
    controller.set_pixel_size(image_id, pixel_size, pixel_size, None)
    controller.save()
    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"napari viewer could not start: {exc}")
    shell = CellQuantWindow(viewer, controller.directory)
    _wait(shell)
    return viewer, shell


def test_layers_carry_the_pixel_size_in_micrometres(tmp_path: Path):
    viewer, shell = _shell(tmp_path, 0.57)
    try:
        image = viewer.layers[0]
        assert np.allclose(image.scale[-2:], 0.57)
        assert str(image.units[-1]) == "micrometer"
        shapes = shell.delete_area_layer()  # layers added later get it too
        assert np.allclose(shapes.scale, 0.57)
        assert viewer.scale_bar.unit == "µm"
    finally:
        viewer.close()


def test_scale_bar_reads_pixels_without_a_pixel_size(tmp_path: Path):
    viewer, shell = _shell(tmp_path, None)
    try:
        assert np.allclose(viewer.layers[0].scale, 1.0)
        assert viewer.scale_bar.unit == "px"
    finally:
        viewer.close()


def test_measure_button_is_under_the_layer_controls(tmp_path: Path):
    viewer, shell = _shell(tmp_path, 0.57)
    try:
        button = shell._view_box.measure
        assert button.text().startswith("Measure")
        button.click()
        assert shell._measure_dock_widget.isVisible() or shell._measure_dock_widget.isFloating()
        assert "Measure" in viewer.layers
    finally:
        viewer.close()


def test_max_projection_round_trip_keeps_z_size(tmp_path: Path):
    from cellquant.controller import AnalysisController
    from cellquant.gui.app import CellQuantWindow
    from tests.test_gui_crop import _wait

    pytest.importorskip("napari")
    import napari

    stack = Path("/mnt/project-files/test-images/CRISPRi cont mCherry_OTX2 far red001.nd2")
    if not stack.exists():
        pytest.skip("the real test ND2 is not available")
    controller = AnalysisController.create(tmp_path / "experiment", "Z")
    controller.add_image_paths([stack])
    image_id = controller.experiment.images[0].image_id
    controller.set_pixel_size(image_id, 0.5, 0.5, 2.0)
    controller.save()
    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"napari viewer could not start: {exc}")
    shell = CellQuantWindow(viewer, controller.directory)
    try:
        _wait(shell)
        layer = viewer.layers[0]
        assert layer.ndim == 3 and np.allclose(layer.scale, (2.0, 0.5, 0.5))
        shell._view_box.max_projection.setChecked(True)
        assert layer.ndim == 2 and np.allclose(layer.scale, 0.5)
        shell._view_box.max_projection.setChecked(False)
        assert layer.ndim == 3 and np.allclose(layer.scale, (2.0, 0.5, 0.5))
        assert str(layer.units[0]) == "micrometer"
        # No Z step: Z stays one pixel, x and y keep µm.
        controller.set_pixel_size(image_id, 0.5, 0.5, 0)
        shell.set_pixel_scale(controller.experiment.image(image_id))
        assert np.allclose(layer.scale, (1.0, 0.5, 0.5))
        assert [str(unit) for unit in layer.units] == ["pixel", "micrometer", "micrometer"]
    finally:
        viewer.close()


def test_switching_images_uses_each_images_pixel_size(tmp_path: Path):
    from cellquant.controller import AnalysisController
    from cellquant.gui.app import CellQuantWindow
    from tests.test_gui_crop import _wait
    from tests.test_workflow import _write_squares

    pytest.importorskip("napari")
    import napari

    first, second = tmp_path / "a.tif", tmp_path / "b.tif"
    _write_squares(first)
    _write_squares(second)
    controller = AnalysisController.create(tmp_path / "experiment", "Two")
    controller.add_image_paths([first, second])
    ids = [record.image_id for record in controller.experiment.images]
    controller.set_pixel_size(ids[0], 0.5, 0.5, None)
    controller.set_pixel_size(ids[1], None, None, None)
    controller.save()
    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"napari viewer could not start: {exc}")
    shell = CellQuantWindow(viewer, controller.directory)
    try:
        _wait(shell)
        assert np.allclose(viewer.layers[0].scale, 0.5) and viewer.scale_bar.unit == "µm"
        shell._nav_index = 1
        shell.show_current()
        _wait(shell)
        assert np.allclose(viewer.layers[0].scale, 1.0) and viewer.scale_bar.unit == "px"
    finally:
        viewer.close()
