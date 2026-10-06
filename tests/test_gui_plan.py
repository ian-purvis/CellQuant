"""The Plan dock: grid and tree views, grouping by layout or folder, ticks, per-image channels, running
the plan and opening a cell. Needs napari and Qt (xvfb-run on Linux); skipped without napari."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

napari = pytest.importorskip("napari")


def _wait(shell, seconds: float = 300) -> None:
    from qtpy.QtWidgets import QApplication

    end = time.time() + seconds
    while (shell._job is not None or shell._batch is not None or shell._loader is not None) and time.time() < end:
        QApplication.processEvents()
        time.sleep(0.02)
    for _ in range(5):
        QApplication.processEvents()


def _settle() -> None:
    from qtpy.QtWidgets import QApplication

    for _ in range(5):
        QApplication.processEvents()


def _items(tree):
    stack = [tree.topLevelItem(index) for index in range(tree.topLevelItemCount())]
    while stack:
        item = stack.pop(0)
        yield item
        stack.extend(item.child(index) for index in range(item.childCount()))


def _image_item(dock, name: str):
    return next(item for item in _items(dock.tree) if item.data(0, dock_kind()) == "image" and item.text(0) == name)


def dock_kind():
    from cellquant.gui.plan_dock import KIND_ROLE

    return KIND_ROLE


def test_plan_dock(tmp_path: Path):
    from qtpy.QtCore import Qt
    from qtpy.QtWidgets import QComboBox

    from cellquant.gui.app import CellQuantWindow
    from tests.test_plan import _experiment, _ids

    controller = _experiment(tmp_path)
    ids = _ids(controller)
    second = controller.add_analysis("Objects in Green", activate=False)
    first = controller.recipe.recipe_id
    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"napari viewer could not start: {exc}")
    shell = CellQuantWindow(viewer, controller.directory)
    try:
        _wait(shell)
        shell.show_plan()
        dock = shell._plan_dock
        controller = shell.controller
        tree = dock.tree
        # Grid, grouped by channel layout: two layouts, one column per analysis.
        assert tree.columnCount() == 3 and tree.topLevelItemCount() == 2
        assert [tree.headerItem().text(column) for column in (1, 2)] == ["Analysis 1", "Objects in Green"]
        reordered = _image_item(dock, "Zeiss 40x/control_r1_reordered.tif")
        assert reordered.text(1).endswith("Far Red")  # found by name in the other layout
        assert "3 image runs ticked" not in dock.summary.text() and "6 image runs ticked" in dock.summary.text()

        # One cell.
        control = _image_item(dock, "Control/Retina 1/control_r1.tif")
        control.setCheckState(2, Qt.Unchecked)
        _settle()
        assert not controller.is_planned(ids["Control/Retina 1/control_r1.tif"], second)
        # A whole layout, in one analysis.
        group = tree.topLevelItem(1)
        group.setCheckState(1, Qt.Unchecked)
        _settle()
        assert not controller.is_planned(ids["Zeiss 40x/control_r1_reordered.tif"], first)
        assert controller.is_planned(ids["Zeiss 40x/control_r1_reordered.tif"], second)
        tree.topLevelItem(1).setCheckState(1, Qt.Checked)
        _settle()
        assert controller.is_planned(ids["Zeiss 40x/control_r1_reordered.tif"], first)

        # The bar: selected image, one analysis, a channel for that image only.
        crispri = _image_item(dock, "CRISPRi/Retina 1/crispri_r1.tif")
        tree.clearSelection()
        crispri.setSelected(True)
        dock.target.setCurrentIndex(dock.target.findData(first))
        dock.channel.setCurrentIndex(dock.channel.findData(1))
        dock.set_channel.click()
        _settle()
        assert controller.segmentation_channel_for(ids["CRISPRi/Retina 1/crispri_r1.tif"], first) == (1, "chosen for this image")
        assert "★" in _image_item(dock, "CRISPRi/Retina 1/crispri_r1.tif").text(1)
        dock.scope.setCurrentIndex(dock.scope.findData("all"))
        dock.target.setCurrentIndex(dock.target.findData(None))
        dock.untick.click()
        _settle()
        assert not any(controller.planned_images(item.recipe_id) for item in controller.analyses())
        dock.tick.click()
        _settle()
        assert all(len(controller.planned_images(item.recipe_id)) == 3 for item in controller.analyses())

        # Folders, and folders inside layouts.
        dock.grouping.setCurrentIndex(dock.grouping.findData("folder"))
        assert tree.topLevelItemCount() == 3
        dock.grouping.setCurrentIndex(dock.grouping.findData("layout_folder"))
        assert tree.topLevelItemCount() == 2 and tree.topLevelItem(0).childCount() == 2

        # Tree view: image ▸ analyses, each with a channel menu.
        dock.view_mode.setCurrentIndex(dock.view_mode.findData("tree"))
        image = _image_item(dock, "Control/Retina 1/control_r1.tif")
        assert image.childCount() == 2
        row = image.child(1)
        menu = tree.itemWidget(row, 2)
        assert isinstance(menu, QComboBox)
        menu.setCurrentIndex(menu.findData(0))
        _settle()
        assert controller.segmentation_channel_for(ids["Control/Retina 1/control_r1.tif"], second) == (0, "chosen for this image")
        image = _image_item(dock, "Control/Retina 1/control_r1.tif")
        image.child(0).setCheckState(0, Qt.Unchecked)
        _settle()
        assert not controller.is_planned(ids["Control/Retina 1/control_r1.tif"], first)

        # Run the plan: 2 + 3 image runs; the dock is locked while it runs.
        shell.run_plan()
        assert not dock.isEnabled()
        _wait(shell)
        assert dock.isEnabled()
        assert controller.plan_status(ids["Control/Retina 1/control_r1.tif"], first) == "not planned"
        assert controller.plan_status(ids["Control/Retina 1/control_r1.tif"], second) == "analyzed"
        result_image = ids["Control/Retina 1/control_r1.tif"]

        # Open a cell: that analysis, that image, step 5.
        shell.open_in_analysis(result_image, second)
        _wait(shell)
        assert controller.recipe.recipe_id == second
        assert shell._nav_ids[shell._nav_index] == result_image
        assert shell._tabs.currentWidget() is shell._step_pages[4]
        result = controller.recall(result_image)
        assert result.provenance["recipe"]["object_set"]["segmentation_channel"] == 0
    finally:
        viewer.close()
