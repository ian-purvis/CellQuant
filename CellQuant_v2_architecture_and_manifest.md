For the engineer, I’d treat Sections 41–46 as the initial development contract and the preceding sections as the architectural/product requirements. The most important thing to enforce from day one is the separation of the analysis engine, recipe schema, and GUI; that decision will determine whether this remains maintainable once batch analysis and additional segmentation methods are added.

# Generic Multichannel Image Segmentation and Quantification Application

## 1. Purpose

Build a desktop scientific image-analysis application for multichannel microscopy/histology images.

The application should allow a researcher to:

1. Import one image or an experiment containing many images.
2. Assign arbitrary user-defined sample names and channel names.
3. Select any image channel as the source for object segmentation.
4. Segment objects using configurable algorithms.
5. Inspect and, when necessary, manually correct segmentation.
6. Measure arbitrary image channels within the segmented objects or derived measurement regions.
7. Define independent positivity/classification thresholds for measured channels.
8. Quantify single-marker and multi-marker coexpression at the object level.
9. Analyze images individually or run the same analysis automatically across an experiment.
10. Save the complete analysis as a reusable recipe.
11. Reproduce an analysis from the original images plus saved recipe and software version.
12. Export object-level, image-level, and experiment-level data.

The software should be biologically agnostic. It must not assume particular tissues, stains, channel names, markers, sample types, or experimental designs.

The conceptual model is:

**Images → Objects → Measurements → Classifications → Phenotypes/Combinations → Results**

---

# 2. Primary Design Principles

## 2.1 Generic

No biological names or assumptions should be hard-coded.

The application should operate using generic concepts:

- Experiment
- Image
- Sample
- Channel
- Object Set
- Measurement
- Classification
- Phenotype
- Analysis Recipe

Users provide meaningful names themselves.

Examples of valid user-defined channel names might include:

- Channel 1
- GFP
- DAPI
- Marker A
- CD31
- Reporter
- OTX2

The application should treat all of these identically.

---

## 2.2 Object-centric

Segmentation creates objects.

All downstream analysis should refer to those objects rather than directly treating pixel overlap as the primary unit of analysis.

Each object must receive a unique identifier within an image.

Example:

```text
Image: sample_001.tif

Object 1
Object 2
Object 3
...
```

Measurements and classifications are attached to these objects.

---

## 2.3 Segmentation and classification must be independent

Segmentation answers:

> Where are the objects?

Classification answers:

> Which properties does each object have?

Changing a marker positivity threshold must not require rerunning segmentation.

Likewise, changing how percentages are summarized must not require rerunning measurements.

The pipeline should therefore be modular:

```text
Image
  ↓
Segmentation
  ↓
Object Labels
  ↓
Measurements
  ↓
Classifications
  ↓
Phenotypes
  ↓
Summary Statistics
```

Intermediate stages should be cacheable.

---

## 2.4 Interactive and batch modes must use the same engine

The exact same analysis functions should support:

- interactive analysis of one image,
- sequential inspection of multiple images,
- automatic batch processing,
- command-line/headless processing in the future.

The GUI must not contain the core analysis logic.

---

## 2.5 Reproducibility is a first-class requirement

Every analysis result must be traceable to:

- input image,
- sample metadata,
- channel assignments,
- segmentation algorithm,
- segmentation parameters,
- model/version if applicable,
- measurement definitions,
- classification thresholds,
- software version,
- recipe version,
- manual edits,
- exclusions,
- processing timestamp.

If stochastic algorithms are used, the random seed must be recorded where applicable.

---

# 3. Recommended Application Architecture

The program should have three primary layers.

