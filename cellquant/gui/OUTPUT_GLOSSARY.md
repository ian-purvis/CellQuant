# CellQuant output glossary

What every file CellQuant writes contains, and what each column means. Open it from napari: **Help → Output glossary…**

## What to open first

| I want… | Open |
|---|---|
| My final numbers (percent positive per image) | `image_summary.csv` in the folder you exported to |
| Numbers per condition (control vs. treated) | `grouped_by_<column>.csv` (export with a group column chosen) |
| One row per cell/nucleus | `objects.csv` |
| To see which objects were found | `labels/<image>_final.tif` (open in napari or Fiji) |
| To know exactly what settings made a result | `recipe.yaml` (settings) and `provenance/<image>.json` (everything about one image) |
| To know why an image shows a warning | `logs/run.log` in the run folder, or `qc/<image>.json` |

Your original images are never changed or written to.

## Two kinds of folders

- **The results folder** is the one you picked in step 1. CellQuant saves its work there automatically.
- **An export folder** is the one you pick in step 6 (**Export results**). It holds tidy copies for a spreadsheet, a commercial package, or R. The results folder's own `exports/` folder is created empty; nothing is written into it unless you choose it.

## The results folder

```
experiment.json            what is in the experiment
recipes/                   the settings of each analysis
runs/run_xxxxxxxx/         one folder per Run (see below)
working/                   the latest open results of the first analysis
analyses/<recipe>/working/ the same, for each further analysis
edits/                     your manual edits, one file per image
.cache/                    saved intermediate results (safe to delete)
exports/                   empty unless you export here
```

### experiment.json
The experiment's index. Written after almost every change.
- `experiment_id`, `experiment_name`, `created_at`, `modified_at`, `software_version`: identity and timing.
- `input_directory`, `directory`: the image folder and this results folder.
- `images`: one entry per image. Notable fields: `image_id` (the short name used in every file name), `source_path`, `sample_name`, `channel_names`, `z_planes`, `pixel_size_x` / `_y` / `_z` (µm), `objective`, `include` (ticked in step 1), `user_metadata` (your Folder/group columns), `processing_status` and `last_result` / `last_message` (state and warning of the last run), `approved_settings_sha256` (see *Approval* below), `count_area` (the counting region drawn in step 5; empty = whole image), `content_hash` / `file_signature` (detect that the image file changed).
- `channels`: channel names and display names.
- `metadata_columns`: the extra columns (such as `group`) shown in step 1.
- `analyses`: each analysis (`recipe_id`, `name`, where its `working_folder` is, its `latest_run_id`, and the review state `image_states` of each image when it is not the active analysis).
- `latest_run_id`: the newest run.

### Recipe files (recipes folder and recipe_snapshot.yaml)
The settings of an analysis: Z-stack mode, which channel the objects are found in and with which method, the crop box, every measurement, every positive/negative cutoff, and the reports (the percentages to calculate). The snapshot is a frozen copy taken when the run started; the `recipes/` file follows later changes (for example a cutoff you move in step 5).

### Edits file (edits folder)
Your manual edits, in the order made. The automated objects are never overwritten; the edits are replayed on top.
- `image_id`: the image the edit belongs to.
- `object_id`: the object edited. `operation`: `delete` (object removed), `restore` (deleted object brought back), `erase` (pixels painted out), or `paint` (pixels added to the object). For `erase` and `paint`, `parameters` holds the pixel coordinates.
- `timestamp`: when. `parameters`: extra detail (empty for delete and restore).
- `segmentation`: which segmentation the edit was made on. An edit only applies while that segmentation is the current one, so re-running step 2 with new settings does not apply old edits to new objects.
- `analysis`: which analysis it belongs to (left out for the first analysis of older experiments).

An empty list (`[]`) means no edits.

### .cache/
Speeds up re-runs: `labels/*.npy` (objects found), `tables/*.csv` (measurements), `meta/*.json` (how they were found). File names are fingerprints of the settings. Safe to delete; CellQuant recreates what it needs.

## One run (a folder inside runs)

Each press of **Run** that starts a new batch creates a run folder. Per-image files are named with the `image_id` (`img_6f553c7b` below).

| File | What it is |
|---|---|
| `run.json` | The run record (below) |
| `recipe_snapshot.yaml` | Settings used |
| `labels/<id>_automated.tif` | Objects as found by the program, before your edits |
| `labels/<id>_final.tif` | Objects after your edits. **Use this one.** |
| `measurements/<id>.csv` | One row per object (the object table) |
| `summaries/<id>.csv` | One row for the image (percent positive) |
| `reports/<id>.csv` | The calculated percentages, one row each |
| `classifications/<id>_phenotypes.csv` | How many objects are positive for each marker and combination |
| `classifications/<id>_combinations.csv` | How many objects in each exact marker pattern |
| `provenance/<id>.json` | Full record of how this image was analyzed |
| `qc/<id>.json` | Quality summary |
| `tables/<id>.json` | Column types of the CSVs above (so they reload correctly; not for people) |
| `edits/<id>.json` | Your edits as they were when this was saved |
| `logs/run.log` | Warnings and notes. Only exists if something was logged |
| `logs/developer.log` | Technical error details. Only exists if an error occurred |

