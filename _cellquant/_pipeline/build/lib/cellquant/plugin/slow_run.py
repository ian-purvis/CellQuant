"""Detect when a CellQuant unit of work looks too slow for an unattended run."""

from __future__ import annotations

# Ask once per job when a single plane/image has taken (or just took) this long.
SLOW_UNIT_SECONDS = 5 * 60

_MODE_LABELS = {
    "volume_3d": "3D volume",
    "stitch_2d": "2D + Z stitch",
    "single_plane_2d": "Single Z plane",
    "max_projection_2d": "Max Z projection",
}

# Alternatives that are typically faster than the current mode (never include current).
_FASTER_ALTERNATIVES = {
    "volume_3d": ("max_projection_2d", "single_plane_2d", "stitch_2d"),
    "stitch_2d": ("max_projection_2d", "single_plane_2d"),
    "single_plane_2d": (),
    "max_projection_2d": ("single_plane_2d",),
}


def should_prompt_slow_run(
    *,
    already_prompted: bool,
    elapsed_current_unit_s: float | None,
    last_finished_unit_s: float | None,
    threshold_s: float = SLOW_UNIT_SECONDS,
) -> bool:
    """Return True when the UI should ask Continue vs Cancel."""

    if already_prompted:
        return False
    if elapsed_current_unit_s is not None and elapsed_current_unit_s >= threshold_s:
        return True
    if last_finished_unit_s is not None and last_finished_unit_s >= threshold_s:
        return True
    return False


def faster_mode_hint(current_mode: str | None) -> str:
    """Suggest faster modes, skipping ones the user is already running."""

    if not current_mode:
        return (
            "If this is too slow, try a faster segmentation mode "
            "(for example Max Z projection or Single Z plane)."
        )
    label = _MODE_LABELS.get(current_mode, current_mode)
    alternatives = [
        _MODE_LABELS[mode_id]
        for mode_id in _FASTER_ALTERNATIVES.get(current_mode, ())
        if mode_id in _MODE_LABELS
    ]
    if not alternatives:
        return (
            f"You are already using {label}. "
            "If this is still too slow, use Kill and try a smaller crop, "
            "a different device setting, or fewer files."
        )
    joined = ", ".join(alternatives)
    return f"You are currently using {label}. Faster options to try next: {joined}."


def slow_run_prompt_text(
    *,
    elapsed_current_unit_s: float | None,
    last_finished_unit_s: float | None,
    unit_label: str = "plane or image",
    current_mode: str | None = None,
) -> str:
    """Build dialog body text for a slow-run confirmation."""

    minutes_bits: list[str] = []
    if elapsed_current_unit_s is not None and elapsed_current_unit_s >= 60:
        minutes_bits.append(
            f"The current {unit_label} has already been running for about "
            f"{elapsed_current_unit_s / 60.0:.0f} minutes."
        )
    elif elapsed_current_unit_s is not None:
        minutes_bits.append(
            f"The current {unit_label} has already been running for about "
            f"{elapsed_current_unit_s:.0f} seconds."
        )
    if last_finished_unit_s is not None:
        minutes_bits.append(
            f"The previous {unit_label} took about {last_finished_unit_s / 60.0:.0f} minutes."
        )
    lead = " ".join(minutes_bits) or "This run looks slower than expected."
    return (
        f"{lead}\n\n"
        "At this rate the full job may take a long time (often much more than a few minutes).\n\n"
        "Continue to keep going.\n"
        "Cancel stops after the current plane/checkpoint (fine for smaller jobs).\n"
        "Kill force-stops the Cellpose worker immediately (use this for long mid-plane runs "
        "so you do not need Task Manager).\n\n"
        f"{faster_mode_hint(current_mode)}"
    )


__all__ = [
    "SLOW_UNIT_SECONDS",
    "faster_mode_hint",
    "should_prompt_slow_run",
    "slow_run_prompt_text",
]
