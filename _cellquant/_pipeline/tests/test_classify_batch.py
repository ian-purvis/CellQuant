"""Batch coexpression over complete *.cellquant runs."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import tifffile
import yaml

from cellquant.classify import ClassificationRecipe
from cellquant.classify.batch import (
    LABELS_NAME,
    LABELS_REVIEWED_NAME,
    REVIEW_JSON_NAME,
    CellQuantRunRef,
    classify_cellquant_run,
    discover_cellquant_runs,
    group_runs_by_layout,
    labels_path_for_run,
    read_run_labels,
    run_classify_batch,
    save_reviewed_labels,
)
from cellquant.cli import main


ROOT = Path(__file__).resolve().parents[1]


def _write_complete_run(
    parent: Path,
    *,
    name: str,
    source: Path,
    labels: np.ndarray,
) -> Path:
    run = parent / f"{name}.cellquant"
    run.mkdir(parents=True)
    raw = yaml.safe_load((ROOT / "sample_config.yaml").read_text())
    raw["io"]["axes_override"] = "ZYXC"
    raw["io"]["spacing_override_um"] = [1, 1, 1]
    raw["segment"]["mode"] = "volume_3d"
    (run / "config.json").write_text(json.dumps(raw), encoding="utf-8")
    (run / "provenance.json").write_text(
        json.dumps({"schema_version": 1, "run_id": "test-run", "source": str(source)}),
        encoding="utf-8",
    )
    (run / "status.json").write_text(json.dumps({"status": "complete"}), encoding="utf-8")
    tifffile.imwrite(
        run / LABELS_NAME,
        labels.astype(np.uint16),
        photometric="minisblack",
        metadata={"axes": "ZYX"},
    )
    return run


def _write_image(path: Path, data: np.ndarray) -> Path:
    tifffile.imwrite(path, data, photometric="minisblack", metadata={"axes": "ZYXC"})
    return path


def test_prefer_reviewed_labels_and_leave_original(tmp_path):
    source = _write_image(tmp_path / "img.tif", np.zeros((2, 4, 4, 1), np.uint16))
    original = np.ones((2, 4, 4), np.uint32)
    run = _write_complete_run(tmp_path, name="a", source=source, labels=original)
    curated = original.copy()
    curated[0, 0, 0] = 2
    saved = save_reviewed_labels(run, curated, note="manual")
    assert saved.name == LABELS_REVIEWED_NAME
    assert labels_path_for_run(run) == run / LABELS_REVIEWED_NAME
    np.testing.assert_array_equal(tifffile.imread(run / LABELS_NAME), original)
    np.testing.assert_array_equal(read_run_labels(run), curated)
    review = json.loads((run / REVIEW_JSON_NAME).read_text(encoding="utf-8"))
    assert review["labels_file"] == LABELS_REVIEWED_NAME
    assert review["supersedes"] == LABELS_NAME


def test_discover_only_complete_runs(tmp_path):
    source = _write_image(tmp_path / "img.tif", np.zeros((2, 4, 4, 2), np.uint16))
    labels = np.ones((2, 4, 4), np.uint32)
    good = _write_complete_run(tmp_path, name="good", source=source, labels=labels)
    bad = tmp_path / "bad.cellquant"
    bad.mkdir()
    (bad / LABELS_NAME).write_bytes(b"x")
    (bad / "status.json").write_text(json.dumps({"status": "failed"}), encoding="utf-8")
    found = discover_cellquant_runs(tmp_path)
    assert [r.path for r in found] == [good]


def test_layout_recipe_vs_image_override_and_summaries(tmp_path):
    img_a = np.zeros((1, 2, 2, 1), np.uint16)
    img_a[..., 0] = 10
    img_b = img_a.copy()
    source_a = _write_image(tmp_path / "a.tif", img_a)
    source_b = _write_image(tmp_path / "b.tif", img_b)
    labels = np.ones((1, 2, 2), np.uint32)
    run_a = _write_complete_run(tmp_path / "runs", name="a", source=source_a, labels=labels)
    run_b = _write_complete_run(tmp_path / "runs", name="b", source=source_b, labels=labels)

    runs = discover_cellquant_runs(tmp_path / "runs")
    assert len(runs) == 2
    assert {run_a.resolve(), run_b.resolve()} == {r.path.resolve() for r in runs}
    layout_ids = set(group_runs_by_layout(runs))
    assert len(layout_ids) == 1
    layout_id = next(iter(layout_ids))

    # expected_channel_names must match inspected TIFF names
    channel_names = list(runs[0].channel_names)
    layout_recipe = ClassificationRecipe(
        {
            "schema_version": 1,
            "name": "layout",
            "calibration_group": "g",
            "expected_channel_names": channel_names,
            "markers": [{"name": "M", "channel": 0, "low": 5, "positive_fraction": 0.5}],
        }
    )
    override = ClassificationRecipe(
        {
            "schema_version": 1,
            "name": "strict",
            "calibration_group": "g",
            "expected_channel_names": channel_names,
            "markers": [{"name": "M", "channel": 0, "low": 50, "positive_fraction": 0.5}],
        }
    )
    artifacts = run_classify_batch(
        runs,
        output_dir=tmp_path / "classify_out",
        layout_recipes={layout_id: layout_recipe},
        image_overrides={str(run_b.resolve()): override},
    )
    runs_df = pd.read_csv(artifacts["runs"])
    assert list(runs_df["status"]) == ["completed", "completed"]
    thresh = pd.read_csv(artifacts["thresholds_used"])
    sources = set(thresh["threshold_source"])
    assert "layout_recipe" in sources and "image_override" in sources
    markers = pd.read_csv(artifacts["marker_results"])
    by_image = {row.image_id: row.percentage for row in markers.itertuples()}
    assert by_image["a.tif"] == 100.0
    assert by_image["b.tif"] == 0.0
    summary = json.loads(Path(artifacts["batch_summary"]).read_text(encoding="utf-8"))
    assert summary["completed"] == 2 and summary["failed"] == 0


def test_missing_source_fails_row_and_continues(tmp_path):
    source = _write_image(tmp_path / "ok.tif", np.full((1, 2, 2, 1), 10, np.uint16))
    labels = np.ones((1, 2, 2), np.uint32)
    run_ok = _write_complete_run(tmp_path, name="ok", source=source, labels=labels)
    run_missing = _write_complete_run(
        tmp_path, name="missing", source=tmp_path / "gone.tif", labels=labels
    )
    layout_id = "Ltest"
    runs = (
        CellQuantRunRef(run_ok, str(source), layout_id, ("C1",), False, "1"),
        CellQuantRunRef(run_missing, str(tmp_path / "gone.tif"), layout_id, ("C1",), False, "2"),
    )
    recipe = ClassificationRecipe(
        {
            "schema_version": 1,
            "name": "t",
            "calibration_group": "g",
            "markers": [{"name": "C1", "channel": 0, "low": 5, "positive_fraction": 0.5}],
        }
    )
    artifacts = run_classify_batch(
        runs, output_dir=tmp_path / "out", layout_recipes={layout_id: recipe}
    )
    df = pd.read_csv(artifacts["runs"])
    assert set(df["status"]) == {"completed", "failed"}
    failed = df[df["status"] == "failed"].iloc[0]
    assert "gone.tif" in str(failed["error"]) or "FileNotFoundError" in str(failed["error"])


def test_classify_cellquant_run_uses_reviewed(tmp_path):
    image = np.zeros((1, 2, 2, 1), np.uint16)
    image[0, 0, 0, 0] = 20
    image[0, 1, 1, 0] = 20
    source = _write_image(tmp_path / "img.tif", image)
    labels = np.zeros((1, 2, 2), np.uint32)
    labels[0, 0, 0] = 1
    labels[0, 1, 1] = 2
    run = _write_complete_run(tmp_path, name="r", source=source, labels=labels)
    reviewed = labels.copy()
    reviewed[0, 1, 1] = 0
    save_reviewed_labels(run, reviewed)
    recipe = ClassificationRecipe(
        {
            "schema_version": 1,
            "name": "t",
            "calibration_group": "g",
            "markers": [{"name": "M", "channel": 0, "low": 10, "positive_fraction": 0.5}],
        }
    )
    pack, result, used = classify_cellquant_run(run, recipe, output_root=tmp_path / "packs")
    assert used == "reviewed"
    assert pack.is_dir()
    assert int(result.metadata["total_eligible"]) == 1


def test_cli_classify_batch(tmp_path, capsys):
    image = np.full((1, 2, 2, 1), 10, np.uint16)
    source = _write_image(tmp_path / "img.tif", image)
    _write_complete_run(
        tmp_path / "runs", name="one", source=source, labels=np.ones((1, 2, 2), np.uint32)
    )
    recipe = {
        "schema_version": 1,
        "name": "t",
        "calibration_group": "g",
        "markers": [{"name": "M", "channel": 0, "low": 5, "positive_fraction": 0.5}],
    }
    recipe_path = tmp_path / "recipe.json"
    recipe_path.write_text(json.dumps(recipe), encoding="utf-8")
    code = main(
        [
            "classify-batch",
            str(tmp_path / "runs"),
            str(tmp_path / "out"),
            "--recipe",
            str(recipe_path),
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["completed"] == 1
    assert Path(payload["output_dir"]).is_dir()
    assert (Path(payload["output_dir"]) / "runs.csv").is_file()


def test_conditional_export_rate_label_and_cancel_finalization(tmp_path):
    from cellquant.contracts import MutableCancellationToken

    # 10 cells: 2 B+ and both A+ → "A among B" is 100% of evaluable B+, not of all cells.
    image = np.zeros((1, 1, 10, 2), np.float32)
    image[0, 0, :, 0] = 1
    image[0, 0, :2, 1] = 1
    source = _write_image(tmp_path / "img.tif", image)
    labels = np.arange(1, 11, dtype=np.uint32).reshape(1, 1, 10)
    run = _write_complete_run(tmp_path, name="c", source=source, labels=labels)
    run_ref = CellQuantRunRef(run, str(source), "L", ("C1", "C2"), False, "1")
    recipe = ClassificationRecipe(
        {
            "schema_version": 1,
            "name": "t",
            "calibration_group": "g",
            "markers": [
                {"name": "A", "channel": 0, "low": 1, "positive_fraction": 0.5},
                {"name": "B", "channel": 1, "low": 1, "positive_fraction": 0.5},
            ],
            "queries": [
                {
                    "name": "A among B",
                    "positive": ["A"],
                    "denominator_positive": ["B"],
                }
            ],
        }
    )
    artifacts = run_classify_batch(
        (run_ref,), output_dir=tmp_path / "ok", layout_recipes={"L": recipe}
    )
    wide = pd.read_csv(artifacts["coexpression_summary"]).iloc[0]
    assert "A among B_pct_of_B+" in wide.index
    assert "A among B_pct_of_cells" not in wide.index
    assert wide["A among B_pct_of_B+"] == 100.0
    assert wide["A among B_numerator"] == 2
    assert wide["A among B_denominator"] == 2

    # Cancel before any run starts → cancelled/unstarted manifest written.
    token = MutableCancellationToken()
    token.cancel()
    second = _write_complete_run(tmp_path, name="d", source=source, labels=labels)
    refs = (
        CellQuantRunRef(run, str(source), "L", ("C1", "C2"), False, "1"),
        CellQuantRunRef(second, str(source), "L", ("C1", "C2"), False, "2"),
    )
    cancelled = run_classify_batch(
        refs, output_dir=tmp_path / "cancelled", layout_recipes={"L": recipe}, cancel=token
    )
    summary = json.loads(Path(cancelled["batch_summary"]).read_text(encoding="utf-8"))
    assert summary["status"] == "cancelled"
    assert summary["unstarted"] == 2
    assert summary["completed"] == 0
    statuses = set(pd.read_csv(cancelled["runs"])["status"])
    assert statuses == {"unstarted"}
