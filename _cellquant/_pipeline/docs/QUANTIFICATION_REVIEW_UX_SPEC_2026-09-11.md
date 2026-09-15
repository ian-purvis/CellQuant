# CellQuant quantification review workflow

> Historical implementation proposal: this records its dated scope, not current feature status. See [current documentation](README.md).

Status: proposed implementation specification  
Date: September 11, 2026  
Scope: Napari Coexpression mode; reviewing existing segmentation and quantification settings

## 1. Problem and outcome

Users currently select an inner `classify_*` folder, reopen it, request a preview, and choose a marker. Alternatively, they assemble an ND2 source, reviewed labels and a recipe manually. This requires knowledge of internal storage and loading order.

Provide one **Review quantification** entry point. It restores an analysis, explains the effective settings, guides cell inspection, previews proposed changes, and saves a traceable new version. A novice should not need to identify an internal run folder. An experienced user must retain direct access to recipes and advanced controls.

This spec defines desired behavior, not a claim that current storage or APIs already support it. Engineers must map these requirements onto the current persistence and classification contracts before implementation.

## 2. Goals and boundaries

Required outcomes:

- Open an outer analysis folder or an existing inner classification run.
- Restore the matching image, segmentation, recipe and available saved calls without resegmenting.
- Make displayed, previewed and submitted settings agree.
- Explain cell calls using the actual classification rule and measurements.
- Compare saved and proposed calls over an explicitly identified population.
- Preserve the original analysis when saving changes.
- Remain usable with CPU-only hardware, large images, narrow docks and interrupted work.

Out of scope: new segmentation algorithms, automatic claims that thresholds are scientifically correct, cloud collaboration, model training, and redesign of the headless CLI. The CLI's existing outputs should remain discoverable by this workflow. Existing manual loading paths remain available under advanced actions.

## 3. Navigation and primary flow

Add a prominent **Review quantification** action within Coexpression mode. It opens a review workspace with four stages: **Open analysis → Inspect cells → Compare changes → Save version**. These are navigable stages, not a mandatory wizard that forces repeated setup.

### 3.1 Open analysis

Provide **Open analysis folder…** and a recent-analysis list. Accept both an outer folder containing analyses and an inner classification folder. File-system selection must work without drag-and-drop.

Search the selected folder through supported run structures. Do not recursively scan arbitrary drives or eagerly open pixel arrays. Run discovery in a cancellable worker, skip reparse-point loops, and expose progress for a long search. Prefer manifests and completion markers over filename parsing; use a documented legacy adapter where needed.

If one valid analysis is found, select and open it. If several are found, show a searchable list with sample/image name, timestamp, markers, segmentation mode and status. Provide Details for run identifiers, paths and provenance. Never choose among distinct classifications silently. Missing metadata is displayed as unavailable, not inferred as fact from a directory name.

Statuses must distinguish: complete and available, incomplete, missing dependencies, incompatible format, and unavailable recent path. A recent entry whose drive is disconnected offers Locate rather than disappearing.

### 3.2 Restore or assemble

For a classification run, resolve its exact source image, segmentation and recipe. Restore available saved calls automatically when their provenance matches. If recomputation is necessary, explain why and present **Compute calls**. Never silently replace the source, segmentation, recipe or engine.

For a segmentation-only run, load its source and labels, then offer **Choose recipe…**. For a folder with several segmentation runs, require a selection. Recipe compatibility checks include the existing pipeline's channel, dimensional and measurement requirements; unresolved mappings must be reviewed before computation.

If a source path moved, offer **Locate source…** and verify its identity using available provenance. Same filename alone is insufficient. If identity cannot be verified, label it an unverified replacement and require explicit acceptance before creating a new version. Do not treat old saved calls as valid for that replacement.

An unavailable source must not prevent viewing a saved summary or provenance. Clearly disable pixel inspection and remeasurement until dependencies are restored. Loading failures should leave the previously open analysis intact.

### 3.3 Orientation

Keep a compact header visible with sample/image, segmentation mode, active settings version and state. Include source series/position when applicable. Put paths, hashes, units and calibration provenance in an expandable Details section.

Use explicit state text: **Saved settings**, **Unsaved changes**, **Computing preview**, **Preview out of date**, or **Ready to save**. Show review status separately from computational validity. “Reviewed” means someone recorded a review; it is not a quality certification.

## 4. Marker inspection

Use marker tabs where space permits; use a labeled marker selector with the same state at narrow widths. Selecting a marker changes its settings, overlay, measurements and summary together. Preserve the selected cell and camera position when meaningful.

Provide a textual legend for positive, negative, uncertain and missing calls, using the categories actually supported by the active rule. Explain why a measurement is missing where known. If only a generic reason is recorded, say that rather than inventing a cause. Status must remain understandable without color.

