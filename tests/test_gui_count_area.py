"""Count area in step 5: draw, use for this image or all images, clear.

Needs napari and Qt; skipped without napari.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

napari = pytest.importorskip("napari")

from tests.test_count_area import TOP_LEFT  # noqa: E402
from tests.test_gui_crop import _events, _wait, open_window  # noqa: E402,F401
from tests.test_workflow import _recipe, _write_squares  # noqa: E402


def test_count_area_buttons(tmp_path: Path, open_window):  # noqa: F811
    from cellquant.controller import AnalysisController
    from cellquant.gui.app import COUNT_AREA_LAYER, drawn_polygons

    first, second = tmp_path / "a.tif", tmp_path / "b.tif"
    _write_squares(first)
    _write_squares(second)
    controller = AnalysisController.create(tmp_path / "experiment", "Count area")
    controller.add_image_paths([first, second])
    controller.set_recipe(_recipe())
    controller.save()
    shell = open_window(controller.directory)
    shell.run_current()
    assert _wait(shell, 120)
    ids = [record.image_id for record in shell.controller.experiment.images]
    panel = shell._review_panel

    panel._use_count_area(every_image=False)  # nothing drawn yet: nothing changes
    assert not shell.controller.experiment.image(ids[0]).count_area

    panel._draw_count_area()
    layer = shell.viewer.layers[COUNT_AREA_LAYER]
    assert layer.mode == "add_polygon"
    layer.add(np.asarray(TOP_LEFT[0], dtype=float), shape_type="polygon")
    layer.add(np.array([[60.0, 60.0], [70.0, 70.0]]), shape_type="line")  # no area: ignored
    assert len(drawn_polygons(layer)) == 1

    panel._use_count_area(every_image=False)
    _events(0.2)
    assert shell.controller.experiment.image(ids[0]).count_area
    assert not shell.controller.experiment.image(ids[1]).count_area
    assert shell.controller.last_results[ids[0]].qc.n_objects == 1

    panel._use_count_area(every_image=True)
    assert all(shell.controller.experiment.image(image_id).count_area for image_id in ids)

    panel._clear_count_area()
    _events(0.2)
    assert not shell.controller.experiment.image(ids[0]).count_area
    assert shell.controller.experiment.image(ids[1]).count_area
    assert COUNT_AREA_LAYER not in shell.viewer.layers
    assert shell.controller.last_results[ids[0]].qc.n_objects == 2
