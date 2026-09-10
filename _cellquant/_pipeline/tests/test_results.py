import json
import numpy as np
import pandas as pd
from cellquant.results import fingerprint, can_resume, save_result


def _tables():
    return {
        "objects": pd.DataFrame({"label": [1]}),
        "measurements_long": pd.DataFrame({"label": [1]}),
        "overlaps": pd.DataFrame({"label": [1]}),
        "coexpression_summary": pd.DataFrame({"object_count": [1]}),
    }


def _source_and_fp(tmp_path):
    source = tmp_path / "input.tif"
    source.write_bytes(b"image")
    return source, fingerprint(source, {"z_selection": "single:1"})


def test_marker_only_is_not_resumable(tmp_path):
    _, fp = _source_and_fp(tmp_path)
    out = tmp_path / "out"; out.mkdir()
    (out / "result.json").write_text(json.dumps({"status": "complete", "fingerprint": fp}))
    assert not can_resume(out, fp)


def test_missing_required_artifact_is_not_resumable(tmp_path):
    _, fp = _source_and_fp(tmp_path)
    out = tmp_path / "out"
    save_result(out, np.array([[0, 1]], dtype=np.uint16), _tables(),
                {"fingerprint": fp}, (None, None, None))
    (out / "objects.csv").unlink()
    assert not can_resume(out, fp)


def test_valid_saved_result_is_resumable_and_corruption_is_rejected(tmp_path):
    source, fp = _source_and_fp(tmp_path)
    out = tmp_path / "out"
    save_result(out, np.array([[0, 1]], dtype=np.uint16), _tables(),
                {"fingerprint": fp}, (None, None, None))
    assert can_resume(out, fp)
    assert not can_resume(out, fingerprint(source, {"z_selection": "max:1-1"}))
    (out / "labels.tif").write_bytes(b"not a tiff")
    assert not can_resume(out, fp)
