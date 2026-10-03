"""Small invented cards, each a way the real card's shape can vary, with the answer decided first.

`card_chain_corpus` is the real card's shape over nine statements. These are four
statements, closing 2026-01-10, 02-10, 03-10, and 04-10 (periods 1 to 4, the
first from its own first row), each with one ordinary purchase both sources carry
and, from the second, a recurring charge the statement lists on its closing day
(three rows: the charge, a fee, and the fee's reversal) and the feed carries
once, dated the first day of the same period. The feed also carries the charge
of a fifth statement, dated after the last closing, because a feed with nothing
after the last closing leaves that statement's rows outside the span the two
sources are compared over. The answers, per variant (the feed rows the rule
folds, and the periods that agree afterwards):

    IN_PERIOD        a different charge each month. Periods 2, 3, and 4 each
                     differ by the feed's charge row, and all three fold. Every
                     period agrees.
    AFTER_CLOSING    the feed dates each charge the day AFTER its closing, the
                     premise two earlier versions of the rule were built on and
                     no real card has shown. The statement itemises it as three
                     rows that never equal it singly. That row is the next
                     period's first day, where the statement lists a different
                     charge, so nothing folds and periods 2 to 4 differ.
    EQUAL_CHARGES    one fixed charge every month. PREDICTED to fold 3 of 3;
                     MEASURED: folds nothing, and only period 2 differs. The
                     identity layer merges each statement's charge row with the
                     feed row dated the day after its closing (the NEXT month's,
                     of the same amount), so the feed's first row of the run is
                     left over with no statement-only row to be the same money
                     as, and every later period agrees.
    WITH_PURCHASE    IN_PERIOD plus a feed-only purchase in period 3. That
                     period differs by the charge AND the purchase, which no
                     subset of the statement's rows sums to, so it folds nothing
                     and keeps differing (the charge is not folded alone: that
                     would leave a difference nobody has explained). Periods 2
                     and 4 fold.
    FEED_TWICE       IN_PERIOD, and the feed posts period 3's charge twice (the
                     first day, and three days later) where the statement lists
                     it once. The difference is two charges and no subset of the
                     statement's rows sums to that, so period 3 folds nothing
                     and differs; periods 2 and 4 fold.
    LISTED_TWICE     as FEED_TWICE, but period 3's statement lists the charge
                     twice too. The difference is two charges, the statement's
                     two rows sum to it, and both feed rows fold: four rows in
                     all, and every period agrees.

The next four are about EXCUSES, which the cross-source page gives a leftover so
that it is not reported there (a confirmed transfer leg, or a row an equal row
under a sibling account accounts for). The statement side of the rule may use an
excused row; the feed side may not. Each disturbs period 2 only:

    EXCUSED_CHARGE   the statement row that IS the charge is a confirmed transfer
                     leg. The other statement rows (a fee and its reversal) never
                     sum to it. The feed charge row folds, period 2 agrees, and
                     the page says which row was used and why it is excused.
    EXCUSED_AND_PLAIN  the statement lists the charge twice and the feed once; one
                     of the two statement rows is excused. The feed row folds
                     once, and the rows used are the plain one, so the sentence
                     names a date and no excuse.
    EXCUSED_FEED     the feed's charge row is the confirmed transfer leg. It
                     equals the difference and the statement itemises it, but the
                     cross-source page has explained it as something else, so it
                     is not folded and period 2 keeps differing.
    SIBLING_EXCUSED_CHARGE  as EXCUSED_CHARGE, but the excuse is a sibling
                     account's row of the same amount, filed by the feed on the
                     closing day. The same answer, and it is only seen when the
                     sibling scope names that account (`SIBLING_SCOPE`).

Amounts are unique except where a variant needs the charge's amount twice, and
none is a recognisable real figure.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from pathlib import Path

from card_chain_corpus import (
    CARD,
    FEED_SOURCE,
    feed_row,
    land,
    pair_with_savings,
    printed,
    statement_day,
    text_day,
)
from obdi.ingest import import_file
from obdi.store import Store
from obdi.synthetic_pdf import build_pdf

CLOSINGS = [date(2026, 1, 10), date(2026, 2, 10), date(2026, 3, 10), date(2026, 4, 10)]
CHARGES = [0, 695, 731, 784]
EQUAL_CHARGE = 695
FEE = 233
OPENING_OWED = 10000
#: The period the single-period variants disturb.
DISTURBED = 2
OTHER_CARD = "other-card"
#: The sibling scope under which the feed's row for `OTHER_CARD` can excuse a
#: statement row.
SIBLING_SCOPE = {FEED_SOURCE: [CARD, OTHER_CARD]}


class Variant(StrEnum):
    IN_PERIOD = "in-period"
    AFTER_CLOSING = "after-closing"
    EQUAL_CHARGES = "equal-charges"
    WITH_PURCHASE = "with-purchase"
    FEED_TWICE = "feed-twice"
    LISTED_TWICE = "listed-twice"
    EXCUSED_CHARGE = "excused-charge"
    EXCUSED_AND_PLAIN = "excused-and-plain"
    EXCUSED_FEED = "excused-feed"
    SIBLING_EXCUSED_CHARGE = "sibling-excused-charge"


@dataclass(frozen=True)
class Row:
    day: date
    description: str
    minor: int
    in_statement: bool = True
    in_feed: bool = True


def first_day_of(position: int) -> date:
    return CLOSINGS[position - 1] + timedelta(days=1)


def _charge(variant: Variant, position: int) -> int:
    return EQUAL_CHARGE if variant is Variant.EQUAL_CHARGES else CHARGES[position]


def _plan(variant: Variant, position: int) -> list[Row]:
    """The charge as each source carries it, and any row beside it."""
    closing = CLOSINGS[position]
    if variant is Variant.AFTER_CLOSING:
        charge = 695 + 37 * position
        first, second = 100 + 13 * position, 200 + 7 * position
        return [
            Row(closing, f"Plan Part {position}-first", first, in_feed=False),
            Row(closing, f"Plan Part {position}-second", second, in_feed=False),
            Row(closing, f"Plan Part {position}-rest", charge - first - second, in_feed=False),
            Row(
                closing + timedelta(days=1),
                f"Plan Charge {position}",
                charge,
                in_statement=False,
            ),
        ]
    if position == 0:
        return []
    charge = _charge(variant, position)
    parts = [
        Row(closing, f"Plan Part {position}-charge", charge, in_feed=False),
        Row(closing, f"Plan Part {position}-fee", FEE + position, in_feed=False),
        Row(closing, f"Plan Part {position}-reversal", -(FEE + position), in_feed=False),
    ]
    feed = Row(first_day_of(position), f"Plan Charge {position}", charge, in_statement=False)
    if variant is Variant.EXCUSED_AND_PLAIN and position == DISTURBED:
        return [
            Row(closing, f"Plan Part {position}-first", charge, in_feed=False),
            Row(closing, f"Plan Part {position}-second", charge, in_feed=False),
            feed,
        ]
    if variant is Variant.LISTED_TWICE and position == DISTURBED:
        return [
            Row(closing, f"Plan Part {position}-first", charge, in_feed=False),
            Row(closing, f"Plan Part {position}-second", charge, in_feed=False),
            feed,
            Row(
                first_day_of(position) + timedelta(days=3),
                f"Plan Charge Again {position}",
                charge,
                in_statement=False,
            ),
        ]
    found = [*parts, feed]
    if variant is Variant.WITH_PURCHASE and position == DISTURBED:
        found.append(
            Row(first_day_of(position) + timedelta(days=14), "Genuine Purchase", 1234, False)
        )
    if variant is Variant.FEED_TWICE and position == DISTURBED:
        found.append(
            Row(
                first_day_of(position) + timedelta(days=3),
                f"Plan Charge Again {position}",
                charge,
                in_statement=False,
            )
        )
    return found


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
                f"{text_day(row.day)} {row.description}   {printed(row.minor)}"
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
    rows = [
        feed_row(row.minor, row.day, row.description)
        for _, row in _rows(variant)
        if row.in_feed
    ]
    if variant is not Variant.AFTER_CLOSING:
        # The charge of a fifth statement that is not held: a feed with nothing
        # after the last closing leaves that statement's rows outside the span
        # the two sources are compared over.
        rows.append(
            feed_row(
                _charge(variant, len(CLOSINGS) - 1) + 33,
                CLOSINGS[-1] + timedelta(days=1),
                "Plan Charge Next",
            )
        )
    land(store, *rows)
    if variant is Variant.EXCUSED_CHARGE:
        pair_with_savings(store, f"Plan Part {DISTURBED}-charge")
    if variant is Variant.EXCUSED_AND_PLAIN:
        pair_with_savings(store, f"Plan Part {DISTURBED}-first")
    if variant is Variant.EXCUSED_FEED:
        pair_with_savings(store, f"Plan Charge {DISTURBED}")
    if variant is Variant.SIBLING_EXCUSED_CHARGE:
        land(
            store,
            feed_row(
                _charge(variant, DISTURBED),
                CLOSINGS[DISTURBED],
                "Sibling Charge",
                account=OTHER_CARD,
            ),
            digest="sibling",
        )
    return card
