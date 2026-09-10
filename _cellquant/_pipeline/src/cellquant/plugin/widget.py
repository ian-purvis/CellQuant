"""npe2/magicgui widget factory for the CellQuant plugin."""

from pathlib import Path

from cellquant.config import load_config
from cellquant.io import resolve_file_type_preset
from cellquant.survey import default_template_config_path, materialize_run_config

from .capabilities import RuntimeCapabilities, detect_runtime_capabilities
from .combo_scroll import install_scrollability_controls, wrap_combo_with_scrollability
from .controller import PluginController
from .diameter import (
    DIAMETER_SHAPES_LAYER,
    bind_diameter_shapes_to_image,
    diameter_from_shapes_layer,
)
from .help_text import tip
from .messages import format_eta_seconds, open_path_in_os
from .paths import require_existing_dir, require_existing_file
from .slow_run import should_prompt_slow_run, slow_run_prompt_text


SEGMENTATION_CHANNEL_WIDGET_OPTIONS = {
    "widget_type": "ComboBox",
    "choices": [],
    "label": "Segmentation channel",
}

FILE_TYPE_CHOICES = (
    "All supported",
    "TIFF (.tif/.tiff)",
    "ND2 (.nd2)",
)

_FILE_TYPE_TO_PRESET = {
    "All supported": "all",
    "TIFF (.tif/.tiff)": "tiff",
    "ND2 (.nd2)": "nd2",
}

SEGMENT_MODE_CHOICES = (
    ("3D volume — true volumetric objects", "volume_3d"),
    ("Linked 2D — planes linked through Z", "stitch_2d"),
    ("Single analysis plane — 2D areas", "single_plane_2d"),
    ("Maximum projection — 2D area overview", "max_projection_2d"),
)

# Guided chooser entries: (button label, mode id, consequence for novices).
GUIDED_MODE_CHOICES = (
    (
        "Quick 2D check",
        "single_plane_2d",
        "Segments one analysis plane. Objects are areas (µm²), not volumes. Fastest check.",
    ),
    (
        "2D planes linked through Z",
        "stitch_2d",
        "Segments each plane, then links objects through Z. Counts linked stacks, not true 3D voxels.",
    ),
    (
        "True 3D objects",
        "volume_3d",
        "Volumetric Cellpose. Objects are volumes (µm³). Heaviest on RAM/GPU.",
    ),
    (
        "Projection overview",
        "max_projection_2d",
        "Collapses Z then segments. Objects are areas; overlapping cells in Z may merge. Fast overview.",
    ),
)

DIAMETER_MODE_CHOICES = (
    ("Native — no rescale (recommended)", "native"),
    ("Manual — diameter in pixels", "manual"),
)

_MODE_LABEL_BY_ID = dict(SEGMENT_MODE_CHOICES)


def segmentation_channel_choices(image_layer):
    """Return friendly 1-based labels mapped to zero-based channel indices."""
    if image_layer is None:
        return []
    data = getattr(image_layer, "data", None)
    shape = tuple(getattr(data, "shape", ()))
    if len(shape) != 4:
        return []
    metadata = dict(getattr(image_layer, "metadata", {}) or {})
    names = tuple(metadata.get("channel_names", ()))
    if len(names) != shape[-1]:
        names = tuple(f"C{index + 1}" for index in range(shape[-1]))
    return [(f"{index + 1} — {name}", index) for index, name in enumerate(names)]


def bind_segmentation_channel_choices(function_widget):
    """Refresh a segmentation form's channel ComboBox when its Image changes."""

    def refresh(*_args):
        function_widget.channel_index.choices = segmentation_channel_choices(
            function_widget.image.value
        )

    function_widget.image.changed.connect(refresh)
    refresh()
    return refresh


def file_type_suffixes(label: str) -> tuple[str, ...]:
    """Map a napari batch UI label to concrete acquisition suffixes."""

    try:
        preset = _FILE_TYPE_TO_PRESET[label]
    except KeyError as exc:
        raise ValueError(f"unknown file type choice {label!r}") from exc
    return resolve_file_type_preset(preset)


def _set_tip(widget, key: str) -> None:
    widget.setToolTip(tip(key))


def _find_layer(viewer, name: str):
    layers = viewer.layers
    try:
        return layers[name]
    except (KeyError, TypeError, IndexError, ValueError):
        return next((layer for layer in layers if getattr(layer, "name", None) == name), None)


