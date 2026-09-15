"""Tests for per-dropdown Scrollability controls."""

from types import SimpleNamespace

from cellquant.plugin.combo_scroll import apply_combo_scrollability, make_scrollability_checkbox


class _Signal:
    def __init__(self):
        self._handlers = []

    def connect(self, handler):
        self._handlers.append(handler)

    def emit(self, *args):
        for handler in list(self._handlers):
            handler(*args)


class _FakeView:
    def __init__(self):
        self.policy = None
        self._min = 0
        self._max = 16777215
        self._h = 100
        self._w = 120
        self._parent = None
        self._visible = False

    def setVerticalScrollBarPolicy(self, policy):
        self.policy = policy

    def setMinimumHeight(self, value):
        self._min = value

    def setMaximumHeight(self, value):
        self._max = value

    def minimumHeight(self):
        return self._min

    def maximumHeight(self):
        return self._max

    def height(self):
        return self._h

    def width(self):
        return self._w

    def resize(self, w, h):
        self._w = w
        self._h = h

    def updateGeometry(self):
        return None

    def parentWidget(self):
        return self._parent

    def sizeHintForRow(self, _row):
        return 20

    def fontMetrics(self):
        return SimpleNamespace(height=lambda: 16)

    def isVisible(self):
        return self._visible

    def show(self):
        self._visible = True


class _FakeContainer:
    def __init__(self):
        self._min = 0
        self._max = 16777215
        self._h = 100
        self._w = 120
        self._visible = True

    def setMinimumHeight(self, value):
        self._min = value

    def setMaximumHeight(self, value):
        self._max = value

    def minimumHeight(self):
        return self._min

    def maximumHeight(self):
        return self._max

    def height(self):
        return self._h

    def width(self):
        return self._w

    def resize(self, w, h):
        self._w = w
        self._h = h

    def updateGeometry(self):
        return None

    def isVisible(self):
        return self._visible

    def show(self):
        self._visible = True


class _FakeCombo:
    def __init__(self, count: int):
        self._count = count
        self.max_visible = None
        self._stylesheet = ""
        self._view = _FakeView()
        self._view._parent = _FakeContainer()
        self.showPopup = lambda: None
        self.model_obj = SimpleNamespace(
            rowsInserted=_Signal(),
            rowsRemoved=_Signal(),
        )

    def count(self):
        return self._count

    def setMaxVisibleItems(self, value):
        self.max_visible = value

    def model(self):
        return self.model_obj

    def styleSheet(self):
        return self._stylesheet

    def setStyleSheet(self, value):
        self._stylesheet = value

    def view(self):
        return self._view

    def width(self):
        return 160


class _FakeCheckBox:
    def __init__(self, text):
        self.text = text
        self._checked = False
        self.toggled = _Signal()
        self.tool_tip = None

    def setChecked(self, value):
        self._checked = bool(value)

    def isChecked(self):
        return self._checked

    def setToolTip(self, text):
        self.tool_tip = text


class _FakeQtWidgets:
    QCheckBox = _FakeCheckBox


def test_apply_combo_scrollability_limits_or_shows_all():
    combo = _FakeCombo(25)
    apply_combo_scrollability(combo, True, visible_items=10)
    assert combo.max_visible == 10
    assert "combobox-popup: 1" in combo.styleSheet()
    assert combo._cellquant_want_scroll is True
    apply_combo_scrollability(combo, False)
    assert combo.max_visible == 25
    assert "combobox-popup: 1" in combo.styleSheet()
    assert combo._cellquant_want_scroll is False


def test_checkbox_defaults_scrollable_for_long_lists():
    combo = _FakeCombo(20)
    box = make_scrollability_checkbox(_FakeQtWidgets, combo)
    assert box.isChecked()
    assert combo.max_visible == 8
    assert combo._cellquant_want_scroll is True
    box.setChecked(False)
    box.toggled.emit(False)
    assert combo.max_visible == 20
    assert combo._cellquant_want_scroll is False


def test_checkbox_defaults_off_for_short_lists():
    combo = _FakeCombo(3)
    box = make_scrollability_checkbox(_FakeQtWidgets, combo)
    assert not box.isChecked()
    assert combo.max_visible == 3
    assert combo._cellquant_want_scroll is False


def test_force_popup_height_sizes_container_for_both_modes():
    from cellquant.plugin.combo_scroll import _force_popup_height

    combo = _FakeCombo(25)
    _force_popup_height(combo, scrollable=False, visible_items=8)
    container = combo.view().parentWidget()
    assert combo.view().minimumHeight() >= 20 * 25
    assert container.minimumHeight() >= combo.view().minimumHeight()
    assert container.maximumHeight() == 16777215

    _force_popup_height(combo, scrollable=True, visible_items=8)
    assert combo.view().maximumHeight() <= 20 * 8 + 4
    assert container.minimumHeight() == 0
    assert container.maximumHeight() == 16777215


def test_native_popup_toggle_changes_scrollbar_and_height():
    """Exercise the actual mouse-open path under Fusion (napari-like)."""
    from qtpy.QtCore import Qt
    from qtpy.QtTest import QTest
    from qtpy.QtWidgets import QApplication, QComboBox, QStyleFactory, QVBoxLayout, QWidget
    from qtpy import QtWidgets

    app = QApplication.instance() or QApplication([])
    if "Fusion" in QStyleFactory.keys():
        app.setStyle(QStyleFactory.create("Fusion"))

    parent = QWidget()
    parent.resize(420, 240)
    layout = QVBoxLayout(parent)
    combo = QComboBox()
    combo.addItems([str(i) for i in range(25)])
    checkbox = make_scrollability_checkbox(QtWidgets, combo)
    layout.addWidget(combo)
    layout.addWidget(checkbox)
    parent.show()
    parent.raise_()
    parent.activateWindow()
    app.processEvents()
    try:
        checkbox.setChecked(False)
        app.processEvents()
        QTest.mouseClick(combo, Qt.MouseButton.LeftButton)
        app.processEvents()
        QTest.qWait(80)
        view = combo.view()
        container = view.parentWidget()
        assert view.isVisible()
        assert view.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        assert view.verticalScrollBar().maximum() == 0
        assert view.height() >= view.sizeHintForRow(0) * 20
        assert container.height() >= view.height()
        combo.hidePopup()
        app.processEvents()

        checkbox.setChecked(True)
        app.processEvents()
        QTest.mouseClick(combo, Qt.MouseButton.LeftButton)
        app.processEvents()
        QTest.qWait(80)
        view = combo.view()
        assert view.isVisible()
        assert combo.maxVisibleItems() == 8
        assert view.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
        assert view.height() <= view.sizeHintForRow(0) * 8 + 8
        assert view.verticalScrollBar().maximum() > 0
    finally:
        combo.hidePopup()
        parent.close()
        app.processEvents()
