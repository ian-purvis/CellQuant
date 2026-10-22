"""Moving between images keeps napari's layers and gives them the next image's pixels and objects:
what is shown must be exactly what newly added layers would show. Needs napari, Qt and OpenGL
(xvfb-run). Skipped without napari.
"""

from __future__ import annotations

import numpy as np
import pytest

napari = pytest.importorskip("napari")


def _state(viewer) -> list[dict]:
    rows = []
    for layer in viewer.layers:
        row = {
            "name": layer.name,
            "type": layer._type_string,
            "visible": layer.visible,
            "opacity": layer.opacity,
            "blending": layer.blending,
            "scale": tuple(layer.scale),
            "data": np.array(layer.data, copy=True),
        }
        if row["type"] == "image":
            row.update(
                contrast_limits=tuple(layer.contrast_limits),
                contrast_limits_range=tuple(layer.contrast_limits_range),
                gamma=layer.gamma,
                colormap=layer.colormap.name,
            )
        elif row["type"] == "labels":
            row.update(mode=layer.mode, selected_label=layer.selected_label, contour=layer.contour, undo=len(layer._undo_history))
        elif row["type"] == "points":
            row.update(text=list(layer.text.values), size=np.asarray(layer.size).tolist())
        rows.append(row)
    return rows


def _same(shown: list[dict], fresh: list[dict]) -> None:
    assert [row["name"] for row in shown] == [row["name"] for row in fresh]
    for got, want in zip(shown, fresh, strict=True):
        assert got.keys() == want.keys(), got["name"]
        for key in want:
            if key == "data":
                assert got[key].shape == want[key].shape and np.array_equal(got[key], want[key]), got["name"]
            else:
                assert got[key] == want[key], (got["name"], key, got[key], want[key])


def test_next_image_reuses_layers_and_looks_like_new_ones(tmp_path):
    from cellquant.gui.app import CellQuantWindow
    from tests.test_gui_plan import _wait
    from tests.test_plan import _experiment

    controller = _experiment(tmp_path)  # two images with one channel order, one with another
    controller.run_images()
    controller.save()
    contrast_before = [dict(channel.display_settings) for channel in controller.experiment.channels]
    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"napari viewer could not start: {exc}")
    shell = CellQuantWindow(viewer, controller.directory)
    try:
        _wait(shell)
        shell.go_to_step(4)
        _wait(shell)
        ids = list(shell._nav_ids)
        assert len(ids) == 3
        kept = 0
        for index in range(len(ids)):
            shell._nav_index = index
            before = {layer.name: layer for layer in viewer.layers}
            shell.show_current()
            _wait(shell)
            shown = _state(viewer)
            kept += all(before.get(row["name"]) is viewer.layers[row["name"]] for row in shown)
            real = shell._reusable_channel_layers
            shell._reusable_channel_layers = lambda *_args: None  # as before: every layer added again
            try:
                shell.show_current()
                _wait(shell)
            finally:
                shell._reusable_channel_layers = real
            _same(shown, _state(viewer))
        assert kept >= 1, "no switch kept the layers"
        # Showing images never stores a contrast the user did not set.
        assert [dict(channel.display_settings) for channel in shell.controller.experiment.channels] == contrast_before
    finally:
        viewer.close()


def test_switching_back_after_painting_has_no_undo_into_the_other_image(tmp_path):
    from cellquant.gui.app import CellQuantWindow
    from tests.test_gui_plan import _wait
    from tests.test_plan import _experiment

    controller = _experiment(tmp_path)
    controller.run_images()
    controller.save()
    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"napari viewer could not start: {exc}")
    shell = CellQuantWindow(viewer, controller.directory)
    try:
        _wait(shell)
        shell.go_to_step(2)
        _wait(shell)
        records = {record.image_id: record for record in shell.controller.experiment.images}
        same_layout = [i for i in shell._nav_ids if records[i].channel_names == records[shell._nav_ids[0]].channel_names]
        assert len(same_layout) >= 2
        shell._nav_index = shell._nav_ids.index(same_layout[0])
        shell.show_current()
        _wait(shell)
        objects = viewer.layers["Objects"]
        objects.mode = "paint"
        objects.selected_label = 7
        objects.paint((5, 5), 7)
        assert len(objects._undo_history) == 1
        shell._nav_index = shell._nav_ids.index(same_layout[1])
        shell.show_current()
        _wait(shell)
        assert viewer.layers["Objects"] is objects, "the layer was kept"
        assert len(objects._undo_history) == 0 and objects.mode == "pan_zoom" and objects.selected_label == 1
        assert np.array_equal(objects.data, shell.controller.recall(same_layout[1]).labels)
    finally:
        viewer.close()