def make_segment_options_panel(
    viewer,
    QtWidgets,
    QtCore,
    *,
    capabilities: RuntimeCapabilities | None = None,
    on_status=None,
    get_image_start_dir=None,
    open_image_fn=None,
):
    """Shared mode/device/diameter controls for single-image and batch pages."""

    QWidget = QtWidgets.QWidget
    QVBoxLayout = QtWidgets.QVBoxLayout
    QHBoxLayout = QtWidgets.QHBoxLayout
    QLabel = QtWidgets.QLabel
    QComboBox = QtWidgets.QComboBox
    QCheckBox = QtWidgets.QCheckBox
    QDoubleSpinBox = QtWidgets.QDoubleSpinBox
    QSpinBox = QtWidgets.QSpinBox
    QPushButton = QtWidgets.QPushButton
    QFileDialog = QtWidgets.QFileDialog

    caps = capabilities or detect_runtime_capabilities()

    panel = QWidget()
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(0, 4, 0, 4)

    note = QLabel(caps.summary)
    note.setWordWrap(True)
    _set_tip(note, "cellpose_engine")
    layout.addWidget(note)

    tip_line = QLabel(
        "Hover any control for help. Diameter Native means no manual rescale (not automatic "
        "size estimation) — use Manual and measure a line when v3 needs a typical width."
    )
    tip_line.setWordWrap(True)
    layout.addWidget(tip_line)

    def _labeled_row(label_text: str, tooltip_key: str, field, *, scrollability: bool = False):
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(label_text)
        _set_tip(label, tooltip_key)
        _set_tip(field, tooltip_key)
        row_layout.addWidget(label)
        if scrollability and isinstance(field, QComboBox):
            from .combo_scroll import wrap_combo_with_scrollability

            row_layout.addWidget(
                wrap_combo_with_scrollability(
                    QtWidgets, field, tip_text=tip("scrollability")
                ),
                1,
            )
        else:
            row_layout.addWidget(field, 1)
        return row

    engine = QComboBox()
    for option in caps.available_engines:
        engine.addItem(option.label, option.engine_id)
    if engine.count() == 0:
        engine.addItem("No compatible Cellpose engine detected", None)
        engine.setEnabled(False)
    layout.addWidget(_labeled_row("Cellpose engine", "cellpose_engine", engine, scrollability=True))

    # Show unavailable engines as a short note so users know what was filtered out.
    blocked = [opt for opt in caps.engines if not opt.available and opt.unavailable_reason]
    if blocked:
        blocked_note = QLabel(
            "Not offered here: "
            + "; ".join(f"{opt.label} ({opt.unavailable_reason})" for opt in blocked)
        )
        blocked_note.setWordWrap(True)
        layout.addWidget(blocked_note)

    mode = QComboBox()
    for label, value in SEGMENT_MODE_CHOICES:
        mode.addItem(label, value)
    layout.addWidget(_labeled_row("Segmentation mode", "segment_mode", mode, scrollability=True))

    guided = QComboBox()
    guided.addItem("Guided setup — pick a goal (optional)", None)
    for label, value, _consequence in GUIDED_MODE_CHOICES:
        guided.addItem(label, value)
    layout.addWidget(_labeled_row("What do you want?", "guided_mode", guided, scrollability=True))
    mode_consequence = QLabel("")
    mode_consequence.setWordWrap(True)
    mode_consequence.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
    layout.addWidget(mode_consequence)

    def _apply_guided_mode(*_args):
        choice = guided.currentData()
        consequence = ""
        for _label, value, text in GUIDED_MODE_CHOICES:
            if value == choice:
                consequence = text
                break
        mode_consequence.setText(consequence)
        if choice is None:
            return
        for index in range(mode.count()):
            if mode.itemData(index) == choice:
                mode.blockSignals(True)
                mode.setCurrentIndex(index)
                mode.blockSignals(False)
                break

    guided.currentIndexChanged.connect(_apply_guided_mode)

    device = QComboBox()
    for option in caps.available_devices:
        device.addItem(option.label, option.device_id)
    # Select detected default (Auto→CPU when CUDA PyTorch is not usable).
    default_device = caps.default_device_id
    for index in range(device.count()):
        if device.itemData(index) == default_device:
            device.setCurrentIndex(index)
            break
    layout.addWidget(_labeled_row("Device", "device", device, scrollability=True))

    blocked_devices = [
        opt for opt in caps.devices if not opt.available and opt.unavailable_reason
    ]
    if blocked_devices:
        device_note = QLabel(
            "GPU not selectable: "
            + "; ".join(
                f"{opt.label} — {opt.unavailable_reason}" for opt in blocked_devices
            )
        )
        device_note.setWordWrap(True)
        layout.addWidget(device_note)

    allow_cpu = QCheckBox("Allow CPU if CUDA unavailable")
    allow_cpu.setChecked(True)
    allow_cpu.setVisible(caps.cuda_available)
    _set_tip(allow_cpu, "allow_cpu_fallback")
    layout.addWidget(allow_cpu)

    diameter_mode = QComboBox()
    for label, value in DIAMETER_MODE_CHOICES:
        diameter_mode.addItem(label, value)
    layout.addWidget(_labeled_row("Diameter", "diameter_mode", diameter_mode, scrollability=True))

    diameter = QDoubleSpinBox()
    diameter.setRange(1.0, 10000.0)
    diameter.setDecimals(1)
    diameter.setValue(30.0)
    diameter_row = _labeled_row("Diameter (px)", "diameter_px", diameter)
    layout.addWidget(diameter_row)

    measure_row = QWidget()
    measure_layout = QHBoxLayout(measure_row)
    measure_layout.setContentsMargins(0, 0, 0, 0)
    open_measure = QPushButton("Open image to measure…")
    add_measure = QPushButton("Add measure layer")
    use_measure = QPushButton("Use drawn line")
    _set_tip(open_measure, "open_measure_image")
    _set_tip(add_measure, "measure_diameter")
    _set_tip(use_measure, "measure_diameter")
    measure_layout.addWidget(open_measure)
    measure_layout.addWidget(add_measure)
    measure_layout.addWidget(use_measure)
    layout.addWidget(measure_row)

    stitch = QDoubleSpinBox()
    stitch.setRange(0.0, 1.0)
    stitch.setDecimals(3)
    stitch.setSingleStep(0.05)
    stitch.setValue(0.25)
    stitch_row = _labeled_row("Stitch threshold", "stitch_threshold", stitch)
    layout.addWidget(stitch_row)

    z_index = QSpinBox()
    z_index.setRange(0, 100000)
    z_index.setValue(0)
    z_row = _labeled_row("Z index (single plane)", "z_index", z_index)
    layout.addWidget(z_row)

    preflight = QLabel("")
    preflight.setWordWrap(True)
    preflight.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
    preflight.setStyleSheet("color: palette(mid);")
    layout.addWidget(preflight)

    def sync_mode_fields(*_args):
        mode_value = mode.currentData()
        stitch_row.setVisible(mode_value == "stitch_2d")
        z_row.setVisible(mode_value == "single_plane_2d")

    def sync_diameter_fields(*_args):
        manual = diameter_mode.currentData() == "manual"
        diameter_row.setVisible(manual)
        measure_row.setVisible(manual)

    mode.currentIndexChanged.connect(sync_mode_fields)
    diameter_mode.currentIndexChanged.connect(sync_diameter_fields)
    sync_mode_fields()
    sync_diameter_fields()

    def open_image_to_measure():
        start = Path.home()
        if get_image_start_dir is not None:
            candidate = get_image_start_dir()
            if candidate is not None and Path(candidate).is_dir():
                start = Path(candidate)
        path, _ = QFileDialog.getOpenFileName(
            panel,
            "Choose an image to measure diameter",
            str(start),
            "Images (*.tif *.tiff *.nd2);;All files (*.*)",
        )
        if not path:
            return None
        if open_image_fn is None:
            raise RuntimeError("open_image_fn was not provided")
        open_image_fn(Path(path))
        if on_status is not None:
            on_status(
                f"Opened {Path(path).name}. Add a measure layer, draw a line across a typical "
                "nucleus, then click Use drawn line."
            )
        return Path(path)

    def add_measure_layer():
        existing = _find_layer(viewer, DIAMETER_SHAPES_LAYER)
        if existing is not None:
            viewer.layers.selection = [existing]
            if on_status is not None:
                on_status(
                    f"Use the line tool on '{DIAMETER_SHAPES_LAYER}', then click Use drawn line."
                )
            return existing
        layer = viewer.add_shapes(
            name=DIAMETER_SHAPES_LAYER,
            shape_type="line",
            edge_width=2,
            edge_color="cyan",
            face_color="transparent",
        )
        image = _find_layer(viewer, "CellQuant image")
        if image is None:
            # Prefer a displayed channel layer that carries spacing.
            for candidate in viewer.layers:
                if getattr(candidate, "metadata", {}).get("cellquant_display_channel"):
                    image = candidate
                    break
        bind_diameter_shapes_to_image(layer, image)
        try:
            layer.mode = "add_line"
        except Exception:  # noqa: BLE001 - mode strings vary slightly by napari version
            try:
                layer.mode = "add_path"
            except Exception:  # noqa: BLE001
                pass
        if on_status is not None:
            on_status(
                f"Draw one line across a typical nucleus on '{DIAMETER_SHAPES_LAYER}', "
                "then click Use drawn line."
            )
        return layer

    def use_drawn_line():
        layer = _find_layer(viewer, DIAMETER_SHAPES_LAYER)
        image = _find_layer(viewer, "CellQuant image")
        if image is None:
            for candidate in viewer.layers:
                if getattr(candidate, "metadata", {}).get("cellquant_display_channel"):
                    image = candidate
                    break
        bind_diameter_shapes_to_image(layer, image)
        length = diameter_from_shapes_layer(layer, image_layer=image)
        diameter_mode.setCurrentIndex(
            next(i for i in range(diameter_mode.count()) if diameter_mode.itemData(i) == "manual")
        )
        diameter.setValue(round(length, 1))
        if on_status is not None:
            on_status(f"Diameter set to {length:.1f} px from the drawn line.")
        return length

    open_measure.clicked.connect(lambda: _safe_ui(open_image_to_measure, on_status))
    add_measure.clicked.connect(lambda: _safe_ui(add_measure_layer, on_status))
    use_measure.clicked.connect(lambda: _safe_ui(use_drawn_line, on_status))

    def overrides() -> dict:
        mode_value = mode.currentData()
        engine_value = engine.currentData()
        if engine_value is None:
            raise ValueError(
                "No compatible Cellpose engine is available in this environment. "
                "Run Install CellQuant.bat (installs both v3 and v4), then use "
                "Open CellQuant.bat and pick the matching engine."
            )
        if diameter_mode.currentData() == "native":
            diameter_value = None
        else:
            diameter_value = float(diameter.value())
        payload = {
            "engine": str(engine_value),
            "mode": mode_value,
            "device": device.currentData(),
            "allow_cpu_fallback": bool(allow_cpu.isChecked()) if caps.cuda_available else True,
            "diameter_px": diameter_value,
        }
        if mode_value == "stitch_2d":
            payload["stitch_threshold"] = float(stitch.value())
        elif mode_value == "single_plane_2d":
            payload["z_index"] = int(z_index.value())
        from cellquant.segment import apply_engine_segment_defaults

        return apply_engine_segment_defaults(payload)

    def _engine_label() -> str:
        engine_id = engine.currentData()
        for option in caps.available_engines:
            if option.engine_id == engine_id:
                return option.label
        return str(engine_id)

    def refresh_preflight(
        *,
        channel_label: str | None = None,
        batch_file_count: int | None = None,
        image_layer=None,
        series: int | None = None,
        position: int | None = None,
    ) -> None:
        try:
            segment_overrides = overrides()
        except ValueError as exc:
            preflight.setText(f"Run preflight: {exc}")
            return
        lines = [
            f"Engine: {_engine_label()}",
            f"Model: {segment_overrides.get('model', '?')}",
            f"Device: {device.currentText()}",
        ]
        if caps.cuda_available:
            fallback = "yes" if allow_cpu.isChecked() else "no"
            lines.append(f"CPU if CUDA unavailable: {fallback}")
        mode_value = segment_overrides.get("mode")
        lines.append(f"Mode: {_MODE_LABEL_BY_ID.get(mode_value, mode_value)}")
        if mode_value == "single_plane_2d":
            lines.append(f"Analysis plane (Z index): {z_index.value()}")
        elif mode_value == "max_projection_2d":
            lines.append("Z: maximum projection (objects are areas; population changes)")
        elif mode_value == "stitch_2d":
            lines.append(f"Linked 2D stitch threshold: {stitch.value():.3f}")
        elif mode_value == "volume_3d":
            lines.append("Objects: 3D volumes (µm³)")
        if diameter_mode.currentData() == "native":
            lines.append("Diameter: Native (no rescale; not auto-size estimation)")
        else:
            lines.append(f"Diameter: {diameter.value():.1f} px")
        if channel_label:
            lines.append(f"Channel: {channel_label}")
        if series is not None:
            lines.append(f"Series: {series}")
        if position is not None:
            lines.append(f"Source position: {position}")
        metadata = dict(getattr(image_layer, "metadata", {}) or {}) if image_layer is not None else {}
        if metadata:
            shape = getattr(getattr(image_layer, "data", None), "shape", None)
            if shape is not None:
                lines.append(f"Image shape: {tuple(shape)}")
            spacing = metadata.get("spacing_um") or getattr(image_layer, "scale", None)
            if spacing is not None:
                lines.append(f"Spacing (µm): {tuple(float(v) for v in list(spacing)[:3])}")
            calibration = metadata.get("calibration_status") or metadata.get("spacing_source")
            if calibration:
                lines.append(f"Calibration: {calibration}")
            if "position" in metadata:
                lines.append(f"Stored source position: {metadata.get('position')}")
        if batch_file_count is not None:
            lines.append(f"Batch files: {batch_file_count}")
        try:
            from cellquant.plugin.resources import estimate_preparation, staging_cache_usage

            estimate = estimate_preparation(
                image=getattr(image_layer, "data", None) if image_layer is not None else None
            )
            lines.extend(estimate.as_preflight_lines())
            scratch, used = staging_cache_usage()
            if used:
                lines.append(f"Staging cache in use: {used / (1024 ** 2):.1f} MiB under {scratch}")
        except Exception as exc:  # noqa: BLE001
            lines.append(f"Resource estimate unavailable: {exc}")
        preflight.setText("\n".join(lines))

    for widget in (engine, mode, device, diameter_mode, guided):
        widget.currentIndexChanged.connect(lambda *_: refresh_preflight())
    diameter.valueChanged.connect(lambda *_: refresh_preflight())
    stitch.valueChanged.connect(lambda *_: refresh_preflight())
    z_index.valueChanged.connect(lambda *_: refresh_preflight())
    allow_cpu.toggled.connect(lambda *_: refresh_preflight())
    refresh_preflight()

    panel.cellquant_overrides = overrides
    panel.cellquant_refresh_preflight = refresh_preflight
    panel.cellquant_preflight = preflight
    panel.cellquant_capabilities = caps
    panel.cellquant_engine = engine
    panel.cellquant_mode = mode
    panel.cellquant_guided = guided
    panel.cellquant_device = device
    panel.cellquant_allow_cpu = allow_cpu
    panel.cellquant_diameter_mode = diameter_mode
    panel.cellquant_diameter = diameter
    return panel


