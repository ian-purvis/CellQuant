"""CellQuant desktop window.

Napari shows the image, object labels, and classification overlay. Every
analysis action goes through AnalysisController, which calls the same engine
as a batch run.
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

import numpy as np
from qtpy.QtCore import QObject, Qt, QSize, QThread, QTimer, Signal
from qtpy.QtGui import QColor
from qtpy.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QToolButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from cellquant import keep_awake
from cellquant.controller import AnalysisController
from cellquant.count_area import outside
from cellquant.errors import CellQuantError, RecipeValidationError
from cellquant.gui import guide
from napari.utils.colormaps import DirectLabelColormap


def choose_existing_directory(parent: QWidget | None, caption: str, start: str = "") -> str:
    """Return one existing directory, or \"\" if the user cancels.

    On Windows the native folder dialog returns the folder whose contents you are
    looking at, not a child you only highlighted. Clicking E14.5 while still in
    the parent therefore saved the parent. Use Qt's own dialog there so the
    highlighted folder is what is returned.
    """

    dialog = QFileDialog(parent, caption, start)
    dialog.setFileMode(QFileDialog.Directory)
    dialog.setOption(QFileDialog.ShowDirsOnly, True)
    dialog.setOption(QFileDialog.DontResolveSymlinks, True)
    if sys.platform.startswith("win"):
        dialog.setOption(QFileDialog.DontUseNativeDialog, True)
    if not dialog.exec():
        return ""
    highlighted = _highlighted_directory(dialog)
    if highlighted:
        return highlighted
    selected = dialog.selectedFiles()
    if not selected:
        return ""
    return str(Path(selected[0]).expanduser().resolve())


def _highlighted_directory(dialog: QFileDialog) -> str:
    """The one folder highlighted in the dialog's list, or "" when none is.

    Single-clicking a folder and pressing Choose could still return the folder
    being looked at (its parent), so the highlighted folder is read directly.
    """

    directory = Path(dialog.directory().absolutePath())
    for view in (dialog.findChild(QAbstractItemView, "listView"), dialog.findChild(QAbstractItemView, "treeView")):
        if view is None or view.selectionModel() is None:
            continue
        names = {index.data() for index in view.selectionModel().selectedRows(0)}
        if len(names) == 1:
            candidate = directory / str(names.pop())
            if candidate.is_dir():
                return str(candidate.expanduser().resolve())
    return ""


def folder_image_counts(folder: str | Path) -> list[tuple[str, int]]:
    """Images under folder, counted per top-level subfolder ("." for files directly inside)."""

    import os

    from cellquant.experiment import _EXPERIMENT_FOLDERS
    from cellquant.image import IMAGE_SUFFIXES

    root = Path(folder)
    counts: dict[str, int] = {}
    for current, subfolders, names in os.walk(root):
        subfolders[:] = sorted(name for name in subfolders if name not in _EXPERIMENT_FOLDERS)
        found = sum(1 for name in names if Path(name).suffix.lower() in IMAGE_SUFFIXES)
        if not found:
            continue
        relative = Path(current).relative_to(root).parts
        key = relative[0] if relative else "."
        counts[key] = counts.get(key, 0) + found
    return sorted(counts.items())


def launch(experiment_dir: str | Path | None = None) -> None:
    import napari

    viewer = napari.Viewer(title="CellQuant")
    CellQuantWindow(viewer, experiment_dir)
    napari.run()


_OBJECT_LAYERS = ("Object fills", "Objects", "Classification", "Object IDs")
# Polygons drawn in step 5; only objects whose center is inside them are counted.
COUNT_AREA_LAYER = "Count area"

# How a classification compares a value with its threshold (shown text, recipe value).
COMPARISONS = (("above the cutoff (>)", "above"), ("at least the cutoff (≥)", "at_least"))


def channel_colormaps(loaded) -> list:
    """Each channel in the color stored in the file (black to that color); gray when the file has none.

    Channels are never given colors the file does not specify.
    """

    from napari.utils.colormaps import Colormap

    colors = list(loaded.channel_colors or ())
    maps = []
    for index in range(loaded.n_channels):
        color = colors[index] if index < len(colors) else None
        if color is None:
            maps.append("gray")
            continue
        red, green, blue = (float(value) for value in color)
        code = "#{:02x}{:02x}{:02x}".format(*(int(round(value * 255)) for value in (red, green, blue)))
        maps.append(Colormap(colors=[[0.0, 0.0, 0.0, 1.0], [red, green, blue, 1.0]], name=f"file color {code}"))
    return maps


def drawn_polygons(layer) -> list[list[list[float]]]:
    """Closed shapes in a napari Shapes layer as (row, column) polygons. Lines and paths have no area."""

    polygons = []
    for vertices, kind in zip(layer.data, layer.shape_type, strict=False):
        corners = np.asarray(vertices, dtype=float)[:, -2:]
        if kind == "ellipse" and len(corners) == 4:
            # napari stores an ellipse as the four corners of its bounding box.
            angles = np.linspace(0.0, 2.0 * np.pi, 64, endpoint=False)[:, None]
            half_a, half_b = (corners[1] - corners[0]) / 2.0, (corners[2] - corners[1]) / 2.0
            corners = corners.mean(axis=0) + np.cos(angles) * half_a + np.sin(angles) * half_b
        elif kind not in ("polygon", "rectangle"):
            continue
        if len(corners) >= 3:
            polygons.append(corners.tolist())
    return polygons


def format_channel_labels(names, n_channels: int) -> list[str]:
    """Stable labels: 'Channel 1 = Green' or 'Channel 1' when the file has no name."""

    labels = []
    for index in range(n_channels):
        raw = ""
        if names is not None and index < len(names) and names[index]:
            raw = str(names[index]).strip()
        if raw and not re.fullmatch(r"Channel\s+\d+", raw, flags=re.I):
            labels.append(f"Channel {index + 1} = {raw}")
        else:
            labels.append(f"Channel {index + 1}")
    return labels


def format_channel_order_text(names, n_channels: int) -> str:
    return " · ".join(format_channel_labels(names, n_channels))


# Typical mammalian nucleus diameter users can adjust (µm). Area of a circle that wide ≈ 28 µm².
DEFAULT_NUCLEUS_DIAMETER_UM = 6.0
DEFAULT_MIN_AREA_UM2 = 5.0


# Dropdown wording: the saved value first, then the plain words shown for it.
METHOD_OPTIONS = (("classical", "Classical (fast, no GPU)"), ("cellpose", "Cellpose (AI model)"))
THRESHOLD_OPTIONS = (("otsu", "Automatic (Otsu)"), ("manual", "Manual threshold"))
AREA_UNIT_OPTIONS = (("px", "pixels"), ("um2", "µm²"))
REGION_OPTIONS = (
    ("object", "Whole object"),
    ("eroded_object", "Object shrunk inward"),
    ("expanded_object", "Object grown outward"),
    ("ring", "Ring around the object"),
)
STATISTIC_OPTIONS = (
    ("mean", "Mean brightness"),
    ("median", "Median brightness"),
    ("min", "Minimum brightness"),
    ("max", "Maximum brightness"),
    ("std", "Brightness spread (standard deviation)"),
    ("integrated", "Total brightness (integrated)"),
    ("area", "Area"),
    ("equivalent_diameter", "Equivalent diameter"),
    ("centroid_x", "Center X"),
    ("centroid_y", "Center Y"),
    ("percent_above", "Percent of pixels above a level"),
)
BACKGROUND_OPTIONS = (
    ("none", "None"),
    ("global", "Subtract image background"),
    ("local_ring", "Subtract local background (ring)"),
)


def _option_text(options, value) -> str:
    """The plain words shown for a saved value (the value itself when it has none)."""

    return next((text for key, text in options if key == value), str(value))


def _measurement_text(controller, measurement) -> str:
    """For example 'OTX2: Mean brightness, Whole object'."""

    return (
        f"{controller._channel_name(int(measurement.channel))}: "
        f"{_option_text(STATISTIC_OPTIONS, measurement.statistic)}, {_option_text(REGION_OPTIONS, measurement.region.type)}"
    )


def _fill_options(box: QComboBox, options) -> None:
    for value, text in options:
        box.addItem(text, value)


def _choose(box: QComboBox, value) -> None:
    index = box.findData(value)
    if index >= 0:
        box.setCurrentIndex(index)


def _show_row(form: QFormLayout, field: QWidget, visible: bool) -> None:
    """Show or hide one form row, its label included."""

    field.setVisible(visible)
    label = form.labelForField(field)
    if label is not None:
        label.setVisible(visible)


# Classification colors: one pair for every marker, remembered on this computer.
# Green and magenta: told apart with red-green color blindness, unlike green and red.
DEFAULT_POSITIVE_COLOR = "#26bf59"
DEFAULT_NEGATIVE_COLOR = "#d43fd4"


def _settings():
    from qtpy.QtCore import QSettings

    return QSettings("CellQuant", "CellQuant")


def classification_colors() -> tuple[str, str]:
    """(positive, negative) colors as #rrggbb."""

    try:
        settings = _settings()
        positive = str(settings.value("review/positive_color", DEFAULT_POSITIVE_COLOR))
        negative = str(settings.value("review/negative_color", DEFAULT_NEGATIVE_COLOR))
    except Exception:  # noqa: BLE001 - settings unreadable: use the defaults
        return DEFAULT_POSITIVE_COLOR, DEFAULT_NEGATIVE_COLOR
    valid = [value if QColor(value).isValid() else default for value, default in ((positive, DEFAULT_POSITIVE_COLOR), (negative, DEFAULT_NEGATIVE_COLOR))]
    return valid[0], valid[1]


def keep_awake_preferred() -> bool:
    try:
        return str(_settings().value("run/keep_awake", "true")).lower() == "true"
    except Exception:  # noqa: BLE001 - settings unreadable: use the default
        return True


def save_keep_awake(on: bool) -> None:
    _settings().setValue("run/keep_awake", "true" if on else "false")


def save_classification_colors(positive: str, negative: str) -> None:
    settings = _settings()
    settings.setValue("review/positive_color", positive)
    settings.setValue("review/negative_color", negative)


def _rgba(color: str) -> np.ndarray:
    value = QColor(color)
    return np.array([value.redF(), value.greenF(), value.blueF(), 1.0], dtype=np.float32)


def _size_flag(name: str):
    policy = getattr(QSizePolicy, "Policy", QSizePolicy)
    return getattr(policy, name)


def release_window_size(viewer) -> None:
    """Keep the napari window free to resize.

    A large image or a wide file-name column makes a child widget report a
    huge minimum size. Qt then refuses to shrink the window. Reset those
    floors so the window can always be resized.
    """

    window = getattr(getattr(viewer, "window", None), "_qt_window", None)
    if window is None:
        return
    unbounded = QSize(16_777_215, 16_777_215)
    window.setMinimumSize(320, 240)
    window.setMaximumSize(unbounded)
    expanding = _size_flag("Expanding")
    targets = []
    qt_viewer = getattr(viewer.window, "_qt_viewer", None)
    if qt_viewer is not None:
        targets.append(qt_viewer)
        native = getattr(getattr(qt_viewer, "canvas", None), "native", None)
        if native is not None:
            targets.append(native)
    for widget in targets:
        widget.setMinimumSize(0, 0)
        widget.setMaximumSize(unbounded)
        widget.setSizePolicy(expanding, expanding)
    window.setSizePolicy(expanding, expanding)


