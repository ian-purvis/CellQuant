"""Safe review probes: synthetic pixels and mocked reader, no inference or real run writes."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from copy import deepcopy
import tempfile
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import numpy as np
from cellquant.config import load_config, RunConfig
from cellquant.contracts import ImageVolume, LabelVolume, MutableCancellationToken
from cellquant.orchestrator import run_measurements
from cellquant.io._nd2 import _spacing
from cellquant.io import open_volume
from cellquant.segment import _eval_kwargs

base = load_config(ROOT / 'sample_config.yaml')
raw = deepcopy(base.raw)
raw['segment'].update(mode='single_plane_2d', z_index=1, stitch_threshold=0.)
config = RunConfig(raw)
pixels = np.zeros((2, 2, 2, 1), dtype=np.float32)
pixels[0] = 10
pixels[1] = 90
image = ImageVolume(pixels, (1.,1.,1.), ('A',), Path('synthetic.nd2'))
labels = LabelVolume(np.ones((1,2,2), dtype=np.uint32), image.spacing_um, {
    'analysis_volume': {'mode':'single_plane_2d', 'original_z_depth':2,
                        'z_selection': {'kind':'single_plane', 'z_index':0}}})
tables = run_measurements(image, labels, config, MutableCancellationToken())
print('LABELS RECORDED PLANE 0, live config plane 1: measured mean=', tables.intensities.iloc[0]['mean'])

handle = SimpleNamespace(
    voxel_size=lambda: SimpleNamespace(z=1.,y=1.,x=1.),
    metadata=SimpleNamespace(channels=[SimpleNamespace(volume=SimpleNamespace(axesCalibrated=[False,False,False]))]))
print('UNCALIBRATED ND2 handle accepted spacing=', _spacing(handle))

with tempfile.TemporaryDirectory(prefix='cellquant-review-') as folder:
    path = Path(folder) / 'synthetic.nd2'
    path.write_bytes(b'mocked reader only')
    positions = np.zeros((2,2,2), dtype=np.uint16)
    positions[1] = 99
    def reader(*args, **kwargs):
        return positions, 'PYX', (1.,1.,1.), ('A',), {'format':'nd2', 'series':0}
    with patch('cellquant.io.read_nd2', reader):
        p0 = open_volume(path, position=0, lazy=False)
        p1 = open_volume(path, position=1, lazy=False)
    print('POSITION PIXELS=', p0.data.mean(), p1.data.mean(), 'identical metadata=', p0.metadata == p1.metadata, 'position recorded=', 'position' in p1.metadata)

raw = deepcopy(base.raw)
raw['segment'].update(mode='volume_3d', z_index=None, z_axis=None)
config = RunConfig(raw)
try:
    _eval_kwargs(config.raw['segment'], (1.,1.,1.))
except Exception as exc:
    print('ACCEPTED z_axis=None DISPATCH:', type(exc).__name__, str(exc))
