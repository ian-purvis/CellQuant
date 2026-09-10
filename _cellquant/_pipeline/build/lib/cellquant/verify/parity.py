from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment
from scipy.stats import ks_2samp, wasserstein_distance


def _labels(value, name: str) -> np.ndarray:
    data = np.asarray(getattr(value, "data", value))
    if data.ndim != 3:
        raise ValueError(f"{name} must have ZYX axes; received shape {data.shape}")
    if not np.issubdtype(data.dtype, np.integer) or np.any(data < 0):
        raise TypeError(f"{name} must contain non-negative integer labels")
    return data


def _contingency(reference: np.ndarray, candidate: np.ndarray):
    ref_ids, ref_inverse, ref_counts = np.unique(reference, return_inverse=True, return_counts=True)
    cand_ids, cand_inverse, cand_counts = np.unique(candidate, return_inverse=True, return_counts=True)
    pair_index = (
        ref_inverse.astype(np.int64, copy=False).ravel() * cand_ids.size
        + cand_inverse.ravel()
    )
    overlap = np.bincount(pair_index, minlength=ref_ids.size * cand_ids.size).reshape(
        ref_ids.size, cand_ids.size
    )
    ref_keep = ref_ids != 0
    cand_keep = cand_ids != 0
    ref_ids, cand_ids = ref_ids[ref_keep], cand_ids[cand_keep]
    ref_counts, cand_counts = ref_counts[ref_keep], cand_counts[cand_keep]
    overlap = overlap[np.ix_(ref_keep, cand_keep)].astype(np.float64, copy=False)
    union = ref_counts[:, None] + cand_counts[None, :] - overlap
    iou = np.divide(overlap, union, out=np.zeros_like(overlap), where=union > 0)
    return ref_ids, cand_ids, ref_counts, cand_counts, iou


def _threshold_assignment(iou: np.ndarray, threshold: float):
    if not 0 <= threshold <= 1:
        raise ValueError("IoU threshold must be in [0, 1]")
    n_ref, n_cand = iou.shape
    if min(n_ref, n_cand) == 0:
        return np.array([], dtype=int), np.array([], dtype=int)
    scale = 2 * min(n_ref, n_cand)
    cost = -(iou >= threshold).astype(np.float64) - iou / scale
    ref_index, cand_index = linear_sum_assignment(cost)
    keep = iou[ref_index, cand_index] >= threshold
    return ref_index[keep], cand_index[keep]


@dataclass(frozen=True)
class MatchResult:
    threshold: float
    true_positive: int
    false_positive: int
    false_negative: int
    precision: float
    recall: float
    f1: float
    matched_mean_iou: float | None


@dataclass(frozen=True)
class ParityResult:
    name: str
    reference_count: int
    candidate_count: int
    count_delta: int
    count_delta_percent: float
    foreground_iou: float
    optimal_matched_mean_iou: float | None
    f1_at_0_50: float
    f1_at_0_75: float
    matched_mean_iou_at_0_50: float | None
    matched_mean_iou_at_0_75: float | None
    volume_wasserstein_voxels: float
    volume_ks_statistic: float
    median_volume_delta_percent: float
    matched_labels: pd.DataFrame
    bland_altman_mean_difference_voxels: float | None = None
    bland_altman_lower_limit_voxels: float | None = None
    bland_altman_upper_limit_voxels: float | None = None
    volume_outlier_count: int = 0
    likely_causes: tuple[str, ...] = ()

    def summary(self) -> dict:
        value = asdict(self)
        value.pop("matched_labels")
        return value


