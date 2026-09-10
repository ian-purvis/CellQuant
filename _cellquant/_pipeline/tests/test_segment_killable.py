"""Tests for Cancel vs Kill Cellpose stop support."""

from multiprocessing import get_context
from time import sleep

import pytest

from cellquant.contracts import MutableCancellationToken, PipelineCancelled
from cellquant.segment.killable import is_killable_cellpose_model, run_killable_cellpose_eval


def _hanging_worker() -> None:
    sleep(120)


def test_cancel_is_cooperative_only():
    token = MutableCancellationToken()
    hits = []
    token.register_force_stop(lambda: hits.append("stop"))
    token.cancel()
    assert token.cancelled
    assert not token.killed
    assert hits == []


def test_kill_runs_force_stop_callbacks():
    token = MutableCancellationToken()
    hits = []
    token.register_force_stop(lambda: hits.append("stop"))
    token.kill()
    assert token.cancelled
    assert token.killed
    assert hits == ["stop"]


def test_is_killable_only_for_cellpose_module_models():
    class Fake:
        pass

    Fake.__module__ = "tests.fake"
    assert not is_killable_cellpose_model(Fake())

    class PretendCellpose:
        pass

    PretendCellpose.__module__ = "cellpose.models"
    assert is_killable_cellpose_model(PretendCellpose())


def test_kill_can_terminate_hanging_spawn_process():
    """Windows-safe check that Kill's force-stop can free a stuck worker."""

    ctx = get_context("spawn")
    process = ctx.Process(target=_hanging_worker, name="cellquant-kill-test")
    process.start()
    token = MutableCancellationToken()

    def force_stop() -> None:
        if process.is_alive():
            process.terminate()

    token.register_force_stop(force_stop)
    assert process.is_alive()
    token.cancel()
    sleep(0.2)
    assert process.is_alive()
    token.kill()
    process.join(timeout=10)
    assert not process.is_alive()
    assert process.exitcode is not None


def test_run_killable_raises_cancelled_when_token_already_set():
    token = MutableCancellationToken()
    token.cancel()
    with pytest.raises(PipelineCancelled):
        run_killable_cellpose_eval(
            constructor_parameters={"device": "cpu", "gpu": False, "pretrained_model": "x"},
            image=__import__("numpy").zeros((2, 2), dtype="float32"),
            eval_kwargs={},
            mode="single_plane_2d",
            cancel=token,
        )


def _synthetic_eval(image_path, masks_path, constructor, kwargs, mode, cancel_event, progress_queue, result_queue):
    import numpy as np
    if mode == 'hang':
        progress_queue.put((1, 1, 'ready'))
        sleep(120)
    elif mode == 'error':
        result_queue.put(('error', 'synthetic child failure'))
    else:
        np.save(masks_path, np.ones_like(np.load(image_path), dtype=np.uint32))
        result_queue.put(('ok', {'synthetic': True}))


def test_kill_registered_after_request_is_immediate():
    token = MutableCancellationToken()
    token.kill()
    hits=[]
    token.register_force_stop(lambda: hits.append('late'))
    assert hits == ['late']


def test_start_failure_unregisters_and_closes_resources(monkeypatch,tmp_path):
    import cellquant.segment.killable as module
    import numpy as np
    from queue import Queue
    from threading import Event
    queues=[]
    class ClosedQueue(Queue):
        def __init__(self): super().__init__(); self.closed=False; self.joined=False
        def close(self): self.closed=True
        def join_thread(self): self.joined=True
    class Process:
        closed=False
        def start(self): raise OSError('synthetic start failure')
        def is_alive(self): return False
        def close(self): self.closed=True
    process=Process()
    class Context:
        def Event(self): return Event()
        def Queue(self):
            q=ClosedQueue(); queues.append(q); return q
        def Process(self,**kwargs): return process
    monkeypatch.setattr(module,'get_context',lambda _:Context())
    monkeypatch.setattr(module.tempfile,'tempdir',str(tmp_path))
    token=MutableCancellationToken()
    with pytest.raises(OSError,match='synthetic start failure'):
        run_killable_cellpose_eval(constructor_parameters={},image=np.zeros((2,2)),eval_kwargs={},mode='test',cancel=token)
    assert not token._force_stops and process.closed
    assert all(q.closed and q.joined for q in queues)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('mode',['ok','error','hang'])
def test_actual_spawn_runner_result_error_and_kill(monkeypatch,tmp_path,mode):
    import cellquant.segment.killable as module
    import numpy as np
    from time import monotonic
    from threading import Timer
    monkeypatch.setattr(module,'_child_main',_synthetic_eval)
    monkeypatch.setattr(module.tempfile,'tempdir',str(tmp_path))
    token=MutableCancellationToken()
    start=monotonic()
    def ready(*_):
        if mode == 'hang': token.kill()
    kwargs=dict(constructor_parameters={},image=np.zeros((2,2)),eval_kwargs={},mode=mode,cancel=token,on_plane_progress=ready,poll_seconds=.05)
    watchdog = Timer(15, token.kill)
    watchdog.start()
    try:
        if mode=='ok':
            masks,meta=run_killable_cellpose_eval(**kwargs)
            assert masks.sum()==4 and meta['force_killable_process']
        else:
            with pytest.raises(PipelineCancelled if mode=='hang' else RuntimeError):
                run_killable_cellpose_eval(**kwargs)
    finally:
        watchdog.cancel(); watchdog.join()
    assert monotonic()-start<20
    assert not token._force_stops
    assert not list(tmp_path.iterdir())


def test_default_segmentation_constructs_no_parent_model(monkeypatch):
    import importlib
    import numpy as np
    from pathlib import Path
    from test_segment_package import _config
    from cellquant.contracts import ImageVolume
    module=importlib.import_module('cellquant.segment')
    monkeypatch.setattr(module,'load_model',lambda *a,**k:pytest.fail('parent model allocated'))
    def eval_child(**kwargs):
        constructor=kwargs['constructor_parameters']
        assert constructor['_runtime']==_config()['runtime']
        assert constructor['_model_spec']==_config()['segment']
        return np.ones_like(kwargs['image'],dtype=np.uint32),dict(force_killable_process=True,model_sha256='a'*64,device='cpu',cellpose_version='4.test',constructor_parameters={'device':'cpu'})
    monkeypatch.setattr('cellquant.segment.killable.run_killable_cellpose_eval',eval_child)
    image=ImageVolume(np.zeros((2,3,3,1),np.float32),(1,1,1),('DAPI',),Path('tiny.tif'))
    result=module.segment(image,_config(),cancel=MutableCancellationToken())
    assert result.data.sum()==18
    assert result.provenance['cellpose_version']=='4.test'
    assert result.provenance['eval_parameters']['diameter']==30



def test_child_progress_observes_cooperative_cancel():
    from threading import Event
    from cellquant.segment.killable import _ChildProgress
    event = Event()
    progress = _ChildProgress(event)
    progress.setValue(1)
    event.set()
    with pytest.raises(PipelineCancelled):
        progress.setValue(2)
