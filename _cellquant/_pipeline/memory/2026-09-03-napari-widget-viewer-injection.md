# Napari widget viewer-injection failure

- Symptom: Opening `CellQuant Cellpose Pipeline` in Napari 0.9 raised `cellquant_widget() missing 1 required positional argument: 'napari_viewer'`.
- Root cause: Napari 0.9 treats function widget contributions as zero-argument factories. The CellQuant function entry point incorrectly required a viewer argument, so Napari invoked it without one.
- Fix: `cellquant_widget()` now accepts no arguments, obtains the active viewer with `napari.current_viewer()`, validates that one exists, and passes it to the existing widget factory.
- Regression test: `tests/test_widget.py` verifies the zero-argument signature, current-viewer handoff, and missing-viewer error without starting Qt.
- Verification: Focused widget tests passed; the complete project suite passed 20/20 with third-party pytest plugin auto-loading disabled and an isolated writable pytest temp directory.
- Status: DONE
