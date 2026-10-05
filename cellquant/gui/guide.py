"""Guidance for first-time users: the Start tab, numbered steps, hover help, quick marker setup.

The analysis panels in app.py stay as they are. Each is shown inside a step
page with a short "what to do here" box, a success check, and Back / Next
buttons. Next is disabled with a plain reason when the step is not finished.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from qtpy.QtCore import Qt, QTimer, QUrl
from qtpy.QtGui import QDesktopServices
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from cellquant.quicksetup import (  # noqa: F401 - used by app.py
    DEFAULT_MIN_PERCENT,
    RULE_MEAN,
    RULE_PERCENT,
    describe_rule,
    marker_recipe,
    starting_threshold,
    uses_pixel_level,
)

# ---------------------------------------------------------------------------
# Words shown to the user


@dataclass(frozen=True)
class Step:
    number: int
    tab: str
    title: str
    todo: str
    success: str


STEPS = (
    Step(
        1,
        "1 Images",
        "Add your images",
        "Click <b>New experiment</b> and choose two folders: the <b>image folder</b> (only read, never changed) "
        "and the <b>results folder</b> (where CellQuant saves its work; by default a new folder beside your images). "
        "That results folder is reloadable with <b>Open</b> — changes are saved automatically (no separate Save).<br>"
        "Every ND2 and TIFF in the image folder and its subfolders is listed. "
        "<b>Advanced</b> holds <b>Add folder</b>, <b>Add images</b>, and bulk include tools.<br>"
        "Check the list: each image is shown with its folder path, slices, channels, pixel size and "
        "objective. Sample name and Folder columns identify the experimental condition.<br>"
        "When an image is open, the blue box shows its channel order, for example "
        "<i>Channel 1 = Green · Channel 2 = Red</i>. Channel names come from the file; "
        "do not rename them to label conditions.<br>"
        "Untick <i>Include</i> for any you do not want, or open <b>Advanced</b> and use "
        "<b>Include shown</b> / <b>Leave out shown</b>.",
        "Every image you expect is listed once, with the right folder, and channel order is clear. "
        "Read the yellow notes above the list.",
    ),
    Step(
        2,
        "2 Find objects",
        "Find the nuclei (or cells)",
        "Set <b>Source channel</b> to the nuclear channel (for example Channel 1 = Far Red).<br>"
        "<b>Method</b>: <i>classical</i> is fast and needs no GPU. <i>cellpose</i> handles crowded "
        "or uneven nuclei better (pick the Cellpose engine when that method is selected).<br>"
        "If the images are Z-stacks, choose <b>Z-stack mode</b>: "
        "<i>2D: one slice</i>, <i>2D: max projection</i>, <i>2D + stitching</i>, or <i>True 3D</i>. "
        "The blue box recommends one for this computer; every option stays available.<br>"
        "<b>Typical nucleus diameter</b> defaults to 6 µm (most nuclei are about 5–7 µm). Adjust it; "
        "minimum object size in µm² drops debris.<br>"
        "The box at the top says whether an NVIDIA GPU was found and whether Cellpose will use it.<br>"
        "Click <b>Preview</b> to try the settings on the area you are looking at, then "
        "<b>Run this image</b> at the bottom.",
        "Outlines sit on the nuclei, with few missed, merged, or split. If not, change the settings "
        "and run again. Zoom in to check. In 3D, move the slice slider under the image to check every slice.",
    ),
    Step(
        3,
        "3 Markers",
        "Choose the markers to count",
        "Tick the channels you want to count, then click <b>Set up markers</b>. CellQuant measures "
        "each marker's brightness inside every object and calls the object positive or negative. "
        "Choose how: by the object's <b>mean brightness</b>, or by the <b>percent of the cell</b> whose "
        "pixels are at or above a pixel level (set the minimum percent here). "
        "It picks a starting cutoff (or pixel level); you check it in the next step.",
        "Every marker you want is listed, and step 4 opens with objects colored.",
    ),
    Step(
        4,
        "4 Check",
        "Check the positive calls",
        "Objects glow <span style='color:#26bf59'><b>green</b></span> when positive and "
        "<span style='color:#e03c3c'><b>red</b></span> when negative (change the colours under the slider). "
        "Drag the <b>Cutoff</b> slider, or type a number beside it, to move the cutoff (one cutoff for "
        "every image). For the percent-of-cell rule the slider is the minimum percent; change the "
        "pixel level below it and click <b>Apply pixel level</b>. Click <b>Approve</b> when it looks right, then <b>Next image ▶</b> at the bottom. "
        "Hover over a button to see what it does.",
        "The green objects are the ones you would call positive by eye.",
    ),
    Step(
        5,
        "5 Results",
        "Run everything and save the results",
        "Click <b>Run all images</b> to apply the same settings to every image. Then click "
        "<b>Export results</b> and choose a folder. With several analyses in the list at the top, "
        "<b>Run all analyses</b> and <b>Export all analyses</b> do the same for each of them.",
        "The export folder has <i>objects.csv</i> (one row per object) and <i>image_summary.csv</i> "
        "(one row per image, with the percentages). Both open in Excel, Prism, or R.",
    ),
)

# Hover help, in the spirit of CellQuant v1's help_text.py. Keys are
# (panel attribute on the window, widget attribute on the panel).
HELP = {
    ("_objects_panel", "channel"): "The channel whose signal marks every object, usually the nuclear stain. Labels show Channel N = the name from the file.",
    ("_objects_panel", "method"): "classical: threshold and split touching objects. Fast, no GPU.\ncellpose: a trained model. Better for crowded or uneven nuclei; faster with an NVIDIA GPU.",
    ("_objects_panel", "threshold_method"): "otsu picks a brightness cutoff automatically. manual uses the value below.",
    ("_objects_panel", "threshold"): "Brightness above which a pixel counts as part of an object (manual only).",
    ("_objects_panel", "sigma"): "Smoothing before finding objects, in pixels. 1-2 helps with noisy images. 0 turns it off.",
    ("_objects_panel", "min_area"): "Objects smaller than this are dropped (debris). Defaults to 5 µm² when pixel size is known. 0 keeps everything.",
    ("_objects_panel", "max_area"): "Objects larger than this are dropped (clumps). 0 keeps everything.",
    ("_objects_panel", "area_unit"): "px: pixels. um2: square micrometres (needs the pixel size).",
    ("_objects_panel", "nucleus_diameter_um"): "Typical nucleus diameter in micrometres. Most nuclei are about 5–7 µm. Used as Cellpose's size hint when pixel size is known.",
    ("_objects_panel", "watershed"): "Split touching objects. Try this if two nuclei come out as one.",
    ("_objects_panel", "cellpose_model"): "The Cellpose model. The first one listed is the usual choice.",
    ("_objects_panel", "diameter"): "Optional Cellpose diameter in pixels. 0 uses Typical nucleus diameter (µm) when pixel size is known, otherwise lets Cellpose decide.",
    ("_objects_panel", "flow"): "Cellpose shape check. Lower keeps fewer, cleaner objects.",
    ("_objects_panel", "cellprob"): "Cellpose confidence. Lower finds more (and fainter) objects.",
    ("_objects_panel", "gpu"): "Use the NVIDIA GPU. Greyed out when no usable GPU was found.",
    ("_objects_panel", "engine_choice"): "The Cellpose installed here. Cellpose-SAM (4) is most accurate, best with a GPU; Classic Cellpose (3) is faster on the CPU. Only one is installed per environment; start CellQuant with the other to use it.",
    ("_review_panel", "display"): "Which marker to color by: green positive, red negative (colours can be changed).",
    ("_review_panel", "threshold_slider"): "The cutoff for this marker: objects at or past it are positive. Drag, or type a number. For the percent-of-cell rule it is the minimum percent of each object's pixels that must pass the pixel level.",
    ("_review_panel", "min_percent"): "A cell is positive when at least this percent of its pixels are at or above the pixel level.",
    ("_review_panel", "pixel_level"): "A pixel passes when its value is at or above this level (same units as the image). Click Apply pixel level to measure again.",
    ("_review_panel", "counts"): "Counts for this image at the current cutoff. Unmeasured objects are left out of all counts.",
    ("_experiment_panel", "filter_box"): "Show only images whose file or sample name contains this text.",
    ("_experiment_panel", "table"): "Image: where the file is inside the folder you added (hover for the full path). Untick Include to leave an image out without deleting it. Sample names and Folder columns can be edited or pasted from Excel; results can be grouped by them.",
    ("_objects_panel", "z_stack"): (
        "2D: one slice — only that plane.\n"
        "2D: max projection — brightest value through all slices (nuclei at different depths can merge).\n"
        "2D + stitching — segment each slice, then join overlapping outlines into 3D objects.\n"
        "True 3D — segment the whole volume at once (slow; needs closely spaced slices).\n"
        "Times are estimates for this computer; ★ marks the recommendation."
    ),
    ("_experiment_panel", "show_type"): "Show only ND2 or only TIFF files in the list. Under Advanced, Include shown or Leave out shown acts on just those.",
    ("_experiment_panel", "use_nd2"): "Add folder takes ND2 files from the folder and its subfolders.",
    ("_experiment_panel", "use_tiff"): "Add folder takes TIFF files from the folder and its subfolders.",
    ("_experiment_panel", "pixel_z"): "Distance between slices of a Z-stack, in micrometres. Read from ND2 files; needed for 3D volumes.",
    ("_experiment_panel", "meta_name"): "Add a column such as Genotype or Age. Results can be grouped by it.",
    ("_experiment_panel", "pixel_x"): "Pixel width in micrometres. Find it in the microscope software's image properties.",
    ("_experiment_panel", "pixel_y"): "Pixel height in micrometres. Usually the same as the width.",
}

BUTTON_HELP = {
    "Preview": "Try the settings on the area you are looking at. Nothing is saved.",
    "Run": "Find objects in this image and measure them.",
    "Delete object": "Click an object in the image first. This removes it from the counts (for debris or a bad outline). Restore object brings it back.",
    "Restore object": "Bring back a deleted object.",
    "Undo": "Undo the last edit.",
    "Record drawn edits": "Save outlines you painted by hand in the Objects layer.",
    "Mark reviewed": "Note that you looked at this image.",
    "Approve": "Mark this image as checked and correct. Editing it later clears the approval.",
    "Exclude image": "Leave this image out of the results.",
}


# ---------------------------------------------------------------------------
# Where the user is


@dataclass
class StepState:
    done: bool
    detail: str = ""
    blocker: str = ""  # why Next is disabled; empty when the user may continue
    hint: str = ""


def step_states(window) -> list[StepState]:
    """Progress through the five steps, from state already in memory. No file access."""

    controller = window.controller
    if controller is None:
        first = StepState(False, "", "Click New experiment, Open experiment, or Try practice images first.")
        return [first] + [StepState(False, "", "Finish step 1 first.") for _ in STEPS[1:]]
    images = [record for record in controller.experiment.images if record.include]
    hint = ""
    if images and not all(record.pixel_size_x for record in images):
        hint = "Tip: some images have no pixel size, so sizes will be in pixels."
    states = [
        StepState(
            bool(images),
            f"{len(images)} image{'s' if len(images) != 1 else ''}" if images else "",
            "" if images else "Add at least one image.",
            hint,
        )
    ]
    results = [result for result in controller.last_results.values() if result is not None]
    analyzed_status = {"analyzed", "reviewed", "approved"}
    analyzed = [record for record in images if record.processing_status in analyzed_status]
    found = bool(results) or bool(analyzed)
    states.append(
        StepState(
            found,
            f"{len(analyzed) or len(results)} analyzed" if found else "",
            "" if found else "Click Run to find objects in this image.",
        )
    )
    markers = controller.recipe.classifications
    names = ", ".join(item.name for item in markers)
    states.append(StepState(bool(markers), names, "" if markers else "Set up at least one marker."))
    classified = any(all(item.id in result.objects.columns for item in markers) for result in results) if markers else False
    reviewed = [record for record in images if record.processing_status in {"reviewed", "approved"}]
    states.append(
        StepState(
            bool(reviewed),
            f"{len(reviewed)} checked" if reviewed else ("thresholds set" if classified else ""),
            "" if classified else "Measure the markers first: finish step 3.",
            "" if reviewed else "Approve images once they look right.",
        )
    )
    exported = getattr(window, "_exported_to", None)
    states.append(StepState(bool(exported), f"saved to {Path(exported).name}" if exported else "", ""))
    return states


# ---------------------------------------------------------------------------
# Widgets


def _card(text: str, color: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextFormat(Qt.RichText)
    label.setStyleSheet(f"QLabel {{ background: {color}; border-radius: 6px; padding: 8px; }}")
    return label


class StepPage(QWidget):
    """One numbered step: instructions, optional quick actions, the panel, and Back / Next."""

    def __init__(self, window, index: int, panel: QWidget, extra: QWidget | None = None, advanced: bool = False):
        super().__init__()
        self.window = window
        self.index = index
        step = STEPS[index]
        layout = QVBoxLayout(self)
        heading = QLabel(f"<b>Step {step.number} of {len(STEPS)}: {step.title}</b>")
        heading.setStyleSheet("font-size: 14px;")
        layout.addWidget(heading)
        layout.addWidget(_card(f"<b>What to do</b><br>{step.todo}", "rgba(80, 120, 200, 0.18)"))
        if extra is not None:
            layout.addWidget(extra)
        self.panel_scroll = QScrollArea()
        self.panel_scroll.setWidgetResizable(True)
        self.panel_scroll.setFrameShape(QFrame.NoFrame)
        self.panel_scroll.setWidget(panel)
        self.panel_scroll.setMinimumWidth(0)
        panel.setMinimumWidth(0)
        from qtpy.QtWidgets import QSizePolicy
        expanding = getattr(getattr(QSizePolicy, "Policy", QSizePolicy), "Expanding")
        ignored = getattr(getattr(QSizePolicy, "Policy", QSizePolicy), "Ignored")
        self.panel_scroll.setSizePolicy(ignored, expanding)
        panel.setSizePolicy(ignored, expanding)
        if advanced:
            self.advanced_toggle = QCheckBox("Show all settings (advanced)")
            self.advanced_toggle.toggled.connect(self.panel_scroll.setVisible)
            self.panel_scroll.setVisible(False)
            layout.addWidget(self.advanced_toggle)
        layout.addWidget(self.panel_scroll, 1)
        if not advanced:
            layout.setStretchFactor(self.panel_scroll, 1)
        else:
            layout.addStretch(1)
        layout.addWidget(_card(f"<b>Success check</b><br>{step.success}", "rgba(60, 170, 90, 0.16)"))
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: #d9a400;")
        layout.addWidget(self.hint)
        bar = QHBoxLayout()
        self.back = QPushButton("← Back")
        self.back.clicked.connect(lambda: window.go_to_step(index - 1))
        self.blocker = QLabel("")
        self.blocker.setWordWrap(True)
        self.blocker.setStyleSheet("color: #e08a00;")
        self.next = QPushButton(f"Next: {STEPS[index + 1].title} →" if index + 1 < len(STEPS) else "Back to Start")
        self.next.clicked.connect(lambda: window.go_to_step(index + 1) if index + 1 < len(STEPS) else window.go_to_start())
        bar.addWidget(self.back)
        bar.addWidget(self.blocker, 1)
        bar.addWidget(self.next)
        layout.addLayout(bar)

    def update_state(self, state: StepState) -> None:
        self.next.setEnabled(not state.blocker)
        self.blocker.setText(state.blocker)
        self.hint.setText(state.hint)


class StartPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        layout = QVBoxLayout(self)
        title = QLabel("<h2>CellQuant</h2>")
        layout.addWidget(title)
        layout.addWidget(
            _card(
                "CellQuant finds nuclei (or cells) in fluorescence images and counts how many are "
                "positive for each marker, for one image or a whole experiment. "
                "Work through the numbered tabs from left to right. Each tab says what to do and "
                "how to tell it worked.<br><small>Add images in step 1, not by dragging them onto the "
                "image area: dragged images are not part of the experiment.</small>",
                "rgba(80, 120, 200, 0.18)",
            )
        )
        self.buttons = QGroupBox("Start here")
        grid = QVBoxLayout(self.buttons)
        for text, tip, slot in (
            ("Try practice images", "Makes a small practice experiment with a known answer. The best first step.", window.start_practice),
            ("New experiment…", "Choose the folder with your images and a separate folder for the results.", window.new_experiment),
            ("Open experiment…", "Open a folder you used before. Your settings and results are kept.", window.open_experiment_dialog),
        ):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.setMinimumHeight(32)
            button.clicked.connect(slot)
            grid.addWidget(button)
        from cellquant.hpc import feature_enabled

        self.hpc_button = None
        if feature_enabled():
            self.hpc_button = QPushButton("HPC prep…")
            self.hpc_button.setToolTip(
                "Prepare the open experiment for a cluster GPU job, get the commands to run there, and import the results."
            )
            self.hpc_button.setMinimumHeight(32)
            self.hpc_button.clicked.connect(window.open_hpc_prep)
            grid.addWidget(self.hpc_button)
        layout.addWidget(self.buttons)
        self.experiment = QLabel("")
        self.experiment.setWordWrap(True)
        layout.addWidget(self.experiment)
        steps = QGroupBox("Your progress")
        rows = QVBoxLayout(steps)
        self.rows: list[QLabel] = []
        for step in STEPS:
            label = QLabel("")
            label.setTextFormat(Qt.RichText)
            rows.addWidget(label)
            self.rows.append(label)
        layout.addWidget(steps)
        self.continue_button = QPushButton("Continue →")
        self.continue_button.setMinimumHeight(34)
        self.continue_button.clicked.connect(self._continue)
        layout.addWidget(self.continue_button)
        guide = QPushButton("Open the step-by-step guide")
        guide.setToolTip("The full guide, with what to check at each step and fixes for common problems.")
        guide.clicked.connect(window.open_guide)
        layout.addWidget(guide)
        layout.addStretch(1)
        self._next_index = 0

    def update_state(self, states: list[StepState]) -> None:
        controller = self.window.controller
        if controller is None:
            self.experiment.setText("No experiment is open.")
        else:
            images = controller.experiment.input_directory or "not set yet"
            self.experiment.setText(
                f"Experiment: <b>{controller.experiment.experiment_name}</b><br>"
                f"<small>Images: {images}<br>Results: {controller.directory}</small>"
            )
        self._next_index = next((index for index, state in enumerate(states) if not state.done), len(STEPS) - 1)
        for index, (step, state) in enumerate(zip(STEPS, states)):
            mark = "✓" if state.done else ("→" if index == self._next_index else "•")
            color = "#2fbf5a" if state.done else ("#5a8dee" if index == self._next_index else "#888")
            detail = f" <span style='color:#999'>({state.detail})</span>" if state.detail else ""
            self.rows[index].setText(f"<span style='color:{color}'><b>{mark}</b></span> {step.number}. {step.title}{detail}")
        target = STEPS[self._next_index]
        self.continue_button.setText(f"Continue: {target.number}. {target.title} →")
        self.continue_button.setEnabled(controller is not None)
        if self.hpc_button is not None:
            self.hpc_button.setEnabled(controller is not None)

    def _continue(self) -> None:
        self.window.go_to_step(self._next_index)


class MarkerSetup(QGroupBox):
    """Step 3: one measurement, one positive/negative call and one result per marker channel."""

    def __init__(self, window):
        super().__init__("Quick setup")
        self.window = window
        self.layout_ = QVBoxLayout(self)
        self.note = QLabel("Markers are measured in every channel you tick. Open an experiment first.")
        self.note.setWordWrap(True)
        self.layout_.addWidget(self.note)
        self.boxes_holder = QVBoxLayout()
        self.layout_.addLayout(self.boxes_holder)
        self.boxes: list[QCheckBox] = []
        rule_form = QFormLayout()
        self.rule = QComboBox()
        self.rule.addItem("its mean brightness is above a cutoff", RULE_MEAN)
        self.rule.addItem("enough of its pixels are bright", RULE_PERCENT)
        self.rule.setToolTip(
            "Mean brightness: one number per cell, compared with a cutoff.\n"
            "Percent of the cell: each pixel is compared with a pixel level; the cell is positive when at least\n"
            "the minimum percent of its pixels are at or above that level. Useful when staining is patchy or\n"
            "only part of a nucleus is labelled. Both numbers can be changed later in step 4."
        )
        self.min_percent = QDoubleSpinBox()
        self.min_percent.setRange(0.0, 100.0)
        self.min_percent.setDecimals(1)
        self.min_percent.setSingleStep(5.0)
        self.min_percent.setSuffix(" %")
        self.min_percent.setValue(DEFAULT_MIN_PERCENT)
        self.min_percent.setToolTip("Minimum percent of a cell's pixels that must be at or above the pixel level.")
        rule_form.addRow("A cell is positive when", self.rule)
        rule_form.addRow("Minimum percent of the cell", self.min_percent)
        self.rule.currentIndexChanged.connect(lambda _index: self._rule_changed())
        self.layout_.addLayout(rule_form)
        self._rule_changed()
        self.button = QPushButton("Set up markers")
        self.button.setMinimumHeight(32)
        self.button.setToolTip("Measure each ticked channel inside every object, and count positives.")
        self.button.clicked.connect(self._apply)
        self.layout_.addWidget(self.button)
        self.current = QLabel("")
        self.current.setWordWrap(True)
        self.layout_.addWidget(self.current)

    def refresh(self) -> None:
        for box in self.boxes:
            box.setParent(None)
        self.boxes = []
        controller = self.window.controller
        self.button.setEnabled(controller is not None)
        if controller is None:
            return
        segmentation = controller.recipe.object_set.segmentation_channel
        existing = {item.channel for item in controller.recipe.measurements}
        for channel in controller.experiment.channels:
            if channel.channel_index == segmentation:
                continue
            box = QCheckBox(channel.channel_name)
            box.setProperty("channel_index", channel.channel_index)
            box.setChecked(channel.channel_index in existing or not existing)
            self.boxes.append(box)
            self.boxes_holder.addWidget(box)
        segment_name = next((c.channel_name for c in controller.experiment.channels if c.channel_index == segmentation), "")
        self.note.setText(
            f"Objects are found in <b>{segment_name}</b>. Tick the marker channels to count:"
            if self.boxes
            else "This image has only one channel, so there is no marker to count."
        )
        markers = controller.recipe.classifications
        self.current.setText(
            "Current markers: " + "; ".join(f"{item.name} ({describe_rule(controller.recipe, item)})" for item in markers)
            if markers
            else ""
        )
        if markers:
            percent = [item for item in markers if uses_pixel_level(controller.recipe, item)]
            self.rule.blockSignals(True)
            self.rule.setCurrentIndex(self.rule.findData(RULE_PERCENT if percent else RULE_MEAN))
            if percent:
                self.min_percent.setValue(float(percent[0].threshold))
            self.rule.blockSignals(False)
            self._rule_changed()

    def _rule_changed(self) -> None:
        self.min_percent.setEnabled(self.rule.currentData() == RULE_PERCENT)

    def _apply(self) -> None:
        controller = self.window.require_controller()
        if controller is None:
            return
        chosen = [(int(box.property("channel_index")), box.text()) for box in self.boxes if box.isChecked()]
        if not chosen:
            self.window.message("Tick at least one marker channel.")
            return
        if controller.recipe.classifications:
            answer = QMessageBox.question(
                self,
                "Replace markers?",
                "This replaces the current markers and results rows with one per ticked channel. Continue?",
            )
            if answer != QMessageBox.Yes:
                return
        self.window.apply_marker_setup(chosen, rule=str(self.rule.currentData()), min_percent=self.min_percent.value())


class ResultsSummary(QGroupBox):
    """Step 5: the numbers for the image on screen, in words, and the two main actions."""

    def __init__(self, window):
        super().__init__("Results")
        self.window = window
        layout = QVBoxLayout(self)
        self.summary = QLabel("Run an image to see its numbers here.")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.RichText)
        layout.addWidget(self.summary)
        run_all = QPushButton("Run all images")
        run_all.setMinimumHeight(32)
        run_all.setToolTip("Apply the current settings to every included image. Progress shows at the bottom.")
        run_all.clicked.connect(lambda: window.start_batch(None))
        export = QPushButton("Export results…")
        export.setMinimumHeight(32)
        export.setToolTip("Save objects.csv, image_summary.csv and the settings to a folder you choose.")
        export.clicked.connect(window.export_dialog)
        layout.addWidget(run_all)
        layout.addWidget(export)
        # Shown when the experiment has more than one analysis (the list at the top of the panel).
        self.run_analyses = QPushButton("Run all analyses")
        self.run_analyses.setToolTip("Run every included image with each analysis, one analysis after another.")
        self.run_analyses.clicked.connect(window.run_all_analyses)
        self.export_analyses = QPushButton("Export all analyses…")
        self.export_analyses.setToolTip(
            "Save each analysis's results in its own folder, plus all_analyses_image_summary.csv with every "
            "analysis's per-image numbers side by side."
        )
        self.export_analyses.clicked.connect(window.export_all_dialog)
        for button in (self.run_analyses, self.export_analyses):
            button.setMinimumHeight(32)
            button.setVisible(False)
            layout.addWidget(button)
        self.batch = QLabel("")
        self.batch.setWordWrap(True)
        layout.addWidget(self.batch)
        self.open_folder = QPushButton("Open the export folder")
        self.open_folder.setVisible(False)
        self.open_folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(window._exported_to))))
        layout.addWidget(self.open_folder)

    def show_result(self, result, recipe) -> None:
        if result is None or result.reports is None or result.reports.empty:
            self.summary.setText("No results yet for this image. Set up markers (step 3) and run it.")
            return
        names = {item.id: item.name for item in recipe.classifications}
        lines = [f"<b>{result.provenance.get('filename', '')}</b>: {result.qc.n_objects} objects"]
        for row in result.reports.to_dict(orient="records"):
            percent = row["percent"]
            percent_text = "—" if percent != percent else f"{percent:.1f}%"
            lines.append(
                f"{_readable(row['numerator'], names)} among {_readable(row['denominator'], names)}: "
                f"<b>{row['count']}</b> of {row['denominator_count']} ({percent_text})"
            )
        unmeasured = int(result.summary.iloc[0].get("n_unmeasured", 0)) if not result.summary.empty and "n_unmeasured" in result.summary.columns else 0
        if unmeasured:
            lines.append(f"<span style='color:#d9a400'>{unmeasured} objects could not be measured and are left out.</span>")
        self.summary.setText("<br>".join(lines))

    def show_batch(self, report) -> None:
        text = f"{len(report.jobs)} images analyzed: {report.completed} fine"
        if report.warnings:
            text += f", {report.warnings} with warnings (check them in step 4)"
        if report.failed:
            text += f", {report.failed} failed (see the messages at the bottom)"
        self.batch.setText(text + ".")

    def show_analysis_actions(self, several: bool) -> None:
        self.run_analyses.setVisible(several)
        self.export_analyses.setVisible(several)

    def show_analyses(self, reports) -> None:
        lines = []
        for report in reports:
            text = f"<b>{report.analysis}</b>: {len(report.jobs)} images, {report.completed} fine"
            if report.warnings:
                text += f", {report.warnings} with warnings"
            if report.failed:
                text += f", {report.failed} failed"
            if report.cancelled:
                text += " (stopped)"
            lines.append(text)
        self.batch.setText("<br>".join(lines) + "<br>Choose an analysis at the top to check its images in step 4.")

    def show_exported(self, path) -> None:
        self.batch.setText(self.batch.text() + f"<br>Saved to <b>{path}</b>.")
        self.open_folder.setVisible(True)


def _readable(expression: str, names: dict[str, str]) -> str:
    text = str(expression).strip()
    if text.casefold() in {"all_objects", "all_measured_objects"}:
        return "all objects"
    parts = re.split(r"\s+AND\s+", text, flags=re.IGNORECASE)
    words = []
    for part in parts:
        negated = part.casefold().startswith("not ")
        token = part.split(None, 1)[1] if negated else part
        name = names.get(token.strip(), token.strip())
        words.append(f"{name}-" if negated else f"{name}+")
    return " and ".join(words)


class GuideDialog(QDialog):
    def __init__(self, parent, text: str):
        super().__init__(parent)
        self.setWindowTitle("CellQuant: step-by-step guide")
        self.resize(760, 820)
        layout = QVBoxLayout(self)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setMarkdown(text)
        layout.addWidget(browser)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        layout.addWidget(close)


def guide_text() -> str:
    path = Path(__file__).with_name("START_HERE.md")
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return "The guide file is missing. See docs/START_HERE.md in the CellQuant folder."


def apply_help(window) -> None:
    for (panel_name, widget_name), text in HELP.items():
        panel = getattr(window, panel_name, None)
        widget = getattr(panel, widget_name, None) if panel is not None else None
        if widget is not None:
            widget.setToolTip(text)
    for panel_name in ("_objects_panel", "_review_panel", "_experiment_panel", "_results_panel"):
        panel = getattr(window, panel_name, None)
        if panel is None:
            continue
        for button in panel.findChildren(QPushButton):
            if button.text() in BUTTON_HELP and not button.toolTip():
                button.setToolTip(BUTTON_HELP[button.text()])


def start_refresh_timer(window) -> QTimer:
    """Refresh step states once a second. Reads only in-memory state."""

    # Parented to the CellQuant panel, so the timer stops when the panel is closed.
    timer = QTimer(window._tabs)
    timer.timeout.connect(window.refresh_guidance)
    timer.start(1000)
    return timer


def choose_practice_folder(parent) -> Path | None:
    default = Path.home() / "Documents"
    base = QFileDialog.getExistingDirectory(parent, "Choose where to put the practice experiment", str(default if default.is_dir() else Path.home()))
    if not base:
        return None
    folder = Path(base) / "CellQuant practice"
    counter = 2
    while folder.exists():
        folder = Path(base) / f"CellQuant practice {counter}"
        counter += 1
    return folder
