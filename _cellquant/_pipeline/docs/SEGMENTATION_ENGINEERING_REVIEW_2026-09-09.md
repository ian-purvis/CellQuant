# CellQuant segmentation: single-image, batch, and engine/mode review

Reviewed 9 September 2026 against the local pipeline source, whose package version is 0.4.0a2. This document is an engineer handoff with 12 prioritized edits. It complements the separate Coexpression review; it does not assume those earlier findings are still unfixed. No production code was changed by this audit.

Start with batch output identity, explicit inclusion, complete batch reports, and consistent save snapshots. These can overwrite results, process excluded inputs, conceal failures, or make a saved mask disagree with its measurements. Then address stitched object identity and the units used for 2D results.

## Scope, coverage, and limits

Reviewed the shared segmentation controls, single-image controller, survey and batch orchestration, configuration validation, preprocessing, Cellpose adapters, measurement/postprocessing, persistence boundaries, and relevant local Cellpose source. Source references are relative to `Napari/_cellquant/_pipeline` unless stated otherwise. A source hash manifest and executable synthetic probes are retained under `artifacts/segmentation_audit_20260909/`.

All four supported segmentation modes were exercised through the actual preparation/adapter path for both engine settings using a recording model. Shared workflow defects were separately exercised with actual method/function bodies and lightweight doubles. These are software contract checks, not real Cellpose inference. No model weights were loaded or downloaded, no retinal accuracy comparison or parameter sweep was performed, and no live Qt interaction or GPU/OOM test was run. The real `stitch3D` algorithm was not evaluated by the matrix probe. A focused pytest invocation failed to start because the command runner timed out; no suite-pass claim is made.

| Mode | v3 recording-model check | v4 recording-model check | Measurement grid / important interpretation |
|---|---|---|---|
| Single Z plane (`single_plane_2d`) | One `(6,7)` eval; output `(1,6,7)` | Same | Selected plane only; 2D objects, not counts throughout the stack |
| Maximum Z projection (`max_projection_2d`) | One `(6,7)` eval; output `(1,6,7)` | Same | Per-channel maximum projection; overlapping objects in Z may not remain separately identifiable |
| 2D planes + Z stitch (`stitch_2d`) | Three `(6,7)` evals; output `(3,6,7)` | Same | Plane-local masks linked into stack object IDs; boundary case in item 5 |
| True 3D (`volume_3d`) | One `(3,6,7)` eval; output `(3,6,7)` | Same | Volumetric inference; `do_3D=True`, `z_axis=0` |

The matrix also confirmed v3 receives `channels=[0,0]`, v4 receives `channels=None`, and only true 3D receives anisotropy. With spacing `(2,0.5,0.5)` and `anisotropy="manifest"`, the passed anisotropy was 4. Both single-plane modes passed `z_axis=None`. These findings establish routing, not compatibility with every installed model or scientific equivalence between modes. In the UI, available engine choices depend on the active environment; the matrix does not imply both engines execute in one environment.

**Priority:** P1 = address before relying on the affected workflow for analysis; P2 = a normal engineering fix or a clearly identified design improvement. “Reproduced” means the stated synthetic probe ran; “source-confirmed” means the implementation path was inspected without exercising the full application.

## 1. P1 — Allocate batch output paths across the entire survey

**Affected:** Batch, both engines, all four modes.

**Defect and evidence.** `src/cellquant/survey/__init__.py:679` builds one queue per layout into the same destination. `src/cellquant/batch/__init__.py:145` maps individual-file inputs to their basenames, and its collision tracking is local to one `build_queue` call. Two different layouts can therefore select the same store path. Their different input fingerprints prevent resume; `RunStore.create` in `src/cellquant/persist/store.py:112` allows reuse of the directory, so later output can replace the earlier run.

**Reproduced:** `specimen_A/image.tif` and `specimen_B/image.tif`, queued separately as different layouts, both resolve to `out/image.tif.cellquant`. The probe confirmed the identical destinations; no real result store was overwritten. The overwrite consequence is established by the subsequent store-creation path.

**Suggested edit.** Plan output identities once for the complete survey. Preserve paths relative to the survey input root or use a stable acquisition identifier. Check collisions globally, including case-insensitive collisions on Windows. Make a different source/configuration produce a distinct run revision or an explicit replacement decision rather than silently reusing a completed store. Define how reviewed-label sidecars belong to a revision so they cannot be accidentally inherited by a new segmentation.

