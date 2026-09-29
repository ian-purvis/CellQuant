"""Practice images with a known answer.

Three small, three-channel images: a nuclear stain and two markers. Every
nucleus is either clearly positive or clearly negative for each marker, so a
first-time user can check that their settings give the expected numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import tifffile

from cellquant.controller import AnalysisController

PIXEL_SIZE_UM = 0.5
CHANNEL_NAMES = ("Nuclei", "Marker A", "Marker B")
# Display colors stored in the practice TIFFs, as a microscope would: blue, green, red.
CHANNEL_COLORS = ((0, 0, 255), (0, 255, 0), (255, 0, 0))


def _luts() -> list:
    ramp = np.arange(256, dtype=np.uint8)
    return [np.stack([ramp * (value // 255) for value in color]).astype(np.uint8) for color in CHANNEL_COLORS]
# Per image: (number of nuclei, number positive for A, number positive for B, number positive for both)
PLAN = ((40, 10, 20, 5), (40, 20, 10, 5), (40, 30, 20, 15))


@dataclass(frozen=True)
class PracticeAnswer:
    filename: str
    nuclei: int
    marker_a: int
    marker_b: int
    both: int

    @property
    def percent_a(self) -> float:
        return 100.0 * self.marker_a / self.nuclei


def expected_answers() -> list[PracticeAnswer]:
    return [
        PracticeAnswer(f"practice_{index + 1}.tif", total, a, b, both)
        for index, (total, a, b, both) in enumerate(PLAN)
    ]


def write_practice_images(folder: str | Path, seed: int = 7) -> list[Path]:
    """Write the practice TIFFs. Nuclei are well separated discs on a grid."""

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    paths = []
    size = 400
    yy, xx = np.mgrid[:size, :size]
    for answer in expected_answers():
        image = np.zeros((3, size, size), dtype=np.float32)
        # 8 x 5 grid of nuclei, jittered, radius 11-15 px (5.5-7.5 um).
        centres = [(40 + 45 * (k // 8) + rng.integers(-6, 7), 30 + 48 * (k % 8) + rng.integers(-6, 7)) for k in range(answer.nuclei)]
        order = rng.permutation(answer.nuclei)
        positive_a = set(order[: answer.marker_a].tolist())
        # "both" nuclei come from the A-positive set; the rest of B from the A-negative set.
        a_list = order[: answer.marker_a].tolist()
        not_a = order[answer.marker_a :].tolist()
        positive_b = set(a_list[: answer.both]) | set(not_a[: answer.marker_b - answer.both])
        for index, (cy, cx) in enumerate(centres):
            radius = rng.integers(11, 16)
            disc = (yy - cy) ** 2 + (xx - cx) ** 2 <= radius * radius
            image[0][disc] = rng.uniform(900, 1300)
            image[1][disc] = rng.uniform(600, 900) if index in positive_a else rng.uniform(40, 120)
            image[2][disc] = rng.uniform(600, 900) if index in positive_b else rng.uniform(40, 120)
        image += rng.normal(30, 6, image.shape)
        path = folder / answer.filename
        tifffile.imwrite(
            path,
            np.clip(image, 0, 65535).astype(np.uint16),
            imagej=True,
            resolution=(1 / PIXEL_SIZE_UM, 1 / PIXEL_SIZE_UM),
            metadata={"axes": "CYX", "unit": "um", "LUTs": _luts()},
        )
        paths.append(path)
    return paths


def create_practice_experiment(folder: str | Path) -> AnalysisController:
    """Practice images plus an experiment with channels named and nuclei settings chosen.

    Markers and thresholds are left for the user, so the practice covers every step.
    """

    folder = Path(folder)
    paths = write_practice_images(folder / "images")
    controller = AnalysisController.create(folder / "experiment", "Practice experiment")
    controller.add_image_paths(paths)
    for record in controller.experiment.images:
        # ImageJ TIFFs store pixel size per centimetre in some readers; set it explicitly.
        controller.set_pixel_size(record.image_id, PIXEL_SIZE_UM, PIXEL_SIZE_UM)
    for index, name in enumerate(CHANNEL_NAMES):
        controller.set_channel_name(index, name)
    controller.set_recipe(
        {
            "recipe_name": "Practice",
            "object_set": {
                "name": "Nuclei",
                "segmentation_channel": 0,
                "algorithm": "classical",
                "parameters": {"threshold_method": "otsu", "sigma": 1.0, "min_area_um2": 20},
            },
        }
    )
    controller.save()
    (folder / "README.txt").write_text(
        "CellQuant practice experiment\n\n"
        "Three images, 40 nuclei each. Expected results:\n"
        + "".join(
            f"  {item.filename}: {item.marker_a} Marker A+ ({item.percent_a:.0f}%), "
            f"{item.marker_b} Marker B+, {item.both} positive for both\n"
            for item in expected_answers()
        ),
        encoding="utf-8",
    )
    return controller
