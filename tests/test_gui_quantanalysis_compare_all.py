"""Every test in the Compare two columns dialog's list runs from the options the dialog itself produces, at each choice of tails.
Needs Qt (xvfb-run on Linux) and matplotlib."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("matplotlib")

from cellquant.gui.quantanalysis.dialogs import CompareDialog  # noqa: E402
from cellquant.gui.quantanalysis.project import COMPARE_TESTS, DataSheet  # noqa: E402
from cellquant.stats import ColumnTable  # noqa: E402


@pytest.fixture
def window(qtbot):
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    window = QuantAnalysisWindow()
    qtbot.addWidget(window)
    window.show()
    return window


@pytest.mark.parametrize("alternative", ["two-sided", "greater", "less"])
@pytest.mark.parametrize("test", list(COMPARE_TESTS))
def test_every_compare_test_runs_from_the_dialogs_own_options(window, test, alternative):
    rng = np.random.default_rng(4)
    table = ColumnTable.from_columns({"Control": rng.normal(10, 2, 9), "Treated": rng.normal(12, 2, 9)}, title="Cells per field")
    data = window.project.add_data(DataSheet("Data", table))
    window.select(data.id)
    dialog = CompareDialog(window, table, sheet=data)
    options = dialog.options()
    dialog.set_options({**options, "test": test, "alternative": alternative})
    assert dialog.test.currentData() == test
    options = dialog.options()
    assert "ci" in options  # the dialog always sends it; every test must cope
    sheet = window.analyze("compare", options)
    assert sheet is not None and not sheet.error, sheet.error
    assert sheet.result is not None and sheet.result.sections