def match_labels(reference, candidate, threshold: float = 0.5) -> MatchResult:
    ref = _labels(reference, "reference")
    cand = _labels(candidate, "candidate")
    if ref.shape != cand.shape:
        raise ValueError(f"label shapes differ: reference {ref.shape}, candidate {cand.shape}")
    if not 0 <= threshold <= 1:
        raise ValueError("IoU threshold must be in [0, 1]")
    if np.array_equal(ref, cand):
        label_count = int(np.count_nonzero(np.unique(ref)))
        return MatchResult(
            threshold=threshold,
            true_positive=label_count,
            false_positive=0,
            false_negative=0,
            precision=1.0,
            recall=1.0,
            f1=1.0,
            matched_mean_iou=1.0 if label_count else None,
        )
    _, _, _, _, iou = _contingency(ref, cand)
    ri, ci = _threshold_assignment(iou, threshold)
    tp = int(ri.size)
    fp = int(iou.shape[1] - tp)
    fn = int(iou.shape[0] - tp)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 1.0
    mean_iou = float(iou[ri, ci].mean()) if tp else None
    return MatchResult(threshold, tp, fp, fn, precision, recall, f1, mean_iou)


def compare_pair(reference, candidate, name: str = "pair") -> ParityResult:
    ref = _labels(reference, "reference")
    cand = _labels(candidate, "candidate")
    if ref.shape != cand.shape:
        raise ValueError(f"label shapes differ: reference {ref.shape}, candidate {cand.shape}")
    if np.array_equal(ref, cand):
        return _identity_result(ref, name)
    ref_ids, cand_ids, ref_volumes, cand_volumes, iou = _contingency(ref, cand)
    if min(iou.shape, default=0):
        ri, ci = linear_sum_assignment(iou, maximize=True)
        positive = iou[ri, ci] > 0
        ri, ci = ri[positive], ci[positive]
    else:
        ri = ci = np.array([], dtype=int)
    matched = pd.DataFrame(
        {
            "reference_label": ref_ids[ri].astype(np.uint32, copy=False),
            "candidate_label": cand_ids[ci].astype(np.uint32, copy=False),
            "iou": iou[ri, ci],
            "reference_volume_voxels": ref_volumes[ri],
            "candidate_volume_voxels": cand_volumes[ci],
        }
    )
    if not matched.empty:
        matched["volume_mean_voxels"] = (
            matched.reference_volume_voxels + matched.candidate_volume_voxels
        ) / 2.0
        matched["volume_difference_voxels"] = (
            matched.candidate_volume_voxels - matched.reference_volume_voxels
        )
        matched["volume_difference_percent"] = (
            100.0 * matched.volume_difference_voxels / matched.reference_volume_voxels
        )
        ba_mean = float(matched.volume_difference_voxels.mean())
        # Population SD makes results independent of pandas' sample-size special case.
        ba_sd = float(matched.volume_difference_voxels.to_numpy(dtype=float).std(ddof=0))
        ba_lower, ba_upper = ba_mean - 1.96 * ba_sd, ba_mean + 1.96 * ba_sd
        matched["is_volume_outlier"] = (
            (matched.volume_difference_voxels < ba_lower)
            | (matched.volume_difference_voxels > ba_upper)
        )
    else:
        ba_mean = ba_lower = ba_upper = None
        matched["volume_mean_voxels"] = pd.Series(dtype=float)
        matched["volume_difference_voxels"] = pd.Series(dtype=float)
        matched["volume_difference_percent"] = pd.Series(dtype=float)
        matched["is_volume_outlier"] = pd.Series(dtype=bool)
    m50 = match_labels(ref, cand, 0.50)
    m75 = match_labels(ref, cand, 0.75)
    ref_foreground, cand_foreground = ref > 0, cand > 0
    union = np.count_nonzero(ref_foreground | cand_foreground)
    foreground_iou = (
        float(np.count_nonzero(ref_foreground & cand_foreground) / union) if union else 1.0
    )
    ref_count, cand_count = int(ref_ids.size), int(cand_ids.size)
    count_delta = cand_count - ref_count
    count_delta_percent = 100 * count_delta / ref_count if ref_count else (0.0 if not cand_count else float("inf"))
    if ref_volumes.size and cand_volumes.size:
        wasserstein = float(wasserstein_distance(ref_volumes, cand_volumes))
        ks = float(ks_2samp(ref_volumes, cand_volumes, method="asymp").statistic)
        ref_median, cand_median = float(np.median(ref_volumes)), float(np.median(cand_volumes))
        median_delta = 100 * (cand_median - ref_median) / ref_median if ref_median else 0.0
    else:
        wasserstein = ks = median_delta = 0.0 if not (ref_volumes.size or cand_volumes.size) else float("inf")
    causes: list[str] = []
    if count_delta > 0:
        causes.append("candidate over-segmentation, object splitting, or extra detections")
    elif count_delta < 0:
        causes.append("candidate under-segmentation, object merging, or missed detections")
    if np.isfinite(median_delta) and median_delta > 10:
        causes.append("candidate objects are systematically larger")
    elif np.isfinite(median_delta) and median_delta < -10:
        causes.append("candidate objects are systematically smaller")
    if m50.f1 < 0.9:
        causes.append("substantial object correspondence or boundary disagreement")
    outlier_count = int(matched.is_volume_outlier.sum())
    if outlier_count:
        causes.append("isolated matched-object volume outliers warrant image-level review")
    if not causes:
        causes.append("no strong systematic discrepancy detected")
    return ParityResult(
        name=name,
        reference_count=ref_count,
        candidate_count=cand_count,
        count_delta=count_delta,
        count_delta_percent=float(count_delta_percent),
        foreground_iou=foreground_iou,
        optimal_matched_mean_iou=float(matched.iou.mean()) if not matched.empty else None,
        f1_at_0_50=m50.f1,
        f1_at_0_75=m75.f1,
        matched_mean_iou_at_0_50=m50.matched_mean_iou,
        matched_mean_iou_at_0_75=m75.matched_mean_iou,
        volume_wasserstein_voxels=wasserstein,
        volume_ks_statistic=ks,
        median_volume_delta_percent=median_delta,
        matched_labels=matched,
        bland_altman_mean_difference_voxels=ba_mean,
        bland_altman_lower_limit_voxels=ba_lower,
        bland_altman_upper_limit_voxels=ba_upper,
        volume_outlier_count=outlier_count,
        likely_causes=tuple(causes),
    )


