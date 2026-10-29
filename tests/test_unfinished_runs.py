"""Runs that stop part-way: written down image by image, offered again, and a guard on closing during a run."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from cellquant.controller import UNFINISHED_RUN_FILE, AnalysisController
from cellquant.segmentation import segment_objects
from tests.test_workflow import _recipe, _write_squares


def _experiment(tmp_path: Path, count: int = 4) -> AnalysisController:
    paths = []
    for index in range(count):
        path = tmp_path / "data" / f"img{index}.tif"
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_squares(path, shift=index)
        paths.append(path)
    controller = AnalysisController.create(tmp_path / "experiment", "Unfinished")
    controller.add_image_paths(paths)
    controller.set_recipe(_recipe())
    controller.save()
    return controller


def _counting(monkeypatch) -> list:
    calls = []

    def counted(*args, **kwargs):
        calls.append(1)
        return segment_objects(*args, **kwargs)

    monkeypatch.setattr("cellquant.pipeline.segment_objects", counted)
    return calls


def test_a_finished_batch_leaves_nothing_to_continue(tmp_path: Path):
    controller = _experiment(tmp_path, 2)
    controller.run_images()
    assert not (controller.directory / UNFINISHED_RUN_FILE).exists()
    assert controller.unfinished_run() is None
    run = json.loads((controller.run_dir / "run.json").read_text(encoding="utf-8"))
    assert run["processing_status"] == "completed"


def test_mid_batch_the_run_is_recorded_as_running(tmp_path: Path, monkeypatch):
    controller = _experiment(tmp_path, 3)
    seen = []

    def on_progress(index, total, filename, status):
        if status != "running" and index < total:
            run = json.loads((controller.run_dir / "run.json").read_text(encoding="utf-8"))
            journal = json.loads((controller.directory / UNFINISHED_RUN_FILE).read_text(encoding="utf-8"))
            seen.append((run["processing_status"], len(journal["done"])))

    controller.run_images(on_progress=on_progress)
    assert seen == [("running", 1), ("running", 2)]


def test_a_run_stopped_by_a_crash_is_offered_with_what_is_left(tmp_path: Path, monkeypatch):
    controller = _experiment(tmp_path, 4)
    ids = [record.image_id for record in controller.experiment.images]
    real = controller.run_image

    def crash_on_third(image_id, **kwargs):
        if image_id == ids[2]:
            raise KeyboardInterrupt  # stands in for the program being killed: nothing after it runs
        return real(image_id, **kwargs)

    monkeypatch.setattr(controller, "run_image", crash_on_third)
    with pytest.raises(KeyboardInterrupt):
        controller.run_images()
    # As after a restart: the record on disk is all that is left.
    journal = json.loads((controller.directory / UNFINISHED_RUN_FILE).read_text(encoding="utf-8"))
    journal["cancelled"], journal["logged"] = False, False
    (controller.directory / UNFINISHED_RUN_FILE).write_text(json.dumps(journal), encoding="utf-8")
    run_dir = controller.run_dir
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    run["processing_status"] = "running"
    (run_dir / "run.json").write_text(json.dumps(run), encoding="utf-8")

    reopened = AnalysisController.open(controller.directory)
    unfinished = reopened.unfinished_run()
    assert unfinished is not None
    assert (unfinished.done, unfinished.total, unfinished.left) == (2, 4, 2)
    assert unfinished.remaining == [(reopened.recipe.recipe_id, ids[2:])]
    assert not unfinished.cancelled
    log = (run_dir / "logs" / "run.log").read_text(encoding="utf-8")
    assert "stopped after 2 of 4 images" in log
    assert json.loads((run_dir / "run.json").read_text(encoding="utf-8"))["processing_status"] == "interrupted"
    reopened.unfinished_run()  # asked again: logged once
    assert (run_dir / "logs" / "run.log").read_text(encoding="utf-8").count("stopped after") == 1


def test_continue_runs_only_the_images_left(tmp_path: Path, monkeypatch):
    controller = _experiment(tmp_path, 4)
    calls = _counting(monkeypatch)
    count = {"n": 0}

    def two_then_stop() -> bool:
        count["n"] += 1
        return count["n"] <= 2

    report = controller.run_images(should_continue=two_then_stop)
    assert report.cancelled and len(report.jobs) == 2 and len(calls) == 2
    unfinished = controller.unfinished_run()
    assert unfinished is not None and unfinished.cancelled and unfinished.left == 2

    reports = controller.run_analyses(work=unfinished.remaining)
    assert [len(item.jobs) for item in reports] == [2]
    assert len(calls) == 4  # the finished two were not run again
    assert controller.unfinished_run() is None
    assert all(record.processing_status == "analyzed" for record in controller.experiment.images)


def test_start_over_reuses_or_finds_objects_again(tmp_path: Path, monkeypatch):
    controller = _experiment(tmp_path, 3)
    calls = _counting(monkeypatch)
    stop = {"n": 0}

    def one_then_stop() -> bool:
        stop["n"] += 1
        return stop["n"] <= 1

    controller.run_images(should_continue=one_then_stop)
    unfinished = controller.unfinished_run()
    assert unfinished is not None and unfinished.done == 1
    assert len(calls) == 1

    controller.run_analyses(work=unfinished.work)  # saved objects reused: only the two left are segmented
    assert len(calls) == 3
    controller.run_analyses(work=unfinished.work, fresh=True)  # every image found again
    assert len(calls) == 6
    assert controller._fresh_segmentation is False


def test_carrying_on_with_the_analysis_forgets_the_run(tmp_path: Path):
    controller = _experiment(tmp_path, 2)
    controller.run_images(should_continue=iter([True, False]).__next__)
    assert controller.unfinished_run() is not None
    controller.forget_unfinished_run("Not continued.")
    assert controller.unfinished_run() is None
    assert "Not continued." in (controller.run_dir / "logs" / "run.log").read_text(encoding="utf-8")


def test_images_or_analyses_removed_since_are_left_out(tmp_path: Path):
    controller = _experiment(tmp_path, 3)
    ids = [record.image_id for record in controller.experiment.images]
    journal = {"format": 1, "started": "", "work": [[controller.recipe.recipe_id, ids + ["gone"]], ["no_such_analysis", ids]],
               "done": [[controller.recipe.recipe_id, ids[0]]], "cancelled": True, "logged": True}
    (controller.directory / UNFINISHED_RUN_FILE).write_text(json.dumps(journal), encoding="utf-8")
    unfinished = controller.unfinished_run()
    assert unfinished.total == 3 and unfinished.remaining == [(controller.recipe.recipe_id, ids[1:])]
    (controller.directory / UNFINISHED_RUN_FILE).write_text("{ not json", encoding="utf-8")
    assert controller.unfinished_run() is None


# -- the window -------------------------------------------------------------------------------------


def _wait(shell, seconds: float = 60) -> None:
    from qtpy.QtWidgets import QApplication

    end = time.time() + seconds
    while (shell._job is not None or shell._batch is not None or shell._loader is not None) and time.time() < end:
        QApplication.processEvents()
        time.sleep(0.02)
    QApplication.processEvents()


@pytest.fixture
def settings(tmp_path, monkeypatch):
    from qtpy.QtCore import QSettings

    from cellquant.gui import app

    store = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    monkeypatch.setattr(app, "_settings", lambda: store)
    return store


def _window(directory=None):
    napari = pytest.importorskip("napari")
    from cellquant.gui.app import CellQuantWindow

    try:
        viewer = napari.Viewer(show=False)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"napari viewer could not start: {exc}")
    return viewer, CellQuantWindow(viewer, directory)


def test_closing_during_a_run_asks_first(tmp_path: Path, settings, monkeypatch):
    from qtpy.QtWidgets import QApplication

    from cellquant.gui.app import CellQuantWindow

    controller = _experiment(tmp_path, 1)
    answers = []
    monkeypatch.setattr(CellQuantWindow, "_confirm_exit_during_run", lambda self: answers.pop(0))
    viewer, shell = _window(controller.directory)
    window = viewer.window._qt_window
    try:
        window.show()
        QApplication.processEvents()
        assert shell._allow_close()  # nothing running: no question
        shell._job = object()  # a run going on
        answers.append(False)
        assert window.close() is False and window.isVisible()  # Stay
        answers.append(True)
        assert shell._allow_close()  # Exit
        assert not answers
        shell._job = None
    finally:
        viewer.close()


def test_opening_an_experiment_with_an_unfinished_run_offers_it(tmp_path: Path, settings, monkeypatch):
    from qtpy.QtWidgets import QApplication

    from cellquant.gui.app import CellQuantWindow

    controller = _experiment(tmp_path, 3)
    controller.run_images(should_continue=iter([True, False]).__next__)
    asked = []

    def answer(self, name, done, total, cancelled, folder=None):
        asked.append((name, done, total, cancelled, folder))
        return "continue", False

    monkeypatch.setattr(CellQuantWindow, "_ask_unfinished", answer)
    viewer, shell = _window(controller.directory)
    try:
        for _ in range(5):
            QApplication.processEvents()
        _wait(shell)
        assert asked == [("Unfinished", 1, 3, True, None)]
        assert not shell.controller.has_unfinished_run()
        assert all(record.processing_status == "analyzed" for record in shell.controller.experiment.images)
        assert not settings.value("run/unfinished_experiment", "")
    finally:
        viewer.close()


def test_starting_cellquant_offers_the_last_unfinished_run(tmp_path: Path, settings, monkeypatch):
    from qtpy.QtWidgets import QApplication

    from cellquant.gui.app import CellQuantWindow

    controller = _experiment(tmp_path, 2)
    controller.run_images(should_continue=iter([True, False]).__next__)
    settings.setValue("run/unfinished_experiment", str(controller.directory))
    choices = [(None, False), ("analysis", False)]
    asked = []

    def answer(self, name, done, total, cancelled, folder=None):
        asked.append((done, total, folder))
        return choices.pop(0)

    monkeypatch.setattr(CellQuantWindow, "_ask_unfinished", answer)
    viewer, shell = _window()
    try:
        QApplication.processEvents()
        assert asked == [(1, 2, str(controller.directory))]
        assert shell.controller is None  # Not now: nothing opened, asked again next time
        assert settings.value("run/unfinished_experiment", "") == str(controller.directory)
        shell._offer_last_unfinished_run()
        assert shell.controller is not None and Path(shell.controller.directory) == controller.directory.resolve()
        assert not shell.controller.has_unfinished_run()  # Continue with the analysis
        assert not settings.value("run/unfinished_experiment", "")
        _wait(shell)
    finally:
        viewer.close()
