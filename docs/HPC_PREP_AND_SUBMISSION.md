# Running CellQuant on a cluster GPU (HPC prep)

HPC prep lets you analyze an experiment on a cluster GPU (Alpine) without a GPU on your own computer.
CellQuant makes a **package** (your images plus frozen settings), you transfer it and run two commands on
the cluster, and CellQuant imports the results as a **new experiment** that you review, edit and export as usual.

- The cluster runs exactly the same analysis code as your computer (`process_image`), one image at a time.
- Your original images and your original experiment are only read. The imported experiment has its own copies.
- CellQuant never logs into the cluster, stores passwords or submits jobs. You run the commands it gives you.

> **Status:** everything up to and including a stubbed Slurm run is built and tested (see
> `docs/HPC_ACCEPTANCE.md`). **No real Alpine job has been run yet**, so every profile is *experimental*
> until the maintainer's smoke test (section 9) is recorded.

## 1. Open it

Open CellQuant, open your experiment, and click
**HPC prep…** on the Start tab. A new **HPC prep** tab appears; your local tabs keep their settings.

## 2. What you need

- An experiment with images added (step 1) and settings made and checked on a few images (steps 2-4), using
  **Cellpose** (the classical method is fast enough to run locally and is not sent to the cluster).
- A **cluster profile** (`.json`) and the **runtime contract** it points to, from the maintainer (section 8).
  Start from **Start from the example** in page 3 if you are the maintainer.
- A short local folder for packages outside your image folders, e.g. `D:\CellQuant_HPC`, with room for a
  copy of the selected images (the page shows the size).
- Images stored on this computer (OneDrive "online-only" files are reported and must be downloaded first).

## 3. The six pages

| Page | What you do | What CellQuant checks |
|---|---|---|
| 1. Images | Tick the images (acquisitions) to send. | Nothing yet. |
| 2. Settings | Read the frozen settings. If asked, click **Use the GPU on the cluster** or **Use the cluster's engine and model**. These change the *package only*; your experiment's settings stay as they are, and the change is listed in blue. Tick the channel-layout box only if images name their channels differently but have the same channels in the same order. | Engine, model, Z mode and GPU against the runtime. |
| 3. Cluster | Load the profile; fill in your Slurm account, project and scratch folders; adjust GPU, CPUs, memory, time; **Check profile**; **Save profile as…**. | Placeholders, runtime contract hash and validation, field formats. |
| 4. Prepare | Choose the package folder; **Check before preparing**; **Prepare package**. Cancel is at the bottom of the window. | Every image is readable, channels match, calibration is valid (3D needs X, Y and Z sizes), the output is outside the image folders and paths are short enough for Windows. |
| 5. Submit | Copy the commands (only shown for a READY package). | Every file against its checksum, and the READY binding. |
| 6. Import | Choose the package, the downloaded run folder and a new empty folder; **Check results**; **Import results**; **Open the imported experiment**. | Every returned file's hash, label shapes, object tables, and that the results belong to this package. |

**Manual edits, deleted objects and approvals are not sent.** The cluster segments every image again.
Sample names, image selection and your metadata columns are kept.

### What preparation does

For each image it reads every channel and every Z slice (an ND2 file is read whole, then the position is
chosen), writes an uncompressed OME-TIFF copy with the same values and data type, reads the copy back and
compares every pixel, and checks that the source file did not change meanwhile. It does not segment, scale,
crop or project anything. A cancelled or failed preparation is left as `<name>.incomplete`, which is never
used; preparing again makes a new package. The absolute paths of your source files are written to
`<name>.local.json` **beside** the package, never inside it.

## 4. Transfer and submit

Page 5 (and `README_SUBMIT.md` in the package) gives the exact commands. In short:

```bash
# on your computer: copy the whole folder (Globus is best for large packages)
rsync -av --partial "D:\CellQuant_HPC\cq_hpc_1a2b3c4d" you@login.rc.colorado.edu:/projects/you/cellquant/
# on a cluster login node
cd /projects/you/cellquant/cq_hpc_1a2b3c4d
bash scripts/preflight.sh            # package, software, model files; no GPU work
bash scripts/preflight.sh --on-gpu   # optional: a small segmentation on a GPU node (15 min limit)
bash scripts/submit.sh               # checks again, creates the run folder, queues one job
```

