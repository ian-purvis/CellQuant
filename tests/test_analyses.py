"""Several analyses of the same images: one per channel, or different settings, each with its own
results, edits and review state; run one after another, exported together."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from cellquant.controller import AnalysisController
from cellquant.errors import CellQuantError
from cellquant.quicksetup import marker_recipe
from cellquant.synthetic_retina import write_retina_set

CLASSICAL = {"sigma": 1.0, "use_watershed": True, "watershed_min_distance_px": 4, "min_area_um2": 5}


def _experiment(tmp_path: Path) -> AnalysisController:
    write_retina_set(tmp_path / "images", size=96, retinas=1, seed=4)
    controller = AnalysisController.create(tmp_path / "experiment", "Analyses", input_directory=tmp_path / "images" / "images")
    controller.add_image_paths([tmp_path / "images" / "images"])
    assert len(controller.experiment.images) == 2
    for index, name in enumerate(("OTX2", "Fluor", "PAX6")):
        controller.set_channel_name(index, name)
    base = controller.recipe.model_dump(mode="json")
    base["object_set"] = {"name": "Nuclei", "segmentation_channel": 2, "algorithm": "classical", "parameters": CLASSICAL}
    controller.set_recipe(marker_recipe(base, [(0, "OTX2"), (1, "Fluor")]))
    controller.save()
    return controller


def _names(controller):
    return [item.name for item in controller.analyses()]


def test_an_experiment_starts_with_one_analysis_and_old_experiments_get_one(tmp_path: Path):
    controller = _experiment(tmp_path)
    assert _names(controller) == ["Analysis 1"]
    image_id = controller.experiment.images[0].image_id
    first = controller.run_image(image_id)
    # An experiment saved before analyses existed: no list, results in working/ and runs/.
    path = controller.directory / "experiment.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("analyses")
    path.write_text(json.dumps(data), encoding="utf-8")
    reopened = AnalysisController.open(controller.directory)
    assert _names(reopened) == ["Analysis 1"] and reopened.active_analysis().working_folder == "working"
    again = reopened.recall(image_id)
    assert again is not None and again.qc.n_objects == first.qc.n_objects


def test_one_analysis_per_channel_each_with_its_own_results(tmp_path: Path):
    controller = _experiment(tmp_path)
    original = controller.recipe.recipe_id
    ids = controller.analyses_for_channels()
    assert len(ids) == 3 and ids[2] == original  # PAX6 is what the current analysis already uses
    assert _names(controller) == ["Analysis 1", "Objects in OTX2", "Objects in Fluor"]
    assert controller.recipe.recipe_id == original  # the active analysis did not change
    assert controller.analyses_for_channels() == ids  # asking again adds nothing

    steps = []
    reports = controller.run_analyses(on_progress=lambda *args: steps.append(args), on_analysis=lambda *args: steps.append(("start", *args)))
    assert [report.analysis for report in reports] == _names(controller)
    assert all(report.completed + report.warnings == 2 and not report.cancelled for report in reports)
    progress = [step for step in steps if step[0] != "start" and step[3] != "running"]
    assert [step[0] for step in progress] == [1, 2, 3, 4, 5, 6] and {step[1] for step in progress} == {6}
    assert progress[2][2].startswith("Objects in OTX2: ")
    assert controller.recipe.recipe_id == original  # restored

    image_id = controller.experiment.images[0].image_id
    channels, counts = {}, {}
    for item in controller.analyses():
        controller.switch_analysis(item.recipe_id)
        result = controller.recall(image_id)
        channels[item.name] = result.provenance["recipe"]["object_set"]["segmentation_channel"]
        counts[item.name] = result.qc.n_objects
        assert controller.experiment.image(image_id).processing_status in {"analyzed", "needs_attention"}
    assert channels == {"Analysis 1": 2, "Objects in OTX2": 0, "Objects in Fluor": 1}
    assert len(set(counts.values())) > 1  # different channels find different objects


def test_review_state_and_edits_stay_with_their_analysis(tmp_path: Path):
    controller = _experiment(tmp_path)
    image_id = controller.experiment.images[0].image_id
    first = controller.run_image(image_id)
    deleted = int(first.objects["object_id"].iloc[0])
    controller.delete_object(image_id, deleted)
    controller.set_status(image_id, "approved")
    other = controller.add_analysis("Objects in OTX2", activate=True)
    data = controller.recipe.model_dump(mode="json")
    data["object_set"]["segmentation_channel"] = 0
    controller.set_recipe(data)
    assert controller.recipe.recipe_id == other  # new settings, same analysis
    assert controller.experiment.image(image_id).processing_status == "not_analyzed"
    assert controller.recall(image_id) is None  # the first analysis's results are not shown here
    second = controller.run_image(image_id)
    assert not second.objects["excluded"].any()  # the edit belongs to the other analysis
    controller.switch_analysis(controller.analyses()[0].recipe_id)
    record = controller.experiment.image(image_id)
    assert record.processing_status == "approved"
    back = controller.recall(image_id)
    assert bool(back.objects.set_index("object_id").loc[deleted, "excluded"])
    # Saved and reopened: the active analysis and each one's state come back.
    controller.save()
    reopened = AnalysisController.open(controller.directory)
    assert reopened.active_analysis().name == "Analysis 1"
    assert reopened.experiment.image(image_id).processing_status == "approved"
    reopened.switch_analysis(other)
    assert reopened.experiment.image(image_id).processing_status == "analyzed"
    assert reopened.recipe.object_set.segmentation_channel == 0


def test_stopping_keeps_finished_images_and_skips_the_other_analyses(tmp_path: Path):
    controller = _experiment(tmp_path)
    controller.analyses_for_channels([0, 1])
    original = controller.recipe.recipe_id
    finished = []

    def on_progress(index, total, filename, status):
        if status != "running":
            finished.append(index)

    reports = controller.run_analyses(on_progress=on_progress, should_continue=lambda: len(finished) < 1)
    assert len(reports) == 1 and reports[0].cancelled and len(reports[0].jobs) == 1
    assert controller.recipe.recipe_id == original
    assert controller.recall(controller.experiment.images[0].image_id) is not None


def test_export_all_analyses(tmp_path: Path):
    controller = _experiment(tmp_path)
    controller.analyses_for_channels([0, 1])
    with pytest.raises(CellQuantError):
        controller.export_all(tmp_path / "empty")
    ids = [item.recipe_id for item in controller.analyses()]
    controller.run_analyses(ids[:2])
    out = controller.export_all(tmp_path / "export", group_by="sample_name")
    combined = pd.read_csv(out / "all_analyses_image_summary.csv")
    assert list(combined["analysis"].unique()) == ["Analysis 1", "Objects in OTX2"]
    assert set(combined["segmentation_channel"]) == {"PAX6", "OTX2"}
    assert (out / "Analysis_1" / "objects.csv").is_file() and (out / "Objects_in_OTX2" / "image_summary.csv").is_file()
    assert "Objects in Fluor" in (out / "analyses_not_exported.txt").read_text(encoding="utf-8")
    assert (out / "all_analyses_grouped_by_sample_name.csv").is_file()
    assert controller.recipe.recipe_id == ids[0]


def test_rename_remove_and_recipe_identity(tmp_path: Path):
    controller = _experiment(tmp_path)
    first = controller.recipe.recipe_id
    second = controller.add_analysis("Copy", activate=True)
    assert controller.add_analysis("Copy", activate=False) != second and _names(controller)[-1] == "Copy (2)"
    controller.rename_analysis(second, "Loose threshold")
    assert controller.recipe.recipe_name == "Loose threshold"
    exported = tmp_path / "settings.yaml"
    controller.export_recipe(exported)
    controller.switch_analysis(first)
    controller.import_recipe(exported)
    assert controller.recipe.recipe_id == first  # loading settings never changes which analysis this is
    controller.remove_analysis(first)
    assert controller.recipe.recipe_id != first and first not in [item.recipe_id for item in controller.analyses()]
    assert (controller.directory / "recipes" / f"{first}.yaml").is_file()  # nothing is deleted
    while len(controller.analyses()) > 1:
        controller.remove_analysis(controller.analyses()[-1].recipe_id)
    with pytest.raises(CellQuantError):
        controller.remove_analysis(controller.analyses()[0].recipe_id)


def test_duplicate_makes_a_new_analysis_and_keeps_the_old_one(tmp_path: Path):
    controller = _experiment(tmp_path)
    image_id = controller.experiment.images[0].image_id
    controller.run_image(image_id)
    first = controller.recipe.recipe_id
    controller.duplicate_recipe("Try Cellpose")
    assert _names(controller) == ["Analysis 1", "Try Cellpose"] and controller.recipe.recipe_id != first
    controller.switch_analysis(first)
    assert controller.recall(image_id) is not None


def test_a_removed_analysis_keeps_its_results_to_itself(tmp_path: Path):
    controller = _experiment(tmp_path)
    first = controller.recipe.recipe_id
    image_id = controller.experiment.images[0].image_id
    second = controller.add_analysis("Objects in OTX2", activate=True)
    data = controller.recipe.model_dump(mode="json")
    data["object_set"]["segmentation_channel"] = 0
    controller.set_recipe(data)
    controller.run_image(image_id)
    controller.remove_analysis(second)
    assert controller.recipe.recipe_id == first
    assert controller.recall(image_id) is None  # the removed analysis's run is not the first's
    reopened = AnalysisController.open(controller.directory)
    assert [item.name for item in reopened.analyses()] == ["Analysis 1"]
    assert reopened.recall(image_id) is None
    with pytest.raises(CellQuantError):
        reopened.export(tmp_path / "export")


def test_analyses_with_the_same_segmentation_keep_their_own_edits(tmp_path: Path):
    controller = _experiment(tmp_path)
    first = controller.recipe.recipe_id
    image_id = controller.experiment.images[0].image_id
    controller.run_image(image_id)
    controller.add_analysis("Copy", activate=True)  # same settings, so the same segmentation
    copy = controller.run_image(image_id)
    deleted = int(copy.objects["object_id"].iloc[0])
    controller.delete_object(image_id, deleted)
    assert bool(controller.recall(image_id).objects.set_index("object_id").loc[deleted, "excluded"])
    controller.switch_analysis(first)
    again = controller.run_image(image_id)
    assert not again.objects["excluded"].any()
    assert controller._active_edits(image_id) == []
    # Edits saved before analyses existed (no analysis stamp) belong to the original analysis.
    for item in controller.edits[image_id]:
        item.analysis = ""
    assert len(controller._active_edits(image_id)) == 1


def test_an_image_included_again_elsewhere_is_not_left_excluded(tmp_path: Path):
    controller = _experiment(tmp_path)
    first = controller.recipe.recipe_id
    image_id = controller.experiment.images[0].image_id
    controller.run_image(image_id)
    controller.set_included(image_id, False)
    second = controller.add_analysis("Copy", activate=True)
    assert controller.experiment.image(image_id).processing_status == "excluded"
    controller.set_included(image_id, True)
    controller.switch_analysis(first)
    assert controller.experiment.image(image_id).processing_status == "analyzed"
    controller.switch_analysis(second)
    assert controller.experiment.image(image_id).processing_status == "not_analyzed"
