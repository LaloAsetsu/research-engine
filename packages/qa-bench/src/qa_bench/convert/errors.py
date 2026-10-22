"""The one error of the converters."""

from __future__ import annotations


class ConversionError(ValueError):
    """A source row or item that the converter cannot map, with the id of the row in the message."""