class NewExperimentDialog(QDialog):
    """Ask for the image folder and the results folder separately."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New experiment")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. P7 OTX2 counts")
        self.images = QLineEdit()
        self.images.setToolTip(
            "Only read, never changed. This folder and its subfolders are scanned, not folders beside it.\n"
            "After Browse, check the blue box shows the folder you meant (for example …\\E14.5_E17.5, "
            "not the parent that also holds P0_P21).\n"
            "You can leave out individual images in step 1 after they are listed."
        )
        self.results = QLineEdit()
        self.results.setToolTip(
            "Runs, measurements and exports are written here. Pick the image folder only if you want "
            "results next to the images."
        )
        form.addRow("Experiment name", self.name)
        form.addRow("Image folder", self._browse_row(self.images, self._pick_images))
        self.images_check = QLabel("")
        self.images_check.setWordWrap(True)
        self.images_check.setTextFormat(Qt.RichText)
        self.images_check.setStyleSheet(
            "QLabel { background: rgba(60, 130, 220, 0.10); border-radius: 6px; padding: 6px; }"
        )
        form.addRow("", self.images_check)
        form.addRow("Results folder", self._browse_row(self.results, self._pick_results))
        types = QHBoxLayout()
        self.use_nd2 = QCheckBox("ND2 files")
        self.use_tiff = QCheckBox("TIFF files")
        for box in (self.use_nd2, self.use_tiff):
            box.setChecked(True)
            types.addWidget(box)
        types.addStretch(1)
        types_box = QWidget()
        types_box.setLayout(types)
        types.setContentsMargins(0, 0, 0, 0)
        form.addRow("Look for", types_box)
        layout.addLayout(form)
        self.images.textChanged.connect(self._update_images_check)
        self._update_images_check()
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def experiment_name(self) -> str:
        return self.name.text().strip() or "Experiment"

    def images_folder(self) -> str:
        return self.images.text().strip()

    def results_folder(self) -> str:
        return self.results.text().strip()

    def file_types(self) -> list[str]:
        return [kind for box, kind in ((self.use_nd2, "nd2"), (self.use_tiff, "tiff")) if box.isChecked()]

    def _browse_row(self, field: QLineEdit, slot) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(field)
        button = QPushButton("Browse…")
        button.clicked.connect(slot)
        layout.addWidget(button)
        return row

    def _update_images_check(self, _text: str = "") -> None:
        images = self.images.text().strip()
        if not images:
            self.images_check.setText("No image folder yet.")
            return
        path = Path(images)
        if not path.is_dir():
            self.images_check.setText(f"Not a folder: {images}")
            return
        self.images_check.setText(f"Imports <b>{path.name}</b> + subfolders")
        self.images_check.setToolTip(f"{path}\nSubfolders inside it are included. Folders next to it are not.")

    def _pick_images(self) -> None:
        folder = choose_existing_directory(
            self,
            "Select the image folder (highlight it, then Choose)",
            self.images.text().strip(),
        )
        if not folder:
            return
        self.images.setText(folder)
        if not self.results.text().strip():
            sibling = Path(folder).parent / f"{Path(folder).name} - CellQuant results"
            self.results.setText(str(sibling))

    def _pick_results(self) -> None:
        folder = choose_existing_directory(
            self,
            "Select the results folder (highlight it, then Choose)",
            self.results.text().strip(),
        )
        if folder:
            self.results.setText(folder)

    def _accept(self) -> None:
        results = self.results.text().strip()
        if not results:
            QMessageBox.warning(self, "Results folder", "Choose a folder for the results.")
            return
        if not self.file_types():
            QMessageBox.warning(self, "File types", "Tick ND2 files, TIFF files, or both.")
            return
        existing = existing_experiment_images(results)
        if existing is not None:
            QMessageBox.warning(
                self,
                "Results folder already used",
                f"Holds another experiment (images: {existing or 'not recorded'}).\n\n"
                "Choose an empty folder, or use Open experiment to continue it.",
            )
            return
        images = self.images.text().strip()
        if images and not Path(images).is_dir():
            QMessageBox.warning(self, "Image folder", f"This image folder could not be found:\n{images}")
            return
        if images:
            path = Path(images)
            counts = folder_image_counts(path)
            found = "\n".join(
                f"  {'(directly in this folder)' if name == '.' else name}: {count}" for name, count in counts
            )
            found = f"{sum(count for _, count in counts)} images:\n{found}" if counts else "No images found."
            answer = QMessageBox.question(
                self,
                "Confirm image folder",
                f"Import {path.name} and its subfolders?\n\n{path}\n\n{found}",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer != QMessageBox.Yes:
                return
        if images and results_inside_images(images, results):
            answer = QMessageBox.question(
                self,
                "Save results with the images?",
                f"{results}\n\nThis is inside the image folder. Images are never changed. Save results there?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        self.accept()


def results_inside_images(images: str | Path, results: str | Path) -> bool:
    """True when the results folder is the image folder or one of its subfolders."""

    try:
        image_root = Path(images).expanduser().resolve()
        result_root = Path(results).expanduser().resolve()
    except OSError:
        return False
    return result_root == image_root or image_root in result_root.parents


def existing_experiment_images(results: str | Path) -> str | None:
    """The image folder of an experiment already saved in results, or None when there is none.

    Starting a new experiment there would reopen that one and list its images,
    not the ones in the folder just chosen.
    """

    path = Path(results).expanduser() / "experiment.json"
    if not path.is_file():
        return None
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("input_directory") or "")
    except (OSError, ValueError, AttributeError):
        return ""


class CellQuantWindow:
    def __init__(self, viewer, experiment_dir: str | Path | None = None):
        self.viewer = viewer
        self.controller: AnalysisController | None = None
        self._managed: set[str] = set()
        self._nav_ids: list[str] = []
        self._nav_index = 0
        self._batch: BatchWorker | None = None
        self._job: CallWorker | None = None
        self._shown_image_id: str | None = None
        self._wanted_image_id: str | None = None
        self._loader: CallWorker | None = None
        self._building = False
        # True while work runs that switches between analyses (Run all analyses, Export all analyses):
        # the settings pages and image navigation are locked, since they would show another analysis.
        self._exclusive = False
        self._nav_filter: str | None = None  # e.g. "failed images" while Previous / Next go through only those
        self._tabs = QTabWidget()
        self._experiment_panel = ExperimentPanel(self)
        self._objects_panel = ObjectsPanel(self)
        self._edit_panel = EditPanel(self)
        self._measurements_panel = MeasurementsPanel(self)
        self._review_panel = ReviewPanel(self)
        self._results_panel = ResultsPanel(self)
        self._exported_to: Path | None = None
        self._hpc_panel = None  # created when HPC prep is first opened
        # Guidance for first-time users: a Start tab, then one numbered page per step.
        self._start_page = guide.StartPage(self)
        self._marker_setup = guide.MarkerSetup(self)
        self._results_summary = guide.ResultsSummary(self)
        self._step_pages = [
            guide.StepPage(self, 0, self._experiment_panel),
            guide.StepPage(self, 1, self._objects_panel),
            guide.StepPage(self, 2, self._edit_panel),
            guide.StepPage(self, 3, self._measurements_panel, extra=self._marker_setup, advanced=True),
            guide.StepPage(self, 4, self._review_panel),
            guide.StepPage(self, 5, self._results_panel, extra=self._results_summary, advanced=True),
        ]
        self._tabs.addTab(self._start_page, "Start")
        for page, step in zip(self._step_pages, guide.STEPS):
            index = self._tabs.addTab(page, str(step.number))
            self._tabs.setTabToolTip(index, step.name)
        self._tabs.setMinimumWidth(460)
        self._analysis_bar = AnalysisBar(self)
        self._dock = QWidget()
        dock_layout = QVBoxLayout(self._dock)
        dock_layout.setContentsMargins(0, 0, 0, 0)
        dock_layout.addWidget(self._analysis_bar)
        # Shown while a run uses the settings, which are greyed out until it ends.
        self._run_lock_note = QLabel("🔒 <b>Running.</b> Settings locked.")
        self._run_lock_note.setToolTip("Settings unlock when the run finishes or you click Cancel.")
        self._run_lock_note.setWordWrap(True)
        self._run_lock_note.setStyleSheet("QLabel { background: rgba(80, 120, 200, 0.22); border-radius: 6px; padding: 6px; }")
        self._run_lock_note.setVisible(False)
        dock_layout.addWidget(self._run_lock_note)
        # Problems are shown here, in red, until dismissed or the next run starts.
        self._error_box = QWidget()
        error_row = QHBoxLayout(self._error_box)
        error_row.setContentsMargins(0, 0, 0, 0)
        self._error_note = QLabel("")
        self._error_note.setWordWrap(True)
        self._error_note.setTextFormat(Qt.RichText)
        self._error_note.setStyleSheet("QLabel { background: rgba(200, 50, 50, 0.25); border-radius: 6px; padding: 6px; }")
        dismiss = QPushButton("✕")
        dismiss.setToolTip("Hide this message.")
        dismiss.setFixedWidth(28)
        dismiss.clicked.connect(lambda: self._error_box.setVisible(False))
        error_row.addWidget(self._error_note, 1)
        error_row.addWidget(dismiss)
        self._error_box.setVisible(False)
        dock_layout.addWidget(self._error_box)
        dock_layout.addWidget(self._tabs, 1)
        self._keep_awake = keep_awake.KeepAwake()
        self._footer = Footer(self)
        self._tabs.currentChanged.connect(
            lambda _index: self._footer.highlight("all" if self._tabs.currentWidget() is self._step_pages[-1] else "current")
        )
        main_dock = self.viewer.window.add_dock_widget(self._dock, name="CellQuant", area="right")
        from cellquant.gui.plan_dock import PlanDock

        self._plan_dock = PlanDock(self)
        self._plan_dock_widget = self.viewer.window.add_dock_widget(self._plan_dock, name="Plan", area="left")
        self._plan_dock_widget.hide()
        run_dock = self.viewer.window.add_dock_widget(self._footer, name="Run", area="bottom")
        self._floating_headers = [floating_header(dock) for dock in (main_dock, self._plan_dock_widget, run_dock)]
        guide.apply_help(self)
        self._guide_timer = guide.start_refresh_timer(self)
        self._start_gpu_check()
        # Menus and number boxes change with the mouse wheel only after being clicked.
        self._wheel_guard = WheelGuard(self._tabs)
        from qtpy.QtWidgets import QApplication

        QApplication.instance().installEventFilter(self._wheel_guard)
        self.viewer.layers.events.inserted.connect(lambda _event: self._release_window_later())
        self.viewer.layers.events.removed.connect(lambda _event: self._release_window_later())
        self._release_window_later()
        self._watch_settings()
        if experiment_dir:
            self.open_experiment(experiment_dir)
        self.refresh_guidance()

    # -- guidance ------------------------------------------------------------

    def go_to_step(self, index: int) -> None:
        if index < 0:
            self.go_to_start()
            return
        index = min(index, len(self._step_pages) - 1)
        self._tabs.setCurrentWidget(self._step_pages[index])
        self.refresh_guidance()

    def go_to_start(self) -> None:
        self._tabs.setCurrentWidget(self._start_page)
        self.refresh_guidance()

    def refresh_guidance(self) -> None:
        # The step that runs everything is step 6; before it, try one image at a time.
        last_step = self._tabs.currentWidget() is self._step_pages[-1]
        self._footer.highlight("all" if last_step else "current")
        if self._run_lock_note.isVisibleTo(self._dock):
            # Settings rebuilt during a run (for example a refreshed list) start enabled; lock them too.
            for widget in self._settings_inputs():
                if widget.isEnabled():
                    self._settings_restore.setdefault(widget, True)
                    widget.setEnabled(False)
        states = guide.step_states(self)
        self._start_page.update_state(states)
        for page, state, step in zip(self._step_pages, states, guide.STEPS):
            page.update_state(state)
            index = self._tabs.indexOf(page)
            self._tabs.setTabText(index, f"{step.number} ✓" if state.done else str(step.number))

    def message(self, text: str) -> None:
        self._footer.message(text)

    def show_error(self, text: str) -> None:
        """A problem the user must see: a red box above the steps, and the Run log."""

        import html

        self._error_note.setText(f"<b>⚠ Problem:</b> {html.escape(text)}")
        self._error_box.setVisible(True)
        self._footer.message(f"Problem: {text}")

    def open_guide(self) -> None:
        guide.GuideDialog(self._tabs, guide.guide_text()).exec()

    def open_hpc_prep(self) -> None:
        """Show the HPC prep tab. Local analysis settings and results are left as they are."""

        if self.require_controller() is None:
            return
        from cellquant.gui.hpc_panel import HpcPanel

        if self._hpc_panel is None:
            self._hpc_panel = HpcPanel(self)
        if self._tabs.indexOf(self._hpc_panel) < 0:
            self._tabs.addTab(self._hpc_panel, "HPC prep")
        self._hpc_panel.refresh()
        self._tabs.setCurrentWidget(self._hpc_panel)

    def open_experiment_dialog(self) -> None:
        directory = QFileDialog.getExistingDirectory(self._tabs, "Open an experiment folder")
        if directory:
            self.open_experiment(directory)

    def start_practice(self) -> None:
        folder = guide.choose_practice_folder(self._tabs)
        if folder is None:
            return
        from cellquant.practice import create_practice_experiment

        self.message("Making the practice images...")

        def ready(controller) -> None:
            self.controller = controller
            self._exported_to = None
            self._refresh_all()
            self.go_to_step(0)
            self.message(
                f"Practice experiment ready (3 images). Answers: {folder / 'README.txt'}."
            )

        self._start_job(lambda: create_practice_experiment(folder), ready)

    def apply_marker_setup(
        self,
        chosen: list[tuple[int, str]],
        rule: str = guide.RULE_MEAN,
        min_percent: float = guide.DEFAULT_MIN_PERCENT,
    ) -> None:
        """Step 4 quick setup: define the markers, measure this image, and pick starting cutoffs.

        With the percent rule the minimum percent is the user's; the starting pixel level of
        each marker is the automatic cutoff between dim and bright cells' mean brightness.
        """

        controller = self.require_controller()
        if controller is None:
            return
        self._panels_to_recipe()
        controller.set_recipe(
            guide.marker_recipe(controller.recipe.model_dump(mode="json"), chosen, rule=rule, min_percent=min_percent)
        )
        controller.save()
        self._measurements_panel.refresh()
        self._results_panel.refresh()
        self._marker_setup.refresh()
        if not self._nav_ids:
            return
        image_id = self._nav_ids[self._nav_index]
        self.message("Measuring the markers in this image...")

        def measured(result) -> None:
            recipe = controller.recipe
            levels = {}
            for item in recipe.classifications:
                if not guide.uses_pixel_level(recipe, item):
                    continue
                spec = next(m for m in recipe.measurements if m.id == item.measurement)
                reference = next(
                    (m.id for m in recipe.measurements if m.channel == spec.channel and m.statistic == "mean"),
                    None,
                )
                if reference is not None and reference in result.objects.columns:
                    levels[spec.id] = (guide.starting_threshold(result.objects[reference].to_numpy(dtype=float)), None)
            if levels:
                result = controller.update_pixel_levels(image_id, levels) or result
            starting = {
                item.id: guide.starting_threshold(result.objects[item.measurement].to_numpy(dtype=float))
                for item in controller.recipe.classifications
                if item.measurement in result.objects.columns and not guide.uses_pixel_level(controller.recipe, item)
            }
            updated = controller.update_thresholds(image_id, starting) or result
            controller.save()
            self._measurements_panel.refresh()
            self._marker_setup.refresh()
            self.show_result(updated)
            self.go_to_step(4)
            first = controller.recipe.classifications[0].id if controller.recipe.classifications else None
            panel = self._review_panel
            if first is not None and panel.display.findData(first) >= 0:
                panel.display.setCurrentIndex(panel.display.findData(first))
            if levels:
                self.message(
                    "Markers measured. Check the starting pixel levels and the Cutoff slider."
                )
            else:
                self.message("Markers measured. Check the starting cutoffs with the Cutoff slider.")

        self._start_job(lambda: controller.run_image(image_id), measured)

    def export_dialog(self) -> None:
        controller = self.require_controller()
        if controller is None:
            return
        directory = QFileDialog.getExistingDirectory(
            self._tabs,
            "Choose a folder for the results",
            str(controller.directory / "exports"),
        )
        if not directory:
            return

        def done(path) -> None:
            self._exported_to = Path(path)
            self._results_summary.show_exported(path)
            self.message(f"Results saved to {path}")
            self.refresh_guidance()

        options = self._results_panel.summary_options()
        self._start_job(lambda: controller.export(directory, **options), done)

    def _start_gpu_check(self) -> None:
        """Load PyTorch in the background to learn whether a GPU is usable."""

        if not self._objects_panel.engine.installed:
            self._objects_panel.show_gpu_status({"available": False})
            return
        from cellquant.engines import gpu_status

        self._gpu_check = CallWorker(gpu_status)
        self._gpu_check.succeeded.connect(self._objects_panel.show_gpu_status)
        self._gpu_check.failed.connect(lambda message: self._objects_panel.show_gpu_status({"available": False, "reason": message}))
        self._gpu_check.start()

    def open_experiment(self, directory: str | Path) -> None:
        try:
            self.controller = AnalysisController.open(directory)
        except Exception as exc:  # noqa: BLE001 - shown to the user; the window stays usable
            self.show_error(f"{directory} could not be opened as an experiment. {_error_text(exc)}")
            return
        self._exported_to = None
        self._refresh_all()
        self.go_to_start()

    def new_experiment(self) -> None:
        dialog = NewExperimentDialog(self._tabs)
        if dialog.exec() != QDialog.Accepted:
            return
        output = Path(dialog.results_folder())
        images = dialog.images_folder()
        if (output / "experiment.json").is_file():
            self.message("That results folder already has an experiment. Use Open experiment… to open it, or choose another folder.")
            return
        try:
            controller = AnalysisController.create(output, dialog.experiment_name(), input_directory=images or None)
        except Exception as exc:  # noqa: BLE001 - e.g. a results folder that cannot be written
            self.show_error(f"The experiment could not be created in {output}. {_error_text(exc)}")
            return
        file_types = dialog.file_types() if hasattr(dialog, "file_types") else ["nd2", "tiff"]
        controller.experiment.import_file_types = list(file_types)
        self.controller = controller
        self._exported_to = None
        self._refresh_all()
        self.go_to_step(0)
        if not images:
            self.message(f"Results will be saved in {output}. Add images when you are ready.")
            return
        self.message(f"Looking for images in {images} and its subfolders...")

        def listed(notices) -> None:
            self._refresh_all()
            self._experiment_panel.set_notices(notices)
            self.message(f"Images are read from {images}. Results are saved in {output}.")

        self._start_job(lambda: controller.add_image_paths([images], file_types=file_types), listed)

    def require_controller(self) -> AnalysisController | None:
        if self.controller is None:
            self._footer.message("Open an experiment first: go to the Start tab.")
            return None
        return self.controller

    def show_current(self) -> None:
        controller = self.controller
        if self._exclusive and self.is_busy():
            return  # another analysis is active in the background; shown again when it finishes
        if controller is None or not self._nav_ids:
            self._clear_managed()
            if controller is not None:
                self._footer.set_position(0, 0, "")
            return
        image_id = self._nav_ids[self._nav_index]
        controller.current_image_id = image_id
        record = controller.experiment.image(image_id)
        result = controller.recall(image_id) if controller else None
        self._footer.set_position(self._nav_index, len(self._nav_ids), record.relative_path or record.filename, self._nav_filter)
        self._review_panel.update_status_line()
        self._experiment_panel.set_pixel_size(record.pixel_size_x, record.pixel_size_y, record.pixel_size_z)
        self._experiment_panel.show_channel_order(record)
        # Reading the file (an ND2 stack can take a second) happens off the interface thread,
        # so the window stays responsive. If the user keeps clicking Next, only the last
        # image asked for is shown.
        self._wanted_image_id = image_id
        if self._loader is not None:
            return
        self._footer.message(f"Loading {record.relative_path or record.filename}...")
        self._load_in_background(record, result)

    def _load_in_background(self, record, result) -> None:
        controller = self.controller
        worker = CallWorker(lambda: controller._load_display(record))
        self._loader = worker

        def shown(loaded) -> None:
            self._loader = None
            if self.controller is not controller:
                return  # a different experiment was opened meanwhile
            if self._wanted_image_id != record.image_id:
                self.show_current()  # the user moved on while this one was loading
                return
            self._show_loaded(loaded, record, result)
            if result is not None:
                self._review_panel.show_result(result)
                self._results_panel.show_result(result)
                self._results_summary.show_result(result, controller.recipe)
            unit = (
                f"µm ({record.pixel_size_x:.3f} µm/pixel)"
                if record.pixel_size_x and record.pixel_size_y
                else "pixels — no pixel size in this image"
            )
            self._footer.set_units(f"{unit}   ·   Analyzed: {loaded.z_description}")
            self._footer.message(f"Showing {record.relative_path or record.filename}")

        def failed(message: str) -> None:
            self._loader = None
            if self.controller is not controller:
                return
            self.show_error(f"{record.relative_path or record.filename} could not be shown. {message}")
            self._clear_managed()
            if self._wanted_image_id != record.image_id:
                self.show_current()

        worker.succeeded.connect(shown)
        worker.failed.connect(failed)
        worker.start()

    def image_loading(self) -> bool:
        return self._loader is not None

    def show_result(self, result) -> None:
        if result is None or self.controller is None:
            return
        image_id = result.provenance.get("image_id")
        if image_id == self._shown_image_id and self._has_image_layers():
            # Same image: keep the channel layers, and update the object layers in place.
            self._set_labels(result.labels, result)
        else:
            record = self.controller.experiment.image(image_id)
            self._wanted_image_id = image_id
            if self._loader is None:
                self._load_in_background(record, result)
            return
        self._review_panel.show_result(result)
        self._results_panel.show_result(result)
        self._results_summary.show_result(result, self.controller.recipe)
        unit = "µm" if result.spatial_unit == "um" else "pixels — no pixel size in this image"
        described = result.provenance.get("z_description")
        self._footer.set_units(f"{unit}   ·   Analyzed: {described}" if described else unit)

    def run_current(self) -> None:
        controller = self.require_controller()
        if controller is None:
            return
        if not self._nav_ids:
            self.message("No images to run: add images in step 1, or tick Include for at least one.")
            return
        self._panels_to_recipe()
        image_id = self._nav_ids[self._nav_index]
        self._start_job(lambda: controller.run_image(image_id, new_run=True), self._run_finished)

    def _run_finished(self, result) -> None:
        self.show_result(result)
        self._refresh_plan()
        self._experiment_panel.refresh_table()
        if result.qc.warnings:
            self._footer.message(result.qc.warnings[0])
        else:
            self._footer.message(f"Found {result.qc.n_objects} objects. Check that the outlines match, then continue.")
        self.refresh_guidance()

    def _start_job(self, fn, on_success) -> None:
        if self._batch is not None or self._job is not None:
            self._footer.message("Wait for the current analysis to finish.")
            return
        worker = CallWorker(fn, report=True)
        self._job = worker
        worker.succeeded.connect(on_success)
        worker.failed.connect(self.show_error)
        worker.step.connect(self._footer.show_step)
        worker.finished.connect(self._clear_job)
        self.set_busy(True)
        worker.start()

    def _clear_job(self) -> None:
        self._job = None
        self.set_busy(False)

    # Buttons that start work, or change what is being analyzed, are unavailable while work runs.
    _BUSY_BUTTONS = frozenset(
        {
            "Preview", "Run this image", "Run selected images", "Run all images", "Set up markers",
            "Export results…", "Add images", "Add folder", "New experiment", "New experiment…", "Open",
            "Open experiment…", "Try practice images", "Include shown", "Leave out shown",
            "Include only selected", "Delete object", "Restore object", "Undo", "Record drawn edits", "Approve", "Use recommended",
            "HPC prep…",  # the HPC prep page manages its own buttons: a second job is refused while one runs
            "Run all analyses", "Export all analyses…", "New analysis…", "One per channel…", "Rename…", "Remove",
            # Buttons that change the settings: never while images are being analyzed with them.
            "Load settings…", "Add measurement", "Remove measurement",
            "Add marker", "Remove marker", "Add result row", "Remove result row", "Apply pixel level",
            "Run all images (this analysis)", "Set sizes (µm)",
        }
    )

    def set_busy(self, busy: bool) -> None:
        """Grey out run buttons while an analysis runs; Cancel (and Pause for batches) become available."""

        if busy:
            buttons = [
                button
                for button in [*self._dock.findChildren(QPushButton), *self._footer.findChildren(QPushButton)]
                if button.text() in self._BUSY_BUTTONS
            ]
            buttons.append(self._analysis_bar.choice)
            self._busy_restore = {button: button.isEnabled() for button in buttons}
            for button in buttons:
                button.setEnabled(False)
            if self._exclusive:
                self._tabs.setEnabled(False)
                self._footer.set_navigation_enabled(False)
            self._plan_dock.setEnabled(False)  # the plan is read when a run starts; no changes while it runs
            self._lock_settings(True)
            if self._footer.keep_awake.isChecked():
                self._keep_awake.hold()
            self._error_box.setVisible(False)
            self._footer.start_busy(batch=self._batch is not None)
        else:
            for button, enabled in getattr(self, "_busy_restore", {}).items():
                try:
                    button.setEnabled(enabled)
                except RuntimeError:
                    pass  # the button was rebuilt meanwhile
            self._busy_restore = {}
            self._keep_awake.release()
            self._lock_settings(False)
            self._footer.end_busy()
            self._plan_dock.setEnabled(True)
            self._refresh_plan()
            if self._exclusive:
                self._exclusive = False
                self._tabs.setEnabled(True)
                self._footer.set_navigation_enabled(True)
                self._refresh_all(keep_image=True)

    def _settings_inputs(self) -> list[QWidget]:
        """Inputs that change the analysis settings. Display-only choices stay usable during a run."""

        from qtpy.QtWidgets import QAbstractSpinBox, QSlider

        view_only = {
            self._experiment_panel.show_type,
            self._experiment_panel.filter_box,
            self._objects_panel.advanced_toggle,
            self._review_panel.advanced_toggle,
            self._review_panel.display,
        }
        inputs: list[QWidget] = [self._review_panel.threshold_slider]
        panels = (self._experiment_panel, self._objects_panel, self._measurements_panel, self._review_panel, self._marker_setup)
        for panel in panels:
            for kind in (QComboBox, QAbstractSpinBox, QLineEdit, QCheckBox, QSlider):
                inputs.extend(widget for widget in panel.findChildren(kind) if widget not in view_only)
        # The crop channels are a checkable list; they change the settings too.
        inputs.append(self._objects_panel.crop_channels)
        return inputs

    def _lock_settings(self, locked: bool) -> None:
        """Grey out the settings while a run uses them, so menus cannot be scrolled or changed."""

        if locked:
            widgets = self._settings_inputs()
            self._settings_restore = {widget: widget.isEnabled() for widget in widgets}
            for widget in widgets:
                widget.setEnabled(False)
        else:
            for widget, enabled in getattr(self, "_settings_restore", {}).items():
                try:
                    widget.setEnabled(enabled)
                except RuntimeError:
                    pass  # the widget was rebuilt meanwhile
            self._settings_restore = {}
            # Ancestor-disabled widgets were saved as False and setEnabled(False) sticks
            # after unlock; re-apply panel rules so Z-stack mode becomes usable again.
            self._objects_panel._apply_z_enablement()
            self._objects_panel._method_changed()
        self._run_lock_note.setVisible(locked)

    def _keep_awake_toggled(self, on: bool) -> None:
        save_keep_awake(on)
        if on and self.is_busy():
            self._keep_awake.hold()
        elif not on:
            self._keep_awake.release()

    def is_busy(self) -> bool:
        return self._job is not None or self._batch is not None

    def preview_current(self) -> None:
        controller = self.require_controller()
        if controller is None:
            return
        if not self._nav_ids:
            self.message("No images to run: add images in step 1, or tick Include for at least one.")
            return
        self._panels_to_recipe()
        image_id = self._nav_ids[self._nav_index]
        crop = _visible_crop(self.viewer)

        def finish(labels) -> None:
            self._set_labels(labels, None)
            self._footer.message("Preview shows the current field of view. Run analyzes the full image.")

        self._start_job(lambda: controller.preview(image_id, crop), finish)

    def start_batch(self, image_ids: list[str] | None, analyses: list[str] | None = None) -> None:
        """Run the images with the current analysis, or (``analyses``) with each of those analyses in turn."""

        controller = self.require_controller()
        if controller is None or self._batch is not None or self._job is not None:
            return
        self._panels_to_recipe()
        worker = BatchWorker(controller, image_ids, analyses)
        worker.progress.connect(self._footer.update_progress)
        worker.step.connect(self._footer.show_step)
        worker.finished_ok.connect(self._batch_finished)
        worker.failed.connect(self._batch_failed)
        self._batch = worker
        self.set_busy(True)
        worker.start()

    def run_all_analyses(self) -> None:
        """Run the plan: each analysis on the images ticked for it (all included images unless changed)."""

        self.run_plan()

    def run_plan(self) -> None:
        controller = self.require_controller()
        if controller is None or self.is_busy():
            return
        if not any(controller.planned_images(item.recipe_id) for item in controller.analyses()):
            self.message("Nothing is ticked in the plan: tick images for at least one analysis.")
            return
        self._exclusive = True
        self.start_batch(None, [item.recipe_id for item in controller.analyses()])

    # -- plan dock ---------------------------------------------------------------------------------

    def show_plan(self) -> None:
        if self.require_controller() is None:
            return
        self._plan_dock_widget.show()
        self._plan_dock_widget.raise_()
        self._plan_dock.refresh()

    def _refresh_plan(self) -> None:
        if self._plan_dock_widget.isVisible() or self._plan_dock.tree.topLevelItemCount():
            self._plan_dock.refresh()

    def plan_changed(self) -> None:
        """After the plan changed in the Plan dock: redraw it and the lists that depend on it.

        Redrawn on the next turn of the event loop: the change usually comes from a signal of a
        row or menu inside the dock, which must not be deleted while its signal runs.
        """

        def redraw() -> None:
            self._plan_dock.refresh()
            self._experiment_panel.refresh_table()
            self.update_navigation()
            self.refresh_guidance()

        QTimer.singleShot(0, redraw)

    def open_in_analysis(self, image_id: str, recipe_id: str | None) -> None:
        """Show this image with this analysis (from the Plan dock), in step 5."""

        controller = self.controller
        if controller is None:
            return
        if self.is_busy():
            self.message("Wait for the current analysis to finish.")
            return
        if recipe_id and recipe_id != controller.recipe.recipe_id:
            self.switch_analysis(recipe_id)
        if image_id not in self._nav_ids:
            self.message("That image is left out (unticked in step 1). Tick Include to show it.")
            return
        self._nav_index = self._nav_ids.index(image_id)
        self.show_current()
        self.go_to_step(4)

    def _batch_finished(self, report) -> None:
        planned = getattr(self._footer, "_batch_total", 0)
        self._batch = None
        self.set_busy(False)
        if isinstance(report, list):
            self._analyses_finished(report, planned)
            return
        stopped = planned and len(report.jobs) < planned
        self._results_panel.show_queue(report)
        self._results_summary.show_batch(report)
        self.refresh_guidance()
        self._experiment_panel.refresh_table()
        if self.controller and self.controller.current_image_id in self.controller.last_results:
            self.show_result(self.controller.last_results[self.controller.current_image_id])
        self._footer.message(
            (f"Stopped after {len(report.jobs)} of {planned} images. " if stopped else "")
            + run_outcome(report.completed, report.warnings, report.failed) + "."
        )
        self._show_failed_images(report.failed, [report])

    def _batch_failed(self, message: str) -> None:
        """The batch stopped with an error before it could report: leave the running state and say why."""

        self._batch = None
        self.set_busy(False)
        self._experiment_panel.refresh_table()
        self.refresh_guidance()
        self.show_error(f"The run stopped: {message} Images finished before it stopped are kept.")

    def _show_failed_images(self, count: int, reports=()) -> None:
        if not count:
            return
        reasons = [(job.filename, job.message) for report in reports for job in report.jobs if job.status == "Failure"]
        if not reasons:
            reasons = [(name, "") for name in self._footer.failed_files]
        listed = "; ".join(f"{name}: {message}" if message else name for name, message in reasons[:5])
        if len(reasons) > 5:
            listed += f"; and {len(reasons) - 5} more"
        self.show_error(
            f"{count} image{'s' if count != 1 else ''} failed"
            + (f" ({listed})" if listed else "")
            + ". Reasons: step 1 Status column. Step 5 Check: Failed goes through them."
        )

    def _analyses_finished(self, reports, planned: int) -> None:
        done = sum(len(report.jobs) for report in reports)
        lines = [
            f"{report.analysis}: {run_outcome(report.completed, report.warnings, report.failed)}"
            + (" (stopped)" if report.cancelled else "")
            for report in reports
        ]
        if reports:
            self._results_panel.show_queue(reports[-1])
        self._results_summary.show_analyses(reports)
        self.refresh_guidance()
        stopped = planned and done < planned
        self._footer.message(
            (f"Stopped after {done} of {planned} image runs. " if stopped else f"Ran {len(reports)} analyses. ")
            + " | ".join(lines)
        )
        self._show_failed_images(sum(report.failed for report in reports), reports)

    def export_all_dialog(self) -> None:
        controller = self.require_controller()
        if controller is None:
            return
        directory = QFileDialog.getExistingDirectory(
            self._tabs, "Choose a folder for the results of every analysis", str(controller.directory / "exports")
        )
        if not directory or self.is_busy():
            return
        self._panels_to_recipe()
        self._exclusive = True

        def done(path) -> None:
            self._exported_to = Path(path)
            self._results_summary.show_exported(path)
            self.message(f"Every analysis with results was saved to {path} (one folder each, and all_analyses_image_summary.csv).")
            self.refresh_guidance()

        options = self._results_panel.summary_options()
        self._start_job(lambda: controller.export_all(directory, **options), done)

    # -- analyses ----------------------------------------------------------------------------------

    def switch_analysis(self, recipe_id: str) -> None:
        controller = self.controller
        if controller is None or recipe_id == controller.recipe.recipe_id:
            return
        if self.is_busy():
            self.message("Wait for the current analysis to finish before switching.")
            self._analysis_bar.refresh()
            return
        self._panels_to_recipe()  # the settings on screen belong to the analysis being left
        try:
            controller.switch_analysis(recipe_id)
        except (CellQuantError, KeyError, OSError) as exc:
            self.show_error(str(exc))
            self._analysis_bar.refresh()
            return
        self._refresh_all(keep_image=True)
        self.refresh_guidance()
        item = controller.active_analysis()
        self.message(f"Showing analysis '{item.name}': objects found in {controller._channel_name(controller.recipe.object_set.segmentation_channel)}.")

    def set_nav_filter(self, words: str | None, ids: list[str] | None = None) -> None:
        """Previous / Next go through only these images (words: what they are), or every included image (None)."""

        self._nav_filter = words if ids else None
        if ids:
            self._nav_ids = list(ids)
            self._nav_index = 0
            self.show_current()
            self.message(f"Previous / Next image now go through the {len(ids)} {words} only. Step 5 has Check all included images to go back.")
        else:
            self.update_navigation()
            self.show_current()
            self.message("Previous / Next image go through every included image.")

    def update_navigation(self) -> None:
        """Previous/Next image go through included images; keep the image on screen when possible."""

        if self.controller is None:
            return
        self._nav_filter = None
        current = self._nav_ids[self._nav_index] if self._nav_ids else None
        ids = self.controller.included_ids()
        self._nav_ids = ids
        if current in ids:
            self._nav_index = ids.index(current)
        else:
            self._nav_index = 0
            self.show_current()
        if self._nav_ids and current in ids:
            record = self.controller.experiment.image(current)
            self._footer.set_position(self._nav_index, len(ids), record.relative_path or record.filename)
        elif not ids:
            self._footer.set_position(0, 0, "")

    def _panels_to_recipe(self) -> None:
        if self.controller is None:
            return
        self._objects_panel.write_recipe()
        self._measurements_panel.write_recipe()
        self._results_panel.write_reports()

    def _autosave(self) -> None:
        """Persist experiment.json (and current recipe panels) after Step 1 edits."""

        controller = self.controller
        if controller is None:
            return
        self._panels_to_recipe()
        controller.save()

    def _watch_settings(self) -> None:
        """Save the settings shortly after the user changes one, when the step changes, and on closing.

        Only signals sent by the user are watched, so filling the pages from saved settings saves nothing.
        """

        self._settings_save_timer = QTimer(self._dock)  # goes with the window, so it never fires after it
        self._settings_save_timer.setSingleShot(True)
        self._settings_save_timer.setInterval(800)
        self._settings_save_timer.timeout.connect(self._save_settings_quietly)
        save_soon = self._settings_save_timer.start
        from qtpy.QtWidgets import QAbstractSpinBox

        for widget in self._settings_inputs():
            if isinstance(widget, QComboBox):
                widget.activated.connect(lambda *_args: save_soon())
            elif isinstance(widget, (QAbstractSpinBox, QLineEdit)):
                widget.editingFinished.connect(save_soon)
            elif isinstance(widget, QCheckBox):
                widget.clicked.connect(lambda *_args: save_soon())
        self._tabs.currentChanged.connect(lambda _index: save_soon())
        self._close_watcher = CloseWatcher(self._save_settings_quietly, self._dock)
        self.viewer.window._qt_window.installEventFilter(self._close_watcher)

    def _save_settings_quietly(self) -> None:
        # Never while a run uses the settings; a half-typed value is reported when the user runs.
        if self.controller is None or self.is_busy() or self._exclusive:
            return
        try:
            self._autosave()
        except (CellQuantError, ValueError):
            pass

    def _refresh_all(self, keep_image: bool = False) -> None:
        if self.controller is None:
            return
        current = self._nav_ids[self._nav_index] if keep_image and self._nav_ids else None
        self._nav_filter = None
        self._nav_ids = [record.image_id for record in self.controller.experiment.images if record.include]
        self._nav_index = self._nav_ids.index(current) if current in self._nav_ids else 0
        self._analysis_bar.refresh()
        self._refresh_plan()
        self._experiment_panel.refresh()
        self._objects_panel.refresh()
        self._measurements_panel.refresh()
        self._results_panel.refresh()
        self._marker_setup.refresh()
        notices = self.controller.channel_notices()
        self._experiment_panel.set_notices(notices)
        if self._hpc_panel is not None:
            self._hpc_panel.refresh()
        self.show_current()
        self._release_window_later()

    def _release_window_later(self) -> None:
        def release() -> None:
            try:
                release_window_size(self.viewer)
            except RuntimeError:
                pass  # the window was closed before this ran

        QTimer.singleShot(0, release)

    def show_classification(self, result) -> None:
        """After a threshold change: recolor objects and update counts. Nothing is reloaded."""

        if result is None or self.controller is None:
            return
        self._set_classification_overlay(result)
        self._review_panel.show_result(result)
        self._results_panel.show_result(result)
        self._results_summary.show_result(result, self.controller.recipe)
        # The Markers table is read back into the settings before every run; keep its cutoffs current.
        self._measurements_panel.refresh()

    def _has_image_layers(self) -> bool:
        return any(name in self.viewer.layers for name in self._managed if name not in _OBJECT_LAYERS)

    def _show_loaded(self, loaded, record, result) -> None:
        self._clear_managed()
        self._shown_image_id = record.image_id
        self._edit_panel.clear_selection()
        file_names = list(getattr(loaded, "channel_names", None) or record.channel_names or ())
        names = format_channel_labels(file_names, loaded.n_channels)
        # Contrast is remembered per experiment channel, matched by the names stored in the file.
        by_index = {channel.channel_index: channel for channel in self.controller.experiment.channels}
        owners = self.controller.display_channels(record, loaded.n_channels)
        colormaps = channel_colormaps(loaded)
        added = self.viewer.add_image(
            loaded.data,
            channel_axis=0,
            name=names,
            colormap=colormaps,
            blending="additive",
        )
        layers = added if isinstance(added, list) else [added]
        for layer, owner in zip(layers, owners, strict=False):
            self._managed.add(layer.name)
            channel = by_index.get(owner) if owner is not None else None
            if channel is None:
                continue  # not in the channel list: nothing to remember its contrast under
            limits = channel.display_settings.get("contrast_limits")
            if limits:
                layer.contrast_limits = tuple(limits)
            layer.events.contrast_limits.connect(
                lambda event, index=channel.channel_index: self._store_contrast(index, event)
            )
        if loaded.data.ndim == 4:
            # Every slice is loaded, so the slider under the image scrolls the stack. Start on the
            # slice a one-slice analysis used, else the middle one.
            slice_index = loaded.z_index if loaded.z_mode == "single_plane" else loaded.data.shape[1] // 2
            self.viewer.dims.set_current_step(0, int(slice_index))
        labels = result.labels if result is not None else None
        self._set_labels(labels, result)
        self.show_count_area(record)
        self._release_window_later()
        self._experiment_panel.show_channel_order(record)

    def _store_contrast(self, index: int, event) -> None:
        if self.controller is None:
            return
        for channel in self.controller.experiment.channels:
            if channel.channel_index == index:
                value = event.value if hasattr(event, "value") else event
                channel.display_settings["contrast_limits"] = [float(value[0]), float(value[1])]

    def _set_labels(self, labels, result) -> None:
        # Adding or removing a napari layer takes on the order of a second, so
        # layers that already exist with the right shape get new data instead.
        if labels is None:
            for name in _OBJECT_LAYERS:
                self._drop(name)
            return
        fills = self._layer_if_shape("Object fills", labels.shape)
        boundaries = self._layer_if_shape("Objects", labels.shape)
        if fills is not None and boundaries is not None:
            fills.data = labels
            boundaries.data = np.array(labels, copy=True)
        else:
            for name in ("Object fills", "Objects"):
                self._drop(name)
            fills = self.viewer.add_labels(labels, name="Object fills", opacity=0.35)
            boundaries = self.viewer.add_labels(np.array(labels, copy=True), name="Objects", opacity=1)
            boundaries.contour = 2
            self._managed.update({"Object fills", "Objects"})
            self._review_panel.bind_labels(boundaries, fills)
            self._edit_panel.bind_labels(boundaries)
        if result is None:
            self._drop("Classification")
            self._drop("Object IDs")
            self._drop("Crop regions")
        else:
            self._set_classification_overlay(result)
            self._set_ids(result)
            self._set_crop_regions(result, labels.shape)

    def _set_crop_regions(self, result, shape) -> None:
        """Outline the rectangles this image was segmented in (when the analysis crops)."""

        self._drop("Crop regions")
        rectangles = result.provenance.get("crop_rectangles") if result is not None else None
        if not rectangles or len(shape) != 2:
            return
        corners = [
            np.array([[y0, x0], [y0, x1], [y1, x1], [y1, x0]], dtype=float) for y0, y1, x0, x1 in rectangles
        ]
        layer = self.viewer.add_shapes(
            corners, shape_type="rectangle", name="Crop regions", edge_color="yellow", face_color="transparent", edge_width=3
        )
        self._managed.add(layer.name)

    def show_count_area(self, record) -> None:
        """Outline this image's saved count area (none: no layer)."""

        self._drop(COUNT_AREA_LAYER)
        if record.count_area:
            self.count_area_layer([np.asarray(polygon, dtype=float) for polygon in record.count_area])

    def count_area_layer(self, polygons=None):
        """The Count area shapes layer, added (with these polygons) when missing."""

        if COUNT_AREA_LAYER in self.viewer.layers:
            return self.viewer.layers[COUNT_AREA_LAYER]
        layer = self.viewer.add_shapes(
            polygons or None,
            ndim=2,
            shape_type="polygon",
            name=COUNT_AREA_LAYER,
            edge_color="cyan",
            face_color="transparent",
            edge_width=3,
        )
        self._managed.add(layer.name)
        return layer

    def _set_classification_overlay(self, result) -> None:
        choice = self._review_panel.classification_id()
        if not choice or choice not in result.objects.columns:
            self._drop("Classification")
            return
        frame = result.objects
        if "excluded" in frame.columns:
            frame = frame.loc[~frame["excluded"].astype(bool)]
        frame = frame.loc[~outside(frame)]  # objects outside the count area are not colored
        # One lookup per pixel instead of one full-image comparison per object.
        labels = np.asarray(result.labels)
        codes = np.zeros(int(labels.max(initial=0)) + 1, dtype=np.int32)
        ids = frame["object_id"].to_numpy(dtype=np.int64)
        flags = np.array([2 if _is_positive_flag(value) else 1 for value in frame[choice].tolist()], dtype=np.int32)
        inside = ids < len(codes)
        codes[ids[inside]] = flags[inside]
        overlay = codes[labels]
        existing = self._layer_if_shape("Classification", overlay.shape)
        if existing is not None:
            existing.data = overlay
            return
        self._drop("Classification")
        layer = self.viewer.add_labels(overlay, name="Classification", opacity=0.85)
        self._managed.add("Classification")
        self.apply_classification_colors()

    def apply_classification_colors(self) -> None:
        """Positive objects in the positive color, negative in the negative color."""

        if "Classification" not in self.viewer.layers:
            return
        positive, negative = classification_colors()
        self.viewer.layers["Classification"].colormap = DirectLabelColormap(
            color_dict={
                None: np.array([0, 0, 0, 0], dtype=np.float32),
                1: _rgba(negative),
                2: _rgba(positive),
            }
        )

    def _set_ids(self, result) -> None:
        frame = result.objects
        if "excluded" in frame.columns:
            frame = frame.loc[~frame["excluded"].astype(bool)]
        if frame.empty:
            return
        coords = np.column_stack([frame["centroid_y"].to_numpy(), frame["centroid_x"].to_numpy()])
        if result.spatial_unit == "um":
            record = self.controller.experiment.image(result.provenance["image_id"])
            if record.pixel_size_x and record.pixel_size_y:
                coords = coords / np.array([record.pixel_size_y, record.pixel_size_x])
        if np.asarray(result.labels).ndim == 3 and "z_first" in frame.columns:
            # Show each number on the object's middle slice.
            middle = (frame["z_first"].to_numpy(dtype=float) + frame["z_last"].to_numpy(dtype=float)) / 2 - 1
            coords = np.column_stack([np.round(middle), coords])
        visible = False
        if "Object IDs" in self.viewer.layers:
            visible = self.viewer.layers["Object IDs"].visible
            self._drop("Object IDs")
        points = self.viewer.add_points(
            coords,
            name="Object IDs",
            size=8,
            face_color="transparent",
            border_color="white",
            text={"string": [str(value) for value in frame["object_id"].tolist()], "size": 10, "color": "white"},
            visible=visible,
        )
        self._managed.add(points.name)

    def _layer_if_shape(self, name: str, shape: tuple[int, ...]):
        if name in self.viewer.layers and tuple(self.viewer.layers[name].data.shape) == tuple(shape):
            return self.viewer.layers[name]
        return None

    def _drop(self, name: str) -> None:
        if name in self.viewer.layers:
            self.viewer.layers.remove(name)
        self._managed.discard(name)

    def _clear_managed(self) -> None:
        for name in list(self._managed):
            self._drop(name)


