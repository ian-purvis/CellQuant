"""Which images each analysis runs on, and how images are grouped for choosing them.

An experiment's images can be grouped by **channel layout** (images whose files list the same
channels in the same order), by **folder** (the folder each image sits in, inside the folder that
was added), or by both (layout, then folder). The window's Plan dock shows these groups, and its
all-or-one buttons tick or untick a whole group, a whole analysis, or chosen images.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath

GROUPINGS = ("layout", "folder", "layout_folder")
GROUPING_LABELS = {
    "layout": "Channel layout",
    "folder": "Folder",
    "layout_folder": "Channel layout, then folder",
}


@dataclass
class ImageGroup:
    key: str
    label: str
    image_ids: list[str] = field(default_factory=list)
    children: list["ImageGroup"] = field(default_factory=list)

    def all_image_ids(self) -> list[str]:
        ids = list(self.image_ids)
        for child in self.children:
            ids.extend(child.all_image_ids())
        return ids


def layout_key(record) -> tuple[str, ...]:
    """The channel names in the file, in order; unnamed files are keyed by their channel count."""

    if record.channel_names:
        return tuple(str(name) for name in record.channel_names)
    count = record.number_of_channels or 0
    return tuple(f"Channel {index + 1}" for index in range(count))


def layout_label(key: tuple[str, ...]) -> str:
    if not key:
        return "Unknown channels"
    return f"{' · '.join(key)} ({len(key)} channel{'s' if len(key) != 1 else ''})"


def folder_of(record) -> str:
    """The folder the image sits in, inside the folder that was added ('' when it is at the top)."""

    relative = record.relative_path or record.filename
    parent = str(PurePosixPath(relative.replace("\\", "/")).parent)
    return "" if parent in (".", "") else parent


def group_images(records, by: str = "layout") -> list[ImageGroup]:
    """Group image records, keeping the experiment's order within each group."""

    if by not in GROUPINGS:
        raise ValueError(f"Unknown grouping: {by}")
    records = list(records)
    if by == "folder":
        return _by(records, lambda record: folder_of(record), lambda key: key or "(top folder)", "folder")
    layouts = _by(records, layout_key, layout_label, "layout")
    if by == "layout_folder":
        lookup = {record.image_id: record for record in records}
        for group in layouts:
            members = [lookup[image_id] for image_id in group.image_ids]
            group.children = _by(members, lambda record: folder_of(record), lambda key: key or "(top folder)", f"{group.key}/folder")
            group.image_ids = []
    return layouts


def _by(records, key_of, label_of, prefix: str) -> list[ImageGroup]:
    groups: dict = {}
    for record in records:
        key = key_of(record)
        if key not in groups:
            groups[key] = ImageGroup(key=f"{prefix}:{key!r}", label=label_of(key))
        groups[key].image_ids.append(record.image_id)
    return list(groups.values())
