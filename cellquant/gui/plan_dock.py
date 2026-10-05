"""The Plan dock: which images each analysis runs, and in which channel, in one place.

Two views of the same plan, chosen at the top:

- **Grid**: one row per image (under its group), one column per analysis. A ticked cell means that
  analysis runs that image; the cell shows its status and the channel objects are found in.
- **Tree**: group, then image, then one row per analysis, with a channel menu on each row.

Images are grouped by channel layout, by folder, or by both. Ticking a group (or an analysis
column's box on a group row) ticks every image under it. The bar at the bottom applies a choice to
the selected images or to all images, in one analysis or in all of them.
"""

from __future__ import annotations

from qtpy.QtCore import Qt
from qtpy.QtGui import QBrush, QColor, QFont
from qtpy.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cellquant.plan import GROUPING_LABELS, group_images

IMAGE_ROLE = Qt.UserRole
KIND_ROLE = Qt.UserRole + 1  # "group", "image" or "analysis"
ANALYSIS_ROLE = Qt.UserRole + 2

# Cell colours by status (soft, readable on light and dark themes).
STATUS_COLORS = {
    "analyzed": QColor(70, 150, 90, 90),
    "reviewed": QColor(70, 150, 90, 130),
    "approved": QColor(40, 150, 70, 170),
    "needs attention": QColor(215, 160, 30, 120),
    "failed": QColor(200, 60, 60, 130),
}
REASON_MARKS = {
    "chosen for this image": "★ ",
    "same name": "",
    "analysis": "",
    "not in this image": "⚠ ",
}


