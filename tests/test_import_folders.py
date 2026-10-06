"""Folder import with subfolders, ND2 and Z-stack loading, and what the user is told."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import numpy as np
import pytest
import tifffile

from cellquant.controller import AnalysisController
from cellquant.errors import ImageLoadError
from cellquant.image import inspect_image, load_image


def _zstack(path: Path, slices: int = 5, pixel: float = 0.5, channels: int = 2, bright_slice: int = 3) -> None:
    data = np.full((slices, channels, 48, 48), 20, dtype=np.uint16)
    data[:, 0, 10:20, 10:20] = 500  # one nucleus in every slice
    data[bright_slice, 0, 30:40, 30:40] = 900  # a second nucleus in one slice only
    data[:, 1, 10:20, 10:20] = 400
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(
        path, data, imagej=True, resolution=(1 / pixel, 1 / pixel), metadata={"axes": "ZCYX", "unit": "um", "spacing": 1.5}
    )


def _tree(root: Path) -> None:
    """Two conditions with retinas, the same filename in two folders, and empty folders."""

    _zstack(root / "Control" / "Retina 1" / "image.tif")
    _zstack(root / "Control" / "Retina 2" / "image.tif")
    _zstack(root / "CRISPRi" / "Retina 1" / "image 40x.tif", pixel=0.25, slices=7)
    for empty in ("Unused" + "/Retina 1", "Unused/Retina 2", "Control/Retina 1/Processed"):
        (root / empty).mkdir(parents=True, exist_ok=True)
    (root / "notes.txt").write_text("not an image")


def _recipe(**extra) -> dict:
    return {
        "object_set": {"segmentation_channel": 0, "algorithm": "classical", "parameters": {"threshold_method": "manual", "threshold": 100}},
        "measurements": [{"id": "m", "channel": 1, "region": {"type": "object"}, "statistic": "mean"}],
        **extra,
    }


def test_folder_import_finds_every_image_and_keeps_readable_names(tmp_path: Path):
    _tree(tmp_path / "data")
    controller = AnalysisController.create(tmp_path / "experiment", "Import")
    notices = controller.add_image_paths([tmp_path / "data"])
    records = controller.experiment.images
    assert sorted(record.relative_path for record in records) == [
        "CRISPRi/Retina 1/image 40x.tif",
        "Control/Retina 1/image.tif",
        "Control/Retina 2/image.tif",
    ]
    names = [record.sample_name for record in records]
    assert len(set(names)) == len(names), "files with the same name in different folders must stay distinct"
    retina2 = next(record for record in records if record.relative_path.startswith("Control/Retina 2"))
    assert retina2.user_metadata == {"Folder 1": "Control", "Folder 2": "Retina 2"}
    assert retina2.z_planes == 5 and retina2.number_of_channels == 2
    assert retina2.pixel_size_x == pytest.approx(0.5) and retina2.pixel_size_z == pytest.approx(1.5)

    text = "\n".join(notices)
    assert "Looking in:" in text
    assert "Found 3 images (3 TIFF) in 3 folders." in text
    assert "Unused (and its 2 subfolders)" in text
    assert "Control/Retina 1/Processed" in text
    assert "Unused/Retina 1;" not in text  # an empty parent is named once
    assert "2 different pixel sizes" in text
    assert "3 images are Z-stacks (5-7 slices)" in text


def test_folder_import_does_not_take_sibling_folders(tmp_path: Path):
    """Images next to the chosen folder must not be imported (even via look-alike paths)."""

    parent = tmp_path / "data"
    e145 = parent / "E14.5"
    adult = parent / "adult"
    _zstack(e145 / "Retina 1" / "image.tif")
    _zstack(adult / "Retina 1" / "image.tif")
    controller = AnalysisController.create(tmp_path / "experiment", "E14.5 only")
    notices = controller.add_image_paths([e145])
    assert sorted(record.relative_path for record in controller.experiment.images) == ["Retina 1/image.tif"]
    assert all("adult" not in (record.source_path or "").replace("\\", "/") for record in controller.experiment.images)
    assert any("Looking in:" in line and "E14.5" in line.replace("\\", "/") for line in notices)
    assert not any("skipped" in line and "outside" in line for line in notices)


def test_adding_the_same_folder_twice_does_not_duplicate(tmp_path: Path):
    _tree(tmp_path / "data")
    controller = AnalysisController.create(tmp_path / "experiment", "Twice")
    controller.add_image_paths([tmp_path / "data"])
    notices = controller.add_image_paths([tmp_path / "data"])
    assert len(controller.experiment.images) == 3
    assert any("3 were already listed" in line for line in notices)


def test_cellquant_output_inside_a_data_folder_is_not_imported(tmp_path: Path):
    """An experiment created inside the data folder: its runs/ labels must not become images."""

    data = tmp_path / "data"
    _tree(data)
    controller = AnalysisController.create(data, "Inside the data folder")
    controller.add_image_paths([data])
    controller.set_recipe(_recipe())
    controller.run_images()
    assert list((data / "runs").rglob("*.tif")), "the run should have written label TIFFs"
    again = AnalysisController.open(data)
    again.add_image_paths([data])
    assert len(again.experiment.images) == 3
    assert not any(record.relative_path.startswith(("runs/", "working/")) for record in again.experiment.images)


def test_zstack_max_projection_and_single_slice(tmp_path: Path):
    path = tmp_path / "stack.tif"
    _zstack(path, slices=5, bright_slice=3)
    info = inspect_image(path)[0]
    assert (info.z_planes, info.n_channels, info.height, info.width) == (5, 2, 48, 48)

    projected = load_image(path)
    assert projected.z_description == "max projection of 5 slices"
    assert projected.data[0, 35, 35] == 900  # the one-slice nucleus is in the projection
    first = load_image(path, z_mode="single_plane", z_index=0)
    assert first.z_description == "slice 1 of 5"
    assert first.data[0, 35, 35] == 20
    with pytest.raises(ImageLoadError, match="Slice 9 was chosen, but this image has 5 slices"):
        load_image(path, z_mode="single_plane", z_index=8)


def test_z_handling_is_a_setting_recorded_with_results(tmp_path: Path):
    path = tmp_path / "data" / "stack.tif"
    _zstack(path, slices=5, bright_slice=3)
    controller = AnalysisController.create(tmp_path / "experiment", "Z")
    controller.add_image_paths([path])
    image_id = controller.experiment.images[0].image_id
    controller.set_recipe(_recipe())
    projected = controller.run_image(image_id)
    controller.set_recipe(_recipe(z_stack="single_plane", z_index=0))
    single = controller.run_image(image_id)
    assert projected.qc.n_objects == 2 and single.qc.n_objects == 1  # not served from the projection's cache
    assert projected.provenance["z_description"] == "max projection of 5 slices"
    assert single.provenance["z_description"] == "slice 1 of 5"
    assert projected.provenance["recipe_sha256"] != single.provenance["recipe_sha256"]


# --- ND2, with a stand-in for the nd2 package --------------------------------------------


class _FakeND2File:
    """Just the parts of nd2.ND2File that CellQuant uses."""

    def __init__(self, sizes: dict, calibrated=(True, True, True), size=(0.575, 0.575, 1.5), names=("Green", "Red", "Far Red")):
        self.sizes = sizes
        self.dtype = np.dtype("uint16")
        self._calibrated = calibrated
        self._size = size
        channel = lambda name: types.SimpleNamespace(  # noqa: E731
            channel=types.SimpleNamespace(name=name),
            microscope=types.SimpleNamespace(objectiveName="APO LWD 20x WI"),
            volume=types.SimpleNamespace(axesCalibrated=calibrated),
        )
        self.metadata = types.SimpleNamespace(channels=[channel(name) for name in names])

    def voxel_size(self):
        return types.SimpleNamespace(x=self._size[0], y=self._size[1], z=self._size[2])

    def asarray(self):
        shape = tuple(self.sizes.values())
        array = np.zeros(shape, dtype=np.uint16)
        axes = "".join(self.sizes)
        if "P" in axes:  # each position gets a different value, so the right one can be checked
            for position in range(self.sizes["P"]):
                index = [slice(None)] * len(shape)
                index[axes.index("P")] = position
                array[tuple(index)] = 100 * (position + 1)
        return array

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _fake_nd2(monkeypatch, **kwargs) -> None:
    module = types.SimpleNamespace(ND2File=lambda path: _FakeND2File(**kwargs))
    monkeypatch.setitem(sys.modules, "nd2", module)


def test_nd2_channels_calibration_and_projection(tmp_path: Path, monkeypatch):
    path = tmp_path / "Retina 1" / "image.nd2"
    path.parent.mkdir()
    path.write_bytes(b"fake")
    _fake_nd2(monkeypatch, sizes={"Z": 7, "C": 3, "Y": 32, "X": 40})
    info = inspect_image(path)[0]
    assert (info.z_planes, info.n_channels, info.channel_names, info.objective) == (7, 3, ("Green", "Red", "Far Red"), "APO LWD 20x WI")
    assert info.pixel_size_x == pytest.approx(0.575)
    loaded = load_image(path)
    assert loaded.data.shape == (3, 32, 40)
    assert loaded.channel_names == ("Green", "Red", "Far Red")
    controller = AnalysisController.create(tmp_path / "experiment", "ND2")
    controller.add_image_paths([tmp_path])
    assert [channel.channel_name for channel in controller.experiment.channels] == ["Green", "Red", "Far Red"]


def test_uncalibrated_nd2_has_no_pixel_size(tmp_path: Path, monkeypatch):
    """Uncalibrated ND2 files report 1 x 1 x 1; that must not be taken as 1 µm."""

    path = tmp_path / "image.nd2"
    path.write_bytes(b"fake")
    _fake_nd2(monkeypatch, sizes={"C": 2, "Y": 16, "X": 16}, calibrated=(False, False, False), size=(1, 1, 1), names=("A", "B"))
    assert inspect_image(path)[0].pixel_size_x is None
    assert "no pixel size in the file" in inspect_image(path)[0].notes
    assert load_image(path).pixel_size_x is None


def test_multiposition_nd2_becomes_one_image_per_position(tmp_path: Path, monkeypatch):
    path = tmp_path / "plate.nd2"
    path.write_bytes(b"fake")
    _fake_nd2(monkeypatch, sizes={"P": 3, "C": 2, "Y": 16, "X": 16}, names=("A", "B"))
    controller = AnalysisController.create(tmp_path / "experiment", "Positions")
    controller.add_image_paths([path])
    records = controller.experiment.images
    assert [record.position for record in records] == [0, 1, 2]
    assert records[1].sample_name == "plate [position 2]"
    assert int(load_image(path, position=2).data.max()) == 300


def test_nd2_time_series_is_refused(tmp_path: Path, monkeypatch):
    path = tmp_path / "movie.nd2"
    path.write_bytes(b"fake")
    _fake_nd2(monkeypatch, sizes={"T": 4, "C": 1, "Y": 8, "X": 8}, names=("A",))
    with pytest.raises(ImageLoadError, match="time series"):
        load_image(path)


def test_results_stay_out_of_the_image_folder_unless_that_folder_is_chosen(tmp_path: Path):
    images = tmp_path / "images"
    images.mkdir()
    data = np.zeros((2, 32, 32), dtype=np.uint16)
    data[0, 8:18, 8:18] = 1000
    tifffile.imwrite(images / "sample.tif", data, photometric="minisblack")
    results = tmp_path / "results"
    controller = AnalysisController.create(results, "Separated", input_directory=images)
    controller.add_image_paths([images])
    controller.set_recipe(
        {
            "object_set": {
                "segmentation_channel": 0,
                "algorithm": "classical",
                "parameters": {"threshold_method": "manual", "threshold": 100},
            },
            "measurements": [
                {"id": "marker_mean", "channel": 1, "region": {"type": "object"}, "statistic": "mean"}
            ],
        }
    )
    controller.run_image(controller.experiment.images[0].image_id)
    assert controller.experiment.input_directory == str(images.resolve())
    assert (results / "experiment.json").is_file()
    assert any((results / "runs").iterdir())
    assert list(images.iterdir()) == [images / "sample.tif"]


def test_results_inside_the_image_folder_is_detected(tmp_path: Path):
    pytest.importorskip("qtpy")
    try:
        from cellquant.gui.app import results_inside_images
    except Exception as exc:  # noqa: BLE001 - no Qt binding installed
        pytest.skip(f"GUI not importable: {exc}")
    images = tmp_path / "images"
    assert results_inside_images(images, images)
    assert results_inside_images(images, images / "analysis")
    assert not results_inside_images(images, tmp_path / "images - CellQuant results")
    assert not results_inside_images(images / "analysis", images)


def test_new_experiment_refuses_a_results_folder_that_holds_another_experiment(tmp_path: Path):
    """Reusing a results folder reopened the old experiment, listing the images of a neighboring folder."""

    pytest.importorskip("qtpy")
    try:
        from cellquant.gui.app import existing_experiment_images
    except Exception as exc:  # noqa: BLE001 - no Qt binding installed
        pytest.skip(f"GUI not importable: {exc}")
    parent = tmp_path / "data"
    _zstack(parent / "E14.5_E17.5" / "a.tif")
    _zstack(parent / "P0_P21" / "b.tif")
    results = tmp_path / "Outputs"
    old = AnalysisController.create(results, "Old", input_directory=parent / "E14.5_E17.5")
    old.add_image_paths([parent / "E14.5_E17.5"])
    assert existing_experiment_images(results) == str((parent / "E14.5_E17.5").resolve())
    assert existing_experiment_images(tmp_path / "P0_P21 - CellQuant results") is None


def test_image_counts_per_subfolder_show_a_parent_folder_pick(tmp_path: Path):
    """Picking the parent of E14.5_E17.5 must be visible before import: both subfolders are listed."""

    pytest.importorskip("qtpy")
    try:
        from cellquant.gui.app import folder_image_counts
    except Exception as exc:  # noqa: BLE001 - no Qt binding installed
        pytest.skip(f"GUI not importable: {exc}")
    parent = tmp_path / "050724_CRISPRi enhancers"
    _zstack(parent / "E14.5_E17.5" / "Control" / "a.tif")
    _zstack(parent / "E14.5_E17.5" / "Control" / "b.tif")
    _zstack(parent / "P0_P21" / "c.tif")
    _zstack(parent / "loose.tif")
    assert folder_image_counts(parent) == [(".", 1), ("E14.5_E17.5", 2), ("P0_P21", 1)]
    assert folder_image_counts(parent / "E14.5_E17.5") == [("Control", 2)]


def test_folder_picker_returns_the_highlighted_folder(tmp_path: Path):
    """Single-click a folder, then Choose: the highlighted folder is returned, not its parent."""

    pytest.importorskip("qtpy")
    try:
        from qtpy.QtCore import QItemSelectionModel
        from qtpy.QtWidgets import QApplication, QFileDialog, QListView

        from cellquant.gui.app import _highlighted_directory
    except Exception as exc:  # noqa: BLE001 - no Qt binding installed
        pytest.skip(f"GUI not importable: {exc}")
    app = QApplication.instance() or QApplication([])
    parent = tmp_path / "050724_CRISPRi enhancers"
    for name in ("E14.5_E17.5", "P0_P21"):
        (parent / name).mkdir(parents=True)
    dialog = QFileDialog(None, "Pick", str(parent))
    dialog.setFileMode(QFileDialog.Directory)
    dialog.setOption(QFileDialog.ShowDirsOnly, True)
    dialog.setOption(QFileDialog.DontUseNativeDialog, True)
    dialog.show()
    view = dialog.findChild(QListView, "listView")
    for _ in range(200):  # the folder listing loads in the background
        app.processEvents()
        if view.model().rowCount(view.rootIndex()) == 2:
            break
    assert _highlighted_directory(dialog) == ""
    root = view.rootIndex()
    index = next(
        view.model().index(row, 0, root)
        for row in range(view.model().rowCount(root))
        if view.model().index(row, 0, root).data() == "E14.5_E17.5"
    )
    view.selectionModel().select(index, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
    assert _highlighted_directory(dialog) == str((parent / "E14.5_E17.5").resolve())
    dialog.close()
