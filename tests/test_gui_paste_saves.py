"""Pasting into step 1's image list applies each cell once and saves once.

Needs napari and Qt; skipped without napari.
"""

from __future__ import annotations

import collections
from pathlib import Path

import pytest

napari = pytest.importorskip("napari")

from tests.gui_support import _texture_sizes  # noqa: E402,F401
from tests.test_gui_crop import _events, _wait, open_window  # noqa: E402,F401


@pytest.fixture
def writes(monkeypatch):
    """How many times experiment.json and recipe.yaml are written."""

    import cellquant.controller as module

    counts = collections.Counter()
    saved_experiment, saved_recipe = module.save_experiment, module.save_recipe
    monkeypatch.setattr(module, "save_experiment", lambda *a, **k: (counts.update(["experiment.json"]), saved_experiment(*a, **k))[1])
    monkeypatch.setattr(module, "save_recipe", lambda *a, **k: (counts.update(["recipe.yaml"]), saved_recipe(*a, **k))[1])
    return counts


def _column(panel, name: str) -> int:
    return [panel.table.horizontalHeaderItem(index).text() for index in range(panel.table.columnCount())].index(name)


def test_a_paste_saves_once_and_gives_the_same_table_and_data(tmp_path: Path, open_window, writes):  # noqa: F811
    from cellquant.practice import create_practice_experiment

    controller = create_practice_experiment(tmp_path / "practice")
    controller.save()
    shell = open_window(controller.directory)
    panel = shell._experiment_panel
    panel.table.sortByColumn(-1, panel.table.horizontalHeader().sortIndicatorOrder())
    column = _column(panel, "Sample name")
    rows = min(3, panel.table.rowCount())
    assert rows >= 2

    writes.clear()
    panel.paste_into(0, column, "\n".join(f"s{index}" for index in range(rows)))
    _events(0.3)
    assert dict(writes) == {"experiment.json": 1, "recipe.yaml": 1}, dict(writes)  # not one of each per cell
    assert [panel.table.item(row, column).text() for row in range(rows)] == [f"s{index}" for index in range(rows)]
    names = {record.image_id: record.sample_name for record in shell.controller.experiment.images}
    assert sorted(names.values())[:rows] == sorted(f"s{index}" for index in range(rows))

    # A column of values in a column of the user's own: one save, and every value lands.
    panel.add_metadata_column("Genotype")
    genotype = _column(panel, "Genotype")
    writes.clear()
    panel.paste_into(0, genotype, "WT\nKO")
    _events(0.3)
    assert dict(writes) == {"experiment.json": 1, "recipe.yaml": 1}, dict(writes)
    assert [panel.table.item(row, genotype).text() for row in range(2)] == ["WT", "KO"]
    assert [record.user_metadata["Genotype"] for record in shell.controller.experiment.images[:2]] == ["WT", "KO"]


def test_typing_in_one_cell_still_saves_once(tmp_path: Path, open_window, writes):  # noqa: F811
    from cellquant.practice import create_practice_experiment

    controller = create_practice_experiment(tmp_path / "practice")
    controller.save()
    shell = open_window(controller.directory)
    panel = shell._experiment_panel
    column = _column(panel, "Sample name")
    writes.clear()
    panel.table.item(0, column).setText("typed")
    _events(0.2)
    assert dict(writes) == {"experiment.json": 1, "recipe.yaml": 1}, dict(writes)
    assert shell.controller.experiment.images[0].sample_name == "typed"
