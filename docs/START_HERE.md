# Start here: CellQuant without coding

CellQuant finds nuclei (or cells) in fluorescence microscope images and counts how many are positive for each marker, for one image or a whole experiment. You check every result by eye in the napari window.

**CellQuant does not decide whether a result is biologically right.** Check the outlines and the positive calls against your experiment, and keep your original images untouched.

This guide is for Windows. If you have never used CellQuant, do the practice run first (about 15 minutes). It has a known answer, so you can tell your settings work.

## Install and open

1. Install **Miniforge** (recommended) or Miniconda if the computer does not have conda. Ask your computer administrator if you cannot install software. You do not need to write code.
2. In the CellQuant folder, double-click **`Install CellQuant.bat`**. It checks the computer and recommends a Cellpose engine:
   - **Cellpose-SAM (Cellpose 4)**: most accurate. Best with an NVIDIA GPU.
   - **Classic Cellpose (Cellpose 3)**: lighter. Faster on computers without a GPU.

   Press Enter to accept each suggested answer. Installing both engines (the default) lets you choose later. Leave the window open until it says **Install finished**. It can take 10 to 30 minutes.
3. Double-click **`Open CellQuant.bat`**. If both engines are installed, it asks which one; press Enter for the recommended one. The napari window can take a minute to appear. Keep the black window open while you work.

**Success check:** a napari window opens with a **CellQuant** panel on the right. The panel's first tab is **Start**.

