"""Standalone Cellpose TIFF import → reopen → Quantification."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import tifffile

from cellquant.classify import ClassificationRecipe
from cellquant.classify.batch import classify_cellquant_run
from cellquant.config import ImportedRunConfig, load_config, load_run_config, reject_imported_config
from cellquant.review import (
    IMPORTED_RUN_KIND,
    LABELS_NAME,
    create_import_bundle,
    load_review_workspace,
    publish_approved,
    relink_source,
)


def _write_image(path: Path, data: np.ndarray) -> Path:
    tifffile.imwrite(path, data, photometric="minisblack", metadata={"axes": "ZYXC"})
    return path


def _write_mask(path: Path, labels: np.ndarray) -> Path:
    tifffile.imwrite(
        path,
        labels.astype(np.uint16),
        photometric="minisblack",
        metadata={"axes": "ZYX"},
    )
    return path


def test_import_unknown_engine_reopen_and_quantify(tmp_path: Path):
    """Criterion 15 / Milestone B+D: unknown Cellpose settings still quantify."""

    image = np.zeros((2, 4, 4, 1), np.uint16)
    image[..., 0] = 20
    source = _write_image(tmp_path / "img.tif", image)
    labels = np.zeros((2, 4, 4), np.uint16)
    labels[0, 0, 0] = 1
    labels[1, 1, 1] = 2
    mask = _write_mask(tmp_path / "mask.tif", labels)

    # Engine/model intentionally unknown — external TIFF exports do not carry them.
    bundle = create_import_bundle(
        mask,
        source=source,
        spacing_um=(1.0, 0.5, 0.5),
        engine=None,
        model=None,
        model_sha256=None,
        settings=None,
    )
    assert bundle.run_dir.is_dir()
    assert (bundle.run_dir / LABELS_NAME).is_file()
    # Imported original must remain untouched.
    np.testing.assert_array_equal(tifffile.imread(mask), labels)

    config = load_run_config(bundle.run_dir / "config.json")
    assert isinstance(config, ImportedRunConfig)
    assert config.run_kind == IMPORTED_RUN_KIND
    provenance = config.raw["segmentation_provenance"]
    assert provenance["origin"] == "cellpose_tiff"
    assert provenance["engine"] is None
    assert provenance["model_sha256"] is None

    with pytest.raises(ValueError, match="imported_labels"):
        load_config(bundle.run_dir / "config.json")
    with pytest.raises(ValueError, match="native Cellpose"):
        reject_imported_config(config, action="segmentation")

    workspace = load_review_workspace(bundle.run_dir)
    assert workspace.shape == (2, 4, 4)
    assert workspace.working_origin == "original"
    assert tuple(float(v) for v in workspace.spacing_um) == pytest.approx((1.0, 0.5, 0.5))

    curated = labels.astype(np.uint32).copy()
    curated[0, 0, 1] = 3
    published = publish_approved(bundle.run_dir, curated)
    assert published.record.review_status == "approved"
    assert published.record.revision == 1

    recipe = ClassificationRecipe(
        {
            "schema_version": 1,
            "name": "import-smoke",
            "calibration_group": "g",
            "expected_channel_names": None,
            "markers": [
                {"name": "M", "channel": 0, "low": 5, "positive_fraction": 0.5},
            ],
        }
    )
    pack, result, used = classify_cellquant_run(
        bundle.run_dir,
        recipe,
        output_root=tmp_path / "classify_out",
        policy="approved_only",
    )
    assert used == "reviewed"
    assert pack.is_dir()
    assert int(result.metadata["total_objects"]) == 3
    # Provenance pins the approved revision rather than a silent original fallback.
    inputs = json.loads((pack / "inputs.json").read_text(encoding="utf-8"))
    labels_meta = inputs["labels"]["provenance"]
    assert labels_meta.get("review_revision") == 1
    assert labels_meta.get("review_status") == "approved"
    assert labels_meta.get("labels_used") == "reviewed"
    assert labels_meta.get("labels_file") == "r000001.tif"


def test_relink_source_updates_provenance_without_changing_grid(tmp_path: Path):
    image = np.zeros((1, 2, 2, 1), np.uint16)
    source = _write_image(tmp_path / "a.tif", image)
    (tmp_path / "moved").mkdir()
    moved = _write_image(tmp_path / "moved" / "a.tif", image)
    labels = np.ones((1, 2, 2), np.uint16)
    mask = _write_mask(tmp_path / "mask.tif", labels)
    bundle = create_import_bundle(
        mask,
        source=source,
        spacing_um=(1.0, 1.0, 1.0),
    )
    before = json.loads((bundle.run_dir / "config.json").read_text(encoding="utf-8"))
    relink_source(bundle.run_dir, moved)
    after = json.loads((bundle.run_dir / "config.json").read_text(encoding="utf-8"))
    provenance = json.loads((bundle.run_dir / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["source"] == str(moved)
    assert after["analysis"] == before["analysis"]
    workspace = load_review_workspace(bundle.run_dir)
    assert workspace.source.resolve() == moved.resolve()
