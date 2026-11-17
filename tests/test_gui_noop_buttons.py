"""Buttons that would do nothing are off, with the reason in their tooltip; the rest say so when clicked.

Needs napari and Qt; skipped without napari.
"""

from __future__ import annotations

from pathlib import Path

import pytest

napari = pytest.importorskip("napari")

from tests.gui_support import _texture_sizes  # noqa: E402,F401
from tests.test_gui_crop import _events, _wait, open_window  # noqa: E402,F401


def _practice(tmp_path: Path, open_window):  # noqa: F811
    from cellquant.practice import create_practice_experiment

    controller = create_practice_experiment(tmp_path / "practice")
    controller.save()
    return open_window(controller.directory)


def _say(shell) -> str:
    return shell._footer.status.text()


def test_with_no_experiment_the_buttons_that_need_one_are_off_and_say_why(open_window):  # noqa: F811
    shell = open_window(None)
    panel = shell._experiment_panel
    for button in (panel.add_images_button, panel.add_folder_button, panel.add_column_button, panel.run_selected_button):
        assert not button.isEnabled() and "Open an experiment first" in button.toolTip(), button.text()
    previous, following = shell._footer._navigation
    assert not previous.isEnabled() and not following.isEnabled() and "No included images" in previous.toolTip()
    assert not shell._analysis_bar.plan_button.isEnabled() and "Open an experiment first" in shell._analysis_bar.plan_button.toolTip()
    plan = shell._plan_dock
    for button in (plan.tick, plan.untick, plan.set_channel):
        assert not button.isEnabled() and "Open an experiment first" in button.toolTip()
    # Load masks… and the rename button have their own rules: masks can start an experiment.
    assert panel.findChildren(type(panel.add_images_button))  # sanity: the panel is built


def test_opening_an_experiment_turns_them_on_with_their_usual_tooltips(tmp_path: Path, open_window):  # noqa: F811
    shell = _practice(tmp_path, open_window)
    panel = shell._experiment_panel
    for button in (panel.add_images_button, panel.add_folder_button, panel.add_column_button):
        assert button.isEnabled() and "Open an experiment first" not in button.toolTip()
    assert shell._analysis_bar.plan_button.isEnabled() and "Open the Plan" in shell._analysis_bar.plan_button.toolTip()

    # Run selected images follows the selection in the table.
    run = panel.run_selected_button
    assert not run.isEnabled() and "Select images" in run.toolTip()
    panel.table.selectRow(0)
    assert run.isEnabled() and "Run the images selected" in run.toolTip()
    panel.table.clearSelection()
    assert not run.isEnabled()

    # Previous / Next follow the position: the first image has no previous one, the last no next one.
    previous, following = shell._footer._navigation
    total = len(shell._nav_ids)
    assert total >= 2
    assert not previous.isEnabled() and "first image" in previous.toolTip()
    assert following.isEnabled() and "next image" in following.toolTip()
    shell._footer._step(1)
    assert _wait(shell, 60)
    assert previous.isEnabled() and previous.toolTip().startswith("Show the previous image in the experiment")
    while shell._nav_index < total - 1:
        shell._footer._step(1)
        assert _wait(shell, 60)
    assert not following.isEnabled() and "last image" in following.toolTip()


def test_plan_buttons_follow_the_scope_and_the_selection(tmp_path: Path, open_window):  # noqa: F811
    shell = _practice(tmp_path, open_window)
    shell.show_plan()
    plan = shell._plan_dock
    plan.scope.setCurrentIndex(plan.scope.findData("selected"))
    plan.tree.clearSelection()
    assert not plan.tick.isEnabled() and "Select images" in plan.tick.toolTip()
    plan.tree.setCurrentItem(plan.tree.topLevelItem(0).child(0) if plan.tree.topLevelItem(0).childCount() else plan.tree.topLevelItem(0))
    assert plan.selected_image_ids() and plan.tick.isEnabled() and plan.untick.isEnabled() and plan.set_channel.isEnabled()
    plan.tree.clearSelection()
    plan.scope.setCurrentIndex(plan.scope.findData("all"))
    assert plan.tick.isEnabled()


def test_undo_is_off_until_there_is_an_edit_and_the_others_say_when_there_was_nothing(tmp_path: Path, open_window):  # noqa: F811
    shell = _practice(tmp_path, open_window)
    shell.run_current()
    assert _wait(shell, 120)
    edit = shell._edit_panel
    assert not edit.undo_button.isEnabled() and "No recorded edit" in edit.undo_button.toolTip()

    # What was painted in napari is not known until the button is clicked: these stay on and answer.
    record, say = edit._buttons["Record drawn edits"], edit._buttons["Undo all"]
    assert record.isEnabled() and say.isEnabled() and edit._buttons["Record all images"].isEnabled()
    for button, words in (
        (record, "Nothing drawn to record"),
        (edit._buttons["Record all images"], "Nothing drawn to record"),
        (say, "Nothing to undo"),
    ):
        shell.message("")
        button.click()
        _events(0.2)
        assert words in _say(shell), (button.text(), _say(shell))

    # An edit turns Undo on; undoing it turns it off again.
    image_id = shell._nav_ids[shell._nav_index]
    result = shell.controller.recall(image_id)
    victim = int(result.labels.max())
    edit.delete_object(victim)
    assert _wait(shell, 60)
    assert edit.undo_button.isEnabled() and edit.undo_button.toolTip().startswith("Undo the last edit")
    edit.undo_button.click()
    assert _wait(shell, 60)
    assert not edit.undo_button.isEnabled()


def test_reset_cutoff_says_when_it_is_already_automatic(tmp_path: Path, open_window):  # noqa: F811
    shell = _practice(tmp_path, open_window)
    shell.run_current()
    assert _wait(shell, 120)
    shell._marker_setup.button.click()
    assert _wait(shell, 120)
    panel = shell._review_panel
    panel.display.setCurrentIndex(1)
    panel._display_changed()
    _events(0.3)
    panel.reset_cutoff.click()  # a fresh cutoff is the automatic one already
    _events(0.3)
    assert "already its automatic value" in _say(shell) and "→" not in _say(shell)
