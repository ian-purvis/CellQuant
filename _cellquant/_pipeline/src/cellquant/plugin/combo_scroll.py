"""Per-dropdown Scrollability controls for CellQuant QComboBox popups."""

from __future__ import annotations

from typing import Any

# When Scrollability is on, show this many rows before the popup scrolls.
_SCROLLABLE_VISIBLE_ITEMS = 8
# Prefer the expanding popup; we size it ourselves after showPopup.
# ``combobox-popup: 0`` (scrollable list) is unreliable on Fusion/Windows —
# popups often open invisible — so we do not use it.
_POPUP_STYLE = "QComboBox { combobox-popup: 1; }"


def _merge_popup_stylesheet(combo: Any) -> None:
    if not hasattr(combo, "_cellquant_base_stylesheet"):
        try:
            combo._cellquant_base_stylesheet = str(combo.styleSheet() or "")
        except Exception:  # noqa: BLE001
            combo._cellquant_base_stylesheet = ""

    base = str(getattr(combo, "_cellquant_base_stylesheet", "") or "").strip()
    cleaned_lines = [
        line
        for line in base.splitlines()
        if "combobox-popup" not in line.replace(" ", "").casefold()
    ]
    cleaned = "\n".join(cleaned_lines).strip()
    stylesheet = f"{cleaned}\n{_POPUP_STYLE}".strip() if cleaned else _POPUP_STYLE
    try:
        combo.setStyleSheet(stylesheet)
    except Exception:  # noqa: BLE001
        pass


def _row_height(view: Any) -> int:
    try:
        row_height = int(view.sizeHintForRow(0))
    except Exception:  # noqa: BLE001
        row_height = 0
    if row_height <= 0:
        try:
            row_height = int(view.fontMetrics().height()) + 4
        except Exception:  # noqa: BLE001
            row_height = 22
    return max(row_height, 1)


def _screen_cap(combo: Any, desired: int) -> int:
    """Clamp popup height to the screen so expand mode still fits."""

    try:
        from qtpy.QtWidgets import QApplication

        screen = None
        if hasattr(combo, "screen"):
            screen = combo.screen()
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return desired
        avail = int(screen.availableGeometry().height())
        return max(min(desired, avail - 48), 22)
    except Exception:  # noqa: BLE001
        return desired


def _clear_popup_size_constraints(combo: Any) -> None:
    view = combo.view() if hasattr(combo, "view") else None
    if view is None:
        return
    try:
        view.setMinimumHeight(0)
        view.setMaximumHeight(16777215)
        container = view.parentWidget() if hasattr(view, "parentWidget") else None
        if container is not None:
            container.setMinimumHeight(0)
            container.setMaximumHeight(16777215)
    except Exception:  # noqa: BLE001
        pass


def _force_popup_height(combo: Any, *, scrollable: bool, visible_items: int) -> None:
    """Size the list view (and expand the frame when needed) after showPopup.

    Fusion ignores ``setMaxVisibleItems``. We open an expanding popup, then
    either clamp it to ``visible_items`` rows (scrollable) or stretch it to the
    full item list (not scrollable, capped to the screen).
    """

    view = combo.view() if hasattr(combo, "view") else None
    if view is None:
        return
    count = int(combo.count())
    if count <= 0:
        return

    row_height = _row_height(view)
    rows = min(count, int(visible_items)) if scrollable else count
    height = _screen_cap(combo, max(row_height * rows + 4, row_height))
    frame = 4
    container_height = height + frame
    container = view.parentWidget() if hasattr(view, "parentWidget") else None

    try:
        from qtpy.QtCore import Qt

        if scrollable:
            policy = getattr(Qt, "ScrollBarAsNeeded", None)
            if policy is None:
                policy = Qt.ScrollBarPolicy.ScrollBarAsNeeded
        else:
            policy = getattr(Qt, "ScrollBarAlwaysOff", None)
            if policy is None:
                policy = Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        view.setVerticalScrollBarPolicy(policy)
    except Exception:  # noqa: BLE001
        pass

    try:
        if scrollable:
            view.setMinimumHeight(0)
            view.setMaximumHeight(height)
        else:
            view.setMaximumHeight(16777215)
            view.setMinimumHeight(height)
    except Exception:  # noqa: BLE001
        pass

    if container is None:
        return
    try:
        container.setMinimumHeight(0)
        container.setMaximumHeight(16777215)
        if scrollable:
            # Shrink the frame to the clamped view without using a hard
            # maximumHeight (that can make Fusion popups open invisible).
            container.resize(max(int(container.width()), int(combo.width())), container_height)
        else:
            container.setMinimumHeight(container_height)
            container.resize(max(int(container.width()), int(combo.width())), container_height)
        view.updateGeometry()
        container.updateGeometry()
    except Exception:  # noqa: BLE001
        pass


