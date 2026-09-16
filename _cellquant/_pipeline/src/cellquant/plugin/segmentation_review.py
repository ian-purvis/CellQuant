"""Segmentation Review/QC: inspect and correct Cellpose masks, then approve.

This mode is independent of Quantification: no markers, thresholds, or
measurements are involved. It owns the review queue, the working mask layer, and
the approve/draft/reject transitions; all durable state goes through
:mod:`cellquant.review`.

Side-by-side comparison uses one camera and one dims slider with the *same*
image arrays translated apart in X. Pan, zoom, Z slice, orientation, channel
selection, and contrast are therefore shared by construction, and enabling the
layout never loads a second source volume.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from qtpy import QtCore, QtWidgets as Q

from cellquant.plugin.display import display_channel_kwargs
from cellquant.contracts import MutableCancellationToken, PipelineCancelled
from cellquant.review import (
    DiscoveredItem,
    DiscoveryError,
    INPUT_POLICIES,
    REVIEW_MODE_LABEL,
    ReviewConflictError,
    ReviewPublishError,
    ReviewSession,
    ReviewWorkspace,
    SessionItem,
    create_import_bundle,
    discover,
    filter_items,
    item_id_for,
    load_review_workspace,
    preflight_table,
    queue_counts,
    save_session,
    validate_label_array,
)
from cellquant.review import persist as review_persist

REFERENCE_PANEL_LABEL = "Original image"
REVIEW_PANEL_LABEL = "Mask review"
ORIGINAL_LABELS_LAYER = "Original Cellpose labels"
WORKING_LABELS_LAYER = "Mask review labels"
PANEL_GAP_FRACTION = 0.05

SCOPES = (
    ("Single image", "single"),
    ("Folder of masks", "folder"),
    ("Segmentation batch", "batch"),
)
LAYOUTS = (("View Side-by-side", "side_by_side"), ("View Overlay", "overlay"))
FILTER_TAGS = ("pending", "draft", "approved", "rejected", "skipped", "blocked")
POLICY_LABELS = {
    "prefer_approved": "Prefer approved (default)",
    "approved_only": "Approved only",
    "original": "Original Cellpose masks",
}


def preferences_path() -> Path:
    return Path.home() / ".cellquant" / "review_preferences.json"


def load_layout_preference(path: Path | None = None) -> str:
    """Remembered layout; first use defaults to Side-by-side."""

    target = path or preferences_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "side_by_side"
    value = str((raw or {}).get("layout") or "")
    return value if value in {"side_by_side", "overlay"} else "side_by_side"


def save_layout_preference(layout: str, path: Path | None = None) -> None:
    target = path or preferences_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        existing: dict[str, Any] = {}
        if target.is_file():
            try:
                loaded = json.loads(target.read_text(encoding="utf-8"))
                existing = dict(loaded) if isinstance(loaded, dict) else {}
            except (OSError, json.JSONDecodeError):
                existing = {}
        existing["layout"] = str(layout)
        target.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        # A remembered preference is a convenience, never a blocker.
        pass


def side_by_side_translate(
    shape_zyx: Sequence[int],
    spacing_um: Sequence[float],
) -> tuple[float, float, float]:
    """X offset that places the mask panel beside the reference panel.

    Both panels keep identical Y/Z placement, so a landmark sits at the same
    in-panel coordinate in each and one camera serves both.
    """

    width = float(int(shape_zyx[2]) * float(spacing_um[2]))
    gap = width * PANEL_GAP_FRACTION
    return (0.0, 0.0, width + gap)


@dataclass(frozen=True)
class QueueEntry:
    """One queue row: discovery metadata plus user inclusion."""

    item: DiscoveredItem
    included: bool = True

    @property
    def identity(self) -> str:
        return self.item.identity


def _find_layer(viewer, name: str):
    for layer in list(getattr(viewer, "layers", []) or []):
        if getattr(layer, "name", None) == name:
            return layer
    return None


class SegmentationReviewPanel(Q.QWidget):
    """Scope → queue → edit → approve, with a Quantification handoff."""

    def __init__(self, viewer, controller, *, preferences: Path | None = None):
        super().__init__()
        self.viewer = viewer
        self.controller = controller
        self._preferences = preferences
        self.entries: list[QueueEntry] = []
        self.index: int = -1
        self.workspace: ReviewWorkspace | None = None
        self._loaded_working: np.ndarray | None = None
        self._working_layer_id: int | None = None
        self._generation = 0
        self._layout_mode = load_layout_preference(preferences)
        self.future = None
        self.cancel_token = MutableCancellationToken()
        self.pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="cellquant-review"
        )
        self._on_loaded = None
        self._session_dir: Path | None = None

        root = Q.QVBoxLayout(self)
        note = Q.QLabel(
            f"{REVIEW_MODE_LABEL}: inspect Cellpose masks over their source images, "
            "correct them, and approve. No markers or thresholds are required. The "
            "original labels.tif is never overwritten."
        )
        note.setWordWrap(True)
        root.addWidget(note)

        root.addWidget(self._build_scope())
        root.addWidget(self._build_queue(), 1)
        root.addWidget(self._build_display())
        root.addWidget(self._build_actions())
        root.addWidget(self._build_handoff())

        self.status = Q.QLabel("Choose a scope and Discover images to review.")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        self.destroyed.connect(lambda: self.pool.shutdown(wait=False, cancel_futures=True))
        self._sync_layout_combo()

    # -- construction ----------------------------------------------------

    def guard(self, fn):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - surface in the status line
            self.status.setText(f"{type(exc).__name__}: {exc}")

    def _buttons(self, layout, actions):
        row = Q.QHBoxLayout()
        layout.addLayout(row)
        made = {}
        for title, fn in actions:
            button = Q.QPushButton(title)
            row.addWidget(button)
            button.clicked.connect(lambda checked=False, f=fn: self.guard(f))
            made[title] = button
        return made

    def _build_scope(self) -> Q.QWidget:
        page = Q.QGroupBox("1. Scope and input")
        layout = Q.QVBoxLayout(page)
        form = Q.QFormLayout()
        layout.addLayout(form)
        self.scope_pick = Q.QComboBox()
        for label, key in SCOPES:
            self.scope_pick.addItem(label, key)
        form.addRow("Scope", self.scope_pick)
        self.input_edit = Q.QLineEdit()
        browse = Q.QPushButton("Browse…")
        browse.clicked.connect(lambda: self.guard(self.choose_input))
        input_row = Q.QHBoxLayout()
        input_row.addWidget(self.input_edit)
        input_row.addWidget(browse)
        form.addRow("Selection", input_row)
        self.recursive = Q.QCheckBox("Include subfolders")
        self.recursive.setChecked(True)
        form.addRow("", self.recursive)
        self.output_edit = Q.QLineEdit()
        out_browse = Q.QPushButton("Browse…")
        out_browse.clicked.connect(lambda: self.guard(self.choose_output))
        out_row = Q.QHBoxLayout()
        out_row.addWidget(self.output_edit)
        out_row.addWidget(out_browse)
        form.addRow("Imported-mask output", out_row)
        self._buttons(layout, [("Discover", self.discover), ("Refresh", self.refresh_queue)])
        return page

    def _build_queue(self) -> Q.QWidget:
        page = Q.QGroupBox("2. Review queue")
        layout = Q.QVBoxLayout(page)
        filters = Q.QHBoxLayout()
        layout.addLayout(filters)
        filters.addWidget(Q.QLabel("Show"))
        self.filters: dict[str, Q.QCheckBox] = {}
        for tag in FILTER_TAGS:
            box = Q.QCheckBox(tag)
            box.setChecked(True)
            box.toggled.connect(lambda *_: self.guard(self._populate_queue))
            filters.addWidget(box)
            self.filters[tag] = box
        self.queue = Q.QTreeWidget()
        self.queue.setHeaderLabels(
            ["Include", "Image", "Review", "Queue", "Rev", "Detail"]
        )
        self.queue.setColumnWidth(1, 260)
        self.queue.itemChanged.connect(self._on_queue_item_changed)
        self.queue.itemDoubleClicked.connect(
            lambda item, _col: self.guard(lambda: self._open_identity(item.data(0, QtCore.Qt.UserRole)))
        )
        layout.addWidget(self.queue)
        self.counts = Q.QLabel("No queue yet.")
        self.counts.setWordWrap(True)
        layout.addWidget(self.counts)
        return page

    def _build_display(self) -> Q.QWidget:
        page = Q.QGroupBox("3. Display")
        layout = Q.QVBoxLayout(page)
        form = Q.QFormLayout()
        layout.addLayout(form)
        self.layout_pick = Q.QComboBox()
        for label, key in LAYOUTS:
            self.layout_pick.addItem(label, key)
        self.layout_pick.currentIndexChanged.connect(
            lambda *_: self.guard(self._on_layout_changed)
        )
        form.addRow("View", self.layout_pick)
        self.labels_only = Q.QCheckBox("Hide source in the mask panel (labels only)")
        self.labels_only.toggled.connect(lambda *_: self.guard(self._apply_layout))
        form.addRow("", self.labels_only)
        self.show_original_labels = Q.QCheckBox("Show original Cellpose labels for comparison")
        self.show_original_labels.toggled.connect(lambda *_: self.guard(self._apply_layout))
        form.addRow("", self.show_original_labels)
        self.mask_opacity = Q.QSlider(QtCore.Qt.Horizontal)
        self.mask_opacity.setRange(10, 100)
        self.mask_opacity.setValue(70)
        self.mask_opacity.valueChanged.connect(lambda *_: self.guard(self._apply_opacity))
        form.addRow("Mask opacity", self.mask_opacity)
        return page

    def _build_actions(self) -> Q.QWidget:
        page = Q.QGroupBox("4. Edit and approve")
        layout = Q.QVBoxLayout(page)
        hint = Q.QLabel(
            "Edit the working mask in napari: paint a new object with an unused "
            "positive ID, paint or erase an existing object, delete or merge whole "
            "objects, or split one by painting a fresh ID. Brush edits affect the "
            "current Z plane; delete-object and merge affect the selected IDs "
            "throughout the volume. Undo/redo use napari's Labels history."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self._buttons(
            layout,
            [
                ("Previous", self.previous_item),
                ("Next", self.next_item),
                ("Save draft", self.save_draft),
                ("Approve & Next", self.approve_and_next),
                ("Skip for now", self.skip_for_now),
                ("Reject", self.reject),
            ],
        )
        self._buttons(
            layout,
            [
                ("Reset to original", self.reset_to_original),
                ("Discard unsaved changes", self.discard_unsaved),
                ("Discard saved draft", self.discard_saved_draft),
                ("Confirm legacy reviewed mask", self.confirm_legacy),
            ],
        )
        self.empty_ack = Q.QCheckBox("I acknowledge an all-background (empty) mask")
        layout.addWidget(self.empty_ack)
        self.note_edit = Q.QLineEdit()
        self.note_edit.setPlaceholderText("Optional note for this image")
        layout.addWidget(self.note_edit)
        self.identity = Q.QLabel("No image opened for review.")
        self.identity.setWordWrap(True)
        layout.addWidget(self.identity)
        return page

    def _build_handoff(self) -> Q.QWidget:
        page = Q.QGroupBox("5. Quantification handoff")
        layout = Q.QVBoxLayout(page)
        form = Q.QFormLayout()
        layout.addLayout(form)
        self.policy_pick = Q.QComboBox()
        for key in INPUT_POLICIES:
            self.policy_pick.addItem(POLICY_LABELS[key], key)
        self.policy_pick.setCurrentIndex(self.policy_pick.findData("approved_only"))
        form.addRow("Quantification input policy", self.policy_pick)
        self._buttons(
            layout,
            [
                ("Open in Quantification", self.open_in_quantification),
                ("Quantify approved images", self.quantify_approved),
                ("Show preflight", self.show_preflight),
            ],
        )
        self.handoff_status = Q.QLabel("Approve images, then hand them to Quantification.")
        self.handoff_status.setWordWrap(True)
        layout.addWidget(self.handoff_status)
        return page

    # -- scope and discovery ---------------------------------------------

    def choose_input(self):
        scope = self.scope_pick.currentData()
        if scope == "single":
            path, _ = Q.QFileDialog.getOpenFileName(
                self, "Choose a label TIFF (or a run's labels.tif)", "", "TIFF (*.tif *.tiff)"
            )
            if not path:
                path = Q.QFileDialog.getExistingDirectory(self, "Choose a .cellquant run")
        elif scope == "folder":
            path = Q.QFileDialog.getExistingDirectory(self, "Choose a folder of masks/runs")
        else:
            path, _ = Q.QFileDialog.getOpenFileName(
                self, "Choose batch_summary.json", "", "JSON (*.json)"
            )
            if not path:
                path = Q.QFileDialog.getExistingDirectory(self, "Choose the batch folder")
        if path:
            self.input_edit.setText(path)

    def choose_output(self):
        path = Q.QFileDialog.getExistingDirectory(self, "Output root for imported masks")
        if path:
            self.output_edit.setText(path)

    def discover(self):
        """Discover queue membership without opening any source volume."""

        selection = self.input_edit.text().strip()
        if not selection:
            raise DiscoveryError("Choose an input for the selected scope first.")
        scope = self.scope_pick.currentData() or "single"
        items = discover(scope, selection, recursive=self.recursive.isChecked())
        self.entries = [QueueEntry(item) for item in items]
        self.index = -1
        self._populate_queue()
        counts = queue_counts(items)
        needs_pairing = sum(1 for item in items if item.needs_source)
        extra = (
            f" {needs_pairing} standalone mask(s) need a source image before approval."
            if needs_pairing
            else ""
        )
        self.status.setText(
            f"Discovered {counts['total']} item(s) for {scope} review.{extra}"
        )
        self._persist_session(scope, selection)

    def refresh_queue(self):
        """Re-read review state for the current membership (explicit action)."""

        if not self.entries:
            raise DiscoveryError("Discover a queue first.")
        refreshed: list[QueueEntry] = []
        for entry in self.entries:
            item = entry.item
            if item.run_dir is not None:
                from cellquant.review.discovery import discover_single

                try:
                    item = discover_single(item.run_dir)
                except DiscoveryError:
                    pass
            refreshed.append(QueueEntry(item, entry.included))
        self.entries = refreshed
        self._populate_queue()
        self.status.setText("Queue refreshed from each run's review.json.")

    def visible_entries(self) -> tuple[QueueEntry, ...]:
        wanted = {tag for tag, box in self.filters.items() if box.isChecked()}
        if len(wanted) == len(FILTER_TAGS):
            return tuple(self.entries)
        kept = filter_items([entry.item for entry in self.entries], statuses=wanted)
        keep = {item.identity for item in kept}
        return tuple(entry for entry in self.entries if entry.identity in keep)

    def _populate_queue(self):
        self.queue.blockSignals(True)
        self.queue.clear()
        for entry in self.visible_entries():
            item = entry.item
            detail = item.blocked_reason or ""
            if not detail and item.needs_source:
                detail = "source image and grid not resolved"
            if not detail and item.needs_confirmation:
                detail = "legacy reviewed mask awaiting confirmation"
            if not detail and item.batch_status:
                detail = f"batch {item.batch_status}"
            row = Q.QTreeWidgetItem(
                [
                    "",
                    item.name,
                    item.review_status,
                    item.queue_status,
                    str(item.revision),
                    detail,
                ]
            )
            row.setFlags(row.flags() | QtCore.Qt.ItemIsUserCheckable)
            row.setCheckState(
                0, QtCore.Qt.Checked if entry.included else QtCore.Qt.Unchecked
            )
            row.setData(0, QtCore.Qt.UserRole, entry.identity)
            self.queue.addTopLevelItem(row)
        self.queue.blockSignals(False)
        counts = queue_counts([entry.item for entry in self.entries])
        self.counts.setText(
            "Approved {approved} · pending {pending} · draft {draft} · "
            "rejected {rejected} · blocked {blocked} · skipped {skipped} "
            "(skips can overlap approvals) of {total}".format(**counts)
        )

    def _on_queue_item_changed(self, row, column):
        if column != 0:
            return
        identity = row.data(0, QtCore.Qt.UserRole)
        included = row.checkState(0) == QtCore.Qt.Checked
        self.entries = [
            QueueEntry(entry.item, included if entry.identity == identity else entry.included)
            for entry in self.entries
        ]

    def _persist_session(self, scope: str, selection: str):
        directory = self._session_directory(selection)
        if directory is None:
            return
        session = ReviewSession(
            scope=scope,
            selection=selection,
            items=tuple(
                SessionItem(
                    item_id=item_id_for(entry.item.path),
                    path=str(entry.item.path),
                    source=entry.item.source,
                    excluded=not entry.included,
                    blocked_reason=entry.item.blocked_reason,
                )
                for entry in self.entries
            ),
            active_item_id=None,
            layout=self._layout_mode,
            recursive=self.recursive.isChecked(),
            discovery_snapshot=queue_counts([entry.item for entry in self.entries]),
        )
        try:
            self._session_dir = directory
            save_session(directory, session)
        except OSError:
            self._session_dir = None

    def _session_directory(self, selection: str) -> Path | None:
        override = self.output_edit.text().strip()
        if override:
            return Path(override)
        candidate = Path(selection)
        directory = candidate if candidate.is_dir() else candidate.parent
        return directory if directory.is_dir() else None

    # -- opening ---------------------------------------------------------

    def _entry_for(self, identity: str) -> QueueEntry | None:
        for entry in self.entries:
            if entry.identity == identity:
                return entry
        return None

    def _open_identity(self, identity: str | None):
        if not identity:
            return
        for position, entry in enumerate(self.entries):
            if entry.identity == identity:
                self.open_index(position)
                return

    def open_index(self, position: int, *, prompt: bool = True):
        """Open queue position ``position``, protecting unsaved edits first."""

        if not 0 <= position < len(self.entries):
            raise IndexError("queue position is out of range")
        if prompt and not self._resolve_unsaved("opening another image"):
            return
        entry = self.entries[position]
        item = entry.item
        if item.blocked_reason:
            self.index = position
            self.identity.setText(
                f"{item.name} cannot be reviewed: {item.blocked_reason}"
            )
            return
        run_dir = item.run_dir
        if run_dir is None:
            raise DiscoveryError(
                f"{item.name} is a standalone mask. Import it with a confirmed source "
                "image and grid before reviewing (Import standalone mask)."
            )
        self.index = position
        self._generation += 1
        generation = self._generation
        self.workspace = None
        self._loaded_working = None
        self._working_layer_id = None
        self.identity.setText(f"Opening {item.name}… ({position + 1}/{len(self.entries)})")

        def work(cancel):
            return load_review_workspace(run_dir, generation=generation, cancel=cancel)

        def completed(workspace: ReviewWorkspace):
            if workspace.generation != self._generation:
                # A newer selection superseded this load; never bind the old one.
                self.status.setText("Stale review load discarded.")
                return
            self.workspace = workspace
            self._publish_workspace(workspace)
            self.note_edit.setText(workspace.record.note if workspace.record else "")
            self._refresh_identity()

        self.cancel_token = MutableCancellationToken()
        self.future = self.pool.submit(work, self.cancel_token)
        self._on_loaded = completed

    def poll(self):
        future = self.future
        if future is None or not future.done():
            return
        self.future = None
        callback, self._on_loaded = self._on_loaded, None
        try:
            payload = future.result()
        except PipelineCancelled:
            self.status.setText("Review load cancelled.")
            return
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"{type(exc).__name__}: {exc}")
            return
        if callback is not None:
            self.guard(lambda: callback(payload))

    def cancel(self):
        self.cancel_token.cancel()
        self.status.setText("Cancellation requested for the current review load.")

    # -- layers ----------------------------------------------------------

    def _publish_workspace(self, workspace: ReviewWorkspace):
        """Add the reference panel, mask panel, and both label layers."""

        self._remove_review_layers()
        analysis = workspace.analysis
        metadata = {
            **dict(analysis.metadata),
            "source": str(analysis.source),
            "spacing_um": tuple(analysis.spacing_um),
            "channel_names": tuple(analysis.channel_names),
            "canonical_axes": "ZYXC",
        }
        for index, channel_name in enumerate(analysis.channel_names):
            # One array, two layers: the reference panel never loads its own copy.
            channel_data = analysis.data[..., index]
            shared = display_channel_kwargs(
                metadata,
                channel_index=index,
                channel_name=channel_name,
                channel_names=analysis.channel_names,
                spacing_um=analysis.spacing_um,
                source=str(analysis.source),
                channel_data=channel_data,
                visible=True,
            )
            for panel in (REFERENCE_PANEL_LABEL, REVIEW_PANEL_LABEL):
                kwargs = dict(shared)
                kwargs["name"] = f"{panel} · {channel_name}"
                kwargs["metadata"] = {
                    **dict(shared["metadata"]),
                    "cellquant_review_panel": panel,
                }
                self.viewer.add_image(channel_data, **kwargs)
        self.viewer.add_labels(
            np.array(workspace.original_labels, copy=True),
            name=ORIGINAL_LABELS_LAYER,
            scale=workspace.spacing_um,
            visible=self.show_original_labels.isChecked(),
            metadata={
                "cellquant_review_panel": REVIEW_PANEL_LABEL,
                "cellquant_review_readonly": True,
                "source_run": str(workspace.run_dir),
            },
        )
        working = np.array(workspace.working_labels.data, copy=True)
        layer = self.viewer.add_labels(
            working,
            name=WORKING_LABELS_LAYER,
            scale=workspace.spacing_um,
            metadata={
                "cellquant_review_panel": REVIEW_PANEL_LABEL,
                **dict(workspace.working_labels.provenance),
            },
        )
        self._loaded_working = np.array(working, copy=True)
        self._working_layer_id = id(layer)
        self._apply_layout()
        self._apply_opacity()

    def _remove_review_layers(self):
        for layer in list(getattr(self.viewer, "layers", []) or []):
            metadata = dict(getattr(layer, "metadata", {}) or {})
            if metadata.get("cellquant_review_panel") or getattr(layer, "name", "") in {
                ORIGINAL_LABELS_LAYER,
                WORKING_LABELS_LAYER,
            }:
                self.viewer.layers.remove(layer)

    def _review_layers(self, panel: str | None = None) -> list[Any]:
        found = []
        for layer in list(getattr(self.viewer, "layers", []) or []):
            metadata = dict(getattr(layer, "metadata", {}) or {})
            value = metadata.get("cellquant_review_panel")
            if value and (panel is None or value == panel):
                found.append(layer)
        return found

    def working_layer(self):
        return _find_layer(self.viewer, WORKING_LABELS_LAYER)

    def _on_layout_changed(self):
        value = self.layout_pick.currentData() or "side_by_side"
        if value == self._layout_mode:
            return
        self._layout_mode = value
        save_layout_preference(value, self._preferences)
        # Switching layouts only moves layers: edits, undo history, active label,
        # slice, and review state are untouched and no volume is reloaded.
        self._apply_layout()
        self.status.setText(f"View: {'Side-by-side' if value == 'side_by_side' else 'Overlay'}.")

    def _sync_layout_combo(self):
        index = self.layout_pick.findData(self._layout_mode)
        if index >= 0:
            self.layout_pick.blockSignals(True)
            self.layout_pick.setCurrentIndex(index)
            self.layout_pick.blockSignals(False)

    @property
    def layout_mode(self) -> str:
        return self._layout_mode

    def _apply_layout(self):
        workspace = self.workspace
        if workspace is None:
            return
        side_by_side = self._layout_mode == "side_by_side"
        offset = (
            side_by_side_translate(workspace.shape, workspace.spacing_um)
            if side_by_side
            else (0.0, 0.0, 0.0)
        )
        for layer in self._review_layers(REFERENCE_PANEL_LABEL):
            # The reference panel stays unmasked so boundaries cannot hide it.
            self._set(layer, "translate", (0.0, 0.0, 0.0))
            self._set(layer, "visible", side_by_side)
        for layer in self._review_layers(REVIEW_PANEL_LABEL):
            self._set(layer, "translate", offset)
            name = getattr(layer, "name", "")
            if name == WORKING_LABELS_LAYER:
                self._set(layer, "visible", True)
            elif name == ORIGINAL_LABELS_LAYER:
                self._set(layer, "visible", self.show_original_labels.isChecked())
            else:
                self._set(layer, "visible", not self.labels_only.isChecked())
        working = self.working_layer()
        if working is not None:
            selection = getattr(self.viewer, "layers", None)
            select = getattr(selection, "selection", None)
            if select is not None:
                try:
                    select.active = working
                except Exception:  # noqa: BLE001 - selection is a convenience
                    pass

    def _apply_opacity(self):
        value = self.mask_opacity.value() / 100.0
        for name in (WORKING_LABELS_LAYER, ORIGINAL_LABELS_LAYER):
            layer = _find_layer(self.viewer, name)
            if layer is not None:
                self._set(layer, "opacity", value)

    @staticmethod
    def _set(layer, attribute: str, value):
        try:
            setattr(layer, attribute, value)
        except Exception:  # noqa: BLE001 - fake viewers in tests, read-only props
            pass

    # -- dirty state -----------------------------------------------------

    @property
    def is_dirty(self) -> bool:
        layer = self.working_layer()
        if layer is None or self._loaded_working is None:
            return False
        try:
            current = np.asarray(layer.data)
        except Exception:  # noqa: BLE001
            return False
        if current.shape != self._loaded_working.shape:
            return True
        return not np.array_equal(current, self._loaded_working)

    def _resolve_unsaved(self, action: str) -> bool:
        """Offer Save draft / Discard / Cancel. Never silently saves or approves."""

        if not self.is_dirty:
            return True
        box = Q.QMessageBox(self)
        box.setIcon(Q.QMessageBox.Warning)
        box.setWindowTitle("Unsaved mask edits")
        box.setText(
            f"This image has unsaved mask edits. Save a draft before {action}, "
            "discard the edits, or cancel."
        )
        save = box.addButton("Save draft", Q.QMessageBox.AcceptRole)
        discard = box.addButton("Discard unsaved changes", Q.QMessageBox.DestructiveRole)
        box.addButton("Cancel", Q.QMessageBox.RejectRole)
        box.exec_()
        clicked = box.clickedButton()
        if clicked is save:
            self.save_draft()
            return True
        if clicked is discard:
            self.discard_unsaved()
            return True
        return False

    def discard_unsaved(self):
        """Restore the in-memory mask to the last loaded/saved state."""

        layer = self.working_layer()
        if layer is None or self._loaded_working is None:
            raise ValueError("No review session is open.")
        layer.data = np.array(self._loaded_working, copy=True)
        self.status.setText("Unsaved edits discarded.")
        self._refresh_identity()

    def reset_to_original(self):
        workspace = self.workspace
        layer = self.working_layer()
        if workspace is None or layer is None:
            raise ValueError("Open an image for review first.")
        if self.is_dirty:
            confirm = Q.QMessageBox.question(
                self,
                "Reset to original",
                "Resetting discards the unsaved edits in this image. Continue?",
                Q.QMessageBox.Yes | Q.QMessageBox.No,
                Q.QMessageBox.No,
            )
            if confirm != Q.QMessageBox.Yes:
                return
        layer.data = np.array(workspace.original_labels, copy=True)
        self.status.setText(
            "Working mask reset to the original Cellpose labels (not yet saved)."
        )
        self._refresh_identity()

    # -- queue actions ---------------------------------------------------

    def _require_bound_layer(self) -> tuple[ReviewWorkspace, np.ndarray]:
        workspace = self.workspace
        if workspace is None:
            raise ValueError("Open an image for review first.")
        layer = self.working_layer()
        if layer is None:
            raise ValueError(f"{WORKING_LABELS_LAYER} layer not found; save is disabled.")
        if self._working_layer_id is not None and id(layer) != self._working_layer_id:
            raise ValueError(
                "The working labels layer was replaced. Re-open the image before saving."
            )
        metadata = dict(getattr(layer, "metadata", {}) or {})
        bound = str(metadata.get("source_run") or "")
        if not bound or Path(bound).resolve() != workspace.run_dir.resolve():
            raise ValueError(
                "The labels layer is not bound to the open review run. Re-open the "
                "image before saving."
            )
        data = validate_label_array(layer.data)
        if tuple(data.shape) != workspace.shape:
            raise ValueError(
                f"Label shape {tuple(data.shape)} no longer matches the bound review "
                f"grid {workspace.shape}."
            )
        return workspace, data

    def save_draft(self):
        workspace, data = self._require_bound_layer()
        record = review_persist.save_draft(
            workspace.run_dir,
            data,
            note=self.note_edit.text(),
            bound_run=workspace.run_dir,
            expected_shape=workspace.shape,
            loaded_record=workspace.record,
            spacing_um=workspace.spacing_um,
        )
        self._commit_local_state(record, data)
        self.status.setText(
            f"Draft saved for {workspace.run_dir.name}. Drafts are never used for "
            "Quantification."
        )

    def approve_and_next(self):
        workspace, data = self._require_bound_layer()
        try:
            result = review_persist.publish_approved(
                workspace.run_dir,
                data,
                note=self.note_edit.text(),
                source=str(workspace.source),
                spacing_um=workspace.spacing_um,
                analysis_context_sha256=workspace.analysis_context_sha256,
                bound_run=workspace.run_dir,
                expected_shape=workspace.shape,
                loaded_record=workspace.record,
                acknowledge_empty=self.empty_ack.isChecked(),
            )
        except (ReviewConflictError, ReviewPublishError) as exc:
            # Nothing was committed and the queue must not advance.
            self.status.setText(f"Approve failed: {exc}")
            return
        self._commit_local_state(result.record, data)
        message = (
            f"Approved revision {result.record.revision} for {workspace.run_dir.name} "
            f"({result.record.label_count} object(s))."
        )
        if result.warnings:
            message = f"{message} {' '.join(result.warnings)}"
        self.status.setText(message)
        self.next_item(prompt=False)

    def skip_for_now(self):
        workspace = self.workspace
        if workspace is None:
            raise ValueError("Open an image for review first.")
        if self.is_dirty and not self._resolve_unsaved("skipping this image"):
            return
        record = review_persist.set_queue_status(
            workspace.run_dir, "skipped", loaded_record=self.workspace.record
        )
        self._commit_local_state(record, None)
        self.status.setText(
            f"{workspace.run_dir.name} skipped. Skipping changes only queue status; "
            "drafts and approval are preserved."
        )
        self.next_item(prompt=False)

    def reject(self):
        workspace = self.workspace
        if workspace is None:
            raise ValueError("Open an image for review first.")
        reason, accepted = Q.QInputDialog.getText(
            self, "Reject mask", "Reason (required):"
        )
        if not accepted:
            return
        if not str(reason).strip():
            raise ValueError("Reject requires a reason.")
        record = review_persist.reject_review(
            workspace.run_dir, reason=str(reason), loaded_record=workspace.record
        )
        self._commit_local_state(record, None)
        self.status.setText(f"{workspace.run_dir.name} rejected: {record.rejection_reason}")

    def discard_saved_draft(self):
        workspace = self.workspace
        if workspace is None:
            raise ValueError("Open an image for review first.")
        record = review_persist.discard_draft(
            workspace.run_dir, restore_approval=True, loaded_record=workspace.record
        )
        self._commit_local_state(record, None)
        self.status.setText(
            f"Saved draft discarded for {workspace.run_dir.name}; review status is now "
            f"{record.review_status}."
        )
        self.open_index(self.index, prompt=False)

    def confirm_legacy(self):
        workspace = self.workspace
        if workspace is None:
            raise ValueError("Open an image for review first.")
        result = review_persist.confirm_legacy_approval(workspace.run_dir)
        self._commit_local_state(result.record, None)
        self.status.setText(
            f"Confirmed the legacy reviewed mask for {workspace.run_dir.name} as "
            f"revision {result.record.revision}."
        )

    def _commit_local_state(self, record, data: np.ndarray | None):
        workspace = self.workspace
        if workspace is not None:
            from dataclasses import replace as _replace

            self.workspace = _replace(workspace, record=record)
        if data is not None:
            self._loaded_working = np.array(data, copy=True)
        identity = None
        if workspace is not None:
            identity = str(workspace.run_dir.resolve()).casefold()
        if identity is not None:
            self.entries = [
                QueueEntry(
                    entry.item
                    if entry.identity != identity
                    else _with_record(entry.item, record),
                    entry.included,
                )
                for entry in self.entries
            ]
        self._populate_queue()
        self._refresh_identity()

    def previous_item(self, prompt: bool = True):
        if self.index <= 0:
            raise IndexError("Already at the first queue item.")
        self.open_index(self.index - 1, prompt=prompt)

    def next_item(self, prompt: bool = True):
        if self.index + 1 >= len(self.entries):
            self.status.setText("End of the review queue.")
            return
        self.open_index(self.index + 1, prompt=prompt)

    def _refresh_identity(self):
        workspace = self.workspace
        if workspace is None:
            self.identity.setText("No image opened for review.")
            return
        record = workspace.record
        status = record.review_status if record is not None else "pending"
        queue_state = record.queue_status if record is not None else "active"
        revision = record.revision if record is not None else 0
        destination = workspace.run_dir / "reviews"
        self.identity.setText(
            f"{workspace.source.name} — run {workspace.run_dir.name} · "
            f"queue {self.index + 1}/{len(self.entries)} · review {status} · "
            f"queue {queue_state} · revision {revision} · "
            f"{'UNSAVED EDITS' if self.is_dirty else 'no unsaved edits'} · "
            f"working mask from {workspace.working_origin} · grid "
            f"{workspace.context_summary} · output {destination}"
        )

    # -- import ----------------------------------------------------------

    def import_standalone(self, mask_path: str | Path, source: str | Path, spacing_um, **kwargs):
        """Create a review-import bundle for a standalone mask and requeue it."""

        bundle = create_import_bundle(
            mask_path,
            source=source,
            spacing_um=spacing_um,
            output_root=self.output_edit.text().strip() or None,
            **kwargs,
        )
        from cellquant.review.discovery import discover_single

        imported = discover_single(bundle.run_dir)
        identity = str(Path(mask_path).resolve()).casefold()
        replaced = False
        entries: list[QueueEntry] = []
        for entry in self.entries:
            if entry.identity == identity:
                entries.append(QueueEntry(imported, entry.included))
                replaced = True
            else:
                entries.append(entry)
        if not replaced:
            entries.append(QueueEntry(imported))
        self.entries = entries
        self._populate_queue()
        self.status.setText(f"Imported {Path(mask_path).name} as {bundle.run_dir.name}.")
        return bundle

    # -- handoff ---------------------------------------------------------

    def _included_run_dirs(self) -> tuple[Path, ...]:
        return tuple(
            entry.item.run_dir
            for entry in self.entries
            if entry.included and entry.item.run_dir is not None
        )

    def show_preflight(self):
        policy = self.policy_pick.currentData() or "approved_only"
        rows = preflight_table(self._included_run_dirs(), policy=policy)
        if not rows:
            raise ValueError("No included runs to preflight.")
        included = [row for row in rows if row.included]
        excluded = [row for row in rows if not row.included]
        lines = [
            f"{row.image}: {row.review_state} → {row.labels_source} (rev {row.revision})"
            for row in included
        ]
        lines += [f"{row.image}: EXCLUDED — {row.reason}" for row in excluded]
        self.handoff_status.setText(
            f"Policy {POLICY_LABELS[policy]} · {len(included)} included, "
            f"{len(excluded)} excluded.\n" + "\n".join(lines)
        )
        return rows

    def open_in_quantification(self):
        """Hand the current approved image to Quantification with its revision pinned."""

        workspace = self.workspace
        if workspace is None:
            raise ValueError("Open an image for review first.")
        record = workspace.record
        if record is None or not record.is_approved:
            raise ValueError(
                f"{workspace.run_dir.name} has no approved revision yet. Approve it, or "
                "quantify original masks from the Quantification mode."
            )
        return self._hand_off((workspace.run_dir,), policy="approved_only")

    def quantify_approved(self):
        from cellquant.review.handoff import approved_run_dirs

        approved = approved_run_dirs([entry.item for entry in self.entries if entry.included])
        if not approved:
            raise ValueError("No approved images in the current queue selection.")
        return self._hand_off(approved, policy="approved_only")

    def _hand_off(self, run_dirs: Sequence[Path], *, policy: str):
        from cellquant.review.handoff import pin_inputs

        pins, rows = pin_inputs(run_dirs, policy=policy)
        index = self.policy_pick.findData(policy)
        if index >= 0:
            self.policy_pick.setCurrentIndex(index)
        panel = getattr(self.controller, "quantification_panel", None)
        accepted = False
        if panel is not None and hasattr(panel, "accept_review_handoff"):
            panel.accept_review_handoff(pins, policy=policy)
            accepted = True
        self.handoff_status.setText(
            f"Pinned {len(pins)} mask revision(s) for Quantification under "
            f"{POLICY_LABELS[policy]}."
            + ("" if accepted else " Open the Coexpression mode to run measurements.")
        )
        return pins, rows

    # -- lifecycle -------------------------------------------------------

    def can_leave(self) -> bool:
        """Called on mode change/close so dirty edits are never lost silently."""

        return self._resolve_unsaved("leaving Segmentation Review/QC")

    def closeEvent(self, event):  # noqa: N802 - Qt naming
        if not self.can_leave():
            event.ignore()
            return
        self.pool.shutdown(wait=False, cancel_futures=True)
        super().closeEvent(event)


def _with_record(item: DiscoveredItem, record) -> DiscoveredItem:
    from dataclasses import replace

    if record is None:
        return item
    return replace(
        item,
        review_status=record.review_status,
        queue_status=record.queue_status,
        revision=record.revision,
        note=record.note,
        needs_confirmation=record.requires_confirmation,
    )


__all__ = [
    "ORIGINAL_LABELS_LAYER",
    "REFERENCE_PANEL_LABEL",
    "REVIEW_PANEL_LABEL",
    "SegmentationReviewPanel",
    "WORKING_LABELS_LAYER",
    "load_layout_preference",
    "save_layout_preference",
    "side_by_side_translate",
]
