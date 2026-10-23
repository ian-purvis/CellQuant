"""The output glossary window: a searchable tree of terms, a page per term, and "Show my data"."""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from qtpy.QtCore import QEvent, QSortFilterProxyModel, Qt, QUrl
from qtpy.QtGui import QColor, QDesktopServices, QFont, QImage, QPainter, QPen, QStandardItem, QStandardItemModel
from qtpy.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QTreeView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cellquant.gui import glossary

ENTRY_ROLE = Qt.UserRole + 1
SEARCH_ROLE = Qt.UserRole + 2

CSS = """
<style>
h3 { margin-top: 14px; margin-bottom: 4px; }
p.short { font-size: 13pt; }
p.eq { font-family: 'DejaVu Serif', 'Cambria Math', 'Times New Roman', serif; font-size: 13pt; background: #eef2f8; color: #1b2433; padding: 8px; }
span.eqi { font-family: 'DejaVu Serif', 'Cambria Math', 'Times New Roman', serif; font-size: 12pt; }
div.live { background: #e9f5ea; color: #13261a; padding: 8px; }
p.value { font-family: monospace; }
.muted { color: #808080; }
table.grid td, table.grid th { padding: 2px 10px 2px 0; }
table.grid th { text-align: left; }
pre { font-size: 9pt; }
</style>
"""


@dataclass
class ExperimentView:
    """What the glossary needs from the open experiment."""

    directory: Path
    working: Path | None
    images: list[tuple[str, str]]  # (image_id, name shown)
    current: str | None


class _Filter(QSortFilterProxyModel):
    """Shows entries whose text contains the search, and the folders above them."""

    def __init__(self):
        super().__init__()
        self.query = ""
        self.setRecursiveFilteringEnabled(True)

    def set_query(self, text: str) -> None:
        self.query = text.casefold().strip()
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent) -> bool:
        if not self.query:
            return True
        index = self.sourceModel().index(row, 0, parent)
        return self.query in (index.data(SEARCH_ROLE) or "")


class _Page(QTextBrowser):
    """The term page. Histograms are drawn on request (``cqhist:<n>`` images)."""

    def __init__(self):
        super().__init__()
        self.setOpenLinks(False)
        self.histograms: list[dict] = []

    def loadResource(self, kind, url):
        if url.scheme() == "cqhist":
            try:
                return _histogram(self.histograms[int(url.path())], max(320, min(640, self.viewport().width() - 40)))
            except (ValueError, IndexError):
                return QImage()
        return super().loadResource(kind, url)


def _histogram(spec: dict, width: int) -> QImage:
    values = np.asarray(spec["values"], float)
    cutoff, log = spec.get("cutoff"), spec.get("log")
    data = np.log10(values[values > 0]) if log else values
    counts, edges = np.histogram(data, bins=50)
    height = 90
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    top = counts.max() or 1
    bar = width / len(counts)
    line = None
    if cutoff is not None and (not log or cutoff > 0):
        line = np.log10(cutoff) if log else cutoff
    for index, count in enumerate(counts):
        size = max(2, (height - 6) * count / top) if count else 0  # a single object still shows
        middle = (edges[index] + edges[index + 1]) / 2
        color = QColor(214, 96, 77) if line is not None and middle > line else QColor(80, 140, 220)
        painter.fillRect(int(index * bar) + 1, int(height - 2 - size), max(1, int(bar) - 1), int(size), color)
    if line is not None and edges[-1] > edges[0]:
        x = (line - edges[0]) / (edges[-1] - edges[0]) * width
        painter.setPen(QPen(QColor(128, 128, 128), 2, Qt.DashLine))
        painter.drawLine(int(x), 0, int(x), height)
    painter.end()
    return image


