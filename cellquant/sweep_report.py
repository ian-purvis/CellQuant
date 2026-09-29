"""Summaries of a finished sweep: how the methods and Z modes compare, and how much the
counts depend on the cell-probability and marker-threshold settings.

These summaries need no hand counts. They show how far the methods agree with each other and
how much each setting matters. With hand counts, ``python -m cellquant.sweep score`` ranks
every run and configuration against them.

    python -m cellquant.sweep report --output RESULTS
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ENGINE_NAMES = {"classical": "Classical", "cellpose3": "Cellpose 3", "cellpose4": "Cellpose-SAM"}
MODE_NAMES = {
    "max_projection": "2D projection",
    "single_plane": "2D one slice",
    "stitch_slices": "3D linked slices",
    "full_3d": "3D whole volume",
}
MATCH_DISTANCE_UM = 3.0
DEFAULT_CELLPROB = 0.0


def method_name(engine: str, mode: str) -> str:
    return f"{ENGINE_NAMES.get(engine, engine)}, {MODE_NAMES.get(mode, mode)}"


def _with_method(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame["method"] = [method_name(e, m) for e, m in zip(frame["engine"], frame["mode"], strict=True)]
    return frame


def _order(frame: pd.DataFrame) -> pd.DataFrame:
    engines = list(ENGINE_NAMES)
    modes = list(MODE_NAMES)
    key = frame["method"].map(
        {
            method_name(e, m): engines.index(e) * 10 + modes.index(m)
            for e in engines
            for m in modes
        }
    )
    return frame.assign(_key=key).sort_values("_key", kind="stable").drop(columns="_key")


def _markers(root: Path) -> list[str]:
    return list(dict.fromkeys(pd.read_csv(root / "natural_thresholds.csv")["channel"].tolist()))


def _default_rows(frame: pd.DataFrame, multiplier: float | None = 1.0) -> pd.DataFrame:
    """One row per run: the default cell probability (classical has a single setting)."""

    rows = frame[np.isclose(frame["cellprob"], DEFAULT_CELLPROB)]
    if multiplier is not None:
        rows = rows[np.isclose(rows["multiplier"], multiplier)]
    return rows


# --- tables ---------------------------------------------------------------------------------------------


def summary_by_method(extended: pd.DataFrame, manifest: pd.DataFrame, markers: list[str]) -> pd.DataFrame:
    rows = _with_method(_default_rows(extended))
    seconds = manifest[manifest["status"] == "done"][["unit_id", "wall_seconds"]]
    rows = rows.merge(seconds, on="unit_id", how="left")
    aggregations = {
        "runs": ("unit_id", "nunique"),
        "nuclei_median": ("n_objects", "median"),
        "nuclei_min": ("n_objects", "min"),
        "nuclei_max": ("n_objects", "max"),
        "seconds_per_run_median": ("wall_seconds", "median"),
    }
    for marker in markers:
        aggregations[f"pct_{marker}_median"] = (f"pct_{marker}", "median")
    table = rows.groupby(["method", "segmentation_channel_name"], as_index=False).agg(**aggregations)
    return _order(table).reset_index(drop=True)


def cellprob_sensitivity(extended: pd.DataFrame) -> pd.DataFrame:
    rows = _with_method(extended[extended["cellprob_applies"] & np.isclose(extended["multiplier"], 1.0)])
    if rows.empty:
        return pd.DataFrame()
    base = rows[np.isclose(rows["cellprob"], DEFAULT_CELLPROB)].set_index(["unit_id"])["n_objects"]
    rows = rows.assign(nuclei_vs_default=rows["n_objects"] / rows["unit_id"].map(base).replace(0, np.nan))
    table = rows.groupby(["method", "segmentation_channel_name", "cellprob"], as_index=False).agg(
        nuclei_median=("n_objects", "median"), nuclei_vs_default=("nuclei_vs_default", "median")
    )
    return _order(table).reset_index(drop=True)


def threshold_sensitivity(extended: pd.DataFrame, markers: list[str]) -> pd.DataFrame:
    rows = _with_method(_default_rows(extended, multiplier=None))
    frames = []
    for marker in markers:
        part = rows.groupby(["method", "segmentation_channel_name", "multiplier"], as_index=False).agg(
            pct_positive_median=(f"pct_{marker}", "median")
        )
        part.insert(2, "marker", marker)
        frames.append(part)
    return _order(pd.concat(frames, ignore_index=True)).reset_index(drop=True)


def _match(first: pd.DataFrame, second: pd.DataFrame, radius: float) -> int:
    """Nuclei found by both: mutual nearest neighbours in the image plane within ``radius`` µm."""

    from scipy.spatial import cKDTree

    if first.empty or second.empty:
        return 0
    a = first[["centroid_x", "centroid_y"]].to_numpy(dtype=float)
    b = second[["centroid_x", "centroid_y"]].to_numpy(dtype=float)
    distance_ab, index_ab = cKDTree(b).query(a)
    _distance_ba, index_ba = cKDTree(a).query(b)
    mutual = index_ba[index_ab] == np.arange(len(a))
    return int(np.count_nonzero(mutual & (distance_ab <= radius)))


def agreement(root: Path, manifest: pd.DataFrame) -> pd.DataFrame:
    """For every image and segmented channel, how many nuclei each pair of methods share."""

    done = manifest[manifest["status"] == "done"]
    objects: dict[str, pd.DataFrame] = {}
    for record in done.itertuples():
        table = pd.read_csv(root / "units" / record.unit_id / "objects.csv.gz", usecols=["cellprob", "centroid_x", "centroid_y"])
        if record.engine != "classical":
            table = table[np.isclose(table["cellprob"], DEFAULT_CELLPROB)]
        objects[record.unit_id] = table
    rows = []
    for (image, channel), group in done.groupby(["image_key", "segmentation_channel_name"]):
        members = list(group.itertuples())
        for i, first in enumerate(members):
            for second in members[i + 1 :]:
                a, b = objects[first.unit_id], objects[second.unit_id]
                shared = _match(a, b, MATCH_DISTANCE_UM)
                rows.append(
                    {
                        "image_key": image,
                        "segmentation_channel_name": channel,
                        "method_a": method_name(first.engine, first.mode),
                        "method_b": method_name(second.engine, second.mode),
                        "nuclei_a": len(a),
                        "nuclei_b": len(b),
                        "shared": shared,
                        "share_of_a": shared / len(a) if len(a) else np.nan,
                        "share_of_b": shared / len(b) if len(b) else np.nan,
                        "agreement": 2 * shared / (len(a) + len(b)) if len(a) + len(b) else np.nan,
                    }
                )
    return pd.DataFrame(rows)


def marker_agreement(extended: pd.DataFrame, markers: list[str]) -> pd.DataFrame:
    """Across images, how well do two methods agree on the percent of positive nuclei? (Pearson r)"""

    rows = _with_method(_default_rows(extended))
    out = []
    for marker in markers:
        wide = rows.pivot_table(index=["image_key", "segmentation_channel_name"], columns="method", values=f"pct_{marker}")
        columns = list(wide.columns)
        for i, first in enumerate(columns):
            for second in columns[i + 1 :]:
                pair = wide[[first, second]].dropna()
                if len(pair) < 3 or pair[first].std() == 0 or pair[second].std() == 0:
                    continue
                out.append(
                    {
                        "marker": marker,
                        "method_a": first,
                        "method_b": second,
                        "n_pairs": len(pair),
                        "pearson_r": float(pair[first].corr(pair[second])),
                        "median_difference_points": float((pair[first] - pair[second]).median()),
                    }
                )
    return pd.DataFrame(out)


# --- figures ---------------------------------------------------------------------------------------------


def _figures(root: Path, out: Path, extended: pd.DataFrame, manifest: pd.DataFrame, markers: list[str], pairs: pd.DataFrame, summary: pd.DataFrame) -> list[Path]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return []
    written = []
    rows = _order(_with_method(_default_rows(extended)))
    channels = list(dict.fromkeys(rows["segmentation_channel_name"]))
    methods = list(dict.fromkeys(rows["method"]))
    colors = {m: plt.get_cmap("tab10")(i % 10) for i, m in enumerate(methods)}

    fig, axes = plt.subplots(1, len(channels), figsize=(5 * len(channels), 4), sharey=False, squeeze=False)
    for axis, channel in zip(axes[0], channels, strict=True):
        part = rows[rows["segmentation_channel_name"] == channel]
        for method, group in part.groupby("method", sort=False):
            group = group.sort_values("image_key")
            axis.plot(group["image_key"], group["n_objects"], marker="o", ms=3, lw=1, label=method, color=colors[method])
        axis.set_title(f"Nuclei found segmenting {channel}")
        axis.set_ylabel("nuclei (default cell probability)")
        axis.tick_params(axis="x", rotation=90)
    axes[0][0].legend(fontsize=6)
    fig.tight_layout()
    written.append(_save(fig, out / "nuclei_per_image.png"))

    sens = cellprob_sensitivity(extended)
    if not sens.empty:
        fig, axes = plt.subplots(1, len(channels), figsize=(5 * len(channels), 4), squeeze=False)
        for axis, channel in zip(axes[0], channels, strict=True):
            part = sens[sens["segmentation_channel_name"] == channel]
            for method, group in part.groupby("method", sort=False):
                axis.plot(group["cellprob"], group["nuclei_vs_default"], marker="o", label=method, color=colors.get(method))
            axis.axhline(1, color="0.7", lw=0.8)
            axis.set_title(f"Segmenting {channel}")
            axis.set_xlabel("cell-probability threshold")
            axis.set_ylabel("nuclei relative to threshold 0")
        axes[0][0].legend(fontsize=6)
        fig.tight_layout()
        written.append(_save(fig, out / "cellprob_sensitivity.png"))

    thr = threshold_sensitivity(extended, markers)
    fig, axes = plt.subplots(1, len(markers), figsize=(5 * len(markers), 4), squeeze=False)
    for axis, marker in zip(axes[0], markers, strict=True):
        part = thr[thr["marker"] == marker].groupby(["method", "multiplier"], sort=False, as_index=False)["pct_positive_median"].median()
        for method, group in part.groupby("method", sort=False):
            axis.plot(group["multiplier"], group["pct_positive_median"], marker="o", label=method, color=colors.get(method))
        axis.set_xscale("log")
        axis.set_title(f"{marker} positive")
        axis.set_xlabel("threshold, as a multiple of the natural threshold")
        axis.set_ylabel("% of nuclei positive (median over images)")
    axes[0][0].legend(fontsize=6)
    fig.tight_layout()
    written.append(_save(fig, out / "threshold_sensitivity.png"))

    if not pairs.empty:
        names = list(dict.fromkeys([*pairs["method_a"], *pairs["method_b"]]))
        grid = pd.DataFrame(np.nan, index=names, columns=names)
        pooled = pairs.groupby(["method_a", "method_b"])["agreement"].median()
        for (a, b), value in pooled.items():
            grid.loc[a, b] = grid.loc[b, a] = value
        for name in names:
            grid.loc[name, name] = 1.0
        fig, axis = plt.subplots(figsize=(1.0 + 0.6 * len(names), 1.0 + 0.6 * len(names)))
        image = axis.imshow(grid.to_numpy(dtype=float), vmin=0, vmax=1, cmap="viridis")
        axis.set_xticks(range(len(names)), names, rotation=90, fontsize=6)
        axis.set_yticks(range(len(names)), names, fontsize=6)
        axis.set_title("Share of nuclei found by both methods\n(median over images and channels)", fontsize=8)
        fig.colorbar(image, ax=axis, shrink=0.7)
        fig.tight_layout()
        written.append(_save(fig, out / "agreement_between_methods.png"))

    seconds = _order(_with_method(manifest[manifest["status"] == "done"])).groupby("method", sort=False)["wall_seconds"].median()
    fig, axis = plt.subplots(figsize=(6, 0.4 * len(seconds) + 1.5))
    axis.barh(seconds.index[::-1], seconds.to_numpy()[::-1] / 60, color="0.5")
    axis.set_xlabel("minutes per run (this computer, one CPU thread per worker)")
    axis.tick_params(axis="y", labelsize=7)
    fig.tight_layout()
    written.append(_save(fig, out / "runtime.png"))
    return written


def _save(fig, path: Path) -> Path:
    fig.savefig(path, dpi=130)
    import matplotlib.pyplot as plt

    plt.close(fig)
    return path


# --- report -----------------------------------------------------------------------------------------------


def _markdown(frame: pd.DataFrame, digits: int = 1) -> str:
    if frame.empty:
        return "_none_\n"
    shown = frame.copy()
    for column in shown.columns:
        if pd.api.types.is_float_dtype(shown[column]):
            shown[column] = shown[column].map(lambda v: "" if pd.isna(v) else f"{v:.{digits}f}")
    header = "| " + " | ".join(map(str, shown.columns)) + " |\n"
    rule = "|" + "|".join("---" for _ in shown.columns) + "|\n"
    body = "".join("| " + " | ".join(str(v) for v in row) + " |\n" for row in shown.itertuples(index=False))
    return header + rule + body


def build_report(output_dir: str | Path) -> dict[str, Path]:
    """Write the summary tables, figures and ``report.md`` into ``<output>/report``."""

    root = Path(output_dir)
    out = root / "report"
    out.mkdir(exist_ok=True)
    manifest = pd.read_csv(root / "runs_manifest.csv")
    extended = pd.read_csv(root / "results_extended.csv")
    markers = _markers(root)
    written: dict[str, Path] = {}

    summary = summary_by_method(extended, manifest, markers)
    sens = cellprob_sensitivity(extended)
    thr = threshold_sensitivity(extended, markers)
    pairs = agreement(root, manifest)
    consistency = marker_agreement(extended, markers)
    for name, table in (
        ("summary_by_method", summary),
        ("cellprob_sensitivity", sens),
        ("threshold_sensitivity", thr),
        ("agreement_by_image", pairs),
        ("marker_agreement", consistency),
    ):
        written[name] = out / f"{name}.csv"
        table.to_csv(written[name], index=False)
    figures = _figures(root, out, extended, manifest, markers, pairs, summary)

    pooled = (
        pairs.groupby(["method_a", "method_b"], as_index=False)
        .agg(share_of_nuclei_found_by_both=("agreement", "median"), images_and_channels=("agreement", "size"))
        if not pairs.empty
        else pd.DataFrame()
    )
    lines = [
        "# How the segmentation modes compare",
        "",
        "These tables use no hand counts; they show how far the methods agree with each other and how much each setting matters.",
        "Counts are at the default cell probability (0) and the natural marker threshold (1x) unless a column says otherwise.",
        "",
        "## Nuclei found and time taken",
        "",
        "Times are wall-clock seconds on the computer that did the work (2 CPU cores, one thread per run, sometimes 2 or 3 runs at once), so read them as relative, not as what a laptop will take.",
        "",
        _markdown(summary, 1),
        "",
        "## Share of nuclei that two methods both find",
        "",
        "Two nuclei are the same when their centres are within "
        f"{MATCH_DISTANCE_UM:g} µm and each is the other's nearest neighbour. 1 means identical sets.",
        "",
        _markdown(pooled.sort_values("share_of_nuclei_found_by_both", ascending=False) if not pooled.empty else pooled, 2),
        "",
        "## Agreement on percent positive between methods (Pearson r across images)",
        "",
        _markdown(consistency, 2),
        "",
        "## Figures",
        "",
        *[f"![{path.stem}]({path.name})" for path in figures],
        "",
    ]
    written["report"] = out / "report.md"
    written["report"].write_text("\n".join(lines), encoding="utf-8")
    return written


# --- README for a hand-over folder ------------------------------------------------------------------------

_NOT_RUN_NOTES = {
    ("cellpose3", "full_3d"): "needs far more memory and time than a laptop has (a 256 x 256 piece of one stack took over 7 minutes and 1.6 GB).",
    ("cellpose4", "stitch_slices"): "runs Cellpose-SAM on every slice, so it takes 6 to 8 times as long as one slice on a CPU. Add --include-cellpose4-3d to run it.",
    ("cellpose4", "full_3d"): "too slow and too memory-hungry without a GPU. Add --include-cellpose4-3d to run it.",
}


def _table(frame: pd.DataFrame) -> str:
    return _markdown(frame, 1)


def write_readme(output_dir: str | Path, destination: str | Path | None = None) -> Path:
    """Write ``README.md``: what was run, how to read the tables, and how to score against hand counts."""

    root = Path(output_dir)
    dest = Path(destination) if destination else root
    manifest = pd.read_csv(root / "runs_manifest.csv")
    images = pd.read_csv(root / "images.csv")
    natural = pd.read_csv(root / "natural_thresholds.csv") if (root / "natural_thresholds.csv").exists() else pd.DataFrame()
    template = pd.read_csv(root / "hand_counts_template.csv") if (root / "hand_counts_template.csv").exists() else pd.DataFrame()
    quant = json.loads((root / "quantification.json").read_text(encoding="utf-8")) if (root / "quantification.json").exists() else {}
    markers = _markers(root)
    finished = manifest[manifest["status"] == "done"]

    ran = (
        finished.groupby(["engine", "mode"])
        .agg(runs=("unit_id", "size"), images=("image_key", "nunique"), median_seconds=("wall_seconds", "median"))
        .reset_index()
    )
    ran.insert(0, "method", [method_name(e, m) for e, m in zip(ran["engine"], ran["mode"], strict=True)])
    ran = _order(ran).drop(columns=["engine", "mode"])
    problems = manifest[manifest["status"] != "done"]
    present = {(e, m) for e, m in zip(manifest["engine"], manifest["mode"], strict=True)} if "engine" in manifest else set()
    skipped = [(key, note) for key, note in _NOT_RUN_NOTES.items() if key not in present]

    configs = pd.DataFrame(quant.get("official_configs", []))
    if not configs.empty:
        configs = configs.rename(columns={"config_id": "configuration", "cellprob": "cell probability", "marker_set": "marker threshold", "multiplier": "x natural threshold"})
    channel_list = ", ".join(images["channels"].iloc[0].split(", ")) if len(images) else ", ".join(markers)
    hand_columns = ", ".join(f"`{c}`" for c in template.columns[2:]) if len(template.columns) > 2 else ""

    lines = [
        "# CellQuant segmentation experiment",
        "",
        f"{len(images)} images, {len(finished)} segmentation runs, each quantified with 10 threshold configurations "
        f"({len(finished) * 10} rows in `results_long.csv`).",
        "Your original images were only read. Nothing here changes them.",
        "",
        "Start with `report/report.md` (how the methods compare, no hand counts needed). To rank the methods against your hand counts, fill in `hand_counts_template.csv` and run the scoring command below.",
        "",
        "## What was run",
        "",
        f"Every image has the channels {channel_list}. Each channel was segmented on its own, with every method and Z mode below, because these images have no separate nuclear counterstain. "
        "The marker names (Green = OTX2-GFP, Red = mCherry, Far Red = PAX6) are how these channels were understood from earlier work; the tables use the channel names.",
        "",
        _table(ran),
        "",
    ]
    if skipped:
        lines += ["**Not run**", ""]
        lines += [f"- {method_name(*key)}: {note}" for key, note in skipped]
        lines += [""]
    if len(problems):
        lines += ["**Runs that did not finish** (see `runs_manifest.csv`, columns `status` and `error`)", "", _table(problems[["unit_id", "status"]]), ""]
    lines += [
        "- Times in `runs_manifest.csv` and the report are from the cloud computer that did the work (2 CPU cores, one thread per run, sometimes 2 or 3 runs sharing them). They show which methods are slow relative to each other; a laptop will differ.",
        "- **Classical** has no cell-probability setting, so its 10 configurations differ only in the marker threshold (the same count appears at each cell probability).",
        "- **Cellpose 3** uses its `nuclei` model, with the nucleus diameter estimated for each image. **Cellpose-SAM** uses `cpsam_v2`. Both use flow threshold 0.4.",
        "- Objects smaller than 5 µm² are dropped in every method, so pixel size does not change what counts as a nucleus.",
        "- Z modes: **2D projection** (max projection of the stack), **2D one slice** (the middle slice of the stack), **3D linked slices** (each slice segmented in 2D, then joined into 3D objects), **3D whole volume** (classical only).",
        "",
        "## The 10 quantification configurations",
        "",
        "A nucleus is positive for a channel when its mean intensity, above the image's own background, is greater than the threshold. "
        "A nucleus that could not be measured is left out of every count.",
        "The threshold for each channel is a multiple of that channel's *natural threshold*, the intensity that best separates dim from bright nuclei "
        "(Otsu on a log scale, over all the nuclei that one reference engine found in all images; `reference_engine` in the table below says which). Each Z domain (projection, one slice, stack) has its own, because intensities differ between them. "
        "Because they come from one engine's nuclei, they suit that engine best; the 0.5x to 2x ladder in `results_extended.csv` shows how much that matters.",
        "",
        _table(configs),
        "",
        "Natural thresholds used (`natural_thresholds.csv`):",
        "",
        _table(natural),
        "",
        "The natural thresholds are estimates from the data, not from your hand counts. If you know better values for a channel, recompute the tables without segmenting again:",
        "",
        "```",
        "python -m cellquant.sweep collate --output <this folder> --natural green=350,red=270,far_red=300",
        "```",
        "",
        "`results_extended.csv` has the same counts at 5 multiples (0.5x, 0.7x, 1x, 1.4x, 2x) instead of 2, so you can see where each method's count stops changing.",
        "",
        "## Files",
        "",
        "| file | what it holds |",
        "|---|---|",
        "| `results_long.csv` | the 10 official configurations for every run: counts and percent positive per marker, and for pairs and all three |",
        "| `results_extended.csv` | the same at 5 threshold multiples per cell probability (25 configurations) |",
        "| `threshold_curves.csv` | for every run, how many nuclei are positive in each channel as the threshold above background is raised in fixed steps |",
        "| `runs_manifest.csv` | one row per segmentation run: method, timing, versions, and a check that saved counts equal CellQuant's own |",
        "| `natural_thresholds.csv` | the threshold each channel is compared against at 1x |",
        "| `images.csv` | what is in each image file (channels, planes, pixel size, objective) |",
        "| `hand_counts_template.csv` | one row per image, to fill in with your hand counts |",
        "| `objects/` | every nucleus of every run: position and area (µm, µm²; volume in 3D), mean intensity in each channel, at each cell probability |",
        "| `labels/` | the outline images, one file per method and channel (see below) |",
        "| `report/` | summary tables, figures and `report.md` |",
        "| `design.json`, `quantification.json` | the settings used. In `design.json`, `marker_sets` and `configs` are only the fixed check-thresholds CellQuant used while segmenting; the 10 official configurations are in `quantification.json` |",
        "",
        "In `results_long.csv` and `results_extended.csv`: `n_objects` is the number of nuclei that could be measured; `n_<marker>` and `pct_<marker>` are the positive count and its percent of `n_objects`; "
        "`n_<a>_and_<b>` and `n_all_markers` are nuclei positive for several markers; `delta_<marker>` is the threshold (above background) that was applied; "
        "`n_unmeasured` is how many objects were left out because they could not be measured.",
        "",
        "## Scoring against your hand counts",
        "",
        f"1. Open `hand_counts_template.csv` and fill in one row per image. Columns: {hand_columns}. "
        "Leave a cell empty to skip it. A count column is compared with the method's count, a percent column with its percent of all nuclei found.",
        "2. Run:",
        "",
        "```",
        "python -m cellquant.sweep score --output <this folder> --hand-counts hand_counts_template.csv",
        "python -m cellquant.sweep score --output <this folder> --hand-counts hand_counts_template.csv --extended",
        "```",
        "",
        "This writes `score_by_configuration.csv` (every method and configuration ranked by error), `score_best_per_mode.csv` (the best configuration of each method) and `score_by_image.csv` (image by image). "
        "It does not need Cellpose, so run it in the same environment you use for CellQuant.",
        "",
        "## Looking at the outlines",
        "",
        "Each `labels_<method>_<channel>.npz` holds one label image per image (named by run), at the default cell probability (0). To view one over the image in napari:",
        "",
        "```python",
        "import numpy as np, napari",
        "labels = np.load('labels/labels_cellpose4_single_plane_green.npz')",
        "print(list(labels.keys()))            # one entry per image",
        "viewer = napari.Viewer()",
        "viewer.add_labels(labels[list(labels.keys())[0]])",
        "napari.run()",
        "```",
        "",
    ]
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / "README.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
