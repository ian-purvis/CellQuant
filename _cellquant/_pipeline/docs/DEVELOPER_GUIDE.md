# CellQuant developer guide

This is the orientation for the **current source tree**, not a record of unverified scientific accuracy. The package version in [`pyproject.toml`](../pyproject.toml) is **0.4.0a2** and requires **Python 3.11**. The [0.4.0a2 release note](RELEASE_0.4.0a2.md) describes the latest recorded changes; [`STATUS.json`](STATUS.json) `current_verification` records the 2026-09-09 software checks. Older top-level `software`, `modules`, and gauntlet fields in that JSON are historical. No Git metadata is present in this project copy, so neither commit chronology nor an exact current source revision can be inferred here.

The latest release record highlights guided nuclear-marker calibration, explicit acceptance of reviewed thresholds, source-bound recipe and result invalidation, cancellable or killable Cellpose workers, and corrected v3 evaluation arguments. Those are software behavior changes; retinal classification accuracy, real-model parity and large-image UI performance remain unmeasured. See the release note for the exact scope and verification evidence.

## Source map

| Area | Current location and responsibility |
| --- | --- |
| Packaging and entry points | `pyproject.toml`, `src/cellquant/cli.py`, `src/cellquant/napari.yaml` |
| Front ends | `src/cellquant/plugin/` builds the napari dock, review/calibration and HPC panels; `cli.py` parses headless commands |
| Shared run core | `src/cellquant/contracts.py`, `config.py`, `orchestrator.py`, `analysis.py` define validated inputs, events and pipeline flow |
| Image analysis | `io/`, `preprocess/`, `segment/`, `postprocess/`, `measure/`, `viz/` |
| Batch and artifacts | `survey/`, `batch/`, `persist/` provide layout assignment, per-file runs, completion/resume and cloud staging |
| Nuclear coexpression | `classify/` scores pixels in reviewed labels, calibrates markers, stores/reopens classifications; `plugin/coexpression.py` and `plugin/calibration.py` are its UI |
| HPC | `hpc/` surveys acquisitions, exports/validates bundles, runs cluster jobs and imports results; `plugin/hpc_panel.py` is the local UI |
| Verification | `harness/`, `verify/`, `tests/`; `ARCHITECTURE.md` defines scientific parity expectations |

Edit **`src/cellquant/`**. `build/lib/`, `dist/`, `artifacts/wheel/`, and `src/cellquant.egg-info/` are generated or packaged snapshots and may be older than source. Do not use their timestamps as a change log. The Windows scripts install two sibling Conda environments: v4 Cellpose-SAM and v3 classic Cellpose. `cellquant_env.json` stores this machine's launcher paths. Do not bake those paths into portable code.

## Set up and exercise a change

Use a dedicated Python 3.11 environment. The Windows double-click install path is documented in [Start here](START_HERE.md); for development, install from this folder:

```powershell
python -m pip install -e ".[gui,cellpose-v4,test]"
python -m pytest
cellquant --help
```

For a separate classic v3 environment, install `.[gui,cellpose-v3,test]` instead. Cellpose v3 and v4 should not be treated as the same engine; `segment.engine` must match the installed major version. Run targeted tests in `tests/` while editing, then the full suite and a small real-image or native napari workflow relevant to the change. Some native Qt tests require the environment described in [the release note](RELEASE_0.4.0a2.md). Tests with synthetic workers do not prove actual Cellpose inference or retinal biological accuracy.

There are three distinct forms of verification: (1) software tests and native UI interaction, (2) model inference and parity against reviewed reference labels, and (3) biological review of the intended tissue and marker calls. The recorded 2026-09-09 suite passed 286 tests and checked an installed wheel, but the latter two gates remain open. A live Alpine smoke job is also unrecorded; see the [HPC guide](HPC_PREP_AND_SUBMISSION.md).

## Run the CLI

The installed `cellquant` executable comes from `cellquant.cli:main`. Use `cellquant <command> --help` for the exact arguments:

```text
survey        inspect channel layouts without segmentation
survey-run    run surveyed layouts with assignments
batch         segment and measure a folder
harness       run one full verification case
parity        compare reference and candidate labels
showcase      run one stage on a bounded crop
classify      score an image plus reviewed labels and recipe
reclassify    verify and rescore an existing classification
classify-batch
hpc prepare | validate | import-results
```

For example:

```powershell
cellquant survey INPUT_DIR SURVEY_OUT --recursive --file-type nd2
cellquant survey-run SURVEY_OUT/survey.json BATCH_OUT --config sample_config.yaml --assignments SURVEY_OUT/assignments.csv
cellquant batch INPUT_DIR OUTPUT_DIR --config sample_config.yaml --recursive --file-type tiff
cellquant classify IMAGE.tif LABELS.tif CLASSIFY_OUT --config sample_config.yaml --recipe recipe.json
```

