# Reliability and guided nuclear-marker calibration

Authorized 2026-09-08: implement steps 1 and 2 from the task discussion.
The later accuracy-optimizing parameter sweep is explicitly deferred; see
../memory/2026-09-08-deferred-parameter-sweep.md. Do not run that sweep.

## Reliability acceptance criteria

- Preserve a loaded recipe's expected channel names. Mismatch blocks scoring and
  saving until the user explicitly reviews/remaps markers and confirms the new
  layout. Never change a saved expectation merely by selecting another image.
- Invalidate result tables, overlays and calibration evidence on label painting,
  relevant input/metadata/transform edits, recipe changes, or reopening inputs.
  Discard late results from an older revision. An already-saved immutable run may
  remain on disk, but must not become a current preview if the UI changed.
- Retain unmappable channel indexes as visibly invalid instead of silently
  converting them to 'not acquired'. Retain custom query meanings when editing.
- Use actual napari/Qt events in regression tests: open, 3/4-channel display,
  preview, paint labels, change recipe, save, reopen, rescore, error and cancel.
  Use tiny known arrays and bounded event-loop waits. Diagnose any prior test
  hang with a minimized command and traceback, not repeated long waits.
- Keep source/package version consistent and historical validation reports
  distinguishable from checks on this revision. Do not change the source version
  to satisfy a stale hard-coded test.

## Guided calibration

Add a marker-specific panel launched from Coexpression. User selects a marker,
sees an intensity histogram from eligible nuclei, and can label representative
nuclei as negative/positive examples by selecting them in the viewer. Numeric
IDs may remain an advanced alternative, but must not be the primary novice path.

Provide two proposals with explicit provenance: Otsu on pooled finite in-nucleus
pixels, or an explicitly chosen percentile of pixels in user-designated negative
control/example nuclei. Suggestions are starting points, never biological truth.
Constant/empty/nonfinite-only distributions must yield actionable explanations.
No arbitrary cutoff is silently accepted. A default positive-pixel fraction may
be shown as a starting value and must be visible and confirmed on acceptance.

Show the raw low/high thresholds, cell positive-pixel fraction and optional
uncertainty margin. Show one current preview with marker calls over the exact
analysis-grid fluorescence and object-level fractions; mark the user's positive
and negative examples and show disagreements. Other unconfigured markers must
not block calibrating this one. Missing markers cannot be calibrated.

The user explicitly accepts the reviewed settings to update the main marker row.
Persist a JSON-serializable calibration record in the recipe so classification
stores carry method/settings, example label IDs, image/label/region evidence
hashes, grid/spacing/channel identity, proposal and accepted bounds/fraction,
and review status. Keep proposal distinct from accepted settings. Editing bounds,
mapping or masks invalidates current evidence; reusing a recipe may retain its
historical calibration source without claiming the new image was reviewed.

This milestone does not search for parameters maximizing coexpression, infer
cell types, or validate cytoplasmic assignment. It does not run a dataset-wide
parameter sweep. Threshold sensitivity can be inspected by explicitly changing
and previewing a threshold, with no automatic accuracy claim or optimization.

## Implementation boundaries

- Reliability owner: plugin/coexpression.py, plugin/controller.py,
  plugin/widget.py and real-Qt reliability tests.
- Calibration core owner: classify/calibration.py, recipe calibration metadata
  extension in classify/__init__.py and calibration core tests.
- Calibration UI owner: plugin/calibration.py and its dedicated real-Qt tests.
  Reliability owner integrates its launch hook into Coexpression.
- Root: design, deferred request, integration review, version/status/docs and
  final artifact/verification coordination. No image parameter sweep.

Calibration API and widget integration are agreed between owners before edits.
No new dependencies are needed: numpy, pandas, matplotlib and Qt are installed.
