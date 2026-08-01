"""QIF, because Lloyds Banking Group exports it and Halifax is on that platform.

QIF is a poor format and is used here only where a bank offers nothing better.
It carries no transaction ids at all, so identity rests entirely on the content
key, and it has no schema to validate against - a misread produces plausible
wrong numbers rather than an error.

Three hazards, all of which corrupt data silently:

**Dates are ambiguous and unlabelled.** UK exports write DD/MM; a US-shaped
parser reads the same string as MM/DD and transposes March and December. The
format is pinned and anything else is refused.

**Years may be two digits, sometimes with an apostrophe** (`14/03'26`), a
Quicken convention for post-2000 dates that a naive split mangles.

**Amounts may carry thousands separators**, and the decimal is not guaranteed.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date

from ..identity import content_key
from ..models import SourceTier, Transaction, TransactionStatus
from ..money import parse_amount
from .base import ParseError, StatementParser

# Quicken writes post-2000 two-digit years with an apostrophe separator.
_DATE_SEPARATORS = re.compile(r"[/\-.']")

def parse_qif_date(text: str) -> date:
    """Parse a QIF date as DAY FIRST, refusing anything that is not.

    Never inferred. A date library left to guess will silently transpose the
    day and month for the first twelve days of every month, and the result
    looks entirely plausible.
    """
    parts = [part for part in _DATE_SEPARATORS.split(text.strip()) if part]
    if len(parts) != 3:
        raise ParseError(f"cannot read QIF date {text!r}")

    day, month, year = parts
    try:
        day_number, month_number = int(day), int(month)
    except ValueError as exc:
        raise ParseError(f"cannot read QIF date {text!r}") from exc

    if month_number > 12:
        raise ParseError(
            f"QIF date {text!r} has a month above 12 - this looks like a "
            "month-first export, which this parser will not guess at"
        )

    # Quicken's own convention: an apostrophe separator marks 2000s. Absent
    # that, a two-digit year in a bank export is not from the 1900s.
    year_number = 2000 + int(year) if len(year) == 2 else int(year)

    try:
        # A calendar date, not an instant: a statement line has no time and
        # no zone, so attaching one would invent precision.
        return date(year_number, month_number, day_number)
    except ValueError as exc:
        raise ParseError(f"QIF date {text!r} is not a real date") from exc


class QifParser(StatementParser):
    """Parses the bank and credit-card record types.

    Investment QIF is a different grammar and is deliberately unsupported
    rather than half-read.
    """

    source = "qif"
    date_format = "%d/%m/%Y"
    expected_headers = ()

    def sniff(self, payload: bytes) -> bool:
        try:
            head = payload.decode("utf-8", errors="strict")[:200].lstrip()
        except UnicodeDecodeError:
            return False
        return head.upper().startswith("!TYPE:")

    def parse(self, payload: bytes, *, account_id: str) -> Iterator[Transaction]:
        text = payload.decode("utf-8", errors="strict")
        header = text.lstrip()[:40].upper()
        if "INVST" in header:
            raise ParseError(
                "investment QIF uses a different grammar and is not supported; "
                "reading it as a bank export would produce plausible nonsense"
            )

        record: dict[str, str] = {}
        for raw_line in text.splitlines():
            line = raw_line.rstrip()
            if not line or line.startswith("!"):
                continue
            if line.startswith("^"):
                if record:
                    yield self._build(record, account_id)
                record = {}
                continue
            record[line[0]] = line[1:].strip()

        # A trailing record with no closing caret is malformed but common.
        if record:
            yield self._build(record, account_id)

    def _build(self, record: dict[str, str], account_id: str) -> Transaction:
        if "D" not in record or "T" not in record:
            raise ParseError(f"QIF record missing a date or amount: {record!r}")

        when = parse_qif_date(record["D"])
        amount = parse_amount(record["T"])

        payee = record.get("P", "")
        memo = record.get("M", "")
        description = payee or memo

        return Transaction(
            account_id=account_id,
            amount_minor=amount,
            value_date=when,
            booking_date=when,
            description=description,
            counterparty=payee,
            # QIF's cleared flag is advisory and frequently absent, and an
            # export is a record of what already happened. Everything in one is
            # therefore booked; inventing pending records from a missing marker
            # would create settlements that never arrive.
            status=TransactionStatus.BOOKED,
            source=self.source,
            # QIF carries no transaction id, so identity rests wholly on
            # content. Overlapping exports depend on that working.
            source_id=None,
            tier=SourceTier.SYNTHETIC,
            content_key=content_key(
                account_id=account_id,
                amount_minor=amount,
                value_date=when,
                description=description,
            ),
            raw=dict(record),
        )
