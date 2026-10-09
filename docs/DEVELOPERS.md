# CellQuant for developers

## Repository map

| Path | What it holds |
|---|---|
| `cellquant/__main__.py` | `cellquant` command: opens the window, or `cellquant run --experiment FOLDER [--recipe FILE]` analyzes without it. |
| `cellquant/controller.py` | `AnalysisController`: every analysis action. The window and batch runs both go through it. |
| `cellquant/pipeline.py`, `segmentation.py`, `quantify.py`, `volume.py` | Segmentation, measurement and classification of one image (2D and 3D). |
| `cellquant/experiment.py`, `storage.py`, `recipe.py` | The experiment folder, saved runs and results, and the settings (`recipe.yaml`). |
| `cellquant/image.py`, `inputs.py` | Reading ND2 and TIFF files. |
| `cellquant/gui/app.py` | The napari window: panels for steps 1-5, the Run bar, background workers. |
| `cellquant/gui/guide.py` | The Start tab, step pages, hover help (`HELP`, `BUTTON_HELP`) and quick marker setup. |
| `cellquant/gui/plan_dock.py` | The Plan dock. |
| `cellquant/gui/START_HERE.md` | The in-app guide: a copy of `docs/START_HERE.md` (a test keeps them identical). |
| `cellquant/sweep.py`, `sweep_report.py` | Comparing segmentation methods (see `docs/SWEEP.md`). |
| `packaging/` | The Windows installer and launcher (`Install CellQuant.bat`, `Open CellQuant.bat` call these). |
| `tests/` | Automated tests; see [tests/README.md](../tests/README.md). |

## Set up and test

```
python -m pip install -e ".[gui,dev]"
python -m pytest tests -q
```

On Linux, run the window tests under `xvfb-run -a python -m pytest tests -q`.

## Conventions

- Words shown to users are plain and consistent: *cutoff* (not threshold) for marker calls, *settings* (not recipe) in the window, US spelling (color, analyze).
- Errors meant for users are `CellQuantError` subclasses (`cellquant/errors.py`) with a sentence the window shows as-is in the red problem box. Unexpected errors are shown with their type, and their traceback is written to `cellquant_developer.log` (`%LOCALAPPDATA%\CellQuant` on Windows, `~/.cellquant` elsewhere).
- After changing `docs/START_HERE.md`, copy it to `cellquant/gui/START_HERE.md`.
- Record user-visible changes in `CHANGELOG.md`.
