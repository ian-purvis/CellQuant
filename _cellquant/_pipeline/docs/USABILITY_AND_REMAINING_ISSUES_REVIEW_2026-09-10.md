# CellQuant remaining issues and usability review

Reviewed 10 September 2026 after the fixes described in `SEGMENTATION_ENGINEERING_REVIEW_2026-09-09.md` and `COEXPRESSION_ENGINEERING_REVIEW_2026-09-09.md`. This review looks for issues that remain after those changes and recommends a workflow that works for beginners, experienced image analysts, CPU-only laptops, CUDA workstations, and heterogeneous microscopy files.

The most consequential remaining problems are:

1. Single-image measurements can still use the current configuration's plane instead of the plane recorded by the labels.
2. ND2 calibration and position identity are incomplete, so physical measurements or multi-position results can be wrong while looking valid.
3. The v3 environment is formally inconsistent with the package requirements, and capability detection can advertise an engine whose PyTorch installation is unusable.
4. Custom coexpression queries can silently change meaning when a marker is removed, and imported analysis-grid declarations can be reset by unrelated layer changes.
5. The UI still needs a clearer novice-to-expert workflow, resource preflight, and recovery model.

The source is not a Git checkout. Reviewed source hashes and runnable probes are retained under `artifacts/usability_audit_20260910/`. No production source was changed.

## Evidence and limits

The core probes use synthetic arrays and mocked readers. They do not run Cellpose inference, download models, or modify user run stores. The UI probes use extracted method bodies and test doubles rather than a live napari window. A focused pytest invocation passed the initial tests but then hit a Windows temporary-directory permission error in the runner; this is an environment limitation, not evidence of a product failure. Real Qt tests on a laptop display, real ND2 files from each acquisition configuration, and real v3/v4 inference remain necessary.

## 1. P1 — Use the labels' analysis context for single-image measurement

**Current behavior.** Coexpression now resolves measurement context from label provenance, but the general single-image measurement path does not. `src/cellquant/orchestrator.py:220` prepares the measurement image from the live configuration, then builds context from the existing labels at line 222. `run_measurements` can therefore use a different plane or projection from the one used to create the labels.

**Reproduction.** A two-plane image has intensities 10 and 90. Labels carry provenance for plane 0. The current configuration selects plane 1. The actual measurement path reports mean intensity 90 for labels recorded from plane 0, without rejecting the mismatch.

**Suggested edit.** Resolve a single immutable `AnalysisContext` from the labels and selected source before measurement. If the labels have no provenance, require an explicit user declaration and mark it as an assumption. Reject conflicting source, series, position, mode, plane, projection, shape, or spacing. Show the selected analysis context in the Measure/Save panel and persist it with the measurement tables.

**Acceptance.** Changing the live segmentation configuration after labels are created cannot silently change the pixels used for measurement. A mismatch produces a repair message. A saved run's labels, measurements, QC, and provenance all identify the same analysis grid.

## 2. P1 — Treat missing ND2 calibration as missing, not as 1 µm

**Current behavior.** `src/cellquant/io/_nd2.py:59` accepts positive values returned by `handle.voxel_size()` without checking whether the metadata says the axes are calibrated. The installed `nd2` reader returns `(1, 1, 1)` when calibration metadata is absent. CellQuant then treats those values as physical micrometres.

**Reproduction.** A mocked ND2 handle with `axesCalibrated=[False, False, False]` and reader fallback values returns spacing `(1.0, 1.0, 1.0)` from `_spacing`. Downstream volumes and anisotropy calculations accept it as valid.

**Suggested edit.** Inspect the reader's calibration flags and distinguish `calibrated`, `explicit override`, and `unknown`. If calibration is unknown, require `io.spacing_override_um` before segmentation, measurement, or coexpression. Preserve the original calibration status in metadata and make the override visible in the preflight.

**Acceptance.** Uncalibrated ND2 files stop with a clear “enter voxel spacing” prompt. Explicit overrides proceed and are labeled as overrides. Calibrated files retain reader values. No output calls a fabricated 1 µm spacing “metadata.”

## 3. P1 — Persist ND2 series/position identity and use it in reopening

**Current behavior.** `src/cellquant/io/__init__.py:81` selects an ND2 position, but `src/cellquant/io/_nd2.py:46` and the metadata assembled at `io/__init__.py:94` do not record the selected position. A result can therefore contain pixels from one position without saying which position produced them. Reopening helpers that reconstruct from stored configuration are vulnerable to losing this identity if the setting is not materialized consistently.

**Reproduction.** A mocked ND2 reader returns position 0 with mean 0 and position 1 with mean 99. `open_volume(..., position=0)` and `open_volume(..., position=1)` return different pixels but identical metadata; neither metadata record contains the position.

**Suggested edit.** Store `position`, `position_count`, `series`, and the source acquisition identity in `ImageVolume.metadata`, provenance, run config, and fingerprints. Make the ND2 reader's position selection explicit in its returned details. Reopening a run must use the stored position and fail if the requested position is no longer available. Include the position in filenames or report columns when multiple positions are processed.