If the install fails, it opens `install_last.log` in Notepad. See [When something looks wrong](#when-something-looks-wrong).

## What to click (the short version)

The CellQuant panel has a **Start** tab and five numbered tabs. Work from left to right. Every tab says what to do at the top, how to tell it worked at the bottom, and has a **Next →** button. If Next is greyed out, the line beside it says why.

1. **Start → Try practice images** (first time) or **New experiment…**
2. **1 Images**: add images and name the channels.
3. **2 Find objects**: pick the nuclear channel, **Preview**, then **Run**.
4. **3 Markers**: tick the marker channels, **Set up markers**.
5. **4 Check**: drag the **Cutoff** slider until the green (positive) objects look right, then **Approve**.
6. **5 Results**: **Run all images**, then **Export results…**

A ✓ appears on each tab as you finish it. The **Start** tab shows your progress and a **Continue** button that takes you to the next unfinished step.

The bar at the bottom of the window is shared by all steps. It shows which image is on screen (**◀ Previous image**, **Next image ▶**), the units, progress, and messages. Hover over any control to see what it does.

---

## Practice run (do this first)

1. On the **Start** tab, click **Try practice images** and choose a folder, for example Documents. CellQuant makes a folder called *CellQuant practice* with three images (a nuclear stain and two markers) and opens it.
2. Follow the steps below. The practice images have 40 nuclei each. Their expected answers are in *CellQuant practice/README.txt*:

   | Image | Marker A+ | Marker B+ | Both |
   |---|---|---|---|
   | practice_1.tif | 10 (25%) | 20 (50%) | 5 |
   | practice_2.tif | 20 (50%) | 10 (25%) | 5 |
   | practice_3.tif | 30 (75%) | 20 (50%) | 15 |

**Success check:** after step 5, your numbers match the table.

---

## Step 1: Images

1. Click **New experiment**. Give the experiment a name and choose two folders:
   - **Image folder**: the folder that holds your images. CellQuant only reads it; your images are never changed.
   - **Look for**: tick **ND2 files**, **TIFF files**, or both. Only the ticked types are taken from the folder; the yellow note says how many of the other type were left alone.
   - **Results folder**: where CellQuant saves its settings, results and exports. It starts as a new folder beside your images, for example *E14.5_E17.5 - CellQuant results*. Click **Browse…** to choose somewhere else. If you choose the image folder itself (or a folder inside it), CellQuant asks you to confirm.
2. CellQuant finds every file of the chosen types in the image folder and in all its subfolders. **Add folder** and **Add images** add more later; the **Add folder looks for** boxes above the list choose ND2, TIFF or both.
3. **Check the list before going on.** Each image is shown by its path inside the folder you chose, for example *mCherry cont/Retina 2/image.nd2*, so files with the same name in different folders are told apart. Hover over a path to see the full location. The list also shows each image's number of **Slices**, its **Channels**, **µm/pixel** and **Objective**. Subfolder names are copied into **Folder 1**, **Folder 2**, … columns, which you can use to group results.
4. Read the yellow notes above the list. They say how many images were found, which folders had none, and anything to watch for, such as images taken at different magnifications or Z-stacks.
5. Under the table, **name each channel** after its stain, for example *DAPI*, *OTX2*, *VSX2*. CellQuant starts with the names stored in the file (such as *Green*, *Red*, *Far Red*). These names appear in every result.
6. If **µm/pixel** says *not set*, enter the pixel size (from the microscope software's image properties) and click **Set sizes (µm)**. Without it, sizes are in pixels. For Z-stacks, **Z step** is the distance between slices; ND2 files include it (the **Slices** column shows, for example, *7 × 1.5 µm*). 3D volumes need it.
7. Choose which images to analyze. Everything listed is included at first. Untick **Include** to leave one image out, or work on several at once:
   - Narrow the list with **Show ND2 only** / **Show TIFF only** and the filter box (for example *Retina 2* or *Control*), then click **Include shown** or **Leave out shown**.
   - Or select rows (click, Ctrl-click, Shift-click) and click **Include only selected**.

   The line above the list says how many images are included. Left-out images are never deleted, are skipped by **Next image ▶**, and are not analyzed by **Run all images**. Tick them again at any time.
8. Optional: edit **Sample name**, or add columns such as *Genotype* or *Age* (you can paste from Excel).

**Success check:** every image you expect is listed once, in the right folder; the ones you want are included; and every channel has a real name.

Channels are shown in the colors saved in the file by the microscope software (for example green, red and magenta for an AXR ND2). A file with no saved colors is shown in gray. CellQuant never assigns colors of its own: each image uses its own file's colors in its own channel order, even when an analysis finds a channel by name in a file with another channel layout. (The green and gray fills in step 4 color the objects, not the channels.)

## Step 2: Find objects

1. If your images are Z-stacks, choose how to handle them under **Z-stacks**:
   - **2D: max projection**: the brightest value through all slices. Fast, but nuclei at different depths can merge into one.
   - **2D: one slice**: misses nuclei outside that slice.
   - **3D: link slices**: finds nuclei in every slice, then joins outlines that overlap in neighbouring slices into one 3D nucleus. Nuclei stacked in depth are counted separately.
   - **3D: whole volume**: segments the stack at once. Slow; it helps only when slices are close together (Z step no more than about twice the pixel size) and there are many of them.

   Each option shows an estimated time per image on this computer. The blue box recommends one based on the computer's GPU and memory and on your images; click **Use recommended** to choose it. Every option stays available. After the first run, the times are measured instead of estimated. The bottom bar shows what is analyzed, for example *3D, 7 slices linked slice by slice*.

   For 3D, **Link overlap** sets how much an outline must overlap the next slice's to be the same nucleus (0.25 is Cellpose's usual value; lower if nuclei split across slices, higher if stacked nuclei join). **Brightness: Whole stack** scales every slice the same way, so a dim top or bottom slice still links correctly. **Minimum slices** removes objects found in fewer slices (1 keeps all).
2. Set **Source channel** to the nuclear stain (for example DAPI). Without a nuclear stain, use the channel that marks the cells you want to count.
3. Choose a **Method**:
   - **classical**: fast, no GPU needed. Good for well separated, evenly bright nuclei.
   - **cellpose**: a trained model. Better for crowded or uneven nuclei. The **Advanced** box shows which Cellpose engine is running and whether a GPU is in use.
4. Click **Preview** to try the settings on the area you are looking at. Nothing is saved.
5. Click **Run** (or **Run this image** at the bottom). The bar at the bottom shows each step, for example *Finding objects: slice 4 of 7*. Run buttons are greyed out until it finishes. **Cancel** stops after the current step; nothing from the unfinished image is saved. (Cellpose's whole-volume 3D step cannot be stopped part-way; Cancel takes effect when it ends.)
   To try other settings, change them and click **Run** again. There is no need to restart CellQuant or go back.
6. Zoom in and check several areas. In 3D, drag the slice slider under the image to check every slice.

**Success check:** outlines sit on the nuclei. Few are missed, merged (two nuclei in one outline), or split (one nucleus in two outlines).

If it is not right:
- Merged nuclei: tick **Watershed** (classical), or use cellpose.
- Debris counted as nuclei: set **Minimum object size**.
- Faint nuclei missed: lower **Cell probability threshold** (cellpose), or use a manual threshold (classical).

## Step 3: Markers

1. Tick the channels you want to count. The channel used to find objects is not listed.
2. Under **A cell is positive when**, choose how a cell is called positive:
   - **its mean brightness is above a cutoff** (the usual choice): one number per cell, compared with a cutoff.
   - **enough of its pixels are bright**: each pixel of the cell is compared with a *pixel level*, and the cell is positive when at least the **Minimum percent of the cell** (for example 30%) is at or above that level. Useful when staining is patchy or covers only part of a nucleus. This is the "positive fraction" rule of CellQuant v1.
3. Click **Set up markers**.

CellQuant measures each marker inside every object and calls each object positive or negative. It picks a starting cutoff (or, for the percent rule, a starting pixel level) automatically and moves you to step 4.

**Success check:** step 4 opens with objects colored green (positive) and gray (negative).

*Advanced:* **Show all settings** lets you measure other statistics (median, total, percent of pixels at or above a level), choose whether a value equal to the cutoff is positive (**at least**) or negative (**above**), measure a ring around each object, subtract background, or set cutoffs by hand.

## Step 4: Check

1. Choose a marker under **Display objects by**. Objects glow green when positive and red when negative. **Positive colour** and **Negative colour** change the colours (remembered on this computer); **Default colours** puts green and red back.
2. Drag the **Cutoff** slider, or type a number beside it. The slider runs from the dimmest to the brightest object for this marker; the objects recolor and the counts below change as you drag. The same cutoff is used for every image, so choose one that works across your images, not just this one.
   With the percent rule, the slider is the minimum percent of each cell's pixels that must pass (0-100%). To change the pixel level, type it under **Percent-of-cell rule** and click **Apply pixel level**: this image is measured again (objects and your edits are kept). Tick **at most** to ignore pixels brighter than a second level, such as saturated spots. The line above the counts says the rule in words, for example *at least 30% of pixels ≥ 1200*.
3. To remove something that is not a real object: click it in the image, then click **Delete object**. **Restore object** and **Undo** reverse it.
4. When the image looks right, click **Approve**. Use **Next image ▶** at the bottom to check other images.

**Success check:** the green objects are the ones you would call positive by eye.

Tips:
- Compare with a negative control image if you have one: its objects should be red (negative).
- An object that could not be measured is left out of every count, and the counts say how many.

## Step 5: Results

1. Click **Run all images** (included images only), or select rows in step 1 and click **Run selected images**. The bar at the bottom shows the image and step, for example *Image 3 of 9: Finding objects: slice 2 of 7*. **Pause** waits after the current step; **Cancel** stops after it, keeping the images already finished. One image failing does not stop the others. To re-run with new settings, change them and click **Run all images** again. With several analyses (see below), **Run all analyses** runs each of them.
2. Click **Export results…** and choose a folder.

The **Results** box shows the numbers for the image on screen, for example *Marker A+ among all objects: 10 of 40 (25.0%)*.

**Success check:** the export folder contains:

| File | What it holds |
|---|---|
| `objects.csv` | One row per object: its position, size, each marker's brightness, and positive/negative calls. In 3D it also has `centroid_z` and `volume` (µm³), the slices each object spans (`z_slices`, `z_first`, `z_last`), and `z_flag` (*one_slice* or *possibly_merged*, for checking). `area` is the largest cross-section. |
| `image_summary.csv` | One row per image: object count and each percentage. |
| `settings_index.csv` | Which settings produced each image's results. |
| `recipe.yaml` | The settings. Load it later to analyse new images the same way. |

If `mixed_settings.txt` is also there, some images were analysed with different settings (for example you changed a cutoff after running them). Run all images again before you report numbers.

*Advanced:* **Show all settings** lets you add results such as "Marker B among Marker A-positive objects", review only flagged or failed images, and save or load settings.

## Several analyses of the same images

The **Analysis** list at the top of the CellQuant panel lets one experiment hold several analyses of the same images, for example finding objects in each channel in turn, or trying two segmentation methods. Each analysis has its own settings, its own results, its own deleted objects and approvals.

- **One per channel…**: tick the channels; each gets an analysis that finds objects in that channel with the current settings otherwise (method, Z-stack mode, markers).
- **New analysis…**: a copy of the current settings under a new name. Change what you need in steps 2-4, for example the channel in step 2.
- Choose an analysis in the list to see, change, check or run it. Steps 2-5 always show the analysis chosen.
- **Run all analyses** (bottom bar, and step 5) runs every included image with each analysis, one after another. Pause and Cancel work as for one analysis; Cancel keeps what is finished and skips the analyses not started.
- **Export all analyses…** (step 5) saves each analysis in its own folder, plus `all_analyses_image_summary.csv`: every analysis's per-image numbers in one table, with `analysis` and `segmentation_channel` columns.
- **Rename…** and **Remove** change the list. Removing only takes an analysis off the list; its settings and results stay in the experiment folder.

**Success check:** after **Run all analyses**, choosing each analysis in the list shows outlines found in its channel.

### The Plan: which images each analysis runs, and in which channel

Click **Plan…** (next to the Analysis list) to open the Plan beside the image. It lists every image, grouped by **Channel layout** (images whose files list the same channels in the same order), by **Folder**, or by **Channel layout, then folder**. Choose a view:

- **Grid: images × analyses**: one column per analysis. A tick means that analysis runs that image; the cell shows its status (green when analyzed, amber when it needs a look, red when it failed) and the channel objects are found in. Ticking a group's box ticks every image in it.
- **Tree: image ▸ analyses**: under each image, one row per analysis, each with a tick and a **Find objects in** menu.

The bar at the bottom applies a choice to the **selected images** or **all images**, in **all analyses** or one: **Tick**, **Untick**, or **Set channel** (find objects in that channel for those images only; ★ marks such a choice). Right-click a cell for the same choice for one image. Double-click a cell to open that image with that analysis in step 4. **Run ticked** (or **Run all analyses**) runs each analysis on its ticked images.

Images with another channel layout are analyzed with the channels of the same names: if an analysis finds objects in *Far Red*, a file that stores Far Red first is segmented in its first channel, and each marker is read from the channel with its name. ⚠ marks an image that has no channel of that name, so the position is used; check it, or set its channel.

---

## Coming back later

Open CellQuant, click **Open experiment…**, and choose the results folder. Your images, settings, deletions and results are kept. The Start tab shows where you left off.

## When something looks wrong

| Symptom | First check |
|---|---|
| Installer says conda was not found | Install Miniforge, then run `Install CellQuant.bat` again. |
| Install failed | Read the end of `install_last.log`. Check the internet connection and run the installer again, pressing Enter for the default folder. |
| Open CellQuant says it is not installed | Run `Install CellQuant.bat` on this computer. Each computer needs its own install. |
| An image will not open | Use ND2 or TIFF files. Make sure OneDrive files are downloaded (right-click, **Always keep on this device**). Time series are not supported yet. |
| Channels look gray | The file has no saved channel colors. CellQuant shows gray rather than inventing colors. |
| Fewer images than expected | Read the yellow note: it lists folders with no images. Files in CellQuant's own folders (runs, working, exports) are skipped. |
| "Save results with the images?" when creating an experiment | The results folder is your image folder or inside it. Click **No** and choose another results folder, unless you want them together. |
| "This image has 3 channels, but the experiment expects 4" | All images in one experiment need the same channels in the same order. Put different layouts in separate experiments. |
| Units say pixels | Enter the pixel size in step 1. |
| Next is greyed out | Read the orange text beside it: it says what is missing. |
| Nothing is found, or everything is one blob | Check that **Source channel** is the nuclear stain, and try Preview with different settings. |
| Cellpose is slow | Without an NVIDIA GPU, Cellpose runs on the CPU. Use Classic Cellpose, or the classical method. |
| "These settings were made for Cellpose-SAM" (or classic) | Close CellQuant and open it again with the engine the settings were made with, or choose a model for the engine you have. |
| "The GPU was requested but Cellpose ran on the CPU" | The NVIDIA driver may need updating. Run the installer again after updating it. |
| 3D: one nucleus appears as two (split across slices) | Lower **Link overlap**, and keep **Brightness** on *Whole stack*. The results flag these as *one_slice*. |
| 3D: two stacked nuclei counted as one | Raise **Link overlap**. The results flag these as *possibly_merged*. |
| 3D option is very slow | The estimate beside each option shows why. Turn on **Use GPU** if there is an NVIDIA GPU, or use Classic Cellpose, or a max projection. |
| Percentages changed after rerunning | The cutoff or settings changed. `settings_index.csv` shows which settings each image used. |

For how CellQuant works inside, see `CellQuant_v2_manifest_rev2.md` and `docs/STATUS.md`.
