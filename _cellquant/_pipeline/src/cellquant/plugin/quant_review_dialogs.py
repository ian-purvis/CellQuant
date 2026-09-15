"""Dialogs for quantification review open/save flows."""

from __future__ import annotations

from pathlib import Path

from qtpy import QtCore, QtWidgets as Q

from cellquant.classify.discover_classify import AnalysisCandidate, discover_classification_analyses


_RECENT_KEY = "cellquant/review_quantification/recent_paths"
_MAX_RECENT = 12


def load_recent_paths(settings: Q.QSettings | None = None) -> list[str]:
    settings = settings or Q.QSettings("CellQuant", "CellQuant")
    raw = settings.value(_RECENT_KEY, [])
    if isinstance(raw, str):
        return [raw] if raw else []
    return [str(p) for p in (raw or []) if str(p).strip()]


def remember_path(path: str | Path, settings: Q.QSettings | None = None) -> None:
    settings = settings or Q.QSettings("CellQuant", "CellQuant")
    path = str(Path(path))
    recent = [p for p in load_recent_paths(settings) if p != path]
    recent.insert(0, path)
    settings.setValue(_RECENT_KEY, recent[:_MAX_RECENT])


class OpenAnalysisDialog(Q.QDialog):
    """Choose an outer folder or known analysis; supports recent Locate."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Review quantification")
        self.setMinimumWidth(520)
        self.selected: AnalysisCandidate | None = None
        self._candidates: list[AnalysisCandidate] = []
        layout = Q.QVBoxLayout(self)
        help_text = Q.QLabel(
            "Open an outer analysis folder or a completed classify_* run. "
            "If several analyses are found, choose one explicitly."
        )
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        self.status = Q.QLabel("Choose a folder to search.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.search = Q.QLineEdit()
        self.search.setPlaceholderText("Filter by sample, marker, or path…")
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)
        self.list = Q.QListWidget()
        self.list.currentRowChanged.connect(self._selection_changed)
        self.list.itemDoubleClicked.connect(lambda *_: self._accept_current())
        layout.addWidget(self.list)
        self.details = Q.QLabel("Details: none selected.")
        self.details.setWordWrap(True)
        layout.addWidget(self.details)
        recent_box = Q.QGroupBox("Recent analyses")
        recent_layout = Q.QVBoxLayout(recent_box)
        self.recent = Q.QListWidget()
        self.recent.setMaximumHeight(110)
        recent_layout.addWidget(self.recent)
        layout.addWidget(recent_box)
        self._populate_recent()
        buttons = Q.QHBoxLayout()
        layout.addLayout(buttons)
        open_btn = Q.QPushButton("Open analysis folder…")
        open_btn.clicked.connect(self._choose_folder)
        buttons.addWidget(open_btn)
        locate_btn = Q.QPushButton("Locate selected recent…")
        locate_btn.clicked.connect(self._locate_recent)
        buttons.addWidget(locate_btn)
        buttons.addStretch(1)
        cancel = Q.QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.open_selected = Q.QPushButton("Open selected")
        self.open_selected.setEnabled(False)
        self.open_selected.clicked.connect(self._accept_current)
        self.open_selected.setDefault(True)
        buttons.addWidget(self.open_selected)

    def _populate_recent(self):
        self.recent.clear()
        for path in load_recent_paths():
            exists = Path(path).exists()
            label = path if exists else f"{path} (unavailable — Locate)"
            item = Q.QListWidgetItem(label)
            item.setData(QtCore.Qt.UserRole, path)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsEnabled)
            self.recent.addItem(item)

    def _choose_folder(self):
        path = Q.QFileDialog.getExistingDirectory(self, "Open analysis folder")
        if path:
            self.scan_folder(path)

    def _locate_recent(self):
        item = self.recent.currentItem()
        if item is None:
            self.status.setText("Select a recent path to locate.")
            return
        path = Q.QFileDialog.getExistingDirectory(self, "Locate analysis folder")
        if not path:
            return
        remember_path(path)
        self._populate_recent()
        self.scan_folder(path)

    def scan_folder(self, path: str):
        self.status.setText(f"Searching {path}…")
        Q.QApplication.processEvents()
        try:
            candidates = list(discover_classification_analyses(path))
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"Could not search folder: {exc}")
            return
        remember_path(path)
        self._populate_recent()
        self._candidates = candidates
        available = [c for c in candidates if c.kind == "classification" and c.status in ("available", "missing_dependencies")]
        if len(available) == 1 and len([c for c in candidates if c.kind == "classification"]) == 1:
            self.selected = available[0]
            self.accept()
            return
        self._filter()
        if not candidates:
            self.status.setText("No classification or segmentation analyses found.")
        else:
            self.status.setText(f"Found {len(candidates)} candidate(s). Select one to open.")

    def _filter(self, *_):
        needle = self.search.text().strip().lower()
        self.list.clear()
        for candidate in self._candidates:
            text = (
                f"{candidate.kind} · {candidate.status} · {candidate.sample_name} · "
                f"{', '.join(candidate.markers) or 'no markers'} · {candidate.path}"
            )
            if needle and needle not in text.lower():
                continue
            item = Q.QListWidgetItem(text)
            item.setData(QtCore.Qt.UserRole, candidate)
            self.list.addItem(item)
        self.open_selected.setEnabled(self.list.count() > 0)
        if self.list.count() == 1:
            self.list.setCurrentRow(0)

    def _selection_changed(self, row: int):
        item = self.list.item(row) if row >= 0 else None
        candidate = None if item is None else item.data(QtCore.Qt.UserRole)
        self.open_selected.setEnabled(candidate is not None)
        if candidate is None:
            self.details.setText("Details: none selected.")
            return
        details = [
            f"kind={candidate.kind}",
            f"status={candidate.status}",
            f"path={candidate.path}",
            f"created={candidate.created_utc or 'unavailable'}",
            f"mode={candidate.segmentation_mode}",
        ]
        for key, value in (candidate.details or {}).items():
            details.append(f"{key}={value}")
        self.details.setText("Details: " + "; ".join(details))

    def _accept_current(self):
        item = self.list.currentItem()
        if item is None:
            self.status.setText("Select an analysis first.")
            return
        candidate = item.data(QtCore.Qt.UserRole)
        if candidate.kind == "incomplete":
            self.status.setText("That run is incomplete and cannot be opened for review.")
            return
        if candidate.kind == "incompatible":
            self.status.setText("That run is incompatible with this viewer.")
            return
        self.selected = candidate
        self.accept()


class SaveReviewedDialog(Q.QDialog):
    """Persist a parent-linked this-image review version."""

    def __init__(self, parent=None, *, parent_run_id: str, settings_summary: str,
                 preview_ready: bool, population_label: str):
        super().__init__(parent)
        self.setWindowTitle("Save reviewed version")
        self.setMinimumWidth(480)
        self.computation_status = None
        self.note = ""
        self.reviewer = ""
        self.output_root = None
        layout = Q.QVBoxLayout(self)
        layout.addWidget(Q.QLabel(f"Parent version: {parent_run_id}"))
        summary = Q.QLabel(settings_summary)
        summary.setWordWrap(True)
        layout.addWidget(summary)
        layout.addWidget(Q.QLabel(f"Scope: This image only\nPopulation / preview: {population_label}"))
        status = "Preview ready for full-image save." if preview_ready else "Preview out of date or unavailable."
        layout.addWidget(Q.QLabel(f"Computation status: {status}"))
        form = Q.QFormLayout()
        layout.addLayout(form)
        self.note_edit = Q.QLineEdit()
        form.addRow("Optional note", self.note_edit)
        self.reviewer_edit = Q.QLineEdit()
        form.addRow("Reviewer (optional)", self.reviewer_edit)
        buttons = Q.QHBoxLayout()
        layout.addLayout(buttons)
        cancel = Q.QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        buttons.addStretch(1)
        settings_btn = Q.QPushButton("Save settings version")
        settings_btn.clicked.connect(lambda: self._finish("settings_only"))
        buttons.addWidget(settings_btn)
        full_btn = Q.QPushButton("Save and compute full results")
        full_btn.setEnabled(preview_ready)
        full_btn.setToolTip("Requires an up-to-date preview of the draft settings.")
        full_btn.clicked.connect(lambda: self._finish("full_image"))
        buttons.addWidget(full_btn)

    def _finish(self, status: str):
        path = Q.QFileDialog.getExistingDirectory(self, "Choose output folder for reviewed version")
        if not path:
            return
        self.output_root = path
        self.computation_status = status
        self.note = self.note_edit.text()
        self.reviewer = self.reviewer_edit.text()
        self.accept()
