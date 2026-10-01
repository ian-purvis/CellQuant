# Changelog

## 2.1.0

- **Several analyses of the same images.** An experiment can hold several analyses, each with its own settings,
  results, deleted objects and approvals: for example one per channel that objects are found in, or two
  segmentation methods. The **Analysis** list at the top of the panel chooses, adds (**New analysis…**,
  **One per channel…**), renames and removes them. **Run all analyses** runs every included image with each
  analysis in turn (with Pause and Cancel); **Export all analyses…** saves each in its own folder plus
  `all_analyses_image_summary.csv`. Experiments made with 2.0.0 open with their settings as the first analysis.
- **The Plan** (button **Plan…**): a dock listing every image grouped by channel layout, by folder, or both,
  in a grid (images × analyses) or a tree (image ▸ analyses). Tick which images each analysis runs, one cell,
  a group, an analysis or everything at once; choose the channel to find objects in for single images; see
  each image's status per analysis; double-click to open an image with an analysis. **Run all analyses** runs
  the plan.
- **Other channel layouts**: an image whose file stores the channels in another order is segmented and
  measured in the channels of the same names; images without such a channel are marked for checking.
- **Duplicate** (advanced, step 5) now makes a new analysis and keeps the old one and its results.

## 2.0.0 (2026-09-29)

CellQuant 2 is a rewrite of the original CellQuant. There was no 1.x release: version 1 ended at 0.4.0a3,
which stays available on the `v1` branch and at tag `v0.4.0a3`. Settings files and results from version 1 do
not open in version 2.

- One guided napari window: images, finding nuclei, markers, checking, results, with a success check per step.
- Segmentation: classical thresholding, Cellpose 3 and Cellpose-SAM; 2D (max projection or one slice) and 3D
  (linked slices or whole volume) analysis of z-stacks; ND2 and TIFF input, including folders of retinas.
- Markers: positive by mean brightness, or by the percent of a nucleus whose pixels are at or above a pixel
  level (the `positive_fraction` rule of version 1); reports such as (Fluor+ OTX2+) / Fluor+.
- Review: delete, restore and draw objects, undo, approve; results are re-measured without segmenting again.
- Experiments: relocatable folders, saved runs, settings fingerprints, exports for Excel, Prism or R.
- HPC prep: packages for a Slurm GPU cluster (CU Boulder Alpine), a worker with checksums and resume, and import
  of the results as a new experiment.
- Sweeps: compare segmentation methods, Z modes and thresholds across images, scored against hand counts.
- Windows installer with one environment per Cellpose engine.
- MIT license, citation file.
