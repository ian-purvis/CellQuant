# CellQuant v2 — Engineering Manifest, Revision 2

Revision date: 2026-09-28
Supersedes: `CellQuant_v2_architecture_and_manifest.md` (kept unchanged for reference)
Applies to code at: `cellquant` 0.1.0 (22 tests passing on 2026-09-28)

---

## 0. How to use this document

This is the build contract for CellQuant v2. It is written for the engineer (human or AI coding agent) who will extend the existing `cellquant/` package.

- **§1–§4** set scope and the rules that must not be broken.
- **§5** is an audit of the code that already exists: what to keep, what to change, and known defects. Read it before touching code.
- **§6–§19** are the functional requirements.
- **§20–§22** are the milestones, tests and acceptance criteria. Work milestone by milestone; do not start a milestone until the previous one's tests pass.
- **§23** lists decisions that need the lab's sign-off. Build against the stated default until told otherwise.

Keywords: **MUST** = required for the milestone to pass. **SHOULD** = expected unless there is a written reason not to. **MAY** = optional.

The biggest change from the previous manifest: **2D and 3D analysis are both first-class.** The data model is 3D throughout (a 2D image is a stack with one plane), and the user chooses how much compute to spend: a fast 2D analysis that runs on any laptop, or a full 3D analysis for a workstation with a GPU. The software must help them make that choice and must never silently mix the two.

---

## 1. Purpose

A desktop application for segmenting objects (typically nuclei) in multichannel fluorescence microscopy images and quantifying marker expression per object. It must be usable by a researcher who has never quantified images before, and reproducible enough to support published results.

Primary users: Brzezinski Lab members and collaborators analyzing confocal Z-stacks (ND2 and TIFF) of retinal tissue, on hardware ranging from a CPU-only laptop to a CUDA workstation. The software remains biologically generic: no channel names, markers, or tissues are hard-coded.

Pipeline:

```text
Images → Analysis grid (2D or 3D) → Objects → Measurements → Classifications → Phenotypes → Results
```

---

## 2. Rules that must not be broken

These carry forward from the original manifest and are already largely honored by the code.

1. The GUI contains no analysis logic. `process_image(image, recipe)` is the single path used by interactive, batch and command-line runs.
2. Napari layers are never the authoritative data. The object table is.
3. Segmentation, measurement, classification and summary are separate, independently cached stages. Changing a threshold never reruns segmentation or measurement. Changing a denominator only reruns the summary.
4. Source images are never modified. Automated labels are never overwritten; manual edits are an ordered log applied on top.
5. Every exported number is traceable to input file, recipe snapshot, software version, model version, calibration, analysis mode and manual edits.
6. Nothing is guessed silently: not pixel size, not the channel axis, not the Z plane, not 2D-vs-3D. If a value is assumed, the assumption is recorded and shown.
7. No biological names or fixed channel counts are hard-coded.

New rules for this revision:

8. **Dimensionality is explicit.** Every result records which analysis mode produced it (§7). 2D and 3D results are never pooled into one summary without a warning.
9. **Recipes refer to channels by name, not by index** (§8.2).
10. **The interface never freezes.** Any operation that can take more than ~100 ms runs off the GUI thread and shows progress. Dragging a threshold must update in well under a second without touching the disk.

---

## 3. Architecture

Keep the existing three-layer structure.

```text
GUI (cellquant/gui)          — experiment setup, viewing, review, results
   │ calls only
Controller (controller.py)   — recipe validation, caching, runs, resource preflight, background jobs
   │ calls only
Engine (image, segmentation, regions, quantify, pipeline, edits)
                              — pure functions on arrays + recipe; no Qt, no napari
```

New engine modules introduced by this revision:

| Module | Responsibility |
|---|---|
| `cellquant/io/` (replaces `image.py`) | TIFF/OME-TIFF and ND2 readers, calibration status, series/position selection, canonical `(C, Z, Y, X)` arrays |
| `cellquant/analysis_grid.py` | Turns a loaded stack into the grid that is analyzed: single plane, projection, or full volume (§7) |
| `cellquant/resources.py` | Hardware detection and memory/runtime estimates for preflight (§10) |
| `cellquant/thresholds.py` | Control-derived and assisted threshold suggestions (§13.3) |

---

## 4. Technology stack

- Python 3.11 or 3.12
- NumPy, SciPy, pandas, scikit-image, tifffile, pydantic 2, PyYAML (already in use)
- `nd2` for Nikon ND2 files (new, required)
- napari ≥ 0.5 and Qt for the GUI
- Cellpose and PyTorch as the learned segmentation engine (§9.3), in two interchangeable versions installed as separate environments: Cellpose-SAM (Cellpose ≥ 4.2) and classic Cellpose (3.1.x). Decided 2026-09-28 (§23.1).
- pytest

Remove Parquet export (it silently does nothing today because `pyarrow` is not a dependency). CSV is sufficient for Excel, Prism, R and Python. Parquet can return post-MVP.

Dask/Zarr lazy loading is **post-MVP**. For the MVP, images are loaded eagerly one at a time, and the resource preflight (§10) prevents loading something that will not fit in memory.

---

## 5. Audit of the existing code (0.1.0)

### 5.1 Keep as-is (good foundations)

| Area | File | Notes |
|---|---|---|
| Error types with user-facing messages | `errors.py` | Keep pattern; add `ResourceError` (§10). |
| Typed recipe, validation, content hash | `recipe.py` | Keep structure; bump to schema v2 (§8). |
| Backend registry and adapter | `segmentation.py` | Keep `register_segmentation_backend`. |
| Nearest-object expansion, rings, erosion | `regions.py` | Correct in 2D. Generalize to 3D with anisotropic sampling (§11). |
| Measurement, background correction, phenotype and report engine | `quantify.py` | Keep; fix defect D3. Make geometry dimension-aware (§12). |
| Edit log replayed onto automated labels | `edits.py` | Keep model; extend to 3D and change storage (§15). |
| Stage cache with atomic writes and corrupt-file recovery | `cache.py` | Keep; extend keys (§17.2). |
| Experiment manifest, runs, logs (human + developer log) | `experiment.py`, `storage.py` | Keep; fix defect D5. |
| Headless `cellquant run` command | `__main__.py` | Keep. |
| Installer puts the venv under `%LOCALAPPDATA%` (avoids OneDrive and path-length problems) | `packaging/` | Keep this decision; revise the rest (§19). |
| Synthetic-image tests with exact expected counts | `tests/` | Keep; extend (§21). |

