"""The panel's persistent chrome, the Start tab, step 1 and step 2: tab names and the lock note, one home for each
control, the shorter step 2, the New experiment dialog, the busy state, and the hidden features made visible.

Needs napari and Qt; skipped without napari.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

napari = pytest.importorskip("napari")

from tests.test_gui_crop import _events, _wait, open_window  # noqa: E402,F401
from tests.test_import_folders import _zstack  # noqa: E402


def _practice(tmp_path: Path):
    from cellquant.practice import create_practice_experiment

    return create_practice_experiment(tmp_path / "practice")


def _stack_experiment(tmp_path: Path):
    from cellquant.controller import AnalysisController

    _zstack(tmp_path / "images" / "stack.tif")
    controller = AnalysisController.create(tmp_path / "stack_experiment", "Stacks")
    controller.add_image_paths([tmp_path / "images"])
    controller.save()
    return controller


def _buttons(widget) -> dict[str, list]:
    from qtpy.QtWidgets import QPushButton

    found: dict[str, list] = {}
    for button in widget.findChildren(QPushButton):
        found.setdefault(button.text(), []).append(button)
    return found


def _app():
    from qtpy.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


# -- tabs ----------------------------------------------------------------------------------------------------


def test_tabs_show_number_and_name_and_the_lock_reason(tmp_path: Path, open_window):
    shell = open_window(_practice(tmp_path).directory)
    tabs = shell._tabs
    texts = [tabs.tabText(i) for i in range(tabs.count())]
    assert texts[0] == "Start"
    assert texts[1].startswith("1 Images") and texts[2].startswith("2 Find")
    assert texts[3].startswith("3 ") and texts[6].startswith("6 Results")  # locked tabs keep their name
    assert not tabs.isTabEnabled(3)
    assert "locked" in tabs.tabToolTip(3)  # the hover text stays
    assert tabs.tabToolTip(1) == "Images"
    # Under the strip, on every page, while a later tab is locked.
    assert "Steps 3-6 unlock after step 2: Run this image first." in shell._start_page.lock_note.text()
    for page in shell._step_pages:
        assert page.lock_note.isVisibleTo(page) and page.lock_note.text() == shell._start_page.lock_note.text()
    from cellquant.gui import guide

    assert guide.lock_note_text(5, "Finish step 4 first.") == "Step 6 unlocks after step 5: Finish step 4 first."


def test_lock_note_with_no_experiment_names_step_1(tmp_path: Path, open_window):
    shell = open_window(None)
    assert shell.controller is None
    assert shell._start_page.lock_note.text().startswith("Steps 2-6 unlock after step 1:")
    assert shell._tabs.tabText(1).startswith("1 Images")


# -- step 1 ---------------------------------------------------------------------------------------------------


def test_step_1_keeps_only_adding_controls_and_points_to_start(tmp_path: Path, open_window):
    shell = open_window(None)
    panel = shell._experiment_panel
    names = set(_buttons(panel))
    assert {"Add images…", "Add folder…", "Load masks…", "Add column…"} <= names
    assert not names & {"New experiment…", "Open experiment…", "Analyze a results folder…", "Load results…", "Open a results folder in QuantAnalysis…"}
    assert panel.no_experiment.isVisibleTo(panel) and "Start" in panel.no_experiment.text()
    # The one home for those actions is the Start tab.
    start = set(_buttons(shell._start_page))
    assert {"New experiment…", "Open experiment…", "Open a results folder in QuantAnalysis…", "Try practice images"} <= start
    assert "Analyze a results folder…" not in start
    # With an experiment open, the pointer goes away.
    shell.controller = _practice(tmp_path)
    shell._refresh_all()
    assert not panel.no_experiment.isVisibleTo(panel)


def test_add_column_button_adds_an_empty_column_through_the_paste_path(tmp_path: Path, open_window):
    from qtpy.QtCore import QTimer
    from qtpy.QtWidgets import QDialogButtonBox

    shell = open_window(_practice(tmp_path).directory)
    panel = shell._experiment_panel
    for bad in ("", "   ", "sample name", "Sample Name", "Image", "image_id", "QC_STATUS"):
        assert panel.column_name_problem(bad), bad
    assert not panel.column_name_problem("Genotype")

    def type_and_accept(text: str, expect_enabled: bool):
        def go():
            panel._column_field.setText(text)
            ok = panel._column_dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok)
            assert ok.isEnabled() is expect_enabled
            (panel._column_dialog.accept if expect_enabled else panel._column_dialog.reject)()

        QTimer.singleShot(50, go)
        panel._add_column()

    type_and_accept("Image", False)  # reserved: OK stays off
    assert "Image" not in shell.controller.experiment.metadata_columns
    type_and_accept("Genotype", True)
    assert shell.controller.experiment.metadata_columns == ["Genotype"]
    headers = [panel.table.horizontalHeaderItem(i).text() for i in range(panel.table.columnCount())]
    assert "Genotype" in headers
    assert all(record.user_metadata.get("Genotype") == "" for record in shell.controller.experiment.images)
    type_and_accept("genotype", False)  # a duplicate, whatever the case
    # Pasting past the last column still adds a column, by the same helper.
    last = panel.table.columnCount() - 1
    panel.paste_into(0, last, "Status text\tpasted")
    assert len(shell.controller.experiment.metadata_columns) == 2


# -- step 2 ---------------------------------------------------------------------------------------------------


def test_step_2_has_one_run_all_button_and_no_channel_tree(tmp_path: Path, open_window):
    shell = open_window(_practice(tmp_path).directory)
    panel = shell._objects_panel
    names = _buttons(panel)
    assert "Find objects in all images" in names and "Run all images" not in names
    assert "Preview" in names and "Run this image" in names
    assert not hasattr(panel, "image_tree") and not hasattr(panel, "image_channel_pick")
    assert "Default (" in panel.image_channel_counts.text()  # the count of images per channel stays
    calls = []
    shell.show_plan = lambda: calls.append("plan")
    names["Different channel for some images…"][0].click()
    assert calls == ["plan"]
    # Step 6 keeps its own button.
    assert "Run all images" in _buttons(shell._results_summary)


def test_step_2_shows_z_stack_controls_only_for_included_stacks(tmp_path: Path, open_window):
    flat = open_window(_practice(tmp_path).directory)
    panel = flat._objects_panel
    assert not panel.z_stack.isVisibleTo(panel) and not panel.z_recommend_box.isVisibleTo(panel)
    from tests.test_gui_step2_params import _shown

    shell = open_window(_stack_experiment(tmp_path).directory)
    panel = shell._objects_panel
    assert _shown(panel, "z_stack")
    # Leaving the only stack out hides them again; including it brings them back.
    from qtpy.QtCore import Qt

    table = shell._experiment_panel.table
    include = next(c for c in range(table.columnCount()) if table.horizontalHeaderItem(c).text() == "Include")
    table.item(0, include).setCheckState(Qt.Unchecked)
    _events(0.3)
    assert not _shown(panel, "z_stack") and not _shown(panel, "z_recommend_box")
    table.item(0, include).setCheckState(Qt.Checked)
    _events(0.3)
    assert _shown(panel, "z_stack")


def test_region_of_interest_is_collapsed_until_crop_is_on(tmp_path: Path, open_window):
    shell = open_window(_practice(tmp_path).directory)
    panel = shell._objects_panel
    assert panel.crop_section.toggle.text() == "Region of interest (optional)"
    assert not panel.crop_section.is_open() and not panel.crop.isVisibleTo(panel)
    panel.crop_section.set_open(True)
    assert panel.crop.isVisibleTo(panel) and not panel.crop_box.isVisibleTo(panel)
    panel.crop.setChecked(True)
    assert panel.crop_box.isVisibleTo(panel)
    shell._panels_to_recipe()
    # A saved recipe that crops opens the section by itself.
    panel.crop_section.set_open(False)
    panel.refresh()
    assert panel.crop_section.is_open() and panel.crop_box.isVisibleTo(panel)


# -- the analysis bar, shortcuts, more tools ------------------------------------------------------------------


def test_plan_button_and_menu_item_both_open_the_plan(tmp_path: Path, open_window):
    shell = open_window(_practice(tmp_path).directory)
    bar = shell._analysis_bar
    calls = []
    shell.show_plan = lambda: calls.append("plan")
    bar.plan_button.click()
    assert calls == ["plan"]
    assert bar.plan_button.text() == "Plan…"
    from qtpy.QtWidgets import QToolButton

    menu_actions = [action.text() for button in bar.findChildren(QToolButton) if button.menu() for action in button.menu().actions()]
    assert "Plan…" in menu_actions  # the menu item stays
    shell.show_plan = type(shell).show_plan.__get__(shell)
    bar.plan_button.click()
    assert not shell._plan_dock_widget.isHidden()


def test_keyboard_shortcuts_window_lists_only_bound_keys(tmp_path: Path, open_window):
    from cellquant.gui import app as gui_app

    shell = open_window(None)
    shell.show_shortcuts()
    window = shell._shortcuts_window
    assert not window.isModal() and not window.isHidden()
    listed = [window.table.item(row, 0).text() for row in range(window.table.rowCount())]
    assert listed == [key for key, _ in gui_app.SHORTCUTS]
    assert not any("Delete" in key or "Del" == key for key in listed)  # the comment about a Delete key is stale: nothing binds it
    source = Path(gui_app.__file__).read_text(encoding="utf-8")
    for key in ("PgUp", "PgDown", "A", "Ctrl+Z", "Esc"):
        assert f'"{key}"' in source, key
    window.close()
    shell.show_shortcuts()
    assert shell._shortcuts_window is window and not window.isHidden()  # reopened, not remade
    window.close()


def test_start_tab_more_tools_call_the_existing_actions(tmp_path: Path, open_window):
    shell = open_window(None)
    page = shell._start_page
    assert set(page.tool_buttons) == {"Plan…", "Measure…", "Output glossary…", "Keyboard shortcuts…"}
    assert not page.tool_buttons["Plan…"].isEnabled()  # needs an experiment
    shell.controller = _practice(tmp_path)
    shell._refresh_all()
    shell.refresh_guidance()  # the once-a-second timer does this
    assert page.tool_buttons["Plan…"].isEnabled() and page.tool_buttons["Measure…"].isEnabled()
    calls = []
    shell.show_plan = lambda: calls.append("plan")
    shell.show_measure = lambda: calls.append("measure")
    shell.open_output_glossary = lambda: calls.append("glossary")
    shell.show_shortcuts = lambda: calls.append("shortcuts")
    for text in page.tool_buttons:
        page.tool_buttons[text].click()
    assert calls == ["plan", "measure", "glossary", "shortcuts"]
    assert hasattr(shell, "_shortcuts_action")  # also in the Help menu


# -- busy state -----------------------------------------------------------------------------------------------


def test_busy_state_disables_every_listed_button_with_an_ellipsis(tmp_path: Path, open_window):
    """The names in _BUSY_BUTTONS lacked the "…" of the real buttons, so Add images / Add folder stayed usable."""

    shell = open_window(_practice(tmp_path).directory)
    names = ("Add images…", "Add folder…", "Load masks…", "Find objects in all images", "New experiment…", "Open experiment…", "Run this image", "Preview")
    real = [button for name in names for button in _buttons(shell._dock)[name]]
    assert len(real) >= len(names)
    assert all(button.isEnabled() for button in real)
    shell.set_busy(True)
    assert not any(button.isEnabled() for button in real), [b.text() for b in real if b.isEnabled()]
    shell.set_busy(False)
    assert all(button.isEnabled() for button in real)
    # Matching ignores a trailing "…" either way.
    assert shell._busy_key("Add images…") == shell._busy_key("Add images") == "Add images"


# -- the New experiment dialog --------------------------------------------------------------------------------


def _wait_scan(dialog, seconds: float = 20.0) -> None:
    end = time.time() + seconds
    while dialog.scanning() and time.time() < end:
        _events(0.05)
    assert not dialog.scanning()


def _images_folder(tmp_path: Path) -> Path:
    root = tmp_path / "Retina images"
    for relative in ("Control/a.nd2", "Control/b.ND2", "Treated/c.nd2", "Treated/d.tif", "e.tiff", "notes.txt"):
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_bytes(b"")
    return root


def test_scan_images_counts_by_folder_and_type(tmp_path: Path):
    from cellquant.gui.app import scan_images

    root = _images_folder(tmp_path)
    assert scan_images(root) == [
        (".", {"nd2": 0, "tiff": 1}),
        ("Control", {"nd2": 2, "tiff": 0}),
        ("Treated", {"nd2": 1, "tiff": 1}),
    ]
    assert scan_images(root, cancelled=lambda: True) is None


def test_suggested_results_folder_is_beside_never_inside_the_images(tmp_path: Path):
    from cellquant.gui.app import results_inside_images, suggested_results_folder

    images = tmp_path / "data" / "E14.5"
    suggestion = suggested_results_folder(images)
    assert suggestion == str(tmp_path / "data" / "E14.5 CellQuant results")
    assert not results_inside_images(images, suggestion)
    assert suggested_results_folder(Path(images.anchor)) == ""  # a drive root has no name to build from


def test_new_experiment_dialog_counts_images_and_suggests_a_results_folder(tmp_path: Path):
    from cellquant.gui.app import NewExperimentDialog

    _app()
    root = _images_folder(tmp_path)
    dialog = NewExperimentDialog()
    assert not dialog.ok_button.isEnabled()  # no results folder yet
    dialog.images.setText(str(root))
    assert dialog.scanning() and "Counting" in dialog.images_check.text()  # shown at once; the scan runs off the GUI thread
    _wait_scan(dialog)
    note = dialog.images_check.text()
    assert "Found <b>5 images</b> (3 ND2, 2 TIFF) in <b>Retina images</b> and its subfolders" in note
    assert dialog.results.text() == str(tmp_path / "Retina images CellQuant results")
    assert dialog.ok_button.isEnabled()
    # The type ticks change the count without a new scan.
    dialog.use_tiff.setChecked(False)
    assert "Found <b>3 images</b> (3 ND2)" in dialog.images_check.text() and "2 TIFF not ticked" in dialog.images_check.text()
    dialog.use_nd2.setChecked(False)
    assert not dialog.ok_button.isEnabled() and "Tick ND2 files" in dialog.types_check.text()
    dialog.use_nd2.setChecked(True)
    dialog.use_tiff.setChecked(True)
    assert dialog.ok_button.isEnabled() and not dialog.types_check.isVisibleTo(dialog)


def test_new_experiment_dialog_stops_suggesting_once_the_user_edits_the_results_folder(tmp_path: Path):
    from cellquant.gui.app import NewExperimentDialog

    _app()
    first, second = _images_folder(tmp_path), tmp_path / "Other images"
    second.mkdir()
    dialog = NewExperimentDialog()
    dialog.images.setText(str(first))
    _wait_scan(dialog)
    assert dialog.results.text().endswith("Retina images CellQuant results")
    dialog.images.setText(str(second))  # untouched: follows the image folder
    _wait_scan(dialog)
    assert dialog.results.text() == str(tmp_path / "Other images CellQuant results")
    dialog.results.textEdited.emit("")  # (the signal a keystroke sends)
    dialog.results.setText(str(tmp_path / "mine"))
    dialog.results.textEdited.emit(str(tmp_path / "mine"))
    dialog.images.setText(str(first))
    _wait_scan(dialog)
    assert dialog.results.text() == str(tmp_path / "mine")


def test_new_experiment_dialog_flags_a_bad_image_folder_inline_and_asks_nothing_else(tmp_path: Path, monkeypatch):
    from qtpy.QtWidgets import QMessageBox

    from cellquant.gui.app import NewExperimentDialog

    _app()
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: asked.append(args[1]) or QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: asked.append(args[1]))
    dialog = NewExperimentDialog()
    dialog.results.setText(str(tmp_path / "results"))
    assert dialog.ok_button.isEnabled()  # no image folder is allowed: images can be added later
    dialog.images.setText(str(tmp_path / "typo"))
    _wait_scan(dialog)
    assert "Not a folder" in dialog.images_check.text() and not dialog.ok_button.isEnabled()
    assert dialog.results.text() == str(tmp_path / "results")  # a results folder the user chose is left alone
    root = _images_folder(tmp_path)
    dialog.images.setText(str(root))
    _wait_scan(dialog)
    assert dialog.ok_button.isEnabled()
    dialog.results.setText(str(tmp_path / "fresh results"))
    dialog._accept()
    assert dialog.result() == dialog.DialogCode.Accepted
    assert asked == []  # no "Confirm image folder"; the count was already on screen
    # The two prompts that guard real data stay: results inside the image folder.
    inside = NewExperimentDialog()
    inside.images.setText(str(root))
    _wait_scan(inside)
    inside.results.setText(str(root / "results"))
    inside._accept()
    assert asked == ["Save results with the images?"]


def test_guide_says_where_the_nucleus_diameter_appears():
    """The diameter row is hidden for Cellpose-SAM without resizing; the guide must not tell users to set it unconditionally."""

    from cellquant.gui import guide

    todo = guide.STEPS[1].todo
    assert "appears only where it is used" in todo and "Resize to nucleus size" in todo
    assert "defaults to 6" not in todo
    assert "shown only when" in guide.HELP[("_objects_panel", "nucleus_diameter_um")]
    assert guide.STEPS[1].title == "Find the objects (nuclei or cells)"
