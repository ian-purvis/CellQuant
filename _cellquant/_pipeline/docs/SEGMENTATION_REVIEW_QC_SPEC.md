# CellQuant segmentation review/QC workflow

Status: implemented in 0.4.0a3  
Date: September 16, 2026  
Scope: standalone Segmentation Review/QC

This document remains the behavioral contract. Implemented code lives in `src/cellquant/review/` and `src/cellquant/plugin/segmentation_review.py`. See [current documentation](README.md) for user-facing entry points.

## 1. Problem and outcome

Add a distinct **Segmentation Review/QC** mode in which users inspect Cellpose instance masks over their source images, correct masks manually, and save reviewed masks for subsequent Quantification. Review must be usable without choosing markers, setting classification thresholds, or running measurements.

This document specifies the feature only. It does not authorize implementation or replace the separate quantification-review specification.

## 2. Goals and boundaries

Support three entry scopes: **Single image**, **Folder of masks**, and **Segmentation batch**. Folder and batch scopes create a review queue; editing still operates on one image/volume at a time. A Z-stack counts as one image for review status.

Confirmed product decision: Quantification remains available for both original, unreviewed segmentation masks and reviewed masks. QC approval is not a universal prerequisite. Quantification must show which input it will use.

Out of scope: running/retraining Cellpose in QC, automatic segmentation-quality scoring, automated split algorithms, collaborative simultaneous editing, per-object approval, new marker classification features, and quantitative analysis during review. Supporting external TIFF exports is in scope; other external mask formats are deferred.

## 3. Navigation and primary flow

The new mode begins with scope selection, input selection, output location, and a queue preview. Discovery reads metadata only where possible; it must not load every image into memory.

### 3.1 Single image

Accept a `.cellquant` run directory, its `labels.tif`, or a standalone Cellpose integer-label TIFF (`.tif`/`.tiff`, extension matching case-insensitive). Selecting a mask inside a run resolves the owning run. Opening an existing reviewed mask resolves its review record rather than importing it as a new segmentation.

### 3.2 Folder of masks

Discover supported standalone mask TIFFs and `.cellquant` runs within a chosen directory. Include the selected directory itself if it is a run. Include subfolders is an explicit checkbox, enabled by default. Deduplicate run artifacts and exclude draft, reviewed, revision, and generated review-output files from being rediscovered as new images. Show candidate paths and allow exclusions before starting. Arbitrary intensity TIFFs must not be silently interpreted as masks: require integer instance labels and explicit user confirmation of ambiguous candidates.

### 3.3 Segmentation batch

Accept `batch_summary.json` or its containing batch directory. Construct membership from the manifest, not every run under the parent directory. Include successful complete runs for editing; display failed, cancelled, missing, and incomplete entries with reasons. They remain visible in batch totals but cannot be approved until their inputs are available and valid. Deduplicate by run identity and resolved path. Permit review of available completed entries in a partially completed batch; refresh membership only on an explicit Refresh action.

### 3.4 Pairing masks with source images

For pipeline runs, use the existing source provenance and resolved configuration. Reconstruct the same analysis grid on which segmentation ran, including series/position, channel mapping, preprocessing, axes, and physical spacing. Equal array shape alone does not establish alignment.

For standalone masks, offer source-image selection and a pairing table for folders. Filename matching may suggest pairs but cannot accept ambiguous matches automatically. Require confirmation of source, axes, spacing, series/position where applicable, and whether the mask is on the source or prepared analysis grid. Reuse existing imported-mask context validation. Provide source relinking for moved files and persist the mapping. Block approval and Quantification handoff until the source/grid is resolved. Do not resize or resample labels merely to make shapes match.

V1 supports pipeline label TIFFs and external Cellpose label TIFF exports. Cellpose `_seg.npy` dictionaries, outlines-only exports, and RGB renderings are not accepted mask inputs; show an actionable request to export integer-label TIFFs.

## 4. Review workspace and editing

