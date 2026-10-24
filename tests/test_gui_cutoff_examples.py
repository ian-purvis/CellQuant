"""Examples mode in steps 4 and 5: choose it per marker, click objects positive or negative.

Needs napari and Qt; skipped without napari.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

napari = pytest.importorskip("napari")

from tests.test_gui_crop import _events, _wait, open_window  # noqa: E402,F401
from tests.test_gui_delete_area import _click  # noqa: E402
from tests.test_workflow import _recipe, _write_squares  # noqa: E402


def test_examples_mode(tmp_path: Path, open_window):  # noqa: F811
    from cellquant.controller import AnalysisController
    from cellquant.gui.app import EXAMPLES_LAYER

    image = tmp_path / "a.tif"
    _write_squares(image)
    controller = AnalysisController.create(tmp_path / "experiment", "Examples")
    controller.add_image_paths([image])
    controller.set_recipe(_recipe())
    controller.save()
    shell = open_window(controller.directory)
    shell.run_current()
    assert _wait(shell, 120)
    controller = shell.controller
    image_id = controller.experiment.images[0].image_id
    panel, viewer = shell._review_panel, shell.viewer
    data = np.asarray(viewer.layers["Objects"].data)

    # Step 4: one "Set cutoff by" choice per marker, Slider to start with.
    setup = shell._marker_setup
    setup.refresh()
    assert len(setup.mode_boxes) == len(controller.recipe.classifications)
    assert all(box.currentData() == "slider" for box in setup.mode_boxes)
    shell.go_to_step(4)
    panel.display.setCurrentIndex(panel.display.findData("class_a"))
    _events(0.2)
    assert panel.threshold_slider.isVisibleTo(panel) and not panel.examples_box.isVisibleTo(panel)
    box = next(box for box in setup.mode_boxes if box.property("classification_id") == "class_a")
    box.setCurrentIndex(box.findData("examples"))
    box.activated.emit(box.currentIndex())
    _events(0.2)
    assert next(item for item in controller.recipe.classifications if item.id == "class_a").cutoff_by == "examples"
    assert panel.examples_box.isVisibleTo(panel) and not panel.threshold_slider.isVisibleTo(panel)
    assert not panel.cutoff_tools.isVisibleTo(panel) and panel.apply_all.isVisibleTo(panel)
    assert not panel.histogram.draggable
    assert "Mark +" in shell._step_pages[4].todo_card.text()

    # Mark +: a click on the bright object marks it; a click on background marks nothing.
    shell._tabs.setCurrentWidget(shell._step_pages[4])
    viewer.layers.selection.active = viewer.layers["Objects"]
    panel.mark_positive.click()
    assert panel.mark_positive.isChecked() and viewer.cursor.style == "cross"
    _click(panel, viewer, data, (1.0, 1.0))
    assert controller.cutoff_examples("class_a", image_id) == {}
    _click(panel, viewer, data, (15.0, 15.0))
    assert controller.cutoff_examples("class_a", image_id) == {1: True}
    assert "one positive and one negative" in panel.examples_note.text()
    assert EXAMPLES_LAYER in viewer.layers and len(viewer.layers[EXAMPLES_LAYER].data) == 1
    # Mark −: the dim object. The cutoff goes halfway, for this image only.
    panel.mark_negative.click()
    assert panel.mark_negative.isChecked() and not panel.mark_positive.isChecked()
    _click(panel, viewer, data, (45.0, 45.0))
    marker = next(item for item in controller.recipe.classifications if item.id == "class_a")
    assert marker.threshold_for(image_id) == 410 and marker.image_thresholds == {image_id: 410}
    assert panel.examples_note.text().startswith("Examples: 1 +, 1 −")
    assert len(viewer.layers[EXAMPLES_LAYER].data) == 2

    # Drawing a count area: the click belongs to that tool.
    panel._draw_count_area()
    _click(panel, viewer, data, (45.0, 45.0))
    assert controller.cutoff_examples("class_a", image_id) == {1: True, 2: False}
    viewer.layers.selection.active = viewer.layers["Objects"]

    # A cutoff the examples disagree with is flagged, in the note and on the image.
    controller.update_thresholds(image_id, {"class_a": 900}, this_image_only=True)
    shell.show_classification(controller.classified_now(image_id))
    assert "Called the other way: object 1" in panel.examples_note.text()
    assert "suggest 410" in panel.examples_note.text()

    # Esc (and a step change) stop marking; Clear examples keeps the cutoff.
    panel.leave_marking()
    assert not panel.mark_negative.isChecked() and viewer.cursor.style != "cross"
    _click(panel, viewer, data, (15.0, 15.0))
    assert controller.cutoff_examples("class_a", image_id) == {1: True, 2: False}
    panel.mark_positive.click()
    shell.go_to_step(3)
    assert panel._marking is None
    panel.clear_examples.click()
    assert controller.cutoff_examples("class_a", image_id) == {}
    assert next(item for item in controller.recipe.classifications if item.id == "class_a").threshold_for(image_id) == 900
    assert EXAMPLES_LAYER not in viewer.layers

    # Back to Slider in step 4: the slider tools return and marking is off.
    box = next(box for box in setup.mode_boxes if box.property("classification_id") == "class_a")
    assert box.currentData() == "examples"
    box.setCurrentIndex(box.findData("slider"))
    box.activated.emit(box.currentIndex())
    _events(0.2)
    assert panel.threshold_slider.isVisibleTo(panel) and not panel.examples_box.isVisibleTo(panel)
    assert panel.histogram.draggable
    assert "Drag" in shell._step_pages[4].todo_card.text()

    # The mode buttons are locked during a run.
    shell.set_busy(True)
    assert not panel.clear_examples.isEnabled()
    shell.set_busy(False)
