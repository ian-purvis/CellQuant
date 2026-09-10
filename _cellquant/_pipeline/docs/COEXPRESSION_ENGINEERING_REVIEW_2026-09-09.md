# Coexpression engineering review

Reviewed 9 September 2026 against the local CellQuant 0.4.0a2 source. This is an engineering handoff, not a biological accuracy assessment. No pipeline source was changed.

The first fixes should bind images, masks, and review saves to the same analysis context, then make recipe editing lossless. These defects can change measurements, display the wrong fluorescence during curation, or save labels to the wrong run. The remaining edits address numerical boundaries, workflow reliability, and interpretability.

## Scope and evidence

Reviewed the classification and calibration core, single-image Coexpression panel, batch panel, persistence interfaces, relevant controller/preprocessing code, tests, README, and existing calibration screenshot. Findings use source inspection and small synthetic reproductions. Some UI methods were exercised with test doubles rather than a live napari session; those limits are stated below. Large-volume latency and biological accuracy were not measured. No parameter optimization was performed.

Source references below are relative to the pipeline root. P1 means address before relying on affected workflows for analysis; P2 means a normal engineering fix; P3 means a small convenience fix. Recommendations are explicitly separate from defects.

## 1. P1 — Bind scoring to the masks' source plane or projection

**Defect.** `src/cellquant/plugin/coexpression.py:381` snapshots selected masks and image, but returns the current controller configuration at line 395. Preview at lines 414–416 prepares measurement pixels from that configuration. Masks' recorded `analysis_volume` provenance is not used to resolve or validate the measurement grid. The classifier checks shape and spacing, which cannot distinguish two different Z planes of identical dimensions. Segmentation records analysis provenance in `src/cellquant/segment/__init__.py:539`.

**Reproduce.** Use a two-plane image with intensity 0 on plane 0 and 100 on plane 1. Give a singleton-Z nucleus mask provenance identifying plane 0, and classify at raw low 50. Preparing with configuration z=0 produces negative; z=1 produces positive without rejecting the mask/source mismatch. Actual preprocessing slicing and classification reproduced this behavior; the isolated harness stubbed an unused SciPy import. Reopened classification bundles have a separate `classification_analysis_grid` bypass and are not the same path.

**Suggested edit.** Introduce a shared analysis-context descriptor containing source identity, series/position, grid mode, Z selection/projection, shape, and spacing. Resolve classification pixels from the selected masks' context. Reject conflicting provenance; require an explicit grid declaration for imported masks without provenance. Show the chosen plane/projection beside the image and labels selectors.

**Acceptance.** Changing segmentation settings after masks exist cannot silently alter their scoring pixels. A same-shaped wrong-plane pair either fails with a repair instruction or consistently uses the recorded plane. Cover both single-plane and maximum-projection masks, including calibration entry from Coexpression.

## 2. P1 — Make batch review loading and saving one bound session

**Defects.** `src/cellquant/plugin/coexpression_batch.py:309` opens a run for review through raw `controller.open_path`, then attaches analysis-grid masks. It does not prepare the selected plane/projection. The delayed callback at line 335 checks only that `image_volume` is non-null; `src/cellquant/plugin/controller.py:602` leaves the old volume available while opening the next one. Finally, `save_curated_labels` at line 367 locates a layer by name and writes to the retained `_review_run`, without verifying that the current layer still belongs to that run.

**Reproduce.**

- Open a run segmented from Z=2 of a three-plane image. Review publishes a raw image of shape `(3,4,4,1)` against masks of shape `(1,4,4)` at Z=0. Projection masks likewise lack the corresponding projection display.
- Start opening run B while image A is loaded and delay B beyond 150 ms. The callback can publish B's masks using A's image spacing and report B ready prematurely.
- Open run A for review, then replace the current labels with unrelated same-shaped labels. Save curated labels can target A because the saved run binding persists and selection is based on layer name.

The first two paths were reproduced using actual UI method bodies with controller/widget doubles; the save binding weakness is also directly visible in the source. This is not a claim that a user's existing run was overwritten.

**Suggested edit.** Load and validate an immutable image-plus-mask analysis bundle in one job. Publish only on that job's success with a matching generation token; invalidate it on cancel, failure, source replacement, or a newer request. Display the actual analysis plane/projection. Bind Save to a specific layer and run identity, and disable it when that association changes. Validate shape, spacing, and analysis identity before writing; retain revision history for curated labels.

**Acceptance.** Slow, failed, cancelled, and out-of-order opens never mix runs or report readiness early. Single-plane and projection overlays match their measurement fluorescence. Replacing the image or labels invalidates Save; same-shaped labels from another run cannot be written into the prior run.

## 3. P1 — Preserve the complete recipe in batch editing

**Defect.** `src/cellquant/plugin/coexpression_batch.py:476` imports only name, calibration group, and basic marker-table fields. `recipe_from_table` at line 445 constructs a new recipe, dropping custom queries and marker calibration evidence, forcing `region_policy="whole_object"`, and rebuilding expected channel names from the layout. Both Apply and Save use this reconstruction.