### 4.1 Display and comparison

Use the existing napari viewer and editable Labels layer. Display the source image, original Cellpose labels as a read-only comparison layer, and a separately bound working-label layer. Support source-channel visibility and contrast, mask visibility/opacity, label picking, pan/zoom, and Z navigation. Show image identity, queue position, status, unsaved-change indicator, and output destination.

Provide a visible **View: Side-by-side | Overlay** control in all three entry scopes. Default to Side-by-side on first use and remember the user's last choice across images and sessions.

- **Side-by-side:** show the unmasked source image on the left, labeled **Original image**, and the same image with the working mask on the right, labeled **Mask review**. Keep the left panel free of masks so segmentation boundaries cannot obscure the reference. Allow hiding the source in the right panel to inspect labels alone. The reference image uses the same aligned analysis grid as the editing panel; “original” here means the unmasked image, not a different raw-image coordinate system.
- **Overlay:** show the working mask over the source image in a single panel, with adjustable mask opacity and a visibility toggle.

Synchronize side-by-side pan, zoom, Z slice, and displayed axis orientation so both panels show the same physical region at the same scale. Source-channel selection, colormaps, and contrast must match across panels. Reflect navigation from either panel in the other without feedback loops. Mask edits occur only in the right panel; the reference panel supports navigation but cannot change image or label data. Both layouts operate on one shared working mask and undo/redo history. Switching layouts must preserve edits, active label, slice, viewport, and review state without reloading the image or marking it approved. Viewing the original Cellpose labels for comparison must remain visibly distinct from editing the working mask. Use linked views/controllers with shared image data rather than loading a second independent source volume.

Source-channel colors and contrast must therefore give the original image the same appearance in both panels. Mask colors are independent, display-only colors used to distinguish object IDs; changing them must not change source pixels or saved label IDs. Matching appearance within CellQuant does not guarantee restoration of the original Nikon software display settings.

### 4.2 Manual editing and approval

Required edits: paint/add a new object with an unused positive ID; paint an existing object; erase voxels; delete an entire selected object; merge selected IDs; and split an object by assigning a selected portion a fresh ID. Preserve IDs of untouched objects. Do not silently renumber labels or convert instance masks to binary masks.

Default brush edits affect the current Z plane. Any volumetric operation must visibly declare its scope. Delete-object and merge affect the selected IDs throughout the volume and must say so. Split may be completed by manual painting across slices; an automatic splitting algorithm is not required. Provide undo/redo for all destructive label operations and reset-to-original with an unsaved-change prompt.

Persist optional image-level notes. Approval is explicit for the entire volume; merely visiting a slice or opening the image does not count as review. Permit approval with no pixel changes and permit an all-background mask, with an explicit empty-mask acknowledgement.

### 4.3 Queue and unsaved changes

Queue controls: Previous, Next, Save draft, Approve & Next, Skip for now, and Reject. Provide filters for pending, draft, approved, rejected, skipped, and blocked entries. Show approval counts separately from skipped counts; these can overlap. Reject requires a reason. Skip changes only queue status, preserves saved drafts and existing approval, and leaves the item eligible for later review. Skipping never grants approval. Dirty edits must be resolved through the unsaved-change prompt before skipping.

Moving to another image, changing modes, or closing with unsaved edits offers Save draft, Discard unsaved changes, or Cancel. The app must not silently save and approve.

## 5. State and persistence

### 5.1 Review and queue state

Use two independent fields: `review_status` = `pending | draft | approved | rejected`; `queue_status` = `active | skipped`. Loading errors are a separate `blocked_reason`, not approval states. A dirty flag describes only in-memory edits. Approved means a validated mask revision was durably published.

Editing an approved image creates a draft; its previous approved revision remains in history but is no longer selected automatically for new Quantification. Discarding the draft can explicitly restore that approval. Rejection likewise removes the run from automatic reviewed selection. Existing completed measurements remain untouched.

### 5.2 Mask artifacts and metadata