def _identity_result(labels: np.ndarray, name: str) -> ParityResult:
    """Return the exact dense-algorithm result without constructing an N x N matrix."""

    ids, volumes = np.unique(labels, return_counts=True)
    foreground = ids != 0
    ids = ids[foreground].astype(np.uint32, copy=False)
    volumes = volumes[foreground]
    matched = pd.DataFrame(
        {
            "reference_label": ids,
            "candidate_label": ids.copy(),
            "iou": np.ones(ids.size, dtype=float),
            "reference_volume_voxels": volumes,
            "candidate_volume_voxels": volumes.copy(),
            "volume_mean_voxels": volumes.astype(float, copy=False),
            "volume_difference_voxels": np.zeros(ids.size, dtype=np.int64),
            "volume_difference_percent": np.zeros(ids.size, dtype=float),
            "is_volume_outlier": np.zeros(ids.size, dtype=bool),
        }
    )
    count = int(ids.size)
    matched_mean = 1.0 if count else None
    return ParityResult(
        name=name,
        reference_count=count,
        candidate_count=count,
        count_delta=0,
        count_delta_percent=0.0,
        foreground_iou=1.0,
        optimal_matched_mean_iou=matched_mean,
        f1_at_0_50=1.0,
        f1_at_0_75=1.0,
        matched_mean_iou_at_0_50=matched_mean,
        matched_mean_iou_at_0_75=matched_mean,
        volume_wasserstein_voxels=0.0,
        volume_ks_statistic=0.0,
        median_volume_delta_percent=0.0,
        matched_labels=matched,
        bland_altman_mean_difference_voxels=0.0 if count else None,
        bland_altman_lower_limit_voxels=0.0 if count else None,
        bland_altman_upper_limit_voxels=0.0 if count else None,
        volume_outlier_count=0,
        likely_causes=("no strong systematic discrepancy detected",),
    )


