"""Per-dropdown Scrollability controls for CellQuant QComboBox popups."""

from __future__ import annotations

from typing import Any

# When Scrollability is on, show this many rows before the popup scrolls.
_SCROLLABLE_VISIBLE_ITEMS = 8
_POPUP_STYLE_SCROLLABLE = "QComboBox { combobox-popup: 0; }"
_POPUP_STYLE_EXPAND = "QComboBox { combobox-popup: 1; }"


def _merge_popup_stylesheet(combo: Any, scrollable: bool) -> None:
    if not hasattr(combo, "_cellquant_base_stylesheet"):
        try:
            combo._cellquant_base_stylesheet = str(combo.styleSheet() or "")
        except Exception:  # noqa: BLE001
            combo._cellquant_base_stylesheet = ""

    base = str(getattr(combo, "_cellquant_base_stylesheet", "") or "").strip()
    # Strip any prior CellQuant popup hints so re-apply stays idempotent.
    cleaned_lines = [
        line
        for line in base.splitlines()
        if "combobox-popup" not in line.replace(" ", "").casefold()
    ]
    cleaned = "\n".join(cleaned_lines).strip()
    popup_rule = _POPUP_STYLE_SCROLLABLE if scrollable else _POPUP_STYLE_EXPAND
    stylesheet = f"{cleaned}\n{popup_rule}".strip() if cleaned else popup_rule
    try:
        combo.setStyleSheet(stylesheet)
    except Exception:  # noqa: BLE001
        pass


def _force_popup_height(combo: Any, *, scrollable: bool, visible_items: int) -> None:
    """After showPopup, size the list view so maxVisibleItems actually sticks."""

    view = combo.view() if hasattr(combo, "view") else None
    if view is None:
        return
    count = int(combo.count())
    if count <= 0:
        return
    try:
        row_height = int(view.sizeHintForRow(0))
    except Exception:  # noqa: BLE001
        row_height = 0
    if row_height <= 0:
        try:
            row_height = int(view.fontMetrics().height()) + 4
        except Exception:  # noqa: BLE001
            row_height = 22

    rows = min(count, int(visible_items)) if scrollable else count
    # Frame / margins; keep a modest pad so the last row is not clipped.
    height = max(row_height * rows + 4, row_height)
    try:
        view.setMinimumHeight(height if not scrollable else 0)
        if scrollable:
            view.setMaximumHeight(height)
        else:
            view.setMaximumHeight(16777215)
    except Exception:  # noqa: BLE001
        pass

    try:
        from qtpy.QtCore import Qt

        if scrollable:
            policy = getattr(Qt, "ScrollBarAsNeeded", None)
            if policy is None:
                policy = Qt.ScrollBarPolicy.ScrollBarAsNeeded
        else:
            # A previous scrollable popup may leave its scrollbar visible even
            # after the checkbox is cleared.  Hide it explicitly; Qt will still
            # constrain the popup to the available screen height if necessary.
            policy = getattr(Qt, "ScrollBarAlwaysOff", None)
            if policy is None:
                policy = Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        view.setVerticalScrollBarPolicy(policy)
    except Exception:  # noqa: BLE001
        pass


def apply_combo_scrollability(
    combo: Any,
    scrollable: bool,
    *,
    visible_items: int = _SCROLLABLE_VISIBLE_ITEMS,
) -> None:
    """Configure whether a combo popup scrolls or lists all items.

    ``QComboBox.setMaxVisibleItems`` alone is ignored by styles that use a
    menu-like popup (notably Fusion). The undocumented ``combobox-popup``
    stylesheet hint forces a scrollable list view (0) or the expanding menu
    popup (1). See QTBUG-89037. We also re-apply on ``showPopup`` and size the
    list view so the checkbox effect is visible in napari.
    """

    combo._cellquant_want_scroll = bool(scrollable)
    combo._cellquant_visible_items = int(visible_items)
    _merge_popup_stylesheet(combo, scrollable)

    if scrollable:
        combo.setMaxVisibleItems(int(visible_items))
    else:
        combo.setMaxVisibleItems(max(int(combo.count()), 1))

    _install_show_popup_hook(combo)


