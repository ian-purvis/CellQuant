"""Step 4: the first time one "Set up markers" button; once markers exist, ticking a channel applies by itself.

Needs napari and Qt; skipped without napari.
"""

from __future__ import annotations

from pathlib import Path

import pytest

napari = pytest.importorskip("napari")

from tests.test_gui_crop import _events, _wait, open_window  # noqa: E402,F401
from tests.test_workflow import _recipe, _write_squares  # noqa: E402


def _shell(tmp_path: Path, open_window):  # noqa: F811
    from cellquant.controller import AnalysisController

    image = tmp_path / "a.tif"
    _write_squares(image)
    controller = AnalysisController.create(tmp_path / "experiment", "Markers")
    controller.add_image_paths([image])
    recipe = _recipe()
    recipe["measurements"], recipe["classifications"], recipe["reports"] = [], [], []
    controller.set_recipe(recipe)
    controller.save()
    shell = open_window(controller.directory)
    shell.run_current()
    assert _wait(shell, 120)
    return shell


def _box(setup, text: str):
    return next(box for box in setup.boxes if box.text() == text)


def test_first_setup_uses_the_button_and_stays_on_step_4(tmp_path: Path, open_window, monkeypatch):  # noqa: F811
    shell = _shell(tmp_path, open_window)
    setup, controller = shell._marker_setup, shell.controller
    said: list[str] = []
    monkeypatch.setattr(shell, "message", said.append)
    shell.go_to_step(3)
    setup.refresh()
    assert setup.button.isVisibleTo(setup) and len(setup.boxes) >= 2
    # Ticking before any marker exists does nothing by itself.
    for box in setup.boxes:
        box.setChecked(box.text() in {setup.boxes[0].text(), setup.boxes[1].text()})
    assert not setup._auto_timer.isActive() and not controller.recipe.classifications
    setup.button.click()
    assert _wait(shell)
    _events(0.3)
    assert len(controller.recipe.classifications) == 2
    assert shell._tabs.currentWidget() is shell._step_pages[3]  # never moved
    assert shell._step_pages[3].next.isEnabled()
    assert said[-1] == "Markers measured. Next: check the cutoffs in step 5."
    assert not setup.button.isVisibleTo(setup)  # once markers exist, no button is needed


def test_ticking_and_unticking_apply_by_themselves_and_keep_what_stays(tmp_path: Path, open_window, monkeypatch):  # noqa: F811
    from qtpy.QtWidgets import QMessageBox

    shell = _shell(tmp_path, open_window)
    setup, controller = shell._marker_setup, shell.controller
    first, second = setup.boxes[0].text(), setup.boxes[1].text()
    for box in setup.boxes:
        box.setChecked(box.text() in {first, second})
    setup.button.click()
    assert _wait(shell)
    _events(0.3)
    image_id = controller.experiment.images[0].image_id
    marker = controller.recipe.classifications[0]
    result = controller.recall(image_id)
    controller.update_thresholds(image_id, {marker.id: 321.0}, this_image_only=True)
    controller.set_status(image_id, "approved")
    asked: list[str] = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda _parent, _title, text, *a, **k: asked.append(text) or QMessageBox.Yes))

    # Tick the third channel: after a pause it is measured, nothing else changes, nothing is asked.
    third = next(box.text() for box in setup.boxes if box.text() not in {first, second})
    _box(setup, third).setChecked(True)
    assert setup._auto_timer.isActive()  # a short pause first, no button press
    _events(1.4)
    assert _wait(shell)
    _events(0.3)
    names = [item.name for item in controller.recipe.classifications]
    assert names == [first, second, third] and not asked
    kept = controller.recipe.classifications[0]
    assert kept.image_thresholds == {image_id: 321.0} and kept.threshold == marker.threshold
    assert controller.experiment.image(image_id).processing_status == "approved"  # a staying marker keeps the approval
    assert all(item.id in controller.recall(image_id).objects.columns for item in controller.recipe.classifications)
    assert shell._tabs.currentWidget() is not shell._step_pages[4]  # never switches tabs

    # Untick the second: its calls by hand and the approval are named in the question; No leaves everything.
    controller.call_cells(image_id, controller.recipe.classifications[1].id, [1], True)
    answers = [QMessageBox.No, QMessageBox.Yes]
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda _parent, _title, text, *a, **k: asked.append(text) or answers.pop(0)))
    _box(setup, second).setChecked(False)
    setup._auto_apply()
    assert "1 object you called by hand for" in asked[-1] and "1 approved image will need approving again" in asked[-1]
    assert [item.name for item in controller.recipe.classifications] == [first, second, third]
    assert _box(setup, second).isChecked()  # the boxes went back to what exists
    _box(setup, second).setChecked(False)
    setup._auto_apply()
    assert _wait(shell)
    _events(0.3)
    assert [item.name for item in controller.recipe.classifications] == [first, third]
    assert controller.experiment.image(image_id).processing_status == "analyzed"
    assert controller.recipe.classifications[0].image_thresholds == {image_id: 321.0}  # the kept marker's own cutoff
    assert shell._tabs.currentWidget() is not shell._step_pages[4]
    # Saved state agrees with what is on screen.
    from cellquant.controller import AnalysisController

    reopened = AnalysisController.open(controller.directory)
    assert [item.name for item in reopened.recipe.classifications] == [first, third]


def test_changing_the_rule_asks_before_restarting_cutoffs(tmp_path: Path, open_window, monkeypatch):  # noqa: F811
    from qtpy.QtWidgets import QMessageBox

    from cellquant.gui import guide

    shell = _shell(tmp_path, open_window)
    setup, controller = shell._marker_setup, shell.controller
    for box in setup.boxes[2:]:
        box.setChecked(False)
    setup.button.click()
    assert _wait(shell)
    _events(0.3)
    asked: list[str] = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda _parent, _title, text, *a, **k: asked.append(text) or QMessageBox.No))
    setup.rule.setCurrentIndex(setup.rule.findData(guide.RULE_PERCENT))
    setup.rule.activated.emit(setup.rule.currentIndex())
    setup._auto_apply()
    assert asked and "Changing the rule starts the cutoffs" in asked[-1]
    assert not any(item.measurement.endswith("_pct") for item in controller.recipe.classifications)  # No: unchanged
    assert setup.rule.currentData() == guide.RULE_MEAN  # and the choice went back
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    setup.rule.setCurrentIndex(setup.rule.findData(guide.RULE_PERCENT))
    setup.rule.activated.emit(setup.rule.currentIndex())
    setup._auto_apply()
    assert _wait(shell)
    _events(0.3)
    assert all(item.comparison == "at_least" for item in controller.recipe.classifications)
    assert shell._tabs.currentWidget() is not shell._step_pages[4]


def test_unticking_every_channel_puts_the_ticks_back(tmp_path: Path, open_window):  # noqa: F811
    shell = _shell(tmp_path, open_window)
    setup, controller = shell._marker_setup, shell.controller
    setup.button.click()
    assert _wait(shell)
    _events(0.3)
    names = [item.name for item in controller.recipe.classifications]
    assert names
    for box in setup.boxes:
        box.blockSignals(True)
        box.setChecked(False)
        box.blockSignals(False)
    setup._auto_apply()
    assert [item.name for item in controller.recipe.classifications] == names  # nothing removed
    assert any(box.isChecked() for box in setup.boxes)  # the boxes match the markers again