Show raw fluorescence and optional label boundaries. A quick overlay toggle must preserve the camera, Z position and display range. Intensity display controls must be labeled as display-only and must not modify quantification thresholds.

For the selected cell show stable object identifier, marker measurements with units, and a rule-derived explanation. Example: “24% of the measured region exceeds the intensity threshold; the required fraction is 20%.” Generate the explanation from the same rule representation used for evaluation, including boundary operators and applicable uncertainty rules. Do not hard-code one explanation for all classifiers.

Provide **Review representative cells** with named groups:

- Clearly negative and clearly positive, when available.
- Near a decision boundary, using a documented rule-specific distance.
- Uncertain or missing measurements.
- A reproducible random sample of eligible objects.

Show group size, sampling method and position in the queue. A group with no eligible cells is visibly empty. Support Previous, Next and direct object selection. Preserve navigation and raw fluorescence when an example or threshold edit invalidates acceptance. Mark the queue stale if its selection criteria change; let the user explicitly refresh it while retaining already inspected object IDs where possible.

For compound rules without a meaningful scalar boundary distance, explain the selection method or make boundary sampling unavailable. Do not present an arbitrary ranking as scientific certainty.

## 5. Editing and preview semantics

Common controls belong beside the inspection view; advanced parameters live in a collapsible section. Each control has a readable label, units, valid range and a short explanation of its effect. Provide contextual visual aids where meaningful: a threshold on a distribution or an outline of the measurement region. Missing physical calibration must not be presented as confirmed micrometres.

Maintain an immutable saved baseline and a separate draft. Guided and advanced views edit the same draft. Marker changes preserve other marker drafts. Switching analyses or closing with edits offers **Save version**, **Discard**, or **Keep editing**; do not silently discard changes.

Provide **Undo** for draft edits and **Revert to saved** for the active draft. Reverting clears stale proposed results and restores the baseline. Recipe/marker changes must also resolve query references; distinguish generated default queries from explicitly authored queries.

Classify edits by dependency:

| Edit | Required invalidation |
|---|---|
| Display contrast, overlay visibility, camera | No measurement or call invalidation |
| Classification rule using unchanged saved measurements | Recompute affected calls |
| Measurement region, preprocessing, or measurement-dependent setting | Remeasure affected scope, then recompute calls |
| Source, mask data, channel mapping or analysis grid | Revalidate context and all affected derived data |
| Review note or display name | No scientific-result invalidation |

Use actual dependencies to implement this table; do not assume every threshold can be applied to cached measurements. Invalidate calibration evidence only when its inputs or relevant settings change. Preview invalidation and calibration-evidence invalidation are separate operations.

Offer an explicit **Update preview** action. Small previews may update automatically after a short debounce, but edits must immediately mark existing results stale. Each job carries a draft revision and source/mask identity. Discard late results for superseded revisions. Users must never see old calls labeled as the current proposal.

## 6. Comparison

Present **Saved** and **Proposed** summary columns plus a difference. Include the denominator, preview scope and counts for all supported call categories. Show **Inspect changed cells** to populate a queue with stable object IDs and before/after explanations.

Example: “GFP-positive: 312 → 347; net +35; 51 cells changed call.” Compute net count change and changed-object count independently: they are not generally equal.

Compare only aligned objects from the same segmentation and eligible population. If masks or eligibility differ, explain that the results are not a direct cell-by-cell comparison and require a new baseline or explicitly scoped comparable subset. A marker-only preview must not imply that dependent coexpression queries are current; recompute affected queries or label them stale.

If saved calls are unavailable, show “Baseline calls unavailable.” Offer computation of the saved baseline when dependencies permit; never fabricate a comparison.

## 7. Saving and applying settings

Use **Save reviewed version…** to open a compact review dialog containing parent version, proposed settings changes, preview/computation status, scope and an optional note. Scope options are **This image**, **Selected images**, and **Compatible batch**. Default to this image. Before a multi-image action, list affected acquisitions, their layouts and any exclusions or unresolved mappings.

Separate two outcomes in this dialog:

- **Save settings version:** persist the draft and review record; do not imply full-image calls were computed.
- **Save and compute full results:** persist a new version and run the required computation for the selected scope.

Support a first release with this-image scope only; hide unavailable scope actions rather than exposing nonfunctional controls. Multi-image application is a separately gated milestone below.

Validate the exact effective recipe shown in the dialog and submit that same immutable specification. Save a new version without overwriting the parent. Record parent identifiers, source/segmentation identities, recipe and schema version, application version, timestamp, note, preview scope, and computation status. Reviewer identity may be user-entered when available; do not imply authenticated attribution.

The saved result must distinguish settings reviewed on a subset from a completed full-image classification. Do not mark unsurveyed cells or samples individually reviewed. Failed or cancelled computation can retain a saved recipe version while clearly marking its results incomplete.

