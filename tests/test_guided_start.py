"""First-time user path without the interface: practice images, quick marker setup, approvals, guide."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from cellquant.practice import create_practice_experiment, expected_answers
from cellquant.quicksetup import marker_recipe, starting_threshold

ROOT = Path(__file__).resolve().parents[1]


def _set_up(folder: Path):
    controller = create_practice_experiment(folder)
    controller.set_recipe(marker_recipe(controller.recipe.model_dump(mode="json"), [(1, "Marker A"), (2, "Marker B")]))
    return controller


def test_practice_experiment_is_ready_for_step_2(tmp_path: Path):
    controller = create_practice_experiment(tmp_path)
    names = [channel.channel_name for channel in controller.experiment.channels]
    assert names == ["Nuclei", "Marker A", "Marker B"]
    assert len(controller.experiment.images) == 3
    assert all(record.pixel_size_x == 0.5 for record in controller.experiment.images)
    assert controller.recipe.classifications == []  # markers are left for the user
    assert "Expected results" in (tmp_path / "README.txt").read_text(encoding="utf-8")


def test_quick_setup_with_starting_cutoffs_gives_the_known_answers(tmp_path: Path):
    controller = _set_up(tmp_path)
    for record, answer in zip(controller.experiment.images, expected_answers(), strict=True):
        result = controller.run_image(record.image_id)
        cutoffs = {
            item.id: starting_threshold(result.objects[item.measurement].to_numpy(dtype=float))
            for item in controller.recipe.classifications
        }
        result = controller.update_thresholds(record.image_id, cutoffs)
        assert result.qc.n_objects == answer.nuclei
        assert result.reports["count"].tolist() == [answer.marker_a, answer.marker_b, answer.both]


def test_marker_recipe_names_ids_and_reports():
    data = marker_recipe(
        {"object_set": {"segmentation_channel": 0, "algorithm": "classical"}},
        [(1, "OTX2"), (2, "VSX2"), (3, "OTX2")],
    )
    assert [item["id"] for item in data["classifications"]] == ["otx2_pos", "vsx2_pos", "otx2_2_pos"]
    assert [item["name"] for item in data["classifications"]] == ["OTX2", "VSX2", "OTX2"]
    numerators = [item["numerator"] for item in data["reports"]]
    assert numerators[:3] == ["otx2_pos", "vsx2_pos", "otx2_2_pos"]
    assert "otx2_pos AND vsx2_pos" in numerators  # double positives for each pair


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([71, 90, 120, 148, 644, 700, 894], 396.0),  # halfway across the gap, above the brightest dim object
        ([5, 5, 5], 5.0),
        ([7], 7.0),
        ([], 0.0),
        ([1, 2, np.nan, 10, 11], 6.0),
    ],
)
def test_starting_threshold(values, expected):
    assert starting_threshold(np.array(values, dtype=float)) == pytest.approx(expected)


def test_starting_threshold_separates_noisy_groups():
    rng = np.random.default_rng(1)
    values = np.r_[rng.normal(100, 20, 300), rng.normal(400, 60, 60)]
    assert int((values > starting_threshold(values)).sum()) == 60


def test_approval_survives_a_rerun_with_the_same_settings(tmp_path: Path):
    controller = _set_up(tmp_path)
    image_id = controller.experiment.images[0].image_id
    controller.run_image(image_id)
    controller.set_status(image_id, "approved")
    controller.run_images()  # "Run all images"
    assert controller.experiment.image(image_id).processing_status == "approved"

    controller.update_thresholds(image_id, {"marker_a_pos": 50.0})
    controller.run_image(image_id)
    record = controller.experiment.image(image_id)
    assert record.processing_status == "analyzed"
    assert "different settings" in record.last_message


def test_editing_an_approved_image_clears_the_approval(tmp_path: Path):
    controller = _set_up(tmp_path)
    image_id = controller.experiment.images[0].image_id
    result = controller.run_image(image_id)
    controller.set_status(image_id, "approved")
    controller.delete_object(image_id, int(result.objects["object_id"].iloc[0]))
    assert controller.experiment.image(image_id).processing_status == "analyzed"


def test_in_app_guide_matches_docs():
    docs = (ROOT / "docs" / "START_HERE.md").read_text(encoding="utf-8")
    bundled = (ROOT / "cellquant" / "gui" / "START_HERE.md").read_text(encoding="utf-8")
    assert bundled == docs, "cellquant/gui/START_HERE.md must be a copy of docs/START_HERE.md"


def test_guide_describes_the_buttons_that_exist():
    """Every bold button name the guide tells users to click appears in the interface code."""

    guide = (ROOT / "docs" / "START_HERE.md").read_text(encoding="utf-8")
    code = (ROOT / "cellquant" / "gui" / "app.py").read_text(encoding="utf-8") + (
        ROOT / "cellquant" / "gui" / "guide.py"
    ).read_text(encoding="utf-8")
    for name in [
        "Try practice images", "New experiment", "Open experiment", "Add images", "Add folder",
        "Set sizes (µm)", "Use recommended", "Include shown", "Leave out shown", "Include only selected", "Show ND2 only", "Preview", "Run this image", "Set up markers", "Display objects by",
        "Delete object", "Restore object", "Undo", "Approve", "Next image ▶", "Run all images", "Export results",
        "Show all settings",
    ]:
        assert f"**{name}" in guide, name
        assert name in code, name
