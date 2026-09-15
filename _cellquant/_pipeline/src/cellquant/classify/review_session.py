"""Authoritative review session state for quantification review."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from cellquant.classify import ClassificationRecipe, ClassificationResult
from cellquant.classify.discover_classify import default_population_label
from cellquant.classify.store import result_from_reopened


class ReviewState(str, Enum):
    SAVED_SETTINGS = "Saved settings"
    UNSAVED_CHANGES = "Unsaved changes"
    COMPUTING_PREVIEW = "Computing preview"
    PREVIEW_OUT_OF_DATE = "Preview out of date"
    READY_TO_SAVE = "Ready to save"


@dataclass
class ReviewSession:
    """Baseline/draft settings plus preview validity for one opened analysis."""

    parent_path: Path
    parent_run_id: str
    parent_manifest_sha256: str
    baseline_recipe: ClassificationRecipe
    baseline_calls: object  # pd.DataFrame
    baseline_result: ClassificationResult
    draft_recipe: ClassificationRecipe
    baseline_labels: object = None  # LabelVolume
    sample_name: str = ""
    image_id: str = ""
    segmentation_mode: str = "unavailable"
    population_label: str = field(default_factory=default_population_label)
    revision: int = 0
    proposed_result: ClassificationResult | None = None
    proposed_revision: int | None = None
    computing: bool = False
    baseline_calls_valid: bool = True
    source_verified: bool = True

    @classmethod
    def from_reopened(cls, bundle, *, sample_name: str = "", segmentation_mode: str = "unavailable"):
        recipe = ClassificationRecipe(bundle.recipe.raw)
        result = result_from_reopened(bundle)
        parent = Path(bundle.path) if bundle.path is not None else Path(".")
        digest = bundle.manifest_sha256 or ""
        context = bundle.context if isinstance(bundle.context, dict) else {}
        return cls(
            parent_path=parent,
            parent_run_id=parent.name,
            parent_manifest_sha256=digest,
            baseline_recipe=recipe,
            baseline_calls=bundle.calls.copy(),
            baseline_result=result,
            draft_recipe=ClassificationRecipe(recipe.raw),
            baseline_labels=bundle.labels,
            sample_name=sample_name or str(context.get("specimen_id") or context.get("image_id") or parent.name),
            image_id=str(context.get("image_id") or ""),
            segmentation_mode=segmentation_mode,
            proposed_result=result,
            proposed_revision=0,
            baseline_calls_valid=True,
            source_verified=True,
        )

    @property
    def dirty(self) -> bool:
        return self.draft_recipe.fingerprint != self.baseline_recipe.fingerprint

    @property
    def state(self) -> ReviewState:
        if self.computing:
            return ReviewState.COMPUTING_PREVIEW
        if self.dirty and (self.proposed_result is None or self.proposed_revision != self.revision):
            return ReviewState.PREVIEW_OUT_OF_DATE if self.proposed_result is not None else ReviewState.UNSAVED_CHANGES
        if self.dirty and self.proposed_revision == self.revision and self.proposed_result is not None:
            return ReviewState.READY_TO_SAVE
        if not self.dirty:
            return ReviewState.SAVED_SETTINGS
        return ReviewState.UNSAVED_CHANGES

    def set_draft_recipe(self, recipe: ClassificationRecipe, *, bump: bool = True) -> None:
        self.draft_recipe = ClassificationRecipe(recipe.raw)
        if bump:
            self.mark_stale()

    def mark_stale(self) -> None:
        self.revision += 1
        # Keep last proposed result visible but mark it out of date via revision mismatch.

    def begin_compute(self) -> int:
        self.computing = True
        return self.revision

    def accept_preview(self, revision: int, result: ClassificationResult) -> bool:
        self.computing = False
        if revision != self.revision:
            return False
        self.proposed_result = result
        self.proposed_revision = revision
        return True

    def discard_late_preview(self) -> None:
        self.computing = False

    def revert_to_saved(self) -> None:
        self.draft_recipe = ClassificationRecipe(self.baseline_recipe.raw)
        self.proposed_result = self.baseline_result
        self.revision += 1
        self.proposed_revision = self.revision
        self.computing = False

    def invalidate_baseline_calls(self, reason: str = "") -> None:
        self.baseline_calls_valid = False
        self.baseline_calls = None
        if self.baseline_result is not None:
            metadata = deepcopy(self.baseline_result.metadata)
            metadata["baseline_invalid_reason"] = reason or "Baseline calls invalidated"
            self.baseline_result = ClassificationResult(
                self.baseline_result.calls.iloc[0:0].copy(),
                self.baseline_result.queries.iloc[0:0].copy(),
                self.baseline_result.patterns.iloc[0:0].copy(),
                self.baseline_result.exclusions.iloc[0:0].copy(),
                metadata,
            )

    def header_text(self) -> str:
        parts = [
            self.sample_name or self.parent_run_id,
            f"mode={self.segmentation_mode}",
            f"parent={self.parent_run_id}",
            str(self.state.value),
        ]
        if self.image_id:
            parts.insert(1, f"image={self.image_id}")
        return " · ".join(parts)