class ExperimentPanel(QWidget):
    def __init__(self, shell: CellQuantWindow):
        super().__init__()
        self.shell = shell
        layout = QVBoxLayout(self)
        buttons = QHBoxLayout()
        for text, slot in (
            ("New experiment…", shell.new_experiment),
            ("Open experiment…", self._open),
            ("Add images…", self._add_images),
            ("Add folder…", self._add_folder),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.notices = QLabel("")
        self.notices.setWordWrap(True)
        self.notices.setTextFormat(Qt.RichText)
        self.notices.setStyleSheet("QLabel { background: rgba(217, 164, 0, 0.14); border-radius: 6px; padding: 6px; }")
        self.notices.setVisible(False)
        layout.addWidget(self.notices)
        filter_row = QHBoxLayout()
        self.show_type = QComboBox()
        self.show_type.addItem("Show all files", "all")
        self.show_type.addItem("Show ND2 only", "nd2")
        self.show_type.addItem("Show TIFF only", "tiff")
        self.show_type.setToolTip("Show only one file type in the list. Combine with the text filter.")
        self.show_type.currentIndexChanged.connect(lambda _index: self._filter(self.filter_box.text()))
        self.filter_box = QLineEdit()
        self.filter_box.setPlaceholderText("Filter")
        self.filter_box.textChanged.connect(self._filter)
        filter_row.addWidget(self.show_type)
        filter_row.addWidget(self.filter_box, 1)
        layout.addLayout(filter_row)
        self.included_label = QLabel("")
        layout.addWidget(self.included_label)
        self.table = ManifestTable(self)
        self.table.setMinimumHeight(210)  # about six rows; the step page scrolls, so the window can still shrink
        self.table.setMinimumWidth(0)
        self.table.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
        self.table.setSizePolicy(_size_flag("Ignored"), _size_flag("Expanding"))
        self.table.setTextElideMode(Qt.ElideMiddle)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        header.setMinimumSectionSize(36)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self.table)
        run_row = QHBoxLayout()
        run_row.addStretch(1)
        run_selected = QPushButton("Run selected images")
        run_selected.setToolTip("Run the images selected in the table (click rows; Ctrl or Shift for more), for example to rerun failed ones.")
        run_selected.clicked.connect(lambda: shell._results_panel._run_selected())
        run_row.addWidget(run_selected)
        layout.addLayout(run_row)
        self.channel_order = QLabel("No image open.")
        self.channel_order.setToolTip(
            "Channel order comes from each image file. Experimental conditions come from "
            "Sample name and Folder columns, not from renaming channels."
        )
        self.channel_order.setWordWrap(True)
        self.channel_order.setTextFormat(Qt.RichText)
        self.channel_order.setStyleSheet("QLabel { background: rgba(60, 130, 220, 0.10); border-radius: 6px; padding: 6px; }")
        layout.addWidget(self.channel_order)
        calibration = QHBoxLayout()
        self.pixel_x = QDoubleSpinBox()
        self.pixel_y = QDoubleSpinBox()
        self.pixel_z = QDoubleSpinBox()
        for box in (self.pixel_x, self.pixel_y, self.pixel_z):
            box.setDecimals(4)
            box.setMaximum(100000)
            box.setSpecialValueText("unset")
        self.pixel_z.setToolTip("Distance between slices of a Z-stack, in µm. Needed for 3D volumes and shapes.")
        apply_cal = QPushButton("Set sizes (µm)")
        apply_cal.clicked.connect(self._apply_pixel_size)
        self.size_scope = QComboBox()
        self.size_scope.addItem("this + images without a size", "missing")
        self.size_scope.addItem("this image only", "this")
        self.size_scope.addItem("all included images", "included")
        self.size_scope.setToolTip("Which images Set sizes changes. Sizes read from a file are kept unless you choose every included image.")
        calibration.addWidget(QLabel("X"))
        calibration.addWidget(self.pixel_x)
        calibration.addWidget(QLabel("Y"))
        calibration.addWidget(self.pixel_y)
        calibration.addWidget(QLabel("Z step"))
        calibration.addWidget(self.pixel_z)
        calibration.addWidget(apply_cal)
        layout.addLayout(calibration)
        scope_row = QHBoxLayout()
        scope_row.addWidget(QLabel("Apply to"))
        scope_row.addWidget(self.size_scope, 1)
        layout.addLayout(scope_row)

    def refresh(self) -> None:
        self.refresh_table()
        controller = self.shell.controller
        if controller is None:
            self.channel_order.setText("No image open.")
            return
        image_id = self.shell._shown_image_id or controller.current_image_id
        if image_id:
            try:
                self.show_channel_order(controller.experiment.image(image_id))
                return
            except KeyError:
                pass
        if controller.experiment.images:
            self.show_channel_order(controller.experiment.images[0])
        else:
            self.channel_order.setText("No image open.")

    def show_channel_order(self, record) -> None:
        names = list(record.channel_names or ())
        count = record.number_of_channels or len(names)
        if not count:
            self.channel_order.setText("No channel information in the file.")
            return
        order = format_channel_order_text(names, count)
        path = record.relative_path or record.filename
        self.channel_order.setText(f"Channels: {order}")
        self.channel_order.setToolTip(
            f"{path}\nChannel order comes from each image file. Experimental conditions come from "
            "Sample name and Folder columns, not from renaming channels."
        )

    def refresh_table(self) -> None:
        controller = self.shell.controller
        self.table.blockSignals(True)
        self.table.clear()
        if controller is None:
            self.table.blockSignals(False)
            return
        details = ["Slices", "Channels", "µm/pixel", "Objective"]
        columns = ["Include", "Image", "Sample name", *details, *controller.experiment.metadata_columns, "Status"]
        self.table.setColumnCount(len(columns))
        self.table.setHorizontalHeaderLabels(columns)
        records = controller.experiment.images
        self.table.setRowCount(len(records))
        for row, record in enumerate(records):
            include = QTableWidgetItem()
            include.setCheckState(Qt.Checked if record.include else Qt.Unchecked)
            include.setData(Qt.UserRole, record.image_id)
            self.table.setItem(row, 0, include)
            label = record.relative_path or record.filename
            if record.positions_in_file > 1:
                label += f" [position {record.position + 1}]"
            image_item = _read_only(label)
            image_item.setToolTip(record.source_path)
            self.table.setItem(row, 1, image_item)
            self.table.setItem(row, 2, QTableWidgetItem(record.sample_name))
            channels = ", ".join(record.channel_names) if record.channel_names else str(record.number_of_channels or "")
            values = [
                (
                    f"{record.z_planes} × {record.pixel_size_z:g} µm"
                    if record.z_planes > 1 and record.pixel_size_z
                    else str(record.z_planes or 1)
                ),
                channels,
                f"{record.pixel_size_x:.3f}" if record.pixel_size_x else "not set",
                record.objective,
            ]
            for offset, value in enumerate(values):
                self.table.setItem(row, 3 + offset, _read_only(value))
            first_meta = 3 + len(details)
            for offset, column in enumerate(controller.experiment.metadata_columns):
                self.table.setItem(row, first_meta + offset, QTableWidgetItem(str(record.user_metadata.get(column, ""))))
            status = _status_text(record)
            if record.last_message and record.last_result == "Failure":
                status = f"{status}: {record.last_message}"
            status_item = _read_only(status)
            status_item.setToolTip(record.last_message or status)
            self.table.setItem(row, len(columns) - 1, status_item)
        header = self.table.horizontalHeader()
        for column in range(self.table.columnCount()):
            if self.table.columnWidth(column) > 220:
                self.table.setColumnWidth(column, 220)
        header.setStretchLastSection(True)
        self.table.blockSignals(False)
        self._filter(self.filter_box.text())
        self._update_included_label()
        self.shell._release_window_later()

    def set_notices(self, notices: list[str]) -> None:
        self.notices.setText("<br>".join(f"• {text}" for text in notices))
        self.notices.setVisible(bool(notices))

    def set_pixel_size(self, x, y, z=None) -> None:
        self.pixel_x.setValue(float(x or 0))
        self.pixel_y.setValue(float(y or 0))
        self.pixel_z.setValue(float(z or 0))

    def apply_cell_edit(self, row: int, column: int) -> None:
        controller = self.shell.controller
        if controller is None:
            return
        image_item = self.table.item(row, 0)
        if image_item is None:
            return
        image_id = image_item.data(Qt.UserRole)
        record = controller.experiment.image(image_id)
        header = self.table.horizontalHeaderItem(column).text()
        if header in {"Image", "Slices", "Channels", "µm/pixel", "Objective", "Status"}:
            return
        if header == "Include":
            controller.set_included(image_id, image_item.checkState() == Qt.Checked)
            self._update_included_label()
            self.shell.update_navigation()
        elif header == "Sample name":
            controller.set_sample_name(image_id, self.table.item(row, column).text())
        elif header in controller.experiment.metadata_columns:
            controller.set_metadata(image_id, header, self.table.item(row, column).text())
        else:
            return
        record.sample_name = controller.experiment.image(image_id).sample_name
        self.shell._autosave()

    def paste_into(self, row: int, column: int, text: str) -> None:
        controller = self.shell.controller
        if controller is None:
            return
        lines = [line.split("\t") for line in text.strip().splitlines() if line.strip()]
        headers = [self.table.horizontalHeaderItem(index).text() for index in range(self.table.columnCount())]
        for row_offset, values in enumerate(lines):
            target_row = row + row_offset
            if target_row >= self.table.rowCount():
                break
            for col_offset, value in enumerate(values):
                target_column = column + col_offset
                if target_column >= len(headers):
                    name = f"Metadata {target_column - 2}"
                    controller.add_metadata_column(name)
                    self.refresh_table()
                    headers = [self.table.horizontalHeaderItem(index).text() for index in range(self.table.columnCount())]
                item = self.table.item(target_row, target_column)
                if item is not None and headers[target_column] not in {"Image", "Slices", "Channels", "µm/pixel", "Objective", "Status"}:
                    item.setText(value)
                    self.apply_cell_edit(target_row, target_column)

    def _open(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Open experiment")
        if directory:
            self.shell.open_experiment(directory)

    def _add_images(self) -> None:
        controller = self.shell.require_controller()
        if controller is None:
            return
        start = controller.experiment.input_directory or ""
        patterns = " ".join(
            pattern for kind, pattern in (("nd2", "*.nd2"), ("tiff", "*.tif *.tiff")) if kind in self.file_types()
        )
        paths, _ = QFileDialog.getOpenFileNames(self, "Choose images", start, f"Images ({patterns});;All images (*.nd2 *.tif *.tiff)")
        if paths:
            self._import(controller, paths)

    def _add_folder(self) -> None:
        controller = self.shell.require_controller()
        if controller is None:
            return
        start = controller.experiment.input_directory or ""
        directory = choose_existing_directory(
            self,
            "Select a folder of images (highlight it, then Choose; subfolders are included)",
            start,
        )
        if directory:
            self._import(controller, [directory])

    def _import(self, controller, paths) -> None:
        """Read the image list off the interface thread: a large folder takes a while to scan."""

        self.shell._footer.message("Looking for images (subfolders are included)...")

        def listed(notices) -> None:
            self.shell._refresh_all()
            self.set_notices(notices)
            self.shell._footer.message(notices[0] if notices else "No new images were added.")

        types = self.file_types()
        self.shell._start_job(lambda: controller.add_image_paths(paths, file_types=types), listed)

    def _apply_pixel_size(self) -> None:
        controller = self.shell.controller
        if controller is None or not self.shell._nav_ids:
            return
        image_id = self.shell._nav_ids[self.shell._nav_index]
        x = self.pixel_x.value() or None
        y = self.pixel_y.value() or None
        z = self.pixel_z.value()
        scope = self.size_scope.currentData()
        targets = [image_id]
        if scope == "included":
            targets += [record.image_id for record in controller.experiment.images if record.include]
        elif scope == "missing":
            targets += [record.image_id for record in controller.experiment.images if not record.pixel_size_x]
        targets = list(dict.fromkeys(targets))
        for target in targets:
            controller.set_pixel_size(target, x, y, z)
        self.refresh_table()
        self.shell._objects_panel.update_recommendation()
        self.shell._footer.set_units("µm" if x and y else "pixels — no pixel size in this image", keep_detail=True)
        self.shell.message(f"Pixel size set for {len(targets)} image{'s' if len(targets) != 1 else ''}.")
        self.shell._autosave()

    def _filter(self, text: str) -> None:
        query = text.casefold()
        kind = self.show_type.currentData() if hasattr(self, "show_type") else "all"
        suffixes = {"nd2": (".nd2",), "tiff": (".tif", ".tiff")}.get(kind)
        for row in range(self.table.rowCount()):
            file_item = self.table.item(row, 1)
            sample_item = self.table.item(row, 2)
            haystack = " ".join(
                item.text() for item in (file_item, sample_item) if item is not None
            ).casefold()
            hidden = bool(query) and query not in haystack
            if suffixes and file_item is not None:
                path = (file_item.toolTip() or file_item.text()).casefold()
                hidden = hidden or not path.endswith(suffixes)
            self.table.setRowHidden(row, hidden)

    def file_types(self) -> list[str]:
        """The file types Add images and Add folder take: those chosen when the experiment was made."""

        controller = self.shell.controller
        return list(controller.experiment.import_file_types) if controller and controller.experiment.import_file_types else ["nd2", "tiff"]

    def selected_ids(self) -> list[str]:
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        return [self.table.item(row, 0).data(Qt.UserRole) for row in rows if self.table.item(row, 0) is not None]

    def _update_included_label(self) -> None:
        controller = self.shell.controller
        if controller is None:
            self.included_label.setText("")
            return
        total = len(controller.experiment.images)
        included = len(controller.included_ids())
        kinds = {}
        for record in controller.experiment.images:
            if record.include:
                kind = "ND2" if record.source_path.lower().endswith(".nd2") else "TIFF"
                kinds[kind] = kinds.get(kind, 0) + 1
        detail = ", ".join(f"{count} {kind}" for kind, count in sorted(kinds.items()))
        self.included_label.setText(
            f"<b>{included} of {total} images included</b>" + (f" ({detail})" if detail else "")
        )
        self.included_label.setToolTip("Run all images analyzes only included images.")


# One set of words for how each image of a run went, used everywhere a run is summarized.
JOB_WORDS = {"Success": "finished", "Warning": "needs a look", "Failure": "failed"}
QC_WORDS = {"success": "No problems found", "warning": "Needs a look (see Run log)"}


def run_outcome(completed: int, warnings: int, failed: int) -> str:
    """For example '7 finished, 1 needs a look, 1 failed'."""

    parts = [f"{completed} finished"]
    if warnings:
        parts.append(f"{warnings} need{'s' if warnings == 1 else ''} a look")
    if failed:
        parts.append(f"{failed} failed")
    return ", ".join(parts)


# Plain words for an image's state. A failed run and a run with warnings are both saved as
# needs_attention; last_result tells them apart.
STATUS_WORDS = {
    "not_analyzed": "Not run",
    "analyzed": "Analyzed",
    "needs_attention": "Needs a look",
    "reviewed": "Reviewed",
    "approved": "Approved",
    "excluded": "Left out",
    "running": "Running",
}


def _status_text(record) -> str:
    if record.last_result == "Failure" and record.processing_status == "needs_attention":
        return "Failed"
    return STATUS_WORDS.get(record.processing_status, record.processing_status.replace("_", " ").capitalize())


def _expression_cell(item: QTableWidgetItem | None) -> str:
    """The saved expression behind a result-row cell (its text for rows typed by older versions)."""

    if item is None:
        return ""
    return str(item.data(Qt.UserRole) or item.text()).strip()


def _read_only(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    return item


class ManifestTable(QTableWidget):
    def __init__(self, panel: ExperimentPanel):
        super().__init__()
        self.panel = panel
        self.setSortingEnabled(True)
        self.itemChanged.connect(lambda item: panel.apply_cell_edit(item.row(), item.column()))
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls()]
        controller = self.panel.shell.require_controller()
        if controller is not None and paths:
            # The same as Add images / Add folder: off the interface thread, with the ticked file types.
            self.panel._import(controller, paths)

    def keyPressEvent(self, event) -> None:
        from qtpy.QtGui import QKeySequence
        from qtpy.QtWidgets import QApplication

        if event.matches(QKeySequence.Paste):
            self.panel.paste_into(self.currentRow(), self.currentColumn(), QApplication.clipboard().text())
            return
        super().keyPressEvent(event)


class ObjectsPanel(QWidget):
    def __init__(self, shell: CellQuantWindow):
        super().__init__()
        self.shell = shell
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._form = form
        self.object_name = QLineEdit("Objects")
        self.channel = QComboBox()
        self.method = QComboBox()
        _fill_options(self.method, METHOD_OPTIONS)
        self.threshold_method = QComboBox()
        _fill_options(self.threshold_method, THRESHOLD_OPTIONS)
        self.threshold = QDoubleSpinBox()
        self.threshold.setMaximum(1e9)
        self.sigma = QDoubleSpinBox()
        self.sigma.setMaximum(100)
        guide.add_range_tip(self.sigma, "px")
        self.min_area = QDoubleSpinBox()
        self.max_area = QDoubleSpinBox()
        for box in (self.min_area, self.max_area):
            box.setSpecialValueText("none")  # 0 keeps every size
        for box in (self.min_area, self.max_area):
            box.setMaximum(1e12)
        self.min_area.setValue(DEFAULT_MIN_AREA_UM2)
        self.area_unit = QComboBox()
        _fill_options(self.area_unit, AREA_UNIT_OPTIONS)
        _choose(self.area_unit, "um2")
        self.nucleus_diameter_um = QDoubleSpinBox()
        self.nucleus_diameter_um.setRange(1.0, 50.0)
        self.nucleus_diameter_um.setDecimals(1)
        self.nucleus_diameter_um.setSingleStep(0.5)
        self.nucleus_diameter_um.setValue(DEFAULT_NUCLEUS_DIAMETER_UM)
        self.nucleus_diameter_um.setToolTip(
            "Typical nucleus diameter in micrometres. Most nuclei are about 5–7 µm across. "
            "Used as Cellpose's size hint when pixel size is known, and to suggest a debris floor in µm²."
        )
        guide.add_range_tip(self.nucleus_diameter_um, "µm")
        self.size_hint = QLabel("")
        self.size_hint.setWordWrap(True)
        self.nucleus_diameter_um.valueChanged.connect(lambda _value: self._update_size_hint())
        # Z-stacks: 2D (one slice / max projection), 2D + stitching, or true 3D.
        from cellquant.hardware import Z_OPTION_LABELS

        self._gpu_status: dict = {}
        self._recommended: str | None = None
        self.z_stack = QComboBox()
        for mode, text in Z_OPTION_LABELS.items():
            self.z_stack.addItem(text, mode)
        self.z_stack.setToolTip(
            "2D: one slice — only that plane.\n"
            "2D: max projection — brightest value through all slices (nuclei at different depths can merge).\n"
            "2D + stitching — segment each slice, then join overlapping outlines into 3D objects.\n"
            "True 3D — segment the whole volume at once (slow; needs closely spaced slices).\n"
            "Times are estimates for this computer."
        )
        self.z_slice = QSpinBox()
        self.z_slice.setRange(0, 1000)
        self.z_slice.setSpecialValueText("middle")
        self.z_slice.setToolTip("Which slice to analyze (1 = first). 'middle' uses the middle slice of each image.")
        guide.add_range_tip(self.z_slice)
        self.z_slice.setEnabled(False)
        z_row = QHBoxLayout()
        z_row.addWidget(self.z_stack, 1)
        self.z_slice_label = QLabel("Slice")
        z_row.addWidget(self.z_slice_label)
        z_row.addWidget(self.z_slice)
        z_row.setContentsMargins(0, 0, 0, 0)
        self.z_link = QDoubleSpinBox()
        self.z_link.setRange(0.05, 0.95)
        self.z_link.setSingleStep(0.05)
        self.z_link.setValue(0.25)
        self.z_link.setToolTip(
            "How much an outline must overlap the one in the next slice to be the same nucleus "
            "(intersection over union). Lower: fewer nuclei split across slices, but stacked nuclei may join. "
            "Higher: the reverse. 0.25 is Cellpose's usual value."
        )
        guide.add_range_tip(self.z_link)
        self.z_scale = QComboBox()
        self.z_scale.addItem("Whole stack (recommended)", "stack")
        self.z_scale.addItem("Each slice separately", "slice")
        self.z_scale.setToolTip(
            "Scale brightness (and automatic thresholds) once for the whole stack, so a dim top or bottom "
            "slice stays dim and its outlines match the neighboring slices. 'Each slice' is Cellpose's default."
        )
        self.z_min_slices = QSpinBox()
        self.z_min_slices.setRange(1, 100)
        self.z_min_slices.setToolTip("Remove objects found in fewer slices than this. 1 keeps everything; one-slice objects are flagged either way.")
        guide.add_range_tip(self.z_min_slices, "slices")
        self.z3d_box = QWidget()
        z3d = QFormLayout(self.z3d_box)
        z3d.setContentsMargins(0, 0, 0, 0)
        z3d.addRow("Link overlap", self.z_link)
        z3d.addRow("Brightness", self.z_scale)
        z3d.addRow("Minimum slices", self.z_min_slices)
        self.z3d_box.setVisible(False)
        self.z_recommend = QLabel("")
        self.z_recommend.setWordWrap(True)
        self.z_recommend.setTextFormat(Qt.RichText)
        self.z_recommend.setStyleSheet("QLabel { background: rgba(60, 130, 220, 0.14); border-radius: 6px; padding: 6px; }")
        self.z_use = QPushButton("Use recommended")
        self.z_use.setToolTip("Choose the Z option suggested for this computer.")
        self.z_use.clicked.connect(self.use_recommendation)
        self.z_recommend.setSizePolicy(_size_flag("Preferred"), _size_flag("MinimumExpanding"))
        self.z_recommend_box = QWidget()
        recommend_column = QVBoxLayout(self.z_recommend_box)
        recommend_column.setContentsMargins(0, 0, 0, 0)
        recommend_column.addWidget(self.z_recommend)
        use_row = QHBoxLayout()
        use_row.addStretch(1)
        use_row.addWidget(self.z_use)
        recommend_column.addLayout(use_row)
        self.z_recommend_box.setVisible(False)
        self.z_box = QWidget()
        z_column = QVBoxLayout(self.z_box)
        z_column.setContentsMargins(0, 0, 0, 0)
        z_column.addLayout(z_row)
        z_column.addWidget(self.z3d_box)
        self.z_stack.currentIndexChanged.connect(lambda _index: self._z_mode_changed())
        from cellquant.engines import CELLPOSE_CLASSIC, CELLPOSE_SAM, ENGINE_LABELS, cellpose_engine

        self.engine = cellpose_engine()
        # Whether a GPU was found, at the top of the page so it is seen before any run.
        self.gpu_label = QLabel("")
        self.gpu_label.setWordWrap(True)
        self.gpu_label.setTextFormat(Qt.RichText)
        # No GPU switch: Cellpose uses a usable GPU on its own; the banner says which is used.
        self.gpu_banner = QWidget()
        gpu_row = QHBoxLayout(self.gpu_banner)
        gpu_row.setContentsMargins(0, 0, 0, 0)
        gpu_row.addWidget(self.gpu_label, 1)
        self._show_gpu_banner("Checking for an NVIDIA GPU...", "neutral")
        layout.addWidget(self.gpu_banner)
        # One engine is installed per environment; the other is listed so users know it exists.
        self.engine_choice = QComboBox()
        for key in (CELLPOSE_SAM, CELLPOSE_CLASSIC):
            if self.engine.installed and key == self.engine.key:
                self.engine_choice.addItem(self.engine.label, key)
            else:
                self.engine_choice.addItem(f"{ENGINE_LABELS[key]} (not installed)", key)
                self.engine_choice.model().item(self.engine_choice.count() - 1).setEnabled(False)
        if self.engine.installed:
            self.engine_choice.setCurrentIndex(max(0, self.engine_choice.findData(self.engine.key)))
            self.engine_choice.setToolTip(
                "The Cellpose installed in this environment. To use the other one, start CellQuant with it "
                "(Open CellQuant.bat asks which one)."
            )
        else:
            self.engine_choice.insertItem(0, "Cellpose not installed", None)
            self.engine_choice.setCurrentIndex(0)
            self.engine_choice.setEnabled(False)
            self.engine_choice.setToolTip("Install Cellpose 3 or 4 to use the cellpose method.")
        self.engine_choice_label = QLabel("Cellpose engine")
        self.method.currentIndexChanged.connect(lambda _index: self.update_recommendation())
        self.method.currentIndexChanged.connect(lambda _index: self._method_changed())
        self.threshold_method.currentIndexChanged.connect(lambda _index: self._method_changed())
        # Root decisions first: what to segment, how, then how to treat Z, then size.
        form.addRow("Source channel", self.channel)
        form.addRow("Method", self.method)
        form.addRow(self.engine_choice_label, self.engine_choice)
        form.addRow("Z-stack mode", self.z_box)
        form.addRow(self.z_recommend_box)  # full width, so the explanation is readable
        form.addRow("Nucleus diameter (µm)", self.nucleus_diameter_um)
        form.addRow(self.size_hint)
        form.addRow("Size unit", self.area_unit)
        form.addRow("Min size", self.min_area)
        form.addRow("Max size", self.max_area)
        form.addRow("Threshold", self.threshold_method)
        form.addRow("Manual threshold", self.threshold)
        form.addRow("Smoothing sigma", self.sigma)
        layout.addLayout(form)
        self.crop = QCheckBox("Crop to the region of interest before finding objects")
        self.crop.setToolTip(
            "Find the area(s) holding the positive cells on the channels ticked below, grow them by the margin, "
            "and find objects only inside rectangles around them. Brightness scaling and thresholds still come "
            "from the whole image, and results stay in full-image coordinates. Off by default."
        )
        self.crop_box = QGroupBox("Region of interest")
        crop_form = QFormLayout(self.crop_box)
        self.crop_channels = QListWidget()
        self.crop_channels.setMaximumHeight(90)
        self.crop_channels.setToolTip(
            "The region must be positive on every ticked channel (AND). By default the channel of the results' "
            "denominator (for example the reporter)."
        )
        self.crop_margin = QDoubleSpinBox()
        self.crop_margin.setRange(0, 1000)
        self.crop_margin.setValue(50.0)
        self.crop_margin.setSuffix(" µm")
        guide.add_range_tip(self.crop_margin)
        self.crop_warning = QLabel("")
        self.crop_warning.setWordWrap(True)
        self.crop_warning.setStyleSheet("QLabel { color: #b9770e; }")
        crop_form.addRow("Positive on", self.crop_channels)
        crop_form.addRow("Margin", self.crop_margin)
        crop_form.addRow(self.crop_warning)
        self.crop_box.setVisible(False)
        self.crop.toggled.connect(self.crop_box.setVisible)
        self.crop.toggled.connect(lambda _checked: self._crop_changed())
        self.crop_channels.itemChanged.connect(lambda _item: self._crop_changed())
        layout.addWidget(self.crop)
        layout.addWidget(self.crop_box)
        self.advanced_toggle = QCheckBox("Advanced")
        self.advanced_box = QGroupBox("Advanced")
        self.advanced_box.setVisible(False)
        self.advanced_toggle.toggled.connect(self.advanced_box.setVisible)
        advanced = QFormLayout(self.advanced_box)
        self._advanced_form = advanced
        self.fill_holes = QCheckBox("Fill holes")
        self.fill_holes.setChecked(True)
        self.opening = QSpinBox()
        self.closing = QSpinBox()
        self.watershed = QCheckBox("Watershed")
        self.watershed_distance = QDoubleSpinBox()
        self.watershed_distance.setValue(5)
        self.compactness = QDoubleSpinBox()
        # Editable so a path to a trained model can be typed in.
        self.cellpose_model = QComboBox()
        self.cellpose_model.setEditable(True)
        self.cellpose_model.addItems(list(self.engine.models))
        self.diameter = QDoubleSpinBox()
        self.diameter.setRange(0, 10000)
        self.diameter.setSpecialValueText("from µm")
        self.diameter.setToolTip(
            "Optional Cellpose diameter in pixels. Leave at 0 to convert Nucleus diameter (µm) "
            "with each image's own pixel size, or to let Cellpose decide when pixel size is unknown."
        )
        guide.add_range_tip(self.diameter, "px")
        self.flow = QDoubleSpinBox()
        self.flow.setValue(0.4)
        self.cellprob = QDoubleSpinBox()
        self.cellprob.setRange(-6, 6)
        guide.add_range_tip(self.cellprob)
        self.watershed.toggled.connect(lambda _checked: self._method_changed())
        self.object_name.setToolTip("The name of the objects in the results, for example Nuclei.")
        advanced.addRow("Object set name", self.object_name)
        advanced.addRow(self.fill_holes)
        advanced.addRow("Opening radius (px)", self.opening)
        advanced.addRow("Closing radius (px)", self.closing)
        advanced.addRow(self.watershed)
        advanced.addRow("Watershed minimum distance", self.watershed_distance)
        advanced.addRow("Watershed compactness", self.compactness)
        advanced.addRow("Cellpose model", self.cellpose_model)
        advanced.addRow("Cellpose diameter (px)", self.diameter)
        advanced.addRow("Flow threshold", self.flow)
        advanced.addRow("Cell probability threshold", self.cellprob)
        layout.addWidget(self.advanced_toggle)
        layout.addWidget(self.advanced_box)
        actions = QHBoxLayout()
        preview = QPushButton("Preview")
        preview.clicked.connect(shell.preview_current)
        preview.setToolTip("Find objects in the part of the image on screen, to check the settings quickly.")
        actions.addWidget(preview)
        layout.addLayout(actions)
        layout.addStretch(1)
        self._update_size_hint()
        self._method_changed()

    def refresh(self) -> None:
        controller = self.shell.controller
        self.channel.clear()
        if controller is None:
            return
        for channel in controller.experiment.channels:
            labels = format_channel_labels([channel.channel_name], 1)
            self.channel.addItem(labels[0], channel.channel_index)
        recipe = controller.recipe
        self.object_name.setText(recipe.object_set.name)
        _choose(self.method, recipe.object_set.algorithm)
        channel_index = self.channel.findData(recipe.object_set.segmentation_channel)
        if channel_index >= 0:
            self.channel.setCurrentIndex(channel_index)
        self.z_stack.setCurrentIndex(max(0, self.z_stack.findData(recipe.z_stack)))
        self.z_slice.setValue(0 if recipe.z_index is None else recipe.z_index + 1)
        self.z_link.setValue(recipe.z_stitch_threshold)
        self.z_scale.setCurrentIndex(max(0, self.z_scale.findData(recipe.z_scale_brightness)))
        self.z_min_slices.setValue(recipe.z_min_slices)
        self._apply_z_enablement()
        parameters = recipe.object_set.parameters
        _choose(self.threshold_method, str(parameters.get("threshold_method", "otsu")))
        if parameters.get("threshold") is not None:
            self.threshold.setValue(float(parameters["threshold"]))
        self.sigma.setValue(float(parameters.get("sigma", 0)))
        self.fill_holes.setChecked(bool(parameters.get("fill_holes", True)))
        self.opening.setValue(int(parameters.get("opening_radius_px", 0)))
        self.closing.setValue(int(parameters.get("closing_radius_px", 0)))
        self.watershed.setChecked(bool(parameters.get("use_watershed", False)))
        self.watershed_distance.setValue(float(parameters.get("watershed_min_distance_px", 5)))
        self.compactness.setValue(float(parameters.get("watershed_compactness", 0)))
        self.cellpose_model.setCurrentText(str(parameters.get("model") or self.engine.default_model or ""))
        self._refresh_crop(controller)
        pixel = self._pixel_size_um()
        if parameters.get("diameter_um") is not None:
            self.nucleus_diameter_um.setValue(float(parameters["diameter_um"]))
        elif parameters.get("diameter_px") and pixel:
            self.nucleus_diameter_um.setValue(float(parameters["diameter_px"]) * float(pixel))
        else:
            self.nucleus_diameter_um.setValue(DEFAULT_NUCLEUS_DIAMETER_UM)
        # Advanced px override only when the recipe stored pixels without a µm value.
        if parameters.get("diameter_px") and parameters.get("diameter_um") is None:
            self.diameter.setValue(float(parameters["diameter_px"]))
        else:
            self.diameter.setValue(0)
        self.flow.setValue(float(parameters.get("flow_threshold", 0.4)))
        self.cellprob.setValue(float(parameters.get("cellprob_threshold", 0)))
        has_calibration = pixel is not None
        if parameters.get("min_area_um2") is not None:
            _choose(self.area_unit, "um2")
            self.min_area.setValue(float(parameters["min_area_um2"]))
        elif parameters.get("min_area_px") is not None:
            _choose(self.area_unit, "px")
            self.min_area.setValue(float(parameters["min_area_px"]))
        else:
            if has_calibration:
                _choose(self.area_unit, "um2")
                self.min_area.setValue(DEFAULT_MIN_AREA_UM2)
            else:
                _choose(self.area_unit, "px")
                self.min_area.setValue(0)
        if parameters.get("max_area_um2") is not None:
            self.max_area.setValue(float(parameters["max_area_um2"]))
        elif parameters.get("max_area_px") is not None:
            self.max_area.setValue(float(parameters["max_area_px"]))
        else:
            self.max_area.setValue(0)
        self._update_size_hint()
        self.update_recommendation()

    def _pixel_size_um(self) -> float | None:
        controller = self.shell.controller
        if controller is None:
            return None
        for record in controller.experiment.images:
            if record.include and record.pixel_size_x:
                return float(record.pixel_size_x)
        for record in controller.experiment.images:
            if record.pixel_size_x:
                return float(record.pixel_size_x)
        return None

    def _update_size_hint(self) -> None:
        diameter = self.nucleus_diameter_um.value()
        typical_area = math.pi * (diameter / 2.0) ** 2
        pixel = self._pixel_size_um()
        if pixel:
            px = diameter / pixel
            self.size_hint.setText(f"≈ {typical_area:.0f} µm², {px:.0f} px across")
            self.size_hint.setToolTip(
                f"A {diameter:g} µm nucleus is about {typical_area:.0f} µm² "
                f"(≈ {px:.0f} px across at {pixel:g} µm/pixel). "
                f"Min size defaults to {DEFAULT_MIN_AREA_UM2:g} µm² to drop debris."
            )
        else:
            self.size_hint.setText(f"≈ {typical_area:.0f} µm² (no pixel size)")
            self.size_hint.setToolTip(
                f"A {diameter:g} µm nucleus is about {typical_area:.0f} µm². "
                "Set µm/pixel in step 1 to convert this for Cellpose and to use µm² size filters."
            )

    def _method_changed(self) -> None:
        """Show only the settings the chosen method uses."""

        cellpose = self.method.currentData() == "cellpose"
        self.engine_choice_label.setVisible(cellpose)
        self.engine_choice.setVisible(cellpose)
        _show_row(self._form, self.threshold_method, not cellpose)
        _show_row(self._form, self.threshold, not cellpose and self.threshold_method.currentData() == "manual")
        _show_row(self._form, self.sigma, not cellpose)
        advanced = self._advanced_form
        for widget in (self.fill_holes, self.opening, self.closing, self.watershed):
            _show_row(advanced, widget, not cellpose)
        for widget in (self.watershed_distance, self.compactness):
            _show_row(advanced, widget, not cellpose and self.watershed.isChecked())
        for widget in (self.cellpose_model, self.diameter, self.flow, self.cellprob):
            _show_row(advanced, widget, cellpose)
        self._refresh_gpu_banner()

    def _refresh_crop(self, controller) -> None:
        from cellquant.crop import crop_channels

        recipe = controller.recipe
        spec = recipe.crop
        chosen = set(crop_channels(recipe))
        self.crop_channels.blockSignals(True)
        self.crop_channels.clear()
        for channel in controller.experiment.channels:
            item = QListWidgetItem(channel.channel_name)
            item.setData(Qt.UserRole, channel.channel_index)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if channel.channel_index in chosen else Qt.Unchecked)
            self.crop_channels.addItem(item)
        self.crop_channels.blockSignals(False)
        self.crop.blockSignals(True)
        self.crop.setChecked(bool(spec is not None and spec.enabled))
        self.crop.blockSignals(False)
        self.crop_box.setVisible(self.crop.isChecked())
        self.crop_margin.setValue(float(spec.margin_um) if spec is not None else 50.0)
        self._show_crop_warning()

    def crop_settings(self, recipe_data: dict) -> dict | None:
        """The crop settings on screen, for the recipe (None when never used)."""

        previous = recipe_data.get("crop") or {}
        channels = [
            int(self.crop_channels.item(row).data(Qt.UserRole))
            for row in range(self.crop_channels.count())
            if self.crop_channels.item(row).checkState() == Qt.Checked
        ]
        if not self.crop.isChecked() and not previous:
            return None
        return {**previous, "enabled": self.crop.isChecked(), "channels": channels or None, "margin_um": self.crop_margin.value()}

    def _crop_changed(self) -> None:
        controller = self.shell.controller
        if controller is None:
            return
        data = controller.recipe.model_dump(mode="json")
        data["crop"] = self.crop_settings(data)
        try:
            controller.set_recipe(data)
        except Exception as exc:  # noqa: BLE001 - shown to the user
            self.shell.message(str(exc))
            return
        self._show_crop_warning()

    def _show_crop_warning(self) -> None:
        controller = self.shell.controller
        warnings = controller.crop_warnings() if controller is not None else []
        self.crop_warning.setText(" ".join(f"⚠ {text}" for text in warnings))
        self.crop_warning.setVisible(bool(warnings))

    def _apply_z_enablement(self) -> None:
        """Keep Z-stack mode usable; never leave the combo stuck disabled after a run lock.

        Locking settings calls setEnabled(False) on each child. That sticks even after the
        parent row is re-enabled, so a refresh that only toggled z_box could leave the
        dropdown permanently greyed out.
        """

        controller = self.shell.controller
        has_stacks = bool(
            controller
            and any(record.z_planes > 1 for record in controller.experiment.images)
        )
        busy = self.shell.is_busy()
        self.z_box.setEnabled(True)
        self.z_recommend_box.setEnabled(True)
        self.z_stack.setEnabled(not busy)
        if not has_stacks:
            self.z_stack.setToolTip(
                "None of the listed images are Z-stacks yet. You can still choose a mode; "
                "it applies when a Z-stack is analyzed."
            )
        else:
            self.z_stack.setToolTip(
                "2D: one slice — only that plane.\n"
                "2D: max projection — brightest value through all slices (nuclei at different depths can merge).\n"
                "2D + stitching — segment each slice, then join overlapping outlines into 3D objects.\n"
                "True 3D — segment the whole volume at once (slow; needs closely spaced slices).\n"
                "Times are estimates for this computer."
            )
        self._z_mode_changed()

    def _z_mode_changed(self) -> None:
        mode = self.z_stack.currentData()
        busy = self.shell.is_busy()
        self.z_slice.setVisible(mode == "single_plane")
        self.z_slice_label.setVisible(mode == "single_plane")
        self.z_slice.setEnabled(not busy and mode == "single_plane")
        self.z3d_box.setVisible(mode in ("stitch_slices", "full_3d"))
        self.z_link.setEnabled(not busy and mode == "stitch_slices")
        self.z_scale.setEnabled(not busy and mode == "stitch_slices")  # whole-volume 3D always scales the whole stack
        self.z_min_slices.setEnabled(not busy and mode in ("stitch_slices", "full_3d"))
        self._show_recommendation_state()

    def recommendation(self):
        """The Z option suggested for this computer and these images, or None."""

        from cellquant.hardware import detect_hardware, recommend
        from cellquant.volume import anisotropy

        controller = self.shell.controller
        if controller is None:
            return None
        records = [record for record in controller.experiment.images if record.include] or controller.experiment.images
        stacks = [
            (record.z_planes, *(record.dimensions[:2] if len(record.dimensions) >= 2 else (1024, 1024)))
            for record in records
        ]
        ratios = [
            anisotropy(record.pixel_size_x, record.pixel_size_z)
            for record in records
            if record.z_planes > 1
        ]
        known = [value for value in ratios if value]
        return recommend(
            method=self.method.currentData(),
            engine=self.engine.key if self.engine.installed else None,
            use_gpu=self.use_gpu(),
            stacks=stacks,
            anisotropy=sorted(known)[len(known) // 2] if known else None,
            hardware=detect_hardware(self._gpu_status),
        )

    def update_recommendation(self) -> None:
        """Refresh the estimated time beside each Z option and the suggestion."""

        from cellquant.hardware import Z_OPTION_LABELS, format_seconds

        suggestion = self.recommendation()
        if suggestion is None:
            self._recommended = None
            for index in range(self.z_stack.count()):
                self.z_stack.setItemText(index, Z_OPTION_LABELS[self.z_stack.itemData(index)])
            self.z_recommend_box.setVisible(False)
            return
        self._recommended = suggestion.mode
        for index in range(self.z_stack.count()):
            mode = self.z_stack.itemData(index)
            item = suggestion.estimates[mode]
            text = f"{Z_OPTION_LABELS[mode]}  (~{format_seconds(item.seconds_per_image)}/image"
            text += ", measured)" if item.measured else ")"
            if item.warning:
                text += f"  ⚠ {item.warning}"
            if mode == suggestion.mode:
                text += "  ★ recommended"
            self.z_stack.setItemText(index, text)
        self.z_recommend.setText(f"<b>Recommended here:</b> {Z_OPTION_LABELS[suggestion.mode]}")
        self.z_recommend.setToolTip(suggestion.reason)
        self.z_recommend_box.setVisible(True)
        self._show_recommendation_state()

    def _show_recommendation_state(self) -> None:
        chosen = self.z_stack.currentData() == self._recommended
        self.z_use.setEnabled(not self.shell.is_busy() and self._recommended is not None and not chosen)
        self.z_use.setText("In use" if chosen else "Use recommended")

    def use_recommendation(self) -> None:
        if self._recommended is None:
            return
        self.z_stack.setCurrentIndex(max(0, self.z_stack.findData(self._recommended)))
        self.update_recommendation()
        self.shell.message(f"Z-stacks: using {self.z_stack.currentText().split('  (')[0]}.")

    def show_gpu_status(self, status: dict) -> None:
        """Called once PyTorch has been checked in the background."""

        self._gpu_status = dict(status)
        self._gpu_checked = True
        self._refresh_gpu_banner()
        self.shell.message(self._gpu_summary())
        self.update_recommendation()

    def use_gpu(self) -> bool:
        """Cellpose uses the GPU whenever this computer has a usable one."""

        return self.engine.installed and bool(self._gpu_status.get("available"))

    def _gpu_summary(self) -> str:
        status = self._gpu_status
        if not self.engine.installed:
            return "Cellpose is not installed, so no GPU is used; the classical method runs on the CPU."
        if status.get("available"):
            memory = f", {status['memory_gb']:.0f} GB" if status.get("memory_gb") else ""
            return f"NVIDIA GPU found: {status.get('name') or 'NVIDIA GPU'}{memory}."
        if status.get("nvidia_gpu"):
            return (
                f"NVIDIA GPU found ({status['nvidia_gpu']}), but CellQuant cannot use it yet, so Cellpose will run on the "
                f"CPU (slower). {status.get('reason', '')}"
            ).strip()
        return f"No usable GPU found; Cellpose will run on the CPU (slower). {status.get('reason', '')}".strip()

    def _show_gpu_banner(self, html: str, tone: str, tip: str = "") -> None:
        colors = {"good": "rgba(60, 170, 90, 0.22)", "warn": "rgba(217, 164, 0, 0.18)", "neutral": "rgba(128, 128, 128, 0.16)"}
        self.gpu_label.setText(html)
        self.gpu_label.setToolTip(tip)
        self.gpu_label.setStyleSheet(f"QLabel {{ background: {colors[tone]}; border-radius: 6px; padding: 6px; }}")

    def _refresh_gpu_banner(self) -> None:
        """Say plainly whether a GPU was found and whether Cellpose will use it."""

        if not getattr(self, "_gpu_checked", False):
            return
        status = self._gpu_status
        cellpose = self.method.currentData() == "cellpose"
        if not self.engine.installed:
            self._show_gpu_banner("Cellpose not installed: CPU only.", "neutral", self._gpu_summary())
        elif status.get("available"):
            memory = f" ({status['memory_gb']:.0f} GB)" if status.get("memory_gb") else ""
            found = f"<b>✔ NVIDIA GPU found:</b> {status.get('name') or 'NVIDIA GPU'}{memory}."
            if cellpose:
                self._show_gpu_banner(f"{found} Cellpose will run on the GPU.", "good")
            else:
                self._show_gpu_banner(found, "good", "The classical method does not use the GPU; Cellpose would.")
        elif status.get("nvidia_gpu"):
            self._show_gpu_banner(
                f"<b>⚠ NVIDIA GPU found but not usable.</b> Cellpose on CPU.",
                "warn",
                f"{status['nvidia_gpu']}: CellQuant cannot use it yet, so Cellpose will run on the CPU (much slower). "
                f"{status.get('reason', '')}".strip(),
            )
        else:
            reason = status.get("reason", "")
            self._show_gpu_banner(
                "<b>No usable GPU found.</b> Cellpose on CPU.", "warn", f"Cellpose will run on the CPU (slower). {reason}".strip()
            )

    def write_recipe(self) -> None:
        controller = self.shell.controller
        if controller is None:
            return
        data = controller.recipe.model_dump(mode="json")
        parameters = {
            "sigma": self.sigma.value(),
            "threshold_method": self.threshold_method.currentData(),
            "fill_holes": self.fill_holes.isChecked(),
            "opening_radius_px": self.opening.value(),
            "closing_radius_px": self.closing.value(),
            "use_watershed": self.watershed.isChecked(),
            "watershed_min_distance_px": self.watershed_distance.value(),
            "watershed_compactness": self.compactness.value(),
        }
        if self.threshold_method.currentData() == "manual":
            parameters["threshold"] = self.threshold.value()
        if self.min_area.value() > 0:
            key = "min_area_um2" if self.area_unit.currentData() == "um2" else "min_area_px"
            parameters[key] = self.min_area.value()
        if self.max_area.value() > 0:
            key = "max_area_um2" if self.area_unit.currentData() == "um2" else "max_area_px"
            parameters[key] = self.max_area.value()
        if self.method.currentData() == "cellpose":
            size_filters = {
                key: value
                for key, value in parameters.items()
                if key in {"min_area_px", "max_area_px", "min_area_um2", "max_area_um2"}
            }
            previous = data["object_set"].get("parameters") or {}
            local = self.engine.installed and self.engine.key is not None
            # The µm diameter is converted with each image's own pixel size when it runs;
            # a diameter in pixels (Advanced) is used as is in every image instead.
            diameter_px = self.diameter.value() or None
            diameter_um = None if diameter_px else self.nucleus_diameter_um.value()
            parameters = {
                # The engine is saved so these settings are not run under the other Cellpose.
                # Without Cellpose here (for example on a computer that only prepares cluster packages),
                # the engine and model already in the settings are kept.
                "engine": self.engine.key if local else previous.get("engine"),
                "model": (self.cellpose_model.currentText() or self.engine.default_model) if local else previous.get("model"),
                "diameter_um": diameter_um,
                "diameter_px": diameter_px,
                # Chosen from the hardware unless the settings name one; a named one is kept.
                **({"precision": previous["precision"]} if previous.get("precision") else {}),
                "flow_threshold": self.flow.value(),
                "cellprob_threshold": self.cellprob.value(),
                # Found on this computer; without Cellpose here, the saved value is kept.
                "gpu": self.use_gpu() if local else bool(previous.get("gpu", False)),
                **size_filters,
            }
        data["z_stack"] = self.z_stack.currentData() or "max_projection"
        data["z_index"] = None if self.z_stack.currentData() != "single_plane" or self.z_slice.value() == 0 else self.z_slice.value() - 1
        data["z_stitch_threshold"] = round(self.z_link.value(), 3)
        data["z_scale_brightness"] = self.z_scale.currentData() or "stack"
        data["z_min_slices"] = self.z_min_slices.value()
        data["crop"] = self.crop_settings(data)
        data["object_set"] = {
            "name": self.object_name.text() or "Objects",
            "segmentation_channel": int(self.channel.currentData() or 0),
            "algorithm": self.method.currentData(),
            "parameters": parameters,
        }
        controller.set_recipe(data)


class MeasurementsPanel(QWidget):
    def __init__(self, shell: CellQuantWindow):
        super().__init__()
        self.shell = shell
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Id", "Channel", "Region", "Statistic", "Background", "Pixel level"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        measurements_label = QLabel("Measurements")
        measurements_label.setToolTip("What is measured in each marker channel.")
        layout.addWidget(measurements_label)
        layout.addWidget(self.table)
        self.form = self._measurement_form()
        layout.addLayout(self.form)
        add = QPushButton("Add measurement")
        add.clicked.connect(self._add)
        remove = QPushButton("Remove measurement")
        remove.clicked.connect(self._remove)
        layout.addWidget(add)
        layout.addWidget(remove)
        markers_label = QLabel("Markers")
        markers_label.setToolTip("How each object is called positive or negative.")
        layout.addWidget(markers_label)
        self.classes = QTableWidget(0, 4)
        self.classes.setHorizontalHeaderLabels(["Name", "Measurement", "Cutoff", "Positive when"])
        layout.addWidget(self.classes)
        self.class_name = QLineEdit()
        self.class_measurement = QComboBox()
        self.class_measurement.currentIndexChanged.connect(lambda _index: self._class_measurement_changed())
        self.class_threshold = QDoubleSpinBox()
        self.class_threshold.setMaximum(1e12)
        self.class_threshold.setMinimum(-1e12)
        self.class_comparison = QComboBox()
        for text, value in COMPARISONS:
            self.class_comparison.addItem(text, value)
        self.class_comparison.setToolTip(
            "above: positive when the value is greater than the cutoff (equal is negative).\n"
            "at least: positive when the value is greater than or equal to the cutoff.\n"
            "For a percent-of-pixels measurement, 'at least' reads as 'at least N% of the pixels'."
        )
        row = QFormLayout()
        row.addRow("Name", self.class_name)
        row.addRow("Measurement", self.class_measurement)
        row.addRow("Cutoff", self.class_threshold)
        row.addRow("Positive when", self.class_comparison)
        layout.addLayout(row)
        class_buttons = QHBoxLayout()
        add_class = QPushButton("Add marker")
        add_class.clicked.connect(self._add_class)
        remove_class = QPushButton("Remove marker")
        remove_class.setToolTip("Remove the marker selected in the Markers table, and the result rows that use it.")
        remove_class.clicked.connect(self._remove_class)
        class_buttons.addWidget(add_class)
        class_buttons.addWidget(remove_class)
        layout.addLayout(class_buttons)

    def _measurement_form(self) -> QFormLayout:
        self.meas_channel = QComboBox()
        self.region = QComboBox()
        _fill_options(self.region, REGION_OPTIONS)
        self.region.currentIndexChanged.connect(lambda _index: self._statistic_changed())
        self.distance = QDoubleSpinBox()
        self.distance.setMaximum(10000)
        guide.add_range_tip(self.distance, "px")
        self.inner = QDoubleSpinBox()
        self.outer = QDoubleSpinBox()
        self.outer.setValue(2)
        self.statistic = QComboBox()
        _fill_options(self.statistic, STATISTIC_OPTIONS)
        self.statistic.setToolTip(
            "Percent of pixels above a level: the percent (0-100) of the region's pixels at or above the pixel level\n"
            "(after background correction, if any). Classify it with 'at least' and a minimum percent."
        )
        self.statistic.currentIndexChanged.connect(lambda _index: self._statistic_changed())
        self.pixel_level = QDoubleSpinBox()
        self.pixel_level.setRange(-1e12, 1e12)
        self.pixel_level.setDecimals(3)
        self.pixel_level.setToolTip("A pixel counts when its value is at or above this level (same units as the image).")
        self.use_high = QCheckBox("at most")
        self.pixel_level_high = QDoubleSpinBox()
        self.pixel_level_high.setRange(-1e12, 1e12)
        self.pixel_level_high.setDecimals(3)
        self.pixel_level_high.setToolTip("Optional: pixels brighter than this do not count (for example saturated spots).")
        self.use_high.toggled.connect(lambda _on: self._statistic_changed())
        high_row = QHBoxLayout()
        high_row.addWidget(self.use_high)
        high_row.addWidget(self.pixel_level_high)
        self.background = QComboBox()
        _fill_options(self.background, BACKGROUND_OPTIONS)
        self.background.currentIndexChanged.connect(lambda _index: self._statistic_changed())
        form = QFormLayout()
        self._measurement_rows = form
        form.addRow("Channel", self.meas_channel)
        form.addRow("Region", self.region)
        form.addRow("Distance (px)", self.distance)
        form.addRow("Ring inner (px)", self.inner)
        form.addRow("Ring outer (px)", self.outer)
        form.addRow("Statistic", self.statistic)
        form.addRow("Pixel level", self.pixel_level)
        self.high_row = QWidget()
        self.high_row.setLayout(high_row)
        high_row.setContentsMargins(0, 0, 0, 0)
        form.addRow("Upper pixel level", self.high_row)
        form.addRow("Background", self.background)
        self._statistic_changed()
        return form

    def _statistic_changed(self) -> None:
        """Show only the settings the chosen region, statistic and background use."""

        percent = self.statistic.currentData() == "percent_above"
        self.pixel_level.setEnabled(percent)
        self.use_high.setEnabled(percent)
        self.pixel_level_high.setEnabled(percent and self.use_high.isChecked())
        form = getattr(self, "_measurement_rows", None)
        if form is None:
            return  # still being built
        region = self.region.currentData()
        ring = region == "ring" or self.background.currentData() == "local_ring"
        _show_row(form, self.distance, region in ("eroded_object", "expanded_object"))
        _show_row(form, self.inner, ring)
        _show_row(form, self.outer, ring)
        _show_row(form, self.pixel_level, percent)
        _show_row(form, self.high_row, percent)

    def _class_measurement_changed(self) -> None:
        controller = self.shell.controller
        if controller is None:
            return
        spec = next((m for m in controller.recipe.measurements if m.id == self.class_measurement.currentData()), None)
        if spec is not None and spec.statistic == "percent_above":
            self.class_comparison.setCurrentIndex(self.class_comparison.findData("at_least"))
            if self.class_threshold.value() <= 0 or self.class_threshold.value() > 100:
                self.class_threshold.setValue(50.0)

    def refresh(self) -> None:
        controller = self.shell.controller
        self.meas_channel.clear()
        self.class_measurement.blockSignals(True)
        self.class_measurement.clear()
        if controller is None:
            self.class_measurement.blockSignals(False)
            return
        for channel in controller.experiment.channels:
            self.meas_channel.addItem(format_channel_labels([channel.channel_name], 1)[0], channel.channel_index)
        self.table.setRowCount(len(controller.recipe.measurements))
        for row, measurement in enumerate(controller.recipe.measurements):
            self.table.setItem(row, 0, QTableWidgetItem(measurement.id))
            self.table.setItem(row, 1, QTableWidgetItem(controller._channel_name(int(measurement.channel))))
            self.table.setItem(row, 2, QTableWidgetItem(_option_text(REGION_OPTIONS, measurement.region.type)))
            self.table.setItem(row, 3, QTableWidgetItem(_option_text(STATISTIC_OPTIONS, measurement.statistic)))
            self.table.setItem(row, 4, QTableWidgetItem(_option_text(BACKGROUND_OPTIONS, measurement.background.type)))
            level = ""
            if measurement.pixel_level is not None:
                level = f"≥ {measurement.pixel_level:g}"
                if measurement.pixel_level_high is not None:
                    level += f" and ≤ {measurement.pixel_level_high:g}"
            self.table.setItem(row, 5, QTableWidgetItem(level))
            self.class_measurement.addItem(_measurement_text(controller, measurement), measurement.id)
        self.class_measurement.blockSignals(False)
        self.classes.setRowCount(len(controller.recipe.classifications))
        for row, classification in enumerate(controller.recipe.classifications):
            self.classes.setItem(row, 0, QTableWidgetItem(classification.name))
            spec = next((m for m in controller.recipe.measurements if m.id == classification.measurement), None)
            measured = QTableWidgetItem(_measurement_text(controller, spec) if spec is not None else classification.measurement)
            measured.setData(Qt.UserRole, classification.measurement)
            measured.setFlags(measured.flags() & ~Qt.ItemIsEditable)
            self.classes.setItem(row, 1, measured)
            self.classes.setItem(row, 2, QTableWidgetItem(str(classification.threshold)))
            self.classes.setCellWidget(row, 3, self._comparison_box(classification.comparison))

    def _comparison_box(self, value: str) -> QComboBox:
        box = QComboBox()
        for text, data in COMPARISONS:
            box.addItem(text, data)
        box.setCurrentIndex(max(box.findData(value), 0))
        return box

    def _row_comparison(self, row: int) -> str:
        box = self.classes.cellWidget(row, 3)
        return str(box.currentData()) if isinstance(box, QComboBox) else "above"

    def write_recipe(self) -> None:
        controller = self.shell.controller
        if controller is None:
            return
        data = controller.recipe.model_dump(mode="json")
        measurements = []
        for row in range(self.table.rowCount()):
            existing = data["measurements"][row] if row < len(data["measurements"]) else None
            if existing:
                measurements.append(existing)
        data["measurements"] = measurements
        classifications = []
        for row in range(self.classes.rowCount()):
            name = self.classes.item(row, 0).text()
            measurement_item = self.classes.item(row, 1)
            measurement = str(measurement_item.data(Qt.UserRole) or measurement_item.text())
            try:
                threshold = float(self.classes.item(row, 2).text())
            except ValueError:
                raise RecipeValidationError(f"The cutoff of {name} must be a number.") from None
            previous = next((item for item in data["classifications"] if item["name"] == name), None)
            if previous:
                entry = dict(previous)
            else:
                # A new marker gets an id no other marker uses (a removed marker can leave a gap).
                taken = {item["id"] for item in data["classifications"]} | {item["id"] for item in classifications}
                number = row + 1
                while f"class_{number}" in taken:
                    number += 1
                entry = {"id": f"class_{number}"}
            entry.update(
                {
                    "name": name,
                    "measurement": measurement,
                    "method": "threshold",
                    "threshold": threshold,
                    "comparison": self._row_comparison(row),
                }
            )
            classifications.append(entry)
        data["classifications"] = classifications
        controller.set_recipe(data)

    def _add(self) -> None:
        controller = self.shell.require_controller()
        if controller is None:
            return
        self.write_recipe()
        data = controller.recipe.model_dump(mode="json")
        region_type = self.region.currentData()
        region: dict = {"type": region_type}
        if region_type in {"eroded_object", "expanded_object"}:
            region["distance_px"] = self.distance.value()
        elif region_type == "ring":
            region["inner_px"] = self.inner.value()
            region["outer_px"] = self.outer.value()
        background: dict = {"type": self.background.currentData()}
        if background["type"] == "local_ring":
            background["inner_px"] = self.inner.value()
            background["outer_px"] = self.outer.value()
        measurement_id = f"measurement_{len(data['measurements']) + 1}"
        entry = {
            "id": measurement_id,
            "channel": int(self.meas_channel.currentData() or 0),
            "region": region,
            "statistic": self.statistic.currentData(),
            "background": background,
        }
        if entry["statistic"] == "percent_above":
            entry["pixel_level"] = self.pixel_level.value()
            if self.use_high.isChecked():
                entry["pixel_level_high"] = self.pixel_level_high.value()
        data["measurements"].append(entry)
        try:
            controller.set_recipe(data)
        except CellQuantError as exc:
            self.shell.show_error(str(exc))
            return
        self.refresh()

    def _remove(self) -> None:
        controller = self.shell.controller
        if controller is None or self.table.currentRow() < 0:
            return
        row = self.table.currentRow()
        if row < 0 or row >= self.table.rowCount():
            self.shell.message("Click a row in the Measurements table first, then Remove measurement.")
            return
        self.write_recipe()
        data = controller.recipe.model_dump(mode="json")
        removed = data["measurements"][row]["id"]
        users = [item["name"] for item in data["classifications"] if item["measurement"] == removed]
        if users:
            self.shell.message(
                f"{removed} is used by {', '.join(users)}. Remove or change that classification first."
            )
            return
        del data["measurements"][row]
        try:
            controller.set_recipe(data)
        except (CellQuantError, ValueError) as exc:
            self.shell.show_error(str(exc))
            return
        self.refresh()

    def _add_class(self) -> None:
        if not self.class_name.text().strip():
            self.shell.message("Type a name for the marker first, for example OTX2.")
            return
        if self.class_measurement.currentData() is None:
            self.shell.message("Add a measurement first: a marker is called from one.")
            return
        row = self.classes.rowCount()
        self.classes.insertRow(row)
        self.classes.setItem(row, 0, QTableWidgetItem(self.class_name.text().strip()))
        measured = QTableWidgetItem(self.class_measurement.currentText())
        measured.setData(Qt.UserRole, self.class_measurement.currentData())
        measured.setFlags(measured.flags() & ~Qt.ItemIsEditable)
        self.classes.setItem(row, 1, measured)
        self.classes.setItem(row, 2, QTableWidgetItem(str(self.class_threshold.value())))
        self.classes.setCellWidget(row, 3, self._comparison_box(str(self.class_comparison.currentData())))
        self.class_name.clear()

    def _remove_class(self) -> None:
        controller = self.shell.controller
        if controller is None:
            return
        row = self.classes.currentRow()
        if row < 0 or row >= self.classes.rowCount():
            self.shell.message("Click a row in the Markers table first, then Remove marker.")
            return
        try:
            self.write_recipe()
        except (CellQuantError, ValueError) as exc:
            self.shell.show_error(str(exc))
            return
        data = controller.recipe.model_dump(mode="json")
        removed = data["classifications"][row]
        # Result rows name a marker by its id or its name, as one word of the expression.
        tokens = "|".join(re.escape(token) for token in {removed["id"], removed["name"]} if token)
        uses = re.compile(rf"(?<![^\s()])(?:{tokens})(?![^\s()])")
        used_by = [item for item in data["reports"] if uses.search(f"{item['numerator']} {item['denominator']}")]
        if used_by:
            answer = QMessageBox.question(
                self,
                "Remove marker?",
                f"{len(used_by)} result row(s) use {removed['name']} and will be removed too. Continue?",
            )
            if answer != QMessageBox.Yes:
                return
        data["classifications"] = [item for index, item in enumerate(data["classifications"]) if index != row]
        data["reports"] = [item for item in data["reports"] if item not in used_by]
        try:
            controller.set_recipe(data)
        except (CellQuantError, ValueError) as exc:
            self.shell.show_error(str(exc))
            return
        controller.save()
        self.refresh()
        self.shell._results_panel.refresh()
        self.shell._marker_setup.refresh()
        self.shell.message(f"Removed the marker {removed['name']}.")


class EditPanel(QWidget):
    """Step 3: fix the objects found in step 2 before markers are measured."""

    def __init__(self, shell: CellQuantWindow):
        super().__init__()
        self.shell = shell
        self._labels = None
        # The object the user clicked. napari's own selected_label starts at 1, so it is not used
        # until the user changes it; otherwise Delete object would remove object 1 unasked.
        self._picked: int | None = None
        self._click_bound = False
        layout = QVBoxLayout(self)
        self.selected = QLabel("Selected object: none")
        self.selected.setToolTip("Click an object in the image to select it.")
        layout.addWidget(self.selected)
        buttons = QHBoxLayout()
        for text, slot in (
            ("Delete object", self._delete),
            ("Restore object", self._restore),
            ("Undo", self._undo),
            ("Record drawn edits", self._commit),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            buttons.addWidget(button)
            if text == "Delete object":
                self.delete_button = button
            elif text == "Undo":
                self.undo_button = button
        layout.addLayout(buttons)
        layout.addStretch(1)

    def refresh(self) -> None:
        return

    def bind_labels(self, boundaries) -> None:
        self._labels = boundaries
        boundaries.events.selected_label.connect(self._on_selected)
        self.clear_selection()
        if not self._click_bound:
            # A plain click on the image picks the object under the pointer, whichever layer is active.
            self.shell.viewer.mouse_drag_callbacks.append(self._on_click)
            self._click_bound = True

    def clear_selection(self) -> None:
        self._picked = None
        self.selected.setText("Selected object: none")

    def _on_click(self, viewer, event):
        start = tuple(event.position)
        dragged = False
        yield
        while event.type == "mouse_move":
            dragged = True
            yield
        if dragged or self._labels is None or self._labels not in viewer.layers:
            return
        try:
            value = self._labels.get_value(start, view_direction=event.view_direction, dims_displayed=event.dims_displayed, world=True)
        except Exception:  # noqa: BLE001 - a click outside the image picks nothing
            value = None
        self.pick(int(value) if isinstance(value, (int, np.integer)) else 0)

    def pick(self, object_id: int) -> None:
        """Select an object for Delete / Restore (0 clears the selection)."""

        if object_id <= 0:
            self.clear_selection()
            return
        self._picked = object_id
        self.selected.setText(f"Selected object: {object_id}")

    def _on_selected(self, event) -> None:
        # Changed by napari's picker tool on the Objects layer: the same as clicking the object.
        value = int(event.value) if hasattr(event, "value") else int(self._labels.selected_label)
        self.pick(value)

    def _selected_id(self) -> int | None:
        return self._picked

    def _delete(self) -> None:
        self._edit("delete")

    def _restore(self) -> None:
        self._edit("restore")

    def _edit(self, kind: str) -> None:
        controller = self.shell.require_controller()
        object_id = self._selected_id()
        if controller is None or not self.shell._nav_ids:
            return
        if object_id is None:
            self.shell.message("Click an object in the image first, then Delete object or Restore object.")
            return
        image_id = self.shell._nav_ids[self.shell._nav_index]

        def finish(result) -> None:
            self.shell.show_result(result)

        if kind == "delete":
            self.shell._start_job(lambda: controller.delete_object(image_id, object_id), finish)
        else:
            self.shell._start_job(lambda: controller.restore_object(image_id, object_id), finish)

    def _undo(self) -> None:
        controller = self.shell.require_controller()
        if controller is None or not self.shell._nav_ids:
            return
        image_id = self.shell._nav_ids[self.shell._nav_index]
        self.shell._start_job(lambda: controller.undo(image_id), lambda result: self.shell.show_result(result) if result is not None else None)

    def _commit(self) -> None:
        controller = self.shell.require_controller()
        if controller is None or "Objects" not in self.shell.viewer.layers or not self.shell._nav_ids:
            return
        drawn = np.asarray(self.shell.viewer.layers["Objects"].data)
        image_id = self.shell._nav_ids[self.shell._nav_index]
        self.shell._start_job(
            lambda: controller.commit_drawn_labels(image_id, drawn),
            self.shell.show_result,
        )



class ReviewPanel(QWidget):
    def __init__(self, shell: CellQuantWindow):
        super().__init__()
        self.shell = shell
        self._labels = None
        self._fills = None
        layout = QVBoxLayout(self)
        self.display = QComboBox()
        self.display.currentIndexChanged.connect(lambda _index: self._recolor())
        layout.addWidget(QLabel("Display objects by"))
        layout.addWidget(self.display)
        # The cutoff, set like napari's opacity: drag the slider or type the number.
        from superqt import QLabeledDoubleSlider

        self.cutoff_label = QLabel("Cutoff")
        self.cutoff_label.setToolTip("Drag until the right objects are positive.")
        layout.addWidget(self.cutoff_label)
        self.threshold_slider = QLabeledDoubleSlider(Qt.Horizontal)
        self.threshold_slider.setRange(0.0, 1.0)
        self.threshold_slider.valueChanged.connect(self._slider_moved)
        layout.addWidget(self.threshold_slider)
        # Recolor at most every 40 ms while dragging, with the latest value.
        self._pending_cutoff: float | None = None
        self._cutoff_timer = QTimer(self)
        self._cutoff_timer.setSingleShot(True)
        self._cutoff_timer.setInterval(40)
        self._cutoff_timer.timeout.connect(self._apply_pending_cutoff)
        layout.addWidget(self._pixel_level_box())
        self.counts = QLabel("Positive: 0\nNegative: 0\nPercent positive: —")
        layout.addWidget(self.counts)
        layout.addWidget(self._count_area_box())
        self.status_line = QLabel("")
        layout.addWidget(self.status_line)
        self.approve_button = QPushButton("Approve")
        self.approve_button.clicked.connect(lambda _checked=False: self._set_status("approved"))
        layout.addWidget(self.approve_button)
        # Previous / Next image go through only these images until "Check all included images".
        queue_buttons = QHBoxLayout()
        queue_buttons.addWidget(QLabel("Check"))
        for text, mode, tip in (
            ("Needs a look", "flagged", "Previous / Next image go through only images with warnings."),
            ("Failed", "failed", "Previous / Next image go through only images that failed."),
            ("All included", "all", "Previous / Next image go through every included image again."),
        ):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(lambda _checked=False, value=mode: self.shell._results_panel._filter_nav(value))
            queue_buttons.addWidget(button)
        layout.addLayout(queue_buttons)
        self.advanced_toggle = QCheckBox("Advanced")
        self.advanced_box = QGroupBox("Advanced")
        self.advanced_box.setVisible(False)
        self.advanced_toggle.toggled.connect(self.advanced_box.setVisible)
        advanced = QVBoxLayout(self.advanced_box)
        colors = QHBoxLayout()
        self.positive_color = QPushButton("Positive color")
        self.negative_color = QPushButton("Negative color")
        self.positive_color.setToolTip("Color of positive objects (at or past the cutoff) in the image.")
        self.negative_color.setToolTip("Color of negative objects in the image.")
        self.positive_color.clicked.connect(lambda: self._choose_color(positive=True))
        self.negative_color.clicked.connect(lambda: self._choose_color(positive=False))
        reset = QPushButton("Default colors")
        reset.setToolTip("Green for positive, magenta for negative (both easy to tell apart with color blindness).")
        reset.clicked.connect(self._reset_colors)
        for button in (self.positive_color, self.negative_color, reset):
            colors.addWidget(button)
        advanced.addLayout(colors)
        self._show_colors()
        layout.addWidget(self.advanced_toggle)
        layout.addWidget(self.advanced_box)
        self.qc = QLabel("")
        self.qc.setWordWrap(True)
        layout.addWidget(self.qc)
        layout.addStretch(1)

    def _count_area_box(self) -> QGroupBox:
        """Optional area to count in, drawn on the image after objects are found."""

        box = QGroupBox("Count area (optional) ⓘ")
        box.setToolTip(
            "Count only objects whose center is inside the drawn area, for example to skip damaged tissue. "
            "Objects outside stay in objects.csv with in_count_area = False. No area: the whole image counts. "
            "Not the same as cropping in step 2, which limits where objects are found."
        )
        row = QHBoxLayout(box)
        for text, slot, tip in (
            ("Draw", self._draw_count_area, "Draw polygons in the Count area layer: click the corners, double-click to finish."),
            ("Use for this image", lambda: self._use_count_area(every_image=False), "Count only inside the drawn area in this image."),
            ("Use for all images", lambda: self._use_count_area(every_image=True), "Count only inside the drawn area in every image, at the same pixel positions."),
            ("Clear", self._clear_count_area, "Remove this image's count area, so the whole image counts."),
        ):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            row.addWidget(button)
        return box

    def _draw_count_area(self) -> None:
        if self.shell.require_controller() is None or not self.shell._nav_ids:
            return
        layer = self.shell.count_area_layer()
        self.shell.viewer.layers.selection.active = layer
        layer.mode = "add_polygon"

    def _use_count_area(self, every_image: bool) -> None:
        controller = self.shell.require_controller()
        if controller is None or not self.shell._nav_ids:
            return
        polygons = drawn_polygons(self.shell.viewer.layers[COUNT_AREA_LAYER]) if COUNT_AREA_LAYER in self.shell.viewer.layers else []
        if not polygons:
            self.shell.message("Click Draw and outline the area to count first.")
            return
        image_id = self.shell._nav_ids[self.shell._nav_index]
        targets = [record.image_id for record in controller.experiment.images] if every_image else [image_id]
        self._set_count_area(targets, polygons)
        self.shell.message(
            "Every image now counts only inside this area." if every_image else "This image now counts only inside this area."
        )

    def _clear_count_area(self) -> None:
        controller = self.shell.require_controller()
        if controller is None or not self.shell._nav_ids:
            return
        self._set_count_area([self.shell._nav_ids[self.shell._nav_index]], [])
        self.shell.message("This image counts the whole image again.")

    def _set_count_area(self, image_ids: list[str], polygons) -> None:
        controller = self.shell.controller
        image_id = self.shell._nav_ids[self.shell._nav_index]
        try:
            controller.set_count_area(image_ids, polygons)
        except CellQuantError as exc:
            self.shell.show_error(str(exc))
            return
        self.shell.show_count_area(controller.experiment.image(image_id))
        self.shell._experiment_panel.refresh_table()
        result = controller.last_results.get(image_id)
        if result is not None:
            self.shell.show_classification(result)

    def _pixel_level_box(self) -> QGroupBox:
        """Shown for a marker called by the percent of its pixels: the two numbers that decide it."""

        box = QGroupBox("Percent-of-cell rule")
        form = QFormLayout(box)
        self.min_percent = QDoubleSpinBox()
        self.min_percent.setRange(0.0, 100.0)
        self.min_percent.setDecimals(1)
        self.min_percent.setSingleStep(1.0)
        self.min_percent.setSuffix(" %")
        self.min_percent.setKeyboardTracking(False)
        self.min_percent.setToolTip("A cell is positive when at least this percent of its pixels pass the pixel level.")
        guide.add_range_tip(self.min_percent)
        self.min_percent.valueChanged.connect(self._min_percent_changed)
        self.pixel_level = QDoubleSpinBox()
        self.pixel_level.setRange(-1e12, 1e12)
        self.pixel_level.setDecimals(3)
        self.pixel_level.setToolTip("A pixel passes when its value is at or above this level (same units as the image).")
        self.use_high = QCheckBox("at most")
        self.pixel_level_high = QDoubleSpinBox()
        self.pixel_level_high.setRange(-1e12, 1e12)
        self.pixel_level_high.setDecimals(3)
        self.pixel_level_high.setToolTip("Optional: pixels brighter than this do not pass (for example saturated spots).")
        self.use_high.toggled.connect(self.pixel_level_high.setEnabled)
        self.pixel_level_high.setEnabled(False)
        high = QHBoxLayout()
        high.addWidget(self.use_high)
        high.addWidget(self.pixel_level_high)
        self.apply_level = QPushButton("Apply pixel level")
        self.apply_level.setToolTip("Measure this image again with the new pixel level. Objects and edits are kept.")
        self.apply_level.clicked.connect(self._apply_pixel_level)
        form.addRow("Min % of cell", self.min_percent)
        form.addRow("Pixel level", self.pixel_level)
        form.addRow("Upper pixel level", high)
        form.addRow(self.apply_level)
        box.setVisible(False)
        self.level_box = box
        return box

    def _current_classification(self):
        controller = self.shell.controller
        classification_id = self.classification_id()
        if controller is None or not classification_id:
            return None
        return next((item for item in controller.recipe.classifications if item.id == classification_id), None)

    def _current_percent_measurement(self):
        classification = self._current_classification()
        if classification is None:
            return None
        spec = next((m for m in self.shell.controller.recipe.measurements if m.id == classification.measurement), None)
        return spec if spec is not None and spec.statistic == "percent_above" else None

    def _show_level_box(self) -> None:
        spec = self._current_percent_measurement()
        self.level_box.setVisible(spec is not None)
        if spec is None:
            return
        classification = self._current_classification()
        for widget in (self.min_percent, self.pixel_level, self.pixel_level_high, self.use_high):
            widget.blockSignals(True)
        self.min_percent.setValue(float(classification.threshold))
        self.pixel_level.setValue(float(spec.pixel_level))
        self.use_high.setChecked(spec.pixel_level_high is not None)
        self.pixel_level_high.setEnabled(spec.pixel_level_high is not None)
        if spec.pixel_level_high is not None:
            self.pixel_level_high.setValue(float(spec.pixel_level_high))
        for widget in (self.min_percent, self.pixel_level, self.pixel_level_high, self.use_high):
            widget.blockSignals(False)

    def _min_percent_changed(self, value: float) -> None:
        classification = self._current_classification()
        if classification is None or float(classification.threshold) == float(value):
            return
        self._threshold_moved(float(value))

    def _apply_pixel_level(self) -> None:
        controller = self.shell.controller
        spec = self._current_percent_measurement()
        if controller is None or spec is None:
            return
        high = self.pixel_level_high.value() if self.use_high.isChecked() else None
        if high is not None and high < self.pixel_level.value():
            self.shell.message("The upper pixel level must be at least the pixel level.")
            return
        image_id = self.shell._nav_ids[self.shell._nav_index] if self.shell._nav_ids else None
        try:
            result = controller.update_pixel_levels(image_id, {spec.id: (self.pixel_level.value(), high)})
        except CellQuantError as exc:
            self.shell.show_error(str(exc))
            return
        controller.save()
        if result is not None:
            self.shell.show_classification(result)
        else:
            self._show_level_box()
        self.shell.message(
            "Pixel level changed. This image was measured again; other images use the new level when they are next run."
        )

    def refresh(self) -> None:
        return

    def show_result(self, result) -> None:
        self.update_status_line()
        self.display.blockSignals(True)
        current = self.display.currentData()
        self.display.clear()
        self.display.addItem("Objects", "")
        for classification in self.shell.controller.recipe.classifications:
            self.display.addItem(classification.name, classification.id)
        index = self.display.findData(current)
        if index >= 0:
            self.display.setCurrentIndex(index)
        self.display.blockSignals(False)
        self._show_cutoff(result)
        area = "—" if result.qc.median_area is None else f"{result.qc.median_area:.2f}"
        border = result.qc.fraction_touching_border
        border_text = "—" if border != border else f"{100 * border:.1f}%"
        area_unit = "µm²" if result.spatial_unit == "um" else "px²"
        self.qc.setText(
            f"Objects: {result.qc.n_objects}\n"
            f"Median area: {area} {area_unit}\n"
            f"Touching border: {border_text}\n"
            f"Checks: {QC_WORDS.get(result.qc.status, result.qc.status)}"
        )

    def classification_id(self) -> str:
        return str(self.display.currentData() or "")

    def bind_labels(self, boundaries, fills) -> None:
        self._labels = boundaries
        self._fills = fills
        viewer = self.shell.viewer
        # Boundaries start shown, fills and IDs hidden; after that the layer eye icons decide.
        for name, visible in (("Objects", True), ("Object fills", False), ("Object IDs", False)):
            if name in viewer.layers:
                viewer.layers[name].visible = visible

    def _show_cutoff(self, result) -> None:
        self._show_level_box()
        classification_id = self.classification_id()
        self.cutoff_label.setVisible(bool(classification_id))
        self.threshold_slider.setVisible(bool(classification_id))
        if not classification_id:
            return
        classification = next(item for item in self.shell.controller.recipe.classifications if item.id == classification_id)
        if classification.measurement not in result.objects.columns:
            return
        values = result.objects[classification.measurement].to_numpy(dtype=float)
        percent_rule = self._current_percent_measurement() is not None
        self._set_slider(values, float(classification.threshold), percent_rule)
        counts = self.shell.controller.histogram(
            result.provenance["image_id"], classification.measurement, classification.threshold, classification.comparison
        )
        percent = counts["percent_positive"]
        percent_text = "—" if percent != percent else f"{percent:.1f}%"
        rule = guide.describe_rule(self.shell.controller.recipe, classification)
        self.counts.setText(
            f"Positive when: {rule}\n"
            f"Positive: {counts['positive']}\nNegative: {counts['negative']}\nUnmeasured: {int(counts['n_missing'])}\nPercent positive: {percent_text}"
        )

    def _set_slider(self, values: np.ndarray, threshold: float, percent_rule: bool) -> None:
        """Range: 0-100 % for the percent rule, otherwise the objects' values (and the cutoff)."""

        if percent_rule:
            low, high, decimals = 0.0, 100.0, 1
            self.cutoff_label.setText("Cutoff (min % of cell)")
        else:
            finite = np.asarray(values, dtype=float)
            finite = finite[np.isfinite(finite)]
            low = float(min(finite.min(), threshold)) if finite.size else min(0.0, threshold)
            high = float(max(finite.max(), threshold)) if finite.size else max(1.0, threshold)
            if high <= low:
                high = low + 1.0
            decimals = 0 if high - low >= 100 else 2
            self.cutoff_label.setText("Cutoff")
        slider = self.threshold_slider
        slider.blockSignals(True)
        slider.setDecimals(decimals)
        slider.setRange(low, high)
        slider.setSingleStep((high - low) / 100)
        slider.setValue(threshold)
        slider.blockSignals(False)

    def _slider_moved(self, value: float) -> None:
        self._pending_cutoff = float(value)
        if not self._cutoff_timer.isActive():
            self._cutoff_timer.start()

    def _apply_pending_cutoff(self) -> None:
        value, self._pending_cutoff = self._pending_cutoff, None
        if value is not None:
            self._threshold_moved(value)

    def _show_colors(self) -> None:
        positive, negative = classification_colors()
        for button, color in ((self.positive_color, positive), (self.negative_color, negative)):
            text = "white" if QColor(color).lightness() < 140 else "black"
            button.setStyleSheet(f"QPushButton {{ background: {color}; color: {text}; }}")

    def _choose_color(self, positive: bool) -> None:
        from qtpy.QtWidgets import QColorDialog

        current_positive, current_negative = classification_colors()
        chosen = QColorDialog.getColor(
            QColor(current_positive if positive else current_negative),
            self,
            "Color of positive objects" if positive else "Color of negative objects",
        )
        if not chosen.isValid():
            return
        if positive:
            save_classification_colors(chosen.name(), current_negative)
        else:
            save_classification_colors(current_positive, chosen.name())
        self._colors_changed()

    def _reset_colors(self) -> None:
        save_classification_colors(DEFAULT_POSITIVE_COLOR, DEFAULT_NEGATIVE_COLOR)
        self._colors_changed()

    def _colors_changed(self) -> None:
        self._show_colors()
        self.shell.apply_classification_colors()

    def _threshold_moved(self, value: float) -> None:
        controller = self.shell.controller
        classification_id = self.classification_id()
        if controller is None or not classification_id or not self.shell._nav_ids:
            return
        image_id = self.shell._nav_ids[self.shell._nav_index]
        result = controller.update_thresholds(image_id, {classification_id: value})
        if result is not None:
            self.shell.show_classification(result)

    def _recolor(self) -> None:
        controller = self.shell.controller
        if controller is None or not self.shell._nav_ids:
            return
        image_id = self.shell._nav_ids[self.shell._nav_index]
        result = controller.last_results.get(image_id)
        if result is not None:
            self.shell.show_classification(result)

    def update_status_line(self) -> None:
        controller = self.shell.controller
        if controller is None or not self.shell._nav_ids:
            self.status_line.setText("")
            return
        included = [record for record in controller.experiment.images if record.include]
        done = sum(record.processing_status in ("approved", "reviewed") for record in included)
        current = controller.experiment.image(self.shell._nav_ids[self.shell._nav_index])
        state = "Approved" if current.processing_status in ("approved", "reviewed") else "Not checked"
        self.status_line.setText(f"{state} · {done} of {len(included)} approved")

    def _set_status(self, status: str) -> None:
        controller = self.shell.require_controller()
        if controller is None or not self.shell._nav_ids:
            return
        image_id = self.shell._nav_ids[self.shell._nav_index]
        controller.set_status(image_id, status)
        self.shell._autosave()
        self.shell._experiment_panel.refresh_table()
        self.shell._refresh_plan()
        self.update_status_line()
        self.shell.refresh_guidance()
        if not self.shell._footer._navigation[1].isEnabled():
            self.shell.message("Approved.")  # image navigation is locked while all analyses run
        elif self._open_next_unchecked():
            self.shell.message("Approved. Showing the next image to check.")
        else:
            self.shell.message("All images checked.")

    def _open_next_unchecked(self) -> bool:
        """Show the next image (after this one, wrapping round) that is not approved yet."""

        controller = self.shell.controller
        ids = self.shell._nav_ids
        for step in range(1, len(ids)):
            index = (self.shell._nav_index + step) % len(ids)
            if controller.experiment.image(ids[index]).processing_status not in ("approved", "reviewed"):
                self.shell._nav_index = index
                self.shell.show_current()
                return True
        return False


class ResultsPanel(QWidget):
    def __init__(self, shell: CellQuantWindow):
        super().__init__()
        self.shell = shell
        layout = QVBoxLayout(self)
        rows_label = QLabel("Result rows")
        rows_label.setToolTip("How many objects of one kind, among another.")
        layout.addWidget(rows_label)
        self.reports = QTableWidget(0, 2)
        self.reports.setHorizontalHeaderLabels(["Count", "Among"])
        self.reports.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.reports.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.reports)
        pick = QHBoxLayout()
        self.report_count = QComboBox()
        self.report_among = QComboBox()
        self.report_count.setToolTip("Which objects to count, for example OTX2+ or OTX2+ and VSX2-.")
        self.report_among.setToolTip("Out of which objects, for example all objects or Fluor+.")
        pick.addWidget(QLabel("Count"))
        pick.addWidget(self.report_count, 1)
        pick.addWidget(QLabel("among"))
        pick.addWidget(self.report_among, 1)
        layout.addLayout(pick)
        report_buttons = QHBoxLayout()
        add = QPushButton("Add result row")
        add.clicked.connect(self._add_report)
        remove = QPushButton("Remove result row")
        remove.clicked.connect(self._remove_report)
        report_buttons.addWidget(add)
        report_buttons.addWidget(remove)
        layout.addLayout(report_buttons)
        layout.addWidget(QLabel("This image"))
        self.phenotypes = QTableWidget(0, 3)
        self.phenotypes.setHorizontalHeaderLabels(["Kind of object", "Count", "Percent"])
        layout.addWidget(self.phenotypes)
        self.queue = QLabel("")
        self.queue.setWordWrap(True)
        layout.addWidget(self.queue)
        # Settings save automatically; loading them from an earlier export stays.
        settings = QHBoxLayout()
        load = QPushButton("Load settings…")
        load.setToolTip("Use the settings from a recipe.yaml file, for example from an earlier export.")
        load.clicked.connect(self._load_recipe)
        settings.addWidget(load)
        settings.addStretch(1)
        layout.addLayout(settings)
        # Summaries in the export: by group, and per biological unit (for example per retina).
        summaries = QHBoxLayout()
        group_label = QLabel("Group by")
        group_label.setToolTip("A column of step 1, for example Folder 1 (the condition). Export adds grouped_by_<column>.csv.")
        summaries.addWidget(group_label)
        self.group_by = QComboBox()
        self.group_by.setToolTip(group_label.toolTip())
        summaries.addWidget(self.group_by, 1)
        unit_label = QLabel("Unit")
        unit_label.setToolTip(
            "The biological unit, for example the retina (by default the folder that holds the images). Export adds "
            "units_by_<column>.csv (each unit's images pooled) and, when grouped, grouped_by_<group>_equal_units.csv: "
            "the mean of the units' percents, so every retina counts once, with n units and SD."
        )
        summaries.addWidget(unit_label)
        self.unit = QComboBox()
        self.unit.setToolTip(unit_label.toolTip())
        summaries.addWidget(self.unit, 1)
        layout.addLayout(summaries)

    def refresh(self) -> None:
        controller = self.shell.controller
        if controller is None:
            return
        names = {item.id: item.name for item in controller.recipe.classifications}
        self.reports.setRowCount(len(controller.recipe.reports))
        for row, report in enumerate(controller.recipe.reports):
            for column, expression in ((0, report.numerator), (1, report.denominator)):
                item = QTableWidgetItem(guide.readable_expression(expression, names))
                item.setData(Qt.UserRole, expression)
                self.reports.setItem(row, column, item)
        self._fill_report_choices(controller)
        self._fill_summary_options(controller)

    def _fill_report_choices(self, controller) -> None:
        """Each marker positive or negative, and pairs of two markers; 'all objects' for Among."""

        markers = controller.recipe.classifications
        choices = []
        for item in markers:
            choices.append((f"{item.name}+", item.id))
            choices.append((f"{item.name}-", f"NOT {item.id}"))
        for first_index, first in enumerate(markers):
            for second in markers[first_index + 1 :]:
                for first_sign in ("+", "-"):
                    for second_sign in ("+", "-"):
                        expression = " AND ".join(
                            (item.id if sign == "+" else f"NOT {item.id}") for item, sign in ((first, first_sign), (second, second_sign))
                        )
                        choices.append((f"{first.name}{first_sign} and {second.name}{second_sign}", expression))
        for box, extra in ((self.report_count, []), (self.report_among, [("all objects", "all_objects")])):
            box.clear()
            for text, value in [*extra, *choices]:
                box.addItem(text, value)

    def _fill_summary_options(self, controller) -> None:
        import pandas as pd

        from cellquant.storage import default_unit_column

        columns = list(controller.experiment.metadata_columns)
        default_unit = default_unit_column(pd.DataFrame(columns=columns))
        for box, first, keep in (
            (self.group_by, ("no grouping", None), self.group_by.currentData()),
            (self.unit, (f"automatic ({default_unit})" if default_unit else "automatic (none)", None), self.unit.currentData()),
        ):
            box.blockSignals(True)
            box.clear()
            box.addItem(*first)
            for column in columns:
                box.addItem(column, column)
            index = box.findData(keep)
            box.setCurrentIndex(index if index >= 0 else 0)
            box.blockSignals(False)

    def summary_options(self) -> dict:
        """``group_by`` and ``unit`` for exports, as chosen here (None: no grouping / the default unit)."""

        return {"group_by": self.group_by.currentData(), "unit": self.unit.currentData()}

    def write_reports(self) -> None:
        controller = self.shell.controller
        if controller is None:
            return
        data = controller.recipe.model_dump(mode="json")
        reports = []
        for row in range(self.reports.rowCount()):
            numerator = _expression_cell(self.reports.item(row, 0))
            denominator = _expression_cell(self.reports.item(row, 1))
            if numerator:
                reports.append({"numerator": numerator, "denominator": denominator or "all_objects"})
        data["reports"] = reports
        controller.set_recipe(data)

    def show_result(self, result) -> None:
        frame = result.phenotype_counts
        self.phenotypes.setRowCount(len(frame))
        for row_index, row in enumerate(frame.itertuples(index=False)):
            self.phenotypes.setItem(row_index, 0, QTableWidgetItem(str(row.phenotype)))
            self.phenotypes.setItem(row_index, 1, QTableWidgetItem(str(row.count)))
            self.phenotypes.setItem(row_index, 2, QTableWidgetItem(f"{row.percent_of_objects:.1f}"))

    def show_queue(self, report) -> None:
        self.queue.setText(f"Last run, {len(report.jobs)} images: {run_outcome(report.completed, report.warnings, report.failed)}.")

    def _add_report(self) -> None:
        if self.report_count.currentData() is None:
            self.shell.message("Set up markers first (step 4): result rows count marker-positive objects.")
            return
        row = self.reports.rowCount()
        self.reports.insertRow(row)
        for column, box in ((0, self.report_count), (1, self.report_among)):
            item = QTableWidgetItem(box.currentText())
            item.setData(Qt.UserRole, box.currentData())
            self.reports.setItem(row, column, item)
        self.shell.message(f"Added {self.report_count.currentText()} among {self.report_among.currentText()}. It is in the results from the next run.")

    def _remove_report(self) -> None:
        row = self.reports.currentRow()
        if row < 0:
            self.shell.message("Click a result row first, then Remove result row.")
            return
        self.reports.removeRow(row)

    def _run_selected(self) -> None:
        ids = self.shell._experiment_panel.selected_ids()
        if not ids:
            self.shell.message("Select images in the list first (click, Ctrl-click or Shift-click), or use Run all images.")
            return
        self.shell.start_batch(ids)

    def _run_all(self) -> None:
        self.shell.start_batch(None)

    def _load_recipe(self) -> None:
        controller = self.shell.require_controller()
        if controller is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Load settings", str(controller.directory), "Settings (*.yaml *.yml *.json)")
        if not path:
            return
        try:
            controller.import_recipe(path)
        except Exception as exc:  # noqa: BLE001 - a wrong or damaged file must not close the window
            self.shell.show_error(f"These settings could not be loaded from {path}. {_error_text(exc)}")
            return
        self.shell._refresh_all()
        self.shell.message(f"Settings loaded from {path}.")

    def _filter_nav(self, mode: str) -> None:
        controller = self.shell.controller
        if controller is None:
            return
        included = [record for record in controller.experiment.images if record.include]
        if mode == "all":
            self.shell.set_nav_filter(None)
            return
        if mode == "failed":
            ids = [record.image_id for record in included if record.last_result == "Failure"]
            words = "failed images"
        else:
            ids = [
                record.image_id
                for record in included
                if record.processing_status == "needs_attention" and record.last_result != "Failure"
            ]
            words = "images that need a look"
        if not ids:
            self.shell.message(f"No {words} among the included images.")
            return
        self.shell.set_nav_filter(words, ids)


class AnalysisBar(QWidget):
    """Which analysis is shown, and adding, renaming and removing analyses.

    An analysis is one set of settings (for example the channel objects are found in) with its own
    results, edits and review state. Every analysis uses the experiment's images.
    """

    def __init__(self, shell: CellQuantWindow):
        super().__init__()
        self.shell = shell
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 2)
        top = QHBoxLayout()
        top.addWidget(QLabel("Analysis:"))
        self.choice = QComboBox()
        self.choice.setToolTip(
            "Each analysis has its own settings (for example which channel objects are found in) and its own "
            "results, for the same images. Choose one to see, change or run it."
        )
        self.choice.currentIndexChanged.connect(self._chosen)
        top.addWidget(self.choice, 1)
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        for text, slot, tip in (
            (
                "Plan…",
                shell.show_plan,
                "Open the Plan: choose which images each analysis runs and which channel it finds objects in, "
                "for all images, a channel layout, a folder, or single images.",
            ),
            ("New analysis…", self._new, "A new analysis that starts with a copy of the current settings."),
            ("One per channel…", self._per_channel, "One analysis per channel: the current settings, finding objects in each channel."),
            ("Rename…", self._rename, "Rename the analysis shown."),
            ("Remove", self._remove, "Take the analysis shown off the list. Its saved results stay in the experiment folder."),
        ):
            action = menu.addAction(text)
            action.setToolTip(tip)
            action.triggered.connect(lambda _checked=False, slot=slot: slot())
            self.remove_button = action
        menu_button = QToolButton()
        menu_button.setText("Analyses ▾")
        menu_button.setToolTip("Plan, add, rename or remove analyses.")
        menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu_button.setMenu(menu)
        top.addWidget(menu_button)
        layout.addLayout(top)
        self.refresh()

    def refresh(self) -> None:
        controller = self.shell.controller
        self.choice.blockSignals(True)
        self.choice.clear()
        if controller is not None:
            for item in controller.analyses():
                self.choice.addItem(item.name, item.recipe_id)
            self.choice.setCurrentIndex(max(self.choice.findData(controller.recipe.recipe_id), 0))
        self.choice.blockSignals(False)
        several = controller is not None and len(controller.analyses()) > 1
        self.remove_button.setEnabled(several)
        footer = getattr(self.shell, "_footer", None)
        if footer is not None:
            footer.run_analyses.setVisible(several)
            footer.run_all.setText("Run all images (this analysis)" if several else "Run all images")
        summary = getattr(self.shell, "_results_summary", None)
        if summary is not None:
            summary.show_analysis_actions(several)

    def _chosen(self, _index: int) -> None:
        recipe_id = self.choice.currentData()
        if recipe_id:
            self.shell.switch_analysis(str(recipe_id))

    def _new(self) -> None:
        controller = self.shell.require_controller()
        if controller is None or self.shell.is_busy():
            return
        name, accepted = QInputDialog.getText(self, "New analysis", "Name:")
        if not accepted:
            return
        self.shell._panels_to_recipe()
        controller.add_analysis(name or "Analysis", activate=True)
        self.shell._refresh_all(keep_image=True)
        self.shell.message(
            f"New analysis '{controller.active_analysis().name}' (copy of the settings)."
        )
        self.shell.refresh_guidance()

    def _per_channel(self) -> None:
        controller = self.shell.require_controller()
        if controller is None or self.shell.is_busy():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("One analysis per channel")
        layout = QVBoxLayout(dialog)
        note = QLabel("Find objects in:")
        note.setToolTip(
            "Each ticked channel gets an analysis that finds objects in that channel, with the current "
            "settings otherwise (method, Z-stack mode, markers). A channel that already has one is not added again. "
            "Then click Run all analyses at the bottom."
        )
        layout.addWidget(note)
        boxes = []
        for channel in controller.experiment.channels:
            box = QCheckBox(channel.channel_name)
            box.setChecked(True)
            box.setProperty("channel_index", channel.channel_index)
            boxes.append(box)
            layout.addWidget(box)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        self._channel_dialog = dialog  # for tests
        self._channel_boxes = boxes
        if dialog.exec() != QDialog.Accepted:
            return
        chosen = [int(box.property("channel_index")) for box in boxes if box.isChecked()]
        self.make_per_channel(chosen)

    def make_per_channel(self, channels: list[int]) -> None:
        controller = self.shell.controller
        if controller is None or not channels:
            return
        self.shell._panels_to_recipe()
        before = {item.recipe_id for item in controller.analyses()}
        ids = controller.analyses_for_channels(channels)
        added = [item.name for item in controller.analyses() if item.recipe_id not in before]
        self.refresh()
        self.shell.message(
            (f"Added {', '.join(added)}. " if added else "Every ticked channel already has an analysis. ")
            + f"{len(ids)} analyses cover these channels."
        )

    def _rename(self) -> None:
        controller = self.shell.require_controller()
        if controller is None:
            return
        item = controller.active_analysis()
        name, accepted = QInputDialog.getText(self, "Rename analysis", "Name:", text=item.name)
        if accepted and name.strip():
            controller.rename_analysis(item.recipe_id, name)
            self.refresh()

    def _remove(self) -> None:
        controller = self.shell.require_controller()
        if controller is None or self.shell.is_busy() or len(controller.analyses()) <= 1:
            return
        item = controller.active_analysis()
        answer = QMessageBox.question(
            self,
            "Remove analysis?",
            f"Remove '{item.name}'? Saved results stay in the experiment folder.",
        )
        if answer != QMessageBox.Yes:
            return
        controller.remove_analysis(item.recipe_id)
        self.shell._refresh_all(keep_image=True)
        self.shell.refresh_guidance()


