"""A common base for "this input was not what we required".

Parsers, the money reader and the JSON boundary each fail in their own way. Left
unrelated, a caller wanting to report "that file could not be read, here is why"
would have to know all three, and missing one turns a clear domain failure into
an unhandled traceback naming an internal function.

They share a base so the contract can be stated once: anything raised while
turning external input into stored records is a `DataError`, and the layer
that knows which file or account is being processed catches it and says so.

Deliberately a ValueError, because that is what it is: a value did not have
the shape required. Nothing here indicates a bug in this code.
"""

from __future__ import annotations


class DataError(ValueError):
    """External input could not be turned into a record."""


class DuplicateImportedIdError(ValueError):
    """The push to Actual was refused because two store rows share an imported id.

    `str()` is the full refusal for the operator's log, including the start
    of the offending key. `public` is the same refusal without that key: the
    key is derived from the payment's content, so it stays out of anything
    sent to a phone.
    """

    def __init__(self, message: str, *, public: str) -> None:
        super().__init__(message)
        self.public = public
