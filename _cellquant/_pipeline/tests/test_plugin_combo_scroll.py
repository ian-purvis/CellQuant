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

    def setVerticalScrollBarPolicy(self, policy):
        self.policy = policy


class _FakeCombo:
    def __init__(self, count: int):
        self._count = count
        self.max_visible = None
        self._stylesheet = ""
        self._view = _FakeView()
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
    assert "combobox-popup: 0" in combo.styleSheet()
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
    assert "combobox-popup: 0" in combo.styleSheet()
    box.setChecked(False)
    box.toggled.emit(False)
    assert combo.max_visible == 20
    assert "combobox-popup: 1" in combo.styleSheet()


def test_checkbox_defaults_off_for_short_lists():
    combo = _FakeCombo(3)
    box = make_scrollability_checkbox(_FakeQtWidgets, combo)
    assert not box.isChecked()
    assert combo.max_visible == 3
    assert "combobox-popup: 1" in combo.styleSheet()


def test_native_popup_toggle_changes_scrollbar_policy():
    """Exercise the actual mouse-open path; instance hooks alone can be bypassed by Qt."""
    from qtpy.QtCore import Qt
    from qtpy.QtTest import QTest
    from qtpy.QtWidgets import QApplication, QComboBox, QWidget
    from qtpy import QtWidgets

    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    combo = QComboBox(parent)
    combo.addItems([str(i) for i in range(25)])
    checkbox = make_scrollability_checkbox(QtWidgets, combo)
    parent.show()
    app.processEvents()
    try:
        checkbox.setChecked(False)
        app.processEvents()
        QTest.mouseClick(combo, Qt.MouseButton.LeftButton)
        app.processEvents()
        assert combo.view().isVisible()
        assert combo.view().verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        combo.hidePopup()
        checkbox.setChecked(True)
        app.processEvents()
        QTest.mouseClick(combo, Qt.MouseButton.LeftButton)
        app.processEvents()
        assert combo.maxVisibleItems() == 8
        assert combo.view().verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    finally:
        combo.hidePopup()
        parent.close()
        app.processEvents()
