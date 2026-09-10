"""Explicit, marker-specific review of nuclear threshold calibration."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from qtpy import QtCore, QtWidgets as Q

from cellquant.classify.calibration import (analyze_calibration, propose_calibration,
    preview_calibration, calibration_record)
from cellquant.contracts import MutableCancellationToken, PipelineCancelled
from cellquant.preprocess import prepare_analysis_volume


class CalibrationPanel(Q.QWidget):
    def __init__(self, viewer, controller, marker, on_accept, *, snapshot, selected_labels=None, source_check=None):
        super().__init__()
        self.viewer, self.marker, self.on_accept = viewer, deepcopy(marker), on_accept
        self.snapshot, self.selected_labels = snapshot, selected_labels
        self.source_check = source_check
        self.revision = 0
        self.analysis = self.proposal = self.review = None
        self.negative_ids, self.positive_ids = set(), set()
        self.future = None
        self.closed = False
        self.dock = None
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cellquant-calibration")
        self.token = MutableCancellationToken()
        self.layers = []
        self.setWindowTitle(f"Review calibration · {marker['name']}")
        self.resize(780, 900)
        outer = Q.QVBoxLayout(self)
        scroll = Q.QScrollArea()
        scroll.setWidgetResizable(True)
        body = Q.QWidget()
        layout = Q.QVBoxLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        title = Q.QLabel(f"Calibrate {marker['name']} · nuclear fluorescence")
        title.setStyleSheet("font-size: 18px; font-weight: bold")
        layout.addWidget(title)
        help_text = Q.QLabel("1. Load the selected marker. 2. Pick nuclei in napari and mark examples. "
            "3. Request a starting threshold, then preview and review the calls. "
            "Proposals describe intensity distributions; they are not biological truth.")
        help_text.setWordWrap(True); layout.addWidget(help_text)
        self.load_button = self.button(layout, "Load / refresh selected marker", self.load_source)
        self.figure = Figure(figsize=(6, 2), tight_layout=True)
        self.canvas = FigureCanvasQTAgg(self.figure); self.canvas.setMinimumHeight(180)
        layout.addWidget(self.canvas)
        self.axes = self.figure.add_subplot(111)
        self.examples = Q.QLabel("No examples marked."); layout.addWidget(self.examples)
        row = Q.QHBoxLayout(); layout.addLayout(row)
        self.negative_button = self.button(row, "Mark selected nucleus negative", lambda: self.mark_example(False))
        self.positive_button = self.button(row, "Mark selected nucleus positive", lambda: self.mark_example(True))
        self.button(row, "Clear examples", self.clear_examples)
        note = Q.QLabel("Use the labels layer picker in napari (press I), then click a nucleus. "
                        "Examples must belong to the selected counting region.")
        note.setWordWrap(True); layout.addWidget(note)
        form = Q.QFormLayout(); layout.addLayout(form)
        form.setRowWrapPolicy(Q.QFormLayout.WrapLongRows)
        form.setFieldGrowthPolicy(Q.QFormLayout.AllNonFixedFieldsGrow)
        self.method = Q.QComboBox()
        self.method.setMinimumWidth(220)
        self.method.setSizePolicy(Q.QSizePolicy.Expanding, Q.QSizePolicy.Fixed)
        self.method.addItem("Otsu · pooled in-nucleus pixels", "otsu")
        self.method.addItem("Negative-example pixel percentile", "negative_percentile")
        self.percentile = Q.QDoubleSpinBox(); self.percentile.setRange(0, 100); self.percentile.setValue(99)
        form.addRow("Proposal method", self.method); form.addRow("Negative pixel percentile (explicit choice)", self.percentile)
        self.suggest_button = self.button(layout, "Propose starting raw low threshold", self.suggest)
        self.fields = {}
        for key, label, default in [("low", "Raw low (inclusive)", ""), ("high", "Raw high (optional, inclusive)", ""),
                ("positive_fraction", "Positive pixel fraction · 0.5 is a starting value", "0.5"),
                ("uncertainty_margin", "Uncertainty margin (fraction units, optional)", "0")]:
            value = marker.get(key)
            field = Q.QLineEdit(default if value is None else str(value))
            field.setMinimumWidth(180)
            field.setSizePolicy(Q.QSizePolicy.Expanding, Q.QSizePolicy.Fixed)
            self.fields[key] = field; form.addRow(label, field)
            field.textChanged.connect(self.invalidate_review)
        self.method.currentIndexChanged.connect(self.proposal_changed)
        self.percentile.valueChanged.connect(self.proposal_changed)
        row = Q.QHBoxLayout(); layout.addLayout(row)
        self.preview_button = self.button(row, "Preview these settings", self.preview)
        self.accept_button = self.button(row, "Accept reviewed settings", self.accept_review)
        self.accept_button.setEnabled(False)
        self.button(row, "Cancel operation", self.cancel)
        legend = Q.QLabel("Linked analysis-grid layers: green = positive, blue = negative, yellow = uncertain, "
                          "magenta = missing; background/excluded = transparent. Table lists nucleus fractions and example disagreements.")
        legend.setWordWrap(True); layout.addWidget(legend)
        self.table = Q.QTableWidget(); self.table.setMinimumHeight(160)
        self.table.setMinimumWidth(520)
        self.table.setEditTriggers(Q.QAbstractItemView.NoEditTriggers)
        self.table.cellClicked.connect(self.navigate); layout.addWidget(self.table)
        from cellquant.plugin.coexpression import configure_readable_table
        configure_readable_table(self.table)
        self.status = Q.QLabel("Load the selected marker to begin. Previously saved evidence is historical until this review is accepted.")
        self.status.setWordWrap(True); layout.addWidget(self.status)
        self.timer = QtCore.QTimer(self); self.timer.setInterval(30); self.timer.timeout.connect(self.poll); self.timer.start()
        self.destroyed.connect(lambda: self.pool.shutdown(wait=False, cancel_futures=True))

    def button(self, layout, text, fn):
        button = Q.QPushButton(text); layout.addWidget(button)
        button.clicked.connect(lambda checked=False: self.guard(fn))
        return button

    def bind_dock(self, dock):
        self.dock = dock
        # Keep labels, fields, and the review table usable when napari creates
        # a narrow dock by default. Users can still widen it beyond this floor.
        dock.setMinimumWidth(520)
        self.setMinimumWidth(520)
        dock.installEventFilter(self)

    def eventFilter(self, watched, event):
        # Closing napari's dock only hides its child; explicitly end this review.
        if watched is self.dock and event.type() == QtCore.QEvent.Close:
            self.close()
        return super().eventFilter(watched, event)

    def event(self, event):
        # Napari's X button removes the dock by detaching its child first.
        if (event.type() == QtCore.QEvent.ParentChange
                and getattr(self, "dock", None) is not None
                and self.parentWidget() is None):
            self.close()
            self.dock = None
        return super().event(event)

    def guard(self, fn):
        try: return fn()
        except Exception as exc: self.status.setText(str(exc))

    def invalidate_review(self, *_):
        self.revision += 1
        self.review = None
        self.accept_button.setEnabled(False)
        self.table.setRowCount(0)
        for layer in self.layers: layer.visible = False

    def proposal_changed(self, *_):
        self.proposal = None
        self.invalidate_review()

    def invalidate_source(self, *_):
        self.token.cancel()
        self.analysis = self.proposal = None
        self.invalidate_review()
        self.axes.clear(); self.canvas.draw_idle()
        self.status.setText("Source or marker changed. Load / refresh the selected marker before reviewing again.")

    def start(self, operation, completed):
        if self.future is not None: raise ValueError("Wait for the current operation or cancel it first.")
        self.token = MutableCancellationToken()
        self._job_revision = self.revision
        self._completed = completed
        self.future = self.pool.submit(operation, self.token)
        self.status.setText("Working on a source snapshot…")

    def poll(self):
        if self.source_check is not None: self.source_check()
        if self.future is None or not self.future.done(): return
        future, self.future = self.future, None
        try:
            value = future.result()
            self.token.raise_if_cancelled()
            if self.closed or self._job_revision != self.revision: return
            self._completed(value)
        except PipelineCancelled: self.status.setText("Cancelled. No new review accepted.")
        except Exception as exc: self.status.setText(f"Could not calibrate: {exc}")

    def cancel(self):
        self.token.cancel(); self.invalidate_review()
        self.status.setText("Cancellation requested. No new review accepted.")

    def load_source(self):
        if self.marker.get("channel") is None: raise ValueError("Select an acquired marker channel first.")
        if self.future is not None: raise ValueError("Wait for the current operation to finish cancelling.")
        image, labels, region, policy, context, config = self.snapshot()
        self.invalidate_source()
        self.clear_examples()
        def work(cancel):
            analysis_image = image if image.metadata.get("classification_analysis_grid") else prepare_analysis_volume(image, config, cancel)
            return analyze_calibration(analysis_image, labels, marker_name=self.marker["name"],
                channel=self.marker["channel"], region=region, region_policy=policy, context=context, cancel=cancel)
        self.start(work, self.source_ready)

    def source_ready(self, analysis):
        self.analysis = analysis
        self.axes.clear()
        self.axes.stairs(analysis.histogram_counts, analysis.histogram_edges, fill=True, color="#5478a6")
        self.axes.set_xlabel("Raw fluorescence in eligible nuclei"); self.axes.set_ylabel("Pixels")
        self.canvas.draw_idle()
        self.status.setText("Source ready. Pick negative/positive examples or request an Otsu starting point.")

    def mark_example(self, positive):
        if self.analysis is None: raise ValueError("Load the selected marker first.")
        layer = self.selected_labels() if self.selected_labels else self.viewer.layers.selection.active
        label = int(getattr(layer, "selected_label", 0))
        if label == 0 or label not in set(self.analysis.objects["label"]):
            raise ValueError("Pick an eligible nucleus on the reviewed nuclei labels layer first.")
        own, other = (self.positive_ids, self.negative_ids) if positive else (self.negative_ids, self.positive_ids)
        own.add(label); other.discard(label)
        self.examples_changed()

    def clear_examples(self):
        self.negative_ids.clear(); self.positive_ids.clear(); self.examples_changed()

    def examples_changed(self):
        self.proposal = None; self.invalidate_review()
        self.examples.setText(f"Negative examples: {sorted(self.negative_ids) or 'none'}    Positive examples: {sorted(self.positive_ids) or 'none'}")

    def suggest(self):
        if self.analysis is None: raise ValueError("Load the selected marker first.")
        analysis = self.analysis
        kwargs = dict(method=self.method.currentData(), negative_ids=sorted(self.negative_ids),
            positive_ids=sorted(self.positive_ids), percentile=self.percentile.value())
        self.invalidate_review()
        self.start(lambda cancel: propose_calibration(analysis, cancel=cancel, **kwargs), self.proposal_ready)

    def proposal_ready(self, proposal):
        self.fields["low"].setText(str(proposal["low"]))
        self.fields["high"].setText("" if proposal.get("high") is None else str(proposal["high"]))
        self.proposal = proposal
        self.status.setText("Starting threshold proposed. Adjust as needed, then preview. Nothing has been accepted.")

    def settings(self):
        return {key: (None if key == "high" and not field.text().strip() else float(field.text() or (0 if key == "uncertainty_margin" else "nan")))
            for key, field in self.fields.items()}

    def preview(self):
        if self.analysis is None: raise ValueError("Load the selected marker first.")
        analysis, settings = self.analysis, self.settings()
        negative, positive = sorted(self.negative_ids), sorted(self.positive_ids)
        self.invalidate_review()
        def work(cancel):
            frame = preview_calibration(analysis, negative_ids=negative, positive_ids=positive, cancel=cancel, **settings)
            # Materialize visualization arrays in worker, never from mutable viewer layers.
            codes = {"positive": 1, "negative": 2, "uncertain": 3, "missing": 4}
            from cellquant.plugin.coexpression import call_overlay
            calls = call_overlay(analysis.labels.data, frame.label.to_numpy(), frame.call.map(codes).to_numpy())
            fluorescence = np.array(analysis.image.data[..., self.marker["channel"]], copy=True)
            return frame, settings, fluorescence, calls
        self.start(work, self.preview_ready)

    def preview_ready(self, payload):
        frame, settings, fluorescence, calls = payload
        self.review = (frame, settings, self.revision)
        self.table.setColumnCount(len(frame.columns)); self.table.setHorizontalHeaderLabels(list(frame.columns))
        self.table.setRowCount(len(frame))
        for r, values in enumerate(frame.itertuples(index=False, name=None)):
            for c, value in enumerate(values): self.table.setItem(r, c, Q.QTableWidgetItem(str(value)))
        from cellquant.plugin.coexpression import configure_readable_table
        configure_readable_table(self.table)
        for layer in list(self.layers):
            if layer in self.viewer.layers: self.viewer.layers.remove(layer)
        metadata = {"cellquant_calibration": True}
        base = self.viewer.add_image(fluorescence, name=f"Calibration {self.marker['name']} · analysis fluorescence",
            scale=self.analysis.image.spacing_um, metadata=metadata, colormap="gray")
        from napari.utils.colormaps import DirectLabelColormap
        overlay = self.viewer.add_labels(calls, name=f"Calibration {self.marker['name']} · calls", scale=self.analysis.labels.spacing_um,
            metadata=metadata, opacity=0.45, colormap=DirectLabelColormap(color_dict={None:"transparent",0:"transparent",1:"green",2:"blue",3:"yellow",4:"magenta"}))
        overlay.editable = False
        self.layers = [base, overlay]
        self.accept_button.setEnabled(True)
        self.status.setText("Review fractions, example disagreements and linked fluorescence. Accept only if these settings are appropriate.")

    def navigate(self, row, _column):
        if self.review is None: return
        record = self.review[0].iloc[row]
        for axis, key in enumerate(("centroid_z", "centroid_y", "centroid_x")):
            if key in record: self.viewer.dims.set_point(axis + self.viewer.dims.ndim - 3, float(record[key]) * self.analysis.labels.spacing_um[axis])
        layer = self.selected_labels() if self.selected_labels else None
        if layer is not None: layer.selected_label = int(record["label"])

    def accept_review(self):
        if self.source_check is not None: self.source_check()
        if self.closed: raise ValueError("Calibration panel is closed; reopen it to review.")
        if self.review is None or self.review[2] != self.revision: raise ValueError("Preview current settings before accepting.")
        settings = self.review[1]
        proposal = deepcopy(self.proposal)
        if proposal is None:
            proposal = {"method": "manual", "settings": {}, "low": settings["low"], "high": settings["high"],
                        "negative_ids": sorted(self.negative_ids), "positive_ids": sorted(self.positive_ids),
                        "evidence_fingerprint": self.analysis.evidence["fingerprint"]}
        record = calibration_record(self.analysis, proposal, **settings)
        marker = deepcopy(self.marker); marker.update(settings); marker["calibration"] = record
        self.on_accept(marker)
        self.accept_button.setEnabled(False)
        self.status.setText("Reviewed settings accepted into the marker row. Evidence is retained with the recipe.")

    def closeEvent(self, event):
        self.closed = True; self.cancel(); self.timer.stop()
        self.pool.shutdown(wait=False, cancel_futures=True)
        for layer in self.layers: layer.visible = False
        super().closeEvent(event)