For a pipeline run, use:

```text
<run>.cellquant/
  labels.tif                 # original; never overwritten by QC
  labels_draft.tif           # latest saved draft; never used implicitly for quantification
  labels_reviewed.tif        # compatibility copy of the last published approved revision
  review.json               # authoritative review state and revision reference
  reviews/r000001.tif        # immutable approved revision
```

Store reviewed masks as lossless, grayscale integer TIFFs, background 0 and objects positive IDs. Use uint16 when the maximum ID fits, otherwise uint32; reject negative, floating-point, and greater-than-uint32 values before casting. Preserve ZYX shape, physical grid, and calibration metadata. Normalize 2D to `(1, Y, X)`. Never save the colored overlay as the analysis mask.

Proposed `review.json` schema v2 (paths relative to the run when possible):

```json
{
  "schema_version": 2,
  "review_status": "approved",
  "queue_status": "active",
  "revision": 1,
  "labels_file": "reviews/r000001.tif",
  "supersedes": "labels.tif",
  "original_labels_sha256": "<digest>",
  "labels_sha256": "<digest>",
  "source": "<source image path>",
  "analysis_context_sha256": "<digest of resolved grid/config/source identity>",
  "shape": [1, 1024, 1024],
  "axes": "ZYX",
  "spacing_um": [1.0, 0.5, 0.5],
  "label_count": 125,
  "reviewed_utc": "2026-09-16T18:00:00Z",
  "updated_utc": "2026-09-16T18:00:00Z",
  "note": "",
  "rejection_reason": null,
  "draft_file": null
}
```

Source identity includes the file fingerprint, selected series/position, and channel/grid context. A changed original segmentation or source/config fingerprint invalidates automatic reviewed selection and prompts re-review. Notes and display settings do not change the mask revision. Reviewer name is optional; accounts are not required.

### 5.3 Publication and compatibility

Publish safely: validate working labels and bound run; write and reopen-check a temporary revision TIFF; move it to its immutable name; atomically replace `review.json` as the commit point; then refresh the compatibility copy. Updated readers follow the manifest reference, never a potentially stale compatibility copy. A failed compatibility-copy update is recoverable from the committed revision. Failure before manifest commit must leave the previous committed state intact and retain the editor's dirty state. Keep a recoverable previous metadata version. Detect concurrent updates using the loaded revision/metadata fingerprint and refuse silent last-writer-wins overwrites.

Legacy migration: schema-v1 `review.json` plus valid reviewed TIFF is treated as legacy approved after grid validation. A reviewed TIFF without metadata is `legacy-unverified`, displayed explicitly and requiring confirmation before being treated as approved. Preserve all original artifacts. Do not bulk rewrite existing runs merely by opening a folder.

### 5.4 Standalone masks and output location

Do not modify imported originals. Default to a `reviewed_masks` sibling output directory, with a user-selectable alternative for read-only or long paths. Create a short, unique `.cellquant` review-import bundle per image, using the source stem plus stable identity suffix to avoid filename collisions. Copy original labels as `labels.tif` and write run metadata (`config.json`, `provenance.json`, `status.json`) through a dedicated import adapter. Mark provenance as imported Cellpose segmentation; never fabricate Cellpose settings. Extend Quantification discovery and loading to accept this validated imported-run contract and preserve the source/grid mapping described above. Store masks/review state inside this bundle using the same review contract as native runs.

The current native configuration validator requires segmentation parameters, an engine, and a model hash (`src/cellquant/config.py:169` and `:243`). External masks cannot truthfully supply these. Add a distinct versioned imported configuration with `schema_version: 1`, `run_kind: "imported_labels"`, `io` (resolved source-opening parameters), `analysis` (the validated grid declaration and preprocessing needed to reconstruct it), and `segmentation_provenance` (origin `"cellpose_tiff"`, nullable engine/model hash/settings). Validate the IO and analysis fields using shared existing context validators; do not pass this configuration through the native segmentation-config validator. Branch the shared run loader on `run_kind` before `load_config`, retain the existing loader for native runs, and reject imported configurations in segmentation execution paths. Native segmentation settings remain mandatory for actual segmentation. Integration tests must import a TIFF with unknown Cellpose engine/model, reopen it, and quantify it successfully with the recorded grid and source. This loader extension is required work, not an assumption that the current loader already accepts imports.

