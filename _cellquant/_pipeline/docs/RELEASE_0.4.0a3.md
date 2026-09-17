# CellQuant 0.4.0a3

This alpha adds a standalone **Segmentation Review/QC** mode for inspecting and
correcting Cellpose instance masks, with durable draft/approve persistence and
explicit Quantification mask-input policies. It remains intended for lab
testing; retinal coexpression accuracy has not yet been validated against expert
review, and no live Alpine smoke run is recorded.

## Changes

- New top-level mode **Segmentation Review/QC** for single-image, folder-of-masks,
  and segmentation-batch review without markers or thresholds.
- Shared `cellquant.review` package: schema-v2 `review.json`, immutable
  `reviews/r######.tif` revisions, `labels_draft.tif`, compatibility
  `labels_reviewed.tif`, and recoverable publication with conflict detection.
- Drafts never become Quantification inputs. Policies are Prefer approved
  (default), Approved only, and Original Cellpose masks.
- Standalone Cellpose integer-label TIFF import creates short `.cellquant`
  review-import bundles (`run_kind: imported_labels`) without fabricating
  Cellpose engine/model settings.
- Quantification batch UI no longer embeds mask editing; it summarizes review
  state and opens Review/QC. Running jobs pin the selected mask revision.
- Side-by-side and Overlay layouts with synchronized navigation; Side-by-side is
  the first-use default and the last choice is remembered.
- CUDA/HPC installer and profile hardening carried alongside this release
  (driver-compatible torch selection and Alpine profile pins).

## Try the workflow

1. Close any open CellQuant napari window and relaunch with `Open CellQuant.bat`
   so the editable install picks up `0.4.0a3`.
2. Set **Mode → Segmentation Review/QC**. Choose Single image, Folder of masks,
   or Segmentation batch, then open a complete `.cellquant` run or integer-label
   TIFF.
3. Edit the working mask, **Save draft** or **Approve & Next**, and confirm the
   original `labels.tif` is unchanged.
4. In **Mode → Coexpression → Batch**, refresh review status, choose a **Mask
   input** policy, inspect the preflight table, and classify.

## Verification

On 2026-09-16 the suite was run in two sessions on the maintainer Windows
machine (Python 3.11 CellQuant env, `PYTHONPATH=src`,
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`):

| Session | Passed | Failed | Skipped | Wall (s) | Artifact |
| --- | ---: | ---: | ---: | ---: | --- |
| Full suite excluding `test_plugin_slow_run.py` | 445 | 0 | 19 | 89.64 | `artifacts/regression_0_4_a3.xml` |
| `test_plugin_slow_run.py` alone | 17 | 0 | 0 | 39.56 | `artifacts/slow_run_0_4_a3.xml` |

Combined single-process full-suite runs can still fail the ten native Qt
slow-dialog / dock-expansion cases after earlier napari tests have polluted
viewer state; those cases pass when run alone. Synthetic tests check software
behavior; they do not establish biological accuracy or long-run performance on
retinal datasets. A live Alpine submission remains unverified.

Detailed verification is recorded in `STATUS.json` under `current_verification`.
Older records in that file are historical and do not describe this source
revision.

## Limits

- Expert-reviewed retinal segmentation and coexpression accuracy remain
  unvalidated.
- No live Alpine smoke job is recorded for this alpha.
- OneDrive is not a multiuser lock for concurrent review edits.
