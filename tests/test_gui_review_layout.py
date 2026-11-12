"""Step 5: the new order, the pinned Approve, the optional sections, the "Applies to" control and the Undo banner.

Needs napari and Qt; skipped without napari.
"""

from __future__ import annotations

from pathlib import Path

import pytest

napari = pytest.importorskip("napari")

from tests.test_gui_crop import _events, _wait, open_window  # noqa: E402,F401
from tests.test_workflow import _recipe, _write_squares  # noqa: E402


def _shell(tmp_path: Path, open_window, images: int = 2):  # noqa: F811
    from cellquant.controller import AnalysisController

    paths = []
    for index in range(images):
        path = tmp_path / f"{'ab'[index]}.tif"
        _write_squares(path)
        paths.append(path)
    controller = AnalysisController.create(tmp_path / "experiment", "Review")
    controller.add_image_paths(paths)
    controller.set_recipe(_recipe())
    controller.save()
    shell = open_window(controller.directory)
    shell.run_current()
    assert _wait(shell, 120)
    shell.go_to_step(4)
    panel = shell._review_panel
    panel.display.setCurrentIndex(panel.display.findData("class_a"))
    _events(0.3)
    return shell


def test_order_pinned_approve_and_closed_sections(tmp_path: Path, open_window):  # noqa: F811
    shell = _shell(tmp_path, open_window)
    panel = shell._review_panel
    content = panel.scroll.widget()
    order = [panel.display, panel.histogram, panel.threshold_slider, panel.scope_row, panel.cutoff_tools, panel.counts, panel.strip,
             panel.count_area_section, panel.queue_section, panel.advanced_toggle]
    positions = [content.layout().indexOf(widget) for widget in order]
    assert all(position >= 0 for position in positions) and positions == sorted(positions)
    assert not panel.count_area_section.is_open() and not panel.queue_section.is_open()
    assert not panel.count_area_section.body.isVisibleTo(panel) and not panel.advanced_box.isVisibleTo(panel)
    assert [panel.queue_section.body.layout().itemAt(i).widget().text() for i in range(3)] == ["Needs a look", "Failed", "All included"]
    # Approve and the status line are outside the scrolling area, so they are always in view.
    assert not content.isAncestorOf(panel.approve_button) and not content.isAncestorOf(panel.status_line)
    assert panel.approve_button.text() == "Approve" and panel.edit_cells.text() == "Override calls…"
    assert not hasattr(panel, "apply_all")  # the bottom "Apply to all images" button is gone
    shell._tabs.setMinimumWidth(430)
    shell._dock.resize(430, 640)
    shell._dock.show()
    _events(0.5)
    shell._dock.grab()  # the napari window is not shown in tests: rendering the dock lays it out
    _events(0.3)
    bottom = panel.approve_button.mapTo(panel, panel.approve_button.rect().bottomLeft()).y()
    assert panel.approve_button.isVisibleTo(panel) and bottom <= panel.height()
    panel.count_area_section.header.click()
    assert panel.count_area_section.is_open() and panel.count_area_section.body.isVisibleTo(panel)


def test_a_section_in_use_opens_itself(tmp_path: Path, open_window):  # noqa: F811
    shell = _shell(tmp_path, open_window)
    panel, controller = shell._review_panel, shell.controller
    image_id = shell._nav_ids[shell._nav_index]
    shell.set_nav_filter("failed images", [image_id])
    _events(0.2)
    assert panel.queue_section.is_open() and not panel.count_area_section.is_open()
    controller.set_count_area([image_id], [[[0.0, 0.0], [0.0, 30.0], [30.0, 30.0], [30.0, 0.0]]])
    shell.show_current()
    assert _wait(shell)
    _events(0.2)
    assert panel.count_area_section.is_open()
    # Closed by hand while still in use: it stays closed until the use starts again.
    panel.count_area_section.set_open(False)
    shell.show_current()
    assert _wait(shell)
    _events(0.2)
    assert not panel.count_area_section.is_open()


def test_cleared_approval_shows_a_banner_and_undo_puts_everything_back(tmp_path: Path, open_window):  # noqa: F811
    from cellquant.controller import AnalysisController

    shell = _shell(tmp_path, open_window)
    panel, controller = shell._review_panel, shell.controller
    marker = controller.recipe.classifications[0]
    image_id = shell._nav_ids[shell._nav_index]
    controller.set_status(image_id, "approved")
    before = controller.recipe.model_dump(mode="json")["classifications"]
    assert not panel.cleared_banner.isVisibleTo(panel)

    panel._threshold_moved(900.0)  # nothing is positive any more: the percent positive changed
    _events(0.2)
    assert controller.experiment.image(image_id).processing_status == "analyzed"
    assert panel.cleared_banner.isVisibleTo(panel)
    assert panel.cleared_text.text() == "Approval cleared for 1 image whose percent positive changed."
    panel._threshold_moved(950.0)  # moving on keeps the state before the first change, and does not repeat the banner text
    _events(0.2)
    assert panel.cleared_text.text() == "Approval cleared for 1 image whose percent positive changed."

    panel.undo_cleared.click()
    _events(0.3)
    assert not panel.cleared_banner.isVisibleTo(panel)
    assert controller.recipe.model_dump(mode="json")["classifications"] == before
    assert controller.experiment.image(image_id).processing_status == "approved"
    assert controller.recipe.classifications[0].threshold_for(image_id) == marker.threshold_for(image_id)
    assert "Positive: 1" in panel.counts.text()  # what is on screen follows
    reopened = AnalysisController.open(controller.directory)  # and so does what is saved
    assert reopened.recipe.classifications[0].image_thresholds == {} and reopened.experiment.image(image_id).processing_status == "approved"

    # The banner goes away when dismissed, and on the next approval.
    panel._threshold_moved(900.0)
    _events(0.2)
    assert panel.cleared_banner.isVisibleTo(panel)
    panel._hide_banner()
    assert not panel.cleared_banner.isVisibleTo(panel) and panel._undo_state is None
    panel.threshold_slider.setValue(5.0)
    _events(0.2)
    panel._set_status("approved")
    _events(0.3)
    assert not panel.cleared_banner.isVisibleTo(panel)


def test_applies_to_goes_back_to_this_image_for_another_marker_or_analysis(tmp_path: Path, open_window, monkeypatch):  # noqa: F811
    shell = _shell(tmp_path, open_window)
    panel, controller = shell._review_panel, shell.controller
    monkeypatch.setattr(panel, "_ask_include_approved", lambda *args: True)

    def all_images() -> None:
        panel.scope.setCurrentIndex(panel.scope.findData("all"))
        panel._scope_chosen()

    all_images()
    assert panel.scope.currentData() == "all"
    panel.display.setCurrentIndex(panel.display.findData("class_b"))
    _events(0.2)
    assert panel.scope.currentData() == "this"
    all_images()
    controller.add_analysis("Second", activate=True)
    shell._refresh_all(keep_image=True)
    assert _wait(shell)
    _events(0.3)
    assert panel.scope.currentData() == "this"
    # The All markers view keeps its own button and never follows the scope.
    panel.display.setCurrentIndex(panel.display.findData("class_a"))
    all_images()
    panel.display.setCurrentIndex(panel.display.count() - 1)
    _events(0.2)
    assert panel.showing_all_markers() and panel._this_image_only()
    assert panel.combined.apply_all.text() == "Apply to all images"
