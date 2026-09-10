# Integrated retinal coexpression

Date: 2026-09-07
Status: APPROVED approach; implementation and scientific validation tracked separately
Mode: Research / builder

## Problem and decision

Researchers need counts and percentages of cells expressing combinations of three
or four antibodies. Most markers are nuclear; some are cytoplasmic. The user
approved an integrated CellQuant workflow and authorized implementation. Retain
napari and the shared calibrated Python core. Classification must be independent
of Cellpose and must be usable with already reviewed labels.

The reference is `C:/Fiji.app/macros/Cellpose_workflow_v5_new_Stage3_script`.
Its useful contracts include immutable review snapshots, marker/channel mapping,
pixel-fraction positivity, inclusive combinations, explicit denominators, and
missing markers represented as NA. Its 256-bin scoring is an approximation and
is not a biological ground truth. Its revised interactive workflow is itself
documented as not yet validated end to end.

## Alternatives considered

1. Literal Fiji Stage 3 port: quickest compatibility, but carries histogram
   approximations and layout-only threshold pooling into the new interface.
2. Integrated independent classification (chosen): exact scoring from image
   pixels and reviewed labels, reusable recipes, reviewable results, and clear
   extension points for calibration and cytoplasmic compartments.
3. Fiji import bridge: useful later for historical runs, but leaves native
   CellQuant quantification and installation problems unresolved.

An independent review identified two prerequisites: intensity summary statistics
cannot reconstruct pixel-positive fractions, and biological sample identity
must survive export. This design therefore scores original image pixels and
records specimen/eye/section/image/region identities without pooling animals.

## First implementation milestone

Add a nuclear coexpression module, immutable classification stores, a headless
command, and a Coexpression page in the existing napari dock. The page uses open
CellQuant images plus reviewed label layers, can load an existing label TIFF,
and offers marker mapping, fixed raw-intensity bounds, positive-pixel fraction,
optional uncertainty band, named counting region, preview, and save/reopen.
No segmentation model is required to reclassify masks. Source data remain intact.

Automatic threshold proposals, control calibration, cross-image biological
aggregation, and cytoplasmic compartments are explicit follow-up milestones,
not capabilities implied by this first release. Nuclear recipes reject unsupported
compartments. Nearby fluorescence is not silently assigned to a nucleus.

## Scientific contracts

- Input grid is the existing analysis grid, ZYXC for image and ZYX for labels,
  with matching physical spacing. Original fluorescence intensities are used;
  display contrast and Cellpose normalization do not alter measurement.
  Existing preparation may select a plane or compute a max-Z projection; record
  that transformation. Exact means exact on the saved analysis grid. Do not use
  interpolated or normalized segmentation input as measurement evidence.
- A recipe has schema_version 1, a name and calibration_group, 1–6 uniquely
  named markers, and region_policy (`whole_object` or `centroid`). Each marker
  specifies zero-based channel or null (not acquired), low, optional high,
  positive_fraction, uncertainty_margin, and compartment `nucleus`.
  Optional expected_channel_names binds a reusable recipe to a known channel
  layout. GUI-created recipes include this binding; a mismatch requires explicit
  remapping. A channel-layout match still does not establish calibration validity.
- Bounds are inclusive. Score is the exact fraction of an object's finite raw
  pixels in [low, high]. Any nonfinite object pixels produce a missing call with
  a reason; an absent marker is missing, never negative. Invalid mapped channel
  indexes fail rather than masquerading as absent stains.
- With cutoff c and margin m: positive if f >= c+m; negative if f < c-m;
  otherwise uncertain. m=0 gives the Fiji-style >= cutoff rule without bins.
  Validate finite numeric values, 0<c<=1, and 0<=m<=min(c,1-c).
- Regions are explicit, grid-matched boolean masks. Whole-object membership
  requires all object voxels in the region. Centroid membership tests the nearest
  voxel to the geometric centroid (floor(coordinate+0.5)); measurement still uses
  the complete object. A missing region means an explicitly named whole image.
  Export policy and exclusions. Never silently extrude a 2D ROI into a stack.
- Queries specify required positive and negative marker names and optional
  denominator_positive names. Default queries enumerate all nonempty inclusive
  positive combinations. A cell is evaluable only when all markers used by that
  query and its denominator have definite positive/negative calls. Missing or
  uncertain unrelated markers do not remove it from a query.
  Reject a marker required both positive and negative, including a negative
  requirement conflicting with denominator_positive. Unknown marker references
  and duplicate query names are invalid.
