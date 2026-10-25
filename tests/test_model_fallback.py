"""A saved model the installed Cellpose engine does not know falls back to the engine default."""

from __future__ import annotations

from pathlib import Path

import pytest

from cellquant import engines
from cellquant.engines import CELLPOSE_CLASSIC, CELLPOSE_SAM, CLASSIC_MODELS, CellposeEngine
from cellquant.segmentation import CellposeBackend, SegmentationError, engine_model, model_is_valid

CLASSIC = CellposeEngine(True, "3.1.0", CELLPOSE_CLASSIC, CLASSIC_MODELS, "nuclei")
SAM = CellposeEngine(True, "4.0.0", CELLPOSE_SAM, ("cpsam",), "cpsam")


def test_engine_model_substitutes_unknown_names(tmp_path: Path):
    assert engine_model(CLASSIC, "cyto3") == "cyto3"
    assert engine_model(CLASSIC, "cpsam_v2") == "nuclei"
    assert engine_model(CLASSIC, None) == "nuclei"
    assert engine_model(SAM, "cpsam") == "cpsam"
    assert engine_model(SAM, "nuclei") == "cpsam"
    assert engine_model(SAM, "other") == "cpsam"
    custom = tmp_path / "my_model"
    custom.write_text("x")
    assert engine_model(CLASSIC, str(custom)) == str(custom)
    assert engine_model(SAM, str(custom)) == str(custom)
    assert model_is_valid(CLASSIC, "nuclei") and not model_is_valid(SAM, "nuclei")


@pytest.mark.parametrize(("engine", "saved", "used"), [(CLASSIC, "cpsam_v2", "nuclei"), (SAM, "cyto3", "cpsam")])
def test_describe_substitutes_instead_of_raising(monkeypatch, engine, saved, used):
    monkeypatch.setattr(engines, "cellpose_engine", lambda: engine)
    monkeypatch.setattr("cellquant.hardware.cellpose_precision", lambda parameters, key: "float32")
    described = CellposeBackend().describe({"model": saved, "engine": engine.key})
    assert described["model"] == used


def test_describe_still_rejects_an_engine_mismatch(monkeypatch):
    monkeypatch.setattr(engines, "cellpose_engine", lambda: CLASSIC)
    with pytest.raises(SegmentationError):
        CellposeBackend().describe({"model": "nuclei", "engine": CELLPOSE_SAM})
