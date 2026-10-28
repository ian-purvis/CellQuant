"""The statistics reference: one page per analysis and per term, read from STATS_REFERENCE.md.

No interface code here. ``parse`` turns the Markdown into a tree of pages (``##`` branches, deeper headings
below them; a heading with fields is a page, one without is a group). Hidden lines under a heading:

- ``<!-- opens: compare:unpaired_t, compare -->`` the results sheets that open this page (see ``sheet_key``);
- ``<!-- equations: t tests -->`` a section of STATISTICS.md shown under "Full equations";
- ``<!-- aliases: P, p value -->`` other names a term is found by (results-sheet row labels, search).

``[[Page]]`` and ``[[Page|text]]`` link to another page, ``[text](https://…)`` to a web page. A fenced
``example`` block is a worked example: data lines (``Name: 1, 2, , 4``, a blank cell left empty), then
``>>>`` lines of Python run with the ``cellquant.stats`` names, the last one giving the result, then
``key = value`` lines: what the result's ``values[key]`` is, to the digits shown. ``run_example`` runs one
and checks it, so the documented numbers are tested and a user can check them against the live engine.
"""

from __future__ import annotations

import html
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from cellquant.gui import glossary

FIELDS = (
    "In short",
    "When to use it",
    "What is done to your data",
    "How it's calculated",
    "Worked example",
    "Interpreting the results",
    "Options",
    "In detail",
    "Where you see it",
    "Checked against",
    "References",
    "Learn more",
    "Related",
)
_HEADING = re.compile(r"^(#{2,6})\s+(.*?)\s*$")
_HIDDEN = re.compile(r"^<!--\s*(opens|equations|aliases):\s*(.+?)\s*-->\s*$")
_FIELD = re.compile(r"^\*\*(" + "|".join(re.escape(name) for name in FIELDS) + r"):\*\*\s*(.*)$")
_EXAMPLE = re.compile(r"^```example\s*\n(.*?)^```\s*$", re.M | re.S)
_DATA_LINE = re.compile(r"^([^:>=]+?):\s*(.*)$")
_EXPECT = re.compile(r"^([A-Za-z_][\w.\[\]]*)\s*=\s*(.+?)\s*$")


@dataclass
class Page:
    title: str
    level: int
    intro: str = ""
    fields: dict[str, str] = field(default_factory=dict)
    opens: list[str] = field(default_factory=list)
    equations: str = ""
    aliases: list[str] = field(default_factory=list)
    children: list[Page] = field(default_factory=list)
    parent: Page | None = None

    @property
    def is_page(self) -> bool:
        return bool(self.fields)

    def path(self) -> list[Page]:
        chain, page = [], self
        while page is not None and page.level > 1:
            chain.insert(0, page)
            page = page.parent
        return chain

    def walk(self):
        for child in self.children:
            yield child
            yield from child.walk()

    def search_text(self) -> str:
        return " ".join([self.title, *self.aliases, self.intro, *self.fields.values()]).casefold()


def parse(markdown: str) -> Page:
    root = Page("Statistics reference", 1)
    stack, current, field_name, fenced = [root], root, None, False
    for line in markdown.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        heading = None if fenced or line.startswith("```") else _HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            while stack[-1].level >= level:
                stack.pop()
            current = Page(re.sub(r"[*`]", "", heading.group(2)), level, parent=stack[-1])
            stack[-1].children.append(current)
            stack.append(current)
            field_name = None
            continue
        hidden = None if fenced else _HIDDEN.match(line)
        if hidden:
            kind, value = hidden.groups()
            if kind == "equations":
                current.equations = value
            else:
                getattr(current, kind).extend(item.strip() for item in value.split(",") if item.strip())
            continue
        found = None if fenced else _FIELD.match(line)
        if found:
            field_name = found.group(1)
            current.fields[field_name] = found.group(2) + "\n"
        elif field_name:
            current.fields[field_name] += line + "\n"
        else:
            current.intro += line + "\n"
    for page in [root, *root.walk()]:
        page.intro = page.intro.strip("\n").rstrip()
        page.fields = {key: value.strip("\n").rstrip() for key, value in page.fields.items()}
    return root


def text() -> str:
    path = Path(__file__).resolve().parents[1] / "STATS_REFERENCE.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return "# Statistics reference\n\nThe reference file is missing. See docs/STATS_REFERENCE.md in the CellQuant folder."


