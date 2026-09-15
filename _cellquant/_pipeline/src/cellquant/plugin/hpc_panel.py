"""HPC prep wizard: select → segment settings → profile → export / import."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Empty, SimpleQueue

from qtpy import QtCore, QtWidgets as Q

from cellquant.config import RunConfig, load_config
from cellquant.contracts import MutableCancellationToken, PipelineCancelled, PipelineEvent
from cellquant.hpc.acquisitions import (
    AcquisitionRef,
    expand_acquisitions,
    with_inclusion,
    with_segment_channels,
)
from cellquant.hpc.export import prepare_bundle
from cellquant.hpc.import_results import import_hpc_results
from cellquant.hpc.cluster_profiles import (
    list_profile_ids,
    load_profile,
    resolve_capabilities,
    validate_user_profile_fields,
)
from cellquant.hpc.contract import DEFAULT_PROFILE_ID, matrix_status
from cellquant.hpc.templates import (
    UserClusterSettings,
    jobcomposer_instructions,
    submission_command,
    transfer_instructions,
)
from cellquant.io import FILE_TYPE_PRESETS
from cellquant.plugin.help_text import tip
from cellquant.survey import default_template_config_path, suggest_segmentation_channel


def _set_tip(widget, key: str) -> None:
    widget.setToolTip(tip(key))


class HpcPrepPanel(Q.QWidget):
    """Prepare portable Alpine packages without loading Cellpose locally."""

    def __init__(self, viewer, controller, segment_options):
        super().__init__()
        self.viewer = viewer
        self.controller = controller
        self.segment_options = segment_options
        self.acquisitions: tuple[AcquisitionRef, ...] = ()
        self.included: set[str] = set()
        self.layout_channels: dict[str, int] = {}
        self._last_bundle: Path | None = None
        self._events: SimpleQueue = SimpleQueue()
        self.cancel_token = MutableCancellationToken()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cellquant-hpc")
        self.future = None
        self.profile = load_profile(DEFAULT_PROFILE_ID)
        self.capabilities = resolve_capabilities(self.profile)
        self._segment_host = None  # filled when parent rebinds options

        root = Q.QVBoxLayout(self)
        help_text = Q.QLabel(
            "HPC prep builds a transfer package for CU Alpine. Segmentation runs on the "
            "cluster GPU — this laptop does not need a GPU or a loaded Cellpose model.\n\n"
            "What goes where:\n"
            "• This laptop — pick input images, choose settings, and write the export package.\n"
            "• Alpine project root — durable folder for the uploaded package and final results.\n"
            "• Alpine scratch root — temporary job workspace (staging/compute only).\n"
            "• Alpine environment — preinstalled CellQuant/Python on the cluster "
            "(not this laptop’s CellQuant folder).\n\n"
            "Exporting here only creates the package. It does not submit or verify the cluster job."
        )
        help_text.setWordWrap(True)
        _set_tip(help_text, "hpc_overview")
        root.addWidget(help_text)

        self.steps = Q.QTabWidget()
        root.addWidget(self.steps)
        self.steps.addTab(self._build_select(), "1. Select images")
        self.steps.addTab(self._build_segment(), "2. Segmentation")
        self.steps.addTab(self._build_profile(), "3. Alpine paths & job")
        self.steps.addTab(self._build_export(), "4. Export & import")
        for index in range(1, self.steps.count()):
            self.steps.setTabEnabled(index, False)

        self.status = Q.QLabel(self.capabilities.summary)
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        root.addWidget(self.status)

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        self.destroyed.connect(lambda: self.pool.shutdown(wait=False, cancel_futures=True))

    def guard(self, fn):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            status = getattr(self, "status", None)
            if status is not None:
                status.setText(str(exc))

    def _buttons(self, layout, actions):
        row = Q.QHBoxLayout()
        layout.addLayout(row)
        for title, fn in actions:
            button = Q.QPushButton(title)
            row.addWidget(button)
            button.clicked.connect(lambda checked=False, f=fn: self.guard(f))
        return row

    def _build_select(self) -> Q.QWidget:
        page = Q.QWidget()
        layout = Q.QVBoxLayout(page)
        intro = Q.QLabel(
            "Step 1 — choose the input images that will be copied into the transfer package "
            "and segmented on Alpine. This is not the Alpine project/scratch path and not "
            "your CellQuant install location."
        )
        intro.setWordWrap(True)
        _set_tip(intro, "hpc_select_images")
        layout.addWidget(intro)
        form = Q.QFormLayout()
        layout.addLayout(form)
        self.input_edit = Q.QLineEdit()
        self.input_edit.setPlaceholderText("Local folder or file list on this laptop")
        _set_tip(self.input_edit, "hpc_select_images")
        browse = Q.QPushButton("Browse folder…")
        browse.clicked.connect(lambda: self.guard(self.choose_folder))
        add_files = Q.QPushButton("Add files…")
        add_files.clicked.connect(lambda: self.guard(self.choose_files))
        input_row = Q.QHBoxLayout()
        input_row.addWidget(self.input_edit)
        input_row.addWidget(browse)
        input_row.addWidget(add_files)
        form.addRow("Input images (this laptop)", input_row)

        self.recursive = Q.QCheckBox("Include subfolders")
        self.recursive.setChecked(True)
        form.addRow("", self.recursive)
        self.file_type = Q.QComboBox()
        for key in ("all", "tiff", "nd2"):
            self.file_type.addItem(key, key)
        self.file_type.setCurrentIndex(0)
        form.addRow("File type filter", self.file_type)

        self._buttons(
            layout,
            [
                ("Survey acquisitions", self.survey),
                ("Include all", self.include_all),
                ("Include none", self.include_none),
            ],
        )
        self.acq_table = Q.QTreeWidget()
        self.acq_table.setHeaderLabels(
            ["Include", "Source", "Series", "Pos", "Shape", "Channels", "Spacing", "Status"]
        )
        self.acq_table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.acq_table)

        layout_box = Q.QGroupBox(
            "Segmentation channel by layout (which fluorescence channel Cellpose uses)"
        )
        layout.addWidget(layout_box)
        self.layout_form = Q.QFormLayout(layout_box)
        self._layout_widgets: dict[str, Q.QComboBox] = {}
        self.select_next = Q.QPushButton("Next")
        self.select_next.setObjectName("select_next")
        self.select_next.clicked.connect(lambda: self.guard(self._next_from_select))
        layout.addWidget(self.select_next)
        return page

    def _build_segment(self) -> Q.QWidget:
        page = Q.QWidget()
        layout = Q.QVBoxLayout(page)
        note = Q.QLabel(
            "Step 2 — choose how Alpine should segment those images (engine, mode, diameter). "
            "Capabilities come from the GPU profile on the next tab, not from this laptop’s "
            "CUDA. Unsupported combinations are rejected with a clear reason — nothing is "
            "silently substituted. Cellpose still runs on the cluster, not here."
        )
        note.setWordWrap(True)
        _set_tip(note, "hpc_segmentation")
        layout.addWidget(note)
        caps = Q.QLabel(self.capabilities.summary)
        caps.setWordWrap(True)
        layout.addWidget(caps)
        # Reuse the shared segment options panel hosted by the parent widget.
        layout.addWidget(self.segment_options)
        refresh = Q.QPushButton("Refresh target capabilities")
        refresh.clicked.connect(lambda: self.guard(self._refresh_caps))
        layout.addWidget(refresh)
        self.segment_next = Q.QPushButton("Next")
        self.segment_next.setObjectName("segment_next")
        self.segment_next.clicked.connect(lambda: self.guard(self._next_from_segment))
        layout.addWidget(self.segment_next)
        return page

    def _build_profile(self) -> Q.QWidget:
        page = Q.QWidget()
        layout = Q.QVBoxLayout(page)
        intro = Q.QLabel(
            "Step 3 — tell CellQuant where things live on Alpine and how to request a GPU job. "
            "Replace USER with your Alpine username in every path.\n\n"
            "Path roles (all on Alpine, not this laptop):\n"
            "• Project root — durable storage. Upload the package here; finished outputs go under "
            "<project root>/results/<package name>/.\n"
            "• Scratch root — temporary fast workspace while the job runs (staging + compute). "
            "Not where you keep long-term results.\n"
            "• CellQuant env on Alpine — preinstalled Python env with CellQuant/Cellpose/PyTorch "
            "(must have bin/python). Not your laptop CellQuant folder and not the export package."
        )
        intro.setWordWrap(True)
        _set_tip(intro, "hpc_alpine_paths")
        layout.addWidget(intro)
        form = Q.QFormLayout()
        layout.addLayout(form)
        self.profile_combo = Q.QComboBox()
        for profile_id in list_profile_ids():
            option = load_profile(profile_id)
            self.profile_combo.addItem(option.display_name, profile_id)
        idx = self.profile_combo.findData(self.profile.profile_id)
        if idx >= 0:
            self.profile_combo.setCurrentIndex(idx)
        self.profile_combo.currentIndexChanged.connect(
            lambda *_: self.guard(self._on_profile_changed)
        )
        _set_tip(self.profile_combo, "hpc_gpu_profile")
        form.addRow("GPU type (Alpine partition)", self.profile_combo)
        self.profile_detail = Q.QLabel()
        self.profile_detail.setWordWrap(True)
        form.addRow("Profile details", self.profile_detail)
        self.account_edit = Q.QLineEdit()
        self.account_edit.setPlaceholderText(
            "Slurm billing account, e.g. amc-general — not your login email"
        )
        _set_tip(self.account_edit, "hpc_account")
        form.addRow("Slurm account (billing allocation)", self.account_edit)
        self.qos = Q.QComboBox()
        _set_tip(self.qos, "hpc_qos")
        form.addRow("QoS (queue / priority)", self.qos)
        self.gres = Q.QComboBox()
        _set_tip(self.gres, "hpc_gres")
        form.addRow("GPU request (GRES)", self.gres)
        self.walltime = Q.QLineEdit(self.profile.default_walltime)
        self.walltime.setPlaceholderText("HH:MM:SS — maximum job runtime before Slurm stops it")
        _set_tip(self.walltime, "hpc_walltime")
        form.addRow("Max runtime (HH:MM:SS)", self.walltime)
        self.project_root = Q.QLineEdit(self.profile.project_root_hint.replace("${USER}", "USER"))
        self.project_root.setPlaceholderText(
            "/projects/<AlpineUser>/cellquant — packages + final results"
        )
        _set_tip(self.project_root, "hpc_project_root")
        form.addRow("Project root (packages + results)", self.project_root)
        self.scratch_root = Q.QLineEdit(self.profile.scratch_root_template.replace("${USER}", "USER"))
        self.scratch_root.setPlaceholderText(
            "/scratch/alpine/<AlpineUser>/cellquant — temporary job workspace"
        )
        _set_tip(self.scratch_root, "hpc_scratch_root")
        form.addRow("Scratch root (job temp workspace)", self.scratch_root)
        self.env_location = Q.QLineEdit("/projects/USER/cellquant/envs/cellquant-hpc")
        self.env_location.setPlaceholderText(
            "/projects/<AlpineUser>/cellquant/envs/cellquant-hpc — cluster Python env"
        )
        _set_tip(self.env_location, "hpc_env_location")
        form.addRow("CellQuant env on Alpine", self.env_location)
        self.email = Q.QLineEdit()
        self.email.setPlaceholderText("optional — leave blank for no email")
        _set_tip(self.email, "hpc_email")
        form.addRow("Email when job ends (optional)", self.email)
        self.summary = Q.QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        for widget in (
            self.account_edit,
            self.qos,
            self.gres,
            self.walltime,
            self.project_root,
            self.scratch_root,
            self.env_location,
            self.email,
        ):
            if isinstance(widget, Q.QComboBox):
                widget.currentIndexChanged.connect(lambda *_: self.guard(self._refresh_summary))
            else:
                widget.textChanged.connect(lambda *_: self.guard(self._refresh_summary))
        self._reload_profile_resource_fields()
        self._refresh_summary()
        self.profile_next = Q.QPushButton("Next")
        self.profile_next.setObjectName("profile_next")
        self.profile_next.clicked.connect(lambda: self.guard(self._next_from_profile))
        layout.addWidget(self.profile_next)
        return page

    def _on_profile_changed(self):
        profile_id = self.profile_combo.currentData()
        self.profile = load_profile(str(profile_id))
        self.capabilities = resolve_capabilities(self.profile)
        self.status.setText(self.capabilities.summary)
        self._reload_profile_resource_fields()
        # Refresh env hint from profile notes when switching hardware.
        env_name = self.profile.environment_spec.get("name") or "cellquant-hpc"
        if "cellquant/envs/" in self.env_location.text().replace("\\", "/"):
            self.env_location.setText(f"/projects/USER/cellquant/envs/{env_name}")
        self._refresh_caps()
        self._update_preflight()
        self._refresh_summary()

    def _reload_profile_resource_fields(self):
        self.qos.blockSignals(True)
        self.gres.blockSignals(True)
        self.qos.clear()
        for value in self.profile.qos_choices:
            self.qos.addItem(value, value)
        self.qos.setCurrentText(self.profile.default_qos)
        self.gres.clear()
        for value in self.profile.gres_choices:
            self.gres.addItem(value, value)
        self.gres.setCurrentText(self.profile.default_gres)
        self.walltime.setText(self.profile.default_walltime)
        self.qos.blockSignals(False)
        self.gres.blockSignals(False)
        docs = self.profile.documentation_checked_utc or "unknown"
        smoke = self.profile.smoke_verified_utc or "not yet"
        ready = "submit-ready" if self.profile.submit_ready else "NOT submit-ready (Requires validation)"
        vram = f"{self.profile.vram_gb} GB" if self.profile.vram_gb else "n/a"
        self.profile_detail.setText(
            f"{self.profile.partition} / {self.profile.default_gres} · VRAM {vram} · "
            f"{self.profile.architecture or 'arch n/a'} · {ready}. "
            f"Docs checked {docs}; smoke {smoke}."
        )

    def _build_export(self) -> Q.QWidget:
        page = Q.QWidget()
        layout = Q.QVBoxLayout(page)
        intro = Q.QLabel(
            "Step 4 — write the transfer package on this laptop, upload it to Alpine project "
            "root, submit the job there, then download results and import them here.\n\n"
            "Local export folder = where CellQuant creates the package on this computer "
            "(before Globus/scp upload). It is not the Alpine project root, scratch root, "
            "or CellQuant env path."
        )
        intro.setWordWrap(True)
        _set_tip(intro, "hpc_export")
        layout.addWidget(intro)
        form = Q.QFormLayout()
        layout.addLayout(form)
        self.output_edit = Q.QLineEdit()
        self.output_edit.setPlaceholderText("Folder on this laptop for the exported package")
        _set_tip(self.output_edit, "hpc_export")
        browse = Q.QPushButton("Browse…")
        browse.clicked.connect(lambda: self.guard(self.choose_output))
        out_row = Q.QHBoxLayout()
        out_row.addWidget(self.output_edit)
        out_row.addWidget(browse)
        form.addRow("Local export folder (this laptop)", out_row)

        self.preflight = Q.QLabel(
            "Survey images and fill Alpine paths/job settings before exporting."
        )
        self.preflight.setWordWrap(True)
        layout.addWidget(self.preflight)

        self._buttons(
            layout,
            [
                ("Export package", self.export_package),
                ("Cancel export", self.cancel_export),
            ],
        )

        self.result_box = Q.QGroupBox("Package ready — next: upload to Alpine, then submit")
        self.result_box.setVisible(False)
        result_layout = Q.QVBoxLayout(self.result_box)
        self.result_path = Q.QLabel()
        self.result_path.setWordWrap(True)
        self.result_path.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        result_layout.addWidget(self.result_path)
        self._buttons(
            result_layout,
            [
                ("Open folder", self.open_bundle_folder),
                ("Copy transfer instructions", self.copy_transfer),
                ("Copy terminal submit", self.copy_submit),
                ("Copy Job Composer steps", self.copy_jobcomposer),
                ("Import HPC results…", self.import_results),
            ],
        )
        how = Q.QLabel(
            "After export:\n"
            "1. Upload this package folder into your Alpine project root "
            "(Globus preferred for large TIFFs; Open OnDemand Files is unsuitable above ~1 GB).\n"
            "2. On Alpine, run prepare_submission.sh inside the package, then submit with "
            "submit.sh (terminal) or paste run_jobcomposer.sbatch into Open OnDemand Job "
            "Composer — never paste submit.sh into Job Composer.\n"
            "3. When the job finishes, download the results folder "
            "(result_manifest.json + runs/) from "
            "<project root>/results/<package name>/ and use Import HPC results…"
        )
        how.setWordWrap(True)
        result_layout.addWidget(how)
        layout.addWidget(self.result_box)
        return page

    def _refresh_caps(self):
        self.capabilities = resolve_capabilities(self.profile)
        self.status.setText(self.capabilities.summary)

    def _refresh_summary(self):
        smoke = self.profile.smoke_verified_utc
        smoke_line = (
            f"Smoke verified {smoke}."
            if smoke
            else "No Alpine smoke verification recorded yet."
        )
        ready = (
            "Submit-ready."
            if self.profile.submit_ready
            else "Not submit-ready — Requires validation."
        )
        project = self.project_root.text().strip() or "(set project root)"
        scratch = self.scratch_root.text().strip() or "(set scratch root)"
        env = self.env_location.text().strip() or "(set CellQuant env path)"
        self.summary.setText(
            f"Where each Alpine path is used:\n"
            f"• Project root (upload package + keep final outputs): {project}\n"
            f"  Final results publish under {project.rstrip('/')}/results/<package name>/\n"
            f"• Scratch root (temporary job workspace only): {scratch}\n"
            f"• CellQuant env on Alpine (cluster Python with CellQuant/torch): {env}\n"
            f"{ready} {smoke_line} "
            "Prefer Globus for large TIFF uploads; Open OnDemand Files is unsuitable above ~1 GB."
        )

    def choose_folder(self):
        path = Q.QFileDialog.getExistingDirectory(self, "Select image folder")
        if path:
            self.input_edit.setText(path)

    def choose_files(self):
        paths, _ = Q.QFileDialog.getOpenFileNames(
            self,
            "Select image files",
            "",
            "Images (*.tif *.tiff *.nd2);;All files (*.*)",
        )
        if not paths:
            return
        # Survey only these files by writing a temporary listing via expand.
        self.input_edit.setText(";".join(paths))

    def choose_output(self):
        path = Q.QFileDialog.getExistingDirectory(
            self, "Select local export folder (this laptop)"
        )
        if path:
            self.output_edit.setText(path)

    def survey(self):
        raw = self.input_edit.text().strip()
        if not raw:
            raise ValueError("Choose a folder or files first")
        suffixes = list(FILE_TYPE_PRESETS[self.file_type.currentData()])
        parts = [p for p in raw.split(";") if p]
        if len(parts) == 1 and Path(parts[0]).is_dir():
            acq = expand_acquisitions(
                [],
                root=parts[0],
                recursive=self.recursive.isChecked(),
                suffixes=suffixes,
            )
        else:
            acq = expand_acquisitions(
                parts,
                recursive=self.recursive.isChecked(),
                suffixes=suffixes,
            )
        self.acquisitions = acq
        self.included = {item.acquisition_id for item in acq if item.error is None}
        self._rebuild_layout_widgets()
        self._fill_table()
        errors = sum(1 for item in acq if item.error)
        self.status.setText(
            f"Found {len(acq)} acquisition row(s); {errors} error(s). "
            "Exclude rows or fix time-series / calibration issues before export."
        )
        self._update_preflight()

    def _rebuild_layout_widgets(self):
        while self.layout_form.rowCount():
            self.layout_form.removeRow(0)
        self._layout_widgets.clear()
        layouts: dict[str, tuple[str, ...]] = {}
        for item in self.acquisitions:
            if item.layout_id and item.error is None:
                layouts[item.layout_id] = item.channel_names
        for layout_id, names in sorted(layouts.items()):
            combo = Q.QComboBox()
            for index, name in enumerate(names):
                combo.addItem(f"{index}: {name}", index)
            suggested, _ = suggest_segmentation_channel(names)
            if suggested is not None:
                combo.setCurrentIndex(suggested)
            self.layout_channels[layout_id] = int(combo.currentData())
            combo.currentIndexChanged.connect(
                lambda _i, lid=layout_id, c=combo: self._set_layout_channel(lid, c)
            )
            self._layout_widgets[layout_id] = combo
            self.layout_form.addRow(layout_id[:16] + "…", combo)

    def _set_layout_channel(self, layout_id: str, combo: Q.QComboBox):
        self.layout_channels[layout_id] = int(combo.currentData())
        self._update_preflight()

    def _fill_table(self):
        self.acq_table.blockSignals(True)
        self.acq_table.clear()
        for item in self.acquisitions:
            spacing = item.spacing_um
            spacing_txt = (
                "ok"
                if spacing and all(v is not None and float(v) > 0 for v in spacing)
                else "missing"
            )
            status = item.error or ("included" if item.acquisition_id in self.included else "excluded")
            row = Q.QTreeWidgetItem(
                [
                    "",
                    item.relative_source,
                    str(item.series),
                    str(item.position),
                    "×".join(str(v) for v in item.shape) if item.shape else "",
                    ",".join(item.channel_names),
                    spacing_txt,
                    status,
                ]
            )
            row.setFlags(row.flags() | QtCore.Qt.ItemIsUserCheckable)
            row.setCheckState(
                0,
                QtCore.Qt.Checked
                if item.acquisition_id in self.included and item.error is None
                else QtCore.Qt.Unchecked,
            )
            row.setData(0, QtCore.Qt.UserRole, item.acquisition_id)
            if item.error:
                row.setDisabled(True)
            self.acq_table.addTopLevelItem(row)
        self.acq_table.blockSignals(False)

    def _on_item_changed(self, item: Q.QTreeWidgetItem, column: int):
        if column != 0:
            return
        acquisition_id = item.data(0, QtCore.Qt.UserRole)
        if item.checkState(0) == QtCore.Qt.Checked:
            self.included.add(acquisition_id)
        else:
            self.included.discard(acquisition_id)
        self._update_preflight()

    def include_all(self):
        self.included = {
            item.acquisition_id for item in self.acquisitions if item.error is None
        }
        self._fill_table()
        self._update_preflight()

    def include_none(self):
        self.included = set()
        self._fill_table()
        self._update_preflight()

    def _user_settings(self) -> UserClusterSettings:
        return UserClusterSettings(
            account=self.account_edit.text().strip() or None,
            qos=self.qos.currentData(),
            gres=self.gres.currentData(),
            walltime=self.walltime.text().strip(),
            project_root=self.project_root.text().strip(),
            scratch_root=self.scratch_root.text().strip(),
            env_location=self.env_location.text().strip(),
            email=self.email.text().strip() or None,
        )

    def _segment_config(self) -> RunConfig:
        base = self.controller.config or load_config(default_template_config_path())
        overrides = self.segment_options.cellquant_overrides()
        raw = dict(base.raw)
        segment = dict(raw["segment"])
        for key, value in overrides.items():
            if key in segment:
                segment[key] = value
        # Force cluster device expectations from profile.
        if self.profile.require_gpu:
            segment["device"] = "cuda"
            segment["allow_cpu_fallback"] = False
        raw["segment"] = segment
        return RunConfig(raw)

    def _select_page_state(self) -> tuple[list[str], list[AcquisitionRef]]:
        selected = [
            item
            for item in self.acquisitions
            if item.acquisition_id in self.included and item.error is None
        ]
        errors: list[str] = []
        if not self.acquisitions:
            errors.append("Survey acquisitions before continuing")
        elif not selected:
            errors.append("Include at least one acquisition without errors")
        missing_layouts = sorted(
            {
                item.layout_id
                for item in selected
                if item.layout_id and item.layout_id not in self.layout_channels
            }
        )
        if missing_layouts:
            errors.extend(
                f"Choose a segmentation channel for layout {layout_id}"
                for layout_id in missing_layouts
            )
        return errors, selected

    def _segment_page_state(self) -> tuple[list[str], RunConfig | None]:
        try:
            config = self._segment_config()
            segment = config.raw["segment"]
            status, reason = matrix_status(
                str(segment["engine"]),
                str(segment["mode"]),
                self.profile.profile_id,
            )
            if status != "enabled":
                return [reason], config
            return [], config
        except Exception as exc:  # noqa: BLE001
            return [f"Fix segmentation settings: {exc}"], None

    def _profile_page_errors(self) -> list[str]:
        settings = self._user_settings()
        return validate_user_profile_fields(
            self.profile,
            account=settings.account,
            qos=settings.qos,
            gres=settings.gres,
            walltime=settings.walltime,
            project_root=settings.project_root,
            scratch_root=settings.scratch_root,
            env_location=settings.env_location,
            email=settings.email,
        )

    def _advance(self, current_index: int, errors: list[str]):
        if errors:
            raise ValueError("; ".join(errors))
        next_index = current_index + 1
        self.steps.setTabEnabled(next_index, True)
        self.steps.setCurrentIndex(next_index)
        self.status.setText(
            f"Step {current_index + 1} complete. Continue with step {next_index + 1}."
        )

    def _next_from_select(self):
        errors, _selected = self._select_page_state()
        self._advance(0, errors)

    def _next_from_segment(self):
        errors, _config = self._segment_page_state()
        self._advance(1, errors)

    def _next_from_profile(self):
        segment_errors, _config = self._segment_page_state()
        self._advance(2, segment_errors + self._profile_page_errors())

    def _update_preflight(self):
        select_errors, selected = self._select_page_state()
        segment_errors, config = self._segment_page_state()
        errors = select_errors + segment_errors + self._profile_page_errors()
        size_note = "Transfer size: not yet estimated (lazy sources are not materialized for sizing)."
        text = (
            f"{len(selected)} acquisition(s) selected for the package. "
            "Export keeps all channels on the source grid (lossless). "
            f"Cluster backend: cellquant_core / {self.profile.profile_id}. {size_note}"
        )
        if errors:
            text += "\nBlocking: " + "; ".join(errors)
        else:
            text += "\nReady to export the package on this laptop."
        self.preflight.setText(text)
        return errors, selected, config

    def export_package(self):
        errors, selected, config = self._update_preflight()
        if errors:
            raise ValueError("; ".join(errors))
        if config is None:
            raise ValueError("invalid segmentation configuration")
        output = self.output_edit.text().strip()
        if not output:
            raise ValueError(
                "Choose a local export folder on this laptop "
                "(where the package will be written before upload)"
            )
        settings = self._user_settings()
        acquisitions = with_inclusion(self.acquisitions, self.included)
        acquisitions = with_segment_channels(acquisitions, self.layout_channels)
        self.cancel_token = MutableCancellationToken()
        self.result_box.setVisible(False)
        self.status.setText("Exporting HPC package…")

        def emit(event: PipelineEvent):
            self._events.put(event)

        def work():
            return prepare_bundle(
                acquisitions,
                output,
                config,
                self.profile,
                settings,
                cancel=self.cancel_token,
                events=emit,
            )

        self.future = self.pool.submit(work)

    def cancel_export(self):
        self.cancel_token.cancel()
        self.status.setText("Cancel requested…")

    def poll(self):
        while True:
            try:
                event = self._events.get_nowait()
            except Empty:
                break
            if event.kind == "progress" and event.current is not None:
                self.status.setText(
                    f"Exporting {event.current}/{event.total}: "
                    f"{(event.details or {}).get('relative_source', '')}"
                )
        if self.future is None or not self.future.done():
            return
        future = self.future
        self.future = None
        try:
            result = future.result()
        except PipelineCancelled:
            self.status.setText("Export cancelled — package left incomplete / not submittable.")
            return
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"Export failed: {exc}")
            return
        if not result.ready:
            self.status.setText(
                f"Incomplete package (not submittable): {result.incomplete_reason}"
            )
            return
        self._last_bundle = result.bundle_dir
        self.result_path.setText(str(result.bundle_dir))
        self.result_box.setVisible(True)
        self.status.setText(
            f"Package ready for transfer — {result.acquisition_count} acquisition(s) at "
            f"{result.bundle_dir}"
        )

    def open_bundle_folder(self):
        if self._last_bundle is None:
            raise ValueError("No package exported yet")
        path = str(self._last_bundle)
        if sys.platform.startswith("win"):
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])  # noqa: S603
        else:
            subprocess.Popen(["xdg-open", path])  # noqa: S603

    def copy_transfer(self):
        if self._last_bundle is None:
            raise ValueError("No package exported yet")
        max_bytes = None
        inputs = self._last_bundle / "inputs"
        if inputs.is_dir():
            sizes = [p.stat().st_size for p in inputs.glob("*.tif")]
            max_bytes = max(sizes) if sizes else None
        text = transfer_instructions(
            local_bundle=self._last_bundle,
            remote_parent=self.project_root.text().strip(),
            max_input_bytes=max_bytes,
        )
        Q.QApplication.clipboard().setText(text)
        self.status.setText("Transfer instructions copied (Globus-oriented).")

    def copy_submit(self):
        if self._last_bundle is None:
            raise ValueError("No package exported yet")
        remote = (
            f"{self.project_root.text().strip().rstrip('/')}/{self._last_bundle.name}"
        )
        Q.QApplication.clipboard().setText(submission_command(remote_bundle=remote))
        self.status.setText("Terminal submission command copied.")

    def copy_jobcomposer(self):
        if self._last_bundle is None:
            raise ValueError("No package exported yet")
        remote = (
            f"{self.project_root.text().strip().rstrip('/')}/{self._last_bundle.name}"
        )
        Q.QApplication.clipboard().setText(jobcomposer_instructions(remote_bundle=remote))
        self.status.setText("Open OnDemand Job Composer steps copied.")

    def import_results(self):
        result_dir = Q.QFileDialog.getExistingDirectory(self, "Select HPC result folder")
        if not result_dir:
            return
        dest = Q.QFileDialog.getExistingDirectory(self, "Select local import destination")
        if not dest:
            return
        summary = import_hpc_results(result_dir, dest)
        self.status.setText(
            f"Imported {summary.complete} complete, {summary.failed} failed, "
            f"{summary.unfinished} unfinished → {summary.destination}"
        )


__all__ = ["HpcPrepPanel"]
