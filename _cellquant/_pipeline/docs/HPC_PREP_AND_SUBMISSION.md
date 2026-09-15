# Prepare images in CellQuant and submit to Alpine

This guide follows the current CellQuant controls and generated files, checked September 11, 2026. Start here if you have not submitted a CellQuant job before. You will prepare images on your computer, transfer a package to Alpine, submit a job, and bring the results back.

**Current release status:** local preparation, package validation, scratch staging, result copy-back and Windows import relocation are implemented. A compatible Alpine environment must already exist — the setup helper does not install one. No live Alpine smoke job has been recorded yet, so a green local validation result is not proof that a cluster job will succeed. Run a small H200 trial after environment setup before a large dataset.

## What you need before starting

- CellQuant installed locally, with **HPC prep** in the plugin's Mode menu. A local GPU is not required for export.
- Source TIFF/OME-TIFF or ND2 images available on disk. For OneDrive files, download them locally first; a cloud placeholder is not readable image data.
- A CURC account with Alpine access, your cluster username, and any required allocation/account name. The allocation name is not necessarily your username. Use the account that worked for your previous Alpine jobs or ask your allocation owner.
- A compatible CellQuant environment already installed on Alpine, with the correct model weights available. Get its exact activation instructions and path from the maintainer. The generated setup helper does **not** install this environment. The maintainer must also check that the generated batch script can activate it; interactive `conda activate` alone is insufficient.
- Enough local disk space for an exported copy of your images and a cluster location for the package and retained results.

For a first trial, select one or two representative images. Use that small package to check the entire round trip before exporting a large dataset.

## Understand the four locations

These are examples; replace `your_username` with your actual cluster username. Keep cluster paths free of spaces for this initial workflow. Do not enter Windows paths in cluster fields.

| Location | Example | Purpose |
|---|---|---|
| Local export root | `D:\CellQuant_HPC` | CellQuant creates a new package folder here |
| Project root on Alpine | `/projects/your_username/cellquant` | Transferred package and retained results |
| Scratch root on Alpine | `/scratch/alpine/your_username/cellquant` | Temporary job working data |
| Environment location on Alpine | `/projects/your_username/cellquant/envs/cellquant-hpc` | Preinstalled software; use the actual supplied path |

The generated package has a name such as `cellquant_hpc_abc123`. Its expected cluster location is **Project root + package name**, for example `/projects/your_username/cellquant/cellquant_hpc_abc123`. Do not add the package name to the Project root field yourself.