```text
┌───────────────────────────────────┐
│              GUI                  │
│                                   │
│ Experiment setup                  │
│ Analysis configuration            │
│ Interactive visualization         │
│ QC/review                         │
│ Results                           │
└────────────────┬──────────────────┘
                 │
                 ▼
┌───────────────────────────────────┐
│        Analysis Controller        │
│                                   │
│ Recipe validation                 │
│ Job scheduling                    │
│ Cache management                  │
│ Provenance tracking               │
│ Batch coordination                │
└────────────────┬──────────────────┘
                 │
                 ▼
┌───────────────────────────────────┐
│          Analysis Engine          │
│                                   │
│ Segmentation                      │
│ Measurement                       │
│ Classification                    │
│ Phenotype generation              │
│ Statistics                        │
│ Export                            │
└───────────────────────────────────┘
```

The scientific analysis engine should be usable independently of the GUI.

---

# 4. Recommended Technology Stack

Preferred initial stack:

- Python
- napari for image visualization
- Qt/PySide for custom application UI
- NumPy
- SciPy
- pandas
- scikit-image
- tifffile
- zarr / OME-Zarr where appropriate
- Dask where lazy/chunked processing becomes necessary
- StarDist and/or Cellpose as optional segmentation engines
- pydantic or equivalent for strongly typed recipe/configuration models
- pytest for automated testing

The application may initially run as a napari plugin, but the architecture should allow the user-facing experience to behave as a purpose-built application rather than exposing napari internals.

Napari should primarily provide:

- image rendering,
- zoom/pan,
- channel display,
- labels,
- overlays,
- manual object editing,
- viewport interaction.

Core scientific logic must not depend on napari layer state.

---

# 5. Core Domain Model

## 5.1 Experiment

An Experiment represents a collection of related images.

Fields:

```text
experiment_id
experiment_name
created_at
modified_at
recipe_id
images[]
metadata_schema
```

---

## 5.2 Image Record

Each imported image should have:

```text
image_id
source_path
filename
sample_name
include/exclude status
dimensions
number_of_channels
pixel_size_x
pixel_size_y
pixel_size_z
image_metadata
user_metadata
processing_status
```

Users must be able to add arbitrary metadata columns.

Examples:

```text
Treatment
Group
Animal
Replicate
Section
Timepoint
Genotype
Batch
Operator
```

The software should preserve these values without interpreting their biological meaning.

---

## 5.3 Channel

Each channel should have:

```text
channel_index
channel_name
display_name
display_settings
```

Channel names are user-defined.

The application must never assume that channel 0 is a nuclear stain or that any specific name has special meaning.

---

## 5.4 Object Set

An Object Set is the result of segmentation.

Examples:

```text
Objects
Nuclei
Cells
Particles
Puncta
Object Set 1
```

Fields:

```text
object_set_id
name
source_channel
segmentation_method
segmentation_parameters
label_image
creation_timestamp
```

MVP may support one primary Object Set per analysis recipe.

The architecture should allow multiple Object Sets later.

---

## 5.5 Measurement

A Measurement defines how a quantitative value is calculated for every object.

Fields:

```text
measurement_id
name
channel
measurement_region
statistic
background_correction
parameters
```

Example:

```text
channel: 2
region: object
statistic: mean
background_correction: none
```

---

## 5.6 Classification

A Classification converts a continuous measurement into a category.

MVP classification:

```text
measurement > threshold → positive
measurement <= threshold → negative
```

Fields:

```text
classification_id
name
source_measurement
method
threshold
positive_label
negative_label
```

The design must support future classification methods without changing the underlying data model.

Possible future methods:

- percentile-based
- control-derived
- Otsu
- Gaussian mixture
- trained classifier
- multi-threshold categories

---

# 6. Experiment Setup Workflow

## 6.1 Create Experiment

User selects:

**New Experiment**

The user may then:

- select one image,
- select multiple images,
- select a directory,
- drag/drop files.

---

## 6.2 Image Manifest

Display a table:

| Include | File | Sample Name | User Metadata... |
|---|---|---|---|

Users must be able to:

- edit sample names,
- add/remove metadata columns,
- paste values from spreadsheets,
- sort/filter table,
- exclude images without deleting them.

Future enhancement:

