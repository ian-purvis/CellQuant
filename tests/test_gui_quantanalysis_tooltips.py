"""Every button, check box, combo box, spin box and menu action of the QuantAnalysis window and its dialogs has a tooltip. Needs Qt
(xvfb-run on Linux) and matplotlib."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("matplotlib")

from qtpy.QtWidgets import (  # noqa: E402
    QAbstractButton,
    QAbstractSpinBox,
    QComboBox,
    QDialog,
    QLineEdit,
    QMenu,
    QWidget,
)

from cellquant.stats import ColumnTable, sources  # noqa: E402
from tests.test_stats_import import make_results  # noqa: E402


@pytest.fixture
def window(qtbot):
    from cellquant.gui.quantanalysis.window import QuantAnalysisWindow

    window = QuantAnalysisWindow()
    qtbot.addWidget(window)
    window.show()
    return window


def _name(widget) -> str:
    chain, parent = [], widget.parentWidget()
    while parent is not None and len(chain) < 3:
        chain.append(type(parent).__name__)
        parent = parent.parentWidget()
    return f"{type(widget).__name__}({widget.text() if isinstance(widget, QAbstractButton) else ''!r} in {' < '.join(chain)})"


def _missing(root: QWidget) -> list[str]:
    out = []
    for widget in root.findChildren(QWidget):
        if not isinstance(widget, (QAbstractButton, QComboBox, QAbstractSpinBox, QLineEdit)):
            continue
        parent = widget.parentWidget()
        if isinstance(parent, (QMenu, QAbstractSpinBox, QComboBox)) or type(parent).__name__ in ("QTabBar", "QScrollBar"):
            continue  # a spin box or combo box's own editor
        if widget.objectName().startswith("qt_") and not isinstance(widget, QAbstractButton):
            continue
        if not widget.toolTip().strip():
            out.append(_name(widget))
    return out


def _menu_actions(window) -> list[str]:
    out = []
    for menu in window.menuBar().findChildren(QMenu):
        for action in menu.actions():
            if action.isSeparator() or action.menu() is not None or not action.text():
                continue
            if not action.toolTip().strip() or action.toolTip().strip() == action.text().replace("&", ""):
                out.append(f"{menu.title()} > {action.text()}")
    return out


def test_every_control_in_the_window_and_its_dialogs_has_a_tooltip(window, monkeypatch, tmp_path):
    from cellquant.gui.quantanalysis.project import DataSheet

    rng = np.random.default_rng(1)
    names = {"Ctrl": rng.normal(10, 2, 8), "Drug A": rng.normal(12, 2, 8), "Drug B": rng.normal(11, 2, 8), "Drug C": rng.normal(15, 2, 8)}
    data = window.project.add_data(DataSheet("Hand table", ColumnTable.from_columns(names, title="Cells per field")))
    window.select(data.id)
    problems: dict[str, list[str]] = {}
    seen: list[str] = []

    def sweep(label: str, root: QWidget) -> None:
        found = _missing(root)
        if found:
            problems.setdefault(label, []).extend(found)

    def fake_exec(self):
        seen.append(type(self).__name__)
        sweep(f"dialog {type(self).__name__}", self)
        return 0

    monkeypatch.setattr(QDialog, "exec", fake_exec)
    sweep("data table", window.stack.currentWidget())
    for kind in ("compare", "anova", "nested", "outliers", "twoway", "linreg", "curvefit", "xycorr", "contingency", "roc", "blandaltman",
                 "survival", "corrmatrix", "pca", "pstack"):
        window.analyze(kind)
    for kind in ("transform", "normalize", "fraction", "baseline"):
        window.process_data(kind)
    window.help_choose()
    window.project_defaults_dialog()
    window.select(data.id)
    source = sources.read_results_folder(str(make_results(tmp_path / "results")))
    window.import_dialog(source).exec()
    result = window.analyze("compare", {"test": "welch_t", "first": 0, "second": 1, "alternative": "two-sided", "ci": 0.95, "alpha": 0.05,
                                        "style": "classic"})
    window.select(result.id)
    sweep("results sheet", window.stack.currentWidget())
    window.select(data.id)
    window.new_graph("scatter")
    sweep("graph", window.stack.currentWidget())
    window.select(data.id)
    bar = window.new_graph("bar")
    from cellquant.gui.quantanalysis.graph_view import GraphExportDialog

    GraphExportDialog(window.views[bar.id], bar).exec()
    window.select(data.id)
    window.new_layout()
    sweep("page layout", window.stack.currentWidget())
    window.new_xy_table()
    sweep("XY table", window.stack.currentWidget())
    window.new_contingency_table()
    sweep("contingency table", window.stack.currentWidget())
    window.new_grouped_table()
    sweep("grouped table", window.stack.currentWidget())
    # The analyses that need another kind of table, the other dialogs, and the windows opened from the Help menu.
    window.new_xy_table()
    for kind in ("linreg", "xycorr", "curvefit"):
        window.analyze(kind)
    window.new_survival_table()
    window.analyze("survival")
    window.new_contingency_table()
    window.analyze("contingency")
    window.new_grouped_table()
    window.analyze("twoway")
    window.new_replicate_table()
    window.analyze("nested")
    from cellquant.gui.quantanalysis.dialogs import ImportCsvDialog
    from cellquant.gui.quantanalysis.format_panel import MagicDialog

    import pandas as pd

    ImportCsvDialog(window, pd.DataFrame({"value": [1.0, 2.0], "group": ["a", "b"]}), "x.csv").exec()
    window.select(bar.id)
    MagicDialog(window.views[bar.id]).exec()
    window.statistics_reference()
    sweep("statistics reference", window.statistics_reference())
    sweep("engine check", window.check_engine())
    window.select(None)
    sweep("welcome page", window.stack.currentWidget())
    for sheet_id, view in window.views.items():  # every page the window made, whether or not it is showing
        sweep(f"page {sheet_id}", view)
    assert len(seen) >= 22, seen  # the dialogs were really opened
    assert not problems, "\n".join(f"{where}: {len(found)} without a tooltip: {found}" for where, found in problems.items())
    assert not _menu_actions(window), _menu_actions(window)
