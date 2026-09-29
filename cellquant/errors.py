"""User-facing errors.

Messages on these exceptions are meant to be shown directly. Tracebacks belong
in a developer log, not in the message text.
"""


class CellQuantError(Exception):
    """Base class for analysis failures a caller can report as-is."""


class ImageLoadError(CellQuantError):
    """The image file could not be opened or interpreted."""


class ChannelMismatchError(CellQuantError):
    """A recipe channel index does not exist in the image."""


class SegmentationError(CellQuantError):
    """Segmentation could not be run with the requested method."""


class CalibrationError(CellQuantError):
    """A physical unit was requested without usable pixel size."""


class RecipeValidationError(CellQuantError):
    """The analysis recipe is incomplete or inconsistent."""
