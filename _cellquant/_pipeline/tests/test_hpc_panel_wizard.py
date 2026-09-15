"""Guided navigation for the HPC prep wizard."""

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qtpy import QtCore
from qtpy.QtTest import QTest
from qtpy.QtWidgets import QApplication, QWidget

from cellquant.hpc.acquisitions import AcquisitionRef
from cellquant.hpc.cluster_profiles import load_profile, validate_user_profile_fields
from cellquant.plugin.hpc_panel import HpcPrepPanel


class _SegmentOptions(QWidget):
    def __init__(self):
        super().__init__()
        self.overrides = {}

    def cellquant_overrides(self):
        return dict(self.overrides)


def _panel():
    app = QApplication.instance() or QApplication([])
    segment = _SegmentOptions()
    panel = HpcPrepPanel(None, SimpleNamespace(config=None), segment)
    return app, panel, segment


def _acquisition(*, error=None):
    return AcquisitionRef(
        acquisition_id="acq-1",
        source=Path("image.tif"),
        relative_source="image.tif",
        series=0,
        position=0,
        series_count=1,
        position_count=1,
        timepoint_count=1,
        channel_names=("DAPI",),
        shape=(1, 8, 8, 1),
        axes="ZYXC",
        spacing_um=(1.0, 0.5, 0.5),
        dtype="uint16",
        format="tiff",
        layout_id="layout-1",
        error=error,
    )


def _select_valid_acquisition(panel):
    panel.acquisitions = (_acquisition(),)
    panel.included = {"acq-1"}
    panel.layout_channels = {"layout-1": 0}


def _fill_valid_profile(panel):
    panel.account_edit.setText("amc-general")
    panel.project_root.setText("/projects/tester/cellquant")
    panel.scratch_root.setText("/scratch/alpine/tester/cellquant")
    panel.env_location.setText("/projects/tester/cellquant/envs/cellquant-hpc")


def _close(app, panel):
    panel.timer.stop()
    panel.pool.shutdown(wait=False, cancel_futures=True)
    panel.close()
    app.processEvents()


def test_wizard_starts_gated_with_stable_next_buttons():
    app, panel, _segment = _panel()
    try:
        assert [panel.steps.isTabEnabled(index) for index in range(4)] == [
            True,
            False,
            False,
            False,
        ]
        assert panel.select_next.objectName() == "select_next"
        assert panel.segment_next.objectName() == "segment_next"
        assert panel.profile_next.objectName() == "profile_next"
    finally:
        _close(app, panel)


def test_select_next_blocks_missing_or_invalid_selection():
    app, panel, _segment = _panel()
    try:
        panel.select_next.click()
        assert panel.steps.currentIndex() == 0
        assert not panel.steps.isTabEnabled(1)
        assert "Survey acquisitions" in panel.status.text()

        panel.acquisitions = (_acquisition(error="unreadable"),)
        panel.included = {"acq-1"}
        panel.select_next.click()
        assert panel.steps.currentIndex() == 0
        assert "without errors" in panel.status.text()

        panel.acquisitions = (_acquisition(),)
        panel.included = {"acq-1"}
        panel.layout_channels = {}
        panel.select_next.click()
        assert panel.steps.currentIndex() == 0
        assert "Choose a segmentation channel for layout layout-1" in panel.status.text()
    finally:
        _close(app, panel)


def test_wizard_advances_sequentially_after_each_page_is_valid():
    app, panel, _segment = _panel()
    try:
        _select_valid_acquisition(panel)
        panel.select_next.click()
        assert panel.steps.currentIndex() == 1
        assert panel.steps.isTabEnabled(1)
        assert not panel.steps.isTabEnabled(2)

        panel.segment_next.click()
        assert panel.steps.currentIndex() == 2
        assert panel.steps.isTabEnabled(2)
        assert not panel.steps.isTabEnabled(3)

        _fill_valid_profile(panel)
        panel.profile_next.click()
        assert panel.steps.currentIndex() == 3
        assert panel.steps.isTabEnabled(3)
        assert all(panel.steps.isTabEnabled(index) for index in range(4))
    finally:
        _close(app, panel)