class Footer(QWidget):
    def __init__(self, shell: CellQuantWindow):
        super().__init__()
        self.shell = shell
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        previous = QPushButton("◀ Previous image")
        next_image = QPushButton("Next image ▶")
        run_current = QPushButton("Run this image")
        run_all = QPushButton("Run all images")
        self.run_analyses = QPushButton("Run all analyses")
        self.run_analyses.setToolTip("Run each analysis on the images ticked for it in the Plan (every included image unless you changed the Plan), one analysis after another.")
        self.run_analyses.clicked.connect(shell.run_all_analyses)
        self.run_analyses.setVisible(False)
        previous.setToolTip("Show the previous image in the experiment.")
        next_image.setToolTip("Show the next image in the experiment.")
        run_current.setToolTip("Find objects and measure markers in the image on screen.")
        run_all.setToolTip("Run every included image with the current settings.")
        previous.clicked.connect(lambda: self._step(-1))
        next_image.clicked.connect(lambda: self._step(1))
        self._navigation = (previous, next_image)
        run_current.clicked.connect(shell.run_current)
        run_all.clicked.connect(shell._results_panel._run_all)
        self.pause = QPushButton("Pause")
        self.resume = QPushButton("Resume")
        self.cancel = QPushButton("Cancel")
        self.pause.clicked.connect(self._pause)
        self.resume.clicked.connect(self._resume)
        self.cancel.clicked.connect(self._cancel)
        # Grouped: moving between images, running, and controlling a run.
        for button in (previous, next_image):
            row.addWidget(button)
        row.addSpacing(16)
        for button in (run_current, run_all, self.run_analyses):
            row.addWidget(button)
        row.addSpacing(16)
        for button in (self.pause, self.resume, self.cancel):
            row.addWidget(button)
        row.addSpacing(16)
        self.keep_awake = QCheckBox("Keep computer awake (recommended) ⓘ")
        self.keep_awake.setToolTip(
            "While a run is going, stop this computer from going to sleep. Sleep pauses the run "
            "until someone wakes the computer. The screen can still turn off."
        )
        self.keep_awake.setChecked(keep_awake_preferred())
        self.keep_awake.setVisible(keep_awake.supported())
        self.keep_awake.toggled.connect(shell._keep_awake_toggled)
        row.addWidget(self.keep_awake)
        self._run_buttons = {"current": run_current, "all": run_all}
        self.run_all = run_all
        self.cancel.setStyleSheet("QPushButton:enabled { color: #e05050; font-weight: bold; }")
        previous.setToolTip("Show the previous image in the experiment (Page Up).")
        next_image.setToolTip("Show the next image in the experiment (Page Down).")
        from qtpy.QtGui import QKeySequence

        try:
            from qtpy.QtWidgets import QShortcut
        except ImportError:  # Qt 6 keeps it in QtGui
            from qtpy.QtGui import QShortcut
        window = shell.viewer.window._qt_window
        for key, button in ((QKeySequence("PgUp"), previous), (QKeySequence("PgDown"), next_image)):
            shortcut = QShortcut(key, window)
            shortcut.activated.connect(lambda button=button: button.click() if button.isEnabled() else None)
        # A, Delete and Ctrl+Z work while the CellQuant panel has focus, but not in a text or number box.
        dock = shell._dock
        for key, button in (
            ("A", shell._review_panel.approve_button),
            ("Del", shell._edit_panel.delete_button),
            ("Ctrl+Z", shell._edit_panel.undo_button),
        ):
            shortcut = QShortcut(QKeySequence(key), dock)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda button=button: self._panel_key(button))
        self.failed_files: list[str] = []
        self.pause.setEnabled(False)
        self.resume.setEnabled(False)
        self.cancel.setEnabled(False)
        self.cancel.setToolTip("Stop the running analysis after the current step. Images already finished are kept.")
        self.pause.setToolTip("Pause a batch after the current step.")
        self.resume.setToolTip("Carry on with a paused batch.")
        layout.addLayout(row)
        self.position = QLabel("No image open.")
        self.units = QLabel("Units: pixels")
        self.progress = QProgressBar()
        self.time_left = QLabel("")
        self.time_left.setMinimumWidth(110)
        self.time_left.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._clock = None  # a TimeLeft while a run is going
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._show_time_left)
        self.status = QLabel("")
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(60)
        self.log.setMaximumHeight(160)
        self.log.setVisible(False)
        self.log_toggle = QPushButton("Log")
        self.log_toggle.setCheckable(True)
        self.log_toggle.setToolTip("Show or hide the full list of messages. The latest one is always shown beside the bar.")
        self.log_toggle.toggled.connect(self.log.setVisible)
        where = QHBoxLayout()
        where.addWidget(self.position)
        where.addWidget(self.units)
        where.addStretch(1)
        layout.addLayout(where)
        bar = QHBoxLayout()
        bar.addWidget(self.progress, 1)
        bar.addWidget(self.time_left)
        bar.addWidget(self.status, 2)
        bar.addWidget(self.log_toggle)
        layout.addLayout(bar)
        layout.addWidget(self.log)

    def set_navigation_enabled(self, enabled: bool) -> None:
        for button in self._navigation:
            button.setEnabled(enabled)

    def set_position(self, index: int, total: int, filename: str, only: str | None = None) -> None:
        if total == 0:
            self.position.setText("No included images (step 1).")
            return
        scope = f" ({only} only)" if only else ""
        self.position.setText(f"Image {index + 1} of {total}{scope}: {filename}")

    def set_units(self, unit: str, keep_detail: bool = False) -> None:
        """The units line; keep_detail keeps what follows it (for example 'Analyzed: …')."""

        separator = "   ·   "
        if keep_detail and separator in unit + self.units.text():
            current = self.units.text()
            if separator in current and separator not in unit:
                unit = unit + separator + current.split(separator, 1)[1]
        self.units.setText(f"Units: {unit}")

    def message(self, text: str) -> None:
        self.status.setText(text)
        self.log.append(text)

    def highlight(self, primary: str) -> None:
        """Make the run button this step expects stand out ('current' or 'all')."""

        for key, button in self._run_buttons.items():
            button.setStyleSheet(
                "QPushButton { background: rgba(60, 130, 220, 0.85); color: white; font-weight: bold; }"
                "QPushButton:disabled { background: rgba(60, 130, 220, 0.3); }"
                if key == primary
                else ""
            )

    def _show_time_left(self) -> None:
        """Time left beside the progress bar, refreshed every second; how it is worked out is in its tooltip."""

        from cellquant.hardware import format_seconds

        if self._clock is None:
            self.time_left.setText("")
            self.time_left.setToolTip("")
            return
        left = self._clock.seconds_left()
        if left is None:
            text = "Estimating…"
        elif left < 1:
            text = "Finishing…"
        else:
            text = f"~{format_seconds(left)} left"
        self.time_left.setText(text)
        self.time_left.setToolTip(self._clock.detail())

    def start_busy(self, batch: bool) -> None:
        from cellquant.progress import TimeLeft

        self._clock = TimeLeft()
        self._show_time_left()
        self._clock_timer.start()
        self.failed_files = []
        self._batch_index = 0
        self._batch_total = 0
        self._batch_name = ""
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        self.cancel.setEnabled(True)
        self.pause.setEnabled(batch)
        self.resume.setEnabled(False)

    def end_busy(self) -> None:
        self.progress.setRange(0, 1000)
        self.progress.setValue(1000 if self.progress.value() > 0 else 0)
        self.cancel.setEnabled(False)
        self.pause.setEnabled(False)
        self.resume.setEnabled(False)
        self._batch_total = 0
        self._clock_timer.stop()
        self._clock = None
        self._show_time_left()

    def show_step(self, text: str, fraction: float) -> None:
        """One step of the running analysis, e.g. 'Finding objects: slice 3 of 7'."""

        if self._clock is not None:
            self._clock.step(text, None if fraction < 0 else fraction)
            self._show_time_left()
        total = getattr(self, "_batch_total", 0)
        if total:
            done = (self._batch_index - 1 + max(fraction, 0.0)) / total
            self.progress.setRange(0, 1000)
            self.progress.setValue(int(round(1000 * done)))
            self.status.setText(f"Image {self._batch_index} of {total} ({self._batch_name}): {text}")
            return
        if fraction < 0:
            self.progress.setRange(0, 0)  # busy: this step's length is unknown
        else:
            self.progress.setRange(0, 1000)
            self.progress.setValue(int(round(1000 * fraction)))
        self.status.setText(text + "...")

    def update_progress(self, index: int, total: int, filename: str, status: str) -> None:
        self._batch_index, self._batch_total, self._batch_name = index, total, filename
        self.progress.setRange(0, 1000)
        finished = status != "running"
        self.progress.setValue(int(round(1000 * (index if finished else index - 1) / max(total, 1))))
        if self._clock is not None:
            (self._clock.image_finished if finished else self._clock.image_started)(index, total)
            self._show_time_left()
        if finished:
            if status == "Failure":
                self.failed_files.append(filename)
            self.message(f"Image {index} of {total} ({filename}): {JOB_WORDS.get(status, status)}")
        else:
            self.status.setText(f"Image {index} of {total} ({filename}): starting")

    def _panel_key(self, button: QPushButton) -> None:
        from qtpy.QtWidgets import QAbstractSpinBox, QApplication, QLineEdit, QTextEdit

        if isinstance(QApplication.focusWidget(), (QLineEdit, QAbstractSpinBox, QTextEdit)):
            return
        if button.isEnabled() and button.isVisibleTo(self.shell._dock):
            button.click()

    def _step(self, delta: int) -> None:
        if not self.shell._nav_ids:
            self.message("No included images (step 1).")
            return
        target = self.shell._nav_index + delta
        if not 0 <= target < len(self.shell._nav_ids):
            self.message("This is the last image." if delta > 0 else "This is the first image.")
            return
        self.shell._nav_index = target
        self.shell.show_current()

    def _pause(self) -> None:
        self._set_paused(True)

    def _resume(self) -> None:
        self._set_paused(False)

    def _set_paused(self, paused: bool) -> None:
        worker = self.shell._batch
        if worker is None or worker.paused == paused:
            return
        worker.paused = paused
        self.pause.setEnabled(not paused)
        self.resume.setEnabled(paused)
        if self._clock is not None:
            self._clock.pause() if paused else self._clock.resume()

    def _cancel(self) -> None:
        worker = self.shell._batch or self.shell._job
        if worker is None:
            return
        worker.cancelled = True
        if self.shell._batch is not None:
            self.shell._batch.paused = False
        self.pause.setEnabled(False)
        self.resume.setEnabled(False)
        self.cancel.setEnabled(False)
        self.status.setText("Stopping after the current step...")