### 5.2 Must change (scope gaps)

| ID | Gap | Where | Required change |
|---|---|---|---|
| G1 | Only 2D images accepted; any file with a Z or T axis is rejected. | `image.py` `normalize_array` | Canonical `(C, Z, Y, X)` for all images; 2D = Z of 1 (§6). |
| G2 | No ND2 support. | `image.py`, `experiment.add_images` (only `.tif/.tiff` discovered) | Add ND2 reader, series/position handling (§6.2). |
| G3 | Cellpose adapter uses the v3 API (`models.Cellpose`, `channels=[0,0]`, `do_3D=False`), which no longer exists in Cellpose 4. Seed handling only sets NumPy, not Torch. | `segmentation.CellposeBackend` | Rewrite for Cellpose 4 with 2D, 3D and stitched modes (§9.3). |
| G4 | Physical units require equal X/Y pixel size; no Z spacing is used anywhere. | `regions.isotropic_pixel_size` | Carry `(z, y, x)` spacing; use anisotropic distance transforms (§11). |
| G5 | Recipes refer to channels by index. | `recipe.py` | Refer by channel name (§8.2). |
| G6 | Cache arrays must be 2D (`labels.ndim != 2` → discarded). | `cache.py` | Accept `(Z, Y, X)`. |
| G7 | Edits store every painted pixel as row/column lists in JSON. Unworkable in 3D (millions of coordinates). | `edits.py` | Store painted/erased voxels in a compressed sidecar file referenced from the log (§15.2). |
| G8 | The installer installs the GUI only; Cellpose needs `--cellpose`, which then installs CPU-only PyTorch on Windows. Model weights download on first use. Python must already be installed. | `packaging/` | Hardware-aware installer (§19). |

### 5.3 Known defects (fix in Milestone 0)

| ID | Defect | Evidence | Fix |
|---|---|---|---|
| D1 | **Every threshold slider movement re-reads the image from disk and re-hashes the entire file** (full SHA-256). On a multi-GB stack the slider becomes unusable. | Probe: 3 calls to `update_thresholds` → 3 full image loads + 3 full hashes. Path: `ReviewPanel._threshold_moved` → `AnalysisController.update_thresholds` → `_cached_analysis` → `_load_record` + `_fingerprint`. | Keep the current image's measurement table in memory. Threshold changes call `classify_objects` / `summarize_image` on it only. Hash a file at most once per session unless `mtime`/size change (§17.3). |
| D2 | `Run current`, `Delete object`, `Restore`, `Undo` and `Commit edits` run on the GUI thread. A Cellpose run freezes the window; Windows marks it "Not responding". | `CellQuantWindow.run_current`, `ReviewPanel._edit`, etc. call controller methods directly. | All controller calls that can load, segment or measure run in a worker with progress and cancel (§16.5). |
| D3 | **Unmeasured objects are counted as negative.** An object whose measurement is missing (e.g. its eroded region vanished) has classification `NA`, but `_positive()` turns `NA` into `False`, so it lands in `A-` in the phenotype table and inflates the `all_objects` denominator. | Probe: values `[5, NaN, 1]`, threshold 2 → report 1/3 = 33.3%; phenotype table `A+`=1, `A-`=2. Correct: 1/2 = 50% with 1 unmeasured, or an explicit `A?` row. | Treat `NA` as a third state everywhere (§14.2). |
| D4 | Deleting or restoring one object re-runs the whole image analysis path, including an image reload and a full file hash. | `delete_object` → `run_image` | Apply the edit to cached labels; re-measure only that image; no re-hash (§17). |
| D5 | The run's `recipe_snapshot.yaml` is written when the run starts, but later single-image runs and threshold changes are appended to the same run under a different recipe. The snapshot then does not describe all the results in that folder. (Per-image provenance is correct.) | `controller._ensure_run(fresh=False)`, `storage.py` docstring | Each image result stores its own recipe hash; the run records every recipe version used, and export refuses to label a table with one recipe when several were used (§17.4). |
| D6 | Parquet files are silently skipped because `pyarrow` is not installed. | `storage._write_parquet` swallows the error | Remove Parquet from the MVP. |
| D7 | Experiment-level grouping averages per-image percentages without saying so. | `storage.grouped_summary` | Label as "mean of per-image %", and also report pooled counts (§18.3). |
| D8 | The launcher requires the exe to sit next to `pyproject.toml` because the app is installed in editable mode from the project folder (often on OneDrive). | `packaging/cellquant_launcher.py` | Install a built wheel into the venv; the launcher runs from anywhere (§19). |

---

## 6. Images and calibration

### 6.1 Canonical in-memory image

```text
LoadedImage
  data:            ndarray (C, Z, Y, X), source dtype preserved
  spacing_um:      (z, y, x) or None per axis
  calibration:     "file" | "user" | "unknown"     (per axis)
  channel_names:   list[str] from file metadata ("" if none)
  source_path, series, position
  axes_source:     how the axes were determined ("file_metadata" | "user" | "inferred")
```

- A 2D image has `Z = 1`. There is one code path for 2D and 3D, not two.
- Time series (T > 1) are rejected in the MVP with a clear message. The user may pick a single time point.
- Axis inference from array shape (current `_MAX_INFERRED_CHANNELS` heuristic) MAY stay as a fallback for unlabelled TIFFs, but the inferred assignment MUST be shown to the user for confirmation on import, with a thumbnail per channel.

