# Start here: use CellQuant without coding

CellQuant finds objects such as nuclei in microscope images and measures their fluorescence. Its napari window lets you inspect the image and the colored object outlines before saving. **CellQuant does not decide whether a result is biologically correct.** Review masks, channel choices, and marker thresholds against your experiment.

This guide is for **Windows** and the current 0.4.0a2 alpha. For a first trial, use one small TIFF, OME-TIFF, or ND2 image with known channel names and pixel/voxel spacing. Keep your original image untouched. Allow ample free disk space for output. If your files are in OneDrive, make them available offline first (for example, **Always keep on this device**).

## Install and open

1. Install **Miniconda**, **Miniforge**, or **Anaconda** on this computer if needed. Ask your computer administrator if you cannot install software. You do not need to write Python code.
2. Open the CellQuant project folder containing **`Install CellQuant.bat`**. Double-click it and press Enter to use the suggested folder. Installation creates two separate environments: **Cellpose-SAM v4** and **classic Cellpose v3**. This can take time. Leave the window open until it says **Install finished**.
3. Double-click **`Open CellQuant.bat`**. Choose **v4** for the current default or **v3** if v4 is too slow or runs out of memory. Wait for the napari window.
4. In napari's menu, choose **Plugins → CellQuant Cellpose Pipeline**. A CellQuant panel appears with a **Mode** menu.

**Success check:** the panel shows **Single image**, **Batch folder**, **HPC prep**, **Coexpression**, and **Segmentation Review/QC**. If installation fails, read the `install_last.log` file opened by the installer. If launch says an environment is missing, run the installer again on this computer. If the plugin is missing, close napari and reopen it with `Open CellQuant.bat`.

## Analyze one image

1. Drag a TIFF/ND2 onto the napari canvas, or set **Mode → Single image** and use **Open lazily**. Leave **series** and **position** at `0` unless the acquisition has more than one series/position. Channels open in their microscope LUT colors (you can change them afterward in the layer controls).
2. In **Run Cellpose**, select any opened channel layer and the channel that contains the objects you want to segment. Check the actual channel name; the first channel is not necessarily your nuclear stain.
3. Review the segmentation settings and preflight summary. Check the image spacing and the intended 2D/3D mode. Hover a control for its explanation. For v4, **Native** diameter is a reasonable starting policy; for v3, a measured **Manual** diameter can help. The engine menu only lists engines installed in the selected environment.
4. Click **Run Cellpose**. Wait for a Labels layer, then inspect several fields and Z planes. Check for missed, merged, or split nuclei. Correct labels in napari if your review procedure calls for it. **Cancel** stops at a supported checkpoint; **Kill** ends a stuck Cellpose worker immediately.
5. In **Measure edited labels + save**, select the reviewed Labels layer, choose an output folder, and click the button. Reopen the output folder to confirm the files were written.

**Success check:** the saved run has `labels.tif`, `objects.csv`, `intensities.csv`, `config.json`, `provenance.json`, `events.jsonl`, and `status.json` plus QC images. `objects.csv` has one row per object; `intensities.csv` has per-channel measurements. `status.json` is the completion record. Keep the whole run folder, not only the spreadsheets. Display brightness in napari does not change raw measurements.

## Analyze a folder

1. Set **Mode → Batch folder**. Choose an **Input folder**, a separate **Output folder**, and **File type**. Select **recursive** only if images in subfolders should be included. Download any cloud-only input placeholders before starting.
2. Click **Survey folder**. Review the channel layouts it finds. CellQuant writes and loads a starting configuration under `<output>/survey/`.
3. For each layout you want, check its inclusion box and select the correct segmentation channel. A channel-name suggestion is only a suggestion. Review segmentation settings and the preflight summary.
4. Click **Run batch**. Each image gets its own run folder, normally named like `sample.tif.cellquant` under the output folder. Inspect results and failure records image by image; one failed image does not stop the rest.

**Success check:** the survey folder contains `cellquant_run_config.yaml`, and completed per-image folders contain `status.json` and measurement files. If nothing runs, check that you selected at least one layout and its channel. Keep input and output folders separate to make review easier.

## Count nuclear marker combinations

Coexpression scores fluorescence pixels inside each complete segmented nucleus. It accepts original Cellpose masks or masks approved in **Segmentation Review/QC**. It does **not** assign cytoplasmic or membrane signal to cells, and its threshold suggestions require biological review.