For native runs, default to saving alongside the original mask. If the run is unwritable, offer a new output root and create a derived bundle preserving source provenance. Show the destination before editing begins. Avoid deep nested output paths; preflight Windows path length and writeability. No source-image duplication is required, but Quantification needs the referenced image to remain accessible.

### 5.5 Session persistence

Persist a `review_session.json` in the selected writable review-session directory with schema version, scope, selected source/manifest, ordered item identities, resolved bundle paths, exclusions, active item, and discovery snapshot. Per-run review metadata is authoritative; reconcile it on resume. A folder/batch session tracks progress without making approval apply to the whole queue at once.

## 6. Quantification handoff

Provide **Open in Quantification** for the current approved image and **Quantify approved images** for approved queue members. These controls pass bundle/run references and exact revisions into Quantification; they do not run measurements. No manual moving, renaming, conversion, or resegmentation should be needed.

Quantification offers three explicit policies:

| Policy | Selection rule |
|---|---|
| Prefer approved (default) | Current valid approved revision if available; original labels for pending/draft items, with the choice shown. Rejected and blocked entries are excluded by default. Queue skip status does not override review status. |
| Approved only | Include only valid, currently approved entries, including approved entries skipped in the review queue. |
| Original Cellpose masks | Use original masks even when approval exists. Explicit selection may include rejected entries, clearly marked with the rejection reason. |

For a review-mode handoff, preselect Approved only and the supplied revisions. For direct entry into Quantification, retain access to original unreviewed masks. Show a preflight table containing image, review state, original/reviewed source, revision, and exclusion reason. Never silently fall back from a missing/corrupt/stale approved revision to originals; require the user to choose originals or repair/re-review.

Resolve and pin the input at Quantification start so edits made afterward cannot change a running job. Record mask path, content hash, review revision/status, and original-versus-reviewed selection in saved provenance and batch outputs. Existing classification packs remain snapshots; when reopened against a newer mask, label them **Measurements use an earlier mask revision** and offer rerun. Do not recompute or overwrite measurements during QC. Object counts and marker measurements must be recomputed from the selected masks when Quantification runs, never reused from pre-edit segmentation tables.

Remove embedded segmentation editing from the Quantification wizard. Replace it with a status summary and **Open Segmentation Review/QC** shortcut that preserves the Quantification selection/settings for return. Quantification-specific threshold and results review stays in Quantification.

## 7. Responsiveness and recovery

Load only the active image and a bounded optional prefetch. Loading must be cancellable and stale load results must not replace a newer selection.

On reopen, offer the latest saved draft and restore queue progress. Uncommitted temporary files must never count as approval. Save failures show the affected path and actionable error; Approve & Next advances only after successful commit. Do not advertise OneDrive as a multiuser locking mechanism.

## 8. Engineering integration and verified starting point

Extract shared, UI-independent discovery, loading, review-state, validation, and save logic into a `cellquant.review` package. Add a `plugin/segmentation_review.py` controller/widget for the new mode. Make `classify/batch.py` delegate mask resolution to the shared service rather than importing UI code. Preserve public compatibility wrappers where tests or callers depend on existing functions.

### 8.1 Source-inspection snapshot: September 16, 2026

Source inspection, not runtime validation, established the following. Paths are relative to the pipeline repository.

