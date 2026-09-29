"""CellQuant desktop window.

Napari shows the image, object labels, and classification overlay. Every
analysis action goes through AnalysisController, which calls the same engine
as a batch run.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from qtpy.QtCore import Qt, QSize, QThread, QTimer, Signal
from qtpy.QtGui import QColor, QPainter
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
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from cellquant.controller import AnalysisController
from cellquant.errors import CellQuantError
from cellquant.gui import guide
from napari.utils.colormaps import DirectLabelColormap


def launch(experiment_dir: str | Path | None = None) -> None:
    import napari

    viewer = napari.Viewer(title="CellQuant")
    CellQuantWindow(viewer, experiment_dir)
    napari.run()


_OBJECT_LAYERS = ("Object fills", "Objects", "Classification", "Object IDs")

# How a classification compares a value with its threshold (shown text, recipe value).
COMPARISONS = (("above the threshold (>)", "above"), ("at least the threshold (≥)", "at_least"))


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
        note = QLabel(
            "The image folder is only read. Results (runs, measurements, and exports) "
            "are written in the results folder. Pick the same folder for both only if you "
            "want those results next to the images."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("For example: P7 OTX2 counts")
        self.images = QLineEdit()
        self.results = QLineEdit()
        form.addRow("Experiment name", self.name)
        form.addRow("Image folder", self._browse_row(self.images, self._pick_images))
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
        after = QLabel("You can leave out individual images in step 1 after they are listed.")
        after.setWordWrap(True)
        layout.addWidget(after)
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

    def _pick_images(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Folder that contains the images")
        if not folder:
            return
        self.images.setText(folder)
        if not self.results.text().strip():
            sibling = Path(folder).parent / f"{Path(folder).name} - CellQuant results"
            self.results.setText(str(sibling))

    def _pick_results(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Folder where results should be saved")
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
        images = self.images.text().strip()
        if images and not Path(images).is_dir():
            QMessageBox.warning(self, "Image folder", f"This image folder could not be found:\n{images}")
            return
        if images and results_inside_images(images, results):
            answer = QMessageBox.question(
                self,
                "Save results with the images?",
                "The results folder is the image folder, or inside it:\n"
                f"{results}\n\n"
                "CellQuant will add its own folders there (runs, working, exports) next to your images. "
                "Your image files are never changed.\n\nSave the results there?",
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
        self._tabs = QTabWidget()
        self._experiment_panel = ExperimentPanel(self)
        self._objects_panel = ObjectsPanel(self)
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
            guide.StepPage(self, 2, self._measurements_panel, extra=self._marker_setup, advanced=True),
            guide.StepPage(self, 3, self._review_panel),
            guide.StepPage(self, 4, self._results_panel, extra=self._results_summary, advanced=True),
        ]
        self._tabs.addTab(self._start_page, "Start")
        for page, step in zip(self._step_pages, guide.STEPS):
            self._tabs.addTab(page, step.tab)
        self._tabs.setMinimumWidth(460)
        self._footer = Footer(self)
        self.viewer.window.add_dock_widget(self._tabs, name="CellQuant", area="right")
        self.viewer.window.add_dock_widget(self._footer, name="Run", area="bottom")
        guide.apply_help(self)
        self._guide_timer = guide.start_refresh_timer(self)
        self._start_gpu_check()
        self.viewer.layers.events.inserted.connect(lambda _event: self._release_window_later())
        self.viewer.layers.events.removed.connect(lambda _event: self._release_window_later())
        self._release_window_later()
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
        states = guide.step_states(self)
        self._start_page.update_state(states)
        for page, state, step in zip(self._step_pages, states, guide.STEPS):
            page.update_state(state)
            index = self._tabs.indexOf(page)
            self._tabs.setTabText(index, f"{step.tab} ✓" if state.done else step.tab)

    def message(self, text: str) -> None:
        self._footer.message(text)

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
                "Practice experiment ready: 3 images with named channels. Expected answers are in "
                f"{folder / 'README.txt'}. Click Next to find the nuclei."
            )

        self._start_job(lambda: create_practice_experiment(folder), ready)

    def apply_marker_setup(
        self,
        chosen: list[tuple[int, str]],
        rule: str = guide.RULE_MEAN,
        min_percent: float = guide.DEFAULT_MIN_PERCENT,
    ) -> None:
        """Step 3 quick setup: define the markers, measure this image, and pick starting cutoffs.

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
            self.go_to_step(3)
            first = controller.recipe.classifications[0].id if controller.recipe.classifications else None
            panel = self._review_panel
            if first is not None and panel.display.findData(first) >= 0:
                panel.display.setCurrentIndex(panel.display.findData(first))
            if levels:
                self.message(
                    "Markers measured. Starting pixel levels were picked automatically and the minimum percent is yours: "
                    "check them in this step (drag the red line to change the percent; type a new pixel level and click Apply)."
                )
            else:
                self.message("Markers measured. Starting cutoffs were picked automatically: check them by dragging the red line.")

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

        self._start_job(lambda: controller.export(directory), done)

    def _start_gpu_check(self) -> None:
        """Load PyTorch in the background to learn whether a GPU is usable."""

        if not self._objects_panel.engine.installed:
            self._objects_panel.show_gpu_status({"available": False})
            return
        from cellquant.engines import gpu_status

        self._objects_panel.gpu_label.setText("Checking for a GPU...")
        self._gpu_check = CallWorker(gpu_status)
        self._gpu_check.succeeded.connect(self._objects_panel.show_gpu_status)
        self._gpu_check.failed.connect(lambda message: self._objects_panel.show_gpu_status({"available": False, "reason": message}))
        self._gpu_check.start()

    def open_experiment(self, directory: str | Path) -> None:
        try:
            self.controller = AnalysisController.open(directory)
        except Exception as exc:
            self._footer.message(str(exc))
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
            self.message("That results folder already holds an experiment, so it was opened instead.")
            self.open_experiment(output)
            return
        controller = AnalysisController.create(output, dialog.experiment_name(), input_directory=images or None)
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
        if controller is None or not self._nav_ids:
            self._clear_managed()
            return
        image_id = self._nav_ids[self._nav_index]
        controller.current_image_id = image_id
        record = controller.experiment.image(image_id)
        result = controller.recall(image_id) if controller else None
        self._footer.set_position(self._nav_index, len(self._nav_ids), record.relative_path or record.filename)
        self._experiment_panel.set_pixel_size(record.pixel_size_x, record.pixel_size_y, record.pixel_size_z)
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
        worker = CallWorker(lambda: controller._load_record(record))
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
            self._footer.set_units(f"{unit}   ·   Shown and analyzed: {loaded.z_description}")
            self._footer.message(f"Showing {record.relative_path or record.filename}")

        def failed(message: str) -> None:
            self._loader = None
            if self.controller is not controller:
                return
            self._footer.message(message)
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
        self._footer.set_units(f"{unit}   ·   Shown and analyzed: {described}" if described else unit)

    def run_current(self) -> None:
        controller = self.require_controller()
        if controller is None or not self._nav_ids:
            return
        self._panels_to_recipe()
        image_id = self._nav_ids[self._nav_index]
        self._start_job(lambda: controller.run_image(image_id, new_run=True), self._run_finished)

    def _run_finished(self, result) -> None:
        self.show_result(result)
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
        worker.failed.connect(self._footer.message)
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
            "Run", "Preview", "Run this image", "Run selected images", "Run all images", "Set up markers",
            "Export results…", "Add images", "Add folder", "New experiment", "New experiment…", "Open",
            "Open experiment…", "Try practice images", "Include shown", "Leave out shown",
            "Include only selected", "Delete object", "Restore object", "Undo", "Approve", "Use recommended",
            "HPC prep…",  # the HPC prep page manages its own buttons: a second job is refused while one runs
        }
    )

    def set_busy(self, busy: bool) -> None:
        """Grey out run buttons while an analysis runs; Cancel (and Pause for batches) become available."""

        if busy:
            buttons = [
                button
                for button in [*self._tabs.findChildren(QPushButton), *self._footer.findChildren(QPushButton)]
                if button.text() in self._BUSY_BUTTONS
            ]
            self._busy_restore = {button: button.isEnabled() for button in buttons}
            for button in buttons:
                button.setEnabled(False)
            self._footer.start_busy(batch=self._batch is not None)
        else:
            for button, enabled in getattr(self, "_busy_restore", {}).items():
                try:
                    button.setEnabled(enabled)
                except RuntimeError:
                    pass  # the button was rebuilt meanwhile
            self._busy_restore = {}
            self._footer.end_busy()

    def is_busy(self) -> bool:
        return self._job is not None or self._batch is not None

    def preview_current(self) -> None:
        controller = self.require_controller()
        if controller is None or not self._nav_ids:
            return
        self._panels_to_recipe()
        image_id = self._nav_ids[self._nav_index]
        crop = _visible_crop(self.viewer)

        def finish(labels) -> None:
            self._set_labels(labels, None)
            self._footer.message("Preview shows the current field of view. Run analyzes the full image.")

        self._start_job(lambda: controller.preview(image_id, crop), finish)

    def start_batch(self, image_ids: list[str] | None) -> None:
        controller = self.require_controller()
        if controller is None or self._batch is not None or self._job is not None:
            return
        self._panels_to_recipe()
        worker = BatchWorker(controller, image_ids)
        worker.progress.connect(self._footer.update_progress)
        worker.step.connect(self._footer.show_step)
        worker.finished_ok.connect(self._batch_finished)
        self._batch = worker
        self.set_busy(True)
        worker.start()

    def _batch_finished(self, report) -> None:
        planned = getattr(self._footer, "_batch_total", 0)
        self._batch = None
        self.set_busy(False)
        stopped = planned and len(report.jobs) < planned
        self._results_panel.show_queue(report)
        self._results_summary.show_batch(report)
        self.refresh_guidance()
        self._experiment_panel.refresh_table()
        if self.controller and self.controller.current_image_id in self.controller.last_results:
            self.show_result(self.controller.last_results[self.controller.current_image_id])
        self._footer.message(
            (f"Stopped after {len(report.jobs)} of {planned} images. " if stopped else "")
            + f"Completed {report.completed}, warnings {report.warnings}, failed {report.failed}."
        )

    def update_navigation(self) -> None:
        """Previous/Next image go through included images; keep the image on screen when possible."""

        if self.controller is None:
            return
        current = self._nav_ids[self._nav_index] if self._nav_ids else None
        ids = self.controller.included_ids() or [record.image_id for record in self.controller.experiment.images]
        self._nav_ids = ids
        if current in ids:
            self._nav_index = ids.index(current)
        else:
            self._nav_index = 0
            self.show_current()
        if self._nav_ids and current in ids:
            record = self.controller.experiment.image(current)
            self._footer.set_position(self._nav_index, len(ids), record.relative_path or record.filename)

    def _panels_to_recipe(self) -> None:
        if self.controller is None:
            return
        self._objects_panel.write_recipe()
        self._measurements_panel.write_recipe()
        self._results_panel.write_reports()

    def _refresh_all(self) -> None:
        if self.controller is None:
            return
        self._nav_ids = [record.image_id for record in self.controller.experiment.images if record.include]
        if not self._nav_ids:
            self._nav_ids = [record.image_id for record in self.controller.experiment.images]
        self._nav_index = 0
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
        names = [channel.display_name for channel in self.controller.experiment.channels]
        while len(names) < loaded.n_channels:
            names.append(f"Channel {len(names) + 1}")
        colormaps = channel_colormaps(loaded)
        added = self.viewer.add_image(
            loaded.data,
            channel_axis=0,
            name=names[: loaded.n_channels],
            colormap=colormaps,
            blending="additive",
        )
        layers = added if isinstance(added, list) else [added]
        for layer, channel in zip(layers, self.controller.experiment.channels, strict=False):
            limits = channel.display_settings.get("contrast_limits")
            if limits:
                layer.contrast_limits = tuple(limits)
            layer.events.contrast_limits.connect(
                lambda event, index=channel.channel_index: self._store_contrast(index, event)
            )
            self._managed.add(layer.name)
        labels = result.labels if result is not None else None
        self._set_labels(labels, result)
        self._release_window_later()

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
        if result is None:
            self._drop("Classification")
            self._drop("Object IDs")
        else:
            self._set_classification_overlay(result)
            self._set_ids(result)

    def _set_classification_overlay(self, result) -> None:
        choice = self._review_panel.classification_id()
        if not choice or choice not in result.objects.columns:
            self._drop("Classification")
            return
        frame = result.objects
        if "excluded" in frame.columns:
            frame = frame.loc[~frame["excluded"].astype(bool)]
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
        layer.colormap = DirectLabelColormap(
            color_dict={
                None: np.array([0, 0, 0, 0], dtype=np.float32),
                1: np.array([0.55, 0.55, 0.58, 1], dtype=np.float32),
                2: np.array([0.15, 0.75, 0.35, 1], dtype=np.float32),
            }
        )
        self._managed.add("Classification")

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
            ("New experiment", shell.new_experiment),
            ("Open", self._open),
            ("Save", self._save),
            ("Add images", self._add_images),
            ("Add folder", self._add_folder),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        types_row = QHBoxLayout()
        types_row.addWidget(QLabel("Add folder looks for:"))
        self.use_nd2 = QCheckBox("ND2 files")
        self.use_tiff = QCheckBox("TIFF files")
        for box in (self.use_nd2, self.use_tiff):
            box.setChecked(True)
            box.setToolTip("Which file types Add folder takes from a folder and its subfolders.")
            box.toggled.connect(self._types_changed)
            types_row.addWidget(box)
        types_row.addStretch(1)
        layout.addLayout(types_row)
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
        self.show_type.setToolTip("Show only one file type in the list. Combine with the text filter, then tick or untick what is shown.")
        self.show_type.currentIndexChanged.connect(lambda _index: self._filter(self.filter_box.text()))
        self.filter_box = QLineEdit()
        self.filter_box.setPlaceholderText("Filter by folder, file or sample name, e.g. Retina 2")
        self.filter_box.textChanged.connect(self._filter)
        filter_row.addWidget(self.show_type)
        filter_row.addWidget(self.filter_box, 1)
        layout.addLayout(filter_row)
        include_row = QHBoxLayout()
        self.include_shown = QPushButton("Include shown")
        self.exclude_shown = QPushButton("Leave out shown")
        self.include_selected = QPushButton("Include only selected")
        self.include_shown.setToolTip("Tick Include for every image in the list as filtered now.")
        self.exclude_shown.setToolTip("Untick Include for every image in the list as filtered now. Nothing is deleted.")
        self.include_selected.setToolTip("Include the rows you selected (Ctrl- or Shift-click) and leave out all others.")
        self.include_shown.clicked.connect(lambda: self._include_rows(self._shown_rows(), True))
        self.exclude_shown.clicked.connect(lambda: self._include_rows(self._shown_rows(), False))
        self.include_selected.clicked.connect(self._include_only_selected)
        for button in (self.include_shown, self.exclude_shown, self.include_selected):
            include_row.addWidget(button)
        layout.addLayout(include_row)
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
        self.big_list = QPushButton("Check the image list in a large window")
        self.big_list.setToolTip("Every image with its full folder path and details, in a window you can enlarge and copy from.")
        self.big_list.clicked.connect(self._show_big_list)
        layout.addWidget(self.big_list)
        meta_row = QHBoxLayout()
        self.meta_name = QLineEdit()
        self.meta_name.setPlaceholderText("Metadata column")
        add_meta = QPushButton("Add column")
        add_meta.clicked.connect(self._add_column)
        meta_row.addWidget(self.meta_name)
        meta_row.addWidget(add_meta)
        layout.addLayout(meta_row)
        self.channels = QWidget()
        self.channel_form = QFormLayout(self.channels)
        layout.addWidget(self.channels)
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
        calibration.addWidget(QLabel("X"))
        calibration.addWidget(self.pixel_x)
        calibration.addWidget(QLabel("Y"))
        calibration.addWidget(self.pixel_y)
        calibration.addWidget(QLabel("Z step"))
        calibration.addWidget(self.pixel_z)
        calibration.addWidget(apply_cal)
        layout.addLayout(calibration)

    def refresh(self) -> None:
        self.refresh_table()
        self._rebuild_channels()

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
            status = record.processing_status.replace("_", " ").capitalize()
            if record.last_message and record.last_result == "Failure":
                status = f"{status}: {record.last_message}"
            self.table.setItem(row, len(columns) - 1, _read_only(status))
        header = self.table.horizontalHeader()
        for column in range(self.table.columnCount()):
            if self.table.columnWidth(column) > 220:
                self.table.setColumnWidth(column, 220)
        header.setStretchLastSection(True)
        self.table.blockSignals(False)
        self._filter(self.filter_box.text())
        for box, kind in ((self.use_nd2, "nd2"), (self.use_tiff, "tiff")):
            box.blockSignals(True)
            box.setChecked(kind in controller.experiment.import_file_types)
            box.blockSignals(False)
        self._update_included_label()
        self.shell._release_window_later()

    def _show_big_list(self) -> None:
        from qtpy.QtWidgets import QApplication, QDialog

        dialog = QDialog(self)
        dialog.setWindowTitle("Images in this experiment")
        dialog.resize(1300, 650)
        box = QVBoxLayout(dialog)
        count = self.table.rowCount()
        included = sum(1 for row in range(count) if self.table.item(row, 0) and self.table.item(row, 0).checkState() == Qt.Checked)
        box.addWidget(QLabel(f"{count} images, {included} included. Hover over a path for the full location on disk."))
        table = QTableWidget(count, self.table.columnCount())
        headers = [self.table.horizontalHeaderItem(index).text() for index in range(self.table.columnCount())]
        table.setHorizontalHeaderLabels(headers)
        for row in range(count):
            for column in range(self.table.columnCount()):
                source = self.table.item(row, column)
                if source is None:
                    continue
                text = source.text() if column else ("yes" if source.checkState() == Qt.Checked else "no")
                item = _read_only(text)
                item.setToolTip(source.toolTip())
                table.setItem(row, column, item)
        table.resizeColumnsToContents()
        box.addWidget(table)
        copy = QPushButton("Copy the list (paste into Excel)")

        def copy_list() -> None:
            lines = ["\t".join(headers)]
            for row in range(count):
                lines.append("\t".join(table.item(row, column).text() if table.item(row, column) else "" for column in range(len(headers))))
            QApplication.clipboard().setText("\n".join(lines))
            copy.setText("Copied")

        copy.clicked.connect(copy_list)
        close = QPushButton("Close")
        close.clicked.connect(dialog.accept)
        row = QHBoxLayout()
        row.addWidget(copy)
        row.addStretch(1)
        row.addWidget(close)
        box.addLayout(row)
        self._big_list_dialog = dialog
        dialog.show()

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
        record.sample_name = controller.experiment.image(image_id).sample_name

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

    def _rebuild_channels(self) -> None:
        while self.channel_form.rowCount():
            self.channel_form.removeRow(0)
        controller = self.shell.controller
        if controller is None:
            return
        for channel in controller.experiment.channels:
            editor = QLineEdit(channel.channel_name)
            editor.editingFinished.connect(
                lambda index=channel.channel_index, box=editor: controller.set_channel_name(index, box.text())
            )
            editor.setToolTip("Name this channel after its stain, for example DAPI or OTX2.")
            self.channel_form.addRow(f"Channel {channel.channel_index + 1} name", editor)

    def _open(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Open experiment")
        if directory:
            self.shell.open_experiment(directory)

    def _save(self) -> None:
        controller = self.shell.require_controller()
        if controller:
            self.shell._panels_to_recipe()
            controller.save()
            self.shell._footer.message("Experiment saved.")

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
        directory = QFileDialog.getExistingDirectory(self, "Choose a folder of images (subfolders are included)", start)
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

    def _add_column(self) -> None:
        controller = self.shell.require_controller()
        if controller is None or not self.meta_name.text().strip():
            return
        controller.add_metadata_column(self.meta_name.text().strip())
        self.meta_name.clear()
        self.refresh_table()

    def _apply_pixel_size(self) -> None:
        controller = self.shell.controller
        if controller is None or not self.shell._nav_ids:
            return
        image_id = self.shell._nav_ids[self.shell._nav_index]
        x = self.pixel_x.value() or None
        y = self.pixel_y.value() or None
        z = self.pixel_z.value()
        controller.set_pixel_size(image_id, x, y, z)
        self.refresh_table()
        self.shell._objects_panel.update_recommendation()
        self.shell._footer.set_units("µm" if x and y else "pixels — no pixel size in this image")

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

    def _types_changed(self) -> None:
        controller = self.shell.controller
        if not self.use_nd2.isChecked() and not self.use_tiff.isChecked():
            # At least one type: turn the other one back on.
            sender = self.sender()
            other = self.use_tiff if sender is self.use_nd2 else self.use_nd2
            other.blockSignals(True)
            other.setChecked(True)
            other.blockSignals(False)
        if controller is not None:
            controller.experiment.import_file_types = self.file_types()

    def file_types(self) -> list[str]:
        return [kind for box, kind in ((self.use_nd2, "nd2"), (self.use_tiff, "tiff")) if box.isChecked()]

    def _shown_rows(self) -> list[int]:
        return [row for row in range(self.table.rowCount()) if not self.table.isRowHidden(row)]

    def _include_rows(self, rows: list[int], include: bool) -> None:
        controller = self.shell.controller
        if controller is None or not rows:
            return
        ids = [self.table.item(row, 0).data(Qt.UserRole) for row in rows if self.table.item(row, 0) is not None]
        controller.set_included_many(ids, include)
        self.refresh_table()
        self.shell.update_navigation()
        verb = "Included" if include else "Left out"
        self.shell.message(f"{verb} {len(ids)} image{'s' if len(ids) != 1 else ''}.")

    def selected_ids(self) -> list[str]:
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        return [self.table.item(row, 0).data(Qt.UserRole) for row in rows if self.table.item(row, 0) is not None]

    def _include_only_selected(self) -> None:
        controller = self.shell.controller
        chosen = set(self.selected_ids())
        if controller is None:
            return
        if not chosen:
            self.shell.message("Select rows in the list first (click, Ctrl-click or Shift-click).")
            return
        controller.set_included_many([record.image_id for record in controller.experiment.images if record.image_id not in chosen], False)
        controller.set_included_many(sorted(chosen), True)
        self.refresh_table()
        self.shell.update_navigation()
        self.shell.message(f"Included {len(chosen)} selected images; the others are left out.")

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
            + ". Run all images analyzes only included images."
        )


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
        if controller and paths:
            notices = controller.add_image_paths(paths)
            self.panel.shell._refresh_all()
            self.panel.set_notices(notices)

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
        self.object_name = QLineEdit("Objects")
        self.channel = QComboBox()
        self.method = QComboBox()
        self.method.addItems(["classical", "cellpose"])
        self.threshold_method = QComboBox()
        self.threshold_method.addItems(["otsu", "manual"])
        self.threshold = QDoubleSpinBox()
        self.threshold.setMaximum(1e9)
        self.sigma = QDoubleSpinBox()
        self.sigma.setMaximum(100)
        self.min_area = QDoubleSpinBox()
        self.max_area = QDoubleSpinBox()
        for box in (self.min_area, self.max_area):
            box.setMaximum(1e12)
        self.area_unit = QComboBox()
        self.area_unit.addItems(["px", "um2"])
        # Z-stacks: 2D (projection or one slice) or 3D (linked slices or the whole volume).
        from cellquant.hardware import Z_OPTION_LABELS

        self._gpu_status: dict = {}
        self._recommended: str | None = None
        self._suggest_gpu = False
        self.z_stack = QComboBox()
        for mode, text in Z_OPTION_LABELS.items():
            self.z_stack.addItem(text, mode)
        self.z_stack.setToolTip(
            "2D: max projection keeps the brightest value through all slices (nuclei at different depths "
            "can merge); one slice misses nuclei outside it.\n"
            "3D: link slices segments each slice and joins outlines that overlap in neighbouring slices; "
            "whole volume segments the stack at once (slow, needs closely spaced slices).\n"
            "Times are estimates for this computer."
        )
        self.z_slice = QSpinBox()
        self.z_slice.setRange(0, 1000)
        self.z_slice.setSpecialValueText("middle")
        self.z_slice.setToolTip("Which slice to analyze (1 = first). 'middle' uses the middle slice of each image.")
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
        self.z_scale = QComboBox()
        self.z_scale.addItem("Whole stack (recommended)", "stack")
        self.z_scale.addItem("Each slice separately", "slice")
        self.z_scale.setToolTip(
            "Scale brightness (and automatic thresholds) once for the whole stack, so a dim top or bottom "
            "slice stays dim and its outlines match the neighbouring slices. 'Each slice' is Cellpose's default."
        )
        self.z_min_slices = QSpinBox()
        self.z_min_slices.setRange(1, 100)
        self.z_min_slices.setToolTip("Remove objects found in fewer slices than this. 1 keeps everything; one-slice objects are flagged either way.")
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
        self.z_use.setToolTip("Choose the Z option (and GPU setting) suggested for this computer.")
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
        form.addRow("Object set name", self.object_name)
        form.addRow("Z-stacks", self.z_box)
        form.addRow(self.z_recommend_box)  # full width, so the explanation is readable
        self.method.currentIndexChanged.connect(lambda _index: self.update_recommendation())
        form.addRow("Source channel", self.channel)
        form.addRow("Method", self.method)
        form.addRow("Threshold", self.threshold_method)
        form.addRow("Manual threshold", self.threshold)
        form.addRow("Smoothing sigma", self.sigma)
        form.addRow("Minimum object size", self.min_area)
        form.addRow("Maximum object size", self.max_area)
        form.addRow("Size unit", self.area_unit)
        layout.addLayout(form)
        self.advanced_toggle = QCheckBox("Advanced")
        self.advanced_box = QGroupBox("Advanced")
        self.advanced_box.setVisible(False)
        self.advanced_toggle.toggled.connect(self.advanced_box.setVisible)
        advanced = QFormLayout(self.advanced_box)
        self.fill_holes = QCheckBox("Fill holes")
        self.fill_holes.setChecked(True)
        self.opening = QSpinBox()
        self.closing = QSpinBox()
        self.watershed = QCheckBox("Watershed")
        self.watershed_distance = QDoubleSpinBox()
        self.watershed_distance.setValue(5)
        self.compactness = QDoubleSpinBox()
        from cellquant.engines import cellpose_engine

        self.engine = cellpose_engine()
        self.engine_label = QLabel(self.engine.label)
        self.engine_label.setWordWrap(True)
        self.gpu_label = QLabel("")
        self.gpu_label.setWordWrap(True)
        # Editable so a path to a trained model can be typed in.
        self.cellpose_model = QComboBox()
        self.cellpose_model.setEditable(True)
        self.cellpose_model.addItems(list(self.engine.models))
        self.diameter = QDoubleSpinBox()
        self.diameter.setMaximum(10000)
        self.flow = QDoubleSpinBox()
        self.flow.setValue(0.4)
        self.cellprob = QDoubleSpinBox()
        self.cellprob.setRange(-6, 6)
        self.gpu = QCheckBox("Use GPU")
        self.gpu.toggled.connect(lambda _checked: self.update_recommendation())
        advanced.addRow(self.fill_holes)
        advanced.addRow("Opening radius (px)", self.opening)
        advanced.addRow("Closing radius (px)", self.closing)
        advanced.addRow(self.watershed)
        advanced.addRow("Watershed minimum distance", self.watershed_distance)
        advanced.addRow("Watershed compactness", self.compactness)
        advanced.addRow("Cellpose engine", self.engine_label)
        advanced.addRow("", self.gpu_label)
        advanced.addRow("Cellpose model", self.cellpose_model)
        advanced.addRow("Cellpose diameter (px)", self.diameter)
        advanced.addRow("Flow threshold", self.flow)
        advanced.addRow("Cell probability threshold", self.cellprob)
        advanced.addRow(self.gpu)
        layout.addWidget(self.advanced_toggle)
        layout.addWidget(self.advanced_box)
        actions = QHBoxLayout()
        preview = QPushButton("Preview")
        run = QPushButton("Run")
        preview.clicked.connect(shell.preview_current)
        run.clicked.connect(shell.run_current)
        actions.addWidget(preview)
        actions.addWidget(run)
        layout.addLayout(actions)
        layout.addStretch(1)

    def refresh(self) -> None:
        controller = self.shell.controller
        self.channel.clear()
        if controller is None:
            return
        for channel in controller.experiment.channels:
            self.channel.addItem(channel.channel_name, channel.channel_index)
        recipe = controller.recipe
        self.object_name.setText(recipe.object_set.name)
        index = self.method.findText(recipe.object_set.algorithm)
        if index >= 0:
            self.method.setCurrentIndex(index)
        channel_index = self.channel.findData(recipe.object_set.segmentation_channel)
        if channel_index >= 0:
            self.channel.setCurrentIndex(channel_index)
        self.z_stack.setCurrentIndex(max(0, self.z_stack.findData(recipe.z_stack)))
        self.z_slice.setValue(0 if recipe.z_index is None else recipe.z_index + 1)
        self.z_link.setValue(recipe.z_stitch_threshold)
        self.z_scale.setCurrentIndex(max(0, self.z_scale.findData(recipe.z_scale_brightness)))
        self.z_min_slices.setValue(recipe.z_min_slices)
        has_stacks = any(record.z_planes > 1 for record in controller.experiment.images)
        self.z_box.setEnabled(has_stacks)
        self.z_recommend_box.setEnabled(has_stacks)
        if not has_stacks:
            self.z_box.setToolTip("None of the images are Z-stacks.")
        self._z_mode_changed()
        parameters = recipe.object_set.parameters
        self.threshold_method.setCurrentText(str(parameters.get("threshold_method", "otsu")))
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
        if parameters.get("diameter_px"):
            self.diameter.setValue(float(parameters["diameter_px"]))
        self.flow.setValue(float(parameters.get("flow_threshold", 0.4)))
        self.cellprob.setValue(float(parameters.get("cellprob_threshold", 0)))
        self.gpu.setChecked(bool(parameters.get("gpu", False)))
        if parameters.get("min_area_um2") is not None:
            self.area_unit.setCurrentText("um2")
            self.min_area.setValue(float(parameters["min_area_um2"]))
        else:
            self.min_area.setValue(float(parameters.get("min_area_px") or 0))
        if parameters.get("max_area_um2") is not None:
            self.max_area.setValue(float(parameters["max_area_um2"]))
        else:
            self.max_area.setValue(float(parameters.get("max_area_px") or 0))
        self.update_recommendation()

    def _z_mode_changed(self) -> None:
        mode = self.z_stack.currentData()
        self.z_slice.setVisible(mode == "single_plane")
        self.z_slice_label.setVisible(mode == "single_plane")
        self.z_slice.setEnabled(mode == "single_plane")
        self.z3d_box.setVisible(mode in ("stitch_slices", "full_3d"))
        self.z_link.setEnabled(mode == "stitch_slices")
        self.z_scale.setEnabled(mode == "stitch_slices")  # whole-volume 3D always scales the whole stack
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
            method=self.method.currentText(),
            engine=self.engine.key if self.engine.installed else None,
            use_gpu=self.gpu.isChecked(),
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
        self.z_recommend.setText(f"<b>Recommended here:</b> {Z_OPTION_LABELS[suggestion.mode]}. {suggestion.reason}")
        self._suggest_gpu = suggestion.use_gpu
        self.z_recommend_box.setVisible(True)
        self._show_recommendation_state()

    def _show_recommendation_state(self) -> None:
        chosen = self.z_stack.currentData() == self._recommended and not getattr(self, "_suggest_gpu", False)
        self.z_use.setEnabled(self._recommended is not None and not chosen)
        self.z_use.setText("In use" if chosen else "Use recommended")

    def use_recommendation(self) -> None:
        if self._recommended is None:
            return
        self.z_stack.setCurrentIndex(max(0, self.z_stack.findData(self._recommended)))
        if getattr(self, "_suggest_gpu", False) and self.gpu.isEnabled():
            self.gpu.setChecked(True)
        self.update_recommendation()
        self.shell.message(f"Z-stacks: using {self.z_stack.currentText().split('  (')[0]}.")

    def show_gpu_status(self, status: dict) -> None:
        """Called once PyTorch has been checked in the background."""

        self._gpu_status = dict(status)
        if not self.engine.installed:
            self.gpu_label.setText("")
            self.gpu.setEnabled(False)
            self.update_recommendation()
            return
        if status.get("available"):
            memory = f" ({status['memory_gb']:.0f} GB)" if status.get("memory_gb") else ""
            self.gpu_label.setText(f"GPU: {status.get('name') or 'NVIDIA GPU'}{memory}")
            self.gpu.setEnabled(True)
        else:
            self.gpu_label.setText(f"No GPU: Cellpose will run on the CPU. {status.get('reason', '')}".strip())
            self.gpu.setChecked(False)
            self.gpu.setEnabled(False)
        self.update_recommendation()

    def write_recipe(self) -> None:
        controller = self.shell.controller
        if controller is None:
            return
        data = controller.recipe.model_dump(mode="json")
        parameters = {
            "sigma": self.sigma.value(),
            "threshold_method": self.threshold_method.currentText(),
            "fill_holes": self.fill_holes.isChecked(),
            "opening_radius_px": self.opening.value(),
            "closing_radius_px": self.closing.value(),
            "use_watershed": self.watershed.isChecked(),
            "watershed_min_distance_px": self.watershed_distance.value(),
            "watershed_compactness": self.compactness.value(),
        }
        if self.threshold_method.currentText() == "manual":
            parameters["threshold"] = self.threshold.value()
        if self.min_area.value() > 0:
            key = "min_area_um2" if self.area_unit.currentText() == "um2" else "min_area_px"
            parameters[key] = self.min_area.value()
        if self.max_area.value() > 0:
            key = "max_area_um2" if self.area_unit.currentText() == "um2" else "max_area_px"
            parameters[key] = self.max_area.value()
        if self.method.currentText() == "cellpose":
            size_filters = {
                key: value
                for key, value in parameters.items()
                if key in {"min_area_px", "max_area_px", "min_area_um2", "max_area_um2"}
            }
            previous = data["object_set"].get("parameters") or {}
            local = self.engine.installed and self.engine.key is not None
            parameters = {
                # The engine is saved so these settings are not run under the other Cellpose.
                # Without Cellpose here (for example on a computer that only prepares cluster packages),
                # the engine and model already in the settings are kept.
                "engine": self.engine.key if local else previous.get("engine"),
                "model": (self.cellpose_model.currentText() or self.engine.default_model) if local else previous.get("model"),
                "diameter_px": self.diameter.value() or None,
                "flow_threshold": self.flow.value(),
                "cellprob_threshold": self.cellprob.value(),
                "gpu": self.gpu.isChecked(),
                **size_filters,
            }
        data["z_stack"] = self.z_stack.currentData() or "max_projection"
        data["z_index"] = None if self.z_stack.currentData() != "single_plane" or self.z_slice.value() == 0 else self.z_slice.value() - 1
        data["z_stitch_threshold"] = round(self.z_link.value(), 3)
        data["z_scale_brightness"] = self.z_scale.currentData() or "stack"
        data["z_min_slices"] = self.z_min_slices.value()
        data["object_set"] = {
            "name": self.object_name.text() or "Objects",
            "segmentation_channel": int(self.channel.currentData() or 0),
            "algorithm": self.method.currentText(),
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
        layout.addWidget(QLabel("Measurements"))
        layout.addWidget(self.table)
        self.form = self._measurement_form()
        layout.addLayout(self.form)
        add = QPushButton("Add measurement")
        add.clicked.connect(self._add)
        remove = QPushButton("Remove measurement")
        remove.clicked.connect(self._remove)
        layout.addWidget(add)
        layout.addWidget(remove)
        layout.addWidget(QLabel("Classifications"))
        self.classes = QTableWidget(0, 4)
        self.classes.setHorizontalHeaderLabels(["Name", "Measurement", "Threshold", "Positive when"])
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
            "above: positive when the value is greater than the threshold (equal is negative).\n"
            "at least: positive when the value is greater than or equal to the threshold.\n"
            "For a percent-of-pixels measurement, 'at least' reads as 'at least N% of the pixels'."
        )
        row = QFormLayout()
        row.addRow("Name", self.class_name)
        row.addRow("Measurement", self.class_measurement)
        row.addRow("Threshold", self.class_threshold)
        row.addRow("Positive when value is", self.class_comparison)
        layout.addLayout(row)
        add_class = QPushButton("Add classification")
        add_class.clicked.connect(self._add_class)
        layout.addWidget(add_class)

    def _measurement_form(self) -> QFormLayout:
        self.meas_channel = QComboBox()
        self.region = QComboBox()
        self.region.addItems(["object", "eroded_object", "expanded_object", "ring"])
        self.distance = QDoubleSpinBox()
        self.distance.setMaximum(10000)
        self.inner = QDoubleSpinBox()
        self.outer = QDoubleSpinBox()
        self.outer.setValue(2)
        self.statistic = QComboBox()
        self.statistic.addItems(
            ["mean", "median", "min", "max", "std", "integrated", "area", "equivalent_diameter", "centroid_x", "centroid_y", "percent_above"]
        )
        self.statistic.setToolTip(
            "percent_above: the percent (0-100) of the region's pixels at or above the pixel level\n"
            "(after background correction, if any). Classify it with 'at least' and a minimum percent."
        )
        self.statistic.currentIndexChanged.connect(lambda _index: self._statistic_changed())
        self.pixel_level = QDoubleSpinBox()
        self.pixel_level.setRange(-1e12, 1e12)
        self.pixel_level.setDecimals(3)
        self.pixel_level.setToolTip("A pixel counts when its value is at or above this level (same units as the image).")
        self.use_high = QCheckBox("Also require at most")
        self.pixel_level_high = QDoubleSpinBox()
        self.pixel_level_high.setRange(-1e12, 1e12)
        self.pixel_level_high.setDecimals(3)
        self.pixel_level_high.setToolTip("Optional: pixels brighter than this do not count (for example saturated spots).")
        self.use_high.toggled.connect(lambda _on: self._statistic_changed())
        high_row = QHBoxLayout()
        high_row.addWidget(self.use_high)
        high_row.addWidget(self.pixel_level_high)
        self.background = QComboBox()
        self.background.addItems(["none", "global", "local_ring"])
        form = QFormLayout()
        form.addRow("Channel", self.meas_channel)
        form.addRow("Region", self.region)
        form.addRow("Distance (px)", self.distance)
        form.addRow("Ring inner (px)", self.inner)
        form.addRow("Ring outer (px)", self.outer)
        form.addRow("Statistic", self.statistic)
        form.addRow("Pixel level (percent_above)", self.pixel_level)
        form.addRow("Upper pixel level (optional)", high_row)
        form.addRow("Background", self.background)
        self._statistic_changed()
        return form

    def _statistic_changed(self) -> None:
        percent = self.statistic.currentText() == "percent_above"
        self.pixel_level.setEnabled(percent)
        self.use_high.setEnabled(percent)
        self.pixel_level_high.setEnabled(percent and self.use_high.isChecked())

    def _class_measurement_changed(self) -> None:
        controller = self.shell.controller
        if controller is None:
            return
        spec = next((m for m in controller.recipe.measurements if m.id == self.class_measurement.currentText()), None)
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
            self.meas_channel.addItem(channel.channel_name, channel.channel_index)
        self.table.setRowCount(len(controller.recipe.measurements))
        for row, measurement in enumerate(controller.recipe.measurements):
            self.table.setItem(row, 0, QTableWidgetItem(measurement.id))
            self.table.setItem(row, 1, QTableWidgetItem(str(measurement.channel)))
            self.table.setItem(row, 2, QTableWidgetItem(measurement.region.type))
            self.table.setItem(row, 3, QTableWidgetItem(measurement.statistic))
            self.table.setItem(row, 4, QTableWidgetItem(measurement.background.type))
            level = ""
            if measurement.pixel_level is not None:
                level = f"≥ {measurement.pixel_level:g}"
                if measurement.pixel_level_high is not None:
                    level += f" and ≤ {measurement.pixel_level_high:g}"
            self.table.setItem(row, 5, QTableWidgetItem(level))
            self.class_measurement.addItem(measurement.id)
        self.class_measurement.blockSignals(False)
        self.classes.setRowCount(len(controller.recipe.classifications))
        for row, classification in enumerate(controller.recipe.classifications):
            self.classes.setItem(row, 0, QTableWidgetItem(classification.name))
            self.classes.setItem(row, 1, QTableWidgetItem(classification.measurement))
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
            measurement = self.classes.item(row, 1).text()
            threshold = float(self.classes.item(row, 2).text())
            previous = next((item for item in data["classifications"] if item["name"] == name), None)
            entry = dict(previous) if previous else {"id": f"class_{row + 1}"}
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
        region_type = self.region.currentText()
        region: dict = {"type": region_type}
        if region_type in {"eroded_object", "expanded_object"}:
            region["distance_px"] = self.distance.value()
        elif region_type == "ring":
            region["inner_px"] = self.inner.value()
            region["outer_px"] = self.outer.value()
        background: dict = {"type": self.background.currentText()}
        if background["type"] == "local_ring":
            background["inner_px"] = self.inner.value()
            background["outer_px"] = self.outer.value()
        measurement_id = f"measurement_{len(data['measurements']) + 1}"
        entry = {
            "id": measurement_id,
            "channel": int(self.meas_channel.currentData() or 0),
            "region": region,
            "statistic": self.statistic.currentText(),
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
            self.shell.message(str(exc))
            return
        self.refresh()

    def _remove(self) -> None:
        controller = self.shell.controller
        if controller is None or self.table.currentRow() < 0:
            return
        self.write_recipe()
        data = controller.recipe.model_dump(mode="json")
        del data["measurements"][self.table.currentRow()]
        controller.set_recipe(data)
        self.refresh()

    def _add_class(self) -> None:
        if not self.class_name.text().strip():
            return
        row = self.classes.rowCount()
        self.classes.insertRow(row)
        self.classes.setItem(row, 0, QTableWidgetItem(self.class_name.text().strip()))
        self.classes.setItem(row, 1, QTableWidgetItem(self.class_measurement.currentText()))
        self.classes.setItem(row, 2, QTableWidgetItem(str(self.class_threshold.value())))
        self.classes.setCellWidget(row, 3, self._comparison_box(str(self.class_comparison.currentData())))
        self.class_name.clear()


class ReviewPanel(QWidget):
    def __init__(self, shell: CellQuantWindow):
        super().__init__()
        self.shell = shell
        self._labels = None
        self._fills = None
        layout = QVBoxLayout(self)
        self.show_boundaries = QCheckBox("Show object boundaries")
        self.show_fills = QCheckBox("Show object fills")
        self.show_ids = QCheckBox("Show object IDs")
        self.show_boundaries.setChecked(True)
        self.show_boundaries.toggled.connect(self._toggle_layers)
        self.show_fills.toggled.connect(self._toggle_layers)
        self.show_ids.toggled.connect(self._toggle_layers)
        layout.addWidget(self.show_boundaries)
        layout.addWidget(self.show_fills)
        layout.addWidget(self.show_ids)
        self.display = QComboBox()
        self.display.currentIndexChanged.connect(lambda _index: self._recolor())
        layout.addWidget(QLabel("Display objects by"))
        layout.addWidget(self.display)
        self.histogram = HistogramWidget()
        self.histogram.setMaximumHeight(100)
        self.histogram.threshold_changed.connect(self._threshold_moved)
        layout.addWidget(self.histogram)
        layout.addWidget(self._pixel_level_box())
        self.counts = QLabel("Positive: 0\nNegative: 0\nPositive: —")
        layout.addWidget(self.counts)
        self.selected = QLabel("Selected object: none")
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
        layout.addLayout(buttons)
        status_buttons = QHBoxLayout()
        for text, status in (("Mark reviewed", "reviewed"), ("Approve", "approved"), ("Exclude image", "excluded")):
            button = QPushButton(text)
            button.clicked.connect(lambda _checked=False, value=status: self._set_status(value))
            status_buttons.addWidget(button)
        layout.addLayout(status_buttons)
        self.qc = QLabel("")
        self.qc.setWordWrap(True)
        layout.addWidget(self.qc)
        layout.addStretch(1)

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
        form.addRow("Minimum percent of the cell", self.min_percent)
        form.addRow("Pixel level (at or above)", self.pixel_level)
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
            self.shell.message(str(exc))
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
        self._show_histogram(result)
        area = "—" if result.qc.median_area is None else f"{result.qc.median_area:.2f}"
        border = result.qc.fraction_touching_border
        border_text = "—" if border != border else f"{100 * border:.1f}%"
        area_unit = "µm²" if result.spatial_unit == "um" else "px²"
        self.qc.setText(
            f"Objects: {result.qc.n_objects}\n"
            f"Median area: {area} {area_unit}\n"
            f"Touching border: {border_text}\n"
            f"Status: {result.qc.status}"
        )

    def classification_id(self) -> str:
        return str(self.display.currentData() or "")

    def bind_labels(self, boundaries, fills) -> None:
        self._labels = boundaries
        self._fills = fills
        boundaries.events.selected_label.connect(self._on_selected)
        self._toggle_layers()

    def _show_histogram(self, result) -> None:
        self._show_level_box()
        classification_id = self.classification_id()
        if not classification_id:
            return
        classification = next(item for item in self.shell.controller.recipe.classifications if item.id == classification_id)
        if classification.measurement not in result.objects.columns:
            return
        values = result.objects[classification.measurement].to_numpy(dtype=float)
        percent_rule = self._current_percent_measurement() is not None
        self.histogram.set_values(
            values,
            classification.threshold,
            value_range=(0.0, 100.0) if percent_rule else None,
            step=1.0 if percent_rule else None,
        )
        counts = self.shell.controller.histogram(
            result.provenance["image_id"], classification.measurement, classification.threshold, classification.comparison
        )
        percent = counts["percent_positive"]
        percent_text = "—" if percent != percent else f"{percent:.1f}%"
        rule = guide.describe_rule(self.shell.controller.recipe, classification)
        self.counts.setText(
            f"Positive when: {rule}\n"
            f"Positive: {counts['positive']}\nNegative: {counts['negative']}\nUnmeasured: {int(counts['n_missing'])}\nPositive: {percent_text}"
        )

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

    def _toggle_layers(self) -> None:
        viewer = self.shell.viewer
        if "Objects" in viewer.layers:
            viewer.layers["Objects"].visible = self.show_boundaries.isChecked()
        if "Object fills" in viewer.layers:
            viewer.layers["Object fills"].visible = self.show_fills.isChecked()
        if "Object IDs" in viewer.layers:
            viewer.layers["Object IDs"].visible = self.show_ids.isChecked()

    def _on_selected(self, event) -> None:
        value = int(event.value) if hasattr(event, "value") else int(self._labels.selected_label)
        self.selected.setText("Selected object: none" if value <= 0 else f"Selected object: {value}")

    def _selected_id(self) -> int | None:
        if self._labels is None:
            return None
        value = int(self._labels.selected_label)
        return value if value > 0 else None

    def _delete(self) -> None:
        self._edit("delete")

    def _restore(self) -> None:
        self._edit("restore")

    def _edit(self, kind: str) -> None:
        controller = self.shell.require_controller()
        object_id = self._selected_id()
        if controller is None or object_id is None or not self.shell._nav_ids:
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

    def _set_status(self, status: str) -> None:
        controller = self.shell.require_controller()
        if controller is None or not self.shell._nav_ids:
            return
        image_id = self.shell._nav_ids[self.shell._nav_index]
        controller.set_status(image_id, status)
        if status == "excluded":
            controller.set_included(image_id, False)
        self.shell._experiment_panel.refresh_table()
        words = {
            "approved": "Approved. Use Next image ▶ at the bottom to check the next image, or go on to step 5.",
            "reviewed": "Marked as reviewed.",
            "excluded": "This image is now left out of the results.",
        }
        self.shell.message(words.get(status, ""))
        self.shell.refresh_guidance()


class ResultsPanel(QWidget):
    def __init__(self, shell: CellQuantWindow):
        super().__init__()
        self.shell = shell
        layout = QVBoxLayout(self)
        self.reports = QTableWidget(0, 2)
        self.reports.setHorizontalHeaderLabels(["Numerator", "Denominator"])
        layout.addWidget(self.reports)
        add = QPushButton("Add result row")
        add.clicked.connect(self._add_report)
        layout.addWidget(add)
        self.phenotypes = QTableWidget(0, 3)
        self.phenotypes.setHorizontalHeaderLabels(["Phenotype", "Count", "Percent"])
        layout.addWidget(self.phenotypes)
        self.queue = QLabel("")
        self.queue.setWordWrap(True)
        layout.addWidget(self.queue)
        queue_buttons = QHBoxLayout()
        for text, mode in (("Review flagged", "flagged"), ("Review all", "all"), ("Review failed", "failed")):
            button = QPushButton(text)
            button.clicked.connect(lambda _checked=False, value=mode: self._filter_nav(value))
            queue_buttons.addWidget(button)
        layout.addLayout(queue_buttons)
        actions = QHBoxLayout()
        for text, slot in (
            ("Run current", shell.run_current),
            ("Run selected", self._run_selected),
            ("Run experiment", self._run_all),
            ("Export", self._export),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            actions.addWidget(button)
        layout.addLayout(actions)
        recipes = QHBoxLayout()
        for text, slot in (
            ("Save recipe", self._save_recipe),
            ("Load recipe", self._load_recipe),
            ("Duplicate", self._duplicate),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            recipes.addWidget(button)
        layout.addLayout(recipes)

    def refresh(self) -> None:
        controller = self.shell.controller
        if controller is None:
            return
        self.reports.setRowCount(len(controller.recipe.reports))
        for row, report in enumerate(controller.recipe.reports):
            self.reports.setItem(row, 0, QTableWidgetItem(report.numerator))
            self.reports.setItem(row, 1, QTableWidgetItem(report.denominator))

    def write_reports(self) -> None:
        controller = self.shell.controller
        if controller is None:
            return
        data = controller.recipe.model_dump(mode="json")
        reports = []
        for row in range(self.reports.rowCount()):
            numerator = self.reports.item(row, 0)
            denominator = self.reports.item(row, 1)
            if numerator and denominator and numerator.text().strip():
                reports.append({"numerator": numerator.text().strip(), "denominator": denominator.text().strip() or "all_objects"})
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
        self.queue.setText(
            f"{len(report.jobs)} images processed\n"
            f"Passed QC: {report.completed}\n"
            f"Review recommended: {report.warnings}\n"
            f"Failed: {report.failed}"
        )

    def _add_report(self) -> None:
        row = self.reports.rowCount()
        self.reports.insertRow(row)
        self.reports.setItem(row, 0, QTableWidgetItem("A"))
        self.reports.setItem(row, 1, QTableWidgetItem("all_objects"))

    def _run_selected(self) -> None:
        ids = self.shell._experiment_panel.selected_ids()
        if not ids:
            self.shell.message("Select images in step 1's list first (click, Ctrl-click or Shift-click), or use Run all images.")
            return
        self.shell.start_batch(ids)

    def _run_all(self) -> None:
        self.shell.start_batch(None)

    def _export(self) -> None:
        self.shell.export_dialog()

    def _save_recipe(self) -> None:
        controller = self.shell.require_controller()
        if controller:
            self.shell._panels_to_recipe()
            controller.save()
            self.shell._footer.message("Recipe saved.")

    def _load_recipe(self) -> None:
        controller = self.shell.require_controller()
        if controller is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Recipe", "", "Recipe (*.yaml *.yml *.json)")
        if path:
            controller.import_recipe(path)
            self.shell._refresh_all()

    def _duplicate(self) -> None:
        controller = self.shell.require_controller()
        if controller is None:
            return
        name, accepted = QInputDialog.getText(self, "Duplicate recipe", "Recipe name")
        if accepted:
            controller.duplicate_recipe(name)
            self.shell._footer.message("Recipe duplicated.")

    def _filter_nav(self, mode: str) -> None:
        controller = self.shell.controller
        if controller is None:
            return
        if mode == "all":
            ids = [record.image_id for record in controller.experiment.images]
        elif mode == "failed":
            ids = [record.image_id for record in controller.experiment.images if record.last_result == "Failure"]
        else:
            ids = [
                record.image_id
                for record in controller.experiment.images
                if record.processing_status == "needs_attention"
            ]
        self.shell._nav_ids = ids or self.shell._nav_ids
        self.shell._nav_index = 0
        self.shell.show_current()


class Footer(QWidget):
    def __init__(self, shell: CellQuantWindow):
        super().__init__()
        self.shell = shell
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        previous = QPushButton("◀ Previous image")
        next_image = QPushButton("Next image ▶")
        run_current = QPushButton("Run this image")
        run_selected = QPushButton("Run selected images")
        run_all = QPushButton("Run all images")
        previous.setToolTip("Show the previous image in the experiment.")
        next_image.setToolTip("Show the next image in the experiment.")
        run_current.setToolTip("Find objects and measure markers in the image on screen.")
        run_selected.setToolTip("Run the images selected in step 1's table.")
        run_all.setToolTip("Run every included image with the current settings.")
        previous.clicked.connect(lambda: self._step(-1))
        next_image.clicked.connect(lambda: self._step(1))
        run_current.clicked.connect(shell.run_current)
        run_selected.clicked.connect(shell._results_panel._run_selected)
        run_all.clicked.connect(shell._results_panel._run_all)
        self.pause = QPushButton("Pause")
        self.cancel = QPushButton("Cancel")
        self.pause.clicked.connect(self._pause)
        self.cancel.clicked.connect(self._cancel)
        for button in (previous, run_current, run_selected, run_all, next_image, self.pause, self.cancel):
            row.addWidget(button)
        self.pause.setEnabled(False)
        self.cancel.setEnabled(False)
        self.cancel.setToolTip("Stop the running analysis after the current step. Images already finished are kept.")
        self.pause.setToolTip("Pause a batch between steps; click again to resume.")
        layout.addLayout(row)
        self.position = QLabel("No image yet. Start on the Start tab.")
        self.units = QLabel("Units: pixels")
        self.progress = QProgressBar()
        self.status = QLabel("")
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(80)
        layout.addWidget(self.position)
        layout.addWidget(self.units)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        layout.addWidget(self.log)

    def set_position(self, index: int, total: int, filename: str) -> None:
        self.position.setText(f"Image {index + 1} / {total}  {filename}")

    def set_units(self, unit: str) -> None:
        self.units.setText(f"Units: {unit}")

    def message(self, text: str) -> None:
        self.status.setText(text)
        self.log.append(text)

    def start_busy(self, batch: bool) -> None:
        self._batch_index = 0
        self._batch_total = 0
        self._batch_name = ""
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        self.cancel.setEnabled(True)
        self.pause.setEnabled(batch)
        self.pause.setText("Pause")

    def end_busy(self) -> None:
        self.progress.setRange(0, 1000)
        self.progress.setValue(1000 if self.progress.value() > 0 else 0)
        self.cancel.setEnabled(False)
        self.pause.setEnabled(False)
        self.pause.setText("Pause")
        self._batch_total = 0

    def show_step(self, text: str, fraction: float) -> None:
        """One step of the running analysis, e.g. 'Finding objects: slice 3 of 7'."""

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
        if finished:
            self.message(f"Image {index} of {total}  {filename}: {status}")
        else:
            self.status.setText(f"Image {index} of {total} ({filename}): starting")

    def _step(self, delta: int) -> None:
        if not self.shell._nav_ids:
            return
        self.shell._nav_index = (self.shell._nav_index + delta) % len(self.shell._nav_ids)
        self.shell.show_current()

    def _pause(self) -> None:
        worker = self.shell._batch
        if worker is None:
            return
        worker.paused = not worker.paused
        self.pause.setText("Resume" if worker.paused else "Pause")

    def _cancel(self) -> None:
        worker = self.shell._batch or self.shell._job
        if worker is None:
            return
        worker.cancelled = True
        if self.shell._batch is not None:
            self.shell._batch.paused = False
        self.cancel.setEnabled(False)
        self.status.setText("Stopping after the current step...")


class HistogramWidget(QWidget):
    threshold_changed = Signal(float)

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(80)
        self._counts = np.zeros(1)
        self._edges = np.array([0.0, 1.0])
        self._threshold = 0.0
        self._step: float | None = None

    def set_values(
        self,
        values: np.ndarray,
        threshold: float,
        value_range: tuple[float, float] | None = None,
        step: float | None = None,
    ) -> None:
        """Show values; ``value_range`` fixes the axis (0-100 for percents); drags snap to ``step``."""

        finite = np.asarray(values, dtype=float)
        finite = finite[np.isfinite(finite)]
        self._step = step
        if value_range is not None:
            self._counts, self._edges = np.histogram(finite, bins=40, range=value_range)
        elif finite.size == 0:
            self._counts = np.zeros(1)
            self._edges = np.array([0.0, 1.0])
        else:
            self._counts, self._edges = np.histogram(finite, bins=40)
        self._threshold = float(threshold)
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(250, 250, 250))
        width = max(self.width() - 8, 1)
        height = max(self.height() - 8, 1)
        peak = max(float(self._counts.max()), 1)
        bin_width = width / max(len(self._counts), 1)
        for index, count in enumerate(self._counts):
            bar = int(height * (count / peak))
            painter.fillRect(int(4 + index * bin_width), 4 + height - bar, max(int(bin_width) - 1, 1), bar, QColor(70, 110, 160))
        span = float(self._edges[-1] - self._edges[0]) or 1
        x = int(4 + width * ((self._threshold - self._edges[0]) / span))
        painter.setPen(QColor(180, 40, 40))
        painter.drawLine(x, 4, x, 4 + height)

    def mousePressEvent(self, event) -> None:
        self._move_threshold(event.position().x() if hasattr(event, "position") else event.x())

    def mouseMoveEvent(self, event) -> None:
        self._move_threshold(event.position().x() if hasattr(event, "position") else event.x())

    def _move_threshold(self, x: float) -> None:
        width = max(self.width() - 8, 1)
        fraction = min(max((float(x) - 4) / width, 0), 1)
        value = float(self._edges[0] + fraction * (self._edges[-1] - self._edges[0]))
        if self._step:
            value = float(round(value / self._step) * self._step)
        self._threshold = value
        self.update()
        self.threshold_changed.emit(value)


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
        except Exception as exc:
            self.failed.emit(str(exc.args[0] if exc.args else exc))


class BatchWorker(QThread):
    progress = Signal(int, int, str, str)
    step = Signal(str, float)
    finished_ok = Signal(object)

    def __init__(self, controller: AnalysisController, image_ids: list[str] | None):
        super().__init__()
        self.controller = controller
        self.image_ids = image_ids
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

        with progress.reporting(
            lambda text, fraction: self.step.emit(text, -1.0 if fraction is None else float(fraction)),
            stop_requested,
        ):
            report = self.controller.run_images(
                self.image_ids,
                on_progress=on_progress,
                should_continue=should_continue,
            )
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
