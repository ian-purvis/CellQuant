"""Run folders, mask files and segmented_channels.csv say which channel the objects were found in."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pytest
import tifffile

from cellquant.controller import AnalysisController
from cellquant.experiment import delete_experiment_files
from cellquant.results_folder import mask_name
from cellquant.storage import (
    CHANNEL_LIST_FILE,
    channel_tag,
    label_paths,
    persist_image_result,
    run_channel_tag,
)
from tests.test_load_results import _experiment


def _rows(folder: Path) -> list[dict[str, str]]:
    with (folder / CHANNEL_LIST_FILE).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _run(tmp_path: Path, channel: str = "AF647", name: str = "exp"):
    """An experiment whose channel 1 is called ``channel``, run once: (controller, image id, report)."""

    controller = _experiment(tmp_path, name)
    controller.rename_channel(0, channel)
    image_id = controller.experiment.images[0].image_id
    return controller, image_id, controller.run_images()


def _working(controller: AnalysisController) -> Path:
    return controller.directory / "working"


def test_channel_tag_keeps_only_safe_characters():
    assert channel_tag('a b/c\\d:e*f?"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"
    assert channel_tag("AF 594") == "AF_594"
    assert channel_tag("a  /  b") == "a_b"
    assert channel_tag("abcdefghijklmnopqrstuvwxyz") == "abcdefghijklmnopqrst"
    assert channel_tag("abcdefghijklmnopqrs tuv") == "abcdefghijklmnopqrs"  # the cut falls on "_": none is left at the end
    for nothing in ("", "?!/ ", "Ü", "核", None):
        assert channel_tag(nothing) == ""


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        ([], ""),
        (["AF647"], "AF647"),
        (["AF647", "AF647", "AF647"], "AF647"),
        (["b", "A"], "A+b"),
        (["AF647", "af647"], "AF647"),  # one channel: folder names do not tell upper from lower case
        (["DAPI", "AF594", "AF647"], "mixed"),
        (["a" * 20, "b" * 20], "mixed"),
        (["??", ""], ""),
    ],
)
def test_run_channel_tag(names, expected):
    assert run_channel_tag(names) == expected


def test_a_run_names_folder_masks_and_list_after_the_channel(tmp_path: Path):
    controller, image_id, report = _run(tmp_path)

    assert report.run_id.startswith("run_") and report.run_id.endswith("_AF647")
    run_dir = controller.directory / "runs" / report.run_id
    assert run_dir.is_dir()
    for folder in (run_dir, _working(controller)):
        assert sorted(path.name for path in (folder / "labels").iterdir()) == [
            f"{image_id}_AF647_automated.tif",
            f"{image_id}_AF647_final.tif",
        ]
        assert _rows(folder) == [
            {
                "image_id": image_id,
                "image": controller.experiment.images[0].filename,
                "segmented_channel": "AF647",
                "mask_file": f"labels/{image_id}_AF647_final.tif",
            }
        ]
        recorded = json.loads((folder / "tables" / f"{image_id}.json").read_text(encoding="utf-8"))["labels"]
        assert recorded == {"final": f"{image_id}_AF647_final.tif", "automated": f"{image_id}_AF647_automated.tif"}


def test_reopened_experiment_gives_the_same_labels(tmp_path: Path):
    controller, image_id, _report = _run(tmp_path)
    expected = controller.recall(image_id).labels

    reopened = AnalysisController.open(controller.directory)

    assert np.array_equal(reopened.recall(image_id).labels, expected) and expected.max() > 0


def _make_old_layout(folder: Path, image_id: str) -> None:
    for kind in ("final", "automated"):
        (folder / "labels" / f"{image_id}_AF647_{kind}.tif").rename(folder / "labels" / f"{image_id}_{kind}.tif")
    table = folder / "tables" / f"{image_id}.json"
    data = json.loads(table.read_text(encoding="utf-8"))
    del data["labels"]
    table.write_text(json.dumps(data), encoding="utf-8")
    (folder / CHANNEL_LIST_FILE).unlink()


def test_results_saved_with_the_old_names_load_and_are_renamed_on_the_next_save(tmp_path: Path):
    controller, image_id, report = _run(tmp_path)
    expected = controller.recall(image_id).labels
    working = _working(controller)
    _make_old_layout(working, image_id)

    reopened = AnalysisController.open(controller.directory)

    assert np.array_equal(reopened.recall(image_id).labels, expected)
    assert label_paths(working, image_id) == (working / "labels" / f"{image_id}_final.tif", working / "labels" / f"{image_id}_automated.tif")
    reopened.run_images()  # a new run saves the image again
    assert sorted(path.name for path in (working / "labels").iterdir()) == [
        f"{image_id}_AF647_automated.tif",
        f"{image_id}_AF647_final.tif",
    ]
    assert (controller.directory / "runs" / report.run_id / "labels" / f"{image_id}_AF647_final.tif").is_file()


def test_a_renamed_channel_replaces_the_working_masks_and_leaves_earlier_runs_alone(tmp_path: Path):
    controller, image_id, first = _run(tmp_path)

    controller.rename_channel(0, "New name")
    second = controller.run_images()

    assert second.run_id.endswith("_New_name") and second.run_id != first.run_id
    assert sorted(path.name for path in (_working(controller) / "labels").iterdir()) == [
        f"{image_id}_New_name_automated.tif",
        f"{image_id}_New_name_final.tif",
    ]
    assert _rows(_working(controller))[0]["segmented_channel"] == "New name"
    earlier = controller.directory / "runs" / first.run_id
    assert (earlier / "labels" / f"{image_id}_AF647_final.tif").is_file()
    assert _rows(earlier)[0]["segmented_channel"] == "AF647"


def test_files_the_user_put_in_labels_survive_a_save(tmp_path: Path):
    controller, image_id, _report = _run(tmp_path)
    labels = _working(controller) / "labels"
    kept = ["notes.txt", f"{image_id}_mine.tif", "other_final.tif"]
    for name in kept:
        (labels / name).write_text("mine", encoding="utf-8")

    controller.rename_channel(0, "New name")
    controller.run_images()

    assert all((labels / name).read_text(encoding="utf-8") == "mine" for name in kept)


def test_channel_name_with_a_space_still_matches_for_load_results(tmp_path: Path):
    first, image_id, _report = _run(tmp_path, "Far Red", "first")
    final = _working(first) / "labels" / f"{image_id}_Far_Red_final.tif"
    assert final.is_file()

    assert mask_name(final) == ("Retina 1/sample A", True, True)
    second = _experiment(tmp_path, "second")
    report = second.load_results(first.directory)

    assert len(report.loaded) == 1 and not report.problems
    assert np.array_equal(second.recall(report.loaded[0]).labels, first.recall(image_id).labels)


def test_a_channel_list_that_cannot_be_written_does_not_stop_a_save(tmp_path: Path):
    controller = _experiment(tmp_path)
    image_id = controller.experiment.images[0].image_id
    (_working(controller) / CHANNEL_LIST_FILE).mkdir(parents=True)  # a folder of that name: nothing can be written

    controller.run_images()

    assert (_working(controller) / CHANNEL_LIST_FILE).is_dir()
    reopened = AnalysisController.open(controller.directory)
    assert reopened.recall(image_id).qc.n_objects == 2


def test_persist_without_a_channel_keeps_the_plain_names(tmp_path: Path):
    controller = _experiment(tmp_path)
    image_id = controller.experiment.images[0].image_id
    result = controller.run_image(image_id)
    folder = tmp_path / "plain"

    persist_image_result(folder, result, [])

    assert (folder / "labels" / f"{image_id}_final.tif").is_file()
    assert _rows(folder)[0]["segmented_channel"] == ""


def test_export_lists_the_segmented_channel(tmp_path: Path):
    controller, image_id, _report = _run(tmp_path)

    folder = controller.export(tmp_path / "out")

    with (folder / CHANNEL_LIST_FILE).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == ["image_id", "image", "segmented_channel"]
        rows = list(reader)
    assert [(row["image_id"], row["segmented_channel"]) for row in rows] == [(image_id, "AF647")]


def test_the_glossary_finds_the_new_named_mask(tmp_path: Path):
    from cellquant.gui.glossary import Source

    controller, image_id, _report = _run(tmp_path)
    source = Source(controller.directory, _working(controller), image_id)

    final = source.path("run/labels/<image>_<channel>_final.tif")
    automated = source.path("run/labels/<image>_<channel>_automated.tif")

    assert final == _working(controller) / "labels" / f"{image_id}_AF647_final.tif" and final.is_file()
    assert automated == _working(controller) / "labels" / f"{image_id}_AF647_automated.tif" and automated.is_file()


def test_deleting_the_experiment_removes_runs_and_working_but_keeps_user_files(tmp_path: Path):
    controller, _image_id, _report = _run(tmp_path)
    note = controller.directory / "my notes.txt"
    note.write_text("mine", encoding="utf-8")

    delete_experiment_files(controller.directory)

    assert not (controller.directory / "runs").exists() and not (controller.directory / "working").exists()
    assert note.read_text(encoding="utf-8") == "mine"