def _install_show_popup_hook(combo: Any) -> None:
    if getattr(combo, "_cellquant_popup_hooked", False):
        return

    original = combo.showPopup

    def show_popup() -> None:
        scrollable = bool(getattr(combo, "_cellquant_want_scroll", False))
        visible_items = int(
            getattr(combo, "_cellquant_visible_items", _SCROLLABLE_VISIBLE_ITEMS)
        )
        _merge_popup_stylesheet(combo, scrollable)
        if scrollable:
            combo.setMaxVisibleItems(visible_items)
        else:
            combo.setMaxVisibleItems(max(int(combo.count()), 1))
        original()
        _force_popup_height(combo, scrollable=scrollable, visible_items=visible_items)

    combo.showPopup = show_popup  # type: ignore[method-assign]
    combo._cellquant_popup_hooked = True

    # QComboBox's native C++ mouse handler can bypass an instance-level
    # ``showPopup`` assignment.  An event filter applies the same settings on
    # the real user interaction path, after Qt has created the popup.
    try:
        from qtpy import QtCore

        class _PopupFilter(QtCore.QObject):
            def eventFilter(self, watched, event):  # noqa: N802
                result = super().eventFilter(watched, event)
                if event.type() in (
                    QtCore.QEvent.Type.MouseButtonPress,
                    QtCore.QEvent.Type.KeyPress,
                ):
                    QtCore.QTimer.singleShot(
                        0,
                        lambda: _force_popup_height(
                            combo,
                            scrollable=bool(getattr(combo, "_cellquant_want_scroll", False)),
                            visible_items=int(getattr(combo, "_cellquant_visible_items", _SCROLLABLE_VISIBLE_ITEMS)),
                        ),
                    )
                return result

        combo._cellquant_popup_filter = _PopupFilter(combo)
        combo.installEventFilter(combo._cellquant_popup_filter)
    except Exception:  # noqa: BLE001
        # Lightweight fakes and non-Qt callers still use the direct hook above.
        pass


def make_scrollability_checkbox(
    QtWidgets,
    combo: Any,
    *,
    default: bool | None = None,
    tip_text: str | None = None,
):
    """Create a Scrollability checkbox wired to ``combo``."""

    box = QtWidgets.QCheckBox("Scrollability")
    if default is None:
        default = int(combo.count()) > _SCROLLABLE_VISIBLE_ITEMS
    box.setChecked(bool(default))
    if tip_text:
        box.setToolTip(tip_text)
    else:
        box.setToolTip(
            "When checked, this dropdown’s popup scrolls after about "
            f"{_SCROLLABLE_VISIBLE_ITEMS} items.\n"
            "When unchecked, the popup expands to show every option (no scroll)."
        )

    def apply(_checked=None) -> None:
        apply_combo_scrollability(combo, box.isChecked())

    box.toggled.connect(apply)
    model = combo.model()
    if model is not None:
        model.rowsInserted.connect(lambda *_: apply())
        model.rowsRemoved.connect(lambda *_: apply())
    apply()
    combo._cellquant_scrollability = box
    return box


def wrap_combo_with_scrollability(
    QtWidgets,
    combo: Any,
    *,
    default: bool | None = None,
    tip_text: str | None = None,
):
    """Return ``[dropdown stretch][Scrollability]`` with the control on the right."""

    existing = getattr(combo, "_cellquant_scroll_row", None)
    if existing is not None:
        return existing

    row = QtWidgets.QWidget()
    layout = QtWidgets.QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    layout.addWidget(combo, 1)
    box = make_scrollability_checkbox(
        QtWidgets, combo, default=default, tip_text=tip_text
    )
    layout.addWidget(box, 0)
    combo._cellquant_scroll_row = row
    return row


def install_scrollability_controls(root: Any, QtWidgets, *, tip_text: str | None = None) -> int:
    """Put Scrollability on the right of every undecorated QComboBox under ``root``."""

    QComboBox = QtWidgets.QComboBox
    QFormLayout = QtWidgets.QFormLayout
    FieldRole = QFormLayout.FieldRole
    decorated = 0

    for combo in list(root.findChildren(QComboBox)):
        if getattr(combo, "_cellquant_scrollability", None) is not None:
            continue

        parent = combo.parentWidget()
        layout = parent.layout() if parent is not None else None
        placement = None  # ("form", row) | ("index", i) | None

        if isinstance(layout, QFormLayout):
            for row_index in range(layout.rowCount()):
                field_item = layout.itemAt(row_index, FieldRole)
                if field_item is not None and field_item.widget() is combo:
                    placement = ("form", row_index)
                    break
        elif layout is not None:
            for index in range(layout.count()):
                item = layout.itemAt(index)
                if item is not None and item.widget() is combo:
                    placement = ("index", index)
                    break

        row = wrap_combo_with_scrollability(QtWidgets, combo, tip_text=tip_text)

        if placement is None:
            if layout is not None:
                layout.addWidget(row)
        elif placement[0] == "form":
            layout.setWidget(placement[1], FieldRole, row)
        else:
            index = placement[1]
            # Combo was reparented out; insert the row at the old index.
            layout.insertWidget(index, row)

        decorated += 1
    return decorated


__all__ = [
    "apply_combo_scrollability",
    "install_scrollability_controls",
    "make_scrollability_checkbox",
    "wrap_combo_with_scrollability",
]
