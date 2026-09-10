"""Path guards for napari widgets (magicgui defaults to ``.``)."""

from __future__ import annotations

from pathlib import Path


def _resolved(path: str | Path) -> Path:
    if path is None:
        raise ValueError("Choose a path first (the widget still points at '.' by default).")
    result = Path(path)
    # magicgui FileEdit often starts as "." — treat that as "not chosen".
    if str(result).strip() in {"", ".", "./", ".\\"}:
        raise ValueError("Choose a path first (the widget still points at '.' by default).")
    return result


def require_existing_file(path: str | Path, *, what: str = "file") -> Path:
    """Require a real file path; reject magicgui's unset ``.`` default."""

    result = _resolved(path)
    try:
        if not result.is_file():
            raise FileNotFoundError(f"{what} not found or is not a file: {result}")
    except PermissionError as exc:
        raise PermissionError(
            f"Cannot access {what} {result}: permission denied. "
            "Choose a real file path (not '.')."
        ) from exc
    return result.resolve()


def require_existing_dir(path: str | Path, *, what: str = "folder", create: bool = False) -> Path:
    """Require a directory path; reject magicgui's unset ``.`` default."""

    result = _resolved(path)
    try:
        if create:
            result.mkdir(parents=True, exist_ok=True)
        if not result.is_dir():
            raise FileNotFoundError(f"{what} not found or is not a folder: {result}")
    except PermissionError as exc:
        raise PermissionError(
            f"Cannot access {what} {result}: permission denied. "
            "Choose a writable folder (not '.'). Cloud folders are supported; "
            "CellQuant stages run stores locally before publishing."
        ) from exc
    return result.resolve()