class GlossaryWindow(QDialog):
    def __init__(self, parent, text: str, experiment: Callable[[], ExperimentView | None] = lambda: None):
        super().__init__(parent)
        self.setWindowTitle("CellQuant: output glossary")
        self.setModal(False)
        self.resize(1150, 820)
        self._experiment = experiment
        self._view: ExperimentView | None = None
        self.root = glossary.parse(text)
        self._names = {name for entry in self.root.walk() for name in entry.names}
        self._entry: glossary.Entry | None = None
        self._choice: str | None = None
        self._drill: list = []
        self._experts = False

        self.model = QStandardItemModel(self)
        self._fill(self.model.invisibleRootItem(), self.root)
        self.proxy = _Filter()
        self.proxy.setSourceModel(self.model)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search, e.g. area, cutoff, crop (Enter opens the first)")
        self.search.setClearButtonEnabled(True)
        self.search.setToolTip("Finds names and words in the explanations. Enter opens the first match.")
        self.search.textChanged.connect(self._search_changed)
        self.search.returnPressed.connect(self._open_first_match)
        self.found = QLabel("")
        self.found.setStyleSheet("color: gray")
        self.tree = QTreeView()
        self.tree.setHeaderHidden(True)
        self.tree.setModel(self.proxy)
        self.tree.selectionModel().currentChanged.connect(lambda current, _previous: self._open(current))
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.search)
        left_layout.addWidget(self.found)
        left_layout.addWidget(self.tree)

        self.where = QLabel()
        self.where.setStyleSheet("color: gray")
        self.where.setWordWrap(True)
        self.title = QLabel()
        font = QFont("monospace")
        font.setStyleHint(QFont.Monospace)
        font.setPointSize(15)
        font.setBold(True)
        self.title.setFont(font)
        self.title.setWordWrap(True)
        self.show_data = QPushButton("Show my data")
        self.show_data.setCheckable(True)
        self.show_data.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.show_data.toggled.connect(lambda _on: self._render())
        self.image = QComboBox()
        self.image.setToolTip("The image whose values are shown")
        self.image.currentIndexChanged.connect(lambda _index: self._render())
        self.results = QComboBox()
        self.results.setToolTip("Latest results: what CellQuant shows now.\nA run: the copy saved when that run finished.")
        self.results.currentIndexChanged.connect(lambda _index: self._render())
        self.image_label = QLabel("Image")
        self.results_label = QLabel("From")
        bar = QHBoxLayout()
        bar.addWidget(self.show_data)
        bar.addWidget(self.image_label)
        bar.addWidget(self.image, 1)
        bar.addWidget(self.results_label)
        bar.addWidget(self.results)
        bar.addStretch(0)
        self.page = _Page()
        self.page.anchorClicked.connect(self._link)
        self.previous = QPushButton("◀ Previous")
        self.previous.setToolTip("The entry above in the list")
        self.previous.clicked.connect(lambda: self._step(-1))
        self.next = QPushButton("Next ▶")
        self.next.setToolTip("The entry below in the list. With Show my data on, this walks through your results.")
        self.next.clicked.connect(lambda: self._step(1))
        steps = QHBoxLayout()
        steps.addStretch(1)
        steps.addWidget(self.previous)
        steps.addWidget(self.next)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self.where)
        right_layout.addWidget(self.title)
        right_layout.addLayout(bar)
        right_layout.addWidget(self.page, 1)
        right_layout.addLayout(steps)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([340, 810])
        layout = QVBoxLayout(self)
        layout.addWidget(split)
        self.refresh_experiment()
        first = self.proxy.index(0, 0)
        self.tree.expand(first)
        start = self.proxy.index(0, 0, first)
        self.tree.setCurrentIndex(start if start.isValid() else first)

    # --- Tree and search ----------------------------------------------------------------------------------------

    def _fill(self, parent: QStandardItem, entry: glossary.Entry) -> None:
        for child in entry.children:
            item = QStandardItem(child.title)
            item.setEditable(False)
            item.setData(child, ENTRY_ROLE)
            item.setData(child.search_text(), SEARCH_ROLE)
            short = child.fields.get("In short") or child.label or child.intro.split("\n")[0]
            if short:
                item.setToolTip(short[:200])
            parent.appendRow(item)
            self._fill(item, child)

    def _items(self, parent=None):
        """Every visible row, top to bottom."""

        from qtpy.QtCore import QModelIndex

        parent = parent if parent is not None else QModelIndex()
        for row in range(self.proxy.rowCount(parent)):
            index = self.proxy.index(row, 0, parent)
            yield index
            yield from self._items(index)

    def _search_changed(self, text: str) -> None:
        self.proxy.set_query(text)
        query = text.casefold().strip()
        bold = QFont()
        bold.setBold(True)
        named = 0
        stack = [self.model.invisibleRootItem()]
        while stack:
            item = stack.pop()
            for row in range(item.rowCount()):
                child = item.child(row)
                hit = bool(query) and query in child.text().casefold()
                named += hit
                child.setFont(bold if hit else QFont())
                stack.append(child)
        if query:
            self.tree.expandAll()
            shown = sum(1 for index in self._items() if query in (index.data(SEARCH_ROLE) or ""))
            self.found.setText(f"{named} names contain “{text.strip()}”, {shown} entries mention it"
                               if shown else f"Nothing mentions “{text.strip()}”")
        else:
            self.found.setText("")
            self.tree.collapseAll()
            if self.tree.currentIndex().isValid():
                self.tree.scrollTo(self.tree.currentIndex())

    def _open_first_match(self) -> None:
        query = self.search.text().casefold().strip()
        indexes = list(self._items())
        best = next((index for index in indexes if query and query in (index.data() or "").casefold()), None)
        best = best or (indexes[0] if indexes else None)
        if best is not None:
            self.tree.setCurrentIndex(best)

    def _step(self, direction: int) -> None:
        indexes = list(self._items())
        current = self.tree.currentIndex()
        position = next((number for number, index in enumerate(indexes) if index == current), -1)
        target = position + direction
        if 0 <= target < len(indexes):
            self.tree.setCurrentIndex(indexes[target])
            self.tree.scrollTo(indexes[target])

    def select(self, name: str) -> bool:
        """Open the term called ``name`` (the one nearest the open page when several share it)."""

        candidates = [entry for entry in self.root.walk() if name in entry.names or entry.title == name]
        if not candidates:
            candidates = [entry for entry in self.root.walk() if entry.names and glossary.matching(entry.names, [name])]
        if not candidates:
            return False
        here = self._entry.data_owner if self._entry else None
        candidates.sort(key=lambda entry: (entry.data_owner is not here, entry.data is None))
        if self.search.text():
            self.search.clear()
        index = self._index_of(candidates[0])
        if index is None:
            return False
        self.tree.setCurrentIndex(index)
        self.tree.scrollTo(index)
        return True

    def _index_of(self, entry: glossary.Entry):
        for index in self._items():
            if index.data(ENTRY_ROLE) is entry:
                return index
        chain = entry.path()
        for depth in range(len(chain)):
            for index in self._items():
                if index.data(ENTRY_ROLE) is chain[depth]:
                    self.tree.expand(index)
        for index in self._items():
            if index.data(ENTRY_ROLE) is entry:
                return index
        return None

    # --- The open experiment ----------------------------------------------------------------------------------------

    def refresh_experiment(self) -> None:
        """Bring the image and results lists up to date with the open experiment."""

        try:
            view = self._experiment()
        except Exception:
            view = None
        self._view = view
        has = view is not None
        self.show_data.setEnabled(has)
        self.show_data.setToolTip(
            "Fill this page with the values from your results.\nStays on as you move between entries."
            if has else "Open an experiment (step 1) to see your own values here."
        )
        for widget in (self.image, self.results):
            widget.blockSignals(True)
        image_id, results = self.image.currentData(), self.results.currentData()
        self.image.clear()
        self.results.clear()
        if has:
            for identifier, name in view.images:
                self.image.addItem(name, identifier)
            for label, folder in glossary.result_sets(view.directory, view.working):
                self.results.addItem(label, str(folder))
            wanted = image_id or view.current
            self.image.setCurrentIndex(max(0, self.image.findData(wanted)))
            self.results.setCurrentIndex(max(0, self.results.findData(results)))
        for widget in (self.image, self.results):
            widget.blockSignals(False)
        if not has and self.show_data.isChecked():
            self.show_data.setChecked(False)
        self._sync_bar()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.ActivationChange and self.isActiveWindow():
            before = (self.image.currentData(), self.results.currentData(), self.results.count(), self.image.count())
            self.refresh_experiment()
            after = (self.image.currentData(), self.results.currentData(), self.results.count(), self.image.count())
            if before != after or self.show_data.isChecked():
                self._render()

    def _sync_bar(self) -> None:
        on = self.show_data.isChecked() and self.show_data.isEnabled()
        for widget in (self.image, self.image_label, self.results, self.results_label):
            widget.setVisible(on)

    def _source(self) -> glossary.Source | None:
        if self._view is None or self.results.currentData() is None:
            return None
        return glossary.Source(self._view.directory, Path(self.results.currentData()), self.image.currentData())

    # --- The page -------------------------------------------------------------------------------------------------

    def _open(self, index) -> None:
        entry = index.data(ENTRY_ROLE) if index.isValid() else None
        if entry is None:
            return
        self._entry = entry
        self._choice = None
        self._drill = []
        self._render()

    def _render(self) -> None:
        entry = self._entry
        if entry is None:
            self._sync_bar()
            return
        chain = entry.path()
        self.where.setText("  ›  ".join(item.title for item in chain[:-1]) or " ")
        self.title.setText(entry.title + (f"   ({entry.label})" if entry.label else ""))
        terms = self._names
        parts = [CSS]
        if entry.intro:
            parts.append(glossary.markdown_html(entry.intro, terms))
        fields = entry.fields
        if "In short" in fields:
            parts.append(f"<p class='short'><b>In short:</b> {glossary._inline(fields['In short'], terms)}</p>")
        if "What it means" in fields:
            parts.append("<h3>What it means</h3>" + glossary.markdown_html(fields["What it means"], terms))
        if "How it's calculated" in fields:
            parts.append("<h3>How it's calculated</h3>" + glossary.markdown_html(fields["How it's calculated"], terms))
        self.page.histograms = []
        source = self._source()
        has_data = entry.data is not None
        self.show_data.setEnabled(self._view is not None and has_data)
        if self._view is not None and not has_data:
            self.show_data.setToolTip("This entry has no file of its own in the results folder.")
        elif self._view is not None:
            self.show_data.setToolTip("Fill this page with the values from your results.\nStays on as you move between entries.")
        self._sync_bar()
        if self.show_data.isChecked() and has_data and source is not None:
            data = glossary.data_html(entry, source, choice=self._choice, drill=self._drill)
            self.page.histograms = data.histograms
            self._file = data.file
            parts.append("<h3>In your data</h3>" + data.html)
        else:
            self._file = None
        if "Good to know" in fields:
            parts.append("<h3>Good to know</h3>" + glossary.markdown_html(fields["Good to know"], terms))
        if "For experts" in fields:
            if self._experts:
                parts.append("<h3><a href='experts:'>▾ For experts</a></h3>" + glossary.markdown_html(fields["For experts"], terms))
            else:
                parts.append("<p><a href='experts:'>▸ For experts</a> <span class='muted'>(the exact rule in the code)</span></p>")
        if "Related" in fields:
            parts.append("<p><b>Related:</b> " + glossary._inline(fields["Related"], terms) + "</p>")
        if entry.children and not fields:
            links = " · ".join(f"<a href='entry:{number}'>{html.escape(child.title)}</a>" for number, child in enumerate(entry.children))
            parts.append(f"<p class='muted'>In here: {links}</p>")
        scroll = self.page.verticalScrollBar().value() if getattr(self, "_keep_scroll", False) else 0
        self.page.setHtml("".join(parts))
        self.page.verticalScrollBar().setValue(scroll)
        self._keep_scroll = False

    def _link(self, url: QUrl) -> None:
        text = url.toString()
        scheme, _, rest = text.partition(":")
        rest = QUrl.fromPercentEncoding(rest.encode()) if "%" in rest else rest
        if scheme == "term":
            self.select(rest)
        elif scheme == "entry" and self._entry is not None:
            child = self._entry.children[int(rest)]
            index = self._index_of(child)
            if index is not None:
                self.tree.setCurrentIndex(index)
        elif scheme == "experts":
            self._experts = not self._experts
            self._keep_scroll = True
            self._render()
        elif scheme == "choice":
            self._choice, self._drill = rest, []
            self._keep_scroll = True
            self._render()
        elif scheme == "drill":
            if rest.startswith("+"):
                self._drill.append(json.loads(rest[1:]))
            else:
                self._drill = self._drill[: int(rest) + 1] if int(rest) >= 0 else []
            self._keep_scroll = True
            self._render()
        elif scheme == "open" and self._file is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._file)))
        elif scheme == "values" and self._file is not None:
            ValuesWindow(self, self._file, rest).show()
        elif scheme == "whole" and self._file is not None:
            kind = self._entry.data[0] if self._entry and self._entry.data else "json"
            WholeFileWindow(self, self._file, kind).show()