Import metadata from CSV.

---

## 6.3 Channel Mapping

Application detects channel count.

User defines names:

```text
Channel 0: [_______________]
Channel 1: [_______________]
Channel 2: [_______________]
Channel 3: [_______________]
```

Names should default to generic values:

```text
Channel 1
Channel 2
Channel 3
...
```

Channel mappings can be applied to the entire experiment.

If images have inconsistent channel structures, the application must alert the user.

---

# 7. Segmentation Module

## 7.1 Source Channel

User chooses:

```text
Segmentation Source
[ Channel ▼ ]
```

Any available channel may be selected.

---

## 7.2 Segmentation Method

Initial MVP should support at minimum:

### Classical segmentation

Suggested operations:

- Gaussian smoothing
- thresholding
- binary cleanup
- fill holes
- remove small objects
- distance transform
- watershed
- connected-component labeling

### One modern learned segmentation method

Preferred:

- StarDist OR
- Cellpose

The implementation should use an adapter interface so additional algorithms can be added later.

Example:

```python
class SegmentationBackend:
    def segment(image, parameters) -> LabelImage:
        ...
```

---

# 8. Segmentation UI

Main segmentation panel:

```text
OBJECT SEGMENTATION

Object Set Name:
[ Objects ]

Source Channel:
[ Channel 1 ▼ ]

Method:
[ StarDist ▼ ]

Parameters
------------------------
[method-specific controls]

Minimum object size:
[ value ]

Maximum object size:
[ optional ]

[Preview]
[Run]
```

Changing parameters and pressing Preview should only process:

- visible field of view, OR
- configurable crop/ROI,

for responsiveness.

The final Run command processes the full image.

---

# 9. Segmentation Visualization

When labels exist, display:

- object boundaries,
- translucent label fill,
- optionally object IDs.

User should be able to toggle:

```text
☑ Show image
☑ Show object boundaries
☐ Show object fills
☐ Show object IDs
```

Clicking an object should select it.

---

# 10. Manual Object Editing

MVP should support, if feasible:

- delete object,
- restore deleted object,
- paint/add object,
- erase object.

Later:

- split object,
- merge objects.

Manual changes must not overwrite the original automated segmentation.

Store:

```text
automated_labels
edit_operations
final_labels
```

Each edit operation should record:

```text
image_id
object_id
operation
timestamp
optional parameters
```

Undo/redo should be supported where practical.

---

# 11. Measurement Regions

Measurements must not be limited to the segmentation mask.

MVP should support:

### Object

Exact segmented object.

### Eroded Object

Object contracted inward by a configurable distance.

### Expanded Object

Object expanded outward by a configurable distance.

Neighboring expanded regions must not overlap ambiguously.

A nearest-object/Voronoi-style partition should be used where appropriate.

### Ring

Area surrounding the object between two distances.

Example:

```text
inner distance: 2 µm
outer distance: 5 µm
```

Useful for local background measurement.

---

# 12. Measurement Module

Users may add an arbitrary number of measurements.

Example UI:

```text
MEASUREMENTS

+ Add Measurement

Measurement 1
Channel:       [ Channel 2 ▼ ]
Region:        [ Object ▼ ]
Statistic:     [ Mean ▼ ]
Background:    [ None ▼ ]

Measurement 2
Channel:       [ Channel 3 ▼ ]
Region:        [ Expanded Object ▼ ]
Expansion:     [ 3.0 µm ]
Statistic:     [ Median ▼ ]
Background:    [ Local Ring ▼ ]
```

---

# 13. Supported Measurement Statistics

MVP should support:

- mean intensity
- median intensity
- minimum
- maximum
- standard deviation
- integrated intensity
- object area
- equivalent diameter
- centroid X
- centroid Y

If 3D support is included later:

- volume
- centroid Z
- surface area

Measurements must be calculated for every object.

---

# 14. Background Correction

MVP should support:

### None

Use raw object intensity.

### Global

