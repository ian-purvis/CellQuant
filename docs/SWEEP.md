# Comparing segmentation methods and thresholds (the sweep)

`python -m cellquant.sweep` runs many segmentations on a folder of images and counts marker-positive nuclei at several settings, so you can see which method and threshold agree with your hand counts. It only reads your images; everything is written to a results folder that must not be inside the image folder.

## 1. Run

```
python -m cellquant.sweep run --input "IMAGES" --output "RESULTS" --engines classical,cellpose4 --threads 2
```

- `--engines`: any of `classical`, `cellpose3`, `cellpose4`. Cellpose 3 and Cellpose-SAM need their own environments (`cellquant2-cellpose3`, `cellquant2-cellpose4`), so run each engine from its own environment. Runs that belong to another environment are skipped with a note.
- `--modes`: `max_projection`, `single_plane`, `stitch_slices`, `full_3d` (default: all). Cellpose-SAM in 3D is skipped unless you add `--include-cellpose4-3d`.
- `--channels 0,2` and `--images 1,2,3` limit the runs. `--dry-run` lists them.
- Each run is saved in `RESULTS/units/...`. Run the same command again to resume; use `--force` to redo finished runs. Runs can be split across computers and the `units` folders merged.

## 2. Collate and report

```
python -m cellquant.sweep collate --output RESULTS
python -m cellquant.sweep report  --output RESULTS
```

`collate` writes `results_long.csv` (10 configurations per run: cell probability -2, -1, 0, 1, 2, each with a lenient 0.7x and a strict 1.4x marker threshold), `results_extended.csv` (0.5x, 0.7x, 1x, 1.4x, 2x), `natural_thresholds.csv`, `runs_manifest.csv` and `threshold_curves.csv`. Thresholds can be changed at any time without segmenting again: `--natural green=350,red=270,far_red=300`, `--lenient 0.6`, `--strict 1.5`.

## 3. Score against hand counts

`RESULTS/hand_counts_template.csv` has one row per image. Fill in what you counted (`total_nuclei`, `<channel>_positive`, `<channel>_percent`; blanks are skipped), then:

```
python -m cellquant.sweep score --output RESULTS --hand-counts hand_counts_template.csv
python -m cellquant.sweep score --output RESULTS --hand-counts hand_counts_template.csv --extended
```

This writes `score_by_configuration.csv` (ranked by error), `score_best_per_mode.csv` and `score_by_image.csv`.

## 4. Hand over

```
python -m cellquant.sweep export --output RESULTS --to "FOLDER"
```

gathers the tables, report, one `objects_<engine>_<mode>.csv.gz` per method, one `labels_<engine>_<mode>_<channel>.npz` per method and channel, and a `README.md` into a folder of a few dozen files.

## Rules the counts follow

- A nucleus is positive when its mean intensity above the image's own background is greater than the threshold.
- A nucleus that could not be measured is left out of every count.
- Classical segmentation has no cell-probability setting, so its 10 configurations differ only in the marker threshold.
- Objects under 5 µm² are dropped for every method.
