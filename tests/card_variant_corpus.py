"""Small invented cards, each a candidate for how a real card differs from the nine-statement one.

`card_chain_corpus` folds nine of nine, and a real card with the same masked
shape (every period over by one charge dated its first day) folded nothing.
The real card's masked report adds a fact the nine-statement corpus cannot
produce: in the periods that differ, ONE row dated the period's closing day has
the charge's amount, beside the one feed row dated the period's first day. Each
variant here builds a shape that can produce it (or a near miss), with the
answer decided before any run:

    closings   2026-01-10, 02-10, 03-10, 04-10 (four statements held)
    charge     6.95 on every statement
    purchases  one ordinary purchase per statement, in both sources

The answers, per variant (the verdict `same_money_fold` records at every
closing, and whether any feed row is folded):

    ITEMISED         parts on the closing day, one feed row at closing + 1.
                     Folds 4 of 4; every closing FOLDED.
    FEED_TWICE       the statement prints the charge ONCE (a single 6.95 row on
                     the closing day) and the feed posts it TWICE, on the
                     closing day and the day after. The statement row pairs
                     with the first, the second stays feed-only. Folds
                     nothing: there is no statement-only row to be its other
                     half. Every closing NO_MATCH, with no statement-only row.
                     This is the shape the real card's masked report matches:
                     one feed row dated each period's first day, and one row
                     dated its closing day, of the same size.
    ITEMISED_TWICE   parts on the closing day, and the feed posts the charge
                     twice on the day after. The rule takes one and its proof
                     refuses it, because the other stays counted in the same
                     period. Every closing REFUSED, naming the periods that
                     would still differ.
    SAME_AMOUNT_BOTH the itemised charge, plus an ordinary 6.95 purchase on
                     each closing day that both sources print. Predicted to
                     fold 4 of 4; MEASURED: folds nothing, every closing
                     NO_BAND_ROWS. The import's identity layer merges the
                     feed's charge row (closing + 1) into the statement's
                     same-size purchase row, so a statement lists the row the
                     rule would have taken and the feed's own purchase row is
                     left as the duplicate.
    TWIN_LATE        the itemised charge, plus a 6.95 row the statement prints
                     on the closing day and the feed posts three days later
                     under another description. Predicted REFUSED; MEASURED:
                     folds nothing, every closing NO_BAND_ROWS, for the same
                     merge as above; the late twin is the only feed-only row
                     and it lies outside the band.

Amounts are unique except where a variant needs the charge's amount twice, and
none is a recognisable real figure.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from pathlib import Path

from card_chain_corpus import CARD, feed_row, land, statement_day, text_day
from obdi.ingest import import_file
from obdi.store import Store
from obdi.synthetic_pdf import build_pdf

CLOSINGS = [date(2026, 1, 10), date(2026, 2, 10), date(2026, 3, 10), date(2026, 4, 10)]
CHARGE = 695
OPENING_OWED = 10000


class Variant(StrEnum):
    ITEMISED = "itemised"
    SAME_AMOUNT_BOTH = "same-amount-both"
    FEED_TWICE = "feed-twice"
    ITEMISED_TWICE = "itemised-twice"
    TWIN_LATE = "twin-late"


@dataclass(frozen=True)
class Row:
    day: date
    description: str
    minor: int
    in_statement: bool = True
    in_feed: bool = True


def _parts(position: int) -> list[int]:
    first, second = 100 + 13 * position, 200 + 7 * position
    return [first, second, CHARGE - first - second]


def _plan(variant: Variant, position: int) -> list[Row]:
    """The charge as each source carries it, and any row beside it."""
    closing = CLOSINGS[position]
    parts = [
        Row(closing, f"Plan Part {position}-{index}", minor, in_feed=False)
        for index, minor in enumerate(_parts(position))
    ]
    day_after = closing + timedelta(days=1)
    if variant is Variant.ITEMISED:
        return [*parts, Row(day_after, f"Plan Charge {position}", CHARGE, in_statement=False)]
    if variant is Variant.SAME_AMOUNT_BOTH:
        return [
            *parts,
            Row(day_after, f"Plan Charge {position}", CHARGE, in_statement=False),
            Row(closing, f"Same Size Purchase {position}", CHARGE),
        ]
    if variant is Variant.FEED_TWICE:
        return [
            Row(closing, f"Plan Charge {position}", CHARGE),
            Row(day_after, f"Plan Charge Again {position}", CHARGE, in_statement=False),
        ]
    if variant is Variant.ITEMISED_TWICE:
        return [
            *parts,
            Row(day_after, f"Plan Charge {position}", CHARGE, in_statement=False),
            Row(day_after, f"Plan Charge Again {position}", CHARGE, in_statement=False),
        ]
    return [
        *parts,
        Row(day_after, f"Plan Charge {position}", CHARGE, in_statement=False),
        Row(closing, f"Late Twin Printed {position}", CHARGE, in_feed=False),
        Row(
            closing + timedelta(days=3), f"Late Twin Posted {position}", CHARGE, in_statement=False
        ),
    ]


def _rows(variant: Variant) -> list[tuple[int, Row]]:
    found: list[tuple[int, Row]] = []
    for position, closing in enumerate(CLOSINGS):
        ordinary = Row(closing - timedelta(days=12), f"Ordinary {position}", 1200 + 91 * position)
        found.append((position, ordinary))
        found.extend((position, row) for row in _plan(variant, position))
    return found


@dataclass
class MiniCard:
    store: Store
    variant: Variant
    owed: int = OPENING_OWED

    def hold(self, root: Path, position: int) -> None:
        rows = [row for which, row in _rows(self.variant) if which == position and row.in_statement]
        closing = self.owed + sum(row.minor for row in rows)
        lines = [
            "Santander UK plc. Registered Office: 2 Triton Square",
            f"Statement Date: {statement_day(CLOSINGS[position])}      Page No: 1 / 1",
            "Account credit limit:            3,000.00",
            f"Balance brought forward from previous statement          {self.owed / 100:,.2f}",
            *(
                f"{text_day(row.day)} {row.description}   {row.minor / 100:,.2f}"
                for row in rows
            ),
            f"Your new balance:                                        {closing / 100:,.2f}",
        ]
        path = root / f"mini-{position}.pdf"
        path.write_bytes(build_pdf(lines))
        import_file(self.store, path, account_id=CARD)
        self.owed = closing


def build_mini_card(store: Store, root: Path, variant: Variant) -> MiniCard:
    """The four statements in order, then the feed's rows."""
    card = MiniCard(store, variant)
    for position in range(len(CLOSINGS)):
        card.hold(root, position)
    land(
        store,
        *(
            feed_row(row.minor, row.day, row.description)
            for _, row in _rows(variant)
            if row.in_feed
        ),
    )
    return card