Subtract a user-defined or automatically measured global image background.

### Local Ring

Measure surrounding ring and calculate:

```text
corrected_intensity =
object_intensity - local_background_intensity
```

The exact output measurement must identify whether it is raw or corrected.

---

# 15. Measurement Table

Every analyzed image should generate an object-level table.

Example:

| object_id | x | y | area | ch1_mean | ch2_mean | ch3_mean |
|---|---:|---:|---:|---:|---:|---:|

This object table is the canonical quantitative dataset.

Classifications should add fields rather than replace measurement values.

---

# 16. Classification Module

Each measurement may optionally be converted to a categorical classification.

Example:

```text
CLASSIFICATION

Source:
[ Channel 3 - Mean Intensity ▼ ]

Classification Name:
[ Marker A Positive ]

Threshold:
[ 425 ]

Rule:
value > threshold

[Show Histogram]
```

---

# 17. Interactive Threshold UI

When configuring classification, show:

- distribution histogram,
- threshold line/slider,
- count positive,
- count negative,
- percentage positive.

Dragging the threshold must immediately update:

- classifications,
- displayed object colors,
- counts.

Example:

```text
Positive: 372
Negative: 581
Positive: 39.0%
```

This update should occur without rerunning segmentation or measurements.

---

# 18. Visual Classification Overlay

User should be able to select a classification and recolor objects.

Example:

```text
Display objects by:
[ Marker A Positive ▼ ]
```

Objects should visually distinguish:

- positive,
- negative,
- excluded.

The exact colors should be configurable later.

---

# 19. Multi-marker Phenotype Engine

If multiple binary classifications exist, automatically generate combinations.

For classifications:

```text
A
B
C
```

The engine should be able to calculate:

```text
A+
B+
C+

A+B+
A+C+
B+C+

A+B+C+
```

and mutually exclusive combinations:

```text
A-B-C-
A+B-C-
A-B+C-
A-B-C+
A+B+C-
A+B-C+
A-B+C+
A+B+C+
```

Do not require the user to create these manually.

---

# 20. Denominator Selection

When reporting percentages, users must be able to specify the denominator.

Examples:

```text
A+ / all objects

A+B+ / A+

A+B+C+ / A+

A+B+ / B+
```

The results engine should calculate percentages from object classifications rather than creating duplicate measurements.

---

# 21. Results Builder

Users should be able to define desired summary outputs.

Example:

```text
RESULTS

☑ Total objects
☑ A+ / all objects
☑ B+ / all objects
☑ A+B+ / A+
☑ A+B+C+ / A+
```

A complete phenotype table should also be available automatically.

---

# 22. Review Modes

The application must support three ways of working.

## 22.1 Interactive Single-image Mode

Workflow:

```text
Open image
→ configure
→ segment
→ inspect
→ measure
→ classify
→ review
→ save
```

---

## 22.2 Sequential Experiment Review

User can navigate:

```text
Previous Image
Next Image
```

The same recipe is applied across images.

User may inspect or manually modify each image.

Image status:

```text
Not analyzed
Analyzed
Reviewed
Approved
Excluded
Needs attention
```

---

## 22.3 Automated Batch Mode

User selects:

```text
Run Current Image
Run Selected Images
Run Entire Experiment
```

Entire experiment uses the same saved recipe unless explicitly overridden.

Batch processing must not require napari interaction for each image.

---

# 23. Batch Execution

Batch runner should:

1. load image,
2. validate channel structure,
3. run segmentation,
4. calculate measurements,
5. apply classifications,
6. calculate requested summaries,
7. save intermediate results,
8. record QC metrics,
9. continue to next image.

One failed image should not terminate the entire batch.

Each image should return:

```text
Success
Warning
Failure
```

with a reason.

---

# 24. Batch Progress UI

Example:

```text
Experiment Processing

Image 38 / 84

████████████░░░░░░░ 45%

Completed: 37
Warnings:   2
Failed:     1

[Pause]
[Cancel]
```

