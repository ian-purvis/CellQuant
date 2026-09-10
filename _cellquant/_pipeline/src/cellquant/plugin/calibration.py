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
from cellquant.analysis import resolve_measurement_image


def _center_camera(viewer, record, spacing_um, *, context_um=25.0):
    """Center dims and camera on a nucleus centroid with surrounding context."""
    centroids = []
    for axis, key in enumerate(("centroid_z", "centroid_y", "centroid_x")):
        if key not in record:
            return
        world = float(record[key]) * float(spacing_um[axis])
        centroids.append(world)
        viewer.dims.set_point(axis + viewer.dims.ndim - 3, world)
    camera = getattr(viewer, "camera", None)
    if camera is None:
        return
    # Prefer 2D plane centering (Y, X); fall back to full 3D center when available.
    try:
        if getattr(camera, "center", None) is not None:
            if len(getattr(camera, "center", ())) >= 3:
                camera.center = (centroids[0], centroids[1], centroids[2])
            else:
                camera.center = (centroids[1], centroids[2])
        zoom = getattr(camera, "zoom", None)
        if zoom is not None and context_um > 0:
            # Larger context_um → lower zoom. Keep a usable floor/ceiling.
            extent = max(float(spacing_um[1]), float(spacing_um[2])) * 8.0
            target = max(extent, float(context_um) * 2.0)
            camera.zoom = max(0.5, min(40.0, 200.0 / target))
    except Exception:
        # Camera APIs vary by napari version; dims centering alone remains useful.
        pass


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
        self._filtered_rows = []
        self._queue_index = -1
        self.setWindowTitle(f"Review calibration · {marker['name']}")
        self.resize(780, 900)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

        outer = Q.QVBoxLayout(self)
        # Persistent review summary stays reachable while the body scrolls.
        self.summary = Q.QLabel("Load the selected marker to begin.")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("font-weight: 600; padding: 4px 0;")
        outer.addWidget(self.summary)
        sticky = Q.QHBoxLayout()
        outer.addLayout(sticky)
        self.load_button = self.button(sticky, "Load / refresh", self.load_source)
        self.preview_button = self.button(sticky, "Preview", self.preview)
        self.accept_button = self.button(sticky, "Accept reviewed settings", self.accept_review)
        self.accept_button.setEnabled(False)
        self.button(sticky, "Cancel", self.cancel)

        scroll = Q.QScrollArea()
        scroll.setWidgetResizable(True)
        body = Q.QWidget()
        layout = Q.QVBoxLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll, stretch=1)

        title = Q.QLabel(f"Calibrate {marker['name']} · nuclear fluorescence")
        title.setStyleSheet("font-size: 18px; font-weight: bold")
        layout.addWidget(title)
        help_text = Q.QLabel(
            "1. Load the selected marker. 2. Filter the review queue and step Previous/Next. "
            "3. Mark examples, propose or enter thresholds, then Preview/Accept. "
            "Proposals describe intensity distributions; they are not biological truth. "
            "Accepted settings become current evidence for this image; prior recipe evidence is historical."
        )
        help_text.setWordWrap(True)
        layout.addWidget(help_text)

        self.evidence_status = Q.QLabel("Evidence: none loaded (manual thresholds remain editable).")
        self.evidence_status.setWordWrap(True)
        layout.addWidget(self.evidence_status)

        self.figure = Figure(figsize=(6, 2), tight_layout=True)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setMinimumHeight(180)
        layout.addWidget(self.canvas)
        self.axes = self.figure.add_subplot(111)
        self.examples = Q.QLabel("No examples marked.")
        layout.addWidget(self.examples)
        row = Q.QHBoxLayout()
        layout.addLayout(row)
        self.negative_button = self.button(row, "Mark selected nucleus negative", lambda: self.mark_example(False))
        self.positive_button = self.button(row, "Mark selected nucleus positive", lambda: self.mark_example(True))
        self.button(row, "Clear examples", self.clear_examples)
        note = Q.QLabel(
            "Use the labels layer picker in napari (press I), then click a nucleus. "
            "Examples must belong to the selected counting region. Nucleus ID stays visible in the queue."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        form = Q.QFormLayout()
        layout.addLayout(form)
        form.setRowWrapPolicy(Q.QFormLayout.WrapLongRows)
        form.setFieldGrowthPolicy(Q.QFormLayout.AllNonFixedFieldsGrow)
        self.method = Q.QComboBox()
        self.method.setMinimumWidth(220)
        self.method.setSizePolicy(Q.QSizePolicy.Expanding, Q.QSizePolicy.Fixed)
        self.method.addItem("Otsu · pooled in-nucleus pixels", "otsu")
        self.method.addItem("Negative-example pixel percentile", "negative_percentile")
        self.percentile = Q.QDoubleSpinBox()
        self.percentile.setRange(0, 100)
        self.percentile.setValue(99)
        form.addRow("Proposal method", self.method)
        form.addRow("Negative pixel percentile (explicit choice)", self.percentile)
        self.suggest_button = self.button(layout, "Propose starting raw low threshold", self.suggest)
        self.fields = {}
        for key, label, default in [
            ("low", "Raw low (inclusive)", ""),
            ("high", "Raw high (optional, inclusive)", ""),
            ("positive_fraction", "Positive pixel fraction · 0.5 is a starting value", "0.5"),
            ("uncertainty_margin", "Uncertainty margin (fraction units, optional)", "0"),
        ]:
            value = marker.get(key)
            field = Q.QLineEdit(default if value is None else str(value))
            field.setMinimumWidth(180)
            field.setSizePolicy(Q.QSizePolicy.Expanding, Q.QSizePolicy.Fixed)
            self.fields[key] = field
            form.addRow(label, field)
            field.textChanged.connect(self.invalidate_review)
        self.method.currentIndexChanged.connect(self.proposal_changed)
        self.percentile.valueChanged.connect(self.proposal_changed)

        queue = Q.QHBoxLayout()
        layout.addLayout(queue)
        self.filter_mode = Q.QComboBox()
        self.filter_mode.addItem("All eligible nuclei", "all")
        self.filter_mode.addItem("Uncertain only", "uncertain")
        self.filter_mode.addItem("Example disagreements", "disagreement")
        self.filter_mode.addItem("Near lower boundary", "near_lower")
        self.filter_mode.addItem("Near upper boundary", "near_upper")
        self.filter_mode.currentIndexChanged.connect(self.refresh_queue)
        queue.addWidget(self.filter_mode, stretch=1)
        self.context_um = Q.QDoubleSpinBox()
        self.context_um.setRange(5.0, 500.0)
        self.context_um.setValue(25.0)
        self.context_um.setSuffix(" µm context")
        queue.addWidget(self.context_um)
        self.button(queue, "Previous", self.previous_cell)
        self.button(queue, "Next", self.next_cell)
        self.queue_label = Q.QLabel("Queue empty")
        layout.addWidget(self.queue_label)

        legend = Q.QLabel(
            "Linked analysis-grid layers: green = positive, blue = negative, yellow = uncertain, "
            "magenta = missing; background/excluded = transparent. "
            "Arrow keys or Previous/Next walk the filtered queue; click a row to jump."
        )
        legend.setWordWrap(True)
        layout.addWidget(legend)
        self.table = Q.QTableWidget()
        self.table.setMinimumHeight(140)
        self.table.setMinimumWidth(360)
        self.table.setEditTriggers(Q.QAbstractItemView.NoEditTriggers)
        self.table.cellClicked.connect(self.navigate)
        layout.addWidget(self.table)
        from cellquant.plugin.coexpression import configure_readable_table
        configure_readable_table(self.table, resize_to_contents=False)

        self.status = Q.QLabel(
            "Load the selected marker to begin. Previously saved evidence is historical until this review is accepted."
        )
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self._update_summary()

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(30)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        self.destroyed.connect(lambda: self.pool.shutdown(wait=False, cancel_futures=True))

    def button(self, layout, text, fn):
        button = Q.QPushButton(text)
        layout.addWidget(button)
        button.clicked.connect(lambda checked=False: self.guard(fn))
        return button

    def bind_dock(self, dock):
        self.dock = dock
        dock.setMinimumWidth(360)
        self.setMinimumWidth(360)
        dock.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self.dock and event.type() == QtCore.QEvent.Close:
            self.close()
        return super().eventFilter(watched, event)

    def event(self, event):
        if (
            event.type() == QtCore.QEvent.ParentChange
            and getattr(self, "dock", None) is not None
            and self.parentWidget() is None
        ):
            self.close()
            self.dock = None
        return super().event(event)

    def keyPressEvent(self, event):
        key = event.key()
        if key in (QtCore.Qt.Key_Right, QtCore.Qt.Key_Down, QtCore.Qt.Key_N):
            self.guard(self.next_cell)
            event.accept()
            return
        if key in (QtCore.Qt.Key_Left, QtCore.Qt.Key_Up, QtCore.Qt.Key_P):
            self.guard(self.previous_cell)
            event.accept()
            return
        super().keyPressEvent(event)

    def guard(self, fn):
        try:
            return fn()
        except Exception as exc:
            self.status.setText(str(exc))

    def _update_summary(self):
        counts = {"positive": 0, "negative": 0, "uncertain": 0, "missing": 0}
        if self.review is not None:
            frame = self.review[0]
            for call, n in frame["call"].value_counts().items():
                counts[str(call)] = int(n)
        settings = []
        for key in ("low", "high", "positive_fraction", "uncertainty_margin"):
            text = self.fields[key].text().strip()
            if text:
                settings.append(f"{key}={text}")
        self.summary.setText(
            f"{self.marker['name']}: +{counts['positive']} −{counts['negative']} "
            f"?{counts['uncertain']} miss{counts['missing']} · "
            f"{', '.join(settings) or 'no settings'} · "
            f"examples −{len(self.negative_ids)}/+{len(self.positive_ids)}"
        )

    def invalidate_review(self, *_):
        self.revision += 1
        self.review = None
        self.accept_button.setEnabled(False)
        self.table.setRowCount(0)
        self._filtered_rows = []
        self._queue_index = -1
        self.queue_label.setText("Queue empty")
        for layer in self.layers:
            layer.visible = False
        self._update_summary()

    def invalidate_acceptance(self, *_):
        """Invalidate Accept without clearing the review queue or current cell."""

        self.revision += 1
        self.review = None
        self.accept_button.setEnabled(False)
        for layer in self.layers:
            layer.visible = False
        self._update_summary()

    def proposal_changed(self, *_):
        self.proposal = None
        self.invalidate_acceptance()

    def invalidate_source(self, *_):
        self.token.cancel()
        self.analysis = self.proposal = None
        self.invalidate_review()
        self.axes.clear()
        self.canvas.draw_idle()
        self.evidence_status.setText("Evidence: source invalidated — load again before accepting.")
        self.status.setText("Source or marker changed. Load / refresh the selected marker before reviewing again.")
        self._update_summary()

    def start(self, operation, completed):
        if self.future is not None:
            raise ValueError("Wait for the current operation or cancel it first.")
        self.token = MutableCancellationToken()
        self._job_revision = self.revision
        self._completed = completed
        self.future = self.pool.submit(operation, self.token)
        self.status.setText("Working on a source snapshot…")

    def poll(self):
        if self.source_check is not None:
            self.source_check()
        if self.future is None or not self.future.done():
            return
        future, self.future = self.future, None
        try:
            value = future.result()
            self.token.raise_if_cancelled()
            if self.closed or self._job_revision != self.revision:
                return
            self._completed(value)
        except PipelineCancelled:
            self.status.setText("Cancelled. No new review accepted.")
        except Exception as exc:
            self.status.setText(f"Could not calibrate: {exc}")

    def cancel(self):
        self.token.cancel()
        self.invalidate_review()
        self.status.setText("Cancellation requested. No new review accepted.")

    def load_source(self):
        if self.marker.get("channel") is None:
            raise ValueError("Select an acquired marker channel first.")
        if self.future is not None:
            raise ValueError("Wait for the current operation to finish cancelling.")
        image, labels, region, policy, context, declared = self.snapshot()
        self.invalidate_source()
        self.clear_examples()

        def work(cancel):
            analysis_image, _ctx = resolve_measurement_image(
                image, labels, declared=declared, cancel=cancel
            )
            return analyze_calibration(
                analysis_image,
                labels,
                marker_name=self.marker["name"],
                channel=self.marker["channel"],
                region=region,
                region_policy=policy,
                context=context,
                cancel=cancel,
            )

        self.start(work, self.source_ready)

    def source_ready(self, analysis):
        self.analysis = analysis
        self.axes.clear()
        self.axes.stairs(analysis.histogram_counts, analysis.histogram_edges, fill=True, color="#5478a6")
        self.axes.set_xlabel("Raw fluorescence in eligible nuclei")
        self.axes.set_ylabel("Pixels")
        self.canvas.draw_idle()
        self.evidence_status.setText(
            f"Evidence: current image loaded ({int(analysis.evidence.get('finite_eligible_objects', 0))} "
            f"finite eligible nuclei). Recipe-stored calibration remains historical until Accept."
        )
        self.status.setText("Source ready. Pick negative/positive examples or request an Otsu starting point.")
        self._update_summary()

    def mark_example(self, positive):
        if self.analysis is None:
            raise ValueError("Load the selected marker first.")
        layer = self.selected_labels() if self.selected_labels else self.viewer.layers.selection.active
        label = int(getattr(layer, "selected_label", 0))
        if label == 0 or label not in set(self.analysis.objects["label"]):
            raise ValueError("Pick an eligible nucleus on the reviewed nuclei labels layer first.")
        own, other = (self.positive_ids, self.negative_ids) if positive else (self.negative_ids, self.positive_ids)
        own.add(label)
        other.discard(label)
        self.examples_changed()

    def clear_examples(self):
        self.negative_ids.clear()
        self.positive_ids.clear()
        self.examples_changed()

    def examples_changed(self):
        self.proposal = None
        self.invalidate_acceptance()
        self.examples.setText(
            f"Negative examples: {sorted(self.negative_ids) or 'none'}    "
            f"Positive examples: {sorted(self.positive_ids) or 'none'}"
        )
        self._update_summary()

    def suggest(self):
        if self.analysis is None:
            raise ValueError("Load the selected marker first.")
        analysis = self.analysis
        kwargs = dict(
            method=self.method.currentData(),
            negative_ids=sorted(self.negative_ids),
            positive_ids=sorted(self.positive_ids),
            percentile=self.percentile.value(),
        )
        self.invalidate_acceptance()
        self.start(lambda cancel: propose_calibration(analysis, cancel=cancel, **kwargs), self.proposal_ready)

    def proposal_ready(self, proposal):
        self.fields["low"].setText(str(proposal["low"]))
        self.fields["high"].setText("" if proposal.get("high") is None else str(proposal["high"]))
        self.proposal = proposal
        self.status.setText(
            "Starting threshold proposed (not biological truth). Adjust as needed, then preview. Nothing has been accepted."
        )
        self._update_summary()

    def settings(self):
        return {
            key: (
                None
                if key == "high" and not field.text().strip()
                else float(field.text() or (0 if key == "uncertainty_margin" else "nan"))
            )
            for key, field in self.fields.items()
        }

    def preview(self):
        if self.analysis is None:
            raise ValueError("Load the selected marker first.")
        analysis, settings = self.analysis, self.settings()
        negative, positive = sorted(self.negative_ids), sorted(self.positive_ids)
        self.invalidate_review()

        def work(cancel):
            frame = preview_calibration(
                analysis, negative_ids=negative, positive_ids=positive, cancel=cancel, **settings
            )
            codes = {"positive": 1, "negative": 2, "uncertain": 3, "missing": 4}
            from cellquant.plugin.coexpression import call_overlay

            calls = call_overlay(analysis.labels.data, frame.label.to_numpy(), frame.call.map(codes).to_numpy())
            fluorescence = np.array(analysis.image.data[..., self.marker["channel"]], copy=True)
            return frame, settings, fluorescence, calls

        self.start(work, self.preview_ready)

    def preview_ready(self, payload):
        frame, settings, fluorescence, calls = payload
        self.review = (frame, settings, self.revision)
        self.table.setColumnCount(len(frame.columns))
        self.table.setHorizontalHeaderLabels(list(frame.columns))
        self.table.setRowCount(len(frame))
        for r, values in enumerate(frame.itertuples(index=False, name=None)):
            for c, value in enumerate(values):
                self.table.setItem(r, c, Q.QTableWidgetItem(str(value)))
        from cellquant.plugin.coexpression import configure_readable_table

        configure_readable_table(self.table, resize_to_contents=False)
        for layer in list(self.layers):
            if layer in self.viewer.layers:
                self.viewer.layers.remove(layer)
        metadata = {"cellquant_calibration": True}
        base = self.viewer.add_image(
            fluorescence,
            name=f"Calibration {self.marker['name']} · analysis fluorescence",
            scale=self.analysis.image.spacing_um,
            metadata=metadata,
            colormap="gray",
        )
        from napari.utils.colormaps import DirectLabelColormap

        overlay = self.viewer.add_labels(
            calls,
            name=f"Calibration {self.marker['name']} · calls",
            scale=self.analysis.labels.spacing_um,
            metadata=metadata,
            opacity=0.45,
            colormap=DirectLabelColormap(
                color_dict={
                    None: "transparent",
                    0: "transparent",
                    1: "green",
                    2: "blue",
                    3: "yellow",
                    4: "magenta",
                }
            ),
        )
        overlay.editable = False
        self.layers = [base, overlay]
        self.accept_button.setEnabled(True)
        self.refresh_queue()
        self._update_summary()
        self.status.setText(
            "Review fractions, disagreements, and linked fluorescence. Accept only if these settings are appropriate."
        )

    def refresh_queue(self, *_):
        if self.review is None:
            self._filtered_rows = []
            self._queue_index = -1
            self.queue_label.setText("Queue empty")
            return
        frame = self.review[0]
        settings = self.review[1]
        mode = self.filter_mode.currentData()
        cutoff = float(settings.get("positive_fraction") or 0.5)
        margin = float(settings.get("uncertainty_margin") or 0.0)
        lower, upper = cutoff - margin, cutoff + margin
        band = max(0.02, margin if margin > 0 else 0.05)
        self._filtered_rows = [
            i
            for i, record in enumerate(frame.itertuples(index=False))
            if self._row_matches(record, frame.columns, mode, lower, upper, band)
        ]
        self._queue_index = 0 if self._filtered_rows else -1
        self._sync_queue_label()
        if self._queue_index >= 0:
            self.focus_queue_row(self._filtered_rows[self._queue_index])

    def _row_matches(self, record, columns, mode, lower, upper, band):
        data = record._asdict() if hasattr(record, "_asdict") else dict(zip(columns, record))
        call = str(data.get("call", ""))
        frac = data.get("fraction")
        disagreement = bool(data.get("disagreement", False))
        if mode == "uncertain":
            return call == "uncertain"
        if mode == "disagreement":
            return disagreement
        if mode == "near_lower" and frac is not None and frac == frac:
            return abs(float(frac) - lower) <= band
        if mode == "near_upper" and frac is not None and frac == frac:
            return abs(float(frac) - upper) <= band
        return mode == "all"

    def _sync_queue_label(self):
        if not self._filtered_rows:
            self.queue_label.setText("Queue empty")
            return
        row = self._filtered_rows[self._queue_index]
        label = int(self.review[0].iloc[row]["label"])
        self.queue_label.setText(
            f"Queue {self._queue_index + 1}/{len(self._filtered_rows)} · nucleus ID {label}"
        )
        self.table.selectRow(row)

    def focus_queue_row(self, row):
        if self.review is None:
            return
        record = self.review[0].iloc[row]
        _center_camera(
            self.viewer,
            record,
            self.analysis.labels.spacing_um,
            context_um=float(self.context_um.value()),
        )
        layer = self.selected_labels() if self.selected_labels else None
        if layer is not None:
            layer.selected_label = int(record["label"])
        self.table.selectRow(row)
        self._sync_queue_label()

    def previous_cell(self):
        if not self._filtered_rows:
            raise ValueError("Preview settings to build a review queue first.")
        self._queue_index = (self._queue_index - 1) % len(self._filtered_rows)
        self.focus_queue_row(self._filtered_rows[self._queue_index])

    def next_cell(self):
        if not self._filtered_rows:
            raise ValueError("Preview settings to build a review queue first.")
        self._queue_index = (self._queue_index + 1) % len(self._filtered_rows)
        self.focus_queue_row(self._filtered_rows[self._queue_index])

    def navigate(self, row, _column):
        if self.review is None:
            return
        if row in self._filtered_rows:
            self._queue_index = self._filtered_rows.index(row)
        else:
            self._filtered_rows = list(range(len(self.review[0])))
            self._queue_index = row
        self.focus_queue_row(row)

    def accept_review(self):
        if self.source_check is not None:
            self.source_check()
        if self.closed:
            raise ValueError("Calibration panel is closed; reopen it to review.")
        if self.review is None or self.review[2] != self.revision:
            raise ValueError("Preview current settings before accepting.")
        settings = self.review[1]
        proposal = deepcopy(self.proposal)
        if proposal is None:
            proposal = {
                "method": "manual",
                "settings": {},
                "low": settings["low"],
                "high": settings["high"],
                "negative_ids": sorted(self.negative_ids),
                "positive_ids": sorted(self.positive_ids),
                "evidence_fingerprint": self.analysis.evidence["fingerprint"],
            }
        record = calibration_record(self.analysis, proposal, **settings)
        record.setdefault("review", {})
        record["review"]["status"] = "current"
        record["review"]["positive_examples"] = len(self.positive_ids)
        record["review"]["negative_examples"] = len(self.negative_ids)
        record["review"]["source"] = str(self.analysis.image.source)
        marker = deepcopy(self.marker)
        marker.update(settings)
        marker["calibration"] = record
        self.on_accept(marker)
        self.accept_button.setEnabled(False)
        self.evidence_status.setText(
            f"Evidence: accepted as current for this image "
            f"(−{len(self.negative_ids)}/+{len(self.positive_ids)} examples; "
            f"method={proposal.get('method', 'manual')})."
        )
        self.status.setText("Reviewed settings accepted into the marker row. Evidence is retained with the recipe.")
        self._update_summary()

    def closeEvent(self, event):
        self.closed = True
        self.cancel()
        self.timer.stop()
        self.pool.shutdown(wait=False, cancel_futures=True)
        for layer in self.layers:
            layer.visible = False
        super().closeEvent(event)
