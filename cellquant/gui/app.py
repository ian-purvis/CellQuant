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
import traceback
from pathlib import Path

import numpy as np
from qtpy.QtCore import QEvent, QObject, Qt, QSize, QThread, QTimer, Signal
from qtpy.QtGui import QColor, QPalette
from qtpy.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
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
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QToolButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cellquant import keep_awake
from cellquant.controller import AnalysisController, display_z
from cellquant.count_area import outside
from cellquant.errors import CellQuantError, RecipeValidationError
from cellquant.gui import guide
from cellquant.segmentation import FULL_3D_FLOW_SMOOTH, FULL_3D_NITER
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

    from cellquant.gui import graphics

    from qtpy.QtWidgets import QApplication

    try:
        viewer = napari.Viewer(title="CellQuant")
        drawing_works = graphics.drawing_works(viewer)
    except Exception as exc:
        if not graphics.is_graphics_error(exc):
            raise
        traceback.print_exc()
        drawing_works = False
    # No usable OpenGL: the launcher reopens CellQuant with software graphics.
    if not drawing_works and graphics.remember_software_graphics():
        print("CellQuant: this computer's graphics cannot draw the viewer. Reopening with software graphics.", flush=True)
        raise SystemExit(graphics.GRAPHICS_EXIT_CODE)
    graphics.watch_viewer_errors(QApplication.instance())
    CellQuantWindow(viewer, experiment_dir)
    napari.run()
    if graphics.restart_requested:
        raise SystemExit(graphics.GRAPHICS_EXIT_CODE)


_OBJECT_LAYERS = ("Object fills", "Objects", "Classification", "Object IDs")
# Polygons drawn in step 5; only objects whose center is inside them are counted.
COUNT_AREA_LAYER = "Count area"
# Polygons drawn in step 3; objects entirely inside them are deleted.
DELETE_AREA_LAYER = "Delete area"

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


def channel_label(index: int, name) -> str:
    """'Channel 2 = AF647' for the channel at index 1."""

    return format_channel_labels([None] * index + [name], index + 1)[-1]


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


# 64 common colors (CSS names) the object masks can be limited to; none picked = every object its own color.
MASK_PALETTE = (
    "#ff0000", "#ff4500", "#ff8c00", "#ffa500", "#ffd700", "#ffff00", "#adff2f", "#7fff00",
    "#00ff00", "#32cd32", "#008000", "#006400", "#00fa9a", "#20b2aa", "#00ffff", "#40e0d0",
    "#008b8b", "#00bfff", "#1e90ff", "#0000ff", "#0000cd", "#000080", "#4169e1", "#6a5acd",
    "#8a2be2", "#800080", "#9400d3", "#ba55d3", "#ff00ff", "#ff69b4", "#ff1493", "#c71585",
    "#dc143c", "#b22222", "#8b0000", "#a52a2a", "#800000", "#cd5c5c", "#f08080", "#fa8072",
    "#ffa07a", "#e9967a", "#d2691e", "#8b4513", "#a0522d", "#cd853f", "#deb887", "#f4a460",
    "#bdb76b", "#808000", "#6b8e23", "#556b2f", "#8fbc8f", "#2e8b57", "#5f9ea0", "#4682b4",
    "#87ceeb", "#b0c4de", "#dda0dd", "#ffc0cb", "#ffffff", "#c0c0c0", "#808080", "#000000",
)


def mask_colors() -> list[str]:
    """Colors the object masks cycle through as #rrggbb; empty = every object its own color."""

    try:
        saved = _settings().value("view/mask_colors", "")
    except Exception:  # noqa: BLE001 - settings unreadable: use the default
        return []
    # Some QSettings backends hand a comma-containing string back as a list; saved with ";" to avoid that.
    items = [str(item) for item in saved] if isinstance(saved, (list, tuple)) else str(saved).replace(",", ";").split(";")
    return [value for value in items if value in MASK_PALETTE]


def mask_colormap(colors: list[str]):
    """napari's usual random-looking object colors for none, else the chosen colors in turn."""

    from napari.utils.colormaps import CyclicLabelColormap, label_colormap

    if not colors:
        return label_colormap()
    rows = [(0.0, 0.0, 0.0, 0.0)] + [(c.redF(), c.greenF(), c.blueF(), 1.0) for c in map(QColor, colors)]
    return CyclicLabelColormap(colors=np.array(rows), background_value=0)


def classification_outline() -> bool:
    """Whether the positive/negative overlay shows object outlines only (default) or filled objects."""

    try:
        return str(_settings().value("review/classification_style", "outline")) != "fill"
    except Exception:  # noqa: BLE001 - settings unreadable: use the default
        return True


def save_classification_style(outline: bool) -> None:
    _settings().setValue("review/classification_style", "outline" if outline else "fill")


def keep_awake_preferred() -> bool:
    try:
        return str(_settings().value("run/keep_awake", "true")).lower() == "true"
    except Exception:  # noqa: BLE001 - settings unreadable: use the default
        return True


def show_all_channels_preferred() -> bool:
    """Whether a segmented image opens with every channel visible (default: only the segmented one)."""

    try:
        return str(_settings().value("view/show_all_channels", "false")).lower() == "true"
    except Exception:  # noqa: BLE001 - settings unreadable: use the default
        return False


