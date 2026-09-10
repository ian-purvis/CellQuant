import numpy as np
import tifffile
from cellquant.io import label_dtype, save_labels


def test_label_dtype_and_ids_are_preserved(tmp_path):
    small = np.array([[0, 2], [17, 1]], dtype=np.int64)
    assert label_dtype(small) == np.uint16
    path = tmp_path / "labels.tif"
    save_labels(path, small)
    loaded = tifffile.imread(path)
    assert loaded.dtype == np.uint16
    assert np.array_equal(loaded, small)
    assert len(set(np.unique(loaded)) - {0}) == 3


def test_uint32_when_label_exceeds_uint16():
    assert label_dtype(np.array([0, 65536])) == np.uint32


def test_uint32_is_saved_without_rescaling(tmp_path):
    labels = np.array([[0, 65536]], dtype=np.uint32)
    path = tmp_path / "large_labels.tif"
    save_labels(path, labels)
    loaded = tifffile.imread(path)
    assert loaded.dtype == np.uint32
    assert np.array_equal(loaded, labels)
