# CellQuant

> **This is CellQuant 2**, a rewrite. The original CellQuant (0.4.0a3 and earlier) is kept unchanged on the
> [`v1` branch](https://github.com/ian-purvis/CellQuant/tree/v1) and at tag
> [`v0.4.0a3`](https://github.com/ian-purvis/CellQuant/releases/tag/v0.4.0a3). Settings files and results from
> version 1 do not open in version 2. See [CHANGELOG.md](CHANGELOG.md).

CellQuant finds nuclei (or cells) in fluorescence microscope images and counts how many are positive for each marker. It works on one image or a whole experiment, and you check every result by eye in napari.

**New to CellQuant? Read [Start here](docs/START_HERE.md).** It covers installing, a 15-minute practice run with a known answer, and each step with a success check.

## Quick start (Windows)

1. Install [Miniforge](https://conda-forge.org/download/) (or Miniconda) if this computer has no conda.
2. Double-click **`Install CellQuant.bat`**. Press Enter to accept each suggested answer.
3. Double-click **`Open CellQuant.bat`**.
4. In the CellQuant panel, click **Start → Try practice images**, then follow the numbered tabs (hover one for its name): **1 Images → 2 Find objects → 3 Markers → 4 Check → 5 Results**.

## Documentation map

### For users
- [docs/START_HERE.md](docs/START_HERE.md) — installation, practice run, end-to-end workflow
- [docs/STATUS.md](docs/STATUS.md) — built/tested features and known limitations
- [docs/SWEEP.md](docs/SWEEP.md) — comparing segmentation methods and thresholds

### For developers
- [docs/DEVELOPERS.md](docs/DEVELOPERS.md) — repository map, entry points, testing commands
- [CellQuant_v2_architecture_and_manifest.md](CellQuant_v2_architecture_and_manifest.md) — architecture and manifest overview
- [CellQuant_v2_manifest_rev2.md](CellQuant_v2_manifest_rev2.md) — detailed implementation manifest
- [tests/README.md](tests/README.md) — test organization and how to run targeted checks

## What is in this folder

| Item | What it is |
|---|---|
| `Install CellQuant.bat` | Installs CellQuant on this computer. Run it again to update. |
| `Open CellQuant.bat` | Starts CellQuant. |
| `docs/START_HERE.md` | The step-by-step guide for users. The same guide opens from the Start tab. |
| `docs/STATUS.md` | What has been built and tested, and what has not. |
| `docs/DEVELOPERS.md` | Repository map and developer workflow for contributors. |
| `docs/SWEEP.md` | Comparing segmentation methods and thresholds on a set of images. |
| `CellQuant_v2_manifest_rev2.md`, `CellQuant_v2_architecture_and_manifest.md` | The design and build plan, for developers. |
| `cellquant/` | The program. |
| `tests/` | Automated tests (`python -m pytest tests -q`). |
| `tests/README.md` | Summary of the test layout and common test commands. |
| `packaging/` | The installer scripts. |

## Status

This is an early version for lab testing. Results have not yet been checked against expert hand counts on real retinal images. Read the *Known limitations* at the top of [docs/STATUS.md](docs/STATUS.md) before relying on the numbers.

## Test images

No real images are stored in this repository. The tests make their own images:

- **Practice images** (`cellquant/practice.py`): three simple 2D images with well-separated nuclei and an exact
  answer, used by **Start → Try practice images**.

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
