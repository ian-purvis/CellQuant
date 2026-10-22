"""Two CellQuant windows: an experiment open in one window cannot be opened in the other.

Needs napari and Qt; skipped without napari. Uses the shared real ND2 when it is there.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

napari = pytest.importorskip("napari")

from tests.test_gui_crop import _wait, open_window  # noqa: E402,F401

ND2 = Path("/mnt/project-files/test-images/CRISPRi cont mCherry_OTX2 far red001.nd2")


def test_an_experiment_opens_in_one_window_at_a_time(tmp_path: Path, open_window):  # noqa: F811
    from cellquant.controller import AnalysisController

    if not ND2.is_file():
        pytest.skip("the shared test ND2 is not on this computer")
    image = tmp_path / ND2.name
    shutil.copyfile(ND2, image)
    first = AnalysisController.create(tmp_path / "first", "First")
    first.add_image_paths([image])
    first.save()
    second = AnalysisController.create(tmp_path / "second", "Second")
    second.save()

    window_a = open_window(first.directory)
    assert window_a.controller is not None and window_a._folder_lock.covers(first.directory)
    window_b = open_window(None)
    window_b.open_experiment(first.directory)
    assert window_b.controller is None
    assert "open in another CellQuant window" in window_b._error_note.text()

    window_a.open_experiment(second.directory)  # the first folder is let go
    assert _wait(window_a)
    window_b.open_experiment(first.directory)
    assert _wait(window_b)
    assert window_b.controller is not None and window_b.controller.experiment.images[0].filename == ND2.name
    window_a.open_experiment(first.directory)
    assert Path(window_a.controller.directory) == Path(second.directory)  # still on its own experiment
