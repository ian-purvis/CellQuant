"""CPU-only smoke test for the current canonical core (no Cellpose inference)."""

from pathlib import Path
import tempfile

import numpy as np

from cellquant.config import load_config
from cellquant.contracts import ImageVolume, LabelVolume, MutableCancellationToken
from cellquant.measure import measure_labels, write_measurements
from cellquant.persist import RunStore
from cellquant.postprocess import run_postprocess
from cellquant.preprocess import run_preprocess
from cellquant.verify import compare_pair
from cellquant.viz import make_qc_figures


def main() -> None:
    project = Path(__file__).resolve().parents[1]
    config = load_config(project / "reference_config.yaml")
    image_data = np.arange(3 * 24 * 20, dtype=np.uint16).reshape(3, 24, 20, 1)
    label_data = np.zeros((3, 24, 20), dtype=np.uint32)
    label_data[:, 3:10, 4:12] = 7
    label_data[:, 14:21, 11:18] = 19
    image = ImageVolume(
        image_data,
        (1.5, 0.575445178776431, 0.575445178776431),
        ("DAPI",),
        Path("synthetic.tif"),
        {"run_id": "smoke", "file_id": "synthetic", "input_fingerprint": "synthetic"},
    )
    labels = LabelVolume(label_data, image.spacing_um, {"input_fingerprint": "synthetic"})
    token = MutableCancellationToken()

    preprocessed = run_preprocess(image, config, token)
    assert preprocessed.data.dtype == np.float32 and preprocessed.data.shape == image.data.shape
    processed = run_postprocess(labels, config, token)
    tables = measure_labels(processed, image, config, token)
    assert list(tables.objects.label) == [7, 19]
    assert set(tables.intensities.channel) == {"DAPI"}
    parity = compare_pair(labels.data, processed.data, "synthetic")
    assert parity.optimal_matched_mean_iou == 1.0 and parity.f1_at_0_75 == 1.0

    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary)
        store = RunStore.create(output, "input", config.fingerprint, run_id="smoke")
        store.write_config(config)
        store.write_labels(processed)
        for path in write_measurements(tables, output):
            store.register_measurement(path)
        for path in make_qc_figures(image, processed, output, config).values():
            store.register_qc_artifact(path)
        store.write_provenance({"kind": "cpu_smoke", "model_sha256": config.raw["segment"]["model_sha256"]})
        store.commit("complete")
        assert store.is_resumable("input", config.fingerprint)
    print("CellQuant canonical-core smoke checks passed")


if __name__ == "__main__":
    main()