class FloatingHeader(QWidget):
    """Header of a panel popped out of the napari window: Minimize, Maximize, Restore size, Reset (dock back), Close.

    Drag it to move the panel; drop it on the napari window, double-click it or click Reset to dock it back.
    """

    def __init__(self, dock):
        super().__init__(dock)
        self.dock = dock
        self._before_maximize = None
        self._height_before_minimize: int | None = None
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 2, 4, 2)
        row.setSpacing(2)
        self.title = QLabel(dock.windowTitle())
        self.title.setStyleSheet("font-weight: bold;")
        row.addWidget(self.title, 1)
        self.buttons: dict[str, QPushButton] = {}
        for key, text, tip, slot in (
            ("minimize", "–", "Minimize", self.minimize),
            ("maximize", "□", "Maximize", self.maximize),
            ("restore", "❐", "Restore size", self.restore),
            ("reset", "⟲", "Reset", self.reset),
            ("close", "✕", "Close", dock.close),
        ):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.setFixedSize(26, 22)
            button.setFlat(True)
            button.clicked.connect(slot)
            row.addWidget(button)
            self.buttons[key] = button
        self._update_buttons()
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Drag to move. Double-click or click Reset to put the panel back in the napari window.")

    @property
    def minimized(self) -> bool:
        return self._height_before_minimize is not None

    @property
    def maximized(self) -> bool:
        return self._before_maximize is not None

    def minimize(self) -> None:
        """Fold the panel up to this header."""

        if self.minimized:
            return
        self._height_before_minimize = self.dock.height()
        self.dock.widget().hide()
        self.dock.resize(self.dock.width(), self.sizeHint().height())
        self._update_buttons()

    def maximize(self) -> None:
        """Fill the screen with the panel."""

        if self.maximized:
            return
        screen = self.dock.screen() if hasattr(self.dock, "screen") else None
        if screen is None:
            return
        self._unfold()
        self._before_maximize = self.dock.geometry()
        self.dock.setGeometry(screen.availableGeometry())
        self._update_buttons()

    def restore(self) -> None:
        """Undo Minimize or Maximize: back to the size the panel had before."""

        if self.minimized:
            self._unfold()
        elif self.maximized:
            self.dock.setGeometry(self._before_maximize)
            self._before_maximize = None
        self._update_buttons()

    def _unfold(self) -> None:
        if self.minimized:
            self.dock.widget().show()
            self.dock.resize(self.dock.width(), self._height_before_minimize)
            self._height_before_minimize = None

    def _update_buttons(self) -> None:
        changed = self.minimized or self.maximized
        self.buttons["minimize"].setEnabled(not self.minimized)
        self.buttons["maximize"].setEnabled(not self.maximized)
        self.buttons["restore"].setEnabled(changed)

    def reset(self) -> None:
        """Put the panel back where it was docked in the napari window."""

        self.forget_size()
        self.dock.setFloating(False)

    def forget_size(self) -> None:
        self._unfold()
        self._before_maximize = None
        self._update_buttons()


