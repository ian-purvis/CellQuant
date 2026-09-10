"""Guided, independent nuclear marker classification in the active napari dock."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import numpy as np
from qtpy import QtCore, QtWidgets as Q

from cellquant.classify import ClassificationRecipe, classify_labels
from cellquant.classify.store import save_classification, reopen_classification
from cellquant.contracts import LabelVolume, MutableCancellationToken, PipelineCancelled
from cellquant.preprocess import prepare_analysis_volume
from .controller import _validated_label_array

OVERLAY_NAME = "Coexpression calls (review only)"


def call_overlay(labels, label_ids, codes):
    """Map sparse nucleus IDs to display codes without scanning per nucleus."""
    labels = np.asarray(labels)
    ids = np.asarray(label_ids, dtype=labels.dtype)
    values = np.asarray(codes, dtype=np.uint8)
    output = np.zeros(labels.shape, dtype=np.uint8)
    if not len(ids):
        return output
    order = np.argsort(ids)
    ids, values = ids[order], values[order]
    # Bound lookup temporaries independently of volume size and maximum ID.
    source, target = labels.reshape(-1), output.reshape(-1)
    for start in range(0, source.size, 1_000_000):
        chunk = source[start:start + 1_000_000]
        positions = np.searchsorted(ids, chunk)
        positions = np.minimum(positions, len(ids) - 1)
        matches = (chunk != 0) & (ids[positions] == chunk)
        target[start:start + len(chunk)] = np.where(matches, values[positions], 0)
    return output

# Keep marker columns wide enough that headers stay readable without guessing.
_MARKER_COLUMN_MIN_WIDTHS = (110, 200, 90, 140, 130, 140)


def configure_readable_table(
    table: Q.QTableWidget,
    *,
    min_widths: tuple[int, ...] | None = None,
    resize_to_contents: bool = True,
) -> None:
    """Let users read (and horizontally resize) every column in a crowded dock."""

    table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
    table.setHorizontalScrollMode(Q.QAbstractItemView.ScrollPerPixel)
    table.setWordWrap(False)
    elide_none = getattr(QtCore.Qt, "ElideNone", None)
    if elide_none is None:
        elide_none = getattr(getattr(QtCore.Qt, "TextElideMode", object), "ElideNone", None)
    if elide_none is not None:
        table.setTextElideMode(elide_none)
    header = table.horizontalHeader()
    header.setStretchLastSection(False)
    header.setMinimumSectionSize(72)
    header.setDefaultSectionSize(120)
    header.setSectionResizeMode(Q.QHeaderView.Interactive)
    set_tips = getattr(header, "setToolTipsVisible", None)
    if callable(set_tips):
        set_tips(True)
    if resize_to_contents and table.columnCount() > 0:
        table.resizeColumnsToContents()
    if min_widths is not None:
        for col, minimum in enumerate(min_widths):
            if col >= table.columnCount():
                break
            table.setColumnWidth(col, max(int(table.columnWidth(col)), int(minimum)))
            tip = table.horizontalHeaderItem(col)
            if tip is not None and tip.text():
                tip.setToolTip(tip.text())
    elif table.columnCount() > 0:
        for col in range(table.columnCount()):
            tip = table.horizontalHeaderItem(col)
            if tip is not None and tip.text():
                tip.setToolTip(tip.text())
            # Leave room to read values after contents-based sizing.
            table.setColumnWidth(col, max(int(table.columnWidth(col)), 96))


def _configure_readable_form(form: Q.QFormLayout) -> None:
    form.setRowWrapPolicy(Q.QFormLayout.WrapLongRows)
    form.setFieldGrowthPolicy(Q.QFormLayout.AllNonFixedFieldsGrow)
    form.setLabelAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)


def validate_transform(layer, spacing, *, image=False):
    """Only the original calibrated grid is accepted; display transforms are not registration."""
    expected = (*spacing, 1.0) if image else spacing
    if (len(layer.scale) != len(expected) or not np.allclose(layer.scale, expected)
            or not np.allclose(layer.translate, 0)
            or not np.allclose(layer.affine.affine_matrix, np.eye(len(expected) + 1))
            or not np.allclose(layer.rotate, np.eye(len(expected)))
            or not np.allclose(layer.shear, 0)):
        raise ValueError(f"{layer.name}: restore its original scale/translation/rotation/affine before scoring. Transforms do not register measurement pixels.")


class CoexpressionPanel(Q.QWidget):
    def __init__(self, viewer, controller):
        super().__init__()
        self.viewer, self.controller = viewer, controller
        self.revision = 0
        self.result = None
        self.future = None
        self._queries = None
        self._expected_channel_names = None
        self._calibrations = {}
        self.calibration_panel = None
        self._source_signature = None
        self._watched = set()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cellquant-classify")
        self.cancel_token = MutableCancellationToken()
        layout = Q.QVBoxLayout(self)
        help_text = Q.QLabel("Nuclear coexpression: open an image in Single image, then select reviewed nuclei. Pixel thresholds use raw fluorescence; the positive fraction is how much of each nucleus must pass. Cytoplasmic assignment is not supported here.")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        form = Q.QFormLayout()
        _configure_readable_form(form)
        layout.addLayout(form)
        self.image = Q.QComboBox(); self.labels = Q.QComboBox(); self.region = Q.QComboBox()
        for title, widget in [("Source image (ZYXC)", self.image), ("Reviewed nuclei (ZYX)", self.labels), ("Counting region", self.region)]:
            widget.setMinimumWidth(180)
            widget.setSizePolicy(Q.QSizePolicy.Expanding, Q.QSizePolicy.Fixed)
            form.addRow(title, widget)
            widget.currentIndexChanged.connect(self.mark_stale)
        self.image.currentIndexChanged.connect(self.refresh_channels)
        self.fields = {}
        for key, title in [("name", "Recipe name"), ("calibration_group", "Calibration group"), ("specimen_id", "Specimen"), ("eye_id", "Eye (optional)"), ("section_id", "Section (optional)"), ("image_id", "Image ID"), ("region_id", "Region name")]:
            field = Q.QLineEdit()
            field.setMinimumWidth(180)
            field.setSizePolicy(Q.QSizePolicy.Expanding, Q.QSizePolicy.Fixed)
            self.fields[key] = field
            form.addRow(title, field); field.textChanged.connect(self.mark_stale)
        self.fields["name"].setText("Nuclear coexpression")
        self.policy = Q.QComboBox()
        self.policy.setMinimumWidth(180)
        self.policy.setSizePolicy(Q.QSizePolicy.Expanding, Q.QSizePolicy.Fixed)
        self.policy.addItem("Whole object: every voxel inside region", "whole_object")
        self.policy.addItem("Centroid: nearest centroid voxel inside region", "centroid")
        form.addRow("Region membership", self.policy)
        self.policy.currentIndexChanged.connect(self.mark_stale)
        note = Q.QLabel("No region layer means the entire image. Nonzero region labels define inclusion; measurements always use the complete nucleus. Name the region explicitly.")
        note.setWordWrap(True); layout.addWidget(note)
        self.markers = Q.QTableWidget(0, 6)
        self.markers.setHorizontalHeaderLabels(["Marker", "Channel", "Raw low", "Raw high (optional)", "Positive fraction", "Uncertainty margin"])
        self.markers.setMinimumHeight(165)
        self.markers.setMinimumWidth(720)
        configure_readable_table(self.markers, min_widths=_MARKER_COLUMN_MIN_WIDTHS)
        self.markers.itemChanged.connect(self.mark_stale)
        layout.addWidget(self.markers)
        self._buttons(layout, [("Add marker", self.add_marker), ("Remove last marker", self.remove_marker), ("Calibrate selected marker", self.calibrate_marker), ("Load reviewed TIFF", self.choose_labels)])
        self._buttons(layout, [("Confirm reviewed channel layout", self.confirm_channel_layout), ("Load recipe", self.choose_recipe), ("Save recipe", self.choose_save_recipe), ("Reopen classification", self.choose_reopen)])
        self._buttons(layout, [("Preview calls", self.preview), ("Save classification", self.choose_save), ("Cancel", self.cancel)])
        self.status = Q.QLabel("Enter marker names and intentional raw thresholds and fractions before preview.")
        self.status.setWordWrap(True); layout.addWidget(self.status)
        self.overlay_marker = Q.QComboBox()
        self.overlay_marker.setMinimumWidth(180)
        self.overlay_marker.setSizePolicy(Q.QSizePolicy.Expanding, Q.QSizePolicy.Fixed)
        self.overlay_marker.currentIndexChanged.connect(self.show_overlay)
        layout.addWidget(self.overlay_marker)
        legend = Q.QLabel("Overlay: 1 positive (green), 2 negative (blue), 3 uncertain (yellow), 4 missing (magenta); 0 background/excluded. Inclusive queries ignore unrequested markers. Exact patterns specify every marker. NA means no defined percentage.")
        legend.setWordWrap(True); layout.addWidget(legend)
        self.tables = {}
        for key, title in [("queries", "Inclusive / recipe queries"), ("patterns", "Exact marker patterns")]:
            layout.addWidget(Q.QLabel(title)); table = Q.QTableWidget(); table.setMinimumHeight(160)
            table.setMinimumWidth(520)
            configure_readable_table(table)
            layout.addWidget(table); self.tables[key] = table
        self.timer = QtCore.QTimer(self); self.timer.setInterval(50); self.timer.timeout.connect(self.poll); self.timer.start()
        viewer.layers.events.inserted.connect(self.refresh_layers)
        viewer.layers.events.removed.connect(self.refresh_layers)
        self.refresh_layers()
        for _ in range(3): self.add_marker()
        self.destroyed.connect(lambda: self.pool.shutdown(wait=False, cancel_futures=True))

    def _buttons(self, layout, actions):
        row = Q.QHBoxLayout(); layout.addLayout(row)
        for title, fn in actions:
            button = Q.QPushButton(title); row.addWidget(button)
            button.clicked.connect(lambda checked=False, f=fn: self.guard(f))

    def guard(self, fn):
        try: return fn()
        except Exception as exc: self.status.setText(str(exc))

    def mark_stale(self, *_):
        self.revision += 1
        self._source_signature = self.source_signature()
        if hasattr(self, "status"):
            self.status.setText("Inputs changed. Preview is stale; preview again. Saving recomputes current inputs.")
        self.result = None
        for table in getattr(self, "tables", {}).values():
            table.setRowCount(0); table.setEnabled(False)
        for layer in self.viewer.layers:
            if layer.name == OVERLAY_NAME: layer.visible = False
        for record in self._calibrations.values():
            record.setdefault("review", {})["status"] = "historical"
        if self.calibration_panel is not None:
            self.calibration_panel.invalidate_source()

    def source_signature(self):
        """Cheap event backstop for mutable metadata/config; paint events cover pixels."""
        layers = [getattr(self, key, None) for key in ("image", "labels", "region")]
        values = []
        for combo in layers:
            layer = None if combo is None else combo.currentData()
            if layer is None:
                values.append(None)
            else:
                values.append((id(layer), id(layer.data), tuple(layer.data.shape),
                    str(layer.data.dtype), repr(layer.metadata), tuple(layer.scale),
                    tuple(layer.translate), repr(layer.affine.affine_matrix)))
        return repr((values, self.controller.config))

    def check_source(self):
        signature = self.source_signature()
        if self._source_signature is None:
            self._source_signature = signature
        elif signature != self._source_signature:
            self.mark_stale()
            self.refresh_channels()

    def channel_names(self):
        layer = self.image.currentData()
        if layer is None: return None
        return list(layer.metadata.get("channel_names") or
                    [f"C{i+1}" for i in range(layer.data.shape[-1])])

    def validate_channel_layout(self):
        names = self.channel_names()
        if self._expected_channel_names is not None and names != self._expected_channel_names:
            raise ValueError("Channel layout differs from this recipe. Review each marker mapping, then Confirm reviewed channel layout.")
        if names is not None:
            for row in range(self.markers.rowCount()):
                channel = self._marker_channel_combo(row).currentData()
                if channel is not None and not 0 <= channel < len(names):
                    raise ValueError(f"Marker row {row+1}: unavailable channel; explicitly remap or choose Not acquired.")

    def confirm_channel_layout(self):
        names = self.channel_names()
        if names is None: raise ValueError("Select the source image first.")
        for row in range(self.markers.rowCount()):
            channel = self._marker_channel_combo(row).currentData()
            if channel is not None and not 0 <= channel < len(names):
                raise ValueError("Fix unavailable channel mappings before confirming.")
        self._expected_channel_names = names
        self.mark_stale()
        self.status.setText("Reviewed channel layout confirmed. Preview again with these mappings.")

    def refresh_layers(self, *events):
        if events and (getattr(getattr(events[0], "value", None), "name", None) == OVERLAY_NAME or getattr(getattr(events[0], "value", None), "metadata", {}).get("cellquant_calibration")):
            return
        for combo, kind, optional in [(self.image, "image", False), (self.labels, "labels", False), (self.region, "labels", True)]:
            previous = combo.currentData(); combo.blockSignals(True); combo.clear()
            combo.addItem("Whole image (no region)" if optional else "Select a layer", None)
            for layer in self.viewer.layers:
                if layer.name == OVERLAY_NAME or layer.metadata.get("cellquant_calibration") or layer._type_string != kind: continue
                if kind == "image" and layer.data.ndim != 4: continue
                combo.addItem(layer.name, layer)
                if layer is previous: combo.setCurrentIndex(combo.count() - 1)
                if id(layer) not in self._watched:
                    self._watched.add(id(layer))
                    for event in ["data", "set_data", "paint", "metadata", "scale", "translate", "rotate", "shear", "affine"]:
                        emitter = getattr(layer.events, event, None)
                        if emitter is not None: emitter.connect(self.mark_stale)
            if not optional and combo.currentIndex() == 0 and combo.count() > 1: combo.setCurrentIndex(1)
            combo.blockSignals(False)
        self.refresh_channels(); self.mark_stale()

    def refresh_channels(self, *_):
        layer = self.image.currentData()
        names = []
        if layer is not None and layer.data.ndim == 4:
            names = layer.metadata.get("channel_names") or [f"C{i+1}" for i in range(layer.data.shape[-1])]
        for row in range(self.markers.rowCount()):
            combo = self._marker_channel_combo(row)
            previous = combo.currentData()
            combo.blockSignals(True); combo.clear(); combo.addItem("Not acquired", None)
            for i, name in enumerate(names): combo.addItem(f"{i+1}: {name}", i)
            index = combo.findData(previous)
            if index < 0 and previous is not None:
                combo.addItem(f"Unavailable channel {previous+1} (fix mapping)", previous)
                index = combo.count()-1
            combo.setCurrentIndex(max(0, index)); combo.blockSignals(False)

    def _marker_channel_combo(self, row: int):
        cell = self.markers.cellWidget(row, 1)
        if cell is None:
            raise RuntimeError(f"Marker row {row + 1} is missing a channel control")
        if hasattr(cell, "currentData"):
            return cell
        for child in cell.findChildren(Q.QComboBox):
            return child
        raise RuntimeError(f"Marker row {row + 1} is missing a channel dropdown")

    def add_marker(self):
        row = self.markers.rowCount()
        if row >= 6: raise ValueError("Use at most six markers.")
        self.markers.insertRow(row)
        for col in [0, 2, 3, 4, 5]: self.markers.setItem(row, col, Q.QTableWidgetItem("0" if col == 5 else ""))
        combo = Q.QComboBox()
        combo.currentIndexChanged.connect(self.mark_stale)
        from cellquant.plugin.combo_scroll import wrap_combo_with_scrollability
        self.markers.setCellWidget(row, 1, wrap_combo_with_scrollability(Q, combo))
        configure_readable_table(self.markers, min_widths=_MARKER_COLUMN_MIN_WIDTHS)
        self.refresh_channels(); self.mark_stale()

    def remove_marker(self):
        if self.markers.rowCount() > 1:
            row = self.markers.rowCount() - 1
            self._calibrations.pop(row, None)
            self.markers.removeRow(row)
            self.mark_stale()

    def recipe(self):
        self.check_source()
        self.validate_channel_layout()
        markers = []
        for row in range(self.markers.rowCount()):
            value = lambda col: self.markers.item(row, col).text().strip()
            if not value(0) or not value(2) or not value(4):
                raise ValueError(f"Marker row {row+1}: enter a biological name, raw low threshold and positive fraction intentionally (also for a reusable missing-marker rule).")
            markers.append(dict(name=value(0), channel=self._marker_channel_combo(row).currentData(), low=float(value(2)), high=float(value(3)) if value(3) else None, positive_fraction=float(value(4)), uncertainty_margin=float(value(5) or 0), compartment="nucleus"))
        for row, marker in enumerate(markers):
            if row in self._calibrations: marker["calibration"] = deepcopy(self._calibrations[row])
        image_layer = self.image.currentData()
        channel_names = self._expected_channel_names
        if channel_names is None and image_layer is not None:
            channel_names = list(image_layer.metadata.get("channel_names") or
                                 [f"C{i + 1}" for i in range(image_layer.data.shape[-1])])
        raw = dict(schema_version=1, name=self.fields["name"].text().strip(), calibration_group=self.fields["calibration_group"].text().strip(), region_policy=self.policy.currentData(), expected_channel_names=channel_names, markers=markers)
        if self._queries is not None: raw["queries"] = deepcopy(self._queries)
        return ClassificationRecipe(raw)

    def apply_recipe(self, recipe):
        raw = recipe.raw
        self._expected_channel_names = deepcopy(raw.get("expected_channel_names"))
        self._calibrations = {}
        self.fields["name"].setText(raw["name"]); self.fields["calibration_group"].setText(raw["calibration_group"])
        self.policy.setCurrentIndex(self.policy.findData(raw["region_policy"]))
        self.markers.setRowCount(0)
        for marker in raw["markers"]:
            self.add_marker(); row = self.markers.rowCount()-1
            for col, key in [(0,"name"),(2,"low"),(3,"high"),(4,"positive_fraction"),(5,"uncertainty_margin")]:
                self.markers.item(row,col).setText("" if marker.get(key) is None else str(marker[key]))
            combo = self._marker_channel_combo(row); index = combo.findData(marker["channel"])
            if index < 0:
                combo.addItem(f"Unavailable channel {marker['channel']+1} (fix mapping)", marker["channel"]); index=combo.count()-1
            combo.setCurrentIndex(index)
        self._queries = deepcopy(raw.get("queries")); self.mark_stale()
        self._calibrations = {row: deepcopy(marker["calibration"]) for row, marker in enumerate(raw["markers"]) if marker.get("calibration")}
        for record in self._calibrations.values():
            record.setdefault("review", {})["status"] = "historical"

    def snapshot_inputs(self):
        self.check_source()
        image_layer, labels_layer = self.image.currentData(), self.labels.currentData()
        if image_layer is None or labels_layer is None: raise ValueError("Select an image and reviewed nuclei first.")
        image = self.controller._volume_from_layer(image_layer)
        validate_transform(image_layer, image.spacing_um, image=True)
        validate_transform(labels_layer, image.spacing_um)
        # Copies are made while Qt owns the layers; workers never access mutable napari arrays.
        image = replace(image, data=np.array(image.data, copy=True), metadata=deepcopy(dict(image.metadata)))
        labels = LabelVolume(_validated_label_array(labels_layer.data).copy(), image.spacing_um, deepcopy(dict(labels_layer.metadata)))
        roi = self.region.currentData(); region = None
        if roi is not None:
            if roi is labels_layer: raise ValueError("Select a separate region layer, not the nuclei layer.")
            validate_transform(roi, image.spacing_um); region = np.array(roi.data != 0, copy=True)
        return image, labels, region, deepcopy(self.controller.config)

    def snapshot(self):
        recipe = self.recipe()
        image, labels, region, config = self.snapshot_inputs()
        context = {key: self.fields[key].text().strip() for key in ["specimen_id","eye_id","section_id","image_id","region_id"]}
        for key in ["specimen_id","image_id","region_id"]:
            if not context[key]: raise ValueError(f"Enter {key.replace('_',' ')} before scoring.")
        if not recipe.raw["calibration_group"]: raise ValueError("Name the calibration group for these threshold settings.")
        return image, labels, region, recipe, context, config

    def start(self, operation, completed):
        if self.future is not None: raise ValueError("An operation is running. Wait or cancel first.")
        self.cancel_token = MutableCancellationToken(); self._completed = completed
        self.future = self.pool.submit(operation, self.cancel_token)
        self.status.setText("Working on an immutable snapshot… You may cancel.")

    def preview(self, output_root=None):
        if self.future is not None: raise ValueError("An operation is already running.")
        image, labels, region, recipe, context, config = self.snapshot(); revision = self.revision
        def work(cancel):
            analysis = image if image.metadata.get("classification_analysis_grid") else prepare_analysis_volume(image, config, cancel)
            if output_root is None:
                result = classify_labels(analysis, labels, recipe, region=region, context=context, cancel=cancel); path=None
            else: path, result = save_classification(output_root, analysis, labels, recipe, region=region, context=context, cancel=cancel)
            return result, labels, revision, path
        self.start(work, self.accept_result)

    def poll(self):
        self.check_source()
        if self.future is None or not self.future.done(): return
        future, self.future = self.future, None
        try:
            payload = future.result(); self.cancel_token.raise_if_cancelled(); self._completed(payload)
        except PipelineCancelled: self.status.setText("Cancelled. No preview published; completed saved runs, if any, remain on disk.")
        except Exception as exc: self.status.setText(f"Could not classify: {exc}")

    def cancel(self):
        self.cancel_token.cancel(); self.status.setText("Cancellation requested…")

    def accept_result(self, payload):
        self.check_source()
        result, labels, revision, path = payload
        if revision != self.revision:
            self.status.setText("Inputs changed; late result discarded." + (f" Immutable saved run remains at {path}" if path else " Preview again."))
            return
        self.result, self.result_labels = result, labels
        for key, table in self.tables.items():
            table.setEnabled(True)
            frame = getattr(result,key); table.setRowCount(len(frame)); table.setColumnCount(len(frame.columns)); table.setHorizontalHeaderLabels(list(frame.columns))
            for r, values in enumerate(frame.itertuples(index=False, name=None)):
                for c, value in enumerate(values):
                    item = Q.QTableWidgetItem("NA" if value is None or (isinstance(value,float) and np.isnan(value)) else str(value))
                    item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable); table.setItem(r,c,item)
            configure_readable_table(table)
        self.overlay_marker.blockSignals(True); self.overlay_marker.clear()
        self.overlay_marker.addItems(list(dict.fromkeys(result.calls["marker"].tolist())))
        self.overlay_marker.blockSignals(False)
        self.result_revision = revision
        self.show_overlay()
        message = f"Saved immutable classification: {path}" if path else "Preview ready. Inspect calls before saving."
        if revision != self.revision: message += " INPUTS CHANGED: these results are stale; preview again."
        self.status.setText(message)

    def show_overlay(self, *_):
        if self.result is None or getattr(self,"result_revision", -1) != self.revision: return
        marker = self.overlay_marker.currentText()
        codes = {"positive":1,"negative":2,"uncertain":3,"missing":4}
        calls = self.result.calls
        selected = calls[calls.marker == marker]
        data = call_overlay(self.result_labels.data, selected.label.to_numpy(), selected.call.map(codes).to_numpy())
        layer = next((x for x in self.viewer.layers if x.name == OVERLAY_NAME), None)
        if layer is None:
            from napari.utils.colormaps import DirectLabelColormap
            layer = self.viewer.add_labels(data, name=OVERLAY_NAME, scale=self.result_labels.spacing_um, colormap=DirectLabelColormap(color_dict={None:"transparent",0:"transparent",1:"lime",2:"blue",3:"yellow",4:"magenta"}))
            layer.editable = False
        else: layer.data = data; layer.scale = self.result_labels.spacing_um
        layer.visible = True

    def load_labels(self, path):
        image_layer = self.image.currentData()
        if image_layer is None: raise ValueError("Open a CellQuant image before loading reviewed labels.")
        spacing = self.controller._volume_from_layer(image_layer).spacing_um
        def work(cancel):
            import tifffile
            data = tifffile.imread(path)
            if data.ndim == 2: data = data[None,...]
            cancel.raise_if_cancelled(); return _validated_label_array(data).copy()
        def completed(data):
            layer = self.viewer.add_labels(data, name="Reviewed nuclei", scale=spacing)
            self.labels.setCurrentIndex(self.labels.findData(layer)); self.status.setText("Reviewed labels loaded. Confirm they match the source analysis grid before preview.")
        self.start(work, completed)

    def reopen(self, path):
        if self.future is not None: raise ValueError("An operation is already running.")
        self.mark_stale()
        def work(cancel):
            bundle = reopen_classification(path); cancel.raise_if_cancelled(); return bundle
        def completed(bundle):
            image = replace(bundle.image, metadata={**bundle.image.metadata,"classification_analysis_grid":True})
            self.controller._publish_image(image); self.controller._publish_labels(bundle.labels)
            self.refresh_layers()
            from .controller import IMAGE_LAYER_NAME, LABEL_LAYER_NAME
            self.image.setCurrentIndex(self.image.findData(self.viewer.layers[IMAGE_LAYER_NAME])); self.labels.setCurrentIndex(self.labels.findData(self.viewer.layers[LABEL_LAYER_NAME]))
            self.region.setCurrentIndex(0)
            if bundle.region is not None:
                layer = self.viewer.add_labels(bundle.region.astype(np.uint8),name="Saved counting region",scale=image.spacing_um)
                self.region.setCurrentIndex(self.region.findData(layer))
            self.apply_recipe(bundle.recipe)
            for key,value in bundle.context.items():
                if key in self.fields: self.fields[key].setText(str(value))
            self.status.setText("Verified saved inputs reopened. Preview to rescore; saving creates a new run.")
        self.start(work, completed)

    def calibrate_marker(self):
        self.check_source()
        self.validate_channel_layout()
        row = self.markers.currentRow()
        if row < 0: raise ValueError("Select the marker row to calibrate first.")
        value = lambda col: self.markers.item(row,col).text().strip()
        marker = dict(name=value(0), channel=self._marker_channel_combo(row).currentData(),
                      low=float(value(2)) if value(2) else None,
                      high=float(value(3)) if value(3) else None,
                      positive_fraction=float(value(4)) if value(4) else None,
                      uncertainty_margin=float(value(5) or 0), compartment="nucleus")
        if not marker["name"] or marker["channel"] is None:
            raise ValueError("Enter a marker name and acquired channel before calibration.")
        def snapshot():
            image, labels, region, config = self.snapshot_inputs()
            context = {key: self.fields[key].text().strip() for key in ["specimen_id","eye_id","section_id","image_id","region_id"]}
            return image, labels, region, self.policy.currentData(), context, config
        if self.calibration_panel is not None:
            self.calibration_panel.close()
            if self.calibration_panel.dock is not None:
                self.viewer.window.remove_dock_widget(self.calibration_panel.dock)
        launched_item = self.markers.item(row, 0)
        def accepted(updated):
            self.check_source()
            if (self.calibration_panel is not panel or panel.closed
                    or row >= self.markers.rowCount()
                    or self.markers.item(row, 0) is not launched_item
                    or self.markers.item(row, 0).text().strip() != marker["name"]
                    or self._marker_channel_combo(row).currentData() != marker["channel"]):
                raise ValueError("Source or marker row changed; reopen calibration before accepting.")
            for col, key in [(2,"low"),(3,"high"),(4,"positive_fraction"),(5,"uncertainty_margin")]:
                self.markers.item(row,col).setText("" if updated.get(key) is None else str(updated[key]))
            self._calibrations[row] = deepcopy(updated["calibration"])
            self.status.setText("Reviewed calibration applied. Preview all marker calls before saving.")
        from .calibration import CalibrationPanel
        panel = CalibrationPanel(self.viewer, self.controller, marker, accepted,
                                 snapshot=snapshot, selected_labels=lambda: self.labels.currentData(),
                                 source_check=self.check_source)
        self.calibration_panel = panel
        dock = self.viewer.window.add_dock_widget(self.calibration_panel, name=f"Calibrate {marker['name']}")
        panel.bind_dock(dock)

    def choose_labels(self):
        path,_ = Q.QFileDialog.getOpenFileName(self,"Reviewed label TIFF","","TIFF (*.tif *.tiff *.TIF *.TIFF)")
        if path: self.load_labels(path)

    def choose_recipe(self):
        path,_ = Q.QFileDialog.getOpenFileName(self,"Load classification recipe","","JSON (*.json)")
        if path: self.apply_recipe(ClassificationRecipe(json.loads(Path(path).read_text(encoding="utf-8"))))

    def choose_save_recipe(self):
        recipe = self.recipe()
        path,_ = Q.QFileDialog.getSaveFileName(self,"Save classification recipe","recipe.json","JSON (*.json)")
        if path: Path(path).write_text(json.dumps(recipe.raw,indent=2),encoding="utf-8")

    def choose_save(self):
        self.snapshot()
        path = Q.QFileDialog.getExistingDirectory(self,"Choose classification output folder")
        if path: self.preview(path)

    def choose_reopen(self):
        path = Q.QFileDialog.getExistingDirectory(self,"Choose a completed classification run")
        if path: self.reopen(path)