def _prepare_combo_popup(combo: Any) -> tuple[bool, int]:
    """Apply stylesheet + maxVisibleItems for the current checkbox state."""

    scrollable = bool(getattr(combo, "_cellquant_want_scroll", False))
    visible_items = int(
        getattr(combo, "_cellquant_visible_items", _SCROLLABLE_VISIBLE_ITEMS)
    )
    _merge_popup_stylesheet(combo)
    if scrollable:
        combo.setMaxVisibleItems(visible_items)
    else:
        combo.setMaxVisibleItems(max(int(combo.count()), 1))
    return scrollable, visible_items


def apply_combo_scrollability(
    combo: Any,
    scrollable: bool,
    *,
    visible_items: int = _SCROLLABLE_VISIBLE_ITEMS,
) -> None:
    """Configure whether a combo popup scrolls or lists all items.

    ``QComboBox.setMaxVisibleItems`` alone is ignored by Fusion (napari). We
    force an expanding popup via stylesheet, then size it on open according to
    the Scrollability checkbox. See QTBUG-89037.
    """

    combo._cellquant_want_scroll = bool(scrollable)
    combo._cellquant_visible_items = int(visible_items)
    _prepare_combo_popup(combo)
    _install_show_popup_hook(combo)

    try:
        view = combo.view()
        if view is not None and view.isVisible():
            _force_popup_height(
                combo, scrollable=bool(scrollable), visible_items=int(visible_items)
            )
    except Exception:  # noqa: BLE001
        pass


def _install_show_popup_hook(combo: Any) -> None:
    if getattr(combo, "_cellquant_popup_hooked", False):
        return

    original = combo.showPopup

    def show_popup() -> None:
        scrollable, visible_items = _prepare_combo_popup(combo)
        _clear_popup_size_constraints(combo)
        original()
        _force_popup_height(combo, scrollable=scrollable, visible_items=visible_items)

    combo.showPopup = show_popup  # type: ignore[method-assign]
    combo._cellquant_popup_hooked = True

    # Native C++ mouse handling can bypass the instance ``showPopup`` patch.
    # Prepare before the click is delivered; resize again after the frame shows.
    try:
        from qtpy import QtCore

        class _PopupFilter(QtCore.QObject):
            def eventFilter(self, watched, event):  # noqa: N802
                etype = event.type()
                if etype in (
                    QtCore.QEvent.Type.MouseButtonPress,
                    QtCore.QEvent.Type.KeyPress,
                ):
                    if watched is combo:
                        scrollable, visible_items = _prepare_combo_popup(combo)
                        _clear_popup_size_constraints(combo)

                        def _after_open(
                            scrollable=scrollable, visible_items=visible_items
                        ) -> None:
                            _force_popup_height(
                                combo,
                                scrollable=scrollable,
                                visible_items=visible_items,
                            )

                        QtCore.QTimer.singleShot(0, _after_open)
                elif (
                    etype == QtCore.QEvent.Type.Show
                    and watched is not combo
                    and bool(getattr(combo, "_cellquant_popup_hooked", False))
                ):
                    _force_popup_height(
                        combo,
                        scrollable=bool(getattr(combo, "_cellquant_want_scroll", False)),
                        visible_items=int(
                            getattr(
                                combo,
                                "_cellquant_visible_items",
                                _SCROLLABLE_VISIBLE_ITEMS,
                            )
                        ),
                    )
                return super().eventFilter(watched, event)

        filt = _PopupFilter(combo)
        combo._cellquant_popup_filter = filt
        combo.installEventFilter(filt)
        view = combo.view() if hasattr(combo, "view") else None
        if view is not None:
            view.installEventFilter(filt)
            parent = view.parentWidget() if hasattr(view, "parentWidget") else None
            if parent is not None:
                parent.installEventFilter(filt)
    except Exception:  # noqa: BLE001
        pass


def make_scrollability_checkbox(
    QtWidgets,
    combo: Any,
    *,
    default: bool | None = None,
    tip_text: str | None = None,
):
    """Create a Scrollability checkbox wired to ``combo``."""

    box = QtWidgets.QCheckBox("Scroll")
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
            layout.insertWidget(index, row)

        decorated += 1
    return decorated


__all__ = [
    "apply_combo_scrollability",
    "install_scrollability_controls",
    "make_scrollability_checkbox",
    "wrap_combo_with_scrollability",
]
