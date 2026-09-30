# CellQuant status

## M0 — known defects (2026-09-28)

Done:

- Threshold changes reclassify the measurement table already in memory. They do not load or hash the image.
- Delete, restore, undo, and drawn edits remeasure from the in-memory image and do not hash the file again.
- Run, preview, edits, and export run off the interface thread.
- Missing measurements stay unmeasured. They are not counted as negative. `all_measured_objects` is the denominator that drops them, and reports include `n_unmeasured`.
- A finished run keeps its snapshot. Later edits are stored in `working/`. Export writes `settings_index.csv` and says when more than one settings version was used.
- Parquet output was removed.
- Group summaries name the mean of per-image percents separately from the pooled percent.

Tests run: `python -m pytest tests -q`.

Not verified: dragging the threshold in the live napari window, and the off-thread jobs on a machine with a display. The earlier offscreen check had no OpenGL.

## M0 follow-up (2026-09-28)

Found while testing M0, then fixed:

- Export now works after reopening an experiment. It reads saved results (working copy first, then the latest run) without loading every result into memory. Threshold changes also work in a reopened session.
- The settings hash covers scientific settings only. Saving no longer changes it, so one unchanged batch has one hash and no longer writes `mixed_settings.txt`. A stale `mixed_settings.txt` is removed when a later export is not mixed.
- Only the two most recently used images are kept in memory (`SESSION_IMAGE_LIMIT`). Editing an older image reloads it without hashing the file again.
- A nucleus that cannot be measured is left out of every count and every denominator. It stays in `objects.csv` with `unmeasured = True` and blank classifications. `image_summary.csv` has an `n_unmeasured` column and the image gets a warning. `all_measured_objects` is kept as an accepted denominator and now means the same as `all_objects`. There are no `?` phenotype rows in the count tables.
- Group summaries use the sample SD (n - 1), blank for one image. Pooled values are blank, with a note, for summaries written by older versions.
- `tests/test_m0.py` no longer expects an `A?` row. New tests are in `tests/test_m0_followup.py`.

Tests run: `python -m pytest tests -q` (37 passed).

Not verified, unchanged from M0: the live napari window. Not done: the threshold slider is not blocked while a background job runs, and single-image jobs have no progress or Cancel. `save()` still rewrites every open result after each image, so a batch of N images does about N squared writes.

## Installer and two Cellpose engines (2026-09-28)

Done:

