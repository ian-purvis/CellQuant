from __future__ import annotations

import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from .io import save_labels


REQUIRED_TABLES = ("objects", "measurements_long", "overlaps", "coexpression_summary")
REQUIRED_ARTIFACTS = {"labels.tif", *(f"{name}.csv" for name in REQUIRED_TABLES)}


def fingerprint(path: str | Path, config: dict) -> str:
    path = Path(path)
    stat = path.stat()
    content = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            content.update(chunk)
    payload = {"path": str(path.resolve()), "size": stat.st_size,
               "mtime_ns": stat.st_mtime_ns, "content_sha256": content.hexdigest(),
               "config": config}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def can_resume(output_dir: str | Path, expected: str) -> bool:
    import tifffile
    marker = Path(output_dir) / "result.json"
    if not marker.is_file():
        return False
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
        if value.get("status") != "complete" or value.get("fingerprint") != expected:
            return False
        artifacts = value.get("required_artifacts")
        if not isinstance(artifacts, list) or "labels.tif" not in artifacts:
            return False
        if not REQUIRED_ARTIFACTS.issubset(set(artifacts)):
            return False
        for name in artifacts:
            if not isinstance(name, str) or Path(name).name != name:
                return False
            artifact = marker.parent / name
            if not artifact.is_file() or artifact.stat().st_size <= 0:
                return False
        labels = np.asarray(tifffile.imread(marker.parent / "labels.tif"))
        if labels.ndim not in (2, 3) or not np.issubdtype(labels.dtype, np.integer):
            return False
        if list(labels.shape) != value.get("label_shape"):
            return False
        if int(labels.max(initial=0)) != value.get("label_max"):
            return False
        return True
    except Exception:
        # Resume validation is deliberately fail-closed for corrupt/truncated
        # TIFFs and future reader errors: the batch reruns the input.
        return False


def save_result(output_dir, labels, tables: dict[str, pd.DataFrame], metadata: dict, calibration):
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    missing = set(REQUIRED_TABLES) - set(tables)
    if missing:
        raise ValueError(f"Missing required result table(s): {sorted(missing)}")
    save_labels(output / "labels.tif", labels, calibration)
    for name, table in tables.items():
        table.to_csv(output / f"{name}.csv", index=False)
    required = ["labels.tif", *[f"{name}.csv" for name in tables]]
    label_array = np.asarray(labels)
    temp = output / "result.json.tmp"
    temp.write_text(json.dumps({**metadata, "status": "complete",
                                "required_artifacts": required,
                                "label_shape": list(label_array.shape),
                                "label_max": int(label_array.max(initial=0))},
                               indent=2, default=str), encoding="utf-8")
    temp.replace(output / "result.json")


def load_result(output_dir):
    import tifffile
    output = Path(output_dir)
    metadata = json.loads((output / "result.json").read_text(encoding="utf-8"))
    labels = np.asarray(tifffile.imread(output / "labels.tif"))
    tables = {p.stem: pd.read_csv(p) for p in output.glob("*.csv")}
    return labels, tables, metadata
