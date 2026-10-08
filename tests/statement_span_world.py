"""Accounts of statements built through the doors a person's files use, with the day each was
received planted, so a test can say which days a statement covers and be wrong in a way the
code cannot hide.

A statement lands in two steps: the artefact with its planted receipt time (`land_artefact`,
the door a pull or upload uses), then `import_file`, which lands the same bytes again (ignored,
being the same artefact) and reads the rows. Nothing here reads a clock.

The layout is Santander's. `previous_close` set prints the layout's "Previous balance as at"
line, which states where the statement begins; left out, the statement states only its closing
day, as a layout that prints no start does.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path

from landing import import_file
from obdi.core.models import RawArtefact, SourceTier, Transaction
from obdi.ingest.identity import artefact_digest, content_key
from obdi.ingest.pipeline import media_type_of, reconcile_batch
from obdi.ingest.store import Store
from obdi.ingest.synthetic_pdf import build_pdf

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _ordinal(day: date) -> str:
    endings = {1: "st", 2: "nd", 3: "rd"}
    suffix = "th" if 10 <= day.day % 100 <= 20 else endings.get(day.day % 10, "th")
    return f"{day.day}{suffix}"


def _pounds(minor: int) -> str:
    return f"{minor / 100:,.2f}"


@dataclass(frozen=True)
class Spend:
    day: date
    payee: str
    minor: int


def statement(
    store: Store,
    root: Path,
    ref: str,
    closing: date,
    opening_minor: int,
    rows: list[Spend],
    *,
    received: date | None,
    previous_close: date | None = None,
    import_rows: bool = True,
) -> int:
    """Land one Santander statement; returns the balance owed at its close, in minor units.

    `received` is the day obdi is taken to have been given the file (noon UTC); None leaves it
    to `import_file`'s own clock, which no test of a period may do - it is only for statements
    whose receipt the test does not ask about. `import_rows` False holds the document without
    reading its rows into the account, as a statement uploaded for its balances while the bank's
    feed supplies the rows (it needs `received`).
    """
    owed = opening_minor + sum(row.minor for row in rows)
    lines = [
        "Santander UK plc. Registered Office: 2 Triton Square",
        f"Statement Date: {_ordinal(closing)} {calendar.month_name[closing.month]} "
        f"{closing.year}      Page No: 1 / 1",
        "Account credit limit:            3,000.00",
    ]
    if previous_close is not None:
        lines.append(
            f"Previous balance as at {_ordinal(previous_close)} "
            f"{calendar.month_name[previous_close.month]} {previous_close.year}: "
            f"£{_pounds(opening_minor)}"
        )
    lines += [
        f"Balance brought forward from previous statement          {_pounds(opening_minor)}",
        *(
            f"{_ordinal(row.day)} {MONTHS[row.day.month - 1]} {row.payee}"
            + (f"   CR   {_pounds(-row.minor)}" if row.minor < 0 else f"   {_pounds(row.minor)}")
            for row in rows
        ),
        f"Your new balance:                                        {_pounds(owed)}",
    ]
    path = root / f"{ref}-{closing.isoformat()}.pdf"
    path.write_bytes(build_pdf(lines))
    if received is not None:
        payload = path.read_bytes()
        store.land_artefact(
            RawArtefact(
                source="pdf",
                account_ref=ref,
                fetched_at=datetime.combine(received, time(12), UTC),
                media_type=media_type_of(payload, path),
                digest=artefact_digest(payload),
                payload=payload,
                origin=path.name,
            )
        )
    if import_rows or received is None:
        import_file(store, path, account_id=ref)
    return owed


def feed(store: Store, ref: str, rows: list[Spend], *, digest: str) -> None:
    """The bank's feed holding these rows (spends are money out)."""
    reconcile_batch(
        store,
        [
            Transaction(
                account_id=ref,
                amount_minor=-row.minor,
                currency="GBP",
                value_date=row.day,
                booking_date=row.day,
                description=row.payee,
                source="truelayer-booked",
                source_id=f"{ref}-{row.day.isoformat()}-{row.minor}",
                tier=SourceTier.AUTHORITATIVE,
                content_key=content_key(
                    amount_minor=-row.minor, value_date=row.day, description=row.payee
                ),
            )
            for row in rows
        ],
        digest=digest,
    )
