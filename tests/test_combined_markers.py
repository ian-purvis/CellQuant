"""Step 5 All markers: each object's mix of positive markers, the mixes table and their colors."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("qtpy")

from cellquant.gui import combined_markers as cm  # noqa: E402


@pytest.fixture
def settings(tmp_path, monkeypatch):
    from qtpy.QtCore import QSettings

    store = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    monkeypatch.setattr(cm, "_settings", lambda: store)
    return store


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "object_id": [1, 2, 3, 4, 5, 6, 7],
            "a_pos": pd.array([True, True, False, False, True, None, True], dtype="boolean"),
            "b_pos": pd.array([True, False, True, False, True, True, False], dtype="boolean"),
            "excluded": [False, False, False, False, False, False, True],
            "in_count_area": [True, True, True, True, False, True, True],
        }
    )


def test_object_mixes_counts_only_counted_objects():
    ids, masks = cm.object_mixes(_frame(), ["a_pos", "b_pos"])
    # 7 is deleted, 5 is outside the count area; 6 has no call for the first marker.
    assert ids.tolist() == [1, 2, 3, 4, 6]
    assert masks.tolist() == [0b11, 0b01, 0b10, 0b00, cm.UNMEASURED]


def test_mix_rows_order_and_percent():
    _ids, masks = cm.object_mixes(_frame(), ["a_pos", "b_pos"])
    rows = cm.mix_rows(masks, 2)
    assert [mask for mask, _count, _percent in rows] == [0b01, 0b10, 0b11, 0b00, cm.UNMEASURED]
    assert [count for _mask, count, _percent in rows] == [1, 1, 1, 1, 1]
    assert rows[2][2] == pytest.approx(25.0)  # of the 4 measured counted objects
    names = ["A", "B"]
    assert [cm.mix_name(mask, names) for mask, _c, _p in rows] == ["A only", "B only", "A + B", "Negative for all", "Not measured"]


def test_overlaps_list_doubles_before_triples():
    assert cm.overlap_masks(3) == [0b011, 0b101, 0b110, 0b111]
    # Past four markers only the overlaps found are listed.
    rows = cm.mix_rows(np.array([0b10001, 0b00011, 0]), 5)
    assert [mask for mask, _c, _p in rows] == [1, 2, 4, 8, 16, 0b00011, 0b10001, 0]


def test_colors_default_saved_and_reset(settings):
    ids = ["a_pos", "b_pos", "c_pos"]
    assert cm.mix_color(0b001, ids) == cm.MARKER_COLORS[0]
    assert cm.mix_color(0b100, ids) == cm.MARKER_COLORS[2]
    assert cm.mix_color(0b011, ids) == cm.MIX_COLORS[0]
    assert cm.mix_color(0b111, ids) == cm.MIX_COLORS[3]
    assert cm.mix_color(0, ids) == cm.NONE_COLOR
    cm.save_mix_color(0b011, ids, "#123456")
    cm.save_mix_color(0b010, ids, "#abcdef")
    assert cm.mix_color(0b011, ids) == "#123456"
    assert cm.mix_color(0b010, ids) == "#abcdef"
    # Saved by marker ids, so the same overlap keeps its color when the markers are reordered.
    assert cm.mix_color(0b110, ["c_pos", "a_pos", "b_pos"]) == "#123456"
    cm.reset_mix_colors(ids)
    assert cm.mix_color(0b011, ids) == cm.MIX_COLORS[0]
    assert cm.mix_color(0b010, ids) == cm.MARKER_COLORS[1]
