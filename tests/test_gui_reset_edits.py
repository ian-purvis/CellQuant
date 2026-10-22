"""Undo all and Reset all images in step 3: back to the objects step 2 found.

Needs napari and Qt; skipped without napari.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

napari = pytest.importorskip("napari")

from tests.test_gui_crop import _events, _wait, open_window  # noqa: E402,F401
from tests.test_workflow import _recipe, _write_squares  # noqa: E402


def test_reset_image_and_all_images(tmp_path: Path, open_window, monkeypatch):  # noqa: F811
    from cellquant.controller import AnalysisController
    from cellquant.gui import app

    first, second = tmp_path / "a.tif", tmp_path / "b.tif"
    _write_squares(first)
    _write_squares(second)
    controller = AnalysisController.create(tmp_path / "experiment", "Reset")
    controller.add_image_paths([first, second])
    controller.set_recipe(_recipe())
    controller.save()
    shell = open_window(controller.directory)
    shell.start_batch(None)
    assert _wait(shell, 240)
    shell._nav_index = 0
    shell.show_current()
    assert _wait(shell)
    _events(0.3)
    controller = shell.controller
    ids = [record.image_id for record in controller.experiment.images]
    panel = shell._edit_panel
    original = np.array(controller.last_results[ids[0]].labels, copy=True)
    asked = []

    def answer(yes):
        def question(_parent, _title, text, *_args):
            asked.append(text)
            return app.QMessageBox.Yes if yes else app.QMessageBox.No

        monkeypatch.setattr(app.QMessageBox, "question", question)

    # Nothing edited: nothing asked.
    panel._reset()
    assert not asked and "Nothing to reset" in panel.record_status.text()

    # Delete an object and approve, then reset: the object and the approval come back / go.
    target = int(original[15, 15])
    panel.delete_object(target)
    assert _wait(shell)
    controller.set_status(ids[0], "approved")
    answer(False)
    panel._reset()
    assert "1 edit" in asked[-1]
    assert controller.edit_count(ids[0]) == 1  # No: nothing changed
    answer(True)
    panel._reset()
    assert _wait(shell)
    _events(0.2)
    assert controller.edit_count(ids[0]) == 0
    assert np.array_equal(controller.last_results[ids[0]].labels, original)
    assert int(np.asarray(shell.viewer.layers["Objects"].data)[15, 15]) == target
    assert controller.experiment.image(ids[0]).processing_status != "approved"

    # Edits on both images (one recorded, one only drawn): Reset all images clears both.
    panel.delete_object(target)
    assert _wait(shell)
    shell._nav_index = 1
    shell.show_current()
    assert _wait(shell)
    _events(0.3)
    layer = shell.viewer.layers["Objects"]
    painted = np.array(layer.data, copy=True)
    painted[60:65, 60:65] = 7
    layer.data = painted
    panel._reset_all()
    assert _wait(shell)
    _events(0.3)
    assert "2 images" in asked[-1]
    assert not panel._pending
    assert all(controller.edit_count(image_id) == 0 for image_id in ids)
    assert np.array_equal(controller.last_results[ids[0]].labels, original)
    assert int(np.asarray(shell.viewer.layers["Objects"].data)[62, 62]) != 7
