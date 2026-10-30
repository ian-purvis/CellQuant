# CellQuant output glossary

What every file CellQuant writes contains, and what each column and key means. Beginners can read the plain parts first. Experts will find the exact rule and the code function at the end of each entry. Open it from napari: **Help → Output glossary…**. The in-app version can fill each page with your own numbers.

## Start here

### What to open first

| I want… | Open |
|---|---|
| My final numbers (percent positive per image) | `image_summary.csv` in the folder you exported to |
| Numbers per condition (control vs. treated) | `grouped_by_<column>.csv` (export with a group column chosen) |
| One row per cell/nucleus | `objects.csv` |
| To see which objects were found | `labels/<image>_final.tif` (open in napari or Fiji) |
| To know exactly what settings made a result | `recipe.yaml` (settings) and `provenance/<image>.json` (everything about one image) |
| To know why an image shows a warning | `logs/run.log` in the run folder, or `qc/<image>.json` |

Your original images are never changed or written to.

### Two kinds of folders

- **The results folder** is the one you picked in step 1. CellQuant saves its work there automatically.
- **An export folder** is the one you pick in step 6 (**Export results**). It holds tidy copies for spreadsheets or R. The results folder's own `exports/` folder is created empty; nothing is written into it unless you choose it.

### How the numbers are made

Every number in the tables comes from the same short chain. Knowing the chain tells you which step to go back to when a number looks wrong.

1. **Step 2, Find objects.** CellQuant finds objects (nuclei or cells) and gives each one a number, its `object_id`. The result is a label image.
2. **Step 3, Edit objects.** Your deletions and paint strokes are applied on top of the found objects. The found objects are never overwritten.
3. **Step 4, Markers.** For each marker, CellQuant measures a brightness value inside every object. By default this is the mean brightness (`<marker>_mean`).
4. **Step 5, Check.** Each marker has a cutoff. An object is positive for a marker when its value is above the cutoff (`<marker>_pos`).
5. **Step 6, Results.** For every image, CellQuant counts objects and turns the counts into percentages (the reports). Export then averages or pools images into groups.

An object is counted only when all three are true:

    counted ⇔ excluded = False  and  in_count_area is not False  and  unmeasured = False

Here `excluded` marks deleted objects, `in_count_area` is only present when you drew a count area, and `unmeasured` marks objects where a marker could not be measured. Deleted, outside-area and unmeasured objects stay in the object table, but they are left out of every count and every denominator.

The main equation behind every percent in the summary files is:

    percent = count ÷ denominator × 100

`count` is the number of counted objects that match the report's numerator and denominator. `denominator` is the number of counted objects that match the report's denominator.

### Approval and fingerprints

A fingerprint is a long string of letters and digits (a SHA-256 hash) made from some content. The same content always gives the same fingerprint. A different content gives a different one. CellQuant uses fingerprints to notice that something changed.

- `recipe_sha256` is a fingerprint of the scientific settings of an analysis. If any setting that changes the numbers changes, the fingerprint changes. Names, ids, dates and the software version are left out, so saving a settings file again does not change it. It is stored in each `provenance/<image>.json`.
- `approved_settings_sha256` (in `experiment.json`, one per image) is that fingerprint plus a colon and the number of edits at the time you clicked **Approve**:

        approved_settings_sha256 = recipe_sha256 + ":" + number of edits

  For example `1187e06c…:1` means these settings and one edit. An image stays approved only while both parts still match. Changing a cutoff clears the approval only if it changes the number of positive objects of that image, because the approval vouches for the percent positive. Editing an object, loading new objects, or running the image again with different settings also clears it. Running again with the same settings keeps it.
- `input_hashes` in `run.json` and `content_hash` in `experiment.json` are fingerprints of the image files, so a changed file is noticed.
- `recipe_hashes` in `run.json` is the `recipe_sha256` used for each image of a run.

Each of these keys has its own page below, with the exact rule: `recipe_sha256` under the provenance file, `approved_settings_sha256` and `content_hash` under the `images` list of `experiment.json`, and `input_hashes` and `recipe_hashes` under `run.json`.

## Results folder

The results folder is the one you picked in step 1. CellQuant saves its work there automatically. It looks like this:

```
experiment.json            what is in the experiment
recipes/                   the settings of each analysis
runs/run_xxxxxxxx/         one folder per Run (see below)
working/                   the latest open results of the first analysis
analyses/<analysis>/working/ the same, for each further analysis
edits/                     your manual edits, one file per image
.cache/                    saved intermediate results (safe to delete)
exports/                   empty unless you export here
```

An experiment can hold several analyses (different settings on the same images, for example one per channel that objects are found in). Each analysis has its own recipe file and its own results. The first analysis keeps its open results in `working/`. Further analyses keep them in `analyses/<analysis>/working/`, where `<analysis>` is the analysis's recipe id.

### `experiment.json` — the experiment index
<!-- data: json experiment.json -->

**In short:** The index of the whole experiment: its images, channels and analyses.

**What it means:** This file is written when you add images in step 1 and rewritten after almost every change, such as a new status, a count area or an approval. When you reopen a results folder, CellQuant reads this file first to find out what is in it. Look here to see which images are included, which analysis is active, and what happened to each image in its last run.

**Good to know:** The image files themselves are not copied into the results folder. The file only remembers where they are (`source_path`).

**For experts:** Serialized by `experiment.save_experiment` from the `experiment.Experiment` model (extra keys are refused when loading). When a folder is opened, `directory` is reset to the folder it was opened from, so a moved or copied folder writes into itself.

**Related:** `images`, `analyses`, `latest_run_id`

#### `experiment_id`, `experiment_name`

**In short:** The experiment's id and the name you gave it.

**What it means:** `experiment_id` looks like `exp_6301da96`. It is made once, when the results folder is created, and is copied into every object table and run record so files can be matched back to this experiment. `experiment_name` is the display name.

**For experts:** `exp_` plus 8 hex characters of a random UUID (`experiment.create_experiment`). If no name is given the name is `Experiment`.

#### `created_at`, `modified_at`

**In short:** When the experiment was created and when it was last saved.

**What it means:** Both are times in UTC. `modified_at` changes every time CellQuant saves the index, so it shows when you last worked in this folder.

**For experts:** ISO 8601 strings from `datetime.now(timezone.utc).isoformat()`. `Experiment.touch` updates `modified_at` and `software_version`.

#### `software_version`

**In short:** The CellQuant version that last saved this file.

**What it means:** Use it to know which version made a result. It is updated every time the file is saved, so it names the last version that touched the folder, not the one that made each image.

**Related:** `software`

#### `recipe_id`

**In short:** The id of the original analysis's settings file.

**What it means:** The settings of an analysis live in `recipes/<analysis>.yaml`, and this id is its name without the extension. In an experiment with several analyses, each analysis has its own id in the `analyses` list.

**Related:** `recipes/<analysis>.yaml`, `analyses`

#### `directory`, `input_directory`

**In short:** The folder where results are kept and the folder where the images were found.

**What it means:** `directory` is this results folder. `input_directory` is the folder you picked when you added images. Neither is used to find the images later; each image remembers its own path.

**For experts:** `directory` is overwritten at load time (`experiment.load_experiment`). `input_directory` is the resolved path given to `create_experiment`, or empty.

#### `import_file_types`

**In short:** The file types chosen the last time you searched a folder for images.

**What it means:** Either `nd2`, `tiff` or both. It only sets the default for the next search.

**For experts:** `nd2` means `.nd2`; `tiff` means `.tif` and `.tiff` (`experiment.FILE_TYPES`).

#### `latest_run_id`

**In short:** The id of the newest run.

**What it means:** Each press of **Run** that starts a new batch makes a run folder (`runs/run_xxxxxxxx/`). This is the newest one. Each analysis also keeps its own latest run in the `analyses` list.

**Related:** `run.json`, `run_id`

#### `metadata_columns`

**In short:** The names of the extra columns shown in step 1.

**What it means:** These are columns you can group by, such as `group`, plus `Folder 1`, `Folder 2`, … which CellQuant makes from the subfolder names of your images. The values for each image are in `user_metadata`. They are copied into the image summary and used by the grouped export.

**For experts:** Folder columns come from `experiment._add_folder_columns`: one column per subfolder level of the image's path inside the added folder.

**Related:** `user_metadata`, `<column>`

#### `channels`

**In short:** The experiment's channels, with the names you gave them.

**What it means:** One entry per channel: `channel_index` (counting from 0), `channel_name` (the name in the file or the name you set in step 1), `display_name` (the name shown in the app) and `display_settings` (how the channel is shown, such as brightness limits). Marker names in the object table come from these names.

**For experts:** `experiment.ChannelRecord`. Names are stored experiment-wide; each image also lists its own `channel_names`.

**Related:** `channel_names`, `<marker>_mean`

#### `analyses`

**In short:** The list of analyses (settings plus results) in this experiment.