| Current component | Existing behavior | Required change |
|---|---|---|
| `src/cellquant/plugin/widget.py:716` | Top-level choices are Single image, Batch folder, HPC prep, and Coexpression. | Add the exact top-level label Segmentation Review/QC. Keep existing entry points; renaming/reorganizing all modes is outside this feature. |
| `src/cellquant/plugin/coexpression_batch.py:90` | Coexpression embeds Select runs → Review labels → Thresholds → Run + results. | Move segmentation editing and its queue into the new mode. Retain a navigation shortcut from Quantification. |
| `src/cellquant/plugin/coexpression_batch.py:404` | Review reconstructs the analysis image using original source, run configuration, and preprocessing. | Reuse this alignment logic. |
| `src/cellquant/plugin/coexpression_batch.py:589` | Save checks layer identity, run binding, and shape. | Preserve these checks in the extracted editor. |
| `src/cellquant/classify/batch.py:50` | `labels_reviewed.tif` wins solely by file existence. | Resolve explicit input policy and review state; drafts must never masquerade as approved masks. |
| `src/cellquant/classify/batch.py:70` | Saves reviewed ZYX TIFF and `review.json`, preserving `labels.tif`. | Extend validation, revision tracking, and recoverable publication. |
| `src/cellquant/classify/batch.py:194` | Discovery finds complete descendant `.cellquant` runs. | Also include a selected run directory itself; add standalone-mask discovery. |
| `src/cellquant/batch/__init__.py:221` | `batch_summary.json` records source, output_dir, status, run_id, and message. | Use this manifest for exact batch membership. |
| `src/cellquant/classify/batch.py:278` | Quantification reconstructs the analysis grid and checks mask shape. | Retain checks and record the exact reviewed revision. |
| `src/cellquant/plugin/coexpression.py:920` | Standalone YX TIFF is promoted to singleton Z. | Reuse this dimensional convention for imported masks. |
| `src/cellquant/analysis.py:277` | Analysis context, shape, and spacing are validated. | Share these checks with review/import. |
| `src/cellquant/classify/store.py:232` | Classification packs snapshot labels and provenance. | Keep snapshots immutable; identify results based on older masks. |

Related document: [Quantification review UX specification](QUANTIFICATION_REVIEW_UX_SPEC_2026-09-11.md). That document concerns downstream quantification review; segmentation QC has a separate purpose and state.

## 9. Implementation milestones and acceptance

Implement the four milestones in order. Acceptance identifiers retain their original numbers for traceability.

### A. Shared review contracts and safe persistence

Shared contracts, v1 compatibility, revision publishing, and input-policy resolution. These prevent draft/approved confusion before adding UI.

Acceptance:

- **Criterion 6:** Save draft followed by application restart restores saved edits without marking approval or automatically using the draft for Quantification.
- **Criterion 7:** Approve & Next writes a reloadable, pixel-identical integer TIFF with the expected shape/spacing and only then advances. Original mask bytes remain unchanged.
- **Criterion 8:** ID values above 65,535 survive round-trip; negative IDs, values above uint32, wrong shape, wrong grid, and wrong bound layer are rejected before publication. An empty approved mask reports zero objects.
- **Criterion 9:** A failed or interrupted save at each publication step leaves a recoverable committed state. Disk-full, read-only destination, metadata failure, and concurrent revision conflict do not silently lose edits or advance the queue.
- **Criterion 12:** Each input policy selects the documented mask; drafts, rejected revisions, corrupt revisions, and stale source/config fingerprints cannot silently become approved inputs.

### B. Native and imported inputs, folder and batch adapters

Native-run, standalone-import, folder, and batch adapters. Test exact membership and source-grid alignment.

Acceptance:

- **Criterion 2:** Single-image entry opens one native run or standalone integer-label TIFF with the correctly aligned source. Singleton-Z and multi-Z fixtures both work.
- **Criterion 3:** Folder discovery includes a selected run root, honors recursion, deduplicates artifacts, and excludes generated review outputs. Ambiguous pairings require resolution.
- **Criterion 4:** Batch selection uses only the chosen manifest's membership, including visible reasons for unavailable entries. An unrelated neighboring run is not included.
- **Criterion 15:** Legacy reviewed-mask runs and standalone imported bundles load through the documented compatibility path. Missing sources can be relinked without silently changing grid interpretation.

