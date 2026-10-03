"""A card held over nine statements with one month missing, built from invented data.

The shape is the one a real card showed the same-money fold: every statement
itemises a recurring plan charge on its closing day as several small rows, and
the feed carries it as ONE row dated the day after the closing, which by date
is the first day of the next period. Every answer is decided here, before any
run:

    closings   2025-10-10, 11-12, 12-10, 2026-01-12, 02-11, [03-11 NOT held],
               04-10, 05-12, 06-10, 07-10
    charge     6.95 on every statement, itemised as three rows (two on the
               last) that never equal 6.95 singly
    feed       one -6.95 row per held statement, dated closing + 1, and one
               more on 2026-03-12 for the statement that is NOT held

    SAME MONEY (folded): the nine feed rows dated closing + 1, one per held
    statement, the last of them dated after the final closing.
    STAYS COUNTED: the 2026-03-12 charge (its statement is not held, so
    nothing but the feed records it) and the 30 ordinary feed rows of that month.

The first period is awkward on purpose: it prints one row the feed never saw,
so its leftovers sum to more than the charge. The second prints one more of a
different amount. Neither changes the answer for any later period.

A first period that also holds a feed-only row yet agrees cannot be built: the
statement parser refuses a total its rows do not carry, so a charge printed
only in a total never reaches the store.

Amounts are unique across the corpus (the cross-source pairing matches on
amount alone within two days), and none is a recognisable real figure.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from obdi.identity import content_key
from obdi.ingest import import_file, reconcile_batch
from obdi.models import SourceTier, Transaction
from obdi.store import Store
from obdi.synthetic_pdf import build_pdf

CARD = "card"
FEED_SOURCE = "truelayer"

CHARGE = 695
CLOSINGS = [
    date(2025, 10, 10),
    date(2025, 11, 12),
    date(2025, 12, 10),
    date(2026, 1, 12),
    date(2026, 2, 11),
    date(2026, 4, 10),
    date(2026, 5, 12),
    date(2026, 6, 10),
    date(2026, 7, 10),
]
MISSING_CLOSING = date(2026, 3, 11)
MISSING_CHARGE_FEED_DAY = date(2026, 3, 12)
#: Statement rows the feed never saw: (statement position, day, amount).
STATEMENT_ONLY = [(0, date(2025, 9, 30), 1777), (1, date(2025, 10, 20), 2999)]


def text_day(day: date) -> str:
    ordinals = {1: "st", 2: "nd", 3: "rd"}
    suffix = "th" if 10 <= day.day % 100 <= 20 else ordinals.get(day.day % 10, "th")
    return f"{day.day}{suffix} {day.strftime('%b')}"


def statement_day(day: date) -> str:
    return f"{text_day(day)} {day.year}"


def charge_parts(position: int) -> list[int]:
    if position == len(CLOSINGS) - 1:
        return [400, 295]
    first, second = 100 + 13 * position, 200 + 7 * position
    return [first, second, CHARGE - first - second]


def feed_day_of(position: int) -> date:
    return CLOSINGS[position] + timedelta(days=1)


@dataclass(frozen=True)
class Purchase:
    day: date
    description: str
    minor: int
    in_feed: bool = True


def purchases() -> list[Purchase]:
    found = [Purchase(date(2025, 9, 19), "Opening Purchase", 1103)]
    for position, closing in enumerate(CLOSINGS):
        found.append(
            Purchase(
                closing - timedelta(days=12),
                f"Ordinary Purchase {position}",
                1200 + 91 * position,
            )
        )
    found.append(Purchase(date(2026, 3, 10), "Inside Purchase", 1717))
    return found


def gap_rows() -> list[Purchase]:
    """The ordinary rows of the missing month: in the feed only."""
    first = date(2026, 2, 13)
    return [
        Purchase(first + timedelta(days=index), f"Gap Purchase {index}", 2100 + 37 * index)
        for index in range(30)
    ]


def rows_of(position: int) -> list[tuple[str, str, int]]:
    """One statement's printed rows: (day text, description, minor)."""
    closing = CLOSINGS[position]
    window_start = CLOSINGS[position - 1] if position else date(2025, 9, 1)
    rows: list[tuple[str, str, int]] = []
    for item in purchases():
        inside = item.description == "Inside Purchase"
        if (window_start < item.day <= closing and not inside) or (inside and position == 5):
            rows.append((text_day(item.day), item.description, item.minor))
    for which, day, minor in STATEMENT_ONLY:
        if which == position:
            rows.append((text_day(day), f"Statement Only {which}", minor))
    for index, minor in enumerate(charge_parts(position)):
        rows.append((text_day(closing), f"Plan Part {position}-{index}", minor))
    return rows


