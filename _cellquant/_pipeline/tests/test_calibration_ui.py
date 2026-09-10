"""Real Qt calibration review on tiny synthetic fluorescence, never dataset tuning."""
from concurrent.futures import Future
import numpy as np
import pytest
from qtpy.QtWidgets import QApplication
from test_coexpression_ui import panel, complete
from cellquant.classify.store import reopen_classification


def launch(panel, *, incomplete=False):
    if incomplete:
        panel.add_marker()  # An unconfigured unrelated marker cannot block calibration.
    panel.markers.setCurrentCell(0, 0)
    panel.calibrate_marker()
    cal = panel.calibration_panel
    cal.load_source(); complete(cal)
    assert cal.analysis is not None, cal.status.text()
    return cal


def review(cal):
    cal.suggest(); complete(cal)
    assert cal.proposal is not None, cal.status.text()
    cal.preview(); complete(cal)
    assert cal.review is not None, cal.status.text()
    assert cal.accept_button.isEnabled()
    assert cal.layers[-1].editable is False


def test_calibration_first_marker_incomplete_others_accept_save_reopen(panel, tmp_path):
    cal = launch(panel, incomplete=True)
    review(cal)
    cal.accept_review()
    assert panel._calibrations[0]['review']['status'] == 'current'
    assert panel._calibrations[0]['proposal']['method'] == 'otsu'
    panel.remove_marker()
    panel.preview(tmp_path); complete(panel)
    assert panel.result is not None, panel.status.text()
    run = next(tmp_path.glob('classify_*'))
    saved = reopen_classification(run)
    assert saved.recipe.raw['markers'][0]['calibration']['method'] == 'otsu'
    panel.reopen(run); complete(panel)
    assert panel._calibrations[0]['review']['status'] == 'historical'
    panel.preview(); complete(panel)
    assert panel.result is not None


def test_viewer_example_selection_and_manual_review(panel):
    cal = launch(panel)
    panel.labels.currentData().selected_label = 1
    cal.mark_example(True)
    assert cal.positive_ids == {1}
    cal.fields['low'].setText('1000')
    cal.preview(); complete(cal)
    assert cal.review[0].disagreement.tolist() == [True]
    cal.accept_review()
    assert panel._calibrations[0]['method'] == 'manual'
    assert panel._calibrations[0]['positive_ids'] == [1]


def test_only_active_panel_accepts_and_mapping_changes_require_reopen(panel):
    old = launch(panel); review(old)
    panel.calibrate_marker()
    current = panel.calibration_panel
    assert current is not old and old.closed
    with pytest.raises(ValueError, match='closed'): old.accept_review()
    current.load_source(); complete(current); review(current)
    panel._marker_channel_combo(0).setCurrentIndex(2)
    assert current.analysis is None and not current.accept_button.isEnabled()
    current.load_source(); complete(current); review(current)
    with pytest.raises(ValueError, match='row changed'): current.accept_review()
    assert 0 not in panel._calibrations


def test_mutated_source_metadata_invalidates_preview_and_accept(panel):
    cal = launch(panel); review(cal)
    panel.image.currentData().metadata['nested'] = {'a': 1}
    with pytest.raises(ValueError, match='Preview current'): cal.accept_review()
    assert cal.analysis is None and not cal.accept_button.isEnabled()
    cal.load_source(); complete(cal); review(cal)
    panel.image.currentData().metadata['nested']['a'] = 2
    cal.poll()
    assert cal.analysis is None and cal.review is None
    assert not cal.layers[-1].visible


def test_late_calibration_result_paint_cancel_error(panel):
    cal = launch(panel)
    previous = cal.analysis
    future = Future()
    cal.future = future
    cal._job_revision = cal.revision
    cal._completed = cal.source_ready
    panel.labels.currentData().paint((0, 1, 1), 2)
    future.set_result(previous); cal.poll()
    assert cal.analysis is None
    cal.load_source(); complete(cal)
    cal.start(lambda cancel: None, lambda _: pytest.fail('cancelled result published'))
    cal.cancel(); complete(cal)
    assert 'Cancelled' in cal.status.text()
    cal.start(lambda cancel: (_ for _ in ()).throw(ValueError('synthetic calibration error')), lambda _: None)
    complete(cal)
    assert 'synthetic calibration error' in cal.status.text()


def test_removing_row_drops_its_evidence(panel):
    panel.add_marker()
    panel._calibrations[1] = {'review': {'status': 'current'}}
    panel.remove_marker()
    panel.add_marker()
    assert 1 not in panel._calibrations


@pytest.mark.parametrize('remove', [False, True])
def test_closing_dock_cancels_review_and_reopens_fresh(panel, remove):
    cal = launch(panel); review(cal)
    if remove:
        panel.viewer.window.remove_dock_widget(cal.dock)
    else:
        cal.dock.close()
    QApplication.processEvents()
    assert cal.closed and not cal.timer.isActive()
    assert not cal.accept_button.isEnabled()
    assert all(not layer.visible for layer in cal.layers)
    with pytest.raises(ValueError, match='closed'):
        cal.accept_review()
    panel.calibrate_marker()
    assert panel.calibration_panel is not cal
    assert not panel.calibration_panel.closed
    assert panel.calibration_panel.review is None


def test_calibration_screenshot(panel, tmp_path):
    cal = launch(panel); review(cal)
    # Size the real dock: resizing its child while the viewer is hidden is
    # overridden by QDockWidget and captures an artificially clipped panel.
    cal.dock.setFloating(True)
    cal.dock.resize(900, 1000)
    cal.dock.show()
    QApplication.processEvents()
    cal.canvas.draw()
    path = tmp_path / 'calibration-synthetic-review.png'
    assert cal.width() >= 750
    assert cal.dock.grab().save(str(path))
    assert path.stat().st_size > 10000
    from qtpy.QtWidgets import QScrollArea
    scroll = cal.findChild(QScrollArea)
    scroll.ensureWidgetVisible(cal.accept_button)
    QApplication.processEvents()
    assert cal.dock.grab().save(str(tmp_path / 'calibration-reviewed-settings.png'))
    panel.viewer.window._qt_window.resize(1366, 900)
    panel.viewer.show()
    cal.dock.setFloating(False)
    QApplication.processEvents()
    scroll.ensureWidgetVisible(cal.accept_button)
    QApplication.processEvents()
    assert cal.width() >= 520
    assert scroll.viewport().rect().intersects(cal.accept_button.rect().translated(cal.accept_button.mapTo(scroll.viewport(), cal.accept_button.rect().topLeft())))
    assert cal.dock.grab().save(str(tmp_path / 'calibration-docked-settings.png'))


def test_marking_example_keeps_queue_navigation_and_disables_accept(panel):
    cal = launch(panel)
    review(cal)
    first = int(cal.review[0].iloc[cal._filtered_rows[cal._queue_index]]["label"])
    assert cal.accept_button.isEnabled()
    panel.labels.currentData().selected_label = first
    cal.mark_example(True)
    assert not cal.accept_button.isEnabled()
    assert cal.review is not None
    assert cal.layers[-1].visible
    cal.next_cell()
    nxt = int(cal.review[0].iloc[cal._filtered_rows[cal._queue_index]]["label"])
    assert panel.labels.currentData().selected_label == nxt
    assert cal.layers[0].visible