Errors should be logged.

---

# 25. Quality Control

Automatically calculate useful QC values per image.

MVP examples:

- number of detected objects,
- median object area,
- percentage of objects touching border,
- percentage excluded by size,
- segmentation failure,
- unusually low/high object count,
- image dimensions mismatch,
- missing expected channels.

Optional classification QC:

- percentage positive for each classification,
- objects near threshold.

QC should not automatically alter scientific results unless specified by the recipe.

It should flag images for review.

---

# 26. Review Queue

After batch processing:

```text
84 images processed

Passed QC:           77
Review recommended:   6
Failed:               1
```

Allow:

```text
Review flagged images
Review all images
Review failed images
```

---

# 27. Analysis Recipe

Every scientific configuration should be serializable.

Suggested format:

YAML or JSON.

Example:

```yaml
recipe_version: 1

object_set:
  name: Objects
  segmentation_channel: 0
  algorithm: stardist
  parameters:
    model: default_model
    probability_threshold: 0.50
    nms_threshold: 0.40
    min_area_um2: 20

measurements:
  - id: measurement_1
    channel: 1
    region:
      type: object
    statistic: mean
    background:
      type: none

  - id: measurement_2
    channel: 2
    region:
      type: expanded_object
      distance_um: 3
    statistic: median
    background:
      type: local_ring
      inner_um: 2
      outer_um: 5

classifications:
  - id: classification_1
    name: A_positive
    measurement: measurement_1
    method: threshold
    threshold: 425

  - id: classification_2
    name: B_positive
    measurement: measurement_2
    method: threshold
    threshold: 680

reports:
  - numerator: A_positive
    denominator: all_objects

  - numerator: A_positive AND B_positive
    denominator: A_positive
```

Users should manipulate this through the GUI.

Direct recipe editing is not required for MVP.

---

# 28. Recipe Operations

Users should be able to:

- Save Recipe
- Save Recipe As
- Load Recipe
- Duplicate Recipe
- Export Recipe
- Import Recipe

Each recipe should have:

```text
recipe_id
recipe_name
recipe_version
created_at
modified_at
software_version
```

The recipe should not contain image-specific manual edits.

Those belong to the analysis run.

---

# 29. Analysis Run Record

Every execution of a recipe against images creates an Analysis Run.

Fields:

```text
run_id
experiment_id
recipe_id
recipe_snapshot
software_version
start_timestamp
completion_timestamp
input_files
input_hashes
processing_status
manual_edits
warnings
errors
```

The exact recipe used must be copied/snapshotted into the run rather than referenced only by mutable filename.

---

# 30. Provenance

Every result should be traceable.

Store at minimum:

```text
software version
analysis engine version
recipe snapshot
recipe hash/checksum
input filename
input checksum where practical
sample name
sample metadata
channel mappings
pixel dimensions
segmentation method
segmentation parameters
model identifier/version
measurement definitions
classification thresholds
manual edits
excluded objects
excluded images
timestamps
```

---

# 31. Caching

Processing stages should be cached independently.

Suggested dependency structure:

```text
Segmentation
    ↓
Measurement
    ↓
Classification
    ↓
Summary
```

Changing a classification threshold:

```text
do NOT rerun segmentation
do NOT rerun measurement
rerun classification
rerun summary
```

Changing the denominator:

```text
rerun summary only
```

Changing the segmentation parameters:

```text
rerun everything downstream
```

Cache keys should depend on:

```text
input image
relevant recipe section
software/model version
```

---

# 32. Data Storage Structure

Suggested experiment directory:

```text
Experiment/
│
├── experiment.json
│
├── recipes/
│   ├── recipe_001.yaml
│   └── recipe_002.yaml
│
├── runs/
│   └── run_001/
│       ├── run.json
│       ├── recipe_snapshot.yaml
│       ├── logs/
│       ├── labels/
│       ├── measurements/
│       ├── classifications/
│       └── summaries/
│
└── exports/
```