@dataclass
class Card:
    store: Store
    root: Path
    held: list[int] = field(default_factory=list)

    def hold(self, position: int, *, opening: int | None = None) -> int:
        """Hold one statement; returns the balance owed it closes on."""
        rows = rows_of(position)
        start = opening if opening is not None else self.owed
        closing = start + sum(minor for _, _, minor in rows)
        lines = [
            "Santander UK plc. Registered Office: 2 Triton Square",
            f"Statement Date: {statement_day(CLOSINGS[position])}      Page No: 1 / 1",
            "Account credit limit:            3,000.00",
            f"Balance brought forward from previous statement          {start / 100:,.2f}",
            *(f"{when} {description}   {minor / 100:,.2f}" for when, description, minor in rows),
            f"Your new balance:                                        {closing / 100:,.2f}",
        ]
        path = self.root / f"card-{position}.pdf"
        path.write_bytes(build_pdf(lines))
        import_file(self.store, path, account_id=CARD)
        self.owed = closing
        self.held.append(position)
        return closing

    owed: int = 10000


def feed_row(minor: int, day: date, description: str) -> Transaction:
    return Transaction(
        account_id=CARD,
        amount_minor=-minor,
        currency="GBP",
        value_date=day,
        booking_date=day,
        description=description,
        source=FEED_SOURCE,
        source_id=f"{FEED_SOURCE}-{description}-{day}",
        tier=SourceTier.AUTHORITATIVE,
        content_key=content_key(amount_minor=-minor, value_date=day, description=description),
    )


def land(store: Store, *rows: Transaction, digest: str = "feed") -> None:
    reconcile_batch(store, list(rows), digest=digest)


def build_card(
    store: Store,
    root: Path,
    *,
    skip: frozenset[int] = frozenset(),
    extra_feed: Sequence[Transaction] = (),
    charge_offset: int = 1,
    feed_charges: Mapping[int, int] | None = None,
) -> Card:
    """The whole corpus: statements in order (the missing month's statement is
    simply never held), then the feed. `skip` leaves held statements out, for
    scenarios that need fewer of them; `extra_feed` adds rows only the feed holds;
    `charge_offset` is the days after each closing the feed dates its charge;
    `feed_charges` gives a statement's feed row another amount."""
    feed_charges = feed_charges or {}
    card = Card(store, root)
    for position in range(len(CLOSINGS)):
        if position in skip:
            continue
        if position == 5:
            gap = sum(item.minor for item in gap_rows())
            card.owed += gap + CHARGE
            card.hold(position, opening=card.owed)
        else:
            card.hold(position)
    feed: list[Transaction] = [
        feed_row(item.minor, item.day, item.description) for item in purchases()
    ]
    feed += [feed_row(item.minor, item.day, item.description) for item in gap_rows()]
    feed += [
        feed_row(
            feed_charges.get(position, CHARGE),
            CLOSINGS[position] + timedelta(days=charge_offset),
            f"Plan Charge {position}",
        )
        for position in range(len(CLOSINGS))
    ]
    feed.append(feed_row(CHARGE, MISSING_CHARGE_FEED_DAY, "Plan Charge Missing"))
    feed += extra_feed
    land(store, *feed)
    return card
