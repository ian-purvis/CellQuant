"""Help → Statistics reference…: a page per analysis and per term, with a search, worked examples that run
with the engine, and the matching equations from the statistics guide."""

from __future__ import annotations

import html
from typing import Callable

from qtpy.QtCore import QSortFilterProxyModel, Qt, QUrl
from qtpy.QtGui import QDesktopServices, QFont, QStandardItem, QStandardItemModel
from qtpy.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from cellquant.gui.quantanalysis import reference

CSS = """
<style>
a { color: #8ab4f8; }
h3 { margin-top: 14px; margin-bottom: 4px; }
p.short { font-size: 13pt; }
p.eq { font-family: 'DejaVu Serif', 'Cambria Math', 'Times New Roman', serif; font-size: 13pt; background: #2a3342; color: #e6edf7; }
table.box { background: #1f3324; color: #e3f1e5; }
table.full { background: #262a30; color: #e6e6e6; }
table.data td, table.data th { padding: 2px 14px 2px 0; text-align: right; }
.muted { color: #9a9a9a; }
table.grid td, table.grid th { padding: 2px 12px 2px 0; }
table.grid th { text-align: left; }
pre { font-size: 9pt; }
</style>
"""
ORDER = [name for name in reference.FIELDS if name not in ("In short", "Related")]


class _Filter(QSortFilterProxyModel):
    """Keeps a row when it, a child or a parent matches every word of the search."""

    def __init__(self):
        super().__init__()
        self.words: list[str] = []
        self.setRecursiveFilteringEnabled(True)

    def set_query(self, text: str) -> None:
        self.words = text.casefold().split()
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent) -> bool:
        if not self.words:
            return True
        page = self.sourceModel().index(row, 0, parent).data(Qt.UserRole)
        found = page.search_text() if page is not None else ""
        return all(word in found for word in self.words)


