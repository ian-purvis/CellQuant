"""Stepped batch coexpression UI over existing ``*.cellquant`` runs."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
from qtpy import QtCore, QtWidgets as Q

from cellquant.classify import ClassificationRecipe
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
        self.image_overrides: dict[str, dict] = {}
        self._canonical_recipe: dict | None = None
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
        layout.addWidget(self.markers)
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
        override_row.addWidget(self.override_pick, stretch=1)
        apply_override = Q.QPushButton("Save as per-image override")
        apply_override.clicked.connect(lambda: self.guard(self.apply_image_override))
        clear_override = Q.QPushButton("Clear override")
        clear_override.clicked.connect(lambda: self.guard(self.clear_image_override))
        override_row.addWidget(apply_override)
        override_row.addWidget(clear_override)
        self.threshold_status = Q.QLabel("Edit markers for the selected layout, then Apply to layout.")
        self.threshold_status.setWordWrap(True)
        layout.addWidget(self.threshold_status)
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
        self.override_pick.clear()
        for run in self.selected_runs() or self.runs:
            tag = "override" if str(run.path.resolve()) in self.image_overrides else "layout"
            self.override_pick.addItem(f"{run.path.name} [{tag}]", str(run.path.resolve()))
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
        self.markers.insertRow(row)
        for col in [0, 2, 3, 4, 5]:
            self.markers.setItem(row, col, Q.QTableWidgetItem("0" if col == 5 else ""))
        combo = Q.QComboBox()
        from cellquant.plugin.combo_scroll import wrap_combo_with_scrollability

        self.markers.setCellWidget(row, 1, wrap_combo_with_scrollability(Q, combo))
        self._fill_channel_combo(row)
        configure_readable_table(self.markers, min_widths=_MARKER_COLUMN_MIN_WIDTHS)

    def remove_marker(self):
        if self.markers.rowCount() > 1:
            self.markers.removeRow(self.markers.rowCount() - 1)

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
        if hasattr(cell, "currentData"):
            return cell
        for child in cell.findChildren(Q.QComboBox):
            return child
        raise RuntimeError("missing channel combo")

    def recipe_from_table(self, *, expected_channel_names=None) -> ClassificationRecipe:
        markers = []
        for row in range(self.markers.rowCount()):
            value = lambda col, r=row: self.markers.item(r, col).text().strip()
            if not value(0) or not value(2) or not value(4):
                raise ValueError(f"Marker row {row + 1}: enter name, raw low, and positive fraction.")
            markers.append(
                dict(
                    name=value(0),
                    channel=self._marker_combo(row).currentData(),
                    low=float(value(2)),
                    high=float(value(3)) if value(3) else None,
                    positive_fraction=float(value(4)),
                    uncertainty_margin=float(value(5) or 0),
                    compartment="nucleus",
                )
            )
        names = expected_channel_names
        if names is None:
            names = self._current_layout_channels() or None
        base = deepcopy(self._canonical_recipe) if self._canonical_recipe else {
            "schema_version": 1,
            "region_policy": "whole_object",
            "queries": None,
        }
        # Preserve calibration evidence by marker name when the user only edits thresholds.
        prior_by_name = {
            m["name"]: m
            for m in (base.get("markers") or [])
            if isinstance(m, dict) and m.get("name")
        }
        for marker in markers:
            prior = prior_by_name.get(marker["name"])
            if prior and prior.get("calibration") is not None:
                marker["calibration"] = deepcopy(prior["calibration"])
        base["name"] = self.recipe_name.text().strip() or "Nuclear coexpression"
        base["calibration_group"] = self.calibration_group.text().strip()
        base["markers"] = markers
        base["expected_channel_names"] = names
        base.setdefault("schema_version", 1)
        base.setdefault("region_policy", "whole_object")
        # Keep queries / region_policy from the canonical model; batch table does not edit them.
        if base.get("queries") is None:
            base.pop("queries", None)
        return ClassificationRecipe(base)

    def apply_recipe_to_table(self, recipe: ClassificationRecipe):
        raw = recipe.raw
        self._canonical_recipe = deepcopy(raw)
        self.recipe_name.setText(raw["name"])
        self.calibration_group.setText(raw["calibration_group"])
        self.markers.setRowCount(0)
        for marker in raw["markers"]:
            self.add_marker()
            row = self.markers.rowCount() - 1
            for col, key in [(0, "name"), (2, "low"), (3, "high"), (4, "positive_fraction"), (5, "uncertainty_margin")]:
                self.markers.item(row, col).setText("" if marker.get(key) is None else str(marker[key]))
            combo = self._marker_combo(row)
            index = combo.findData(marker["channel"])
            if index < 0 and marker["channel"] is not None:
                combo.addItem(f"Unavailable channel {marker['channel'] + 1}", marker["channel"])
                index = combo.count() - 1
            combo.setCurrentIndex(max(0, index))
        calibrated = sum(1 for m in raw["markers"] if m.get("calibration"))
        queries = raw.get("queries") or []
        self.threshold_status.setText(
            f"Loaded recipe '{raw['name']}' · region_policy={raw.get('region_policy')} · "
            f"queries={len(queries)} (preserved; not edited in this table) · "
            f"calibration evidence on {calibrated} marker(s)."
        )

    def load_layout_into_table(self):
        layout_id = self.layout_pick.currentData()
        if layout_id is None:
            return
        if layout_id in self.layout_recipes:
            self.apply_recipe_to_table(ClassificationRecipe(self.layout_recipes[layout_id]))
            return
        self._canonical_recipe = None
        # Seed one marker per channel when available.
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

    def apply_to_layout(self):
        layout_id = self.layout_pick.currentData()
        if layout_id is None:
            raise ValueError("Select a layout first.")
        recipe = self.recipe_from_table()
        if not recipe.raw["calibration_group"]:
            raise ValueError("Enter a calibration group name.")
        self.layout_recipes[layout_id] = deepcopy(recipe.raw)
        self._canonical_recipe = deepcopy(recipe.raw)
        self.threshold_status.setText(
            f"Applied recipe to layout {layout_id} ({len(recipe.raw['markers'])} markers; "
            f"fingerprint {recipe.fingerprint[:12]}…)."
        )
        self.refresh_summary()

    def apply_image_override(self):
        key = self.override_pick.currentData()
        if not key:
            raise ValueError("Select an image for override.")
        recipe = self.recipe_from_table()
        self.image_overrides[key] = deepcopy(recipe.raw)
        self._canonical_recipe = deepcopy(recipe.raw)
        self.threshold_status.setText(f"Saved per-image override for {Path(key).name}.")
        self.refresh_layout_pick()
        self.refresh_summary()

    def clear_image_override(self):
        key = self.override_pick.currentData()
        if key and key in self.image_overrides:
            del self.image_overrides[key]
            self.threshold_status.setText(f"Cleared override for {Path(key).name}.")
            self.refresh_layout_pick()
            self.refresh_summary()

    def choose_load_recipe(self):
        path, _ = Q.QFileDialog.getOpenFileName(self, "Load recipe", "", "JSON (*.json)")
        if path:
            self.apply_recipe_to_table(ClassificationRecipe(json.loads(Path(path).read_text(encoding="utf-8"))))

    def choose_save_recipe(self):
        recipe = self.recipe_from_table()
        path, _ = Q.QFileDialog.getSaveFileName(self, "Save recipe", "recipe.json", "JSON (*.json)")
        if path:
            Path(path).write_text(json.dumps(recipe.raw, indent=2), encoding="utf-8")

    def refresh_summary(self):
        selected = self.selected_runs()
        layouts = group_runs_by_layout(selected)
        missing = [lid for lid in layouts if lid not in self.layout_recipes]
        lines = [
            f"Included runs: {len(selected)}",
            f"Layouts: {len(layouts)} (recipes set: {len(layouts) - len(missing)})",
            f"Per-image overrides: {len(self.image_overrides)}",
            f"Reviewed labels: {sum(1 for r in selected if r.has_reviewed_labels)}",
        ]
        if missing:
            lines.append("Missing layout recipes: " + ", ".join(missing))
        self.summary.setText("\n".join(lines))

    def run_batch(self):
        if self.future is not None:
            raise ValueError("A batch is already running.")
        selected = self.selected_runs()
        if not selected:
            raise ValueError("Include at least one run.")
        missing = [r.layout_id for r in selected if r.layout_id not in self.layout_recipes and str(r.path.resolve()) not in self.image_overrides]
        # Deduplicate missing layouts
        missing = sorted(set(missing))
        if missing:
            raise ValueError("Apply layout recipes first for: " + ", ".join(missing))
        parent = self.output_edit.text().strip() or self.root_edit.text().strip()
        if not parent:
            raise ValueError("Choose an output folder.")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = Path(parent) / f"classify_batch_{stamp}"
        # Known before the worker starts so cancelled batches can still be opened.
        self._last_output = destination
        recipes = {key: ClassificationRecipe(value) for key, value in self.layout_recipes.items()}
        overrides = {key: ClassificationRecipe(value) for key, value in self.image_overrides.items()}
        self.cancel_token = MutableCancellationToken()
        self.progress.setText(f"Starting batch → {destination}")
        self.status.setText("Running batch classify…")

        def work(cancel):
            return run_classify_batch(
                selected,
                output_dir=destination,
                layout_recipes=recipes,
                image_overrides=overrides,
                cancel=cancel,
            )

        self.future = self.pool.submit(work, self.cancel_token)

    def cancel(self):
        self.cancel_token.cancel()
        self.status.setText("Cancellation requested…")

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
        if self.future is None or not self.future.done():
            return
        future, self.future = self.future, None
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
            parts = [f"{summary.get('completed', 0)} completed", f"{summary.get('failed', 0)} failed"]
            if cancelled:
                parts.append(f"{summary.get('cancelled', 0)} cancelled")
                parts.append(f"{summary.get('unstarted', 0)} not started")
            headline = "Cancelled" if cancelled else "Done"
            self.progress.setText(f"{headline}: {', '.join(parts)} → {self._last_output}")
            self.status.setText(
                f"Batch {'cancelled; partial results kept' if cancelled else 'finished'}: {self._last_output}"
            )
            self.summary.setText(self.summary.text() + f"\nLast output: {self._last_output}")
        except PipelineCancelled:
            self._review_completed = None
            partial = self._last_output is not None and Path(self._last_output).is_dir()
            self.status.setText(
                f"Batch cancelled; partial results: {self._last_output}" if partial else "Batch cancelled."
            )
            self.progress.setText(f"Cancelled → {self._last_output}" if partial else "Cancelled.")
            self.review_status.setText("Review open cancelled.")
        except Exception as exc:  # noqa: BLE001
            self._review_completed = None
            self.status.setText(f"Batch failed: {exc}")
            self.progress.setText(str(exc))
            self.review_status.setText(f"Could not open for review: {exc}")
