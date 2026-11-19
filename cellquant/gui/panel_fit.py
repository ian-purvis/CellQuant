"""Docked panels that give way when the window is too short for them.

A panel's full height is a floor for the column it is docked in. CellQuant lets its window be smaller than its
panels ask for (so it can always be resized), and in a window shorter than a column's panels together Qt draws
them over each other and over the bar at the bottom. Here each panel sits in a ``PanelScroll``; a ``PanelFitter``
keeps every panel at its full height while they all fit and, when they do not, lets them scroll, one after another.
"""

from __future__ import annotations

from qtpy.QtCore import QEvent, QObject, QSize, Qt, QTimer
from qtpy.QtWidgets import QDockWidget, QScrollArea, QSizePolicy, QWidget


_DOCK_FLOOR = 50  # the smallest size napari gives its dock widgets; a panel's own floor comes on top of the header


class PanelScroll(QScrollArea):
    """Holds one panel. Squeezed, it can be made shorter than the panel and scrolls up and down."""

    FLOOR = 48  # the height a squeezed panel keeps: enough to see it and its scroll bar

    def __init__(self, panel: QWidget):
        super().__init__()
        self._squeezed = False
        # Spare height in the column is shared as it was with the panel docked directly.
        policy = panel.sizePolicy()
        grows = bool(policy.expandingDirections() & Qt.Orientation.Vertical)
        if panel.layout() is not None:
            grows = grows or bool(panel.layout().expandingDirections() & Qt.Orientation.Vertical)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding if grows else QSizePolicy.Policy.Preferred)
        self.setWidget(panel)
        # The panel is sized here (see _place), not by the scroll area: for a panel with wrapping text Qt would
        # hold it at its preferred height and scroll it even where its smallest height fits.
        self.setWidgetResizable(False)
        self.setFrameShape(QScrollArea.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        panel.installEventFilter(self)  # a row shown or hidden changes the height the panel asks for

    def eventFilter(self, watched, event) -> bool:
        if watched is self.widget() and event.type() == QEvent.Type.LayoutRequest:
            self.updateGeometry()
            self._place()
        return False

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place()

    def _needed(self, width: int) -> int:
        """The smallest height the panel can be drawn in at this width."""

        panel = self.widget()
        height = panel.minimumSizeHint().height()
        layout = panel.layout()
        if layout is not None and layout.hasHeightForWidth():
            height = max(height, layout.minimumHeightForWidth(width))
        return max(height, panel.minimumHeight())

    def _place(self) -> None:
        """The panel fills the space it is given, and is never shorter than it can be drawn: below that it scrolls."""

        view = self.viewport().size()
        size = QSize(view.width(), max(view.height(), self._needed(view.width())))
        if self.widget().size() != size:
            self.widget().resize(size)

    def _frame(self) -> int:
        return 2 * self.frameWidth()  # the theme gives a scroll area a border even with no frame shape

    def full_height(self) -> int:
        """The height at which nothing needs to scroll."""

        width = max(self.viewport().width(), self.widget().minimumSizeHint().width())
        return self._needed(width) + self._frame()

    def squeezed(self) -> bool:
        return self._squeezed

    def set_squeezed(self, squeezed: bool) -> None:
        if squeezed != self._squeezed:
            self._squeezed = squeezed
            self.updateGeometry()

    def sizeHint(self):
        frame = QSize(self._frame(), self._frame())
        return (self.widget().sizeHint() + frame).expandedTo(self.minimumSizeHint())

    def minimumSizeHint(self):
        width = self.widget().minimumSizeHint().width() + self._frame()
        if not self._squeezed:
            return QSize(width, self.full_height())
        # Room for the scroll bar beside the panel, so it does not cover the panel's right edge.
        return QSize(width + self.verticalScrollBar().sizeHint().width(), min(self.full_height(), self.FLOOR))


class PanelFitter(QObject):
    """Squeezes the docked panels it is given, in the order given, only while the window is too short for them all."""

    def __init__(self, window, docks: list[QDockWidget]):
        super().__init__(window)
        self._window = window
        self._docks = list(docks)  # each one's widget() is a PanelScroll
        self._seen = None
        window.installEventFilter(self)
        for dock in self._docks:
            for signal in (dock.topLevelChanged, dock.visibilityChanged, dock.dockLocationChanged):
                signal.connect(lambda *_args: self.fit())

    def eventFilter(self, watched, event) -> bool:
        # Before the window lays itself out again: after a resize, and when a panel or the bottom bar asks for another height.
        if watched is self._window and event.type() in (QEvent.Type.Resize, QEvent.Type.LayoutRequest, QEvent.Type.Show):
            self.fit()
        return False

    def _docked(self, dock) -> bool:
        return not dock.isHidden() and not dock.isFloating()

    @staticmethod
    def _scroll(dock):
        """The dock's PanelScroll, or None while the dock holds something else (a panel taken out or swapped meanwhile)."""

        widget = dock.widget()
        return widget if isinstance(widget, PanelScroll) else None

    def _state(self):
        """What the fit depends on. Squeezing changes none of it, so the layout request that follows a fit ends here."""

        mine = tuple(
            (self._docked(dock), scroll.full_height() if scroll is not None else 0)
            for dock, scroll in ((dock, self._scroll(dock)) for dock in self._docks)
        )
        others = tuple(
            dock.minimumSizeHint().height()
            for dock in self._window.findChildren(QDockWidget)
            if dock not in self._docks and self._docked(dock)
        )
        return self._window.height(), mine, others

    def _release(self, dock) -> None:
        """Qt pins a popped-out panel's smallest size to its full size and keeps that when it is docked again; undo it."""

        if not dock.isFloating() and (dock.minimumHeight() != _DOCK_FLOOR or dock.minimumWidth() != _DOCK_FLOOR):
            dock.setMinimumSize(_DOCK_FLOOR, _DOCK_FLOOR)

    def fit(self) -> None:
        try:
            for dock in self._docks:
                self._release(dock)
            state = self._state()
        except RuntimeError:
            return  # the window is closing
        if state == self._seen:
            return
        self._seen = state
        layout = self._window.layout()
        for dock in self._docks:
            if self._scroll(dock) is not None:
                self._scroll(dock).set_squeezed(False)
        squeezed = []
        for dock in (dock for dock in self._docks if self._docked(dock) and self._scroll(dock) is not None):
            layout.invalidate()  # the height the window needs is worked out again, with the panels as they are now
            if layout.totalMinimumSize().height() <= self._window.height():
                break
            self._scroll(dock).set_squeezed(True)
            squeezed.append(dock)
        if squeezed:
            QTimer.singleShot(0, lambda: self._share(squeezed))

    def _share(self, squeezed: list[QDockWidget]) -> None:
        """A squeezed panel takes the room its column has to spare, up to its full height (Qt would leave it at the floor)."""

        try:
            docks = [dock for dock in squeezed if self._docked(dock) and self._scroll(dock) is not None and self._scroll(dock).squeezed()]
            heights = [self._scroll(dock).full_height() + dock.height() - dock.widget().height() for dock in docks]
            if docks:
                self._window.resizeDocks(docks, heights, Qt.Vertical)
        except RuntimeError:
            pass  # the window was closed meanwhile
