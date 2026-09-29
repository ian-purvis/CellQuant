"""Synthetic retina z-stacks with a known answer.

The images imitate confocal z-stacks of embryonic mouse retina like the ones
CellQuant was built for (Nikon ND2, 20x water objective): three channels named
"Green", "Red" and "Far Red" with the microscope's display colors, 12-bit values
in a 16-bit file with a camera offset, 7 slices 1.5 µm apart, 0.575 µm pixels,
crowded, touching, slightly elongated nuclei in a curved band of tissue, blur
that is stronger along Z, dimmer deep slices, uneven illumination, shot and read
noise, dark nucleoli, a few saturated specks, and marker-positive nuclei that
are sometimes dim or labelled in only part of the nucleus.

Nothing here comes from a real image. Every nucleus is drawn from a recorded
plan, so the right answer is known exactly:

- ``<name>.tif``: the image (ImageJ hyperstack, Z × C × Y × X, uint16) with
  channel names, colors and pixel sizes, readable by CellQuant, Fiji and napari.
- ``<name>_labels.tif``: the true nuclei (Z × Y × X, 0 = background).
- ``<name>_truth.csv``: one row per nucleus: label, centroid, voxels, and
  whether it is Fluor+ (Red), OTX2+ (Green) and PAX6-high (Far Red), and how its
  OTX2 is distributed ("whole", "partial" or "none").
- ``truth_summary.csv`` (one row per image) and ``README.txt`` in the folder.

Channel roles follow the lab's naming (mCherry reporter, OTX2, PAX6):

- Red (mCherry, "Fluor"): a reporter in a subset of cells, filling the nucleus
  and a thin rim of cytoplasm around it.
- Green (OTX2): nuclear, in a subset of cells.
- Far Red (PAX6): nuclear, in every nucleus at a high or low level, so it can be
  used to find every nucleus.

The headline number is the percent of Fluor+ nuclei that are also OTX2+,
``(Fluor+ OTX2+) / Fluor+``; ``write_retina_set`` makes Control and CRISPRi
images with different targets for it (31% and 24% by default).

Command line::

    python -m cellquant.synthetic_retina OUTPUT_FOLDER [--size 256] [--retinas 2] [--seed 11]
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import tifffile
from scipy import ndimage

PIXEL_SIZE_UM = 0.5754449716687396  # 20x, as in the lab's ND2 files
Z_STEP_UM = 1.5
CHANNEL_NAMES = ("Green", "Red", "Far Red")
# Display colors of the three channels in NIS-Elements (RGB, 0-255).
CHANNEL_COLORS = ((54, 255, 0), (255, 0, 0), (255, 0, 255))
# Camera offset per channel (the darkest values in the real files).
OFFSETS = (27.0, 80.0, 51.0)
FULL_SCALE = 4095  # 12-bit camera
GREEN, RED, FAR_RED = 0, 1, 2


@dataclass
class RetinaPlan:
    """What one synthetic image contains. Every count is exact in the output."""

    name: str
    condition: str = "Control"
    size: int = 256
    slices: int = 7
    n_nuclei: int = 420
    fluor_percent: float = 35.0  # percent of nuclei that are Fluor+
    otx2_of_fluor_percent: float = 31.0  # percent of Fluor+ nuclei that are OTX2+ (the headline)
    otx2_of_other_percent: float = 30.0  # percent of Fluor- nuclei that are OTX2+
    pax6_high_percent: float = 70.0
    partial_otx2_percent: float = 25.0  # of OTX2+ nuclei, labelled in only part of the nucleus
    dim_positive_percent: float = 15.0  # of positives, near the negatives in brightness
    seed: int = 11
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Nucleus:
    label: int
    center_um: tuple[float, float, float]  # z, y, x
    radii_um: tuple[float, float, float]
    angle: float
    fluor: bool
    otx2: bool
    otx2_pattern: str
    pax6_high: bool
    brightness: tuple[float, float, float]  # Green, Red, Far Red peak above offset


def write_retina_image(plan: RetinaPlan, folder: str | Path) -> dict:
    """Write one image, its true labels and its truth table. Returns the summary row."""

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(plan.seed)
    shape = (plan.slices, plan.size, plan.size)
    band, normal_angle = _tissue_band(plan.size, rng)
    nuclei = _place_nuclei(plan, band, normal_angle, rng)
    labels = _rasterize(nuclei, shape)
    present = set(np.unique(labels[labels > 0]).tolist())
    nuclei = [item for item in nuclei if item.label in present]
    image = _render(nuclei, labels, band, shape, rng)

    tifffile.imwrite(
        folder / f"{plan.name}.tif",
        image,
        imagej=True,
        resolution=(1 / PIXEL_SIZE_UM, 1 / PIXEL_SIZE_UM),
        metadata={
            "axes": "ZCYX",
            "unit": "um",
            "spacing": Z_STEP_UM,
            "Labels": list(CHANNEL_NAMES),
            "LUTs": _luts(),
        },
    )
    tifffile.imwrite(
        folder / f"{plan.name}_labels.tif",
        labels.astype(np.uint16 if labels.max() < 65535 else np.uint32),
        imagej=True,
        resolution=(1 / PIXEL_SIZE_UM, 1 / PIXEL_SIZE_UM),
        metadata={"axes": "ZYX", "unit": "um", "spacing": Z_STEP_UM},
    )
    voxels = np.bincount(labels.ravel(), minlength=max(present, default=0) + 1)
    with (folder / f"{plan.name}_truth.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["label", "z_um", "y_um", "x_um", "voxels", "fluor", "otx2", "otx2_pattern", "pax6_high"])
        for item in nuclei:
            z, y, x = item.center_um
            writer.writerow(
                [item.label, f"{z:.3f}", f"{y:.3f}", f"{x:.3f}", int(voxels[item.label]), int(item.fluor), int(item.otx2), item.otx2_pattern, int(item.pax6_high)]
            )
    fluor = [item for item in nuclei if item.fluor]
    both = [item for item in fluor if item.otx2]
    return {
        "image": f"{plan.name}.tif",
        "condition": plan.condition,
        "n_nuclei": len(nuclei),
        "n_fluor": len(fluor),
        "n_otx2": sum(item.otx2 for item in nuclei),
        "n_fluor_otx2": len(both),
        "n_pax6_high": sum(item.pax6_high for item in nuclei),
        "pct_otx2_of_fluor": round(100.0 * len(both) / len(fluor), 3) if fluor else float("nan"),
        "pct_fluor": round(100.0 * len(fluor) / len(nuclei), 3) if nuclei else float("nan"),
    }


def write_retina_set(
    folder: str | Path,
    *,
    size: int = 256,
    retinas: int = 2,
    control_percent: float = 31.0,
    crispri_percent: float = 24.0,
    seed: int = 11,
) -> list[dict]:
    """Control and CRISPRi images laid out like the lab's folders:

    ``<folder>/Control/Retina 1/control_r1.tif``, ``<folder>/CRISPRi/Retina 1/crispri_r1.tif``, ...
    """

    folder = Path(folder)
    rows = []
    for condition, percent, offset in (("Control", control_percent, 0), ("CRISPRi", crispri_percent, 1000)):
        for index in range(1, retinas + 1):
            plan = RetinaPlan(
                name=f"{condition.casefold()}_r{index}",
                condition=condition,
                size=size,
                n_nuclei=_nuclei_for(size),
                otx2_of_fluor_percent=percent,
                seed=seed + offset + index,
            )
            row = write_retina_image(plan, folder / condition / f"Retina {index}")
            row["image"] = f"{condition}/Retina {index}/{row['image']}"
            rows.append(row)
    with (folder / "truth_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (folder / "README.txt").write_text(_readme(rows), encoding="utf-8")
    return rows


# --- geometry ------------------------------------------------------------------------------------


def _nuclei_for(size: int) -> int:
    # About 420 tightly packed nuclei in a 256 × 256 field (20x); the same density for other sizes.
    return max(20, int(round(420 * (size / 256) ** 2)))


def _tissue_band(size: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """A curved band of tissue (the neuroblastic layer) across the field, and the direction across it.

    Returns the band (Y × X, True inside) and, per column, the angle of the band's normal: retinal
    progenitor nuclei are elongated along it (apical to basal).
    """

    x = np.arange(size, dtype=float)
    frequency, phase = rng.uniform(0.5, 0.9), rng.uniform(0, 2 * np.pi)
    amplitude = size * rng.uniform(0.08, 0.14)
    curve = size * 0.5 + amplitude * np.sin(2 * np.pi * frequency * x / size + phase)
    slope = amplitude * 2 * np.pi * frequency / size * np.cos(2 * np.pi * frequency * x / size + phase)
    half_width = size * rng.uniform(0.30, 0.36)
    yy = np.arange(size, dtype=float)[:, None]
    band = np.abs(yy - curve[None, :]) <= half_width
    normal_angle = np.arctan2(-slope, 1.0)  # (cos, sin) of this angle is the (y, x) normal
    return band, normal_angle


def _place_nuclei(plan: RetinaPlan, band: np.ndarray, normal_angle: np.ndarray, rng: np.random.Generator) -> list[Nucleus]:
    slices = plan.slices
    depth_um = slices * Z_STEP_UM
    inside = np.argwhere(band)
    centers: list[np.ndarray] = []
    angles: list[float] = []
    min_gap_um = 4.8  # measured across the nucleus; along its long axis the gap may be twice this
    attempts = 0
    while len(centers) < plan.n_nuclei and attempts < plan.n_nuclei * 600:
        attempts += 1
        y, x = inside[rng.integers(len(inside))] + rng.uniform(-0.5, 0.5, 2)
        z = rng.uniform(-1.0, depth_um - 0.5)  # some nuclei are cut by the top or bottom of the stack
        point = np.array([z, y * PIXEL_SIZE_UM, x * PIXEL_SIZE_UM])
        angle = float(normal_angle[min(int(x), len(normal_angle) - 1)] + rng.normal(0.0, 0.25))
        if centers:
            offset = np.asarray(centers) - point
            along = offset[:, 1] * np.cos(angle) + offset[:, 2] * np.sin(angle)
            across = -offset[:, 1] * np.sin(angle) + offset[:, 2] * np.cos(angle)
            # Neighbours may sit above one another (Z counts less) or end to end (along counts half).
            gaps = np.sqrt((0.9 * offset[:, 0]) ** 2 + (0.5 * along) ** 2 + across**2)
            if gaps.min() < min_gap_um:
                continue
        centers.append(point)
        angles.append(angle)
    count = len(centers)
    order = rng.permutation(count)
    n_fluor = int(round(count * plan.fluor_percent / 100))
    fluor_ids = set(order[:n_fluor].tolist())
    fluor_list, other_list = order[:n_fluor].tolist(), order[n_fluor:].tolist()
    n_both = int(round(n_fluor * plan.otx2_of_fluor_percent / 100))
    n_other = int(round(len(other_list) * plan.otx2_of_other_percent / 100))
    otx2_ids = set(fluor_list[:n_both]) | set(other_list[:n_other])
    pax6_ids = set(rng.permutation(count)[: int(round(count * plan.pax6_high_percent / 100))].tolist())
    otx2_sorted = sorted(otx2_ids)
    partial_ids = set(rng.permutation(otx2_sorted)[: int(round(len(otx2_sorted) * plan.partial_otx2_percent / 100))].tolist()) if otx2_sorted else set()

    nuclei = []
    for index, center in enumerate(centers):
        radii = (rng.uniform(2.3, 3.2), rng.uniform(4.2, 6.2), rng.uniform(2.2, 2.9))  # z, long, short (µm)
        fluor, otx2, pax6 = index in fluor_ids, index in otx2_ids, index in pax6_ids
        dim = rng.uniform() < plan.dim_positive_percent / 100
        green = (rng.lognormal(np.log(140 if dim else 700), 0.35)) if otx2 else rng.lognormal(np.log(14), 0.4)
        red = (rng.lognormal(np.log(90 if dim else 380), 0.35)) if fluor else rng.lognormal(np.log(6), 0.4)
        far_red = rng.lognormal(np.log(800), 0.3) if pax6 else rng.lognormal(np.log(230), 0.35)
        nuclei.append(
            Nucleus(
                label=index + 1,
                center_um=(float(center[0]), float(center[1]), float(center[2])),
                radii_um=radii,
                angle=angles[index],
                fluor=fluor,
                otx2=otx2,
                otx2_pattern=("partial" if index in partial_ids else "whole") if otx2 else "none",
                pax6_high=pax6,
                brightness=(float(green), float(red), float(far_red)),
            )
        )
    return nuclei


def _rasterize(nuclei: list[Nucleus], shape: tuple[int, int, int]) -> np.ndarray:
    """Label every voxel with the nucleus whose ellipsoid it is deepest inside (touching nuclei split cleanly)."""

    labels = np.zeros(shape, dtype=np.int32)
    best = np.full(shape, np.inf)
    zc, yc, xc = (np.arange(n, dtype=float) for n in shape)
    zc *= Z_STEP_UM
    yc *= PIXEL_SIZE_UM
    xc *= PIXEL_SIZE_UM
    for item in nuclei:
        cz, cy, cx = item.center_um
        rz, ra, rb = item.radii_um
        reach = max(ra, rb)
        z0, z1 = _span(cz, rz, Z_STEP_UM, shape[0])
        y0, y1 = _span(cy, reach, PIXEL_SIZE_UM, shape[1])
        x0, x1 = _span(cx, reach, PIXEL_SIZE_UM, shape[2])
        if z0 >= z1 or y0 >= y1 or x0 >= x1:
            continue
        dz = (zc[z0:z1] - cz)[:, None, None]
        dy = (yc[y0:y1] - cy)[None, :, None]
        dx = (xc[x0:x1] - cx)[None, None, :]
        cos, sin = np.cos(item.angle), np.sin(item.angle)
        along = dy * cos + dx * sin
        across = -dy * sin + dx * cos
        distance = (dz / rz) ** 2 + (along / ra) ** 2 + (across / rb) ** 2
        window = (slice(z0, z1), slice(y0, y1), slice(x0, x1))
        take = (distance <= 1.0) & (distance < best[window])
        labels[window][take] = item.label
        best[window][take] = distance[take]
    return labels


def _span(center: float, radius: float, step: float, length: int) -> tuple[int, int]:
    return max(0, int(np.floor((center - radius) / step))), min(length, int(np.ceil((center + radius) / step)) + 1)


# --- intensities ---------------------------------------------------------------------------------


def _render(nuclei: list[Nucleus], labels: np.ndarray, band: np.ndarray, shape, rng: np.random.Generator) -> np.ndarray:
    slices, size, _ = shape
    by_label = np.zeros((3, labels.max() + 1), dtype=np.float64)
    for item in nuclei:
        by_label[:, item.label] = item.brightness
    signal = np.stack([by_label[channel][labels] for channel in range(3)])

    # Texture: grainy chromatin and one or two dark nucleoli per nucleus.
    grain = ndimage.gaussian_filter(rng.normal(1.0, 0.18, shape), (0.4, 0.8, 0.8))
    signal *= np.clip(grain, 0.5, 1.5)[None]
    nucleoli = np.ones(shape)
    for item in nuclei:
        for _ in range(rng.integers(1, 3)):
            cz, cy, cx = item.center_um
            spot = (
                int(round((cz + rng.uniform(-1, 1)) / Z_STEP_UM)),
                int(round((cy + rng.uniform(-1.2, 1.2)) / PIXEL_SIZE_UM)),
                int(round((cx + rng.uniform(-1.2, 1.2)) / PIXEL_SIZE_UM)),
            )
            if all(0 <= value < limit for value, limit in zip(spot, shape)) and labels[spot] == item.label:
                nucleoli[spot] = 0.35
    nucleoli = ndimage.minimum_filter(nucleoli, size=(1, 2, 2))
    signal[GREEN] *= nucleoli
    signal[FAR_RED] *= nucleoli

    # OTX2 in only part of some nuclei: one side of a random plane through the nucleus.
    for item in nuclei:
        if item.otx2_pattern != "partial":
            continue
        voxels = np.argwhere(labels == item.label)
        if len(voxels) == 0:
            continue
        direction = rng.normal(size=3)
        direction /= np.linalg.norm(direction)
        centered = (voxels - voxels.mean(axis=0)) * (Z_STEP_UM, PIXEL_SIZE_UM, PIXEL_SIZE_UM)
        projection = centered @ direction
        cut = np.quantile(projection, rng.uniform(0.35, 0.65))
        dark = voxels[projection < cut]
        signal[GREEN][tuple(dark.T)] *= 0.12

    # The reporter also fills a thin rim of cytoplasm.
    fluor_labels = [item.label for item in nuclei if item.fluor]
    if fluor_labels:
        fluor_mask = np.isin(labels, fluor_labels)
        grown, owner = _grow(labels, fluor_mask, radius_px=2)
        rim = grown & (labels == 0)
        signal[RED][rim] = 0.45 * by_label[RED][owner[rim]]

    # Tissue autofluorescence, a few saturated specks, optics, illumination and depth.
    tissue = np.broadcast_to(band[None], shape).astype(float)
    haze = ndimage.gaussian_filter(rng.gamma(2.0, 0.5, shape), (0.5, 1.2, 1.2)) * tissue
    signal += haze[None] * np.array([34.0, 16.0, 28.0])[:, None, None, None]
    specks = rng.integers(0, slices, 6), rng.integers(0, size, 6), rng.integers(0, size, 6)
    signal[RED][specks] = 20000.0
    signal = np.stack([ndimage.gaussian_filter(channel, (0.8, 1.2, 1.2)) for channel in signal])
    yy, xx = np.mgrid[:size, :size]
    radius2 = ((yy - size / 2) ** 2 + (xx - size / 2) ** 2) / (size / 2) ** 2
    illumination = 1.0 - 0.22 * radius2
    depth = np.exp(-0.07 * np.arange(slices))[:, None, None]
    signal *= (illumination[None] * depth)[None]

    # Camera: shot noise (gain 2 counts per photon), read noise, offset, 12-bit ceiling.
    gain = 2.0
    counts = rng.poisson(np.clip(signal, 0, None) / gain) * gain
    counts = counts + rng.normal(0.0, 2.5, counts.shape) + np.asarray(OFFSETS)[:, None, None, None]
    counts = np.clip(np.round(counts), 0, FULL_SCALE).astype(np.uint16)
    return np.ascontiguousarray(np.moveaxis(counts, 0, 1))  # Z, C, Y, X


def _grow(labels: np.ndarray, mask: np.ndarray, radius_px: int) -> tuple[np.ndarray, np.ndarray]:
    """Grow ``mask`` by ``radius_px`` in XY and return the owning label of each grown voxel."""

    distance, indices = ndimage.distance_transform_edt(~mask, sampling=(Z_STEP_UM / PIXEL_SIZE_UM * 4, 1.0, 1.0), return_indices=True)
    grown = distance <= radius_px
    owner = labels[tuple(indices)]
    return grown, owner


def _luts() -> list:
    ramp = np.arange(256, dtype=np.float64) / 255.0
    return [np.round(np.stack([ramp * value for value in color])).astype(np.uint8) for color in CHANNEL_COLORS]


def _readme(rows: list[dict]) -> str:
    lines = [
        "Synthetic retina z-stacks made by cellquant.synthetic_retina. No real image data.",
        "",
        "Each image: 7 slices (1.5 um apart), 3 channels (Green = OTX2, Red = mCherry reporter 'Fluor',",
        "Far Red = PAX6, present in every nucleus), 12-bit values in 16-bit files, 0.575 um pixels.",
        "<name>_labels.tif holds the true nuclei; <name>_truth.csv says which are Fluor+, OTX2+, PAX6-high.",
        "",
        "Known answers, (Fluor+ OTX2+) / Fluor+:",
    ]
    for row in rows:
        lines.append(
            f"  {row['image']}: {row['n_fluor_otx2']} of {row['n_fluor']} Fluor+ nuclei ({row['pct_otx2_of_fluor']:.1f}%); "
            f"{row['n_nuclei']} nuclei in all"
        )
    lines += [
        "",
        "Suggested settings: find nuclei in Far Red; measure Green and Red in each nucleus.",
        "Some OTX2+ nuclei are dim, and some are labelled in only part of the nucleus",
        "(otx2_pattern = partial): compare the mean-brightness rule with the percent-of-cell rule.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m cellquant.synthetic_retina", description=__doc__.split("\n\n")[0])
    parser.add_argument("output", help="folder to write (created if needed)")
    parser.add_argument("--size", type=int, default=256, help="image width and height in pixels (1024 matches the real files)")
    parser.add_argument("--retinas", type=int, default=2, help="images per condition")
    parser.add_argument("--control-percent", type=float, default=31.0)
    parser.add_argument("--crispri-percent", type=float, default=24.0)
    parser.add_argument("--seed", type=int, default=11)
    args = parser.parse_args(argv)
    rows = write_retina_set(
        args.output,
        size=args.size,
        retinas=args.retinas,
        control_percent=args.control_percent,
        crispri_percent=args.crispri_percent,
        seed=args.seed,
    )
    print(_readme(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
