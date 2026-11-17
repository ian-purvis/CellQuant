"""Step 1's Rename… button names the channels, the way renaming a channel's layer does.

Needs napari and Qt; skipped without napari.
"""

from __future__ import annotations

from pathlib import Path

import pytest

napari = pytest.importorskip("napari")

from tests.gui_support import _texture_sizes  # noqa: E402,F401
from tests.test_gui_crop import _events, _wait, open_window  # noqa: E402,F401


def _open(tmp_path: Path, open_window):  # noqa: F811
    from cellquant.practice import create_practice_experiment

    controller = create_practice_experiment(tmp_path / "practice")
    controller.save()
    return open_window(controller.directory), controller


def _rename(shell, monkeypatch, typed: dict[int, str]) -> None:
    """Click Rename…, type the names in the dialog's fields and accept it."""

    from qtpy.QtWidgets import QDialog

    panel = shell._experiment_panel

    def fake_exec(dialog):
        for index, text in typed.items():
            panel._rename_fields[index].setText(text)
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, "exec", fake_exec)
    panel.rename_button.click()
    _events(0.6)


def test_rename_button_has_a_tooltip_and_follows_the_experiment(tmp_path: Path, open_window):  # noqa: F811
    shell, _controller = _open(tmp_path, open_window)
    button = shell._experiment_panel.rename_button
    assert button.text() == "Rename…" and button.isEnabled() and button.toolTip()
    shell._clear_managed()
    shell.controller = None
    shell._experiment_panel.refresh()
    assert not button.isEnabled() and "Open an experiment" in button.toolTip()


def test_rename_dialog_names_channels_through_the_layer_path(tmp_path: Path, open_window, monkeypatch):  # noqa: F811
    from cellquant.controller import AnalysisController

    shell, controller = _open(tmp_path, open_window)
    layers = [layer for layer in shell.viewer.layers if layer._type_string == "image"]
    assert [layer.name for layer in layers] == ["Channel 1 = Nuclei", "Channel 2 = Marker A", "Channel 3 = Marker B"]

    _rename(shell, monkeypatch, {1: "OTX2", 2: "PAX6"})
    assert [layer.name for layer in layers] == ["Channel 1 = Nuclei", "Channel 2 = OTX2", "Channel 3 = PAX6"]  # the layers follow
    assert [channel.channel_name for channel in shell.controller.experiment.channels] == ["Nuclei", "OTX2", "PAX6"]
    assert "OTX2" in shell._experiment_panel.channel_order.text() and "Marker A" not in shell._experiment_panel.channel_order.text()
    assert "Marker A" not in [box.text() for box in shell._marker_setup.boxes]
    assert [channel.channel_name for channel in AnalysisController.open(controller.directory).experiment.channels] == ["Nuclei", "OTX2", "PAX6"]

    # Renaming a layer in the layer list still works the same way.
    layers[1].name = "CRX"
    assert shell.controller._channel_name(1) == "CRX"


def test_rename_dialog_refuses_a_name_another_channel_has(tmp_path: Path, open_window, monkeypatch):  # noqa: F811
    from qtpy.QtWidgets import QDialog, QDialogButtonBox

    shell, _controller = _open(tmp_path, open_window)
    panel = shell._experiment_panel
    seen = {}

    def fake_exec(dialog):
        ok = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok)
        panel._rename_fields[0].setText("Nuclei")
        panel._rename_fields[1].setText("Nuclei")  # two fields with one name
        seen["ok"] = ok.isEnabled()
        return QDialog.Rejected

    monkeypatch.setattr(QDialog, "exec", fake_exec)
    panel.rename_button.click()
    assert seen["ok"] is False  # OK is off while two channels share a name
    assert shell.controller._channel_name(1) == "Marker A"

    # The controller's refusal is the layer's refusal: a message, and nothing changes.
    shell.rename_channels({1: "nuclei"})
    _events(0.3)
    assert shell.controller._channel_name(1) == "Marker A"
    assert "already called" in shell._footer.status.text()


def test_rename_without_an_image_on_screen_names_the_channel_directly(tmp_path: Path, open_window, monkeypatch):  # noqa: F811
    shell, _controller = _open(tmp_path, open_window)
    shell._clear_managed()
    shell._shown_image_id = None
    _rename(shell, monkeypatch, {1: "OTX2"})
    assert shell.controller._channel_name(1) == "OTX2"
    assert "OTX2" in [box.text() for box in shell._marker_setup.boxes]