### 6.2 Readers

- **TIFF / OME-TIFF** via `tifffile`, using OME or ImageJ axes and spacing metadata when present (ImageJ `spacing` tag gives Z step).
- **ND2** via `nd2`. MUST:
  - read X/Y/Z voxel size **and** check the file's calibration flag. Uncalibrated ND2 files report `(1, 1, 1)`; that value MUST be recorded as `calibration = "unknown"`, not as 1 µm.
  - read channel names from metadata.
  - treat each multipoint position (and each series) as a **separate image record** on import, named `<file> [pos N]`, with `position` stored in the record, provenance and filenames.
- Supported extensions for folder import: `.tif`, `.tiff`, `.ome.tif`, `.nd2`.

### 6.3 Calibration rules

| Situation | Behavior |
|---|---|
| X, Y and Z calibrated in file | Use; show "µm (from file)". |
| Some axis unknown | Block any analysis that needs physical units. Show a prompt: "This image doesn't say how big its pixels are. Enter X/Y pixel size and Z step (µm). In NIS-Elements, this is in the image's calibration settings." Values entered become `calibration = "user"` for that axis and can be applied to all images with the same dimensions. |
| User chooses to continue uncalibrated | Allowed for 2D modes only; all outputs are in pixels, and every table column and the results header say "px". 3D volume mode requires Z spacing and is refused without it (anisotropy is undefined). |
| X ≠ Y spacing | Supported (anisotropic sampling), not rejected as it is today. |

---

## 7. Analysis modes (2D vs 3D)

The user picks **one analysis mode per recipe**. It determines the grid on which segmentation runs and on which every measurement is taken. The chosen mode and its parameters are part of the recipe and of every result's provenance.

| Mode (recipe value) | What is analyzed | Objects are | Size unit | Intended hardware |
|---|---|---|---|---|
| `single_plane` | One Z plane (`z_index`) of every channel | 2D | µm² | Any computer |
| `projection` | A Z-projection of every channel (`max` default; `sum`, `mean` available) over all planes or a Z range | 2D | µm² | Any computer |
| `stitched_2d` | Each plane segmented in 2D, objects linked across planes by overlap (`stitch_threshold`) | 3D | µm³ | Any computer (slower); GPU helps |
| `volume_3d` | The full stack, segmented in 3D with anisotropy = Z step / XY pixel size | 3D | µm³ | GPU strongly recommended |

Rules:

- Images with `Z = 1` can only use `single_plane` (which is then the only plane) or `projection` (trivial). The UI hides the 3D modes for them.
- The analysis grid is built once by `analysis_grid.build(image, mode_spec) → AnalysisGrid` and passed to segmentation **and** measurement, so measurements always come from the same pixels the objects were drawn on. (This was a real defect in CellQuant v1: measurement used the live setting rather than the setting that produced the labels.)
- In `projection` mode, intensity statistics come from the projected channel images. The results header MUST say "measured on max projection", because max-projected intensities are not comparable with volume intensities.
- `single_plane` SHOULD offer "suggest a plane", which picks the plane with the highest focus score (e.g. variance of Laplacian) in the segmentation channel. The user confirms it.
- Labels are always stored as `(Z, Y, X)` `uint32`. 2D modes store `Z = 1`.
- Summaries and exports MUST refuse to pool images analyzed in different modes into a single experiment-level number. They are reported in separate groups with a warning.

---

### 7.1 Implementation status (2026-09-28)

Built in the 0.1 code base, ahead of the schema-v2 rewrite. The recipe field is `z_stack`, and the values map to this section as follows:

| This manifest | Code (`z_stack`) | Notes |
|---|---|---|
| `single_plane` | `single_plane` | `z_index`, 0-based; middle slice when blank |
| `projection` (max) | `max_projection` | sum/mean projections and Z ranges not built |
| `stitched_2d` | `stitch_slices` | `z_stitch_threshold` (IoU, default 0.25); linking is done by CellQuant (`volume.stitch_slices`, same rule as Cellpose `stitch3D`) so it works for every method |
| `volume_3d` | `full_3d` | Cellpose `do_3D=True`, `z_axis=0`, `anisotropy = z step / xy pixel`; classical: 3D threshold and anisotropic watershed |

