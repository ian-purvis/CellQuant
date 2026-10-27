"""Page layouts: several graphs on one page, for a figure, with panel letters and an optional title.

A layout is a grid of cells on a page (rows × columns, inside the margins, with a gap between cells). Each cell
holds one graph or nothing. A graph keeps its own size and font sizes; one larger than its cell is shrunk to fit
(never enlarged), and sits at the top left of its cell under its panel letter, or centred in the cell. The page is drawn from the graphs as they are
now, so a layout follows its graphs and their data tables.

SVG export nests each graph's own vector drawing in the page (ids are made unique per graph); PNG, TIFF and
JPEG draw each graph at the page's resolution. No Qt here; matplotlib is imported on first use.
"""

from __future__ import annotations

import io
import re
import string
from pathlib import Path

from cellquant.errors import CellQuantError
from cellquant.gui.quantanalysis import graphs
from cellquant.gui.quantanalysis.graph_format import MM_PER_INCH, size_inches

PT_PER_MM = 72 / MM_PER_INCH
# key: (name, width mm, height mm) in portrait
PAPERS = {
    "letter": ("US Letter (216 × 279 mm)", 215.9, 279.4),
    "a4": ("A4 (210 × 297 mm)", 210.0, 297.0),
    "custom": ("Custom size", 180.0, 240.0),
}
LABELS = {"upper": "A, B, C…", "lower": "a, b, c…", "none": "No panel letters"}
ALIGN = {"top_left": "Top left, under its letter", "center": "Centred in its cell"}
# key: (name, file suffix, raster)
EXPORT_FORMATS = {
    "svg": ("SVG (vector, for a vector editor such as Inkscape)", ".svg", False),
    "png": ("PNG", ".png", True),
    "tiff": ("TIFF, LZW compressed (high resolution)", ".tif", True),
    "jpeg": ("JPEG", ".jpg", True),
}
DEFAULT_PAGE = {
    "paper": "letter", "orientation": "portrait", "width": 180.0, "height": 240.0,  # width/height: the custom size, mm
    "rows": 2, "columns": 2, "margin": 12.0, "gap": 6.0, "align": "top_left",        # mm
    "labels": "upper", "label_size": 12.0, "label_bold": True, "title": "", "title_size": 14.0,
}


def page_options(page: dict | None) -> dict:
    """``page`` with every missing or unreadable setting taken from DEFAULT_PAGE, and numbers kept in range."""

    out = {**DEFAULT_PAGE, **(page if isinstance(page, dict) else {})}

    def number(key: str, kind=float):
        try:
            value = kind(out[key])
        except (TypeError, ValueError, OverflowError):
            return kind(DEFAULT_PAGE[key])
        return value if value == value else kind(DEFAULT_PAGE[key])  # NaN: the default

    for key, choices in (("paper", PAPERS), ("labels", LABELS), ("align", ALIGN)):
        out[key] = out[key] if isinstance(out[key], str) and out[key] in choices else DEFAULT_PAGE[key]
    out["orientation"] = "landscape" if out["orientation"] == "landscape" else "portrait"
    out["rows"] = max(1, min(6, number("rows", int)))
    out["columns"] = max(1, min(6, number("columns", int)))
    for key, low in (("width", 20.0), ("height", 20.0), ("margin", 0.0), ("gap", 0.0), ("label_size", 4.0), ("title_size", 4.0)):
        out[key] = min(2000.0, max(low, number(key)))
    out["label_bold"] = bool(out["label_bold"])
    out["title"] = str(out["title"] or "")
    return out


def page_size(page: dict) -> tuple[float, float]:
    """(width, height) of the page in mm."""

    page = page_options(page)
    if page["paper"] == "custom":
        return page["width"], page["height"]
    _name, width, height = PAPERS[page["paper"]]
    return (height, width) if page["orientation"] == "landscape" else (width, height)