class ReferenceWindow(QDialog):
    """``open_example(data)`` puts a worked example's data into the project as a new data table."""

    def __init__(self, parent, text: str, guide: str = "", open_example: Callable[[str, dict, bool], None] | None = None):
        super().__init__(parent)
        self.setWindowTitle("QuantAnalysis: statistics reference")
        self.setModal(False)
        self.resize(1150, 820)
        self.root = reference.parse(text)
        self.index = reference.Index(self.root)
        self._titles = set(self.index.titles)
        self._guide = guide
        self._open_example = open_example
        self._page: reference.Page | None = None
        self._full = False
        self._checks: dict[int, list[reference.Check]] = {}

        self.model = QStandardItemModel(self)
        self._fill(self.model.invisibleRootItem(), self.root)
        self.proxy = _Filter()
        self.proxy.setSourceModel(self.model)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search, e.g. Welch, df, Tukey (Enter opens the first)")
        self.search.setClearButtonEnabled(True)
        self.search.setToolTip("Finds titles and words in the pages. Enter opens the first match.")
        self.search.textChanged.connect(self._search_changed)
        self.search.returnPressed.connect(self._open_first_match)
        self.tree = QTreeView()
        self.tree.setHeaderHidden(True)
        self.tree.setModel(self.proxy)
        self.tree.selectionModel().currentChanged.connect(lambda current, _previous: self._open(current))
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.search)
        left_layout.addWidget(self.tree)

        self.where = QLabel()
        self.where.setStyleSheet("color: gray")
        self.title = QLabel()
        font = QFont()
        font.setPointSize(15)
        font.setBold(True)
        self.title.setFont(font)
        self.title.setWordWrap(True)
        self.page = QTextBrowser()
        self.page.setOpenLinks(False)
        self.page.anchorClicked.connect(self._link)
        self.back = QPushButton("◀ Back")
        self.back.setToolTip("The page you were on before")
        self.back.clicked.connect(self._go_back)
        self._history: list[reference.Page] = []
        steps = QHBoxLayout()
        steps.addWidget(self.back)
        steps.addStretch(1)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self.where)
        right_layout.addWidget(self.title)
        right_layout.addWidget(self.page, 1)
        right_layout.addLayout(steps)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([330, 820])
        layout = QVBoxLayout(self)
        layout.addWidget(split)
        first = self._find(lambda page: page.is_page)
        if first is not None:
            self.show_page(first)

    # --- Tree and search ----------------------------------------------------------------------------------------

    def _fill(self, parent: QStandardItem, page: reference.Page) -> None:
        for child in page.children:
            item = QStandardItem(child.title)
            item.setEditable(False)
            item.setData(child, Qt.UserRole)
            if not child.is_page:
                bold = item.font()
                bold.setBold(True)
                item.setFont(bold)
            parent.appendRow(item)
            self._fill(item, child)

    def _items(self, parent=None):
        parent = parent if parent is not None else self.proxy.index(-1, -1)
        for row in range(self.proxy.rowCount(parent)):
            index = self.proxy.index(row, 0, parent)
            yield index
            yield from self._items(index)

    def _search_changed(self, text: str) -> None:
        self.proxy.set_query(text)
        if text.strip():
            self.tree.expandAll()

    def _open_first_match(self) -> None:
        """The first page whose title or alias has every word, else the first page found."""

        found = [index for index in self._items() if index.data(Qt.UserRole) is not None and index.data(Qt.UserRole).is_page]
        words = self.proxy.words
        named = [index for index in found
                 if any(all(word in name.casefold() for word in words) for name in (index.data(Qt.UserRole).title, *index.data(Qt.UserRole).aliases))]
        if named or found:
            self.tree.setCurrentIndex((named or found)[0])

    def _find(self, test) -> reference.Page | None:
        return next((page for page in self.root.walk() if test(page)), None)

    def _index_of(self, page: reference.Page):
        for index in self._items():
            if index.data(Qt.UserRole) is page:
                return index
        self.search.clear()
        for index in self._items():
            if index.data(Qt.UserRole) is page:
                return index
        return None

    # --- Opening pages ----------------------------------------------------------------------------------------------

    def show_page(self, page: reference.Page, remember: bool = True) -> None:
        if remember and self._page is not None and self._page is not page:
            self._history.append(self._page)
        index = self._index_of(page)
        if index is not None and self.tree.currentIndex() != index:
            self._remember = False
            self.tree.setCurrentIndex(index)
            self.tree.scrollTo(index)
        self._page, self._full, self._checks = page, False, {}
        self._render()

    def show_name(self, name: str) -> bool:
        page = self.index.page(name)
        if page is None:
            return False
        self.show_page(page)
        return True

    def show_sheet(self, kind: str, options: dict | None = None) -> bool:
        """The page for a results sheet (False, and the first page stays, when there is none)."""

        page = self.index.for_sheet(kind, options)
        if page is None:
            return False
        self.show_page(page)
        return True

    def _open(self, index) -> None:
        page = index.data(Qt.UserRole) if index.isValid() else None
        if page is None or page is self._page:
            return
        if getattr(self, "_remember", True) and self._page is not None:
            self._history.append(self._page)
        self._remember = True
        self._page, self._full, self._checks = page, False, {}
        self._render()

    def _go_back(self) -> None:
        if self._history:
            self.show_page(self._history.pop(), remember=False)

    # --- The page -------------------------------------------------------------------------------------------------

    def _render(self) -> None:
        page = self._page
        self.back.setEnabled(bool(self._history))
        if page is None:
            return
        self.where.setText("  ›  ".join(item.title for item in page.path()[:-1]) or " ")
        self.title.setText(page.title)
        titles = self._titles
        parts = [CSS]
        if page.intro:
            parts.append(reference.markdown_html(page.intro, titles))
        fields = page.fields
        if "In short" in fields:
            parts.append(f"<p class='short'><b>In short:</b> {reference.inline_html(fields['In short'], titles)}</p>")
        for name in ORDER:
            if name not in fields:
                continue
            if name == "Worked example":
                parts.append("<h3>Worked example</h3>" + self._example_html(fields[name]))
            else:
                parts.append(f"<h3>{html.escape(name)}</h3>" + reference.markdown_html(fields[name], titles))
            if name == "How it's calculated" and page.equations:
                parts.append(self._full_html(page))
        if page.equations and "How it's calculated" not in fields:
            parts.append(self._full_html(page))
        if "Related" in fields:
            parts.append("<p><b>Related:</b> " + reference.inline_html(fields["Related"], titles) + "</p>")
        if page.children and not fields:
            links = " · ".join(f"<a href='page:{html.escape(child.title)}'>{html.escape(child.title)}</a>" for child in page.children)
            parts.append(f"<p class='muted'>In here: {links}</p>")
        scroll = self.page.verticalScrollBar().value() if getattr(self, "_keep_scroll", False) else 0
        self.page.setHtml("".join(parts))
        self.page.verticalScrollBar().setValue(scroll)
        self._keep_scroll = False

    def _example_html(self, body: str) -> str:
        """The field's text with each example drawn with its checks, once run."""

        out, last = [], 0
        for number, match in enumerate(reference._EXAMPLE.finditer(body)):
            out.append(reference.markdown_html(body[last:match.start()], self._titles))
            out.append(reference.example_html(reference.example(match.group(1)), number, self._checks.get(number)))
            last = match.end()
        out.append(reference.markdown_html(body[last:], self._titles))
        return "\n".join(part for part in out if part)

    def _full_html(self, page: reference.Page) -> str:
        if not self._full:
            return "<p><a href='full:'>▸ Full equations</a> <span class='muted'>(from the statistics guide)</span></p>"
        body = reference.equations(self._guide, page.equations)
        return "<p><a href='full:'>▾ Full equations</a></p><table class='full' width='100%' cellpadding='8'><tr><td>" + reference.markdown_html(body, self._titles) + "</td></tr></table>"

    def _examples(self) -> list[reference.Example]:
        return reference.examples((self._page.fields if self._page else {}).get("Worked example", ""))

    def run_example(self, number: int) -> list[reference.Check]:
        """Run the page's example ``number`` with the engine and show its numbers beside the page's."""

        item = self._examples()[number]
        try:
            checks = reference.run_example(item)
        except Exception as error:  # noqa: BLE001 - shown on the page
            checks = [reference.Check("(error)", "", f"{type(error).__name__}: {error}", False)]
        self._checks[number] = checks
        self._keep_scroll = True
        self._render()
        return checks

    def _link(self, url: QUrl) -> None:
        scheme, _, rest = url.toString().partition(":")
        rest = QUrl.fromPercentEncoding(rest.encode())
        if scheme == "page":
            self.show_name(rest)
        elif scheme == "full":
            self._full = not self._full
            self._keep_scroll = True
            self._render()
        elif scheme == "run":
            self.run_example(int(rest))
        elif scheme == "table" and self._open_example is not None:
            item = self._examples()[int(rest)]
            self._open_example(f"Example: {self._page.title}", item.data, any("paired=True" in line for line in item.code))
        elif scheme in ("http", "https"):
            QDesktopServices.openUrl(url)
