# HPC prep: acceptance status (2026-09-29)

Criteria from `docs/HPC_PREP_SPEC.md`, section 10. "Met" means an automated test or recorded check passes in this
release. Stubbed or CPU checks do not certify real Cellpose on an Alpine GPU; those items say so.
Full suite: 265 passed (`xvfb-run pytest tests`), ShellCheck clean on every generated script.

| ID | Status | Evidence |
|---|---|---|
| AC01 | Met | `test_preparation_needs_no_cellpose_and_never_segments`: Cellpose and PyTorch made unimportable, segmentation functions made to fail; a READY package is still prepared. |
| AC02 | Met | `test_tiff_pixels_dtype_and_singletons_round_trip` (uint8/uint16/float32, singleton Z and C), `test_nd2_positions_round_trip_exactly` (3 positions × 4 slices, user calibration override recorded separately), source hash and mtime unchanged. Real ND2 (1024², 7 slices, 3 channels) round-tripped exactly (evidence below). |
| AC03 | Met | Truncated input, changed recipe/script/runtime/profile, READY bound to other checksums or missing, schema version 2, legacy `cellquant_hpc` kind, `../` and symlinked paths, duplicate IDs, changed spacing (task key), damaged pixels with consistent hashes (`--deep`): all fail validation before inference (`tests/test_hpc_prepare.py`), and the worker refuses a damaged scratch copy. |
| AC04 | Met | `test_settings_are_frozen_at_preparation`; the worker never receives edits (tasks' edit logs are empty); package-only GPU/engine choices never change the experiment (`test_prepare_submit_and_import_in_the_window`). |
| AC05 | Met | Missing 3D calibration, unequal XY, differing channel counts, unconfirmed channel names, disabled mode, other engine, other model, GPU off, classical method, unvalidated runtime, different CellQuant build: each blocks with a fix, and the recipe is unchanged. The worker fails an image that ran on the CPU (`E_DEVICE`) and the preflight fails a CPU test segmentation (`E_GPU`). |
| AC06 | Met (locally) | `test_the_worker_imports_without_a_display` (fresh process: no napari/Qt/controller), Python ≥ 3.11 enforced by the runtime contract, exact runtime/model identity required before inference (`E_RUNTIME_*`). On Alpine: pending the smoke test. |
| AC07 | Met | `test_transport_parity_in_every_mode`: all four Z modes, deterministic segmenter, identical labels, equal object and report tables within rtol 1e-6. |
| AC08 | Partly met | Real weights on CPU, original source vs prepared package in the same process and runtime (`docs/hpc_evidence/`): Cellpose-SAM 4.2.1.1 `cpsam_v2` in max projection (whole ND2), one slice, linked slices; Cellpose 3.1.1.3 `nuclei` in one slice, max projection (whole ND2), linked slices, whole volume. Every case: identical pixels, identical labels, measurements within rtol 1e-6 / atol 1e-8. **Still required:** the same check on an allocated Alpine GPU node per enabled engine/mode, and Cellpose-SAM whole volume (too slow on CPU). |
| AC09 | Met | Cancel before the first image, during an image, after one image: every image accounted for, nothing from the interrupted image kept, outcome `cancelled`, import refuses without `--allow-partial` (`tests/test_hpc_worker.py`); SIGTERM to a real worker process ends it cleanly with exit 130. |
| AC10 | Met | Kill between publishing files and the commit record: no false success, run stays owned; `recover-lease` refuses running, requeued, preempted, unknown, missing-accounting jobs and wrong token/job/attempt/package; after recovery, resume skips only hash-verified successes (unchanged commit) and redoes the rest; a corrupted success is reported and redone; two owners cannot hold one run. |
| AC11 | Met | Simulated "no space left on device" on the second image: exit 5, publication `failed`, first image usable, scratch kept, partial import works; logs are in `attempts/<id>/` of the durable run. |
| AC12 | Met | Import keeps QC, reports, combinations, column types and missing classifications exactly; delete, restore, undo, drawn edits, thresholds, measurements, save, reopen and export work with Cellpose unimportable, originals and package deleted, and more images than the in-memory cache holds. |
| AC13 | Met | Partial imports list unfinished images (marked "Needs attention", no result); no image is approved; reimport is idempotent; non-empty and conflicting destinations are left untouched; damaged results import nothing. |
| AC14 | Met | Imported experiments open after moving (relative `inputs/` paths; `load_experiment` uses the folder it is opened from). |
| AC15 | Met | Preparation, validation and import run through the window's worker with progress and Cancel (`tests/test_gui_hpc.py`); scripts are LF and pass `bash -n` and ShellCheck; the run and log folders exist before `sbatch` (checked by the stub); long Windows paths are refused before writing; a full submit → job → publish run works through the generated scripts with Slurm and the GPU stubbed, in folders with spaces. |
| AC16 | Not met | Needs a CURC account: see `docs/HPC_SMOKE_REPORT_TEMPLATE.md`. Every profile stays `experimental` until then. |

## Real-weight parity (CPU), 2026-09-29

Source: one of the lab's 3-channel 20x ND2 z-stacks of embryonic retina (not included in this repository), and TIFF crops of it, segmenting Far Red, measuring all three channels.

| Engine | Mode | Input | Objects | Pixels | Labels | Measurements |
|---|---|---|---|---|---|---|
| Cellpose-SAM 4.2.1.1 | max projection | whole ND2 (3 × 7 × 1024 × 1024) | 1111 | identical | identical | within tolerance |
| Cellpose-SAM 4.2.1.1 | one slice | 384² crop | 235 | identical | identical | within tolerance |
| Cellpose-SAM 4.2.1.1 | linked slices | 160² crop | 184 | identical | identical | within tolerance |
| Cellpose 3.1.1.3 | one slice | 384² crop | 107 | identical | identical | within tolerance |
| Cellpose 3.1.1.3 | max projection | whole ND2 | 616 | identical | identical | within tolerance |
| Cellpose 3.1.1.3 | linked slices | 192² crop | 151 | identical | identical | within tolerance |
| Cellpose 3.1.1.3 | whole volume | 96² crop | 35 | identical | identical | within tolerance |

Script: `docs/hpc_evidence/parity_check.py`; raw results: `docs/hpc_evidence/parity_cellpose*.json`.

## Where this release differs from the spec, and why

- **GPU setting.** The spec did not cover recipes saved with "Use GPU" off (usual on a laptop without a GPU):
  such a package would run Cellpose on the cluster CPU. Preparation blocks it (`E_GPU`); the panel offers
  **Use the GPU on the cluster**, a visible package-only change.
- **Engine and model on computers without that engine.** Step 2 used to overwrite the engine and model with
  whatever was installed locally (or blank them when none was). They are now kept when Cellpose is not installed,
  and the panel offers **Use the cluster's engine and model** as a package-only change.
- **Classical method** is not sent to the cluster (runtime contracts name a Cellpose engine); it runs locally.
- **Earlier runs.** Running one image hid other images' results saved in earlier runs, which would have hidden
  imported cluster results; results are now looked up newest run first.
- **Moved experiments** kept writing into their old folder; fixed so AC14 holds.
- **`run --attempt-id`** and the helper commands `allocate`, `record-job`, `mark-attempt` were added so the run
  and log folders exist before `sbatch` and the job's Slurm ID is recorded for lease recovery.
- **Slurm early warning.** Jobs ask Slurm for SIGUSR1 up to 10 minutes before the time limit: the worker stops
  starting images and keeps finished ones. `scancel` (SIGTERM) also stops inside the current image.
- **Offline jobs.** Job scripts set `CELLPOSE_LOCAL_MODELS_PATH` from the runtime contract and Hugging Face
  offline mode, so Cellpose-SAM cannot download weights inside a job.
- **Feature switch.** HPC prep is shown only with `CELLQUANT_ENABLE_HPC=1` or `~/.cellquant/enable_hpc`.