Do not modify source image files.

---

# 33. Export Requirements

## Object-level export

CSV and preferably Parquet.

Each row = one object.

Required metadata columns should include:

```text
experiment_id
run_id
sample_name
image_id
filename
object_set
object_id
centroid_x
centroid_y
area
```

Followed by measurements and classifications.

Example:

```text
channel_1_mean
channel_2_mean
channel_3_median
A_positive
B_positive
phenotype
```

---

## Image-level export

One row per image.

Example:

```text
sample_name
filename
total_objects
A_positive_count
A_positive_percent
A_B_positive_count
A_B_positive_percent_of_A
QC_status
```

---

## Experiment-level export

Summaries grouped by user-selected metadata.

This may be a post-MVP feature.

MVP should at least produce image-level and object-level outputs suitable for downstream analysis in:

- Excel,
- R,
- Python,
- Prism.

---

# 34. User Interface Structure

The main application should have five primary sections.

```text
1. EXPERIMENT
2. OBJECTS
3. MEASUREMENTS
4. REVIEW
5. RESULTS
```

Avoid exposing the user to implementation details whenever possible.

---

# 35. Proposed Main Window

```text
┌──────────────────────────────────────────────────────────────┐
│ Experiment: Example Experiment              Image 12 / 84    │
├──────────────────┬───────────────────────────────────────────┤
│ EXPERIMENT       │                                           │
│ ✓ Images         │                                           │
│ ✓ Channels       │                                           │
│                  │                                           │
│ OBJECTS          │              IMAGE VIEW                   │
│ ✓ Segmentation   │                                           │
│                  │                                           │
│ MEASUREMENTS     │                                           │
│ ✓ Measurement 1  │                                           │
│ ✓ Measurement 2  │                                           │
│                  │                                           │
│ REVIEW           │                                           │
│ → Current Image  │                                           │
│                  │                                           │
│ RESULTS          │                                           │
│                  │                                           │
├──────────────────┴───────────────────────────────────────────┤
│ Previous   Run Current   Run Selected   Run Experiment  Next │
└──────────────────────────────────────────────────────────────┘
```

---

# 36. Hide Internal Processing Layers

The user should not see dozens of temporary napari layers such as:

```text
threshold_mask
smoothed
temp_binary
distance_transform
expanded_labels
background_ring
```

These may exist internally but should be hidden or managed automatically.

Routine users should primarily see:

```text
Image channels
Objects
Classification overlays
```

---

# 37. Advanced Settings

Keep the primary workflow simple.

Complex parameters should be placed behind:

```text
Advanced
```

Examples:

- morphological cleanup radius,
- watershed compactness,
- inference tile overlap,
- GPU configuration,
- local background details.

Preserve them in recipes regardless of UI visibility.

---

# 38. Units

Where image calibration exists, use physical units:

```text
µm
µm²
µm³
```

Do not silently assume pixel size.

If metadata lacks calibration:

- operate in pixels,
- prominently indicate this,
- allow manual calibration.

---

# 39. Logging

Every batch run should create a human-readable log.

Include:

```text
timestamp
image
processing stage
warnings
errors
execution information
```

Do not expose debug traceback information as the primary user-facing error message.

Detailed traceback may be written to a developer log.

---

# 40. Error Handling

Examples:

### Missing image

```text
File could not be opened.
```

### Channel mismatch

```text
This image has 3 channels, but the experiment expects 4.
```

### Segmentation failure

```text
Segmentation returned no objects.
```

### Corrupted analysis cache

The application should regenerate the affected stage rather than corrupting the experiment.

---

# 41. MVP Scope

The first usable release should include:

### Experiment management
- Create experiment
- Import one or multiple images
- User-defined sample names
- User-defined channel names
- Optional metadata columns

### Visualization
- Multichannel image viewer
- Contrast controls
- segmentation overlays
- click/select objects