1. Open the source image in **Single image** and run segmentation, or use **Mode → Segmentation Review/QC** to inspect/correct masks. You can also load a previously reviewed labels TIFF.
2. Set **Mode → Coexpression**. Select the image and its matching labels; use **Load reviewed TIFF** if needed. Confirm that the labels align with the image. If a loaded recipe's channel order differs, remap markers and use **Confirm reviewed channel layout** after checking it.
3. Add marker rows. Give each marker a biological name, assign the acquired channel, and enter raw intensity bounds and the **positive fraction** (the fraction of each nucleus's pixels that must pass). A marker that was not acquired must be marked missing, not negative.
4. To choose a threshold, select a marker row and click **Calibrate selected marker**. In the calibration panel, click **Load / refresh**. Select representative nuclei in napari and use **Mark selected nucleus negative** or **Mark selected nucleus positive**. Choose an Otsu or negative-example percentile method and click **Propose starting raw low threshold** if you want a suggested starting value. Click **Preview** to examine calls and disagreements. Click **Accept reviewed settings** only after review; a proposal alone does not change the main marker row. You can calibrate one marker before filling in other rows.
5. Click **Preview calls** and inspect the overlay, per-cell calls, query totals, denominators, missing counts, and uncertain counts. Save a recipe if you want to reuse its settings. Click **Save classification** to write an immutable analysis. Use **Reopen classification** to check the saved result or compare a proposed revision.

**Success check:** the saved classification folder includes `calls.csv`, `queries.csv`, `patterns.csv`, `exclusions.csv`, and `metadata.json`. The recipe keeps calibration evidence from the image on which it was collected; loading it on another image does not mean that second image has been reviewed. Threshold and label edits clear stale previews, so preview again before saving.

### Classify a completed batch

Use this after segmenting a **Batch folder** (optionally after **Segmentation Review/QC**). Keep the original TIFF/ND2 images at the paths recorded during segmentation; batch classification needs to read them again. In **Mode → Coexpression**, choose the **Batch** tab:

1. In **1. Select runs**, choose the batch output folder and click **Discover runs**. Include only the completed runs you intend to classify.
2. In **2. Review labels**, check each run's review status. Mask editing lives in **Mode → Segmentation Review/QC** — use **Open Segmentation Review/QC** to edit there, then return. Approval is not required; Quantification can use original Cellpose masks via the **Mask input** policy.
3. In **3. Thresholds**, select each channel layout, set marker names, channels and thresholds, then click **Apply to layout**. Check per-image settings if an acquisition needs a deliberate override.
4. In **4. Run + results**, choose the classification output folder, pick a **Mask input** policy (prefer approved / approved only / original), review the preflight table, then click **Run batch classify**. Use **Open results folder** to inspect the per-image results and failures.

**Success check:** a new `classify_batch_...` folder appears under the chosen output folder. Check its summary and each included image's results; a completed segmentation run still needs its own classification review.

## Optional: prepare a cluster job

Use **Mode → HPC prep** if you have Alpine access and a maintainer-supplied compatible cluster environment. The local export does not need a GPU. Complete each tab and click **Next** at its bottom; CellQuant unlocks the next tab only after the current tab's required image selection, segmentation settings, or cluster profile information passes validation. The status message explains what to correct when a tab cannot advance, and earlier unlocked tabs remain available for review. Start with one or two representative images and follow [Prepare images in CellQuant and submit to Alpine](HPC_PREP_AND_SUBMISSION.md) from image selection through export, transfer, job submission, and result import. Choose either its [terminal](SUBMIT_THROUGH_TERMINAL.md) or [Open OnDemand](SUBMIT_THROUGH_OPEN_ONDEMAND.md) submission path. The generated package does **not** install the cluster environment, and no live Alpine smoke run is recorded for this alpha.

## When something looks wrong

| Symptom | First check |
| --- | --- |
| Installer cannot find Conda | Install Miniconda/Miniforge/Anaconda, then rerun `Install CellQuant.bat`; inspect `install_last.log`. |
| Image will not open | Confirm it is a local, downloaded TIFF/OME-TIFF/ND2; check series/position and acquisition metadata. |
| GPU is absent from device choices | Read the environment summary. CUDA appears only if the selected environment's PyTorch can use it; a visible NVIDIA card alone is insufficient. |
| Segmentation looks implausible | Recheck channel, spacing, mode, and label alignment. Try a small reviewed sample before a batch. |
| Coexpression cannot score | Confirm image/labels share an analysis grid, every acquired marker maps to the right channel, and recipe layout changes were reviewed. |
| Batch output seems missing | Check the selected Output folder and its per-image folders; each file has its own status. |

For parameter meanings and limits, see the [developer guide](DEVELOPER_GUIDE.md) and [architecture](../ARCHITECTURE.md). For a problem specific to Alpine preparation or import, use the [HPC guide](HPC_PREP_AND_SUBMISSION.md).
