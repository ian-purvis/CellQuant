"""Segmentation backends.

Algorithms register behind one adapter. The engine calls ``segment_objects``
and does not depend on a particular method. Size limits are applied here so
every backend shares the same object filter.
"""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Protocol

import numpy as np
from pydantic import BaseModel, ConfigDict, model_validator
from scipy import ndimage
from skimage.feature import peak_local_max
from skimage.filters import gaussian, threshold_otsu
from skimage.measure import label, regionprops
from skimage.morphology import binary_closing, binary_opening, disk
from skimage.segmentation import clear_border, watershed

from cellquant import progress
from cellquant.errors import CalibrationError, RecipeValidationError, SegmentationError
from cellquant.regions import isotropic_pixel_size


class SegmentationBackend(Protocol):
    def segment(self, image: np.ndarray, parameters: dict[str, Any]) -> np.ndarray:
        """Return an integer label image. Background is 0."""


_BACKENDS: dict[str, SegmentationBackend] = {}


def register_segmentation_backend(name: str, backend: SegmentationBackend) -> None:
    """Register an additional segmentation method under ``name``."""

    if not name:
        raise SegmentationError("Segmentation backend name must not be empty.")
    _BACKENDS[name] = backend


def available_segmentation_backends() -> list[str]:
    return sorted(_BACKENDS)