The `working/` folders have the same layout as a run folder (without the run record and log). They hold the open results you see in the app, updated as you edit and review.

### run.json
`run_id`, `experiment_id`, `recipe_id`, `software_version`; `start_timestamp` / `completion_timestamp`; `input_files` and `input_hashes` (a fingerprint of each image file); `processing_status` (`running`, `completed`, `completed_with_failures`, `failed`, or `cancelled`); `manual_edits` (edits per image); `recipe_hashes` (fingerprint of the settings used for each image); `warnings`; `errors`.

### Label images (.tif)
A picture the same size as the analyzed image (a stack for 3D). Background is `0`; every object is filled with its `object_id` number. Saved as 32-bit integers. The same `object_id` appears in `measurements/<id>.csv`. Cropped runs are pasted back to full frame.

### The object table (measurements folder; objects.csv on export)
One row per object, including objects you deleted (marked `excluded`).

| Column | Meaning |
|---|---|
| `experiment_id`, `run_id`, `sample_name`, `image_id`, `filename` | Where the object came from |
| `object_set` | Name of the object type (default `Cells`) |
| `object_id` | The object's number; matches the label image |
| `centroid_x`, `centroid_y` | Center position. µm if the image has a pixel size, otherwise pixels |
| `centroid_z` | 3D only. Center depth in µm; blank if there is no Z step |
| `area` | Size in µm² (px² without a pixel size). In 3D, the object's largest cross-section |
| `volume` | 3D only. µm³ (voxels without a pixel size; blank without a Z step) |
| `z_slices`, `z_first`, `z_last` | 3D only. How many slices the object spans, and the first and last (counting from 1) |
| one column per measurement | Your measurements (step 4), named by the measurement id, for example `af647_mean`. Brightness statistics are in the image's own intensity units; `percent_above` is 0–100 |
| `excluded` | `True` if you deleted the object. It stays in the file but is not counted |
| `manual_edit_status` | `deleted`, `edited` (painted, erased or restored), or blank |
| `at_crop_edge` | Only when cropping was used. `True` if the object touches an edge of the crop box and may be cut |
| `z_flag` | 3D only. `one_slice` (only in one slice, maybe part of a nucleus) or `possibly_merged` (much deeper than wide, maybe two nuclei linked). For checking only, counts are unchanged |
| `in_count_area` | Only when a count area is drawn. `False` = outside it, not counted |
| one `True`/`False` column per marker | Positive (`True`) or not, named by the marker id, for example `af647_pos`. Blank if it could not be measured |
| `unmeasured` | `True` if a marker could not be measured for the object (for example no pixels in its ring). Not counted |
| `phenotype` | All markers in one text, for example `AF647+|mCherry-` (`?` = not measurable) |

### The image summary (summaries folder; image_summary.csv on export)
One row per image.

| Column | Meaning |
|---|---|
| `sample_name`, `filename`, `image_id` | The image |
| `total_objects` | Objects that count (not deleted, inside the count area, measurable) |
| `count_area` | Only when a count area is drawn: its size (µm² or px²) |
| `n_unmeasured` | Objects left out because a marker could not be measured |
| `qc_status` | `success` or `warning` |
| your extra columns | Folder/group columns from step 1 (for example `group`) |
| `report_N_count` | Objects matching the report's numerator (N counts reports 1, 2, …) |
| `report_N_denominator` | Objects in the denominator (a number) |
| `report_N_percent` | `count / denominator × 100`. Blank if the denominator is 0 |
| `report_N_numerator`, `report_N_denominator_expression` | The report's wording, for example `mCherry AND AF647` and `mCherry` |

### Reports file (reports folder)
The same reports in tall form: `report` (`report_1`…), `numerator`, `denominator` (as text), `count`, `denominator_count`, `n_unmeasured`, `percent`.

### Phenotype and combination counts (classifications folder)
Columns `phenotype`, `count`, `percent_of_objects`.
- `_phenotypes`: each marker status on its own and each combination seen (for example `AF647+`, `mCherry+`, `AF647+|mCherry+`). One object counts in several rows.
- `_combinations`: every exact pattern (`AF647+|mCherry-`, …), including patterns with 0. Each object counts in exactly one row, so these add up to 100 %.

### Quality file (qc folder)
`status` (`success`/`warning`), `n_objects`, `median_area`, `fraction_touching_border` (0–1: objects touching the image edge), `percent_excluded_by_size` (objects dropped by Min/Max size), `warnings`, `errors`.

