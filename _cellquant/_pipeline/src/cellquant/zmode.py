from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ZSelection:
    mode: str
    first: int
    last: int

    @property
    def slice(self) -> slice:
        return slice(self.first - 1, self.last)


_SPEC = re.compile(r"^(single|max|stitch|volume):(\d+)(?:-(\d+))?$")


def parse_z_selection(spec: str, size_z: int) -> ZSelection:
    """Parse a documented 1-based UI Z selection and validate against size_z."""
    match = _SPEC.fullmatch(spec.strip().lower())
    if not match:
        raise ValueError("Z selection must be single:N, max:A-B, stitch:A-B, or volume:A-B")
    mode, first_s, last_s = match.groups()
    first, last = int(first_s), int(last_s or first_s)
    if mode == "single" and last_s is not None:
        raise ValueError("single accepts one 1-based plane: single:N")
    if mode != "single" and last_s is None:
        raise ValueError(f"{mode} requires an inclusive 1-based range: {mode}:A-B")
    if not (1 <= first <= last <= size_z):
        raise ValueError(f"Z range {first}-{last} is outside 1-{size_z}")
    return ZSelection(mode, first, last)


def prepare_z(channel_zyx, selection: ZSelection):
    cropped = channel_zyx[selection.slice]
    if selection.mode == "single":
        return cropped[0]
    if selection.mode == "max":
        return cropped.max(axis=0)
    return cropped