def floating_header(dock) -> FloatingHeader:
    """When a napari panel is popped out, give it a FloatingHeader in place of the plain system title bar."""

    header = FloatingHeader(dock)
    header.hide()

    def apply() -> None:
        try:
            if not dock.isFloating() or dock.titleBarWidget() is header:
                return
        except RuntimeError:
            return  # the panel was closed meanwhile
        from qtpy.QtWidgets import QApplication

        if QApplication.mouseButtons() != Qt.MouseButton.NoButton:
            QTimer.singleShot(200, apply)  # still being dragged; changing the header now would drop it
            return
        # napari swaps its own header in when the panel is shown; keep it from undoing this one.
        dock.blockSignals(True)
        try:
            dock.setTitleBarWidget(header)
            header.show()
            dock.show()
        finally:
            dock.blockSignals(False)

    def docked() -> None:
        header.forget_size()
        if dock.titleBarWidget() is header:
            dock.setTitleBarWidget(None)
            header.hide()
            # napari puts its own docked header back when the panel is shown.
            restore = getattr(dock, "_on_visibility_changed", None)
            if restore is not None:
                restore(True)

    dock.topLevelChanged.connect(lambda floating: QTimer.singleShot(0, apply) if floating else docked())
    dock.visibilityChanged.connect(lambda visible: QTimer.singleShot(0, apply) if visible else None)
    return header