- Numerator is always a subset of the explicit denominator. Export total eligible,
  evaluable, missing, uncertain, denominator, numerator and percentage. Zero
  denominator yields undefined/NA, never 0%. Missing and uncertain counts use
  disjoint categories (missing takes precedence).
- Exact marker patterns are a separate table, reporting complete patterns and
  excluding unknown calls with their counts explicit. No silent all-negative
  interpretation or default conditional denominator based on marker ordering.
- Context preserves specimen_id, eye_id, section_id, image_id, region_id. This
  milestone reports one image/region; it does not claim stereological estimates,
  cell-type identities, density, or animal-level statistics.

## Shared APIs and persistence

`cellquant.classify.ClassificationRecipe(raw)` validates and canonicalizes a
JSON-serializable recipe and exposes `.raw` and `.fingerprint`.
`classify_labels(image, labels, recipe, *, region=None, context=None, cancel=None)`
accepts ImageVolume/LabelVolume on the same analysis grid and returns
`ClassificationResult(calls, queries, patterns, exclusions, metadata)` with
pandas DataFrames and a JSON-serializable metadata dict. Calls key by label/marker
and include fraction and call string positive/negative/uncertain/missing.

`cellquant.classify.store.save_classification(output_root, image, labels, recipe,
*, region=None, context=None, cancel=None)` computes and saves a new unique run
directory, returning `(path, result)`. Store exact analysis image, labels, optional
region, recipe, context, CSVs and checksummed manifest. Publish completion last;
cancelled/failed runs cannot be mistaken for completed results. Use opaque unique
directory names, never user marker names as paths. Store arrays as allow_pickle=False
NPY data and hash content, shape, dtype, spacing and channels in dependencies.
`reopen_classification(path)` verifies checksums and returns the saved input bundle;
`reclassify_run(path, output_root, recipe=None, *, cancel=None)` writes a new run.
Editing a mask or recipe never overwrites a previous classification. Reopening
does not trust summary CSVs as measurement evidence.

The CLI prepares analysis grids using existing configuration for image+labels
classification. Reclassification uses verified saved analysis inputs directly.
The GUI uses the same core, snapshots arrays before a worker runs, rejects label
or ROI transforms inconsistent with the image grid, and marks previews stale
when inputs or rules change. Saving recomputes from current snapshots. Preview
overlays use separate layers and preserve source images/labels. Raw thresholds
are explicit user choices; unreviewed arbitrary defaults must not silently save.

## User flow sketch

Open image / load reviewed labels → Coexpression → name sample and region →
map markers and enter thresholds → Preview calls → inspect marker overlay and
numerators/denominators → Save classification. Save/load recipe and reopen a
classification support iteration. Long scoring and saving run off the UI thread.
Contextual help explains pixel threshold versus cell-positive fraction and
inclusive versus exact combinations. State handling covers no image, no labels,
invalid rules, mismatched grids, no cells, missing markers, cancelled work and
stale previews.

## Validation and release

Unit tests use hand-counted voxel examples, inclusive/exact patterns, conditional
denominators, missing and uncertain calls, ROI boundary semantics, sparse label
IDs, empty masks, nonfinite inputs, cancellation, and distinct intensity
distributions with equal summary statistics. Integration tests cover saved
reopen/rescore, checksum tampering, label edits, CLI/GUI shared results and
standalone wheel resources. Qt tests use an offscreen real viewer where feasible.

Use a small real retinal crop with existing masks for an engineering smoke test;
that demonstrates execution only. Expert-annotated representative retinal fields
and independent novice usability sessions are required before claiming counting
accuracy or production readiness. Threshold methods, expected error bounds and
the cytoplasmic assignment method remain scientific validation questions.

## Distribution and remaining milestones

Preserve Windows install/launch entry points and package default configuration
inside the wheel so an install works away from the source tree. Build and test a
wheel locally. macOS/Linux installation, GPU inference and real interactive Qt
validation need their own measured gates. No package publication or remote CI is
claimed without an actual repository and release destination.

After the first milestone: control-based calibration and sensitivity review;
per-layout mappings plus explicit calibration groups; region/sample batch
manifests and aggregation; separately validated cytoplasmic assignment; novice
pilot and cross-platform release qualification.

The next real-world validation task is for the lab to select representative
retinal fields with expert-reviewed masks and marker calls, including dim,
crowded and ambiguous cases. Software fixtures must not substitute for that set.

Design review: independent review passed (9/10); analysis-grid semantics and
contradictory-query validation were clarified before implementation.