**Acceptance.** Two positions from one ND2 file produce distinct, self-identifying run stores. Reopening and coexpression use the same position. A missing or changed position fails with a targeted message instead of silently selecting position 0.

## 4. P1 — Make v3 and v4 environments independently valid

**Current behavior.** `pyproject.toml:14` requires `cellpose==4.2.1.1`, while `scripts/install_windows.ps1:242` force-reinstalls Cellpose 3 for the v3 environment. On the installed v3 environment, `pip check` reports that CellQuant requires Cellpose 4.2.1.1 while Cellpose 3.1.1.3 is installed.

**Impact.** A user repairing or upgrading the v3 environment through ordinary package tooling can replace v3 with v4 or be told the environment is broken. This is especially confusing for users who only know that they selected the “v3” launcher.

**Suggested edit.** Define separate, tested dependency constraints for the v3 and v4 distributions. The package metadata used by each environment must describe its intended Cellpose major version. Validate both clean installs and upgrades with `pip check` before reporting success. Show the environment prefix, CellQuant version, Cellpose version, and model readiness in a copyable diagnostics panel.

**Acceptance.** Fresh v3 and v4 installations each pass `pip check`, keep their selected engine after a CellQuant reinstall, and report a clear incompatibility if a user launches the plugin in the wrong environment.

## 5. P1 — Do not advertise an engine when PyTorch is not runnable

**Current behavior.** `src/cellquant/plugin/capabilities.py:320–340` derives engine availability from Cellpose package metadata. A failed PyTorch import can still leave Cellpose-SAM listed as runnable. A direct capability probe produced the summary “PyTorch not found” alongside “Runnable here: Cellpose-SAM (v4).”

**Suggested edit.** Separate “installed,” “importable,” and “ready for inference.” Require successful imports of Cellpose, PyTorch, and the engine-specific model constructor before enabling Run. Keep a diagnostic state visible for users who need to repair an environment. A healthy CPU-only PyTorch installation should remain runnable; a missing or broken PyTorch installation should be blocked with a concrete repair path.

**Acceptance.** Missing DLLs, missing PyTorch, mismatched Cellpose major versions, and healthy CPU-only environments each produce the correct engine/device state. The Run button never becomes enabled for a state that will fail at model construction.

## 6. P1 — Reject null `z_axis` before dispatch

**Current behavior.** `RunConfig` accepts `segment.z_axis: null` for 3D and stitched modes, but `_eval_kwargs` in `src/cellquant/segment/__init__.py:336` calls `int(spec["z_axis"])` for those modes. The configuration loads successfully, then dispatch fails with a `TypeError`.

**Suggested edit.** Normalize `z_axis` to the canonical value 0 during config validation, or require an explicit 0 for modes that use Z. Prefer the first option for user-authored YAML while recording the effective value in provenance. Add a configuration-level error with the field name if another value is supplied.

**Acceptance.** The shipped template, generated configs, hand-edited configs, and old configs all either normalize safely or fail during config loading with an actionable message. No valid-looking config reaches Cellpose and then fails on `int(None)`.

## 7. P1 — Never silently weaken a custom coexpression query

**Current behavior.** `src/cellquant/plugin/coexpression.py:480–494` removes unknown marker names from custom query clauses and retains the query's old name. Removing marker B from “A+ AND B−” can produce an A-only query still labeled “A+ AND B−”.

**Suggested edit.** Detect dependent custom queries before a marker is removed or renamed. Offer explicit choices: remove the query, edit its clauses, or cancel the marker change. Display positive, negative, and denominator-positive clauses directly instead of relying on a human-authored query name. Keep a visible “custom query changed” state until the user previews again.

**Acceptance.** Removing B cannot create an A-only result named “A+ AND B−.” Every changed custom query requires an explicit, reviewable action and is covered by a saved recipe diff.

## 8. P2 — Preserve imported analysis-grid declarations

**Current behavior.** Coexpression layer refreshes can reseed the analysis declaration from the controller's current config. A manually confirmed maximum-projection declaration can revert to volume 3D after an unrelated layer insertion/removal; the same risk applies to a selected Z index.

**Suggested edit.** Seed the declaration only when a new image/mask pair is selected. Preserve explicit user choices across unrelated layer changes. Invalidate only when source, labels, spacing, shape, or relevant provenance changes. Show the declaration and its source in the panel.

**Acceptance.** Adding an annotation layer leaves mode and Z/projection selection unchanged. Selecting a genuinely different mask pair requires a new declaration. Same-shaped singleton-Z masks from different planes cannot inherit the prior declaration silently.

## 9. P2 — Keep calibration navigation alive while examples are edited

**Current behavior.** `src/cellquant/plugin/calibration.py:381` invalidates the review when an example is marked, clearing the queue, table, and overlays. The user must Preview again before moving to the next cell, and the queue restarts.