@lru_cache(maxsize=1)
def default_index() -> "Index":
    return Index(parse(text()))


# --- Finding pages ----------------------------------------------------------------------------------------------

def sheet_key(kind: str, options: dict | None = None) -> str:
    """The key of a results sheet, e.g. ``compare:unpaired_t``, ``anova:ordinary``, ``outliers:rout``, ``linreg``."""

    options = options or {}
    if kind in ("compare", "anova") and options.get("test"):
        return f"{kind}:{options['test']}"
    if kind == "outliers":
        return f"outliers:{options.get('method', 'rout')}"
    if kind == "curvefit" and options.get("model"):
        return f"curvefit:{options['model']}"
    if kind == "xycorr":
        return f"xycorr:{options.get('method', 'pearson')}"
    return kind


class Index:
    """The pages by title, alias and results-sheet key."""

    def __init__(self, root: Page):
        self.root = root
        self.titles: dict[str, Page] = {}
        self.keys: dict[str, Page] = {}
        for page in root.walk():
            for name in (page.title, *page.aliases):
                self.titles.setdefault(name.casefold(), page)
            for key in page.opens:
                self.keys.setdefault(key, page)

    def page(self, name: str) -> Page | None:
        return self.titles.get(name.strip().casefold())

    def for_sheet(self, kind: str, options: dict | None = None) -> Page | None:
        """The page for a results sheet: its exact key, else the key of its kind (``compare``, ``anova``…)."""

        key = sheet_key(kind, options)
        return self.keys.get(key) or self.keys.get(key.split(":")[0])

    def for_label(self, label: str) -> Page | None:
        """The term a results-sheet row label names, e.g. "P value (two-tailed)" → P value, "df" → Degrees of freedom."""

        clean = re.sub(r"\s*\(.*?\)\s*", " ", label).strip()
        for candidate in (label, clean, clean.split(",")[0]):
            page = self.page(candidate)
            if page is not None and page.parent is not None and page.parent.title == "Terms":
                return page
        return None


def equations(guide: str, heading: str) -> str:
    """The section of STATISTICS.md whose heading starts with ``heading``, up to the next heading of its level or above."""

    lines = guide.splitlines()
    for start, line in enumerate(lines):
        match = re.match(r"^(#{2,6})\s+(.*)$", line)
        if match and re.sub(r"`[^`]*`|\(\s*\)", "", match.group(2)).strip().casefold().startswith(heading.casefold()):
            level = len(match.group(1))
            body = []
            for other in lines[start + 1:]:
                deeper = re.match(r"^(#{2,6})\s", other)
                if deeper and len(deeper.group(1)) <= level:
                    break
                body.append(other)
            return "\n".join(body).strip("\n")
    return ""


# --- Worked examples --------------------------------------------------------------------------------------------

@dataclass
class Example:
    data: dict[str, list[float]]
    code: list[str]
    expected: list[tuple[str, str]]


@dataclass
class Check:
    key: str
    documented: str
    live: Any
    ok: bool


def examples(body: str) -> list[Example]:
    return [example(match.group(1)) for match in _EXAMPLE.finditer(body)]


def example(block: str) -> Example:
    data, code, expected = {}, [], []
    for line in block.splitlines():
        if not line.strip():
            continue
        if line.startswith(">>>"):
            code.append(line[3:].strip())
        elif code:
            found = _EXPECT.match(line)
            if found:
                expected.append((found.group(1), found.group(2)))
        else:
            found = _DATA_LINE.match(line)
            if found:
                cells = [cell.strip() for cell in found.group(2).split(",")]
                data[found.group(1).strip()] = [float(cell) if cell else math.nan for cell in cells]
    return Example(data, code, expected)


def run_example(item: Example) -> list[Check]:
    """Run an example with the engine and compare each documented value with the live one."""

    import cellquant.stats as stats

    space: dict[str, Any] = {name: getattr(stats, name) for name in stats.__all__}
    space.update(np=np, data=item.data)
    space["table"] = stats.ColumnTable.from_columns(item.data) if item.data else None
    result = None
    for number, line in enumerate(item.code):
        if number == len(item.code) - 1 and not re.match(r"^\w+\s*=[^=]", line):
            result = eval(line, space)  # noqa: S307 - the bundled reference's own examples
        else:
            exec(line, space)  # noqa: S102
    values = result if isinstance(result, dict) else getattr(result, "values", result)
    return [_check(key, documented, _lookup(values, key)) for key, documented in item.expected]