### Provenance file (provenance folder)
Everything needed to reproduce one image's result. Main keys:
- Program: `software`, `software_version`, `analysis_engine`, `timestamp`.
- Settings: `recipe` (full settings), `recipe_sha256` (their fingerprint), `measurement_definitions`, `classification_definitions`.
- Image: `experiment_id`, `run_id`, `image_id`, `sample_name`, `filename`, `source_path`, `user_metadata`, `n_channels`, `channel_names_in_file`, `channel_axis_source`, `position`, `objective`, `image_shape_yx`, `pixel_size_x_um`, `pixel_size_y_um`, `pixel_size_z_um`, `spatial_unit` (`um` or `px`).
- Z-stack: `z_planes_in_file`, `z_handling` (the Z-stack mode), `z_index`, `z_description` (plain-words summary), `analysis_dimensions` (2 or 3), `z_stitch_threshold`, `z_scale_brightness`, `z_min_slices` (the last three only matter for the modes that use them, otherwise `null`), `anisotropy` (3D Z-to-XY scale used, when recorded).
- Finding objects: `segmentation_algorithm`, `segmentation_engine` (what ran, for example the model or threshold used; `null` when reused from saved results), `segmentation_parameters`, `segmentation_channel`, `segmentation_seconds` and `segmentation_timings` (when recorded), `diameter_px_used`, `segmentation_key` (links to `.cache` and to your edits).
- Crop: `crop_rectangles` (the boxes searched as `[row start, row end, column start, column end]` in pixels; `null` = whole frame) and `crop` (statistics when recorded: `fraction` of the frame covered, `skipped` if the box covered too much and the full frame was used instead, `seconds` spent finding the boxes, `note` in words).
- Loaded objects: only when the objects were loaded from label files instead of found by CellQuant. `segmentation_origin` (`loaded`), `segmentation_source` (the file), `segmentation_settings` (the finding settings in force when loaded), `segmentation_input_sha256` (fingerprint of the image they were loaded for), `segmentation_calibration` (pixel sizes x, y, z at that time).
- Count area: `count_area` (polygons, row/column pixels) and `count_area_size`, present only if one is drawn.
- Result: `warnings`, `manual_edits` (edits applied), `excluded_object_ids` (deleted objects).

## What **Export results** writes

Into the folder you choose:

| File | What it is |
|---|---|
| `objects.csv` | All objects of all images (object table above) |
| `image_summary.csv` | One row per image (summary above) |
| `grouped_by_<column>.csv` | Only if you chose a group column. One row per group |
| `units_by_<unit>.csv` | One row per unit (for example per retina), every report pooled over its images |
| `grouped_by_<column>_equal_units.csv` | Only with a group column and units: each group's mean over units, every unit counted once |
| `recipe.yaml` | The current settings |
| `settings_index.csv` | `image_id`, `filename`, `recipe_sha256`: which settings made each image |
| `mixed_settings.txt` | Only if images were made with more than one settings version |
| `run.json` | Copy of the latest run record |

**Grouped columns** (`<report>` is `report_1`, …):
- `n_images`: images in the group. `summary_note`: a reminder of the two ways to average.
- `<report>_mean_of_per_image_percent`, `<report>_sd_of_per_image_percent`: average of each image's percent and its sample SD (blank for one image).
- `<report>_pooled_count`, `_pooled_denominator`, `_pooled_percent`: all counts added up, divided by all denominators added up. Images with more nuclei weigh more.

**Unit columns:** `n_images`, then `<report>_unit_pooled_count`, `_unit_pooled_denominator`, `_unit_pooled_percent`, `_unit_n_images`.

**Equal-unit columns:** the group column, `n_units`, `summary_note`, `<report>_n_units`, `<report>_equal_unit_mean_percent`, `<report>_equal_unit_sd_percent`.

**Export all analyses** makes one subfolder per analysis (the same files as above), plus in the main folder:
- `all_analyses_image_summary.csv`: every analysis's image summary, with `analysis` and `segmentation_channel` added in front.
- `all_analyses_grouped_by_<column>.csv`: grouped tables with an `analysis` column (only with a group column).
- `analyses_not_exported.txt`: lists analyses that had no results yet.

## Approval and fingerprints

- `recipe_sha256` is a fingerprint of the scientific settings. If any setting that changes the numbers changes, the fingerprint changes.
- `approved_settings_sha256` (in `experiment.json`) is that fingerprint plus a colon and the number of edits at the time you clicked **Approve**, for example `1187e06c…:1`. An image stays approved only while both still match. Changing a cutoff in a way that changes the percent positive clears the approval.
- `input_hashes` in `run.json` and `content_hash` in `experiment.json` are fingerprints of the image files, so a changed file is noticed.