**What it means:** Each analysis has its own settings and its own results. Each entry holds `recipe_id`, `name`, `working_folder` (where its open results are, such as `working` or `analyses/<analysis>/working`), `latest_run_id`, and `image_states`. `image_states` keeps the review state of each image (`processing_status`, `last_result`, `last_message`, `approved_settings_sha256`) for analyses that are not the active one; the active analysis keeps these on the image entries. The entry also has `owns_legacy` (true for the original analysis, which owns everything saved before analyses existed), `removed` (taken off the list but kept, so its runs and edits are never mistaken for another analysis's) and `plan` (which images it runs on, a channel to find objects in for one image, and whether to use the full image even when cropping).

**Good to know:** Edits belong to the analysis they were made in. Two analyses can share the same found objects but each keeps its own deletions.

**For experts:** `experiment.AnalysisRecord` and `experiment.PlanEntry` (`run`: true, false or null for the default; `channel`; `full_image`). Older experiments with no entries have a single, implicit analysis.

**Related:** `analysis`, `edits/<image>.json`

#### `images`

**In short:** One entry per image: where it is, how it is calibrated and where it stands in the workflow.

**What it means:** The list holds every image you added in step 1, ticked or not. The fields below are the entries of one image. The in-app version shows the entry of the image you pick.

**For experts:** `experiment.ImageRecord`.

##### `image_id`

**In short:** The short name used in every file name for this image.

**What it means:** It looks like `img_f7058235`. It is made when the image is added and never changes. Every per-image file, such as `measurements/img_f7058235.csv`, uses it, which keeps the files short and safe even when the original file names are long.

**For experts:** `img_` plus 8 hex characters of a random UUID. File names go through `storage._safe`, which replaces other characters with `_`.

##### `source_path`, `relative_path`, `source_path_relative`

**In short:** Where the image file is.

**What it means:** `source_path` is the full path of the file when it was added. `relative_path` is its path inside the folder you added, for example `mCherry cont/Retina 2/image.nd2`, which keeps apart files with the same name in different subfolders. `source_path_relative` is only used for results that travel with their own copy of the images: when set and the file is there, it is used instead of `source_path`, so the folder can be moved.

**For experts:** `source_path_relative` uses POSIX separators and is relative to the results folder.

##### `filename`, `sample_name`

**In short:** The file name and the name CellQuant shows for the image.

**What it means:** `filename` is the file name with its extension. `sample_name` is the image's path inside the added folder without the extension, for example `mCherry cont/Retina 2/image`. A file with several stage positions gets `[position N]` added. Both are copied into the object table and the summary.

**For experts:** `experiment._sample_name`. Position numbers shown are 1-based.

##### `position`, `positions_in_file`

**In short:** Which stage position of the file this image is, and how many the file has.

**What it means:** A multi-position ND2 file holds several images. Each becomes its own entry here. `position` counts from 0. `positions_in_file` is 1 for ordinary files.

##### `z_planes`

**In short:** How many Z slices the file has.

**What it means:** 1 means a single plane. For a stack, how the slices are used is chosen in step 2 (the Z-stack mode); see `z_handling`.

**Related:** `z_handling`, `z_planes_in_file`

##### `channel_names`, `channel_colors`, `number_of_channels`

**In short:** The channels of this image: their names, display colors and number.

**What it means:** `channel_names` are read from the file, for example `AF594`, `AF647`. `channel_colors` are the display colors stored in the file, as red, green, blue between 0 and 1 (or `null` when the file has none). `number_of_channels` is the count.

##### `objective`

**In short:** The objective lens named in the file.

**What it means:** Text copied from the file's metadata, for example `PLAN APO λD 20x OFN25 DIC N2`. It is only recorded; it does not change any number.

##### `include`

**In short:** Whether the image is ticked in step 1.

**What it means:** Only ticked images are analyzed, shown in the results and exported. Unticking never deletes anything.

##### `dimensions`

**In short:** The image size in pixels, as height then width.

**What it means:** For a 1024 × 1024 image this is `[1024, 1024]`. It matches `image_shape_yx` in the provenance file.

##### `pixel_size_x`, `pixel_size_y`, `pixel_size_z`

**In short:** The size of one pixel (and the Z step) in µm.

**What it means:** CellQuant uses these to turn pixels into µm and µm². They come from the file and you can correct them in step 1. Without them all sizes are in pixels. Physical distances, such as a ring around a nucleus, need X and Y to be equal.

**Good to know:** A 3D analysis needs the Z step (`pixel_size_z`) for `volume` and `centroid_z`.

**For experts:** Unequal X and Y sizes (more than 0.1 % apart) raise a calibration error (`regions.isotropic_pixel_size`). The provenance file keeps the values used as `pixel_size_x_um`, `pixel_size_y_um`, `pixel_size_z_um`.

**Related:** `pixel_size_x_um`, `spatial_unit`, `area`

##### `image_metadata`

**In short:** Extra facts read from the file.

**What it means:** `axes` (the order of the file's dimensions, such as `ZCYX`), `dtype` (the type of the pixel numbers, such as `uint16`) and `notes` (anything unusual found while reading the file).

**Related:** `channel_axis_source`

##### `user_metadata`

**In short:** Your own labels for the image, such as its group.

**What it means:** These are the values of the `metadata_columns`: the columns you added in step 1 and the `Folder N` columns. They are copied into the image summary so you can group by them on export.

##### `processing_status`

**In short:** Where the image stands in the workflow.

**What it means:** One of `not_analyzed`, `analyzed`, `reviewed`, `approved`, `excluded` or `needs_attention`. It is what the status shown for the image in the app is saved from.

**For experts:** `experiment.IMAGE_STATUSES`. A changed approval or a failed run sets `needs_attention` or `analyzed` (see `approved_settings_sha256`).

##### `last_result`, `last_message`

**In short:** The outcome and the warning text of the last run of this image.

**What it means:** `last_result` is `Success`, `Warning` or `Failure`. `last_message` holds the warnings, joined by `; `, or the reason a run failed, or a note such as that an approval was cleared.

**For experts:** `controller._job_status`: `Success` when the QC status is `success`, otherwise `Warning` with the QC warnings as the message.

**Related:** `status`, `warnings`

##### `approved_settings_sha256`

**In short:** The fingerprint of the settings and edits you approved, or blank.

**What it means:** It is blank until you click **Approve** for the image in step 5. Then it holds the fingerprint of the settings plus the number of edits at that time. The image stays approved only while the settings and the edit count still match. Changing a cutoff clears the approval only if it changes the number of positive objects in that image.

**How it's calculated:**

    approved_settings_sha256 = recipe_sha256 + ":" + number of edits

For example `1187e06c…:1`.

**Good to know:** An approval is also cleared by editing an object, by loading new objects, and by running the image again with different settings. Running again with the same settings keeps it. **Undo** in step 3 clears it only if the number of counted objects, or of any marker's positive objects, changes; when the approval stays, the edit count in the fingerprint is updated.

**For experts:** `controller._approval_fingerprint`. For a 3D stack cropped to its region `:stack-crop` is added, so approvals made before stacks could be cropped no longer match. `controller._clear_changed_approvals` compares the positive count at the old and new cutoff (`>` or `≥` as the marker uses); other values are not compared.

**Related:** `recipe_sha256`, `processing_status`

##### `content_hash`, `file_signature`

**In short:** Fingerprints of the image file, used to notice that it changed.

**What it means:** `content_hash` is a SHA-256 fingerprint of everything in the image file. `file_signature` is a quick check made from the file's modification time and size. If either differs from what was saved, CellQuant knows the file is not the same one any more and does not reuse old results for it. `input_hashes` in `run.json` copies the content hashes at the start of a run.

**For experts:** `storage.file_fingerprint` returns `(f"{st_mtime_ns}:{st_size}", sha256 of the bytes)`, read in 1 MB chunks.

**Related:** `input_hashes`

##### `count_area`

**In short:** The counting region you drew for this image, or empty for the whole image.

**What it means:** You draw it in step 5 to count only part of the image, for example only the retina and not the empty slide. It is a list of polygons. Each polygon is a list of `[row, column]` points in pixels. Only objects whose center lies inside a polygon are counted. An empty list means the whole image counts.

**Good to know:** The count area is shared by every analysis of the image. Objects outside it stay in the object table with `in_count_area` set to `False`.

**For experts:** `count_area.clean` drops polygons with fewer than three points. The polygon applies to every Z slice.

**Related:** `in_count_area`, `count_area`

### `recipes/<analysis>.yaml` — the settings of one analysis
<!-- data: yaml recipes/<analysis>.yaml -->

**In short:** The settings of one analysis, kept as a text file.

**What it means:** It holds everything that decides the numbers: how Z-stacks are handled, which channel the objects are found in and with which method, the crop box, every measurement, every positive/negative cutoff, and the reports (the percentages to calculate). The file follows later changes, for example a cutoff you move in step 5. The file name is the analysis's recipe id, such as `recipes/recipe_6301da96.yaml`.

**Good to know:** The copy in a run folder (`recipe_snapshot.yaml`) is frozen when the run starts. This one is not. The export folder gets a copy as `recipe.yaml`.

**For experts:** `recipe.Recipe`, written by `recipe.save_recipe` (`yaml.safe_dump` of `canonical_dict`). Version 1 of the schema (`recipe_version: 1`). Extra keys are refused when loading.

**Related:** `recipe_snapshot.yaml`, `recipe_sha256`

#### `recipe_version`, `recipe_id`, `recipe_name`

**In short:** The schema version and the identity of the settings.

**What it means:** `recipe_version` is the file format (always 1). `recipe_id` is the id that names the file and ties edits and runs to the analysis. `recipe_name` is the name shown in the app, for example `Analysis 1`.

**Good to know:** The id and name are not part of `recipe_sha256`, so renaming an analysis does not change its fingerprint.

#### `z_stack`, `z_index`

**In short:** How a Z-stack is analyzed, and which slice when only one is used.

**What it means:** `z_stack` is one of `max_projection` (flatten the stack to 2D by taking the brightest value through all slices), `single_plane` (use one slice), `stitch_slices` (find objects slice by slice, then link outlines in neighbouring slices into 3D objects) or `full_3d` (the method works on the whole volume). `z_index` counts from 0 and only matters for `single_plane`; blank means the middle slice. Single-plane images ignore both.

**For experts:** `recipe.Recipe.z_stack` (default `max_projection`). Reported per image as `z_handling`, `z_index`, `z_description` in the provenance file.

**Related:** `z_handling`, `z_description`

#### `z_stitch_threshold`, `z_scale_brightness`, `z_min_slices`

**In short:** Extra 3D settings: how strictly slices are linked, how brightness is scaled, and the shortest object kept.

**What it means:** `z_stitch_threshold` (default 0.25) is the overlap two outlines in neighbouring slices need to be linked. `z_scale_brightness` is `stack` (one scale for every slice) or `slice` (each slice scaled on its own). `z_min_slices` (default 1) removes 3D objects found in fewer slices than this; 1 keeps all of them.

**How it's calculated:**

    overlap = pixels in both outlines ÷ pixels in either outline    (IoU)
    outlines can link ⇔ overlap ≥ z_stitch_threshold

IoU means intersection over union. Each outline links to its best-overlapping partner in the slice above, or starts a new object.

**For experts:** `volume.stitch_slices`. The three values are part of `recipe_sha256` only for `stitch_slices` and `full_3d`; `z_stitch_threshold` is used only for `stitch_slices`.

**Related:** `z_description`

#### `object_set`

**In short:** Which channel the objects are found in and with which method.

**What it means:** `name` is the object type (default `Cells`; it appears in the `object_set` column). `segmentation_channel` is the channel the objects are found in, counting from 0. `algorithm` is the method (`classical` or `cellpose`). `parameters` holds that method's settings, such as the diameter or the Min/Max size from step 2.

**For experts:** `recipe.ObjectSetSpec`. Size limits are `min_area_px`, `max_area_px`, `min_area_um2`, `max_area_um2`: an object is removed when its area is below the minimum or above the maximum, so an object exactly at a limit is kept.

**Related:** `segmentation_algorithm`, `segmentation_channel`, `segmentation_parameters`

#### `crop`

**In short:** Whether to find objects only inside the part of the image that holds your cells of interest.

**What it means:** Off by default. When on, CellQuant finds the region where your reporter channel is bright, grows it by a margin, and finds objects only inside rectangles around it. This saves time on large images. The labels are put back into the full frame, so edits and exports are unchanged. Settings: `enabled`, `channels`, `margin_um` (50), `sensitivity` (3), `bin_um` (2), `nucleus_diameter_um` (6.5), `merge_gap_um` (100) and `max_fraction` (0.7).

**Good to know:** If the rectangles would cover more than `max_fraction` of the image, the whole image is used instead. A result's own crop record is in the provenance file as `crop_rectangles` and `crop`.

**For experts:** `recipe.CropSpec`; `crop.regions_for` and `crop.detect_regions`. A region is the set of binned pixels brighter than the background by `sensitivity` noise units on every chosen channel (AND), where noise is estimated from the dimmest quarter of the image. Without `channels`, the channels used by the reports' denominators are taken. 2D only for the classical method; a Z-stack needs Cellpose.

**Related:** `crop_rectangles`, `at_crop_edge`

#### `measurements`

**In short:** The list of measurements taken in every object, one column each in the object table.

**What it means:** Each entry has an `id` (the column name), a display `name`, the `channel` (counting from 0), the `region` it is measured in, a `statistic`, and a `background` correction. You set these in step 4. The marker setup makes `<marker>_mean` (and `<marker>_pct`) for you.

**For experts:** `recipe.MeasurementSpec`. An `id` cannot be one of the reserved column names (`experiment_id`, `run_id`, `sample_name`, `image_id`, `filename`, `object_set`, `object_id`, `centroid_x`, `centroid_y`, `area`, `phenotype`, `excluded`, `unmeasured`, `centroid_z`, `volume`, `z_slices`, `z_first`, `z_last`, `z_flag`, `at_crop_edge`, `area_unit`, `volume_unit`, `length_unit`).

**Related:** `<measurement>`, `measurement_definitions`

##### `statistic`

**In short:** What is calculated from the pixels in the region.

**What it means:** One of `mean`, `median`, `min`, `max`, `std` (spread), `integrated` (sum), `area`, `equivalent_diameter`, `centroid_x`, `centroid_y` or `percent_above`. The equation for each is on the `<measurement>` page.

**Good to know:** Background correction does not apply to `area`, `equivalent_diameter`, `centroid_x`, `centroid_y` or `std`, and CellQuant refuses it for them.

**Related:** `<measurement>`

##### `region`

**In short:** Which pixels around each object are measured.

**What it means:** `type` is `object` (the object's own pixels), `eroded_object` (the object shrunk inward by `distance_um` or `distance_px`), `expanded_object` (grown outward by that distance) or `ring` (the band between `inner_um`/`inner_px` and `outer_um`/`outer_px` from the object's edge, not including the object). Distances are given in µm or pixels, never both. A ring's inner distance must be less than its outer distance.

**Good to know:** Expanded regions and rings never overlap: each pixel goes to its nearest object. Eroded regions can become empty for small objects, and then the value is blank.

**For experts:** `regions.create_measurement_region`. Distances are Euclidean in XY pixels (µm ÷ pixel size). Erosion removes object pixels whose distance to the outside is ≤ the distance. Expansion adds background pixels with distance ≤ the distance. A ring keeps background pixels with inner ≤ distance ≤ outer. In 3D the Z step is converted to XY pixels so a distance means the same along Z.

##### `background`

**In short:** How the background brightness is subtracted before the cutoff is applied.

**What it means:** `type` is `none`, `global` (one number for the whole channel) or `local_ring` (a ring around each object, with `inner_um`/`inner_px` and `outer_um`/`outer_px`). For `global`, `value` is a number you give; if blank, CellQuant uses the median of all pixels that belong to no object. Use it when the background differs between images or regions.

**How it's calculated:**

    corrected = region statistic − background

For `integrated` the background is multiplied by the number of pixels in the region first.

**For experts:** `quantify.measure_objects`. Global with no value: median of `channel[labels == 0]`, or the 10th percentile of the channel (with a warning) if no pixel is outside objects. Local ring: the same statistic taken in the ring (the mean for `integrated`); `percent_above` uses the median of the ring.

##### `pixel_level`, `pixel_level_high`

**In short:** For `percent_above` only: the brightness a pixel needs to count, and an optional upper limit.

**What it means:** A pixel counts when its (background-corrected) value is at least `pixel_level` and, if `pixel_level_high` is set, at most `pixel_level_high`. These are in the image's own brightness units. They are set in step 4 or moved in step 5.

**For experts:** Both ends are inclusive (`≥` and `≤`). A level on any other statistic is refused. `pixel_level_high` must be at least `pixel_level`.

**Related:** `<marker>_pct`

#### `classifications`

**In short:** The positive/negative rules, one per marker.

**What it means:** Each entry has an `id` (the column name, such as `af647_pos`), a display `name` (used in the phenotype text and in reports), the `measurement` it looks at, and a `threshold` (the cutoff). You set the cutoffs in step 5.

**For experts:** `recipe.ClassificationSpec`. `method` is always `threshold`. `positive_label` and `negative_label` (default `positive`, `negative`) are only labels. Ids cannot be reserved column names, and names must be unique.

**Related:** `<marker>_pos`, `classification_definitions`

##### `threshold`

**In short:** The cutoff that separates positive from negative.

**What it means:** The value of the measurement above which an object is positive. It is in the same units as the measurement. The marker setup starts it at a first guess from the image (Otsu), which you check in step 5.

**How it's calculated:**

    positive ⇔ value > threshold        (comparison: above)
    positive ⇔ value ≥ threshold        (comparison: at_least)

**For experts:** `quantify._positive`. `recipe.ClassificationSpec.threshold_for(image_id)` returns an image's own cutoff from `image_thresholds`, otherwise `threshold`.

##### `comparison`

**In short:** Whether equal to the cutoff counts as positive.

**What it means:** `above` (the default) means an object exactly at the cutoff is negative. `at_least` means it is positive. The "at least N % of pixels" rule uses `at_least`.

##### `auto_method`, `auto_percent`

**In short:** How the automatic cutoff in step 5 is found.

**What it means:** `auto_method` is `otsu` (default), `triangle`, `li` or `top_percent`. `auto_percent` (default 10, between 0 and 100) is the N for `top_percent`. They only choose how **Auto** and **Reset cutoff** propose a number; they do not change results and are not part of `recipe_sha256`.

**How it's calculated:**

    otsu:         the split of the sorted object values with the largest between-group variance,
                  placed halfway across the gap
    top_percent:  cutoff = the (100 − N)th percentile of the object values

**For experts:** `quicksetup.auto_threshold`. `otsu` is `quicksetup.starting_threshold`, evaluated at every gap between the sorted values. `triangle` and `li` call `skimage.filters.threshold_triangle` (`nbins` = number of values, limited to 16 to 256) and `threshold_li`. Fewer than two values, or all values equal, falls back to `starting_threshold`. Only finite values are used.

##### `image_thresholds`

**In short:** Cutoffs set for single images.

**What it means:** A list of image id and cutoff pairs. Any image not listed uses `threshold`. It lets you correct one image without moving the others.

**For experts:** `Recipe.for_image` puts an image's cutoff into `threshold` and clears this list, so a provenance file shows the cutoff that image actually used.

##### `cutoff_by`

**In short:** How this marker's cutoff is set in step 5: `slider` (the default) or `examples`.

**What it means:** Chosen per marker under **Set cutoff by** in step 4. With `examples` you click objects you call positive or negative in step 5, and the image's cutoff moves between them. It only chooses the tools; it does not change results and is not part of `recipe_sha256`.

**Related:** `cutoff_examples/<image>.json`

##### `cell_calls`

**In short:** Objects called positive or negative by hand, overriding the cutoff for those objects only.

**What it means:** Set in step 5 with **Edit cells…**: you select objects and click Positive, Negative or Use cutoff. Listed per image as object number, the call, and the object's centre. Every other object is still called by the cutoff, and moving a cutoff never changes a call made by hand. A call is kept only while its object is still there: same number, not deleted, and its centre within half its radius of where it was. Running the image again with new objects drops calls whose object moved.

**Good to know:** Calls by hand are a judgement, not a measurement. Each object called by hand is marked in `<marker>_pos_by_hand`, and each image's number is `n_called_by_hand`, so readers can see how much of a result was set by hand.

**For experts:** `recipe.ClassificationSpec.cell_calls` (image id → object id → `positive`, `x`, `y`), applied by `quantify.classify_objects` through `quantify.hand_calls`. `Recipe.for_image` keeps only that image's calls, so a provenance file lists the calls its image used. Not part of `recipe_sha256`; an image's approval covers its calls.

**Related:** `<marker>_pos`, `<marker>_pos_by_hand`, `n_called_by_hand`

#### `reports`

**In short:** The percentages to calculate, each as a numerator and a denominator.

**What it means:** Each report is a pair of expressions over positive/negative calls. The numerator and denominator are marker ids (or names) joined by `AND`, with `not` in front to mean negative. The denominator can be `all_objects` (every counted object). Examples: `af647_pos` over `all_objects`, or `af594_pos AND af647_pos` over `af594_pos`. The marker setup makes one report per marker and one double-positive report for each pair of markers.

**Good to know:** The words `AND` and `not` are not case sensitive. The count is of objects that match both the numerator and the denominator, so the numerator is always inside the denominator.

**For experts:** `recipe.ReportSpec`, evaluated by `quantify._expression_mask`. `all_objects` and `all_measured_objects` are the same thing. A token that matches a classification id and a different classification name is refused as ambiguous.

**Related:** `report_N_percent`, `report`

### `edits/<image>.json` — your manual edits
<!-- data: json edits/<image>.json -->

**In short:** Your manual edits to one image, in the order you made them.

**What it means:** Deleting an object, restoring one, or painting or erasing pixels in step 3 is saved here, one file per image. The automated objects are never overwritten. When results are made, the edits are replayed on top of them. An empty list (`[]`) means no edits.

**Good to know:** This root file holds every edit ever made to the image, including edits made in other analyses or on earlier object-finding settings. The copy inside a run or working folder holds only the edits that applied to that result.

**For experts:** `edits.EditOperation` list written by `storage.save_edits`. `edits.apply_edits` replays them in order. The run copy comes from `controller._active_edits`.

**Related:** `segmentation`, `analysis`

#### `image_id`, `object_id`

**In short:** The image the edit belongs to and the object it changed.

#### `operation`

**In short:** What the edit did: `delete`, `restore`, `erase` or `paint`.

**What it means:** `delete` removes the object. `restore` brings a deleted object back. `erase` paints pixels out of the object. `paint` adds pixels to the object. Pixel edits are recorded as the pixels changed, not as a brush path.

**For experts:** `edits.apply_edits`. `restore` copies the object back from the automated labels. An unknown operation raises an error.

#### `timestamp`

**In short:** When the edit was made.

**What it means:** A time in UTC.

#### `parameters`

**In short:** Extra detail of the edit: the pixel coordinates for `erase` and `paint`.

**What it means:** Empty for `delete` and `restore`. For `erase` and `paint` it holds `rows` and `cols` (pixel positions), and `planes` as well for a 3D image.

**For experts:** An edit with `planes` cannot be applied to 2D labels and the reverse (an error is raised).

#### `segmentation`

**In short:** Which found objects the edit was made on.

**What it means:** Object numbers only mean something for the objects found by one set of settings. An edit only applies while that segmentation is the current one, so running step 2 again with new settings does not apply old edits to new objects. Blank (older saves) means it always applies.

**Related:** `segmentation_key`

#### `analysis`

**In short:** Which analysis the edit belongs to.

**What it means:** Each analysis keeps its own deletions. The key is left out for the first analysis of older experiments, which own every edit that has no analysis.

**Related:** `analyses`

### `.cache/` — saved intermediate results
<!-- data: folder .cache/ -->

**In short:** Saved work that speeds up re-runs. Safe to delete.

**What it means:** It holds `labels/*.npy` (objects found), `tables/*.csv` (measurements) and `meta/*.json` (how they were found). File names are fingerprints of the settings, so changing a setting finds a new file instead of reusing an old one. If you delete the folder, CellQuant recreates what it needs, only more slowly.

**For experts:** `cache.stage_key` is the SHA-256 of the stage name, the software version and the settings, so a new CellQuant version does not reuse another version's files. A damaged file is discarded and recomputed (`cache.StageCache`).

**Related:** `segmentation_key`

### `exports/` — an empty folder for exports
<!-- data: folder exports/ -->

**In short:** A folder you can export into. It starts empty.

**What it means:** The results folder makes it for you, but nothing is written into it unless you choose it as the export folder in step 6. Most people export somewhere else, so the results folder stays untouched.

### `logs/` — the Run log, saved

<!-- data: folder logs/ -->

**In short:** The messages from the Run log at the bottom of the window, saved by day. Only exists once something was logged.

**What it means:** `logs/session_<date>.log` has one line per message: the time, a tab, then the message. A new file starts each day. It is separate from the logs inside each run folder. Safe to delete, and **Overwrite** treats the folder as CellQuant's own.

### `runs/run_…/` and `working/`

Each press of **Run** that starts a new batch creates a run folder, `runs/run_xxxxxxxx/`. It is a record of that batch and does not change afterwards (apart from the run record being finished). The `working/` folder holds the open results you see in the app, updated as you edit and review. It has the same layout as a run folder, without `run.json`, `recipe_snapshot.yaml` and the logs. The first analysis keeps its open results in `working/`. Each further analysis keeps them in `analyses/<analysis>/working/`. Per-image files are named with the `image_id` (`img_6f553c7b` below), shown as `<image>` in this glossary.

| File | What it is |
|---|---|
| `run.json` | The run record (below) |
| `recipe_snapshot.yaml` | Settings used |
| `labels/<image>_automated.tif` | Objects as found by the program, before your edits |
| `labels/<image>_final.tif` | Objects after your edits. **Use this one.** |
| `measurements/<image>.csv` | One row per object (the object table) |
| `summaries/<image>.csv` | One row for the image (percent positive) |
| `reports/<image>.csv` | The calculated percentages, one row each |
| `classifications/<image>_phenotypes.csv` | How many objects have each exact marker pattern |
| `classifications/<image>_combinations.csv` | How many objects are positive for each marker and each combination of markers |
| `provenance/<image>.json` | Full record of how this image was analyzed |
| `qc/<image>.json` | Quality summary |
| `tables/<image>.json` | Column types of the CSVs above (so they reload correctly; not for people) |
| `edits/<image>.json` | Your edits as they were when this was saved |
| `cutoff_examples/<image>.json` | Objects you marked positive or negative in step 5 (`working/` only) |
| `logs/run.log` | Warnings and notes. Only exists if something was logged |
| `logs/developer.log` | Technical error details. Only exists if an error occurred |

#### `run.json`
<!-- data: json run/run.json -->

**In short:** The record of one run: when it ran, on which files, and how it ended.

**What it means:** It is written when the run starts and rewritten when the run ends. Use it to find out which software version, image files and settings produced the run, and whether anything failed. The export folder gets a copy of the latest one.

**For experts:** `storage.RunRecord`, written by `storage.start_run` and `storage.finish_run`.

**Related:** `recipe_snapshot.yaml`, `input_hashes`

##### `run_id`, `experiment_id`, `recipe_id`

**In short:** The ids of the run, the experiment and the analysis's settings.

**What it means:** `run_id` looks like `run_e402fc66` and is also the folder name. The other two say which experiment and which analysis made the run.

**For experts:** `run_` plus 8 hex characters of a random UUID.

##### `software_version`

**In short:** The CellQuant version that made the run.

##### `start_timestamp`, `completion_timestamp`

**In short:** When the run started and when it ended.

**What it means:** Both are times in UTC. `completion_timestamp` is `null` while the run is still going or if CellQuant closed during it.

##### `input_files`

**In short:** The image files the run used.

**What it means:** The full paths of the included images when the run started.

##### `input_hashes`

**In short:** A fingerprint of each image file, by `image_id`.

**What it means:** It lets you check later that an image file is still the same one that was analyzed. If a file is edited or replaced, its fingerprint no longer matches. The same fingerprint is kept as `content_hash` in `experiment.json`.

**For experts:** SHA-256 of the file bytes (`storage.file_fingerprint`), copied from the image records when the run starts.

**Related:** `content_hash`

##### `processing_status`

**In short:** How the run ended.

**What it means:** `running` while it goes, then `completed` (all images worked), `completed_with_failures` (some failed), `failed` (all failed) or `cancelled` (you stopped it). A cancelled run keeps the images finished before you stopped it.

**For experts:** Set in `controller` at the end of the batch. A run that is not finished stays `running`.

##### `manual_edits`

**In short:** The edits applied to each image in this run.

**What it means:** The edits per `image_id`, in the form of the edits file. An empty list means no edits.

**Related:** `edits/<image>.json`

##### `recipe_hashes`

**In short:** The settings fingerprint used for each image of the run.

**What it means:** Each value is the `recipe_sha256` of one image's result. If two images have different values, they were not made with the same settings.

**Related:** `recipe_sha256`, `settings_index.csv`

##### `warnings`, `errors`

**In short:** Warnings and failures of the run, as plain text.

**What it means:** Each entry starts with the image file name, then the message. Warnings are images that finished with a warning. Errors are images that failed. More detail is in `logs/run.log`.

**Related:** `logs/run.log`

#### `recipe_snapshot.yaml`
<!-- data: yaml run/recipe_snapshot.yaml -->

**In short:** A frozen copy of the settings the run started with.

**What it means:** It has the same content as `recipes/<analysis>.yaml` had when you pressed **Run**. Later changes, such as a cutoff you move in step 5, change the recipes file but not this one. To see what settings made one image's numbers, use the provenance file, which records the cutoffs actually used.

**For experts:** Written once by `storage.start_run`.

**Related:** `recipes/<analysis>.yaml`, `recipe`

#### `labels/<image>_automated.tif`
<!-- data: tif run/labels/<image>_automated.tif -->

**In short:** The objects as the program found them, before your edits.

**What it means:** It is a picture the same size as the analyzed image (a stack of pictures for 3D). Background is `0` and every object is filled with its `object_id` number. It is the starting point that your edits are replayed on.

**Good to know:** Use `_final` for analysis. This file is for going back and for deleted objects: a deleted object is still here.

**For experts:** `int32`, written by `storage.persist_image_result`. Cropped runs are pasted back to the full frame.

**Related:** `labels/<image>_final.tif`

#### `labels/<image>_final.tif`
<!-- data: tif run/labels/<image>_final.tif -->

**In short:** The objects after your edits. Use this one.

**What it means:** A picture the same size as the analyzed image (a stack for 3D). Background is `0`; every object is filled with its `object_id` number. The same `object_id` appears in `measurements/<image>.csv`. Open it in napari or Fiji to see which objects were counted. Deleted objects are `0` here.

**For experts:** `int32`. Equal to `edits.apply_edits(automated, edits)`. Cropped runs are pasted back to the full frame, so centroids and labels match the original image.

**Related:** `object_id`, `labels/<image>_automated.tif`

#### `measurements/<image>.csv`
<!-- data: csv run/measurements/<image>.csv -->

**In short:** The object table: one row per object.

**What it means:** This is the most detailed result. Every found object has a row with its position, size, your marker measurements and its positive/negative calls. It includes objects you deleted, marked `excluded`, so nothing disappears. Everything in the summary files is counted from this table. On export, the tables of all images are joined into `objects.csv`.

**Good to know:** The table has a fixed first part (identity, position and size) and then one column per measurement and per marker. Which columns you see depends on your settings, on 3D, on cropping and on the count area.

**For experts:** Built by `quantify.measure_objects`, `classify_objects` and `generate_phenotypes`, and annotated in `pipeline.assemble_result`. Column types are kept in `tables/<image>.json` so booleans and blanks reload correctly.

**Related:** `image_summary.csv`, `tables/<image>.json`

##### `experiment_id`, `run_id`

**In short:** Which experiment and run the object came from.

**What it means:** `experiment_id` ties the row to the experiment. `run_id` is the run it was made in. It can be blank in files in `working/` that were made before a run was started.

##### `sample_name`, `filename`

**In short:** The name and the file of the image the object is in.

**What it means:** Copied from `experiment.json`. In an exported `objects.csv` they tell you which image each row is from.

##### `image_id`

**In short:** The short id of the image the object is in.

**What it means:** The key that joins this row to the other files of the same image and to `experiment.json`.

##### `object_set`

**In short:** The name of the object type, by default `Cells`.

**What it means:** You can name what you are counting (for example nuclei). It is the same for every row.

**Related:** `object_set`

##### `object_id`

**In short:** The object's number. It matches the number in the label image.

**What it means:** Object 17 in this table is the pixels with value 17 in `labels/<image>_final.tif`. Numbers are not renumbered after deletions, so there can be gaps.

##### `centroid_x`, `centroid_y`

**In short:** Where the center of the object is, from the left and from the top of the image.

**What it means:** In µm if the image has a pixel size, otherwise in pixels. They are measured from the middle of the top-left pixel. Use them to plot where objects are or to check that two tables describe the same objects.

**How it's calculated:**

    centroid_x = mean column of the object's pixels × pixel size
    centroid_y = mean row of the object's pixels × pixel size

The pixel size is 1 without a calibration.

**For experts:** `skimage.measure.regionprops(labels).centroid` times `linear_scale` (`quantify._base_table`). These always describe the segmented object, never a measurement region. In 3D the XY centroid is of the whole object.

**Related:** `in_count_area`

##### `centroid_z`

**In short:** How deep the center of the object is. 3D only.

**What it means:** In µm from the first slice. Blank if the image has no Z step.

**How it's calculated:**

    centroid_z = mean slice index of the object's voxels × Z step (µm)

The first slice has index 0.

**For experts:** `quantify._base_table_3d`; `NaN` (blank) when `pixel_size_z` is missing.

**Related:** `pixel_size_z`

##### `area`

**In short:** How big each object is, seen from above.

**What it means:** Each object is a set of pixels in the label image. Its area is how many pixels it covers, converted to µm² with the image's pixel size. Compare it with Min/Max size in step 2, and use it to spot debris (very small) or two nuclei merged into one (very large).

**How it's calculated:**

    area = pixels in the object × pixel width × pixel height

Without a pixel size the unit is px² (each pixel counts 1). In 3D it is the object's largest cross-section.

**Good to know:** Area counts every pixel of the object, not only bright ones. Objects cut by the image edge look smaller (see `fraction_touching_border`).

**For experts:** `skimage.measure.regionprops(labels).area × pixel_size²` on `labels/<image>_final.tif` (after edits), `quantify._base_table`. 3D: the largest per-slice pixel count × the XY pixel area (`quantify._base_table_3d`, `volume.largest_cross_section`), so size limits mean the same as in 2D. Only equal X and Y pixel sizes are supported.

**Related:** `volume`, `median_area`, `pixel_size_x`

##### `volume`

**In short:** How big each object is in 3D. 3D only.

**What it means:** The space the object fills, in µm³. It needs the Z step. Without a pixel size at all, it is counted in voxels. With a pixel size but no Z step it is blank, and a warning says to enter the Z step in step 1.

**How it's calculated:**

    volume = voxels in the object × pixel width × pixel height × Z step

**For experts:** `regionprops(labels).area` of a 3D label image is the voxel count. `quantify._base_table_3d`.

**Related:** `area`, `pixel_size_z`

##### `area_unit`, `volume_unit`, `length_unit`

**In short:** The unit of this image's size columns, written on every row.

**What it means:** `area_unit` is `um2` (µm²) or `px2` (pixels). `volume_unit` (3D only) is `um3` (µm³), `voxels`, or blank when the image has a pixel size but no Z step. `length_unit` is `um` or `px` and applies to `centroid_x`, `centroid_y` and to length measurements such as `equivalent_diameter`. Images in one experiment can differ, so check these columns before pooling rows from different images.

**Good to know:** Marker brightness columns (`<marker>_mean` and the other intensity statistics) are in the image's own intensity units, not µm. `centroid_z` is always in µm, whatever `length_unit` says, and is blank without a Z step.

**For experts:** `quantify.add_unit_columns`. They follow `spatial_unit` in the provenance file. Results saved by earlier versions have no unit columns; their unit is `spatial_unit`.

**Related:** `spatial_unit`, `area`, `volume`

##### `z_slices`, `z_first`, `z_last`

**In short:** How many slices the object spans and the first and last of them. 3D only.

**What it means:** Slice numbers count from 1. An object in slices 4 to 9 has `z_first` 4, `z_last` 9 and `z_slices` 6. Check them if objects look too thin or too deep.

**For experts:** `z_slices` is the number of slices in which the object has any pixel, so a gap inside the object is not counted (`volume.object_z_extent`).

**Related:** `z_flag`

##### `<marker>_mean`

**In short:** The mean brightness of a marker inside each object.

**What it means:** The marker setup in step 4 makes one such column per marker, named from the channel name in lower case with other characters changed to `_`: channel `AF647` gives `af647_mean`. It is the value the cutoff is compared with (unless you chose the "% of pixels" rule). It is in the image's own brightness units, so values from images taken with different settings are not comparable. With a Z-stack on max projection, each pixel is the brightest value through the slices.

**How it's calculated:**

    mean = Σ pixel values in the object ÷ number of pixels in the object

Σ means sum. If the object has no pixels in the region (for example a region that shrank to nothing) the value is blank.

**Good to know:** Compare it across objects of the same image. A positive cutoff is usually somewhere between the dim and the bright group.

**For experts:** Measurement spec `statistic: mean`, `region: object`, `background: none` (`quicksetup.marker_recipe`). `regionprops(..., intensity_image=channel).intensity_mean` (`quantify._property_statistic`). If two channels give the same name, a number is added (`af647_2_mean`); an empty name becomes `channel_<index>`. You can change region, statistic or background and keep the id.

**Related:** `<marker>_pos`, `<measurement>`, `statistic`

##### `<marker>_pct`

**In short:** The percent of an object's pixels that are bright enough. Only with the "% of pixels" rule.

**What it means:** Instead of the mean, step 4 can call an object positive when at least a set percent of its pixels are at or above a pixel level. This column holds that percent for each object, from 0 to 100. The mean column is kept too, for reference.

**How it's calculated:**

    percent = 100 × pixels with level ≤ value ≤ high ÷ pixels in the region

`level` is the pixel level, `high` the optional upper level, and the value is the pixel's brightness after any background correction.

**Good to know:** It is less affected by one very bright spot than the mean.

**For experts:** `quantify._percent_above`: one division of whole numbers, so a classification "at least N %" is exact at the boundary. Blank when the region has no pixels, when any pixel is not finite, or when a local ring has no pixels. Marker default rule: positive when `<marker>_pct ≥ 50` (`DEFAULT_MIN_PERCENT`, comparison `at_least`).

**Related:** `pixel_level`, `<marker>_pos`

##### `<measurement>`

**In short:** Any other measurement you defined in step 4. The column name is the measurement's id.

**What it means:** One column is added per measurement in the recipe, named by its id. Brightness statistics are in the image's own intensity units. `percent_above` is 0 to 100. Size and position statistics are in µm (µm² for area) if the image has a pixel size, otherwise pixels. The measurement is taken in its region (the object, a shrunk or grown object, or a ring) of its channel.

**How it's calculated:**

    mean        = Σ values ÷ n
    median      = middle value of the sorted values
    min, max    = smallest, largest value
    std         = √( Σ (value − mean)² ÷ n )
    integrated  = Σ values
    area        = n × pixel width × pixel height
    equivalent_diameter = √( 4 × area ÷ π )
    percent_above       = 100 × (pixels from level to high) ÷ n

`n` is the number of pixels in the region and the sums run over those pixels. With a background correction, `corrected = statistic − background`; for `integrated`, `corrected = Σ values − background × n`.

**Good to know:** `integrated` grows with object size; the mean does not. Pick the one that answers your question.

**For experts:** `quantify.measure_objects` with `_statistic_by_object` and `_property_statistic`. `std` is the population standard deviation (`numpy.std`, ddof 0). `median` is `numpy.median`. In 3D, `area` and `equivalent_diameter` use the largest cross-section; the other statistics use every voxel. A global background without a value is the median of pixels outside all objects. A ring background that has no pixels for an object gives a blank value for that object and a warning. The measurement ids cannot equal reserved column names.

**Related:** `statistic`, `region`, `background`, `unmeasured`

##### `excluded`

**In short:** `True` if the object is no longer in the final objects (you deleted it).

**What it means:** Deleted objects stay in the file, so you can see what was removed, but they are not counted anywhere. Its measurements are those of the pixels it had before it was deleted.

**For experts:** True for object ids that are in `_automated` but not in `_final` (`edits.excluded_object_ids`), so erasing every pixel of an object also gives `True`. Deleted objects are measured from the automated labels (`pipeline._append_excluded_objects`).

**Related:** `manual_edit_status`, `unmeasured`

##### `manual_edit_status`

**In short:** Whether you edited this object: `deleted`, `edited` or blank.

**What it means:** `deleted` means you deleted it. `edited` means you painted it, erased part of it or restored it. Blank means it is as found.

**For experts:** `deleted` wins over `edited`; `edited` comes from `edits.edited_object_ids` (`paint`, `erase`, `restore`).

##### `at_crop_edge`

**In short:** `True` if the object touches the edge of a crop box and may be cut. Only when cropping was used.

**What it means:** With cropping on, objects are found inside rectangles. An object that touches a rectangle edge which is not the image border may have been cut. It is flagged, never removed. Compare these with the rest before trusting their size.

**For experts:** `crop.objects_at_crop_edges`. The column exists only when the result has crop rectangles. A warning is added to the QC file when any object is flagged.

**Related:** `crop`

##### `z_flag`

**In short:** A hint for 3D objects that may be wrong: `one_slice` or `possibly_merged`.

**What it means:** `one_slice` means the object is in only one slice of a stack. It may be part of a nucleus that did not link, or a nucleus at the top or bottom of the stack. `possibly_merged` means the object is much deeper than nuclei are wide, which often means nuclei stacked in Z were linked into one. The column is blank for objects with no flag. It is for checking; counts are unchanged.

**How it's calculated:**

    possibly_merged ⇔ z_slices × Z step > 1.6 × typical width   and   z_slices > 2
    typical width = median of √(4 × area ÷ π) over the objects that are not deleted

**For experts:** `volume.flag_z_problems`. `one_slice` needs `z_slices ≤ 1` and more than one plane in the file. `possibly_merged` needs a Z step and an XY pixel size (without the XY size the width cannot be compared). The ratio is `volume.DEPTH_FLAG_RATIO`. Warnings with the counts are added to the QC file.

**Related:** `z_slices`

##### `in_count_area`

**In short:** `False` if the object is outside the count area you drew. Only when a count area is drawn.

**What it means:** Objects outside the count area stay in the table but are left out of every count and every denominator. `True` means the object is counted (if nothing else rules it out).

**How it's calculated:**

    in_count_area = centroid inside at least one count-area polygon

**Good to know:** An object that is half inside counts or not by where its center is.

**For experts:** `count_area.mark_objects` (`skimage.measure.points_in_poly` on the centroid in pixels). Blank is treated as inside (`count_area.outside`). The column is removed when no polygon is drawn.

**Related:** `count_area`, `total_objects`

##### `<marker>_pos`

**In short:** Whether the object is positive for the marker: `True`, `False` or blank.

**What it means:** This is the call that every percent is built from. It compares the marker's measurement with the cutoff you set in step 5. The column is named by the marker id, for example `af647_pos`. Blank means the object could not be measured, so it is neither positive nor negative.

**How it's calculated:**

    positive ⇔ measurement > cutoff       (or ≥ cutoff with "at least")
    an object called by hand takes that call instead

**Good to know:** Equal to the cutoff is negative unless the marker uses the "at least" rule. If you move a cutoff, only this column and what is counted from it change; nothing is measured again. Objects called by hand keep their call.

**For experts:** `quantify.classify_objects`: a nullable boolean column; values that are not finite stay missing, and `unmeasured` is set. `quantify._positive` uses `>` for `comparison: above` and `≥` for `at_least`.

**Related:** `threshold`, `comparison`, `unmeasured`, `phenotype`, `cell_calls`

##### `<marker>_pos_by_hand`

**In short:** `True` when the object's call for the marker was set by hand instead of by the cutoff.

**What it means:** Only present for markers with at least one call by hand in the image. `<marker>_pos` then holds the call you made, whatever the measurement.

**For experts:** Added by `quantify.classify_objects` for each marker whose `cell_calls` has an object still in the table.

**Related:** `cell_calls`, `n_called_by_hand`

##### `unmeasured`

**In short:** `True` if a marker could not be measured for the object. Such objects are not counted.

**What it means:** It happens when a measurement has no value for the object, for example no pixels in its ring or region. It is not the same as negative: the object is left out of every count and denominator, and the number left out is `n_unmeasured` in the summary.

**For experts:** True when any classification column is missing (`quantify._unmeasured_mask`). `quantify.count_unmeasured` does not count deleted objects or objects outside the count area.

**Related:** `n_unmeasured`, `excluded`

##### `phenotype`

**In short:** All of an object's marker calls in one piece of text.

**What it means:** For example `AF647+|mCherry-` means positive for AF647 and negative for mCherry. `?` means the marker could not be measured. It uses the markers' names, not their ids. Use it to filter or count by exact pattern in a spreadsheet.

**For experts:** `quantify.generate_phenotypes`. Names are joined with `|` in recipe order. Empty text if the recipe has no classifications.

**Related:** `<marker>_pos`

#### `summaries/<image>.csv`
<!-- data: csv run/summaries/<image>.csv -->

**In short:** One row for the image: its object count and the percent positive of each report.

**What it means:** These are your final numbers for one image. The columns are the image's name, how many objects were counted, how many were left out, whether there was a warning, your group columns and then four columns for each report. On export, the rows of all images are joined into `image_summary.csv`.

**For experts:** `quantify.image_summary_row`.

**Related:** `image_summary.csv`

##### `sample_name`, `filename`, `image_id`

**In short:** The image the row is about.

##### `total_objects`

**In short:** The number of objects that count.

**What it means:** Objects that are not deleted, are inside the count area and could be measured. It is also the denominator of "all objects" reports.

**How it's calculated:**

    total_objects = objects with excluded = False, inside the count area, and unmeasured = False

**For experts:** `pipeline._qc_report` (`n_objects`), the same number as `n_objects` in the QC file.

**Related:** `n_objects`, `report_N_denominator`

##### `count_area`, `count_area_unit`

**In short:** The size of the counting region you drew, and its unit. Only when a count area is drawn.

**What it means:** In µm² (or px² without a pixel size); `count_area_unit` says which (`um2` or `px2`). Use it to turn counts into counts per area, for example cells per mm².

**How it's calculated:**

    count_area = pixels covered by the polygons × pixel width × pixel height

**Good to know:** Overlapping polygons count once. Parts outside the image are not counted.

**For experts:** `count_area.area`, rasterised with `skimage.draw.polygon2mask` on the image's rows and columns.

**Related:** `in_count_area`

##### `n_unmeasured`

**In short:** How many objects were left out because a marker could not be measured.

**What it means:** Normally 0. A higher number means some objects had no value for a marker, so the percentages are of the rest. A warning is added to the QC file.

**Related:** `unmeasured`

##### `n_called_by_hand`

**In short:** How many counted objects have at least one marker called by hand.

**What it means:** Normally 0. A higher number means some calls in this image were set in step 5 **Edit cells…** rather than by the cutoff.

**For experts:** `quantify.count_by_hand`: counted objects with any `<marker>_pos_by_hand` True.

**Related:** `cell_calls`, `<marker>_pos_by_hand`

##### `qc_status`

**In short:** `success` or `warning`.

**What it means:** `warning` means there is at least one note worth reading in `qc/<image>.json` or `logs/run.log`.

**Related:** `status`

##### `<column>`

**In short:** Your own group columns from step 1, such as `group` or `Folder 1`.

**What it means:** One column per entry in the image's `user_metadata`. They are what you pick to group by when exporting.

**Related:** `user_metadata`, `grouped_by_<column>.csv`

##### `report_N_count`

**In short:** The number of objects that match the report's numerator.

**What it means:** `N` is 1, 2, 3 … in the order of the reports. This is the top of the fraction. Only counted objects that also match the denominator are included.

**How it's calculated:**

    count = number of counted objects that match the numerator and the denominator

**Related:** `report_N_percent`, `report_N_denominator`

##### `report_N_denominator`

**In short:** The number of objects the percent is out of.

**What it means:** A whole number: the counted objects that match the report's denominator. For "all objects" it equals `total_objects`.

**Good to know:** Older summaries stored the denominator as text. Pooled export values are blank for those.

**Related:** `total_objects`

##### `report_N_percent`

**In short:** The percent for the report. This is usually the number you report.

**What it means:** The share of the denominator that matches the numerator. Blank if the denominator is 0. Compare it across images, groups or your own controls.

**How it's calculated:**

    percent = count ÷ denominator × 100

**For experts:** `quantify.summarize_image`; `float("nan")` (blank in the CSV) when the denominator count is 0.

**Related:** `report_N_count`, `percent`, `<report>_mean_of_per_image_percent`

##### `report_N_numerator`, `report_N_denominator_expression`

**In short:** The report written in words, for example `af594_pos AND af647_pos` over `af594_pos`.

**What it means:** The exact expressions behind the numbers, so you can check what was counted. `all_objects` means every counted object.

**Related:** `reports`

#### `reports/<image>.csv`
<!-- data: csv run/reports/<image>.csv -->

**In short:** The same reports as the summary, in tall form: one row per report.

**What it means:** The same numbers as the `report_N_…` columns of the summary, laid out one report per row. This is easier to read for many reports.

**Related:** `summaries/<image>.csv`

##### `report`

**In short:** The report's name: `report_1`, `report_2`, …

**What it means:** The order is the order of the reports in the recipe. `report_N_…` in the summary uses the same N.

##### `numerator`, `denominator`

**In short:** The report's two expressions, as text.

**What it means:** For example `af594_pos AND af647_pos` and `all_objects`. The numerator is checked inside the denominator.

##### `count`, `denominator_count`

**In short:** The top and the bottom of the fraction.

**What it means:** `count` is the number of counted objects matching both expressions; `denominator_count` the number matching the denominator.

##### `n_unmeasured`

**In short:** How many objects were left out because a marker could not be measured.

**What it means:** Normally 0. It comes from the calculation in step 6 Results. Objects that count but have no value for a marker used by a classification cannot be called positive or negative, so they are left out of the counts. A number above 0 is worth a look at step 5 Check, because the percentages are then of the remaining objects.

**Related:** `unmeasured`

##### `n_called_by_hand`

**In short:** How many counted objects have at least one marker called by hand.

**What it means:** Normally 0. It comes from step 5 **Edit cells…**, where single objects can be called positive or negative instead of by the cutoff.

**Related:** `cell_calls`, `<marker>_pos_by_hand`

##### `percent`

**In short:** `count ÷ denominator_count × 100`. Blank if the denominator is 0.

**What it means:** The same value as `report_N_percent` in the summary.

**How it's calculated:**

    percent = count ÷ denominator_count × 100

#### `classifications/<image>_phenotypes.csv`
<!-- data: csv run/classifications/<image>_phenotypes.csv -->

**In short:** How many objects have each exact marker pattern.

**What it means:** One row for every pattern of positive and negative calls, for example `AF647+|mCherry-`, including patterns with 0 objects. Each object counts in exactly one row, so the percents add up to 100. Use it to see how the population splits.

**Good to know:** With 2 markers there are 4 rows, with 3 there are 8. With more than 6 markers the table is not made (header only) and a warning says so. The requested reports are still calculated.

**For experts:** `quantify._phenotype_tables`, over counted objects only (the `exclusive` table, `ImageResult.phenotype_counts`). Rows follow `itertools.product((+, −))` in recipe order.

**Related:** `phenotype`, `classifications/<image>_combinations.csv`

##### `phenotype`

**In short:** The pattern of positive (`+`) and negative (`-`) calls, with markers joined by `|`.

**What it means:** Each row names one exact pattern of marker calls, for example `AF647+|mCherry-`. The file is made after the markers are set in step 4 and shown in step 6 Results. Look at it to see how the objects split into every possible combination.

##### `count`

**In short:** How many counted objects have this pattern.

**What it means:** The number of objects that have the pattern named in `phenotype`. Only objects that count are included, so deleted objects, objects outside the count area and unmeasured objects are not. The counts of all rows add up to `total_objects`.

##### `percent_of_objects`

**In short:** The share of counted objects with this pattern.

**What it means:** The share of all counted objects that have this pattern. Because each object is in exactly one row of this file, the percents add up to 100. Use it for a quick look at the makeup of the population in step 6 Results.

**How it's calculated:**

    percent_of_objects = count ÷ counted objects × 100

Blank when no object is counted.

**For experts:** `quantify._percent`.

#### `classifications/<image>_combinations.csv`
<!-- data: csv run/classifications/<image>_combinations.csv -->

**In short:** How many objects are positive for each marker and each combination of markers.

**What it means:** One row for each marker on its own (`AF647+`, `mCherry+`) and for each combination of markers (`AF647+|mCherry+`). The other markers may be positive or negative. One object counts in several rows, so the percents do not add up to 100. Use it to see the overlap between markers.

**For experts:** Every non-empty subset of the classifications, by size and then recipe order (the `combination` table, `ImageResult.combination_counts`). Same columns as `_phenotypes.csv`, and the same limit of 6 markers.

**Related:** `classifications/<image>_phenotypes.csv`

#### `qc/<image>.json`
<!-- data: json run/qc/<image>.json -->

**In short:** A small quality summary of one image.

**What it means:** It tells you whether the image needs a second look: how many objects were counted, how big they are typically, how many touch the edge, and any warnings. Its `status` is what shows as a warning in the app.

**For experts:** `pipeline.QCReport`, written by `storage._qc_to_json`. Values that are not finite are saved as `null`.

##### `status`

**In short:** `success` or `warning`.

**What it means:** `warning` if there is any message in `warnings`, or if no object was counted. Otherwise `success`.

**For experts:** `warning` is also set when segmentation found nothing, when everything was excluded or unmeasured, and when nothing is inside the count area.

**Related:** `warnings`

##### `n_objects`

**In short:** The number of objects that count.

**What it means:** The same as `total_objects` in the summary.

##### `median_area`

**In short:** The middle object size.

**What it means:** Half of the counted objects are smaller and half larger. It is in µm² (px² without a pixel size). Compare it between images that should be alike: a big change often means a segmentation or magnification problem. Blank (`null`) when no object counts.

**How it's calculated:**

    median_area = median of area over the counted objects

**For experts:** `pandas` median (`Series.median()`) of the `area` column over objects that are not deleted, not unmeasured and inside the count area.

**Related:** `area`

##### `fraction_touching_border`

**In short:** The share of objects that touch the edge of the image (0 to 1).

**What it means:** Objects cut by the edge look smaller and may be only part of a nucleus. A high fraction tells you to check the edge. Blank (`null`) when no object counts.

**How it's calculated:**

    fraction = objects touching an image edge ÷ objects in the final label image

**For experts:** `pipeline._qc_report`: an object touches when its bounding box reaches row 0, column 0, the last row or the last column. The denominator is every object in `_final.tif`, including objects outside the count area or unmeasured. In 3D only the XY edge counts, not the top and bottom slice.

**Related:** `area`

##### `percent_excluded_by_size`

**In short:** The percent of found objects that were dropped by your Min/Max size.

**What it means:** The step 2 size limits remove objects that are too small or too large. A high number means the limits remove a lot, which may mean they are too strict. `null` if no objects were found or the objects were loaded from files.

**How it's calculated:**

    percent_excluded_by_size = (found − kept) ÷ found × 100

`found` is the number of objects before the size filter. `kept` is the number after it.

**For experts:** `segmentation.filter_objects`. The filter also applies "exclude border" and, in 3D, the minimum number of slices. An object is removed when its area is `< min` or `> max`. The value is carried with the saved objects, so it is the same on later measurements.

##### `warnings`, `errors`

**In short:** Notes about the image in plain words, and failures.

**What it means:** `warnings` can include: no objects found, objects left out because a marker was not measurable, a measurement without pixels for some objects (with their ids), objects cut by a crop edge, a Z-stack with many one-slice or too-deep objects, a missing Z step, or a segmentation engine note. `errors` is normally an empty list.

**Related:** `logs/run.log`

#### `provenance/<image>.json`
<!-- data: json run/provenance/<image>.json -->

**In short:** The full record of how one image's result was made.

**What it means:** Everything needed to repeat or check the result: the program and version, the complete settings, the image and its calibration, how objects were found, and what was edited. Keep it with your data. When a reviewer asks what you did, this is the file to look at.

**Good to know:** If you move a cutoff after the run, the provenance file records the cutoffs actually used for the numbers.

**For experts:** Assembled in `pipeline.assemble_result`; the settings part is refreshed by `pipeline.reclassify_result` when cutoffs change.

##### Program

###### `software`

**In short:** Always `cellquant`.

###### `software_version`

**In short:** The CellQuant version that made this result.

###### `analysis_engine`

**In short:** The name of the function that produced the result.

**What it means:** Always `cellquant.pipeline.process_image`. It names the single code path used by interactive work and batch runs.

###### `timestamp`

**In short:** When this result was made.

**What it means:** A time in UTC. It is updated if the result is classified again with new cutoffs.

##### Settings

###### `recipe`

**In short:** The complete settings used for this image.

**What it means:** The same content as the recipe file, with the image's own cutoffs. Use it to see every setting at once.

**Related:** `recipes/<analysis>.yaml`

###### `recipe_sha256`

**In short:** A fingerprint of the settings that change the numbers.

**What it means:** Two images with the same fingerprint were analyzed with the same settings. It is also used to detect that settings changed since an image was approved. It is shown in `settings_index.csv` on export, and `mixed_settings.txt` warns when images differ.

**How it's calculated:**

    recipe_sha256 = SHA-256 of the settings that change the numbers, as sorted compact JSON

These are: the Z-stack mode and slice (and the 3D settings only for the modes that use them), the object-finding channel, method and parameters, the crop settings when cropping is on, every measurement, every classification (including its cutoff for this image) and every report. Names, ids, dates, the software version and the automatic-cutoff choices are left out.

**Good to know:** Moving a cutoff changes the fingerprint of the image that uses it.

**For experts:** `recipe.Recipe.content_hash` over `scientific_dict()`. The approval value is this plus `:` and the edit count (`controller._approval_fingerprint`). Settings that equal their default in newer fields are omitted from the JSON, so older recipes keep their hash.

**Related:** `approved_settings_sha256`, `settings_index.csv`

###### `measurement_definitions`

**In short:** The measurements that were taken, as in the settings.

**What it means:** The list of measurement entries (`id`, `name`, `channel`, `region`, `statistic`, `background`, and the pixel levels when used). Each `id` is a column in the object table.

**Related:** `measurements`

###### `classification_definitions`

**In short:** The positive/negative rules that were used, with this image's cutoffs.

**What it means:** The list of classification entries: the `id`, `name`, `measurement`, `threshold` and `comparison`. Use it to see the exact cutoff behind each `_pos` column.

**Related:** `classifications`

##### Image

###### `experiment_id`, `run_id`, `image_id`, `sample_name`, `filename`, `source_path`, `user_metadata`

**In short:** Which image this is, the same as in `experiment.json`.

**What it means:** `run_id` can be `null` for results made outside a run.

###### `n_channels`, `channel_names_in_file`, `channel_axis_source`

**In short:** How many channels the image has, what they are called, and how CellQuant knew which dimension is the channel.

**What it means:** `channel_axis_source` says where that was decided: `nd2_metadata` (the ND2 file says so), `tiff_axes` (the TIFF's axes tag), `inferred` (guessed from the shape), `argument` (chosen by you) or `single_plane` (the image has one channel).

###### `position`, `objective`

**In short:** The stage position in the file and the objective, as in `experiment.json`.

###### `image_shape_yx`

**In short:** The image size in pixels, height then width.

**What it means:** The height and width of the image in pixels, as read when the image was opened in step 1 Images. It is the size of the frame that objects were searched in. Use it to check that two images are the same size, or that loaded label files fit.

###### `pixel_size_x_um`, `pixel_size_y_um`, `pixel_size_z_um`

**In short:** The pixel sizes used, in µm.

**What it means:** `null` where unknown. They are what turned pixels into µm and µm² in this result.

**Related:** `pixel_size_x`

###### `spatial_unit`

**In short:** `um` if sizes are in µm, `px` if the image has no pixel size.

**What it means:** It tells you the unit of `area`, `centroid_x`, `centroid_y`, `volume` and the other size columns. The object table repeats it per row as `area_unit`, `volume_unit` and `length_unit`.

##### Z-stack

###### `z_planes_in_file`

**In short:** How many Z slices the file has.

**What it means:** The number of focal planes (Z slices) stored in the image file. A flat image has 1. It is the starting point for the Z-stack choice in step 2 Find objects. Compare it with the slice numbers in `z_description`.

###### `z_handling`

**In short:** How the Z-stack was used: the Z-stack mode.

**What it means:** `max_projection`, `single_plane`, `stitch_slices` or `full_3d`. For a single-plane image the value is `none`.

**Related:** `z_stack`

###### `z_index`

**In short:** The slice used, counting from 0, when one slice was analyzed. Otherwise `null`.

**What it means:** Which slice was analyzed when you chose a single slice in the Z-stack settings of step 2. Counting starts at 0, so 0 is the first slice. If you did not pick one, the middle slice is used. It is `null` for a max projection and for the 3D modes.

###### `z_description`

**In short:** The Z handling in plain words.

**What it means:** For example `max projection of 7 slices`, `slice 3 of 7`, `3D, 7 slices linked slice by slice`, `3D, 7 slices as one volume` or `single plane`.

###### `analysis_dimensions`

**In short:** 2 or 3: whether the objects are flat or 3D.

**What it means:** Tells you whether the objects were found as flat shapes in a 2D image (2) or as solid shapes through the stack (3). It follows from the Z-stack mode chosen in step 2 Find objects. It is 3 only for the two 3D modes, `stitch_slices` and `full_3d`. It tells you how to read size columns such as `area` and `volume`.

###### `z_stitch_threshold`, `z_scale_brightness`, `z_min_slices`

**In short:** The 3D settings used. Otherwise `null`.

**What it means:** They only matter for the modes that use them: `z_stitch_threshold` for `stitch_slices`, the other two for both 3D modes. Otherwise `null`.

###### `anisotropy`

**In short:** The Z step divided by the XY pixel size, for 3D results.

**What it means:** How much taller each voxel is than it is wide. A value of 1 means the Z step equals the pixel size, and larger values mean the slices are further apart. It is recorded for 3D results from step 2 Find objects. It matters because a large value makes 3D object finding less reliable.

**How it's calculated:**

    anisotropy = Z step (µm) ÷ XY pixel size (µm)

`null` when either is unknown or the result is not 3D.

##### Finding objects

###### `segmentation_algorithm`

**In short:** The method that found the objects: `classical` or `cellpose`.

**What it means:** The kind of object finder chosen in step 2 Find objects. `classical` uses thresholds and shapes, and `cellpose` uses a trained model. Look at it when you compare results made with different methods.

###### `segmentation_engine`

**In short:** What ran, for example the Cellpose model, version and device.

**What it means:** Details about the engine that found the objects. For objects loaded from a file it names the file. It is `null` for the classical method or when saved objects were reused.

###### `segmentation_parameters`

**In short:** The settings of the object-finding method.

**What it means:** The method's `parameters` from the recipe, such as the diameter, Min/Max size or threshold.

###### `segmentation_channel`

**In short:** The channel the objects were found in, counting from 0.

**What it means:** The number of the channel that step 2 Find objects used to find the objects, for example the nuclear stain. The first channel is 0, so it is one less than the channel number you may count in your head. The export shows the channel name instead, in `all_analyses_image_summary.csv`.

###### `segmentation_seconds`, `segmentation_timings`

**In short:** How long finding the objects took, in seconds, and by stage.

**What it means:** Recorded when the objects were found in this session. `null` when saved objects were reused.

###### `diameter_px_used`

**In short:** The object diameter in pixels that Cellpose used.

**What it means:** If you gave a diameter in µm it is converted with this image's own pixel size. If Cellpose estimated it, the estimate is shown. `null` when no diameter was used.

**How it's calculated:**

    diameter_px = diameter_um ÷ pixel size (µm per pixel)

**For experts:** `pipeline.cellpose_diameter_px`; `null` when the engine did not resize to the diameter.

###### `segmentation_key`

**In short:** A fingerprint of the object-finding settings. It links the result to `.cache` and to your edits.

**What it means:** Edits are only applied while this key is the current one. The cache file names use it too.

**Related:** `segmentation`, `.cache/`

##### Crop

###### `crop_rectangles`

**In short:** The boxes the objects were searched in, in pixels. `null` means the whole frame.

**What it means:** Each box is `[row start, row end, column start, column end]`. The end numbers are not included.

**Related:** `crop`

###### `crop`

**In short:** Facts about how the crop was made, when recorded.

**What it means:** `fraction` is the share of the frame inside the boxes (0 to 1). `skipped` is `true` if the boxes covered too much and the full frame was used instead. `seconds` is the time spent finding the boxes. `note` explains in words when cropping was not applied.

##### Loaded objects

These keys are only present when the objects were loaded from label files instead of found by CellQuant.

###### `segmentation_origin`

**In short:** `loaded` when the objects came from a file.

**What it means:** Says that the objects were not found by CellQuant but loaded from label files you supplied in step 2 Find objects. The key is absent for objects CellQuant found itself. Look at it when you want to know if a result used your own segmentation.

###### `segmentation_source`

**In short:** The label file the objects were loaded from.

**What it means:** The path of the label file that supplied the objects. It is recorded when the objects are loaded in step 2 Find objects. Use it to trace which file the objects came from.

###### `segmentation_settings`

**In short:** The object-finding settings in force when the file was loaded.

**What it means:** The Z-stack settings and the object set. If they change later, the loaded objects no longer fit and are not reused.

###### `segmentation_input_sha256`

**In short:** A fingerprint of the image the objects were loaded for.

**What it means:** A fingerprint (SHA-256) of the image file the loaded objects were made for. CellQuant compares it with the image on later runs. If the image has changed, the loaded objects no longer fit and are not reused.

###### `segmentation_calibration`

**In short:** The pixel sizes (x, y, z) at the time of loading.

**What it means:** The pixel sizes in µm (x, y, z) the image had when the objects were loaded. CellQuant compares them with the current pixel sizes from step 1 Images. If they differ, the loaded objects are not reused, because sizes would no longer be right.

##### Count area

These keys are only present if a count area is drawn.

###### `count_area`

**In short:** The counting polygons, as `[row, column]` points in pixels.

**What it means:** The corners of the counting regions you drew, in pixels. You draw them on the image while working on it. Objects are only counted if they fall inside. Use it to redraw or check the region.

**Related:** `in_count_area`

###### `count_area_size`

**In short:** The size of the counting region, in µm² (px² without a pixel size).

**What it means:** The same number as `count_area` in the summary.

##### Result

###### `warnings`

**In short:** The same warnings as in the QC file.

**What it means:** The notes about problems found while making this image's result, such as no objects found or objects that could not be measured. The same list is in the QC file. Check them in step 5 Check before you trust the numbers.

###### `manual_edits`

**In short:** The edits applied to produce the final objects.

**What it means:** The list of edits in the form of the edits file.

###### `excluded_object_ids`

**In short:** The ids of the objects you deleted.

**What it means:** A sorted list. It matches the rows where `excluded` is `True`.

###### `hand_calls_sha256`

**In short:** A checksum of the calls made by hand in this image. Only present when there are any.

**What it means:** The calls themselves are in `classification_definitions` (`cell_calls`). The checksum changes whenever a call is added, changed or taken back, so two provenance files with the same checksum used the same calls.

**For experts:** SHA-256 of the image's `cell_calls` per marker, as sorted JSON (`pipeline._hand_calls_digest`).

**Related:** `cell_calls`

#### `tables/<image>.json`
<!-- data: json run/tables/<image>.json -->

**In short:** The column types of the CSV files, so they reload correctly. Not for people.

**What it means:** CSV files lose the difference between blank text and a missing value, and between `True` and the text "True". This file records it. Ignore it unless you write your own reader.

**For experts:** `storage.describe_table` and `storage.read_table`.

##### `format`

**In short:** The version of this file's layout (currently 1).

##### `tables`

**In short:** For each saved table, its columns and number of rows.

**What it means:** The keys are `objects`, `summary`, `phenotype_counts`, `combination_counts` and `reports`. Each has `columns` (the `name` and pandas `dtype` of every column, and `missing_rows` for text or boolean columns that had blanks) and `rows` (the row count).

#### `edits/<image>.json`
<!-- data: json run/edits/<image>.json -->

**In short:** Your edits to this image, as they were when this result was saved.

**What it means:** The edits that applied to this result: those made on these found objects and in this analysis. The file in the results folder's `edits/` folder holds all of them. The fields are the same.

**Related:** `operation`, `segmentation`

#### `cutoff_examples/<image>.json`

**In short:** The objects you marked positive or negative for each marker in this image (Examples mode in step 5). Only in `working/`.

**What it means:** One entry per marker id, then one per object number: `positive` (true or false) and the object's `x` and `y` center when it was marked. A mark whose object is gone, or whose number now belongs to an object elsewhere (after the image is run again), is ignored. The cutoff itself is saved in the settings (`image_thresholds`), so deleting this file keeps the cutoff.

**How it's calculated:**

    suggested cutoff = halfway across the widest gap between neighbouring example values
                       among the gaps that call the most examples right

**For experts:** `cutoff_examples.suggest_cutoff`, `AnalysisController.mark_example`. A mark is kept when the object's center is within half its radius (`0.5 × √(area / π)`) of where it was.

**Related:** `cutoff_by`, `image_thresholds`

#### `logs/run.log`
<!-- data: log run/logs/run.log -->

**In short:** Warnings and notes from the run, one line each. Only exists if something was logged.

**What it means:** Read it to find out why an image shows a warning or failed. Each line has the time, the image file name, the stage and the message, separated by tabs.

**Related:** `warnings`

#### `logs/developer.log`
<!-- data: log run/logs/developer.log -->

**In short:** Technical error details. Only exists if an error occurred.

**What it means:** The same lines as `run.log` for failures, followed by the technical trace. Send it along if you report a bug.

## Export folder

**Export results** in step 6 writes tidy copies of the results into a folder you choose, ready for a spreadsheet or R. Nothing in it is used by CellQuant to reopen your work; the results folder is for that. Here is what it writes:

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

**Export all analyses** makes one subfolder per analysis (the same files as above), plus three files in the main folder. They are described below.

There are two ways to average images, and they give different numbers. The **mean of per-image percents** treats every image the same, however many objects it has. The **pooled percent** adds up all counts and divides by all denominators, so an image with more objects weighs more. Report which one you used.

### `objects.csv`

**In short:** All objects of all images in one table.

**What it means:** The object tables of every included image, joined one after another. The columns are the ones described for the object table, so a long exported table is the place to filter by image, group or marker. Deleted objects are still in it, marked `excluded`.

**Related:** `measurements/<image>.csv`

### `image_summary.csv`

**In short:** One row per image, with the counts and percents of every report.

**What it means:** The image summaries of every included image, joined. This is the file most people start from. Its columns are described under the summary file. Your group columns (such as `group` or `Folder 1`) are in it, which is what the grouped files are made from.

**Related:** `summaries/<image>.csv`

### `grouped_by_<column>.csv`

**In short:** The results per condition: one row per value of the group column you chose.

**What it means:** Use it to compare control and treated. Each report gets two kinds of numbers: the average of the images' percents and the pooled percent. They answer slightly different questions, which is why they are in differently named columns. `<report>` below is `report_1`, `report_2`, and so on.

**For experts:** `storage.grouped_summary`. Groups come from `groupby(column, dropna=False)`, so images with a blank group value form their own group.

**Related:** `image_summary.csv`

#### `<column>`

**In short:** The group value for this row, in a column named after the group column.

**What it means:** For example a column `group` with values `control` and `treated`. It is one of the columns you set in step 1.

#### `n_images`, `summary_note`

**In short:** How many images are in the group, and a reminder of the two ways to average.

**What it means:** `n_images` counts every image in the group, including images that have no value for a report. `summary_note` is a sentence on each row that says what the mean-of-percents and pooled columns are. In older results it also says that pooled values are blank where an image's summary stored the denominator as text.

#### `<report>_mean_of_per_image_percent`, `<report>_sd_of_per_image_percent`

**In short:** The average of the images' percents in the group, and how much they vary.

**What it means:** Each image counts once. The SD (standard deviation) tells you how spread out the images are. It is blank when the group has fewer than two images with a value. Images with a blank percent are left out.

**How it's calculated:**

    mean = Σ percentᵢ ÷ n
    sd   = √( Σ (percentᵢ − mean)² ÷ (n − 1) )

`percentᵢ` is the report's percent in image i, and `n` is the number of images in the group that have a percent.

**Good to know:** With few images, treat the SD with care. The SD is of images, not of nuclei. Example: three images with 10, 20 and 30 percent give a mean of 20 and an SD of 10 (√((100 + 0 + 100) ÷ 2)).

**For experts:** `pandas` `mean()` and `std(ddof=1)` on `report_N_percent` after `pd.to_numeric`; `NaN` (blank) with no values or, for the SD, with fewer than two.

**Related:** `<report>_pooled_percent`, `report_N_percent`

#### `<report>_pooled_count`, `<report>_pooled_denominator`, `<report>_pooled_percent`

**In short:** The group's counts added up, and the percent made from the sums.

**What it means:** All the images' counts are added up, all denominators are added up, and one percent is made from the sums. Every object weighs the same, so images with many objects count for more. Use it when you want to say "of all nuclei in the group, this share was positive".

**How it's calculated:**

    pooled_percent = Σ countᵢ ÷ Σ denominatorᵢ × 100

The sums run over the images in the group that have both a count and a denominator.

**Good to know:** If a marker was missing from some images, those images are left out of that report (they have a blank count). Example: counts 10, 20 and 30 out of 100 each give 60 ÷ 300 × 100 = 20 percent. If a fourth image has 80 out of 200, the pooled percent is 140 ÷ 500 × 100 = 28, but the mean of the four image percents is 25.

**For experts:** `storage._pool`. Blank when the sum of denominators is 0, when no image has values, or when a summary from an older version stored the denominator as text.

**Related:** `<report>_mean_of_per_image_percent`, `report_N_count`

### `units_by_<unit>.csv`

**In short:** One row per unit (for example per retina), every report pooled over the unit's images.

**What it means:** A unit is the biological sample that has several images, such as one retina with four pictures. This file is useful when you want one number per animal or per retina. The name of the file is the unit column, for example `units_by_Folder 2.csv`. By default the unit is the deepest folder level that holds the images.

**Good to know:** With a group column, a unit is identified by its group and its own value, so "Retina 1" of two conditions stays two units.

**For experts:** `storage.unit_summary`, `storage.default_unit_column` (the highest-numbered `Folder N` column). Old unit files are deleted before a new export, so none are left from an earlier choice.

**Related:** `grouped_by_<column>_equal_units.csv`

#### `<unit>`

**In short:** The unit's own value, in a column named after the unit column.

**What it means:** With a group column, the group column comes first, then this one.

#### `n_images`

**In short:** How many images are in the unit.

**What it means:** How many images went into this unit's pooled numbers. The unit is one biological sample, such as a retina. The images are the ones that share the same unit value from step 1 Images. Look at it to see how much data one unit rests on.

#### `<report>_unit_pooled_count`, `<report>_unit_pooled_denominator`, `<report>_unit_pooled_percent`

**In short:** The unit's counts added up, and the percent made from the sums.

**What it means:** The same method as the pooled columns of the grouped file, but for one unit. An image with more objects weighs more within its unit.

**How it's calculated:**

    unit_pooled_percent = Σ countᵢ ÷ Σ denominatorᵢ × 100

The sums run over the images of the unit.

**Good to know:** Example: one retina has 3 images with 10, 20 and 30 positive out of 100 each. Its unit pooled percent is 60 ÷ 300 × 100 = 20.

**Related:** `<report>_pooled_percent`

#### `<report>_unit_n_images`

**In short:** How many of the unit's images had a value for this report.

**What it means:** It is lower than `n_images` when some images have no value for the report, for example when a marker channel is missing in them.

### `grouped_by_<column>_equal_units.csv`

**In short:** Per group, the mean of its units' percents, so every unit counts once.

**What it means:** This is the number to use when the biological replicate is the unit (the retina, the animal) and not the nucleus. A retina with a few thousand nuclei and one with a few hundred get the same weight. Compare it with the pooled percent in the grouped file, where every nucleus weighs the same. It is only written with a group column and a unit column that are different columns.

**For experts:** `storage.equal_unit_summary`, built from `unit_summary`.

**Related:** `grouped_by_<column>.csv`, `units_by_<unit>.csv`

#### `n_units`

**In short:** How many units are in the group.

**What it means:** The number of units, for example retinas, in the group. It is the n that you would quote for the group in `grouped_by_<column>_equal_units.csv`. Each of those units counts once, however many images or objects it has.

#### `<report>_n_units`

**In short:** How many of the group's units have a value for this report.

**What it means:** The number of units that have a pooled percent for this report. It is lower than `n_units` when a unit has no usable images for the report, for example when the marker is missing. It is the `n` used for the equal-unit mean and SD of the same report.

#### `<report>_equal_unit_mean_percent`, `<report>_equal_unit_sd_percent`

**In short:** The mean of the units' pooled percents, and their SD.

**What it means:** Each unit, for example a retina, gives one pooled percent, and these are averaged so every unit counts once. This is the number to use when the unit is your biological replicate. It comes from the units made in step 6 Results.

**How it's calculated:**

    mean = Σ unit_pooled_percentᵤ ÷ n
    sd   = √( Σ (unit_pooled_percentᵤ − mean)² ÷ (n − 1) )

`unit_pooled_percentᵤ` is the unit's value in `units_by_<unit>.csv` and `n` is the number of units that have a value. This is the sample SD (n − 1). The SD is blank for fewer than two units.

**Good to know:** Three ways to average give three different numbers. Take two retinas. Retina A has 3 images with 10/100, 20/100 and 30/100 positive. Retina B has 1 image with 80/200 positive. The mean of the 4 image percents (10, 20, 30, 40) is 25. The pooled percent adds everything: 140 ÷ 500 = 28. The equal-unit mean first pools each retina (A: 60 ÷ 300 = 20, B: 40) and then averages them: (20 + 40) ÷ 2 = 30, with SD √200 = 14.1. Only the last one gives each retina the same weight.

**For experts:** `storage.equal_unit_summary`: `Series.mean()` and `Series.std(ddof=1)` over `<report>_unit_pooled_percent`; units without a value are skipped.

**Related:** `<report>_unit_pooled_percent`, `<report>_pooled_percent`, `<report>_mean_of_per_image_percent`

### `recipe.yaml`

**In short:** The current settings, as a text file.

**What it means:** A copy of the analysis's settings at the time of export. Keep it with your tables so the settings are known.

**Good to know:** It is the current settings, not a label for every row. If images were made with different settings, `mixed_settings.txt` is written, and `settings_index.csv` tells which settings made which image.

**Related:** `recipes/<analysis>.yaml`

### `settings_index.csv`

**In short:** Which settings made each image.

**What it means:** One row per image. If two images have different fingerprints, they were not analyzed with the same settings.

**Related:** `recipe_sha256`

#### `image_id`, `filename`

**In short:** The image.

#### `recipe_sha256`

**In short:** The fingerprint of the settings that made this image.

**What it means:** A fingerprint of the settings that made this image. If two rows show the same value, the images were analyzed with the same settings. Different values mean the settings changed in between, so compare the images with care.

**Related:** `recipe_sha256`

### `mixed_settings.txt`

**In short:** A note that the images were made with more than one version of the settings. Only written then.

**What it means:** It says that `settings_index.csv` lists the settings fingerprint of each image and that `recipe.yaml` is the current settings, not one label for every row. If all images share one version, an older note is removed.

**Good to know:** Images that differ only because each uses its own channel (a per-image channel choice or another channel layout), or only in a cutoff set for a single image, do not count as different settings.

**For experts:** `controller._settings_versions` compares settings after normalising channels, per-image cutoffs and crop channels.

### `run.json`

**In short:** A copy of the latest run record.

**What it means:** The same as the run folder's `run.json`: software version, image fingerprints and how the run ended. It is the run record of the latest run, not necessarily the run that made every image.

**Related:** `run.json`

### `all_analyses_image_summary.csv`

**In short:** Every analysis's image summary in one table. Only with Export all analyses.

**What it means:** The image summaries of all analyses that have results, one after another, so you can compare analyses. The analysis that found its objects in a different channel is told apart by the two columns added in front.

**Related:** `image_summary.csv`

#### `analysis`

**In short:** The name of the analysis the row came from.

#### `segmentation_channel`

**In short:** The name of the channel the objects were found in.

**What it means:** The image's own channel name, not a number.

### `all_analyses_grouped_by_<column>.csv`

**In short:** The grouped tables of every analysis in one table. Only with Export all analyses and a group column.

**What it means:** Each analysis's grouped table, with an `analysis` column that names the analysis. The columns are those of the grouped file.

**Related:** `grouped_by_<column>.csv`

### `analyses_not_exported.txt`

**In short:** A list of analyses that had no results yet, so were not exported. Only written then.

**What it means:** It starts with a sentence and then lists the analysis names. If every analysis was exported, an older note is removed.
