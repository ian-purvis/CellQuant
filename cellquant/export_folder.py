"""Where an export goes: a new dated subfolder of the folder the user picked, so an earlier export is never overwritten.

No interface code here, so it can be tested without Qt.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path


def filename_safe(name: str, fallback: str = "analysis") -> str:
    """The name with every run of characters a file name cannot hold (or should not) turned into one underscore."""

    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(name or "")).strip("_.") or fallback


def export_folder_name(analysis_name: str | None = None, when: datetime | None = None) -> str:
    """``<YYYY-MM-DD_HHMM>_<analysis name>``; without an analysis name (Export all analyses) ``..._all_analyses``."""

    stamp = (when or datetime.now()).strftime("%Y-%m-%d_%H%M")
    return f"{stamp}_{filename_safe(analysis_name) if analysis_name is not None else 'all_analyses'}"


def new_export_folder(parent: str | Path, analysis_name: str | None = None, when: datetime | None = None) -> Path:
    """A folder inside ``parent`` that does not exist yet (``_2``, ``_3``... are added to a name already taken).

    The folder is not created: ``Controller.export`` makes the directory it is given.
    """

    base = export_folder_name(analysis_name, when)
    folder = Path(parent) / base
    number = 2
    while folder.exists():
        folder = Path(parent) / f"{base}_{number}"
        number += 1
    return folder


def export_files(
    group_by: str | None,
    unit: str | None,
    metadata_columns: list[str] | tuple[str, ...] = (),
    all_analyses: bool = False,
) -> list[tuple[str, str]]:
    """The files an export writes with these choices, as (name, when) pairs; ``when`` is blank for a file always written.

    Mirrors ``Controller.export`` (one analysis) and ``Controller.export_all`` (``all_analyses``: one folder
    per analysis, each with the first list, plus the files that put every analysis side by side). ``unit`` None
    is "automatic": the deepest ``Folder N`` column. This only describes; it does not change what is written.
    """

    import pandas as pd

    from cellquant.storage import default_unit_column

    columns = list(metadata_columns)
    resolved = unit or default_unit_column(pd.DataFrame(columns=columns))
    unit_known = bool(resolved) and resolved in columns
    files: list[tuple[str, str]] = [
        ("objects.csv", ""),
        ("image_summary.csv", ""),
        ("settings_index.csv", ""),
        ("recipe.yaml", ""),
        ("run.json", "if the analysis was run"),
        ("mixed_settings.txt", "only if settings differ"),
    ]
    if group_by:
        files.append((f"grouped_by_{group_by}.csv", ""))
    if unit_known:
        files.append((f"units_by_{resolved}.csv", ""))
        if group_by and group_by != resolved:
            files.append((f"grouped_by_{group_by}_equal_units.csv", ""))
    if not all_analyses:
        return files
    each = [(f"<analysis name>/{name}", when) for name, when in files]
    extra: list[tuple[str, str]] = [("all_analyses_image_summary.csv", "")]
    if group_by:
        extra.append((f"all_analyses_grouped_by_{group_by}.csv", ""))
    extra.append(("analyses_not_exported.txt", "only if one has no results"))
    return extra + each
