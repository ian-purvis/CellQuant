"""Read-only synthetic mode semantics probes; no model inference or real data writes."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import numpy as np
from cellquant.contracts import ImageVolume, LabelVolume
from cellquant.measure import measure_labels
from cellquant.postprocess import filter_size, remove_border_labels

for dz in (1., 5.):
    spacing = (dz, .5, .5)
    labels = np.zeros((1, 4, 4), dtype=np.uint32)
    labels[:, 1:3, 1:3] = 1
    provenance = {'analysis_volume': {'mode': 'max_projection_2d'}}
    mask = LabelVolume(labels, spacing, provenance)
    image = ImageVolume(np.ones((1, 4, 4, 1), dtype=np.float32), spacing, ('DAPI',), Path('synthetic.tif'), provenance)
    table = measure_labels(mask, image)
    filtered = filter_size(mask, {'min_volume_um3': 2.})
    print(f'dz={dz}: area=1 um2, reported volume={table.objects.iloc[0].volume_um3} um3, retained IDs={np.unique(filtered.data).tolist()}')
    border = remove_border_labels(mask, ['z0', 'z1'])
    print(f'2D interior object after z-border removal: {np.unique(border.data).tolist()}')
