"""Step 5 "All markers": every marker's cutoff for the image shown, at once.

Each object is colored by its mix of positive markers: a marker's own color when it is positive for
that marker only, a color of its own for each overlap (double, triple ... positive), and the
negative color when it is positive for none. Every color can be changed and is remembered on this
computer. A mix is a bitmask: bit ``i`` set means positive for the ``i``-th marker of the recipe.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from qtpy.QtCore import Qt, QTimer
from qtpy.QtGui import QColor
from qtpy.QtWidgets import (
    QColorDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cellquant.count_area import outside

# The Display objects by choice for this view (never a marker id: those are column names).
ALL_MARKERS = "*all*"
# Marker colors in recipe order; the first two match the usual positive / negative pair's hues.
MARKER_COLORS = ("#26bf59", "#d43fd4", "#2f8fe8", "#f28e1c", "#18c0c0", "#9b6bdc")
# Overlap colors in the order the overlaps are listed (doubles first, then triples ...).
MIX_COLORS = ("#f2c12e", "#ff5a36", "#b4f03c", "#ffffff", "#7fd4ff", "#ff9ec8", "#c8a2ff", "#ffe08a")
NONE_COLOR = "#8c8c8c"
UNMEASURED = -1
# Past this many markers only the overlaps found in the image are listed (6 markers: 57 overlaps).
LIST_EVERY_MIX_UP_TO = 4


def _settings():
    from qtpy.QtCore import QSettings

    return QSettings("CellQuant", "CellQuant")


def _saved_color(key: str, default: str) -> str:
    try:
        value = str(_settings().value(key, default))
    except Exception:  # noqa: BLE001 - settings unreadable: use the default
        return default
    return value if QColor(value).isValid() else default


def mix_key(mask: int, ids: list[str]) -> str:
    """The mix's name in the settings: its positive markers' ids, so it survives reordering markers."""

    return "+".join(sorted(ids[bit] for bit in range(len(ids)) if mask >> bit & 1)) or "none"


def mix_name(mask: int, names: list[str]) -> str:
    if mask == UNMEASURED:
        return "Not measured"
    positive = [names[bit] for bit in range(len(names)) if mask >> bit & 1]
    if not positive:
        return "Negative for all"
    if len(positive) == 1:
        return f"{positive[0]} only"
    return " + ".join(positive)


def overlap_masks(n_markers: int) -> list[int]:
    """Every mix with two or more positive markers: doubles first, then triples ..."""

    masks = [mask for mask in range(1 << n_markers) if bin(mask).count("1") >= 2]
    return sorted(masks, key=lambda mask: (bin(mask).count("1"), [-(mask >> bit & 1) for bit in range(n_markers)]))


def mix_color(mask: int, ids: list[str]) -> str:
    """The color an object with this mix is drawn in."""

    count = bin(mask).count("1")
    if count == 0:
        return _saved_color("review/mix_colors/none", NONE_COLOR)
    if count == 1:
        bit = mask.bit_length() - 1
        return _saved_color(f"review/marker_colors/{ids[bit]}", MARKER_COLORS[bit % len(MARKER_COLORS)])
    default = MIX_COLORS[overlap_masks(len(ids)).index(mask) % len(MIX_COLORS)]
    return _saved_color(f"review/mix_colors/{mix_key(mask, ids)}", default)


def save_mix_color(mask: int, ids: list[str], color: str) -> None:
    if bin(mask).count("1") == 1:
        _settings().setValue(f"review/marker_colors/{ids[mask.bit_length() - 1]}", color)
    else:
        _settings().setValue(f"review/mix_colors/{mix_key(mask, ids)}", color)


def reset_mix_colors(ids: list[str]) -> None:
    settings = _settings()
    for identifier in ids:
        settings.remove(f"review/marker_colors/{identifier}")
    settings.remove("review/mix_colors")


def object_mixes(frame: pd.DataFrame, ids: list[str]) -> tuple[np.ndarray, np.ndarray]:
    """(object ids, mix of each) for the counted objects: not excluded and inside the count area.
    An object without a call for some marker (not measured) gets ``UNMEASURED``."""

    if "excluded" in frame.columns:
        frame = frame.loc[~frame["excluded"].astype(bool)]
    frame = frame.loc[~outside(frame)]
    masks = np.zeros(len(frame), dtype=np.int64)
    missing = np.zeros(len(frame), dtype=bool)
    for bit, identifier in enumerate(ids):
        if identifier not in frame.columns:
            missing[:] = True
            continue
        column = frame[identifier]
        missing |= column.isna().to_numpy()
        positive = column.map(_positive).to_numpy(dtype=bool)
        masks |= positive.astype(np.int64) << bit
    masks[missing] = UNMEASURED
    return frame["object_id"].to_numpy(dtype=np.int64), masks


def _positive(value) -> bool:
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "1", "positive"}
    try:
        return bool(value) and not pd.isna(value)
    except (TypeError, ValueError):
        return False


def mix_rows(masks: np.ndarray, n_markers: int) -> list[tuple[int, int, float]]:
    """(mix, count, percent of measured counted objects) in the order they are listed: each marker
    alone, the overlaps, negative for all, then not measured (when any)."""

    measured = masks[masks != UNMEASURED]
    total = len(measured)
    counts = np.bincount(measured, minlength=1 << n_markers) if total else np.zeros(1 << n_markers, dtype=np.int64)
    overlaps = overlap_masks(n_markers)
    if n_markers > LIST_EVERY_MIX_UP_TO:
        overlaps = [mask for mask in overlaps if counts[mask]]
    order = [1 << bit for bit in range(n_markers)] + overlaps + [0]
    rows = [(mask, int(counts[mask]), 100.0 * counts[mask] / total if total else float("nan")) for mask in order]
    unmeasured = int((masks == UNMEASURED).sum())
    if unmeasured:
        rows.append((UNMEASURED, unmeasured, float("nan")))
    return rows


def slider_range(values: np.ndarray, threshold: float, percent_rule: bool) -> tuple[float, float, int]:
    """Like the single marker slider: 0-100 % for the percent rule, otherwise the objects' values."""

    if percent_rule:
        return 0.0, 100.0, 1
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    low = float(min(finite.min(), threshold)) if finite.size else min(0.0, threshold)
    high = float(max(finite.max(), threshold)) if finite.size else max(1.0, threshold)
    if high <= low:
        high = low + 1.0
    return low, high, 0 if high - low >= 100 else 2