Scratch is temporary, not a backup. CURC documents its retention policy and suitable I/O locations in [Filesystems](https://curc.readthedocs.io/en/latest/compute/filesystems.html).

## 1. Open HPC prep on your computer

1. Launch CellQuant using your usual launcher.
2. In Napari, choose **Plugins → CellQuant Cellpose Pipeline**.
3. Set **Mode → HPC prep**.

You should see four tabs: **1. Select images**, **2. Segmentation**, **3. Cluster profile**, and **4. Export + return**. The first tab is available initially. Complete it and use the **Next** button at the bottom to unlock and open the following tab. Previously unlocked tabs remain available if you need to review or change an earlier choice. If HPC prep is absent, check that you launched the installation containing this feature; the old standalone Alpine counting scripts are a different workflow.

## 2. Select and survey images

1. In **1. Select images**, choose **Browse folder…** for a directory or **Add files…** for specific images.
2. Set **File type** to `nd2`, `tiff`, or `all`. Leave **Include subfolders** checked only if you want to search those folders too.
3. Click **Survey acquisitions**.
4. Review the table. Each row represents an acquisition, with Source, Series, Pos, Shape, Channels, Spacing and Status. A single file can produce several rows. Series and position indexes are zero-based: 0 means the first.
5. Uncheck unwanted rows. For a first trial, use **Include none** and check one or two valid rows. Rows with errors need correction or exclusion; multi-timepoint acquisitions are currently unsupported.
6. Under **Channel assignment by layout**, choose the channel that should be segmented for each layout. For nuclei this is usually the nuclear stain, such as DAPI. Confirm the actual channel name rather than assuming channel 0 is correct for every file.
7. Click **Next**. CellQuant advances only after you have surveyed the sources, included at least one acquisition without an error, and assigned a segmentation channel to every included layout. Read the status message at the bottom if the tab does not advance; it identifies what still needs attention.

**Success check:** the included acquisitions, nuclear channels and spacing match your intended experiment. Do not guess missing calibration. Correct the input metadata through your established workflow or obtain help before export.

There is no need to split channels or perform the historical Fiji TIFF preparation for this exporter. It writes the selected acquisition as a lossless multichannel export; the cluster applies the configured scientific preprocessing.

## 3. Set segmentation options

In **2. Segmentation**, review the shared CellQuant segmentation settings. Start with settings already reviewed on representative images. The target profile determines supported settings; your laptop's lack of CUDA does not prevent preparation.

| Mode meaning | What to check |
|---|---|
| Single-plane 2D | Select the intended Z plane; other planes are not the analysis population |
| Maximum-projection 2D | Overlapping objects along Z may merge in projection |
| Linked/stitch 2D | Slices are segmented and linked; review the stitch setting |
| True 3D | Objects are segmented as volumes; check Z versus XY calibration |

Check engine/model, diameter policy, thresholds and mode-specific options. Diameter values labeled pixels are not micrometres. Do not copy a physical diameter into a pixel field. The current export matrix enables v4 modes; v3 export is blocked until its validation gates are met. Unsupported options must not be replaced with a different mode just to make export proceed.

Click **Next** after reviewing the settings. CellQuant first builds the segmentation configuration and checks that the selected engine, mode and GPU profile are an enabled combination. If it stays on this tab, correct the setting named in the status message.

## 4. Configure Alpine

In **3. Cluster profile**, select the GPU profile **before** completing the remaining fields, then recheck every path. Changing profiles can reset fields.

| Field | What to enter |
|---|---|
| GPU profile | H200 is the recommended starting option based on Ian's prior runs |
| Account (Slurm) | Required. From `sacctmgr` — for your account this is `amc-general`. Never use your login email (e.g. `ian.purvis@xsede.org`). |
| QoS | Use the profile's standard value, normally `gpu-normal` |
| GPU (GRES) | Keep the full H200 profile request `gpu:h200:1` for the initial H200 trial |
| Walltime (HH:MM:SS) | Maximum allowed run duration, e.g. `00:30:00` for a genuinely small trial; this is not a runtime prediction |
| Project root | Your project directory from the location table |
| Scratch root | Your scratch directory from the location table |
| Environment location | Exact path of the compatible environment supplied by the maintainer |
| Email notify | Optional; leave blank if unwanted |

Replace every literal `USER` placeholder. Review **Details** and the status text. “Submit-ready” is a profile flag; “No Alpine smoke verification recorded yet” still means a real run has not been verified. H200 remains the recommended default; RTX Pro 6000 is enabled for comparative trials (`gpu:rtx_pro_6000:1`, account required). See [CURC GPU resource guidance](https://curc.readthedocs.io/en/latest/clusters/alpine/alpine-hardware.html).

Click **Next** when the profile is complete. CellQuant checks the required Slurm account, the profile's QoS and GPU request, walltime, all three Alpine paths, replacement of `USER` placeholders, and the optional email format. It also rechecks that this profile supports the segmentation settings selected on the previous tab. The **Export + return** tab unlocks only after these checks pass.

## 5. Export the package

1. In **4. Export + return**, choose an **Export root**, preferably a short local path with sufficient space.
2. Review the preflight information and click **Export package**.
3. Wait for **Package ready for transfer**. **Cancel export** stops preparation; an incomplete folder is not a submission package.
4. Click **Open folder** and read `README_SUBMIT.md` inside the newly created package. It contains the actual package name and configured cluster paths.

Expect `inputs/`, `configs/`, `profiles/`, `scripts/`, `runtime/`, `bundle.json`, `checksums.json`, `export_report.json`, and `READY.json`. `READY.json` indicates completed local export, not successful cluster execution. The runtime metadata does not include a complete installed Python environment.

Preserve the entire package. Do not rename its input files or edit its checksummed scripts/configuration to change parameters; correct settings in CellQuant and export a new package. Keep the original ND2/TIFF sources as well.

## 6. Transfer the package

Use **Copy transfer instructions** for the expected destination. Transfer the entire new `cellquant_hpc_...` folder into the configured Project root, keeping its name and contents intact. Avoid creating an accidental extra nested folder with the same name.

For large image files, use Globus or your established cluster transfer method. CURC advises against transferring files larger than 1 GB through the Open OnDemand Files application. See [CURC Files guidance](https://curc.readthedocs.io/en/latest/open_ondemand/files_app.html).

**Success check on Alpine:** the expected package directory directly contains `README_SUBMIT.md`, `READY.json`, and `scripts/`. Upload success does not validate file integrity; the preparation step in the next guide does that.

## 7. Choose how to submit

After transfer and environment setup, use either:

- [Submit through the terminal](SUBMIT_THROUGH_TERMINAL.md): includes commands, expected output, monitoring and cancellation. **Copy terminal submit** supplies your package-specific submission command.
- [Submit through Open OnDemand](SUBMIT_THROUGH_OPEN_ONDEMAND.md): submit the dedicated Job Composer script after preparation. **Copy Job Composer steps** supplies the corresponding instructions. Core Desktop is not required.

Choose one path per run; using both submits two jobs. Environment setup/preparation may still require a terminal even when Job Composer performs submission.

## 8. Download and import results

After the job finishes, inspect its logs and `result_manifest.json`; a scheduler job ID or a disappearing queue entry does not prove that every image succeeded.

The configured result location is `<Project root>/results/<package name>`. Download the entire result folder, including `result_manifest.json`, `runs/`, completion records and logs. Preserve the source package for identity checks. Manifest run paths are relative (`runs/<name>.cellquant`) so import can relocate them after a Windows download.

In **4. Export + return**, use **Import HPC results…**. Select the downloaded result folder, then a **new empty local destination** for the imported stores. The importer can replace same-named stores in the destination, so do not select a folder containing your previous reviewed work.

The current import button appears in the successful-export result box. If you reopened CellQuant and cannot see it, use the CLI from a local terminal with the CellQuant environment activated:

```powershell
cellquant hpc import-results "D:\HPC_Return\cellquant_hpc_abc123" "D:\HPC_Imported\trial_01" --source-bundle "D:\CellQuant_HPC\cellquant_hpc_abc123"
```

Replace all example paths. This command runs on your computer, not on Alpine. Import reports complete, failed and unfinished acquisitions and writes a mapping; it does not automatically display image/label layers or certify manual segmentation review. Keep original sources available for the subsequent CellQuant image/label review workflow.

## Alpine environment prerequisite

The package does not install its own cluster software. Before exporting a package, ask the maintainer for a Python 3.11 environment with CellQuant, compatible Cellpose/PyTorch and model weights, plus its exact **Environment location**. The maintainer should verify imports and confirm that the generated batch script activates this environment on a compute node. A standard Conda prefix may work interactively yet fail the batch script's activation checks; do not infer compatibility from `conda activate` alone. GPU availability must be checked inside an allocated GPU job. Once this prerequisite is met, use the same verified location in the Cluster profile tab.

## Current release limitations

These were identified by source inspection; a live Alpine job has not been recorded.

1. **Environment provisioning is incomplete.** `setup_environment.sh` checks whether a directory exists and prints advice. It does not install CellQuant, CUDA-compatible PyTorch or model weights. A normal Conda prefix is not necessarily compatible with the job's activation logic. Obtain a tested environment and activation contract before submitting.
2. **Cluster smoke test is not recorded.** Local export, bundle validation and the generated staging/copy-back/import path can be exercised offline. They do not replace an authorized H200 trial. Do not treat a green local validation result as cluster verification.

Track these separately from the usability of the instructions. Once a small H200 test succeeds, record that evidence on the profile before full-dataset submission.
