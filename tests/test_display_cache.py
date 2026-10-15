"""Going back to an image, or to one read ahead, does not read the file again."""

from pathlib import Path

import numpy as np
import tifffile

from cellquant.controller import AnalysisController


def test_display_is_cached_and_follows_file_changes(tmp_path: Path):
    folder = tmp_path / "in"
    folder.mkdir()
    path = folder / "a.tif"
    tifffile.imwrite(path, np.random.default_rng(0).integers(0, 4000, (32, 32), dtype=np.uint16))
    controller = AnalysisController.create(tmp_path / "exp", "t", input_directory=folder)
    controller.add_image_paths([path])
    record = controller.experiment.images[0]
    first = controller._load_display(record)
    assert controller._load_display(record) is first
    controller.preload_display(record)
    assert controller._load_display(record) is first
    tifffile.imwrite(path, np.zeros((32, 32), dtype=np.uint16))  # file changed: read again
    assert controller._load_display(record) is not first