class CloseWatcher(QObject):
    """Call a function when the window it watches is about to close, while its widgets still exist."""

    def __init__(self, on_close, parent=None):
        super().__init__(parent)
        self._on_close = on_close

    def eventFilter(self, watched, event) -> bool:
        from qtpy.QtCore import QEvent

        if event.type() == QEvent.Close:
            self._on_close()
        return False


class WheelGuard(QObject):
    """Stop the mouse wheel from changing a dropdown or number box the user has not clicked.

    The wheel then scrolls the page under the pointer instead, so scrolling past a setting never changes it.
    """

    def eventFilter(self, watched, event) -> bool:
        from qtpy.QtCore import QEvent
        from qtpy.QtWidgets import QAbstractSpinBox

        if event.type() == QEvent.Wheel and isinstance(watched, (QComboBox, QAbstractSpinBox)) and not watched.hasFocus():
            event.ignore()  # passed on to the scroll area around it
            return True
        return False


class CallWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)
    # (step text, fraction 0-1 of this step, or -1 when its length is unknown)
    step = Signal(str, float)

    def __init__(self, fn, report: bool = False):
        super().__init__()
        self._fn = fn
        self._report = report
        self.cancelled = False

    def run(self) -> None:
        from cellquant import progress

        try:
            if self._report:
                with progress.reporting(
                    lambda text, fraction: self.step.emit(text, -1.0 if fraction is None else float(fraction)),
                    lambda: self.cancelled,
                ):
                    value = self._fn()
            else:
                value = self._fn()
            self.succeeded.emit(value)
        except Exception as exc:  # noqa: BLE001 - shown to the user, with the traceback in the developer log
            self.failed.emit(_error_text(exc))


