"""Manual object edits.

Edits are an ordered log applied to the automated label image. The automated
labels are never overwritten. Replaying the log reproduces the final labels.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_serializer


class EditOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_id: str
    object_id: int
    operation: str
    timestamp: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    # The segmentation the edit was made on. Object ids only mean something for that
    # segmentation, so an edit is applied only while it is the current one. Blank for
    # edits saved by older versions, which are always applied.
    segmentation: str = ""
    # The analysis (recipe id) the edit was made in. Two analyses can share a segmentation (for
    # example they differ only in markers), but each keeps its own deletions. Blank for edits made
    # before analyses existed; those belong to the experiment's original analysis.
    analysis: str = ""

    @model_serializer(mode="wrap")
    def _drop_blank_analysis(self, handler):
        data = handler(self)
        if isinstance(data, dict) and not data.get("analysis"):
            data.pop("analysis", None)
        return data

    @classmethod
    def create(
        cls,
        image_id: str,
        object_id: int,
        operation: str,
        parameters: dict[str, Any] | None = None,
    ) -> EditOperation:
        return cls(
            image_id=image_id,
            object_id=int(object_id),
            operation=operation,
            timestamp=datetime.now(timezone.utc).isoformat(),
            parameters=parameters or {},
        )


def apply_edits(automated: np.ndarray, edits: list[EditOperation] | list[dict]) -> np.ndarray:
    """Return final labels. ``automated`` is left unchanged."""

    labels = np.array(automated, copy=True)
    operations = [item if isinstance(item, EditOperation) else EditOperation.model_validate(item) for item in edits]
    for operation in operations:
        if operation.operation == "delete":
            labels[labels == operation.object_id] = 0
        elif operation.operation == "restore":
            mask = automated == operation.object_id
            labels[mask] = operation.object_id
        elif operation.operation == "erase":
            index = _coordinates(operation.parameters, labels.ndim)
            if len(index[0]):
                labels[index] = 0
        elif operation.operation == "paint":
            index = _coordinates(operation.parameters, labels.ndim)
            if len(index[0]):
                labels[index] = operation.object_id
        else:
            raise ValueError(f"Unknown edit operation '{operation.operation}'.")
    return labels


def excluded_object_ids(automated: np.ndarray, edits: list[EditOperation] | list[dict]) -> list[int]:
    """Automated objects removed by delete and not brought back."""

    if automated.size == 0 or int(np.max(automated)) == 0:
        return []
    final = apply_edits(automated, edits)
    present = set(np.unique(automated).tolist()) - {0}
    surviving = set(np.unique(final).tolist()) - {0}
    return sorted(int(object_id) for object_id in present - surviving)


def edited_object_ids(edits: list[EditOperation] | list[dict]) -> set[int]:
    operations = [item if isinstance(item, EditOperation) else EditOperation.model_validate(item) for item in edits]
    return {
        int(operation.object_id)
        for operation in operations
        if operation.operation in {"paint", "erase", "restore"}
    }


def operations_from_diff(
    before: np.ndarray,
    after: np.ndarray,
    image_id: str,
) -> list[EditOperation]:
    """Record paint and erase operations that turn ``before`` into ``after``."""

    if before.shape != after.shape:
        raise ValueError("Drawn labels do not match the image shape.")
    changed = before != after
    if not np.any(changed):
        return []
    where = np.nonzero(changed)
    new_values = after[where]
    old_values = before[where]
    operations: list[EditOperation] = []
    for object_id in sorted({int(value) for value in new_values.tolist() if int(value) != 0}):
        selected = new_values == object_id
        operations.append(
            EditOperation.create(
                image_id,
                object_id,
                "paint",
                _coordinate_parameters(*(axis[selected] for axis in where)),
            )
        )
    erased = new_values == 0
    if np.any(erased):
        for object_id in sorted({int(value) for value in old_values[erased].tolist() if int(value) != 0}):
            selected = erased & (old_values == object_id)
            operations.append(
                EditOperation.create(
                    image_id,
                    object_id,
                    "erase",
                    _coordinate_parameters(*(axis[selected] for axis in where)),
                )
            )
    return operations


def _coordinates(parameters: dict[str, Any], ndim: int = 2) -> tuple[np.ndarray, ...]:
    """Index arrays for an edit: (rows, cols), or (planes, rows, cols) for 3D labels."""

    rows = np.asarray(parameters.get("rows", []), dtype=int)
    cols = np.asarray(parameters.get("cols", []), dtype=int)
    if rows.shape != cols.shape:
        raise ValueError("Edit coordinates must contain the same number of rows and columns.")
    if ndim == 2:
        if "planes" in parameters:
            raise ValueError("This edit was made on 3D labels and cannot be applied to 2D labels.")
        return rows, cols
    if "planes" not in parameters:
        raise ValueError("This edit was made on 2D labels and cannot be applied to 3D labels.")
    planes = np.asarray(parameters["planes"], dtype=int)
    if planes.shape != rows.shape:
        raise ValueError("Edit coordinates must contain the same number of planes, rows and columns.")
    return planes, rows, cols


def _coordinate_parameters(*axes: np.ndarray) -> dict[str, list[int]]:
    if len(axes) == 3:
        planes, rows, cols = axes
        return {"planes": planes.astype(int).tolist(), "rows": rows.astype(int).tolist(), "cols": cols.astype(int).tolist()}
    rows, cols = axes
    return {"rows": rows.astype(int).tolist(), "cols": cols.astype(int).tolist()}
