# CellQuant architecture and scientific parity contract

## Purpose and baseline

CellQuant ports the Brzezinski lab's Fiji-prep plus Cellpose-SAM v4 notebook
workflow into one installable napari plugin and one headless batch CLI. Both
front ends call the same Python core; neither contains segmentation logic.

The requested `reference/` directory is absent from this checkout. Until it is
added, the reference corpus is:

- inputs: `../Cellpose_documentation/Cellpose_test/test_images` (37 TIFF stacks)
- baseline labels: `../Cellpose_documentation/Cellpose_test/outputs` (the 37
  same-basename TIFFs; `_overlay.tif` files are display artifacts only)
- acquisition metadata: `../Cellpose_documentation/Cellpose_test/nuclei_manifest.csv`
- Fiji prep source: `../Cellpose_documentation/Cellpose_test/fiji_macros/Cellpose_nuclei_prep.ijm`
- segmentation source: `../Cellpose_documentation/Cellpose_test/notebooks/Cellpose_SAM_v4_colab_or_local.ipynb`

The reference notebook's 3D settings are the initial parity profile:
`CellposeModel`, built-in Cellpose-SAM weights, `diameter=30`, `do_3D=True`,
per-file `anisotropy=voxel_z/voxel_xy`, `min_size=200`,
`flow_threshold=0.4`, `cellprob_threshold=0.0`, `z_axis=0`, and
`channel_axis=None`. The Fiji macro extracts one user-selected nuclear channel
without rescaling it and carries XY/Z voxel sizes through its manifest. These
facts are compatibility requirements, not inferred defaults.

## Global contracts

### Image and label data

- Canonical image axis order is `(Z, Y, X, C)`, including singleton axes.
- Eager arrays implement the NumPy array protocol. Lazy arrays implement the
  Dask array protocol. Core algorithms accept either unless their API says
  `eager`; any materialization is explicit, timed, and emitted as an event.
- Source intensity dtype is preserved by I/O. Preprocessing returns `float32`
  and records the transformation. No implicit integer-to-float rescaling is
  allowed.
- Voxel spacing is `(z_um, y_um, x_um)` in micrometres and is mandatory for
  calibrated operations. It is metadata, never guessed from shape or filename.
- Labels are `(Z, Y, X)`, `uint32`, background `0`, and positive integers for
  objects. Persisted TIFF may use `uint16` only when lossless; it is read back as
  `uint32`. Object count is the number of distinct nonzero IDs.
- The public pipeline returns labels on the source image's ZYX grid and spacing
  for `volume_3d` and `stitch_2d`. The two 2D modes return a singleton-Z grid and
  never expand their masks across the original stack.
  If preprocessing resamples the segmentation substrate, the orchestrator
  restores labels with deterministic nearest-neighbour interpolation, records
  both grids in provenance, and emits the materialization; intensity measurement
  therefore remains aligned to every original image channel.
- The shared core type is `ImageVolume(data, spacing_um, channel_names,
  source, metadata)`; `LabelVolume(data, spacing_um, provenance)` is its label
  counterpart. Only the integrator changes these types or the event protocol.

### Configuration and provenance

Every run consumes one validated, versioned configuration. Every parameter sent
to Cellpose, every normalization percentile, every filter threshold, the chosen
axes/channels, and all library versions are explicit. The output directory gets
the canonical serialized configuration, a SHA-256 config fingerprint, input
fingerprint, model-weight SHA-256, environment inventory, stage log, and final
status. Missing required values fail validation; compatibility-profile values
are serialized even when they equal a default.

### Events and failure boundaries

Subsystems emit typed `PipelineEvent` records to a supplied sink:
`stage_started`, `progress`, `materialized`, `warning`, `artifact_written`,
`stage_finished`, `cancelled`, and `failed`. Each record has run/file/stage IDs,
UTC timestamp, progress numerator/denominator where applicable, and structured
details. CLI logging, JSON logs, and napari signals adapt this one stream.

Cancellation is cooperative through a shared token checked between chunks,
tiles, planes, and postprocessing passes. Exceptions cross subsystem boundaries
with stage and input context and are never converted to empty results. The batch
runner catches errors only at the per-file boundary, writes a failure record,
releases GPU/host resources, and continues the remaining queue.

## Package boundaries

### `cellquant.io`

Public API: `open_volume(path, *, series=0, position=0, lazy=True,
axes_override=None, spacing_override_um=None) -> ImageVolume`,
`inspect_volume(path) -> ImageMetadata`, and `iter_supported_files(root,
recursive) -> Iterator[Path]`.

Consumes TIFF, OME-TIFF, or ND2 paths. Emits canonical ZYXC data, preserved
source dtype, explicit channel names, source axes, and calibrated spacing.
Batch discovery is controlled by `io.recursive` and `io.suffixes` (a non-empty
subset of `.tif`, `.tiff`, `.nd2`). Ambiguous axes, time points, series, or
positions fail unless selected in the config. TIFF/OME-TIFF and ND2 are lazy
where the backend permits. Events report metadata decisions, chunking, and any
explicit eager load.

