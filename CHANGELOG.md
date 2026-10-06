# Changelog

## Unreleased

- **Crop to the region of interest** (step 2, off by default, 2D analyses): finds the area holding the positive cells on the ticked channels (by default the reports' denominator, e.g. the reporter), grows it by a margin (50 µm), and finds objects only inside rectangles around it. Brightness scaling and cutoffs come from the whole image; an image whose region covers more than 70% of it is analyzed in full. Objects touching a crop edge are flagged (`at_crop_edge`), review outlines the rectangles, and the Plan's right-click menu has **Use the full image** per image. Restored from unpublished local work.
- **Step 4**: the marker cutoff is a slider with an editable number. Positive objects are green and negative ones magenta by default (a pair that stays distinct with red-green color blindness); **Positive color** / **Negative color** change them.
- **Clicking an object selects it** for **Delete object** / **Restore object**. Nothing is deleted until an object has been clicked (before, Delete object could remove object 1).
- **Problems appear in a red box** above the steps: failed runs (with each failed image and its reason), images that cannot be shown or opened, and settings that cannot be loaded. A run that stops on an unexpected error no longer leaves the window locked; the details go to `cellquant_developer.log`.
- **Plain words throughout**: menus such as "Cellpose (AI model)" and "Automatic (Otsu)"; *cutoff* rather than threshold; *settings* rather than recipe; the Measurements and Markers tables show channel names and plain statistics; result rows are chosen from menus (**Count** … **among** …). Runs are summarized the same way everywhere ("7 finished, 1 needs a look, 1 failed"), and step 1's Status column tells failed images from ones that need a look.
- **Channel layers** are labeled from each image's own file (*Channel 1 = Far Red*), and step 1 shows that image's channel order; contrast is remembered per channel, matched by name.
- **Step 1**: Browse picks the highlighted folder on Windows and confirms it; edits save automatically (no Save button); Add images/Add folder, Include shown and metadata columns are under **Advanced**.
- **Step 2**: **Typical nucleus diameter (µm)** (default 6) sets Cellpose's size and a 5 µm² debris floor; Z-stack mode stays usable after a run.
- **Set sizes (µm)** can set the pixel size for this image and every image without one (the default), this image only, or every included image.
- **Use GPU** is ticked automatically when a usable NVIDIA GPU is found and the settings do not say otherwise. The GPU banner names a GPU that PyTorch cannot use and says how to fix it.
- **Navigation**: Previous / Next stop at the first and last image, the bottom bar says when they go through only failed images or images that need a look, and an experiment with no included images says so.
- Step 5's advanced section no longer repeats the run and export buttons; **Run all images** says "(this analysis)" when there are several analyses.
- ND2 files on OneDrive that are not downloaded yet are read through an ordinary open file.
- `cellquant run` has help text and a plain summary.
- New `docs/DEVELOPERS.md` and `tests/README.md`; `docs/STATUS.md` starts with the known limitations.

## 2.1.0 (2026-09-30)

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