**Acceptance:** Same basenames across layouts yield distinct stores; reversing layout order does not change their identities. Resume selects the matching source/configuration only. An incompatible rerun preserves the earlier result and its reviewed labels unless replacement is explicitly requested.

## 2. P1 — Honor unchecked layouts and CSV exclusions

**Affected:** Batch selection in the UI and assignments CSV; both engines/all modes.

**Defect and evidence.** The UI in `src/cellquant/plugin/widget.py:704` excludes a layout by omitting it from assignments. `read_assignments_csv` in `src/cellquant/survey/__init__.py:583` similarly skips `include=no`. But `with_assignments` at line 621 falls back to the survey's previous `segment_channel` when a layout is absent. Recognizable channels can already have a default assignment, so omission restores them to the run plan.

**Reproduced:** Two layouts start assigned to channel 0. Applying a map containing only the first leaves the second assigned to channel 0 rather than excluded.

**Suggested edit.** Represent inclusion independently from channel assignment. Alternatively, define an explicit assignments map as the complete selected set, with a separate API for partial updates. The summary and queue must derive from the same final plan.

**Acceptance:** Deselect one of two preassigned DAPI layouts and verify it is never queued and produces no output. Repeat with CSV `include=no`, all layouts excluded, and a previously saved survey. The displayed selected-file count agrees with the executed queue.

## 3. P1 — Consolidate reports across layouts

**Affected:** Batch progress/results, both engines/all modes.

**Defect and evidence.** Each `run_batch` writes the same root `batch_summary.json` and `failures.csv` through `src/cellquant/batch/__init__.py:220`. `run_survey_batches` calls it repeatedly against that root (`src/cellquant/survey/__init__.py:679`). The last layout replaces the earlier reports, including failures.

**Reproduced:** Writing a report for a failed first layout and then a successful second layout leaves `total=1`, `failed=0`, and an empty failures CSV.

**Suggested edit.** Maintain one survey-wide result ledger with layout and acquisition identity. Publish consolidated root reports at checkpoints and at completion/cancellation; optionally retain layout-specific reports under separate paths. Point UI totals and Open failures at the consolidated ledger.

**Acceptance:** A two-layout run with one failure reports both acquisitions and retains that failure. Repeat with resumed items and cancellation after the first layout. In-memory totals, saved reports, and UI links agree.

## 4. P1 — Save one consistent edited-mask revision

**Affected:** Single-image Measure and Save, both engines/all modes.

**Defect and evidence.** `src/cellquant/plugin/controller.py:755` retains a reference to `layer.data`. In the worker, `_validated_label_array` returns `astype(np.uint32, copy=False)` at line 102, so ordinary uint32 labels still alias the editable viewer data. Measurements at line 769 and persistence at line 772 can observe different contents. The worker also dereferences shared `self.image_volume` between stages.

**Reproduced:** A simulated paint between measurement and persistence changed the label-array sum from 4 at measurement to 12 at save. The probe executed the actual controller method/validator, with persistence intercepted; it did not write a real run.

**Suggested edit.** Admit the save operation, then bind a consistent source, mask revision, configuration, and analysis context for its entire lifetime. Use a safely synchronized immutable snapshot or an explicit edit lock during preparation. Moving an unsynchronized copy onto a worker does not by itself guarantee consistency. Keep snapshot preparation responsive for large arrays.

**Acceptance:** Painting or attempting to switch sources during Save cannot alter that save's inputs. Measurements, labels TIFF, QC, and provenance all identify the same revision. Test edits during snapshot preparation and between measurement/persistence, including the normal uint32 path.

## 5. P1 — Define zero stitching without merging unrelated object IDs

**Affected:** `stitch_2d`, both engines, direct and killable paths.

**Defect and evidence.** `src/cellquant/segment/__init__.py:377` stacks plane masks but only calls `stitch3D` if the threshold is greater than zero. The equivalent branch exists in `src/cellquant/segment/killable.py:119`. Plane-local IDs are not offset when stitching is skipped. The UI permits threshold 0 and configuration also accepts negative thresholds.

**Reproduced:** Three independently segmented planes each return a four-pixel object numbered 1. Threshold 0 produces one shared nonzero ID `[1]` across the stack, so downstream label-based measurement treats them as one object. This is a skipped-stitching ID defect; it does not test the real positive-threshold stitching algorithm.

