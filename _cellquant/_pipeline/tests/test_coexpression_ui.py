"""Actual napari/Qt regression paths; no model or real dataset is used."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import time
from concurrent.futures import Future
from pathlib import Path
import numpy as np
import pytest
from qtpy.QtWidgets import QApplication
import napari
from cellquant.classify import ClassificationRecipe
from cellquant.contracts import ImageVolume, LabelVolume, PipelineCancelled
from cellquant.plugin.controller import PluginController, IMAGE_LAYER_NAME, LABEL_LAYER_NAME
from cellquant.plugin.coexpression import CoexpressionPanel, OVERLAY_NAME


def test_overlay_sparse_ids_and_chunk_boundary():
    from cellquant.plugin.coexpression import call_overlay
    labels = np.zeros((1, 1, 1_000_005), dtype=np.uint32)
    labels[0, 0, :5] = [0, 7, 4_000_000_000, 42, 99]
    labels[0, 0, -3:] = [7, 4_000_000_000, 99]
    result = call_overlay(labels, [4_000_000_000, 7, 42], [4, 1, 3])
    assert result.dtype == np.uint8 and result.shape == labels.shape
    assert result[0, 0, :5].tolist() == [0, 1, 4, 3, 0]
    assert result[0, 0, -3:].tolist() == [1, 4, 0]
    assert np.count_nonzero(result) == 5
    assert not call_overlay(labels, [], []).any()

@pytest.fixture
def panel():
    app = QApplication.instance() or QApplication([])
    viewer = napari.Viewer(show=False)
    controller = PluginController(viewer)
    image = ImageVolume(np.arange(96,dtype=np.float32).reshape(2,4,4,3), (1,1,1), ('A','B','C'), Path('synthetic.tif'), {'classification_analysis_grid':True})
    controller._publish_image(image)
    controller._publish_labels(LabelVolume(np.ones((2,4,4),np.uint32), (1,1,1)))
    widget = CoexpressionPanel(viewer, controller)
    widget.apply_recipe(ClassificationRecipe(dict(name='test',calibration_group='test',expected_channel_names=['A','B','C'],markers=[dict(name='A',channel=0,low=20,positive_fraction=.5)])))
    for key in ['specimen_id','image_id','region_id']: widget.fields[key].setText('test')
    yield widget
    if widget.calibration_panel is not None:
        widget.calibration_panel.close()
        widget.calibration_panel.pool.shutdown(wait=True, cancel_futures=True)
    widget.timer.stop(); widget.pool.shutdown(wait=True, cancel_futures=True)
    if hasattr(widget, "batch"):
        widget.batch.timer.stop()
        widget.batch.pool.shutdown(wait=True, cancel_futures=True)
    widget.close(); viewer.close(); app.processEvents()

def complete(panel):
    deadline = time.monotonic()+15
    while panel.future is not None and time.monotonic()<deadline:
        QApplication.processEvents(); panel.poll(); time.sleep(.01)
    assert panel.future is None, 'background work exceeded 15 seconds'

@pytest.mark.parametrize('channels',[3,4])
def test_canonical_source_and_spatial_channel_display(panel,channels):
    image = ImageVolume(np.ones((2,4,4,channels),np.float32),(2,1,1),tuple('ABCD'[:channels]),Path('tiny.tif'))
    panel.controller._publish_image(image)
    source = panel.viewer.layers[IMAGE_LAYER_NAME]
    assert source.rgb is False and source.ndim==4 and not source.visible
    display=[x for x in panel.viewer.layers if x.metadata.get('cellquant_display_channel')]
    assert len(display)==channels
    assert all(x.data.shape==(2,4,4) and tuple(x.scale)==(2,1,1) for x in display)

def test_preview_paint_edit_save_reopen(panel,tmp_path):
    panel.preview(); complete(panel)
    assert panel.result is not None and panel.tables['queries'].rowCount()>0
    labels=panel.labels.currentData(); labels.brush_size=1
    labels.paint((0,1,1),2)
    assert panel.result is None and panel.tables['queries'].rowCount()==0
    assert not panel.viewer.layers[OVERLAY_NAME].visible
    panel.preview(tmp_path); complete(panel)
    assert panel.result is not None
    runs=[p.parent for p in tmp_path.rglob('complete.json')]
    assert len(runs)==1
    panel.reopen(runs[0]); assert panel.result is None
    complete(panel); assert panel.result is None
    panel.preview(); complete(panel); assert panel.result is not None
    panel.markers.item(0,2).setText('100')
    assert panel.result is None

def test_layout_requires_explicit_review_and_retains_unavailable(panel):
    raw=panel.recipe().raw
    raw['expected_channel_names']=['different','B','C']
    raw['markers'][0]['channel']=5
    panel.apply_recipe(ClassificationRecipe(raw)); panel.refresh_channels()
    assert panel._marker_channel_combo(0).currentData()==5
    assert 'Unavailable' in panel._marker_channel_combo(0).currentText()
    with pytest.raises(ValueError,match='layout'): panel.recipe()
    with pytest.raises(ValueError,match='unavailable'): panel.confirm_channel_layout()
    panel._marker_channel_combo(0).setCurrentIndex(1)
    with pytest.raises(ValueError,match='layout'): panel.recipe()
    panel.confirm_channel_layout()
    assert panel.recipe().raw['expected_channel_names']==['A','B','C']

def test_late_result_cancel_and_error(panel):
    panel.preview(); complete(panel)
    payload=(panel.result,panel.result_labels,panel.revision,None)
    panel.mark_stale(); panel.accept_result(payload)
    assert panel.result is None and panel.tables['queries'].rowCount()==0
    assert 'discarded' in panel.status.text()
    panel.start(lambda cancel: (_ for _ in ()).throw(RuntimeError('synthetic failure')), lambda _:None)
    complete(panel); assert 'synthetic failure' in panel.status.text()
    panel.start(lambda cancel: None, lambda _: pytest.fail('cancelled result published'))
    panel.cancel(); complete(panel); assert 'Cancelled' in panel.status.text()

def test_transform_and_metadata_invalidate(panel):
    panel.preview(); complete(panel)
    panel.labels.currentData().translate=(0,1,0)
    assert panel.result is None
    with pytest.raises(ValueError,match='restore'): panel.snapshot()
    panel.labels.currentData().translate=(0,0,0)
    panel.preview(); complete(panel)
    panel.image.currentData().metadata={'channel_names':['C','B','A'], 'spacing_um':(1,1,1)}
    assert panel.result is None
    with pytest.raises(ValueError,match='layout'): panel.recipe()