**Reproduce.** Import a recipe with a conditional or negative-marker query and calibration evidence, then Apply to layout or Save recipe without editing it. The query becomes default positive combinations and the calibration evidence disappears. A recipe with `centroid` becomes `whole_object`. The round trip was reproduced with real `ClassificationRecipe` and UI method bodies running against widget doubles. Region-policy loss has no numerical effect in the current batch path when no ROI is supplied, but it still changes the saved recipe.

**Suggested edit.** Keep a complete canonical recipe as the editing model and patch only the fields the user changes. Display custom query and calibration status summaries. If a field cannot be supported in batch, report that incompatibility explicitly instead of silently replacing it. Require explicit channel remapping when layouts differ.

**Acceptance.** Import → Apply → Save without edits preserves semantic content and the recipe fingerprint for a matching layout. Conditional and negative query clauses, calibration evidence, region policy, and channel expectations survive. Deliberate changes appear in a reviewable recipe diff.

## 4. P2 — Repair query dependencies when single-image markers change

**Defect.** `src/cellquant/plugin/coexpression.py:376` retains loaded queries in hidden `_queries`; line 358 reuses them after marker edits. `src/cellquant/classify/__init__.py:98` generates defaults only when queries are absent. The panel provides no query editor or reset.

**Reproduce.** Save/load a normal one-marker recipe, rename the marker, and Preview or Save recipe. Validation fails with `query positive has duplicate or unknown marker names`. Adding a marker instead leaves the old query set, omitting new default inclusive combinations. Recipe validation reproduced the rename failure.

**Suggested edit.** Record whether queries are automatic or custom. Regenerate automatic queries on marker addition/removal; preserve custom relationships through stable marker IDs or explicit repair. Expose a query summary, editor, and reset-to-default action. Define migration behavior for existing recipes, whose canonicalized default queries look like explicit queries.

**Acceptance.** Add, rename, and remove markers after loading a default recipe; preview and save work and query coverage updates. Custom dependencies remain visible and can be repaired without editing JSON.

## 5. P2 — Make uncertainty-boundary comparisons numerically consistent

**Defect.** `src/cellquant/classify/__init__.py:199` computes cutoff ± margin in binary floating point. At line 202, cutoff `0.2` plus margin `0.1` becomes `0.30000000000000004`. A nucleus with exactly 3/10 positive voxels has fraction `0.3` and is called uncertain instead of positive at the intended inclusive upper boundary.

**Evidence.** Reproduced with the actual classification engine in the CellQuant environment. Existing `tests/test_classification.py:73` covers uncertainty endpoints but not this decimal combination.

**Suggested edit.** Define decimal boundary semantics and implement a narrowly justified numerical comparison or exact count-based rule. Avoid a broad default tolerance that changes meaningfully subthreshold values. Apply the same policy to both boundaries and calibration previews.

**Acceptance.** Test 3/10 against cutoff 0.2 and margin 0.1, exact lower endpoints, just-below/above cases, zero margin, and large voxel counts. Document that the lower boundary belongs to uncertainty while the upper boundary belongs to positive calls.

## 6. P2 — Remove duplicate synchronous snapshots and bound review rendering

**Defect.** `src/cellquant/plugin/coexpression.py:565` calls `snapshot()` solely to validate before opening the output-folder dialog. Choosing a folder calls Preview at line 567, which snapshots again. `snapshot_inputs` at lines 389–390 copies full multichannel image and label arrays on the Qt thread before a worker starts. Cancelling the folder dialog still incurs the first copy.

**Suggested edit.** Split cheap validation from materialization. Choose a destination first, then create one immutable snapshot. Keep protection against mutable viewer data while introducing a responsive preparation stage with memory estimation and cancellation. Do not simply move reads of mutable arrays to an unsynchronized worker.

Calibration has a related scalability risk: `src/cellquant/plugin/calibration.py:246` builds every table item on the UI thread and resizes columns to contents; `src/cellquant/classify/calibration.py:67` copies the complete image again. Consider a model-backed table, bounded column sizing, and sharing immutable snapshots. These are source-supported performance risks, not measured latency claims.

**Acceptance.** Cancelling folder selection causes zero full-volume copies; a successful save makes one consistent snapshot. On a representative large stack and high-cell-count table, measure responsiveness, peak memory, and cancellation latency against agreed budgets. Editing the source during preparation cannot produce mixed inputs.

## 7. P3 — Fix Open results folder

**Defect.** `src/cellquant/plugin/coexpression_batch.py:607` calls `QtCore.QDesktopServices`, but this class belongs to QtGui. The results-folder action therefore fails rather than opening the completed output directory.

**Suggested edit.** Import QtGui from qtpy, call `QtGui.QDesktopServices.openUrl`, and show a usable path if the OS refuses the request.

**Acceptance.** After a completed batch, the button passes the completed output directory as a local-file URL to the QtGui service; a failed launch produces an actionable message. This was source-verified, not exercised through the live desktop.

## 8. P2 — Correct batch percentage labels and expose denominator coverage