def save_show_all_channels(on: bool) -> None:
    _settings().setValue("view/show_all_channels", "true" if on else "false")


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
        self._pixel_scale: tuple[float, float, float] | None = None  # (z, y, x) in µm for the shown image (z None: no Z step, stays 1 px); None: pixels
        self._shown_z: tuple[str | None, int | None] = (None, None)
        self._labels_image_id: str | None = None  # image the Objects layer holds
        self._wanted_image_id: str | None = None
        self._loader: CallWorker | None = None
        self._preloader: CallWorker | None = None
        self._building = False
        # True while work runs that switches between analyses (Run all analyses, Export all analyses):
        # the settings pages and image navigation are locked, since they would show another analysis.
        self._exclusive = False
        self._show_note: str | None = None  # said before "Showing <image>" once the next image is shown, e.g. "Approved."
        self._nav_filter: str | None = None  # e.g. "failed images" while Previous / Next go through only those
        self._tabs = QTabWidget()
        self._experiment_panel = ExperimentPanel(self)
        self._objects_panel = ObjectsPanel(self)
        self._edit_panel = EditPanel(self)
        self._measurements_panel = MeasurementsPanel(self)
        self._review_panel = ReviewPanel(self)
        self._results_panel = ResultsPanel(self)
        self._exported_to: Path | None = None
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
        self._tabs.currentChanged.connect(lambda _index: self._markers_page_shown())
        main_dock = self.viewer.window.add_dock_widget(self._dock, name="CellQuant", area="right")
        from cellquant.gui.plan_dock import PlanDock

        self._plan_dock = PlanDock(self)
        self._plan_dock_widget = self.viewer.window.add_dock_widget(self._plan_dock, name="Plan", area="left")
        self._plan_dock_widget.hide()
        from cellquant.gui.measure_dock import MeasureDock

        self._measure_dock = MeasureDock(self)
        self._measure_dock_widget = self.viewer.window.add_dock_widget(self._measure_dock, name="Measure", area="right")
        self._measure_dock_widget.hide()
        run_dock = self.viewer.window.add_dock_widget(FooterScroll(self._footer), name="Run", area="bottom")
        # Under napari's layer controls: a view control that stays put whichever layer is selected.
        self._view_box = ViewBox(self)
        view_dock = self.viewer.window.add_dock_widget(self._view_box, name="View", area="left")
        self._floating_headers = [floating_header(dock) for dock in (main_dock, self._plan_dock_widget, self._measure_dock_widget, run_dock, view_dock)]
        # napari's own layer controls and layer list: popped out they get the same header, minus Close, and they
        # cannot be hidden by accident. Any panel can be docked side by side with another, not just tabbed.
        qt_viewer = self.viewer.window._qt_viewer
        for dock in (qt_viewer.dockLayerControls, qt_viewer.dockLayerList):
            self._floating_headers.append(floating_header(dock, closable=False))
            keep_open(dock)
        from qtpy.QtWidgets import QMainWindow

        main_window = self.viewer.window._qt_window
        main_window.setDockOptions(main_window.dockOptions() | QMainWindow.DockOption.AllowNestedDocks)
        guide.apply_help(self)
        self._guide_timer = guide.start_refresh_timer(self)
        self._start_gpu_check()
        # Menus and number boxes change with the mouse wheel only after being clicked.
        self._wheel_guard = WheelGuard(self._tabs)
        from qtpy.QtWidgets import QApplication

        QApplication.instance().installEventFilter(self._wheel_guard)
        self.viewer.layers.events.inserted.connect(lambda _event: self._release_window_later())
        self.viewer.layers.events.removed.connect(lambda _event: self._release_window_later())
        self.viewer.layers.events.inserted.connect(lambda _event: self._view_box.refresh())
        self.viewer.layers.events.inserted.connect(lambda event: self._scale_layer(event.value))
        self.viewer.layers.events.removed.connect(lambda _event: self._view_box.refresh())
        self._release_window_later()
        self._watch_settings()
        if experiment_dir:
            self.open_experiment(experiment_dir)
        self.refresh_guidance()

    # -- guidance ------------------------------------------------------------

    def _markers_page_shown(self) -> None:
        """Step 4 lists every channel but the one objects are found in, so read that from step 2 first."""

        if self._tabs.currentWidget() is not self._step_pages[3] or self.controller is None or self.is_busy():
            return
        self._panels_to_recipe()
        self._marker_setup.refresh()

    def go_to_step(self, index: int) -> None:
        if index < 0:
            self.go_to_start()
            return
        index = min(index, len(self._step_pages) - 1)
        self.refresh_guidance()  # unlocks the tab first
        self._tabs.setCurrentWidget(self._step_pages[index])

    def go_to_start(self) -> None:
        self._tabs.setCurrentWidget(self._start_page)
        self.refresh_guidance()

    def refresh_guidance(self) -> None:
        if self._run_lock_note.isVisibleTo(self._dock):
            # Settings rebuilt during a run (for example a refreshed list) start enabled; lock them too.
            for widget in self._settings_inputs():
                if not widget.testAttribute(Qt.WA_ForceDisabled):
                    self._settings_restore.setdefault(widget, True)
                    widget.setEnabled(False)
        states = guide.step_states(self)
        self._start_page.update_state(states)
        # A step's tab opens once every earlier step's Next is allowed; going back is always allowed.
        locked_by = None
        next_index = next((i for i, state in enumerate(states) if not state.done), None)
        for i, (page, state, step) in enumerate(zip(self._step_pages, states, guide.STEPS)):
            page.update_state(state)
            index = self._tabs.indexOf(page)
            self._tabs.setTabEnabled(index, locked_by is None)
            if locked_by is not None:
                blocker_step, blocker = locked_by
                self._tabs.setTabText(index, str(step.number))
                self._tabs.setTabToolTip(index, f"{step.name}: locked. Step {blocker_step.number} first: {blocker}")
            else:
                mark = " ✓" if state.done else (" →" if i == next_index else "")
                self._tabs.setTabText(index, f"{step.number}{mark}")
                self._tabs.setTabToolTip(index, step.name)
            if locked_by is None and state.blocker:
                locked_by = (step, state.blocker)
        if not self._tabs.isTabEnabled(self._tabs.currentIndex()):
            open_pages = [page for page in self._step_pages if self._tabs.isTabEnabled(self._tabs.indexOf(page))]
            self._tabs.setCurrentWidget(open_pages[-1] if open_pages else self._start_page)

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
            self._measure_dock.forget()
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
        self._measure_dock.forget()
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
        self._measure_dock.forget()
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
        result = controller.recall(image_id, measure=False) if controller else None
        measuring = result is None and controller.is_pending(image_id) and not self.is_busy()
        self._footer.set_position(self._nav_index, len(self._nav_ids), record.relative_path or record.filename, self._nav_filter)
        self._review_panel.update_status_line()
        self._experiment_panel.set_pixel_size(record.pixel_size_x, record.pixel_size_y, record.pixel_size_z)
        self._experiment_panel.show_channel_order(record)
        # Reading the file (an ND2 stack can take a second) happens off the interface thread,
        # so the window stays responsive. If the user keeps clicking Next, only the last
        # image asked for is shown.
        self._wanted_image_id = image_id
        if measuring:
            # Loaded objects: measure them first, then show the image once, as it was segmented.
            self._measure_loaded(image_id)
            return
        if self._loader is not None:
            return
        self._footer.message(f"Loading {record.relative_path or record.filename}...")
        self._load_in_background(record, result)

    def _load_in_background(self, record, result) -> None:
        controller = self.controller
        worker = CallWorker(lambda: controller._load_display(record, result=result))
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
            else:
                self._review_panel.show_no_result()
            unit = (
                f"µm ({record.pixel_size_x:.3f} µm/pixel)"
                if record.pixel_size_x and record.pixel_size_y
                else "pixels — no pixel size in this image"
            )
            self._footer.set_units(f"{unit}   ·   Analyzed: {loaded.z_description}")
            note, self._show_note = self._show_note, None
            self._footer.message(f"{note} Showing {record.relative_path or record.filename}" if note else f"Showing {record.relative_path or record.filename}")
            self._preload_next()

        def failed(message: str) -> None:
            self._loader = None
            self._show_note = None
            if self.controller is not controller:
                return
            self.show_error(f"{record.relative_path or record.filename} could not be shown. {message}")
            self._clear_managed()
            if self._wanted_image_id != record.image_id:
                self.show_current()

        worker.succeeded.connect(shown)
        worker.failed.connect(failed)
        worker.start()

    def _preload_next(self) -> None:
        """Read the next image in the background so pressing Next shows it at once."""

        controller = self.controller
        if controller is None or self._preloader is not None or self._nav_index + 1 >= len(self._nav_ids):
            return
        try:
            record = controller.experiment.image(self._nav_ids[self._nav_index + 1])
            result = controller.recall(record.image_id, measure=False)
        except Exception:  # noqa: BLE001 - only a head start
            return
        worker = CallWorker(lambda: controller.preload_display(record, result))
        self._preloader = worker

        def done(_value=None) -> None:
            self._preloader = None

        worker.succeeded.connect(done)
        worker.failed.connect(done)
        worker.start()

    def image_loading(self) -> bool:
        return self._loader is not None

    def show_result(self, result) -> None:
        if result is None or self.controller is None:
            return
        image_id = result.provenance.get("image_id")
        if image_id == self._shown_image_id and self._has_image_layers() and self._shown_z == display_z(result):
            # Same image: keep the channel layers, and update the object layers in place.
            first_result = "Objects" not in self.viewer.layers
            self._set_labels(result.labels, result)
            if first_result:
                self._show_segmented_channel_only(
                    image_id, [layer for layer in self.viewer.layers if layer.name in self._managed and layer.name not in _OBJECT_LAYERS]
                )
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

    def load_results_dialog(self) -> None:
        if self.is_busy():
            return
        controller = self.controller
        start = (controller.experiment.input_directory or str(controller.directory)) if controller else str(Path.home())
        folder = QFileDialog.getExistingDirectory(self._tabs, "Choose a folder of results (masks)", start)
        if not folder or self.is_busy():
            return
        if controller is None:
            controller = self._experiment_for_results(Path(folder))
            if controller is None:
                return
            start = folder
        self._panels_to_recipe()

        def found(originals) -> None:
            if not originals.needs_folder:
                self._after_job(lambda: self._load_results(folder, originals.found))
                return
            listed = f" ({len(originals.missing)} listed images are not where they were)" if originals.missing else ""
            where = choose_existing_directory(
                self._tabs, f"Where are the original images?{listed} Subfolders are included.", start
            )
            if not where:
                self._after_job(lambda: self._load_results(folder, originals.found))
                return
            self._after_job(lambda: self._start_job(
                lambda: controller.find_originals(folder, where),
                lambda again: self._after_job(lambda: self._load_results(folder, again.found)),
            ))

        self._footer.message("Looking for the original images...")
        self._start_job(lambda: controller.find_originals(folder), found)

    def _experiment_for_results(self, folder: Path) -> AnalysisController | None:
        """No experiment is open: make one for these results, in a folder the user picks."""

        package = folder.parent if folder.name.lower() == "results" else folder
        output = QFileDialog.getExistingDirectory(
            self._tabs, f"No experiment is open. Choose an empty folder to save a new one for {package.name}", str(package.parent)
        )
        if not output:
            return None
        output = Path(output)
        if (output / "experiment.json").is_file():
            self.message("That folder already has an experiment. Open it with Open experiment…, or choose another folder.")
            return None
        try:
            controller = AnalysisController.create(output, package.name)
        except Exception as exc:  # noqa: BLE001 - e.g. a folder that cannot be written
            self.show_error(f"The experiment could not be created in {output}. {_error_text(exc)}")
            return None
        self.controller = controller
        self._exported_to = None
        self._measure_dock.forget()
        self._refresh_all()
        self.go_to_step(0)
        return controller

    def _after_job(self, fn) -> None:
        """Run ``fn`` once the current job has finished (a job's result handler runs before it ends)."""

        from qtpy.QtCore import QTimer

        if self._job is not None or self._batch is not None:
            QTimer.singleShot(20, lambda: self._after_job(fn))
            return
        fn()

    def _load_results(self, folder: str, images: list[str]) -> None:
        controller = self.controller
        if controller is None:
            return
        self._start_job(lambda: controller.load_results(folder, images=images), self._results_loaded)

    def _measure_loaded(self, image_id: str) -> None:
        """Measure the markers of loaded objects off the interface thread, then show them."""

        controller = self.controller

        def measured(result) -> None:
            self._experiment_panel.refresh_table()
            self.refresh_guidance()
            if self._nav_ids and self._nav_ids[self._nav_index] == image_id:
                self.show_result(result)
            else:
                self._after_job(self.show_current)  # the user moved on meanwhile

        def failed(message: str) -> None:
            self.show_error(message)
            self._experiment_panel.refresh_table()
            self._after_job(self.show_current)

        if self._batch is not None or self._job is not None:
            return
        worker = CallWorker(lambda: controller.measure_loaded(image_id), report=True)
        self._job = worker
        worker.succeeded.connect(measured)
        worker.failed.connect(failed)
        worker.step.connect(self._footer.show_step)
        worker.finished.connect(self._clear_job)
        self.set_busy(True)
        self._footer.message("Measuring markers in the loaded objects...")
        worker.start()

    def _results_loaded(self, report) -> None:
        controller = self.controller
        if controller is None:
            return
        self._refresh_all(keep_image=True)
        if report.notices:
            self._experiment_panel.set_notices(report.notices)
        lines = [f"Loaded objects for {len(report.loaded)} of {len(controller.experiment.images)} images."]
        if report.settings:
            lines.append("Step 2 now uses the masks' " + " and ".join(report.settings) + ".")
        if report.problems:
            lines.append(f"{len(report.problems)} masks did not fit: " + " | ".join(report.problems[:5]))
            if len(report.problems) > 5:
                lines.append(f"… and {len(report.problems) - 5} more (see the Log).")
        if report.missing and report.loaded:
            lines.append(f"{len(report.missing)} images had no mask in the folder.")
        if report.unmatched:
            lines.append(f"{report.unmatched} files in the folder matched no image by name.")
        if report.duplicates:
            lines.append(f"{report.duplicates} images had more than one mask; the newest was used.")
        if not report.loaded and not report.problems:
            lines.append("No mask in the folder is named after an image of this experiment (for example image001_masks.tif).")
        if report.problems or not report.loaded:
            self.show_error(" ".join(lines))
        else:
            self._footer.message(" ".join(lines))
        shown = self._nav_ids[self._nav_index] if self._nav_ids else None
        if shown in report.loaded:
            self._after_job(lambda: self._measure_loaded(shown))

    def _run_finished(self, result) -> None:
        self.show_result(result)
        self._refresh_plan()
        self._experiment_panel.refresh_table()
        took = self._footer.run_time_text()
        if result.qc.warnings:
            if took:
                self._footer.log.append(f"Found {result.qc.n_objects} objects{took}.")
            self._footer.message(result.qc.warnings[0])
        else:
            self._footer.message(f"Found {result.qc.n_objects} objects{took}. Check that the outlines match, then continue.")
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
            "Preview", "Run this image", "Load results…", "Run selected images", "Run all images", "Set up markers",
            "Export results…", "Add images", "Add folder", "New experiment", "New experiment…", "Open",
            "Open experiment…", "Try practice images", "Include shown", "Leave out shown",
            "Include only selected", "Delete object", "Restore object", "Undo", "Record drawn edits", "Approve", "Use recommended",
            "Run all analyses", "Export all analyses…", "New analysis…", "One per channel…", "Rename…", "Remove",
            # Buttons that change the settings: never while images are being analyzed with them.
            "Load settings…", "Add measurement", "Remove measurement",
            "Add marker", "Remove marker", "Add result row", "Remove result row", "Apply pixel level",
            "Run all images (this analysis)", "Set sizes (µm)", "Reset cutoff",
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
            self._busy_restore = {button: not button.testAttribute(Qt.WA_ForceDisabled) for button in buttons}
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
        inputs: list[QWidget] = [self._review_panel.threshold_slider, self._review_panel.histogram]
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
            # The widget's own setting: a locked step tab greys its page without changing it.
            self._settings_restore = {widget: not widget.testAttribute(Qt.WA_ForceDisabled) for widget in widgets}
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

    def show_measure(self) -> None:
        """Open the Measure window, popped out, and get the image ready to draw on."""

        if self.require_controller() is None:
            return
        first = not self._measure_dock_widget.isVisible()
        self._measure_dock_widget.show()
        if first:
            self._measure_dock_widget.setFloating(True)
        self._measure_dock_widget.raise_()
        if self._shown_image_id is not None:
            self._measure_dock.ensure_layer()
        self._measure_dock.refresh()

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
            self._objects_panel.refresh_image_channels()
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

    def set_pixel_scale(self, record) -> None:
        """Give every layer the shown image's pixel size, so napari's scale bar reads in µm (px when unknown)."""

        x, y = record.pixel_size_x, record.pixel_size_y
        if x and y:
            self._pixel_scale = (float(record.pixel_size_z) if record.pixel_size_z else None, float(y), float(x))
        else:
            self._pixel_scale = None
        for layer in list(self.viewer.layers):
            self._scale_layer(layer)
        try:
            self.viewer.scale_bar.unit = "µm" if self._pixel_scale else "px"
        except Exception:  # noqa: BLE001 - older napari: the bar keeps its own unit
            pass

    def _scale_layer(self, layer) -> None:
        """Layers stay in pixel coordinates (shapes, points, painting); only their scale and unit change."""

        ndim = layer.ndim
        if ndim < 2:
            return
        if self._pixel_scale:
            z, y, x = self._pixel_scale
            axes = [(z or 1.0, "micrometer" if z else "pixel"), (y, "micrometer"), (x, "micrometer")]
        else:
            axes = [(1.0, "pixel")] * 3
        axes = ([(1.0, "pixel")] * max(ndim - 3, 0) + axes)[-ndim:]
        try:
            want = np.array([scale for scale, _unit in axes], dtype=float)
            if not np.allclose(layer.scale, want):
                layer.scale = want
            layer.units = tuple(unit for _scale, unit in axes)
        except Exception:  # noqa: BLE001 - older napari has no layer units
            pass

    def _has_image_layers(self) -> bool:
        return any(name in self.viewer.layers for name in self._managed if name not in _OBJECT_LAYERS)

    def _show_loaded(self, loaded, record, result) -> None:
        self._edit_panel.keep_drawing()
        self._labels_image_id = None
        self._clear_managed()
        self._shown_image_id = record.image_id
        self._shown_z = display_z(result)
        self._edit_panel.clear_selection()
        file_names = list(getattr(loaded, "channel_names", None) or record.channel_names or ())
        names = format_channel_labels(file_names, loaded.n_channels)
        # Contrast is remembered per experiment channel, matched by the names stored in the file.
        by_index = {channel.channel_index: channel for channel in self.controller.experiment.channels}
        owners = self.controller.display_channels(record, loaded.n_channels)
        colormaps = channel_colormaps(loaded)
        self.set_pixel_scale(record)
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
                lambda event, index=channel.channel_index, layer=layer: self._store_contrast(index, layer)
            )
        self._view_box.reset()
        if result is not None:
            self._show_segmented_channel_only(record.image_id, layers)
        if loaded.data.ndim == 4:
            # Every slice is loaded, so the slider under the image scrolls the stack. Start on the
            # slice a one-slice analysis used, else the middle one.
            slice_index = loaded.z_index if loaded.z_mode == "single_plane" else loaded.data.shape[1] // 2
            self.viewer.dims.set_current_step(0, int(slice_index))
        labels = result.labels if result is not None else None
        self._set_labels(labels, result)
        self.show_count_area(record)
        self._measure_dock.bring_back(record.image_id)
        self._release_window_later()
        self._experiment_panel.show_channel_order(record)

    def _show_segmented_channel_only(self, image_id: str, layers) -> None:
        """Review starts on the channel that was segmented; the others stay in the layer list, hidden."""

        if len(layers) < 2 or show_all_channels_preferred():
            return
        segmented = self.controller.segmentation_channel_for(image_id)[0]
        if 0 <= segmented < len(layers):
            for position, layer in enumerate(layers):
                layer.visible = position == segmented

    def _store_contrast(self, index: int, layer) -> None:
        if self.controller is None:
            return
        for channel in self.controller.experiment.channels:
            if channel.channel_index == index:
                low, high = layer.contrast_limits
                channel.display_settings["contrast_limits"] = [float(low), float(high)]

    def _set_labels(self, labels, result) -> None:
        # Adding or removing a napari layer takes on the order of a second, so
        # layers that already exist with the right shape get new data instead.
        self._edit_panel.keep_drawing()
        self._labels_image_id = None
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
        self.apply_mask_colors()
        if result is None:
            self._drop("Classification")
            self._drop("Object IDs")
            self._drop("Crop regions")
        else:
            self._set_classification_overlay(result)
            self._set_ids(result)
            self._set_crop_regions(result, labels.shape)
            self._labels_image_id = result.provenance.get("image_id")
            self._edit_panel.bring_back_drawing(self._labels_image_id, boundaries)

    def apply_mask_colors(self) -> None:
        """Give the object layers the colors chosen in step 3."""

        colors = mask_colors()
        for name in ("Object fills", "Objects"):
            if name in self.viewer.layers:
                self.viewer.layers[name].colormap = mask_colormap(colors)

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

    def delete_area_layer(self):
        """The Delete area shapes layer, added when missing."""

        if DELETE_AREA_LAYER in self.viewer.layers:
            return self.viewer.layers[DELETE_AREA_LAYER]
        layer = self.viewer.add_shapes(
            None, ndim=2, shape_type="polygon", name=DELETE_AREA_LAYER, edge_color="red", face_color="transparent", edge_width=3
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
        layer = self.viewer.layers["Classification"]
        layer.colormap = DirectLabelColormap(
            color_dict={
                None: np.array([0, 0, 0, 0], dtype=np.float32),
                1: _rgba(negative),
                2: _rgba(positive),
            }
        )
        layer.contour = 2 if classification_outline() else 0

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
        self._measure_dock.keep()  # what is drawn to measure stays with its image
        for name in list(self._managed):
            self._drop(name)


class ViewBox(QWidget):
    """View-only controls under napari's layer controls. Nothing here changes what is segmented or measured."""

    def __init__(self, shell: CellQuantWindow):
        super().__init__()
        self.shell = shell
        self._stacks: dict[str, tuple] = {}  # layer name -> (full stack, contrast limits)
        self._step = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        self.max_projection = QCheckBox("Max projection ⓘ")
        self.max_projection.setToolTip(
            "Show the brightest value of every Z slice as one flat image. This changes only what you see: "
            "what is segmented and measured is set by Z handling in step 2. Switch off to scroll the stack again."
        )
        self.max_projection.toggled.connect(self._toggled)
        layout.addWidget(self.max_projection)
        self.measure = QPushButton("Measure…")
        self.measure.setToolTip("Open the Measure window: draw lines or shapes on the image to get length, area and volume, and export them to a CSV.")
        self.measure.clicked.connect(lambda: shell.show_measure())
        layout.addWidget(self.measure)
        self.refresh()

    def _image_layers(self):
        names = self.shell._managed
        return [
            layer for layer in self.shell.viewer.layers
            if layer.name in names and layer.name not in _OBJECT_LAYERS and hasattr(layer, "contrast_limits")
        ]

    def refresh(self) -> None:
        try:
            layers = self._image_layers()
            self.max_projection.setEnabled(any(layer.name in self._stacks or layer.data.ndim == 3 for layer in layers))
        except RuntimeError:
            pass  # the window was closed meanwhile

    def reset(self) -> None:
        """A new image was shown: it starts as the normal view."""

        self._stacks.clear()
        self.max_projection.blockSignals(True)
        self.max_projection.setChecked(False)
        self.max_projection.blockSignals(False)
        self.refresh()

    def _toggled(self, on: bool) -> None:
        viewer = self.shell.viewer
        if on and viewer.dims.ndim > 2:
            self._step = viewer.dims.current_step[0]
        for layer in self._image_layers():
            limits = tuple(layer.contrast_limits)
            if on and layer.data.ndim == 3 and layer.name not in self._stacks:
                self._stacks[layer.name] = (layer.data, limits)
                layer.data = np.max(layer.data, axis=0)
                layer.contrast_limits = limits
            elif not on and layer.name in self._stacks:
                layer.data = self._stacks.pop(layer.name)[0]
                layer.contrast_limits = limits
        for layer in self._image_layers():
            self.shell._scale_layer(layer)  # swapping 3D and 2D data pads the scale; put the pixel size back
        if not on and self._step is not None and viewer.dims.ndim > 2:
            viewer.dims.set_current_step(0, self._step)
        self.refresh()


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
        load = QPushButton("Load results…")
        load.clicked.connect(shell.load_results_dialog)
        load.setToolTip(
            "Open objects found elsewhere (a cluster run, Cellpose, another CellQuant experiment) "
            "over their original images.\n"
            "Pick the results folder: its original images are added (you are asked where they are "
            "if they moved), and each mask, a TIFF of object numbers named after its image, "
            "becomes that image's objects. Nothing is segmented.\n"
            "With no experiment open, you are asked where to save a new one.\n"
            "Markers are measured when an image is opened."
        )
        loading = QHBoxLayout()
        loading.addWidget(load)
        loading.addStretch(1)
        layout.addLayout(loading)
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
        self.pixel_summary = QLabel("")
        self.pixel_summary.setToolTip("Pixel size of the included images. Nothing to set.")
        self.pixel_summary.setWordWrap(True)
        self.pixel_summary.setVisible(False)
        self.change_sizes_button = QPushButton("Change…")
        self.change_sizes_button.setToolTip("Show the pixel size boxes, for example to correct a size you typed.")
        self.change_sizes_button.clicked.connect(self._show_size_controls)
        self.summary_row = QWidget()
        summary_layout = QHBoxLayout(self.summary_row)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        summary_layout.addWidget(self.pixel_summary, 1)
        summary_layout.addWidget(self.change_sizes_button)
        self.summary_row.setVisible(False)
        self._changing_sizes = False
        self._size_controller = None
        layout.addWidget(self.summary_row)
        self.calibration_widget = QWidget()
        calibration = QHBoxLayout(self.calibration_widget)
        calibration.setContentsMargins(0, 0, 0, 0)
        self.pixel_x = QDoubleSpinBox()
        self.pixel_y = QDoubleSpinBox()
        self.pixel_z = QDoubleSpinBox()
        for box in (self.pixel_x, self.pixel_y, self.pixel_z):
            box.setDecimals(4)
            box.setMaximum(100000)
            box.setSpecialValueText("unset")
        self.pixel_z.setToolTip("Distance between slices of a Z-stack, in µm. Needed for 3D volumes and shapes.")
        apply_cal = self.apply_sizes_button = QPushButton("Set sizes (µm)")
        apply_cal.setToolTip("Save these sizes for the images chosen under Apply to.")
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
        layout.addWidget(self.calibration_widget)
        self.scope_widget = QWidget()
        scope_row = QHBoxLayout(self.scope_widget)
        scope_row.setContentsMargins(0, 0, 0, 0)
        scope_row.addWidget(QLabel("Apply to"))
        scope_row.addWidget(self.size_scope, 1)
        layout.addWidget(self.scope_widget)
        self.calibration_widget.setVisible(False)
        self.scope_widget.setVisible(False)

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
        self._update_calibration_visibility()
        self.shell._release_window_later()

    @staticmethod
    def _lacks_size(record) -> bool:
        return not record.pixel_size_x or not record.pixel_size_y or (record.z_planes > 1 and not record.pixel_size_z)

    def _update_calibration_visibility(self) -> None:
        # The manual controls appear only when an included image lacks a pixel size, or a Z step for a stack.
        controller = self.shell.controller
        included = [r for r in controller.experiment.images if r.include] if controller is not None else []
        missing = [r for r in included if self._lacks_size(r)]
        if controller is not self._size_controller:
            self._size_controller, self._changing_sizes = controller, False
        self._changing_sizes = self._changing_sizes and bool(included)
        self.calibration_widget.setVisible(bool(missing) or self._changing_sizes)
        self.scope_widget.setVisible(bool(missing) or self._changing_sizes)
        self.pixel_summary.setVisible(bool(included) and not missing)
        self.summary_row.setVisible(bool(included) and not missing)
        self.change_sizes_button.setVisible(not self._changing_sizes)
        for widget in (self.pixel_x, self.pixel_y, self.pixel_z, self.apply_sizes_button):
            widget.setToolTip(widget.toolTip().split("\n\nShown because")[0])
        if missing:
            why = f"\n\nShown because {len(missing)} included image{'s lack' if len(missing) != 1 else ' lacks'} a pixel size or Z step."
            for widget in (self.pixel_x, self.pixel_y, self.pixel_z, self.apply_sizes_button):
                widget.setToolTip(widget.toolTip() + why)
            return
        if not included:
            return

        def span(values: list[float]) -> str:
            low, high = min(values), max(values)
            return f"{low:.3f}" if f"{low:.3f}" == f"{high:.3f}" else f"{low:.3f}–{high:.3f}"

        x_text = span([r.pixel_size_x for r in included])
        y_text = span([r.pixel_size_y for r in included])
        text = f"Pixel size {x_text} µm/pixel" if x_text == y_text else f"Pixel size X {x_text}, Y {y_text} µm/pixel"
        z_steps = [r.pixel_size_z for r in included if r.z_planes > 1 and r.pixel_size_z]
        if z_steps:
            text += f", Z step {span(z_steps)} µm"
        self.pixel_summary.setText(text)

    def _show_size_controls(self) -> None:
        self._changing_sizes = True
        self._update_calibration_visibility()

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
            self._update_calibration_visibility()
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
        self._changing_sizes = False
        targets = [image_id]
        if scope == "included":
            targets += [record.image_id for record in controller.experiment.images if record.include]
        elif scope == "missing":
            targets += [record.image_id for record in controller.experiment.images if self._lacks_size(record)]
        targets = list(dict.fromkeys(targets))
        for target in targets:
            record = controller.experiment.image(target)
            if scope == "missing" and target != image_id:
                # Only fill what is missing; sizes read from the file stay.
                controller.set_pixel_size(target, record.pixel_size_x or x, record.pixel_size_y or y, record.pixel_size_z or z)
            else:
                controller.set_pixel_size(target, x, y, z)
        self.refresh_table()
        self.shell._objects_panel.update_recommendation()
        if self.shell._shown_image_id in targets:
            self.shell.set_pixel_scale(controller.experiment.image(self.shell._shown_image_id))
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
        self._show_gpu_banner("Checking for a GPU...", "neutral")
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
        default_label = QLabel("Default channel")
        default_label.setToolTip("Used for every image left on Default below.")
        form.addRow(default_label, self.channel)
        self.image_tree = None
        self._build_image_channels()
        form.addRow(self.image_channels_box)
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
        # Full 3D only (Cellpose-SAM paper defaults); 0 voxels = scaled to each image.
        self.niter_3d = QSpinBox()
        self.niter_3d.setRange(10, 10000)
        self.niter_3d.setSingleStep(100)
        self.niter_3d.setValue(FULL_3D_NITER)
        guide.add_range_tip(self.niter_3d)
        self.flow3d_smooth = QDoubleSpinBox()
        self.flow3d_smooth.setRange(0, 10)
        self.flow3d_smooth.setDecimals(1)
        self.flow3d_smooth.setSingleStep(0.5)
        self.flow3d_smooth.setValue(FULL_3D_FLOW_SMOOTH)
        guide.add_range_tip(self.flow3d_smooth)
        self.min_voxels_3d = QSpinBox()
        self.min_voxels_3d.setRange(0, 1000000)
        self.min_voxels_3d.setSpecialValueText("auto")
        guide.add_range_tip(self.min_voxels_3d, "voxels")
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
        advanced.addRow("3D iterations", self.niter_3d)
        advanced.addRow("3D flow smoothing", self.flow3d_smooth)
        advanced.addRow("3D smallest object (voxels)", self.min_voxels_3d)
        layout.addWidget(self.advanced_toggle)
        layout.addWidget(self.advanced_box)
        actions = QHBoxLayout()
        preview = QPushButton("Preview")
        preview.clicked.connect(shell.preview_current)
        preview.setToolTip("Find objects in the part of the image on screen, to check the settings quickly.")
        run = QPushButton("Run this image")
        run.clicked.connect(shell.run_current)
        run.setToolTip("Find objects and measure markers in the whole image on screen. Use it to try the settings on one image.")
        run_all = QPushButton("Run all images")
        run_all.clicked.connect(lambda: shell._results_panel._run_all())
        run_all.setToolTip(
            "Run every included image with these settings, one after another. "
            "Use it when you already know the settings: no need to run or review one image first."
        )
        actions.addWidget(preview)
        actions.addWidget(run)
        actions.addWidget(run_all)
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
            self.channel.addItem(channel_label(channel.channel_index, channel.channel_name), channel.channel_index)
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
        self.niter_3d.setValue(int(parameters.get("niter_3d") or FULL_3D_NITER))
        self.flow3d_smooth.setValue(float(parameters.get("flow3D_smooth", FULL_3D_FLOW_SMOOTH)))
        self.min_voxels_3d.setValue(int(parameters.get("min_voxels_3d") or 0))
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
        self.refresh_image_channels()

    # -- a channel per image: pick images (or folders), give them a channel (step 2) ---------------

    def _build_image_channels(self) -> None:
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        header = QLabel("Channel per image ⓘ")
        header.setToolTip(
            "Select images (click, Ctrl-click, Shift-click; a folder selects every image in it), "
            "then pick their channel below. Images left on Default use the Default channel. One run does them all."
        )
        tree = QTreeWidget()
        tree.setColumnCount(2)
        tree.setHeaderLabels(["Image", "Channel"])
        tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        tree.setMinimumHeight(240)
        tree.header().setStretchLastSection(False)
        tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        tree.itemSelectionChanged.connect(self._image_selection_changed)
        self.image_tree = tree
        pick_row = QHBoxLayout()
        self.image_channel_label = QLabel("Selected: none")
        self.image_channel_pick = QComboBox()
        self.image_channel_pick.setToolTip("The channel to find objects in for the selected images.")
        self.image_channel_pick.activated.connect(lambda _i: self._image_channel_chosen())
        pick_row.addWidget(self.image_channel_label, 1)
        pick_row.addWidget(self.image_channel_pick)
        self.image_channel_counts = QLabel("")
        self.image_channel_counts.setStyleSheet("QLabel { color: gray; }")
        self.image_channel_counts.setWordWrap(True)
        column.addWidget(header)
        column.addWidget(tree)
        column.addLayout(pick_row)
        column.addWidget(self.image_channel_counts)
        self.image_channels_box = box

    def _selected_image_ids(self) -> list[str]:
        ids: list[str] = []

        def add(item) -> None:
            image_id = item.data(0, Qt.ItemDataRole.UserRole)
            if image_id:
                if image_id not in ids:
                    ids.append(str(image_id))
                return
            for index in range(item.childCount()):
                add(item.child(index))

        for item in self.image_tree.selectedItems():
            add(item)
        return ids

    def _image_channel_text(self, controller, ids: list[str]) -> str:
        chosen = controller.group_plan_channel(ids)
        if chosen is None:
            return "Default"
        return "Mixed" if chosen == "mixed" else str(chosen)

    def refresh_image_channels(self) -> None:
        tree = self.image_tree
        controller = self.shell.controller
        selected = set(self._selected_image_ids()) if tree.topLevelItemCount() else set()
        expanded = set()

        def walk(item) -> None:
            parts = item.data(0, Qt.ItemDataRole.UserRole + 1)
            if parts and item.isExpanded():
                expanded.add(tuple(parts))
            for index in range(item.childCount()):
                walk(item.child(index))

        for index in range(tree.topLevelItemCount()):
            walk(tree.topLevelItem(index))
        first_fill = tree.topLevelItemCount() == 0
        tree.blockSignals(True)
        tree.clear()
        if controller is None:
            tree.blockSignals(False)
            self._image_selection_changed()
            return
        from cellquant.plan import folder_of

        records = [record for record in controller.experiment.images if record.include]
        folders: dict[tuple[str, ...], QTreeWidgetItem] = {}
        folder_ids: dict[tuple[str, ...], list[str]] = {}

        def folder_item(parts: tuple[str, ...]):
            if not parts:
                return None
            if parts not in folders:
                parent = folder_item(parts[:-1])
                item = QTreeWidgetItem([parts[-1], ""])
                item.setData(0, Qt.ItemDataRole.UserRole + 1, list(parts))
                if parent is None:
                    tree.addTopLevelItem(item)
                else:
                    parent.addChild(item)
                folders[parts] = item
                folder_ids[parts] = []
            return folders[parts]

        for record in records:
            parts = tuple(part for part in folder_of(record).split("/") if part)
            parent = folder_item(parts)
            for depth in range(1, len(parts) + 1):
                folder_ids[parts[:depth]].append(record.image_id)
            leaf = QTreeWidgetItem([record.filename, self._image_channel_text(controller, [record.image_id])])
            leaf.setData(0, Qt.ItemDataRole.UserRole, record.image_id)
            leaf.setToolTip(0, record.relative_path or record.filename)
            if parent is None:
                tree.addTopLevelItem(leaf)
            else:
                parent.addChild(leaf)
            if record.image_id in selected:
                leaf.setSelected(True)
        for parts, item in folders.items():
            ids = folder_ids[parts]
            item.setText(0, f"{parts[-1]} ({len(ids)})")
            item.setText(1, self._image_channel_text(controller, ids))
            item.setExpanded(first_fill or parts in expanded)
        tree.blockSignals(False)
        self._image_selection_changed()
        counts: dict[str, int] = {}
        for record in records:
            text = self._image_channel_text(controller, [record.image_id])
            counts[text] = counts.get(text, 0) + 1
        default = channel_label(controller.recipe.object_set.segmentation_channel, controller._channel_name(controller.recipe.object_set.segmentation_channel))
        self.image_channel_counts.setText(
            " · ".join(f"{name}: {count}" for name, count in counts.items()).replace("Default:", f"Default ({default.split(' = ')[-1]}):")
        )

    def _image_selection_changed(self) -> None:
        controller = self.shell.controller
        ids = self._selected_image_ids() if self.image_tree is not None else []
        combo = self.image_channel_pick
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("Set channel…", "__none__")
        combo.model().item(0).setEnabled(False)
        if controller is not None and ids:
            combo.addItem("Default", "__default__")
            names: list[str] = []
            for image_id in ids:
                for name in controller.image_channel_names(image_id):
                    if name not in names:
                        names.append(name)
            for name in names:
                combo.addItem(name, name)
        combo.setCurrentIndex(0)
        combo.setEnabled(bool(ids))
        combo.blockSignals(False)
        self.image_channel_label.setText(f"Selected: {len(ids)} image{'' if len(ids) == 1 else 's'}" if ids else "Selected: none")

    def _image_channel_chosen(self) -> None:
        controller = self.shell.controller
        name = self.image_channel_pick.currentData()
        ids = self._selected_image_ids()
        if controller is None or not ids or name == "__none__":
            return
        if self.shell.is_busy():
            self.shell.message("Wait for the run to finish before changing channels.")
            self._image_selection_changed()
            return
        try:
            missing = controller.set_plan_channel_by_name(ids, None if name == "__default__" else name)
        except CellQuantError as exc:
            self.shell.message(str(exc))
            return
        if missing:
            self.shell.message(f"{len(missing)} selected image(s) have no {name} channel; left as they were.")
        else:
            label = "Default" if name == "__default__" else name
            self.shell.message(f"{len(ids)} image{'' if len(ids) == 1 else 's'} set to {label}.")
        self.shell.plan_changed()

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
        self._show_3d_rows()
        self._refresh_gpu_banner()

    def _show_3d_rows(self) -> None:
        if not hasattr(self, "min_voxels_3d"):  # the Z options are built before the Advanced box
            return
        shown = self.method.currentData() == "cellpose" and self.z_stack.currentData() == "full_3d"
        for widget in (self.niter_3d, self.flow3d_smooth, self.min_voxels_3d):
            _show_row(self._advanced_form, widget, shown)

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
        self._show_3d_rows()
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
            if status.get("kind") == "mps":
                return f"Apple GPU found: {status.get('name') or 'Apple silicon GPU'} (shares the Mac's memory)."
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
            if status.get("kind") == "mps":
                found = f"<b>✔ Apple GPU found:</b> {status.get('name') or 'Apple silicon GPU'}."
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
            if self.z_stack.currentData() == "full_3d":
                # Only for full 3D, so the settings (and saved results) of the other modes do not change.
                parameters["niter_3d"] = self.niter_3d.value()
                parameters["flow3D_smooth"] = self.flow3d_smooth.value()
                if self.min_voxels_3d.value():
                    parameters["min_voxels_3d"] = self.min_voxels_3d.value()
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
            self.meas_channel.addItem(channel_label(channel.channel_index, channel.channel_name), channel.channel_index)
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


class MaskColorDialog(QDialog):
    """Pick which of 64 common colors the object masks use; none picked means all different."""

    def __init__(self, on_change, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Mask colours")
        self._on_change = on_change
        layout = QVBoxLayout(self)
        self.all_different = QCheckBox("All different")
        self.all_different.setToolTip("Every object gets its own colour, with no limit on how many. Untick to pick colours.")
        self.all_different.toggled.connect(self._changed)
        layout.addWidget(self.all_different)
        self._grid_holder = QWidget()
        grid = QGridLayout(self._grid_holder)
        grid.setSpacing(2)
        self.swatches: dict[str, QToolButton] = {}
        for index, color in enumerate(MASK_PALETTE):
            button = QToolButton()
            button.setCheckable(True)
            button.setFixedSize(28, 28)
            button.setToolTip(color)
            button.setStyleSheet(
                f"QToolButton {{ background: {color}; border: 2px solid transparent; }}"
                "QToolButton:checked { border: 3px solid #ffffff; }"
            )
            button.toggled.connect(self._changed)
            grid.addWidget(button, index // 8, index % 8)
            self.swatches[color] = button
        layout.addWidget(self._grid_holder)
        self.note = QLabel("Tick one or more colours; they are used in turn.")
        layout.addWidget(self.note)
        self._loading = False

    def load(self, colors: list[str]) -> None:
        self._loading = True
        self.all_different.setChecked(not colors)
        for color, button in self.swatches.items():
            button.setChecked(color in colors)
        self._loading = False
        self._refresh()

    def chosen(self) -> list[str]:
        if self.all_different.isChecked():
            return []
        return [color for color, button in self.swatches.items() if button.isChecked()]

    def _refresh(self) -> None:
        self._grid_holder.setEnabled(not self.all_different.isChecked())
        self.note.setVisible(not self.all_different.isChecked())

    def _changed(self, _on=None) -> None:
        if self._loading:
            return
        self._refresh()
        self._on_change(self.chosen())


class EditPanel(QWidget):
    """Step 3: fix the objects found in step 2 before markers are measured."""

    def __init__(self, shell: CellQuantWindow):
        super().__init__()
        self.shell = shell
        self._labels = None
        # The object the user clicked. napari's own selected_label starts at 1, so it is not used
        # until the user changes it; otherwise Delete object would remove object 1 unasked.
        self._picked: int | None = None
        self._last_label = 1  # napari's selected_label when last seen
        self._click_bound = False
        layout = QVBoxLayout(self)
        self.selected = QLabel("Selected object: none")
        self.selected.setToolTip("Click an object in the image to select it.")
        layout.addWidget(self.selected)
        # One button per function. Which ones show depends on the mode: Home, Delete or Add (or all).
        self._mode = "home"
        self._buttons: dict[str, QPushButton] = {}
        rows = (
            ("Delete objects", self._enter_delete, "Add new objects", self._enter_add),
            ("Delete object", self._delete, "Restore object", self._restore),
            ("Draw area", self._draw_area, "Delete in area", self._delete_in_area),
            ("Draw new object", self._draw_new, None, None),
            ("Record drawn edits", self._commit, "Record all images", self._commit_all),  # a row of their own: no label cut off
            ("Undo", self._undo, "Back", self._go_home),
            ("Reset image", self._reset, "Reset all images", self._reset_all),
        )
        self._rows: list[tuple[QWidget, list[str]]] = []
        for left, left_slot, right, right_slot in rows:
            holder = QWidget()
            line = QHBoxLayout(holder)
            line.setContentsMargins(0, 0, 0, 0)
            names = []
            for text, slot in ((left, left_slot), (right, right_slot)):
                if text is None:
                    continue
                button = QPushButton(text)
                button.clicked.connect(slot)
                line.addWidget(button)
                self._buttons[text] = button
                names.append(text)
            layout.addWidget(holder)
            self._rows.append((holder, names))
        self.delete_button = self._buttons["Delete object"]
        self.undo_button = self._buttons["Undo"]
        for text, button in self._buttons.items():
            if text in guide.BUTTON_HELP:
                button.setToolTip(guide.BUTTON_HELP[text])
        self.mask_colors_row = QWidget()
        colors_line = QHBoxLayout(self.mask_colors_row)
        colors_line.setContentsMargins(0, 0, 0, 0)
        colors_line.addWidget(QLabel("Mask colours"))
        self.mask_colors_button = QPushButton()
        self.mask_colors_button.setToolTip("Colours of the object masks: every object its own colour, or the colours you tick, used in turn. Remembered on this computer.")
        self.mask_colors_button.clicked.connect(self._pick_mask_colors)
        colors_line.addWidget(self.mask_colors_button)
        colors_line.addStretch(1)
        self._mask_dialog = None
        self._show_mask_colors()
        layout.addWidget(self.mask_colors_row)
        self.show_all = QCheckBox("Show all functions")
        self.show_all.setToolTip("Show every function at once instead of one task at a time.")
        self.show_all.toggled.connect(lambda _on: self._apply_mode())
        # Drawing not recorded yet, per (analysis, image): (objects it was drawn on, drawn objects).
        # Kept when you move to another image, so Record all images can save every image at once.
        self._pending: dict[tuple[str, str], tuple[np.ndarray, np.ndarray]] = {}
        # (image, objects) the Objects layer was last given, before any drawing.
        self._shown: tuple[str, np.ndarray] | None = None
        self.pending_note = QLabel("")
        self.pending_note.setToolTip("Drawing on these images is kept until you record it or close CellQuant.")
        self.pending_note.hide()
        layout.addWidget(self.pending_note)
        # Why Record drawn edits did nothing, or that it worked; blank otherwise.
        self.record_status = QLabel("")
        self.record_status.setWordWrap(True)
        self.record_status.hide()
        layout.addWidget(self.record_status)
        layout.addStretch(1)
        layout.addWidget(self.show_all)
        self._apply_mode()

    def refresh(self) -> None:
        return

    _MODE_BUTTONS = {
        "home": ("Delete objects", "Add new objects", "Undo", "Record drawn edits", "Reset image", "Reset all images"),
        "delete": ("Delete object", "Restore object", "Draw area", "Delete in area", "Undo", "Back"),
        "add": ("Draw new object", "Record drawn edits", "Record all images", "Undo", "Back"),
    }
    _MODE_HINT = {
        "home": "Pick a task: delete objects or add new ones.",
        "delete": "Click an object, then <b>Delete object</b>.",
        "add": "<b>Draw new object</b>, paint it, then <b>Record drawn edits</b>.",
    }

    def _apply_mode(self) -> None:
        everything = self.show_all.isChecked()
        wanted = set(self._buttons) - {"Back"} if everything else set(self._MODE_BUTTONS[self._mode])
        for holder, names in self._rows:
            for name in names:
                self._buttons[name].setVisible(name in wanted)
            holder.setVisible(any(name in wanted for name in names))
        self.mask_colors_row.setVisible(everything or self._mode == "home")
        hint = "Click an object, then <b>Delete object</b>, or paint and <b>Record drawn edits</b>." if everything else self._MODE_HINT[self._mode]
        card = getattr(self.shell, "_step_pages", None)
        card = getattr(card[2], "todo_card", None) if card else None
        if card is not None:
            card.setText(f"{hint} ⓘ")

    def _show_mask_colors(self) -> None:
        chosen = mask_colors()
        self.mask_colors_button.setText(f"{len(chosen)} colours" if chosen else "All different")

    def _pick_mask_colors(self) -> None:
        if self._mask_dialog is None:
            self._mask_dialog = MaskColorDialog(self._set_mask_colors, self)
        self._mask_dialog.load(mask_colors())
        self._mask_dialog.show()
        self._mask_dialog.raise_()

    def _set_mask_colors(self, colors: list[str]) -> None:
        try:
            _settings().setValue("view/mask_colors", ";".join(colors))
        except Exception:  # noqa: BLE001 - not remembered, still applied
            pass
        self._show_mask_colors()
        self.shell.apply_mask_colors()

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        self._apply_mode()

    def _enter_delete(self) -> None:
        self.set_mode("delete")

    def _enter_add(self) -> None:
        self.set_mode("add")

    def _go_home(self) -> None:
        self.set_mode("home")

    def _draw_new(self) -> None:
        """Select the Objects layer, give it an unused number, and switch on napari's brush."""

        controller = self.shell.require_controller()
        if controller is None or not self.shell._nav_ids:
            return
        if self._labels is None or self._labels not in self.shell.viewer.layers:
            self.shell.message("Run step 2 on this image first.")
            return
        layer = self._labels
        top = int(np.max(layer.data)) if layer.data.size else 0
        current = controller.recall(self.shell._nav_ids[self.shell._nav_index])
        if current is not None and current.automated_labels.size:
            top = max(top, int(np.max(current.automated_labels)))  # numbers of deleted objects stay taken
        self.shell.viewer.layers.selection.active = layer
        layer.selected_label = top + 1
        layer.mode = "paint"

    def _say(self, text: str) -> None:
        self.record_status.setText(text)
        self.record_status.setVisible(bool(text))

    def _key(self, image_id: str) -> tuple[str, str]:
        controller = self.shell.controller
        return (controller.recipe.recipe_id or "" if controller is not None else "", image_id)

    def _show_pending(self) -> None:
        count = sum(1 for analysis, _image in self._pending if analysis == self._key("")[0])
        self.pending_note.setText(f"Not recorded yet: {count} image{'s' if count != 1 else ''}." if count else "")
        self.pending_note.setVisible(bool(count))

    def keep_drawing(self) -> None:
        """Before the Objects layer changes: keep what was drawn on the image it shows."""

        controller = self.shell.controller
        image_id = self.shell._labels_image_id
        if controller is None or image_id is None or self._labels is None or self._labels not in self.shell.viewer.layers:
            return
        # Compared with the objects the layer was given, not the newest result: after Delete object
        # the result has already changed, and the old outline is not a drawing.
        shown_id, base = self._shown if self._shown is not None else (None, None)
        drawn = np.asarray(self._labels.data)
        if shown_id != image_id or drawn.shape != base.shape:
            return
        if np.array_equal(drawn, base):
            self._pending.pop(self._key(image_id), None)
        else:
            self._pending[self._key(image_id)] = (np.array(base, copy=True), np.array(drawn, copy=True))
        self._show_pending()

    def bring_back_drawing(self, image_id: str | None, layer) -> None:
        """After an image's objects are shown: put back drawing not recorded yet (if made on these objects)."""

        controller = self.shell.controller
        self._shown = (image_id, np.array(layer.data, copy=True)) if image_id is not None else None
        if image_id is None or controller is None:
            return
        key = self._key(image_id)
        kept = self._pending.get(key)
        current = controller.last_results.get(image_id)
        if kept is None or current is None:
            return
        base, drawn = kept
        if base.shape == current.labels.shape and np.array_equal(base, current.labels):
            layer.data = np.array(drawn, copy=True)
        else:
            self._pending.pop(key, None)  # the objects changed (run again, deleted, ...): the drawing no longer applies
        self._show_pending()

    def bind_labels(self, boundaries) -> None:
        self._labels = boundaries
        self._last_label = int(boundaries.selected_label)
        boundaries.events.selected_label.connect(self._on_selected)
        self.clear_selection()
        if not self._click_bound:
            # A plain click on the image picks the object under the pointer, whichever layer is active.
            self.shell.viewer.mouse_drag_callbacks.append(self._on_click)
            self._click_bound = True

    def clear_selection(self) -> None:
        self._picked = None
        self.selected.setText("Selected object: none")
        self._say("")

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
        # napari sends this event without a value, and also when only the layer's colors change.
        value = int(self._labels.selected_label)
        if value == self._last_label:
            return
        self._last_label = value
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
        if self.shell._labels_image_id != image_id:
            # The picked number belongs to the objects on screen, not to an image still loading.
            self.shell.message("Wait for the image to finish loading, then click the object again.")
            self.clear_selection()
            return

        def finish(result) -> None:
            self.shell.show_result(result)

        if kind == "delete":
            self.shell._start_job(lambda: controller.delete_object(image_id, object_id), finish)
        else:
            self.shell._start_job(lambda: controller.restore_object(image_id, object_id), finish)

    def _draw_area(self) -> None:
        if self.shell.require_controller() is None or not self.shell._nav_ids:
            return
        layer = self.shell.delete_area_layer()
        self.shell.viewer.layers.selection.active = layer
        layer.mode = "add_polygon"

    def _delete_in_area(self) -> None:
        controller = self.shell.require_controller()
        if controller is None or not self.shell._nav_ids:
            return
        layer = self.shell.viewer.layers[DELETE_AREA_LAYER] if DELETE_AREA_LAYER in self.shell.viewer.layers else None
        polygons = drawn_polygons(layer) if layer is not None else []
        if not polygons:
            self.shell.message("Click Draw area and outline the objects to delete first.")
            return
        image_id = self.shell._nav_ids[self.shell._nav_index]

        def finish(outcome) -> None:
            result, count = outcome
            self.shell._drop(DELETE_AREA_LAYER)
            if result is None:
                self.shell.message("Run step 2 on this image first.")
                return
            self.shell.show_result(result)
            self.shell.message(f"Deleted {count} object{'s' if count != 1 else ''}." if count else "No object lies entirely inside the area.")

        self.shell._start_job(lambda: controller.delete_objects_in_area(image_id, polygons), finish)

    def _undo(self) -> None:
        controller = self.shell.require_controller()
        if controller is None or not self.shell._nav_ids:
            return
        image_id = self.shell._nav_ids[self.shell._nav_index]
        self.shell._start_job(lambda: controller.undo(image_id), lambda result: self.shell.show_result(result) if result is not None else None)

    def _discard_drawing(self, image_id: str) -> None:
        """Forget drawing not recorded yet; on screen, the Objects layer goes back to what it was given."""

        self._pending.pop(self._key(image_id), None)
        shown_id, base = self._shown if self._shown is not None else (None, None)
        if shown_id == image_id and self._labels is not None and self._labels in self.shell.viewer.layers:
            if np.asarray(self._labels.data).shape == base.shape:
                self._labels.data = np.array(base, copy=True)

    def _reset(self) -> None:
        """Back to the objects step 2 found for this image: recorded edits and unrecorded drawing go."""

        controller = self.shell.require_controller()
        if controller is None or not self.shell._nav_ids:
            return
        if self.shell.is_busy():
            self._say("Not reset: wait for the current analysis to finish.")
            return
        image_id = self.shell._nav_ids[self.shell._nav_index]
        self.keep_drawing()
        count = controller.edit_count(image_id)
        drawn = self._key(image_id) in self._pending
        if not count and not drawn:
            self._say("Nothing to reset: this image has no edits.")
            return
        parts = [f"{count} edit{'s' if count != 1 else ''}"] if count else []
        if drawn:
            parts.append("drawing not recorded yet")
        answer = QMessageBox.question(
            self,
            "Reset image?",
            f"Remove {' and '.join(parts)} on this image and go back to the objects found in step 2? This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._discard_drawing(image_id)
        self._show_pending()

        def finish(result) -> None:
            if result is not None:
                self.shell.show_result(result)
            self._say("Reset to the objects found in step 2.")

        self._say("")
        self.shell._start_job(lambda: controller.reset_edits(image_id), finish)

    def _reset_all(self) -> None:
        """Back to the objects step 2 found, on every image of this analysis."""

        controller = self.shell.require_controller()
        if controller is None:
            return
        if self.shell.is_busy():
            self._say("Not reset: wait for the current analysis to finish.")
            return
        self.keep_drawing()
        analysis = self._key("")[0]
        edited = [record.image_id for record in controller.experiment.images if controller.edit_count(record.image_id)]
        drawn = [image_id for owner, image_id in self._pending if owner == analysis]
        images = list(dict.fromkeys(edited + drawn))
        if not images:
            self._say("Nothing to reset: no image has edits.")
            return
        answer = QMessageBox.question(
            self,
            "Reset all images?",
            f"Remove every edit on {len(images)} image{'s' if len(images) != 1 else ''} and go back to the objects "
            "found in step 2? This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        for image_id in drawn:
            self._discard_drawing(image_id)
        self._show_pending()
        shown = self.shell._labels_image_id

        def reset_all():
            on_screen = None
            for image_id in edited:
                result = controller.reset_edits(image_id)
                if image_id == shown:
                    on_screen = result
            if on_screen is None and shown in drawn:
                on_screen = controller.recall(shown, measure=False)
            return on_screen

        def finish(on_screen) -> None:
            if on_screen is not None:
                self.shell.show_result(on_screen)
            self._say(f"Reset {len(images)} image{'s' if len(images) != 1 else ''} to the objects found in step 2.")

        self._say("")
        self.shell._start_job(reset_all, finish)

    def _commit(self) -> None:
        controller = self.shell.require_controller()
        if controller is None or not self.shell._nav_ids:
            return
        image_id = self.shell._nav_ids[self.shell._nav_index]
        if self.shell.is_busy():
            self._say("Not recorded: wait for the current analysis to finish.")
            return
        if self.shell.image_loading() or self.shell._shown_image_id != image_id:
            self._say("Not recorded: wait for the image to finish loading.")
            return
        current = controller.recall(image_id)
        if "Objects" not in self.shell.viewer.layers or current is None:
            self._say("Not recorded: run step 2 on this image first.")
            return
        # A copy: painting can go on while the edits are saved.
        drawn = np.array(self.shell.viewer.layers["Objects"].data, copy=True)
        if drawn.shape != current.labels.shape:
            self._say("Not recorded: the objects on screen are not this image's. Show the image again, then draw.")
            return
        if np.array_equal(drawn, current.labels):
            self._say("Nothing to record: paint or erase in the Objects layer first.")
            return

        def finish(result) -> None:
            self._pending.pop(self._key(image_id), None)
            self.shell.show_result(result)
            self._show_pending()
            self._say("Recorded.")

        self._say("")
        self.shell._start_job(lambda: controller.commit_drawn_labels(image_id, drawn), finish)

    def _commit_all(self) -> None:
        controller = self.shell.require_controller()
        if controller is None:
            return
        if self.shell.is_busy():
            self._say("Not recorded: wait for the current analysis to finish.")
            return
        if self.shell.image_loading():
            self._say("Not recorded: wait for the image to finish loading.")
            return
        self.keep_drawing()
        analysis = self._key("")[0]
        todo = {image_id: kept for (owner, image_id), kept in self._pending.items() if owner == analysis}
        if not todo:
            self._say("Nothing to record: paint or erase in the Objects layer first.")
            return
        shown = self.shell._labels_image_id

        def record_all():
            recorded, skipped, on_screen = [], [], None
            for image_id, (base, drawn) in todo.items():
                current = controller.recall(image_id)
                if current is None or current.labels.shape != base.shape or not np.array_equal(current.labels, base):
                    skipped.append(image_id)
                    continue
                result = controller.commit_drawn_labels(image_id, drawn)
                recorded.append(image_id)
                if image_id == shown:
                    on_screen = result
            return recorded, skipped, on_screen

        def finish(outcome) -> None:
            recorded, skipped, on_screen = outcome
            for image_id in recorded + skipped:
                self._pending.pop((analysis, image_id), None)
            if on_screen is not None:
                self.shell.show_result(on_screen)
            self._show_pending()
            text = f"Recorded {len(recorded)} image{'s' if len(recorded) != 1 else ''}."
            if skipped:
                text += f" {len(skipped)} skipped: their objects changed after you drew."
            self._say(text)

        self._say("")
        self.shell._start_job(record_all, finish)



class CutoffHistogram(QWidget):
    """Every object's value as a histogram, with the cutoff as a line. Drag the line, or click, to move it."""

    cutoff_dragged = Signal(float)

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(80)
        self.setCursor(Qt.SizeHorCursor)
        self._values = np.zeros(0)
        self._low, self._high = 0.0, 1.0
        self._cutoff = 0.0
        self._at_least = False

    def set_data(self, values: np.ndarray, low: float, high: float, cutoff: float, comparison: str) -> None:
        values = np.asarray(values, dtype=float)
        self._values = values[np.isfinite(values)]
        self._low, self._high = float(low), float(high)
        self._cutoff = float(cutoff)
        self._at_least = comparison == "at_least"
        self.update()

    def set_cutoff(self, value: float) -> None:
        self._cutoff = float(value)
        self.update()

    def _plot_area(self):
        return 4, 4, max(1, self.width() - 8), max(1, self.height() - 8)

    def _x(self, value: float) -> float:
        left, _top, width, _height = self._plot_area()
        span = (self._high - self._low) or 1.0
        return left + (value - self._low) / span * width

    def _value(self, x: float) -> float:
        left, _top, width, _height = self._plot_area()
        fraction = min(1.0, max(0.0, (x - left) / width))
        return self._low + fraction * (self._high - self._low)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt name
        from qtpy.QtGui import QPainter, QPen

        painter = QPainter(self)
        left, top, width, height = self._plot_area()
        text_color = self.palette().color(self.foregroundRole())
        if self._values.size:
            counts, edges = np.histogram(np.clip(self._values, self._low, self._high), bins=48, range=(self._low, self._high))
            # Log heights: a few bright positives stay visible next to many dim negatives.
            heights = np.log1p(counts) / max(np.log1p(counts.max()), 1e-9)
            positive, negative = (QColor(color) for color in classification_colors())
            for count, fraction, start, end in zip(counts, heights, edges[:-1], edges[1:]):
                if not count:
                    continue
                middle = (start + end) / 2
                past = middle >= self._cutoff if self._at_least else middle > self._cutoff
                x0, x1 = self._x(start), self._x(end)
                bar = max(1, int(fraction * height))
                painter.fillRect(int(x0), top + height - bar, max(1, int(x1 - x0) - 1), bar, positive if past else negative)
        pen = QPen(text_color)
        pen.setWidth(2)
        painter.setPen(pen)
        x = int(self._x(self._cutoff))
        painter.drawLine(x, top, x, top + height)
        painter.end()

    def _drag(self, event) -> None:
        position = event.position().x() if hasattr(event, "position") else event.x()
        value = self._value(position)
        self.set_cutoff(value)
        self.cutoff_dragged.emit(value)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt name
        if self._values.size:
            self._drag(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt name
        if self._values.size and event.buttons() & Qt.LeftButton:
            self._drag(event)


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
        self.histogram = CutoffHistogram()
        self.histogram.cutoff_dragged.connect(lambda value: self.threshold_slider.setValue(value))
        layout.addWidget(self.histogram)
        self.threshold_slider = QLabeledDoubleSlider(Qt.Horizontal)
        self.threshold_slider.setRange(0.0, 1.0)
        self.threshold_slider.valueChanged.connect(self._slider_moved)
        layout.addWidget(self.threshold_slider)
        self.cutoff_tools = QWidget()
        tools = QFormLayout(self.cutoff_tools)
        tools.setContentsMargins(0, 0, 0, 0)
        from cellquant.quicksetup import AUTO_METHODS

        self.auto_method = QComboBox()
        for value, text, tip in AUTO_METHODS:
            self.auto_method.addItem(text, value)
            self.auto_method.setItemData(self.auto_method.count() - 1, tip, Qt.ToolTipRole)
        self.auto_method.activated.connect(lambda _index: self._auto_method_chosen())
        self.auto_percent = QDoubleSpinBox()
        self.auto_percent.setRange(0.1, 99.9)
        self.auto_percent.setDecimals(1)
        self.auto_percent.setSuffix(" %")
        self.auto_percent.editingFinished.connect(self._auto_method_chosen)
        method_row = QHBoxLayout()
        method_row.addWidget(self.auto_method, 1)
        method_row.addWidget(self.auto_percent)
        tools.addRow("Auto", method_row)
        self.apply_to = QComboBox()
        self.apply_to.addItem("All images", "all")
        self.apply_to.addItem("This image only", "image")
        self.apply_to.activated.connect(lambda _index: self._apply_to_chosen())
        tools.addRow("Apply to", self.apply_to)
        self.reset_cutoff = QPushButton("Reset cutoff")
        self.reset_cutoff.clicked.connect(self._reset_cutoff)
        tools.addRow(self.reset_cutoff)
        layout.addWidget(self.cutoff_tools)
        # Recolor at most every 40 ms while dragging, with the latest value.
        self._pending_cutoff: float | None = None
        self._cutoff_timer = QTimer(self)
        self._cutoff_timer.setSingleShot(True)
        self._cutoff_timer.setInterval(40)
        self._cutoff_timer.timeout.connect(self._apply_pending_cutoff)
        layout.addWidget(self._pixel_level_box())
        self.counts = QLabel("Positive: 0\nNegative: 0\nPercent positive: —")
        layout.addWidget(self.counts)
        layout.addWidget(self._strip_box())
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
        style = QHBoxLayout()
        style.addWidget(QLabel("Overlay"))
        self.overlay_style = QComboBox()
        self.overlay_style.addItem("Fill", "fill")
        self.overlay_style.addItem("Outline", "outline")
        self.overlay_style.setToolTip("Fill colors each whole object; Outline colors only its edge.")
        self.overlay_style.setCurrentIndex(1 if classification_outline() else 0)
        self.overlay_style.activated.connect(lambda _index: self._overlay_style_chosen())
        style.addWidget(self.overlay_style)
        style.addStretch(1)
        advanced.addLayout(style)
        self._show_colors()
        layout.addWidget(self.advanced_toggle)
        layout.addWidget(self.advanced_box)
        self.qc = QLabel("")
        self.qc.setWordWrap(True)
        layout.addWidget(self.qc)
        layout.addStretch(1)

    def _strip_box(self) -> QGroupBox:
        """Percent positive in every image (or the ticked ones) at the current cutoffs."""

        self.strip = QGroupBox("Images ⓘ")
        box = QVBoxLayout(self.strip)
        self.strip_mode = QComboBox()
        self.strip_mode.addItem("All images", "all")
        self.strip_mode.addItem("Selected images", "selected")
        self.strip_mode.activated.connect(lambda _index: self._strip_mode_chosen())
        box.addWidget(self.strip_mode)
        self.strip_table = QTableWidget(0, 3)
        self.strip_table.setHorizontalHeaderLabels(["Image", "Positive", "%"])
        self.strip_table.horizontalHeaderItem(1).setToolTip("Positive / counted objects at the cutoff the image uses (* = its own cutoff).")
        self.strip_table.horizontalHeaderItem(2).setToolTip("Percent positive.")
        self.strip_table.verticalHeader().setVisible(False)
        self.strip_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.strip_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.strip_table.horizontalHeader().setStretchLastSection(False)
        self.strip_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.strip_table.setMaximumHeight(170)
        self.strip_table.itemChanged.connect(self._strip_ticked)
        self.strip_table.cellDoubleClicked.connect(self._strip_open)
        box.addWidget(self.strip_table)
        self.strip_total = QLabel("")
        box.addWidget(self.strip_total)
        self._strip_ticks: set[str] | None = None  # loaded per experiment
        self._strip_experiment = ""
        return self.strip

    def _strip_settings_key(self) -> str:
        return f"review/strip/{self.shell.controller.experiment.experiment_id}"

    def _load_strip_choice(self) -> None:
        """The mode and ticked images, remembered per experiment on this computer."""

        experiment_id = self.shell.controller.experiment.experiment_id
        if self._strip_experiment == experiment_id:
            return
        self._strip_experiment = experiment_id
        mode, ticks = "all", None
        try:
            settings = _settings()
            mode = str(settings.value(f"{self._strip_settings_key()}/mode", "all"))
            stored = settings.value(f"{self._strip_settings_key()}/ticked", None)
            if stored is not None:
                ticks = {str(item) for item in (stored if isinstance(stored, (list, tuple)) else [stored]) if item}
        except Exception:  # noqa: BLE001 - settings unreadable: all images
            pass
        self.strip_mode.setCurrentIndex(max(0, self.strip_mode.findData(mode)))
        self._strip_ticks = ticks

    def _save_strip_choice(self) -> None:
        try:
            settings = _settings()
            settings.setValue(f"{self._strip_settings_key()}/mode", self.strip_mode.currentData())
            settings.setValue(f"{self._strip_settings_key()}/ticked", sorted(self._strip_ticks or []))
        except Exception:  # noqa: BLE001 - not remembered this time
            pass

    def _strip_selected(self) -> bool:
        return self.strip_mode.currentData() == "selected"

    def strip_pool(self) -> list[str] | None:
        """The images Auto and Reset use for All images: None for every measured image."""

        if not self._strip_selected() or self.shell.controller is None:
            return None
        return [record.image_id for record in self.shell.controller.experiment.images if record.image_id in (self._strip_ticks or set())]

    def _strip_mode_chosen(self) -> None:
        if self._strip_selected() and self._strip_ticks is None:
            self._strip_ticks = {self._shown_image_id()} - {None}  # start from the image shown
        self._save_strip_choice()
        self._show_strip()

    def _strip_ticked(self, item) -> None:
        if item.column() != 0 or not self._strip_selected():
            return
        image_id = item.data(Qt.UserRole)
        ticks = set(self._strip_ticks or set())
        if item.checkState() == Qt.Checked:
            ticks.add(image_id)
        else:
            ticks.discard(image_id)
        if ticks != self._strip_ticks:
            self._strip_ticks = ticks
            self._save_strip_choice()
            self._show_strip()

    def _strip_open(self, row: int, _column: int) -> None:
        image_id = self.strip_table.item(row, 0).data(Qt.UserRole)
        if image_id not in self.shell._nav_ids:
            self.shell.message("That image is not in the Previous / Next list now (see Check: All included).")
        elif image_id != self._shown_image_id():
            self.shell._nav_index = self.shell._nav_ids.index(image_id)
            self.shell.show_current()

    def _show_strip(self) -> None:
        """One row per included image: positive / counted objects and percent positive at the cutoff it
        uses. Selected images: tick the images to show and to find the automatic cutoff from."""

        controller, classification = self.shell.controller, self._current_classification()
        self.strip.setVisible(classification is not None)
        if classification is None:
            return
        self._load_strip_choice()
        selected = self._strip_selected()
        included = [record for record in controller.experiment.images if record.include]
        counts = controller.positive_counts(classification.id, [record.image_id for record in included])
        shown = self._shown_image_id()
        table = self.strip_table
        table.blockSignals(True)
        try:
            table.setRowCount(len(included))
            total_positive = total_counted = 0
            for row, record in enumerate(included):
                ticked = record.image_id in (self._strip_ticks or set())
                name = QTableWidgetItem(record.sample_name or record.filename)
                name.setData(Qt.UserRole, record.image_id)
                name.setToolTip(record.relative_path or record.filename)
                if selected:
                    name.setFlags(name.flags() | Qt.ItemIsUserCheckable)
                    name.setCheckState(Qt.Checked if ticked else Qt.Unchecked)
                else:
                    name.setFlags(name.flags() & ~Qt.ItemIsUserCheckable)
                    name.setData(Qt.CheckStateRole, None)
                font = name.font()
                font.setBold(record.image_id == shown)
                name.setFont(font)
                own = classification.image_thresholds.get(record.image_id) is not None
                if record.image_id in counts:
                    positive, counted = counts[record.image_id]
                    count_text = f"{positive} / {counted}"
                    percent_text = f"{100 * positive / counted:.1f}" if counted else "—"
                    if not selected or ticked:
                        total_positive += positive
                        total_counted += counted
                else:
                    count_text, percent_text = "—", "—"
                cells = (name, QTableWidgetItem(count_text + (" *" if own else "")), QTableWidgetItem(percent_text))
                for column, cell in enumerate(cells):
                    if column:
                        cell.setTextAlignment(int(Qt.AlignRight | Qt.AlignVCenter))
                    if selected and not ticked:
                        cell.setForeground(self.palette().color(QPalette.Disabled, QPalette.Text))
                    table.setItem(row, column, cell)
            table.resizeColumnToContents(1)
            table.resizeColumnToContents(2)
        finally:
            table.blockSignals(False)
        overall = f"{100 * total_positive / total_counted:.1f}%" if total_counted else "—"
        scope = "Ticked images" if selected else "All images"
        self.strip_total.setText(f"{scope}: {total_positive} / {total_counted} positive ({overall})")

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
        self.min_percent.setValue(classification.threshold_for(self._shown_image_id()))
        self.pixel_level.setValue(float(spec.pixel_level))
        self.use_high.setChecked(spec.pixel_level_high is not None)
        self.pixel_level_high.setEnabled(spec.pixel_level_high is not None)
        if spec.pixel_level_high is not None:
            self.pixel_level_high.setValue(float(spec.pixel_level_high))
        for widget in (self.min_percent, self.pixel_level, self.pixel_level_high, self.use_high):
            widget.blockSignals(False)

    def _min_percent_changed(self, value: float) -> None:
        classification = self._current_classification()
        if classification is None or classification.threshold_for(self._shown_image_id()) == float(value):
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

    def show_no_result(self) -> None:
        """An image not run yet: no numbers left over from the previous image."""

        self.update_status_line()
        self.counts.setText("Not run yet.")
        self.qc.setText("")
        self._show_strip()  # the bold row follows the image shown

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
        self.counts.setText("")  # filled in below when a marker is shown; clears "Not run yet."
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
        for widget in (self.cutoff_label, self.histogram, self.threshold_slider, self.cutoff_tools, self.strip):
            widget.setVisible(bool(classification_id))
        if not classification_id:
            return
        classification = next(item for item in self.shell.controller.recipe.classifications if item.id == classification_id)
        if classification.measurement not in result.objects.columns:
            self._show_strip()  # no value for this marker here: the strip must not keep the last marker's numbers
            return
        image_id = result.provenance["image_id"]
        cutoff = classification.threshold_for(image_id)
        values = result.objects[classification.measurement].to_numpy(dtype=float)
        percent_rule = self._current_percent_measurement() is not None
        self._set_slider(values, cutoff, percent_rule)
        slider = self.threshold_slider
        self.histogram.set_data(values, slider.minimum(), slider.maximum(), cutoff, classification.comparison)
        self._show_cutoff_tools(classification, image_id, percent_rule)
        counts = self.shell.controller.histogram(image_id, classification.measurement, cutoff, classification.comparison)
        percent = counts["percent_positive"]
        percent_text = "—" if percent != percent else f"{percent:.1f}%"
        shown = self.shell.controller.recipe.for_image(image_id)  # this image's own cutoff, if it has one
        rule = guide.describe_rule(shown, next(item for item in shown.classifications if item.id == classification_id))
        self.counts.setText(
            f"Positive when: {rule}\n"
            f"Positive: {counts['positive']}\nNegative: {counts['negative']}\nUnmeasured: {int(counts['n_missing'])}\nPercent positive: {percent_text}"
        )
        self._show_strip()

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

    def _show_cutoff_tools(self, classification, image_id: str, percent_rule: bool) -> None:
        """Auto method, Apply to and Reset as stored for this marker and image."""

        own = classification.image_thresholds.get(image_id) is not None
        self.apply_to.setCurrentIndex(self.apply_to.findData("image" if own else "all"))
        self.auto_method.setCurrentIndex(max(0, self.auto_method.findData(classification.auto_method)))
        self.auto_percent.blockSignals(True)
        self.auto_percent.setValue(float(classification.auto_percent))
        self.auto_percent.blockSignals(False)
        self.auto_percent.setVisible(classification.auto_method == "top_percent")
        if not self.threshold_slider.isEnabled():
            return  # locked while a run uses the settings; unlocking restores them
        # The percent-of-cell rule's cutoff is a minimum percent: no automatic value for it.
        for widget in (self.auto_method, self.auto_percent, self.reset_cutoff):
            widget.setEnabled(not percent_rule)

    def _this_image_only(self) -> bool:
        return self.apply_to.currentData() == "image"

    def _shown_image_id(self) -> str | None:
        if self.shell.controller is None or not self.shell._nav_ids:
            return None
        return self.shell._nav_ids[self.shell._nav_index]

    def _auto_method_chosen(self) -> None:
        """Remember the method and move the cutoff to its value, like a slider move: for all images
        (from every measured image) or for this image only. Own cutoffs of other images stay."""

        controller, classification, image_id = self.shell.controller, self._current_classification(), self._shown_image_id()
        if classification is None or image_id is None:
            return
        method, percent = str(self.auto_method.currentData()), round(self.auto_percent.value(), 1)
        self.auto_percent.setVisible(method == "top_percent")
        if method == classification.auto_method and (method != "top_percent" or percent == round(classification.auto_percent, 1)):
            return  # nothing changed (for example the % box lost focus)
        controller.set_auto_method(classification.id, method, percent)
        try:
            value, used = controller.auto_cutoff(
                classification.id, image_id, this_image_only=self._this_image_only(), pool=self.strip_pool()
            )
        except CellQuantError as exc:
            self.shell.message(f"{exc} The method is kept for later.")
            return
        old = classification.threshold_for(image_id)
        result = controller.update_thresholds(image_id, {classification.id: value}, this_image_only=self._this_image_only())
        source = "this image" if self._this_image_only() else f"{used} image{'s' if used != 1 else ''}"
        self._after_cutoff_change(result, f"{classification.name} cutoff ({self.auto_method.currentText()}, from {source}): {old:g} → {value:g}.")

    def _after_cutoff_change(self, result, text: str = "") -> None:
        """Save, show the new calls, and say (and show) which approvals the change cleared."""

        controller = self.shell.controller
        controller.save()
        if result is not None:
            self.shell.show_classification(result)
        cleared = len(controller.cleared_approvals)
        if cleared:
            text = f"{text} Approval cleared for {cleared} image{'s' if cleared != 1 else ''} whose percent positive changed.".strip()
            self.shell._experiment_panel.refresh_table()
            self.shell._refresh_plan()
            self.update_status_line()
            self.shell.refresh_guidance()
        if text:
            self.shell.message(text)

    def _apply_to_chosen(self) -> None:
        """This image only: it keeps its cutoff as its own. All images: it uses the shared cutoff again."""

        controller, classification, image_id = self.shell.controller, self._current_classification(), self._shown_image_id()
        if classification is None or image_id is None:
            return
        own = classification.image_thresholds.get(image_id) is not None
        if self._this_image_only() == own:
            return
        if self._this_image_only():
            result = controller.update_thresholds(image_id, {classification.id: classification.threshold}, this_image_only=True)
            text = f"{classification.name}: this image now has its own cutoff. Other images keep the shared one."
        else:
            controller.use_shared_cutoff(classification.id, image_id)
            result = controller.classified_now(image_id)
            text = f"{classification.name}: this image uses the shared cutoff ({classification.threshold:g}) again."
        self._after_cutoff_change(result, text)

    def _reset_cutoff(self, _checked: bool = False) -> None:
        """Back to the automatic cutoff: for this image's own objects (This image only), or found
        again from every measured included image (All images; then no image keeps its own)."""

        controller, classification, image_id = self.shell.controller, self._current_classification(), self._shown_image_id()
        if classification is None or image_id is None:
            return
        this_image = self._this_image_only()
        own = [] if this_image else list(classification.image_thresholds)
        approved = [
            record.image_id
            for record in controller.experiment.images
            if record.processing_status == "approved" and (not this_image or record.image_id == image_id)
        ]
        if own or approved:
            lines = [f"Set the {classification.name} cutoff to its automatic value ({self.auto_method.currentText()})"
                     + (" for this image?" if this_image else " for all images?")]
            if own:
                lines.append(f"{len(own)} image{'s' if len(own) != 1 else ''} with {'their' if len(own) != 1 else 'its'} own cutoff will use the shared one.")
            if approved:
                lines.append("Approved images whose percent positive changes will need approving again.")
            answer = QMessageBox.question(self, "Reset cutoff", "\n".join(lines), QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
        try:
            old, new, used = controller.reset_cutoff(classification.id, image_id, this_image_only=this_image, pool=self.strip_pool())
        except CellQuantError as exc:
            self.shell.message(str(exc))
            return
        source = "this image" if this_image else f"{used} image{'s' if used != 1 else ''}"
        self._after_cutoff_change(controller.classified_now(image_id), f"{classification.name} cutoff (from {source}): {old:g} → {new:g}.")

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

    def _overlay_style_chosen(self) -> None:
        save_classification_style(self.overlay_style.currentData() == "outline")
        self.shell.apply_classification_colors()

    def _colors_changed(self) -> None:
        self._show_colors()
        self.shell.apply_classification_colors()

    def _threshold_moved(self, value: float) -> None:
        controller = self.shell.controller
        classification_id = self.classification_id()
        if controller is None or not classification_id or not self.shell._nav_ids:
            return
        if self._current_classification() is None:
            return  # the marker shown was removed in step 4
        image_id = self.shell._nav_ids[self.shell._nav_index]
        result = controller.update_thresholds(image_id, {classification_id: value}, this_image_only=self._this_image_only())
        if controller.cleared_approvals:
            self._after_cutoff_change(result)
        elif result is not None:
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
        if controller.experiment.image(image_id).processing_status not in ("analyzed", "needs_attention", "reviewed", "approved"):
            self.shell.message("Run this image first.")
            return
        controller.set_status(image_id, status)
        self.shell._autosave()
        self.shell._experiment_panel.refresh_table()
        self.shell._refresh_plan()
        self.update_status_line()
        self.shell.refresh_guidance()
        if not self.shell._footer._navigation[1].isEnabled():
            self.shell.message("Approved.")  # image navigation is locked while all analyses run
            return
        self.shell._show_note = "Approved."
        if not self._open_next_unchecked():
            self.shell._show_note = None
            self.shell.message("Approved. All images checked.")

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
            "Then click Run all analyses in step 6."
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


class FooterScroll(QScrollArea):
    """The bottom bar scrolls sideways in a narrow window instead of cutting off buttons and text."""

    def __init__(self, footer: QWidget):
        super().__init__()
        self.setWidget(footer)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        footer.installEventFilter(self)  # wrapped text or the log changes the height the bar needs

    def eventFilter(self, watched, event) -> bool:
        if watched is self.widget() and event.type() == QEvent.Type.LayoutRequest:
            QTimer.singleShot(0, self.fit_height)
        return False

    def _height(self) -> int:
        footer = self.widget()
        width = max(self.viewport().width(), footer.minimumSizeHint().width())
        layout = footer.layout()
        height = layout.heightForWidth(width) if layout.hasHeightForWidth() else footer.sizeHint().height()
        height = max(height, footer.minimumSizeHint().height())
        if footer.minimumSizeHint().width() > self.viewport().width():
            height += self.horizontalScrollBar().sizeHint().height()
        return height

    def fit_height(self) -> None:
        """Grow or shrink to the bar's height, plus the scroll bar when it shows."""

        height = self._height()
        if self.minimumHeight() != height:
            self.setMinimumHeight(height)
            self.updateGeometry()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.minimumHeight() != self._height():
            QTimer.singleShot(0, self.fit_height)

    def sizeHint(self):
        return QSize(self.widget().sizeHint().width(), self._height())

    def minimumSizeHint(self):
        return QSize(100, self._height())


class Footer(QWidget):
    def __init__(self, shell: CellQuantWindow):
        super().__init__()
        self.shell = shell
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        previous = QPushButton("◀ Previous image")
        next_image = QPushButton("Next image ▶")
        previous.setToolTip("Show the previous image in the experiment.")
        next_image.setToolTip("Show the next image in the experiment.")
        previous.clicked.connect(lambda: self._step(-1))
        next_image.clicked.connect(lambda: self._step(1))
        self._navigation = (previous, next_image)
        self.pause = QPushButton("Pause")
        self.resume = QPushButton("Resume")
        self.cancel = QPushButton("Cancel")
        self.pause.clicked.connect(self._pause)
        self.resume.clicked.connect(self._resume)
        self.cancel.clicked.connect(self._cancel)
        # Running starts in the step windows; this bar moves between images and follows a run.
        for button in (previous, next_image):
            row.addWidget(button)
        self.keep_awake = QCheckBox("Keep computer awake (recommended) ⓘ")
        self.keep_awake.setToolTip(
            "While a run is going, keep this computer and its screen on. Sleep pauses the run "
            "until someone wakes the computer, and on many laptops the screen turning off means sleep."
        )
        self.keep_awake.setChecked(keep_awake_preferred())
        self.keep_awake.setVisible(keep_awake.supported())
        self.keep_awake.toggled.connect(shell._keep_awake_toggled)
        self.all_channels = QCheckBox("Show all channels ⓘ")
        self.all_channels.setToolTip(
            "When a segmented image opens, show every channel. Off: only the segmented channel and "
            "the objects show; the other channels stay in the layer list, hidden. Applies the next "
            "time an image is shown."
        )
        self.all_channels.setChecked(show_all_channels_preferred())
        self.all_channels.toggled.connect(save_show_all_channels)
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
        self.position = QLabel("No image open.")
        self.units = QLabel("Units: pixels")
        self.progress = QProgressBar()
        self.progress.setMinimumWidth(80)
        self.time_left = QLabel("")
        self.time_left.setMinimumWidth(110)
        self.time_left.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._clock = None  # a TimeLeft while a run is going
        self._step_line: str | None = None  # the running step, shown with its time so far
        self._step_name: str | None = None
        self._step_since: float | None = None  # when a step of unknown length started
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
        self.log_toggle.toggled.connect(self._show_log)
        # Long text wraps onto more lines in a narrow window instead of being cut off.
        for label in (self.position, self.units, self.status):
            label.setWordWrap(True)
        row.addWidget(self.position, 3)
        row.addWidget(self.units, 2)
        layout.addLayout(row)
        bar = QHBoxLayout()
        bar.addWidget(self.progress, 2)
        bar.addWidget(self.time_left)
        bar.addWidget(self.status, 3)
        for button in (self.pause, self.resume, self.cancel, self.log_toggle):
            bar.addWidget(button)
        bar.addWidget(self.all_channels)
        bar.addWidget(self.keep_awake)
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
        self.position.setToolTip(self.position.text())

    def set_units(self, unit: str, keep_detail: bool = False) -> None:
        """The units line; keep_detail keeps what follows it (for example 'Analyzed: …')."""

        separator = "   ·   "
        if keep_detail and separator in unit + self.units.text():
            current = self.units.text()
            if separator in current and separator not in unit:
                unit = unit + separator + current.split(separator, 1)[1]
        self.units.setText(f"Units: {unit}")
        self.units.setToolTip(self.units.text())

    def message(self, text: str) -> None:
        self._step_line = None  # the step line stops ticking once something else is said
        self.status.setText(text)
        self.status.setToolTip(text)
        self.log.append(text)

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
        self._show_step_text()
        self._log_batch_notes()

    def _log_batch_notes(self) -> None:
        from cellquant.hardware import batch_notes

        for note in batch_notes():
            self.log.append(note)

    def _show_step_text(self) -> None:
        """The step line, with how long a step of unknown length (a whole 3D volume) has run so far."""

        import time

        from cellquant.progress import elapsed_text

        line = self._step_line
        if line is None:
            return
        since = self._step_since
        if since is not None and time.monotonic() - since >= 5:
            dots = "..." if line.endswith("...") else ""
            line = f"{line.removesuffix('...')}, {elapsed_text(time.monotonic() - since)} so far{dots}"
        self._step_text = line
        self.status.setText(line)

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
        self._log_batch_notes()
        # A step line left over from the finished work would read as still running.
        if self.status.text() and self.status.text() == getattr(self, "_step_text", None):
            self.status.setText("")
        self._step_text = None
        self._step_line = None

    def show_step(self, text: str, fraction: float) -> None:
        """One step of the running analysis, e.g. 'Finding objects: slice 3 of 7'."""

        import time

        if fraction >= 0 or text != self._step_name:
            self._step_since = time.monotonic() if fraction < 0 else None
        self._step_name = text
        if self._clock is not None:
            self._clock.step(text, None if fraction < 0 else fraction)
            self._show_time_left()
        total = getattr(self, "_batch_total", 0)
        if total:
            done = (self._batch_index - 1 + max(fraction, 0.0)) / total
            self.progress.setRange(0, 1000)
            self.progress.setValue(int(round(1000 * done)))
            self._step_line = f"Image {self._batch_index} of {total} ({self._batch_name}): {text}"
            self._show_step_text()
            return
        if fraction < 0:
            self.progress.setRange(0, 0)  # busy: this step's length is unknown
        else:
            self.progress.setRange(0, 1000)
            self.progress.setValue(int(round(1000 * fraction)))
        self._step_line = text + "..."
        self._show_step_text()

    def update_progress(self, index: int, total: int, filename: str, status: str) -> None:
        from cellquant.progress import elapsed_text

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
            self._log_batch_notes()  # e.g. the 3D time split, before this image's line
            took = self._clock.last_image_seconds() if self._clock is not None else None
            timing = "" if took is None else f" in {elapsed_text(took)}"
            self.message(f"Image {index} of {total} ({filename}): {JOB_WORDS.get(status, status)}{timing}")
        else:
            self._step_line = None
            self.status.setText(f"Image {index} of {total} ({filename}): starting")

    def run_time_text(self) -> str:
        """' in 4 min 12 s' for the running single image, or '' outside a run."""

        from cellquant.progress import elapsed_text

        return "" if self._clock is None else f" in {elapsed_text(self._clock.image_seconds())}"

    def _show_log(self, shown: bool) -> None:
        self.log.setVisible(shown)
        scroll = self.parentWidget().parentWidget() if self.parentWidget() is not None else None
        if isinstance(scroll, FooterScroll):
            scroll.fit_height()
        if shown:
            return
        # Give the image its height back: Qt keeps a docked window at its larger size otherwise.
        from qtpy.QtWidgets import QDockWidget, QMainWindow

        dock = self.parentWidget()
        while dock is not None and not isinstance(dock, QDockWidget):
            dock = dock.parentWidget()
        main = dock.parentWidget() if dock is not None else None
        if isinstance(main, QMainWindow) and not dock.isFloating():
            height = self.sizeHint().height() + dock.height() - self.height()  # plus the dock's own title bar
            dock.setMinimumHeight(0)  # napari fixed it at the height with the log open
            QTimer.singleShot(0, lambda: main.resizeDocks([dock], [height], Qt.Vertical))

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

    def __init__(self, dock, closable: bool = True):
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
            if key == "close" and not closable:
                continue
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


def floating_header(dock, closable: bool = True) -> FloatingHeader:
    """When a napari panel is popped out, give it a FloatingHeader in place of the plain system title bar."""

    header = FloatingHeader(dock, closable)
    header.hide()

    def apply() -> None:
        try:
            if not dock.isFloating():
                return
        except RuntimeError:
            return  # the panel was closed meanwhile
        from qtpy.QtWidgets import QApplication

        if QApplication.mouseButtons() != Qt.MouseButton.NoButton:
            QTimer.singleShot(200, apply)  # still being dragged; changing the header now would drop it
            return
        if dock.titleBarWidget() is header:
            own_window(dock)  # Qt may have made it a napari tool window again (after a move, for one)
            return
        # napari swaps its own header in when the panel is shown; keep it from undoing this one.
        dock.blockSignals(True)
        try:
            # napari turns a popped-out panel's header sideways, down the left edge; keep it across the top.
            vertical = QDockWidget.DockWidgetFeature.DockWidgetVerticalTitleBar
            if dock.features() & vertical:
                dock.setFeatures(dock.features() & ~vertical)
            dock.setTitleBarWidget(header)
            header.show()
            own_window(dock)
            dock.show()
        finally:
            dock.blockSignals(False)

    def docked() -> None:
        header.forget_size()
        # napari picks the header direction for where the panel docks (across the top on the left or right).
        main = dock.parentWidget()
        orient = getattr(dock, "_set_title_orientation", None)
        if orient is not None and hasattr(main, "dockWidgetArea"):
            orient(main.dockWidgetArea(dock))
        if dock.titleBarWidget() is header:
            dock.setTitleBarWidget(None)
            header.hide()
            # napari puts its own docked header back when the panel is shown.
            restore = getattr(dock, "_on_visibility_changed", None)
            if restore is not None:
                restore(True)

    dock.topLevelChanged.connect(lambda floating: QTimer.singleShot(0, apply) if floating else docked())
    dock.visibilityChanged.connect(lambda visible: QTimer.singleShot(0, apply) if visible else None)
    header._window_watcher = WindowWatcher(lambda: QTimer.singleShot(0, apply), dock)
    dock.installEventFilter(header._window_watcher)
    return header


def keep_open(dock) -> None:
    """Keep a napari panel from being hidden by a stray click: no hide button, and closing a popped-out window docks it back.

    napari's Window menu can still hide and show it.
    """

    def hide_button() -> None:
        try:
            button = getattr(dock.titleBarWidget(), "hide_button", None)
        except RuntimeError:
            return  # the panel was deleted meanwhile
        if button is not None and not button.isHidden():
            button.hide()

    dock._keep_open = PanelGuard(dock, lambda: QTimer.singleShot(0, hide_button))
    dock.installEventFilter(dock._keep_open)
    hide_button()


class PanelGuard(QObject):
    """Turn a system close of the panel it watches into docking it back, and report when napari swaps the panel's header."""

    def __init__(self, dock, on_header):
        super().__init__(dock)
        self._dock = dock
        self._on_header = on_header

    def eventFilter(self, watched, event) -> bool:
        from qtpy.QtCore import QEvent

        # Only a close from the system (Alt+F4, the taskbar); napari's Window menu still hides it.
        if event.type() == QEvent.Close and event.spontaneous():
            event.ignore()
            if self._dock.isFloating():
                QTimer.singleShot(0, lambda: self._dock.setFloating(False))
            return True
        if event.type() in (QEvent.ChildAdded, QEvent.ChildPolished, QEvent.Show):
            self._on_header()
        return False


def own_window(dock) -> None:
    """Make a popped-out panel a window of its own, so it stays up when the napari window is minimized.

    Qt makes a popped-out panel a tool window owned by the napari window, and Windows hides owned windows
    whenever their owner is minimized. As its own window the panel also gets its own taskbar button.
    """

    kind = Qt.WindowType.WindowType_Mask
    flags = dock.windowFlags()
    geometry = None
    if flags & kind != Qt.WindowType.Window:
        geometry = dock.geometry()
        dock.setWindowFlags((flags & ~kind) | Qt.WindowType.Window)  # this hides it and makes a new native window
    dock.winId()  # make sure the native window exists
    handle = dock.windowHandle()
    if handle is not None and handle.transientParent() is not None:
        handle.setTransientParent(None)
        if sys.platform == "win32" and dock.isVisible():
            # Qt passes a new owner to Windows only when it next shows the window; drop it now.
            import ctypes

            ctypes.windll.user32.SetWindowLongPtrW(ctypes.c_void_p(int(dock.winId())), -8, None)  # GWLP_HWNDPARENT
    if geometry is not None:
        dock.setGeometry(geometry)
        dock.show()


class WindowWatcher(QObject):
    """Call a function when the widget it watches gets a new native window or is shown."""

    def __init__(self, on_change, parent=None):
        super().__init__(parent)
        self._on_change = on_change

    def eventFilter(self, watched, event) -> bool:
        from qtpy.QtCore import QEvent

        if event.type() in (QEvent.Type.WinIdChange, QEvent.Type.Show):
            self._on_change()
        return False


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
