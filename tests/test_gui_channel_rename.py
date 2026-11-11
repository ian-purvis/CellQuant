"""Renaming a channel layer in napari's layer list names the channel for the whole experiment.

Needs napari and Qt; skipped without napari.
"""

from __future__ import annotations

from pathlib import Path

import pytest

napari = pytest.importorskip("napari")

from tests.test_gui_crop import _events, _wait, open_window  # noqa: E402,F401


def _channel_layers(shell) -> list:
    return [layer for layer in shell.viewer.layers if layer._type_string == "image"]


def test_renaming_a_channel_layer_names_the_channel(tmp_path: Path, open_window):  # noqa: F811
    from cellquant.controller import AnalysisController
    from cellquant.practice import create_practice_experiment

    controller = create_practice_experiment(tmp_path / "practice")
    controller.save()
    shell = open_window(controller.directory)
    layers = _channel_layers(shell)
    assert [layer.name for layer in layers] == ["Channel 1 = Nuclei", "Channel 2 = Marker A", "Channel 3 = Marker B"]

    layers[1].name = "OTX2"  # what typing a name in the layer list does
    assert layers[1].name == "Channel 2 = OTX2"  # shown the way CellQuant labels channels
    assert shell.controller._channel_name(1) == "OTX2"
    assert "Channel 2 = OTX2" in shell._managed and "Channel 2 = Marker A" not in shell._managed
    _events(0.2)  # the lists that name channels refresh after napari is done with the rename
    assert "OTX2" in [box.text() for box in shell._marker_setup.boxes]

    # The next image shows the same name, in the same layers.
    shell._footer._step(1)
    assert _wait(shell, 60)
    assert [layer.name for layer in _channel_layers(shell)] == ["Channel 1 = Nuclei", "Channel 2 = OTX2", "Channel 3 = Marker B"]
    assert _channel_layers(shell)[1] is layers[1]

    # Typing the whole label works too; a name another channel has is refused and the label put back.
    layers[2].name = "Channel 3 = PAX6"
    assert shell.controller._channel_name(2) == "PAX6" and layers[2].name == "Channel 3 = PAX6"
    layers[2].name = "otx2"
    assert layers[2].name == "Channel 3 = PAX6" and shell.controller._channel_name(2) == "PAX6"
    layers[2].name = "   "
    assert layers[2].name == "Channel 3 = PAX6"

    # Saved with the experiment.
    assert [channel.channel_name for channel in AnalysisController.open(controller.directory).experiment.channels] == ["Nuclei", "OTX2", "PAX6"]

    # The layers are still CellQuant's own: closing the experiment's image removes them.
    shell._clear_managed()
    assert not _channel_layers(shell)
