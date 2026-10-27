"""Step 5 Edit cells: select objects and call them positive or negative by hand for a marker.

Selecting works like QuPath: click an object, Shift+click to add or remove one, draw around
objects, or select every object with a call. The chosen call then overrides the cutoff for those
objects only (``AnalysisController.call_cells``). napari only shows and takes the clicks; every
function is a button here.
"""

from __future__ import annotations

import numpy as np
from qtpy.QtCore import Qt, QTimer
from qtpy.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from cellquant.count_area import outside
from cellquant.errors import CellQuantError

SELECTED_LAYER = "Selected cells"
CALLED_LAYER = "Called by hand"
SELECT_AREA_LAYER = "Select area"
CALLED_COLOR = "#ffd54a"

CALL_WORDS = {True: "Positive", False: "Negative", None: "Use cutoff"}


class EditCellsWindow(QDialog):
    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.shell = shell
        self.setWindowTitle("Edit cells")
        self.setWindowFlag(Qt.WindowType.Tool, True)
        self.setModal(False)
        self.selected: set[int] = set()
        self._image_id: str | None = None
        self._click_bound = False
        layout = QVBoxLayout(self)
        self.marker_line = QLabel("")
        self.marker_line.setWordWrap(True)
        layout.addWidget(self.marker_line)
        layout.addWidget(self._select_box())
        layout.addWidget(self._call_box())
        layout.addWidget(self._selected_box())
        layout.addWidget(self._called_box())
        self.counts = QLabel("")
        self.counts.setWordWrap(True)
        layout.addWidget(self.counts)
        self.resize(520, 640)

    # ----- layout

    def _select_box(self) -> QGroupBox:
        box = QGroupBox("Select")
        rows = QVBoxLayout(box)
        line = QHBoxLayout()
        self.click_button = QPushButton("Click")
        self.click_button.setCheckable(True)
        self.click_button.setChecked(True)
        self.click_button.setToolTip("Click an object in the image to select it. Shift+click adds or removes one.")
        self.area_button = QPushButton("Select in area")
        self.area_button.setCheckable(True)
        self.area_button.setToolTip("Draw around objects in the image: every object entirely inside is selected.")
        self.area_button.toggled.connect(self._area_mode)
        self.select_all = QComboBox()
        self.select_all.addItem("Select all…", "")
        self.select_all.addItem("Positive", "positive")
        self.select_all.addItem("Negative", "negative")
        self.select_all.addItem("Called by hand", "by_hand")
        self.select_all.setToolTip("Select every counted object with this call for the marker shown.")
        self.select_all.activated.connect(lambda _index: self._select_by_call())
        clear = QPushButton("Clear")
        clear.setToolTip("Select nothing.")
        clear.clicked.connect(lambda: self.set_selection(set()))
        for widget in (self.click_button, self.area_button, self.select_all, clear):
            line.addWidget(widget)
        rows.addLayout(line)
        self.selection_line = QLabel("No cells selected.")
        rows.addWidget(self.selection_line)
        return box

    def _call_box(self) -> QGroupBox:
        box = QGroupBox("Call for the marker shown")
        rows = QVBoxLayout(box)
        line = QHBoxLayout()
        self.positive_button = QPushButton("Positive")
        self.negative_button = QPushButton("Negative")
        self.cutoff_button = QPushButton("Use cutoff")
        self.positive_button.setToolTip("Call the selected cells positive, whatever the cutoff.")
        self.negative_button.setToolTip("Call the selected cells negative, whatever the cutoff.")
        self.cutoff_button.setToolTip("Let the cutoff call the selected cells again.")
        for button, call in ((self.positive_button, True), (self.negative_button, False), (self.cutoff_button, None)):
            button.clicked.connect(lambda _checked=False, call=call: self.call_selected(call))
            line.addWidget(button)
        line.addStretch(1)
        rows.addLayout(line)
        note = QLabel("The cutoff no longer decides these cells for this marker until you click Use cutoff.")
        note.setWordWrap(True)
        rows.addWidget(note)
        return box

    def _selected_box(self) -> QGroupBox:
        box = QGroupBox("Selected cells, every marker")
        rows = QVBoxLayout(box)
        self.selected_table = QTableWidget(0, 6)
        self.selected_table.setHorizontalHeaderLabels(["Cell", "Marker", "Value", "Cutoff", "By cutoff", "Call"])
        self.selected_table.horizontalHeaderItem(5).setToolTip("Set any marker's call for that cell here.")
        self.selected_table.verticalHeader().setVisible(False)
        self.selected_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        rows.addWidget(self.selected_table)
        return box

    def _called_box(self) -> QGroupBox:
        self.called_box = QGroupBox("Called by hand in this image")
        rows = QVBoxLayout(self.called_box)
        self.called_table = QTableWidget(0, 4)
        self.called_table.setHorizontalHeaderLabels(["Cell", "Marker", "By cutoff", "Call"])
        self.called_table.verticalHeader().setVisible(False)
        self.called_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.called_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.called_table.setToolTip("Click a row to select that cell and center the image on it.")
        self.called_table.cellClicked.connect(self._called_row_clicked)
        rows.addWidget(self.called_table)
        line = QHBoxLayout()
        self.undo_button = QPushButton("Undo")
        self.undo_button.setToolTip("Take back the last call made by hand in this image.")
        self.undo_button.clicked.connect(self._undo)
        self.clear_button = QPushButton("Use cutoff for all…")
        self.clear_button.setToolTip("Let the cutoff call every cell of this image again, for the marker shown.")
        self.clear_button.clicked.connect(self._clear_all)
        line.addWidget(self.undo_button)
        line.addWidget(self.clear_button)
        line.addStretch(1)
        rows.addLayout(line)
        return self.called_box

    # ----- state

    def _context(self):
        """(controller, marker shown or None, image id or None, result or None)."""

        shell = self.shell
        controller = shell.controller
        image_id = shell._review_panel._shown_image_id()
        classification = shell._review_panel._current_classification()
        result = controller.last_results.get(image_id) if controller is not None and image_id else None
        return controller, classification, image_id, result

    def showEvent(self, event):  # noqa: N802 - Qt name
        super().showEvent(event)
        if not self._click_bound:
            self.shell.viewer.mouse_drag_callbacks.append(self._on_click)
            self._click_bound = True
        self.shell._review_panel.leave_marking()  # a click selects here, it does not mark an example
        self.refresh()

    def hideEvent(self, event):  # noqa: N802 - Qt name
        self.area_button.setChecked(False)
        self.shell._drop(SELECTED_LAYER)
        self.shell._drop(CALLED_LAYER)
        super().hideEvent(event)

    def refresh(self, result=None) -> None:
        """Show the image and marker on screen now: selection, tables, counts and the two layers."""

        if not self.isVisible():
            return
        controller, classification, image_id, current = self._context()
        result = result if result is not None else current
        if image_id != self._image_id:
            self.selected = set()  # numbers belong to one image
            self._image_id = image_id
        if result is not None and "object_id" in result.objects.columns:
            present = set(int(value) for value in result.objects["object_id"].tolist())
            self.selected &= present
        on = controller is not None and result is not None and classification is not None
        for widget in (self.positive_button, self.negative_button, self.cutoff_button, self.select_all, self.clear_button):
            widget.setEnabled(on)
        self.undo_button.setEnabled(bool(image_id) and controller is not None and controller.can_undo_cell_calls(image_id))
        if controller is None or result is None:
            self.marker_line.setText("Run this image first.")
        elif classification is None:
            self.marker_line.setText(
                "Calls are made one marker at a time: choose a single marker (not all markers together) under "
                "<b>Display objects by</b> in step 5."
            )
        else:
            self.marker_line.setText(f"Marker shown: <b>{classification.name}</b>")
        if on:
            positive, negative = _colors()
            self.positive_button.setStyleSheet(f"border-left: 10px solid {positive};")
            self.negative_button.setStyleSheet(f"border-left: 10px solid {negative};")
        self._show_selection(result)
        self._show_called(result)
        self._show_counts(result, classification)

    def _show_selection(self, result) -> None:
        ids = sorted(self.selected)
        if not ids:
            self.selection_line.setText("No cells selected.")
        else:
            shown = ", ".join(f"#{value}" for value in ids[:8]) + (" …" if len(ids) > 8 else "")
            self.selection_line.setText(f"{len(ids)} cell{'s' if len(ids) != 1 else ''} selected ({shown}).")
        self._fill_selected_table(result)
        self._show_selected_layer(result)

    def _fill_selected_table(self, result) -> None:
        table = self.selected_table
        table.setRowCount(0)
        controller, _classification, image_id, _current = self._context()
        if result is None or controller is None or not self.selected:
            return
        rows = result.objects.drop_duplicates("object_id").set_index("object_id")
        markers = controller.recipe.classifications
        ids = sorted(self.selected)[:50]  # a table of thousands helps nobody
        table.setRowCount(len(ids) * len(markers))
        row = 0
        for object_id in ids:
            calls = {item.id: controller.cell_calls(image_id, item.id) for item in markers}
            for item in markers:
                value = rows.at[object_id, item.measurement] if item.measurement in rows.columns else np.nan
                cutoff = item.threshold_for(image_id)
                by_cutoff = _by_cutoff(value, cutoff, item.comparison)
                called = calls[item.id].get(object_id)
                cells = [f"#{object_id}", item.name, _number(value), f"{cutoff:g}", CALL_WORDS.get(by_cutoff, "—")]
                for column, text in enumerate(cells):
                    table.setItem(row, column, QTableWidgetItem(text))
                choice = QComboBox()
                for call in (True, False, None):
                    label = CALL_WORDS[call] if call is not None else "Cutoff"
                    choice.addItem(f"◆ {label}" if call is not None and called is not None and call == called else label, call)
                choice.setCurrentIndex({True: 0, False: 1, None: 2}[called])
                choice.activated.connect(
                    lambda index, box=choice, marker=item.id, object_id=object_id: self._call_one(marker, object_id, box.itemData(index))
                )
                table.setCellWidget(row, 5, choice)
                row += 1
        table.resizeColumnsToContents()

    def _show_called(self, result) -> None:
        table = self.called_table
        table.setRowCount(0)
        controller, _classification, image_id, _current = self._context()
        if result is None or controller is None:
            self.called_box.setTitle("Called by hand in this image")
            self.shell._drop(CALLED_LAYER)
            return
        rows = result.objects.drop_duplicates("object_id").set_index("object_id")
        entries = []
        for item in controller.recipe.classifications:
            for object_id, positive in sorted(controller.cell_calls(image_id, item.id).items()):
                value = rows.at[object_id, item.measurement] if item.measurement in rows.columns else np.nan
                entries.append((object_id, item.name, _by_cutoff(value, item.threshold_for(image_id), item.comparison), positive))
        self.called_box.setTitle(f"Called by hand in this image ({len(entries)})")
        table.setRowCount(len(entries))
        for row, (object_id, name, by_cutoff, positive) in enumerate(entries):
            for column, text in enumerate([f"#{object_id}", name, CALL_WORDS.get(by_cutoff, "—"), CALL_WORDS[positive]]):
                cell = QTableWidgetItem(text)
                cell.setData(Qt.ItemDataRole.UserRole, object_id)
                table.setItem(row, column, cell)
        table.resizeColumnsToContents()
        self._show_called_layer(result, sorted({entry[0] for entry in entries}))

    def _show_counts(self, result, classification) -> None:
        if result is None or classification is None or classification.id not in result.objects.columns:
            self.counts.setText("")
            return
        frame = result.objects
        if "excluded" in frame.columns:
            frame = frame.loc[~frame["excluded"].astype(bool)]
        frame = frame.loc[~outside(frame)]
        calls = frame[classification.id]
        positive = int((calls == True).sum())  # noqa: E712 - nullable booleans
        counted = int(calls.notna().sum())
        by_hand = int(frame[f"{classification.id}_by_hand"].sum()) if f"{classification.id}_by_hand" in frame.columns else 0
        percent = f"{100 * positive / counted:.1f}%" if counted else "—"
        self.counts.setText(
            f"<b>{classification.name}</b>: {positive} positive of {counted} ({percent}), {by_hand} called by hand."
        )

    # ----- layers

    def _show_selected_layer(self, result) -> None:
        shell = self.shell
        if result is None or not self.selected:
            shell._drop(SELECTED_LAYER)
            return
        labels = np.asarray(result.labels)
        lookup = np.zeros(int(labels.max(initial=0)) + 1, dtype=np.uint8)
        ids = np.array([value for value in self.selected if value < len(lookup)], dtype=np.int64)
        lookup[ids] = 1
        data = lookup[labels]
        layer = shell._layer_if_shape(SELECTED_LAYER, data.shape)
        if layer is not None:
            layer.data = data
            return
        shell._drop(SELECTED_LAYER)
        active = shell.viewer.layers.selection.active
        from napari.utils.colormaps import DirectLabelColormap

        layer = shell.viewer.add_labels(data, name=SELECTED_LAYER, opacity=1.0)
        layer.colormap = DirectLabelColormap(color_dict={None: np.zeros(4, dtype=np.float32), 1: np.ones(4, dtype=np.float32)})
        layer.contour = 3
        shell._managed.add(layer.name)
        shell._scale_layer(layer)
        if active is not None and active in shell.viewer.layers:
            shell.viewer.layers.selection.active = active

    def _show_called_layer(self, result, object_ids: list[int]) -> None:
        shell = self.shell
        frame = result.objects
        frame = frame.loc[frame["object_id"].isin(object_ids)].drop_duplicates("object_id") if object_ids else frame.iloc[0:0]
        if frame.empty:
            shell._drop(CALLED_LAYER)
            return
        centers = shell.object_centers(result, frame)
        if CALLED_LAYER in shell.viewer.layers and shell.viewer.layers[CALLED_LAYER].ndim == centers.shape[1]:
            layer = shell.viewer.layers[CALLED_LAYER]
            layer.selected_data = set()
            layer.data = centers
        else:
            shell._drop(CALLED_LAYER)
            active = shell.viewer.layers.selection.active
            layer = shell.viewer.add_points(centers, name=CALLED_LAYER, size=5, symbol="diamond")
            shell._managed.add(layer.name)
            if active is not None and active in shell.viewer.layers:
                shell.viewer.layers.selection.active = active
        layer.face_color = CALLED_COLOR
        layer.border_color = "black"

    # ----- selecting

    def set_selection(self, object_ids) -> None:
        """Select these objects (replacing the selection). Other windows may call this too."""

        self.selected = {int(value) for value in object_ids}
        _controller, _classification, _image_id, result = self._context()
        self._show_selection(result)

    def _on_click(self, viewer, event):
        if not self.isVisible() or not self.click_button.isChecked() or self.area_button.isChecked():
            return
        review = self.shell._review_panel
        if review._marking is not None:
            return  # Mark + / Mark − take the click
        start = tuple(event.position)
        shift = "Shift" in {str(getattr(key, "name", key)) for key in getattr(event, "modifiers", ())}
        dragged = False
        yield
        while event.type == "mouse_move":
            dragged = True
            yield
        labels = review._labels
        if dragged or labels is None or labels not in viewer.layers:
            return
        if self.shell._tabs.currentWidget() is not self.shell._step_pages[4]:
            return
        active = viewer.layers.selection.active
        if active is not None and getattr(active, "mode", "pan_zoom") not in ("pan_zoom", "transform"):
            return  # drawing a count area or measuring: the click belongs to that tool
        try:
            value = labels.get_value(start, view_direction=event.view_direction, dims_displayed=event.dims_displayed, world=True)
        except Exception:  # noqa: BLE001 - a click outside the image selects nothing
            value = None
        object_id = int(value) if isinstance(value, (int, np.integer)) and int(value) > 0 else None
        if shift:
            if object_id is not None:
                self.set_selection(self.selected ^ {object_id})
        else:
            self.set_selection({object_id} if object_id is not None else set())

    def _area_mode(self, on: bool) -> None:
        shell = self.shell
        if not on:
            shell._drop(SELECT_AREA_LAYER)
            return
        if shell.controller is None or not shell._nav_ids:
            self.area_button.setChecked(False)
            return
        shell._drop(SELECT_AREA_LAYER)
        layer = shell.viewer.add_shapes(
            name=SELECT_AREA_LAYER, edge_color="white", face_color="transparent", edge_width=2, ndim=2
        )
        shell._managed.add(layer.name)
        shell._scale_layer(layer)
        layer.events.data.connect(lambda _event: QTimer.singleShot(0, self._area_drawn))
        shell.viewer.layers.selection.active = layer
        layer.mode = "add_polygon_lasso" if "add_polygon_lasso" in _modes(layer) else "add_polygon"
        shell.message("Draw around the cells to select.")

    def _area_drawn(self) -> None:
        shell = self.shell
        if SELECT_AREA_LAYER not in shell.viewer.layers:
            return
        layer = shell.viewer.layers[SELECT_AREA_LAYER]
        polygons = [np.asarray(shape)[:, -2:] for shape in layer.data if len(shape) >= 3]
        if not polygons:
            return
        _controller, _classification, _image_id, result = self._context()
        if result is None:
            return
        from cellquant.edits import objects_inside

        found = set(objects_inside(result.labels, polygons))
        self.set_selection(found)
        self.area_button.setChecked(False)
        shell.message(f"{len(found)} cell{'s' if len(found) != 1 else ''} selected." if found else "No cell lies entirely inside the area.")

    def _select_by_call(self) -> None:
        choice = self.select_all.currentData()
        self.select_all.setCurrentIndex(0)
        controller, classification, image_id, result = self._context()
        if not choice or result is None or classification is None or classification.id not in result.objects.columns:
            return
        frame = result.objects
        if "excluded" in frame.columns:
            frame = frame.loc[~frame["excluded"].astype(bool)]
        frame = frame.loc[~outside(frame)]
        if choice == "by_hand":
            ids = set(controller.cell_calls(image_id, classification.id))
        else:
            wanted = choice == "positive"
            calls = frame[classification.id]
            ids = set(int(value) for value in frame.loc[calls.notna() & (calls == wanted), "object_id"].tolist())
        self.set_selection(ids)

    def _called_row_clicked(self, row: int, _column: int) -> None:
        cell = self.called_table.item(row, 0)
        if cell is None:
            return
        object_id = int(cell.data(Qt.ItemDataRole.UserRole))
        self.set_selection({object_id})
        _controller, _classification, _image_id, result = self._context()
        if result is not None:
            frame = result.objects.loc[result.objects["object_id"] == object_id].drop_duplicates("object_id")
            if not frame.empty:
                self.shell.center_on(self.shell.object_centers(result, frame)[0])

    # ----- calling

    def call_selected(self, call: bool | None) -> None:
        _controller, classification, _image_id, _result = self._context()
        if classification is None:
            self.shell.message("Choose a marker under Display objects by first.")
            return
        if not self.selected:
            self.shell.message("Select cells first: click them in the image, or draw around them.")
            return
        self._apply(classification.id, sorted(self.selected), call)

    def _call_one(self, classification_id: str, object_id: int, call: bool | None) -> None:
        self._apply(classification_id, [object_id], call)

    def _apply(self, classification_id: str, object_ids: list[int], call: bool | None) -> None:
        controller, _classification, image_id, _result = self._context()
        if not self._ready(controller, image_id):
            return
        was = controller.experiment.image(image_id).processing_status
        try:
            result = controller.call_cells(image_id, classification_id, object_ids, call)
        except CellQuantError as exc:
            self.shell.message(str(exc))
            return
        self._after_call(result, was, f"{len(object_ids)} cell{'s' if len(object_ids) != 1 else ''}: {CALL_WORDS[call].lower()}.")

    def _ready(self, controller, image_id) -> bool:
        """Calls change the settings: never while an analysis runs or another image is loading."""

        if controller is None or image_id is None:
            return False
        if self.shell.is_busy():
            self.shell.message("Wait for the current analysis to finish, then try again.")
            return False
        if self.shell._labels_image_id != image_id:
            self.shell.message("Wait for the image to finish loading, then try again.")
            return False
        return True

    def _undo(self) -> None:
        controller, _classification, image_id, _result = self._context()
        if not self._ready(controller, image_id):
            return
        was = controller.experiment.image(image_id).processing_status
        self._after_call(controller.undo_cell_calls(image_id), was, "Last call by hand taken back.")

    def _clear_all(self) -> None:
        controller, classification, image_id, _result = self._context()
        if classification is None or not self._ready(controller, image_id):
            return
        count = len(controller.cell_calls(image_id, classification.id))
        if not count:
            self.shell.message(f"No cell of this image is called by hand for {classification.name}.")
            return
        answer = QMessageBox.question(
            self,
            "Use the cutoff for every cell?",
            f"Let the cutoff call the {count} cell{'s' if count != 1 else ''} called by hand for {classification.name} in this image? Undo brings the calls back.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes or not self._ready(controller, image_id):
            return
        was = controller.experiment.image(image_id).processing_status
        self._after_call(controller.clear_cell_calls(image_id, classification.id), was, "The cutoff calls every cell again.")

    def _after_call(self, result, was: str, text: str) -> None:
        """Save, show the new calls everywhere, and say whether the approval was cleared."""

        controller, _classification, image_id, _result = self._context()
        controller.save()
        if result is not None:
            self.shell.show_classification(result)
        if was == "approved" and controller.experiment.image(image_id).processing_status != "approved":
            text += " Approval cleared: the percent positive changed."
            self.shell._experiment_panel.refresh_table()
            self.shell._refresh_plan()
            self.shell._review_panel.update_status_line()
            self.shell.refresh_guidance()
        self.refresh(result)
        self.shell.message(text)


def _colors() -> tuple[str, str]:
    from cellquant.gui.app import classification_colors

    return classification_colors()


def _by_cutoff(value, cutoff: float, comparison: str) -> bool | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(number):
        return None
    return bool(number >= cutoff if comparison == "at_least" else number > cutoff)


def _number(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{number:.4g}" if np.isfinite(number) else "—"


def _modes(layer) -> set[str]:
    try:
        return {str(mode) for mode in type(layer.mode).__members__.values()} | {mode.value for mode in type(layer.mode)}
    except Exception:  # noqa: BLE001 - older napari: plain strings
        return {"add_polygon"}
