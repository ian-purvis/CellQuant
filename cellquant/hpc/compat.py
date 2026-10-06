"""Scientific admission rules for a package: engine, model, Z mode, GPU and calibration.

Nothing here changes a recipe. A setting the cluster cannot run as written is
reported with what to change; no engine, model, device or Z mode is swapped in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cellquant.hpc.models import Issue, RuntimeContract
from cellquant.recipe import Recipe

CELLPOSE_ENGINES = ("cellpose3", "cellpose4")


@dataclass(frozen=True)
class EngineChoice:
    engine: str
    model: str


def recipe_engine(recipe: Recipe) -> tuple[EngineChoice | None, list[Issue]]:
    """The Cellpose engine and model a recipe names, in the runtime contract's words."""

    object_set = recipe.object_set
    parameters = object_set.parameters or {}
    if object_set.algorithm != "cellpose":
        return None, [
            Issue(
                code="E_ENGINE",
                message=f"The settings use the '{object_set.algorithm}' method. Cluster packages run Cellpose only.",
                fix="Run the classical method on this computer (it is fast), or choose Cellpose in step 2.",
            )
        ]
    engine = parameters.get("engine")
    model = parameters.get("model")
    issues = []
    if engine not in CELLPOSE_ENGINES:
        issues.append(
            Issue(
                code="E_ENGINE",
                message="The settings do not say which Cellpose engine to use.",
                fix="Open step 2, choose the Cellpose model again and save, so the engine is recorded.",
            )
        )
    if not model:
        issues.append(
            Issue(code="E_MODEL", message="The settings do not name a Cellpose model.", fix="Choose a model in step 2 and save.")
        )
    if issues:
        return None, issues
    return EngineChoice(str(engine), str(model)), []


def check_against_runtime(recipe: Recipe, contract: RuntimeContract) -> list[Issue]:
    """Engine, model, Z mode and GPU: all must be exactly what the runtime provides."""

    choice, issues = recipe_engine(recipe)
    if choice is None:
        return issues
    parameters = recipe.object_set.parameters or {}
    if choice.engine != contract.engine:
        issues.append(
            Issue(
                code="E_ENGINE",
                message=f"The settings use {choice.engine}, but the cluster runtime '{contract.runtime_id}' runs {contract.engine}.",
                fix="Choose a profile whose runtime has this engine, or change the engine in step 2.",
            )
        )
    if choice.model != contract.model:
        issues.append(
            Issue(
                code="E_MODEL",
                message=f"The settings use model '{choice.model}', but the runtime provides '{contract.model}'.",
                fix="Choose a runtime with this model, or select the runtime's model in step 2.",
            )
        )
    if recipe.z_stack not in contract.supported_modes:
        enabled = ", ".join(contract.supported_modes) or "none yet"
        issues.append(
            Issue(
                code="E_MODE",
                message=f"Z mode '{recipe.z_stack}' is not enabled for runtime '{contract.runtime_id}' (enabled: {enabled}).",
                fix="A mode is enabled only after it passes validation on the cluster. Choose an enabled mode, or ask the maintainer.",
            )
        )
    if not bool(parameters.get("gpu", False)):
        issues.append(
            Issue(
                code="E_GPU",
                message="The settings do not ask for the GPU, so the cluster job would run Cellpose on the CPU.",
                fix="In HPC prep click 'Use the GPU on the cluster', then prepare the package. The setting is part of the frozen recipe.",
            )
        )
    return issues


def uses_physical_units(recipe: Recipe) -> bool:
    """True when any setting is in µm (sizes, distances), so images need a pixel size."""

    def walk(value: Any) -> bool:
        if isinstance(value, dict):
            return any((str(key).endswith(("_um", "_um2")) and item is not None) or walk(item) for key, item in value.items())
        if isinstance(value, list):
            return any(walk(item) for item in value)
        return False

    return walk(recipe.scientific_dict())


def check_calibration(
    effective: list[float | None],
    *,
    recipe: Recipe,
    z_planes: int,
    acquisition_label: str,
) -> tuple[list[Issue], list[Issue]]:
    """(errors, warnings) for an acquisition's effective pixel sizes.

    3D modes need positive X, Y and Z sizes (stricter than local analysis,
    which falls back to equal steps). Physical settings need X and Y. X and Y
    must be equal; they are never averaged.
    """

    x, y, z = effective
    errors: list[Issue] = []
    warnings: list[Issue] = []
    fix = "Enter the pixel size (and Z step) in step 1, or correct the file's metadata, then prepare again."
    if (x is None) != (y is None):
        errors.append(Issue(code="E_CALIBRATION", message=f"{acquisition_label}: only one of the X and Y pixel sizes is set.", fix=fix))
        return errors, warnings
    if x is not None and y is not None and abs(x - y) / max(x, y) > 1e-3:
        errors.append(
            Issue(
                code="E_CALIBRATION",
                message=f"{acquisition_label}: X and Y pixel sizes differ ({x} and {y} µm). Unequal pixel sizes are not supported.",
                fix="Resample the image to square pixels, or correct the pixel size in step 1.",
            )
        )
    three_d = recipe.z_stack in ("stitch_slices", "full_3d") and z_planes > 1
    if three_d and (x is None or z is None):
        errors.append(
            Issue(
                code="E_CALIBRATION",
                message=f"{acquisition_label}: 3D analysis on the cluster needs the X, Y and Z step in µm.",
                fix=fix,
            )
        )
    elif x is None and uses_physical_units(recipe):
        errors.append(
            Issue(code="E_CALIBRATION", message=f"{acquisition_label}: the settings use µm, but this image has no pixel size.", fix=fix)
        )
    elif x is None:
        warnings.append(
            Issue(
                code="W_UNCALIBRATED",
                message=f"{acquisition_label}: no pixel size, so sizes and areas will be in pixels.",
                fix="Enter the pixel size in step 1 if it is known.",
            )
        )
    return errors, warnings


def effective_z(recipe: Recipe, z_planes: int) -> tuple[str, int | None, list[Issue]]:
    """How this acquisition will be read: the Z mode that applies and, for one slice, which slice."""

    from cellquant.errors import ImageLoadError
    from cellquant.image import effective_z_index

    if z_planes <= 1:
        return "none", None, []
    if recipe.z_stack == "single_plane":
        try:
            return "single_plane", effective_z_index(z_planes, recipe.z_index), []
        except ImageLoadError as exc:
            return "single_plane", None, [Issue(code="E_Z_INDEX", message=str(exc), fix="Choose a slice that every image has, in step 2.")]
    return recipe.z_stack, None, []
