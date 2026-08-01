"""A common base for "this input was not what we required".

Parsers, the money reader and the JSON boundary each raised their own
unrelated exception, so a caller wanting to report "that file could not be
read, here is why" had to know all three - and missing one turned a clear
domain failure into an unhandled traceback naming an internal function.

They share a base so the contract can be stated once: anything raised while
turning external input into stored records is a `DataError`, and the layer
that knows which file or account is being processed catches it and says so.

Deliberately a ValueError, because that is what it is: a value did not have
the shape required. Nothing here indicates a bug in this code.
"""

from __future__ import annotations


class DataError(ValueError):
    """External input could not be turned into a record."""