### Segmentation
- Select segmentation channel
- one classical method
- one AI-based method
- preview segmentation
- full-image segmentation
- min/max object size filtering

### Measurements
- arbitrary measurement channels
- object region
- expanded object region
- mean/median/max/integrated intensity
- area and centroid

### Classification
- arbitrary classification names
- one threshold per classification
- histogram
- interactive threshold
- positive/negative object overlay

### Coexpression
- combinations of multiple binary classifications
- user-selectable denominators

### Batch
- current image
- selected images
- entire experiment
- progress display
- continue after image-level failures

### Review
- sequential image navigation
- image status
- delete obvious erroneous objects
- approve image

### Reproducibility
- saved recipe
- recipe snapshot per run
- software version
- parameters
- manual edit history

### Export
- object-level CSV
- image-level CSV
- analysis recipe
- provenance/run metadata

---

# 42. Explicit MVP Non-goals

Do not delay the MVP for:

- full Imaris feature parity,
- 3D rendering,
- tracking objects through time,
- complex machine-learning phenotype classifiers,
- cloud processing,
- collaborative multi-user editing,
- whole-slide pathology support,
- registration across images,
- multiple interacting object sets,
- sophisticated spatial statistics,
- database server,
- custom neural-network training,
- publication-quality report generation.

Design the architecture so these could be added later.

---

# 43. Future Extensions

Potential subsequent features:

## Multiple Object Sets

Example:

```text
Object Set 1 → cells
Object Set 2 → vessels
Object Set 3 → puncta
```

Then calculate object-object relationships.

---

## Spatial measurements

- nearest-neighbor distance
- distance to another object set
- object density
- neighborhood composition
- radial distributions

---

## Additional segmentation methods

Plugin-style registration:

```python
register_segmentation_backend(...)
```

Possible methods:

- Cellpose
- StarDist
- watershed
- threshold
- SAM-derived methods
- custom laboratory models

---

## Classification methods

- controls-based thresholding
- Gaussian mixture
- percentile
- auto-thresholding
- supervised classification

---

## Whole-slide support

Use pyramidal and tiled image formats.

---

## Command-line mode

Example:

```text
image-analyzer run \
    --experiment experiment.json \
    --recipe recipe.yaml
```

This is desirable for reproducibility and high-throughput processing.

---

# 44. Testing Requirements

Scientific correctness should be covered by automated tests.

## Unit tests

Examples:

- region measurements match known values,
- expanded masks have expected geometry,
- thresholding produces expected classifications,
- phenotype combinations are calculated correctly,
- percentages use correct denominator.

---

## Synthetic-image tests

Create known test images where expected segmentation and measurements are predetermined.

For example:

```text
10 objects
5 classified A+
3 classified B+
2 classified A+B+
```

Verify outputs exactly.

---

## Regression tests

Store representative example images and expected outputs.

When analysis code changes, detect unintended changes.

---

## Recipe reproducibility test

Given:

```text
Image X
Recipe Y
Software version Z
```

the analysis should produce the expected output table.

---

# 45. Acceptance Criteria for Initial Release

The MVP is considered successful when a non-programmer can:

1. Create an experiment containing multiple multichannel images.
2. Give the images arbitrary sample names.
3. Give channels arbitrary names.
4. Choose any channel for segmentation.
5. Preview and run segmentation.
6. Visually confirm segmented objects.
7. Measure at least three arbitrary channels.
8. Set independent positivity thresholds using an interactive histogram.
9. Visually inspect positive and negative classifications.
10. Calculate double- and triple-positive object populations.
11. Choose the denominator for reported percentages.
12. Save all settings as a recipe.
13. Apply that recipe to a second image without re-entering parameters.
14. Run the recipe automatically across an entire experiment.
15. Review individual processed images afterward.
16. Make and record limited manual segmentation corrections.
17. Export one row per object.
18. Export one summary row per image.
19. Reopen the experiment and recover the previous analysis state.
20. Determine exactly which settings produced any exported result.

