"""Tests for slow-run Continue/Cancel prompting helpers."""

from cellquant.plugin.slow_run import (
    SLOW_UNIT_SECONDS,
    faster_mode_hint,
    should_prompt_slow_run,
    slow_run_prompt_text,
)


def test_should_prompt_when_current_unit_exceeds_threshold():
    assert should_prompt_slow_run(
        already_prompted=False,
        elapsed_current_unit_s=SLOW_UNIT_SECONDS,
        last_finished_unit_s=None,
    )
    assert not should_prompt_slow_run(
        already_prompted=False,
        elapsed_current_unit_s=SLOW_UNIT_SECONDS - 1,
        last_finished_unit_s=None,
    )


def test_should_prompt_when_previous_unit_was_slow():
    assert should_prompt_slow_run(
        already_prompted=False,
        elapsed_current_unit_s=10.0,
        last_finished_unit_s=SLOW_UNIT_SECONDS,
    )


def test_should_not_prompt_twice():
    assert not should_prompt_slow_run(
        already_prompted=True,
        elapsed_current_unit_s=SLOW_UNIT_SECONDS * 2,
        last_finished_unit_s=SLOW_UNIT_SECONDS * 2,
    )


def test_prompt_text_mentions_continue_cancel_and_kill():
    text = slow_run_prompt_text(
        elapsed_current_unit_s=360.0,
        last_finished_unit_s=None,
        unit_label="Z plane",
        current_mode="stitch_2d",
    )
    assert "6 minutes" in text
    assert "continue" in text.casefold()
    assert "cancel" in text.casefold()
    assert "kill" in text.casefold()
    assert "Linked 2D" in text
    assert "Maximum projection" in text
    assert "change which objects are counted" in text


def test_max_projection_hint_does_not_suggest_max_projection():
    hint = faster_mode_hint("max_projection_2d")
    assert "Maximum projection" in hint  # names current mode
    assert "Faster options" in hint
    assert hint.count("Maximum projection") == 1
    assert "Single analysis plane" in hint


def test_already_fastest_mode_hint_does_not_suggest_other_modes():
    hint = faster_mode_hint("single_plane_2d")
    assert "already using Single analysis plane" in hint
    assert "Maximum projection" not in hint


def test_faster_mode_hint_warns_projection_modes_change_population():
    hint = faster_mode_hint("volume_3d")
    assert "Maximum projection" in hint
    assert "change which objects are counted" in hint

# Real Qt regression coverage: a worker can finish while the slow dialog's
# nested event loop is open. Never apply its old decision to another job.
import pytest


@pytest.mark.parametrize('decision', ['Continue', 'Cancel run', 'Kill now'])
@pytest.mark.parametrize('job_change', ['same', 'finished', 'replaced'])
def test_slow_dialog_decision_targets_only_original_busy_job(monkeypatch, decision, job_change):
    import napari
    from qtpy.QtWidgets import QApplication, QMessageBox
    from cellquant.contracts import MutableCancellationToken
    from cellquant.plugin import widget

    app = QApplication.instance() or QApplication([])
    viewer = napari.Viewer(show=False)
    root = widget.make_cellquant_widget(viewer)
    root.cellquant_timer.stop()
    controller = root.cellquant_controller
    controller._busy = True
    controller._job_started_monotonic = 1.0
    counts = {'prompts': 0, 'cancel': 0, 'kill': 0}
    monkeypatch.setattr(widget, 'should_prompt_slow_run', lambda **kw: not kw['already_prompted'])
    monkeypatch.setattr(controller, 'cancel', lambda: counts.__setitem__('cancel', counts['cancel'] + 1))
    monkeypatch.setattr(controller, 'kill', lambda: counts.__setitem__('kill', counts['kill'] + 1))

    def execute(box):
        counts['prompts'] += 1
        if job_change == 'finished':
            controller._busy = False
            controller.status_text = 'Complete with reviewed results'
        elif job_change == 'replaced':
            controller.cancel_token = MutableCancellationToken()
            controller.status_text = 'Replacement job running'
        next(button for button in box.buttons() if button.text() == decision).click()
        return 0

    monkeypatch.setattr(QMessageBox, 'exec_', execute)
    try:
        root.cellquant_timer.timeout.emit()
        assert counts['prompts'] == 1
        if job_change == 'same':
            assert counts['cancel'] == (decision == 'Cancel run')
            assert counts['kill'] == (decision == 'Kill now')
            root.cellquant_timer.timeout.emit()
            assert counts['prompts'] == 1
        else:
            assert counts['cancel'] == counts['kill'] == 0
            assert controller.status_text == ('Complete with reviewed results' if job_change == 'finished' else 'Replacement job running')
    finally:
        controller._busy = False
        root.close()
        viewer.close()
        app.processEvents()


def test_real_napari_dock_allows_vertical_expansion():
    import napari
    from qtpy.QtWidgets import QApplication, QSizePolicy
    from cellquant.plugin.widget import make_cellquant_widget

    app = QApplication.instance() or QApplication([])
    viewer = napari.Viewer(show=False)
    root = make_cellquant_widget(viewer)
    root.cellquant_timer.stop()
    try:
        viewer.window.add_dock_widget(root, name='CellQuant resize regression')
        app.processEvents()
        root._unlock_vertical_resize()
        assert root.sizePolicy().verticalPolicy() == QSizePolicy.Expanding
        assert root.cellquant_scroll.sizePolicy().verticalPolicy() == QSizePolicy.Expanding
        assert root.maximumHeight() == 16777215
        assert root.layout().stretch(0) == 1
        assert root.layout().stretch(1) == 0
    finally:
        root.close()
        viewer.close()
        app.processEvents()
