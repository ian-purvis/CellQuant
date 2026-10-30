"""Reading CellQuant results folders into column tables (cellquant.stats.sources), without a window."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cellquant.errors import StatisticsError
from cellquant.stats import sources
from cellquant.storage import unit_summary

# image: (condition, replicate, approved, objects counted, positive). Replicate r1 of "ctrl" has two images.
IMAGES = {
    "img_1": ("ctrl", "r1", True, 100, 10),
    "img_2": ("ctrl", "r1", True, 300, 60),
    "img_3": ("ctrl", "r2", True, 200, 30),
    "img_4": ("treated", "r3", True, 100, 40),
    "img_5": ("treated", "r4", True, 50, 30),
    "img_6": ("treated", "r4", False, 100, 90),
}


def make_results(root: Path) -> Path:
    """A small results folder in CellQuant's layout: experiment.json, the recipe, working/summaries and measurements."""

    working = root / "working"
    (working / "summaries").mkdir(parents=True)
    (working / "measurements").mkdir()
    (root / "recipes").mkdir()
    records = []
    for image_id, (condition, replicate, approved, total, positive) in IMAGES.items():
        records.append({"image_id": image_id, "include": True, "processing_status": "approved" if approved else "analyzed"})
        pd.DataFrame([{
            "sample_name": image_id, "filename": f"{image_id}.nd2", "image_id": image_id, "total_objects": total, "n_unmeasured": 0,
            "qc_status": "success", "Folder 1": condition, "Folder 2": replicate,
            "report_1_count": positive, "report_1_percent": 100 * positive / total, "report_1_denominator": total,
            "report_1_numerator": "mk_pos", "report_1_denominator_expression": "all_objects",
        }]).to_csv(working / "summaries" / f"{image_id}.csv", index=False)
        rows = []
        for number in range(total + 2):  # two extra objects that do not count
            rows.append({"image_id": image_id, "object_id": number + 1, "area": 10.0 + number % 7 + (5 if condition == "treated" else 0),
                         "mk_mean": 100.0 + number, "mk_pos": number < positive, "excluded": number == total,
                         "unmeasured": number == total + 1})
        pd.DataFrame(rows).to_csv(working / "measurements" / f"{image_id}.csv", index=False)
    (root / "experiment.json").write_text(json.dumps({
        "experiment_name": "Demo", "recipe_id": "r1", "images": records,
        "analyses": [{"recipe_id": "r1", "name": "Analysis 1", "working_folder": "working"}],
    }), encoding="utf-8")
    (root / "recipes" / "r1.yaml").write_text(
        "classifications:\n- id: mk_pos\n  name: Marker\n  measurement: mk_mean\nmeasurements:\n- id: mk_mean\n  channel: 1\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture
def source(tmp_path):
    return sources.read_results_folder(make_results(tmp_path / "results"))


def test_results_folder_is_read(source):
    assert source.label == "Demo"
    assert source.approvals_known
    assert int(source.images["approved"].sum()) == 5
    assert source.metadata_columns() == ["Folder 1", "Folder 2"]
    assert source.default_unit() == "Folder 2"
    measures = {item.key: item for item in source.measures()}
    assert measures["report_1_percent"].label == "% Marker+ of all objects"
    assert measures["mk_mean"].level == "object" and measures["mk_mean"].label == "Marker mean intensity per object"
    assert "object_id" not in measures and "mk_pos" not in measures
    assert sources.groups(source, "mk_pos") == ["Marker−", "Marker+"]


def test_unit_pooling_matches_unit_summary(source):
    spec = sources.ImportSpec("report_1_percent", dot="unit", unit="Folder 2", group_by="Folder 1", approved_only=True)
    imported = sources.build(source, spec)
    assert imported.table.names == ["ctrl", "treated"]
    ctrl = sorted(imported.table["ctrl"].finite)
    # r1 pools 70 of 400 = 17.5 %, not the mean of 10 % and 20 % (15 %).
    assert ctrl == pytest.approx(sorted([100 * 70 / 400, 100 * 30 / 200]))
    # r4 has one approved image (30 of 50); the unapproved one is left out.
    assert sorted(imported.table["treated"].finite) == pytest.approx([40.0, 60.0])
    approved = source.images.loc[source.images["approved"]]
    expected = unit_summary(approved, "Folder 2", "Folder 1")
    assert sorted(expected["report_1_unit_pooled_percent"]) == pytest.approx(sorted([*ctrl, 40.0, 60.0]))
    # Both levels are kept: the images for a SuperPlot, the replicates for the tests.
    assert len(imported.images) == 5 and set(imported.images["unit"]) == {"r1", "r2", "r3", "r4"}
    assert list(imported.units.columns) == ["group", "dot", "value"]
    assert not imported.pseudoreplicated


def test_all_images_and_order(source):
    spec = sources.ImportSpec("report_1_percent", dot="image", group_by="Folder 1", order=["treated", "ctrl"], approved_only=False)
    imported = sources.build(source, spec)
    assert imported.table.names == ["treated", "ctrl"]
    assert imported.table["treated"].finite.size == 3
    only = sources.build(source, spec.with_(order=["ctrl"]))
    assert only.table.names == ["ctrl"]


def test_counts_are_summed_per_unit(source):
    imported = sources.build(source, sources.ImportSpec("total_objects", dot="unit", unit="Folder 2", group_by="Folder 1"))
    assert sorted(imported.table["ctrl"].finite) == [200.0, 400.0]


def test_objects_count_only_counted_ones_and_warn(source):
    imported = sources.build(source, sources.ImportSpec("area", dot="object", group_by="Folder 1", approved_only=True))
    assert imported.pseudoreplicated
    assert any("Pseudoreplication" in text for text in imported.warnings)
    assert imported.table["ctrl"].finite.size == 600  # excluded and unmeasured objects left out
    assert imported.table["treated"].finite.size == 150


def test_object_groups_per_image_are_paired(source):
    imported = sources.build(source, sources.ImportSpec("mk_mean", dot="image", group_by="mk_pos", approved_only=False))
    assert imported.table.paired
    assert imported.table.names == ["Marker−", "Marker+"]
    assert len(imported.row_labels) == 6
    # img_1: positives are objects 0-9 (mean 104.5), negatives 10-99 (mean 154.5).
    assert imported.table["Marker+"].values[0] == pytest.approx(104.5)
    assert imported.table["Marker−"].values[0] == pytest.approx(154.5)


def test_no_approved_image_is_explained(tmp_path):
    root = make_results(tmp_path / "results")
    document = json.loads((root / "experiment.json").read_text())
    for record in document["images"]:
        record["processing_status"] = "analyzed"
    (root / "experiment.json").write_text(json.dumps(document))
    source = sources.read_results_folder(root)
    with pytest.raises(StatisticsError, match="approved"):
        sources.build(source, sources.ImportSpec("report_1_percent", group_by="Folder 1"))


def test_export_folder_and_run_folder(tmp_path):
    root = make_results(tmp_path / "results")
    export = tmp_path / "export"
    export.mkdir()
    pd.concat([pd.read_csv(path) for path in sorted((root / "working" / "summaries").glob("*.csv"))]).to_csv(export / "image_summary.csv", index=False)
    pd.concat([pd.read_csv(path) for path in sorted((root / "working" / "measurements").glob("*.csv"))]).to_csv(export / "objects.csv", index=False)
    source = sources.read_results_folder(export)
    assert not source.approvals_known
    imported = sources.build(source, sources.ImportSpec("report_1_percent", dot="unit", unit="Folder 2", group_by="Folder 1"))
    assert "does not record approvals" in " ".join(imported.notes)
    assert sorted(imported.table["treated"].finite) == pytest.approx([40.0, 100 * 120 / 150])
    working = sources.read_results_folder(root / "working")
    assert working.approvals_known  # the active analysis's working folder: experiment.json has its approvals
    with pytest.raises(StatisticsError, match="not a CellQuant"):
        sources.read_results_folder(tmp_path)


def test_readable_names():
    names = {"a_pos": "A", "b_pos": "B"}
    assert sources.readable("a_pos AND NOT b_pos", names) == "A+ and B−"
    assert sources.readable("all_objects", names) == "all objects"
    assert sources.group_label("a_pos", "True", names) == "A+"
    assert sources.group_label("a_pos", False, names) == "A−"
    assert np.isnan(pd.to_numeric(pd.Series([""]), errors="coerce")[0])


def test_units_appear_in_labels_and_are_not_group_columns(tmp_path):
    folder = make_results(tmp_path / "units")
    for path in (folder / "working" / "summaries").glob("*.csv"):
        table = pd.read_csv(path)
        table["count_area"], table["count_area_unit"] = 5.0, "um2"
        table.to_csv(path, index=False)
    for path in (folder / "working" / "measurements").glob("*.csv"):
        table = pd.read_csv(path)
        table["area_unit"], table["length_unit"] = "um2", "um"
        table["a_diam"] = 3.0
        table.to_csv(path, index=False)
    recipe = folder / "recipes" / "r1.yaml"
    recipe.write_text(recipe.read_text(encoding="utf-8") + "- id: a_diam\n  channel: 1\n  statistic: equivalent_diameter\n", encoding="utf-8")
    source = sources.read_results_folder(folder)
    assert "count_area_unit" not in source.metadata_columns()
    labels = {item.key: item.label for item in source.measures()}
    assert labels["area"].startswith("Area in µm²")
    assert "in µm" in labels["a_diam"] and "in µm²" not in labels["a_diam"]
    assert "area_unit" not in labels and "length_unit" not in labels
    assert "in µm²" in labels["count_area"]