### `cellquant.preprocess`

Public API: `select_channel(volume, index) -> ImageVolume`,
`prepare_analysis_volume(volume, config) -> ImageVolume`,
`normalize(volume, spec) -> ImageVolume`, `rescale(volume, spec) -> ImageVolume`,
`denoise(volume, spec, cancel, events) -> ImageVolume`, and
`run_preprocess(volume, config, cancel, events) -> ImageVolume`.

Consumes ZYXC image data and emits ZYXC `float32`. The parity profile selects the
already-extracted singleton nuclear channel and performs no normalization,
denoising, or spatial rescaling. Other modes must state percentile scope
(per-plane/per-volume), interpolation, antialiasing, and boundary behavior.

### `cellquant.segment`

Public API: `load_model(spec, device, events) -> Segmenter` and
`segment(volume, config, cancel, events) -> LabelVolume`.

Consumes a single-channel ZYXC preprocessed volume and emits ZYX `uint32` labels.
Modes are `stitch_2d` (one whole-Z call with `do_3D=False` and explicit
`stitch_threshold`), `volume_3d` (`do_3D=True` with explicit anisotropy),
`single_plane_2d` (one explicit zero-based `segment.z_index`), and
`max_projection_2d` (maximum-Z projection of every image channel). The latter
two send a YX array to Cellpose with `do_3D=False`, `anisotropy=None`,
`z_axis=None`, and `stitch_threshold=0`, then retain singleton-Z labels.
If preprocessing rescaling is enabled, these 2D modes resample YX only and keep
the source Z spacing as the explicit one-plane thickness.
Model name/path, Cellpose version, weight hash, diameter, thresholds, tile size,
overlap, batch size, augmentation, resampling, normalization, axes, and device
are serialized. GPU initialization failure is fatal when `device=cuda`; CPU
fallback occurs only when the config explicitly permits it and is logged.

### `cellquant.postprocess`

Public API: `filter_size(labels, spec) -> LabelVolume`,
`remove_border_labels(labels, faces) -> LabelVolume`,
`relabel(labels) -> LabelVolume`, `merge_labels(labels, pairs) -> LabelVolume`,
`split_label(labels, request) -> LabelVolume`, and
`run_postprocess(labels, config, cancel, events) -> LabelVolume`.

Consumes and emits ZYX `uint32` labels with unchanged spacing. Volume thresholds
use cubic micrometres; voxel-count thresholds are separate named fields. Border
faces are explicit. The parity profile applies no additional postprocessing
beyond Cellpose's explicit `min_size=200` behavior.

### `cellquant.measure`

Public API: `measure_labels(labels, image, config, cancel, events) ->
MeasurementTables` and `write_measurements(tables, directory) -> list[Path]`.

Consumes ZYX labels and matching ZYXC intensities. Emits tidy tables with one
row per label for morphometrics (voxel count, volume, centroid and bounding box
in pixels and micrometres) plus one row per label/channel for intensity
statistics. Label IDs remain `uint32`; floating values record units and use
stable column names. Empty-label volumes produce header-only valid tables and a
warning rather than invented measurements.

For single-plane and max-projection modes, intensity statistics come from the
matching plane or per-channel maximum projection. Extent is dimension-aware
(objects schema `MEASURE_SCHEMA_VERSION`): those modes report `area_um2` from
Y/X spacing alone and leave `volume_um3` empty, while volumetric and stitched
masks report `volume_um3` and leave `area_um2` empty. The dimensionality comes
from the mask's `analysis_volume` provenance, or from an `AnalysisContext`
passed by the caller.

### `cellquant.persist`

Public API: `RunStore.create(...)`, `write_labels(labels)`,
`write_config(config)`, `write_provenance(provenance)`, `append_event(event)`,
`commit(status)`, and `is_resumable(input_fingerprint, config_fingerprint) -> bool`.

Consumes artifacts from all stages and emits atomic, checksummed files. A run is
complete only after labels, measurements, configuration, provenance, QC images,
and JSON log validate and an atomic completion marker is committed. Temporary
files never satisfy resume. TIFF label round-trips are checked for shape, dtype,
and maximum ID.

### `cellquant.viz`

Public API: `make_qc_figures(image, labels, output_dir, config) ->
dict[str, Path]`, `outline_slice(...)`, `orthogonal_view(...)`, and
`label_projection(...)`.

Consumes canonical images/labels and emits deterministic PNGs: mid-stack image
with outlines, calibrated orthogonal view, and max projection of labels.
Display normalization is explicit and affects visualization only. Label colors
derive from a fixed seed. Figures embed input/config fingerprints in metadata.

### `cellquant.verify`

Public API: `match_labels(reference, candidate, threshold) -> MatchResult`,
`compare_pair(...) -> ParityResult`, `compare_reference_set(...) -> DataFrame`,
and `render_parity_report(...) -> Path`.

