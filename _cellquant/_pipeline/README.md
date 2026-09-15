# CellQuant

CellQuant is a napari plugin and command-line tool for segmenting nuclei with Cellpose and measuring fluorescence images. It accepts TIFF, OME-TIFF, and ND2 images. The napari interface supports **Single image**, **Batch folder**, **HPC prep**, and **Coexpression**. The command line uses the same analysis core.

**New to CellQuant or to scientific software?** Follow [Start here](docs/START_HERE.md). It walks through installation, a first image, saving results, batch analysis, coexpression review, and the optional Alpine route without requiring code.

**Maintaining or extending the code?** Read the [developer guide](docs/DEVELOPER_GUIDE.md) for the source map, setup, tests, output contracts, and current verification limits. [Architecture](ARCHITECTURE.md) holds the detailed scientific contract. The [documentation index](docs/README.md) separates current instructions from release evidence and historical plans.

## Windows quick start

1. Install Miniconda, Miniforge, or Anaconda if it is not already installed. CellQuant requires Python 3.11; the installer creates its own environments.
2. In this folder, double-click **`Install CellQuant.bat`**. Press Enter for the suggested install folder unless you have a specific location. The installer creates a Cellpose-SAM **v4** environment and a lighter classic **v3** environment beside it. Wait for **Install finished**; a failure opens `install_last.log` with details.
3. Double-click **`Open CellQuant.bat`**, choose v4 or v3, and wait for napari. If v4 is too slow or runs out of memory, try v3.
4. In napari, choose **Plugins → CellQuant Cellpose Pipeline**, then **Mode → Single image**. Start with a small image whose channel identity and voxel spacing you know.

The launcher remembers environment locations in `cellquant_env.json`. A CUDA-capable GPU can speed large runs, but a GPU is not required to start. The Windows installer attempts a compatible CUDA PyTorch build when it detects supported NVIDIA hardware; confirm GPU availability in the plugin's environment summary.

CellQuant 0.4.0a2 is an **alpha for lab testing**. The latest recorded software checks are dated **2026-09-09**. They do not establish biological accuracy on expert-reviewed retinal images or a live Alpine job. See the [release note](docs/RELEASE_0.4.0a2.md) and [`STATUS.json`](docs/STATUS.json) before treating results as validated scientific evidence.