def _safe_ui(action, on_status):
    try:
        return action()
    except Exception as exc:  # noqa: BLE001
        if on_status is not None:
            on_status(f"Diameter measure: {exc}")
        return None


def make_cellquant_widget(viewer=None):
    """Create the dock widget, importing GUI libraries only on demand."""
    import napari
    from magicgui import magicgui
    from napari.layers import Image, Labels
    from qtpy.QtCore import QEvent, QSize, QTimer, Qt
    from qtpy import QtCore, QtWidgets
    from qtpy.QtWidgets import (
        QCheckBox,
        QComboBox,
        QHBoxLayout,
        QLabel,
        QMessageBox,
        QProgressBar,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QStackedWidget,
        QVBoxLayout,
        QWidget,
    )
    from time import monotonic

    viewer = viewer or napari.current_viewer()
    if viewer is None:
        raise RuntimeError("CellQuant requires an active napari viewer")
    controller = PluginController(viewer)
    # Prefetch the bundled template so single-image runs need no config browse.
    controller.set_config(load_config(default_template_config_path()))

    # Napari's QtViewerDockWidget forces Preferred/Maximum size policy on the
    # docked widget, which blocks vertical growth. Undo that after parenting so
    # users can drag the dock taller (not only wider) while keeping scroll.
    class ExpandingScrollArea(QScrollArea):
        def sizeHint(self):
            return QSize(420, 520)

        def minimumSizeHint(self):
            return QSize(260, 160)

    class DockRoot(QWidget):
        def __init__(self):
            super().__init__()
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.setMinimumWidth(280)
            self.setMinimumHeight(200)

        def sizeHint(self):
            return QSize(440, 720)

        def minimumSizeHint(self):
            return QSize(280, 200)

        def event(self, event):
            result = super().event(event)
            # ParentChange/Show are enough to undo napari's Maximum size policy.
            # PolishFromParent is not available on all Qt bindings (e.g. some PyQt6).
            unlock_types = {QEvent.Type.ParentChange, QEvent.Type.Show}
            polish = getattr(QEvent.Type, "PolishFromParent", None)
            if polish is not None:
                unlock_types.add(polish)
            if event.type() in unlock_types:
                # Napari may re-apply Maximum after parenting; unlock a few times.
                for delay_ms in (0, 50, 250):
                    QTimer.singleShot(delay_ms, self._unlock_vertical_resize)
            return result

        def showEvent(self, event):
            super().showEvent(event)
            for delay_ms in (0, 50, 250):
                QTimer.singleShot(delay_ms, self._unlock_vertical_resize)
        def _unlock_vertical_resize(self):
            expanding = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.setSizePolicy(expanding)
            self.setMaximumHeight(16777215)
            parent = self.parentWidget()
            # Walk wrappers until the QDockWidget; napari may set Maximum on us
            # (and occasionally on a thin wrapper).
            while parent is not None:
                if parent.metaObject().className() in {
                    "QtViewerDockWidget",
                    "QDockWidget",
                } or "DockWidget" in type(parent).__name__:
                    break
                parent.setSizePolicy(expanding)
                parent.setMaximumHeight(16777215)
                parent = parent.parentWidget()

    # Expandable outer shell: scrollable body + sticky progress/status footer.
    root = DockRoot()
    root_layout = QVBoxLayout(root)
    root_layout.setContentsMargins(0, 0, 0, 0)
    root_layout.setSpacing(0)

    scroll = ExpandingScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
    scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    content = QWidget()
    content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    layout = QVBoxLayout(content)
    layout.setContentsMargins(6, 6, 6, 6)
    status = QLabel("Ready — hover any control for help.")
    status.setWordWrap(True)
    status.setTextInteractionFlags(Qt.TextSelectableByMouse)
    status.setMinimumHeight(72)
    status.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

    def set_status(text: str):
        controller.status_text = text
        status.setText(text)

    progress = QProgressBar()
    progress.setRange(0, 0)
    progress.setTextVisible(True)
    progress.setFormat("Working…")
    progress.hide()
    progress_meta = {"name": "", "status": "", "current": None, "total": None, "stage": ""}
    slow_watch = {
        "job_key": None,
        "prompted": False,
        "dialog_open": False,
        "unit_key": None,
        "unit_started": None,
        "last_finished_unit_s": None,
    }

    mode_row = QWidget()
    mode_layout = QHBoxLayout(mode_row)
    mode_layout.setContentsMargins(0, 0, 0, 0)
    mode_label = QLabel("Mode")
    _set_tip(mode_label, "workflow_mode")
    mode_layout.addWidget(mode_label)
    mode = QComboBox()
    mode.addItem("Single image", "single")
    mode.addItem("Batch folder", "batch")
    mode.addItem("Coexpression", "coexpression")
    _set_tip(mode, "workflow_mode")
    from .combo_scroll import wrap_combo_with_scrollability

    mode_layout.addWidget(
        wrap_combo_with_scrollability(QtWidgets, mode, tip_text=tip("scrollability")),
        1,
    )

    pages = QStackedWidget()
    pages.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    single_page = QWidget()
    single_layout = QVBoxLayout(single_page)
    single_layout.setContentsMargins(0, 0, 0, 0)
    batch_page = QWidget()
    batch_layout = QVBoxLayout(batch_page)
    batch_layout.setContentsMargins(0, 0, 0, 0)

    layout_rows: dict[str, dict] = {}
    batch_paths = {"input": None, "output": None, "recursive": True, "file_type": FILE_TYPE_CHOICES[2]}
    forms: dict[str, object] = {"survey": None}
    capabilities = detect_runtime_capabilities()
    set_status(capabilities.summary)

    def get_image_start_dir():
        if batch_paths["input"] is not None:
            return batch_paths["input"]
        survey = forms.get("survey")
        if survey is not None:
            try:
                value = survey.input_dir.value
                path = Path(value)
                if path.is_dir() and str(path) not in {".", ""}:
                    return path
            except Exception:  # noqa: BLE001
                pass
        return Path.home()

    def open_image_for_measure(path: Path):
        return controller.open_path(require_existing_file(path, what="image"))

    single_options = make_segment_options_panel(
        viewer,
        QtWidgets,
        QtCore,
        capabilities=capabilities,
        on_status=set_status,
        get_image_start_dir=get_image_start_dir,
        open_image_fn=open_image_for_measure,
    )
    batch_options = make_segment_options_panel(
        viewer,
        QtWidgets,
        QtCore,
        capabilities=capabilities,
        on_status=set_status,
        get_image_start_dir=get_image_start_dir,
        open_image_fn=open_image_for_measure,
    )

    def refresh_batch_preflight(*_args):
        file_count = 0
        survey = controller.survey_result
        if survey is not None:
            for layout_info in survey.layouts:
                widgets = layout_rows.get(layout_info.layout_id)
                if widgets is not None and widgets["include"].isChecked():
                    file_count += layout_info.file_count
        batch_options.cellquant_refresh_preflight(
            batch_file_count=file_count if file_count else None
        )

    def _run(action):
        try:
            return action()
        except Exception as exc:  # noqa: BLE001 - surface sync UI failures in-dock
            controller.report_exception(exc)
            return None

    @magicgui(call_button="Open lazily", path={"mode": "r"})
    def open_image(path: Path, series: int = 0, position: int = 0):
        def action():
            source = require_existing_file(path, what="image")
            return controller.open_path(source, series=series, position=position)

        return _run(action)

    @magicgui(
        call_button="Run Cellpose",
        channel_index=SEGMENTATION_CHANNEL_WIDGET_OPTIONS,
    )
    def run_segmentation(image: Image, channel_index: int):
        def action():
            refresh_single_preflight()
            if controller.config is None:
                controller.set_config(load_config(default_template_config_path()))
            return controller.segment(
                image,
                channel_index=channel_index,
                segment_overrides=single_options.cellquant_overrides(),
            )

        return _run(action)

    bind_segmentation_channel_choices(run_segmentation)

    def _channel_label_from_choices(channel_index: int) -> str:
        choices = dict(getattr(run_segmentation.channel_index, "choices", []) or [])
        return str(choices.get(channel_index, channel_index))

    def refresh_single_preflight(*_args):
        single_options.cellquant_refresh_preflight(
            channel_label=_channel_label_from_choices(run_segmentation.channel_index.value)
        )

    run_segmentation.channel_index.changed.connect(refresh_single_preflight)
    refresh_single_preflight()

    @magicgui(call_button="Measure edited labels + save", output_dir={"mode": "d"})
    def save_edited(labels: Labels, output_dir: Path):
        def action():
            destination = require_existing_dir(output_dir, what="output folder", create=True)
            if controller.config is None:
                config, path = materialize_run_config(
                    destination / "cellquant_run_config.yaml",
                    segment_channel=0,
                    segment_overrides=single_options.cellquant_overrides(),
                )
                controller.set_config(config)
                controller.last_config_path = path
            return controller.measure_and_save(destination, labels)

        return _run(action)

    @magicgui(
        call_button="Survey folder",
        input_dir={"mode": "d", "label": "Input folder"},
        output_dir={"mode": "d", "label": "Output folder"},
        file_type={"choices": list(FILE_TYPE_CHOICES), "label": "File type"},
    )
    def survey_batch(
        input_dir: Path,
        output_dir: Path,
        recursive: bool = True,
        file_type: str = "ND2 (.nd2)",
    ):
        def action():
            source = require_existing_dir(input_dir, what="input folder")
            destination = require_existing_dir(output_dir, what="output folder", create=True)
            batch_paths["input"] = source
            batch_paths["output"] = destination
            batch_paths["recursive"] = recursive
            batch_paths["file_type"] = file_type
            return controller.run_survey(
                source,
                destination,
                recursive=recursive,
                suffixes=file_type_suffixes(file_type),
                segment_overrides=batch_options.cellquant_overrides(),
            )

        return _run(action)

    forms["survey"] = survey_batch

    # magicgui tooltips (best-effort; widget attribute names are stable for these forms)
    for widget, key in (
        (open_image.path, "open_path"),
        (open_image.series, "series"),
        (open_image.position, "position"),
        (run_segmentation.image, "image_layer"),
        (run_segmentation.channel_index, "segmentation_channel"),
        (save_edited.labels, "image_layer"),
        (save_edited.output_dir, "output_dir"),
        (survey_batch.input_dir, "input_folder"),
        (survey_batch.output_dir, "batch_output_folder"),
        (survey_batch.recursive, "recursive"),
        (survey_batch.file_type, "file_type"),
    ):
        try:
            widget.tooltip = tip(key)
        except Exception:  # noqa: BLE001
            native = getattr(widget, "native", None)
            if native is not None:
                _set_tip(native, key)

    run_batch_button = QPushButton("Run batch")
    _set_tip(run_batch_button, "run_batch")

    def run_batch_clicked():
        def action():
            # Always read the live Output folder control. Caching only at Survey
            # time left later UI changes ignored and wrote into the previous run.
            try:
                selected = Path(survey_batch.output_dir.value)
            except Exception as exc:  # noqa: BLE001
                raise ValueError("Choose an output folder before running the batch") from exc
            if str(selected).strip() in {"", "."}:
                raise ValueError("Choose an output folder before running the batch")
            destination = require_existing_dir(selected, what="output folder", create=True)
            batch_paths["output"] = destination
            if controller.survey_result is None:
                raise ValueError("Survey a folder first so layouts and the run config are set")
            assignments = {}
            for layout_id, widgets in layout_rows.items():
                if not widgets["include"].isChecked():
                    continue
                assignments[layout_id] = int(widgets["channel"].currentData())
            if not assignments:
                raise ValueError("Include at least one surveyed layout and choose its channel")
            refresh_batch_preflight()
            return controller.run_survey_batches(
                destination,
                assignments=assignments,
                segment_overrides=batch_options.cellquant_overrides(),
            )

        return _run(action)

    run_batch_button.clicked.connect(run_batch_clicked)

    layouts_host = QWidget()
    layouts_host_layout = QVBoxLayout(layouts_host)
    layouts_host_layout.setContentsMargins(0, 0, 0, 0)
    layouts_label = QLabel(
        "After Survey: choose the segmentation channel for each layout, then Run batch. "
        "Suggestions from channel names are optional defaults only. "
        "Hover controls for help. Config is written under <output>/survey/. "
        "Changing Output folder before Run batch sends results to the new folder."
    )
    layouts_label.setWordWrap(True)
    _set_tip(layouts_label, "survey_layouts")
    layouts_host_layout.addWidget(layouts_label)
    layouts_body = QWidget()
    layouts_body_layout = QVBoxLayout(layouts_body)
    layouts_body_layout.setContentsMargins(0, 4, 0, 4)
    layouts_body_layout.setSpacing(10)
    layouts_host_layout.addWidget(layouts_body)

    def clear_layout_rows():
        layout_rows.clear()
        while layouts_body_layout.count():
            item = layouts_body_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def rebuild_layout_rows():
        clear_layout_rows()
        survey = controller.survey_result
        if survey is None:
            empty = QLabel("No survey yet — click Survey folder.")
            empty.setWordWrap(True)
            layouts_body_layout.addWidget(empty)
            return
        for layout_info in survey.layouts:
            row = QWidget()
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(0, 6, 0, 6)
            include = QCheckBox(
                f"{layout_info.file_count} file(s) · {layout_info.channel_count} ch · "
                f"{layout_info.layout_id}"
            )
            include.setChecked(layout_info.segment_channel is not None)
            _set_tip(include, "survey_layouts")
            names = QLabel("Channels: " + (" | ".join(layout_info.channel_names) or "(unnamed)"))
            names.setWordWrap(True)
            names.setTextInteractionFlags(Qt.TextSelectableByMouse)
            channel = QComboBox()
            channel.setMinimumHeight(28)
            _set_tip(channel, "segmentation_channel")
            for index, name in enumerate(layout_info.channel_names):
                channel.addItem(f"{index + 1} — {name}", index)
            preferred = layout_info.segment_channel
            if preferred is None:
                preferred = layout_info.suggested_channel
            if preferred is not None and 0 <= preferred < channel.count():
                channel.setCurrentIndex(preferred)
            channel_row = wrap_combo_with_scrollability(
                QtWidgets, channel, tip_text=tip("scrollability")
            )
            row_layout.addWidget(include)
            row_layout.addWidget(names)
            row_layout.addWidget(channel_row)
            layouts_body_layout.addWidget(row)
            include.toggled.connect(refresh_batch_preflight)
            layout_rows[layout_info.layout_id] = {"include": include, "channel": channel}
        refresh_batch_preflight()

    for native in (open_image.native, run_segmentation.native):
        single_layout.addWidget(native)
    single_layout.addWidget(single_options)
    single_layout.addWidget(save_edited.native)
    batch_layout.addWidget(survey_batch.native)
    batch_layout.addWidget(batch_options)
    batch_layout.addWidget(layouts_host, 1)
    batch_layout.addWidget(run_batch_button)
    pages.addWidget(single_page)
    pages.addWidget(batch_page)
    from .coexpression import CoexpressionPanel
    coexpression_page = CoexpressionPanel(viewer, controller)
    pages.addWidget(coexpression_page)
    root.cellquant_coexpression = coexpression_page

    # Decorate magicgui / coexpression combos that were not built via _labeled_row.
    for host in (
        open_image.native,
        run_segmentation.native,
        save_edited.native,
        survey_batch.native,
        coexpression_page,
    ):
        install_scrollability_controls(host, QtWidgets, tip_text=tip("scrollability"))

    def sync_mode(index: int):
        pages.setCurrentIndex(index)
        label = mode.itemText(index)
        set_status(f"Mode: {label}")

    mode.currentIndexChanged.connect(sync_mode)

    actions = QWidget()
    actions_layout = QHBoxLayout(actions)
    actions_layout.setContentsMargins(0, 0, 0, 0)
    cancel = QPushButton("Cancel")
    _set_tip(cancel, "cancel")
    cancel.clicked.connect(controller.cancel)
    kill = QPushButton("Kill")
    _set_tip(kill, "kill")
    kill.clicked.connect(controller.kill)
    open_output = QPushButton("Open output folder")
    _set_tip(open_output, "open_output")
    open_failures = QPushButton("Open failures.csv")
    _set_tip(open_failures, "open_failures")

    def _open_guarded(path: Path | None, missing: str):
        try:
            if path is None:
                raise FileNotFoundError(missing)
            open_path_in_os(path)
        except Exception as exc:  # noqa: BLE001
            controller.report_exception(exc)

    def open_output_clicked():
        _open_guarded(controller.last_output_dir, "No output folder from the latest CellQuant run yet")

    def open_failures_clicked():
        path = controller.last_failures_path
        if path is None and controller.last_output_dir is not None:
            candidate = Path(controller.last_output_dir) / "failures.csv"
            path = candidate if candidate.exists() else None
        _open_guarded(path, "No failures.csv from the latest batch yet")

    open_output.clicked.connect(open_output_clicked)
    open_failures.clicked.connect(open_failures_clicked)
    actions_layout.addWidget(cancel)
    actions_layout.addWidget(kill)
    actions_layout.addWidget(open_output)
    actions_layout.addWidget(open_failures)

    def display_event(event):
        sync_slow_job()
        details = dict(getattr(event, "details", {}) or {})
        stage = str(getattr(event, "stage", "") or "")
        if event.current is not None and event.total is not None:
            previous_key = slow_watch["unit_key"]
            previous_started = slow_watch["unit_started"]
            progress_meta["status"] = str(details.get("status") or event.kind)
            file_id = getattr(event, "file_id", "") or ""
            progress_meta["name"] = Path(str(file_id)).name if file_id else ""
            progress_meta["current"] = int(event.current)
            progress_meta["total"] = int(event.total)
            progress_meta["stage"] = stage
            progress.show()
            name = progress_meta["name"]
            suffix = f" — {name}" if name else ""

            # Prefer batch file progress for the bar. Stitch plane events would
            # otherwise replace "3/37 files" with "12/40 planes" and hide ETA.
            batch_progress = getattr(controller, "batch_progress", None)
            if stage == "batch" or batch_progress is None:
                total = max(int(event.total), 1)
                current = max(min(int(event.current), total), 0)
                progress.setRange(0, total)
                if progress_meta["status"] == "running":
                    progress.setValue(max(current - 1, 0))
                else:
                    progress.setValue(current)
                if stage == "batch":
                    progress.setFormat(f"%v / %m files{suffix}")
                else:
                    progress.setFormat(f"%v / %m done{suffix}")
            else:
                batch_current, batch_total = batch_progress
                total = max(int(batch_total), 1)
                current = max(min(int(batch_current), total), 0)
                status_token = getattr(controller, "batch_progress_status", None) or "running"
                progress.setRange(0, total)
                if status_token == "running":
                    progress.setValue(max(current - 1, 0))
                else:
                    progress.setValue(current)
                plane_current = int(event.current)
                plane_total = int(event.total)
                progress.setFormat(
                    f"file %v/%m — plane {plane_current}/{plane_total}{suffix}"
                )

            unit_key = (stage, str(file_id), int(event.current), progress_meta["status"])
            if progress_meta["status"] == "running" and unit_key != previous_key:
                if (
                    previous_started is not None
                    and previous_key is not None
                    and previous_key[3] == "running"
                ):
                    slow_watch["last_finished_unit_s"] = monotonic() - float(previous_started)
                slow_watch["unit_key"] = unit_key
                slow_watch["unit_started"] = monotonic()
            elif progress_meta["status"] in {"completed", "resumed"} and previous_started is not None:
                slow_watch["last_finished_unit_s"] = monotonic() - float(previous_started)
                slow_watch["unit_key"] = unit_key
                slow_watch["unit_started"] = None
        elif controller.is_busy and not progress.isVisible():
            progress.show()
            progress.setRange(0, 0)
            progress.setFormat("Working…")
            if slow_watch["unit_started"] is None:
                slow_watch["unit_started"] = monotonic()
                slow_watch["unit_key"] = ("job", "", 1, "running")

    def active_segment_mode() -> str | None:
        workflow = mode.currentData()
        panel = None
        if workflow == "single":
            panel = single_options
        elif workflow == "batch":
            panel = batch_options
        if panel is None:
            return None
        combo = getattr(panel, "cellquant_mode", None)
        if combo is None:
            return None
        value = combo.currentData()
        return str(value) if value is not None else None

    def sync_slow_job():
        job_key = controller.cancel_token
        if slow_watch["job_key"] is not job_key:
            slow_watch["job_key"] = job_key
            slow_watch["prompted"] = False
            slow_watch["last_finished_unit_s"] = None
            slow_watch["unit_started"] = controller._job_started_monotonic or monotonic()
            slow_watch["unit_key"] = ("job", "", 1, "running")

    def maybe_prompt_slow_run():
        if slow_watch["dialog_open"] or not controller.is_busy:
            return
        sync_slow_job()
        job_token = controller.cancel_token
        elapsed = None
        if slow_watch["unit_started"] is not None:
            elapsed = monotonic() - float(slow_watch["unit_started"])
        if not should_prompt_slow_run(
            already_prompted=bool(slow_watch["prompted"]),
            elapsed_current_unit_s=elapsed,
            last_finished_unit_s=slow_watch["last_finished_unit_s"],
        ):
            return

        slow_watch["prompted"] = True
        slow_watch["dialog_open"] = True
        segment_mode = active_segment_mode()
        status_l = controller.status_text.casefold()
        if segment_mode == "stitch_2d" or "plane" in status_l or "stitch" in status_l:
            unit_label = "Z plane"
        else:
            unit_label = "image"
        body = slow_run_prompt_text(
            elapsed_current_unit_s=elapsed,
            last_finished_unit_s=slow_watch["last_finished_unit_s"],
            unit_label=unit_label,
            current_mode=segment_mode,
        )
        set_status("This run looks slow — choose Continue, Cancel, or Kill in the dialog.")
        box = QMessageBox(root)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("CellQuant: long run detected")
        box.setText(body)
        continue_btn = box.addButton("Continue", QMessageBox.YesRole)
        cancel_btn = box.addButton("Cancel run", QMessageBox.NoRole)
        kill_btn = box.addButton("Kill now", QMessageBox.DestructiveRole)
        box.setDefaultButton(cancel_btn)
        box.exec_()
        clicked = box.clickedButton()
        slow_watch["dialog_open"] = False
        # The nested Qt dialog loop can finish this job or start another one.
        # A decision about the old job must never overwrite its final status
        # or cancel/kill a replacement job.
        if not controller.is_busy or controller.cancel_token is not job_token:
            return
        if clicked is continue_btn:
            # Keep a Cellpose-ish status so ETA refresh continues to attach.
            name = progress_meta.get("name") or ""
            suffix = f" — {name}" if name else ""
            set_status(f"Continuing Cellpose run{suffix}")
        elif clicked is kill_btn:
            controller.kill()
        else:
            controller.cancel()
        slow_watch["dialog_open"] = False

    def _strip_eta_suffix(text: str) -> str:
        base = text
        lowered = base.casefold()
        for marker in (" — ~", " — under a minute", " — about "):
            if marker in lowered:
                # Case-sensitive split on the em-dash form used by format_eta_seconds.
                if " — " in base:
                    base = base.rsplit(" — ", 1)[0]
                break
        return base

    def refresh_progress_while_busy():
        """Keep the bar visible and refresh ETA text during long Cellpose calls."""
        if controller.is_busy:
            if not progress.isVisible():
                progress.show()
                if controller.progress is None and controller.batch_progress is None:
                    progress.setRange(0, 0)
                    progress.setFormat("Working…")
                if slow_watch["unit_started"] is None:
                    slow_watch["unit_started"] = monotonic()
            eta = None
            if hasattr(controller, "_batch_eta_text"):
                eta = controller._batch_eta_text()
            elif (
                controller.batch_progress is not None
                and getattr(controller, "_job_started_monotonic", None) is not None
            ):
                current, total = controller.batch_progress
                status_token = getattr(controller, "batch_progress_status", None) or "running"
                completed = (
                    int(current)
                    if status_token in {"completed", "resumed"}
                    else max(int(current) - 1, 0)
                )
                eta = format_eta_seconds(
                    monotonic() - controller._job_started_monotonic,
                    int(current),
                    int(total),
                    completed=completed,
                )
            if eta:
                base = _strip_eta_suffix(controller.status_text)
                controller.status_text = f"{base} — {eta}"
            maybe_prompt_slow_run()
        else:
            if progress.isVisible():
                if progress.maximum() > 0:
                    progress.setValue(progress.maximum())
                    progress.setFormat("%v / %m done")
                progress.hide()
                progress.setRange(0, 0)
                progress.setFormat("Working…")
                progress_meta["name"] = ""
                progress_meta["status"] = ""
                progress_meta["current"] = None
                progress_meta["total"] = None
                progress_meta["stage"] = ""
            slow_watch["job_key"] = None
            slow_watch["prompted"] = False
            slow_watch["unit_key"] = None
            slow_watch["unit_started"] = None
            slow_watch["last_finished_unit_s"] = None
            slow_watch["dialog_open"] = False

    def refresh_action_buttons():
        open_output.setEnabled(controller.last_output_dir is not None)
        has_failures = controller.last_failures_path is not None and Path(
            controller.last_failures_path
        ).exists()
        if not has_failures and controller.last_output_dir is not None:
            has_failures = (Path(controller.last_output_dir) / "failures.csv").exists()
        open_failures.setEnabled(bool(has_failures))
        run_batch_button.setEnabled(controller.survey_result is not None)

    last_survey_token = {"value": None}

    def poll_events():
        controller.drain_events(display_event)
        refresh_progress_while_busy()
        status.setText(controller.status_text)
        refresh_action_buttons()
        survey = controller.survey_result
        token = None if survey is None else (survey.surveyed_utc, len(survey.layouts))
        if token != last_survey_token["value"]:
            last_survey_token["value"] = token
            rebuild_layout_rows()

    timer = QTimer(content)
    timer.timeout.connect(poll_events)
    timer.start(50)

    layout.addWidget(mode_row)
    layout.addWidget(pages, 1)
    layout.addWidget(actions)
    scroll.setWidget(content)

    footer = QWidget()
    footer_layout = QVBoxLayout(footer)
    footer_layout.setContentsMargins(6, 4, 6, 6)
    footer_layout.setSpacing(4)
    footer_layout.addWidget(progress)
    footer_layout.addWidget(status)

    root_layout.addWidget(scroll, 1)
    root_layout.addWidget(footer, 0)

    root.cellquant_controller = controller
    root.cellquant_timer = timer
    root.cellquant_mode = mode
    root.cellquant_pages = pages
    root.cellquant_survey_batch = survey_batch
    root.cellquant_open_output = open_output
    root.cellquant_open_failures = open_failures
    root.cellquant_single_options = single_options
    root.cellquant_batch_options = batch_options
    root.cellquant_content = content
    root.cellquant_scroll = scroll
    root.cellquant_progress = progress
    sync_mode(0)
    refresh_action_buttons()
    return root


def cellquant_widget():
    """Zero-argument npe2 widget entry point."""
    return make_cellquant_widget()


__all__ = [
    "DIAMETER_MODE_CHOICES",
    "FILE_TYPE_CHOICES",
    "SEGMENTATION_CHANNEL_WIDGET_OPTIONS",
    "SEGMENT_MODE_CHOICES",
    "bind_segmentation_channel_choices",
    "cellquant_widget",
    "file_type_suffixes",
    "make_cellquant_widget",
    "make_segment_options_panel",
    "segmentation_channel_choices",
]
