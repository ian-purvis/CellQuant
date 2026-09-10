from pathlib import Path

import numpy as np
import pytest

from cellquant.io._common import ImageMetadata
from cellquant.survey import (
    layout_id_for,
    materialize_run_config,
    read_assignments_csv,
    suggest_nuclear_channel,
    suggest_segmentation_channel,
    survey_folder,
    with_assignments,
    write_survey,
)


def _meta(names, spacing=(1.0, 0.5, 0.5)):
    return ImageMetadata(
        source=Path("x.nd2"),
        format="nd2",
        shape=(2, 8, 8, len(names)),
        dtype=np.dtype(np.uint16),
        axes="ZYXC",
        spacing_um=spacing,
        channel_names=tuple(names),
    )


def test_suggest_segmentation_channel_is_soft_and_not_dna_only():
    assert suggest_segmentation_channel(("488", "DAPI", "647")) == (1, "nuclear_dna")
    assert suggest_segmentation_channel(("488", "phalloidin")) == (1, "cytoplasmic_or_structure")
    assert suggest_segmentation_channel(("custom-A", "custom-B")) == (None, None)
    # Compatibility alias still returns only the index.
    assert suggest_nuclear_channel(("488", "DAPI", "647")) == 1


def test_survey_clusters_by_channel_layout(tmp_path):
    root = tmp_path / "in"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir(parents=True)
    paths = {
        root / "a" / "one.nd2": ("DAPI", "488"),
        root / "a" / "two.nd2": ("DAPI", "488"),
        root / "b" / "three.nd2": ("DAPI", "488", "647"),
    }
    for path in paths:
        path.write_bytes(b"x")

    def inspect(path):
        return _meta(paths[Path(path)])

    survey = survey_folder(root, recursive=True, suffixes=[".nd2"], inspect_fn=inspect)
    assert survey.file_count == 3
    assert len(survey.layouts) == 2
    by_count = {layout.channel_count: layout for layout in survey.layouts}
    assert by_count[2].file_count == 2
    assert by_count[2].suggested_channel == 0
    assert by_count[3].file_count == 1
    assert layout_id_for(("DAPI", "488")) == by_count[2].layout_id

    artifacts = write_survey(survey, tmp_path / "survey")
    assert artifacts["survey_json"].is_file()
    assert artifacts["assignments_csv"].is_file()
    config, config_path = materialize_run_config(
        tmp_path / "survey" / "cellquant_run_config.yaml",
        survey=survey,
        segment_channel=0,
        segment_overrides={
            "mode": "stitch_2d",
            "device": "auto",
            "allow_cpu_fallback": True,
            "stitch_threshold": 0.25,
        },
    )
    assert config_path.is_file()
    assert config.raw["preprocess"]["channel"] == 0
    assert config.raw["io"]["suffixes"] == [".nd2"]
    assert config.raw["segment"]["mode"] == "stitch_2d"
    assert config.raw["segment"]["device"] == "auto"
    assert config.raw["segment"]["allow_cpu_fallback"] is True
    assert config.raw["segment"]["stitch_threshold"] == 0.25
    assigned = with_assignments(survey, {by_count[2].layout_id: 0, by_count[3].layout_id: 2})
    channels = {layout.layout_id: layout.segment_channel for layout in assigned.layouts}
    assert channels[by_count[2].layout_id] == 0
    assert channels[by_count[3].layout_id] == 2


def test_materialize_clears_stitch_threshold_outside_stitch_mode(tmp_path):
    config, _ = materialize_run_config(
        tmp_path / "cellquant_run_config.yaml",
        segment_channel=0,
        segment_overrides={"mode": "volume_3d", "stitch_threshold": 0.5},
    )
    assert config.raw["segment"]["mode"] == "volume_3d"
    assert config.raw["segment"]["stitch_threshold"] == 0.0
    assert config.raw["segment"]["z_index"] is None


def test_read_assignments_csv_supports_1based_and_include(tmp_path):
    path = tmp_path / "assignments.csv"
    path.write_text(
        "layout_id,channel_names,file_count,segment_channel,segment_channel_1based,include\n"
        "L2-aaa,DAPI | 488,2,,1,yes\n"
        "L3-bbb,DAPI | 488 | 647,1,2,,no\n",
        encoding="utf-8",
    )
    assert read_assignments_csv(path) == {"L2-aaa": 0}


def test_with_assignments_rejects_out_of_range_channel(tmp_path):
    root = tmp_path / "in"
    root.mkdir()
    source = root / "one.nd2"
    source.write_bytes(b"x")

    def inspect(path):
        return _meta(("DAPI", "488"))

    survey = survey_folder(root, suffixes=[".nd2"], inspect_fn=inspect)
    layout_id = survey.layouts[0].layout_id
    with pytest.raises(IndexError):
        with_assignments(survey, {layout_id: 5})
