"""Generic multichannel segmentation and quantification engine.

The analysis functions in this package do not depend on a viewer. Interactive
and batch runs are expected to call the same ``process_image`` entry point.
"""

from cellquant.__version__ import __version__
from cellquant.errors import (
    CalibrationError,
    CellQuantError,
    ChannelMismatchError,
    ImageLoadError,
    RecipeValidationError,
    SegmentationError,
)
from cellquant.pipeline import export_image_result, load_image, process_image
from cellquant.quantify import (
    classify_objects,
    generate_phenotypes,
    measure_objects,
    summarize_image,
)
from cellquant.recipe import Recipe, load_recipe, save_recipe
from cellquant.regions import create_measurement_region
from cellquant.segmentation import filter_objects, register_segmentation_backend, segment_objects

__all__ = [
    "CalibrationError",
    "CellQuantError",
    "ChannelMismatchError",
    "ImageLoadError",
    "Recipe",
    "RecipeValidationError",
    "SegmentationError",
    "__version__",
    "classify_objects",
    "create_measurement_region",
    "export_image_result",
    "filter_objects",
    "generate_phenotypes",
    "load_image",
    "load_recipe",
    "measure_objects",
    "process_image",
    "register_segmentation_backend",
    "save_recipe",
    "segment_objects",
    "summarize_image",
]
