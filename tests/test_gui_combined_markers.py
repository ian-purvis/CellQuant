"""Step 5 All markers with the practice images: every marker's cutoff at once, each mix of markers
in its own color, the pop-out window and the step 6 overlap lines.

Needs napari, Qt and OpenGL (a desktop, or xvfb-run with Mesa). Skipped when napari is missing.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

napari = pytest.importorskip("napari")

from tests.test_gui_cutoff_tools import _settle  # noqa: E402
from tests.test_gui_guided_flow import _wait  # noqa: E402


def test_all_markers(tmp_path: Path, monkeypatch):
    from qtpy.QtCore import QSettings, Qt

    from cellquant.gui import combined_markers, guide
    from cellquant.gui.app import CellQuantWindow
    from cellquant.practice import expected_answers

    store = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    monkeypatch.setattr(combined_markers, "_settings", lambda: store)
    monkeypatch.setattr(guide, "choose_practice_folder", lambda parent: tmp_path / "practice")
    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"napari viewer could not start: {exc}")
    shell = CellQuantWindow(viewer, None)
    monkeypatch.setattr(shell, "message", lambda *_args: None)
    try:
        shell.start_practice()
        _wait(shell)
        shell.run_current()
        _wait(shell)
        shell._marker_setup.button.click()
        _wait(shell)
        panel, controller = shell._review_panel, shell.controller
        view = panel.combined
        image_id = shell._nav_ids[shell._nav_index]
        record = controller.experiment.image(image_id)
        answer = next(item for item in expected_answers() if item.filename == Path(str(record.filename)).name)
        ids = [item.id for item in controller.recipe.classifications]
        assert len(ids) == 2

        panel.display.setCurrentIndex(panel.display.findData(combined_markers.ALL_MARKERS))
        _settle()
        assert panel.combined_box.isVisibleTo(panel) and not panel.cutoff_tools.isVisibleTo(panel)
        assert panel.classification_id() == ""
        assert set(view._rows) == set(ids)
        counts = {view.table.item(row, 2).text(): int(view.table.item(row, 3).text()) for row in range(view.table.rowCount())}
        names = [item.name for item in controller.recipe.classifications]
        assert counts[f"{names[0]} + {names[1]}"] == answer.both
        assert counts[f"{names[0]} only"] == answer.marker_a - answer.both
        assert counts[f"{names[1]} only"] == answer.marker_b - answer.both

        # The overlay draws each object as its mix + 1, in that mix's color.
        layer = viewer.layers["Classification"]
        both_value = 0b11 + 1
        result = controller.last_results[image_id]
        object_ids, masks = combined_markers.object_mixes(result.objects, ids)
        both_object = int(object_ids[masks == 0b11][0])
        labels = np.asarray(result.labels)
        assert np.all(np.asarray(layer.data)[labels == both_object] == both_value)
        assert np.allclose(layer.colormap.color_dict[both_value][:3], [c / 255 for c in (0xF2, 0xC1, 0x2E)], atol=0.01)

        # A new color for the overlap is used at once and remembered.
        combined_markers.save_mix_color(0b11, ids, "#0000ff")
        shell.apply_classification_colors()
        assert np.allclose(layer.colormap.color_dict[both_value][:3], [0, 0, 1], atol=0.01)

        # Unticking Show hides those objects.
        row = next(r for r in range(view.table.rowCount()) if view._table_masks[r] == 0b11)
        view.table.item(row, 0).setCheckState(Qt.Unchecked)
        _settle()
        assert np.all(np.asarray(layer.data)[labels == both_object] == 0)
        view.table.item(row, 0).setCheckState(Qt.Checked)
        _settle()

        # Each marker's slider sets this image's own cutoff; the counts and the overlay follow.
        first = view._rows[ids[0]]
        high = first.slider.maximum()
        first.slider.setValue(high)
        _settle()
        assert controller.own_cutoff(image_id, ids[0]) == pytest.approx(high, rel=0.01)
        counts = {view.table.item(r, 2).text(): int(view.table.item(r, 3).text()) for r in range(view.table.rowCount())}
        assert counts[f"{names[0]} + {names[1]}"] == 0
        assert not np.any(np.asarray(layer.data) == both_value)

        # Pop out and put back.
        view.pop_out()
        _settle()
        assert view.popped_out() and not panel.combined_box.isVisibleTo(panel) and view.window() is not shell
        view._window.close()
        _settle()
        assert not view.popped_out() and panel.combined_box.isVisibleTo(panel)

        # Back to one marker: the usual positive / negative overlay.
        panel.display.setCurrentIndex(panel.display.findData(ids[1]))
        _settle()
        assert not panel.combined_box.isVisibleTo(panel)
        assert set(np.unique(np.asarray(layer.data))) <= {0, 1, 2}
    finally:
        viewer.close()


def test_step6_overlap_lines():
    import pandas as pd

    from cellquant.gui.guide import _overlap_lines

    class Result:
        combination_counts = pd.DataFrame(
            {"phenotype": ["A+", "B+", "A+|B+"], "count": [5, 4, 2], "percent_of_objects": [50.0, 40.0, 20.0]}
        )

    assert _overlap_lines(Result()) == ["Positive for A and B: <b>2</b> (20.0% of counted objects)"]

    # A set a report already counts among all objects is not listed twice.
    from cellquant.recipe import ClassificationSpec, ReportSpec

    class Recipe:
        classifications = [ClassificationSpec(id="a_pos", name="A", measurement="a", threshold=1.0),
                           ClassificationSpec(id="b_pos", name="B", measurement="b", threshold=1.0)]
        reports = [ReportSpec(numerator="a_pos AND b_pos", denominator="all_objects")]

    assert _overlap_lines(Result(), Recipe()) == []
