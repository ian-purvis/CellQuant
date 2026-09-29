# CellQuant

> **This is CellQuant 2**, a rewrite. The original CellQuant (0.4.0a3 and earlier) is kept unchanged on the
> [`v1` branch](https://github.com/ian-purvis/CellQuant/tree/v1) and at tag
> [`v0.4.0a3`](https://github.com/ian-purvis/CellQuant/releases/tag/v0.4.0a3). Settings files and results from
> version 1 do not open in version 2. See [CHANGELOG.md](CHANGELOG.md).

CellQuant finds nuclei (or cells) in fluorescence microscope images and counts how many are positive for each marker. It works on one image or a whole experiment, and you check every result by eye in a napari window.

**New to CellQuant? Read [Start here](docs/START_HERE.md).** It covers installing, a 15-minute practice run with a known answer, and each step with a success check.

## Quick start (Windows)

1. Install [Miniforge](https://conda-forge.org/download/) (or Miniconda) if this computer has no conda.
2. Double-click **`Install CellQuant.bat`**. Press Enter to accept each suggested answer.
3. Double-click **`Open CellQuant.bat`**.
4. In the CellQuant panel, click **Start → Try practice images**, then follow the numbered tabs: **1 Images → 2 Find objects → 3 Markers → 4 Check → 5 Results**.

## What is in this folder

| Item | What it is |
|---|---|
| `Install CellQuant.bat` | Installs CellQuant on this computer. Run it again to update. |
| `Open CellQuant.bat` | Starts CellQuant. |
| `docs/START_HERE.md` | The step-by-step guide for users. The same guide opens from the Start tab. |
| `docs/STATUS.md` | What has been built and tested, and what has not. |
| `docs/HPC_PREP_AND_SUBMISSION.md` | Running the analysis on a cluster GPU (Alpine), for large experiments. |
| `docs/SWEEP.md` | Comparing segmentation methods and thresholds on a set of images. |
| `CellQuant_v2_manifest_rev2.md`, `CellQuant_v2_architecture_and_manifest.md` | The design and build plan, for developers. |
| `cellquant/` | The program. |
| `tests/` | Automated tests (`python -m pytest tests -q`). |
| `packaging/` | The installer scripts. |

## Status

This is an early version for lab testing. Results have not yet been checked against expert hand counts on real retinal images. See `docs/STATUS.md` before relying on the numbers.

## Test images

No real images are stored in this repository. The tests make their own images:

- **Practice images** (`cellquant/practice.py`): three simple 2D images with well-separated nuclei and an exact
  answer, used by **Start → Try practice images**.
- **Synthetic retina z-stacks** (`cellquant/synthetic_retina.py`): images made to resemble confocal z-stacks of
  embryonic mouse retina (three channels named Green, Red and Far Red, 12-bit, 7 slices 1.5 µm apart,
  0.575 µm pixels, crowded elongated nuclei, blur, noise, dim and partly labelled nuclei), each with its true
  nuclei and which are reporter+ (Red), OTX2+ (Green) and PAX6-high (Far Red). Make a Control and a CRISPRi
  set with known answers (31% and 24% of reporter+ nuclei are OTX2+) with:

  ```
  python -m cellquant.synthetic_retina "D:\CellQuant test images"
  ```

  Add the `images` folder it makes to CellQuant; the answers are in `truth`, `truth_summary.csv` and `README.txt`.
  Add `--size 1024` for full-size images.

## Running the tests

```
python -m pip install -e ".[gui,dev]"
python -m pytest tests -q
```

The window tests need a display (on Linux, run them under `xvfb-run`).

## Citing

If CellQuant contributes to published work, please cite it (GitHub shows a **Cite this repository** button,
from [CITATION.cff](CITATION.cff)) and name the version you used.

## License

MIT. See [LICENSE](LICENSE). CellQuant uses [Cellpose](https://github.com/MouseLand/cellpose) and
[napari](https://napari.org) (both BSD-3-Clause) when they are installed.