`submit.sh` prints the job ID, the log file and the results folder
(`<project>/results/<package>/<run_id>/`). The job:

1. checks the GPU node (`preflight.json`): PyTorch must see a GPU and a small test segmentation must run on
   it, in the package's Z mode. **It never falls back to the CPU and never downloads models.**
2. copies the package to scratch and checks it again,
3. analyzes the images one at a time, and publishes each finished image (verified by hash) to the results
   folder before starting the next,
4. keeps the scratch copy and prints the command to remove it.

`scancel` or reaching the time limit stops after (or, for scancel, during) the current image. Finished
images are kept; unfinished ones stay `pending`. Continue with `bash scripts/submit.sh --resume RUN_ID`:
finished images are checked and skipped, the rest run again.

If a job was killed so hard that it could not release the run, `submit.sh --resume` says so and prints a
`recover-lease` command. Run it only after `sacct -j JOB_ID` shows the job ended. CellQuant itself refuses
while Slurm reports the job pending, running or requeued, or cannot report on it.

## 5. Download and import

Download the whole run folder (`results/<package>/<run_id>`, containing `results.json`, `tasks/`,
`attempts/`). In page 6 choose the package on your computer, the downloaded run folder and a new, empty
folder. Or from a terminal with the CellQuant environment active:

```powershell
python -m cellquant.hpc import --bundle "D:\CellQuant_HPC\cq_hpc_1a2b3c4d" --results "D:\Downloads\run_20260929T..." --destination "D:\Experiments\E14 cluster run"
```

If some images did not finish, CellQuant lists them and imports only with **Import completed results only**
(`--allow-partial`); the others are listed "Needs attention" without results. Importing the same results
into the same folder again just opens the earlier import; any other non-empty folder is refused.

The imported experiment has its own `inputs/` copy of every image, so it can be moved or copied to another
folder or computer and still opens. You can review, delete and restore objects, draw, change marker
thresholds and measurements, and export, **without Cellpose installed and without the original ND2 files**.
Editing never segments again: if you change the segmentation settings, pixel sizes or image, CellQuant says
the cluster's objects no longer apply; click **Run** to segment again on purpose.

## 6. Command line

```text
python -m cellquant.hpc prepare  --experiment EXP --profile PROFILE.json --output FOLDER [--image-ids ID1,ID2] [--confirm-channel-layout]
python -m cellquant.hpc validate --bundle PACKAGE [--deep]
python -m cellquant.hpc preflight --bundle PACKAGE [--require-gpu] [--output FILE|-]
python -m cellquant.hpc run      --bundle PACKAGE --scratch-results DIR --publish-to RUN_DIR [--attempt-id ID] [--resume]
python -m cellquant.hpc recover-lease --bundle PACKAGE --results RUN_DIR --run-id RUN --attempt-id ATT --job-id JOB --expected-lease-token TOKEN
python -m cellquant.hpc import   --bundle PACKAGE --results RUN_DIR --destination NEW_FOLDER [--allow-partial]
python -m cellquant.hpc runtime-inspect --engine cellpose4 --model cpsam_v2 --runtime-id ID --lock LOCKFILE --output runtime.json [--modes ...] [--models-dir DIR]
python -m cellquant.hpc schemas  --output DIR
```

`allocate`, `record-job` and `mark-attempt` are used by the generated scripts. Exit codes: `0` done
(including an accepted partial import), `2` invalid arguments, package or profile, `3` runtime or preflight
mismatch, `4` the run ended with failed or unfinished images, `5` publication, import or integrity failure,
`130` cancelled. Error reports list a code (`E_CHECKSUM`, `E_CALIBRATION`, ...), the image and the fix.

## 7. Files

