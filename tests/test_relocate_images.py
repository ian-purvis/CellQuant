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