def _error_text(exc: BaseException) -> str:
    """A message for the red problem box. Unexpected errors name their type; the traceback goes to the developer log."""

    _log_developer_error(exc)
    # These carry sentences written for the user; others (KeyError 'x', TypeError, ...) would read as noise.
    if isinstance(exc, (CellQuantError, ValueError, OSError, RuntimeError)) and not isinstance(exc, (KeyError, IndexError)):
        return str(exc.args[0] if exc.args else exc) or "The analysis failed."
    detail = str(exc) or "no details"
    return f"Unexpected error ({type(exc).__name__}: {detail}). Details are in cellquant_developer.log (see Start here: When something looks wrong)."


def _log_developer_error(exc: BaseException) -> None:
    """Append the traceback to the developer log beside the per-computer settings."""

    import traceback

    import os

    try:
        # Beside the per-computer install record on Windows (%LOCALAPPDATA%\\CellQuant); ~/.cellquant elsewhere.
        local = os.environ.get("LOCALAPPDATA")
        folder = Path(local) / "CellQuant" if local else Path.home() / ".cellquant"
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / "cellquant_developer.log").open("a", encoding="utf-8") as handle:
            handle.write("".join(traceback.format_exception(exc)) + "\n")
    except Exception:  # noqa: BLE001 - logging must never hide the original problem
        pass


class BatchWorker(QThread):
    progress = Signal(int, int, str, str)
    step = Signal(str, float)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, controller: AnalysisController, image_ids: list[str] | None, analyses: list[str] | None = None):
        super().__init__()
        self.controller = controller
        self.image_ids = image_ids
        self.analyses = analyses
        self.paused = False
        self.cancelled = False

    def run(self) -> None:
        def on_progress(index, total, filename, status):
            self.progress.emit(index, total, filename, status)

        def should_continue() -> bool:
            while self.paused and not self.cancelled:
                self.msleep(100)
            return not self.cancelled

        from cellquant import progress

        def stop_requested() -> bool:
            # Pausing waits here, between slices or steps, as well as between images.
            while self.paused and not self.cancelled:
                self.msleep(100)
            return self.cancelled

        try:
            with progress.reporting(
                lambda text, fraction: self.step.emit(text, -1.0 if fraction is None else float(fraction)),
                stop_requested,
            ):
                if self.analyses is not None:
                    report = self.controller.run_analyses(
                        self.analyses,
                        self.image_ids,
                        on_progress=on_progress,
                        should_continue=should_continue,
                    )
                else:
                    report = self.controller.run_images(
                        self.image_ids,
                        on_progress=on_progress,
                        should_continue=should_continue,
                    )
        except Exception as exc:  # noqa: BLE001 - the window must leave the running state whatever happened
            self.failed.emit(_error_text(exc))
            return
        self.finished_ok.emit(report)


def _is_positive_flag(value) -> bool:
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "1", "positive"}
    if value is None:
        return False
    try:
        if isinstance(value, float) and np.isnan(value):
            return False
        return bool(value)
    except TypeError:
        return False


def _visible_crop(viewer) -> tuple[int, int, int, int] | None:
    if not viewer.layers:
        return None
    layer = viewer.layers[0]
    try:
        corners = np.asarray(layer.corner_pixels)
        y0 = int(np.floor(corners[0, -2]))
        x0 = int(np.floor(corners[0, -1]))
        y1 = int(np.ceil(corners[1, -2]))
        x1 = int(np.ceil(corners[1, -1]))
        return y0, y1, x0, x1
    except Exception:
        return None
