"""Guided, independent nuclear marker classification in the active napari dock."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import numpy as np
from qtpy import QtCore, QtWidgets as Q

from cellquant.classify import ClassificationRecipe, classify_labels, default_queries_for_markers
from cellquant.classify.compare import compare_marker_calls
from cellquant.classify.explain import explain_marker_call
from cellquant.classify.review_session import ReviewSession
from cellquant.classify.store import (
    empty_settings_result,
    make_review_record,
    reopen_classification,
    result_from_reopened,
    save_classification,
)
from cellquant.contracts import LabelVolume, MutableCancellationToken, PipelineCancelled
from cellquant.analysis import (
    analysis_context_from_config,
    analysis_context_summary,
    resolve_measurement_image,
)
from .controller import _validated_label_array
from .quant_review_dialogs import OpenAnalysisDialog, SaveReviewedDialog

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
    is_display = bool(getattr(layer, "metadata", {}).get("cellquant_display_channel"))
    if image and is_display:
        expected = tuple(spacing)
    elif image:
        expected = (*spacing, 1.0)
    else:
        expected = spacing
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
        layout = Q.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.mode_tabs = Q.QTabWidget()
        layout.addWidget(self.mode_tabs)
        self.single = SingleImageCoexpressionPanel(viewer, controller)
        self.mode_tabs.addTab(self.single, "Quick / single image")
        from .coexpression_batch import BatchCoexpressionPanel

        self.batch = BatchCoexpressionPanel(viewer, controller)
        self.mode_tabs.addTab(self.batch, "Batch")

    def __getattr__(self, name):
        # Preserve existing tests and callers that use single-image APIs on the dock.
        return getattr(self.single, name)


class SingleImageCoexpressionPanel(Q.QWidget):
    def __init__(self, viewer, controller):
        super().__init__()
        self.viewer, self.controller = viewer, controller
        self.revision = 0
        self.result = None
        self.future = None
        self._queries = None
        self._queries_custom = False
        self._queries_dirty = False
        self._expected_channel_names = None
        self._calibrations = {}
        self.calibration_panel = None
        self._source_signature = None
        self._declaration_pair = None
        self._declaration_seeded = False
        self._watched = set()
        self.review_session = None
        self._review_restoring = False
        self._remeasure_enabled = True
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cellquant-classify")
        self.cancel_token = MutableCancellationToken()
        layout = Q.QVBoxLayout(self)
        help_text = Q.QLabel("Nuclear coexpression: open an image in Single image, then select reviewed nuclei. Pixel thresholds use raw fluorescence; the positive fraction is how much of each nucleus must pass. Cytoplasmic assignment is not supported here.")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        self._buttons(layout, [("Review quantification…", self.choose_review_quantification)])
        self.review_header = Q.QLabel("Review: no analysis open.")
        self.review_header.setWordWrap(True)
        layout.addWidget(self.review_header)
        self.cell_explain = Q.QLabel("Selected cell: select a label in Reviewed nuclei.")
        self.cell_explain.setWordWrap(True)
        layout.addWidget(self.cell_explain)
        self.compare_summary = Q.QLabel("Comparison: open a saved analysis to compare Saved vs Proposed.")
        self.compare_summary.setWordWrap(True)
        layout.addWidget(self.compare_summary)
        self._buttons(layout, [
            ("Update preview", self.preview),
            ("Revert to saved", self.revert_to_saved),
            ("Save reviewed version…", self.choose_save_reviewed),
            ("Locate source…", self.choose_locate_source),
        ])
        form = Q.QFormLayout()
        _configure_readable_form(form)
        layout.addLayout(form)
        self.image = Q.QComboBox(); self.labels = Q.QComboBox(); self.region = Q.QComboBox()
        for title, widget in [("Source image (ZYXC)", self.image), ("Reviewed nuclei (ZYX)", self.labels), ("Counting region", self.region)]:
            widget.setMinimumWidth(180)
            widget.setSizePolicy(Q.QSizePolicy.Expanding, Q.QSizePolicy.Fixed)
            form.addRow(title, widget)
            widget.currentIndexChanged.connect(self._source_layers_changed)
        self.image.currentIndexChanged.connect(self.refresh_channels)
        self.image.currentIndexChanged.connect(self.refresh_analysis_context_ui)
        self.labels.currentIndexChanged.connect(self.refresh_analysis_context_ui)
        self.analysis_context_label = Q.QLabel("Measurement grid: select reviewed nuclei.")
        self.analysis_context_label.setWordWrap(True)
        form.addRow("Bound analysis grid", self.analysis_context_label)
        self.declare_mode = Q.QComboBox()
        self.declare_mode.setMinimumWidth(180)
        for label, value in [
            ("Single plane", "single_plane_2d"),
            ("Maximum projection", "max_projection_2d"),
            ("Full volume 3D", "volume_3d"),
            ("Stitched 2D", "stitch_2d"),
        ]:
            self.declare_mode.addItem(label, value)
        self.declare_z = Q.QSpinBox()
        self.declare_z.setMinimum(0)
        self.declare_z.setMaximum(0)
        declare_row = Q.QHBoxLayout()
        declare_row.addWidget(self.declare_mode, stretch=1)
        declare_row.addWidget(Q.QLabel("Z index"))
        declare_row.addWidget(self.declare_z)
        form.addRow("Declare grid (imported masks)", declare_row)
        self.declare_mode.currentIndexChanged.connect(self._declared_mode_changed)
        self.declare_mode.currentIndexChanged.connect(self._user_edited_declaration)
        self.declare_mode.currentIndexChanged.connect(self.mark_preview_stale)
        self.declare_z.valueChanged.connect(self._user_edited_declaration)
        self.declare_z.valueChanged.connect(self.mark_preview_stale)
        self.fields = {}
        for key, title in [("name", "Recipe name"), ("calibration_group", "Calibration group"), ("specimen_id", "Specimen"), ("eye_id", "Eye (optional)"), ("section_id", "Section (optional)"), ("image_id", "Image ID"), ("region_id", "Region name")]:
            field = Q.QLineEdit()
            field.setMinimumWidth(180)
            field.setSizePolicy(Q.QSizePolicy.Expanding, Q.QSizePolicy.Fixed)
            self.fields[key] = field
            form.addRow(title, field); field.textChanged.connect(self.mark_preview_stale)
        self.fields["name"].setText("Nuclear coexpression")
        self.policy = Q.QComboBox()
        self.policy.setMinimumWidth(180)
        self.policy.setSizePolicy(Q.QSizePolicy.Expanding, Q.QSizePolicy.Fixed)
        self.policy.addItem("Whole object: every voxel inside region", "whole_object")
        self.policy.addItem("Centroid: nearest centroid voxel inside region", "centroid")
        form.addRow("Region membership", self.policy)
        self.policy.currentIndexChanged.connect(self.mark_preview_stale)
        note = Q.QLabel("No region layer means the entire image. Nonzero region labels define inclusion; measurements always use the complete nucleus. Name the region explicitly.")
        note.setWordWrap(True); layout.addWidget(note)
        self.query_summary = Q.QLabel("Queries: automatic inclusive combinations.")
        self.query_summary.setWordWrap(True)
        layout.addWidget(self.query_summary)
        self.markers = Q.QTableWidget(0, 6)
        self.markers.setHorizontalHeaderLabels(["Marker", "Channel", "Raw low", "Raw high (optional)", "Positive fraction", "Uncertainty margin"])
        self.markers.setMinimumHeight(140)
        self.markers.setMinimumWidth(420)
        configure_readable_table(self.markers, min_widths=_MARKER_COLUMN_MIN_WIDTHS)
        self.markers.itemChanged.connect(self._marker_item_changed)
        layout.addWidget(self.markers)
        self.evidence_label = Q.QLabel("Calibration evidence: none stored.")
        self.evidence_label.setWordWrap(True)
        layout.addWidget(self.evidence_label)
        self._buttons(layout, [("Add marker", self.add_marker), ("Remove last marker", self.remove_marker), ("Calibrate selected marker", self.calibrate_marker), ("Load reviewed TIFF", self.choose_labels)])
        self._buttons(layout, [("Confirm reviewed channel layout", self.confirm_channel_layout), ("Load recipe", self.choose_recipe), ("Save recipe", self.choose_save_recipe), ("Reset queries to defaults", self.reset_queries)])
        self._buttons(layout, [("Reopen classification", self.choose_reopen), ("Preview calls", self.preview), ("Save classification", self.choose_save), ("Cancel", self.cancel)])
        self.status = Q.QLabel("Enter marker names and intentional raw thresholds and fractions before preview.")
        self.status.setWordWrap(True); layout.addWidget(self.status)
        self.overlay_marker = Q.QComboBox()
        self.overlay_marker.setMinimumWidth(180)
        self.overlay_marker.setSizePolicy(Q.QSizePolicy.Expanding, Q.QSizePolicy.Fixed)
        self.overlay_marker.currentIndexChanged.connect(self.show_overlay)
        self.overlay_marker.currentIndexChanged.connect(self._sync_review_panels)
        layout.addWidget(self.overlay_marker)
        legend = Q.QLabel("Overlay: 1 positive (green), 2 negative (blue), 3 uncertain (yellow), 4 missing (magenta); 0 background/excluded. Inclusive queries ignore unrequested markers. Exact patterns specify every marker. NA means no defined percentage.")
        legend.setWordWrap(True); layout.addWidget(legend)
        self.tables = {}
        for key, title in [("queries", "Inclusive / recipe queries"), ("patterns", "Exact marker patterns")]:
            layout.addWidget(Q.QLabel(title)); table = Q.QTableWidget(); table.setMinimumHeight(140)
            table.setMinimumWidth(360)
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

    def mark_preview_stale(self, *_):
        if getattr(self, "_review_restoring", False):
            return
        self.revision += 1
        self._source_signature = self.source_signature()
        if self.review_session is not None:
            self.review_session.mark_stale()
            try:
                self.review_session.set_draft_recipe(self.recipe(), bump=False)
            except Exception:
                pass
        if hasattr(self, "status"):
            if self.review_session is not None:
                self.status.setText(f"{self.review_session.state.value}. Update preview to refresh proposed calls.")
            else:
                self.status.setText("Inputs changed. Preview is stale; preview again. Saving recomputes current inputs.")
        self.result = None
        for table in getattr(self, "tables", {}).values():
            table.setRowCount(0); table.setEnabled(False)
        for layer in self.viewer.layers:
            if layer.name == OVERLAY_NAME: layer.visible = False
        self._sync_review_panels()

    def mark_stale(self, *_):
        """Keep preview-only invalidation. Source/region changes use ``_source_layers_changed``."""
        self.mark_preview_stale()

    def invalidate_calibration_evidence(self, reason: str, *, rows=None):
        targets = (
            list(self._calibrations)
            if rows is None
            else [row for row in rows if row in self._calibrations]
        )
        for row in targets:
            record = self._calibrations[row]
            review = record.setdefault("review", {})
            review["status"] = "historical"
            review["historical_reason"] = reason
        if rows is None and self.calibration_panel is not None:
            self.calibration_panel.invalidate_source()
        self._sync_evidence_label()

    def _source_layers_changed(self, *_):
        self.mark_preview_stale()
        self.invalidate_calibration_evidence("Source image, nuclei, or counting region changed")
        if hasattr(self, "declare_z"):
            self.refresh_analysis_context_ui()

    def _marker_item_changed(self, item):
        row = item.row() if item is not None else None
        self.mark_preview_stale()
        if row is not None:
            self.invalidate_calibration_evidence(
                f"Marker row {row + 1} settings changed",
                rows=(row,),
            )
        self._sync_evidence_label()

    def _marker_channel_changed(self, row):
        self.mark_preview_stale()
        self.invalidate_calibration_evidence("Channel mapping changed", rows=(row,))
        if self.calibration_panel is not None and getattr(self, "_calibrating_row", None) == row:
            self.calibration_panel.invalidate_source()

    def _sync_evidence_label(self):
        if not hasattr(self, "evidence_label"):
            return
        if not self._calibrations:
            self.evidence_label.setText("Calibration evidence: none stored.")
            return
        parts = []
        for row in sorted(self._calibrations):
            record = self._calibrations[row]
            review = record.get("review") or {}
            name = ""
            if row < self.markers.rowCount() and self.markers.item(row, 0) is not None:
                name = self.markers.item(row, 0).text().strip() or f"row {row + 1}"
            else:
                name = f"row {row + 1}"
            status = review.get("status") or "unknown"
            reason = review.get("historical_reason")
            if status == "historical" and reason:
                parts.append(f"{name}: historical ({reason})")
            else:
                parts.append(f"{name}: {status}")
        self.evidence_label.setText("Calibration evidence: " + "; ".join(parts))

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
            self._source_layers_changed()
            self.refresh_channels()
            self.refresh_analysis_context_ui()

    def channel_names(self):
        layer = self.image.currentData()
        if layer is None:
            volume = getattr(self.controller, "image_volume", None)
            if volume is None:
                return None
            return list(volume.channel_names)
        names = layer.metadata.get("channel_names")
        if names:
            return list(names)
        data = getattr(layer, "data", None)
        if data is not None and getattr(data, "ndim", 0) == 4:
            return [f"C{i+1}" for i in range(data.shape[-1])]
        return None

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
        self.mark_preview_stale()
        self.status.setText("Reviewed channel layout confirmed. Preview again with these mappings.")

    def refresh_layers(self, *events):
        if events and (getattr(getattr(events[0], "value", None), "name", None) == OVERLAY_NAME or getattr(getattr(events[0], "value", None), "metadata", {}).get("cellquant_calibration")):
            return
        previous_image = self.image.currentData()
        previous_labels = self.labels.currentData()
        previous_region = self.region.currentData()
        previous_ids = (
            id(previous_image) if previous_image is not None else None,
            id(getattr(previous_image, "data", None)) if previous_image is not None else None,
            id(previous_labels) if previous_labels is not None else None,
            id(getattr(previous_labels, "data", None)) if previous_labels is not None else None,
            id(previous_region) if previous_region is not None else None,
        )
        for combo, kind, optional in [(self.image, "image", False), (self.labels, "labels", False), (self.region, "labels", True)]:
            previous = combo.currentData(); combo.blockSignals(True); combo.clear()
            combo.addItem("Whole image (no region)" if optional else "Select a layer", None)
            for layer in self.viewer.layers:
                if layer.name == OVERLAY_NAME or layer.metadata.get("cellquant_calibration") or layer._type_string != kind: continue
                if kind == "image":
                    ndim = getattr(getattr(layer, "data", None), "ndim", 0)
                    is_display = bool(layer.metadata.get("cellquant_display_channel"))
                    if ndim == 4 or (ndim == 3 and is_display):
                        pass
                    else:
                        continue
                combo.addItem(layer.name, layer)
                if layer is previous: combo.setCurrentIndex(combo.count() - 1)
                if id(layer) not in self._watched:
                    self._watched.add(id(layer))
                    for event in ["data", "set_data", "paint", "metadata", "scale", "translate", "rotate", "shear", "affine"]:
                        emitter = getattr(layer.events, event, None)
                        if emitter is not None: emitter.connect(self._source_layers_changed)
            if not optional and combo.currentIndex() == 0 and combo.count() > 1: combo.setCurrentIndex(1)
            combo.blockSignals(False)
        self.refresh_channels(); self.refresh_analysis_context_ui()
        new_ids = (
            id(self.image.currentData()) if self.image.currentData() is not None else None,
            id(getattr(self.image.currentData(), "data", None)) if self.image.currentData() is not None else None,
            id(self.labels.currentData()) if self.labels.currentData() is not None else None,
            id(getattr(self.labels.currentData(), "data", None)) if self.labels.currentData() is not None else None,
            id(self.region.currentData()) if self.region.currentData() is not None else None,
        )
        if new_ids != previous_ids:
            self._source_layers_changed()

    def _declared_mode_changed(self, *_):
        self.declare_z.setEnabled(self.declare_mode.currentData() == "single_plane_2d")

    def _user_edited_declaration(self, *_):
        self._declaration_seeded = True

    def refresh_analysis_context_ui(self, *_):
        labels_layer = self.labels.currentData()
        image_layer = self.image.currentData()
        if image_layer is not None and getattr(image_layer.data, "ndim", 0) == 4:
            self.declare_z.setMaximum(max(0, int(image_layer.data.shape[0]) - 1))
        if labels_layer is None:
            self.analysis_context_label.setText("Measurement grid: select reviewed nuclei.")
            self.declare_mode.setEnabled(True)
            self._declared_mode_changed()
            return
        provenance = dict(getattr(labels_layer, "metadata", {}) or {}).get("analysis_volume") or {}
        if provenance:
            try:
                from cellquant.analysis import analysis_context_from_dict

                spacing = tuple(
                    (getattr(labels_layer, "metadata", {}) or {}).get(
                        "spacing_um", tuple(labels_layer.scale[:3])
                    )
                )
                context = analysis_context_from_dict(
                    provenance,
                    spacing_um=spacing,
                    shape_zyx=tuple(int(v) for v in labels_layer.data.shape),
                )
                self.analysis_context_label.setText(f"From masks: {analysis_context_summary(context)}")
                self.declare_mode.setEnabled(False)
                self.declare_z.setEnabled(False)
                return
            except Exception as exc:  # noqa: BLE001
                self.analysis_context_label.setText(f"Mask provenance unusable ({exc}); declare grid.")
        else:
            self.analysis_context_label.setText(
                "Imported masks lack analysis provenance — declare plane/projection before scoring."
            )
        self.declare_mode.setEnabled(True)
        self._declared_mode_changed()
        pair = (
            id(image_layer) if image_layer is not None else None,
            id(getattr(image_layer, "data", None)) if image_layer is not None else None,
            tuple(int(v) for v in getattr(getattr(image_layer, "data", None), "shape", ()) or ()),
            id(labels_layer) if labels_layer is not None else None,
            id(getattr(labels_layer, "data", None)) if labels_layer is not None else None,
            tuple(int(v) for v in getattr(labels_layer.data, "shape", ())),
            str(dict(getattr(labels_layer, "metadata", {}) or {}).get("analysis_volume") or {}),
        )
        # Seed from controller config only when the image/mask pair changes.
        if pair != self._declaration_pair:
            self._declaration_pair = pair
            self._declaration_seeded = False
        if self._declaration_seeded:
            self.analysis_context_label.setText(
                self.analysis_context_label.text() + " (keeping your declared grid)"
            )
            return
        config = getattr(self.controller, "config", None)
        if config is not None:
            try:
                mode = config.raw["segment"]["mode"]
                index = self.declare_mode.findData(mode)
                if index >= 0:
                    self.declare_mode.blockSignals(True)
                    self.declare_mode.setCurrentIndex(index)
                    self.declare_mode.blockSignals(False)
                if mode == "single_plane_2d":
                    self.declare_z.setValue(int(config.raw["segment"].get("z_index") or 0))
                self._declaration_seeded = True
                self.analysis_context_label.setText(
                    self.analysis_context_label.text() + " (seeded once from segmentation settings)"
                )
            except Exception:
                pass

    def declared_context(self):
        self._declaration_seeded = True
        image_layer = self.image.currentData()
        if image_layer is None:
            raise ValueError("Select the source image before declaring a measurement grid.")
        image = self.controller.resolve_image_volume(image_layer)
        mode = self.declare_mode.currentData()
        config = {
            "segment": {"mode": mode, **({"z_index": int(self.declare_z.value())} if mode == "single_plane_2d" else {})}
        }
        return analysis_context_from_config(
            config,
            original_z_depth=int(image.data.shape[0]),
            spacing_um=tuple(image.spacing_um),
            source=image.source,
            shape_yx=(int(image.data.shape[1]), int(image.data.shape[2])),
        )

    def refresh_channels(self, *_):
        layer = self.image.currentData()
        names = self.channel_names() or []
        if layer is not None and not names and getattr(getattr(layer, "data", None), "ndim", 0) == 4:
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
        combo.currentIndexChanged.connect(lambda *_ , r=row: self._marker_channel_changed(r))
        from cellquant.plugin.combo_scroll import wrap_combo_with_scrollability
        self.markers.setCellWidget(row, 1, wrap_combo_with_scrollability(Q, combo))
        configure_readable_table(self.markers, min_widths=_MARKER_COLUMN_MIN_WIDTHS)
        self.refresh_channels()
        if not self._queries_custom:
            self._queries = None
        self._sync_query_summary()
        self.mark_preview_stale()

    def remove_marker(self):
        if self.markers.rowCount() <= 1:
            return
        row = self.markers.rowCount() - 1
        item = self.markers.item(row, 0)
        removed_name = item.text().strip() if item is not None else ""
        dependents = []
        if self._queries_custom and self._queries and removed_name:
            for query in self._queries:
                clauses = (
                    list(query.get("positive") or [])
                    + list(query.get("negative") or [])
                    + list(query.get("denominator_positive") or [])
                )
                if removed_name in clauses:
                    dependents.append(query)
        if dependents:
            names = ", ".join(q.get("name", "?") for q in dependents)
            answer = Q.QMessageBox.question(
                self,
                "Custom queries use this marker",
                (
                    f"Removing {removed_name!r} would change custom queries:\n{names}\n\n"
                    "Yes — delete those queries (or reset to automatic if none remain).\n"
                    "No — cancel and keep the marker."
                ),
                Q.QMessageBox.Yes | Q.QMessageBox.No,
                Q.QMessageBox.No,
            )
            if answer != Q.QMessageBox.Yes:
                return
            keep = [q for q in self._queries if q not in dependents]
            self._queries = keep or None
            self._queries_custom = bool(keep)
            self._queries_dirty = True
        self._calibrations.pop(row, None)
        self.markers.removeRow(row)
        if not self._queries_custom:
            self._queries = None
        self._sync_query_summary()
        self.mark_preview_stale()

    def reset_queries(self):
        self._queries = None
        self._queries_custom = False
        self._queries_dirty = False
        self._sync_query_summary()
        self.mark_preview_stale()
        self.status.setText("Queries reset to automatic inclusive combinations.")

    def _marker_names(self):
        names = []
        for row in range(self.markers.rowCount()):
            item = self.markers.item(row, 0)
            name = item.text().strip() if item is not None else ""
            if name:
                names.append(name)
        return names

    def _format_query_clauses(self, query):
        bits = []
        for key, symbol in (
            ("positive", "+"),
            ("negative", "−"),
            ("denominator_positive", "denom+"),
        ):
            names = list(query.get(key) or [])
            if names:
                bits.append(f"{symbol}{','.join(names)}" if symbol != "denom+" else f"denom+({','.join(names)})")
        return " ".join(bits) or "(empty)"

    def _sync_query_summary(self):
        if not hasattr(self, "query_summary"):
            return
        if not self._queries_custom or self._queries is None:
            self.query_summary.setText("Queries: automatic inclusive combinations (update when markers change).")
            return
        parts = [
            f"{q.get('name', '?')} [{self._format_query_clauses(q)}]" for q in self._queries
        ]
        preview = "; ".join(parts[:3]) + ("…" if len(parts) > 3 else "")
        dirty = " — custom query changed; preview again" if self._queries_dirty else ""
        self.query_summary.setText(f"Queries: custom ({len(parts)}) — {preview}{dirty}")

    def _repair_custom_queries(self):
        """Drop empty queries only; never silently rewrite clause meaning/names."""

        if not self._queries:
            return
        names = set(self._marker_names())
        kept = []
        changed = False
        for query in self._queries:
            clauses = {
                key: list(query.get(key) or [])
                for key in ("positive", "negative", "denominator_positive")
            }
            if any(name not in names for values in clauses.values() for name in values):
                changed = True
                continue
            if not any(clauses.values()):
                changed = True
                continue
            kept.append(query)
        if changed:
            self._queries_dirty = True
        self._queries = kept or None
        if not kept:
            self._queries_custom = False

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
        if self._queries_custom and self._queries is not None:
            raw["queries"] = deepcopy(self._queries)
        return ClassificationRecipe(raw)

    def apply_recipe(self, recipe):
        raw = recipe.raw
        self._expected_channel_names = deepcopy(raw.get("expected_channel_names"))
        self._calibrations = {}
        self._queries_custom = False
        self._queries = None
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
        names = [m["name"] for m in raw["markers"]]
        loaded = deepcopy(raw.get("queries"))
        defaults = default_queries_for_markers(names)
        self._queries_custom = loaded is not None and loaded != defaults
        self._queries = loaded if self._queries_custom else None
        self._sync_query_summary()
        self.mark_preview_stale()
        self._calibrations = {row: deepcopy(marker["calibration"]) for row, marker in enumerate(raw["markers"]) if marker.get("calibration")}
        for record in self._calibrations.values():
            review = record.setdefault("review", {})
            review["status"] = "historical"
            review.setdefault("historical_reason", "Loaded from recipe; review on this image before treating as current")
        self._sync_evidence_label()

    def snapshot_inputs(self):
        self.check_source()
        image_layer, labels_layer = self.image.currentData(), self.labels.currentData()
        if image_layer is None or labels_layer is None: raise ValueError("Select an image and reviewed nuclei first.")
        image = self.controller.resolve_image_volume(image_layer)
        validate_transform(image_layer, image.spacing_um, image=True)
        validate_transform(labels_layer, image.spacing_um)
        # Copies are made while Qt owns the layers; workers never access mutable napari arrays.
        image = replace(image, data=np.array(image.data, copy=True), metadata=deepcopy(dict(image.metadata)))
        labels = LabelVolume(_validated_label_array(labels_layer.data).copy(), image.spacing_um, deepcopy(dict(labels_layer.metadata)))
        roi = self.region.currentData(); region = None
        if roi is not None:
            if roi is labels_layer: raise ValueError("Select a separate region layer, not the nuclei layer.")
            validate_transform(roi, image.spacing_um); region = np.array(roi.data != 0, copy=True)
        declared = None
        if not dict(labels.provenance.get("analysis_volume") or {}):
            declared = self.declared_context()
        return image, labels, region, declared

    def validate_ready(self):
        """Cheap validation without copying image/label volumes."""
        recipe = self.recipe()
        if self.image.currentData() is None or self.labels.currentData() is None:
            raise ValueError("Select an image and reviewed nuclei first.")
        context = {key: self.fields[key].text().strip() for key in ["specimen_id","eye_id","section_id","image_id","region_id"]}
        for key in ["specimen_id","image_id","region_id"]:
            if not context[key]: raise ValueError(f"Enter {key.replace('_',' ')} before scoring.")
        if not recipe.raw["calibration_group"]: raise ValueError("Name the calibration group for these threshold settings.")
        if not dict((self.labels.currentData().metadata or {}).get("analysis_volume") or {}):
            self.declared_context()  # raises if declaration incomplete
        return recipe, context

    def snapshot(self):
        recipe, context = self.validate_ready()
        image, labels, region, declared = self.snapshot_inputs()
        return image, labels, region, recipe, context, declared

    def start(self, operation, completed):
        if self.future is not None: raise ValueError("An operation is running. Wait or cancel first.")
        self.cancel_token = MutableCancellationToken(); self._completed = completed
        self.future = self.pool.submit(operation, self.cancel_token)
        self.status.setText("Working on an immutable snapshot… You may cancel.")

    def preview(self, output_root=None):
        if not self._remeasure_enabled:
            raise ValueError("Pixel remeasurement is disabled until the source image is located and accepted.")
        if self.future is not None: raise ValueError("An operation is already running.")
        image, labels, region, recipe, context, declared = self.snapshot(); revision = self.revision
        if self.review_session is not None:
            self.review_session.revision = revision
            self.review_session.begin_compute()
            self._sync_review_panels()
        def work(cancel):
            analysis, bound = resolve_measurement_image(image, labels, declared=declared, cancel=cancel)
            if output_root is None:
                result = classify_labels(analysis, labels, recipe, region=region, context=context, cancel=cancel); path=None
            else: path, result = save_classification(output_root, analysis, labels, recipe, region=region, context=context, cancel=cancel)
            return result, labels, revision, path, analysis_context_summary(bound)
        self.start(work, self.accept_result)

    def poll(self):
        self.check_source()
        self._sync_selected_cell()
        if self.future is None or not self.future.done(): return
        future, self.future = self.future, None
        try:
            payload = future.result(); self.cancel_token.raise_if_cancelled(); self._completed(payload)
        except PipelineCancelled:
            if self.review_session is not None:
                self.review_session.discard_late_preview()
            self.status.setText("Cancelled. No preview published; completed saved runs, if any, remain on disk.")
            self._sync_review_panels()
        except Exception as exc:
            if self.review_session is not None:
                self.review_session.discard_late_preview()
            self.status.setText(f"Could not classify: {exc}")
            self._sync_review_panels()

    def cancel(self):
        self.cancel_token.cancel(); self.status.setText("Cancellation requested…")

    def accept_result(self, payload):
        self.check_source()
        if len(payload) == 5:
            result, labels, revision, path, grid_summary = payload
        else:
            result, labels, revision, path = payload
            grid_summary = None
        if revision != self.revision:
            if self.review_session is not None:
                self.review_session.discard_late_preview()
            self.status.setText("Inputs changed; late result discarded." + (f" Immutable saved run remains at {path}" if path else " Preview again."))
            self._sync_review_panels()
            return
        if self.review_session is not None and not self.review_session.accept_preview(revision, result):
            self.status.setText("Inputs changed; late result discarded.")
            self._sync_review_panels()
            return
        self._queries_dirty = False
        self._sync_query_summary()
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
        if self.review_session is not None and path is None:
            message = f"{self.review_session.state.value}. {message}"
        if grid_summary:
            message = f"{message} Measurement grid: {grid_summary}."
            self.analysis_context_label.setText(f"Scored with: {grid_summary}")
        if revision != self.revision: message += " INPUTS CHANGED: these results are stale; preview again."
        self.status.setText(message)
        self._sync_review_panels()

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
        spacing = self.controller.resolve_image_volume(image_layer).spacing_um
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
        if not self._confirm_discard_review_edits():
            return
        self.mark_stale()
        def work(cancel):
            bundle = reopen_classification(path); cancel.raise_if_cancelled(); return bundle
        def completed(bundle):
            self._apply_reopened_bundle(bundle, restore_calls=False)
            self.status.setText("Verified saved inputs reopened. Preview to rescore; saving creates a new run.")
        self.start(work, completed)

    def choose_review_quantification(self):
        if not self._confirm_discard_review_edits():
            return
        dialog = OpenAnalysisDialog(self)
        if dialog.exec_() != Q.QDialog.Accepted or dialog.selected is None:
            return
        candidate = dialog.selected
        if candidate.kind == "segmentation":
            self.status.setText(
                f"Loaded segmentation candidate {candidate.path.name}. "
                "Use Load recipe / choose recipe before preview. Full segmentation open is advanced."
            )
            Q.QMessageBox.information(
                self, "Segmentation-only analysis",
                "This folder is a segmentation run. Load or choose a recipe after opening its labels, "
                "or select a completed classify_* analysis for automatic restoration.",
            )
            return
        self.open_review_analysis(candidate.path)

    def open_review_analysis(self, path):
        if self.future is not None: raise ValueError("An operation is already running.")
        def work(cancel):
            bundle = reopen_classification(path); cancel.raise_if_cancelled(); return bundle
        def completed(bundle):
            self._apply_reopened_bundle(bundle, restore_calls=True)
            self.status.setText("Saved settings restored. Marker calls loaded without resegmentation.")
            self._sync_review_panels()
        self.start(work, completed)

    def _apply_reopened_bundle(self, bundle, *, restore_calls: bool):
        image = replace(bundle.image, metadata={**bundle.image.metadata, "classification_analysis_grid": True})
        self.controller._publish_image(image)
        self.controller._publish_labels(bundle.labels)
        self.refresh_layers()
        from .controller import LABEL_LAYER_NAME
        display = next(
            (
                layer
                for layer in self.viewer.layers
                if getattr(layer, "metadata", {}).get("cellquant_display_channel")
            ),
            None,
        )
        if display is not None:
            self.image.setCurrentIndex(self.image.findData(display))
        self.labels.setCurrentIndex(self.labels.findData(self.viewer.layers[LABEL_LAYER_NAME]))
        self.region.setCurrentIndex(0)
        if bundle.region is not None:
            layer = self.viewer.add_labels(
                bundle.region.astype(np.uint8), name="Saved counting region", scale=image.spacing_um
            )
            self.region.setCurrentIndex(self.region.findData(layer))
        self._review_restoring = True
        try:
            self.apply_recipe(bundle.recipe)
            for key, value in bundle.context.items():
                if key in self.fields:
                    self.fields[key].setText(str(value))
            volume = (bundle.image.metadata or {}).get("analysis_volume") or {}
            mode = volume.get("mode") if isinstance(volume, dict) else "unavailable"
            if restore_calls:
                self.review_session = ReviewSession.from_reopened(
                    bundle, sample_name=str(bundle.context.get("specimen_id") or bundle.path.name),
                    segmentation_mode=str(mode or "unavailable"),
                )
                self.revision = self.review_session.revision
                self._publish_saved_result(result_from_reopened(bundle), bundle.labels)
                self._remeasure_enabled = True
                source = Path(bundle.image.source)
                if str(bundle.image.source) and not source.exists():
                    self._remeasure_enabled = False
                    self.status.setText(
                        "Saved calls restored, but the original source path is unavailable. "
                        "Summary review works; Locate source… before remeasurement."
                    )
            else:
                self.review_session = None
        finally:
            self._review_restoring = False
            self._source_signature = self.source_signature()
            self._sync_review_panels()

    def _publish_saved_result(self, result, labels):
        self.result = result
        self.result_labels = labels
        self.result_revision = self.revision
        for key, table in self.tables.items():
            table.setEnabled(True)
            frame = getattr(result, key)
            table.setRowCount(len(frame))
            table.setColumnCount(len(frame.columns))
            table.setHorizontalHeaderLabels(list(frame.columns))
            for r, values in enumerate(frame.itertuples(index=False, name=None)):
                for c, value in enumerate(values):
                    item = Q.QTableWidgetItem(
                        "NA" if value is None or (isinstance(value, float) and np.isnan(value)) else str(value)
                    )
                    item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                    table.setItem(r, c, item)
            configure_readable_table(table)
        self.overlay_marker.blockSignals(True)
        self.overlay_marker.clear()
        if len(result.calls):
            self.overlay_marker.addItems(list(dict.fromkeys(result.calls["marker"].tolist())))
        self.overlay_marker.blockSignals(False)
        self.show_overlay()

    def revert_to_saved(self):
        if self.review_session is None:
            raise ValueError("Open a review analysis before reverting.")
        self._review_restoring = True
        try:
            self.review_session.revert_to_saved()
            self.apply_recipe(self.review_session.baseline_recipe)
            self.revision = self.review_session.revision
            if self.review_session.baseline_calls_valid:
                labels = self.review_session.baseline_labels
                if labels is None:
                    labels = self.labels.currentData()
                self._publish_saved_result(self.review_session.baseline_result, labels)
            else:
                self.result = None
            self.status.setText("Reverted to saved settings.")
        finally:
            self._review_restoring = False
            self._source_signature = self.source_signature()
            self._sync_review_panels()

    def choose_save_reviewed(self):
        if self.review_session is None:
            raise ValueError("Open a review analysis before saving a reviewed version.")
        try:
            draft = self.recipe()
            self.review_session.set_draft_recipe(draft, bump=False)
        except Exception as exc:
            raise ValueError(f"Draft settings are incomplete: {exc}") from exc
        preview_ready = (
            self.result is not None
            and getattr(self, "result_revision", -1) == self.revision
            and self.review_session.proposed_revision == self.review_session.revision
        )
        baseline = self.review_session.baseline_recipe.raw
        draft_raw = draft.raw
        changes = []
        if baseline.get("markers") != draft_raw.get("markers"):
            changes.append("marker thresholds/fractions changed")
        if baseline.get("region_policy") != draft_raw.get("region_policy"):
            changes.append("region policy changed")
        if baseline.get("name") != draft_raw.get("name"):
            changes.append("recipe name changed")
        summary = "Proposed settings: " + (", ".join(changes) if changes else "no marker/rule changes detected")
        dialog = SaveReviewedDialog(
            self,
            parent_run_id=self.review_session.parent_run_id,
            settings_summary=summary,
            preview_ready=preview_ready,
            population_label=self.review_session.population_label,
        )
        if dialog.exec_() != Q.QDialog.Accepted or not dialog.output_root:
            return
        review = make_review_record(
            parent_run_id=self.review_session.parent_run_id,
            parent_manifest_sha256=self.review_session.parent_manifest_sha256,
            computation_status=dialog.computation_status,
            preview_scope=self.review_session.population_label,
            note=dialog.note,
            reviewer=dialog.reviewer,
        )
        if dialog.computation_status == "settings_only":
            self._save_settings_version(dialog.output_root, draft, review)
        else:
            self._save_full_reviewed(dialog.output_root, draft, review)

    def _save_settings_version(self, output_root, recipe, review):
        if self.future is not None:
            raise ValueError("An operation is already running.")
        image, labels, region, declared = self.snapshot_inputs()
        context = {key: self.fields[key].text().strip() for key in ["specimen_id", "eye_id", "section_id", "image_id", "region_id"]}
        result = empty_settings_result(recipe, context=context, image=image)
        def work(cancel):
            analysis, bound = resolve_measurement_image(image, labels, declared=declared, cancel=cancel)
            path, saved = save_classification(
                output_root, analysis, labels, recipe, region=region, context=context,
                cancel=cancel, review=review, result=result,
            )
            return saved, labels, self.revision, path, analysis_context_summary(bound)
        self.start(work, self.accept_result)

    def _save_full_reviewed(self, output_root, recipe, review):
        if not self._remeasure_enabled:
            raise ValueError("Locate and accept the source before computing full results.")
        if self.future is not None:
            raise ValueError("An operation is already running.")
        image, labels, region, declared = self.snapshot_inputs()
        context = {key: self.fields[key].text().strip() for key in ["specimen_id", "eye_id", "section_id", "image_id", "region_id"]}
        revision = self.revision
        def work(cancel):
            analysis, bound = resolve_measurement_image(image, labels, declared=declared, cancel=cancel)
            path, saved = save_classification(
                output_root, analysis, labels, recipe, region=region, context=context,
                cancel=cancel, review=review,
            )
            return saved, labels, revision, path, analysis_context_summary(bound)
        self.start(work, self.accept_result)

    def choose_locate_source(self):
        if self.review_session is None:
            raise ValueError("Open a review analysis before locating a source.")
        path, _ = Q.QFileDialog.getOpenFileName(self, "Locate source image", "", "Images (*.tif *.tiff *.nd2 *.npy);;All (*.*)")
        if not path:
            return
        image_layer = self.image.currentData()
        if image_layer is None:
            raise ValueError("Restored analysis image is missing.")
        expected = self.controller.resolve_image_volume(image_layer)
        import numpy as np
        from pathlib import Path as P
        candidate_path = P(path)
        # Identity check without silently accepting filename-only matches.
        try:
            if candidate_path.suffix.lower() == ".npy":
                data = np.load(candidate_path, allow_pickle=False)
            else:
                import tifffile
                data = tifffile.imread(path)
                if data.ndim == 3 and expected.data.ndim == 4 and data.shape[-1] == expected.data.shape[-1]:
                    data = data[None, ...]
            verified = (
                tuple(data.shape) == tuple(expected.data.shape)
                and str(data.dtype) == str(expected.data.dtype)
            )
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"Could not read replacement source: {exc}") from exc
        if not verified:
            answer = Q.QMessageBox.question(
                self, "Unverified source replacement",
                "Shape/dtype do not match the saved analysis. Accept as an unverified replacement? "
                "Saved baseline calls will be invalidated.",
            )
            if answer != Q.QMessageBox.Yes:
                return
            self.review_session.invalidate_baseline_calls("Unverified source replacement")
            self.review_session.source_verified = False
        else:
            self.review_session.source_verified = True
        # Publish replacement pixels onto the existing image layer metadata/source path.
        volume = replace(expected, data=np.array(data, copy=True), source=candidate_path)
        self.controller._publish_image(replace(volume, metadata={**volume.metadata, "classification_analysis_grid": True}))
        self.refresh_layers()
        self._remeasure_enabled = True
        self.mark_preview_stale()
        self.status.setText("Source located. Update preview before trusting proposed calls.")
        self._sync_review_panels()

    def _confirm_discard_review_edits(self) -> bool:
        if self.review_session is None or not self.review_session.dirty:
            return True
        box = Q.QMessageBox(self)
        box.setWindowTitle("Unsaved review changes")
        box.setText("This review has unsaved draft settings.")
        save_btn = box.addButton("Save version…", Q.QMessageBox.AcceptRole)
        discard_btn = box.addButton("Discard", Q.QMessageBox.DestructiveRole)
        keep_btn = box.addButton("Keep editing", Q.QMessageBox.RejectRole)
        box.exec_()
        clicked = box.clickedButton()
        if clicked is keep_btn:
            return False
        if clicked is save_btn:
            self.choose_save_reviewed()
            return self.review_session is None or not self.review_session.dirty
        return True

    def _sync_review_panels(self, *_):
        if not hasattr(self, "review_header"):
            return
        if self.review_session is None:
            self.review_header.setText("Review: no analysis open.")
            self.compare_summary.setText("Comparison: open a saved analysis to compare Saved vs Proposed.")
            return
        self.review_header.setText(self.review_session.header_text())
        marker = self.overlay_marker.currentText() if self.overlay_marker.count() else ""
        if not marker and self.review_session.baseline_recipe.raw.get("markers"):
            marker = self.review_session.baseline_recipe.raw["markers"][0]["name"]
        baseline = self.review_session.baseline_calls if self.review_session.baseline_calls_valid else None
        proposed = None if self.result is None else self.result.calls
        comparison = compare_marker_calls(
            baseline, proposed, marker or "",
            population_label=self.review_session.population_label,
        )
        self.compare_summary.setText("\n".join(comparison.summary_lines()))
        self._sync_selected_cell()

    def _sync_selected_cell(self, *_):
        if not hasattr(self, "cell_explain"):
            return
        labels_layer = self.labels.currentData()
        if labels_layer is None or self.result is None:
            self.cell_explain.setText("Selected cell: select reviewed nuclei with restored or previewed calls.")
            return
        selected = int(getattr(labels_layer, "selected_label", 0) or 0)
        if selected <= 0:
            self.cell_explain.setText("Selected cell: click a nucleus label to inspect measurements.")
            return
        marker = self.overlay_marker.currentText()
        rows = self.result.calls
        match = rows[(rows["marker"] == marker) & (rows["label"] == selected)] if marker else rows.iloc[0:0]
        if match.empty:
            self.cell_explain.setText(f"Selected cell {selected}: no call for marker {marker or '(none)'}.")
            return
        row = match.iloc[0]
        marker_spec = next((m for m in self.recipe().raw["markers"] if m["name"] == marker), None)
        if marker_spec is None:
            self.cell_explain.setText(f"Selected cell {selected}: marker settings unavailable.")
            return
        fraction = row.get("fraction")
        units = "fraction of measured voxels (display intensity does not change thresholds)"
        frac_text = "NA" if fraction is None or (isinstance(fraction, float) and np.isnan(fraction)) else f"{100 * float(fraction):.1f}%"
        explanation = explain_marker_call(marker_spec, row)
        self.cell_explain.setText(
            f"Selected cell {selected} · marker {marker} · measured {frac_text} ({units}).\n{explanation}"
        )

    def choose_labels(self):
        path,_ = Q.QFileDialog.getOpenFileName(self,"Reviewed label TIFF","","TIFF (*.tif *.tiff *.TIF *.TIFF)")
        if path: self.load_labels(path)

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
            image, labels, region, declared = self.snapshot_inputs()
            context = {key: self.fields[key].text().strip() for key in ["specimen_id","eye_id","section_id","image_id","region_id"]}
            return image, labels, region, self.policy.currentData(), context, declared
        if self.calibration_panel is not None:
            self.calibration_panel.close()
            if self.calibration_panel.dock is not None:
                self.viewer.window.remove_dock_widget(self.calibration_panel.dock)
        launched_item = self.markers.item(row, 0)
        self._calibrating_row = row
        def accepted(updated):
            self.check_source()
            if (self.calibration_panel is not panel or panel.closed
                    or row >= self.markers.rowCount()
                    or self.markers.item(row, 0) is not launched_item
                    or self.markers.item(row, 0).text().strip() != marker["name"]
                    or self._marker_channel_combo(row).currentData() != marker["channel"]):
                raise ValueError("Source or marker row changed; reopen calibration before accepting.")
            self.markers.blockSignals(True)
            try:
                for col, key in [(2,"low"),(3,"high"),(4,"positive_fraction"),(5,"uncertainty_margin")]:
                    self.markers.item(row,col).setText("" if updated.get(key) is None else str(updated[key]))
            finally:
                self.markers.blockSignals(False)
            self._calibrations[row] = deepcopy(updated["calibration"])
            self.mark_preview_stale()
            self._sync_evidence_label()
            self.status.setText("Reviewed calibration applied. Preview all marker calls before saving.")
        from .calibration import CalibrationPanel
        panel = CalibrationPanel(self.viewer, self.controller, marker, accepted,
                                 snapshot=snapshot, selected_labels=lambda: self.labels.currentData(),
                                 source_check=self.check_source)
        self.calibration_panel = panel
        dock = self.viewer.window.add_dock_widget(self.calibration_panel, name=f"Calibrate {marker['name']}")
        panel.bind_dock(dock)

    def choose_recipe(self):
        path,_ = Q.QFileDialog.getOpenFileName(self,"Load classification recipe","","JSON (*.json)")
        if path: self.apply_recipe(ClassificationRecipe(json.loads(Path(path).read_text(encoding="utf-8"))))

    def choose_save_recipe(self):
        recipe = self.recipe()
        path,_ = Q.QFileDialog.getSaveFileName(self,"Save classification recipe","recipe.json","JSON (*.json)")
        if path: Path(path).write_text(json.dumps(recipe.raw,indent=2),encoding="utf-8")

    def choose_save(self):
        self.validate_ready()
        path = Q.QFileDialog.getExistingDirectory(self,"Choose classification output folder")
        if path: self.preview(path)

    def choose_reopen(self):
        path = Q.QFileDialog.getExistingDirectory(self,"Choose a completed classification run")
        if path: self.reopen(path)
