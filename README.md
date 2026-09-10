# CellQuant

CellQuant is a napari plugin and command-line tool for nucleus segmentation
(Cellpose-SAM) and per-object measurement on 2D or 3D fluorescence images.
The GUI and CLI share the same calibrated analysis core.

Supported inputs include TIFF, OME-TIFF, and ND2. Outputs are label masks,
measurement tables, QC figures, and a full provenance record for each run.

The runnable package lives in [`_cellquant/_pipeline`](_cellquant/_pipeline).
Installers, launch scripts, source, tests, and configs are there. Commands
below assume that directory as the working directory.

For the scientific parity contract, package boundaries, and verification
gates, see [`ARCHITECTURE.md`](_cellquant/_pipeline/ARCHITECTURE.md). Measured
validation status lives in [`docs/STATUS.json`](_cellquant/_pipeline/docs/STATUS.json).

---

## Requirements

- **Python 3.11**
- **napari** (installed with CellQuant)
- Optional but recommended for large volumes: a **CUDA-capable GPU** and a
  driver-compatible PyTorch build
- On Windows, **Miniconda**, **Miniforge**, or **Anaconda** if you use the
  provided install/launch scripts

Review voxel spacing, segmentation channel, anisotropy, device, and model
path/hash in your config before each acquisition or campaign.

---

## Install

### Windows (recommended)

In `_cellquant/_pipeline`, double-click **`Install CellQuant.bat`**, or from
PowerShell:

```powershell
cd _cellquant/_pipeline
.\scripts\install_windows.ps1
```

The installer creates **two** conda environments (sibling folders):

| Env | Purpose |
| --- | --- |
| **Cellpose-SAM (v4)** | Current scientific default; heavier |
| **Cellpose classic (v3)** | Lighter; use if v4 is too slow or runs out of memory |

You choose the folder for the **v4** env (Enter for
`<conda-base>\envs\cellquant-napari`). The **v3** env is created beside it as
`<name>-v3` (for example `cellquant-napari-v3`). You can also pass a path:

```bat
Install CellQuant.bat "D:\Software\cellquant-napari"
```

```powershell
.\scripts\install_windows.ps1 -Prefix "D:\Software\cellquant-napari"
```

Paths are saved in `cellquant_env.json`. On a machine with an NVIDIA GPU and
working drivers, the Windows installer automatically replaces the CPU PyTorch
wheel with a driver-compatible CUDA build (`cu118`–`cu130`) so Cellpose can use
the GPU. Machines without NVIDIA hardware keep the CPU build.

### Any platform (pip)

From `_cellquant/_pipeline`:

```bash
python -m pip install -e ".[test]"
```

Use a dedicated virtual environment or conda env with Python 3.11. For classic
Cellpose 3.x in that env: `pip install "cellpose>=3.1,<4" --force-reinstall`
after the editable install.

---

## How to open it

### Option A — Double-click (Windows)

1. In `_cellquant/_pipeline`, double-click **`Open CellQuant.bat`** and choose **v4** or **v3** when prompted.
2. Wait for the napari window to appear.
3. In napari, open **Plugins → CellQuant Cellpose Pipeline**.

If v4 fails or feels unusable on your machine, choose **v3** at the prompt. The
engine menu in the plugin only lists what is installed in the active env.

If the launcher reports that the environment is missing, run
**`Install CellQuant.bat`** once, then try again.

You can pin a launcher to the taskbar or create a desktop shortcut for quicker
access.

### Option B — PowerShell launcher

```powershell
cd _cellquant/_pipeline
.\scripts\launch_napari.ps1            # prompts when both envs exist
.\scripts\launch_napari.ps1 -Engine v3
.\scripts\launch_napari.ps1 -Engine v4
```

### Option C — Already-known environment

If you installed to a custom folder (see `cellquant_env.json`):

```bash
conda activate "D:\Software\cellquant-napari"      # v4
conda activate "D:\Software\cellquant-napari-v3"   # v3
napari
```

Then choose **Plugins → CellQuant Cellpose Pipeline**.

---

## Using the napari plugin

Open **Plugins → CellQuant Cellpose Pipeline**, then choose a **Mode**:

### Batch folder

