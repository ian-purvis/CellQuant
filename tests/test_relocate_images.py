"""An experiment opened where its images are not (another computer, a moved folder) finds them again."""

from pathlib import Path

import numpy as np
import tifffile

from cellquant.controller import AnalysisController


def test_relocate_images_by_relative_path(tmp_path: Path):
    old = tmp_path / "old"
    (old / "sub").mkdir(parents=True)
    path = old / "sub" / "a.tif"
    tifffile.imwrite(path, np.zeros((16, 16), dtype=np.uint16))
    controller = AnalysisController.create(tmp_path / "exp", "t", input_directory=old)
    controller.add_image_paths([old])
    assert not controller.missing_images()
    new = tmp_path / "new"
    old.rename(new)
    reopened = AnalysisController.open(tmp_path / "exp")
    assert len(reopened.missing_images()) == 1
    assert reopened.relocate_images(new) == 1
    assert not reopened.missing_images()
    assert AnalysisController.open(tmp_path / "exp").experiment.input_directory == str(new)


def test_relocate_images_does_not_guess_between_same_names(tmp_path: Path):
    old = tmp_path / "old"
    for sub in ("a", "b"):
        (old / sub).mkdir(parents=True)
        tifffile.imwrite(old / sub / "x.tif", np.zeros((16, 16), dtype=np.uint16))
    controller = AnalysisController.create(tmp_path / "exp", "t", input_directory=old)
    controller.add_image_paths([old])
    new = tmp_path / "new"
    (new / "c").mkdir(parents=True)
    (new / "d").mkdir()
    for sub in ("c", "d"):
        tifffile.imwrite(new / sub / "x.tif", np.zeros((16, 16), dtype=np.uint16))
    old.rename(tmp_path / "gone")
    reopened = AnalysisController.open(tmp_path / "exp")
    assert reopened.relocate_images(new) == 0
    assert len(reopened.missing_images()) == 2