class _MarkerRow(QGroupBox):
    """One marker: its histogram, cutoff slider and positive count for the image shown."""

    def __init__(self, view: CombinedMarkersView, classification_id: str, name: str):
        super().__init__(name)
        from superqt import QLabeledDoubleSlider

        from cellquant.gui.app import CutoffHistogram

        self.classification_id = classification_id
        layout = QVBoxLayout(self)
        self.histogram = CutoffHistogram()
        self.histogram.setMinimumHeight(60)
        self.slider = QLabeledDoubleSlider(Qt.Horizontal)
        self.slider.setRange(0.0, 1.0)
        self.histogram.cutoff_dragged.connect(lambda value: self.slider.setValue(value))
        self.slider.valueChanged.connect(lambda value: view.cutoff_moved(classification_id, float(value)))
        self.counts = QLabel("")
        layout.addWidget(self.histogram)
        layout.addWidget(self.slider)
        layout.addWidget(self.counts)

    def show_values(
        self, values: np.ndarray, cutoff: float, comparison: str, percent_rule: bool, counts: dict, by_examples: bool = False
    ) -> None:
        low, high, decimals = slider_range(values, cutoff, percent_rule)
        self.slider.blockSignals(True)
        self.slider.setDecimals(decimals)
        self.slider.setRange(low, high)
        self.slider.setSingleStep((high - low) / 100)
        self.slider.setValue(cutoff)
        self.slider.blockSignals(False)
        self.histogram.set_data(values, low, high, cutoff, comparison)
        classified = counts["positive"] + counts["negative"]
        percent = counts["percent_positive"]
        percent_text = "—" if percent != percent else f"{percent:.1f}%"
        rule = "min % of cell" if percent_rule else "cutoff"
        text = f"Positive {counts['positive']} of {classified} ({percent_text}) · {rule} {cutoff:g}"
        # A cutoff set by clicked examples (step 4) is changed there, with this marker shown alone.
        self.slider.setVisible(not by_examples)
        self.histogram.set_draggable(not by_examples)
        self.histogram.setVisible(True)
        self.counts.setText(text + ("\nSet by examples: show this marker alone to change it." if by_examples else ""))

    def show_unmeasured(self) -> None:
        for widget in (self.histogram, self.slider):
            widget.setVisible(False)
        self.counts.setText("Not measured in this image.")


