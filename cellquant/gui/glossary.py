"""The output glossary as a tree of terms, and the user's own data for each term.

No interface code here. ``parse`` turns OUTPUT_GLOSSARY.md into entries (``##`` branches, deeper headings
nested below; a heading made only of backticked names is a term, any other heading a group). A hidden
``<!-- data: kind path -->`` line under a file heading says where that file's data is; terms below it inherit it.
``page_html`` renders an entry's text, and ``data_html`` the matching values from a results folder, read lazily:
only the file of the chosen term, and of a CSV only the columns it needs.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

FIELDS = ("In short", "What it means", "How it's calculated", "Good to know", "For experts", "Related")
PLACEHOLDERS = ("<image>", "<id>", "<marker>", "<measurement>", "<report>", "<column>", "<unit>", "<analysis>", "<recipe>")
_HEADING = re.compile(r"^(#{2,6})\s+(.*?)\s*$")
_DATA = re.compile(r"^<!--\s*data:\s*(\w+)\s+(.+?)\s*-->\s*$")
_FIELD = re.compile(r"^\*\*(" + "|".join(re.escape(name) for name in FIELDS) + r"):\*\*\s*(.*)$")
_TERM = re.compile(r"^(`[^`]+`)(\s*,\s*`[^`]+`)*(\s+(—|-)\s+(.*))?$")
LIST_LIMIT = 20  # list items shown before "… N more"
VALUE_LIMIT = 70  # characters of a value shown in one line


@dataclass
class Entry:
    title: str  # as shown in the tree
    names: list[str]  # backticked names (empty for a group)
    level: int
    intro: str = ""  # Markdown before the first field
    fields: dict[str, str] = field(default_factory=dict)
    data: tuple[str, str] | None = None  # (kind, path), inherited from the file heading
    data_owner: Entry | None = None  # the entry whose heading set ``data``
    children: list[Entry] = field(default_factory=list)
    parent: Entry | None = None
    label: str = ""  # plain words after a file name, e.g. "the object table"

    @property
    def is_term(self) -> bool:
        return bool(self.names)

    def path(self) -> list[Entry]:
        chain, entry = [], self
        while entry is not None and entry.level > 1:
            chain.insert(0, entry)
            entry = entry.parent
        return chain

    def walk(self):
        for child in self.children:
            yield child
            yield from child.walk()

    def search_text(self) -> str:
        return " ".join([self.title, self.label, self.intro, *self.fields.values()]).casefold()


def parse(markdown: str) -> Entry:
    root = Entry("Glossary", [], 1)
    stack = [root]
    current = root
    field_name = None
    for line in markdown.splitlines():
        heading = _HEADING.match(line)
        if heading:
            level, text = len(heading.group(1)), heading.group(2)
            while stack[-1].level >= level:
                stack.pop()
            parent = stack[-1]
            names, label = _names(text)
            title = ", ".join(names) if names else re.sub(r"[*`]", "", text)
            current = Entry(title, names, level, parent=parent, data=parent.data, data_owner=parent.data_owner, label=label)
            parent.children.append(current)
            stack.append(current)
            field_name = None
            continue
        data = _DATA.match(line)
        if data:
            current.data = (data.group(1), data.group(2))
            current.data_owner = current
            continue
        if current is root:
            root.intro += line + "\n"
            continue
        found = _FIELD.match(line)
        if found:
            field_name = found.group(1)
            current.fields[field_name] = found.group(2) + "\n"
        elif field_name:
            current.fields[field_name] += line + "\n"
        else:
            current.intro += line + "\n"
    for entry in root.walk():
        entry.intro = entry.intro.strip("\n").rstrip()
        entry.fields = {key: value.strip("\n").rstrip() for key, value in entry.fields.items()}
    return root


def _names(text: str) -> tuple[list[str], str]:
    match = _TERM.match(text)
    if not match:
        return [], ""
    names = re.findall(r"`([^`]+)`", text.split(" — ")[0].split(" - ")[0])
    return names, (match.group(5) or "").strip()


def name_regex(name: str) -> re.Pattern:
    """A term name as a pattern: ``<marker>_pos`` matches ``af647_pos``, ``report_N_count`` matches ``report_2_count``."""

    pattern = re.escape(name)
    for placeholder in PLACEHOLDERS:
        pattern = pattern.replace(re.escape(placeholder), r"[A-Za-z0-9_.\- ]+?")
    pattern = re.sub(r"(?<=_)N(?=_|$)", r"\\d+", pattern)
    return re.compile(rf"^{pattern}$")


def matching(names: list[str], available) -> list[str]:
    """The available names (columns, keys) one of ``names`` covers, exact names first."""

    available = list(available)
    exact = [name for name in names if name in available]
    if exact:
        return exact
    patterns = [name_regex(name) for name in names]
    return [item for item in available if any(pattern.match(str(item)) for pattern in patterns)]


# --- Text -----------------------------------------------------------------------------------------------------

def markdown_html(text: str, terms: set[str] = frozenset()) -> str:
    """The small Markdown subset the glossary uses: paragraphs, bullets, tables, indented equations, inline
    code (a link when it names a term), bold and italic."""

    out: list[str] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        if line.startswith("    ") or line.startswith("\t"):
            block = []
            while index < len(lines) and (lines[index].startswith(("    ", "\t")) or not lines[index].strip()):
                block.append(lines[index].strip())
                index += 1
            out.append("<p class='eq'>" + "<br>".join(html.escape(item) for item in block if item) + "</p>")
            continue
        if line.startswith("```"):
            index += 1
            block = []
            while index < len(lines) and not lines[index].startswith("```"):
                block.append(html.escape(lines[index]))
                index += 1
            index += 1
            out.append("<pre>" + "\n".join(block) + "</pre>")
            continue
        if line.lstrip().startswith("|"):
            rows = []
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                cells = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-+:?", cell) for cell in cells):
                    rows.append(cells)
                index += 1
            body = "".join(
                "<tr>" + "".join(f"<{tag}>{_inline(cell, terms)}</{tag}>" for cell in row) + "</tr>"
                for number, row in enumerate(rows)
                for tag in ["th" if number == 0 else "td"]
            )
            out.append(f"<table class='grid'>{body}</table>")
            continue
        if re.match(r"\s*[-*] ", line):
            items = []
            while index < len(lines) and lines[index].strip():
                item = lines[index]
                if re.match(r"\s*[-*] ", item):
                    items.append(re.sub(r"^\s*[-*] ", "", item))
                elif items:
                    items[-1] += " " + item.strip()
                index += 1
            out.append("<ul>" + "".join(f"<li>{_inline(item, terms)}</li>" for item in items) + "</ul>")
            continue
        paragraph = []
        while index < len(lines) and lines[index].strip() and not lines[index].startswith(("    ", "\t", "|", "```")) \
                and not re.match(r"\s*[-*] ", lines[index]):
            paragraph.append(lines[index].strip())
            index += 1
        out.append(f"<p>{_inline(' '.join(paragraph), terms)}</p>")
    return "\n".join(out)


def _inline(text: str, terms) -> str:
    parts = re.split(r"(`[^`]+`)", text)
    out = []
    for part in parts:
        if part.startswith("`") and part.endswith("`") and len(part) > 1:
            name = part[1:-1]
            code = f"<code>{html.escape(name)}</code>"
            out.append(f"<a href='term:{html.escape(name)}'>{code}</a>" if name in terms else code)
        else:
            escaped = html.escape(part)
            escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
            escaped = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", escaped)
            out.append(escaped)
    return "".join(out)


# --- Data -----------------------------------------------------------------------------------------------------

@dataclass
class Source:
    """One results set to read from: the experiment folder, a set folder (working/ or runs/run_…), one image."""

    experiment: Path
    folder: Path
    image_id: str | None

    def path(self, pattern: str) -> Path:
        relative = pattern
        base = self.experiment
        if relative.startswith("run/"):
            relative, base = relative[4:], self.folder
        image = self.image_id or ""
        for placeholder in ("<image>", "<id>"):
            relative = relative.replace(placeholder, image)
        return base / relative


def result_sets(experiment: Path, working: Path | None) -> list[tuple[str, Path]]:
    """``[("Latest results", working), ("Run 23 Oct 18:55", runs/run_…), …]``, newest run first."""

    sets = []
    if working is not None:
        sets.append(("Latest results", working))
    runs = []
    for run_json in (experiment / "runs").glob("*/run.json"):
        try:
            started = json.loads(run_json.read_text(encoding="utf-8")).get("started_at") or ""
        except (OSError, ValueError):
            started = ""
        runs.append((started, run_json.parent))
    for started, folder in sorted(runs, key=lambda item: item[0] or item[1].name, reverse=True):
        sets.append((f"Run {_when(started) or folder.name}", folder))
    return sets


def _when(stamp: str) -> str:
    try:
        return datetime.fromisoformat(stamp).astimezone().strftime("%d %b %H:%M")
    except (TypeError, ValueError):
        return ""


@dataclass
class DataPage:
    html: str
    histograms: list[dict] = field(default_factory=list)  # {"values", "cutoff", "log"} drawn as cqhist:<n>
    file: Path | None = None  # what "Open file" opens


def data_html(entry: Entry, source: Source, *, choice: str | None = None, drill: list | None = None) -> DataPage:
    """The values of ``entry`` from ``source``, short. ``choice`` picks one of several matching columns or keys,
    ``drill`` a part inside a JSON value."""

    if entry.data is None:
        return DataPage("<p class='muted'>This entry has no file of its own. Pick a file, column or key below it.</p>")
    kind, pattern = entry.data
    path = source.path(pattern)
    if "<" in pattern and not source.image_id:
        return DataPage("<p class='muted'>Pick an image above.</p>")
    if not path.exists():
        return DataPage(f"<p class='muted'>Not in this results set: <code>{html.escape(_short_path(path, source))}</code>. "
                        "Run the image, or pick another image or run above.</p>")
    file_entry = entry.data_owner is entry
    try:
        if kind == "csv":
            return _csv_file(path, source) if file_entry else _csv_term(entry, path, source, choice)
        if kind in ("json", "yaml"):
            return _json_page(entry, path, source, kind, file_entry, choice, drill or [])
        if kind == "tif":
            return _tif_page(path, source)
        if kind == "log":
            return _log_page(path)
        if kind == "folder":
            return _folder_page(path)
    except Exception as error:  # a damaged file must not break the glossary
        return DataPage(f"<p class='muted'>Could not read <code>{html.escape(path.name)}</code>: {html.escape(str(error))}</p>", file=path)
    return DataPage("")


def _short_path(path: Path, source: Source) -> str:
    try:
        return path.relative_to(source.experiment).as_posix()
    except ValueError:
        return str(path)


def _file_line(path: Path, source: Source) -> str:
    stat = path.stat()
    when = datetime.fromtimestamp(stat.st_mtime).strftime("%d %b %Y %H:%M")
    return (f"<p class='muted'>From <code>{html.escape(_short_path(path, source))}</code> · {_size(stat.st_size)} · "
            f"saved {when} · <a href='open:file'>Open file</a></p>")


def _size(size: float) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


def _csv_file(path: Path, source: Source) -> DataPage:
    import pandas as pd

    frame = pd.read_csv(path, nrows=1)
    rows = _count_rows(path)
    out = [_file_line(path, source), f"<p><b>{rows}</b> rows × <b>{len(frame.columns)}</b> columns. First row:</p><table class='grid'>"]
    first = frame.iloc[0] if len(frame) else {}
    for column in frame.columns:
        value = first[column] if len(frame) else ""
        out.append(f"<tr><td><a href='term:{html.escape(column)}'><code>{html.escape(column)}</code></a></td>"
                   f"<td>{html.escape(_one_line(value))}</td></tr>")
    out.append("</table>")
    return DataPage("".join(out), file=path)


def _count_rows(path: Path) -> int:
    with path.open("rb") as handle:
        return max(0, sum(chunk.count(b"\n") for chunk in iter(lambda: handle.read(1 << 20), b"")) - 1)


def csv_columns(path: Path) -> list[str]:
    import pandas as pd

    return [str(column) for column in pd.read_csv(path, nrows=0).columns]


def read_columns(path: Path, columns: list[str]):
    import pandas as pd

    header = csv_columns(path)
    return pd.read_csv(path, usecols=[column for column in columns if column in header])


def _csv_term(entry: Entry, path: Path, source: Source, choice: str | None) -> DataPage:
    header = csv_columns(path)
    columns = matching(entry.names, header)
    if all(name in PLACEHOLDERS for name in entry.names) and entry.parent is not None:
        # A catch-all such as <measurement>: only the columns no other entry of this file explains.
        others = [item for item in entry.parent.children if item is not entry and item.names]
        columns = [column for column in columns if not any(matching(item.names, [column]) for item in others)]
    if not columns:
        return DataPage(f"<p class='muted'>This file has no <code>{html.escape(entry.title)}</code> column "
                        "(it only appears with some settings).</p>" + _file_line(path, source), file=path)
    column = choice if choice in columns else columns[0]
    out = [_file_line(path, source)]
    if len(columns) > 1:
        out.append("<p>Columns: " + " · ".join(
            f"<b>{html.escape(item)}</b>" if item == column else f"<a href='choice:{html.escape(item)}'>{html.escape(item)}</a>"
            for item in columns) + "</p>")
    keep = [item for item in ("object_id",) if item in header and item != column]
    frame = read_columns(path, keep + [column])
    values = frame[column]
    page = DataPage("", file=path)
    example = EXAMPLES.get(_example_key(entry))
    if example is not None:
        try:
            out.append(example(source, column, frame, page))
        except Exception:  # an example is extra; the plain view below still shows the values
            pass
    out.append(_summary(values, page))
    out.append(f"<p><a href='values:{html.escape(column)}'>See all {len(values)} values</a></p>")
    page.html = "".join(out)
    return page


def _example_key(entry: Entry) -> str:
    return entry.names[0] if entry.names else entry.title


def _summary(values, page: DataPage, cutoff: float | None = None) -> str:
    import pandas as pd

    n = len(values)
    blank = int(values.isna().sum())
    kinds = set(values.dropna().map(type)) if n else set()
    if n and (values.dtype == bool or kinds <= {bool, np.bool_} and kinds):
        true = int((values == True).sum())  # noqa: E712 - pandas comparison
        false = int((values == False).sum())  # noqa: E712
        return (f"<p><b>{n}</b> rows: <b>{true}</b> True ({_pct(true, n)}), <b>{false}</b> False ({_pct(false, n)})"
                + (f", {blank} blank" if blank else "") + "</p>")
    if n == 1:
        return f"<p>Value: <b>{html.escape(_one_line(values.iloc[0]))}</b></p>"
    numeric = pd.to_numeric(values, errors="coerce")
    if n and numeric.notna().sum() == values.notna().sum() and numeric.notna().any():
        finite = numeric.dropna()
        text = (f"<p><b>{n}</b> rows · smallest <b>{_num(finite.min())}</b> · median <b>{_num(finite.median())}</b> · "
                f"mean <b>{_num(finite.mean())}</b> · largest <b>{_num(finite.max())}</b>"
                + (f" · {blank} blank" if blank else "") + "</p>")
        if finite.nunique() > 2 and len(finite) >= 5:
            log = bool(finite.min() > 0 and finite.max() / max(finite.min(), 1e-12) > 100)
            page.histograms.append({"values": finite.to_numpy(float), "cutoff": cutoff, "log": log})
            note = ", log scale" if log else ""
            text += (f"<p><img src='cqhist:{len(page.histograms) - 1}'><br><span class='muted'>How the values spread, "
                     f"from {_num(finite.min())} (left) to {_num(finite.max())} (right){note}; taller bar = more objects"
                     f"{', red = above the cutoff' if cutoff is not None else ''}.</span></p>")
        return text
    counts = values.astype(object).where(values.notna(), "(blank)").astype(str).value_counts()
    shown = " · ".join(f"{html.escape(_one_line(key))}: <b>{count}</b>" for key, count in counts.head(6).items())
    more = f" · {len(counts) - 6} other values" if len(counts) > 6 else ""
    return f"<p><b>{n}</b> rows · {shown}{more}</p>"


def _pct(part: float, whole: float) -> str:
    return f"{100 * part / whole:.1f} %" if whole else "–"


def _num(value) -> str:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return html.escape(str(value))
    if not np.isfinite(value):
        return "blank"
    if value == int(value) and abs(value) < 1e12:
        return f"{int(value)}"
    return f"{value:.4g}" if abs(value) < 1e5 else f"{value:.3e}"


def _one_line(value) -> str:
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, dict):
        return f"{len(value)} entries" if value else "(empty)"
    if isinstance(value, list):
        return f"list of {len(value)}" if value else "(empty list)"
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "(blank)"
    if isinstance(value, float):
        return _num(value)
    text = value if isinstance(value, str) else json.dumps(value)
    return text if len(text) <= VALUE_LIMIT else text[: VALUE_LIMIT - 1] + "…"


def load_structured(path: Path, kind: str):
    text = path.read_text(encoding="utf-8")
    if kind == "yaml":
        import yaml

        return yaml.safe_load(text)
    return json.loads(text)


def _json_page(entry: Entry, path: Path, source: Source, kind: str, file_entry: bool, choice, drill: list) -> DataPage:
    document = load_structured(path, kind)
    page = DataPage("", file=path)
    out = [_file_line(path, source).replace("Open file</a>", "Open file</a> · <a href='whole:'>Show whole file…</a>")]
    value, where = document, []
    record = False  # the images entry itself: the chosen image's record
    if path.name == "experiment.json" and isinstance(document, dict) and source.image_id:
        under_images = any(item.names == ["images"] for item in entry.path())
        if under_images:
            value = next((item for item in document.get("images", []) if item.get("image_id") == source.image_id), {})
            where = [f"images (image {source.image_id})"]
            record = entry.names == ["images"]
    if not file_entry and entry.is_term and not record:
        keys = matching(entry.names, value.keys()) if isinstance(value, dict) else []
        if not keys:
            found = _find_key(value, entry.names)
            if found is None:
                return DataPage("".join(out) + f"<p class='muted'><code>{html.escape(entry.title)}</code> is not in this "
                                "file (it is only written with some settings).</p>", file=path)
            keys, value, where = [found[0]], found[1], where + found[2]
        key = choice if choice in keys else keys[0]
        if len(keys) > 1:
            out.append("<p>Keys: " + " · ".join(
                f"<b>{html.escape(item)}</b>" if item == key else f"<a href='choice:{html.escape(item)}'>{html.escape(item)}</a>"
                for item in keys) + "</p>")
        value, where = value[key], where + [key]
    for step in drill:
        value = value[step]
    crumbs = where + [str(step) for step in drill]
    if drill:
        back = "".join(f"<a href='drill:{index}'>{html.escape(str(part))}</a> › " for index, part in enumerate(drill[:-1]))
        out.append(f"<p class='muted'><a href='drill:-1'>{html.escape(where[-1] if where else path.name)}</a> › {back}"
                   f"<b>{html.escape(str(drill[-1]))}</b></p>")
    example = EXAMPLES.get(_example_key(entry)) if not drill else None
    if example is not None:
        try:
            out.append(example(source, crumbs[-1] if crumbs else "", value, page))
        except Exception:
            pass
    out.append(_value_html(value, drill))
    page.html = "".join(out)
    return page


def _find_key(value, names, where=None, depth=0):
    """A key anywhere inside ``value`` (one level of lists too): (key, parent, path)."""

    where = where or []
    if depth > 3:
        return None
    if isinstance(value, dict):
        keys = matching(names, value.keys())
        if keys:
            return keys[0], value, where
        for key, child in value.items():
            found = _find_key(child, names, where + [key], depth + 1)
            if found:
                return found[0], found[1], found[2]
    return None


def _value_html(value, drill: list) -> str:
    def link(key) -> str:
        return "drill:+" + html.escape(json.dumps(key))

    if isinstance(value, dict):
        if not value:
            return "<p>(empty)</p>"
        rows = []
        for key, item in value.items():
            shown = html.escape(_one_line(item))
            if isinstance(item, (dict, list)) and item:
                shown = f"<a href='{link(key)}'>{shown} ›</a>"
            rows.append(f"<tr><td><code>{html.escape(str(key))}</code></td><td>{shown}</td></tr>")
        return f"<table class='grid'>{''.join(rows)}</table>"
    if isinstance(value, list):
        if not value:
            return "<p>(empty list)</p>"
        if all(isinstance(item, dict) for item in value):
            columns = list(dict.fromkeys(key for item in value[:LIST_LIMIT] for key in item))[:8]
            head = "<tr><th>#</th>" + "".join(f"<th>{html.escape(str(column))}</th>" for column in columns) + "</tr>"
            body = "".join(
                f"<tr><td><a href='{link(index)}'>{index + 1}</a></td>"
                + "".join(f"<td>{html.escape(_one_line(item.get(column)))}</td>" for column in columns) + "</tr>"
                for index, item in enumerate(value[:LIST_LIMIT])
            )
            more = f"<p class='muted'>… {len(value) - LIST_LIMIT} more (Show whole file)</p>" if len(value) > LIST_LIMIT else ""
            return f"<p>{len(value)} items</p><table class='grid'>{head}{body}</table>{more}"
        shown = ", ".join(html.escape(_one_line(item)) for item in value[:LIST_LIMIT])
        more = f", … {len(value) - LIST_LIMIT} more" if len(value) > LIST_LIMIT else ""
        return f"<p>{len(value)} items: {shown}{more}</p>"
    return f"<p class='value'>{html.escape(_one_line(value) if not isinstance(value, str) else value)}</p>"


def _tif_page(path: Path, source: Source) -> DataPage:
    out = [_file_line(path, source)]
    try:
        import tifffile

        with tifffile.TiffFile(path) as handle:
            labels = handle.asarray()
        n = int(len(np.unique(labels[labels > 0])))
        shape = " × ".join(str(size) for size in labels.shape)
        out.append(f"<p>{shape} pixels ({labels.dtype}). <b>{n}</b> objects: each object's pixels hold its "
                   f"<code>object_id</code>, background is 0.</p>")
    except Exception:
        out.append("<p class='muted'>Open it in napari or Fiji to see the objects.</p>")
    others = sorted(path.parent.glob("*" + path.name.split(source.image_id or "\0")[-1])) if source.image_id else []
    if len(others) > 1:
        out.append(f"<p class='muted'>{len(others)} images have this file in this results set.</p>")
    return DataPage("".join(out), file=path)


def _log_page(path: Path) -> DataPage:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    tail = "\n".join(html.escape(line) for line in lines[-30:])
    note = f"Last 30 of {len(lines)} lines" if len(lines) > 30 else f"{len(lines)} lines"
    return DataPage(f"<p class='muted'>{note} · <a href='open:file'>Open file</a></p><pre>{tail}</pre>", file=path)


def _folder_page(path: Path) -> DataPage:
    count, total = 0, 0
    for item in path.rglob("*"):
        if item.is_file():
            count += 1
            total += item.stat().st_size
            if count >= 20000:
                break
    more = "+" if count >= 20000 else ""
    return DataPage(f"<p><b>{count}{more}</b> files, {_size(total)} · <a href='open:file'>Open folder</a></p>", file=path)


# --- Worked examples: the term's equation with the user's own numbers ---------------------------------------------

def _provenance(source: Source) -> dict:
    path = source.path("run/provenance/<image>.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _live(text: str) -> str:
    return f"<div class='live'>{text}</div>"


def _eq(text: str) -> str:
    return f"<span class='eqi'>{text}</span>"


def _example_area(source: Source, column: str, frame, page: DataPage) -> str:
    provenance = _provenance(source)
    x, y = provenance.get("pixel_size_x_um"), provenance.get("pixel_size_y_um")
    row = frame.dropna(subset=[column]).iloc[0]
    label = f"Object {int(row['object_id'])}" if "object_id" in frame else "The first object"
    if not x or not y:
        return _live(f"{label}: no pixel size, so the area is the pixel count: {_eq(_num(row[column]) + ' px²')}")
    pixels = round(float(row[column]) / (x * y))
    dims = provenance.get("analysis_dimensions") == 3
    where = " in its largest slice" if dims else ""
    return _live(f"{label} covers <b>{pixels}</b> pixels{where}; this image's pixels are {x:.4g} × {y:.4g} µm:<br>"
                 + _eq(f"{pixels} × {x:.4g} µm × {y:.4g} µm = {_num(row[column])} µm²"))


def _example_volume(source: Source, column: str, frame, page: DataPage) -> str:
    provenance = _provenance(source)
    x, y, z = (provenance.get(key) for key in ("pixel_size_x_um", "pixel_size_y_um", "pixel_size_z_um"))
    row = frame.dropna(subset=[column]).iloc[0]
    if not (x and y and z):
        return _live(f"No full pixel size, so the volume is a voxel count: {_eq(_num(row[column]))}")
    voxels = round(float(row[column]) / (x * y * z))
    return _live(f"Object {int(row['object_id'])} covers <b>{voxels}</b> voxels of {x:.3g} × {y:.3g} × {z:.3g} µm:<br>"
                 + _eq(f"{voxels} × {x:.3g} × {y:.3g} × {z:.3g} µm³ = {_num(row[column])} µm³"))


def _cutoff(source: Source, column: str) -> tuple[dict | None, float | None]:
    for item in _provenance(source).get("classification_definitions") or []:
        if item.get("id") == column:
            cut = (item.get("image_thresholds") or {}).get(str(source.image_id), item.get("threshold"))
            return item, cut
    return None, None


def _example_positive(source: Source, column: str, frame, page: DataPage) -> str:
    item, cut = _cutoff(source, column)
    if item is None or cut is None:
        return ""
    measurement = item.get("measurement")
    path = source.path("run/measurements/<image>.csv")
    values = read_columns(path, ["object_id", measurement, column])
    if measurement not in values:
        return ""
    above = item.get("comparison", "above") != "at_least"
    sign_yes, sign_no = (">", "≤") if above else ("≥", "<")
    lines = [f"Cutoff for <b>{html.escape(item.get('name', column))}</b> in this image: <b>{_num(cut)}</b> "
             f"(on <code>{html.escape(measurement)}</code>)"]
    for wanted in (True, False):
        rows = values[values[column] == wanted]  # noqa: E712
        if len(rows):
            row = rows.iloc[0]
            sign = sign_yes if wanted else sign_no
            verdict = "positive" if wanted else "negative"
            lines.append(f"Object {int(row['object_id'])}: " + _eq(f"{_num(row[measurement])} {sign} {_num(cut)} → {verdict}"))
    # The spread of the measurement with the cutoff drawn in is the clearest picture of a positive call.
    finite = values[measurement].dropna().to_numpy(float)
    picture = ""
    if len(finite) >= 5:
        log = bool(finite.min() > 0 and finite.max() / max(finite.min(), 1e-12) > 100)
        page.histograms.append({"values": finite, "cutoff": float(cut), "log": log})
        picture = (f"<p><img src='cqhist:{len(page.histograms) - 1}'><br><span class='muted'>"
                   f"<code>{html.escape(measurement)}</code> of every object, from {_num(finite.min())} (left) to "
                   f"{_num(finite.max())} (right); red = above the cutoff (dashed line).</span></p>")
    return _live("<br>".join(lines)) + picture


def _example_phenotype(source: Source, column: str, frame, page: DataPage) -> str:
    definitions = _provenance(source).get("classification_definitions") or []
    ids = [item.get("id") for item in definitions]
    path = source.path("run/measurements/<image>.csv")
    values = read_columns(path, ["object_id", *ids, column])
    row = values.dropna(subset=[column]).iloc[0]
    parts = []
    for item in definitions:
        state = row.get(item.get("id"))
        mark = "?" if state is None or (isinstance(state, float) and np.isnan(state)) else ("+" if bool(state) else "-")
        parts.append(f"{html.escape(item.get('id'))} = {html.escape(str(state))} → {html.escape(item.get('name', ''))}{mark}")
    return _live(f"Object {int(row['object_id'])}: " + "; ".join(parts) + "<br>" + _eq(f"phenotype = {html.escape(str(row[column]))}"))


def _summary_row(source: Source):
    import pandas as pd

    return pd.read_csv(source.path("run/summaries/<image>.csv")).iloc[0]


def _example_report(source: Source, column: str, frame, page: DataPage) -> str:
    match = re.match(r"report_(\d+)_", column)
    if not match:
        return ""
    row = _summary_row(source)
    prefix = f"report_{match.group(1)}"
    count, denominator = row.get(f"{prefix}_count"), row.get(f"{prefix}_denominator")
    names = {item.get("id"): item.get("name") for item in _provenance(source).get("classification_definitions") or []}

    def readable(expression) -> str:
        words = re.split(r"(\W+)", str(expression))
        text = "".join(f"{names[word]}+" if word in names else word for word in words)
        return text.replace("all_objects", "all objects").replace(" AND ", " and ").replace(" OR ", " or ")

    question = f"{readable(row.get(f'{prefix}_numerator'))} of {readable(row.get(f'{prefix}_denominator_expression'))}"
    if denominator in (0, None) or (isinstance(denominator, float) and np.isnan(denominator)):
        return _live(f"{html.escape(prefix)} ({html.escape(question)}): the denominator is 0, so the percent is blank.")
    return _live(f"{html.escape(prefix)} asks: <b>{html.escape(question)}</b><br>"
                 + _eq(f"{_num(count)} ÷ {_num(denominator)} × 100 = {_num(row.get(f'{prefix}_percent'))} %"))


def _example_total(source: Source, column: str, frame, page: DataPage) -> str:
    path = source.path("run/measurements/<image>.csv")
    header = csv_columns(path)
    flags = [name for name in ("excluded", "unmeasured", "in_count_area") if name in header]
    table = read_columns(path, ["object_id", *flags])
    n = len(table)
    deleted = int(table["excluded"].fillna(False).astype(bool).sum()) if "excluded" in table else 0
    outside = int((~table["in_count_area"].fillna(True).astype(bool)).sum()) if "in_count_area" in table else 0
    total = _summary_row(source).get("total_objects")
    unmeasured = _summary_row(source).get("n_unmeasured", 0) or 0
    return _live(f"{n} objects in the object table − {deleted} deleted − {outside} outside the count area − "
                 f"{_num(unmeasured)} unmeasured:<br>" + _eq(f"total_objects = {_num(total)}"))


def _example_median_area(source: Source, key: str, value, page: DataPage) -> str:
    path = source.path("run/measurements/<image>.csv")
    table = read_columns(path, ["area", "excluded", "unmeasured", "in_count_area"])
    counted = np.ones(len(table), dtype=bool)
    for column in ("excluded", "unmeasured"):
        if column in table:
            counted &= ~table[column].fillna(False).astype(bool).to_numpy()
    if "in_count_area" in table:
        counted &= table["in_count_area"].fillna(True).astype(bool).to_numpy()
    areas = table.loc[counted, "area"].dropna().sort_values().to_numpy()
    if not len(areas):
        return ""
    middle = len(areas) // 2
    how = (f"number {middle + 1} of {len(areas)} sorted areas" if len(areas) % 2
           else f"the mean of numbers {middle} and {middle + 1} of {len(areas)} sorted areas")
    return _live(f"Sort the area of every counted object from small to large; the median is {how}:<br>" + _eq(f"median_area = {_num(value)}"))


def _example_fraction(source: Source, key: str, value, page: DataPage) -> str:
    try:
        return _live(_eq(f"{_num(value)} × 100 = {float(value) * 100:.1f} % of objects touch the image edge"))
    except (TypeError, ValueError):
        return ""


def _example_share(source: Source, column: str, frame, page: DataPage) -> str:
    """percent (reports) and percent_of_objects (classifications): a row's count over its total."""

    import pandas as pd

    table = pd.read_csv(page.file)
    if not len(table):
        return ""
    row = table.iloc[0]
    if column == "percent" and {"count", "denominator_count"} <= set(table.columns):
        return _live(f"{html.escape(str(row.get('report', 'The first row')))}: "
                     + _eq(f"{_num(row['count'])} ÷ {_num(row['denominator_count'])} × 100 = {_num(row['percent'])} %"))
    if column == "percent_of_objects" and "count" in table.columns and row[column]:
        total = round(float(row["count"]) * 100 / float(row[column]))
        return _live(f"{html.escape(str(row['phenotype']))}: "
                     + _eq(f"{_num(row['count'])} ÷ {total} objects × 100 = {_num(row[column])} %"))
    return ""


EXAMPLES = {
    "area": _example_area,
    "volume": _example_volume,
    "<marker>_pos": _example_positive,
    "phenotype": _example_phenotype,
    "report_N_percent": _example_report,
    "report_N_count": _example_report,
    "report_N_denominator": _example_report,
    "total_objects": _example_total,
    "median_area": _example_median_area,
    "fraction_touching_border": _example_fraction,
    "percent": _example_share,
    "percent_of_objects": _example_share,
}
