"""Single-image analysis entry point.

``process_image`` is the path shared by interactive and batch runs. It does
not read viewer state and it does not modify the source file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import json

import numpy as np
import pandas as pd

from cellquant import progress
from cellquant.__version__ import __version__
from cellquant.edits import EditOperation, apply_edits, edited_object_ids, excluded_object_ids
from cellquant.errors import ChannelMismatchError
from cellquant.image import LoadedImage, load_image
from cellquant.quantify import (
    classify_objects,
    count_unmeasured,
    generate_phenotypes,
    image_summary_row,
    measure_objects,
    summarize_image,
)
from cellquant.recipe import Recipe, load_recipe
from cellquant.regions import isotropic_pixel_size, spatial_unit
from cellquant.segmentation import segment_objects, segment_regions
from cellquant.volume import flag_z_problems, segment_volume
from skimage.measure import regionprops


@dataclass
class QCReport:
    status: str
    n_objects: int
    median_area: float | None
    fraction_touching_border: float
    percent_excluded_by_size: float
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class ImageResult:
    """Complete analysis of one image. ``objects`` is the canonical table."""

    labels: np.ndarray
    automated_labels: np.ndarray
    objects: pd.DataFrame
    summary: pd.DataFrame
    phenotype_counts: pd.DataFrame
    combination_counts: pd.DataFrame
    reports: pd.DataFrame
    qc: QCReport
    provenance: dict
    spatial_unit: str


def process_image(
    image: str | Path | np.ndarray | LoadedImage,
    recipe: Recipe | dict | str | Path,
    *,
    sample_name: str = "",
    image_id: str | None = None,
    experiment_id: str | None = None,
    run_id: str | None = None,
    filename: str | None = None,
    pixel_size_x: float | None = None,
    pixel_size_y: float | None = None,
    channel_axis: int | None = None,
    user_metadata: dict | None = None,
    manual_edits: list | None = None,
    automated_labels: np.ndarray | None = None,
    segmentation_details: dict | None = None,
) -> ImageResult:
    """Run segmentation, measurement, classification, and summaries for one image.

    Pass ``automated_labels`` to reuse a segmentation. Pass a cached measurement
    table to ``assemble_result`` when only classification or summaries change.
    """

    parsed = load_recipe(recipe)
    loaded = load_image(
        image,
        channel_axis=channel_axis,
        pixel_size_x=pixel_size_x,
        pixel_size_y=pixel_size_y,
        z_mode=parsed.z_stack,
        z_index=parsed.z_index,
    )
    _validate_channels(loaded, parsed)
    pixel_size = isotropic_pixel_size(loaded.pixel_size_x, loaded.pixel_size_y)
    edits = _parse_edits(manual_edits)
    details = dict(segmentation_details or {})
    if automated_labels is None:
        progress.update("Finding objects")
        automated = segment_channel(loaded, parsed, details)
    else:
        automated = np.asarray(automated_labels, dtype=np.int32)
    final_labels = apply_edits(automated, edits)
    progress.update("Measuring markers in each object")
    measured, measure_warnings = measure_objects(
        loaded.data,
        final_labels,
        parsed.measurements,
        pixel_size=pixel_size,
        pixel_size_z=loaded.pixel_size_z,
    )
    measured, measure_warnings = _append_excluded_objects(
        measured,
        measure_warnings,
        loaded.data,
        automated,
        edits,
        parsed.measurements,
        pixel_size,
        loaded.pixel_size_z,
    )
    return assemble_result(
        loaded=loaded,
        recipe=parsed,
        automated_labels=automated,
        labels=final_labels,
        measured=measured,
        measure_warnings=measure_warnings,
        edits=edits,
        segmentation_details=details,
        sample_name=sample_name,
        image_id=image_id,
        experiment_id=experiment_id,
        run_id=run_id,
        filename=filename,
        user_metadata=user_metadata,
    )


def segment_channel(
    loaded: LoadedImage,
    recipe: Recipe,
    details: dict | None = None,
    *,
    record_timing: bool = True,
) -> np.ndarray:
    """Segment the recipe's source channel: 2D, or 3D for a volume.

    The time taken is kept for this session's time estimates, unless
    ``record_timing`` is False (previews of a small area).
    """

    import time

    started = time.perf_counter()
    details = {} if details is None else details
    labels = _segment(loaded, recipe, details)
    elapsed = time.perf_counter() - started
    details["segmentation_seconds"] = elapsed
    engine = details.get("engine") or {}
    height, width = loaded.shape_yx
    slices = loaded.spatial_shape[0] if loaded.is_3d else 1
    if not record_timing:
        return labels
    from cellquant.hardware import record_speed

    record_speed(
        recipe.object_set.algorithm,
        engine.get("engine"),
        str(engine.get("device") or "cpu"),
        loaded.z_mode if loaded.z_mode != "none" else "max_projection",
        elapsed,
        height * width * slices / 1e6,
    )
    return labels


_NOT_GIVEN = object()


def cellpose_diameter_px(parameters: dict, pixel_size_x: float | None, pixel_size_y: float | None) -> float | None:
    """The Cellpose diameter in pixels for one image: ``diameter_um`` divided by that image's own
    µm per pixel, else ``diameter_px`` (the same in every image), else None (Cellpose decides)."""

    micrometres = parameters.get("diameter_um")
    pixel_size = isotropic_pixel_size(pixel_size_x, pixel_size_y)
    if micrometres not in (None, 0) and pixel_size:
        return float(micrometres) / float(pixel_size)
    if micrometres not in (None, 0):
        return None  # no pixel size: Cellpose decides
    pixels = parameters.get("diameter_px", parameters.get("diameter"))
    return None if pixels in (None, 0) else float(pixels)


def _segmentation_parameters(loaded: LoadedImage, recipe: Recipe, details: dict) -> dict:
    """The object-finding settings for this image, with a µm diameter converted with its own pixel size."""

    parameters = dict(recipe.object_set.parameters or {})
    if recipe.object_set.algorithm != "cellpose":
        return parameters
    diameter = cellpose_diameter_px(parameters, loaded.pixel_size_x, loaded.pixel_size_y)
    parameters.pop("diameter", None)
    parameters["diameter_px"] = diameter
    if diameter is not None:
        details["diameter_px_used"] = diameter
    return parameters


def _segment(loaded: LoadedImage, recipe: Recipe, details: dict) -> np.ndarray:
    from cellquant.crop import regions_for

    channel = loaded.data[recipe.object_set.segmentation_channel]
    parameters = _segmentation_parameters(loaded, recipe, details)
    # Crop rectangles: given by the caller (None: the full image), or found here when cropping is on.
    rectangles = details.pop("crop_rectangles_given", _NOT_GIVEN)
    crop_info = details.pop("crop_info_given", None)
    if rectangles is _NOT_GIVEN:
        crop_info = {}
        rectangles = regions_for(loaded, recipe, crop_info)
    cropping = recipe.crop is not None and recipe.crop.enabled
    if cropping and crop_info:
        # Recorded with the result: how much of the image the region covered, and why it was not cropped.
        details["crop_info"] = {key: crop_info[key] for key in ("fraction", "skipped", "seconds", "note") if key in crop_info}
    if cropping and crop_info and crop_info.get("warning"):
        details["crop_warning"] = crop_info["warning"]
    elif cropping and loaded.is_3d:
        details["crop_warning"] = "Cropping applies to 2D analyses only; this Z-stack was analyzed in full."
    elif cropping and rectangles == []:
        details["crop_warning"] = "No region of positive cells was found for cropping, so the full image was segmented."
    if rectangles and not loaded.is_3d:
        return segment_regions(
            channel,
            recipe.object_set.algorithm,
            parameters,
            rectangles,
            pixel_size_x=loaded.pixel_size_x,
            pixel_size_y=loaded.pixel_size_y,
            details=details,
        )
    if loaded.is_3d:
        return segment_volume(
            channel,
            recipe.object_set.algorithm,
            parameters,
            loaded.z_mode,
            stitch_threshold=recipe.z_stitch_threshold,
            scale=recipe.z_scale_brightness,
            pixel_size_x=loaded.pixel_size_x,
            pixel_size_y=loaded.pixel_size_y,
            pixel_size_z=loaded.pixel_size_z,
            min_slices=recipe.z_min_slices,
            details=details,
        )
    return segment_objects(
        channel,
        recipe.object_set.algorithm,
        parameters,
        pixel_size_x=loaded.pixel_size_x,
        pixel_size_y=loaded.pixel_size_y,
        details=details,
    )


def assemble_result(
    *,
    loaded: LoadedImage,
    recipe: Recipe,
    automated_labels: np.ndarray,
    labels: np.ndarray,
    measured: pd.DataFrame,
    measure_warnings: list[str],
    edits: list[EditOperation] | None = None,
    segmentation_details: dict | None = None,
    sample_name: str = "",
    image_id: str | None = None,
    experiment_id: str | None = None,
    run_id: str | None = None,
    filename: str | None = None,
    user_metadata: dict | None = None,
) -> ImageResult:
    """Classify and summarize measurements without rerunning segmentation."""

    parsed = load_recipe(recipe)
    operations = _parse_edits(edits)
    resolved_image_id = image_id or loaded.source_path or "image"
    resolved_filename = filename or (Path(loaded.source_path).name if loaded.source_path else "")
    base = _measurement_columns(measured, parsed)
    objects = _annotate_objects(
        base,
        experiment_id=experiment_id or "",
        run_id=run_id or "",
        sample_name=sample_name,
        image_id=resolved_image_id,
        filename=resolved_filename,
        object_set=parsed.object_set.name,
    )
    removed = set(excluded_object_ids(automated_labels, operations))
    touched = edited_object_ids(operations)
    crop_details = segmentation_details or {}
    crop_edges = set(int(value) for value in crop_details.get("crop_edge_objects") or [])
    if crop_details.get("crop_rectangles") is not None and len(objects):
        # Objects touching a crop edge (not the image border) may be cut: flagged, never dropped.
        objects["at_crop_edge"] = objects["object_id"].map(lambda value: int(value) in crop_edges)
    if len(objects):
        objects["excluded"] = objects["object_id"].map(lambda value: int(value) in removed)
        objects["manual_edit_status"] = [
            "deleted" if int(value) in removed else "edited" if int(value) in touched else ""
            for value in objects["object_id"].tolist()
        ]
    z_warnings: list[str] = []
    if np.asarray(labels).ndim == 3:
        objects, z_warnings = flag_z_problems(
            objects,
            isotropic_pixel_size(loaded.pixel_size_x, loaded.pixel_size_y),
            loaded.pixel_size_z,
            loaded.z_planes,
        )
        if not loaded.pixel_size_z:
            z_warnings.append(
                "This image has no Z step, so volume and centroid_z are blank. Enter the Z step in step 1."
            )
    classified = classify_objects(objects, parsed.classifications)
    with_phenotypes = generate_phenotypes(classified, parsed.classifications)
    reports, phenotype_counts, combination_counts, summary_warnings = summarize_image(
        with_phenotypes,
        parsed.reports,
        parsed.classifications,
    )
    details = segmentation_details or {}
    engine = details.get("engine") or {}
    engine_warnings = [engine["warning"]] if engine.get("warning") else []
    if details.get("warning_z_step"):
        engine_warnings.append(details["warning_z_step"])
    if details.get("crop_warning"):
        engine_warnings.append(details["crop_warning"])
    if len(objects) and "at_crop_edge" in objects.columns and bool(objects["at_crop_edge"].any()):
        count = int(objects["at_crop_edge"].sum())
        engine_warnings.append(
            f"{count} object{'s touch' if count != 1 else ' touches'} a crop edge and may be cut (column at_crop_edge)."
        )
    qc = _qc_report(
        labels,
        with_phenotypes,
        automated_labels,
        engine_warnings + list(measure_warnings) + summary_warnings + z_warnings,
        details.get("percent_excluded_by_size", float("nan")),
    )
    metadata = dict(user_metadata or {})
    summary = image_summary_row(
        sample_name=sample_name,
        filename=resolved_filename,
        image_id=resolved_image_id,
        n_objects=qc.n_objects,
        qc_status=qc.status,
        reports=reports,
        metadata=metadata,
        n_unmeasured=count_unmeasured(with_phenotypes),
    )
    provenance = {
        "software": "cellquant",
        "software_version": __version__,
        "analysis_engine": "cellquant.pipeline.process_image",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "recipe": parsed.canonical_dict(),
        "recipe_sha256": parsed.content_hash(),
        "experiment_id": experiment_id,
        "run_id": run_id,
        "image_id": resolved_image_id,
        "sample_name": sample_name,
        "filename": resolved_filename,
        "source_path": loaded.source_path,
        "user_metadata": metadata,
        "n_channels": loaded.n_channels,
        "channel_names_in_file": list(loaded.channel_names),
        "position": loaded.position,
        "objective": loaded.objective,
        "z_planes_in_file": loaded.z_planes,
        "z_handling": loaded.z_mode,
        "z_index": loaded.z_index,
        "z_description": loaded.z_description,
        "pixel_size_z_um": loaded.pixel_size_z,
        "analysis_dimensions": 3 if loaded.is_3d else 2,
        "z_stitch_threshold": parsed.z_stitch_threshold if loaded.z_mode == "stitch_slices" else None,
        "z_scale_brightness": parsed.z_scale_brightness if loaded.is_3d else None,
        "z_min_slices": parsed.z_min_slices if loaded.is_3d else None,
        "anisotropy": details.get("anisotropy"),
        "diameter_px_used": details.get("diameter_px_used") if details.get("diameter_px_used") is not None else engine.get("estimated_diameter_px"),
        "crop_rectangles": details.get("crop_rectangles"),
        "crop": details.get("crop_info"),
        "segmentation_timings": details.get("timings"),
        "segmentation_seconds": details.get("segmentation_seconds"),
        "image_shape_yx": list(loaded.shape_yx),
        "channel_axis_source": loaded.channel_axis_source,
        "pixel_size_x_um": loaded.pixel_size_x,
        "pixel_size_y_um": loaded.pixel_size_y,
        "spatial_unit": spatial_unit(isotropic_pixel_size(loaded.pixel_size_x, loaded.pixel_size_y)),
        "segmentation_algorithm": parsed.object_set.algorithm,
        "segmentation_parameters": parsed.object_set.parameters,
        "segmentation_engine": engine or None,
        "segmentation_channel": parsed.object_set.segmentation_channel,
        "measurement_definitions": [item.model_dump(mode="json") for item in parsed.measurements],
        "classification_definitions": [item.model_dump(mode="json") for item in parsed.classifications],
        "warnings": qc.warnings,
        "manual_edits": [item.model_dump(mode="json") for item in operations],
        "excluded_object_ids": sorted(removed),
    }
    return ImageResult(
        labels=np.asarray(labels, dtype=np.int32),
        automated_labels=np.array(automated_labels, dtype=np.int32, copy=True),
        objects=with_phenotypes,
        summary=summary,
        phenotype_counts=phenotype_counts,
        combination_counts=combination_counts,
        reports=reports,
        qc=qc,
        provenance=provenance,
        spatial_unit=provenance["spatial_unit"],
    )


# Provenance that describes where a segmentation came from; kept when the same labels are measured again.
SEGMENTATION_PROVENANCE = (
    "segmentation_key",
    "segmentation_origin",
    "segmentation_settings",
    "segmentation_input_sha256",
    "segmentation_calibration",
    "hpc",
)


def segmentation_details_of(result: ImageResult) -> dict:
    """Segmentation details that later measurements of the same labels still report."""

    details: dict = {"percent_excluded_by_size": result.qc.percent_excluded_by_size}
    if result.provenance.get("segmentation_engine"):
        details["engine"] = result.provenance["segmentation_engine"]
    if result.provenance.get("anisotropy") is not None:
        details["anisotropy"] = result.provenance["anisotropy"]
    if result.provenance.get("diameter_px_used") is not None:
        details["diameter_px_used"] = result.provenance["diameter_px_used"]
    if result.provenance.get("crop_rectangles") is not None:
        details["crop_rectangles"] = result.provenance["crop_rectangles"]
        if "at_crop_edge" in result.objects.columns:
            flags = result.objects["at_crop_edge"].fillna(False).astype(bool)
            details["crop_edge_objects"] = [int(value) for value in result.objects.loc[flags, "object_id"]]
    crop_warning = next((text for text in result.qc.warnings if "crop" in text and text.startswith(("Cropping", "No region"))), None)
    if crop_warning:
        details["crop_warning"] = crop_warning
    no_z_step = next((text for text in result.qc.warnings if text.startswith("This image has no Z step, so slices")), None)
    if no_z_step:
        details["warning_z_step"] = no_z_step
    return details


def carry_segmentation_provenance(source: ImageResult, target: ImageResult) -> ImageResult:
    for key in SEGMENTATION_PROVENANCE:
        if source.provenance.get(key) is not None:
            target.provenance[key] = source.provenance[key]
    return target


def remeasure_persisted_result(
    loaded: LoadedImage,
    recipe: Recipe | dict | str | Path,
    persisted: ImageResult,
    *,
    manual_edits: list | None = None,
    sample_name: str = "",
    image_id: str | None = None,
    experiment_id: str | None = None,
    run_id: str | None = None,
    filename: str | None = None,
    user_metadata: dict | None = None,
) -> ImageResult:
    """Measure a saved segmentation again: apply edits to its automated labels, measure, classify.

    No segmentation engine is inspected or loaded, so results made elsewhere
    (for example on a cluster GPU) stay editable on a computer without that
    engine. ``loaded`` must be read with the saved segmentation's Z handling;
    the caller checks that the image, pixel sizes and segmentation settings
    still match.
    """

    if tuple(np.asarray(persisted.automated_labels).shape) != tuple(loaded.spatial_shape):
        raise ChannelMismatchError(
            "The saved objects do not fit this image (different shape). The image or its Z handling changed; segment it again."
        )
    result = process_image(
        loaded,
        recipe,
        sample_name=sample_name,
        image_id=image_id,
        experiment_id=experiment_id,
        run_id=run_id,
        filename=filename,
        user_metadata=user_metadata,
        manual_edits=manual_edits,
        automated_labels=persisted.automated_labels,
        segmentation_details=segmentation_details_of(persisted),
    )
    return carry_segmentation_provenance(persisted, result)


def reclassify_result(result: ImageResult, recipe: Recipe | dict | str | Path) -> ImageResult:
    """Apply new thresholds to an existing object table. Does not load or hash files."""

    parsed = load_recipe(recipe)
    frame = result.objects.copy()
    previous_ids = [item.get("id") for item in result.provenance.get("classification_definitions", [])]
    drop = [column for column in [*previous_ids, "phenotype"] if column in frame.columns]
    if drop:
        frame = frame.drop(columns=drop)
    classified = classify_objects(frame, parsed.classifications)
    with_phenotypes = generate_phenotypes(classified, parsed.classifications)
    reports, phenotype_counts, combination_counts, summary_warnings = summarize_image(
        with_phenotypes,
        parsed.reports,
        parsed.classifications,
    )
    metadata = dict(result.provenance.get("user_metadata") or {})
    summary = image_summary_row(
        sample_name=str(result.provenance.get("sample_name") or ""),
        filename=str(result.provenance.get("filename") or ""),
        image_id=str(result.provenance.get("image_id") or ""),
        n_objects=result.qc.n_objects,
        qc_status=result.qc.status,
        reports=reports,
        metadata=metadata,
        n_unmeasured=count_unmeasured(with_phenotypes),
    )
    provenance = dict(result.provenance)
    provenance["recipe"] = parsed.canonical_dict()
    provenance["recipe_sha256"] = parsed.content_hash()
    provenance["classification_definitions"] = [item.model_dump(mode="json") for item in parsed.classifications]
    provenance["timestamp"] = datetime.now(timezone.utc).isoformat()
    provenance["warnings"] = list(dict.fromkeys([*result.qc.warnings, *summary_warnings]))
    return ImageResult(
        labels=result.labels,
        automated_labels=result.automated_labels,
        objects=with_phenotypes,
        summary=summary,
        phenotype_counts=phenotype_counts,
        combination_counts=combination_counts,
        reports=reports,
        qc=result.qc,
        provenance=provenance,
        spatial_unit=result.spatial_unit,
    )


def export_image_result(result: ImageResult, directory: str | Path) -> Path:
    """Write the object table, image summary, phenotypes, labels, and provenance.

    Source images are not touched. Labels written here are an analysis output.
    """

    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    result.objects.to_csv(destination / "objects.csv", index=False)
    result.summary.to_csv(destination / "image_summary.csv", index=False)
    result.phenotype_counts.to_csv(destination / "phenotype_counts.csv", index=False)
    result.combination_counts.to_csv(destination / "combination_counts.csv", index=False)
    result.reports.to_csv(destination / "reports.csv", index=False)
    (destination / "provenance.json").write_text(
        json.dumps(result.provenance, indent=2, default=_json_default),
        encoding="utf-8",
    )
    # Local import keeps image IO out of callers that only need tables.
    import tifffile

    tifffile.imwrite(destination / "labels.tif", result.labels.astype(np.int32))
    return destination


def _validate_channels(image: LoadedImage, recipe: Recipe) -> None:
    expected = [recipe.object_set.segmentation_channel]
    expected.extend(item.channel for item in recipe.measurements)
    invalid = sorted({index for index in expected if index < 0 or index >= image.n_channels})
    if not invalid:
        return
    listed = ", ".join(str(index) for index in invalid)
    raise ChannelMismatchError(
        f"This image has {image.n_channels} channels, but the analysis expects "
        f"channel index {listed}."
    )


def _qc_report(
    labels: np.ndarray,
    objects: pd.DataFrame,
    automated_labels: np.ndarray,
    warnings: list[str],
    percent_excluded_by_size: float,
) -> QCReport:
    collected = list(warnings)
    active = objects
    if len(objects):
        counted = np.ones(len(objects), dtype=bool)
        for column in ("excluded", "unmeasured"):
            if column in objects.columns:
                counted &= ~objects[column].fillna(False).astype(bool).to_numpy()
        active = objects.loc[counted]
    n_objects = int(len(active))
    automated_count = int(np.count_nonzero(np.unique(automated_labels))) if automated_labels.size else 0
    if automated_count == 0:
        collected.append("Segmentation returned no objects.")
    elif n_objects == 0:
        collected.append("All detected objects were excluded or could not be measured.")
    if n_objects == 0:
        return QCReport(
            status="warning",
            n_objects=0,
            median_area=None,
            fraction_touching_border=float("nan"),
            percent_excluded_by_size=percent_excluded_by_size,
            warnings=collected,
        )
    # The XY edge of the image; in 3D, objects cut by the top or bottom slice are not "at the border".
    height, width = labels.shape[-2:]
    touching = 0
    properties = list(regionprops(labels))
    for prop in properties:
        box = prop.bbox
        half = len(box) // 2
        min_row, min_col = box[half - 2], box[half - 1]
        max_row, max_col = box[-2], box[-1]
        if min_row == 0 or min_col == 0 or max_row == height or max_col == width:
            touching += 1
    return QCReport(
        status="warning" if collected else "success",
        n_objects=n_objects,
        median_area=float(active["area"].median()),
        fraction_touching_border=touching / max(len(properties), 1),
        percent_excluded_by_size=percent_excluded_by_size,
        warnings=collected,
    )


def _append_excluded_objects(
    measured: pd.DataFrame,
    warnings: list[str],
    image: np.ndarray,
    automated: np.ndarray,
    edits: list[EditOperation],
    measurements,
    pixel_size: float | None,
    pixel_size_z: float | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    removed = excluded_object_ids(automated, edits)
    if not removed:
        return measured, warnings
    deleted_labels = np.where(np.isin(automated, removed), automated, 0).astype(np.int32)
    extra, extra_warnings = measure_objects(
        image,
        deleted_labels,
        measurements,
        pixel_size=pixel_size,
        pixel_size_z=pixel_size_z,
    )
    extra = extra.loc[extra["object_id"].isin(removed)]
    combined = pd.concat([measured, extra], ignore_index=True)
    combined = combined.sort_values("object_id").reset_index(drop=True)
    return combined, warnings + extra_warnings


def measurement_values(result: ImageResult, recipe: Recipe) -> pd.DataFrame:
    """Geometry and measurement columns, without classifications or annotations."""

    parsed = load_recipe(recipe)
    measurement_ids = [item.id for item in parsed.measurements]
    columns = [
        "object_id",
        "centroid_x",
        "centroid_y",
        "centroid_z",
        "area",
        "volume",
        "z_slices",
        "z_first",
        "z_last",
        *measurement_ids,
    ]
    present = [column for column in columns if column in result.objects.columns]
    return result.objects.loc[:, present].copy()


def _measurement_columns(measured: pd.DataFrame, recipe: Recipe) -> pd.DataFrame:
    drop = {item.id for item in recipe.classifications}
    drop.update({"phenotype", "excluded", "unmeasured", "manual_edit_status", "z_flag", "at_crop_edge"})
    columns = [column for column in measured.columns if column not in drop]
    return measured.loc[:, columns].copy()


def _parse_edits(manual_edits: list | None) -> list[EditOperation]:
    if not manual_edits:
        return []
    return [
        item if isinstance(item, EditOperation) else EditOperation.model_validate(item)
        for item in manual_edits
    ]


def _annotate_objects(
    table: pd.DataFrame,
    *,
    experiment_id: str,
    run_id: str,
    sample_name: str,
    image_id: str,
    filename: str,
    object_set: str,
) -> pd.DataFrame:
    output = table.copy()
    output.insert(0, "object_set", object_set)
    output.insert(0, "filename", filename)
    output.insert(0, "image_id", image_id)
    output.insert(0, "sample_name", sample_name)
    output.insert(0, "run_id", run_id)
    output.insert(0, "experiment_id", experiment_id)
    output["excluded"] = False
    return output


def _json_default(value):
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