---

# 46. Suggested Engineering Milestones

## Milestone 1 — Core engine

Implement without sophisticated GUI:

```text
load image
segment selected channel
generate labels
measure selected channels
classify objects
calculate combinations
export table
```

Deliverable:

A Python API capable of performing the complete analysis on one image.

---

## Milestone 2 — Recipe system

Implement:

- typed recipe schema,
- validation,
- serialization,
- loading,
- versioning.

Deliverable:

The same image + recipe reproduces the same analysis.

---

## Milestone 3 — Basic napari application

Implement:

- experiment window,
- image viewer,
- segmentation controls,
- measurement controls,
- classification controls.

Deliverable:

Single-image interactive analysis without direct coding.

---

## Milestone 4 — Interactive QC

Implement:

- object overlays,
- object selection,
- histogram thresholding,
- live classification recoloring,
- limited manual editing.

Deliverable:

A scientist can visually validate the analysis.

---

## Milestone 5 — Experiment/batch processing

Implement:

- image manifest,
- experiment metadata,
- run current/selected/all,
- progress reporting,
- error isolation,
- per-image processing state.

Deliverable:

A recipe can process an entire experiment automatically.

---

## Milestone 6 — Provenance and caching

Implement:

- stage-specific caches,
- run records,
- recipe snapshots,
- software/model version tracking,
- manual edit logs.

Deliverable:

Analyses are auditable and do not unnecessarily recompute unchanged stages.

---

## Milestone 7 — Results and export

Implement:

- object table,
- image summary,
- phenotype summaries,
- configurable denominators,
- CSV/Parquet export.

Deliverable:

Outputs can be directly analyzed in R, Python, Excel, or Prism.

---

# 47. Recommended Internal API

The scientific core should roughly expose functions of this form:

```python
load_image(...)
segment_objects(...)
filter_objects(...)
create_measurement_region(...)
measure_objects(...)
classify_objects(...)
generate_phenotypes(...)
summarize_image(...)
process_image(...)
process_experiment(...)
```

Higher-level example:

```python
result = process_image(
    image=image,
    recipe=recipe,
)
```

Where `result` contains:

```text
labels
measurements
classifications
phenotypes
summary
qc
provenance
```

This API should not require a napari viewer.

---

# 48. Definition of the Canonical Data

The canonical scientific result should be the object table.

Example:

```text
experiment_id
run_id
sample_name
image_id
object_id
centroid_x
centroid_y
area

measurement_1
measurement_2
measurement_3

classification_A
classification_B
classification_C

phenotype
excluded
manual_edit_status
```

Summary percentages should be calculated from this table.

This allows all experiment-level summaries to be regenerated later without rerunning image analysis.

---

# 49. Critical Engineering Constraints

Do not:

- hard-code biological channel names,
- hard-code a fixed number of channels,
- hard-code three classifications,
- couple segmentation to marker classification,
- recompute segmentation when only classification changes,
- store critical parameters only in GUI state,
- make napari layers the authoritative scientific data model,
- overwrite original segmentation after manual editing,
- modify original source images,
- rely only on summary statistics.

Do:

- preserve object-level data,
- preserve recipes,
- preserve manual edits,
- version configuration schemas,
- keep analysis independent from visualization,
- expose algorithms through adapters/interfaces,
- make batch and interactive processing use the same code path.

---

# 50. Product Goal

The target experience is:

> A scientist with no programming experience can load multichannel images, define what the channels represent, select how objects are segmented, specify how arbitrary channels are measured and classified, inspect the results visually, and either analyze images individually or reproducibly apply the exact same analysis across an experiment.

The software should provide the ease of an interactive commercial image-analysis application while retaining the transparency, flexibility, reproducibility, and extensibility of an open scientific analysis pipeline.

The guiding principle should remain:

**Segment once → measure everything required → classify flexibly → preserve every decision → reproduce the analysis exactly.**