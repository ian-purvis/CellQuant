# Tests

Run everything with `python -m pytest tests -q`. The window tests (`test_gui_*.py`) need napari, Qt and OpenGL: a desktop, or `xvfb-run -a` on Linux. They are skipped when napari is not installed.

| Files | What they cover |
|---|---|
| `test_engine.py`, `test_percent_rule.py`, `test_3d.py`, `test_colors_types_progress.py` | Segmentation, measurement, classification, 3D, image types and progress reporting. |
| `test_workflow.py`, `test_m0.py`, `test_m0_followup.py`, `test_network_reuse.py` | Experiments end to end: runs, edits, reopening, exports. |
| `test_import_folders.py`, `test_plan.py`, `test_analyses.py` | Importing folders, the Plan and several analyses. |
| `test_guided_start.py`, `test_synthetic_retina.py` | Practice images, quick marker setup, and that the guide names buttons that exist. |
| `test_cellpose_engines.py`, `test_hardware.py`, `test_sweep.py` | Cellpose 3 and 4 adapters, GPU and time estimates, sweeps. |
| `test_gui_*.py` | The window: display, guided flow, analyses, Plan dock, HPC tab. |
| `test_hpc_*.py`, `hpc_helpers.py` | Cluster packages: prepare, worker, scripts, import. |
| `test_windows_installer.py`, `ps_lint.py` | Installer scripts. |

Run one area, for example: `python -m pytest tests/test_plan.py -q`, or one test with `-k name`.
