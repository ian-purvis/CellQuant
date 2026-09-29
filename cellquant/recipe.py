"""Typed analysis recipe.

A recipe stores scientific settings only. Manual edits and image paths belong
to an analysis run, not to the recipe.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from cellquant.errors import RecipeValidationError

RESERVED_COLUMN_NAMES = frozenset(
    {
        "experiment_id",
        "run_id",
        "sample_name",
        "image_id",
        "filename",
        "object_set",
        "object_id",
        "centroid_x",
        "centroid_y",
        "area",
        "phenotype",
        "excluded",
        "unmeasured",
        "centroid_z",
        "volume",
        "z_slices",
        "z_first",
        "z_last",
        "z_flag",
    }
)


class RegionSpec(BaseModel):
    """Where a measurement is taken relative to each segmented object."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["object", "eroded_object", "expanded_object", "ring"]
    distance_um: float | None = None
    distance_px: float | None = None
    inner_um: float | None = None
    inner_px: float | None = None
    outer_um: float | None = None
    outer_px: float | None = None

    @model_validator(mode="after")
    def _check_distances(self) -> RegionSpec:
        if self.type == "object":
            if any(
                value is not None
                for value in (
                    self.distance_um,
                    self.distance_px,
                    self.inner_um,
                    self.inner_px,
                    self.outer_um,
                    self.outer_px,
                )
            ):
                raise ValueError("An object region does not take a distance")
            return self
        if self.type in {"eroded_object", "expanded_object"}:
            _exactly_one_length(
                self.distance_um,
                self.distance_px,
                "distance_um or distance_px",
            )
            _non_negative(self.distance_um, "distance_um")
            _non_negative(self.distance_px, "distance_px")
            return self
        inner_unit = _exactly_one_length(self.inner_um, self.inner_px, "inner_um or inner_px")
        outer_unit = _exactly_one_length(self.outer_um, self.outer_px, "outer_um or outer_px")
        if inner_unit != outer_unit:
            raise ValueError("Ring inner and outer distances must use the same unit")
        _non_negative(self.inner_um, "inner_um")
        _non_negative(self.inner_px, "inner_px")
        _non_negative(self.outer_um, "outer_um")
        _non_negative(self.outer_px, "outer_px")
        inner = self.inner_um if inner_unit == "um" else self.inner_px
        outer = self.outer_um if outer_unit == "um" else self.outer_px
        if inner is None or outer is None or not inner < outer:
            raise ValueError("Ring inner distance must be less than the outer distance")
        return self


