"""A removed marker leaves saved results at once; Undo keeps an approval only when counts stay."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from cellquant.controller import AnalysisController

from tests.test_workflow import _recipe, _write_squares


def _controller(tmp_path: Path) -> tuple[AnalysisController, str]:
    image = tmp_path / "squares.tif"
    _write_squares(image)
    controller = AnalysisController.create(tmp_path / "experiment", "Demo")
    controller.add_image_paths([image])
    recipe = _recipe()
    recipe["reports"] = [{"numerator": "A", "denominator": "all_objects"}]
    controller.set_recipe(recipe)
    controller.run_images()
    return controller, controller.experiment.images[0].image_id


def _without_marker_b(controller: AnalysisController) -> None:
    data = controller.recipe.model_dump(mode="json")
    data["classifications"] = [item for item in data["classifications"] if item["id"] != "class_b"]
    controller.set_recipe(data)


def test_removed_marker_leaves_results_without_a_rerun(tmp_path: Path):
    controller, image_id = _controller(tmp_path)
    assert any("B" in str(name) for name in controller.recall(image_id).phenotype_counts["phenotype"])
    _without_marker_b(controller)
    shown = controller.recall(image_id)
    assert "class_b" not in shown.objects.columns
    assert not any("B" in str(name) for name in shown.phenotype_counts["phenotype"])
    controller.save()

    reopened = AnalysisController.open(tmp_path / "experiment")
    saved = reopened.recall(image_id)
    assert "class_b" not in saved.objects.columns
    exported = reopened.export(tmp_path / "export")
    assert "class_b" not in pd.read_csv(exported / "objects.csv").columns


def test_undo_clears_approval_when_counts_change(tmp_path: Path):
    controller, image_id = _controller(tmp_path)
    first = int(controller.recall(image_id).objects["object_id"].iloc[0])
    controller.delete_object(image_id, first)
    controller.set_status(image_id, "approved")
    controller.undo(image_id)
    record = controller.experiment.image(image_id)
    assert record.processing_status == "analyzed"
    assert "undone" in record.last_message


def test_undo_keeps_approval_when_counts_stay(tmp_path: Path):
    controller, image_id = _controller(tmp_path)
    # Count only the top-left square; the other square is outside the count area.
    controller.set_count_area([image_id], [[[0, 0], [0, 30], [30, 30], [30, 0]]])
    objects = controller.recall(image_id).objects
    outside = int(objects.loc[objects["centroid_x"] > 30, "object_id"].iloc[0])
    controller.delete_object(image_id, outside)
    controller.set_status(image_id, "approved")
    controller.undo(image_id)
    record = controller.experiment.image(image_id)
    assert record.processing_status == "approved"
    assert record.approved_settings_sha256 == controller._approval_fingerprint(image_id)