1. Choose input/output folders, file type (e.g. ND2), and recursive.
   **Cloud folders (OneDrive, Dropbox, etc.) are supported.** Inputs can stay
   on OneDrive if files are downloaded (“Always keep on this device”).
   For outputs, CellQuant writes each run store to a local staging area under
   `%LOCALAPPDATA%\CellQuant\staging\`, then publishes a copy into your chosen
   cloud folder when that file finishes — so sync locks do not fail mid-Cellpose.
2. Click **Survey folder** — CellQuant clusters files by channel layout and
   **writes + auto-loads** `<output>/survey/cellquant_run_config.yaml` (from the
   bundled `sample_config.yaml`; you do not browse for a config)
3. For each layout, include it and pick the **segmentation channel** (any
   channel — nuclear or cytoplasmic; name hints are optional defaults only)
4. Click **Run batch** — one queue per layout; per-layout YAMLs are written under
   `<output>/survey/configs/`

### Single image

- opens TIFF / OME-TIFF / ND2 lazily into **CellQuant image**
- uses the bundled sample config automatically (no config file picker)
- pick segmentation channel from the image metadata dropdown, then Run Cellpose
- measure/save writes a run store; a run config is materialized in the output
  folder if needed

### Nuclear coexpression

After opening an image and reviewing or loading a labels layer, choose
**Coexpression** in the dock. Enter marker names, map each to a channel, and
provide intentional raw intensity bounds and a cell positive-pixel fraction.
CellQuant reports inclusive marker combinations and complete exact patterns,
each with its numerator, evaluable denominator, missing count, and uncertain
count. Save recipes for reuse; a changed channel order requires explicit
remapping.

This first classification mode measures the complete segmented nucleus. It does
not assign cytoplasmic or membrane signal to a nucleus. Classification is
independent of Cellpose, so label review and threshold changes do not require
rerunning segmentation.

### Review a marker threshold

Select a marker row, give it a biological name and acquired channel, then choose
**Calibrate selected marker**. Other marker rows can remain unfinished while you
calibrate this one. Load the marker, pick nuclei in napari, and mark known
negative or positive examples. You can request an Otsu starting threshold or an
explicit percentile of pixels from your negative examples, or enter a threshold
yourself. A negative-pixel percentile does not specify the false-positive rate
of whole cells.

Choose **Preview these settings** to inspect the raw fluorescence, cell calls,
and example disagreements. **Accept reviewed settings** copies the reviewed
thresholds into the marker row. The recipe retains the examples, source
fingerprints, proposed settings, and accepted settings. Reusing a recipe on a
different image retains its historical calibration evidence; it does not mean
the new image has been reviewed. Input edits invalidate the current preview.

Once all markers are configured, preview coexpression and save a classification.
These tools support review; biological accuracy still needs validation against
expert-reviewed retinal cells.

Parameter optimization is deferred until Ian has tested the pipeline and
explicitly confirmed that it works correctly. See
[`memory/2026-09-08-deferred-parameter-sweep.md`](_cellquant/_pipeline/memory/2026-09-08-deferred-parameter-sweep.md).

Cellpose runs in a killable worker process in napari. **Cancel** stops after the
current plane/checkpoint (good for smaller jobs). **Kill** terminates the worker
immediately mid-eval so you should not need Task Manager for stuck long runs.

---

## Headless commands

With CellQuant installed and on your `PATH` (or inside the conda env):

```bash
cellquant survey INPUT_DIR SURVEY_OUT --recursive --file-type nd2
cellquant survey-run SURVEY_OUT/survey.json BATCH_OUT --config your_config.yaml --assignments SURVEY_OUT/assignments.csv
cellquant batch INPUT_DIR OUTPUT_DIR --config your_config.yaml --recursive --file-type tiff
cellquant harness STACK.tif CASE_OUTPUT --config your_config.yaml
cellquant parity REFERENCE_LABELS CANDIDATE_LABELS REPORT_DIR
cellquant showcase preprocess STACK.tif SHOWCASE_DIR --config your_config.yaml --crop 0:8,0:256,0:256
cellquant classify IMAGE.tif LABELS.tif CLASSIFY_OUT --config your_config.yaml --recipe recipe.json
cellquant reclassify CLASSIFY_RUN CLASSIFY_OUT --recipe revised_recipe.json
```

- `survey` clusters acquisitions by channel layout; edit `assignments.csv` (or
  use the napari Survey UI) before `survey-run`.
- Batch discovers supported images under the input path. Use `--recursive` /
  `--no-recursive` to override `io.recursive`, and `--file-type {all,tiff,nd2}`
  or `--suffixes .tif .nd2` to override `io.suffixes`.
- Batch outputs mirror input parents (for example `sample.tif.cellquant`).
- A file resumes only when content/size/mtime and full config fingerprints
  match a validated completion marker.
- A failed file writes a failure record and does not stop the rest of the queue.
- `parity` accepts two label TIFFs or two directories; candidate batch trees are
  recognized via `*.cellquant/labels.tif`.

---

## Configuration

- Use **[`sample_config.yaml`](_cellquant/_pipeline/sample_config.yaml)** as the
  full schema example for new projects.
- Use **[`reference_config.yaml`](_cellquant/_pipeline/reference_config.yaml)**
  only when you intentionally want the documented Fiji/notebook-compatible
  profile for the lab reference corpus.

Configs are validated and versioned. Model weights can be pinned by SHA-256;
a hash mismatch fails the run instead of silently substituting another file.

**Cellpose engine (napari):** the dock probes the active environment at startup and
lists only engines that env can run (v4 in the SAM env, v3 in the classic env).
It also reports the installed PyTorch build and whether an NVIDIA GPU is visible
via `nvidia-smi`. CUDA appears in the device list only when
`torch.cuda.is_available()` is true — a system GPU alone is not enough if the
env has a CPU-only PyTorch wheel. Your current env summary is shown at the top of
the segmentation options.

**Two Windows envs:** Install CellQuant creates both. Prefer **v3** at the
`Open CellQuant.bat` prompt if SAM v4 is too heavy. `segment.engine` must match
the Cellpose major version in the active env (`v3` or `v4`); the plugin applies
model defaults (nuclei vs cpsam) when you switch.

Engine detection confirms software availability. It does not establish retinal
segmentation or coexpression accuracy. The current release checks adapter
arguments and synthetic worker behavior; validation on expert-reviewed retinal
images remains a separate step.

**Diameter:** Cellpose-SAM (v4) is size-tolerant — prefer **Native**
(`diameter_px: null`). Classic Cellpose (v3) benefits more from a measured nuclear
width (**Manual**, or **Add measure layer** → draw a line → **Use drawn line**).
A larger manual diameter downsamples (often faster; can merge small objects).

Hover any dock control for a short explanation of that parameter.

Segmentation modes:

| Mode | Role |
| --- | --- |
| `volume_3d` | Full 3D Cellpose (`do_3D=True`) |
| `stitch_2d` | Per-plane 2D + Z stitching (`stitch_threshold` required) |
| `single_plane_2d` | One explicit zero-based `segment.z_index` |
| `max_projection_2d` | Max-Z projection, then 2D segmentation |

Only `stitch_2d` may use a nonzero `stitch_threshold`, and it must satisfy
`0 < stitch_threshold <= 1`: zero would skip stitching and leave plane-local
Cellpose IDs to collide across planes.

Single-plane and max-projection runs produce singleton-Z labels. Their
morphology table reports `area_um2` from Y/X spacing only and leaves
`volume_um3` empty, so source Z spacing cannot change a 2D area or an
area-based size filter; volumetric and stitched runs report `volume_um3` and
leave `area_um2` empty. For 2D modes `postprocess.min_volume_um3` /
`max_volume_um3` are read as µm² areas (use `min_area_um2` / `max_area_um2` to
say so explicitly), and Z border faces are rejected because a single plane is
simultaneously `z0` and `z1`.

One channel drives segmentation per run. To segment several channels from one
image, run them separately with different channel selections and separate
output directories.

---

## Data contracts (short)

| Item | Contract |
| --- | --- |
| Images | `(Z, Y, X, C)`; source intensity dtype preserved at I/O |
| Labels | `(Z, Y, X)` `uint32`; background `0` |
| Spacing | Positive `(z_um, y_um, x_um)` metadata — never inferred from name/shape |
| Discovery | `io.recursive` plus `io.suffixes` (subset of `.tif` / `.tiff` / `.nd2`) |
| Provenance | Config, fingerprints, events, environment inventory, atomic status marker |

Disabled transforms remain explicit in the serialized config. Every consumed
Cellpose argument is recorded.

---

## Further reading

- [`ARCHITECTURE.md`](_cellquant/_pipeline/ARCHITECTURE.md) — scientific contract and package APIs
- [`docs/STATUS.json`](_cellquant/_pipeline/docs/STATUS.json) — measured gates and open issues
- [`docs/CUDA_GAUNTLET.md`](_cellquant/_pipeline/docs/CUDA_GAUNTLET.md) — GPU validation checklist
