# CellQuant 0.4.0a2

This alpha adds guided nuclear-marker calibration and fixes state handling in
coexpression review and long segmentation runs. It is intended for lab testing;
retinal coexpression accuracy has not yet been validated against expert review.

## Changes

- Calibrate one marker before completing the other marker rows. Inspect its raw
  intensity histogram, mark negative and positive example nuclei, request an
  Otsu or negative-example percentile proposal, and preview the resulting calls.
  Settings enter the marker recipe only after explicit acceptance.
- Recipes retain the source fingerprints, selected examples, proposal, and
  accepted settings. Calibration evidence from another image remains historical.
- Loading a recipe preserves its expected channel layout. A changed layout or
  unavailable channel requires deliberate review and remapping.
- Editing labels, metadata, transforms, or marker settings clears stale results.
  Late worker results and obsolete calibration panels cannot publish a new
  accepted review over changed inputs.
- Closing or removing a calibration dock cancels its pending operation and
  invalidates its review. Call overlays use bounded chunks and sorted label IDs
  instead of rescanning the entire image for every nucleus, including when label
  IDs are large and sparse.
- Cancel requests a stop at the next supported checkpoint. Kill terminates the
  Cellpose child process. Startup failures and interrupted runs release their
  callbacks and process resources. Default cancellable runs load the model in
  the child rather than loading another copy in the napari process first.
- Slow-run dialog responses apply only to the job that opened the dialog. Timing
  state resets for each new job, and whole-job timing is not used to estimate
  nested plane progress. The dock retains its vertical expansion policy.
- The v3 adapter uses supported evaluation arguments and preserves configured
  evaluation settings. Tiling is intrinsic to the supported engines; disabling
  it is rejected instead of silently claiming that it was disabled.

## Try the workflow

1. Close the existing CellQuant napari window and relaunch with
   `Open CellQuant.bat` to load the updated source.
2. Open an image and select the matching reviewed nuclear labels in Coexpression.
3. Name a marker, select its channel and row, then choose
   **Calibrate selected marker**.
4. Load the marker, review examples and a threshold preview, and accept the
   settings when appropriate. Complete the remaining marker rows.
5. Preview coexpression, save a classification, and reopen it to check that the
   stored inputs and settings reproduce the expected calls.

The calibration tools measure nuclear signal. Cytoplasmic signal assignment
remains outside this workflow.

## Verification

On 2026-09-09 the full suite passed **286 tests** in 132.21 seconds. An additional
native Windows layout test passed after checking the floating and docked panel
and scrolling to the Accept button. Independent review confirmed the calibration,
subprocess, v3 argument, overlay, and native Qt lifecycle fixes.

The wheel built successfully. Its 48 Python/YAML files match the current source
after normalizing line endings. The active editable installation loads the
updated source when napari restarts. A separate wheel installation into a test
folder also passed CLI import, packaged configuration, and exact synthetic
classification checks without changing the user's environments.

Detailed verification is recorded in `STATUS.json`, under
`current_verification`. Older records in that file are historical and do not
describe this source revision. Synthetic tests check software behavior; they do
not establish biological accuracy or long-run performance on retinal datasets.

Native Qt tests used `QT_QPA_PLATFORM=windows`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`,
`PYTHONPATH=src`, a short writable pytest `--basetemp`, and writable
`NUMBA_CACHE_DIR`, `WIN_PD_OVERRIDE_LOCAL_APPDATA`, and
`WIN_PD_OVERRIDE_APPDATA` directories. Windows offscreen Qt cannot provide the
OpenGL context needed by the real napari viewer in this environment.

Evidence: `../artifacts/regression_0_4_a2.xml`,
`../artifacts/calibration_layout_0_4_a2.xml`,
`../artifacts/calibration_histogram_0_4_a2.png`, and
`../artifacts/calibration_docked_0_4_a2.png`.

## Deferred parameter search

No parameter sweep has been started or scheduled. It remains deferred until Ian
has tested the pipeline and explicitly confirmed that it works correctly. The
subsequent search will need an agreed reference annotation and accuracy metric;
larger positive counts alone do not demonstrate better accuracy. See
`../memory/2026-09-08-deferred-parameter-sweep.md`.
