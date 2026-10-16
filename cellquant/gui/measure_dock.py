"""The Measure window: draw lines and shapes on the image, see their length, area and volume, export a CSV.

The drawing happens in napari, in a "Measure" shapes layer; this window picks the drawing tool,
lists the measurements and writes them out. What is drawn is kept per image while the experiment
is open, so switching images and coming back keeps it.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from qtpy.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cellquant.measure import (
    DEPTH_OPTIONS,
    Calibration,
    measure_shapes,
    volume_unit,
    write_csv,
)

MEASURE_LAYER = "Measure"
# (button text, napari shapes mode, tooltip)
TOOLS = (
    ("Line", "add_line", "Straight line: click and drag. Gives its length."),
    ("Freehand line", "add_path", "Freehand line: hold the mouse button and draw. Gives its length."),
    ("Rectangle", "add_rectangle", "Rectangle or square: click and drag. Gives its area and volume."),
    ("Freehand shape", "add_polygon_lasso", "Freehand shape: hold the mouse button and draw around the area. Gives its area and volume."),
)


def image_name(record) -> str:
    return record.relative_path or record.filename


def calibration_of(record) -> Calibration:
    return Calibration(record.pixel_size_x, record.pixel_size_y, record.pixel_size_z, max(int(record.z_planes or 1), 1))


def depth_preference() -> str:
    from cellquant.gui.app import _settings

    try:
        value = str(_settings().value("measure/volume_depth", "stack"))
    except Exception:  # noqa: BLE001 - settings unreadable: use the default
        return "stack"
    return value if value in dict(DEPTH_OPTIONS) else "stack"


class MeasureDock(QWidget):
    def __init__(self, shell):
        super().__init__()
        self.shell = shell
        self._drawn: dict[str, list[tuple[np.ndarray, str]]] = {}  # image id -> shapes kept while not shown
        self._layer = None
        self._layer_image: str | None = None
        layout = QVBoxLayout(self)
        intro = QLabel("Draw on the image. ⓘ")
        intro.setToolTip(
            "Pick a tool, then draw on the image in napari. Each line or shape is measured here.\n"
            "Lines give a length; rectangles and freehand shapes give perimeter, area and volume.\n"
            "Units are µm from the image file, or pixels when the file has no pixel size.\n"
            "To move or delete a shape, use the select tool in napari's layer controls."
        )
        layout.addWidget(intro)
        tools = QHBoxLayout()
        self.tool_buttons = {}
        for text, mode, tip in TOOLS:
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(lambda _checked=False, mode=mode: self.draw(mode))
            tools.addWidget(button)
            self.tool_buttons[mode] = button
        layout.addLayout(tools)
        depth_row = QHBoxLayout()
        depth_label = QLabel("Volume depth")
        depth_row.addWidget(depth_label)
        self.depth = QComboBox()
        for key, label in DEPTH_OPTIONS:
            self.depth.addItem(label, key)
        self.depth.setCurrentIndex(max(self.depth.findData(depth_preference()), 0))
        tip = (
            "Volume = shape area × depth.\nWhole stack: every Z slice of the image (slices × Z step).\n"
            "One slice: one Z step. Remembered."
        )
        depth_label.setToolTip(tip)
        self.depth.setToolTip(tip)
        self.depth.currentIndexChanged.connect(self._depth_changed)
        depth_row.addWidget(self.depth, 1)
        layout.addLayout(depth_row)
        self.table = QTableWidget(0, 7)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.table.setColumnWidth(0, 140)  # long file names are cut short; the full name is in the tooltip
        layout.addWidget(self.table, 1)
        actions = QHBoxLayout()
        self.clear_button = QPushButton("Clear image")
        self.clear_button.setToolTip("Remove every line and shape drawn on this image.")
        self.clear_button.clicked.connect(self.clear_image)
        actions.addWidget(self.clear_button)
        self.export_button = QPushButton("Export CSV…")
        self.export_button.setToolTip(
            "Save every measurement, from every image, to a new CSV file you choose: one row per line or shape."
        )
        self.export_button.clicked.connect(self.export)
        actions.addWidget(self.export_button)
        layout.addLayout(actions)
        self.refresh()

    # -- the Measure layer ---------------------------------------------------

    def _layer_alive(self) -> bool:
        return self._layer is not None and self._layer in self.shell.viewer.layers

    def keep(self) -> None:
        """Before the image changes: keep what is drawn on the image shown."""

        if self._layer_alive() and self._layer_image is not None:
            shapes = [(np.array(vertices, copy=True), kind) for vertices, kind in zip(self._layer.data, self._layer.shape_type, strict=False)]
            if shapes:
                self._drawn[self._layer_image] = shapes
            else:
                self._drawn.pop(self._layer_image, None)
        self._layer = None
        self._layer_image = None

    def bring_back(self, image_id: str | None) -> None:
        """After an image is shown: put back its shapes (the layer is added when there are any or the window is open)."""

        self.keep()
        if image_id is None:
            return
        if image_id in self._drawn or self.isVisible():
            self.ensure_layer(image_id)
        self.refresh()

    def ensure_layer(self, image_id: str | None = None):
        image_id = image_id or self.shell._shown_image_id
        if self._layer_alive() and self._layer_image == image_id:
            return self._layer
        if image_id is None:
            return None
        shapes = self._drawn.pop(image_id, [])
        viewer = self.shell.viewer
        if MEASURE_LAYER in viewer.layers:
            viewer.layers.remove(MEASURE_LAYER)
        layer = viewer.add_shapes(
            [vertices for vertices, _ in shapes] or None,
            shape_type=[kind for _, kind in shapes] or "polygon",
            ndim=2,
            name=MEASURE_LAYER,
            edge_color="orange",
            face_color="transparent",
            edge_width=2,
        )
        self.shell._managed.add(layer.name)
        self._layer = layer
        self._layer_image = image_id
        layer.events.data.connect(lambda _event: self.refresh())
        return layer

    def draw(self, mode: str) -> None:
        if self.shell.require_controller() is None or self.shell._shown_image_id is None:
            self.shell.message("Open an image first, then draw on it.")
            return
        layer = self.ensure_layer()
        self.shell.viewer.layers.selection.active = layer
        layer.mode = mode

    def clear_image(self) -> None:
        if self._layer_alive():
            self._layer.data = []
        self.refresh()

    # -- measurements --------------------------------------------------------

    def _depth_changed(self, _index: int) -> None:
        from cellquant.gui.app import _settings

        try:
            _settings().setValue("measure/volume_depth", self.depth.currentData())
        except Exception:  # noqa: BLE001, S110 - not remembered, still used
            pass
        self.refresh()

    def _shapes_by_image(self) -> list[tuple[object, list[tuple[np.ndarray, str]]]]:
        controller = self.shell.controller
        if controller is None:
            return []
        shown = {}
        if self._layer_alive() and self._layer_image is not None:
            shown[self._layer_image] = list(zip(self._layer.data, self._layer.shape_type, strict=False))
        out = []
        for record in controller.experiment.images:
            shapes = shown.get(record.image_id, self._drawn.get(record.image_id, []))
            if len(shapes):
                out.append((record, shapes))
        return out

    def measurements(self):
        """Every drawn shape's measurements, in image order. µm only when every image measured has a pixel size."""

        by_image = self._shapes_by_image()
        use_um = all(calibration_of(record).calibrated for record, _ in by_image)
        depth = self.depth.currentData()
        rows = []
        for record, shapes in by_image:
            rows.extend(measure_shapes(shapes, calibration_of(record), image=image_name(record), depth_mode=depth, use_um=use_um))
        return rows

    def refresh(self) -> None:
        rows = self.measurements()
        unit = rows[0].unit if rows else "µm"
        area = "µm²" if unit == "µm" else "px²"
        self.table.setHorizontalHeaderLabels(
            ["Image", "#", "Shape", f"Length ({unit})", f"Perimeter ({unit})", f"Area ({area})", f"Volume ({volume_unit(unit)})"]
        )
        self.table.setRowCount(len(rows))
        for row, item in enumerate(rows):
            values = [item.image, str(item.number), item.shape]
            values += ["" if value is None else f"{value:.2f}" for value in (item.length, item.perimeter, item.area, item.volume)]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if column == 0:
                    cell.setToolTip(item.image)
                elif column == 6 and item.depth:
                    cell.setToolTip(f"Depth: {item.depth}")
                self.table.setItem(row, column, cell)
        self.export_button.setEnabled(bool(rows))

    def export(self) -> None:
        rows = self.measurements()
        if not rows:
            self.shell.message("Draw a line or shape first.")
            return
        controller = self.shell.controller
        start = str(Path(controller.directory) / "measurements.csv") if controller is not None else "measurements.csv"
        path, _ = QFileDialog.getSaveFileName(self, "Export measurements", start, "CSV files (*.csv)")
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        try:
            write_csv(path, rows)
        except OSError as exc:
            self.shell.show_error(f"Could not save {path}: {exc}")
            return
        self.shell.message(f"Exported {len(rows)} measurement{'s' if len(rows) != 1 else ''} to {path}.")

    def forget(self) -> None:
        """A different experiment was opened: drop the shapes kept for the old one."""

        self._drawn.clear()
        if self._layer_alive():
            self.shell._drop(MEASURE_LAYER)
        self._layer = None
        self._layer_image = None
        self.refresh()