def test_segment_next_blocks_unsupported_matrix_combination():
    app, panel, segment = _panel()
    try:
        _select_valid_acquisition(panel)
        panel.select_next.click()
        segment.overrides = {"engine": "v3"}
        panel.segment_next.click()
        assert panel.steps.currentIndex() == 1
        assert not panel.steps.isTabEnabled(2)
        assert "require parity tests" in panel.status.text()
    finally:
        _close(app, panel)


def test_profile_next_blocks_invalid_fields_then_accepts_valid_fields():
    app, panel, _segment = _panel()
    try:
        panel.steps.setTabEnabled(2, True)
        panel.steps.setCurrentIndex(2)
        panel.profile_next.click()
        assert panel.steps.currentIndex() == 2
        assert not panel.steps.isTabEnabled(3)
        assert "allocation/account is required" in panel.status.text()

        _fill_valid_profile(panel)
        panel.profile_next.click()
        assert panel.steps.currentIndex() == 3
        assert panel.steps.isTabEnabled(3)
    finally:
        _close(app, panel)


def test_profile_next_rechecks_segment_support(monkeypatch):
    app, panel, _segment = _panel()
    try:
        _select_valid_acquisition(panel)
        panel.select_next.click()
        panel.segment_next.click()
        assert panel.steps.currentIndex() == 2
        _fill_valid_profile(panel)

        monkeypatch.setattr(
            "cellquant.plugin.hpc_panel.matrix_status",
            lambda engine, mode, profile: (
                "unsupported",
                "selected profile no longer supports these segmentation settings",
            ),
        )
        panel.profile_next.click()
        assert panel.steps.currentIndex() == 2
        assert not panel.steps.isTabEnabled(3)
        assert "no longer supports" in panel.status.text()
    finally:
        _close(app, panel)


def test_profile_next_rejects_unreplaced_scratch_user_template():
    app, panel, _segment = _panel()
    try:
        panel.steps.setTabEnabled(2, True)
        panel.steps.setCurrentIndex(2)
        _fill_valid_profile(panel)
        panel.scratch_root.setText("/scratch/alpine/${USER}/cellquant")
        panel.profile_next.click()
        assert panel.steps.currentIndex() == 2
        assert not panel.steps.isTabEnabled(3)
        assert "scratch_root still contains a USER placeholder" in panel.status.text()
    finally:
        _close(app, panel)


def test_profile_validation_rejects_scratch_user_template_without_substrings():
    profile = load_profile()
    common = {
        "account": "amc-general",
        "qos": profile.default_qos,
        "gres": profile.default_gres,
        "walltime": "01:00:00",
        "project_root": "/projects/testuser/cellquant",
        "env_location": "/projects/testuser/cellquant/envs/cellquant-hpc",
    }
    errors = validate_user_profile_fields(
        profile,
        scratch_root="/scratch/alpine/${USER}/cellquant",
        **common,
    )
    assert any("scratch_root still contains a USER placeholder" in error for error in errors)

    errors = validate_user_profile_fields(
        profile,
        scratch_root="/scratch/alpine/testuser/cellquant",
        **common,
    )
    assert not any("USER placeholder" in error for error in errors)


def test_disabled_tabs_cannot_be_used_to_skip_steps():
    app, panel, _segment = _panel()
    try:
        panel.show()
        app.processEvents()
        QTest.mouseClick(
            panel.steps.tabBar(),
            QtCore.Qt.LeftButton,
            pos=panel.steps.tabBar().tabRect(3).center(),
        )
        assert panel.steps.currentIndex() == 0
        assert not panel.steps.isTabEnabled(3)
    finally:
        _close(app, panel)