Also built: `z_scale_brightness` (`stack` by default: one percentile scaling for the whole stack, passed to Cellpose with `normalize=False`; Cellpose's own default, `norm3D=False`, scales each slice separately, which breaks linking when top or bottom slices are dim); `z_min_slices`; per-object `centroid_z`, `volume`, `z_slices`, `z_first`, `z_last` and `z_flag` (`one_slice`, `possibly_merged`); `area` is the largest cross-section in 3D; anisotropic regions and rings; 3D edits; edits tied to the segmentation they were made on; Z step editable in step 1.

Hardware (§10): `cellquant/hardware.py` estimates time per image for every option from GPU, VRAM, RAM, method and stack size, replaces the estimate with speeds measured on the computer after the first run, and recommends one option. Deviation from §10.2: nothing is refused. Every option stays available; an option that may not fit in memory is marked with a warning. Z settings enter the recipe hash only in 3D modes, so 2D settings are never reported as mixed.

Not yet built: `analysis_grid.py` as a separate module (loading does this), labels always `(Z, Y, X)` (2D modes keep `(Y, X)`), sum/mean projections, "suggest a plane", orthogonal views, refusal of pooled summaries across modes (the existing mixed-settings warning covers it), Cellpose runs verified on real weights (only argument-level tests with stand-in packages here).

## 8. Recipe (schema version 2)

### 8.1 Contents

A recipe stores scientific settings only. It never contains file paths, image-specific edits or per-image calibration.

```yaml
recipe_version: 2
recipe_name: "Nuclei — OTX2 / VSX2"
software_version: "0.2.0"          # written on save

channels:                           # names this recipe expects, with the role each plays
  - name: "DAPI"
  - name: "OTX2"
  - name: "VSX2"

analysis:
  mode: volume_3d                   # single_plane | projection | stitched_2d | volume_3d
  z_index: null                     # single_plane only
  projection: null                  # projection only: {type: max, z_start: null, z_end: null}

object_set:
  name: "Nuclei"
  segmentation_channel: "DAPI"
  algorithm: cellpose               # cellpose | classical
  parameters:
    diameter_um: null               # null = model default / auto
    flow_threshold: 0.4
    cellprob_threshold: 0.0
    stitch_threshold: null          # stitched_2d only
    random_seed: 0
  filters:
    min_size_um: 20                 # µm² in 2D modes, µm³ in 3D modes (unit shown in UI)
    max_size_um: null
    exclude_xy_border: true
    exclude_z_border: false         # objects touching first/last plane
    min_planes: 2                   # 3D modes: drop objects present in fewer planes

measurements:
  - id: otx2_mean
    channel: "OTX2"
    region: {type: object}
    statistic: mean
    background: {type: none}

classifications:
  - id: otx2_pos
    name: "OTX2+"
    measurement: otx2_mean
    rule: greater_than              # value > threshold is positive; equal is negative
    threshold: 425
    threshold_source: manual        # manual | control_percentile | assisted (§13.3)

reports:
  - numerator: "OTX2+"
    denominator: all_measured_objects
  - numerator: "OTX2+ AND VSX2+"
    denominator: "OTX2+"
```

Distances and sizes MAY be given in µm (`*_um`) or pixels (`*_px`), exactly one per field, as the current schema already enforces.

### 8.2 Channels by name

- Recipes name channels. The experiment maps names to indices for each image.
- On applying a recipe, every channel name MUST resolve in every included image. Unresolved or ambiguous names block the run with a message listing the image, the missing name and the channels it does have, plus a **Map channels…** button.
- Changing channel order between imaging sessions therefore cannot silently measure the wrong channel.

### 8.3 Versioning

- `recipe_version: 1` files (index-based, 2D only) load through a migration that maps index → the experiment's channel name at that index and sets `analysis.mode: single_plane` with `z_index: 0`. The migration is shown to the user and saved as a new recipe; the original file is not overwritten.
- Content hash uses only scientific fields (existing `scientific_dict`), not timestamps or display names.

### 8.4 Quick-start template

"New analysis" MUST offer **"Count % positive for each marker"**. The user picks the segmentation channel; the template creates, for every other channel, a mean-intensity measurement on the object region, one threshold classification, and a report against all measured objects. Everything else is optional. The template produces an ordinary recipe that can be edited.

---

## 9. Segmentation

### 9.1 Interface

```python
class SegmentationBackend(Protocol):
    supports: set[str]    # analysis modes this backend can run
    def segment(self, grid: AnalysisGrid, params: dict, *, device: Device,
                cancel: CancelToken, progress: ProgressSink) -> np.ndarray:  # (Z, Y, X) uint32
```

The engine applies shared filters (size, borders, min planes) after the backend returns, exactly as `segment_objects` does today, but on `(Z, Y, X)` labels with physical-unit sizes computed from `spacing_um`.

### 9.2 Classical backend (fast, any computer)

- Keep the existing pipeline: Gaussian smoothing → Otsu or manual threshold → opening/closing → fill holes → optional distance-transform watershed → connected components.
- Generalize to 3D: `sigma` and morphology radii in µm, converted per axis; `ndimage.distance_transform_edt(..., sampling=spacing)`; 3D structuring elements that respect anisotropy; `peak_local_max` in 3D.
- Supports all four modes (`stitched_2d` = 2D per plane + overlap linking).

### 9.3 Cellpose backend (learned)

- Two engines, one per Python environment (they cannot be installed together):
  - **Cellpose-SAM** (Cellpose ≥ 4.2): `models.CellposeModel(gpu=…, pretrained_model=…)`. Default model is the installed release's own default, read from the package (`cpsam_v2` in 4.2.1.1, `cpsam` in earlier 4.x). No `channels` argument.
  - **Classic Cellpose** (Cellpose 3.1.x): `models.Cellpose(gpu=…, model_type="nuclei")`, which adds the size model so a blank diameter is estimated; `channels=[0, 0]`.
- The recipe records `engine` (`cellpose4` or `cellpose3`) and `model`. Running a recipe under the other engine, or with a model the running engine does not have, MUST be refused with a message saying which engine to open. (Cellpose 4 silently replaces unknown model names with its default.)
- The segmentation cache key includes the engine, Cellpose version and resolved model, so labels from one engine are never reused under the other.
- The installed engine is detected from package metadata without importing Cellpose or PyTorch (`cellquant/engines.py`). The GUI shows the engine, its models, and whether a GPU is usable.
- Mode mapping:

| Mode | Cellpose call |
|---|---|
| `single_plane`, `projection` | 2D array, `do_3D=False` |
| `stitched_2d` | Stack, `do_3D=False`, `stitch_threshold` from recipe (required, > 0), `z_axis=0` |
| `volume_3d` | Stack, `do_3D=True`, `anisotropy = z_step / xy_pixel`, `z_axis=0` |

- Diameter is taken in µm in the recipe and converted to pixels per image; `null` uses the model default.
- Seeds: set Python, NumPy and Torch seeds from `random_seed`; record whether deterministic kernels were available.
- Record Cellpose version, model name and **SHA-256 of the weights file** in provenance.
- Device: CUDA when available and selected, else CPU. A GPU failure mid-run MUST NOT silently fall back to CPU; it fails that image with a message offering to switch to CPU for the rest of the batch.
- Import Cellpose/Torch lazily so the app opens quickly and the classical path works without them.

### 9.4 Preview

Preview runs segmentation on a small region so parameters can be tuned quickly.

- 2D modes: the visible field of view, capped at 1024 × 1024 px.
- 3D modes: a sub-volume centered on the view: visible XY (capped at 512 × 512) × up to 16 planes around the current plane.
- The preview is never cached as a result and is visibly labelled "Preview".

---

## 10. Hardware detection and resource preflight

This is what makes one app usable on both a laptop and a workstation.

### 10.1 Detection (on launch, cached, refreshable)

- CPU cores, total and free RAM.
- NVIDIA GPU present? CUDA-capable PyTorch installed? VRAM total/free.
- Cellpose installed and model weights present?

Shown in plain language in **Settings → This computer**, e.g. "Laptop: 16 GB memory, no compatible GPU. 2D analysis recommended; 3D analysis will be slow."

### 10.2 Preflight before every run (single image and batch)

For the chosen mode, backend and the largest included image, estimate peak memory and a runtime range:

- Memory: voxel count × bytes per voxel × a per-stage factor, calibrated from benchmark runs (§21.5) and stored in `resources.py` as named constants, not guessed per call.
- Runtime: per-megavoxel rates for each (backend, mode, device) measured by the benchmark and stored with the hardware tier they came from.

Outcomes:

| Estimate | Behavior |
|---|---|
| Fits comfortably | Run. |
| Tight (> 70% of free RAM/VRAM) or runtime > 10 min per image | Warn with the estimate and suggest a lighter mode (e.g. "3D on CPU: ~25 min per image. Stitched 2D: ~4 min. Max projection: ~20 s."). User may proceed. |
| Will not fit | Refuse with the reason and the lighter options. Never start and crash. |

The recommended mode for the current hardware is pre-selected in new recipes; the user may change it.

---

## 11. Measurement regions

MVP regions (already implemented in 2D; extend to 3D):

| Region | Definition |
|---|---|
| `object` | The segmented object. |
| `expanded_object` | Object grown outward by a distance; each new voxel goes to the nearest object so neighbors never overlap (existing nearest-object EDT approach). |
| `eroded_object` | Object shrunk inward by a distance. Objects that vanish produce **unmeasured** values (not zero, not negative). |
| `ring` | Voxels between an inner and outer distance from the nearest object, assigned to that object. |

- All distances are Euclidean in µm, computed with `distance_transform_edt(..., sampling=spacing_zyx)` so anisotropic Z is handled correctly.
- In 2D modes the Z term is absent.
- Region images are cached per image per region spec (existing `_cached_region`).

---

## 12. Measurements

### 12.1 Statistics

| Statistic | 2D modes | 3D modes |
|---|---|---|
| mean, median, min, max, std, integrated | ✓ | ✓ |
| `size` | area, µm² | volume, µm³ |
| `equivalent_diameter` | circle, µm | sphere, µm |
| `centroid_x`, `centroid_y` | ✓ | ✓ |
| `centroid_z` | — | ✓ |
| `n_planes` | — | ✓ (planes the object spans) |

The object table always carries `object_id`, `centroid_x/y` (and `z` in 3D), and `size` with its unit in the column name (`size_um2` or `size_um3`, or `_px` when uncalibrated). Do not use a unit-less `area` column for 3D objects.

### 12.2 Background correction

Keep the existing `none`, `global` (median of non-object voxels, or a fixed value) and `local_ring` options and formulas. The output column records whether values are raw or corrected (`…_raw` / `…_bgcorr`, or a `background` attribute in the column metadata file).

### 12.3 Unmeasured values

If a region contains no voxels for an object, the value is `NaN` and the object is **unmeasured** for that measurement. Unmeasured objects are counted and reported (§14.2), never silently zero.

---

## 13. Classification and thresholds

### 13.1 Rule

MVP: `value > threshold → positive`, `value ≤ threshold → negative`, `NaN → unmeasured`. The rule is stored explicitly (`rule: greater_than`) so other rules can be added later.

**Note for the lab:** CellQuant v1 called a nucleus positive when a chosen percentage of its voxels exceeded a threshold. v2 uses a per-object statistic (default mean). Results from the two versions are not directly comparable, and the methods text (§18.4) must say which rule was used.

### 13.2 Threshold scope

- One threshold per classification per recipe, applied to **every** image in the experiment. This is the default and the only thing the novice path offers.
- Per-image overrides MAY exist but MUST be explicitly enabled, shown with a badge on the image in every list, listed in the results header, and recorded in provenance.
- When groups defined by a metadata column (e.g. Genotype) are compared in results, the app MUST warn if any image in those groups uses an override.

### 13.3 Help choosing a threshold (MVP)

The histogram-plus-slider alone does not tell a novice where to put the line. The threshold screen MUST offer:

1. **Histogram** of the per-object measurement across the current image, or across all analyzed images (toggle), with the threshold line, positive/negative/unmeasured counts and % positive updating live.
2. **Object gallery**: the 8 objects just above and 8 just below the current threshold, as small crops of the marker channel with the object outline. The user checks that the cutoff separates them sensibly.
3. **From a control**: the user marks one or more images as negative controls (no-primary, knockout, known-negative region) using a metadata column or a checkbox. The app proposes the threshold as a chosen percentile (default 99th) of control-object values, explains it in one sentence ("99% of nuclei in your control images fall below 425"), and records `threshold_source: control_percentile` with the percentile and control image IDs.
4. Live recoloring of objects in the viewer (positive / negative / unmeasured / excluded).

Threshold changes MUST NOT reload images, rehash files, or re-measure (D1).

---

## 14. Phenotypes, denominators and reports

### 14.1 Phenotypes

Keep the existing engine: exclusive phenotype strings (`A+|B-|C+`) and positive-combination counts.

- Auto-expand exclusive phenotypes for up to 6 classifications (64 rows). Above that, compute only requested reports and say so. (Current cap: 12 → 4,096 rows.)

### 14.2 Three-state logic (fixes D3)

Each object is `positive`, `negative` or `unmeasured` for each classification.

- An object unmeasured for any classification in a phenotype is placed in a `?` phenotype (e.g. `A+|B?`), never in `B-`.
- Denominator keywords:
  - `all_objects`: every included object.
  - `all_measured_objects`: included objects measured for every classification referenced in the numerator (**default** in templates).
  - any classification expression, e.g. `"A+"` or `"A+ AND B+"`.
- Every report row carries `count`, `denominator_count`, `n_unmeasured` and `percent`.
- Excluded objects (deleted by the user, or removed by border/size filters) are never in any denominator; their counts are reported separately.

### 14.3 Report expressions

Keep the `AND` grammar. Add `NOT` (e.g. `"A+ AND NOT B+"`). Tokens resolve to classification names or IDs as today.

---

## 15. Review and manual editing

### 15.1 Image review states

`not_analyzed → analyzed → reviewed → approved`, plus `excluded` and `needs_attention`. Any edit or re-segmentation after approval returns the image to `analyzed` and says why.

### 15.2 Edits in 2D and 3D

Operations: `delete`, `restore`, `paint`, `erase` (MVP); `merge` and `split` post-MVP.

- The edit log stays an ordered list in `edits/<image_id>.json`.
- `paint` and `erase` record the affected voxels in a compressed sidecar (`edits/<image_id>/<op_index>.npz`: `z, y, x` arrays or a bounding-box mask), referenced from the log entry with its SHA-256. JSON coordinate lists (current format) are only allowed for 2D edits under 10,000 pixels, for backwards compatibility.
- In 3D, painting happens plane by plane in napari. The GUI shows the object in orthogonal views so the user can see it across planes.
- Undo/redo applies to the log.

### 15.3 Object-level QC flags (review queue)

Beyond per-image QC, flag individual objects so review time goes where errors are likely:

- size < 5th or > 95th percentile of the image, or outside 0.5×–2× the median;
- low solidity (likely merged nuclei);
- 3D: spans a single plane, or touches the first/last plane;
- within ±10% of a classification threshold ("borderline").

The review panel offers **Next flagged object**, which centers the viewer on it (all three orthogonal views in 3D).

### 15.4 What good segmentation looks like

The review panel SHOULD have a collapsible reference strip with small example images: correct, merged, over-split, missed, and partial (border) nuclei, each with one sentence of what to do about it.

---

## 16. User interface

### 16.1 Layout

Keep the existing five sections as a numbered stepper with checkmarks, and the footer navigation:

```text
1 Images → 2 Channels & mode → 3 Find objects → 4 Measure & classify → 5 Review → 6 Results
```

- Step 2 is new: name channels (with a thumbnail of each), pick the segmentation channel, and choose the analysis mode (§7) with the hardware recommendation from §10.
- Each step has one primary action and a **Next** button that is disabled until the step is complete, with the reason shown next to it.

### 16.2 Mode chooser wording

Offer choices in plain language, with the technical name in small text:

- **Quick — one slice** (single plane). Any computer. Good for a first look.
- **Quick — flattened stack** (max projection). Any computer. Overlapping nuclei may merge.
- **Slice-by-slice 3D** (stitched 2D). Any computer, slower. Counts each nucleus once through the stack.
- **Full 3D** (volume). Best accuracy for thick stacks; needs a GPU for reasonable speed.

Each option shows the estimated time per image from §10.

### 16.3 Language

User-facing text MUST avoid: analysis grid, provenance, recipe hash, affine, anisotropy, label layer, policy, override (use "different setting for this image"), NaN. The recipe is called **"Analysis settings"** in the UI and "recipe" only in files and code.

### 16.4 Errors

Every blocking message states what is wrong in one sentence and offers the action that fixes it (a button or the exact next step). Tracebacks go to `logs/developer.log` only.

### 16.5 Responsiveness

- Loading, segmentation, measurement, edits that re-measure, batch runs and export run in a worker thread (`QThread` or napari `thread_worker`) with a progress bar and **Cancel**.
- Cancellation is checked between planes, tiles and images. A cancelled image leaves no partial result.
- The GUI thread never loads image files or hashes them.

### 16.6 Viewing 3D data

- Default view is 2D slice view with a Z slider; objects are shown as outlines on the current plane.
- An **orthogonal views** toggle shows XZ and YZ; required for reviewing 3D segmentations.
- 3D rendering is NOT required.
- Only these layers are visible to the user: channel images, objects, classification overlay, preview. Internal layers are hidden.

### 16.7 Practice dataset

Ship 3 small images (one 2D, two short Z-stacks) with a ready-made recipe and known answers. **Help → Try with practice images** runs a 5-step guided tour that ends with a stated answer (e.g. "You should see 25% Marker A+ in image 1"). The practice data also serves as the end-to-end test fixture (§21.4).

---

## 17. Performance, caching and provenance

### 17.1 Stage dependencies

```text
image + calibration + mode ─▶ analysis grid
analysis grid + object_set ─▶ automated labels
automated labels + edits   ─▶ final labels
final labels + grid + measurements ─▶ measurement table
measurement table + classifications ─▶ classified table
classified table + reports ─▶ summaries
```

### 17.2 Cache keys

| Stage | Key includes |
|---|---|
| Analysis grid | file signature, series/position, spacing (with calibration source), analysis mode section |
| Automated labels | grid key, object_set section, backend version, model weight hash, device class (cpu/cuda) |
| Measurement table | automated labels key, edit log hash, measurements section |
| Classification, summary | computed in memory from the measurement table; not cached on disk |

### 17.3 File identity

- On import and on each run, record `(size, mtime)` as the file signature.
- Compute the full SHA-256 once, in a background worker, when a file is first analyzed or when its signature changes. Never on the GUI thread, never on threshold changes (D1).

### 17.4 Runs (fixes D5)

- **Run current / selected / all** each create a new run folder with a recipe snapshot.
- Interactive changes after the run (thresholds, edits) are saved to a **working state**, not written into the finished run. **Save as new run** snapshots the current recipe and re-exports results.
- Every per-image result stores its recipe hash. A run folder whose images were produced under different recipe hashes is labelled "mixed settings" in its summary, and export lists each hash with its images.

### 17.5 Provenance per image

Everything from the original manifest §30, plus: analysis mode and its parameters, `z_index`/projection range, `spacing_um` with calibration source per axis, series/position, device used, Cellpose version and weights SHA-256, random seed, threshold source (manual / control percentile with control IDs), and the edit log hash.

---

## 18. Results and export

### 18.1 Object table (canonical)

One row per object, including excluded objects (flagged). Columns: experiment/run/image identifiers, sample name, user metadata columns, analysis mode, object_id, centroid_x/y(/z), size with unit, n_planes (3D), every measurement, every classification as `positive`/`negative`/`unmeasured`, phenotype, excluded, exclusion reason, manual edit status.

### 18.2 Image table

One row per image: identifiers, metadata, mode, total objects, excluded counts by reason, per-report count / denominator / unmeasured / percent, QC status, threshold override flag.

### 18.3 Group summary

For a chosen metadata column: for each group, the number of images, the **mean and SD of per-image percentages**, and the **pooled percentage** (sum of counts ÷ sum of denominators), both clearly labelled. Groups analyzed in different modes are not merged (§7).

### 18.4 Report page

Export also writes `report.html`, a single readable page:

- headline numbers per image and per group;
- a thumbnail of each image with object outlines;
- settings used, in plain language;
- all warnings (uncalibrated images, overrides, mixed modes, unmeasured objects);
- a **methods paragraph** that can be pasted into a paper, generated from the recipe (e.g. "Nuclei were segmented in 3D from the DAPI channel using Cellpose-SAM (Cellpose 4.x; anisotropy 3.2) … A nucleus was classified OTX2+ when its mean OTX2 intensity exceeded 425 (99th percentile of no-primary controls) …").

CSV files remain the data of record.

---

## 19. Installation and launch

Built 2026-09-28 on the CellQuant v1 installer's design, which works on the lab's computers. Files: `Install CellQuant.bat`, `Open CellQuant.bat`, `packaging/windows/`.

- **Requires conda** (Miniforge recommended, or Miniconda). If conda is missing, the installer says so and links to Miniforge.
- **Installer** (`Install CellQuant.bat` → `packaging/windows/install_windows.ps1`):
  1. Checks the computer: NVIDIA GPU name and memory, driver CUDA version, compute capability, and system memory. Chooses the PyTorch CUDA build (cu118–cu130; Blackwell needs cu128+) with v1's table.
  2. Recommends an engine: Cellpose-SAM with an NVIDIA GPU of ≥ 6 GB (a rule of thumb); classic Cellpose otherwise. Says why in one sentence.
  3. If CellQuant is installed: **[O]** open, **[U]** update (and optionally add an engine), **[R]** delete and reinstall.
  4. Asks where the environments go (default: conda's `envs` folder). Refuses OneDrive folders, other users' profiles, very long paths, and folders the account cannot write to.
  5. Asks which engines: **[B]** both (default), **[S]** Cellpose-SAM only, **[C]** classic only. Shows the rough size per engine and the free space; defaults to the recommended engine alone when space is short.
  6. For each engine: creates a conda-forge-only Python 3.11 environment (`cellquant2-cellpose4` / `cellquant2-cellpose3`); installs **GPU PyTorch first** (plus matching torchvision for Cellpose 4) so pip keeps it; installs CellQuant (not editable, so the environment does not depend on OneDrive; `-Dev` gives an editable install); checks the Cellpose major version; confirms PyTorch can use the GPU and repairs it if not; runs `pip check`; downloads the default model (a failure is only a warning); runs a small test analysis.
  7. One engine failing does not undo the other. The record of what is installed is saved after each engine to `%LOCALAPPDATA%\CellQuant\cellquant_env.json` (per computer, not in the shared project folder).
  8. Writes `install_last.log` and opens it in Notepad on failure.
- **Launcher** (`Open CellQuant.bat` → `launch_cellquant.ps1`): asks which engine when both are installed (Enter = the recommended one) and starts `python -m cellquant` in that environment.
- `CellQuant.exe` and `Install_CellQuant.exe` only open the two .bat files. They must be rebuilt on Windows with `packaging/build_exes.py` after changes to their Python sources.
- **Updates**: re-running the installer and choosing [U] reinstalls CellQuant from the project folder and keeps model weights.
- Not yet done: installing conda for the user; a splash screen; OneDrive placeholder warnings on image import.

---

## 20. Milestones

Each milestone ends with its tests passing and a short `docs/STATUS.md` entry (what was done, tests run, what was not verified).

**M0 — Fix known defects (2D, existing scope).**
D1–D7. Threshold dragging uses in-memory tables; all long work off the GUI thread; three-state classification; run/recipe snapshot fix; Parquet removed; group summary labelled.
*Exit:* threshold drag on a 2 GB file does no disk I/O (test with a mocked loader asserting zero calls); D3 probe gives 50% with 1 unmeasured.

**M1 — 3D-capable data model and I/O.**
`(C, Z, Y, X)` images, `(Z, Y, X)` labels, `(z, y, x)` spacing with calibration status; ND2 reader with positions and calibration flag; channel-by-name recipes (schema v2) with v1 migration; cache and edits accept 3D.
*Exit:* all existing 2D tests pass unchanged through the new model (2D = Z of 1); ND2 and multi-position fixtures load correctly.

**M2 — Analysis modes and 3D engine.**
`analysis_grid.py` with all four modes; classical backend in 3D; anisotropic regions; dimension-aware measurements; object-level filters (borders, min planes).
*Exit:* synthetic 3D tests (§21.2) pass; 2D-mode results on a Z-stack equal those from running the extracted plane as a 2D image.

**M3 — Cellpose 4 and hardware awareness.**
Cellpose-SAM backend for all modes; device selection; seeds and weight hashing; `resources.py` detection, benchmarks and preflight.
*Exit:* preflight refuses an oversize job on a mocked 8 GB machine and suggests lighter modes; Cellpose runs in each mode on the practice images (CPU and, where available, GPU).

**M4 — Thresholds and results.**
Quick-start template; control-derived threshold; object gallery; override rules; three-state reports; group summary; `report.html` with methods paragraph.
*Exit:* control-percentile threshold reproduces a hand-computed value on a synthetic control set; report page lists every warning type.

**M5 — Novice interface.**
Stepper with Channels & mode step; plain-language mode chooser with time estimates; orthogonal views; 3D painting; object-level QC review queue; reference strip; errors with fix actions; practice dataset and tour.
*Exit:* §22 novice acceptance test passes with at least two first-time users.

**M6 — Installer.**
§19 in full.
*Exit:* on a clean Windows VM with no Python, install → practice tour completes; tested once with and once without an NVIDIA GPU.

**M7 — Biological validation.**
See §21.6.

---

## 21. Testing

### 21.1 Keep

All 22 existing tests. They must keep passing through M1–M2 unchanged except for the explicit schema-v2 migration.

### 21.2 Add: synthetic 3D

- Anisotropic spheres (e.g. Z step 3× XY) with known volumes: `size_um3` within 5% of truth; expanded-region radii correct in µm along Z and XY.
- Two touching spheres: classical 3D watershed splits them.
- An object spanning 1 plane is removed by `min_planes: 2`; objects touching Z borders are removed only when `exclude_z_border` is set.
- Projection mode on a stack gives identical objects and intensities to running the explicitly projected 2D image.
- Stitched 2D produces one object per sphere, not one per plane.

### 21.3 Add: correctness of counts

- The D3 case: unmeasured objects appear in `n_unmeasured`, not in the negative count; `all_measured_objects` excludes them.
- 1 positive out of 4 objects of different sizes reports exactly 25%.
- Two images in one experiment use identical thresholds unless an override is enabled, and an override is flagged in the image table.
- Mixed-mode experiments are not pooled.

### 21.4 Add: interface and workflow

- Threshold change does not call the image loader or hasher (mocked).
- Long operations run off the GUI thread (assert via a worker hook or thread identity).
- Practice dataset end-to-end: import → recipe → run all → export; exported numbers equal the stored known answers.
- Recipe with a channel name missing from one image blocks the run and names the image.

### 21.5 Add: resources

- Benchmark script (`scripts/benchmark.py`) times each backend × mode × device on the practice stacks and writes the rates used by preflight, with the hardware it ran on.
- Preflight unit tests with mocked RAM/VRAM.

### 21.6 Biological validation (M7)

- An expert hand-counts nuclei and marker-positive nuclei on at least 6 retinal fields (≥ 2 ages or genotypes), in 3D where applicable.
- Report, for each analysis mode the lab intends to use: nucleus detection F1 (IoU ≥ 0.5), % positive vs. hand count (Bland–Altman), and time per image on a laptop and a workstation.
- Record the results in `docs/VALIDATION.md`. The app's About box states which modes have been validated and on what data. Until then, results are labelled "not yet validated against expert counts".

---

## 22. Acceptance criteria for the first release

A person who has never quantified images and never used CellQuant, with only the practice dataset and their own data, can do all of the following without help:

1. Install CellQuant on a Windows laptop without a GPU, and separately on a GPU workstation.
2. Complete the practice tour and get the stated answer.
3. Create an experiment from a folder of ND2 files, including multi-position files.
4. See immediately which images lack calibration and fix them.
5. Name channels, choosing from thumbnails.
6. Choose an analysis mode suited to their computer, seeing the time estimate.
7. Preview and run segmentation in 2D and in 3D.
8. Find and fix obvious segmentation errors using the flagged-object queue.
9. Set a marker threshold from a negative control and check it with the object gallery.
10. Get single-, double- and triple-positive percentages with a chosen denominator.
11. Save the settings, reopen them next week, and apply them to a new batch of images with a different channel order without measuring the wrong channel.
12. Run a whole experiment in the background while still using the app.
13. Export an object table, an image table, a group summary and a report page with a methods paragraph.
14. Reopen the experiment later and recover all results, edits and approvals.
15. For any number in the export, find the exact settings, mode, calibration and edits that produced it.

Engineering criteria:

- No GUI freeze > 250 ms during any of the above (measured with a watchdog).
- Threshold drag updates in < 200 ms for 10,000 objects.
- All §21 tests pass.

---

## 23. Decisions for the lab to confirm

Build against the default in **bold** until told otherwise.

1. ~~Lightweight learned model for CPU-only computers.~~ **Decided 2026-09-28: (b).** Both Cellpose-SAM (Cellpose 4) and classic Cellpose (Cellpose 3) are supported, each in its own environment. The installer recommends one based on the hardware and the user picks at launch (§9.3, §19).
2. **Positivity rule.** **Mean intensity above threshold** (standard), or also offer v1's "percentage of voxels above threshold" as a second rule.
3. **Default control percentile** for control-derived thresholds: **99th**.
4. **Default mode for new recipes**: **the hardware recommendation from §10**, or always `stitched_2d` for comparability across computers.
5. **Whether experiments may mix 2D-only images and Z-stacks** under one recipe: **no; the recipe's mode must be valid for every included image**.

---

## 24. Non-goals for the first release

Unchanged from the original manifest (3D rendering, tracking, whole-slide, registration, multiple interacting object sets, spatial statistics, cloud processing, model training, multi-user editing), plus: lazy/chunked processing of images larger than memory (use preflight to refuse instead), Parquet export, merge/split editing, and non-Windows installers (the code must stay cross-platform; only the installer is Windows-specific).