- `Install CellQuant.bat` and `Open CellQuant.bat`, with PowerShell in `packaging/windows/`, modeled on the CellQuant v1 installer. The installer checks the GPU and memory, recommends an engine, and installs the ones chosen: both (default), Cellpose-SAM only, or classic Cellpose only. Each engine gets its own conda environment (`cellquant2-cellpose4`, `cellquant2-cellpose3`). GPU PyTorch is installed before Cellpose; the default model is downloaded during install; each environment runs a small test analysis. The launcher asks which engine to start when both are installed.
- The install record is per computer, at `%LOCALAPPDATA%\CellQuant\cellquant_env.json`, not in the shared project folder.
- The Cellpose adapter supports Cellpose 3 (`models.Cellpose`, `nuclei`) and Cellpose 4 (`CellposeModel`, the release's default model, `cpsam_v2` in 4.2.1.1). Recipes record the engine and model. A recipe for the other engine, or a model the engine does not have, is refused. The segmentation cache key and provenance include engine, version, model and the device used. A GPU request that ran on the CPU is a warning.
- The GUI shows the engine and its models, checks for a GPU in the background, and keeps size filters when Cellpose is selected (they were dropped before).
- `gui` extra now installs `napari[pyqt6]`. Plain `napari` has no Qt backend, so the window could not open in a fresh environment.
- `CellQuant.exe` and `Install_CellQuant.exe` sources now just open the .bat files.

Tests run: `python -m pytest tests -q` (71 passed), also in an environment with napari 0.6.6 and PyQt6. Cellpose adapter tests use stand-ins with the real Cellpose 3.1.1.3 and 4.2.1.1 signatures. Both `[gui,cellpose-v4]` and `[gui,cellpose-v3]` resolve with pip (dry run).

Not verified: the PowerShell scripts have not been run. PowerShell is not available in the test environment; the scripts were checked statically (`tests/ps_lint.py`: brackets, PowerShell 7-only syntax, undefined functions and parameters, ASCII, CRLF). Real Cellpose inference and model downloads were not run. The two .exe files in the project folder are from the old design and must be rebuilt on Windows (`python packaging/build_exes.py`) or deleted.

Developer install (editable, for working on the code): `powershell -ExecutionPolicy Bypass -File packaging\windows\install_windows.ps1 -Dev`

## Live window check (2026-09-28)

The napari window was run for the first time, on a virtual display with software OpenGL (Xvfb and Mesa llvmpipe), with a scripted session: open an experiment, run one image, move the threshold, delete an object, run a batch of 3, export.

Found and fixed: every threshold movement took about 10 seconds. `show_result` re-read the image from disk and deleted and re-created all seven layers (each add or remove takes about a second). Threshold changes now only recolor the classification layer (`show_classification`). Other updates reuse layers of the same shape, and the image is reloaded only when a different image is shown. The overlay is built with one lookup instead of one full-image comparison per object. Measured: about 10,100 ms per threshold change before, 79 ms median and 130 ms at most after, with identical counts.

New test `tests/test_gui_display.py` runs the real window and fails on the old code. It skips where napari is not installed. Run it with a display, or on Linux with `xvfb-run -a python -m pytest tests/test_gui_display.py`.

Still seen: about 2.7 seconds without response when the first result of an image appears, while its object layers are created. This was measured with software OpenGL and should be shorter with a GPU. The area line in the Review panel says "um" for an area; it should say µm².

## Guidance for first-time users (2026-09-28)

Modeled on CellQuant v1's START_HERE guide, hover help, and "Continue" buttons.

- New **Start** tab: what CellQuant does, **Try practice images**, **New experiment…**, **Open experiment…**, a progress checklist, a **Continue** button to the next unfinished step, and the step-by-step guide.
- The panels are now numbered steps: **1 Images → 2 Find objects → 3 Markers → 4 Check → 5 Results**. Each has "What to do", "Success check", and Back / Next. Next is disabled until the step is done, with the reason beside it. Finished steps get a ✓.
- **Practice images** (`cellquant/practice.py`): 3 images, 40 nuclei each, two markers, known answers written to README.txt.
- **Quick marker setup** in step 3 (`cellquant/quicksetup.py`): one mean-intensity measurement, positive/negative call and result row per ticked channel, plus double positives. It measures the image and proposes starting cutoffs (Otsu's criterion evaluated exactly on the object values, cutoff halfway across the gap). The histogram form of Otsu put the cutoff just below the brightest dim object on the practice data.
- **Results in words** in step 5 ("Marker A+ among all objects: 10 of 40 (25.0%)"), with **Run all images** and **Export results…** as the main actions. The manual settings for steps 3 and 5 are under **Show all settings (advanced)**.
- Hover help on the main controls (`HELP` in `cellquant/gui/guide.py`); plain button names ("Run this image", "Next image ▶"); channel names labelled "Channel 1 name"; statuses in words; area shown in µm².
- `README.md` and `docs/START_HERE.md` (also opened from the Start tab; `cellquant/gui/START_HERE.md` is a copy, kept identical by a test).

Fixed along the way:
- A cutoff set by dragging was lost on the next run: the Markers table, read back into the settings before each run, still held the old value.
- "Run all images" cleared every approval. Approval now records the settings and number of edits; a re-run with the same ones keeps it, and different settings or an edit clear it.
- The guidance timer is attached to the panel, so closing the panel does not leave it running.

Tests run: `python -m pytest tests -q` (84 passed, 2 skipped without napari) and with the live window under `xvfb-run` (87 passed). `tests/test_gui_guided_flow.py` runs the whole practice experiment through the five steps and checks the exported counts against the known answers.

Not verified: the guidance on a real Windows desktop and screen size; the windows were checked at 1600 x 1000 on Linux. Not done: the loader ignores the pixel size ImageJ writes into TIFFs (the practice setup enters it directly).

## ND2, Z-stacks and folder import, tested on real images (2026-09-28)

Tested on a lab folder of E14.5-E17.5 retina images (9 Nikon AXR ND2 files; not included in this repository, 6-8 slices, 3 channels, 20x and 40x), copied; the originals were not changed.

- **ND2 files** are read (`nd2` package): channel names, calibrated pixel size and Z step (an uncalibrated file's 1 x 1 x 1 is not used), objective, and multi-position files as one image per position. Time series are refused with a message. ImageJ TIFFs with Z and channel axes are read too, including their µm pixel size.
- **Z-stacks** are analyzed as one 2D image per channel: **max projection** (default) or **one slice**, chosen in step 2 and stored in the settings (`z_stack`, `z_index`). The choice is part of the settings fingerprint and the cache key, and each result records it (`z_description`, e.g. "max projection of 7 slices"). This is an interim step; full 3D is milestone M2.
- **Add folder** finds every ND2 and TIFF in the folder and all subfolders. Each image is listed by its path inside that folder (e.g. `Control/Retina 2/...nd2`), so same-named files in different folders stay distinct; sample names are that path. Subfolder names fill **Folder 1**, **Folder 2**, ... columns. The step 1 table shows slices, channels (names from the file), µm/pixel and objective, and **Check the image list in a large window** shows the full list and copies it for Excel.
- A summary above the list says how many images were found and in how many folders, which folders have none (a fully empty folder is named once), and warns about mixed pixel sizes and Z-stacks.
- CellQuant's own folders (runs, working, .cache, exports, edits, recipes) are skipped when a folder holding an experiment is imported, so label TIFFs are never imported as images. Creating a new experiment in a folder that contains images now asks first.

Real-data result: all 9 images listed with the right folders; 20x and 40x pixel sizes (0.575, 0.281 µm/pixel) and 6-8 slices shown; empty folders reported (one condition folder and its 3 retinas, two other retina folders, the Processed folders). The classical method on the PAX6 channel of a max projection gave 511 fragmented outlines in dense tissue: fine for testing import, not a usable segmentation. Cellpose could not be run here (its model download is blocked in the test environment).

Tests: `tests/test_import_folders.py` (9 tests, all fail on the previous code). Suite: 93 passed and 2 skipped without napari; 96 passed with the live window.

Not done: loading runs on the interface thread when moving between images (about half a second per ND2 here); full 3D.

## Separate image and results folders; responsive image switching (2026-09-28)

- **New experiment** asks for an experiment name, an **Image folder** (read only) and a **Results folder**. The results folder defaults to a new sibling folder, `<image folder> - CellQuant results`. Choosing the image folder itself, or a folder inside it, asks for confirmation first. The image folder is stored in `experiment.json` (`input_directory`) and shown on the Start tab next to the results folder. Add images/Add folder and Export start in the matching folder.
- The image folder is scanned in the background when the experiment is created, and when **Add folder** or **Add images** is used, so a large folder does not freeze the window.
- Switching images reads the file (ND2 stacks included) in the background. Clicking Next several times shows only the last image asked for.
- Window-resize lock (user fix): child widgets no longer set a large minimum size, so the napari window can shrink after a folder is loaded. Fixed a crash in that change: `ScrollMode` is on `QAbstractItemView`, not `QAbstractScrollArea`.
- Tests: results stay out of the image folder; results-inside-images detection; New experiment from the GUI; switching images returns in under 0.5 s while a slow file loads. 100 passed with the window (xvfb + Mesa).
- Live check on the 9 real ND2 files: all listed; results written only to the separate folder; Next image returns in about 1 ms; the window shrinks from 1500x950 to 900x650.

## 3D analysis of Z-stacks, with hardware-based recommendations (2026-09-28)

- Step 2 **Z-stacks** now offers four options; all stay available: **2D: max projection**, **2D: one slice**, **3D: link slices** (each slice segmented in 2D, outlines linked when they overlap by at least **Link overlap**, IoU, default 0.25), **3D: whole volume** (Cellpose `do_3D` with anisotropy from the Z step; classical 3D threshold and anisotropic watershed).
- Brightness for 3D is scaled once for the whole stack by default (**Brightness: Whole stack**), then passed to Cellpose with `normalize=False`. Cellpose's own default (`norm3D=False`) scales each slice separately, which stretches dim top and bottom slices so their outlines stop matching; that is a likely cause of the earlier "bimodal" stitching failures. For classical Otsu, "whole stack" means one threshold for all slices. Classic Cellpose with a blank diameter estimates it once, from the projection, so all slices use the same size.
- 3D results: each object is measured over all its voxels. New columns `centroid_z` (µm), `volume` (µm³; voxels without a pixel size; blank without a Z step), `z_slices`, `z_first`, `z_last`, `z_flag`. `area` is the largest cross-section so size limits mean the same as in 2D. **Minimum slices** removes objects in fewer slices.
- Linking checks: objects in one slice are flagged `one_slice` (split nuclei, or nuclei at the stack's top or bottom); objects more than 1.6x deeper than the typical object is wide are flagged `possibly_merged`. Both are counted in a warning with a hint (lower or raise Link overlap). Flags do not change counts.
- Rings and grown/shrunk regions work in 3D with the Z step taken into account. Manual edits work in 3D. Edits are now tied to the segmentation they were made on (older bug: a deletion saved on one segmentation was re-applied by object number to a different segmentation after settings changed).
- Step 1: **Z step** can be entered next to the pixel size (**Set sizes (µm)**); the Slices column shows, e.g., 7 × 1.5 µm.
- Hardware recommendation (`cellquant/hardware.py`): GPU name and memory (from the background PyTorch check), system memory and CPU count, the method and engine, and the stack sizes give an estimated time per image beside every option, and a recommendation with the reason: whole volume only with a GPU with 8 GB or more, 10+ slices and a Z step no more than 2x the pixel size; a max projection first when linking slices would take more than 5 minutes per image (for example Cellpose-SAM on a CPU); otherwise link slices. It also says when a usable GPU is switched off. **Use recommended** applies it. After a run, estimates use the speed measured on this computer. Options that may not fit in memory are marked, not refused.
- Tests: `tests/test_3d.py` (linking rules, threshold effect, stack vs slice scaling, stacked nuclei counted separately in both 3D modes and merged in a projection, volumes, flags, minimum slices, 3D size limits, 3D rings, 3D edits, edits tied to segmentations, reopen and export, both Cellpose engines called with `normalize=False`, `do_3D`, `z_axis=0` and the anisotropy), `tests/test_hardware.py`, and a live-window 3D test; linking also matches Cellpose's `stitch3D` grouping exactly on random stacks. 132 passed with the window.
- Live check on the real ND2 files (20x, 7 slices, 1.5 µm steps, anisotropy 2.6): recommendation "3D: link slices", with whole volume noted as unhelpful for this Z step; the run shows the slice slider and 3D outlines. With the classical method on PAX6, 291 of 665 objects were one-slice objects, which the flag reports; classical is not suitable for this tissue.
- Not verified: Cellpose in 3D with real model weights (the weights cannot be downloaded here). The time constants for Cellpose are starting guesses until the first run on your computer.

## Original channel colors, choosing file types and images, progress and Cancel (2026-09-28)

- **Colors:** channels are shown in the colors stored in the file: ND2 channel colors from NIS-Elements (the AXR files: Green 54,255,0; Red 255,0,0; Far Red 255,0,255), ImageJ/Fiji LUTs, or OME-TIFF channel colors. A channel with no stored color is shown in gray; CellQuant no longer assigns napari's default false colors. Colors are kept per image (`channel_colors` on the image record). The practice images now store blue/green/red LUTs.
- **File types:** New experiment has **Look for: ND2 files / TIFF files** (both by default; at least one required). Step 1 has the same boxes for **Add folder**; the choice is saved with the experiment. The import note says how many files of the other type were not added, and a folder holding only the unchosen type is not reported as empty. Files picked one by one with **Add images** are always added.
- **Choosing images:** **Show all / ND2 only / TIFF only** plus the text filter narrow the list; **Include shown**, **Leave out shown** and **Include only selected** (row selection with Ctrl/Shift) change many rows at once; a line shows "N of M images included (… ND2, … TIFF)". Left-out images are skipped by Next/Previous image and by Run all images. Re-including an image restores its status (it previously stayed "excluded"). **Run selected images** with nothing selected now says so instead of running every image.
- **Progress:** single-image runs, previews, imports and batches report each step to the bar and status line ("Reading the image file", "Finding objects: slice 4 of 7", "Linking outlines across slices", "Measuring markers in each object", "Saving results"; batches prefix "Image 3 of 9 (name):" and the bar covers the whole batch). Steps of unknown length (Cellpose 2D, Cellpose whole-volume 3D) show a busy bar.
- **Cancel / Pause:** Cancel works for single images as well as batches and stops after the current step (between slices in 3D); nothing from the unfinished image is saved; finished batch images are kept and the message says "Stopped after N of M images". Pause also takes effect between steps. Cellpose's whole-volume 3D call cannot be interrupted part-way. Run, Preview, Set up markers, Export, import and edit buttons are greyed out while work runs and restored afterwards.
- Re-running needs no restart: change settings and click Run or Run all images again (confirmed in the window tests).
- Tests: `tests/test_colors_types_progress.py` (ND2, ImageJ and OME colors; gray without colors; practice colors; file-type choice; subsets and re-including; per-slice progress; cancel mid-image keeps nothing; cancelling a batch keeps finished images) and window tests (progress text and bar during a run, run buttons disabled and Cancel enabled, cancel part-way, batch progress, include buttons, layer colormaps from the file). 148 passed with the window.
- Live check on the real ND2 files: layers show green, red and magenta from the files; "4 of 9 images included (4 ND2)" after leaving out the Retina 1 folders; progress "Finding objects: slice 4 of 6", 50%, with Run buttons greyed out.

## Segmentation and threshold experiment on the E14.5_E17.5 images (2026-09-28/29)

- `cellquant/sweep.py` and `cellquant/sweep_report.py` (new; how to use them: `docs/SWEEP.md`) run a grid: every image, every channel as the channel to segment, every method (classical, Cellpose 3, Cellpose-SAM) and Z mode, then count positives at 10 configurations per run (5 cell-probability thresholds x lenient/strict marker thresholds). Runs are saved one by one (`units/<image>_<channel>_<engine>_<mode>/`), so an interrupted experiment resumes and runs can be split across computers.
- Real experiment on the 9 ND2 files: 243 runs, all finished; counts recomputed from the saved objects equal CellQuant's own counts for every run; no unmeasured nuclei. Not run: Cellpose 3 whole-volume 3D (a 256 x 256 piece of one stack took over 7 minutes and 1.6 GB), Cellpose-SAM 3D (excluded; `--include-cellpose4-3d` runs it).
- Marker thresholds are multiples (0.7x lenient, 1.4x strict; the extended table has 0.5x to 2x) of a per-channel, per-Z-domain natural threshold (log-scale Otsu of nuclear mean intensity above background, from a reference engine's nuclei). They come from the data, not from hand counts; `collate --natural green=...` sets them by hand without segmenting again. `score --hand-counts` ranks every method and configuration against a filled-in `hand_counts_template.csv`.
- `segmentation.py`: Cellpose models are loaded once per process, and `keep_network_output()` lets the five cell-probability thresholds share one network pass (Cellpose applies the threshold after the network). Outside that block nothing changes. Real Cellpose 3 and Cellpose-SAM weights gave identical masks with and without the shortcut.
- Real Cellpose was run for the first time here, in a cloud computer (2 CPU cores) with the weights copied from your `.cellpose\models`. Cellpose 3 in its own environment: `PYTHONPATH` pointed at a separate install.
- Tests: `tests/test_sweep.py` (design, counting rules including unmeasured objects, resume, merge, collate equals the pipeline, report, hand-over folder and README, scoring against hand counts) and `tests/test_network_reuse.py`. 169 passed with the window.
- Findings so far (no hand counts yet): nuclei per image differ up to about 8x between methods on the same image (far red: 214 for classical one slice to 737 for Cellpose 3 linked slices); Cellpose-SAM finds a median of 2.7x as many nuclei as Cellpose 3 in the same 2D mode; percent positive for the other markers is steadier across methods than the counts; cell probability -2 loses up to about 47% of nuclei (merging) and +2 loses 17 to 30%.

## HPC prep: cluster packages, the Slurm worker, and import (2026-09-29)

Built from `docs/HPC_PREP_SPEC.md` (slices 1-6; slice 7, a real Alpine run, needs a cluster account). User and maintainer guide: `docs/HPC_PREP_AND_SUBMISSION.md`. Acceptance status per criterion: `docs/HPC_ACCEPTANCE.md`.

- New package `cellquant/hpc/`: versioned records with published JSON schemas (`models.py`, `schemas/`), lossless preparation with READY binding (`prepare.py`, `validate.py`), profiles and runtime contracts (`profiles.py`, `runtime.py`), Slurm scripts (`templates.py`), preflight (`preflight.py`), the headless worker with per-image publication, lease and resume (`runner.py`, `publish.py`, `lease.py`), import (`import_results.py`), lineage keys (`lineage.py`), and the CLI `python -m cellquant.hpc`. Window: `cellquant/gui/hpc_panel.py`, behind a switch (`CELLQUANT_ENABLE_HPC=1` or `~/.cellquant/enable_hpc`).
- Shared code changed so the cluster and this computer read and save images the same way: `image.read_stack` / `reduce_stack` (load_image is now these two steps; behavior unchanged), `cellquant/inputs.py` (every record is loaded through `load_record_image`), and complete result persistence (QC, reports, marker combinations, column types and missing values now survive a reload; older saved results still open as before).
- Fixes found on the way: a cancelled batch was recorded as "completed" (now "cancelled"); an experiment folder that was moved kept writing to its old location (`load_experiment` now uses the folder it is opened from); running one image hid other images' results saved in earlier runs (results are now looked up newest run first); with no Cellpose installed, step 2 erased the engine and model from the settings (now kept).
- Imported cluster results are re-measured from their saved objects without the segmentation engine (`remeasure_persisted_result`), so they can be edited, re-thresholded and exported with no Cellpose and no original files; edits never re-segment.
- Tests: `tests/test_hpc_*.py`, `tests/test_gui_hpc.py` (including a full run through the generated submit.sh and job.sbatch with Slurm and the GPU stubbed, ShellCheck clean). 265 passed with the window. Real Cellpose-SAM and Cellpose 3 weights (CPU): original and prepared input gave identical labels and measurements (`docs/HPC_ACCEPTANCE.md`).
- Not done here: environments built on Alpine, GPU preflight on a real node, and the smoke test; profiles stay experimental until then.

## Positive by percent of the cell (2026-09-29)

CellQuant v1's `positive_fraction` rule is back: a cell is positive when at least a minimum percent of its
pixels are at or above a pixel level (optionally at most an upper level).

- Recipe: measurement statistic `percent_above` with `pixel_level` and optional `pixel_level_high`
  (values after background correction; `local_ring` uses the ring's median); classification
  `comparison: at_least` (≥) besides the default `above` (>). The percent is `100 × passing / pixels` in
  one division, so "at least N%" is exact at the boundary. Objects with a non-finite pixel are unmeasured.
  Recipes without these fields keep their settings fingerprint.
- Window: step 3 **A cell is positive when** (mean brightness, or enough of its pixels are bright, with
  the minimum percent; the starting pixel level is the automatic cutoff between dim and bright cells' means);
  step 4 **Percent-of-cell rule** box (minimum percent, pixel level, optional upper level, **Apply pixel
  level** measures again without segmenting); advanced Measurements table (`percent_above`, pixel levels,
  **Positive when value is** above / at least).
- Not carried over from v1: the uncertainty margin (a third "uncertain" call) and centroid-based region
  eligibility.
- Tests: `tests/test_percent_rule.py`, `test_percent_of_cell_rule_in_the_window`.

## Synthetic retina test images; release 2.0.0 (2026-09-29)

- `cellquant/synthetic_retina.py` makes z-stacks that resemble the lab's 20x ND2 files (checked side by side
  with a real image: channel names and colors, 12-bit values with camera offsets, 7 slices at 1.5 µm, 0.575 µm
  pixels, crowded nuclei elongated across a curved band of tissue, Z-heavy blur, dimmer deep slices, uneven
  illumination, shot and read noise, nucleoli, saturated specks), with true labels and per-nucleus truth
  (reporter+, OTX2+ whole or partial, PAX6-high). Control and CRISPRi sets have known (Fluor+ OTX2+) / Fluor+.
- With the true nuclei, the best single cutoff calls about 98.5% (OTX2) and 99% (reporter) of nuclei right;
  the automatic starting cutoff misses dim positives (27.6% instead of 31.3% on one image), which is the kind
  of error step 4 is for. Classical 3D segmentation finds 523 objects for 420 true nuclei (over-splitting).
- Tests: `tests/test_synthetic_retina.py`. Suite: 289 passed.
- Version 2.0.0 (version 1 ended at 0.4.0a3), MIT license, `CITATION.cff`, `CHANGELOG.md`, `.gitignore` that
  keeps images and results out of the repository. Lab file and folder names were removed from the docs.

## Several analyses of the same images (2.1.0, 2026-09-29)

- `experiment.json` has an `analyses` list (`AnalysisRecord`: recipe id, name, working folder, latest run,
  stored per-image review state). The active analysis's review state lives on the image records as before;
  switching stores it and restores the other's. Experiments without the list get one analysis on opening,
  using `working/` and every earlier run, so 2.0.0 experiments open unchanged.
- Results are kept apart: each analysis has its own working folder (`analyses/<id>/working`; the first keeps
  `working/`), and a run is read only by the analysis whose recipe id it records. Edits carry the
  segmentation they were made on; unstamped (older) edits belong to the first analysis only. Loading settings
  never changes which analysis they belong to.
- Controller: `add_analysis`, `switch_analysis`, `rename_analysis`, `remove_analysis` (keeps files),
  `analyses_for_channels` (one per channel, reusing an analysis with the same settings), `run_analyses`
  (progress counted across analyses, Cancel skips the rest, the active analysis is restored), `export_all`
  (one folder per analysis plus `all_analyses_image_summary.csv`).
- Window: Analysis list and buttons above the steps; Run all analyses (bottom bar and step 5) and Export all
  analyses (step 5) appear when there are several; switching is refused while anything runs; settings on
  screen are saved to the analysis being left. HPC prep packages the analysis shown.
- Tests: `tests/test_analyses.py` (10), `tests/test_gui_analyses.py`; synthetic retina z-stacks are the test
  images. Suite: 300 passed. An independent review found four problems (a removed analysis's runs read by the
  original one; edits shared by analyses with the same segmentation; a re-included image left "excluded"; settings
  pages usable during Run all analyses), all fixed with tests.

## The Plan dock and other channel layouts (2.1.0, 2026-09-29)

- Each analysis has a plan (`AnalysisRecord.plan`: image id -> `PlanEntry(run, channel)`); blank entries mean
  "run every included image with the analysis's channel". Controller: `is_planned`, `planned_images`,
  `set_planned` (ticking a left-out image includes it), `set_plan_channel` (checked against the image's channel
  count), `segmentation_channel_for` and `measurement_channels_for` (with the reason), `plan_status`.
  `run_analyses` without a list of images runs each analysis's ticked images.
- Channel layouts: the experiment's channel list comes from the first image; an image whose file lists the
  same names in another order is segmented and measured in the channels of the same names (the analysis's
  settings are not changed; the result records the channels used). Without the name, the position is kept
  and the Plan marks the image ⚠. Per-image channel choices override both.
- `cellquant/plan.py` groups images by layout, folder, or layout then folder. `cellquant/gui/plan_dock.py`:
  grid and tree views, group ticks, the apply bar (selected/all images × one/all analyses: tick, untick, set
  channel), right-click and double-click, status colours; locked while anything runs.
- Tests: `tests/test_plan.py` (4, including the same image in another channel order giving the same objects
  and marker values), `tests/test_gui_plan.py`.
- An independent review found: channels mapped twice when an image of another layout was edited after
  reopening (fixed: the per-image settings are applied once); settings saved during such an analysis could
  store the image's channels (fixed: the analysis's own settings are saved, and changing settings then is
  refused); HPC prep ignored the plan (fixed: it starts from the plan's images and refuses images with
  per-image or other-layout channels, which packages cannot express yet); a failed per-image channel choice
  could apply to some images (fixed: checked first); approvals and "mixed settings" did not allow for
  per-image channels (fixed). A crash when a tick in the dock redrew the dock under its own signal was fixed
  by redrawing afterwards. Suite: 307 passed, twice.
- Known limits: the reference channel layout is the first image with named channels that match the
  experiment's channel count; a file that names two channels the same uses the first.
- Never false color (a standing rule): channels are shown only in the colors stored in each file (ND2
  channel colors, ImageJ LUTs, OME-XML), black to that color, and gray when the file stores none; RGB camera
  ND2s keep their true red, green and blue. New test: images of different channel layouts in one experiment
  are each shown in their own file's colors and order. The sweep report's plots use chart colors, never
  image data.