Consumes same-shape ZYX integer label volumes. Matching uses maximum-cardinality,
maximum-IoU bipartite assignment. It reports every matched-label IoU, mean and
distribution, F1 at 0.50 and 0.75, reference/candidate counts and delta, and
volume-distribution differences. Shape or axis mismatch is an error, not a
coercion. The final report includes parity tables, distributions, Bland-Altman
volume analysis, resource comparisons, outlier overlays and written causes.

### `cellquant.harness`

Public API: `run_case(case, config, output_dir) -> HarnessResult`,
`showcase(module, crop, config, output_dir) -> ShowcaseResult`, and CLI commands
`cellquant harness`, `cellquant parity`, and `cellquant showcase`.

Runs the same core orchestration as batch/plugin. Each case writes a label volume,
JSON stage/resource log, and all three QC PNGs. Showcase mode uses a recorded crop
and stages exactly one package with its inputs/outputs. It never treats process
exit success as scientific success.

### `cellquant.batch`

Public API: `build_queue(inputs, output_root, config) -> list[BatchItem]` and
`run_batch(queue, config, cancel, events) -> BatchSummary`.

Consumes file paths and a validated config. It isolates every file in its own
run store, supports resume only after complete fingerprint validation, records
per-file logs, and never lets one exception stop later files. Queue ordering is
stable. Concurrency defaults to one GPU worker; I/O prefetch is bounded.

### `cellquant.plugin`

Public API is the npe2 manifest and magicgui widget factories. Widgets consume
napari Image/Labels layers with stable names and types. The dock exposes
single-image and batch modes. Batch mode embeds the Fiji-style survey: scan →
channel-layout assignment → auto-written/loaded run config → per-layout
`build_queue` / `run_batch`. Single-image mode loads the bundled sample config
automatically. File opening uses lazy Dask/Zarr-backed layers. All work
exceeding 100 ms runs through napari `thread_worker`, relays the shared event
stream as progress, and exposes a cancel button wired to the shared token.
Core functions never import napari or Qt.

### `cellquant.survey`

Public API: `survey_folder(...)`, `write_survey(...)`, `load_survey(...)`,
`read_assignments_csv(...)`, `with_assignments(...)`, and
`run_survey_batches(...)`.

Walks supported acquisitions with metadata-only inspection, clusters files by
channel count and normalized channel names, suggests nuclear-looking channels,
and runs one assigned batch queue per layout against a shared base config.

## Determinism

- Python, NumPy, Cellpose, and Torch RNGs are seeded from serialized config.
- Torch deterministic algorithms and cuDNN deterministic mode are requested and
  their effective status is logged; unsupported deterministic kernels fail the
  parity profile rather than silently relaxing the requirement.
- The exact model weights are pinned by SHA-256. A name without a verified hash
  may be used for exploration but cannot produce a parity-pass result.
- Input files use content SHA-256 plus size/mtime; outputs record code/package
  versions and canonical configuration. Re-running from those artifacts must be
  sufficient without UI state.

## Verification gates

The harness and parity checker are built before new production modules. Each
package must produce showcase artifacts. Critic runs use at least three reference
stacks selected from low, median, and high baseline label density.

- Score 10: mean matched IoU >= 0.95, F1@0.75 >= 0.95, count delta <= 1%.
- Score 8.5: mean matched IoU >= 0.90, F1@0.50 >= 0.95, count delta <= 3%,
  with explained, biologically defensible differences.
- Score 7: usable with a documented systematic bias.
- Score 5: qualitatively different segmentation.

Pass requires >= 8.5, no uncaught exception, no silent dtype/axis coercion, no
unlogged parameter default, reproducible config, and runtime <= 1.5 times the
reference workflow on identical hardware. Metrics, scores, failed rounds, and
ranked open issues persist in `docs/STATUS.json`; no absent measurement is
represented as passing.

Plugin acceptance is separate: scripted interactions capture screenshots,
confirm progress/cancel and stable layers, exercise lazy large-stack opening,
and record any main-thread stall over 100 ms.

## Measured performance budget

Budgets are baselines obtained by the harness on named hardware, never estimates.
Before a configuration may pass, the status file must contain, for each tested
stack and for both reference and candidate runs:

- peak host resident memory in bytes (process plus children),
- peak CUDA allocated and reserved memory in bytes, or explicit `not_available`,
- wall-clock seconds per stage and total,
- hardware/driver/CUDA/Torch/Cellpose identifiers.

The hard initial constraint is candidate wall time <= 1.5x the measured reference
on identical hardware. Peak RAM and VRAM limits remain `unmeasured` until the
first complete three-stack gauntlet; the measured maxima plus 10% headroom then
become the configuration's regression budgets. A 40 GB acquisition must open
lazily; full-volume host materialization is a logged failure of the plugin gate.

## Build waves and ownership

1. `io`, `persist`, `viz`, `harness`, and `verify` establish the test loop.
2. `preprocess`, `segment`, `postprocess`, and `measure` implement science stages.
3. `batch` and `plugin` expose the shared core.
4. Integration reproduces the full reference workflow and generates the report.

Builder agents own only their assigned module/test files. The integrator alone
owns shared types, configuration, orchestration, packaging, and this contract.
After each wave a read-only critic reruns the artifacts and records evidence.