class BackgroundSpec(BaseModel):
    """How intensity statistics are corrected before classification."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["none", "global", "local_ring"] = "none"
    value: float | None = None
    inner_um: float | None = None
    inner_px: float | None = None
    outer_um: float | None = None
    outer_px: float | None = None

    @model_validator(mode="after")
    def _check(self) -> BackgroundSpec:
        if self.type == "none":
            return self
        if self.type == "global":
            return self
        _exactly_one_length(self.inner_um, self.inner_px, "inner_um or inner_px")
        _exactly_one_length(self.outer_um, self.outer_px, "outer_um or outer_px")
        inner_unit = "um" if self.inner_um is not None else "px"
        outer_unit = "um" if self.outer_um is not None else "px"
        if inner_unit != outer_unit:
            raise ValueError("Local-ring inner and outer distances must use the same unit")
        inner = self.inner_um if inner_unit == "um" else self.inner_px
        outer = self.outer_um if outer_unit == "um" else self.outer_px
        if inner is None or outer is None or not inner < outer:
            raise ValueError("Local-ring inner distance must be less than the outer distance")
        return self


class MeasurementSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str | None = None
    channel: int
    region: RegionSpec
    statistic: Literal[
        "mean",
        "median",
        "min",
        "max",
        "std",
        "integrated",
        "area",
        "equivalent_diameter",
        "centroid_x",
        "centroid_y",
        "percent_above",
    ]
    background: BackgroundSpec = Field(default_factory=BackgroundSpec)
    # percent_above only: the percent (0-100) of the region's pixels whose value
    # (after background correction, if any) is at least pixel_level and, when
    # pixel_level_high is set, at most pixel_level_high.
    pixel_level: float | None = None
    pixel_level_high: float | None = None

    @model_serializer(mode="wrap")
    def _drop_unused(self, handler):
        # Settings added later are left out when unused, so older recipes keep their hash.
        return _without_defaults(handler(self), {"pixel_level": None, "pixel_level_high": None})

    @model_validator(mode="after")
    def _check(self) -> MeasurementSpec:
        if self.channel < 0:
            raise ValueError("Measurement channel index must be >= 0")
        if not self.id:
            raise ValueError("Measurement id must not be empty")
        if self.id in RESERVED_COLUMN_NAMES:
            raise ValueError(f"Measurement id '{self.id}' is reserved")
        if self.statistic == "percent_above":
            if self.pixel_level is None or not math.isfinite(self.pixel_level):
                raise ValueError("'percent_above' needs a pixel level (pixel_level)")
            if self.pixel_level_high is not None and not self.pixel_level_high >= self.pixel_level:
                raise ValueError("pixel_level_high must be at least pixel_level")
        elif self.pixel_level is not None or self.pixel_level_high is not None:
            raise ValueError(f"A pixel level applies only to 'percent_above', not '{self.statistic}'")
        geometric = {"area", "equivalent_diameter", "centroid_x", "centroid_y"}
        if self.statistic in geometric and self.background.type != "none":
            raise ValueError(
                f"Background correction does not apply to '{self.statistic}'"
            )
        if self.statistic == "std" and self.background.type != "none":
            raise ValueError(
                "Background correction does not change standard deviation; "
                "set background type to none"
            )
        return self


class ClassificationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    measurement: str
    method: Literal["threshold"] = "threshold"
    threshold: float
    # "above": positive when value > threshold (equal is negative).
    # "at_least": positive when value >= threshold (for "at least N% of the pixels").
    comparison: Literal["above", "at_least"] = "above"
    positive_label: str = "positive"
    negative_label: str = "negative"

    @model_serializer(mode="wrap")
    def _drop_unused(self, handler):
        return _without_defaults(handler(self), {"comparison": "above"})

    @model_validator(mode="after")
    def _check(self) -> ClassificationSpec:
        if not self.id:
            raise ValueError("Classification id must not be empty")
        if not self.name:
            raise ValueError("Classification name must not be empty")
        if self.id in RESERVED_COLUMN_NAMES:
            raise ValueError(f"Classification id '{self.id}' is reserved")
        return self


class ReportSpec(BaseModel):
    """A percentage defined as numerator / denominator over objects."""

    model_config = ConfigDict(extra="forbid")

    numerator: str
    denominator: str


class ObjectSetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "Objects"
    segmentation_channel: int
    algorithm: str
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> ObjectSetSpec:
        if self.segmentation_channel < 0:
            raise ValueError("Segmentation channel index must be >= 0")
        if not self.name:
            raise ValueError("Object set name must not be empty")
        if not self.algorithm:
            raise ValueError("Segmentation algorithm must not be empty")
        return self


class Recipe(BaseModel):
    """Serializable analysis configuration. Version 1 is the MVP schema."""

    model_config = ConfigDict(extra="forbid")

    recipe_version: Literal[1] = 1
    recipe_id: str | None = None
    recipe_name: str | None = None
    created_at: str | None = None
    modified_at: str | None = None
    software_version: str | None = None
    # How a Z-stack is analyzed. Ignored for single-plane images.
    #   max_projection: 2D, brightest value through all slices
    #   single_plane:   2D, one slice (z_index, 0-based; None = middle slice)
    #   stitch_slices:  3D, each slice segmented in 2D, then outlines in neighbouring
    #                   slices that overlap by at least z_stitch_threshold (IoU) are linked
    #   full_3d:        3D, the segmentation method works on the whole volume
    z_stack: Literal["max_projection", "single_plane", "stitch_slices", "full_3d"] = "max_projection"
    z_index: int | None = Field(default=None, ge=0)
    z_stitch_threshold: float = Field(default=0.25, gt=0.0, lt=1.0)
    # Brightness scaling (Cellpose) and automatic thresholds (classical) for 3D:
    # "stack" uses one scale for every slice; "slice" scales each slice on its own.
    z_scale_brightness: Literal["stack", "slice"] = "stack"
    # 3D objects found in fewer slices than this are removed (1 keeps all of them).
    z_min_slices: int = Field(default=1, ge=1)
    object_set: ObjectSetSpec
    measurements: list[MeasurementSpec] = Field(default_factory=list)
    classifications: list[ClassificationSpec] = Field(default_factory=list)
    reports: list[ReportSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_references(self) -> Recipe:
        measurement_ids = [item.id for item in self.measurements]
        _unique(measurement_ids, "measurement id")
        classification_ids = [item.id for item in self.classifications]
        _unique(classification_ids, "classification id")
        _unique([item.name for item in self.classifications], "classification name")
        known = set(measurement_ids)
        for item in self.classifications:
            if item.measurement not in known:
                raise ValueError(
                    f"Classification '{item.id}' references unknown measurement "
                    f"'{item.measurement}'"
                )
        return self

    def canonical_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def content_hash(self) -> str:
        """Hash of the scientific settings only.

        Saving a recipe updates ``modified_at`` and ``software_version``. Those
        must not change the hash, or identical settings would look different.
        """

        payload = json.dumps(self.scientific_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def scientific_dict(self) -> dict[str, Any]:
        """Settings that change results. Timestamps and names used only for display are omitted."""

        return {
            "recipe_version": self.recipe_version,
            "z_stack": self.z_stack,
            "z_index": self.z_index,
            **(
                {
                    "z_stitch_threshold": self.z_stitch_threshold,
                    "z_scale_brightness": self.z_scale_brightness,
                    "z_min_slices": self.z_min_slices,
                }
                if self.z_stack in ("stitch_slices", "full_3d")
                else {}
            ),
            "object_set": self.object_set.model_dump(mode="json"),
            "measurements": [item.model_dump(mode="json") for item in self.measurements],
            "classifications": [item.model_dump(mode="json") for item in self.classifications],
            "reports": [item.model_dump(mode="json") for item in self.reports],
        }


def load_recipe(source: Recipe | dict[str, Any] | str | Path) -> Recipe:
    """Load a recipe from a model, mapping, YAML/JSON string, or file path."""

    if isinstance(source, Recipe):
        return source
    if isinstance(source, dict):
        data = source
    else:
        text = source if isinstance(source, str) and not _looks_like_path(source) else None
        if text is None:
            path = Path(source)
            if not path.is_file():
                raise RecipeValidationError(f"Recipe file could not be opened: {path}")
            text = path.read_text(encoding="utf-8")
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise RecipeValidationError("Recipe file is not valid YAML or JSON") from exc
        if not isinstance(data, dict):
            raise RecipeValidationError("Recipe file must contain a mapping")
    try:
        return Recipe.model_validate(data)
    except Exception as exc:
        if isinstance(exc, RecipeValidationError):
            raise
        raise RecipeValidationError(f"Recipe is invalid: {exc}") from exc


def save_recipe(recipe: Recipe, path: str | Path) -> None:
    """Write a recipe as YAML. The file is the scientific configuration only."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = recipe.canonical_dict()
    destination.write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
    )


def _looks_like_path(value: str) -> bool:
    if "\n" in value:
        return False
    return value.endswith((".yaml", ".yml", ".json")) or Path(value).is_file()


def _exactly_one_length(um: float | None, px: float | None, label: str) -> Literal["um", "px"]:
    if (um is None) == (px is None):
        raise ValueError(f"Specify exactly one of {label}")
    return "um" if um is not None else "px"


def _non_negative(value: float | None, label: str) -> None:
    if value is not None and value < 0:
        raise ValueError(f"{label} must be non-negative")


def _without_defaults(data: Any, defaults: dict[str, Any]) -> Any:
    if isinstance(data, dict):
        return {key: value for key, value in data.items() if not (key in defaults and value == defaults[key])}
    return data


def _unique(values: list[str], label: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"Duplicate {label}: {value}")
        seen.add(value)
