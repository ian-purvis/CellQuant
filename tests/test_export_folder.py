"""The dated subfolder an export is written into (no Qt needed)."""

from __future__ import annotations

from datetime import datetime

from cellquant.export_folder import export_folder_name, filename_safe, new_export_folder

WHEN = datetime(2026, 3, 9, 14, 5)


def test_one_analysis_folder_name_is_dated_and_filename_safe():
    assert export_folder_name("OTX2 / VSX2: nuclei", WHEN) == "2026-03-09_1405_OTX2_VSX2_nuclei"
    assert export_folder_name("Analysis 1", WHEN) == "2026-03-09_1405_Analysis_1"


def test_all_analyses_folder_name():
    assert export_folder_name(None, WHEN) == "2026-03-09_1405_all_analyses"


def test_a_name_with_nothing_usable_still_gives_a_name():
    assert filename_safe("???") == "analysis"
    assert export_folder_name("???", WHEN) == "2026-03-09_1405_analysis"


def test_new_folder_is_inside_the_chosen_folder_and_not_created(tmp_path):
    folder = new_export_folder(tmp_path, "Run A", WHEN)
    assert folder == tmp_path / "2026-03-09_1405_Run_A"
    assert not folder.exists()


def test_a_taken_name_gets_a_number(tmp_path):
    first = new_export_folder(tmp_path, "Run A", WHEN)
    first.mkdir()
    second = new_export_folder(tmp_path, "Run A", WHEN)
    assert second.name == "2026-03-09_1405_Run_A_2"
    second.mkdir()
    assert new_export_folder(tmp_path, "Run A", WHEN).name == "2026-03-09_1405_Run_A_3"
    allfolder = new_export_folder(tmp_path, None, WHEN)
    allfolder.mkdir()
    assert new_export_folder(tmp_path, None, WHEN).name == "2026-03-09_1405_all_analyses_2"


def _names(files):
    return [name for name, _when in files]


def test_files_listed_without_choices_follow_the_default_unit():
    from cellquant.export_folder import export_files

    plain = _names(export_files(None, None, ["Folder 1", "Folder 2"]))
    assert plain == ["objects.csv", "image_summary.csv", "settings_index.csv", "recipe.yaml", "run.json", "mixed_settings.txt", "units_by_Folder 2.csv"]
    assert "units_by_Folder 2.csv" not in _names(export_files(None, None, ["Condition"]))  # no folder level: no automatic unit


def test_grouping_and_unit_add_their_files():
    from cellquant.export_folder import export_files

    files = _names(export_files("Folder 1", "Folder 2", ["Folder 1", "Folder 2"]))
    assert "grouped_by_Folder 1.csv" in files and "units_by_Folder 2.csv" in files and "grouped_by_Folder 1_equal_units.csv" in files
    # A group that is also the unit has no equal-units table.
    assert "grouped_by_Folder 2_equal_units.csv" not in _names(export_files("Folder 2", "Folder 2", ["Folder 1", "Folder 2"]))
    # A unit that is not a column of this experiment writes nothing for it.
    assert not [name for name in _names(export_files(None, "Retina", ["Folder 1"])) if name.startswith("units_by_")]


def test_export_all_analyses_lists_the_side_by_side_files_and_one_folder_each():
    from cellquant.export_folder import export_files

    files = _names(export_files("Folder 1", None, ["Folder 1", "Folder 2"], all_analyses=True))
    assert files[:3] == ["all_analyses_image_summary.csv", "all_analyses_grouped_by_Folder 1.csv", "analyses_not_exported.txt"]
    assert "<analysis name>/objects.csv" in files and "<analysis name>/grouped_by_Folder 1.csv" in files