class _OwnWindow(QWidget):
    """The view in a window of its own; closing it puts the view back in step 5."""

    def __init__(self, view: CombinedMarkersView):
        super().__init__(None, Qt.Window)
        from qtpy.QtWidgets import QScrollArea

        self.view = view
        self.setWindowTitle("All markers · CellQuant")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        # Scrolls when the markers do not fit, like the step 5 panel.
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        layout.addWidget(self.scroll)
        self.resize(520, 760)

    def closeEvent(self, event) -> None:
        self.view.dock()
        super().closeEvent(event)


class CombinedMarkersView(QWidget):
    """Every marker's cutoff for the image shown, and how many objects have each mix of markers."""

    COLUMNS = ("Show", "Color", "Objects", "Count", "%")

    def __init__(self, panel):
        super().__init__()
        self.panel = panel  # step 5's ReviewPanel: it sets the cutoffs and redraws
        self._ids: list[str] = []
        self._names: list[str] = []
        self._rows: dict[str, _MarkerRow] = {}
        self._hidden: set[str] = set()  # mix keys unticked under Show (this session)
        self._table_masks: list[int] = []
        self._window: _OwnWindow | None = None
        self._slot = None  # where the view sits in step 5 while docked
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        hint = QLabel("Each marker's cutoff for this image. Objects are colored by the markers they are positive for.")
        hint.setWordWrap(True)
        top.addWidget(hint, 1)
        self.pop_button = QPushButton("Pop out")
        self.pop_button.setToolTip("Show these cutoffs and counts in a window of their own; close it to put them back here.")
        self.pop_button.clicked.connect(self._pop_clicked)
        top.addWidget(self.pop_button, 0, Qt.AlignTop)
        layout.addLayout(top)
        self.rows_box = QVBoxLayout()
        layout.addLayout(self.rows_box)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(list(self.COLUMNS))
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setToolTip("Untick Show to hide those objects in the image. Click a color to change it.")
        self.table.itemChanged.connect(self._show_ticked)
        self.table.cellClicked.connect(self._cell_clicked)
        mixes = QLabel("Mixes of markers")
        mixes.setToolTip("Each object counts once, under the exact set of markers it is positive for.")
        layout.addWidget(mixes)
        layout.addWidget(self.table)
        self.total = QLabel("")
        layout.addWidget(self.total)
        buttons = QHBoxLayout()
        defaults = QPushButton("Default colors")
        defaults.setToolTip("Each marker and each overlap back to its starting color.")
        defaults.clicked.connect(self._reset_colors)
        self.apply_all = QPushButton("Apply to all images")
        self.apply_all.setToolTip("This image's cutoff for every marker, for every image.")
        self.apply_all.clicked.connect(lambda _checked=False: self.panel.apply_all_markers_to_all())
        buttons.addWidget(defaults)
        buttons.addStretch(1)
        buttons.addWidget(self.apply_all)
        layout.addLayout(buttons)
        # Recolor at most every 40 ms while dragging, with each marker's latest value.
        self._pending: dict[str, float] = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._apply_pending)

    # Cutoffs --------------------------------------------------------------------------------

    def cutoff_moved(self, classification_id: str, value: float) -> None:
        self._pending[classification_id] = value
        if not self._timer.isActive():
            self._timer.start()

    def _apply_pending(self) -> None:
        pending, self._pending = self._pending, {}
        if pending:
            self.panel.set_cutoffs(pending)

    def settings_inputs(self) -> list[QWidget]:
        """The sliders and histograms: greyed out while a run uses the settings."""

        return [widget for row in self._rows.values() for widget in (row.slider, row.histogram)]

    # Showing an image -------------------------------------------------------------------------

    def show_result(self, result, controller) -> None:
        recipe = controller.recipe
        classifications = list(recipe.classifications)
        ids = [item.id for item in classifications]
        if ids != self._ids or [item.name for item in classifications] != self._names:
            self._build_rows(classifications)
        image_id = result.provenance["image_id"]
        percent_ids = {m.id for m in recipe.measurements if m.statistic == "percent_above"}
        for classification in classifications:
            row = self._rows[classification.id]
            if classification.measurement not in result.objects.columns:
                row.show_unmeasured()
                continue
            cutoff = classification.threshold_for(image_id)
            values = result.objects[classification.measurement].to_numpy(dtype=float)
            counts = controller.histogram(image_id, classification.measurement, cutoff, classification.comparison)
            row.show_values(
                values, cutoff, classification.comparison, classification.measurement in percent_ids, counts,
                by_examples=classification.cutoff_by == "examples",
            )
        self._color_rows()
        self._show_table(result)

    def _color_rows(self) -> None:
        """Each marker's histogram in that marker's color above the cutoff, grey below."""

        for bit, identifier in enumerate(self._ids):
            histogram = self._rows[identifier].histogram
            histogram.colors = (mix_color(1 << bit, self._ids), mix_color(0, self._ids))
            histogram.update()

    def show_no_result(self) -> None:
        """An image not run yet: no numbers left over from the previous image."""

        for row in self._rows.values():
            row.show_unmeasured()
            row.counts.setText("Not run yet.")
        self._table_masks = []
        self.table.setRowCount(0)
        self.total.setText("")

    def _build_rows(self, classifications) -> None:
        while self.rows_box.count():
            widget = self.rows_box.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        self._ids = [item.id for item in classifications]
        self._names = [item.name for item in classifications]
        self._rows = {}
        for classification in classifications:
            row = _MarkerRow(self, classification.id, classification.name)
            self._rows[classification.id] = row
            self.rows_box.addWidget(row)

    def _show_table(self, result) -> None:
        _object_ids, masks = object_mixes(result.objects, self._ids)
        rows = mix_rows(masks, len(self._ids))
        self._table_masks = [mask for mask, _count, _percent in rows]
        table = self.table
        table.blockSignals(True)
        table.setRowCount(len(rows))
        for index, (mask, count, percent) in enumerate(rows):
            show = QTableWidgetItem()
            if mask == UNMEASURED:
                show.setFlags(Qt.ItemIsEnabled)
            else:
                show.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                show.setCheckState(Qt.Unchecked if mix_key(mask, self._ids) in self._hidden else Qt.Checked)
            swatch = QTableWidgetItem("")
            if mask != UNMEASURED:
                swatch.setBackground(QColor(mix_color(mask, self._ids)))
                swatch.setToolTip("Click to change this color.")
            name = QTableWidgetItem(mix_name(mask, self._names))
            if mask not in (0, UNMEASURED):
                name.setToolTip(f"Positive for {mix_name(mask, self._names).removesuffix(' only')} and negative for the other markers.")
            number = QTableWidgetItem(str(count))
            share = QTableWidgetItem("—" if percent != percent else f"{percent:.1f}%")
            for item in (number, share):
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            for column, item in enumerate((show, swatch, name, number, share)):
                table.setItem(index, column, item)
        table.blockSignals(False)
        # As tall as its rows: no empty space under a short list.
        table.setFixedHeight(table.horizontalHeader().height() + sum(table.rowHeight(r) for r in range(len(rows))) + 2 * table.frameWidth())
        measured = int((masks != UNMEASURED).sum())
        self.total.setText(f"{measured} counted objects with a call for every marker.")

    # Colors and Show ------------------------------------------------------------------------

    def showing_all(self) -> bool:
        return self.panel.showing_all_markers()

    def _marker_ids(self) -> list[str]:
        # From the recipe, not the rows: the overlay is drawn before the rows are shown.
        controller = self.panel.shell.controller
        return [] if controller is None else [item.id for item in controller.recipe.classifications]

    def code_colors(self) -> dict[int, str]:
        """Overlay value -> color: each mix is drawn as value mix + 1 (0 is not drawn)."""

        ids = self._marker_ids()
        return {mask + 1: mix_color(mask, ids) for mask in range(1 << len(ids))}

    def overlay_codes(self, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """(object ids, overlay value): mix + 1, or 0 for objects not drawn (hidden or not measured)."""

        ids = self._marker_ids()
        object_ids, masks = object_mixes(frame, ids)
        codes = masks + 1
        codes[masks == UNMEASURED] = 0
        for mask in range(1 << len(ids)):
            if mix_key(mask, ids) in self._hidden:
                codes[masks == mask] = 0
        return object_ids, codes

    def _show_ticked(self, item: QTableWidgetItem) -> None:
        if item.column() != 0 or item.row() >= len(self._table_masks):
            return
        key = mix_key(self._table_masks[item.row()], self._ids)
        if item.checkState() == Qt.Checked:
            self._hidden.discard(key)
        else:
            self._hidden.add(key)
        self.panel.redraw()

    def _cell_clicked(self, row: int, column: int) -> None:
        if column != 1 or row >= len(self._table_masks) or self._table_masks[row] == UNMEASURED:
            return
        mask = self._table_masks[row]
        chosen = QColorDialog.getColor(QColor(mix_color(mask, self._ids)), self, f"Color of {mix_name(mask, self._names)}")
        if not chosen.isValid():
            return
        save_mix_color(mask, self._ids, chosen.name())
        self.table.item(row, 1).setBackground(chosen)
        self._color_rows()
        self.panel.shell.apply_classification_colors()

    def _reset_colors(self, _checked: bool = False) -> None:
        reset_mix_colors(self._ids)
        for row, mask in enumerate(self._table_masks):
            if mask != UNMEASURED:
                self.table.item(row, 1).setBackground(QColor(mix_color(mask, self._ids)))
        self._color_rows()
        self.panel.shell.apply_classification_colors()

    # Docked in step 5 or in a window of its own ---------------------------------------------------

    def set_slot(self, layout) -> None:
        self._slot = layout
        layout.addWidget(self)

    def popped_out(self) -> bool:
        return self._window is not None

    def _pop_clicked(self, _checked: bool = False) -> None:
        if self._window is None:
            self.pop_out()
        else:
            self._window.close()  # docks it back

    def pop_out(self) -> None:
        if self._window is not None:
            return
        self._window = _OwnWindow(self)
        self._window.scroll.setWidget(self)
        self.pop_button.setText("Put back")
        self.pop_button.setToolTip("Put these cutoffs and counts back in step 5.")
        self.setVisible(True)
        self._window.show()
        self.panel.combined_moved()

    def dock(self) -> None:
        window, self._window = self._window, None
        if window is None:
            return
        window.scroll.takeWidget()  # the window is closing: keep this view
        if self._slot is not None:
            self._slot.addWidget(self)
        self.pop_button.setText("Pop out")
        self.pop_button.setToolTip("Show these cutoffs and counts in a window of their own; close it to put them back here.")
        self.panel.combined_moved()