def compare_reference_set(pairs: Iterable[tuple[str, object, object]]) -> pd.DataFrame:
    rows = [compare_pair(reference, candidate, name).summary() for name, reference, candidate in pairs]
    return pd.DataFrame(rows)


def render_parity_report(
    results: Iterable[ParityResult],
    output_dir: str | Path,
    *,
    title: str = "CellQuant parity report",
    performance: Mapping[str, Mapping[str, Mapping[str, float | int | None]]] | None = None,
    source_images: Mapping[str, object] | None = None,
) -> Path:
    import matplotlib

    matplotlib.use("Agg", force=True)
    from matplotlib import pyplot as plt

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    results = list(results)
    table = pd.DataFrame([result.summary() for result in results])
    table.to_csv(output / "parity_table.csv", index=False)
    for result in results:
        result.matched_labels.to_csv(output / f"{Path(result.name).stem}_matched_labels.csv", index=False)
        result.matched_labels[result.matched_labels.is_volume_outlier].to_csv(
            output / f"{Path(result.name).stem}_volume_outliers.csv", index=False
        )
    fig, axes = plt.subplots(1, 3, figsize=(14, 4), layout="constrained")
    if results:
        distributions = [result.matched_labels.iou.to_numpy() for result in results if not result.matched_labels.empty]
        if distributions:
            axes[0].hist(
                np.concatenate(distributions),
                bins=np.linspace(0, 1, 21),
                histtype="stepfilled",
                color="#3478b8",
                alpha=0.65,
            )
        axes[1].scatter(table.reference_count, table.count_delta, s=24)
        for result in results:
            matched = result.matched_labels
            if not matched.empty:
                axes[2].scatter(
                    matched.volume_mean_voxels,
                    matched.volume_difference_voxels,
                    s=12,
                    alpha=0.6,
                    label=result.name,
                )
                axes[2].axhline(
                    result.bland_altman_mean_difference_voxels,
                    color="#555555",
                    linewidth=0.6,
                    alpha=0.45,
                )
                axes[2].axhline(
                    result.bland_altman_lower_limit_voxels,
                    color="#b04444",
                    linewidth=0.6,
                    alpha=0.35,
                    linestyle="--",
                )
                axes[2].axhline(
                    result.bland_altman_upper_limit_voxels,
                    color="#b04444",
                    linewidth=0.6,
                    alpha=0.35,
                    linestyle="--",
                )
    axes[0].set(xlabel="Matched-label IoU", ylabel="Labels", title="IoU distributions")
    axes[1].axhline(0, color="black", linewidth=0.8)
    axes[1].set(xlabel="Reference label count", ylabel="Candidate - reference", title="Count difference")
    axes[2].axhline(0, color="black", linewidth=0.8)
    axes[2].set(
        xlabel="Mean matched volume (voxels)",
        ylabel="Candidate - reference (voxels)",
        title="Bland–Altman volume analysis",
    )
    fig.suptitle(title)
    figure_path = output / "parity_summary.png"
    fig.savefig(figure_path, dpi=150, metadata={"Software": "cellquant"})
    plt.close(fig)
    performance_rows = _performance_rows(performance or {})
    if performance_rows:
        pd.DataFrame(performance_rows).to_csv(output / "performance_comparison.csv", index=False)
    if source_images:
        by_name = {result.name: result for result in results}
        for name, source in sorted(source_images.items()):
            if name in by_name:
                _render_outlier_overlay(by_name[name], source, output, plt)
    payload = {
        "title": title,
        "stack_count": len(results),
        "aggregate": {
            "mean_optimal_matched_iou": float(table.optimal_matched_mean_iou.mean()) if len(table) else None,
            "mean_f1_at_0_50": float(table.f1_at_0_50.mean()) if len(table) else None,
            "mean_f1_at_0_75": float(table.f1_at_0_75.mean()) if len(table) else None,
        },
        "results": [result.summary() for result in results],
        "performance": performance_rows,
    }
    report_path = output / "parity_report.json"
    report_path.write_text(
        json.dumps(_json_safe(payload), indent=2, allow_nan=False), encoding="utf-8"
    )
    return report_path


