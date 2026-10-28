"""A graph sheet: the figure (white, as it will be exported), its format panel, click-to-edit, and graph export.

The format panel is a column of collapsible sections (format_panel.py); Format graph… and Format axes…
open the same sections as tabbed dialogs. Clicking the graph selects what was clicked: matplotlib pick
events name the artist, and every artist that ``graphs.draw`` makes clickable carries a tag (a data
set, an axis, the title, the legend, a text box). The selection is kept as that tag, not as the artist,
so it survives every redraw; it is outlined on screen only (exports draw a fresh figure). Double-click
opens the matching dialog tab; dragging a text box, arrow or the legend moves it and saves where.

matplotlib is imported here only, when the first graph is shown, so the window opens without it.
"""

from __future__ import annotations

import html
from pathlib import Path

from qtpy.QtCore import Qt, QTimer
from qtpy.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from cellquant.errors import CellQuantError
from cellquant.gui.quantanalysis import format_panel, graphs, tips
from cellquant.gui.quantanalysis import graph_format as gf
from cellquant.gui.quantanalysis.datagrid import DANGER_STYLE, NOTE_STYLE, WARNING_STYLE
from cellquant.gui.quantanalysis.project import PSEUDO_WARNING, GraphSheet, Project

# key: (name, file suffix, raster)
EXPORT_FORMATS = {
    "svg": ("SVG (vector, for a vector editor such as Inkscape)", ".svg", False),
    "pdf": ("PDF (vector)", ".pdf", False),
    "eps": ("EPS (vector, older journals)", ".eps", False),
    "png": ("PNG", ".png", True),
    "tiff": ("TIFF, LZW compressed (high resolution)", ".tif", True),
    "jpeg": ("JPEG", ".jpg", True),
}
DPI_CHOICES = (300, 600, 1200)
HIGHLIGHT = "#1f8fff"
# When one click hits several artists, the one in front of the others wins.
PRIORITY = {"annotation": 6, "legend": 5, "title": 4, "bracket": 3, "dataset": 3, "connect": 2, "axis": 1}
DRAGGABLE = ("annotation", "legend")


def export_figure(data, graph: GraphSheet, result, path: str | Path, kind: str, dpi: int = 300,
                  width: float | None = None, height: float | None = None, units: str | None = None, outliers=None) -> Path:
    """Draw the graph on a fresh figure at the export size and save it. Returns the path written."""

    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    sized = GraphSheet(graph.name, graph.data_id, graph.kind, graph.format.copy(), graph.result_id, id=graph.id)
    for key, value in (("width", width), ("height", height), ("units", units)):
        if value is not None:
            sized.format[key] = value
    figure = Figure()
    FigureCanvasAgg(figure)
    graphs.draw(figure, data, sized, result, outliers)
    path = Path(path)
    options = {"dpi": dpi, "facecolor": "white", "format": kind}
    if kind == "tiff":
        options["pil_kwargs"] = {"compression": "tiff_lzw"}
    elif kind == "jpeg":
        options["pil_kwargs"] = {"quality": 95}
    figure.savefig(path, **options)
    return path


def section_for(target) -> tuple[str, str, str]:
    """(side-panel section, dialog, dialog tab) that formats ``target``."""

    kind = target[0]
    if kind in ("dataset", "connect"):
        return "datasets", "graph", "datasets"
    if kind == "axis":
        return "axes", "axes", target[1]
    if kind in ("title", "legend"):
        return "text", "graph", "text"
    if kind == "annotation":
        return "annotations", "graph", "annotations"
    return "significance", "graph", "significance"