def cells(page: dict) -> list[tuple[float, float, float, float]]:
    """(x, y, width, height) of each cell in mm from the page's top left, row by row."""

    page = page_options(page)
    width, height = page_size(page)
    top = page["margin"] + (page["title_size"] * 1.6 / PT_PER_MM if page["title"] else 0.0)
    inner_w = width - 2 * page["margin"] - (page["columns"] - 1) * page["gap"]
    inner_h = height - top - page["margin"] - (page["rows"] - 1) * page["gap"]
    if inner_w <= 1 or inner_h <= 1:
        raise CellQuantError("The margins and gaps leave no room for the graphs: make them smaller or the page larger.")
    cell_w, cell_h = inner_w / page["columns"], inner_h / page["rows"]
    return [(page["margin"] + column * (cell_w + page["gap"]), top + row * (cell_h + page["gap"]), cell_w, cell_h)
            for row in range(page["rows"]) for column in range(page["columns"])]


def label_text(page: dict, index: int) -> str:
    page = page_options(page)
    if page["labels"] == "none":
        return ""
    letters = string.ascii_uppercase if page["labels"] == "upper" else string.ascii_lowercase
    return letters[index % 26] * (index // 26 + 1)


def placements(layout, project) -> list[dict]:
    """Where each graph of ``layout`` goes: cell index, panel letter, its box in mm (x, y, width, height from the
    page's top left) and the scale it is drawn at (1 = its own size). Empty cells and missing graphs are skipped;
    panel letters count the filled cells only."""

    page = page_options(layout.page)
    band = page["label_size"] * 1.3 / PT_PER_MM if page["labels"] != "none" else 0.0
    out = []
    for index, (box, graph_id) in enumerate(zip(cells(page), layout.graphs)):
        graph = project.graphs.get(graph_id or "")
        if graph is None or graph.data_id not in project.data:
            continue
        x, y, width, height = box
        graph_w, graph_h = (value * MM_PER_INCH for value in size_inches(graph.format))
        room_h = max(1.0, height - band)
        scale = min(1.0, width / graph_w, room_h / graph_h)
        drawn_w, drawn_h = graph_w * scale, graph_h * scale
        centred = page["align"] == "center"
        left = x + (width - drawn_w) / 2 if centred else x
        top = y + band + ((room_h - drawn_h) / 2 if centred else 0.0)
        out.append({"cell": index, "graph": graph, "label": label_text(page, len(out)), "label_at": (left, top - band),
                    "box": (left, top, drawn_w, drawn_h), "scale": scale})
    return out


def notes(layout, project) -> list[str]:
    """What the page does not show as the graphs are: shrunk graphs, empty cells and graphs that are gone."""

    out = []
    for place in placements(layout, project):
        if place["scale"] < 0.995:
            out.append(f"{place['graph'].name} is shrunk to {place['scale'] * 100:.0f}% to fit its cell (its text too). "
                       "Make the page larger, the graph smaller, or use fewer rows or columns.")
    count = len(cells(layout.page))
    if len(layout.graphs) > count and any(layout.graphs[count:]):
        out.append("Some graphs are in cells outside the grid and are not shown: add rows or columns.")
    return out


# --- Drawing --------------------------------------------------------------------------------------------------------


def _graph_figure(project, graph):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    data = project.data[graph.data_id]
    result = project.results.get(graph.result_id or "")
    figure = Figure()
    FigureCanvasAgg(figure)
    graphs.draw(figure, data, graph, result.result if result is not None else None, project.flags(data.id))
    return figure


def _page_figure(layout, places: list[dict], dpi: float):
    """The page with its title and panel letters only (graphs are added by the caller)."""

    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    page = page_options(layout.page)
    width, height = page_size(page)
    figure = Figure(figsize=(width / MM_PER_INCH, height / MM_PER_INCH), dpi=dpi)
    FigureCanvasAgg(figure)
    figure.set_facecolor("white")
    if page["title"]:
        figure.text(0.5, 1 - page["margin"] / height, page["title"], ha="center", va="top", fontsize=page["title_size"],
                    fontweight="bold", color="#111111")
    for place in places:
        if place["label"]:
            x, y = place["label_at"]
            figure.text(x / width, 1 - y / height, place["label"], ha="left", va="top", fontsize=page["label_size"],
                        fontweight="bold" if page["label_bold"] else "normal", color="#111111")
    return figure


def render(layout, project, dpi: float = 100.0):
    """The page as an RGB image array (rows, columns, 3) at ``dpi``: for the screen and for raster export."""

    import numpy as np

    places = placements(layout, project)
    figure = _page_figure(layout, places, dpi)
    _width, height = page_size(layout.page)
    for place in places:
        x, y, _w, h = place["box"]
        buffer = io.BytesIO()
        _graph_figure(project, place["graph"]).savefig(buffer, format="png", dpi=dpi * place["scale"], facecolor="white")
        buffer.seek(0)
        from matplotlib.image import imread

        image = imread(buffer)
        top = (height - y - h) / MM_PER_INCH * dpi  # figimage counts pixels from the bottom left
        figure.figimage(image, xo=round(x / MM_PER_INCH * dpi), yo=round(top), origin="upper", zorder=1)
    figure.canvas.draw()
    return np.asarray(figure.canvas.buffer_rgba())[..., :3].copy()


_ID = re.compile(r'\bid="([^"]+)"')
_REF = re.compile(r'(url\(#|href="#)([^)"]+)')


def _nested_svg(svg: str, prefix: str, box_pt: tuple[float, float, float, float]) -> str:
    """A graph's SVG as an <svg> element placed at ``box_pt`` on the page, with its ids made unique."""

    body = svg[svg.index("<svg"):]
    head_end = body.index(">") + 1
    head, inner = body[:head_end], body[head_end:body.rindex("</svg>")]
    view_box = re.search(r'viewBox="([^"]+)"', head)
    inner = _ID.sub(lambda match: f'id="{prefix}{match.group(1)}"', inner)
    inner = _REF.sub(lambda match: f"{match.group(1)}{prefix}{match.group(2)}", inner)
    x, y, width, height = box_pt
    return (f'<svg x="{x:.3f}" y="{y:.3f}" width="{width:.3f}" height="{height:.3f}" '
            f'viewBox="{view_box.group(1) if view_box else f"0 0 {width} {height}"}" preserveAspectRatio="xMidYMid meet" '
            f'overflow="hidden">{inner}</svg>')


def svg_text(layout, project) -> str:
    """The page as one SVG document: every graph stays vector."""

    places = placements(layout, project)
    figure = _page_figure(layout, places, 72.0)
    buffer = io.StringIO()
    figure.savefig(buffer, format="svg", facecolor="white")
    page = buffer.getvalue()
    nested = []
    for number, place in enumerate(places, start=1):
        graph_buffer = io.StringIO()
        _graph_figure(project, place["graph"]).savefig(graph_buffer, format="svg", facecolor="white")
        box = tuple(value * PT_PER_MM for value in place["box"])
        nested.append(_nested_svg(graph_buffer.getvalue(), f"panel{number}-", box))
    end = page.rindex("</svg>")
    return page[:end] + "".join(nested) + page[end:]


def export(layout, project, path: str | Path, kind: str, dpi: int = 600) -> Path:
    """Save the page as SVG, PNG, TIFF or JPEG. Returns the path written."""

    if kind not in EXPORT_FORMATS:
        raise CellQuantError(f"Page layouts export as {', '.join(EXPORT_FORMATS)}, not {kind}.")
    path = Path(path)
    try:
        if kind == "svg":
            path.write_text(svg_text(layout, project), encoding="utf-8")
            return path
        from PIL import Image

        image = Image.fromarray(render(layout, project, dpi))
        options = {"dpi": (dpi, dpi)}
        if kind == "tiff":
            options["compression"] = "tiff_lzw"
        elif kind == "jpeg":
            options["quality"] = 95
        image.save(path, format={"png": "PNG", "tiff": "TIFF", "jpeg": "JPEG"}[kind], **options)
    except OSError as exc:
        raise CellQuantError(f"{path} could not be saved: {exc}") from exc
    return path