def _performance_rows(
    performance: Mapping[str, Mapping[str, Mapping[str, float | int | None]]]
) -> list[dict]:
    """Flatten optional baseline/candidate resource records with safe ratios."""

    rows: list[dict] = []
    aliases = {
        "runtime_seconds": ("runtime_seconds", "wall_seconds"),
        "ram_bytes": ("ram_bytes", "peak_host_ram_bytes"),
        "vram_bytes": ("vram_bytes", "peak_vram_allocated_bytes"),
    }

    def value(record, names):
        for field in names:
            if isinstance(record, Mapping) and field in record:
                return record[field]
            if hasattr(record, field):
                return getattr(record, field)
        return None

    for name in sorted(performance):
        record = performance[name]
        row: dict[str, object] = {"name": name}
        baseline_record = record.get("baseline", {})
        candidate_record = record.get("candidate", {})
        for metric, names in aliases.items():
            baseline = value(baseline_record, names)
            candidate = value(candidate_record, names)
            row[f"baseline_{metric}"] = baseline
            row[f"candidate_{metric}"] = candidate
            row[f"candidate_to_baseline_{metric}_ratio"] = (
                float(candidate) / float(baseline)
                if baseline not in (None, 0) and candidate is not None
                else None
            )
        rows.append(row)
    return rows


def _json_safe(value):
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def _render_outlier_overlay(result: ParityResult, source, output: Path, plt) -> None:
    """Write a deterministic source overlay when label volumes accompany the image."""

    if not isinstance(source, Mapping) or "reference" not in source or "candidate" not in source:
        return
    reference = _labels(source["reference"], "reference")
    candidate = _labels(source["candidate"], "candidate")
    image = np.asarray(getattr(source.get("image"), "data", source.get("image")))
    if image.ndim == 4:
        image = image[..., 0]
    if image.ndim != 3 or image.shape != reference.shape or candidate.shape != reference.shape:
        return
    base = np.max(image.astype(np.float32, copy=False), axis=0)
    lo, hi = np.percentile(base, (1, 99))
    base = np.clip((base - lo) / (hi - lo), 0, 1) if hi > lo else np.zeros_like(base)
    from scipy.ndimage import binary_erosion

    ref_fg, cand_fg = reference > 0, candidate > 0
    ref_edge = np.max(ref_fg ^ binary_erosion(ref_fg), axis=0)
    cand_edge = np.max(cand_fg ^ binary_erosion(cand_fg), axis=0)
    rgb = np.repeat(base[..., None], 3, axis=-1)
    rgb[ref_edge] = (1.0, 0.2, 0.2)
    rgb[cand_edge] = (0.1, 0.9, 1.0)
    outliers = result.matched_labels[result.matched_labels.is_volume_outlier]
    if not outliers.empty:
        ids = outliers.candidate_label.to_numpy(dtype=np.uint32)
        mask = np.max(np.isin(candidate, ids), axis=0)
        rgb[mask] = 0.55 * rgb[mask] + 0.45 * np.array([1.0, 1.0, 0.0])
    fig, ax = plt.subplots(figsize=(6, 6), layout="constrained")
    ax.imshow(rgb)
    ax.set(title=f"{result.name}: reference red, candidate cyan, outliers yellow")
    ax.axis("off")
    fig.savefig(
        output / f"{Path(result.name).stem}_outlier_overlay.png",
        dpi=150,
        metadata={"Software": "cellquant"},
    )
    plt.close(fig)