**Suggested edit.** Either require `0 < stitch_threshold <= 1` for the stitched mode, or explicitly support an unlinked-plane mode that offsets IDs across planes and reports its different counting meaning. Do not silently represent separate plane objects with one stack label. Apply the same policy to both execution paths.

**Acceptance:** Zero is rejected before inference or produces globally distinct IDs under a documented unlinked policy. Negative/out-of-range values fail validation. Positive-threshold tests cover linked objects, disjoint objects, empty intermediate planes, and cancellation for direct and killable execution.

## 6. P1 — Use dimension-appropriate measurements and filters for 2D

**Affected:** Single-plane and maximum-projection modes, both engines, single and batch workflows. The Z-border subcase requires those faces to be configured; defaults do not remove them.

**Defect and evidence.** `prepare_analysis_volume` preserves source Z spacing on singleton-Z images (`src/cellquant/preprocess/__init__.py:131`). `src/cellquant/measure/__init__.py:140` and line 158 always report voxel count × Z × Y × X spacing as `volume_um3`, including projections. `filter_size` in `src/cellquant/postprocess/__init__.py:30` uses the same cubic calculation. A maximum projection does not supply a reconstructed object thickness. Separately, `remove_border_labels` at line 50 treats its singleton plane as both Z borders, removing every object when either Z face is selected.

**Reproduced using actual measurement/postprocess functions:** A four-pixel interior mask with 0.5 µm XY spacing has area 1 µm². Changing only source Z spacing from 1 to 5 µm changes reported volume from 1 to 5 µm³. A `min_volume_um3=2` filter changes from dropping to retaining the same projected mask. Selecting Z borders removes the interior object in both cases.

**Suggested edit.** Make dimensionality an explicit part of the analysis contract. Report area in µm² for 2D and use area-based filters; report volume in µm³ for volumetric/linked-stack masks. If slab-volume estimates are required for a single section, give them an explicit thickness assumption and distinct name. Reject inapplicable Z-border settings for 2D with a repair message. Version the output schema so existing consumers do not silently reinterpret columns.

**Acceptance:** Changing source Z spacing cannot change projected area or an area-based inclusion decision. Test single-plane, projection, true 3D, and stitched outputs. A 2D run cannot silently lose every object because of inherited Z-border settings. Output tables clearly distinguish area, volume, and any explicit slab estimate.

## 7. P2 — Convert drawn diameters into the selected image's pixel coordinates

**Affected:** Shared manual-diameter controls used in single and batch; both engines/all modes.

**Defect and evidence.** Image channels are published with physical spacing (`src/cellquant/plugin/controller.py:674`). The diameter Shapes layer is created without corresponding scale/source binding (`src/cellquant/plugin/widget.py:303`). `src/cellquant/plugin/diameter.py:15` calculates the raw Shapes-coordinate length and line 64 returns it as pixels, without converting through the image transform.

**Reproduced at helper level:** A 10-pixel object at 0.5 µm/pixel spans five world units. A default-scale line spanning those coordinates reports 5 px instead of 10. Live napari drawing was not exercised.

**Suggested edit.** Bind the measurement line to the selected image. Transform line endpoints into that image's XY pixel grid before calculating diameter, handling the viewer's spatial axes explicitly. Show both physical length and pixel diameter. Invalidate or reconvert measurements when the source/grid changes, particularly when a representative batch image differs in calibration from other images.

**Acceptance:** A 10-pixel object reports 10 px at unit spacing, 0.5 µm spacing, and anisotropic XY spacing. Include scaled/translated layers, source changes, and the hidden canonical ZYXC layer plus displayed channel-layer arrangement.

## 8. P2 — Reject busy requests before mutating source/configuration state

**Affected:** Single-image controller; both engines/all modes, including a second request during Save.

**Defect and evidence.** `src/cellquant/plugin/controller.py:700` applies configuration changes and sets `self.image_volume` before `_dispatch` checks `_busy` at line 290. A request that fails admission can still replace the active source/configuration. UI controls remain callable during active jobs; Save reads the shared source between stages.

**Reproduced:** Calling the actual `segment` method against a busy dispatcher changed source A to B before raising the busy error.