**Suggested edit.** Separate navigation state from acceptance validity. Marking an example should invalidate the current accepted preview and disable Accept, but retain the current cell, raw fluorescence, queue filters, and Previous/Next navigation. Recompute disagreement status asynchronously after the user requests a new preview.

**Acceptance.** A reviewer can inspect a cell, mark it negative, move to the next cell, mark another positive, and return to either cell without rebuilding the whole review. Accept remains disabled until the new settings are previewed.

## 10. P2 — Make preparation safe on memory-constrained machines

**Current behavior.** Save now snapshots labels and the full image consistently, but `plugin/controller.py:784–787` still copies large arrays synchronously before dispatch. Coexpression has the same class of preparation step. Killable Cellpose also writes an eager temporary `.npy` input before spawning the worker. Cloud staging retains local mirrors under the application cache.

**Suggested edit.** Add a preparation phase with an estimated memory requirement, available RAM, available scratch space, expected temporary size, and a cancelable progress state. Offer a user-configurable scratch directory, including a local non-synced drive. Refuse or ask for confirmation when the estimate exceeds a safe fraction of available memory/disk. Show cleanup status and a button to remove completed staging caches only when no resumable work depends on them.

**Acceptance.** On a laptop-sized RAM budget, a large run explains whether it can proceed before allocating the full snapshot. A low-disk scratch location gives a repairable error. The UI reports where temporary and staged files live and how much space they consume.

## 11. P2 — Make the dock usable at laptop sizes and high display scaling

**Current behavior.** The coexpression marker table has a 720-pixel minimum and calibration forces a 520-pixel minimum. Long rows of controls and a second floating/docked calibration panel compete with the image view. At 125–150% scaling or a 1366×768 display, users can be forced into horizontal scrolling to reach essential actions.

**Suggested edit.** Keep horizontal scrolling inside tables rather than on the entire form. Reflow action rows into vertical groups at narrow widths. Put advanced settings and provenance metadata in collapsible sections. Offer a compact calibration tab that temporarily replaces the parent panel on narrow docks, while preserving the richer side-by-side layout for workstations. Keep Preview, Accept, Cancel, status, and the current cell reachable without scrolling to the bottom.

**Acceptance.** Test at 100%, 125%, and 150% Windows scaling with 1366×768 and a large workstation display. Essential controls remain reachable, labels are not truncated, and the image retains enough area for context.

## 12. P2 — Give users a guided novice path without hiding expert control

**Recommended workflow.** Start with a mode chooser that asks what the user wants to produce: “quick 2D check,” “2D planes linked through Z,” “true 3D objects,” or “projection overview.” Each choice should reveal a short consequence statement, expected runtime/resource profile, and whether output objects represent areas, linked stacks, or volumes.

Add a “guided setup” path with four steps: verify file/series/position and calibration; choose the segmentation channel; run a small representative preview; review labels and adjust postprocessing; then run the full batch. Keep an “Advanced settings” path for users who need model, threshold, tiling, anisotropy, normalization, and output controls. Never silently change settings when switching between paths.

Before Run, show a preflight card containing source identity, series/position, image shape and spacing, calibration source, channel, engine/model, effective device, mode, Z selection/projection, diameter interpretation, estimated memory/scratch use, and included file count. Make the card copyable as text for support requests.

After Run, show a result card with completed/failed/cancelled counts, labels used, analysis grid, model and environment identity, calibration status, output location, and a “review failures” action. Explain whether a resumed result was reused or recomputed. Keep the raw technical logs in the run store, but translate common failures into one next action.

## 13. P2 — Improve accessibility and terminology consistency

Use one vocabulary throughout the UI, config, reports, and help: “source position,” “analysis plane,” “maximum projection,” “linked 2D,” “3D volume,” “area,” and “volume.” Avoid relying on color alone for positive/negative/uncertain/missing; include text, patterns, and counts. Ensure keyboard focus order follows setup order, tables expose headers to assistive technologies, and every disabled control explains what enables it. Make warnings selectable and copyable, especially on machines where users cannot share screenshots.

## Recommended order

1. Fix measurement context, ND2 calibration, ND2 position provenance, and null `z_axis` validation.
2. Make v3/v4 environments and capability states truthful before users troubleshoot models.
3. Prevent custom-query weakening and preserve imported analysis declarations.
4. Add resource preflight, configurable scratch management, and failure recovery.
5. Reflow the dock and add guided setup, result summaries, accessibility text, and consistent terminology.

## Retained evidence

The review artifacts are in `artifacts/usability_audit_20260910/`:

- `core_repro.py` exercises the wrong-plane measurement, uncalibrated ND2 spacing, position metadata loss, and null-Z dispatch failure.
- `portability_audit_20260910/` contains environment capability and installer probes, including a real `pip check` failure in the installed v3 environment.
- `source_manifest.json` records hashes for reviewed setup, capability, staging, IO, orchestration, and plugin files.

No real microscope data was modified. The next verification gate should use real ND2 files with multiple positions and explicit calibration states, one representative laptop and one CUDA workstation, live Qt at several display scales, and separate v3/v4 environments with a small inference fixture for every supported mode.
