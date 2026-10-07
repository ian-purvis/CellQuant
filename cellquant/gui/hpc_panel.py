"""HPC prep: prepare a package for a cluster GPU job, get the commands to run, and import the results.

Every slow step (checking, preparing, validating, importing) runs off the
interface thread through the window's worker, with the footer's progress bar
and Cancel. Nothing here segments, logs into a cluster or submits a job.
"""

from __future__ import annotations

import json
from pathlib import Path

from qtpy.QtCore import Qt, QTimer

from cellquant.gui.guide import add_range_tip
from cellquant.quicksetup import describe_rule
from qtpy.QtWidgets import (
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

PAGES = ("1. Images", "2. Settings", "3. Cluster", "4. Prepare", "5. Submit", "6. Import")


def _note(text: str, color: str = "rgba(80, 120, 200, 0.14)", tip: str = "") -> QLabel:
    label = QLabel(text)
    label.setToolTip(tip)
    label.setWordWrap(True)
    label.setTextFormat(Qt.RichText)
    label.setStyleSheet(f"QLabel {{ background: {color}; border-radius: 6px; padding: 6px; }}")
    return label


def _issues_html(errors, warnings=()) -> str:
    lines = []
    for issue in errors:
        fix = f"<br><small>{issue.fix}</small>" if getattr(issue, "fix", None) else ""
        lines.append(f"<p style='color:#c0392b'>✗ {issue.message}{fix}</p>")
    for issue in warnings:
        lines.append(f"<p style='color:#9a7d0a'>! {issue.message}</p>")
    return "".join(lines) or "<p style='color:#2e8b57'>✓ No problems found.</p>"


def _read_only(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    return item


class HpcPanel(QWidget):
    """The HPC prep workflow, one tab per step, inside the CellQuant dock."""

    def __init__(self, shell):
        super().__init__()
        self.shell = shell
        self.profile = None  # ResolvedProfile
        self.plan = None
        self.package_dir: Path | None = None
        self.preview = None
        self.imported_to: Path | None = None
        # Changes made here for the package only (never to the experiment's own settings), shown on page 2.
        self.overrides: dict = {}
        layout = QVBoxLayout(self)
        layout.addWidget(
            _note(
                "<b>HPC prep</b> ⓘ",
                tip="Makes a package of your images and frozen settings for a cluster GPU job, "
                "gives you the commands to run there, and imports the results as a new experiment. "
                "Local analysis stays as it was; use the other tabs to return to it.",
            )
        )
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self._build_images()
        self._build_settings()
        self._build_cluster()
        self._build_prepare()
        self._build_submit()
        self._build_import()

    # -- 1. images -----------------------------------------------------------------------------------------

    def _build_images(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        send_label = QLabel("Tick images to send.")
        send_label.setToolTip("Each row is one image: a file, or one position (field of view) of an ND2 file.")
        layout.addWidget(send_label)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Send", "Sample", "File", "Position", "Channels", "Slices", "Pixel size (µm)"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        for text, value in (("Select all", True), ("Select none", False)):
            button = QPushButton(text)
            button.clicked.connect(lambda _checked=False, value=value: self._select_all(value))
            row.addWidget(button)
        refresh = QPushButton("Reload images")
        refresh.clicked.connect(self.refresh)
        row.addWidget(refresh)
        layout.addLayout(row)
        self.selection_label = QLabel("")
        layout.addWidget(self.selection_label)
        self.table.itemChanged.connect(lambda _item: self._update_selection_label())
        self.tabs.addTab(page, PAGES[0])

    def refresh(self) -> None:
        controller = self.shell.controller
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        if controller is not None:
            for record in controller.experiment.images:
                row = self.table.rowCount()
                self.table.insertRow(row)
                tick = QTableWidgetItem()
                tick.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                # The images the analysis shown runs (its Plan); every included image unless changed.
                tick.setCheckState(Qt.Checked if controller.is_planned(record.image_id) else Qt.Unchecked)
                tick.setData(Qt.UserRole, record.image_id)
                self.table.setItem(row, 0, tick)
                size = f"{record.pixel_size_x:.4g}" if record.pixel_size_x else "none"
                if record.pixel_size_z:
                    size += f" × {record.pixel_size_z:.3g} (Z)"
                values = [
                    record.sample_name,
                    record.relative_path or record.filename,
                    str(record.position + 1) if record.positions_in_file > 1 else "",
                    ", ".join(record.channel_names) or str(record.number_of_channels or ""),
                    str(record.z_planes),
                    size,
                ]
                for column, value in enumerate(values, start=1):
                    self.table.setItem(row, column, _read_only(value))
        self.table.blockSignals(False)
        self._update_selection_label()
        self.refresh_settings()

    def _select_all(self, value: bool) -> None:
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(Qt.Checked if value else Qt.Unchecked)

    def selected_ids(self) -> list[str]:
        return [
            self.table.item(row, 0).data(Qt.UserRole)
            for row in range(self.table.rowCount())
            if self.table.item(row, 0).checkState() == Qt.Checked
        ]

    def _update_selection_label(self) -> None:
        self.selection_label.setText(f"{len(self.selected_ids())} of {self.table.rowCount()} images selected.")

    # -- 2. settings ---------------------------------------------------------------------------------------

    def _build_settings(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        frozen = QLabel("Frozen into the package. ⓘ")
        frozen.setToolTip(
            "These settings are frozen into the package when it is prepared. Later changes here do not change a "
            "prepared package; prepare a new one instead."
        )
        layout.addWidget(frozen)
        self.settings_text = QTextEdit()
        self.settings_text.setReadOnly(True)
        layout.addWidget(self.settings_text)
        self.settings_issues = QLabel("")
        self.settings_issues.setWordWrap(True)
        self.settings_issues.setTextFormat(Qt.RichText)
        layout.addWidget(self.settings_issues)
        self.override_label = QLabel("")
        self.override_label.setWordWrap(True)
        self.override_label.setTextFormat(Qt.RichText)
        layout.addWidget(self.override_label)
        row = QHBoxLayout()
        use = QPushButton("Use the current settings")
        use.setToolTip("Read the settings from steps 2-5, including changes not yet run.")
        use.clicked.connect(self.refresh_settings)
        row.addWidget(use)
        self.gpu_button = QPushButton("Use the GPU on the cluster")
        self.gpu_button.setToolTip("Cluster jobs run Cellpose on the GPU. Changes the package's settings only, not this experiment's.")
        self.gpu_button.clicked.connect(lambda: self._override({"gpu": True}))
        self.gpu_button.setVisible(False)
        row.addWidget(self.gpu_button)
        self.engine_button = QPushButton("Use the cluster's engine and model")
        self.engine_button.setToolTip("Changes the package's settings only, not this experiment's.")
        self.engine_button.clicked.connect(self._use_cluster_engine)
        self.engine_button.setVisible(False)
        row.addWidget(self.engine_button)
        self.clear_overrides = QPushButton("Undo package-only changes")
        self.clear_overrides.clicked.connect(lambda: self._override(None))
        self.clear_overrides.setVisible(False)
        row.addWidget(self.clear_overrides)
        layout.addLayout(row)
        self.confirm_layout = QCheckBox("Same channel order despite other names")
        self.confirm_layout.setToolTip(
            "Images whose channels have other names still have the same channels in the same order.\n"
            "Only tick this if you are sure. Channels are never reordered."
        )
        layout.addWidget(self.confirm_layout)
        layout.addWidget(
            _note(
                "Edits and approvals are <b>not</b> sent. ⓘ",
                "rgba(230, 160, 40, 0.18)",
                "Manual edits, deleted objects and approvals are not sent: the cluster segments every image again. "
                "Image selection, sample names and your metadata columns are kept.",
            )
        )
        self.tabs.addTab(page, PAGES[1])

    def package_recipe(self):
        """The settings the package will freeze: the experiment's current settings plus package-only changes."""

        controller = self.shell.controller
        if controller is None:
            return None
        self.shell._panels_to_recipe()
        if not self.overrides:
            return controller.recipe.model_copy(deep=True)
        from cellquant.recipe import load_recipe

        data = controller.recipe.model_dump(mode="json")
        data["object_set"]["parameters"] = {**(data["object_set"].get("parameters") or {}), **self.overrides}
        return load_recipe(data)

    def _override(self, values: dict | None) -> None:
        self.overrides = {} if values is None else {**self.overrides, **values}
        self.refresh_settings()

    def _use_cluster_engine(self) -> None:
        runtime = self.profile.runtime if self.profile is not None else None
        if runtime is not None:
            self._override({"engine": runtime.engine, "model": runtime.model})

    def refresh_settings(self) -> None:
        controller = self.shell.controller
        if controller is None:
            self.settings_text.setPlainText("Open an experiment first.")
            return
        try:
            recipe = self.package_recipe()
        except Exception as exc:  # noqa: BLE001 - an unfinished form; shown to the user
            self.settings_issues.setText(f"<p style='color:#c0392b'>The settings forms have a problem: {exc}</p>")
            return
        parameters = recipe.object_set.parameters or {}
        channels = [channel.channel_name for channel in sorted(controller.experiment.channels, key=lambda item: item.channel_index)]

        def channel(index: int) -> str:
            return f"{index + 1} ({channels[index]})" if index < len(channels) else str(index + 1)

        controller = self.shell.controller if hasattr(self, "shell") else None
        analysis = controller.active_analysis().name if controller is not None else ""
        lines = [
            *([f"Analysis: {analysis} (choose another in the Analysis list at the top; one package per analysis)"] if analysis else []),
            f"Method: {recipe.object_set.algorithm}"
            + (f", engine {parameters.get('engine')}, model {parameters.get('model')}, GPU {'on' if parameters.get('gpu') else 'off'}" if recipe.object_set.algorithm == "cellpose" else ""),
            f"Channel segmented: {channel(recipe.object_set.segmentation_channel)}",
            f"Z-stacks: {recipe.z_stack}" + (f", slice {recipe.z_index + 1}" if recipe.z_index is not None else (", middle slice" if recipe.z_stack == 'single_plane' else "")),
            "Segmentation settings: " + ", ".join(f"{key}={value}" for key, value in sorted(parameters.items()) if key not in ("engine", "model", "gpu")),
            "",
            "Measurements:",
            *[f"  {item.id}: {item.statistic} of channel {channel(item.channel)} in {item.region.type}" for item in recipe.measurements],
            "Markers (positive when):",
            *[f"  {item.name}: {describe_rule(recipe, item)} ({item.measurement})" for item in recipe.classifications],
            "Reports:",
            *[f"  {item.numerator} / {item.denominator}" for item in recipe.reports],
            "",
            f"Settings fingerprint: {recipe.content_hash()[:16]}",
        ]
        self.settings_text.setPlainText("\n".join(lines))
        issues = []
        if self.profile is not None and self.profile.runtime is not None:
            from cellquant.hpc.compat import check_against_runtime

            issues = check_against_runtime(recipe, self.profile.runtime)
        else:
            from cellquant.hpc.compat import recipe_engine

            issues = recipe_engine(recipe)[1]
            if recipe.object_set.algorithm == "cellpose" and not parameters.get("gpu"):
                from cellquant.hpc.models import Issue

                issues.append(Issue(code="E_GPU", message="These settings were made without a GPU, so the cluster would run Cellpose on the CPU."))
        self.gpu_button.setVisible(any(issue.code == "E_GPU" for issue in issues))
        self.engine_button.setVisible(
            self.profile is not None and self.profile.runtime is not None and any(issue.code in ("E_ENGINE", "E_MODEL") for issue in issues)
            and recipe.object_set.algorithm == "cellpose"
        )
        self.clear_overrides.setVisible(bool(self.overrides))
        self.override_label.setText(
            "<p style='color:#1f618d'><b>Changed for this package only:</b> "
            + ", ".join(f"{key} = {value}" for key, value in self.overrides.items())
            + ". The experiment's own settings are unchanged.</p>"
            if self.overrides
            else ""
        )
        note = "" if self.profile is not None else "<p><small>Load a cluster profile (step 3) to check against the cluster.</small></p>"
        self.settings_issues.setText(_issues_html(issues) + note)

    # -- 3. cluster ----------------------------------------------------------------------------------------

    def _build_cluster(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        load = QPushButton("Load profile…")
        load.clicked.connect(self._choose_profile)
        example = QPushButton("Start from the example")
        example.clicked.connect(self._load_example)
        save = QPushButton("Save profile as…")
        save.clicked.connect(self._save_profile)
        for button in (load, example, save):
            row.addWidget(button)
        layout.addLayout(row)
        self.profile_path = QLabel("No profile loaded.")
        self.profile_path.setWordWrap(True)
        layout.addWidget(self.profile_path)
        form = QFormLayout()
        self.account = QLineEdit()
        self.durable = QLineEdit()
        self.scratch = QLineEdit()
        self.python_path = QLineEdit()
        self.gres = QLineEdit()
        self.cpus = QSpinBox()
        self.cpus.setRange(1, 256)
        self.memory = QSpinBox()
        self.memory.setRange(1, 4096)
        self.memory.setSuffix(" GiB")
        self.hours = QDoubleSpinBox()
        self.hours.setRange(0.1, 24 * 14)
        self.hours.setSuffix(" h")
        for box in (self.cpus, self.memory, self.hours):
            add_range_tip(box)
        for label, widget in (
            ("Slurm account", self.account),
            ("Project folder on the cluster", self.durable),
            ("Scratch folder", self.scratch),
            ("Environment's Python", self.python_path),
            ("GPU (Slurm GRES)", self.gres),
            ("CPUs", self.cpus),
            ("Memory", self.memory),
            ("Time limit", self.hours),
        ):
            form.addRow(label, widget)
        layout.addLayout(form)
        apply = QPushButton("Check profile")
        apply.clicked.connect(self._apply_profile_fields)
        layout.addWidget(apply)
        self.profile_status = QLabel("")
        self.profile_status.setWordWrap(True)
        self.profile_status.setTextFormat(Qt.RichText)
        layout.addWidget(self.profile_status)
        layout.addWidget(
            _note(
                "Resources never change the analysis. ⓘ",
                tip="The engine, model and enabled Z modes come from the cluster's runtime record (a file describing "
                "the software installed on the cluster, made by whoever set it up); a profile stays experimental "
                "until a test run on the cluster is recorded.",
            )
        )
        layout.addStretch(1)
        self.tabs.addTab(page, PAGES[2])

    def _choose_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose a cluster profile", "", "Profiles (*.json)")
        if path:
            self.load_profile(path)

    def _load_example(self) -> None:
        from cellquant.hpc.profiles import examples_dir

        self.load_profile(examples_dir() / "alpine_h200_example.json")
        self.shell.message("Example profile loaded. Fill in your account and folders, then save it beside the cluster's runtime record (the file describing its installed software).")

    def load_profile(self, path) -> None:
        from cellquant.hpc.profiles import load_profile

        try:
            self.profile = load_profile(path)
        except Exception as exc:  # noqa: BLE001 - shown to the user
            self.profile = None
            self.profile_status.setText(f"<p style='color:#c0392b'>The profile could not be read: {exc}</p>")
            return
        self._show_profile()

    def _show_profile(self) -> None:
        resolved = self.profile
        if resolved is None:
            return
        profile = resolved.profile
        self.profile_path.setText(f"Profile: {resolved.path}" if resolved.path else "Profile (not saved)")
        self.account.setText(profile.account)
        self.durable.setText(profile.remote_durable_root)
        self.scratch.setText(profile.scratch_root)
        self.python_path.setText(profile.python_path)
        self.gres.setText(profile.gpu_resource)
        self.cpus.setValue(profile.cpus)
        self.memory.setValue(max(1, round(profile.memory_mib / 1024)))
        self.hours.setValue(profile.walltime_seconds / 3600)
        runtime = resolved.runtime
        runtime_text = (
            f"Runtime {runtime.runtime_id}: {runtime.engine} {runtime.cellpose_version}, model {runtime.model}, "
            f"Z modes enabled: {', '.join(runtime.supported_modes) or 'none'}; validated {runtime.runtime_validation_date or 'not yet'}."
            if runtime is not None
            else "No runtime record (the file describing the software installed on the cluster) was found for this profile."
        )
        state = "Ready for packages." if resolved.submission_ready else "Not ready for packages yet."
        self.profile_status.setText(
            f"<p><b>{state}</b> {resolved.status_text}</p><p>{runtime_text}</p>" + _issues_html(resolved.errors, resolved.warnings)
        )
        self.refresh_settings()

    def _apply_profile_fields(self) -> None:
        from cellquant.hpc.profiles import resolve_profile

        if self.profile is None:
            self.profile_status.setText("<p style='color:#c0392b'>Load a profile first.</p>")
            return
        try:
            edited = self.profile.profile.model_validate(
                {
                    **self.profile.profile.model_dump(mode="json"),
                    "account": self.account.text().strip(),
                    "remote_durable_root": self.durable.text().strip(),
                    "scratch_root": self.scratch.text().strip(),
                    "python_path": self.python_path.text().strip(),
                    "gpu_resource": self.gres.text().strip(),
                    "cpus": self.cpus.value(),
                    "memory_mib": int(self.memory.value() * 1024),
                    "walltime_seconds": int(round(self.hours.value() * 3600)),
                }
            )
        except Exception as exc:  # noqa: BLE001 - validation message for the user
            self.profile_status.setText(f"<p style='color:#c0392b'>{exc}</p>")
            return
        self.profile = resolve_profile(edited, self.profile.path)
        self._show_profile()

    def _save_profile(self) -> None:
        from cellquant.hpc.profiles import load_profile, save_profile

        if self.profile is None:
            return
        self._apply_profile_fields()
        path, _ = QFileDialog.getSaveFileName(self, "Save the cluster profile", "cluster_profile.json", "Profiles (*.json)")
        if not path:
            return
        profile = self.profile.profile
        contract = Path(profile.runtime_contract_path)
        if not contract.is_absolute() and self.profile.path is not None:
            contract = (self.profile.path.resolve().parent / contract).resolve()
            target_dir = Path(path).resolve().parent
            try:
                contract = contract.relative_to(target_dir)
            except ValueError:
                pass
            profile = profile.model_copy(update={"runtime_contract_path": str(contract).replace("\\", "/")})
        save_profile(profile, path)
        self.profile = load_profile(path)
        self._show_profile()

    # -- 4. prepare ----------------------------------------------------------------------------------------

    def _build_prepare(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        self.output = QLineEdit()
        self.output.setPlaceholderText("e.g. D:\\CellQuant_HPC")
        self.output.setToolTip("A short folder path outside your image folders.")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._choose_output)
        row.addWidget(QLabel("Package folder:"))
        row.addWidget(self.output)
        row.addWidget(browse)
        layout.addLayout(row)
        buttons = QHBoxLayout()
        self.check_button = QPushButton("Check before preparing")
        self.check_button.clicked.connect(self.check_plan)
        self.prepare_button = QPushButton("Prepare package")
        self.prepare_button.setEnabled(False)
        self.prepare_button.clicked.connect(self.prepare)
        buttons.addWidget(self.check_button)
        buttons.addWidget(self.prepare_button)
        layout.addLayout(buttons)
        self.plan_text = QLabel("Choose images, profile and folder, then check.")
        self.plan_text.setWordWrap(True)
        self.plan_text.setTextFormat(Qt.RichText)
        layout.addWidget(self.plan_text)
        layout.addWidget(
            _note(
                "Copies images unchanged; does not segment. ⓘ",
                tip="Preparing copies every channel and slice of each image, unchanged, into the package, and checks each "
                "copy against the original. Cancel (bottom of the window) stops between steps and "
                "leaves a folder named '.incomplete', which is never used.",
            )
        )
        layout.addStretch(1)
        self.tabs.addTab(page, PAGES[3])

    def _choose_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose where the package folder is made")
        if path:
            self.output.setText(path)

    def _plan_inputs(self):
        controller = self.shell.controller
        if controller is None:
            self.plan_text.setText("<p style='color:#c0392b'>Open an experiment first.</p>")
            return None
        if self.profile is None:
            self.plan_text.setText("<p style='color:#c0392b'>Load a cluster profile in step 3 first.</p>")
            return None
        self.refresh_settings()
        return controller, self.package_recipe(), self.selected_ids(), self.confirm_layout.isChecked()

    def check_plan(self) -> None:
        from cellquant.hpc.prepare import plan_preparation

        inputs = self._plan_inputs()
        if inputs is None:
            return
        controller, recipe, ids, confirm = inputs
        profile = self.profile
        per_image = [
            controller.experiment.image(image_id)
            for image_id in ids
            if controller.segmentation_channel_for(image_id)[1] in ("chosen for this image", "same name")
            or any(reason == "same name" for _channel, reason in controller.measurement_channels_for(image_id).values())
        ]
        if per_image:
            names = ", ".join(record.relative_path or record.filename for record in per_image[:5])
            more = f" and {len(per_image) - 5} more" if len(per_image) > 5 else ""
            self.plan_text.setText(
                "<p style='color:#c0392b'>Cluster packages use one channel setting for every image, but these images "
                f"use a channel chosen for them or another channel layout: {names}{more}. Untick them here (run them "
                "on this computer), or give them an analysis of their own.</p>"
            )
            self.prepare_button.setEnabled(False)
            return
        self.prepare_button.setEnabled(False)

        def work():
            return plan_preparation(controller.experiment, recipe, profile, image_ids=ids, confirm_channel_layout=confirm)

        self.shell._start_job(work, self._show_plan)

    def _show_plan(self, plan) -> None:
        self.plan = plan
        summary = (
            f"<p><b>{len(plan.acquisitions)} images</b>, {plan.input_bytes / 1024**3:.2f} GiB of pixels to copy; "
            f"up to {plan.peak_memory_bytes / 1024**3:.2f} GiB of memory while preparing "
            "(ND2 files are read whole).</p>"
        )
        self.plan_text.setText(summary + _issues_html(plan.all_errors(), plan.all_warnings()))
        self.prepare_button.setEnabled(plan.ok and bool(self.output.text().strip()))
        if plan.ok and not self.output.text().strip():
            self.plan_text.setText(self.plan_text.text() + "<p>Choose a package folder above.</p>")

    def prepare(self) -> None:
        from cellquant.hpc.prepare import PreparationError, prepare_package
        from cellquant.progress import AnalysisCancelled

        plan = self.plan
        output = self.output.text().strip()
        if plan is None or not plan.ok or not output:
            return

        def work():
            try:
                return prepare_package(plan, output)
            except (PreparationError, AnalysisCancelled, OSError) as exc:
                return exc

        self.prepare_button.setEnabled(False)
        self.shell._start_job(work, self._prepared)

    def _prepared(self, outcome) -> None:
        from cellquant.hpc.prepare import PreparationError
        from cellquant.progress import AnalysisCancelled

        if isinstance(outcome, AnalysisCancelled):
            self.plan_text.setText("<p>Preparation was stopped. The unfinished folder ends in '.incomplete' and is not a package.</p>")
            self.prepare_button.setEnabled(True)
            return
        if isinstance(outcome, PreparationError):
            self.plan_text.setText(f"<p style='color:#c0392b'>{outcome}</p>" + _issues_html(outcome.issues))
            self.prepare_button.setEnabled(True)
            return
        if isinstance(outcome, Exception):
            self.plan_text.setText(f"<p style='color:#c0392b'>Writing the package failed: {outcome}</p>")
            self.prepare_button.setEnabled(True)
            return
        self.package_dir = outcome.package_dir
        self.plan_text.setText(
            f"<p style='color:#2e8b57'><b>Package ready:</b> {outcome.package_dir}</p>"
            f"<p><small>A record of the original files was kept beside it (not transferred): {outcome.sidecar.name}</small></p>"
        )
        self.package_field.setText(str(outcome.package_dir))
        self.import_package.setText(str(outcome.package_dir))
        self.tabs.setCurrentIndex(4)
        self._when_idle(self.validate_package)

    def _when_idle(self, action) -> None:
        """Run an action once the window's current job has finished (one job runs at a time)."""

        if self.shell.is_busy():
            QTimer.singleShot(50, lambda: self._when_idle(action))
        else:
            action()

    # -- 5. submit -----------------------------------------------------------------------------------------

    def _build_submit(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        self.package_field = QLineEdit()
        self.package_field.setPlaceholderText("A prepared package folder (cq_hpc_...)")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._choose_package)
        check = QPushButton("Check package")
        check.clicked.connect(self.validate_package)
        row.addWidget(self.package_field)
        row.addWidget(browse)
        row.addWidget(check)
        layout.addLayout(row)
        self.submit_status = QLabel("Prepare or choose a package.")
        self.submit_status.setWordWrap(True)
        self.submit_status.setTextFormat(Qt.RichText)
        layout.addWidget(self.submit_status)
        self.commands = QTextEdit()
        self.commands.setReadOnly(True)
        self.commands.setEnabled(False)
        layout.addWidget(self.commands)
        self.copy_button = QPushButton("Copy the commands")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(lambda: QApplication.clipboard().setText(self.commands.toPlainText()))
        layout.addWidget(self.copy_button)
        self.tabs.addTab(page, PAGES[4])

    def _choose_package(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose a prepared package (cq_hpc_...)")
        if path:
            self.package_field.setText(path)
            self.validate_package()

    def validate_package(self) -> None:
        from cellquant.hpc.validate import validate_bundle

        path = self.package_field.text().strip()
        if not path:
            return
        self.commands.setEnabled(False)
        self.copy_button.setEnabled(False)

        def work():
            return validate_bundle(path, require_ready=True)

        self.shell._start_job(work, lambda outcome: self._validated(path, outcome))

    def _validated(self, path: str, outcome) -> None:
        from cellquant.hpc.templates import remote_package_dir, remote_results_root

        report, bundle = outcome
        if bundle is None:
            self.submit_status.setText("<p style='color:#c0392b'><b>This is not a READY package.</b> Commands are shown only for READY packages.</p>" + _issues_html(report.errors))
            self.commands.setPlainText("")
            return
        profile, manifest = bundle.profile, bundle.manifest
        remote = remote_package_dir(profile, manifest)
        results = remote_results_root(profile, manifest)
        unresolved = profile.unresolved_fields()
        text = f"""# 1. Transfer the whole folder so that it is at:
#    {remote}
#    Globus is best for large packages. rsync from a terminal on this computer also works:
rsync -av --partial "{path}" YOUR_USERNAME@{profile.host}:{profile.remote_durable_root}/

# 2. On a login node of {profile.host}:
cd {remote}
bash scripts/preflight.sh            # the package, software and model files (no GPU work)
bash scripts/submit.sh               # queues the GPU job and prints its ID and log

# 3. Watch it:        squeue -j JOB_ID
#    After it ends:   sacct -j JOB_ID
#    Results:         {results}/RUN_ID  (download this whole folder)
#    Continue a stopped run:  bash scripts/submit.sh --resume RUN_ID
"""
        self.commands.setPlainText(text)
        self.commands.setEnabled(True)
        self.copy_button.setEnabled(True)
        count = len(manifest.acquisitions)
        warning = f"<p style='color:#c0392b'>Placeholders remain: {', '.join(unresolved)}</p>" if unresolved else ""
        self.submit_status.setText(
            f"<p style='color:#2e8b57'><b>READY</b>: {manifest.package_name}, {count} image{'s' if count != 1 else ''}, "
            f"engine {bundle.runtime.engine}, model {bundle.runtime.model}. Every file matches its checksum.</p>"
            f"<p>Full instructions are in README_SUBMIT.md inside the package.</p>{warning}"
        )
        self.package_dir = Path(path)
        if not self.import_package.text().strip():
            self.import_package.setText(path)

    # -- 6. import -----------------------------------------------------------------------------------------

    def _build_import(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self.import_package = QLineEdit()
        self.import_results_field = QLineEdit()
        self.import_destination = QLineEdit()
        for label, field, title, folder in (
            ("Prepared package", self.import_package, "Choose the prepared package on this computer", True),
            ("Downloaded run folder", self.import_results_field, "Choose the downloaded run folder (run_...)", True),
            ("New experiment folder", self.import_destination, "Choose a new, empty folder for the imported experiment", True),
        ):
            row = QHBoxLayout()
            row.addWidget(field)
            browse = QPushButton("Browse…")
            browse.clicked.connect(lambda _checked=False, field=field, title=title: self._browse_into(field, title))
            row.addWidget(browse)
            holder = QWidget()
            holder.setLayout(row)
            form.addRow(label, holder)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        self.preview_button = QPushButton("Check results")
        self.preview_button.clicked.connect(self.check_results)
        self.import_button = QPushButton("Import results")
        self.import_button.setEnabled(False)
        self.import_button.clicked.connect(self.run_import)
        buttons.addWidget(self.preview_button)
        buttons.addWidget(self.import_button)
        layout.addLayout(buttons)
        self.partial = QCheckBox("Import completed results only")
        self.partial.setToolTip("Some images did not finish. Their records are imported without results, marked for attention.")
        self.partial.setVisible(False)
        self.partial.toggled.connect(self._update_import_button)
        layout.addWidget(self.partial)
        self.results_table = QTableWidget(0, 4)
        self.results_table.setHorizontalHeaderLabels(["Image", "Sample", "State", "Note"])
        self.results_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        layout.addWidget(self.results_table)
        self.import_status = QLabel("Imports as a new experiment. ⓘ")
        self.import_status.setToolTip("The import makes a new experiment with its own copy of the images. Nothing existing is changed.")
        self.import_status.setWordWrap(True)
        self.import_status.setTextFormat(Qt.RichText)
        layout.addWidget(self.import_status)
        self.open_imported = QPushButton("Open the imported experiment")
        self.open_imported.setVisible(False)
        self.open_imported.clicked.connect(self._open_imported)
        layout.addWidget(self.open_imported)
        self.tabs.addTab(page, PAGES[5])

    def _browse_into(self, field: QLineEdit, title: str) -> None:
        path = QFileDialog.getExistingDirectory(self, title)
        if path:
            field.setText(path)

    def check_results(self) -> None:
        from cellquant.hpc.import_results import ImportFailed, preview_import

        package = self.import_package.text().strip()
        results = self.import_results_field.text().strip()
        if not package or not results:
            self.import_status.setText("<p style='color:#c0392b'>Choose the prepared package and the downloaded run folder.</p>")
            return
        self.import_button.setEnabled(False)

        def work():
            try:
                return preview_import(package, results)
            except ImportFailed as exc:
                return exc

        self.shell._start_job(work, self._show_preview)

    def _show_preview(self, outcome) -> None:
        from cellquant.hpc.import_results import ImportFailed

        self.results_table.setRowCount(0)
        if isinstance(outcome, ImportFailed):
            self.preview = None
            self.import_status.setText(f"<p style='color:#c0392b'>{outcome}</p>" + _issues_html(outcome.issues))
            self.partial.setVisible(False)
            return
        self.preview = outcome
        for row in outcome.rows:
            index = self.results_table.rowCount()
            self.results_table.insertRow(index)
            for column, value in enumerate((row.acquisition_id, row.sample_name, row.state, row.message or "; ".join(row.warnings[:1]))):
                self.results_table.setItem(index, column, _read_only(value))
        complete = outcome.complete
        self.partial.setVisible(not complete)
        self.partial.setChecked(False)
        self.import_status.setText(
            f"<p>{outcome.status_text()}</p>"
            + ("" if complete else "<p style='color:#9a7d0a'>Not every image finished. Tick 'Import completed results only' to import the finished ones; the others are listed without results.</p>")
        )
        self._update_import_button()

    def _update_import_button(self) -> None:
        preview = self.preview
        allowed = preview is not None and preview.importable_count > 0 and (preview.complete or self.partial.isChecked())
        self.import_button.setEnabled(bool(allowed))

    def run_import(self) -> None:
        from cellquant.hpc.import_results import ImportFailed, import_results
        from cellquant.progress import AnalysisCancelled

        destination = self.import_destination.text().strip()
        preview = self.preview
        if preview is None or not destination:
            self.import_status.setText("<p style='color:#c0392b'>Choose a new, empty folder for the imported experiment.</p>")
            return
        package = self.import_package.text().strip()
        results = self.import_results_field.text().strip()
        partial = self.partial.isChecked()

        def work():
            try:
                return import_results(package, results, destination, allow_partial=partial, preview=preview)
            except (ImportFailed, AnalysisCancelled, OSError) as exc:
                return exc

        self.import_button.setEnabled(False)
        self.shell._start_job(work, self._imported)

    def _imported(self, outcome) -> None:
        from cellquant.hpc.import_results import ImportFailed

        if isinstance(outcome, ImportFailed):
            self.import_status.setText(f"<p style='color:#c0392b'>{outcome}</p>" + _issues_html(outcome.issues))
            self._update_import_button()
            return
        if isinstance(outcome, Exception):
            self.import_status.setText(f"<p style='color:#c0392b'>The import stopped: {outcome}. Nothing was published.</p>")
            self._update_import_button()
            return
        self.imported_to = outcome.destination
        self.import_status.setText(f"<p style='color:#2e8b57'>{outcome.summary_text()}</p>")
        self.open_imported.setVisible(True)

    def _open_imported(self) -> None:
        if self.imported_to is not None:
            self.shell.open_experiment(self.imported_to)
            self.shell.go_to_step(4)


def summary_json(panel: HpcPanel) -> str:
    """For tests and support: what the panel currently holds."""

    return json.dumps(
        {
            "selected": panel.selected_ids(),
            "profile": str(panel.profile.path) if panel.profile else None,
            "package": str(panel.package_dir) if panel.package_dir else None,
        }
    )