**Suggested edit.** Perform an atomic admission check before changing controller state. Keep accepted operation inputs local; publish result state only with its matching job identity. Disable conflicting controls while explaining the active operation, but retain the controller guard for programmatic calls. Implement this together with item 4's immutable save context.

**Acceptance:** Attempted second runs during segmentation and saving leave the accepted job's source, configuration, and result association unchanged. Cancelled or obsolete callbacks cannot replace a newer job's state.

## 9. P1 — Reject unknown v3 model names and missing custom weight paths

**Affected:** v3, all modes, especially configuration/CLI/custom-model use.

**Source-confirmed defect.** `src/cellquant/segment/__init__.py:194` treats a nonexistent path as `model_type` rather than rejecting it. The installed classic Cellpose source at `C:/Users/ianpu/miniconda3/envs/cellquant-napari-v3/Lib/site-packages/cellpose/models.py:239` falls back to its default when that name does not exist; the default is `cyto3` at line 216. CellQuant's zero-hash sentinel, used for its normal v3 built-ins, accepts whatever weight file resolves at adapter line 237. Thus an unknown name or missing custom path with that sentinel can resolve a different model rather than fail. This path was inspected without loading weights. With a pinned nonzero hash, a different resolved model should instead fail the hash check.

**Suggested edit.** Validate built-in names against the supported model registry and distinguish them from custom filesystem paths. Missing custom files and unknown names must fail before model construction. Preserve both requested model identity and resolved weight identity in the run record. Keep hash checks as verification of the intended model, not a substitute for validating its identity.

**Acceptance:** A misspelled built-in and a deleted custom-model file fail before inference, including when the sentinel hash is used. Valid nuclei and custom models resolve to the requested identity; no fallback to cyto3 is accepted without an explicit user choice.

## 10. P2 — Validate canonical axes and reject ineffective engine settings

**Affected:** Both engines/all modes; most exposed through configuration/CLI rather than ordinary UI defaults.

**Reproduced validation gaps.** `src/cellquant/config.py:153` allows `channel_axis=0`, `z_axis=2`, `diameter_px=NaN`, `compute_masks=False`, and a negative stitch threshold. Yet the adapter always removes C and supplies YX or ZYX (`src/cellquant/segment/__init__.py:491`), while forwarding user axes at lines 300–301. Arbitrary axes can contradict that canonical contract. `compute_masks=False` also contradicts a pipeline whose next stage requires a valid mask. The probe establishes acceptance of these settings, not every downstream failure mode.

**v4-specific source evidence.** The adapter forwards `rescale_factor` at line 303, but installed v4 `models.py:267` resets `rescale=1.0` and then derives rescaling from diameter. A configured rescale factor can therefore look effective in configuration/provenance while being ignored by this installed engine.

**Suggested edit.** Define a capability/validation table per supported Cellpose version and mode. Own canonical axis arguments inside the adapter instead of exposing contradictory overrides. Validate finite numeric ranges, required masks, and mode-specific parameters before model loading. Reject or visibly mark unsupported/no-op settings; distinguish requested parameters from effective parameters in provenance.

**Acceptance:** All contradictory examples above fail early with field-specific messages. Valid v3/v4 configurations still pass all four modes. For every exposed rescaling control, a recording or installed-engine test demonstrates the effective behavior; changing an ignored field cannot masquerade as an applied scientific parameter.

## 11. P2 — Extend batch failure isolation to store setup and secondary failures

**Affected:** Batch, both engines/all modes; especially storage interruptions and staging publication failures.

**Source-confirmed defect.** In `src/cellquant/batch/__init__.py:264`, resume checks, staged-output publication, and store creation occur before the per-file `try` at line 301. Exceptions there can abort the entire queue. Logging or committing failure status in the error handler can also raise before final reports are written. This is a control-flow finding, not a live disk-failure test.

**Suggested edit.** Put item-specific setup inside the failure boundary. Preserve the original exception when secondary logging/commit operations fail; record a fallback error without relying on the failed store. Finalize the aggregate ledger for all known outcomes. If the destination is globally unavailable, stop explicitly with a retained partial manifest rather than repeatedly attempting every item.

**Acceptance:** Inject one store-creation failure, one staged-publication failure, and one error while recording a failure. Independent later files continue where storage permits, the original error remains visible, and the consolidated report accurately describes completed, failed, cancelled, and unstarted work.

## 12. Design recommendation — Make engine/mode consequences visible before running

