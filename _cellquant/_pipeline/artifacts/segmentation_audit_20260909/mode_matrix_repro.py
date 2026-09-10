"""Actual adapter/preparation with recording model; no inference or real stitching."""
from pathlib import Path
from copy import deepcopy
import sys
import types
import importlib.util

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import numpy as np
import yaml

if importlib.util.find_spec('scipy') is None:
    scipy = types.ModuleType('scipy')
    scipy.ndimage = types.ModuleType('scipy.ndimage')
    sys.modules['scipy'] = scipy
    sys.modules['scipy.ndimage'] = scipy.ndimage
fake_utils = types.ModuleType('cellpose.utils')
fake_utils.stitch3D = lambda masks, **kwargs: masks
sys.modules['cellpose'] = types.ModuleType('cellpose')
sys.modules['cellpose.utils'] = fake_utils

from cellquant.config import RunConfig
from cellquant.contracts import ImageVolume
from cellquant.preprocess import prepare_analysis_volume
import cellquant.segment as adapter
adapter._seed = lambda runtime: None

raw = yaml.safe_load((ROOT / 'sample_config.yaml').read_text())
source = ImageVolume(np.ones((3, 6, 7, 1), dtype=np.float32), (2., .5, .5), ('DAPI',), Path('synthetic.tif'))

class RecordingModel:
    def __init__(self): self.calls = []
    def eval(self, image, **kwargs):
        self.calls.append((list(image.shape), {k: kwargs[k] for k in ('do_3D', 'z_axis', 'channel_axis', 'channels', 'anisotropy', 'stitch_threshold')}))
        masks = np.zeros(image.shape, dtype=np.uint32)
        masks[..., 1:3, 1:3] = 1
        return masks, None, None

for engine in ('v3', 'v4'):
    for mode in ('single_plane_2d', 'max_projection_2d', 'stitch_2d', 'volume_3d'):
        cfg = deepcopy(raw)
        cfg['segment'].update(engine=engine, mode=mode, z_index=1 if mode == 'single_plane_2d' else None, stitch_threshold=.25 if mode == 'stitch_2d' else 0., anisotropy='manifest')
        model = RecordingModel()
        analysis = prepare_analysis_volume(source, cfg)
        output = adapter.segment(analysis, cfg, segmenter=adapter.Segmenter(model, 'fake', 'cpu', 'recording'))
        expected = (1,6,7) if mode in ('single_plane_2d', 'max_projection_2d') else (3,6,7)
        assert output.data.shape == expected
        print(engine, mode, 'output=', output.data.shape, 'calls=', model.calls)

cfg['segment'].update(mode='stitch_2d', z_index=None, stitch_threshold=0.)
output = adapter.segment(source, cfg, segmenter=adapter.Segmenter(RecordingModel(), 'fake', 'cpu', 'recording'))
print('ZERO STITCH: independent plane objects=3, nonzero output IDs=', np.unique(output.data[output.data != 0]).tolist())
for field, value in (('channel_axis',0), ('z_axis',2), ('diameter_px',float('nan')), ('compute_masks',False), ('stitch_threshold',-.2)):
    probe = deepcopy(cfg)
    probe['segment'][field] = value
    RunConfig(probe)
    print('CONFIG ACCEPTED', field, repr(value))
print('Limits: fake model, stubbed stitch3D, RNG setup disabled; no model/GPU/inference verification.')