Use existing atomic persistence and completion conventions where possible. A write/publish failure must retain the draft and any recoverable staging data, offer Retry, and identify where data were retained. Only mark results complete after the pipeline's required completion conditions are met.

## 8. Responsiveness, portability and accessibility

- Perform discovery, pixel loading, measurement and full-image classification outside the UI thread. Route progress through a thread-safe queue.
- Estimate image memory from shape/dtype without materializing lazy arrays. Display unknown estimates explicitly. Check scratch space before large preparation and explain available recovery actions.
- Default expensive exploratory work to the selected cell or a clearly defined small region. Always label the sampled population and scope; subset results cannot satisfy full-result completion.
- Show actual execution device, current stage/item, completed/total where known, and elapsed time. Do not offer an exact ETA without adequate evidence. CPU-only operation must remain supported wherever the underlying operation supports it.
- Cancellation acknowledges the request immediately, then reports retained, completed and unfinished work when the worker stops. A cancelled or superseded preview cannot become the active result later.
- Test at 1366×768 with 100% and 150% scaling, a narrow dock, keyboard-only navigation and long paths. Primary actions/status remain reachable. Provide visible focus, accessible labels and text equivalents for color-coded states. Avoid fixed-width layouts that clip controls.
- Support paths with spaces, Unicode, moved roots and disconnected drives. Avoid deep generated paths; cloud publication and local computation status must be distinguishable.

## 9. Suggested engineering structure

These are responsibilities, not mandatory new module names:

1. **Analysis resolver:** discover runs, adapt legacy manifests, resolve dependencies and return availability diagnostics without loading pixels.
2. **Review session model:** hold baseline, draft, revision, source/mask identities, selected marker/object, queue, preview scope, validity and review record. Qt widgets render this state rather than maintaining separate authoritative copies.
3. **Preview coordinator:** build immutable job requests, determine required computation, manage cancellation/progress and reject stale results.
4. **Comparison service:** align object IDs, calculate category transitions and produce changed-object queues over a declared population.
5. **Version writer:** validate and persist the exact effective recipe, lineage and status; integrate with existing run stores.

Inspect current `plugin/coexpression.py`, `plugin/calibration.py`, `plugin/coexpression_batch.py`, `plugin/controller.py`, `plugin/resources.py` and persistence contracts before assigning ownership. Avoid duplicating rule evaluation or weakening existing analysis-grid and provenance validation to make restoration easier.

## 10. Delivery milestones and acceptance gates

### A. Open and restore

Deliver the entry point, outer-folder discovery, readable chooser, automatic restoration and missing-dependency recovery.

Acceptance: a new user opens an outer folder containing one valid classification and sees the correct marker calls without selecting an inner folder or pressing Preview. Multiple analyses require an explicit choice. Segmentation-only data prompt for a recipe. Missing/moved data produce actionable recovery without altering the original run. Legacy inner-folder selection still works.

### B. Inspect and edit

Deliver marker navigation, representative queues, call explanations, baseline/draft state and stale-job handling.

Acceptance: changing marker retains drafts and selected cell where valid. Marking an example does not break Next. Display contrast changes no counts. A dependent measurement edit invalidates its result immediately. Two previews completing out of order cannot replace the latest result with an older one. Rule explanations match boundary and uncertainty test cases.

### C. Compare and save for one image — minimum complete release

Deliver scoped comparison, changed-cell inspection, undo/revert, version saving and accurate completion status.

Acceptance: a fixture with bidirectional call changes reports the correct net difference and distinct changed-object count. Incompatible populations cannot produce an unqualified comparison. Saved settings exactly match the displayed draft. Reopening the new version restores its lineage/settings while the parent remains unchanged. A subset preview is never labeled a completed full-image analysis. Interrupted saves and cancelled computation preserve recoverable state.

### D. Apply across images

Deliver selected-image/batch scope, effective per-image recipe display and progress/recovery.

Acceptance: incompatible channel layouts require reviewed mapping or exclusion. Existing overrides are shown and explicitly retained/replaced. No unresolved visible draft can silently dispatch saved settings. Mixed success/failure produces an itemized result; retry can target unfinished work without overwriting completed parent analyses.

### E. Release usability checks

Run an observed novice walkthrough from outer-folder selection to finding a saved version, plus an expert workflow using advanced parameters. Record where assistance was needed. Exercise the display/scaling, CPU-only, lazy-array, low-scratch, disconnected-source and cancellation scenarios above using safe fixtures. No UI-only automated test substitutes for this final hands-on pass.

## 11. Definition of done

Milestones A–C and E satisfy the first release. Each requirement has corresponding focused tests or recorded manual evidence; the core workflow requires no internal folder knowledge; displayed settings and submitted settings share one source of truth; original analyses remain intact; partial and stale results are unambiguous. Milestone D is required before exposing multi-image review application as a supported action.