### C. Separate review workspace, editing and queue controls

Extract the editor, add queue/state controls, register the top-level mode, and remove embedded editing from Quantification.

Acceptance:

- **Criterion 1:** Segmentation Review/QC is a distinct top-level mode and opens without marker or threshold configuration. Quantification no longer embeds mask-edit controls.
- **Criterion 5:** Paint, erase, add, whole-object delete, merge, split, undo, and redo produce expected voxel/ID changes, including objects spanning multiple slices.
- **Criterion 10:** Previous/Next, mode switching, and close protect dirty edits. Skip, reject, resume, and filter operations preserve per-image status and queue counts.
- **Criterion 16:** Queue discovery does not eagerly load all source volumes. Cancellation and rapid item switching cannot bind an old load or save to the new item. Measure representative large-volume latency and peak memory during verification and record results; no performance figures are asserted here.
- **Criterion 17:** Overlay and Side-by-side are available in single-image, folder, and batch review. First use defaults to Side-by-side; subsequent sessions restore the chosen layout. The left reference remains unmasked, and the right panel supports mask-over-image and labels-only display.
- **Criterion 18:** In Side-by-side, navigation from either panel keeps pan, zoom, Z slice, orientation, source channels, and contrast synchronized. A known landmark occupies the corresponding image coordinates in both panels. Editing affects only the working mask; the reference image and original Cellpose labels remain unchanged.
- **Criterion 19:** Switching layouts after an edit preserves the edited voxels, undo/redo history, selected label, slice, viewport, and approval/draft state. Verify on both singleton-Z and multi-Z fixtures, including switching back after undo/redo. No second independent source-volume load occurs solely because Side-by-side is enabled.

### D. Quantification handoff and end-to-end verification

Add handoff, pinned mask provenance, stale-result indications, documentation, and end-to-end verification.

Acceptance:

- **Criterion 11:** Approved images reach Quantification directly without filename/path manipulation. Pending images remain quantifiable through the original-mask path.
- **Criterion 13:** A deterministic fixture with known per-object intensities produces changed counts/measurements after mask editing, proving Quantification uses the new mask rather than stale tables. Provenance identifies that exact revision.
- **Criterion 14:** Re-reviewing after Quantification preserves existing packs and flags their earlier mask revision. A running job continues using its pinned input.

Extend `tests/test_classify_batch.py` and `tests/test_coexpression_batch_ui.py`; add focused review-service/import/UI tests. The engineer should estimate each work package after a spike on the standalone import adapter and napari split/undo behavior; elapsed-time estimates are not established by this spec.

Use unit tests for state transitions, discovery, ID validation, policies, hashes, and migration; integration tests for TIFF round-trips, import bundles, transaction failures, and Quantification measurements; and Qt/napari interaction tests plus a manual smoke test for the three entry scopes and editing controls. Keep existing segmentation and quantification regression suites passing.

## 10. Definition of done

All four milestones are required for the supported release. Completion requires:

- A distinct Segmentation Review/QC mode supporting single-image, folder, and segmentation-batch review.
- Both display layouts, synchronized source appearance/navigation, and manual edits with preserved undo/redo.
- Durable draft and approved-revision persistence, resumable queues, and recovery without changing originals.
- Native and imported-mask handoff into Quantification with explicit policies and pinned provenance, while retaining direct unreviewed quantification.
- All 19 acceptance criteria covered by focused tests or recorded manual evidence, with existing segmentation and quantification regression suites passing.

## 11. Rollback

Original images and segmentation labels remain intact. Rollback can disable the new mode, but older code must not be allowed to treat a stale `labels_reviewed.tif` compatibility copy as current approval. Before reverting the resolver, use an explicit migration that verifies the current approved revision, refreshes only approved aliases, and archives nonapproved aliases without deleting revision history. Do not downgrade schema-v2 data silently.
