"""Original channel colors, choosing file types and images, progress and cancelling."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import numpy as np
import pytest
import tifffile

from cellquant import progress
from cellquant.controller import AnalysisController
from cellquant.image import inspect_image, load_image
from cellquant.progress import AnalysisCancelled
from tests.test_3d import _recipe as recipe_3d
from tests.test_3d import _stack
from tests.test_import_folders import _FakeND2File

# --- colors -------------------------------------------------------------------------------------


def _fake_nd2_with_colors(monkeypatch, colors):
    class Colored(_FakeND2File):
        def __init__(self):
            super().__init__({"Z": 3, "C": 3, "Y": 16, "X": 16})
            for item, color in zip(self.metadata.channels, colors, strict=True):
                item.channel.color = types.SimpleNamespace(r=color[0], g=color[1], b=color[2], a=1.0)

    monkeypatch.setitem(sys.modules, "nd2", types.SimpleNamespace(ND2File=lambda path: Colored()))


def test_nd2_channel_colors_come_from_the_file(tmp_path: Path, monkeypatch):
    # The colors in the lab's AXR files: Green, Red, Far Red shown as magenta.
    _fake_nd2_with_colors(monkeypatch, [(54, 255, 0), (255, 0, 0), (255, 0, 255)])
    path = tmp_path / "image.nd2"
    path.write_bytes(b"fake")
    expected = ((54 / 255, 1.0, 0.0), (1.0, 0.0, 0.0), (1.0, 0.0, 1.0))
    assert np.allclose(load_image(path).channel_colors, expected)
    assert np.allclose(inspect_image(path)[0].channel_colors, expected)
    controller = AnalysisController.create(tmp_path / "results", "Colors")
    controller.add_image_paths([path])
    assert np.allclose(controller.experiment.images[0].channel_colors, expected)


def test_imagej_luts_give_channel_colors(tmp_path: Path):
    ramp = np.arange(256, dtype=np.uint8)
    zeros = np.zeros(256, dtype=np.uint8)
    luts = [np.stack([zeros, ramp, zeros]), np.stack([ramp, zeros, ramp])]  # green, magenta
    path = tmp_path / "lut.tif"
    tifffile.imwrite(path, np.zeros((2, 8, 8), dtype=np.uint16), imagej=True, metadata={"axes": "CYX", "LUTs": luts})
    assert load_image(path).channel_colors == ((0.0, 1.0, 0.0), (1.0, 0.0, 1.0))


def test_ome_tiff_channel_colors(tmp_path: Path):
    path = tmp_path / "ome.ome.tif"
    tifffile.imwrite(
        path,
        np.zeros((2, 8, 8), dtype=np.uint16),
        ome=True,
        metadata={"axes": "CYX", "Channel": {"Name": ["DAPI", "GFP"], "Color": [0x0000FFFF, 0x00FF00FF]}},
    )
    assert load_image(path).channel_colors == ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0))


def test_files_without_colors_are_not_given_any(tmp_path: Path):
    path = tmp_path / "plain.tif"
    tifffile.imwrite(path, np.zeros((2, 8, 8), dtype=np.uint16), photometric="minisblack")
    assert load_image(path).channel_colors == ()


def test_display_uses_file_colors_or_gray():
    napari = pytest.importorskip("napari")  # noqa: F841 - the colormap class comes from napari
    from cellquant.gui.app import channel_colormaps
    from cellquant.image import LoadedImage

    loaded = LoadedImage(data=np.zeros((3, 4, 4), dtype=np.uint16), channel_colors=((54 / 255, 1.0, 0.0), None, (1.0, 0.0, 1.0)))
    maps = channel_colormaps(loaded)
    assert maps[1] == "gray"
    assert np.allclose(maps[0].colors[-1][:3], (54 / 255, 1.0, 0.0))
    assert np.allclose(maps[2].colors[-1][:3], (1.0, 0.0, 1.0))
    assert np.allclose(maps[0].colors[0][:3], (0, 0, 0))
    no_colors = LoadedImage(data=np.zeros((2, 4, 4), dtype=np.uint16))
    assert channel_colormaps(no_colors) == ["gray", "gray"]


def test_practice_images_carry_their_colors(tmp_path: Path):
    from cellquant.practice import write_practice_images

    paths = write_practice_images(tmp_path)
    assert load_image(paths[0]).channel_colors == ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0))


# --- file types and choosing images ---------------------------------------------------------------


def _mixed_folder(root: Path, monkeypatch) -> None:
    _fake_nd2_with_colors(monkeypatch, [(0, 255, 0), (255, 0, 0), (255, 0, 255)])
    (root / "Retina 1").mkdir(parents=True)
    (root / "Retina 2").mkdir(parents=True)
    (root / "Retina 1" / "a.nd2").write_bytes(b"fake")
    (root / "Retina 2" / "b.nd2").write_bytes(b"fake")
    tifffile.imwrite(root / "Retina 1" / "a_projection.tif", np.zeros((3, 16, 16), dtype=np.uint16), photometric="minisblack")
    (root / "TIFF only").mkdir()
    tifffile.imwrite(root / "TIFF only" / "c.tif", np.zeros((3, 16, 16), dtype=np.uint16), photometric="minisblack")


def test_only_the_chosen_file_types_are_added(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    _mixed_folder(data, monkeypatch)
    controller = AnalysisController.create(tmp_path / "results", "Types")
    notices = controller.add_image_paths([data], file_types=["nd2"])
    assert sorted(record.relative_path for record in controller.experiment.images) == ["Retina 1/a.nd2", "Retina 2/b.nd2"]
    text = "\n".join(notices)
    assert "2 TIFF files were not added, because only ND2 files were chosen." in text
    assert "Folders with no images" not in text  # a TIFF-only folder is not "empty"
    assert controller.experiment.import_file_types == ["nd2"]
    # Adding TIFFs later brings in only the new files.
    controller.add_image_paths([data], file_types=["tiff"])
    assert len(controller.experiment.images) == 4


def test_both_types_by_default_and_at_least_one_required(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    _mixed_folder(data, monkeypatch)
    controller = AnalysisController.create(tmp_path / "results", "Both")
    controller.add_image_paths([data])
    assert len(controller.experiment.images) == 4
    with pytest.raises(ValueError, match="at least one file type"):
        controller.add_image_paths([data], file_types=[])


def test_left_out_images_are_not_run_and_can_come_back(tmp_path: Path):
    for index in range(3):
        _stack(tmp_path / "data" / f"s{index}.tif")
    controller = AnalysisController.create(tmp_path / "results", "Subset")
    controller.add_image_paths([tmp_path / "data"])
    controller.set_recipe(recipe_3d("max_projection"))
    ids = [record.image_id for record in controller.experiment.images]
    controller.set_included_many(ids[1:], False)
    report = controller.run_images()
    assert [job.image_id for job in report.jobs] == ids[:1]
    assert controller.experiment.image(ids[1]).processing_status == "excluded"
    controller.set_included(ids[1], True)
    assert controller.experiment.image(ids[1]).processing_status == "not_analyzed"
    controller.set_included(ids[0], False)
    controller.set_included(ids[0], True)
    assert controller.experiment.image(ids[0]).processing_status == "analyzed"


# --- progress and cancelling ------------------------------------------------------------------------


def test_progress_reports_each_slice(tmp_path: Path):
    path = tmp_path / "stack.tif"
    _stack(path)
    controller = AnalysisController.create(tmp_path / "results", "Progress")
    controller.add_image_paths([path])
    controller.set_recipe(recipe_3d("stitch_slices"))
    seen: list[tuple[str, float | None]] = []
    with progress.reporting(lambda text, fraction: seen.append((text, fraction))):
        controller.run_image(controller.experiment.images[0].image_id)
    texts = [text for text, _fraction in seen]
    assert texts[0] == "Reading the image file"
    assert "Finding objects: slice 1 of 9" in texts and "Finding objects: slice 9 of 9" in texts
    assert "Linking outlines across slices" in texts
    assert "Measuring markers in each object" in texts
    assert texts[-1] == "Saving results"
    fractions = [fraction for text, fraction in seen if text.startswith("Finding objects: slice")]
    assert fractions == sorted(fractions) and fractions[0] == 0.0


def test_cancel_stops_mid_image_and_keeps_nothing_from_it(tmp_path: Path):
    path = tmp_path / "stack.tif"
    _stack(path)
    controller = AnalysisController.create(tmp_path / "results", "Cancel")
    controller.add_image_paths([path])
    controller.set_recipe(recipe_3d("stitch_slices"))
    image_id = controller.experiment.images[0].image_id
    state = {"slices": 0}

    def on_update(text, _fraction):
        if text.startswith("Finding objects: slice"):
            state["slices"] += 1

    with progress.reporting(on_update, lambda: state["slices"] >= 3):
        with pytest.raises(AnalysisCancelled):
            controller.run_image(image_id)
    assert state["slices"] == 3
    assert controller.recall(image_id) is None
    assert controller.experiment.image(image_id).processing_status != "analyzed"


def test_cancelling_a_batch_keeps_finished_images(tmp_path: Path):
    for index in range(3):
        _stack(tmp_path / "data" / f"s{index}.tif")
    controller = AnalysisController.create(tmp_path / "results", "Batch cancel")
    controller.add_image_paths([tmp_path / "data"])
    controller.set_recipe(recipe_3d("stitch_slices"))
    state = {"images": 0}

    def on_image(index, total, name, status):
        if status == "running":
            state["images"] = index

    # Cancel while the second image is being segmented.
    with progress.reporting(lambda text, fraction: None, lambda: state["images"] >= 2):
        report = controller.run_images(on_progress=on_image)
    assert len(report.jobs) == 1 and report.jobs[0].status in ("Success", "Warning")


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_time_left_from_slices_then_finished_images():
    clock = _Clock()
    eta = progress.TimeLeft(clock)
    eta.image_started(1, 3)
    assert eta.seconds_left() is None  # nothing timed yet
    clock.now = 4  # reading the file
    eta.step("Finding objects with Cellpose: slice 1 of 10", 0.0)
    clock.now = 6
    eta.step("Finding objects with Cellpose: slice 2 of 10", 0.1)
    # 2 s per slice: 18 s of slices left, plus a 4 s guess for after them; two more images of 28 s each.
    assert eta.seconds_left() == pytest.approx(22 + 2 * 28)
    clock.now = 7  # the estimate counts down between updates
    assert eta.seconds_left() == pytest.approx(21 + 2 * 28)
    assert "2.0 s per slice" in eta.detail()
    clock.now = 24
    eta.step("Linking outlines across slices", None)
    clock.now = 30
    eta.image_finished(1, 3)
    assert eta.seconds_left() == pytest.approx(2 * 30)
    # In the second image, the time after the slices comes from the first image (6 s).
    eta.image_started(2, 3)
    clock.now = 34
    eta.step("Finding objects with Cellpose: slice 1 of 10", 0.0)
    clock.now = 37
    eta.step("Finding objects with Cellpose: slice 2 of 10", 0.1)
    assert eta.seconds_left() == pytest.approx(27 + 6 + 30)
    clock.now = 64
    eta.step("Linking outlines across slices", None)
    clock.now = 66
    assert eta.seconds_left() == pytest.approx(4 + 30)


def test_time_left_skips_paused_time():
    clock = _Clock()
    eta = progress.TimeLeft(clock)
    eta.image_started(1, 2)
    clock.now = 10
    eta.image_finished(1, 2)
    eta.image_started(2, 2)
    clock.now = 12
    eta.pause()
    clock.now = 100
    assert eta.seconds_left() == pytest.approx(8)
    eta.resume()
    assert eta.seconds_left() == pytest.approx(8)