class ValuesWindow(QDialog):
    """Every value of one column, with its object number."""

    def __init__(self, parent, path: Path, column: str):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(f"{column} · {path.name}")
        self.resize(420, 560)
        frame = glossary.read_columns(path, ["object_id", column])
        table = QTableWidget(len(frame), len(frame.columns))
        table.setHorizontalHeaderLabels(list(frame.columns))
        for row, values in enumerate(frame.itertuples(index=False)):
            for number, value in enumerate(values):
                item = QTableWidgetItem()
                item.setData(Qt.DisplayRole, value.item() if hasattr(value, "item") else str(value))
                table.setItem(row, number, item)
        table.setSortingEnabled(True)
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"{len(frame)} rows. Click a header to sort."))
        layout.addWidget(table)


class WholeFileWindow(QDialog):
    """A JSON or YAML file as a tree, closed, with a filter."""

    def __init__(self, parent, path: Path, kind: str):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(path.name)
        self.resize(700, 640)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Key", "Value"])
        document = glossary.load_structured(path, kind)
        self._add(self.tree.invisibleRootItem(), document)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        search = QLineEdit()
        search.setPlaceholderText("Filter keys and values")
        search.setClearButtonEnabled(True)
        search.textChanged.connect(self._filter)
        layout = QVBoxLayout(self)
        layout.addWidget(search)
        layout.addWidget(self.tree)

    def _add(self, parent, value) -> None:
        items = value.items() if isinstance(value, dict) else enumerate(value) if isinstance(value, list) else []
        for key, child in items:
            label = str(key + 1) if isinstance(value, list) else str(key)
            node = QTreeWidgetItem([label, glossary._one_line(child)])
            parent.addChild(node)
            if isinstance(child, (dict, list)):
                self._add(node, child)

    def _filter(self, text: str) -> None:
        query = text.casefold().strip()

        def visit(item) -> bool:
            shown = False
            for number in range(item.childCount()):
                shown |= visit(item.child(number))
            own = not query or query in item.text(0).casefold() or query in item.text(1).casefold()
            if item is not self.tree.invisibleRootItem():
                item.setHidden(not (own or shown))
                item.setExpanded(bool(query) and shown)
            return own or shown

        visit(self.tree.invisibleRootItem())
