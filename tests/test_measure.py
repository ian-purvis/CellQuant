"""The Measure window: lengths, areas and volumes of drawn shapes, kept per image, exported to a CSV."""

from __future__ import annotations

import csv
import math
from pathlib import Path

import numpy as np
import pytest

from cellquant.measure import Calibration, measure_shape, measure_shapes, write_csv

UM = Calibration(pixel_size_x=0.5, pixel_size_y=0.5, pixel_size_z=2.0, z_slices=7)


def test_line_and_freehand_line_lengths():
    line = measure_shape([[0, 0], [30, 40]], "line", UM)
    assert line.shape == "line" and line.unit == "µm"
    assert line.length == pytest.approx(25.0)  # 50 px × 0.5 µm
    assert line.area is None and line.volume is None
    path = measure_shape([[0, 0], [0, 10], [10, 10]], "path", UM)
    assert path.shape == "freehand line" and path.length == pytest.approx(10.0)


def test_closed_shapes_area_perimeter_and_volume_depth():
    square = [[0, 0], [0, 10], [10, 10], [10, 0]]
    stack = measure_shape(square, "rectangle", UM, depth_mode="stack")
    assert stack.area == pytest.approx(25.0)  # (10 × 0.5)²
    assert stack.perimeter == pytest.approx(20.0)
    assert stack.volume == pytest.approx(25.0 * 7 * 2.0)
    assert stack.depth == "whole stack (7 slices)"
    one = measure_shape(square, "polygon", UM, depth_mode="slice")
    assert one.shape == "freehand shape" and one.volume == pytest.approx(50.0) and one.depth == "one slice"
    ellipse = measure_shape([[0, 0], [0, 20], [20, 20], [20, 0]], "ellipse", UM)
    assert ellipse.area == pytest.approx(math.pi * 5 * 5, rel=0.01)


def test_pixels_without_calibration_and_no_z_step():
    bare = Calibration(z_slices=3)
    square = measure_shape([[0, 0], [0, 4], [4, 4], [4, 0]], "rectangle", bare)
    assert square.unit == "px" and square.area == pytest.approx(16.0) and square.volume == pytest.approx(48.0)
    flat = Calibration(pixel_size_x=1.0, pixel_size_y=1.0)
    assert measure_shape([[0, 0], [0, 4], [4, 4]], "polygon", flat).volume is None  # no Z step: no volume


def test_csv_names_units_in_header(tmp_path: Path):
    rows = measure_shapes(
        [(np.array([[0, 0], [0, 10]]), "line"), (np.array([[0, 0], [0, 10], [10, 10], [10, 0]]), "rectangle")],
        UM,
        image="a.nd2",
    )
    path = tmp_path / "out.csv"
    write_csv(path, rows)
    with open(path, encoding="utf-8-sig", newline="") as handle:
        table = list(csv.reader(handle))
    assert table[0] == ["image", "shape_number", "shape", "length (µm)", "perimeter (µm)", "area (µm²)", "volume (µm³)", "volume_depth"]
    assert table[1][:4] == ["a.nd2", "1", "line", "5.0"]
    assert table[2][2] == "rectangle" and table[2][5] == "25.0" and table[2][6] == "350.0"


def test_measure_window_keeps_shapes_per_image_and_exports(tmp_path: Path, monkeypatch):
    pytest.importorskip("napari")
    import napari
    from qtpy.QtWidgets import QFileDialog

    from cellquant.controller import AnalysisController
    from cellquant.gui.app import CellQuantWindow
    from cellquant.gui.measure_dock import MEASURE_LAYER
    from tests.test_gui_crop import _events, _wait
    from tests.test_workflow import _write_squares

    first, second = tmp_path / "a.tif", tmp_path / "b.tif"
    _write_squares(first)
    _write_squares(second)
    controller = AnalysisController.create(tmp_path / "experiment", "Measure")
    controller.add_image_paths([first, second])
    controller.save()
    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"napari viewer could not start: {exc}")
    shell = CellQuantWindow(viewer, controller.directory)
    try:
        _wait(shell)
        shell.show_measure()
        dock = shell._measure_dock
        dock.draw("add_line")
        layer = viewer.layers[MEASURE_LAYER]
        assert layer.mode == "add_line"
        layer.add_lines(np.array([[0.0, 0.0], [30.0, 40.0]]))
        layer.add_rectangles(np.array([[0.0, 0.0], [10.0, 10.0]]))
        _events()
        assert dock.table.rowCount() == 2
        assert dock.table.item(0, 3).text() == "50.00"

        shell._nav_index = 1
        shell.show_current()
        _wait(shell)
        assert len(viewer.layers[MEASURE_LAYER].data) == 0  # the second image has nothing drawn
        assert dock.table.rowCount() == 2  # the first image's shapes are still listed
        shell._nav_index = 0
        shell.show_current()
        _wait(shell)
        assert len(viewer.layers[MEASURE_LAYER].data) == 2  # and come back with their image

        out = tmp_path / "chosen" / "my measures"
        out.parent.mkdir()
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(out), ""))
        dock.export()
        with open(str(out) + ".csv", encoding="utf-8-sig", newline="") as handle:
            table = list(csv.reader(handle))
        assert [row[2] for row in table[1:]] == ["line", "rectangle"]
        assert table[0][3] == "length (px)"
    finally:
        viewer.close()