def _extent(artist, renderer):
    """The screen box of an artist. Collections are measured from their points: matplotlib's own box for them is
    the whole data area."""

    import numpy as np
    from matplotlib.collections import Collection, PathCollection
    from matplotlib.transforms import Bbox

    if isinstance(artist, PathCollection):
        offsets = np.asarray(artist.get_offsets(), dtype=float)
        if offsets.size == 0:
            return None
        points = artist.get_offset_transform().transform(offsets)
        sizes = artist.get_sizes()
        radius = (np.sqrt(float(np.max(sizes))) if len(sizes) else 4.0) * artist.figure.dpi / 72 / 2
        return Bbox([points.min(axis=0) - radius, points.max(axis=0) + radius])
    if isinstance(artist, Collection):
        paths = artist.get_paths()
        if not paths:
            return None
        points = artist.get_transform().transform(np.concatenate([path.vertices for path in paths]))
        points = points[np.all(np.isfinite(points), axis=1)]
        return Bbox([points.min(axis=0), points.max(axis=0)]) if len(points) else None
    return artist.get_window_extent(renderer)


class GraphSheetView(QWidget):
    def __init__(self, window, project: Project, sheet: GraphSheet):
        super().__init__()
        self.window = window
        self.project = project
        self.sheet = sheet
        self.sections: list = []
        self.dialogs: dict = {}
        self.selection = None          # the tag of the selected part, e.g. ("dataset", "Control")
        self._highlights: list = []
        self._picked = None            # [mouse event, tag, artist] of the front-most artist under the last click
        self._drag = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        left = QVBoxLayout()
        top = QHBoxLayout()
        self.source = QLabel()
        self.source.setStyleSheet(NOTE_STYLE)
        top.addWidget(self.source, 1)
        for text, tip, slot in (("Format graph…", tips.FORMAT_GRAPH, lambda: self.format_dialog("graph")),
                                ("Format axes…", tips.FORMAT_AXES, lambda: self.format_dialog("axes")),
                                ("Export graph…", "Save as SVG, PDF, EPS, PNG, TIFF (300 to 1200 dpi, LZW) or JPEG, at a size in mm or inches.",
                                 self.export_dialog)):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(lambda _checked=False, slot=slot: slot())
            top.addWidget(button)
        left.addLayout(top)
        self.warning = QLabel()
        self.warning.setWordWrap(True)
        left.addWidget(self.warning)
        self.notes = QLabel()
        self.notes.setWordWrap(True)
        self.notes.setStyleSheet(WARNING_STYLE)
        left.addWidget(self.notes)
        self.page = QScrollArea()
        self.page.setWidgetResizable(True)
        self.page.setStyleSheet("QScrollArea { background: #3b4048; border: none; }")
        holder = QWidget()
        holder.setStyleSheet("background: #3b4048;")
        self.holder_layout = QVBoxLayout(holder)
        self.holder_layout.setAlignment(Qt.AlignCenter)
        self.page.setWidget(holder)
        left.addWidget(self.page, 1)
        self.hint = QLabel(tips.SELECT)
        self.hint.setStyleSheet(NOTE_STYLE)
        self.hint.setWordWrap(True)
        left.addWidget(self.hint)
        layout.addLayout(left, 1)
        self.canvas = None
        self.figure = None
        self.missing = None
        try:
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
            from matplotlib.figure import Figure
        except ImportError:
            self.missing = QLabel(
                "Graphs need matplotlib, which is not installed.<br>Install it with:<br>"
                "<code>pip install \"cellquant[stats]\"</code><br>then open QuantAnalysis again."
            )
            self.missing.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.missing.setStyleSheet("QLabel { color: #f0f0f0; font-size: 13px; }")
            self.holder_layout.addWidget(self.missing)
        else:
            self.figure = Figure()
            self.canvas = FigureCanvasQTAgg(self.figure)
            self.canvas.setStyleSheet("background: white;")
            self.canvas.setFocusPolicy(Qt.ClickFocus)
            self.holder_layout.addWidget(self.canvas)
            self.canvas.mpl_connect("pick_event", self._on_pick)
            self.canvas.mpl_connect("button_press_event", self._on_press)
            self.canvas.mpl_connect("motion_notify_event", self._on_motion)
            self.canvas.mpl_connect("button_release_event", self._on_release)
            self.canvas.mpl_connect("key_press_event", self._on_key)
        layout.addWidget(self._format_panel())
        self._redraw = QTimer(self)
        self._redraw.setSingleShot(True)
        self._redraw.setInterval(120)
        self._redraw.timeout.connect(self.draw)
        self.fill()

    # --- The format panel -----------------------------------------------------------------------------------------

    def _format_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setFixedWidth(372)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.panel_scroll = scroll
        panel = QWidget()
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 6, 0)
        column.setSpacing(2)
        self.panel = {}
        self.collapsibles = {}
        for kind in format_panel.SECTIONS:
            section = kind(self)
            self.panel[section.key] = section
            for part in section.sections() if hasattr(section, "sections") else [section]:
                self.add_section(part)
            box = format_panel.Collapsible(section.title, section, section.tip, expanded=section.key == "graph")
            self.collapsibles[section.key] = box
            column.addWidget(box)
        column.addStretch(1)
        scroll.setWidget(panel)
        # The controls the first format panel had, under their old names.
        graph, significance = self.panel["graph"], self.panel["significance"]
        for name in ("kind", "error", "comparison", "show_outliers", "dot_size", "font_size"):
            setattr(self, name, getattr(graph, name))
        for name in ("bracket", "bracket_mode", "hide_ns", "letters", "letter_style"):
            setattr(self, name, getattr(significance, name))
        self.show_n = self.panel["axes"].axis.show_n
        return scroll

    def add_section(self, section) -> None:
        if section not in self.sections:
            self.sections.append(section)

    def remove_section(self, section) -> None:
        if section in self.sections:
            self.sections.remove(section)

    def fill(self) -> None:
        """Show the sheet's settings in the panel (and any open dialog), then draw."""

        for section in list(self.sections):
            section.fill()
        self.draw()

    def format_changed(self, source=None) -> None:
        """A section changed the format: redraw at once (after a short pause while typing) and refresh the other sections."""

        for section in list(self.sections):
            if section is not source:
                section.fill()
        self._redraw.start()
        self.project.touch()

    def flush(self) -> None:
        """Draw now if a redraw is waiting (tests and exports)."""

        if self._redraw.isActive():
            self._redraw.stop()
            self.draw()

    def set_ref_color(self, number: int, color: str) -> None:
        colors = list(self.sheet.format.ref_colors) + graphs.DEFAULT_FORMAT["ref_colors"][len(self.sheet.format.ref_colors):]
        colors[number] = color
        self.sheet.format.ref_colors = colors
        self.panel["significance"].fill()
        self.draw()

    def format_dialog(self, which: str = "graph", tab: str | None = None):
        if self.figure is None:
            self.window.status('Graphs need matplotlib: pip install "cellquant[stats]"')
            return None
        dialog = self.dialogs.get(which)
        if dialog is None:
            dialog = format_panel.FormatDialog(self, which, tab)
            self.dialogs[which] = dialog
        elif tab:
            dialog.show_tab(tab)
        dialog.show()
        dialog.raise_()
        return dialog

    # --- Styles ---------------------------------------------------------------------------------------------------

    def names(self) -> list[str]:
        data = self.project.data.get(self.sheet.data_id)
        return graphs.series_names(data) if data is not None else []

    def apply_style(self, style: dict, groups=None, graph_ids=None) -> None:
        """Copy the chosen groups of ``style`` onto this graph (and the other graphs named)."""

        for graph_id in graph_ids or [self.sheet.id]:
            graph = self.project.graphs.get(graph_id)
            if graph is None:
                continue
            data = self.project.data.get(graph.data_id)
            gf.apply_style(graph.format, style, graphs.series_names(data) if data else [], groups)
            if graph is not self.sheet:
                self.project.changed.emit(graph.id)
        self.format_changed(None)

    def apply_named_style(self, name: str | None) -> None:
        style = gf.all_styles().get(name or "")
        if style is None:
            return
        self.apply_style(style, style.get("groups"))
        for section in self.sections:
            if isinstance(section, format_panel.StyleSection):
                section.choice.setCurrentIndex(max(0, section.choice.findData(name)))
        self.window.log(f"graph {self.sheet.name}: style “{name}” applied")
        self.window.status(f"Applied the style “{name}”.")

    def save_style(self, name: str) -> dict:
        style = gf.make_style(self.sheet.format, self.names(), format_panel.SAVED_GROUPS)
        replaced = name in gf.load_styles()
        try:
            gf.save_style(name, style)
        except OSError as exc:
            self.window.status(f"The style “{name}” could not be saved to {gf.styles_path()}: {exc.strerror or exc}.")
            return style
        for section in self.sections:
            if isinstance(section, format_panel.StyleSection):
                section.fill()
                section.choice.setCurrentIndex(max(0, section.choice.findData(name)))
        self.window.status(f"{'Replaced' if replaced else 'Saved'} the style “{name}” ({gf.styles_path()}).")
        return style

    def magic_dialog(self) -> format_panel.MagicDialog:
        dialog = format_panel.MagicDialog(self)
        dialog.accepted.connect(lambda: self._magic(dialog))
        dialog.open()
        return dialog

    def _magic(self, dialog) -> None:
        style = dialog.style()
        if style is not None:
            self.apply_style(style, dialog.chosen_groups(), dialog.target_ids())
            self.window.log(f"graph {self.sheet.name}: made to look like {dialog.source.currentText()} ({', '.join(dialog.chosen_groups())})")

    def copy_format(self) -> str:
        text = gf.to_clipboard(self.sheet.format, self.names())
        self.window._format_clipboard = text
        format_panel.clipboard().setText(text)
        self.window.status("Format copied. Select another graph and click Paste format.")
        return text

    def paste_format(self) -> bool:
        style = gf.from_clipboard(format_panel.clipboard().text()) or gf.from_clipboard(getattr(self.window, "_format_clipboard", ""))
        if style is None:
            self.window.status("Copy the format of a graph first (Copy format).")
            return False
        self.apply_style(style, format_panel.PASTED_GROUPS)
        self.window.status("Format pasted.")
        return True

    # --- Selection: click to edit --------------------------------------------------------------------------------

    def artists_of(self, target) -> list:
        if self.figure is None or target is None:
            return []
        return self.figure.findobj(lambda artist: getattr(artist, "_cq_target", None) == target)

    def select(self, target, reveal: bool = True) -> None:
        """Select a part of the graph (or nothing), outline it, and open its section of the panel."""

        self.selection = tuple(target) if target else None
        if self.selection and reveal:
            self._reveal(self.selection)
        self._highlight()

    def _reveal(self, target) -> None:
        key, _which, tab = section_for(target)
        box = self.collapsibles.get(key)
        if box is not None:
            box.expand(True)
        section = self.panel.get(key)
        sections = [section] + [page for dialog in self.dialogs.values() for page in dialog.pages.values()]
        for part in sections:
            if target[0] == "dataset" and isinstance(part, format_panel.DataSetSection):
                part.show_dataset(target[1])
            elif target[0] == "axis" and isinstance(part, format_panel.AxesSection):
                part.axis.show_axis(target[1])
            elif target[0] == "annotation" and isinstance(part, format_panel.AnnotationSection):
                part.show_annotation(target[1])
        for dialog in self.dialogs.values():
            dialog.show_tab(tab)
        if box is not None:
            QTimer.singleShot(0, lambda: self.panel_scroll.ensureWidgetVisible(box, 0, 0))

    def _highlight(self) -> None:
        """Outline the selected part (on screen only)."""

        if self.figure is None or self._redraw.isActive():
            return  # the redraw that is waiting outlines it
        from matplotlib.patches import Rectangle
        from matplotlib.transforms import Bbox, IdentityTransform

        for artist in self._highlights:
            try:
                artist.remove()
            except (ValueError, NotImplementedError):
                pass
        self._highlights = []
        target = self.selection
        if target is None:
            self.canvas.draw_idle()
            return
        renderer = self.canvas.get_renderer()
        boxes = []
        layout = getattr(self.figure, "_cq_axes", {}) or {}
        if target[0] == "axis":
            axes = layout.get("segments", []) if target[1] in ("y", "x") else [layout.get("right")]
            for ax in axes:
                if ax is None:
                    continue
                axis = ax.xaxis if target[1] == "x" else ax.yaxis
                if target[1] == "x" and ax is not layout.get("bottom"):
                    continue
                try:
                    boxes.append(axis.get_tightbbox(renderer))
                except (RuntimeError, ValueError):
                    pass
        for artist in self.artists_of(target):
            if not artist.get_visible():
                continue
            try:
                box = _extent(artist, renderer)
            except (RuntimeError, ValueError, TypeError, AttributeError):
                continue
            axes = getattr(artist, "axes", None)
            if box is not None and axes is not None and artist.get_clip_on() and target[0] in ("dataset", "connect"):
                box = Bbox.intersection(box, axes.bbox)  # a bar drawn in every segment of a broken axis shows only in some
            if box is not None and box.width >= 0 and box.height >= 0 and abs(box.x0) < 1e6:
                boxes.append(box)
        boxes = [box for box in boxes if box is not None and box.width + box.height > 0]
        if not boxes:
            if not self.artists_of(target) and target[0] not in ("axis",):
                self.selection = None  # the part is gone (deleted, or a legend turned off)
            self.canvas.draw_idle()
            return
        union = Bbox.union(boxes).padded(3)
        rect = Rectangle((union.x0, union.y0), union.width, union.height, transform=IdentityTransform(), fill=False, edgecolor=HIGHLIGHT,
                         linewidth=1.2, linestyle=(0, (4, 2)), zorder=1000)
        rect._cq_highlight = True
        self.figure.add_artist(rect)
        self._highlights.append(rect)
        self.canvas.draw_idle()

    def _on_pick(self, event) -> None:
        target = getattr(event.artist, "_cq_target", None)
        if target is None:
            return
        mouse = event.mouseevent
        if self._picked is None or self._picked[0] is not mouse or PRIORITY.get(target[0], 0) > PRIORITY.get(self._picked[1][0], 0):
            self._picked = [mouse, target, event.artist]
            self.select(target, reveal=True)

    def _area_at(self, event):
        """What lies under a click that hit no artist: the axis numbers, the legend or nothing."""

        layout = getattr(self.figure, "_cq_axes", None) or {}
        plot = getattr(self.figure, "_cq_plot", None)
        if plot is None or event.x is None:
            return None
        legend = getattr(self.figure, "_cq_legend", None)
        renderer = self.canvas.get_renderer()
        if legend is not None and legend.get_window_extent(renderer).contains(event.x, event.y):
            return ("legend",)
        (x0, y0), (x1, y1) = plot.transform((0, 0)), plot.transform((1, 1))
        if y0 <= event.y <= y1 and event.x < x0:
            return ("axis", "y")
        if x0 <= event.x <= x1 and event.y < y0:
            return ("axis", "x")
        if layout.get("right") is not None and y0 <= event.y <= y1 and event.x > x1:
            return ("axis", "right")
        return None

    def _on_press(self, event) -> None:
        if event.button != 1 or self.figure is None:
            return
        picked = self._picked if self._picked is not None and self._picked[0] is event else None
        self._picked = None
        target = picked[1] if picked else self._area_at(event)
        if target != self.selection:
            self.select(target)
        if target is None:
            return
        if event.dblclick:
            _key, which, tab = section_for(target)
            self.format_dialog(which, tab)
            return
        if target[0] in DRAGGABLE:
            self._start_drag(target, event)

    def _start_drag(self, target, event) -> None:
        plot = getattr(self.figure, "_cq_plot", None)
        if plot is None:
            return
        start = {}
        for artist in self.artists_of(target):
            if hasattr(artist, "set_positions"):  # an arrow
                start[artist] = ("arrow", [tuple(point) for point in artist._posA_posB])
            elif hasattr(artist, "set_bbox_to_anchor"):  # the legend
                box = artist.get_window_extent(self.canvas.get_renderer())
                start[artist] = ("legend", tuple(plot.inverted().transform((box.x0, box.y1))))
            elif hasattr(artist, "get_xdata"):
                start[artist] = ("line", (list(artist.get_xdata()), list(artist.get_ydata())))
            elif hasattr(artist, "get_position"):
                start[artist] = ("text", tuple(artist.get_position()))
        self._drag = {"target": target, "origin": (event.x, event.y), "start": start, "delta": (0.0, 0.0)}

    def _delta(self, event) -> tuple[float, float]:
        plot = self.figure._cq_plot
        inverse = plot.inverted()
        (ax, ay), (bx, by) = inverse.transform(self._drag["origin"]), inverse.transform((event.x, event.y))
        return bx - ax, by - ay

    def _on_motion(self, event) -> None:
        if self._drag is None or event.x is None:
            return
        dx, dy = self._delta(event)
        self._drag["delta"] = (dx, dy)
        self._drag["moved"] = self._drag.get("moved") or abs(event.x - self._drag["origin"][0]) + abs(event.y - self._drag["origin"][1]) > 3
        plot = self.figure._cq_plot
        for artist, (kind, value) in self._drag["start"].items():
            if kind == "arrow":
                (ax, ay), (bx, by) = value
                artist.set_positions((ax + dx, ay + dy), (bx + dx, by + dy))
            elif kind == "legend":
                artist.set_loc("upper left")
                artist.set_bbox_to_anchor((value[0] + dx, value[1] + dy), transform=plot)
            elif kind == "line":
                xs, ys = value
                artist.set_data([x + dx for x in xs], [y + dy for y in ys])
            else:
                artist.set_position((value[0] + dx, value[1] + dy))
        for artist in self._highlights:
            artist.set_visible(False)
        self.canvas.draw_idle()

    def _on_release(self, event) -> None:
        drag, self._drag = self._drag, None
        if drag is None or not drag.get("moved"):
            return
        dx, dy = drag["delta"]
        target = drag["target"]
        fmt = self.sheet.format
        if target[0] == "annotation":
            for note in fmt.annotations:
                if note.id == target[1]:
                    note.x, note.y, note.x2, note.y2 = note.x + dx, note.y + dy, note.x2 + dx, note.y2 + dy
        elif target[0] == "legend":
            start = next((value for kind, value in drag["start"].values() if kind == "legend"), None)
            if start is not None:
                fmt.legend.position = "custom"
                fmt.legend.xy = [round(start[0] + dx, 4), round(start[1] + dy, 4)]
        self.format_changed(None)
        self.flush()

    def _on_key(self, event) -> None:
        if event.key in ("delete", "backspace") and self.selection and self.selection[0] == "annotation":
            self.sheet.format.annotations = [note for note in self.sheet.format.annotations if note.id != self.selection[1]]
            self.select(None)
            self.format_changed(None)

    # --- Drawing ------------------------------------------------------------------------------------------------

    def linked_result(self):
        result = self.project.results.get(self.sheet.result_id) if self.sheet.result_id else None
        return result.result if result is not None else None

    def draw(self) -> list[str]:
        data = self.project.data.get(self.sheet.data_id)
        self.source.setText(f"From data table: {data.name}" if data else "")
        pseudo = data is not None and data.pseudoreplicated
        self.warning.setText(f"⚠ {html.escape(PSEUDO_WARNING)}" if pseudo else "")
        self.warning.setStyleSheet(DANGER_STYLE)
        self.warning.setVisible(pseudo)
        if self.figure is None or data is None:
            self.notes.setVisible(False)
            return []
        width, height = gf.size_inches(self.sheet.format)
        available = self.page.viewport().size()
        dpi = max(60.0, min(200.0, (available.width() - 30) / width, (available.height() - 30) / height)) if available.width() > 100 else 110.0
        self.figure.set_dpi(dpi)
        self.canvas.setFixedSize(int(width * dpi), int(height * dpi))
        self._highlights = []
        self._drag = None
        notes = graphs.draw(self.figure, data, self.sheet, self.linked_result(), self.project.flags(data.id))
        self.figure.set_dpi(dpi)
        self._highlight()
        self.canvas.draw_idle()
        self.notes.setText("\n".join(notes))
        self.notes.setVisible(bool(notes))
        return notes

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.figure is not None:
            self._redraw.start()

    def export_dialog(self) -> None:
        if self.figure is None:
            self.window.status('Graphs need matplotlib: pip install "cellquant[stats]"')
            return
        dialog = GraphExportDialog(self, self.sheet)
        if dialog.exec():
            self.export(dialog.path(), dialog.kind(), dialog.dpi(), *dialog.size())

    def export(self, path, kind: str, dpi: int = 300, width=None, height=None, units=None) -> Path:
        if self.figure is None:
            raise CellQuantError('Graphs need matplotlib: pip install "cellquant[stats]"')
        self.project.flush_fits()
        data = self.project.data[self.sheet.data_id]
        written = export_figure(data, self.sheet, self.linked_result(), path, kind, dpi, width, height, units, self.project.flags(data.id))
        self.window.status(f"Saved {written}")
        self.window.log(f"graph {self.sheet.name} exported to {written} ({kind}, {dpi} dpi)")
        return written