def _lookup(values: Any, key: str) -> Any:
    current = values
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", key):
        try:
            current = current[int(part[1:-1])] if part.startswith("[") else current[part]
        except (KeyError, IndexError, TypeError):
            return None
    return current


def _check(key: str, documented: str, live: Any) -> Check:
    try:
        number = float(documented.replace("−", "-"))
    except ValueError:
        return Check(key, documented, live, str(live) == documented)
    if live is None or isinstance(live, str):
        return Check(key, documented, live, False)
    live = float(live)
    if math.isnan(number) or math.isinf(number):
        return Check(key, documented, live, str(live) == str(number))
    mantissa = documented.replace("−", "-").lstrip("+-").lower().split("e")
    decimals = len(mantissa[0].split(".")[1]) if "." in mantissa[0] else 0
    exponent = int(mantissa[1]) if len(mantissa) > 1 else 0
    tolerance = 0.5 * 10.0 ** (exponent - decimals)
    return Check(key, documented, live, abs(live - number) <= tolerance * (1 + 1e-9) + 1e-12)


def live_text(value: Any) -> str:
    if value is None:
        return "(not found)"
    if isinstance(value, (float, np.floating)):
        return f"{float(value):.6g}"
    return str(value)


# --- HTML -------------------------------------------------------------------------------------------------------

def markdown_html(body: str, titles: set[str]) -> str:
    """The glossary's Markdown subset, plus ``[[Page]]`` links, web links and example blocks."""

    parts, last = [], 0
    for number, match in enumerate(_EXAMPLE.finditer(body)):
        parts.append(_links(glossary.markdown_html(body[last:match.start()]), titles))
        parts.append(example_html(example(match.group(1)), number))
        last = match.end()
    parts.append(_links(glossary.markdown_html(body[last:]), titles))
    return "\n".join(part for part in parts if part)


def inline_html(body: str, titles: set[str]) -> str:
    return _links(glossary._inline(body, set()), titles)


def _links(text: str, titles: set[str]) -> str:
    def page(match):
        target, _, shown = match.group(1).partition("|")
        shown = shown or target
        if target.casefold() in titles:
            return f"<a href='page:{target}'>{shown}</a>"
        return shown

    text = re.sub(r"\[\[([A-Za-z][^\]]*)\]\]", page, text)
    return re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"<a href='\2'>\1</a>", text)


def example_html(item: Example, number: int, checks: list[Check] | None = None) -> str:
    rows = max((len(values) for values in item.data.values()), default=0)
    out = []
    if item.data:
        head = "".join(f"<th>{html.escape(name)}</th>" for name in item.data)
        body = "".join(
            "<tr>" + "".join(
                f"<td>{_cell(values[row]) if row < len(values) else ''}</td>" for values in item.data.values()
            ) + "</tr>"
            for row in range(rows)
        )
        out.append(f"<table class='data'><tr>{head}</tr>{body}</table>")
    if item.code:
        out.append("<pre>" + "\n".join(html.escape(">>> " + line) for line in item.code) + "</pre>")
    if item.expected:
        if checks is None:
            lines = "".join(f"<tr><td><code>{html.escape(key)}</code></td><td>{html.escape(value)}</td></tr>" for key, value in item.expected)
            out.append(f"<table class='grid'><tr><th>Result</th><th>Value</th></tr>{lines}</table>")
        else:
            lines = "".join(
                f"<tr><td><code>{html.escape(check.key)}</code></td><td>{html.escape(check.documented)}</td>"
                f"<td>{html.escape(live_text(check.live))}</td><td>{'✓' if check.ok else '✗ differs'}</td></tr>"
                for check in checks
            )
            out.append(f"<table class='grid'><tr><th>Result</th><th>This page</th><th>The engine now</th><th></th></tr>{lines}</table>")
    links = [f"<a href='run:{number}'>Run it with the engine</a>"]
    if item.data:
        links.append(f"<a href='table:{number}'>Open as a data table</a>")
    out.append("<p class='muted'>" + " · ".join(links) + "</p>")
    return "<table class='box' width='100%' cellpadding='8'><tr><td>" + "\n".join(out) + "</td></tr></table>"


def _cell(value: float) -> str:
    if math.isnan(value):
        return ""
    return f"{value:g}"
