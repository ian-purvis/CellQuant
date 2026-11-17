"""Units in QuantAnalysis outputs: results sheets, copied and exported results and the methods sentence state the unit of the values that are
in it; the estimation plot's difference axis names it; measure labels never show internal column ids; one measure imported at two dot levels
gives two tables with different titles. Needs Qt (xvfb-run on Linux) and matplotlib."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("matplotlib")

from cellquant.stats import ColumnTable, sources  # noqa: E402
from cellquant.stats.units import unit_of  # noqa: E402
from tests.test_stats_import import make_results  # noqa: E402

A = [48.0, 50.5, 52.1, 49.9, 51.3, 47.2]
B = [40.1, 41.7, 39.5, 42.2, 38.8, 40.9]
WELCH = {"test": "welch_t", "first": "Control", "second": "Treated", "alternative": "two-sided", "alpha": 0.05, "ci": 0.95, "style": "classic"}


@pytest.fixture
def window(qtbot):
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    window = QuantAnalysisWindow()
    qtbot.addWidget(window)
    window.show()
    return window


def _sheet(window, title: str, names=("Control", "Treated")):
    from cellquant.gui.quantanalysis.project import DataSheet

    data = window.project.add_data(DataSheet("Data", ColumnTable.from_columns(dict(zip(names, (A, B))), title=title)))
    window.select(data.id)
    return data


def _rows(sheet) -> dict[str, str]:
    from cellquant.gui.quantanalysis.results_view import result_rows

    return {label: values[0] for label, values in result_rows(sheet)[1] if values}


def test_the_unit_is_read_from_a_table_title():
    assert unit_of("Area in µm² per object") == "µm²" and unit_of("Count area in px") == "px"
    assert unit_of("OTX2 mean intensity (a.u.) per image") == "a.u."
    assert unit_of("% OTX2+ of all objects per image") == "%"
    assert unit_of("Objects counted per image") == "" and unit_of("Cells per field") == ""


def test_a_compare_sheet_states_the_unit_of_means_and_differences_only(window):
    from cellquant.gui.quantanalysis.results_view import result_frame, result_text

    _sheet(window, "Area in µm² per object")
    sheet = window.analyze("compare", WELCH)
    rows = _rows(sheet)
    assert rows["Unit"].startswith("µm²")
    assert rows["Mean of Control"].endswith(" µm²") and rows["Mean of Treated"].endswith(" µm²")
    assert rows["Difference between means (Treated - Control) ± SEM"].endswith(" µm²")
    assert rows["95% confidence interval"].endswith(" µm²")
    for unitless in ("P value", "t, df", "R squared (eta squared)", "Cohen's d", "Hedges' g", "Sample size, Control"):
        assert "µm²" not in rows[unitless], unitless
    assert "Values are in µm²." in sheet.methods
    frame = result_frame(sheet)  # exported and copied results are the same text
    assert frame.loc[frame["label"] == "Unit", "formatted"].iloc[0].startswith("µm²")
    assert frame.loc[frame["label"] == "Mean of Control", "formatted"].iloc[0] == rows["Mean of Control"]
    assert "Mean of Control\t" + rows["Mean of Control"] in result_text(sheet) and "Values are in µm²." in result_text(sheet)
    assert sheet.result.row("How big is the difference?", "Mean of Control").formatted.count("µm²") == 0  # the engine's own result is unchanged


def test_rank_tests_and_anova_state_the_unit_too(window):
    from cellquant.gui.quantanalysis.project import DataSheet

    _sheet(window, "Area in µm²")
    assert _rows(window.analyze("compare", {**WELCH, "test": "mann_whitney"}))["Median of Control"].endswith(" µm²")
    three = window.project.add_data(DataSheet("Three", ColumnTable.from_columns({"A": A, "B": B, "C": A}, title="Area in µm²")))
    window.select(three.id)
    options = {"test": "ordinary", "comparisons": "all", "alpha": 0.05, "ci": 0.95, "style": "classic"}
    rows = _rows(window.analyze("anova", options))
    assert rows["Unit"].startswith("µm²") and "SD =" in rows["A"] and rows["A"].count("µm²") == 3
    assert any(text.startswith("Mean Diff.") and text.count("µm²") == 2 for text in rows.values())
    kruskal = _rows(window.analyze("anova", {**options, "test": "kruskal_wallis"}))
    assert not any("rank diff" in text and "µm²" in text for text in kruskal.values())


def test_column_statistics_label_the_statistics_that_are_in_the_unit(window):
    _sheet(window, "AF647 mean intensity (a.u.) per image")
    sheet = window.analyze("colstats")
    rows = _rows(sheet)
    assert "Mean (a.u.)" in rows and "Std. Deviation (a.u.)" in rows and "Number of values" in rows and "Coefficient of variation" in rows
    assert "Skewness" in rows and "Skewness (a.u.)" not in rows


def test_no_unit_row_without_a_unit_or_after_a_transform(window):
    _sheet(window, "Cells per field")
    sheet = window.analyze("compare", WELCH)
    assert "Unit" not in _rows(sheet) and "Values are in" not in sheet.methods
    data = _sheet(window, "Area in µm²")
    processed = window.project.add_processed(data, {"kind": "transform", "function": "log10"})  # no longer in µm²
    window.select(processed.id)
    sheet = window.analyze("compare", WELCH)
    assert "Unit" not in _rows(sheet) and "Values are in" not in sheet.methods


def test_the_estimation_difference_axis_names_the_unit(window):
    data = _sheet(window, "Area in µm² per object")
    window.analyze("compare", WELCH)
    window.select(data.id)
    view = window.new_graph("estimation") and window.stack.currentWidget()
    view.draw()
    labels = [child.get_ylabel() for child in view.figure.axes[0].child_axes]
    assert any(label.startswith("Difference (Treated − Control) in µm²") and "95% CI" in label for label in labels), labels
    plain = _sheet(window, "Cells per field", names=("Control", "Treated"))
    window.select(plain.id)
    window.analyze("compare", WELCH)
    window.select(plain.id)
    window.new_graph("estimation")
    other = window.stack.currentWidget()
    other.draw()
    assert any(child.get_ylabel().startswith("Difference (Treated − Control)\n") for child in other.figure.axes[0].child_axes)


def _source() -> sources.Source:
    images = pd.DataFrame({"image_id": ["a", "b"], "sample_name": ["a", "b"], "total_objects": [10, 12], "count_area": [5.0, 6.0],
                           "count_area_unit": ["um2", "um2"], "Folder 1": ["x", "y"]})
    objects = pd.DataFrame({"image_id": ["a", "a", "b"], "object_id": [1, 2, 1], "area": [1.0, 2.0, 3.0], "area_unit": ["um2"] * 3,
                            "mk_mean": [10.0, 11.0, 12.0], "mk_pos": [True, False, True], "nuc_area": [1.0, 2.0, 3.0],
                            "bright": [1.0, 2.0, 3.0]})
    return sources.Source(images, objects, {"mk_pos": "Marker"}, {"mk_mean": "Marker"}, {"nuc_area": "area", "bright": "mean"}, {"bright": "Brightness"})


def test_measure_labels_never_show_internal_column_ids():
    source = _source()
    labels = {item.key: item.label for item in source.measures()}
    assert labels["total_objects"] == "Objects counted"
    assert labels["count_area"] == "Count area in µm²"
    assert labels["mk_mean"] == "Marker mean intensity (a.u.) per object"
    assert labels["bright"] == "Brightness (a.u.) per object"  # the name the user gave it
    assert labels["nuc_area"] == "Nuc area in µm² per object"
    for key, label in labels.items():
        assert key not in label or key == "area", (key, label)
    assert "_" not in " ".join(labels.values())


def test_the_keys_stay_the_ids_so_saved_specs_still_match():
    source = _source()
    assert source.measure("nuc_area").key == "nuc_area" and source.measure("total_objects").label == "Objects counted"
    imported = sources.build(source, sources.ImportSpec("nuc_area", dot="object", group_by="Folder 1"))
    assert imported.table.title == "Nuc area in µm² per object"


def test_a_measure_imported_at_two_dot_levels_gets_two_titles(window, tmp_path):
    folder = make_results(tmp_path / "results")
    first = window.open_folder(folder, sources.ImportSpec("area", dot="object", group_by="Folder 1"), accept=True)
    second = window.open_folder(folder, sources.ImportSpec("area", dot="image", group_by="Folder 1"), accept=True)
    third = window.open_folder(folder, sources.ImportSpec("area", dot="unit", unit="Folder 2", group_by="Folder 1"), accept=True)
    titles = [first.table.title, second.table.title, third.table.title]
    assert titles == ["Area per object", "Area per image", "Area per replicate"]
    assert len({first.name, second.name, third.name}) == 3 and not any("(2)" in name for name in (first.name, second.name, third.name))