**Export defect.** `src/cellquant/classify/batch.py:469` writes every query percentage into a `*_pct_of_cells` column, including conditional queries. It then duplicates conditional rates into a second denominator-specific column. With 10 eligible cells, two B+ cells, and both of those A+, “A among B” is 100% of evaluable B+ cells, not 100% of cells. Emit only a denominator-appropriate rate label, and include its numerator and denominator. Add this exact fixture to the batch-export tests. This is source-confirmed; the export path was not exercised end to end.

**Additional reporting recommendation.** Show conditional denominator coverage explicitly:

`src/cellquant/classify/__init__.py:213` uses complete-case denominators: missing/uncertain states in any involved marker are removed before applying the denominator-positive condition. This is a defensible convention, not an established calculation bug. However, reporting only the resulting percentage can be misleading when measurements are incomplete.

**Observed example.** Four A+ cells, one B+ measurement, and three missing B measurements produce “B among A”: numerator 1, denominator 1, percentage 100%, missing 3. The output does not separately expose the complete A+ population or coverage within that population; existing missing counts cover all eligible cells involved in the query.

**Suggested edit.** Report “1 / 1 evaluable A+ cells; 1 / 4 A+ cells evaluable (25% coverage), 3 missing B,” with uncertain counts separately. Preserve the current percentage while naming its denominator. Define treatment of unknown denominator-marker states explicitly. Carry these fields into exported tables.

**Acceptance.** The four-cell example shows 100% coexpression alongside 25% denominator coverage. Add cases where missing measurements occur outside the A+ population, where A itself is missing, and where the evaluable denominator is zero.

## 9. Recommendation — Make calibration a focused cell-review workflow

The current panel provides a histogram, cell table, example disagreements, and colored overlays, which are useful foundations. `src/cellquant/plugin/calibration.py:268` selects the label and changes dimension points, but has no camera-centering step. The table is a full dump without a review queue; settings, review, and acceptance share a scrolling panel. The existing `artifacts/calibration_docked_0_4_a2.png` illustrates the control density, but is not a new live usability test.

**Suggested edits.**

- Add filters for uncertain cells, example disagreements, and cells near each decision boundary, with Previous/Next navigation.
- Center the camera on the selected nucleus with adjustable surrounding context; show the raw channel and outline together. Keep the nucleus ID visible so example marking is unambiguous.
- Put positive/negative/uncertain/missing counts and current settings in a persistent review summary, and keep Preview/Accept/status reachable while scrolling.
- Mark accepted settings as historical versus reviewed for the current image, including the calibration source and number of positive/negative examples. Preserve manual threshold entry without implying that an automatic proposal establishes biological accuracy.

**Acceptance.** A reviewer can move through all uncertain/disagreeing cells without searching the volume manually, see which nucleus is being marked, and distinguish current evidence from historical evidence. Validate at a typical dock width with both 2D and 3D data, a large label count, and keyboard navigation.

## 10. P2 — Finalize partial batch output consistently on cancellation

**Defect.** `src/cellquant/classify/batch.py:503` catches cancellation as an ordinary per-run exception. The next loop's cancellation checkpoint at line 386 can exit before the aggregate CSVs and summary are written at lines 530 onward. If cancellation happens during the last run, there is no next loop checkpoint, producing different finalization behavior. Completed classification evidence packs can remain on disk, but `src/cellquant/plugin/coexpression_batch.py:613` only records the output location after a successful future and cancellation check.

**Suggested edit.** Handle `PipelineCancelled` separately and finalize a partial manifest in a controlled cancellation path. Distinguish completed, failed, cancelled, and unstarted runs. Retain the destination as soon as the run starts and offer Open results for surviving outputs. Do not label cancellation as an ordinary processing failure.

**Acceptance.** Cancel during an early run and during the final run. Both produce a consistent cancelled status, readable partial manifest, discoverable completed packs, and accurate completed/unstarted counts. No incomplete pack is presented as complete. This finding is based on source control-flow inspection, not a live cancellation test.

## Suggested implementation order

1. Shared analysis-context and review-session binding (items 1–2).
2. Lossless recipe model and dependency-aware editing (items 3–4).
3. Numerical boundary fix, snapshot preparation, folder action, export labels, and cancellation finalization (items 5–8 and 10).
4. Denominator coverage and focused calibration review (items 8–9).

Keep targeted regression fixtures for source-plane mismatch, delayed opens, wrong-run saves, recipe round trips, and decimal boundaries. Retain the current immutable classification-output design. Biological validation should use separately reviewed reference cells and should not equate increased positivity with improved accuracy.

## Reproduction artifacts

`artifacts/coexpression_audit_20260909/batch_repro.py` and `batch_repro_output.txt` retain four focused reproductions: lossy recipe editing, analysis-grid mismatch, delayed-open source mismatch, and wrong-run save acceptance. They execute actual AST-extracted UI method bodies with test doubles and real recipe/volume contracts. They do not replace real Qt integration tests. The save reproduction intercepts the write; it does not overwrite a real reviewed-label file.

A focused pytest invocation for test_classification.py and test_calibration.py was attempted in the installed CellQuant environment. It produced no test output for several minutes and was interrupted; no suite-pass claim is made. The synthetic reproductions above are the completed execution evidence.
