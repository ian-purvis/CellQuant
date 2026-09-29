"""The sweep: many segmentation runs, each counted at several threshold settings."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cellquant import engines, sweep
from cellquant.sweep import (
    Design,
    Quantification,
    collate,
    counts_from_objects,
    find_images,
    plan_units,
    run_unit,
    score,
)
from tests.test_3d import _stack
from tests.test_cellpose_engines import _V3, _install_fake


def _folder(root: Path, count: int = 2) -> Path:
    for index in range(count):
        _stack(root / "Group A" / f"image {index + 1}.tif")
    return root


def _units(root: Path, engines_=("classical",), modes=("max_projection", "stitch_slices")):
    images = find_images(root, (".tif",))
    return images, plan_units(images, ("Channel 1", "Channel 2"), engines=engines_, modes=modes, channels=(0,))


def test_images_are_named_in_a_stable_order(tmp_path: Path):
    _folder(tmp_path / "data", 3)
    images = find_images(tmp_path / "data", (".tif",))
    assert [image.key for image in images] == ["img01", "img02", "img03"]
    assert [image.relative_path for image in images] == [f"Group A/image {n}.tif" for n in (1, 2, 3)]
    assert find_images(tmp_path / "data", (".nd2",)) == []


def test_cellpose_sam_in_3d_only_runs_when_asked(tmp_path: Path):
    _folder(tmp_path / "data", 1)
    images = find_images(tmp_path / "data", (".tif",))
    names = ("Channel 1", "Channel 2")
    everything = {(u.engine, u.mode) for u in plan_units(images, names)}
    assert ("cellpose4", "max_projection") in everything and ("cellpose4", "single_plane") in everything
    assert ("cellpose4", "stitch_slices") not in everything and ("cellpose4", "full_3d") not in everything
    assert ("cellpose3", "full_3d") in everything
    asked = {(u.engine, u.mode) for u in plan_units(images, names, include_slow=True)}
    assert ("cellpose4", "full_3d") in asked


def test_a_run_writes_its_files_and_is_skipped_when_done(tmp_path: Path):
    _folder(tmp_path / "data", 1)
    _images, units = _units(tmp_path / "data")
    out = tmp_path / "out"
    record = run_unit(units[1], Design(), out)  # stitch_slices
    folder = out / "units" / units[1].unit_id
    assert record["status"] == "done"
    for name in ("unit.json", "objects.csv.gz", "configs.csv", "curves.csv", "labels.npz"):
        assert (folder / name).exists(), name
    labels = np.load(folder / "labels.npz")["labels"]
    assert labels.ndim == 3 and labels.max() >= 2
    objects = pd.read_csv(folder / "objects.csv.gz")
    assert {"mean_channel_1", "mean_channel_2", "centroid_z", "z_slices", "unmeasured"} <= set(objects.columns)
    stamp = (folder / "unit.json").stat().st_mtime_ns
    assert run_unit(units[1], Design(), out)["status"] == "done"
    assert (folder / "unit.json").stat().st_mtime_ns == stamp  # not redone
    run_unit(units[1], Design(), out, force=True)
    assert (folder / "unit.json").stat().st_mtime_ns != stamp


def test_classical_runs_ignore_cell_probability(tmp_path: Path):
    _folder(tmp_path / "data", 1)
    _images, units = _units(tmp_path / "data", modes=("max_projection",))
    out = tmp_path / "out"
    run_unit(units[0], Design(), out)
    rows = pd.read_csv(out / "units" / units[0].unit_id / "configs.csv")
    assert len(rows) == 10 and not rows["cellprob_applies"].any()
    for _name, group in rows.groupby("marker_set"):
        assert group["n_objects"].nunique() == 1  # every cell-probability setting gives the same objects


def test_collate_counts_agree_with_the_pipeline_and_cover_ten_configurations(tmp_path: Path):
    _folder(tmp_path / "data", 2)
    _images, units = _units(tmp_path / "data")
    out = tmp_path / "out"
    for unit in units:
        assert run_unit(unit, Design(), out)["status"] == "done"
    written = collate(out)
    manifest = pd.read_csv(written["runs_manifest"])
    assert manifest["counts_match_pipeline"].all()  # counts from the saved objects == CellQuant's own counts
    long = pd.read_csv(written["results_long"])
    assert len(long) == len(units) * 10
    assert long.groupby("unit_id").size().eq(10).all()
    assert set(long["config_id"]) == {f"C{n:02d}" for n in range(1, 11)}
    extended = pd.read_csv(written["results_extended"])
    assert len(extended) == len(units) * 25
    natural = pd.read_csv(written["natural_thresholds"])
    assert set(natural["domain"]) == {"projection", "stack"}
    assert {"delta_channel_1", "delta_channel_2", "n_channel_1", "pct_channel_1_and_channel_2"} <= set(long.columns)
    strict = long[long["marker_set"] == "strict"]
    lenient = long[long["marker_set"] == "lenient"]
    # A stricter threshold never finds more positive nuclei.
    assert (strict["n_channel_2"].to_numpy() <= lenient["n_channel_2"].to_numpy()).all()


def test_thresholds_can_be_set_by_hand_and_change_without_rerunning(tmp_path: Path):
    _folder(tmp_path / "data", 1)
    _images, units = _units(tmp_path / "data", modes=("max_projection",))
    out = tmp_path / "out"
    run_unit(units[0], Design(), out)
    low = pd.read_csv(collate(out, natural={"channel_1": 10, "channel_2": 10})["results_long"])
    high = pd.read_csv(collate(out, natural={"channel_1": 5000, "channel_2": 5000})["results_long"])
    assert low["n_channel_2"].max() > 0 and high["n_channel_2"].max() == 0
    assert (low["n_objects"] == high["n_objects"]).all()
    table = pd.read_csv(out / "natural_thresholds.csv")
    assert (table["how"] == "set by hand").all()


def test_objects_that_cannot_be_measured_are_left_out_of_every_count():
    objects = pd.DataFrame(
        {
            "mean_a": [10.0, 500.0, np.nan, 900.0],
            "mean_b": [10.0, 10.0, 900.0, 900.0],
            "unmeasured": [False, False, True, False],
        }
    )
    row = counts_from_objects(objects, ["a", "b"], {"a": 100.0, "b": 100.0})
    assert row["n_objects"] == 3 and row["n_unmeasured"] == 1
    assert row["n_a"] == 2 and row["n_b"] == 1 and row["n_a_and_b"] == 1
    assert "n_all_markers" not in row  # with two channels the pair is already "all markers"
    three = counts_from_objects(objects.assign(mean_c=[10.0, 500.0, 500.0, 500.0]), ["a", "b", "c"], {"a": 100, "b": 100, "c": 100})
    assert three["n_all_markers"] == 1 and three["n_a_and_c"] == 2
    assert row["pct_a"] == pytest.approx(200 / 3, abs=0.001)
    equal = counts_from_objects(objects, ["a", "b"], {"a": 500.0, "b": 100.0})
    assert equal["n_a"] == 1  # equal to the threshold is negative, as in CellQuant


def test_scoring_ranks_the_configuration_that_matches_the_hand_counts(tmp_path: Path):
    _folder(tmp_path / "data", 2)
    _images, units = _units(tmp_path / "data", modes=("max_projection", "stitch_slices"))
    out = tmp_path / "out"
    for unit in units:
        run_unit(unit, Design(), out)
    long = pd.read_csv(collate(out)["results_long"])
    template = pd.read_csv(out / "hand_counts_template.csv") if (out / "hand_counts_template.csv").exists() else None
    assert template is None  # run_unit alone does not write the image tables; the command line does
    target = long[(long["mode"] == "max_projection") & (long["config_id"] == "C04")]
    hand = pd.DataFrame(
        {
            "image_key": target["image_key"].to_numpy(),
            "total_nuclei": target["n_objects"].to_numpy(),
            "channel_2_positive": target["n_channel_2"].to_numpy(),
            "channel_2_percent": target["pct_channel_2"].to_numpy(),
        }
    )
    hand_path = tmp_path / "hand.csv"
    hand.to_csv(hand_path, index=False)
    result = score(out, hand_path)
    ranked = pd.read_csv(result["by_configuration"])
    best = ranked.iloc[0]
    assert best["mean_relative_error"] == 0 and best["mean_error_percentage_points"] == 0
    assert best["mode"] == "max_projection" and best["n_images"] == 2
    assert (ranked["mean_relative_error"].diff().dropna() >= 0).all()  # sorted best first
    assert set(pd.read_csv(result["best_per_mode"])["mode"]) == {"max_projection", "stitch_slices"}
    # Percentages alone also work, and are scored in percentage points.
    hand[["image_key", "channel_2_percent"]].to_csv(hand_path, index=False)
    only_percent = pd.read_csv(score(out, hand_path)["by_configuration"])
    assert "mean_relative_error" not in only_percent.columns and only_percent.iloc[0]["mean_error_percentage_points"] == 0
    pd.DataFrame({"image_key": ["img01"], "total_nuclei": [np.nan]}).to_csv(hand_path, index=False)
    with pytest.raises(ValueError, match="no filled-in counts"):
        score(out, hand_path)
    pd.DataFrame({"image_key": ["nope"], "total_nuclei": [5]}).to_csv(hand_path, index=False)
    with pytest.raises(ValueError, match="None of the image_key"):
        score(out, hand_path)


def test_the_hand_count_template_follows_the_channel_names(tmp_path: Path):
    _folder(tmp_path / "data", 2)
    sweep.write_image_tables(find_images(tmp_path / "data", (".tif",)), tmp_path / "out")
    template = pd.read_csv(tmp_path / "out" / "hand_counts_template.csv")
    assert list(template.columns) == [
        "image_key", "relative_path", "total_nuclei",
        "channel_1_positive", "channel_2_positive", "channel_1_percent", "channel_2_percent",
    ]
    assert template["image_key"].tolist() == ["img01", "img02"]
    images = pd.read_csv(tmp_path / "out" / "images.csv")
    assert images.loc[0, "channels"] == "Channel 1, Channel 2" and images.loc[0, "z_planes"] == 9


def test_the_results_folder_cannot_be_inside_the_image_folder(tmp_path: Path, capsys):
    _folder(tmp_path / "data", 1)
    code = sweep.main(["run", "--input", str(tmp_path / "data"), "--output", str(tmp_path / "data" / "results"), "--types", "tiff"])
    assert code == 2
    assert "inside the image folder" in capsys.readouterr().err
    assert not (tmp_path / "data" / "results").exists()


def test_a_dry_run_lists_runs_and_writes_nothing(tmp_path: Path, capsys):
    _folder(tmp_path / "data", 1)
    code = sweep.main(
        ["run", "--input", str(tmp_path / "data"), "--output", str(tmp_path / "out"), "--types", "tiff", "--engines", "classical", "--dry-run"]
    )
    assert code == 0
    text = capsys.readouterr().out
    assert "1 images, 8 segmentation runs, 10 configurations each." in text
    assert "img1_channel_1_classical_max_projection" in text.replace("img01", "img1")
    assert not (tmp_path / "out").exists()


def test_an_engine_that_is_not_installed_is_not_run(tmp_path: Path, monkeypatch):
    _folder(tmp_path / "data", 1)
    _images, units = _units(tmp_path / "data", engines_=("cellpose4",), modes=("max_projection",))
    monkeypatch.setattr(engines, "cellpose_engine", lambda: engines.CellposeEngine(installed=False))
    record = run_unit(units[0], Design(), tmp_path / "out")
    assert record["status"] == "not_run" and "not installed" in record["reason"]
    assert not (tmp_path / "out" / "units" / units[0].unit_id).exists()


def test_cellpose_runs_are_repeated_at_every_cell_probability(tmp_path: Path, monkeypatch):
    _install_fake(tmp_path, monkeypatch, "3.1.1.3", _V3)
    from cellquant import segmentation

    segmentation._MODELS.clear()
    _folder(tmp_path / "data", 1)
    _images, units = _units(tmp_path / "data", engines_=("cellpose3",), modes=("max_projection",))
    out = tmp_path / "out"
    record = run_unit(units[0], Design(), out)
    assert record["status"] == "done", record.get("error")
    assert sorted(record["levels"]) == [f"cellprob {v}" for v in ("-1", "-2", "0", "1", "2")]
    objects = pd.read_csv(out / "units" / units[0].unit_id / "objects.csv.gz")
    assert sorted(objects["cellprob"].unique()) == [-2.0, -1.0, 0.0, 1.0, 2.0]
    rows = pd.read_csv(out / "units" / units[0].unit_id / "configs.csv")
    assert rows["cellprob_applies"].all() and sorted(rows["cellprob"].unique()) == [-2.0, -1.0, 0.0, 1.0, 2.0]
    assert (out / "units" / units[0].unit_id / "labels.npz").exists()
    assert record["engine_details"]["cellpose_version"] == "3.1.1.3"
    segmentation._MODELS.clear()


def test_a_failed_run_is_recorded_and_does_not_stop_the_rest(tmp_path: Path, monkeypatch):
    _folder(tmp_path / "data", 1)
    _images, units = _units(tmp_path / "data", modes=("max_projection",))

    def broken(*_args, **_kwargs):
        raise RuntimeError("out of memory")

    monkeypatch.setattr(sweep, "segment_channel", broken)
    record = run_unit(units[0], Design(), tmp_path / "out", log=lambda *_a: None)
    assert record["status"] == "failed" and "out of memory" in record["error"]
    saved = json.loads((tmp_path / "out" / "units" / units[0].unit_id / "unit.json").read_text())
    assert saved["status"] == "failed"
    monkeypatch.undo()
    assert run_unit(units[0], Design(), tmp_path / "out")["status"] == "done"  # a failed run is retried
    assert Quantification().official_configs()[0][0] == "C01"


def test_the_report_summarises_methods_and_settings(tmp_path: Path):
    from cellquant.sweep_report import build_report

    _folder(tmp_path / "data", 2)
    _images, units = _units(tmp_path / "data", modes=("max_projection", "single_plane", "stitch_slices"))
    out = tmp_path / "out"
    for unit in units:
        run_unit(unit, Design(), out)
    collate(out)
    written = build_report(out)
    for name in ("summary_by_method", "cellprob_sensitivity", "threshold_sensitivity", "agreement_by_image", "marker_agreement", "report"):
        assert written[name].exists(), name
    summary = pd.read_csv(written["summary_by_method"])
    assert set(summary["method"]) == {"Classical, 2D projection", "Classical, 2D one slice", "Classical, 3D linked slices"}
    assert (summary["runs"] == 2).all()
    pairs = pd.read_csv(written["agreement_by_image"])
    assert len(pairs) == 2 * 3  # two images, three method pairs
    assert pairs["agreement"].between(0, 1).all()
    thresholds = pd.read_csv(written["threshold_sensitivity"])
    # A higher threshold never gives a higher percentage of positive nuclei.
    for _key, group in thresholds.groupby(["method", "marker"]):
        assert group.sort_values("multiplier")["pct_positive_median"].is_monotonic_decreasing
    text = written["report"].read_text(encoding="utf-8")
    assert "How the segmentation modes compare" in text and "Classical, 2D projection" in text
    assert (out / "report" / "nuclei_per_image.png").exists()


def test_the_hand_over_copy_gathers_runs_into_a_few_files(tmp_path: Path):
    from cellquant.sweep_report import build_report

    _folder(tmp_path / "data", 2)
    images, units = _units(tmp_path / "data", modes=("max_projection", "stitch_slices"))
    out = tmp_path / "out"
    sweep.write_image_tables(images, out)
    (out / "design.json").write_text(json.dumps(Design().as_dict()), encoding="utf-8")
    for unit in units:
        run_unit(unit, Design(), out)
    collate(out)
    build_report(out)
    dest = sweep.export_bundle(out, tmp_path / "copy")
    names = sorted(path.name for path in (dest / "objects").iterdir())
    assert names == ["objects_classical_max_projection.csv.gz", "objects_classical_stitch_slices.csv.gz"]
    table = pd.read_csv(dest / "objects" / names[0])
    assert set(table["image_key"]) == {"img01", "img02"} and set(table["unit_id"]) == {u.unit_id for u in units if u.mode == "max_projection"}
    labels = np.load(dest / "labels" / "labels_classical_stitch_slices_channel_1.npz")
    assert sorted(labels.files) == sorted(u.unit_id for u in units if u.mode == "stitch_slices")
    assert labels[labels.files[0]].ndim == 3
    for name in ("results_long.csv", "results_extended.csv", "hand_counts_template.csv", "runs_manifest.csv"):
        assert (dest / name).exists(), name
    assert (dest / "report" / "report.md").exists()
    assert not (dest / "units").exists()
    readme = (dest / "README.md").read_text(encoding="utf-8")
    assert "Classical, 2D projection" in readme and "C10" in readme and "hand_counts_template.csv" in readme
    assert "Cellpose-SAM, 3D whole volume" in readme  # listed as not run, with the reason
    assert "not run" in readme.lower()
    design = json.loads((dest / "design.json").read_text(encoding="utf-8"))
    assert "quantification.json" in design["note"]
