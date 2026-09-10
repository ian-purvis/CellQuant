from copy import deepcopy
from pathlib import Path

import pytest

from cellquant.config import RunConfig, load_config


REFERENCE = Path(__file__).parents[1] / "reference_config.yaml"


def test_reference_configuration_is_complete_and_explicit():
    config = load_config(REFERENCE)
    assert config.raw["segment"]["model_type"] is None
    assert config.raw["segment"]["diam_mean"] is None
    assert config.raw["segment"]["nchan"] is None
    assert config.raw["preprocess"]["rescale"]["boundary"] == "constant"
    assert config.raw["preprocess"]["denoise"]["boundary"] == "reflect"
    assert config.raw["segment"]["stitch_threshold"] == 0.0
    assert len(config.fingerprint) == 64


@pytest.mark.parametrize("section", ["preprocess", "measure", "viz"])
def test_scientific_sections_cannot_be_empty(section):
    raw = deepcopy(load_config(REFERENCE).raw)
    raw[section] = {}
    with pytest.raises(ValueError, match="cannot be empty"):
        RunConfig(raw)


def test_preprocess_subsections_require_explicit_boundary_contract():
    raw = deepcopy(load_config(REFERENCE).raw)
    del raw["preprocess"]["rescale"]["boundary"]
    with pytest.raises(ValueError, match="preprocess.rescale omits"):
        RunConfig(raw)


@pytest.mark.parametrize(
    ("section", "remaining"),
    [
        ("io", {}),
        ("postprocess", {}),
        ("runtime", {"seed": 0}),
        ("viz", {"label_seed": 0}),
        ("measure", {"channels": "all"}),
    ],
)
def test_all_consumed_or_defaulted_section_parameters_are_required(section, remaining):
    raw = deepcopy(load_config(REFERENCE).raw)
    raw[section] = remaining
    with pytest.raises(ValueError, match=f"{section} config omits"):
        RunConfig(raw)


def test_suffixes_must_be_supported_and_non_empty():
    raw = deepcopy(load_config(REFERENCE).raw)
    raw["io"]["suffixes"] = []
    with pytest.raises(ValueError, match="io.suffixes"):
        RunConfig(raw)
    raw = deepcopy(load_config(REFERENCE).raw)
    raw["io"]["suffixes"] = [".png"]
    with pytest.raises(ValueError, match="io.suffixes"):
        RunConfig(raw)
    raw = deepcopy(load_config(REFERENCE).raw)
    raw["io"]["suffixes"] = ["ND2", ".tif"]
    assert RunConfig(raw).raw["io"]["suffixes"] == [".nd2", ".tif"]


def test_volume_mode_rejects_a_serialized_but_ineffective_stitch_threshold():
    raw = deepcopy(load_config(REFERENCE).raw)
    raw["segment"]["stitch_threshold"] = 0.25
    with pytest.raises(ValueError, match="must be 0.0 unless"):
        RunConfig(raw)


def test_z_index_is_required_and_mode_specific():
    raw = deepcopy(load_config(REFERENCE).raw)
    del raw["segment"]["z_index"]
    with pytest.raises(ValueError, match="z_index"):
        RunConfig(raw)

    for invalid in (None, -1, 1.5, True):
        raw = deepcopy(load_config(REFERENCE).raw)
        raw["segment"].update(mode="single_plane_2d", z_index=invalid)
        with pytest.raises(ValueError, match="non-negative integer"):
            RunConfig(raw)

    raw = deepcopy(load_config(REFERENCE).raw)
    raw["segment"].update(mode="single_plane_2d", z_index=2)
    assert RunConfig(raw).raw["segment"]["z_index"] == 2

    for mode in ("volume_3d", "stitch_2d", "max_projection_2d"):
        raw = deepcopy(load_config(REFERENCE).raw)
        # stitch_2d requires a linking threshold; set one so z_index is checked.
        updates = {"mode": mode, "z_index": 0}
        if mode == "stitch_2d":
            updates["stitch_threshold"] = 0.25
        raw["segment"].update(updates)
        with pytest.raises(ValueError, match="must be null"):
            RunConfig(raw)


@pytest.mark.parametrize("threshold", [0, 0.0, -0.25, -1, 1.5])
def test_stitch_mode_rejects_thresholds_that_cannot_link_planes(threshold):
    raw = deepcopy(load_config(REFERENCE).raw)
    raw["segment"].update(mode="stitch_2d", stitch_threshold=threshold)
    with pytest.raises(ValueError, match="stitch_threshold"):
        RunConfig(raw)


def test_stitch_mode_accepts_a_linking_threshold():
    raw = deepcopy(load_config(REFERENCE).raw)
    raw["segment"].update(mode="stitch_2d", stitch_threshold=0.25)
    assert RunConfig(raw).raw["segment"]["stitch_threshold"] == 0.25


def test_postprocess_accepts_optional_area_thresholds():
    raw = deepcopy(load_config(REFERENCE).raw)
    raw["postprocess"].update(min_area_um2=0.5, max_area_um2=10.0)
    config = RunConfig(raw)
    assert config.raw["postprocess"]["min_area_um2"] == 0.5
    assert config.raw["postprocess"]["max_area_um2"] == 10.0


def test_postprocess_rejects_unknown_keys():
    raw = deepcopy(load_config(REFERENCE).raw)
    raw["postprocess"]["min_area_px2"] = 1.0
    with pytest.raises(ValueError, match="unknown parameter"):
        RunConfig(raw)


def test_diameter_px_may_be_null_for_native_cellpose_sam_size():
    raw = deepcopy(load_config(REFERENCE).raw)
    raw["segment"]["diameter_px"] = None
    assert RunConfig(raw).raw["segment"]["diameter_px"] is None
    raw["segment"]["diameter_px"] = 0
    with pytest.raises(ValueError, match="diameter_px"):
        RunConfig(raw)
