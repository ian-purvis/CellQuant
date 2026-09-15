"""Microscope-style display colors for fluorescence channels.

Colors are display-only. They never alter measured intensities.
"""

from __future__ import annotations

from typing import Any, Sequence


# Cycle used when metadata and name hints are missing.
_FALLBACK_RGB: tuple[tuple[float, float, float], ...] = (
    (0.0, 0.22, 1.0),  # blue
    (0.0, 1.0, 0.0),  # green
    (1.0, 0.0, 1.0),  # magenta
    (1.0, 1.0, 0.0),  # yellow
    (1.0, 0.0, 0.0),  # red
    (0.0, 1.0, 1.0),  # cyan
)

# Substring → RGB. Longer / more specific keys should be checked first.
_NAME_HINTS: tuple[tuple[str, tuple[float, float, float]], ...] = (
    ("far red", (1.0, 0.0, 1.0)),
    ("farred", (1.0, 0.0, 1.0)),
    ("hoechst", (0.0, 0.22, 1.0)),
    ("dapi", (0.0, 0.22, 1.0)),
    ("nuclei", (0.0, 0.22, 1.0)),
    ("nuclear", (0.0, 0.22, 1.0)),
    ("alexa647", (1.0, 0.0, 1.0)),
    ("alexa 647", (1.0, 0.0, 1.0)),
    ("af647", (1.0, 0.0, 1.0)),
    ("cy5", (1.0, 0.0, 1.0)),
    ("alexa594", (1.0, 0.0, 0.0)),
    ("alexa 594", (1.0, 0.0, 0.0)),
    ("af594", (1.0, 0.0, 0.0)),
    ("alexa555", (1.0, 1.0, 0.0)),
    ("alexa 555", (1.0, 1.0, 0.0)),
    ("af555", (1.0, 1.0, 0.0)),
    ("alexa488", (0.0, 1.0, 0.0)),
    ("alexa 488", (0.0, 1.0, 0.0)),
    ("af488", (0.0, 1.0, 0.0)),
    ("fitc", (0.0, 1.0, 0.0)),
    ("gfp", (0.0, 1.0, 0.0)),
    ("egfp", (0.0, 1.0, 0.0)),
    ("tritc", (1.0, 0.0, 0.0)),
    ("mcherry", (1.0, 0.0, 0.0)),
    ("rfp", (1.0, 0.0, 0.0)),
    ("cy3", (1.0, 1.0, 0.0)),
    ("txred", (1.0, 0.0, 0.0)),
    ("texas", (1.0, 0.0, 0.0)),
)


def normalize_rgb(value: Any) -> tuple[float, float, float] | None:
    """Convert common metadata color forms to float RGB in ``[0, 1]``."""

    if value is None:
        return None
    if isinstance(value, (tuple, list)) and len(value) >= 3:
        try:
            r, g, b = float(value[0]), float(value[1]), float(value[2])
        except (TypeError, ValueError):
            return None
        peak = max(r, g, b)
        if peak > 1.0:
            # OME often stores 0–255 or 0–65535 channel colors.
            scale = 255.0 if peak <= 255.0 else 65535.0
            r, g, b = r / scale, g / scale, b / scale
        if not all(0.0 <= component <= 1.0 for component in (r, g, b)):
            return None
        if r == g == b == 0.0:
            return None
        return (r, g, b)
    for attr in ("r", "g", "b"):
        if not hasattr(value, attr):
            break
    else:
        try:
            return normalize_rgb(
                (getattr(value, "r"), getattr(value, "g"), getattr(value, "b"))
            )
        except Exception:
            return None
    if isinstance(value, str) and "," in value:
        parts = [part.strip() for part in value.split(",")]
        return normalize_rgb(parts[:3])
    return None


def color_from_channel_name(name: str) -> tuple[float, float, float] | None:
    """Best-effort LUT from a fluorescence channel label."""

    label = str(name or "").strip().lower().replace("_", " ")
    if not label:
        return None
    for needle, rgb in _NAME_HINTS:
        if needle in label:
            return rgb
    return None


def resolve_channel_colors(
    channel_names: Sequence[str],
    metadata_colors: Sequence[Any] | None = None,
) -> tuple[tuple[float, float, float], ...]:
    """Return one RGB LUT per channel: metadata → name hint → fallback cycle."""

    count = len(tuple(channel_names))
    raw = list(metadata_colors or [])
    resolved: list[tuple[float, float, float]] = []
    for index, name in enumerate(channel_names):
        color = None
        if index < len(raw):
            color = normalize_rgb(raw[index])
        if color is None:
            color = color_from_channel_name(name)
        if color is None:
            color = _FALLBACK_RGB[index % len(_FALLBACK_RGB)]
        resolved.append(color)
    if len(resolved) != count:
        raise ValueError("channel color resolution length mismatch")
    return tuple(resolved)