class PlanDock(QWidget):
    def __init__(self, shell):
        super().__init__()
        self.shell = shell
        self._building = False
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Tick which images each analysis runs. A ticked group ticks every image in it. "
            "★ marks a channel chosen for one image; ⚠ means that image has no channel of the analysis's "
            "channel name, so check it. Double-click a cell to open that image with that analysis."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        options = QHBoxLayout()
        options.addWidget(QLabel("Group by"))
        self.grouping = QComboBox()
        for key, label in GROUPING_LABELS.items():
            self.grouping.addItem(label, key)
        self.grouping.setToolTip(
            "Channel layout: images whose files list the same channels in the same order.\n"
            "Folder: the folder each image is in, inside the folder you added."
        )
        options.addWidget(self.grouping, 1)
        options.addWidget(QLabel("View"))
        self.view_mode = QComboBox()
        self.view_mode.addItem("Grid: images × analyses", "grid")
        self.view_mode.addItem("Tree: image ▸ analyses", "tree")
        options.addWidget(self.view_mode, 1)
        layout.addLayout(options)
        self.tree = QTreeWidget()
        self.tree.setMinimumHeight(260)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        layout.addWidget(self.tree, 1)

        apply_row = QHBoxLayout()
        apply_row.addWidget(QLabel("Apply to"))
        self.scope = QComboBox()
        self.scope.addItem("selected images", "selected")
        self.scope.addItem("all images", "all")
        apply_row.addWidget(self.scope)
        apply_row.addWidget(QLabel("in"))
        self.target = QComboBox()
        apply_row.addWidget(self.target, 1)
        layout.addLayout(apply_row)
        actions = QHBoxLayout()
        self.tick = QPushButton("Tick")
        self.untick = QPushButton("Untick")
        self.channel = QComboBox()
        self.channel.setToolTip("The channel to find objects in, for the chosen images only.")
        self.set_channel = QPushButton("Set channel")
        for widget in (self.tick, self.untick):
            actions.addWidget(widget)
        actions.addWidget(QLabel("Find objects in"))
        actions.addWidget(self.channel, 1)
        actions.addWidget(self.set_channel)
        layout.addLayout(actions)
        self.tick.clicked.connect(lambda: self._apply_run(True))
        self.untick.clicked.connect(lambda: self._apply_run(False))
        self.set_channel.clicked.connect(self._apply_channel)
        bottom = QHBoxLayout()
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        bottom.addWidget(self.summary, 1)
        self.run = QPushButton("Run ticked")
        self.run.setToolTip("Run every ticked image with its analysis, one analysis after another.")
        self.run.clicked.connect(lambda: self.shell.run_plan())
        bottom.addWidget(self.run)
        layout.addLayout(bottom)
        self.grouping.currentIndexChanged.connect(lambda _index: self.refresh())
        self.view_mode.currentIndexChanged.connect(lambda _index: self.refresh())

    # -- building ----------------------------------------------------------------------------------

    def refresh(self) -> None:
        controller = self.shell.controller
        self._building = True
        try:
            expanded = self._expanded_keys()
            self.tree.clear()
            self._fill_targets()
            if controller is None:
                self.summary.setText("Open an experiment first.")
                return
            groups = group_images(controller.experiment.images, str(self.grouping.currentData()))
            if self.view_mode.currentData() == "tree":
                self._build_tree(groups)
            else:
                self._build_grid(groups)
            self._restore_expanded(expanded)
            self._update_summary()
        finally:
            self._building = False

    def _analyses(self):
        controller = self.shell.controller
        return controller.analyses() if controller is not None else []

    def _build_grid(self, groups) -> None:
        analyses = self._analyses()
        self.tree.setColumnCount(1 + len(analyses))
        self.tree.setHeaderLabels(["Image", *[item.name for item in analyses]])
        for group in groups:
            self._grid_group(self.tree.invisibleRootItem(), group, analyses)
        self.tree.expandAll()
        for column in range(self.tree.columnCount()):
            self.tree.resizeColumnToContents(column)

    def _grid_group(self, parent, group, analyses) -> QTreeWidgetItem:
        item = QTreeWidgetItem(parent, [group.label])
        self._mark(item, "group", group.all_image_ids(), key=group.key)
        font = QFont(item.font(0))
        font.setBold(True)
        item.setFont(0, font)
        for child in group.children:
            self._grid_group(item, child, analyses)
        for image_id in group.image_ids:
            self._grid_image(item, image_id, analyses)
        for column, analysis in enumerate(analyses, start=1):
            self._set_group_state(item, column, group.all_image_ids(), analysis.recipe_id)
        return item

    def _grid_image(self, parent, image_id, analyses) -> None:
        controller = self.shell.controller
        record = controller.experiment.image(image_id)
        item = QTreeWidgetItem(parent, [record.relative_path or record.filename])
        self._mark(item, "image", [image_id])
        item.setToolTip(0, f"{record.relative_path or record.filename}\nChannels: {', '.join(record.channel_names) or 'unnamed'}")
        for column, analysis in enumerate(analyses, start=1):
            status = controller.plan_status(image_id, analysis.recipe_id)
            channel, reason = controller.segmentation_channel_for(image_id, analysis.recipe_id)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(column, Qt.Checked if status != "not planned" else Qt.Unchecked)
            item.setText(column, self._cell_text(record, status, channel, reason))
            item.setToolTip(column, self._cell_tip(analysis.name, record, status, channel, reason))
            color = STATUS_COLORS.get(status)
            if color is not None:
                item.setBackground(column, QBrush(color))

    def _build_tree(self, groups) -> None:
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Image / analysis", "Status", "Find objects in"])
        for group in groups:
            self._tree_group(self.tree.invisibleRootItem(), group)
        self.tree.resizeColumnToContents(0)

    def _tree_group(self, parent, group) -> None:
        item = QTreeWidgetItem(parent, [group.label])
        self._mark(item, "group", group.all_image_ids(), key=group.key)
        font = QFont(item.font(0))
        font.setBold(True)
        item.setFont(0, font)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        for child in group.children:
            self._tree_group(item, child)
        for image_id in group.image_ids:
            self._tree_image(item, image_id)
        self._set_group_state(item, 0, group.all_image_ids(), None)

    def _tree_image(self, parent, image_id) -> None:
        controller = self.shell.controller
        record = controller.experiment.image(image_id)
        item = QTreeWidgetItem(parent, [record.relative_path or record.filename])
        self._mark(item, "image", [image_id])
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        self._set_group_state(item, 0, [image_id], None)
        for analysis in self._analyses():
            status = controller.plan_status(image_id, analysis.recipe_id)
            channel, reason = controller.segmentation_channel_for(image_id, analysis.recipe_id)
            row = QTreeWidgetItem(item, [analysis.name, status, ""])
            self._mark(row, "analysis", [image_id], analysis=analysis.recipe_id)
            row.setFlags(row.flags() | Qt.ItemIsUserCheckable)
            row.setCheckState(0, Qt.Checked if status != "not planned" else Qt.Unchecked)
            row.setToolTip(0, self._cell_tip(analysis.name, record, status, channel, reason))
            color = STATUS_COLORS.get(status)
            if color is not None:
                row.setBackground(1, QBrush(color))
            menu = QComboBox()
            chosen = reason == "chosen for this image"
            base = int(controller._recipe_of(analysis.recipe_id).object_set.segmentation_channel)
            default, default_reason = controller._map_channel(record, base)
            menu.addItem(f"{REASON_MARKS.get(default_reason, '')}analysis channel ({self._name(record, default)})", None)
            for index, name in enumerate(self._channel_names(record)):
                menu.addItem(f"★ {name} (this image)", index)
            menu.setCurrentIndex(menu.findData(channel) if chosen else 0)
            menu.currentIndexChanged.connect(
                lambda _index, box=menu, image=image_id, recipe=analysis.recipe_id: self._channel_menu(box, image, recipe)
            )
            self.tree.setItemWidget(row, 2, menu)

    # -- helpers -----------------------------------------------------------------------------------

    @staticmethod
    def _mark(item, kind: str, image_ids: list[str], key: str = "", analysis: str = "") -> None:
        item.setData(0, KIND_ROLE, kind)
        item.setData(0, IMAGE_ROLE, list(image_ids))
        item.setData(0, ANALYSIS_ROLE, analysis or key)

    def _set_group_state(self, item, column: int, image_ids: list[str], recipe_id) -> None:
        controller = self.shell.controller
        recipes = [recipe_id] if recipe_id else [analysis.recipe_id for analysis in self._analyses()]
        ticks = [controller.is_planned(image_id, recipe) for image_id in image_ids for recipe in recipes]
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        if ticks and all(ticks):
            item.setCheckState(column, Qt.Checked)
        elif any(ticks):
            item.setCheckState(column, Qt.PartiallyChecked)
        else:
            item.setCheckState(column, Qt.Unchecked)

    def _channel_names(self, record) -> list[str]:
        controller = self.shell.controller
        if record.channel_names:
            return list(record.channel_names)
        count = record.number_of_channels or len(controller.experiment.channels)
        return [controller._channel_name(index) for index in range(count)]

    def _name(self, record, index: int) -> str:
        names = self._channel_names(record)
        return names[index] if 0 <= index < len(names) else f"channel {index + 1}"

    def _cell_text(self, record, status: str, channel: int, reason: str) -> str:
        if status == "not planned":
            return "—"
        return f"{status} · {REASON_MARKS.get(reason, '')}{self._name(record, channel)}"

    def _cell_tip(self, analysis: str, record, status: str, channel: int, reason: str) -> str:
        why = {
            "chosen for this image": "chosen for this image",
            "same name": "the channel of the same name in this image's layout",
            "analysis": "the analysis's channel",
            "not in this image": "this image has no channel of the analysis's channel name: check it",
        }.get(reason, reason)
        return f"{analysis}\n{record.relative_path or record.filename}\nStatus: {status}\nFinds objects in {self._name(record, channel)} ({why})"

    def _fill_targets(self) -> None:
        current = self.target.currentData()
        self.target.blockSignals(True)
        self.target.clear()
        self.target.addItem("all analyses", None)
        for analysis in self._analyses():
            self.target.addItem(analysis.name, analysis.recipe_id)
        index = self.target.findData(current)
        self.target.setCurrentIndex(index if index >= 0 else 0)
        self.target.blockSignals(False)
        self.channel.clear()
        self.channel.addItem("the analysis's channel", None)
        controller = self.shell.controller
        if controller is not None:
            for channel in controller.experiment.channels:
                self.channel.addItem(channel.channel_name, channel.channel_index)

    def _update_summary(self) -> None:
        controller = self.shell.controller
        runs = sum(len(controller.planned_images(analysis.recipe_id)) for analysis in self._analyses())
        self.summary.setText(f"{runs} image runs ticked in {len(self._analyses())} analyses.")

    def _expanded_keys(self) -> set[str]:
        keys = set()
        iterator = [self.tree.topLevelItem(index) for index in range(self.tree.topLevelItemCount())]
        while iterator:
            item = iterator.pop()
            if item.data(0, KIND_ROLE) == "group" and not item.isExpanded():
                keys.add(str(item.data(0, ANALYSIS_ROLE)))
            iterator.extend(item.child(index) for index in range(item.childCount()))
        return keys

    def _restore_expanded(self, collapsed: set[str]) -> None:
        iterator = [self.tree.topLevelItem(index) for index in range(self.tree.topLevelItemCount())]
        while iterator:
            item = iterator.pop()
            if item.data(0, KIND_ROLE) == "group":
                item.setExpanded(str(item.data(0, ANALYSIS_ROLE)) not in collapsed)
            elif item.data(0, KIND_ROLE) == "image" and self.view_mode.currentData() == "tree":
                item.setExpanded(True)
            iterator.extend(item.child(index) for index in range(item.childCount()))

    def selected_image_ids(self) -> list[str]:
        ids: list[str] = []
        for item in self.tree.selectedItems():
            for image_id in item.data(0, IMAGE_ROLE) or []:
                if image_id not in ids:
                    ids.append(image_id)
        return ids

    # -- changes -----------------------------------------------------------------------------------

    def _guard(self) -> bool:
        if self.shell.controller is None:
            return False
        if self.shell.is_busy():
            self.shell.message("Wait for the current analysis to finish before changing the plan.")
            return False
        return True

    def _item_changed(self, item, column: int) -> None:
        if self._building or not self._guard():
            return
        controller = self.shell.controller
        kind = item.data(0, KIND_ROLE)
        ids = list(item.data(0, IMAGE_ROLE) or [])
        ticked = item.checkState(column) != Qt.Unchecked
        if self.view_mode.currentData() == "tree":
            if column != 0:
                return
            recipes = [item.data(0, ANALYSIS_ROLE)] if kind == "analysis" else None
        else:
            if column == 0:
                return
            recipes = [self._analyses()[column - 1].recipe_id]
        controller.set_planned(ids, ticked, recipes)
        self.shell.plan_changed()

    def _apply_ids(self) -> list[str]:
        controller = self.shell.controller
        if self.scope.currentData() == "all":
            return [record.image_id for record in controller.experiment.images]
        ids = self.selected_image_ids()
        if not ids:
            self.shell.message("Select images (or groups) in the plan first, or choose 'all images'.")
        return ids

    def _apply_recipes(self) -> list[str] | None:
        chosen = self.target.currentData()
        return [str(chosen)] if chosen else None

    def _apply_run(self, ticked: bool) -> None:
        if not self._guard():
            return
        ids = self._apply_ids()
        if ids:
            self.shell.controller.set_planned(ids, ticked, self._apply_recipes())
            self.shell.plan_changed()

    def _apply_channel(self) -> None:
        if not self._guard():
            return
        ids = self._apply_ids()
        if not ids:
            return
        controller = self.shell.controller
        recipes = self._apply_recipes() or [analysis.recipe_id for analysis in self._analyses()]
        try:
            controller.set_plan_channel(ids, self.channel.currentData(), recipes)
        except Exception as exc:  # noqa: BLE001 - shown to the user
            self.shell.show_error(str(exc))
            return
        self.shell.plan_changed()

    def _channel_menu(self, box: QComboBox, image_id: str, recipe_id: str) -> None:
        if not self._guard():
            return
        try:
            self.shell.controller.set_plan_channel([image_id], box.currentData(), [recipe_id])
        except Exception as exc:  # noqa: BLE001
            self.shell.show_error(str(exc))
        self.shell.plan_changed()

    def _cell(self, item, column: int) -> tuple[str | None, str | None]:
        kind = item.data(0, KIND_ROLE)
        ids = item.data(0, IMAGE_ROLE) or []
        if kind == "analysis":
            return ids[0], str(item.data(0, ANALYSIS_ROLE))
        if kind == "image" and self.view_mode.currentData() == "grid" and column >= 1:
            return ids[0], self._analyses()[column - 1].recipe_id
        if kind == "image":
            return ids[0], None
        return None, None

    def _double_clicked(self, item, column: int) -> None:
        image_id, recipe_id = self._cell(item, column)
        if image_id is not None:
            self.shell.open_in_analysis(image_id, recipe_id)

    def _context_menu(self, position) -> None:
        item = self.tree.itemAt(position)
        if item is None or self.shell.controller is None:
            return
        column = self.tree.columnAt(position.x())
        image_id, recipe_id = self._cell(item, column)
        if image_id is None:
            return
        menu = QMenu(self)
        open_action = menu.addAction("Open this image" + (" with this analysis" if recipe_id else ""))
        open_action.triggered.connect(lambda: self.shell.open_in_analysis(image_id, recipe_id))
        if recipe_id:
            record = self.shell.controller.experiment.image(image_id)
            sub = menu.addMenu("Find objects in (this image, this analysis)")
            default = sub.addAction("the analysis's channel")
            default.triggered.connect(lambda: self._set_one(image_id, recipe_id, None))
            for index, name in enumerate(self._channel_names(record)):
                action = sub.addAction(name)
                action.triggered.connect(lambda _checked=False, value=index: self._set_one(image_id, recipe_id, value))
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def _set_one(self, image_id: str, recipe_id: str, channel) -> None:
        if not self._guard():
            return
        try:
            self.shell.controller.set_plan_channel([image_id], channel, [recipe_id])
        except Exception as exc:  # noqa: BLE001
            self.shell.show_error(str(exc))
        self.shell.plan_changed()
