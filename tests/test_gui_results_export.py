"""Step 6: Group by and Unit sit in the visible Export box, with the list of files that will be written."""

from __future__ import annotations

from pathlib import Path

import pytest

napari = pytest.importorskip("napari")

from tests.test_gui_crop import _events, _wait, open_window  # noqa: E402,F401
from tests.test_workflow import _recipe, _write_squares  # noqa: E402


def test_export_box_lists_the_files_that_follow_the_choices(tmp_path: Path, open_window):  # noqa: F811
    from cellquant.controller import AnalysisController

    image = tmp_path / "a.tif"
    _write_squares(image)
    controller = AnalysisController.create(tmp_path / "experiment", "Export")
    controller.add_image_paths([image])
    controller.set_recipe(_recipe())
    if "Condition" not in controller.experiment.metadata_columns:
        controller.experiment.metadata_columns = [*controller.experiment.metadata_columns, "Condition"]
    controller.save()
    shell = open_window(controller.directory)
    shell.go_to_step(0)
    summary, panel = shell._results_summary, shell._results_panel
    shell._tabs.setCurrentWidget(shell._step_pages[5])
    shell._dock.resize(430, 900)
    shell._dock.show()
    _events(0.4)
    # Visible without ticking Advanced, in an Export group together with the export buttons.
    assert summary.export_box.title() == "Export" and summary.export_box.isVisibleTo(shell._step_pages[5])
    assert summary.export_box.isAncestorOf(panel.group_by) and summary.export_box.isAncestorOf(panel.unit)
    assert summary.export_box.isAncestorOf(summary.export_button) and summary.analyze.text() == "Open in QuantAnalysis…"
    text = summary.files_note.text()
    assert "Files that will be written" in text and "objects.csv" in text and "settings_index.csv" in text and "grouped_by_" not in text
    panel.group_by.setCurrentIndex(panel.group_by.findData("Condition"))
    assert "grouped_by_Condition.csv" in summary.files_note.text()
    panel.group_by.setCurrentIndex(0)
    assert "grouped_by_" not in summary.files_note.text()
    summary.show_analysis_actions(True)
    assert "all_analyses_image_summary.csv" in summary.files_note.text()
    summary.show_analysis_actions(False)
    assert "all_analyses_image_summary.csv" not in summary.files_note.text()


def test_exports_go_into_new_dated_folders_and_never_overwrite(tmp_path: Path, open_window, monkeypatch):  # noqa: F811
    import re

    from qtpy.QtWidgets import QFileDialog

    from cellquant.controller import AnalysisController

    image = tmp_path / "a.tif"
    _write_squares(image)
    controller = AnalysisController.create(tmp_path / "experiment", "Export folders")
    controller.add_image_paths([image])
    controller.set_recipe(_recipe())
    controller.save()
    shell = open_window(controller.directory)
    shell.run_current()
    assert _wait(shell, 120)
    chosen = tmp_path / "chosen"
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *args, **kwargs: str(chosen)))
    name = shell.controller.active_analysis().name.replace(" ", "_")

    shell.export_dialog()
    assert _wait(shell, 120)
    first = shell._exported_to
    assert first.parent == chosen and re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{4}_" + name, first.name)
    assert (first / "image_summary.csv").is_file() and not (chosen / "image_summary.csv").exists()
    assert str(first) in shell._results_summary.batch.text()  # the message and "Open export folder" name the new folder

    shell.export_dialog()  # the same minute: a second folder, the first one untouched
    assert _wait(shell, 120)
    second = shell._exported_to
    assert second != first and second.parent == chosen and second.name.startswith(first.name) and (first / "objects.csv").is_file()

    shell.export_all_dialog()
    assert _wait(shell, 120)
    everything = shell._exported_to
    assert everything.parent == chosen and re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{4}_all_analyses(_\d+)?", everything.name)
    assert (everything / "all_analyses_image_summary.csv").is_file()
