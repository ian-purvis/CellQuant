"""Stepped batch coexpression UI over existing ``*.cellquant`` runs."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from queue import Empty, SimpleQueue
import time

import numpy as np
from qtpy import QtCore, QtWidgets as Q

from cellquant.classify import ClassificationRecipe, default_queries_for_markers
from cellquant.classify.batch import (
    CellQuantRunRef,
    discover_cellquant_runs,
    group_runs_by_layout,
    read_run_labels,
    run_classify_batch,
    save_reviewed_labels,
    _open_run_volume,
)
from cellquant.config import load_config
from cellquant.contracts import LabelVolume, MutableCancellationToken, PipelineCancelled
from cellquant.plugin.controller import LABEL_LAYER_NAME, _validated_label_array
from cellquant.plugin.coexpression import (
    _MARKER_COLUMN_MIN_WIDTHS,
    _configure_readable_form,
    configure_readable_table,
)
from cellquant.preprocess import prepare_analysis_volume
from cellquant.analysis import analysis_context_summary, analysis_context_from_dict
from dataclasses import replace


class BatchCoexpressionPanel(Q.QWidget):
    """Select → Review → Thresholds → Run (one step visible at a time)."""

    def __init__(self, viewer, controller):
        super().__init__()
        self.viewer = viewer
        self.controller = controller
        self.runs: tuple[CellQuantRunRef, ...] = ()
        self.included: set[str] = set()
        self.layout_recipes: dict[str, dict] = {}
        self.layout_drafts: dict[str, dict] = {}
        self.image_overrides: dict[str, dict] = {}
        self._canonical_recipe: dict | None = None
        self._queries_custom = False
        self._displayed_layout_id = None
        self._loading_table = False
        self._events: SimpleQueue = SimpleQueue()
        self._batch_started: float | None = None
        self._progress = {
            "current": 0,
            "total": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
            "active": None,
            "cancel_requested": False,
        }
        self._review_run: Path | None = None
        self._review_generation = 0
        self._review_layer_id: int | None = None
        self._review_shape: tuple[int, ...] | None = None
        self._review_context_summary = ""
        self.future = None
        self.cancel_token = MutableCancellationToken()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cellquant-classify-batch")
        self._last_output: Path | None = None

        root = Q.QVBoxLayout(self)
        help_text = Q.QLabel(
            "Batch coexpression on complete CellQuant runs. Review labels optionally, "
            "set per-channel thresholds per layout (or override per image), then classify. "
            "Original ND2/TIFF must still be reachable via each run's provenance."
        )
        help_text.setWordWrap(True)
        root.addWidget(help_text)

        self.steps = Q.QTabWidget()
        root.addWidget(self.steps)
        self.steps.addTab(self._build_select(), "1. Select runs")
        self.steps.addTab(self._build_review(), "2. Review labels")
        self.steps.addTab(self._build_thresholds(), "3. Thresholds")
        self.steps.addTab(self._build_run(), "4. Run + results")

        self.status = Q.QLabel("Choose a batch output folder and Discover complete *.cellquant runs.")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        self.destroyed.connect(lambda: self.pool.shutdown(wait=False, cancel_futures=True))

    def guard(self, fn):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - surface in status
            self.status.setText(str(exc))

    def _buttons(self, layout, actions):
        row = Q.QHBoxLayout()
        layout.addLayout(row)
        for title, fn in actions:
            button = Q.QPushButton(title)
            row.addWidget(button)
            button.clicked.connect(lambda checked=False, f=fn: self.guard(f))

    def _build_select(self) -> Q.QWidget:
        page = Q.QWidget()
        layout = Q.QVBoxLayout(page)
        form = Q.QFormLayout()
        _configure_readable_form(form)
        layout.addLayout(form)
        self.root_edit = Q.QLineEdit()
        self.survey_edit = Q.QLineEdit()
        browse = Q.QPushButton("Browse…")
        browse.clicked.connect(lambda: self.guard(self.choose_root))
        survey_browse = Q.QPushButton("Browse…")
        survey_browse.clicked.connect(lambda: self.guard(self.choose_survey))
        root_row = Q.QHBoxLayout()
        root_row.addWidget(self.root_edit)
        root_row.addWidget(browse)
        survey_row = Q.QHBoxLayout()
        survey_row.addWidget(self.survey_edit)
        survey_row.addWidget(survey_browse)
        form.addRow("Batch / output root", root_row)
        form.addRow("Survey JSON (optional)", survey_row)
        self._buttons(layout, [("Discover runs", self.discover), ("Include all", self.include_all), ("Include none", self.include_none)])
        self.run_list = Q.QTreeWidget()
        self.run_list.setHeaderLabels(["Include", "Run", "Layout", "Labels", "Source"])
        self.run_list.setColumnWidth(0, 70)
        self.run_list.setColumnWidth(1, 280)
        self.run_list.itemChanged.connect(self._on_run_item_changed)
        layout.addWidget(self.run_list)
        return page

    def _build_review(self) -> Q.QWidget:
        page = Q.QWidget()
        layout = Q.QVBoxLayout(page)
        note = Q.QLabel(
            "Optional: open a selected run, edit CellQuant labels in napari, then Save curated labels. "
            "This writes labels_reviewed.tif without overwriting Cellpose labels.tif."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.review_pick = Q.QComboBox()
        layout.addWidget(self.review_pick)
        self._buttons(
            layout,
            [
                ("Open for review", self.open_for_review),
                ("Save curated labels", self.save_curated_labels),
                ("Refresh list", self.refresh_review_pick),
            ],
        )
        self.review_status = Q.QLabel("No run opened for review.")
        self.review_status.setWordWrap(True)
        layout.addWidget(self.review_status)
        layout.addStretch(1)
        return page

    def _build_thresholds(self) -> Q.QWidget:
        page = Q.QWidget()
        layout = Q.QVBoxLayout(page)
        form = Q.QFormLayout()
        _configure_readable_form(form)
        layout.addLayout(form)
        self.layout_pick = Q.QComboBox()
        self.layout_pick.currentIndexChanged.connect(lambda *_: self.guard(self.load_layout_into_table))
        form.addRow("Layout", self.layout_pick)
        self.recipe_name = Q.QLineEdit("Nuclear coexpression")
        self.calibration_group = Q.QLineEdit("batch")
        form.addRow("Recipe name", self.recipe_name)
        form.addRow("Calibration group", self.calibration_group)
        self.markers = Q.QTableWidget(0, 6)
        self.markers.setHorizontalHeaderLabels(
            ["Marker", "Channel", "Raw low", "Raw high (optional)", "Positive fraction", "Uncertainty margin"]
        )
        self.markers.setMinimumHeight(165)
        configure_readable_table(self.markers, min_widths=_MARKER_COLUMN_MIN_WIDTHS)
        self.markers.itemChanged.connect(lambda *_: self._on_threshold_table_changed())
        layout.addWidget(self.markers)
        self.query_status = Q.QLabel("Queries: automatic inclusive combinations (update when markers change).")
        self.query_status.setWordWrap(True)
        layout.addWidget(self.query_status)
        self._buttons(
            layout,
            [
                ("Add marker", self.add_marker),
                ("Remove last marker", self.remove_marker),
                ("Apply to layout", self.apply_to_layout),
                ("Load recipe JSON", self.choose_load_recipe),
                ("Save recipe JSON", self.choose_save_recipe),
            ],
        )
        override_row = Q.QHBoxLayout()
        layout.addLayout(override_row)
        self.override_pick = Q.QComboBox()
        self.override_pick.currentIndexChanged.connect(lambda *_: self.guard(self.load_image_effective_recipe))
        override_row.addWidget(self.override_pick, stretch=1)
        apply_override = Q.QPushButton("Save as per-image override")
        apply_override.clicked.connect(lambda: self.guard(self.apply_image_override))
        reset_override = Q.QPushButton("Reset to layout settings")
        reset_override.clicked.connect(lambda: self.guard(self.clear_image_override))
        override_row.addWidget(apply_override)
        override_row.addWidget(reset_override)
        self.threshold_status = Q.QLabel("Edit markers for the selected layout, then Apply to layout.")
        self.threshold_status.setWordWrap(True)
        layout.addWidget(self.threshold_status)
        self.recipe_name.textChanged.connect(lambda *_: self._on_threshold_table_changed())
        self.calibration_group.textChanged.connect(lambda *_: self._on_threshold_table_changed())
        return page

    def _build_run(self) -> Q.QWidget:
        page = Q.QWidget()
        layout = Q.QVBoxLayout(page)
        self.summary = Q.QLabel("Discover runs and set layout recipes before classifying.")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.output_edit = Q.QLineEdit()
        out_row = Q.QHBoxLayout()
        out_row.addWidget(self.output_edit)
        out_browse = Q.QPushButton("Browse…")
        out_browse.clicked.connect(lambda: self.guard(self.choose_output))
        out_row.addWidget(out_browse)
        layout.addLayout(out_row)
        self._buttons(
            layout,
            [
                ("Refresh summary", self.refresh_summary),
                ("Run batch classify", self.run_batch),
                ("Cancel", self.cancel),
                ("Open results folder", self.open_results),
            ],
        )
        self.progress = Q.QLabel("")
        self.progress.setWordWrap(True)
        layout.addWidget(self.progress)
        layout.addStretch(1)
        return page

    def choose_root(self):
        path = Q.QFileDialog.getExistingDirectory(self, "Choose folder containing *.cellquant runs")
        if path:
            self.root_edit.setText(path)
            if not self.output_edit.text().strip():
                self.output_edit.setText(path)

    def choose_survey(self):
        path, _ = Q.QFileDialog.getOpenFileName(self, "Survey JSON", "", "JSON (*.json)")
        if path:
            self.survey_edit.setText(path)

    def choose_output(self):
        path = Q.QFileDialog.getExistingDirectory(self, "Classification batch output parent folder")
        if path:
            self.output_edit.setText(path)

    def discover(self):
        root = self.root_edit.text().strip()
        if not root:
            raise ValueError("Choose a batch root folder first.")
        survey = self.survey_edit.text().strip() or None
        self.runs = discover_cellquant_runs(root, survey_json=survey)
        self.included = {str(run.path.resolve()) for run in self.runs}
        self._populate_run_list()
        self.refresh_review_pick()
        self.refresh_layout_pick()
        self.refresh_summary()
        self.status.setText(f"Found {len(self.runs)} complete CellQuant run(s).")

    def include_all(self):
        self.included = {str(run.path.resolve()) for run in self.runs}
        self._populate_run_list()

    def include_none(self):
        self.included = set()
        self._populate_run_list()

    def _populate_run_list(self):
        self.run_list.blockSignals(True)
        self.run_list.clear()
        grouped = group_runs_by_layout(self.runs)
        for layout_id, members in grouped.items():
            parent = Q.QTreeWidgetItem([ "", f"Layout {layout_id} ({len(members)})", layout_id, "", "" ])
            parent.setFlags(parent.flags() & ~QtCore.Qt.ItemIsUserCheckable)
            self.run_list.addTopLevelItem(parent)
            for run in members:
                key = str(run.path.resolve())
                item = Q.QTreeWidgetItem(["", run.path.name, run.layout_id, "reviewed" if run.has_reviewed_labels else "original", run.source])
                item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
                item.setCheckState(0, QtCore.Qt.Checked if key in self.included else QtCore.Qt.Unchecked)
                item.setData(0, QtCore.Qt.UserRole, key)
                parent.addChild(item)
            parent.setExpanded(True)
        self.run_list.blockSignals(False)

    def _on_run_item_changed(self, item, column):
        if column != 0:
            return
        key = item.data(0, QtCore.Qt.UserRole)
        if not key:
            return
        if item.checkState(0) == QtCore.Qt.Checked:
            self.included.add(key)
        else:
            self.included.discard(key)
        self.refresh_summary()

    def selected_runs(self) -> tuple[CellQuantRunRef, ...]:
        return tuple(run for run in self.runs if str(run.path.resolve()) in self.included)

    def refresh_review_pick(self):
        self.review_pick.clear()
        for run in self.selected_runs() or self.runs:
            label = f"{run.path.name} [{('reviewed' if run.has_reviewed_labels else 'original')}]"
            self.review_pick.addItem(label, str(run.path))

    def refresh_layout_pick(self):
        previous = self.layout_pick.currentData()
        previous_override = self.override_pick.currentData()
        self.layout_pick.blockSignals(True)
        self.layout_pick.clear()
        for layout_id, members in group_runs_by_layout(self.selected_runs() or self.runs).items():
            names = " | ".join(members[0].channel_names) if members and members[0].channel_names else "(channels unknown)"
            self.layout_pick.addItem(f"{layout_id} — {names} ({len(members)})", layout_id)
        self.layout_pick.blockSignals(False)
        if previous is not None:
            index = self.layout_pick.findData(previous)
            if index >= 0:
                self.layout_pick.setCurrentIndex(index)
        self.override_pick.blockSignals(True)
        self.override_pick.clear()
        for run in self.selected_runs() or self.runs:
            key = str(run.path.resolve())
            tag = "override" if key in self.image_overrides else "inherited"
            self.override_pick.addItem(f"{run.path.name} [{tag}]", key)
        if previous_override is not None:
            index = self.override_pick.findData(previous_override)
            if index >= 0:
                self.override_pick.setCurrentIndex(index)
        self.override_pick.blockSignals(False)
        if self.override_pick.currentData():
            self.load_image_effective_recipe()
        else:
            self.load_layout_into_table()

    def open_for_review(self):
        path = self.review_pick.currentData()
        if not path:
            raise ValueError("Select a run to review.")
        if self.future is not None:
            raise ValueError("Wait for the current batch/review operation to finish.")
        run_dir = Path(path)
        self._review_generation += 1
        generation = self._review_generation
        self._review_run = None
        self._review_layer_id = None
        self._review_shape = None
        self.review_status.setText(f"Opening {run_dir.name} as a bound review session…")

        def work(cancel):
            provenance = json.loads((run_dir / "provenance.json").read_text(encoding="utf-8"))
            source = Path(str(provenance.get("source") or ""))
            if not source.is_file():
                raise FileNotFoundError(f"Source image missing: {source}")
            config = load_config(run_dir / "config.json")
            cancel.raise_if_cancelled()
            volume = _open_run_volume(source, config)
            analysis = prepare_analysis_volume(volume, config, cancel=cancel)
            labels_array = read_run_labels(run_dir)
            if labels_array.shape != analysis.data.shape[:3]:
                raise ValueError(
                    f"labels shape {labels_array.shape} does not match analysis grid "
                    f"{analysis.data.shape[:3]}"
                )
            analysis_meta = dict(analysis.metadata)
            analysis_meta["classification_analysis_grid"] = True
            analysis = replace(analysis, metadata=analysis_meta)
            labels = LabelVolume(
                np.array(labels_array, copy=True),
                analysis.spacing_um,
                {
                    "source_run": str(run_dir.resolve()),
                    "review": True,
                    "review_generation": generation,
                    "analysis_volume": dict(analysis.metadata.get("analysis_volume") or {}),
                },
            )
            used = "reviewed" if (run_dir / "labels_reviewed.tif").is_file() else "original"
            summary = analysis_context_summary(
                analysis_context_from_dict(
                    labels.provenance["analysis_volume"],
                    spacing_um=tuple(labels.spacing_um),
                    shape_zyx=tuple(int(v) for v in labels.data.shape),
                    source=analysis.source,
                )
            )
            return analysis, labels, run_dir, used, summary, generation

        def completed(payload):
            analysis, labels, run_dir, used, summary, gen = payload
            if gen != self._review_generation:
                self.review_status.setText("Stale review open discarded (a newer request superseded it).")
                return
            self.controller.config = load_config(run_dir / "config.json")
            self.controller._publish_image(analysis)
            self.controller._publish_labels(labels)
            layer = None
            for candidate in self.viewer.layers:
                if candidate.name == LABEL_LAYER_NAME:
                    layer = candidate
                    break
            self._review_run = run_dir
            self._review_layer_id = id(layer) if layer is not None else None
            self._review_shape = tuple(int(v) for v in labels.data.shape)
            self._review_context_summary = summary
            self.review_status.setText(
                f"Bound review session for {run_dir.name} ({used} labels). "
                f"Display/measurement grid: {summary}. Save is bound to this labels layer."
            )
            self.status.setText(f"Reviewing {run_dir.name}")

        self.cancel_token = MutableCancellationToken()
        self.future = self.pool.submit(work, self.cancel_token)
        self._review_completed = completed

    def save_curated_labels(self):
        if self._review_run is None or self._review_layer_id is None:
            raise ValueError("Open a bound review session first.")
        layer = None
        for candidate in self.viewer.layers:
            if candidate.name == LABEL_LAYER_NAME:
                layer = candidate
                break
        if layer is None:
            raise ValueError("CellQuant labels layer not found; Save disabled.")
        if id(layer) != self._review_layer_id:
            raise ValueError(
                "Labels layer was replaced; Save is bound to the review session layer. "
                "Re-open the run for review before saving."
            )
        meta = dict(getattr(layer, "metadata", {}) or {})
        source_run = Path(str(meta.get("source_run") or ""))
        if source_run.resolve() != self._review_run.resolve():
            raise ValueError(
                "Current labels are not bound to the open review run. "
                "Re-open the run before saving curated labels."
            )
        if self._review_shape is not None and tuple(layer.data.shape) != self._review_shape:
            raise ValueError(
                f"Label shape {tuple(layer.data.shape)} no longer matches the bound review "
                f"grid {self._review_shape}."
            )
        path = save_reviewed_labels(
            self._review_run,
            _validated_label_array(layer.data),
            note=f"bound review generation {self._review_generation}",
        )
        self.runs = tuple(
            CellQuantRunRef(
                path=run.path,
                source=run.source,
                layout_id=run.layout_id,
                channel_names=run.channel_names,
                has_reviewed_labels=True if run.path.resolve() == self._review_run.resolve() else run.has_reviewed_labels,
                run_id=run.run_id,
            )
            for run in self.runs
        )
        self._populate_run_list()
        self.refresh_review_pick()
        self.review_status.setText(
            f"Saved curated labels to {path.name} for {self._review_run.name} "
            f"({self._review_context_summary}). Original labels.tif unchanged."
        )
        self.status.setText(f"Curated labels saved for {self._review_run.name}")

    def add_marker(self):
        row = self.markers.rowCount()
        if row >= 6:
            raise ValueError("Use at most six markers.")
        blocked = self.markers.signalsBlocked()
        self.markers.blockSignals(True)
        try:
            self.markers.insertRow(row)
            for col in [0, 2, 3, 4, 5]:
                self.markers.setItem(row, col, Q.QTableWidgetItem("0" if col == 5 else ""))
            combo = Q.QComboBox()
            from cellquant.plugin.combo_scroll import wrap_combo_with_scrollability

            combo.currentIndexChanged.connect(lambda *_: self._on_threshold_table_changed())
            self.markers.setCellWidget(row, 1, wrap_combo_with_scrollability(Q, combo))
            self._fill_channel_combo(row)
            configure_readable_table(self.markers, min_widths=_MARKER_COLUMN_MIN_WIDTHS)
        finally:
            self.markers.blockSignals(blocked)
        if not blocked and not self._loading_table:
            self._on_threshold_table_changed()

    def remove_marker(self):
        if self.markers.rowCount() > 1:
            self.markers.removeRow(self.markers.rowCount() - 1)
            self._on_threshold_table_changed()

    def _current_layout_channels(self) -> list[str]:
        layout_id = self.layout_pick.currentData()
        for run in self.runs:
            if run.layout_id == layout_id and run.channel_names:
                return list(run.channel_names)
        return []

    def _fill_channel_combo(self, row: int, selected=None):
        combo = self.markers.cellWidget(row, 1)
        if hasattr(combo, "findChildren"):
            children = combo.findChildren(Q.QComboBox)
            if children:
                combo = children[0]
        names = self._current_layout_channels()
        previous = selected if selected is not None else combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("Not acquired", None)
        for i, name in enumerate(names):
            combo.addItem(f"{i + 1}: {name}", i)
        index = combo.findData(previous)
        combo.setCurrentIndex(max(0, index))
        combo.blockSignals(False)

    def _marker_combo(self, row: int) -> Q.QComboBox:
        cell = self.markers.cellWidget(row, 1)
        if cell is None:
            raise RuntimeError("missing channel combo")
        if hasattr(cell, "currentData"):
            return cell
        for child in cell.findChildren(Q.QComboBox):
            return child
        raise RuntimeError("missing channel combo")

    def _table_state(self) -> dict:
        markers = []
        for row in range(self.markers.rowCount()):
            value = lambda col, r=row: (self.markers.item(r, col).text().strip() if self.markers.item(r, col) else "")
            try:
                channel = self._marker_combo(row).currentData()
            except RuntimeError:
                channel = None
            markers.append(
                {
                    "name": value(0),
                    "channel": channel,
                    "low": value(2),
                    "high": value(3),
                    "positive_fraction": value(4),
                    "uncertainty_margin": value(5),
                }
            )
        return {
            "name": self.recipe_name.text().strip() or "Nuclear coexpression",
            "calibration_group": self.calibration_group.text().strip(),
            "markers": markers,
            "queries": deepcopy((self._canonical_recipe or {}).get("queries")),
            "queries_custom": bool(self._queries_custom),
            "region_policy": (self._canonical_recipe or {}).get("region_policy", "whole_object"),
            "expected_channel_names": self._current_layout_channels() or None,
            "canonical": deepcopy(self._canonical_recipe) if self._canonical_recipe else None,
        }

    def _restore_table_state(self, state: dict):
        self._loading_table = True
        try:
            self._canonical_recipe = deepcopy(state.get("canonical"))
            self._queries_custom = bool(state.get("queries_custom"))
            if self._canonical_recipe is not None and "queries" in state:
                self._canonical_recipe["queries"] = deepcopy(state.get("queries"))
            self.recipe_name.blockSignals(True)
            self.calibration_group.blockSignals(True)
            self.markers.blockSignals(True)
            self.recipe_name.setText(state.get("name") or "Nuclear coexpression")
            self.calibration_group.setText(state.get("calibration_group") or "")
            self.markers.setRowCount(0)
            for marker in state.get("markers") or []:
                self.add_marker()
                row = self.markers.rowCount() - 1
                self.markers.item(row, 0).setText(str(marker.get("name") or ""))
                self.markers.item(row, 2).setText("" if marker.get("low") in (None, "") else str(marker["low"]))
                self.markers.item(row, 3).setText("" if marker.get("high") in (None, "") else str(marker["high"]))
                self.markers.item(row, 4).setText(
                    "" if marker.get("positive_fraction") in (None, "") else str(marker["positive_fraction"])
                )
                self.markers.item(row, 5).setText(str(marker.get("uncertainty_margin") or "0"))
                combo = self._marker_combo(row)
                combo.blockSignals(True)
                index = combo.findData(marker.get("channel"))
                if index < 0 and marker.get("channel") is not None:
                    combo.addItem(f"Unavailable channel {marker['channel'] + 1}", marker["channel"])
                    index = combo.count() - 1
                combo.setCurrentIndex(max(0, index))
                combo.blockSignals(False)
        finally:
            self.recipe_name.blockSignals(False)
            self.calibration_group.blockSignals(False)
            self.markers.blockSignals(False)
            self._loading_table = False
        self._sync_query_status()
        self._update_threshold_state_label()

    def _store_current_draft(self, layout_id):
        if layout_id is None or self._loading_table:
            return
        key = self.override_pick.currentData() if hasattr(self, "override_pick") else None
        if key in self.image_overrides:
            return
        self.layout_drafts[layout_id] = self._table_state()

    def _on_threshold_table_changed(self):
        if self._loading_table:
            return
        layout_id = self.layout_pick.currentData()
        if layout_id is None:
            return
        self._store_current_draft(layout_id)
        self._sync_generated_queries()
        self._sync_query_status()
        self._update_threshold_state_label()

    def _marker_names_from_state(self, state=None):
        source = state if state is not None else self._table_state()
        return [m["name"] for m in source.get("markers") or [] if m.get("name")]

    def _queries_match_defaults(self, queries, names) -> bool:
        generated = [
            (
                q["name"],
                tuple(q.get("positive") or []),
                tuple(q.get("negative") or []),
                tuple(q.get("denominator_positive") or []),
            )
            for q in default_queries_for_markers(names)
        ]
        loaded = [
            (
                q.get("name"),
                tuple(q.get("positive") or []),
                tuple(q.get("negative") or []),
                tuple(q.get("denominator_positive") or []),
            )
            for q in (queries or [])
        ]
        return loaded == generated

    def _sync_generated_queries(self):
        if self._queries_custom:
            return
        if self._canonical_recipe is not None:
            self._canonical_recipe["queries"] = None

    def _custom_query_problems(self, names):
        if not self._queries_custom:
            return []
        queries = (self._canonical_recipe or {}).get("queries") or []
        known = set(names)
        problems = []
        for query in queries:
            clauses = (
                list(query.get("positive") or [])
                + list(query.get("negative") or [])
                + list(query.get("denominator_positive") or [])
            )
            missing = [name for name in clauses if name not in known]
            if missing:
                problems.append(
                    f"{query.get('name', '?')} references {', '.join(missing)} — remap or remove this query"
                )
        return problems

    def _sync_query_status(self):
        if not hasattr(self, "query_status"):
            return
        names = self._marker_names_from_state()
        if not self._queries_custom:
            defaults = default_queries_for_markers(names) if names else []
            preview = ", ".join(q["name"] for q in defaults[:4])
            extra = "…" if len(defaults) > 4 else ""
            self.query_status.setText(
                "Queries: automatic inclusive combinations"
                + (f" ({preview}{extra})" if preview else "")
                + ". Regenerated when markers are added, renamed, or removed."
            )
            return
        problems = self._custom_query_problems(names)
        if problems:
            self.query_status.setText("Custom queries need a decision: " + "; ".join(problems))
        else:
            queries = (self._canonical_recipe or {}).get("queries") or []
            self.query_status.setText(
                f"Queries: custom ({len(queries)}) preserved. Reset by loading a recipe without queries."
            )

    def _update_threshold_state_label(self):
        if not hasattr(self, "threshold_status"):
            return
        layout_id = self.layout_pick.currentData()
        key = self.override_pick.currentData() if hasattr(self, "override_pick") else None
        run = self._run_for_key(key) if key else None
        parts = []
        if run is not None:
            if key in self.image_overrides:
                parts.append(f"{run.path.name}: per-image override (not layout defaults)")
            else:
                parts.append(f"{run.path.name}: inherited from layout {run.layout_id}")
        saved = self.layout_recipes.get(layout_id) if layout_id is not None else None
        dirty = False
        if layout_id is not None:
            try:
                current = self.recipe_from_table().raw
                dirty = saved is None or self._marker_payload(current) != self._marker_payload(saved)
            except Exception:
                dirty = bool(self.layout_drafts.get(layout_id)) and saved is not None
        if dirty:
            parts.append("Unsaved changes")
        elif saved is not None:
            parts.append(f"Saved layout recipe ({len(saved.get('markers') or [])} markers)")
        else:
            parts.append("No saved layout recipe yet — Apply to layout or Run will use this table")
        self.threshold_status.setText(" · ".join(parts))

    def _marker_payload(self, raw: dict) -> list:
        return [
            {
                "name": m.get("name"),
                "channel": m.get("channel"),
                "low": m.get("low"),
                "high": m.get("high"),
                "positive_fraction": m.get("positive_fraction"),
                "uncertainty_margin": m.get("uncertainty_margin"),
            }
            for m in raw.get("markers") or []
        ]

    def _run_for_key(self, key):
        if not key:
            return None
        for run in self.runs:
            if str(run.path.resolve()) == key:
                return run
        return None

    def recipe_from_table(self, *, expected_channel_names=None) -> ClassificationRecipe:
        return self._recipe_from_state(self._table_state(), expected_channel_names=expected_channel_names)

    def _recipe_from_state(self, state: dict, *, expected_channel_names=None) -> ClassificationRecipe:
        markers = []
        names = self._marker_names_from_state(state)
        if state.get("queries_custom"):
            problems = self._custom_query_problems(names)
            if problems:
                raise ValueError("Custom queries still reference old marker names. " + "; ".join(problems))
        for index, row in enumerate(state.get("markers") or []):
            if not row.get("name") or row.get("low") in ("", None) or row.get("positive_fraction") in ("", None):
                raise ValueError(f"Marker row {index + 1}: enter name, raw low, and positive fraction.")
            markers.append(
                dict(
                    name=row["name"],
                    channel=row.get("channel"),
                    low=float(row["low"]),
                    high=float(row["high"]) if row.get("high") not in ("", None) else None,
                    positive_fraction=float(row["positive_fraction"]),
                    uncertainty_margin=float(row.get("uncertainty_margin") or 0),
                    compartment="nucleus",
                )
            )
        names = expected_channel_names
        if names is None:
            names = state.get("expected_channel_names") or self._current_layout_channels() or None
        base = deepcopy(state.get("canonical")) if state.get("canonical") else {
            "schema_version": 1,
            "region_policy": state.get("region_policy") or "whole_object",
            "queries": None,
        }
        prior_by_name = {
            m["name"]: m
            for m in (base.get("markers") or [])
            if isinstance(m, dict) and m.get("name")
        }
        for marker in markers:
            prior = prior_by_name.get(marker["name"])
            if prior and prior.get("calibration") is not None:
                marker["calibration"] = deepcopy(prior["calibration"])
        base["name"] = state.get("name") or "Nuclear coexpression"
        base["calibration_group"] = state.get("calibration_group") or ""
        base["markers"] = markers
        base["expected_channel_names"] = names
        base.setdefault("schema_version", 1)
        base.setdefault("region_policy", "whole_object")
        if not state.get("queries_custom"):
            base["queries"] = None
        elif base.get("queries") is None:
            base.pop("queries", None)
        return ClassificationRecipe(base)

    def apply_recipe_to_table(self, recipe: ClassificationRecipe):
        raw = recipe.raw
        names = [m["name"] for m in raw["markers"]]
        loaded = deepcopy(raw.get("queries"))
        custom = loaded is not None and not self._queries_match_defaults(loaded, names)
        self._restore_table_state(
            {
                "name": raw["name"],
                "calibration_group": raw["calibration_group"],
                "markers": [
                    {
                        "name": marker["name"],
                        "channel": marker["channel"],
                        "low": marker["low"],
                        "high": marker.get("high"),
                        "positive_fraction": marker["positive_fraction"],
                        "uncertainty_margin": marker.get("uncertainty_margin", 0),
                    }
                    for marker in raw["markers"]
                ],
                "queries": loaded,
                "queries_custom": custom,
                "region_policy": raw.get("region_policy", "whole_object"),
                "expected_channel_names": raw.get("expected_channel_names"),
                "canonical": deepcopy(raw),
            }
        )
        calibrated = sum(1 for m in raw["markers"] if m.get("calibration"))
        extra = (
            f"Loaded recipe '{raw['name']}' · region_policy={raw.get('region_policy')} · "
            f"calibration evidence on {calibrated} marker(s)."
        )
        self.threshold_status.setText(self.threshold_status.text() + " · " + extra)

    def load_layout_into_table(self):
        if self._loading_table:
            return
        layout_id = self.layout_pick.currentData()
        if layout_id is None:
            return
        if self._displayed_layout_id is not None and self._displayed_layout_id != layout_id:
            self._store_current_draft(self._displayed_layout_id)
        self._displayed_layout_id = layout_id
        if layout_id in self.layout_drafts:
            self._restore_table_state(self.layout_drafts[layout_id])
            return
        if layout_id in self.layout_recipes:
            self.apply_recipe_to_table(ClassificationRecipe(self.layout_recipes[layout_id]))
            return
        self._canonical_recipe = None
        self._queries_custom = False
        self._loading_table = True
        try:
            self.markers.setRowCount(0)
            channels = self._current_layout_channels()
            if channels:
                for i, name in enumerate(channels):
                    self.add_marker()
                    row = self.markers.rowCount() - 1
                    self.markers.item(row, 0).setText(name)
                    self._marker_combo(row).setCurrentIndex(self._marker_combo(row).findData(i))
                    self.markers.item(row, 4).setText("0.5")
            else:
                self.add_marker()
        finally:
            self._loading_table = False
        self._store_current_draft(layout_id)
        self._sync_query_status()
        self._update_threshold_state_label()

    def load_image_effective_recipe(self):
        if self._loading_table:
            return
        key = self.override_pick.currentData()
        run = self._run_for_key(key)
        if run is None:
            self.load_layout_into_table()
            return
        if self._displayed_layout_id is not None:
            self._store_current_draft(self._displayed_layout_id)
        index = self.layout_pick.findData(run.layout_id)
        if index >= 0:
            self.layout_pick.blockSignals(True)
            self.layout_pick.setCurrentIndex(index)
            self.layout_pick.blockSignals(False)
            self._displayed_layout_id = run.layout_id
        if key in self.image_overrides:
            self.apply_recipe_to_table(ClassificationRecipe(self.image_overrides[key]))
        elif run.layout_id in self.layout_drafts:
            self._restore_table_state(self.layout_drafts[run.layout_id])
        elif run.layout_id in self.layout_recipes:
            self.apply_recipe_to_table(ClassificationRecipe(self.layout_recipes[run.layout_id]))
        else:
            self._displayed_layout_id = None
            self.load_layout_into_table()
        self._update_threshold_state_label()

    def apply_to_layout(self):
        layout_id = self.layout_pick.currentData()
        if layout_id is None:
            raise ValueError("Select a layout first.")
        recipe = self.recipe_from_table()
        if not recipe.raw["calibration_group"]:
            raise ValueError("Enter a calibration group name.")
        self.layout_recipes[layout_id] = deepcopy(recipe.raw)
        self._canonical_recipe = deepcopy(recipe.raw)
        self.layout_drafts.pop(layout_id, None)
        self.threshold_status.setText(
            f"Applied recipe to layout {layout_id} ({len(recipe.raw['markers'])} markers; "
            f"fingerprint {recipe.fingerprint[:12]}…)."
        )
        self._sync_query_status()
        self.refresh_summary()

    def apply_image_override(self):
        key = self.override_pick.currentData()
        run = self._run_for_key(key)
        if run is None:
            raise ValueError("Select an image for override.")
        layout_id = self.layout_pick.currentData()
        if run.layout_id != layout_id:
            raise ValueError(
                f"{run.path.name} belongs to layout {run.layout_id}, not {layout_id}. "
                "Select the image to load its layout, review channel mapping, then save the override."
            )
        recipe = self.recipe_from_table(expected_channel_names=list(run.channel_names) or None)
        self.image_overrides[key] = deepcopy(recipe.raw)
        self._canonical_recipe = deepcopy(recipe.raw)
        self.layout_drafts.pop(layout_id, None)
        self.threshold_status.setText(f"Saved per-image override for {Path(key).name}.")
        self.refresh_layout_pick()
        self.refresh_summary()

    def clear_image_override(self):
        key = self.override_pick.currentData()
        if key and key in self.image_overrides:
            del self.image_overrides[key]
            run = self._run_for_key(key)
            if run is not None:
                self.layout_drafts.pop(run.layout_id, None)
            self._displayed_layout_id = None
            self.threshold_status.setText(f"Reset {Path(key).name} to layout settings.")
            self.refresh_layout_pick()
            self.refresh_summary()

    def choose_load_recipe(self):
        path, _ = Q.QFileDialog.getOpenFileName(self, "Load recipe", "", "JSON (*.json)")
        if path:
            self.apply_recipe_to_table(ClassificationRecipe(json.loads(Path(path).read_text(encoding="utf-8"))))
            layout_id = self.layout_pick.currentData()
            if layout_id is not None:
                self._store_current_draft(layout_id)

    def choose_save_recipe(self):
        recipe = self.recipe_from_table()
        path, _ = Q.QFileDialog.getSaveFileName(self, "Save recipe", "recipe.json", "JSON (*.json)")
        if path:
            Path(path).write_text(json.dumps(recipe.raw, indent=2), encoding="utf-8")

    def _commit_visible_drafts(self) -> dict[str, ClassificationRecipe]:
        layout_id = self.layout_pick.currentData()
        key = self.override_pick.currentData() if hasattr(self, "override_pick") else None
        run = self._run_for_key(key) if key else None
        if run is not None and key in self.image_overrides:
            recipe = self.recipe_from_table(expected_channel_names=list(run.channel_names) or None)
            self.image_overrides[key] = deepcopy(recipe.raw)
        elif layout_id is not None:
            self._store_current_draft(layout_id)
        recipes: dict[str, ClassificationRecipe] = {}
        selected = self.selected_runs()
        needed = {run.layout_id for run in selected}
        for lid in needed:
            state = self.layout_drafts.get(lid)
            if state is not None:
                recipe = self._recipe_from_state(state)
                recipes[lid] = recipe
                self.layout_recipes[lid] = deepcopy(recipe.raw)
            elif lid in self.layout_recipes:
                recipes[lid] = ClassificationRecipe(self.layout_recipes[lid])
        self.layout_drafts.clear()
        return recipes

    def refresh_summary(self, *, dispatched=None):
        selected = self.selected_runs()
        layouts = group_runs_by_layout(selected)
        resolved = dispatched or {}
        missing = [
            lid
            for lid in layouts
            if lid not in resolved and lid not in self.layout_recipes and lid not in self.layout_drafts
        ]
        lines = [
            f"Included runs: {len(selected)}",
            f"Layouts: {len(layouts)} (recipes set: {len(layouts) - len(missing)})",
            f"Per-image overrides: {len(self.image_overrides)}",
            f"Reviewed labels: {sum(1 for r in selected if r.has_reviewed_labels)}",
        ]
        if missing:
            lines.append("Missing layout recipes: " + ", ".join(missing))
        sources = dispatched if dispatched is not None else {
            lid: ClassificationRecipe(raw) for lid, raw in self.layout_recipes.items()
        }
        if sources:
            lines.append("Effective settings that will run:")
            for lid, recipe in sources.items():
                markers = ", ".join(f"{m['name']}={m['low']}" for m in recipe.raw["markers"])
                lines.append(f"  {lid}: {markers}")
        for key, raw in self.image_overrides.items():
            markers = ", ".join(f"{m['name']}={m['low']}" for m in raw.get("markers") or [])
            lines.append(f"  override {Path(key).name}: {markers}")
        self.summary.setText("\n".join(lines))

    def _enqueue_event(self, event):
        self._events.put(event)

    def _apply_progress_event(self, event):
        details = dict(getattr(event, "details", {}) or {})
        status = details.get("status")
        self._progress["current"] = event.current or self._progress["current"]
        self._progress["total"] = event.total or self._progress["total"]
        self._progress["active"] = Path(event.file_id).name if event.file_id else self._progress["active"]
        if status == "completed":
            self._progress["completed"] += 1
        elif status == "failed":
            self._progress["failed"] += 1
        elif status == "cancelled":
            self._progress["cancelled"] += 1
        self._refresh_progress_label(running=True, status=status, message=details.get("message"))

    def _refresh_progress_label(self, *, running=True, status=None, message=None):
        elapsed = 0.0 if self._batch_started is None else max(0.0, time.monotonic() - self._batch_started)
        current = self._progress["current"]
        total = self._progress["total"]
        active = self._progress["active"] or "…"
        parts = [
            f"{active} ({current}/{total})" if total else str(active),
            f"{self._progress['completed']} completed",
            f"{self._progress['failed']} failed",
            f"elapsed {elapsed:.0f}s (ETA is approximate)",
        ]
        if self._progress["cancel_requested"]:
            parts.insert(0, "Cancellation requested")
        elif status == "running" or running:
            parts.insert(0, "Running")
        if message:
            parts.append(str(message))
        self.progress.setText(" · ".join(parts))

    def _drain_events(self):
        while True:
            try:
                event = self._events.get_nowait()
            except Empty:
                break
            self._apply_progress_event(event)

    def run_batch(self):
        if self.future is not None:
            raise ValueError("A batch is already running.")
        selected = self.selected_runs()
        if not selected:
            raise ValueError("Include at least one run.")
        recipes = self._commit_visible_drafts()
        missing = [
            run.layout_id
            for run in selected
            if run.layout_id not in recipes and str(run.path.resolve()) not in self.image_overrides
        ]
        missing = sorted(set(missing))
        if missing:
            raise ValueError("Apply layout recipes first for: " + ", ".join(missing))
        parent = self.output_edit.text().strip() or self.root_edit.text().strip()
        if not parent:
            raise ValueError("Choose an output folder.")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = Path(parent) / f"classify_batch_{stamp}"
        self._last_output = destination
        overrides = {key: ClassificationRecipe(value) for key, value in self.image_overrides.items()}
        self.cancel_token = MutableCancellationToken()
        self._progress = {
            "current": 0,
            "total": len(selected),
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
            "active": None,
            "cancel_requested": False,
        }
        self._batch_started = time.monotonic()
        self.refresh_summary(dispatched=recipes)
        self.progress.setText(f"Starting batch → {destination}")
        self.status.setText("Running batch classify…")

        def work(cancel):
            return run_classify_batch(
                selected,
                output_dir=destination,
                layout_recipes=recipes,
                image_overrides=overrides,
                cancel=cancel,
                events=self._enqueue_event,
            )

        self.future = self.pool.submit(work, self.cancel_token)

    def cancel(self):
        self.cancel_token.cancel()
        self._progress["cancel_requested"] = True
        self.status.setText("Cancellation requested…")
        self._refresh_progress_label(running=True)

    def open_results(self):
        if self._last_output is None or not Path(self._last_output).is_dir():
            raise ValueError("No results folder yet.")
        try:
            from cellquant.plugin.messages import open_path_in_os

            open_path_in_os(self._last_output)
        except Exception as exc:  # noqa: BLE001 - surface actionable path
            raise RuntimeError(
                f"Could not open results folder ({exc}). Path: {self._last_output}"
            ) from exc

    def poll(self):
        self._drain_events()
        if self.future is None:
            return
        if not self.future.done():
            if self._batch_started is not None:
                self._refresh_progress_label(running=True)
            return
        future, self.future = self.future, None
        self._drain_events()
        try:
            artifacts = future.result()
            completed = getattr(self, "_review_completed", None)
            if completed is not None and isinstance(artifacts, tuple) and len(artifacts) == 6:
                self._review_completed = None
                completed(artifacts)
                return
            self._last_output = Path(artifacts["output_dir"])
            summary = json.loads(Path(artifacts["batch_summary"]).read_text(encoding="utf-8"))
            cancelled = summary.get("status") == "cancelled"
            parts = [
                f"{summary.get('completed', 0)} completed",
                f"{summary.get('failed', 0)} failed",
            ]
            if cancelled:
                parts.append(f"{summary.get('cancelled', 0)} cancelled during active item")
                parts.append(f"{summary.get('unstarted', 0)} not started")
            headline = "Cancelled" if cancelled else "Done"
            self.progress.setText(f"{headline}: {', '.join(parts)} → {self._last_output}")
            self.status.setText(
                f"Batch {'cancelled; completed work kept, unfinished work not written as success' if cancelled else 'finished'}: {self._last_output}"
            )
            self.summary.setText(self.summary.text() + f"\nLast output: {self._last_output}")
            self._batch_started = None
            self._progress["cancel_requested"] = False
        except PipelineCancelled:
            self._review_completed = None
            self._batch_started = None
            partial = self._last_output is not None and Path(self._last_output).is_dir()
            self.status.setText(
                f"Batch cancelled; completed work kept at {self._last_output}" if partial else "Batch cancelled."
            )
            self.progress.setText(f"Cancelled → {self._last_output}" if partial else "Cancelled.")
            self.review_status.setText("Review open cancelled.")
        except Exception as exc:  # noqa: BLE001
            self._review_completed = None
            self._batch_started = None
            self.status.setText(f"Batch failed: {exc}")
            self.progress.setText(str(exc))
            self.review_status.setText(f"Could not open for review: {exc}")