class ClassicalParameters(BaseModel):
    """Threshold, cleanup, optional watershed, then size filtering by the engine."""

    model_config = ConfigDict(extra="forbid")

    sigma: float = 0.0
    threshold_method: str = "otsu"
    threshold: float | None = None
    fill_holes: bool = True
    opening_radius_px: int = 0
    closing_radius_px: int = 0
    use_watershed: bool = False
    watershed_min_distance_px: float = 5.0
    watershed_compactness: float = 0.0
    min_area_px: float | None = None
    max_area_px: float | None = None
    min_area_um2: float | None = None
    max_area_um2: float | None = None
    exclude_border: bool = False

    @model_validator(mode="after")
    def _check(self) -> ClassicalParameters:
        if self.sigma < 0:
            raise ValueError("sigma must be non-negative")
        if self.threshold_method not in {"manual", "otsu"}:
            raise ValueError("threshold_method must be 'manual' or 'otsu'")
        if self.threshold_method == "manual" and self.threshold is None:
            raise ValueError("A manual threshold requires 'threshold'")
        if self.opening_radius_px < 0 or self.closing_radius_px < 0:
            raise ValueError("Morphological radii must be non-negative")
        if self.watershed_min_distance_px <= 0:
            raise ValueError("watershed_min_distance_px must be positive")
        for name in ("min_area_px", "max_area_px", "min_area_um2", "max_area_um2"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must be non-negative")
        if (
            self.min_area_px is not None
            and self.max_area_px is not None
            and self.min_area_px > self.max_area_px
        ):
            raise ValueError("min_area_px is greater than max_area_px")
        if (
            self.min_area_um2 is not None
            and self.max_area_um2 is not None
            and self.min_area_um2 > self.max_area_um2
        ):
            raise ValueError("min_area_um2 is greater than max_area_um2")
        return self


class ClassicalBackend:
    def segment(self, image: np.ndarray, parameters: dict[str, Any]) -> np.ndarray:
        parsed = _parse_classical(parameters)
        return classical_segment(image, parsed)

    def segment_volume(self, volume, parameters, *, mode, scale, anisotropy):
        from cellquant.volume import classical_volume

        return classical_volume(volume, parameters, mode=mode, scale=scale, anisotropy=anisotropy)


class CellposeBackend:
    """Cellpose adapter for Cellpose 3 (classic) and Cellpose 4 (Cellpose-SAM).

    Only one Cellpose can be installed per environment. The recipe may name the
    engine it was made with (``engine``: ``cellpose3`` or ``cellpose4``). A
    recipe made for the other engine is refused rather than run with a
    different model, because Cellpose 4 silently replaces model names it does
    not know with its own default.
    """

    def describe(self, parameters: dict[str, Any]) -> dict[str, Any]:
        """Engine, version, model and (Cellpose-SAM) numeric precision that will run.

        Imports PyTorch only to decide the precision automatically (once per program run).
        """

        from cellquant.engines import CELLPOSE_CLASSIC, ENGINE_LABELS, cellpose_engine

        engine = cellpose_engine()
        if not engine.installed:
            raise SegmentationError(
                "Cellpose is not installed in this copy of CellQuant. "
                "Run Install CellQuant.bat and choose a Cellpose engine, or use the classical method."
            )
        wanted = parameters.get("engine")
        if wanted and wanted != engine.key:
            raise SegmentationError(
                f"These settings were made for {ENGINE_LABELS.get(wanted, wanted)}, but this copy of "
                f"CellQuant runs {engine.label}. Close CellQuant and open it again with "
                f"{ENGINE_LABELS.get(wanted, wanted)}, or choose a model for {ENGINE_LABELS[engine.key]}."
            )
        model = parameters.get("model") or engine.default_model
        if engine.key == CELLPOSE_CLASSIC:
            if model not in engine.models and not _is_model_file(model):
                raise SegmentationError(
                    f"Classic Cellpose has no model named '{model}'. "
                    f"Choose one of: {', '.join(engine.models)}."
                )
        elif model in _CLASSIC_ONLY_MODELS or (model not in engine.models and not _is_model_file(model)):
            raise SegmentationError(
                f"Cellpose-SAM has no model named '{model}'"
                + (" (that is a classic Cellpose model)" if model in _CLASSIC_ONLY_MODELS else "")
                + f". Choose one of: {', '.join(engine.models)}, or open CellQuant with classic Cellpose."
            )
        described = {
            "engine": engine.key,
            "engine_label": ENGINE_LABELS[engine.key],
            "cellpose_version": engine.version,
            "model": str(model),
        }
        if engine.key != CELLPOSE_CLASSIC:
            from cellquant.hardware import cellpose_precision

            try:
                # bfloat16 only where the device computes it natively; part of the cache key and provenance.
                described["precision"] = cellpose_precision(parameters, engine.key)
            except ValueError as exc:
                raise SegmentationError(str(exc)) from exc
        return described

    def segment(self, image: np.ndarray, parameters: dict[str, Any]) -> np.ndarray:
        labels, _details = self.segment_with_details(image, parameters)
        return labels

    def estimate_diameter(self, scaled_image: np.ndarray, parameters: dict[str, Any]) -> float | None:
        """Classic Cellpose's size estimate on an already scaled whole image; None for Cellpose-SAM."""

        from cellquant.engines import CELLPOSE_CLASSIC

        details = self.describe(parameters)
        if details["engine"] != CELLPOSE_CLASSIC:
            return None
        return _estimate_classic_diameter(self._model(details, parameters), scaled_image)

    def _model(self, details: dict[str, Any], parameters: dict[str, Any]):
        from cellquant.engines import CELLPOSE_CLASSIC

        try:
            from cellpose import models
        except ImportError as exc:
            raise SegmentationError(
                "Cellpose could not be loaded in this copy of CellQuant. Run Install CellQuant.bat "
                f"and choose Update. Details: {exc}"
            ) from exc
        from cellquant.hardware import apply_threads

        gpu = bool(parameters.get("gpu", False))
        _seed_everything(parameters.get("random_seed"))
        _opt_out_of_sparse_checks()
        apply_threads(details["engine"])
        return _cached_model(models, details["engine"] == CELLPOSE_CLASSIC, details["model"], gpu, details.get("precision"))

    def _finish(self, model, details: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
        device = getattr(model, "device", None)
        device_type = str(getattr(device, "type", device or "cpu"))
        details["device"] = device_type
        details["random_seed"] = parameters.get("random_seed")
        if parameters.get("gpu") and device_type != "cuda":
            details["warning"] = (
                "The GPU was requested but Cellpose ran on the CPU. "
                "Check that an NVIDIA GPU is present and that Install CellQuant.bat installed GPU support."
            )
        return details

    def segment_volume(
        self,
        volume: np.ndarray,
        parameters: dict[str, Any],
        *,
        mode: str,
        scale: str,
        anisotropy: float,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        """3D: labels per slice (``stitch_slices``, linked by the caller) or one volume (``full_3d``).

        Brightness is scaled here, for the whole stack or per slice, and
        Cellpose is told not to scale again.
        """

        from cellquant.engines import CELLPOSE_CLASSIC
        from cellquant.volume import scale_brightness

        details = self.describe(parameters)
        model = self._model(details, parameters)
        classic = details["engine"] == CELLPOSE_CLASSIC
        scaled = scale_brightness(volume, "stack" if mode == "full_3d" else scale)
        diameter = parameters.get("diameter_px", parameters.get("diameter"))
        diameter = None if diameter in (None, 0) else float(diameter)
        if diameter is None and classic:
            # Estimate the size once, from the projection, so every slice uses the same diameter.
            diameter = _estimate_classic_diameter(model, scaled.max(axis=0))
            details["estimated_diameter_px"] = diameter
        options = {
            "flow_threshold": float(parameters.get("flow_threshold", 0.4)),
            "cellprob_threshold": float(parameters.get("cellprob_threshold", 0.0)),
            "min_size": int(parameters.get("min_size", 15)),
            "normalize": False,
        }
        if classic:
            options["channels"] = [0, 0]
        try:
            if mode == "stitch_slices":
                planes = []
                for index, plane in enumerate(scaled):
                    progress.update(f"Finding objects with Cellpose: slice {index + 1} of {len(scaled)}", index, len(scaled))
                    output = model.eval(plane, diameter=diameter, do_3D=False, **options)
                    planes.append(np.asarray(output[0] if isinstance(output, (tuple, list)) else output))
                labels = np.stack(planes)
            else:
                progress.update("Finding objects with Cellpose in the whole volume (this step cannot be stopped part-way)")
                output = model.eval(
                    scaled,
                    diameter=diameter,
                    do_3D=True,
                    z_axis=0,
                    anisotropy=float(anisotropy),
                    **options,
                )
                labels = np.asarray(output[0] if isinstance(output, (tuple, list)) else output)
        except (SegmentationError, progress.AnalysisCancelled):
            raise
        except Exception as exc:
            raise SegmentationError(f"Cellpose failed: {exc}") from exc
        return labels.astype(np.int32), self._finish(model, details, parameters)

    def segment_with_details(
        self,
        image: np.ndarray,
        parameters: dict[str, Any],
    ) -> tuple[np.ndarray, dict[str, Any]]:
        from cellquant.engines import CELLPOSE_CLASSIC

        details = self.describe(parameters)
        diameter = parameters.get("diameter_px", parameters.get("diameter"))
        diameter = None if diameter in (None, 0) else float(diameter)
        gpu = bool(parameters.get("gpu", False))
        flow_threshold = float(parameters.get("flow_threshold", 0.4))
        cellprob_threshold = float(parameters.get("cellprob_threshold", 0.0))
        min_size = int(parameters.get("min_size", 15))
        seed = parameters.get("random_seed")
        # False when the image was already scaled (a crop scaled like the whole image).
        normalize = bool(parameters.get("normalize", True))
        try:
            model = self._model(details, parameters)
            timings = details.setdefault("timings", {})
            if details["engine"] == CELLPOSE_CLASSIC and diameter is None:
                import time

                started = time.perf_counter()
                network_before = _STAGE_TIMES["network"] if _STAGE_TIMES is not None else 0.0
                diameter = _estimate_classic_diameter(model, np.asarray(image), normalize=normalize)
                timings["size_estimate"] = timings.get("size_estimate", 0.0) + time.perf_counter() - started
                if _STAGE_TIMES is not None:  # network time of the size model belongs to the size estimate
                    timings["size_estimate_network"] = _STAGE_TIMES["network"] - network_before
                if diameter is not None:
                    details["estimated_diameter_px"] = diameter
            if details["engine"] == CELLPOSE_CLASSIC:
                output = model.eval(
                    np.asarray(image),
                    diameter=diameter,
                    channels=[0, 0],
                    do_3D=False,
                    flow_threshold=flow_threshold,
                    cellprob_threshold=cellprob_threshold,
                    min_size=min_size,
                    normalize=normalize,
                )
            else:
                output = model.eval(
                    np.asarray(image),
                    diameter=diameter,
                    do_3D=False,
                    flow_threshold=flow_threshold,
                    cellprob_threshold=cellprob_threshold,
                    min_size=min_size,
                    normalize=normalize,
                )
            device = getattr(model, "device", None)
        except SegmentationError:
            raise
        except Exception as exc:
            raise SegmentationError(f"Cellpose failed: {exc}") from exc
        masks = output[0] if isinstance(output, (tuple, list)) else output
        device_type = str(getattr(device, "type", device or "cpu"))
        details["device"] = device_type
        details["random_seed"] = seed
        if gpu and device_type != "cuda":
            details["warning"] = (
                "The GPU was requested but Cellpose ran on the CPU. "
                "Check that an NVIDIA GPU is present and that Install CellQuant.bat installed GPU support."
            )
        return np.asarray(masks, dtype=np.int32), details


# --- reusing Cellpose models and network output -----------------------------------------------------

_MODELS: dict[tuple, Any] = {}
_NETWORK_MEMO: dict | None = None
# While a ``stage_timer`` block runs: seconds spent in Cellpose's network (accumulated).
_STAGE_TIMES: dict | None = None


@contextmanager
def stage_timer() -> Iterator[dict]:
    """Collect stage times of the Cellpose calls in this block: ``network`` (seconds in the network)."""

    global _STAGE_TIMES
    previous = _STAGE_TIMES
    _STAGE_TIMES = {"network": 0.0, "network_calls": 0}
    try:
        yield _STAGE_TIMES
    finally:
        _STAGE_TIMES = previous


def _cached_model(models, classic: bool, name: str, gpu: bool, precision: str | None = None):
    """One Cellpose model per (class, name, GPU, precision) for the life of the program.

    Loading Cellpose-SAM reads more than 1 GB, so batches should not repeat it.
    """

    cls = models.Cellpose if classic else models.CellposeModel
    key = (cls, str(name), bool(gpu), precision)
    model = _MODELS.get(key)
    if model is None:
        if classic:
            model = cls(gpu=gpu, model_type=name)
        elif precision:
            model = cls(gpu=gpu, pretrained_model=name, use_bfloat16=precision == "bfloat16")
        else:
            model = cls(gpu=gpu, pretrained_model=name)
        _share_network_output(model)
        _MODELS[key] = model
    return model


@contextmanager
def keep_network_output() -> Iterator[None]:
    """Remember Cellpose's network output while this block runs.

    Changing only ``cellprob_threshold``, ``flow_threshold`` or ``min_size``
    then skips the slow network step: Cellpose finds masks again from the saved
    flows and gets exactly the masks a fresh run would. Call
    ``clear_network_output`` between images to free the memory.
    """

    global _NETWORK_MEMO
    outermost = _NETWORK_MEMO is None
    if outermost:
        _NETWORK_MEMO = {}
    try:
        yield
    finally:
        if outermost:
            _NETWORK_MEMO = None


def clear_network_output() -> None:
    if _NETWORK_MEMO is not None:
        _NETWORK_MEMO.clear()


def _share_network_output(model) -> None:
    inner = getattr(model, "cp", model)  # Cellpose 3's Cellpose class wraps the model
    original = getattr(inner, "_run_net", None)
    if original is None or getattr(inner, "_cellquant_memo", False):
        return

    def run_net(*args, **kwargs):
        import time

        started = time.perf_counter()
        try:
            return _run_net_memo(*args, **kwargs)
        finally:
            if _STAGE_TIMES is not None:
                _STAGE_TIMES["network"] += time.perf_counter() - started
                _STAGE_TIMES["network_calls"] += 1

    def _run_net_memo(*args, **kwargs):
        memo = _NETWORK_MEMO
        if memo is None:
            return original(*args, **kwargs)
        digest = hashlib.blake2b(digest_size=16)
        digest.update(str(id(inner)).encode())
        for value in (*args, *(item for _name, item in sorted(kwargs.items())), *sorted(kwargs)):
            if isinstance(value, np.ndarray):
                digest.update(str((value.shape, value.dtype)).encode())
                digest.update(np.ascontiguousarray(value).tobytes())
            else:
                digest.update(repr(value).encode())
        key = digest.hexdigest()
        if key not in memo:
            memo[key] = original(*args, **kwargs)
        return copy.deepcopy(memo[key])

    inner._run_net = run_net
    inner._cellquant_memo = True


def _estimate_classic_diameter(model, image: np.ndarray, normalize: bool = False) -> float | None:
    """Classic Cellpose's size model on one 2D image; None when it is not available.

    ``normalize=False`` for an image already scaled to 0-1 (as Cellpose would scale it).
    """

    size_model = getattr(model, "sz", None)
    if size_model is None or getattr(model, "pretrained_size", None) is None:
        return None
    try:
        diameter, _style = size_model.eval(image, channels=[0, 0], normalize=normalize)
    except Exception:  # noqa: BLE001 - fall back to the model's own default size
        return None
    value = float(np.asarray(diameter).ravel()[0])
    return value if np.isfinite(value) and value > 0 else None


_CLASSIC_ONLY_MODELS = frozenset({"nuclei", "cyto", "cyto2", "cyto3", "cyto2_cp3", "tissuenet_cp3", "livecell_cp3"})


def _is_model_file(model: object) -> bool:
    from pathlib import Path

    return isinstance(model, str) and Path(model).is_file()


def _seed_everything(seed: object) -> None:
    """Seed NumPy and, when it is loaded, PyTorch. Cellpose uses both."""

    if seed is None:
        return
    value = int(seed)  # type: ignore[arg-type]
    np.random.seed(value)
    try:
        import torch

        torch.manual_seed(value)
    except Exception:  # noqa: BLE001 - PyTorch is optional for classical runs
        return


def _opt_out_of_sparse_checks() -> None:
    """Say explicitly that PyTorch should skip sparse tensor checks, as it already does by default.

    Cellpose builds a sparse tensor when it finds masks; newer PyTorch warns
    ("Sparse invariant checks are implicitly disabled") unless the choice is made.
    A choice already made (for example checks turned on) is kept.
    """

    try:
        import torch

        checks = torch.sparse.check_sparse_tensor_invariants
        if not checks.is_enabled():
            checks.disable()
    except Exception:  # noqa: BLE001 - older PyTorch without this switch never warns
        return


def engine_signature(algorithm: str, parameters: dict[str, Any] | None) -> dict[str, Any]:
    """What must match for cached labels to be reused.

    For Cellpose this is the engine, version, and resolved model, so labels
    made with one engine are never reused under the other.
    """

    backend = _BACKENDS.get(algorithm)
    describe = getattr(backend, "describe", None)
    if describe is None:
        return {"algorithm": algorithm}
    try:
        return {"algorithm": algorithm, **describe(dict(parameters or {}))}
    except SegmentationError as exc:
        return {"algorithm": algorithm, "error": str(exc)}


def segment_objects(
    channel: np.ndarray,
    algorithm: str,
    parameters: dict[str, Any] | None = None,
    *,
    pixel_size_x: float | None = None,
    pixel_size_y: float | None = None,
    details: dict[str, Any] | None = None,
) -> np.ndarray:
    """Segment one 2D channel and apply shared size and border filters."""

    if algorithm not in _BACKENDS:
        known = ", ".join(available_segmentation_backends()) or "none"
        raise SegmentationError(
            f"Unknown segmentation algorithm '{algorithm}'. Available algorithms: {known}."
        )
    import time

    backend = _BACKENDS[algorithm]
    with_details = getattr(backend, "segment_with_details", None)
    if with_details is not None:
        started = time.perf_counter()
        with stage_timer() as stages:
            raw, engine_details = with_details(np.asarray(channel), dict(parameters or {}))
        elapsed = time.perf_counter() - started
        timings = engine_details.pop("timings", {})
        if details is not None:
            details["engine"] = engine_details
            network = stages["network"] - timings.pop("size_estimate_network", 0.0)
            details["timings"] = {
                **timings,
                "network": network,
                "masks_and_rest": max(0.0, elapsed - network - timings.get("size_estimate", 0.0)),
            }
    else:
        raw = backend.segment(np.asarray(channel), dict(parameters or {}))
    labels = np.asarray(raw)
    if labels.ndim != 2:
        raise SegmentationError("Segmentation must return a 2D label image.")
    if labels.shape != np.asarray(channel).shape:
        raise SegmentationError("Segmentation labels do not match the image shape.")
    if not np.issubdtype(labels.dtype, np.integer):
        raise SegmentationError("Segmentation labels must be integers.")
    typed = labels.astype(np.int32, copy=False)
    n_before = _object_count(typed)
    filtered = filter_objects(
        typed,
        parameters or {},
        pixel_size_x=pixel_size_x,
        pixel_size_y=pixel_size_y,
    )
    if details is not None:
        n_after = _object_count(filtered)
        details["n_before_filter"] = n_before
        details["n_after_filter"] = n_after
        details["percent_excluded_by_size"] = (
            float("nan") if n_before == 0 else 100.0 * (n_before - n_after) / n_before
        )
    return filtered


def normalize_like_cellpose(image: np.ndarray, lower: float = 1.0, upper: float = 99.0) -> np.ndarray:
    """Scale a whole 2D image as Cellpose does by default (1st to 99th percentile to 0-1)."""

    data = np.asarray(image, dtype=np.float32)
    low, high = np.percentile(data, [lower, upper])
    if high - low <= 1e-12:
        return data - low
    return (data - low) / (high - low)


def segment_regions(
    channel: np.ndarray,
    algorithm: str,
    parameters: dict[str, Any] | None,
    rectangles: list[tuple[int, int, int, int]],
    *,
    pixel_size_x: float | None = None,
    pixel_size_y: float | None = None,
    details: dict[str, Any] | None = None,
) -> np.ndarray:
    """Segment only inside these rectangles of one 2D channel; labels come back in the full frame.

    Everything that depends on the whole image is computed on the whole image: the classical
    threshold (after the same smoothing) and Cellpose's brightness scaling, which is applied to
    the full image before cropping (Cellpose is then told not to scale again). Size limits apply
    to the pasted, full-frame labels. Object numbers are unique across rectangles.
    """

    import time

    from cellquant.crop import objects_at_crop_edges

    timings: dict[str, float] = {}
    if algorithm not in _BACKENDS:
        raise SegmentationError(f"Unknown segmentation algorithm '{algorithm}'.")
    backend = _BACKENDS[algorithm]
    image = np.asarray(channel)
    parameters = dict(parameters or {})
    region_parameters = dict(parameters)
    if algorithm == "classical":
        parsed = _parse_classical(parameters)
        source = image.astype(np.float64)
        if parsed.sigma > 0:
            source = gaussian(source, sigma=parsed.sigma, preserve_range=True)
        if parsed.threshold_method == "otsu":
            try:
                threshold = float(threshold_otsu(source))
            except ValueError as exc:
                raise SegmentationError("The source channel has uniform intensity, so a threshold could not be computed.") from exc
        else:
            threshold = float(parsed.threshold)  # type: ignore[arg-type]
        region_parameters.update(sigma=0.0, threshold_method="manual", threshold=threshold)
    elif algorithm == "cellpose":
        started = time.perf_counter()
        source = normalize_like_cellpose(image)  # the whole image's scaling
        timings["normalize"] = time.perf_counter() - started
        region_parameters["normalize"] = False
        if region_parameters.get("diameter_px") in (None, 0) and hasattr(backend, "estimate_diameter"):
            # Classic Cellpose estimates the size once, on the largest crop rectangle (not a full-image
            # pass), so every crop uses the same diameter. A diameter given in µm or pixels skips this.
            started = time.perf_counter()
            y0, y1, x0, x1 = max(rectangles, key=lambda box: (box[1] - box[0]) * (box[3] - box[2]))
            estimate = backend.estimate_diameter(np.ascontiguousarray(source[y0:y1, x0:x1]), region_parameters)
            timings["size_estimate"] = time.perf_counter() - started
            if estimate:
                region_parameters["diameter_px"] = estimate
                if details is not None:
                    details["diameter_px_used"] = estimate
    else:
        source = image
    for key in ("min_area_px", "max_area_px", "min_area_um2", "max_area_um2", "exclude_border"):
        region_parameters.pop(key, None)  # applied once, to the full frame
    full = np.zeros(image.shape, dtype=np.int32)
    offset = 0
    with_details = getattr(backend, "segment_with_details", None)
    started = time.perf_counter()
    with stage_timer() as stages:
        for y0, y1, x0, x1 in rectangles:
            part = np.ascontiguousarray(source[y0:y1, x0:x1])
            if with_details is not None:
                raw, engine_details = with_details(part, region_parameters)
                engine_details.pop("timings", None)
                if details is not None:
                    details["engine"] = engine_details
            else:
                raw = backend.segment(part, region_parameters)
            labels = np.asarray(raw).astype(np.int32, copy=False)
            if labels.shape != part.shape:
                raise SegmentationError("Segmentation labels do not match the image shape.")
            inside = labels > 0
            full[y0:y1, x0:x1][inside] = labels[inside] + offset
            offset += int(labels.max()) if labels.size else 0
    elapsed = time.perf_counter() - started
    timings["network"] = stages["network"]
    timings["masks_and_rest"] = max(0.0, elapsed - stages["network"])
    n_before = _object_count(full)
    filtered = filter_objects(full, parameters, pixel_size_x=pixel_size_x, pixel_size_y=pixel_size_y)
    if details is not None:
        n_after = _object_count(filtered)
        details["n_before_filter"] = n_before
        details["n_after_filter"] = n_after
        details["percent_excluded_by_size"] = float("nan") if n_before == 0 else 100.0 * (n_before - n_after) / n_before
        details["crop_rectangles"] = [list(map(int, box)) for box in rectangles]
        details["crop_edge_objects"] = objects_at_crop_edges(filtered, rectangles)
        details["timings"] = {**details.get("timings", {}), **timings}
    return filtered


def _object_count(labels: np.ndarray) -> int:
    return int(np.count_nonzero(np.unique(labels)))


def filter_objects(
    labels: np.ndarray,
    parameters: dict[str, Any] | None = None,
    *,
    pixel_size_x: float | None = None,
    pixel_size_y: float | None = None,
    min_area_px: float | None = None,
    max_area_px: float | None = None,
    min_area_um2: float | None = None,
    max_area_um2: float | None = None,
    exclude_border: bool = False,
    min_slices: int = 1,
) -> np.ndarray:
    """Remove objects outside the requested size range. Label ids of kept objects stay put.

    For a 3D label image ``(z, y, x)`` the size is the object's largest
    cross-section, so limits mean the same as in 2D; the border is the XY
    edge of any slice; objects in fewer than ``min_slices`` slices are removed.
    """

    parsed = _filter_settings(
        parameters or {},
        min_area_px=min_area_px,
        max_area_px=max_area_px,
        min_area_um2=min_area_um2,
        max_area_um2=max_area_um2,
        exclude_border=exclude_border,
    )
    output = np.array(labels, copy=True, dtype=np.int32)
    if output.ndim == 3:
        return _filter_volume(output, parsed, pixel_size_x, pixel_size_y, min_slices)
    if parsed["exclude_border"]:
        output = clear_border(output)
    needs_area = any(
        parsed[key] is not None
        for key in ("min_area_px", "max_area_px", "min_area_um2", "max_area_um2")
    )
    if not needs_area:
        return output
    pixel_size = isotropic_pixel_size(pixel_size_x, pixel_size_y)
    if (parsed["min_area_um2"] is not None or parsed["max_area_um2"] is not None) and pixel_size is None:
        raise CalibrationError(
            "Object size limits are in µm², but this image has no pixel size. "
            "Provide a pixel size or set the limit in pixels."
        )
    area_scale = 1.0 if pixel_size is None else pixel_size * pixel_size
    remove: list[int] = []
    for prop in regionprops(output):
        area_px = float(prop.area)
        area_physical = area_px * area_scale
        if parsed["min_area_px"] is not None and area_px < parsed["min_area_px"]:
            remove.append(int(prop.label))
        elif parsed["max_area_px"] is not None and area_px > parsed["max_area_px"]:
            remove.append(int(prop.label))
        elif parsed["min_area_um2"] is not None and area_physical < parsed["min_area_um2"]:
            remove.append(int(prop.label))
        elif parsed["max_area_um2"] is not None and area_physical > parsed["max_area_um2"]:
            remove.append(int(prop.label))
    for object_id in remove:
        output[output == object_id] = 0
    return output


def _filter_volume(output, parsed, pixel_size_x, pixel_size_y, min_slices: int) -> np.ndarray:
    from cellquant.volume import largest_cross_section, object_z_extent

    remove: set[int] = set()
    if parsed["exclude_border"]:
        edges = np.concatenate(
            [output[:, 0, :].ravel(), output[:, -1, :].ravel(), output[:, :, 0].ravel(), output[:, :, -1].ravel()]
        )
        remove.update(int(value) for value in np.unique(edges) if value)
    if min_slices > 1:
        for object_id, (count, _first, _last) in object_z_extent(output).items():
            if count < min_slices:
                remove.add(object_id)
    limits = [parsed[key] for key in ("min_area_px", "max_area_px", "min_area_um2", "max_area_um2")]
    if any(value is not None for value in limits):
        pixel_size = isotropic_pixel_size(pixel_size_x, pixel_size_y)
        if (parsed["min_area_um2"] is not None or parsed["max_area_um2"] is not None) and pixel_size is None:
            raise CalibrationError(
                "Object size limits are in µm², but this image has no pixel size. "
                "Provide a pixel size or set the limit in pixels."
            )
        area_scale = 1.0 if pixel_size is None else pixel_size * pixel_size
        for object_id, area_px in largest_cross_section(output).items():
            physical = area_px * area_scale
            if (
                (parsed["min_area_px"] is not None and area_px < parsed["min_area_px"])
                or (parsed["max_area_px"] is not None and area_px > parsed["max_area_px"])
                or (parsed["min_area_um2"] is not None and physical < parsed["min_area_um2"])
                or (parsed["max_area_um2"] is not None and physical > parsed["max_area_um2"])
            ):
                remove.add(object_id)
    if remove:
        output[np.isin(output, sorted(remove))] = 0
    return output


def classical_segment(channel: np.ndarray, parameters: ClassicalParameters) -> np.ndarray:
    image = np.asarray(channel, dtype=np.float64)
    if parameters.sigma > 0:
        image = gaussian(image, sigma=parameters.sigma, preserve_range=True)
    if parameters.threshold_method == "otsu":
        try:
            threshold = float(threshold_otsu(image))
        except ValueError as exc:
            raise SegmentationError(
                "The source channel has uniform intensity, so a threshold could not be computed."
            ) from exc
    else:
        threshold = float(parameters.threshold)  # type: ignore[arg-type]
    mask = image > threshold
    if parameters.opening_radius_px:
        mask = binary_opening(mask, disk(parameters.opening_radius_px))
    if parameters.closing_radius_px:
        mask = binary_closing(mask, disk(parameters.closing_radius_px))
    if parameters.fill_holes:
        mask = ndimage.binary_fill_holes(mask)
    if not np.any(mask):
        return np.zeros(image.shape, dtype=np.int32)
    if parameters.use_watershed:
        return _watershed(mask, parameters)
    labeled, _count = label(mask, return_num=True, connectivity=1)
    return labeled.astype(np.int32, copy=False)


def _watershed(mask: np.ndarray, parameters: ClassicalParameters) -> np.ndarray:
    distance = ndimage.distance_transform_edt(mask)
    smoothed = gaussian(distance, sigma=1.0, preserve_range=True)
    coordinates = peak_local_max(
        smoothed,
        min_distance=max(1, int(round(parameters.watershed_min_distance_px))),
        labels=mask.astype(bool),
        exclude_border=False,
    )
    markers = np.zeros(mask.shape, dtype=np.int32)
    if len(coordinates) == 0:
        labeled, _count = label(mask, return_num=True, connectivity=1)
        return labeled.astype(np.int32, copy=False)
    markers[tuple(coordinates.T)] = np.arange(1, len(coordinates) + 1, dtype=np.int32)
    labeled = watershed(
        -distance,
        markers,
        mask=mask,
        compactness=parameters.watershed_compactness,
    )
    return np.asarray(labeled, dtype=np.int32)


def _parse_classical(parameters: dict[str, Any]) -> ClassicalParameters:
    try:
        return ClassicalParameters.model_validate(parameters)
    except Exception as exc:
        raise RecipeValidationError(f"Classical segmentation parameters are invalid: {exc}") from exc


def _filter_settings(
    parameters: dict[str, Any],
    *,
    min_area_px: float | None,
    max_area_px: float | None,
    min_area_um2: float | None,
    max_area_um2: float | None,
    exclude_border: bool,
) -> dict[str, Any]:
    return {
        "min_area_px": min_area_px if min_area_px is not None else parameters.get("min_area_px"),
        "max_area_px": max_area_px if max_area_px is not None else parameters.get("max_area_px"),
        "min_area_um2": min_area_um2 if min_area_um2 is not None else parameters.get("min_area_um2"),
        "max_area_um2": max_area_um2 if max_area_um2 is not None else parameters.get("max_area_um2"),
        "exclude_border": bool(exclude_border or parameters.get("exclude_border", False)),
    }


register_segmentation_backend("classical", ClassicalBackend())
register_segmentation_backend("cellpose", CellposeBackend())
