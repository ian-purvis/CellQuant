# Changelog

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
- Test images: synthetic retina z-stacks with known answers (`python -m cellquant.synthetic_retina`).
- Windows installer with one environment per Cellpose engine.
- MIT license, citation file.