**Affected:** Both workflows and all engines/modes. These recommendations do not assert that one segmentation mode is biologically more accurate or universally faster.

The current mode menu and tooltips explain the basic options, but important run semantics remain spread across defaults, hover text, and later warnings. `src/cellquant/plugin/slow_run.py:16` suggests projection/single-plane modes as faster alternatives to volumetric work, without stating in the slow-run dialog that the counted population changes. The shared Native diameter recommendation is not tailored to v3/v4. The CPU-fallback checkbox says “if GPU fails,” while `load_model` at `src/cellquant/segment/__init__.py:182` handles unavailable CUDA but does not retry arbitrary constructor/evaluation failures.

**Suggested edits.**

- Add a compact preflight showing engine/model, effective device, selected channel, mode, plane/projection range, image dimensions, spacing, effective anisotropy, diameter units/value, and selected file count. Let users inspect the exact configuration used for each layout.
- In batch, flag mixed pixel spacing within a channel layout. A shared pixel diameter represents different physical sizes across calibrations; expose physical-size entry/per-image conversion or make the common-pixel choice explicit. The bundled anisotropy value is fixed, while the adapter supports `manifest`; show whether anisotropy is fixed or derived from each image.
- For single-plane mode, bind the valid Z range to the image, show the selected plane immediately, and offer “Use displayed Z.” Before batch inference, identify files too shallow for the selected index.
- State in the slow-run dialog that maximum projection and single-plane runs change the analysis, rather than presenting them as interchangeable speed settings. Preserve mode/grid identity in result summaries so 2D and volumetric counts cannot be casually combined.
- Use engine-specific diameter copy: make clear that Native is not automatic diameter estimation. Explain environment switching for v3/v4 without implying both engines are loaded in one session.
- Rename fallback to match the implemented unavailable-CUDA policy, or implement a deliberate, narrowly defined retry policy with the effective device recorded. Do not silently rerun arbitrary inference failures on CPU.

**Acceptance:** Before Run, a user can identify which pixels and model will be used and whether a batch shares or derives calibration parameters. A changed Z/mode/device is reflected in the summary. Slow-run alternatives explicitly identify changes in measurement meaning. Representative CPU-only and CUDA environments show accurate engine/device availability and failure-policy copy.

## Implementation order and verification handoff

1. Build one complete run plan with inclusion, acquisition identity, unique output paths, and a consolidated ledger (items 1–3 and 11).
2. Bind single-image jobs and saves to immutable source/mask/configuration revisions (items 4 and 8).
3. Correct stitched ID handling and mode-aware measurement/filter semantics (items 5–6).
4. Harden model/parameter validation and coordinate-aware diameter measurement (items 7, 9–10).
5. Add the preflight and mode-specific guidance (item 12).

Preserve the already-correct basic YX/ZYX routing and singleton-Z output contract. Add focused regressions for each defect before changing it; then run real Qt tests and small real v3/v4 inference fixtures in their separate environments. Extend the matrix to the killable subprocess, cancellation during model load/evaluation, CPU/CUDA behavior, positive-threshold stitching, and single-image versus batch equivalence with identical inputs/configuration. Biological quality needs an expert-reviewed reference set and a held-out evaluation, not just matching shapes or increased cell counts.

## Retained evidence

| Artifact in `artifacts/segmentation_audit_20260909/` | What it establishes |
|---|---|
| `mode_matrix_repro.py`, `mode_matrix_output.txt` | Eight preparation/adapter routes, zero-stitch identity counterexample, accepted invalid settings; fake model/stitching, RNG setup disabled |
| `measurement_repro.py`, `measurement_repro_output.txt` | Actual 2D measurement/filter/Z-border counterexamples; no inference or doubles in those functions |
| `single_repro.py`, `single_repro_output.txt` | Actual controller/helper bodies with simulated busy state and paint; intercepted persistence, no live Qt |
| `batch_repro.py`, `batch_repro_output.txt` | Actual assignment/queue/report bodies with synthetic paths; exclusion, cross-layout collision, report replacement |
| `source_manifest.json` | SHA-256 and modification times for reviewed source files; this workspace is not a Git checkout |

The mode matrix runs with the installed CellQuant Python environment. The other probes use lightweight dependencies and do not require model inference. Reproduction scripts write only audit/test artifacts, not existing user run stores. Recorded source hashes identify this review's evidence even if other work changes the pipeline afterward.
