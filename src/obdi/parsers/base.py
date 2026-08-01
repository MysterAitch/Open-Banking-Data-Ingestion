"""Parser contract, plus the two hazards that corrupt data silently.

Date formats: `DD/MM/YYYY` is near-universal in UK exports and is never
labelled. Auto-detection will transpose March and December without complaint,
so every parser PINS its format and raises on anything else.

Sign conventions: most UK banks split into unsigned money-in / money-out
columns; American Express inverts, making a spend positive. A parser declares
its convention explicitly rather than inferring it.
"""

from __future__ import annotations

import csv
import io
from abc import ABC, abstractmethod
from collections.abc import Iterator
from datetime import date, datetime

from ..errors import DataError
from ..models import Transaction


class ParseError(DataError):
    """Raised when input does not match what the parser expects.

    Always preferred over a best-effort guess: a loud failure costs minutes, a
    silent misparse corrupts the store and is found months later.
    """


def parse_date(text: str, fmt: str) -> date:
    """Parse a date with a pinned format, refusing anything else."""
    cleaned = text.strip()
    if not cleaned:
        raise ParseError("empty date")
    try:
        # Naive on purpose: a statement line is a calendar date, with no time
        # and no zone. Attaching one would invent precision the bank never
        # supplied, and the result is converted to a date immediately.
        return datetime.strptime(cleaned, fmt).date()  # noqa: DTZ007
    except ValueError as exc:
        raise ParseError(f"date {cleaned!r} does not match pinned format {fmt!r}") from exc


class StatementParser(ABC):
    """Base for file-export parsers.

    `source` names the origin and is part of every record's provenance.
    `date_format` is pinned per source. `expected_headers` drives version
    detection: banks change their export layout without notice, so a parser
    that no longer recognises its input must refuse rather than misread it.
    """

    source: str
    date_format: str
    expected_headers: tuple[str, ...]
    encoding: str = "utf-8"

    def sniff(self, payload: bytes) -> bool:
        """Whether this parser recognises the payload's header row."""
        try:
            header = self._header(payload)
        except ParseError:
            return False
        return all(column in header for column in self.expected_headers)

    def _header(self, payload: bytes) -> list[str]:
        text = payload.decode(self.encoding, errors="strict")
        reader = csv.reader(io.StringIO(text))
        try:
            return [cell.strip() for cell in next(reader)]
        except StopIteration as exc:
            raise ParseError("file is empty") from exc

    def rows(self, payload: bytes) -> Iterator[dict[str, str]]:
        text = payload.decode(self.encoding, errors="strict")
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None:
            raise ParseError("file has no header row")
        missing = [c for c in self.expected_headers if c not in reader.fieldnames]
        if missing:
            raise ParseError(
                f"{self.source}: export layout changed - missing {missing}. "
                "Verify the format before widening the parser."
            )
        for row in reader:
            # A row with MORE fields than the header puts the surplus under the
            # restkey as a list, and one with fewer yields None. Both used to
            # reach .strip() and raise AttributeError, which breaks the parser
            # contract - callers catch ParseError and expect a message naming
            # the file, not a stack trace from a comprehension.
            if None in row:
                raise ParseError(
                    f"{self.source}: a row has more fields than the header. "
                    "An unquoted comma in a description is the usual cause."
                )
            yield {
                (key or "").strip(): (value or "").strip() for key, value in row.items()
            }

    @abstractmethod
    def parse(self, payload: bytes, *, account_id: str) -> Iterator[Transaction]:
        """Yield Transaction records. Implementations must not swallow errors."""