class GraphExportDialog(QDialog):
    def __init__(self, parent, sheet: GraphSheet):
        super().__init__(parent)
        self.setWindowTitle("Export graph")
        fmt = sheet.format
        form = QFormLayout(self)
        self.format = QComboBox()
        for key, (name, _suffix, _raster) in EXPORT_FORMATS.items():
            self.format.addItem(name, key)
        self.format.setToolTip("Vector formats (SVG, PDF, EPS) stay sharp at any size and can be edited. "
                               "TIFF at 600 dpi or more is what most journals ask for as a raster.")
        self.format.currentIndexChanged.connect(lambda _index: self._format_changed())
        form.addRow("Format", self.format)
        self.dpi_box = QComboBox()
        for value in DPI_CHOICES:
            self.dpi_box.addItem(f"{value} dpi", value)
        self.dpi_box.setCurrentIndex(1)
        self.dpi_box.setToolTip("Dots per inch for PNG, TIFF and JPEG. 300 for screens and drafts, 600 for print, 1200 for line art.")
        form.addRow("Resolution", self.dpi_box)
        self.width = QDoubleSpinBox()
        self.height = QDoubleSpinBox()
        for widget, value in ((self.width, fmt.width), (self.height, fmt.height)):
            widget.setRange(1, 1000)
            widget.setDecimals(2)
            widget.setValue(float(value))
            widget.setToolTip(tips.GRAPH_SIZE + " The exported graph is the one on screen, drawn at this size: text keeps its point size.")
        self.units = QComboBox()
        self.units.addItem("mm", "mm")
        self.units.addItem("inches", "in")
        self.units.setCurrentIndex(max(0, self.units.findData(fmt.units)))
        self.units.currentIndexChanged.connect(lambda _index: self._units_changed())
        self._units = self.units.currentData()
        size = QHBoxLayout()
        size.addWidget(self.width)
        size.addWidget(QLabel("×"))
        size.addWidget(self.height)
        size.addWidget(self.units)
        form.addRow("Size", size)
        safe = "".join(" " if character in '\\/:*?"<>|' else character for character in sheet.name).strip()
        self.file = QLineEdit(str(Path.home() / f"{' '.join(safe.split())}.svg"))
        self.file.setMinimumWidth(320)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        where = QHBoxLayout()
        where.addWidget(self.file, 1)
        where.addWidget(browse)
        form.addRow("File", where)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self._format_changed()

    def _format_changed(self) -> None:
        _name, suffix, raster = EXPORT_FORMATS[self.kind()]
        self.dpi_box.setEnabled(raster)
        path = Path(self.file.text())
        self.file.setText(str(path.with_suffix(suffix)))

    def _units_changed(self) -> None:
        new = self.units.currentData()
        if new != self._units:
            factor = 1 / gf.MM_PER_INCH if new == "in" else gf.MM_PER_INCH
            for widget in (self.width, self.height):
                widget.setValue(widget.value() * factor)
            self._units = new

    def _browse(self) -> None:
        name, suffix, _raster = EXPORT_FORMATS[self.kind()]
        path, _ = QFileDialog.getSaveFileName(self, "Export graph", self.file.text(), f"{name} (*{suffix})")
        if path:
            self.file.setText(path)

    def kind(self) -> str:
        return self.format.currentData()

    def dpi(self) -> int:
        return int(self.dpi_box.currentData())

    def size(self) -> tuple[float, float, str]:
        return self.width.value(), self.height.value(), self.units.currentData()

    def path(self) -> str:
        return self.file.text()