The UI auto-materializes a configuration for Single image and Batch folder. Headless commands require an explicit configuration. [`sample_config.yaml`](../sample_config.yaml) is the full starting schema; [`reference_config.yaml`](../reference_config.yaml) is the intentional Fiji/notebook compatibility profile, not a generic recommendation. Validation rejects missing or incompatible settings and can pin model weights by SHA-256. Review voxel spacing, channel selection, engine, mode, anisotropy, device and model identity for each acquisition. The [architecture contract](../ARCHITECTURE.md) documents every serialized parameter and stage boundary.

## Contracts and outputs

The core image shape is **`(Z, Y, X, C)`** and labels are **`(Z, Y, X)`** integer IDs with background `0`. Spacing is positive `(z_um, y_um, x_um)` metadata and is not inferred from a filename or shape. Source intensity dtype is preserved at I/O; preprocessing and display transformations are explicit. Single-plane and max-projection segmentation yield singleton-Z labels; their measurements use `area_um2`. Volumetric and stitched masks use `volume_um3`. One channel drives segmentation per run; segmenting several channels requires separate runs and output directories.

A completed segmentation store contains `labels.tif`, `objects.csv`, `intensities.csv`, `config.json`, `provenance.json`, `events.jsonl`, QC figures, and a final `status.json`. The completion marker is written last and checked before resume. Batch runs isolate failure per input and mirror relative input parents under `*.cellquant` directories. Cloud output is first staged locally and then published to the chosen folder, so a sync copy is not the active analysis directory. Preserve full stores when investigating a result.

Classification is independent of Cellpose and uses raw fluorescence pixels inside **reviewed complete nuclei**. Its saved folder contains `metadata.json`, `calls.csv`, `queries.csv`, `patterns.csv`, `exclusions.csv` and additional evidence files. Query totals include evaluable denominators, missing and uncertain counts. Recipes bind expected channel layouts and retain calibration provenance; reusing a recipe on a new image does not turn old examples into a new review. See [the release note](RELEASE_0.4.0a2.md) for the current reliability/calibration behavior. Cytoplasmic assignment is outside this implementation.

## Trace a behavior or reported change

1. Start with [the release note](RELEASE_0.4.0a2.md) for the latest recorded change set and [`STATUS.json`](STATUS.json) `current_verification` for its evidence and limits. Do not read older JSON sections as results for 0.4.0a2.
2. Find the user's entry point in `plugin/widget.py`, `plugin/coexpression.py`, `plugin/hpc_panel.py`, or `cli.py`; follow its call into the shared core. Consult [`ARCHITECTURE.md`](../ARCHITECTURE.md) for intended contracts and then check the actual `src/` implementation.
3. Search `tests/` for the behavior and reproduce it with a small input. Examine the entire saved store, especially `config.json`, `provenance.json`, `events.jsonl`, and `status.json`, before attributing a difference to Cellpose or a threshold.
4. Use the retained design rationale, proposed quantification UX and settled HPC contract in [the documentation index](README.md) for context. A proposal's desired UI does not prove that UI exists. Record new changes and verification explicitly rather than inferring a timeline from old build artifacts.

## Maintain the documentation

Update documentation in the same change as the behavior it describes. This project copy has no Git metadata, so do not infer a change history from file dates or generated artifacts.

1. Check the current behavior in `src/cellquant/` and exercise the actual UI or CLI entry point. If a user-facing step changes, update [Start here](START_HERE.md) or the relevant task guide with the exact visible labels, commands, inputs, outputs and recovery steps. Write for someone with no coding experience.
2. Update [the architecture contract](../ARCHITECTURE.md), configuration examples or output descriptions only when their contracts change. Keep proposed behavior clearly labeled as proposed; recheck retained design and proposal documents against current source before presenting an old issue as current.
3. For each release, add a dated, versioned release note stating user-visible changes, checks actually run and remaining limits. Update version and release-note pointers in this guide, [the docs index](README.md) and the [project README](../README.md). Update [`STATUS.json`](STATUS.json) `current_verification` only with evidence actually produced for that release, including the check date and unverified limits; preserve historical records.
4. Check local Markdown links, copy UI labels and CLI commands against the running product or `--help`, and try the documented examples. Run relevant tests and a manual workflow where the change needs one. State explicitly in the release note what was not run; never turn an unverified workflow into a claimed pass.

Keep portable instructions free of machine-specific paths and credentials. Use placeholders that a first-time user can identify and replace.
