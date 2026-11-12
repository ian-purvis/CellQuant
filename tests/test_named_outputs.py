"""Exported tables name each result for what was counted, and a renamed channel is exported under its new name.

Saved runs number their result rows (``report_1_percent``). An export names them after the markers
(``Marker_A_pos_percent``); a marker is named after its channel, and follows the channel when the user
renames it in the viewer. The experiment's own settings, results and approvals never change with a name.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from cellquant.errors import CellQuantError
from cellquant.quantify import name_reports, rename_in_expression, report_columns, report_key, report_prefixes


NAMES = {"otx2_pos": "OTX2", "pax6_pos": "PAX6", "mch_pos": "mCherry", "a_pos": "Alexa 594"}


def test_a_report_is_named_for_what_it_counts():
    assert report_key("otx2_pos", "all_objects", NAMES) == "OTX2_pos"
    assert report_key("otx2_pos AND pax6_pos", "all_objects", NAMES) == "OTX2_pos_and_PAX6_pos"
    assert report_key("otx2_pos AND NOT pax6_pos", "all_objects", NAMES) == "OTX2_pos_and_PAX6_neg"
    assert report_key("otx2_pos", "mch_pos", NAMES) == "OTX2_pos_of_mCherry_pos"
    assert report_key("a_pos", "all_measured_objects", NAMES) == "Alexa_594_pos"  # nothing a table reader chokes on
    assert report_key("OTX2", "all_objects", NAMES) == "OTX2_pos"  # an expression may use the marker's name
    assert report_key("gone_pos", "all_objects", NAMES) == "gone_pos"  # a marker the settings no longer have
    assert report_key("all_objects", "mch_pos", NAMES) == "all_objects_of_mCherry_pos"


def _summary(**reports) -> pd.DataFrame:
    row: dict[str, object] = {"image_id": "a", "total_objects": 4, "Folder 1": "ctrl"}
    for prefix, (numerator, denominator) in reports.items():
        row |= {
            f"{prefix}_count": 1, f"{prefix}_percent": 25.0, f"{prefix}_denominator": 4,
            f"{prefix}_numerator": numerator, f"{prefix}_denominator_expression": denominator,
        }
    return pd.DataFrame([row])


def test_summary_columns_are_renamed_and_the_rest_is_kept():
    summary = _summary(report_1=("otx2_pos", "all_objects"), report_2=("otx2_pos", "mch_pos"))
    named = name_reports(summary, NAMES)
    assert list(named.columns) == [
        "image_id", "total_objects", "Folder 1",
        "OTX2_pos_count", "OTX2_pos_percent", "OTX2_pos_denominator", "OTX2_pos_numerator", "OTX2_pos_denominator_expression",
        "OTX2_pos_of_mCherry_pos_count", "OTX2_pos_of_mCherry_pos_percent", "OTX2_pos_of_mCherry_pos_denominator",
        "OTX2_pos_of_mCherry_pos_numerator", "OTX2_pos_of_mCherry_pos_denominator_expression",
    ]
    assert named.iloc[0].tolist() == summary.iloc[0].tolist()  # only the names changed
    assert report_prefixes(named.columns) == ["OTX2_pos", "OTX2_pos_of_mCherry_pos"]
    assert "Folder 1" not in report_columns(named.columns) and "OTX2_pos_percent" in report_columns(named.columns)
    assert list(name_reports(named, NAMES).columns) == list(named.columns)  # naming twice changes nothing


def test_two_reports_with_one_name_are_told_apart():
    summary = _summary(report_1=("otx2_pos", "all_objects"), report_2=("OTX2", "all_objects"))
    assert report_prefixes(name_reports(summary, NAMES).columns) == ["OTX2_pos", "OTX2_pos_2"]


def test_expressions_follow_renamed_ids():
    ids = {"af594_pos": "otx2_pos"}
    assert rename_in_expression("af594_pos AND NOT pax6_pos", ids) == "otx2_pos AND NOT pax6_pos"
    assert rename_in_expression("all_objects", ids) == "all_objects"
    assert rename_in_expression("AF594", {}, {"AF594": "OTX2"}) == "OTX2"
    assert rename_in_expression(float("nan"), ids) != rename_in_expression(float("nan"), ids)  # a blank stays blank


def _practice(tmp_path: Path, names=("Marker A", "Marker B")):
    from cellquant.controller import AnalysisController
    from cellquant.practice import PIXEL_SIZE_UM, write_practice_images
    from cellquant.quicksetup import marker_recipe

    controller = AnalysisController.create(tmp_path / "experiment", "Names")
    controller.add_image_paths(write_practice_images(tmp_path / "raw"))
    for record in controller.experiment.images:
        controller.set_pixel_size(record.image_id, PIXEL_SIZE_UM, PIXEL_SIZE_UM)
    controller.set_recipe(
        {"object_set": {"name": "Nuclei", "segmentation_channel": 0, "algorithm": "classical", "parameters": {"threshold_method": "otsu", "sigma": 1.0}}}
    )
    data = marker_recipe(controller.recipe.model_dump(mode="json"), [(1, names[0]), (2, names[1])])
    for item in data["classifications"]:
        item["threshold"] = 400.0
    controller.set_recipe(data)
    controller.run_images()
    return controller


def test_export_names_the_markers(tmp_path: Path):
    from cellquant.practice import expected_answers

    controller = _practice(tmp_path)
    out = controller.export(tmp_path / "export", group_by="Folder 1" if "Folder 1" in controller.experiment.metadata_columns else None)
    summary = pd.read_csv(out / "image_summary.csv")
    assert not [name for name in summary.columns if name.startswith("report_")]
    counts = summary[["Marker_A_pos_count", "Marker_B_pos_count", "Marker_A_pos_and_Marker_B_pos_count"]].to_numpy().tolist()
    assert counts == [[item.marker_a, item.marker_b, item.both] for item in expected_answers()]
    assert summary["Marker_A_pos_denominator"].tolist() == summary["total_objects"].tolist()
    assert summary["Marker_A_pos_numerator"].tolist() == ["marker_a_pos"] * 3  # the column of objects.csv it counts
    units = pd.read_csv(next(out.glob("units_by_*.csv"))) if list(out.glob("units_by_*.csv")) else None
    if units is not None:
        assert "Marker_A_pos_unit_pooled_percent" in units.columns
    # The saved run keeps its numbered columns: only what is exported is named.
    saved = controller.recall(controller.experiment.images[0].image_id).summary
    assert "report_1_percent" in saved.columns


def test_a_channel_renamed_after_marker_setup_is_exported_under_its_new_name(tmp_path: Path):
    from cellquant.recipe import load_recipe

    controller = _practice(tmp_path)
    first = controller.experiment.images[0].image_id
    controller.set_status(first, "approved")
    before = controller.recipe.content_hash()
    approved = {record.image_id: (record.processing_status, record.approved_settings_sha256) for record in controller.experiment.images}
    assert approved[first][0] == "approved" and approved[first][1]

    controller.rename_channel(1, "OTX2")

    assert controller.marker_names() == {"marker_a_pos": "OTX2", "marker_b_pos": "Marker B"}
    assert controller.export_ids() == {"marker_a_mean": "otx2_mean", "marker_a_pos": "otx2_pos"}
    # Nothing about the analysis changed: same settings hash, same ids, same approvals.
    assert controller.recipe.content_hash() == before
    assert [item.id for item in controller.recipe.classifications] == ["marker_a_pos", "marker_b_pos"]
    assert {record.image_id: (record.processing_status, record.approved_settings_sha256) for record in controller.experiment.images} == approved
    assert controller._approval_fingerprint(first) == approved[first][1]  # the approval still vouches for these settings

    out = controller.export(tmp_path / "export")
    summary = pd.read_csv(out / "image_summary.csv")
    assert {"OTX2_pos_count", "OTX2_pos_percent", "OTX2_pos_and_Marker_B_pos_percent", "Marker_B_pos_percent"} <= set(summary.columns)
    assert not [name for name in summary.columns if "Marker_A" in name or name.startswith("report_")]
    assert summary["OTX2_pos_numerator"].tolist() == ["otx2_pos"] * 3
    assert summary["OTX2_pos_and_Marker_B_pos_numerator"].tolist() == ["otx2_pos AND marker_b_pos"] * 3

    objects = pd.read_csv(out / "objects.csv")
    assert {"otx2_mean", "otx2_pos", "marker_b_mean", "marker_b_pos"} <= set(objects.columns)
    assert not [name for name in objects.columns if name.startswith("marker_a")]
    assert objects["phenotype"].str.startswith("OTX2").all() and objects["phenotype"].str.contains(r"\|Marker B[+\-?]$").all()

    exported = load_recipe(out / "recipe.yaml")  # the settings beside the tables use the same names
    assert [(item.id, item.name, item.measurement) for item in exported.classifications] == [
        ("otx2_pos", "OTX2", "otx2_mean"), ("marker_b_pos", "Marker B", "marker_b_mean"),
    ]
    assert [item.numerator for item in exported.reports] == ["otx2_pos", "marker_b_pos", "otx2_pos AND marker_b_pos"]

    # Reopened, the experiment still knows the name.
    from cellquant.controller import AnalysisController

    reopened = AnalysisController.open(controller.directory)
    assert reopened._channel_name(1) == "OTX2" and reopened.marker_names()["marker_a_pos"] == "OTX2"


def test_a_marker_the_user_named_keeps_its_name(tmp_path: Path):
    controller = _practice(tmp_path, names=("Bright cells", "Marker B"))
    controller.rename_channel(1, "OTX2")
    assert controller.marker_names()["bright_cells_pos"] == "Bright cells"
    assert controller.export_ids() == {}
    summary = pd.read_csv(controller.export(tmp_path / "export") / "image_summary.csv")
    assert "Bright_cells_pos_percent" in summary.columns


def test_channel_names_must_be_usable(tmp_path: Path):
    controller = _practice(tmp_path)
    with pytest.raises(CellQuantError, match="already called"):
        controller.rename_channel(1, "marker b")
    with pytest.raises(CellQuantError, match="needs a name"):
        controller.rename_channel(1, "   ")
    controller.rename_channel(1, "OTX2")
    controller.rename_channel(1, "Marker A")  # back again: exported as it was first
    assert controller.marker_names()["marker_a_pos"] == "Marker A" and controller.export_ids() == {}


def test_quantanalysis_reads_named_exports_and_numbered_runs(tmp_path: Path):
    from cellquant.stats import sources

    controller = _practice(tmp_path)
    controller.rename_channel(1, "OTX2")
    out = controller.export(tmp_path / "export")
    export = sources.read_results_folder(out)
    labels = {item.key: item.label for item in export.measures() if item.kind == "percent"}
    assert labels["OTX2_pos_percent"] == "% OTX2+ of all objects"
    assert labels["OTX2_pos_and_Marker_B_pos_percent"] == "% OTX2+ and Marker B+ of all objects"
    assert not [name for name in export.metadata_columns() if name.startswith(("OTX2_pos", "Marker_B_pos"))]

    live = sources.from_controller(controller)  # the open experiment: numbered columns, current names
    labels = {item.key: item.label for item in live.measures() if item.kind == "percent"}
    assert labels["report_1_percent"] == "% OTX2+ of all objects"