**Package** (`cq_hpc_<id>/`): `bundle.json` (every image: source identity and hash, pixel digest, shape,
channels, file and effective pixel sizes, Z choice, task key), `recipe.yaml`, `runtime.json`,
`cluster.json`, `inputs/a000001.ome.tif`…, `scripts/`, `README_SUBMIT.md`, `checksums.json` (size and
SHA-256 of every file above), `validation.json`, and `READY` (the bundle ID and the SHA-256 of
`checksums.json`; written last). JSON schemas for every record are in `cellquant/hpc/schemas/`.

**Run folder** (`results/<package>/<run_id>/`): `run.json`, `results.json` (every image's state:
pending, running, succeeded, failed, cancelled or interrupted; compute and publication outcomes; every
attempt), `tasks/<image>/<attempt>/` (the result files and `COMMIT.json`, written last with every file's
hash, or `FAILED.json`), `attempts/<attempt>/` (Slurm log, `preflight.json`, `runtime_report.json`,
`worker.log`), and `lease_history.jsonl`.

**Imported experiment:** a normal experiment plus `inputs/`, `runs/<run_id>/hpc/` (the index, the bundle
record and every commit record) and `hpc_import.json` (what was imported from where).

## 8. For the maintainer: environments and runtime contracts

A package runs only in the exact environment its runtime contract describes: Python version, CellQuant
build (a digest of every release file under `cellquant/`, the same on Windows and Linux), every installed
package version, the Cellpose engine and version, the model and the SHA-256 of its weight files.

1. Build one environment per engine with `packaging/hpc/build_environment.sh` (pinned requirements in
   `packaging/hpc/requirements-cellpose4.txt` and `-cellpose3.txt`). It installs PyTorch from the CUDA
   12.8 index (covers Hopper H200 and Blackwell), installs CellQuant from the release folder, downloads the
   model weights once into a model folder, writes the dependency lock (`pip freeze --all`) and exports a
   runtime contract with **no Z modes enabled**. Keep the lock; the contract records its SHA-256.
2. Check each Z mode you want to offer: run `bash scripts/preflight.sh --on-gpu` for a package in that mode,
   and the parity check (`docs/HPC_ACCEPTANCE.md`, AC08): the same images through the original files and
   through a package, on the same GPU node, must give identical labels and measurements within
   `rtol=1e-6, atol=1e-8`. Keep a mode disabled if it fails; do not loosen tolerances.
3. Edit the runtime contract: list the passing modes in `supported_modes`, note the evidence in
   `validation_notes`, and set `runtime_validation_date`. Give users the contract and a profile
   (`cellquant/hpc/profiles/alpine_h200_example.json` is a starting point) with `runtime_sha256` set to
   the contract's SHA-256.
4. Check the profile's partition, QoS, GPU request and limits against current CURC documentation and set
   `profile_verified_date`. The example's values come from September 2026 notes and are **unverified**.
5. After the smoke test (section 9) set `live_smoke_date` and `validation_status: "production"`.

Every CellQuant release changes the build digest, so a new release needs a new environment (or a
reinstall of CellQuant into a copy) and a new runtime contract. Packages always name the contract they
were made for; a mismatch stops the job before any analysis (`runtime_report.json`).

## 9. Smoke test (required before production use)

With a real account, record in `docs/HPC_SMOKE_REPORT.md` (template: `docs/HPC_SMOKE_REPORT_TEMPLATE.md`):
the job ID and date, the profile, runtime and model hashes, the GPU name (from `preflight.json`), two
representative images through prepare → transfer → submit → download → import, the output validation, a
successful import and review on Windows, and one forced failure with resume (for example `scancel` after the
first image, then `submit.sh --resume`).

## 10. Limits of this release

One engine, one recipe and one GPU per package, images run one after another. No job arrays, several GPUs,
automatic transfer or submission, Open OnDemand, remote monitoring, parameter sweeps, or tissue regions.
ND2 files are read whole during preparation (memory estimate on page 4). Images with a different channel
layout need their own package. Results can be imported only into a new experiment, not added to an existing
one. Scratch copies are never deleted automatically.
