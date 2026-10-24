"""A change made in one panel shows at once in every other panel that shows the same thing, without
reopening the experiment. Needs napari and Qt (xvfb-run on Linux); skipped without napari."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

napari = pytest.importorskip("napari")

from tests.test_gui_plan import _settle, _wait  # noqa: E402


def _open(tmp_path: Path, run: int | None = None):
    """The plan test experiment, its first ``run`` images run (all when None), open in a window."""

    from cellquant.gui.app import CellQuantWindow
    from tests.test_plan import _experiment

    controller = _experiment(tmp_path)
    records = controller.experiment.images
    for record in records[: len(records) if run is None else run]:
        controller.run_image(record.image_id)
    controller.save()
    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"napari viewer could not start: {exc}")
    shell = CellQuantWindow(viewer, controller.directory)
    _wait(shell)
    return viewer, shell


def _column(table, name: str) -> int:
    return next(c for c in range(table.columnCount()) if table.horizontalHeaderItem(c).text() == name)


def _show_marker(shell) -> None:
    shell._review_panel.display.setCurrentIndex(1)
    _settle()


def _strip_names(panel) -> list[str]:
    return [panel.strip_table.item(row, 0).text() for row in range(panel.strip_table.rowCount())]


def _texts(box) -> list[str]:
    return [box.itemText(index) for index in range(box.count())]


def test_unticked_image_leaves_step_5_strip_and_counts(tmp_path: Path):
    from qtpy.QtCore import Qt

    viewer, shell = _open(tmp_path)
    try:
        _show_marker(shell)
        review, table = shell._review_panel, shell._experiment_panel.table
        total = len(_strip_names(review))
        assert f"of {total} approved" in review.status_line.text()
        table.item(total - 1, _column(table, "Include")).setCheckState(Qt.Unchecked)
        _settle()
        assert len(_strip_names(review)) == total - 1
        assert f"of {total - 1} approved" in review.status_line.text()
        assert table.item(total - 1, _column(table, "Status")).text() == "Left out"
    finally:
        viewer.close()


def test_sample_name_reaches_step_5_strip(tmp_path: Path):
    viewer, shell = _open(tmp_path)
    try:
        _show_marker(shell)
        table = shell._experiment_panel.table
        table.item(1, _column(table, "Sample name")).setText("Renamed retina")
        _settle()
        assert "Renamed retina" in _strip_names(shell._review_panel)
    finally:
        viewer.close()


def test_step_3_edit_shows_lost_approval_in_step_1(tmp_path: Path):
    viewer, shell = _open(tmp_path)
    try:
        controller, table = shell.controller, shell._experiment_panel.table
        image_id = shell._nav_ids[shell._nav_index]
        controller.set_status(image_id, "approved")
        shell._experiment_panel.refresh_table()
        row = next(r for r in range(table.rowCount()) if table.item(r, 0).data(256) == image_id)
        assert table.item(row, _column(table, "Status")).text() == "Approved"
        labels = controller.recall(image_id).labels
        shell._edit_panel.delete_object(int(np.unique(labels[labels > 0])[0]))
        _wait(shell)
        assert controller.experiment.image(image_id).processing_status != "approved"
        assert table.item(row, _column(table, "Status")).text() != "Approved"
    finally:
        viewer.close()


def test_added_marker_reaches_step_5_and_6_lists(tmp_path: Path):
    viewer, shell = _open(tmp_path)
    try:
        markers = shell._measurements_panel
        markers.class_name.setText("Extra")
        markers._add_class()
        shell._panels_to_recipe()  # as the autosave does on changing step
        _settle()
        assert "Extra" in _texts(shell._review_panel.display)
        assert "Extra+" in _texts(shell._results_panel.report_count)
    finally:
        viewer.close()


def test_removed_marker_leaves_step_5(tmp_path: Path):
    viewer, shell = _open(tmp_path)
    try:
        _show_marker(shell)
        review, markers = shell._review_panel, shell._measurements_panel
        name = review.display.currentText()
        row = [markers.classes.item(r, 0).text() for r in range(markers.classes.rowCount())].index(name)
        markers.classes.setCurrentCell(row, 0)
        from qtpy.QtWidgets import QMessageBox

        original = QMessageBox.question
        QMessageBox.question = staticmethod(lambda *args, **kwargs: QMessageBox.Yes)
        try:
            markers._remove_class()
        finally:
            QMessageBox.question = original
        _settle()
        assert name not in _texts(review.display)
        assert not review.threshold_slider.isVisibleTo(review)
    finally:
        viewer.close()


def test_removed_marker_leaves_step_6_counts(tmp_path: Path):
    from qtpy.QtWidgets import QMessageBox

    viewer, shell = _open(tmp_path)
    try:
        markers, results = shell._measurements_panel, shell._results_panel
        name = markers.classes.item(0, 0).text()
        kinds = lambda: [results.phenotypes.item(r, 0).text() for r in range(results.phenotypes.rowCount())]  # noqa: E731
        assert any(name in kind for kind in kinds())
        markers.classes.setCurrentCell(0, 0)
        original = QMessageBox.question
        QMessageBox.question = staticmethod(lambda *args, **kwargs: QMessageBox.Yes)
        try:
            markers._remove_class()
        finally:
            QMessageBox.question = original
        _settle()
        assert kinds() and not any(name in kind for kind in kinds())
    finally:
        viewer.close()


def test_step_3_undo_shows_lost_approval_in_step_1(tmp_path: Path):
    viewer, shell = _open(tmp_path)
    try:
        controller, table = shell.controller, shell._experiment_panel.table
        image_id = shell._nav_ids[shell._nav_index]
        labels = controller.recall(image_id).labels
        shell._edit_panel.delete_object(int(np.unique(labels[labels > 0])[0]))
        _wait(shell)
        controller.set_status(image_id, "approved")
        shell._experiment_panel.refresh_table()
        row = next(r for r in range(table.rowCount()) if table.item(r, 0).data(256) == image_id)
        assert table.item(row, _column(table, "Status")).text() == "Approved"
        shell._edit_panel._undo()
        _wait(shell)
        assert controller.experiment.image(image_id).processing_status != "approved"
        assert table.item(row, _column(table, "Status")).text() != "Approved"
    finally:
        viewer.close()


def test_cutoff_typed_in_step_4_reaches_step_5(tmp_path: Path):
    viewer, shell = _open(tmp_path)
    try:
        _show_marker(shell)
        review, markers = shell._review_panel, shell._measurements_panel
        name = review.display.currentText()
        row = [markers.classes.item(r, 0).text() for r in range(markers.classes.rowCount())].index(name)
        wanted = review.threshold_slider.value() + 7.5
        markers.classes.item(row, 2).setText(str(wanted))
        shell._panels_to_recipe()
        _settle()
        assert review.threshold_slider.value() == pytest.approx(wanted)
    finally:
        viewer.close()


def test_pixel_size_reaches_step_2_size_hint(tmp_path: Path):
    viewer, shell = _open(tmp_path, run=0)
    try:
        panel, objects = shell._experiment_panel, shell._objects_panel
        before = objects.size_hint.text()
        panel._show_size_controls()
        panel.set_pixel_size(5.0, 5.0, 5.0)
        panel.size_scope.setCurrentIndex(panel.size_scope.findData("included"))
        panel._apply_pixel_size()
        _settle()
        assert objects.size_hint.text() != before
        shown = objects.size_hint.text()
        objects._update_size_hint()
        assert objects.size_hint.text() == shown
    finally:
        viewer.close()


def test_step_2_channel_names_the_default_channel(tmp_path: Path):
    viewer, shell = _open(tmp_path, run=0)
    try:
        objects = shell._objects_panel
        index = (objects.channel.currentIndex() + 1) % objects.channel.count()
        objects.channel.setCurrentIndex(index)
        _settle()
        name = objects.channel.currentText().split(" = ")[-1]
        assert f"Default ({name})" in objects.image_channel_counts.text()
    finally:
        viewer.close()


def test_image_not_run_clears_step_6(tmp_path: Path):
    viewer, shell = _open(tmp_path, run=1)
    try:
        assert shell._results_panel.phenotypes.rowCount()
        shell._nav_index = 1
        shell.show_current()
        _wait(shell)
        assert shell._results_panel.phenotypes.rowCount() == 0
        assert shell._results_summary.summary.text() == "No results yet for this image."
    finally:
        viewer.close()


def test_pasted_column_reaches_step_6_group_by(tmp_path: Path):
    viewer, shell = _open(tmp_path, run=0)
    try:
        panel = shell._experiment_panel
        before = list(shell.controller.experiment.metadata_columns)
        panel.paste_into(0, panel.table.columnCount() - 1, "x\ty")
        _settle()
        added = [name for name in shell.controller.experiment.metadata_columns if name not in before]
        assert added and added[0] in _texts(shell._results_panel.group_by)
    finally:
        viewer.close()


def test_pasted_sample_names_refresh_step_5_strip_once(tmp_path: Path):
    viewer, shell = _open(tmp_path)
    try:
        _show_marker(shell)
        panel, review = shell._experiment_panel, shell._review_panel
        calls = []
        original = review._show_strip
        review._show_strip = lambda: (calls.append(1), original())
        names = [f"Pasted {row}" for row in range(panel.table.rowCount())]
        panel.paste_into(0, _column(panel.table, "Sample name"), "\n".join(names))
        _settle()
        assert len(calls) == 1
        assert set(names) <= set(_strip_names(review))
    finally:
        viewer.close()


def test_marker_change_keeps_step_6_choices(tmp_path: Path):
    viewer, shell = _open(tmp_path)
    try:
        results, markers = shell._results_panel, shell._measurements_panel
        results.report_count.setCurrentIndex(1)
        results.report_among.setCurrentIndex(2)
        chosen = (results.report_count.currentData(), results.report_among.currentData())
        markers.classes.item(0, 2).setText(str(float(markers.classes.item(0, 2).text()) + 1))
        shell._panels_to_recipe()
        _settle()
        assert (results.report_count.currentData(), results.report_among.currentData()) == chosen
    finally:
        viewer.close()
