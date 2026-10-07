"""A card held over nine statements with one month missing, built from invented data.

The shape is the one a real card's masked report showed the same-money fold, in
dates: a recurring plan charge that the statement lists on its CLOSING day and
the feed carries once, dated the FIRST day of the SAME period, a month before
the statement's rows. The store counts both, so every period after the first is
over by exactly that feed row. The feed row dated the day AFTER a closing is the
NEXT statement's charge, which is why a search around the closing never found
anything to match. Every answer is decided here, before any run:

    closings   2025-10-10, 11-12, 12-10, 2026-01-12, 02-11, [03-11 NOT held],
               04-10, 05-12, 06-10, 07-10
    charge     a different amount every month (`CHARGES`), so a row can only
               match its own period; `equal_charges` makes them all one amount
    statement  prints three rows on the closing day: the charge, a fee, and the
               fee's reversal (so the three sum to the charge); the first
               period prints two, and the first closing's period holds more
    feed       one charge row per held statement, dated its period's first day;
               one dated 2026-03-12 for the April statement (the second month of
               the gap period); one dated 2026-02-12 for the March statement,
               which is NOT held; one dated 2026-07-11 for an August statement
               that is not held either

    SAME MONEY (folded): the feed row dated each period's first day, for every
    held statement after the first (eight rows), including 2026-03-12 for the
    April statement, whose period spans the missing one.
    STAYS COUNTED: the 2026-02-12 charge (the March statement is not held, so
    nothing but the feed records it), the 2026-07-11 charge (until an August
    statement is held), and the 30 ordinary feed rows of the missing month.
    The first period folds nothing: it agrees as it stands. After the folds
    every between-closings period agrees.

Two awkward periods are built on purpose. The second period holds a statement
row the feed never saw and an EXCUSED one (a confirmed transfer leg), so its
statement-only and feed-only rows sum to different figures while the difference
still equals the feed row and one statement row. The last period prints two rows
(the charge and one other) where the rest print three.

The real card's second period differed from that: the excused row was the CHARGE
itself, so the rows the rule used (dated 2025-10-30 and twice on the closing
day) never summed to the difference. `build_card(transfer_row="Plan Part
1-charge")` is that shape. KNOWN ANSWER: the feed row folds and all eight
periods agree, as with the default; on the rule before excused statement rows
were usable, that closing alone folded nothing and its period kept differing by
the charge.

With `equal_charges` the answer PREDICTED was the same eight folds; MEASURED:
nothing folds and every period already agrees. The identity layer merges each
feed row with the statement charge row of the same amount a day away, so no
period is left over.

The first period's feed row is PENDING, which the store does not count. The real
card's first period agreed with a feed-only row beside it, and no counted row can
do that (the parser refuses a statement whose rows do not carry its total), so
that stands in for an unknown cause; it is why this corpus cannot say what the
real card's first-period row is.

Amounts are unique across the corpus (the cross-source pairing matches on
amount alone within two days), and none is a recognisable real figure.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from obdi.core.models import SourceTier, Transaction, TransactionStatus
from obdi.ingest.identity import content_key
from obdi.ingest.pipeline import import_file, reconcile_batch
from obdi.ingest.store import Store
from obdi.ingest.synthetic_pdf import build_pdf

CARD = "card"
SAVINGS = "savings"
FEED_SOURCE = "truelayer"

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
#: The position of the statement whose period spans the missing one.
GAP_POSITION = 5
FIRST_ROW_DAY = date(2025, 9, 19)
CHARGES = [695, 731, 784, 819, 865, 902, 958, 997, 1043]
#: The charges of the two statements that are not held, in feed rows only.
MARCH_CHARGE = 1111
AUGUST_CHARGE = 1089
MARCH_CHARGE_DAY = date(2026, 2, 12)
APRIL_CHARGE_DAY = date(2026, 3, 12)
AUGUST_CHARGE_DAY = date(2026, 7, 11)
#: Statement rows the feed never saw: (statement position, day, amount).
STATEMENT_ONLY = [
    (0, date(2025, 9, 30), 1777),
    (0, date(2025, 9, 30), 1313),
    (1, date(2025, 10, 30), 2999),
]
#: A fee on the closing day and its reversal, so the two net to nothing.
FEES = [421, 433, 447, 461, 479, 491, 503, 517, 529]
#: The statement-1 row that is a transfer to another account, so excused.
TRANSFER_AMOUNT = 1550


def charge_of(position: int, *, equal: bool = False) -> int:
    return CHARGES[0] if equal else CHARGES[position]


def first_day_of(position: int) -> date:
    """The first day of the period a statement closes."""
    return FIRST_ROW_DAY if position == 0 else CLOSINGS[position - 1] + timedelta(days=1)


def printed(minor: int) -> str:
    """A figure as the statement prints it: owed plain, a credit with CR before it."""
    return f"{minor / 100:,.2f}" if minor >= 0 else f"CR {-minor / 100:,.2f}"


def charge_day_of(position: int) -> date:
    """The day the feed dates a statement's charge: its period's first day,
    except where that period spans the missing statement."""
    if position == 0:
        return FIRST_ROW_DAY + timedelta(days=1)
    return APRIL_CHARGE_DAY if position == GAP_POSITION else first_day_of(position)


def text_day(day: date) -> str:
    ordinals = {1: "st", 2: "nd", 3: "rd"}
    suffix = "th" if 10 <= day.day % 100 <= 20 else ordinals.get(day.day % 10, "th")
    return f"{day.day}{suffix} {day.strftime('%b')}"


def statement_day(day: date) -> str:
    return f"{text_day(day)} {day.year}"


@dataclass(frozen=True)
class Purchase:
    day: date
    description: str
    minor: int


def purchases() -> list[Purchase]:
    """The ordinary rows both sources carry, one per period and two in the first."""
    found = [Purchase(FIRST_ROW_DAY, "Opening Purchase", 1103)]
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
    first = date(2026, 2, 14)
    return [
        Purchase(first + timedelta(days=index), f"Gap Purchase {index}", 2100 + 37 * index)
        for index in range(30)
    ]


def rows_of(position: int, *, equal: bool = False) -> list[tuple[str, str, int]]:
    """One statement's printed rows: (day text, description, minor)."""
    closing = CLOSINGS[position]
    window_start = CLOSINGS[position - 1] if position else date(2025, 9, 1)
    rows: list[tuple[str, str, int]] = []
    for item in purchases():
        inside = item.description == "Inside Purchase"
        if (window_start < item.day <= closing and not inside) or (
            inside and position == GAP_POSITION
        ):
            rows.append((text_day(item.day), item.description, item.minor))
    for which, day, minor in STATEMENT_ONLY:
        if which == position:
            rows.append((text_day(day), f"Statement Only {which}-{minor}", minor))
    charge = charge_of(position, equal=equal)
    fee = FEES[position]
    closing_text = text_day(closing)
    rows.append((closing_text, f"Plan Part {position}-charge", charge))
    if position == 0:
        rows.append((closing_text, f"Plan Part {position}-fee", fee))
    elif position == 1:
        rows.append((closing_text, f"Plan Part {position}-fee", fee))
        rows.append((closing_text, "Transfer Leg", TRANSFER_AMOUNT))
    elif position == len(CLOSINGS) - 1:
        rows.append((closing_text, f"Plan Part {position}-fee", fee))
    else:
        rows.append((closing_text, f"Plan Part {position}-fee", fee))
        rows.append((closing_text, f"Plan Part {position}-reversal", -fee))
    return rows


@dataclass
class Card:
    store: Store
    root: Path
    equal: bool = False
    held: list[int] = field(default_factory=list)
    owed: int = 10000

    def hold(self, position: int, *, opening: int | None = None) -> int:
        """Hold one statement; returns the balance owed it closes on."""
        rows = rows_of(position, equal=self.equal)
        start = opening if opening is not None else self.owed
        closing = start + sum(minor for _, _, minor in rows)
        lines = [
            "Santander UK plc. Registered Office: 2 Triton Square",
            f"Statement Date: {statement_day(CLOSINGS[position])}      Page No: 1 / 1",
            "Account credit limit:            3,000.00",
            f"Balance brought forward from previous statement          {start / 100:,.2f}",
            *(f"{when} {description}   {printed(minor)}" for when, description, minor in rows),
            f"Your new balance:                                        {closing / 100:,.2f}",
        ]
        path = self.root / f"card-{position}.pdf"
        path.write_bytes(build_pdf(lines))
        import_file(self.store, path, account_id=CARD)
        self.owed = closing
        self.held.append(position)
        return closing


def feed_row(
    minor: int,
    day: date,
    description: str,
    *,
    account: str = CARD,
    status: TransactionStatus = TransactionStatus.BOOKED,
) -> Transaction:
    """A feed row; `minor` is what is owed, so the stored amount is its negative."""
    return Transaction(
        account_id=account,
        amount_minor=-minor,
        currency="GBP",
        value_date=day,
        booking_date=day,
        description=description,
        source=FEED_SOURCE,
        source_id=f"{FEED_SOURCE}-{description}-{day}",
        tier=SourceTier.AUTHORITATIVE,
        content_key=content_key(amount_minor=-minor, value_date=day, description=description),
        status=status,
    )


def land(store: Store, *rows: Transaction, digest: str = "feed") -> None:
    reconcile_batch(store, list(rows), digest=digest)


def pair_with_savings(store: Store, description: str) -> None:
    """Confirm the card's row of this description as a transfer to savings, by
    landing the opposite row there the same day. A confirmed leg is never folded,
    and on the statement's side it is an excused leftover."""
    [leg] = [t for t in store.transactions_for_account(CARD) if t.description == description]
    land(
        store,
        feed_row(leg.amount_minor, leg.value_date, "Savings Side", account=SAVINGS),
        digest="savings",
    )
    [other] = store.transactions_for_account(SAVINGS)
    store.replace_transfer_pairs([(leg.entity_id, other.entity_id)])
    store.connection.commit()


def confirm_transfer(store: Store) -> None:
    """Make the second statement's 'Transfer Leg' row an excused leftover."""
    pair_with_savings(store, "Transfer Leg")


def charge_feed_rows(
    *,
    equal: bool = False,
    feed_charges: Mapping[int, int] | None = None,
    charge_days: Mapping[int, date] | None = None,
    missing: int,
) -> list[Transaction]:
    """The feed's charge rows, each dated its period's first day: the first
    period's (pending, so not counted), one per later held statement, the
    April statement's on 2026-03-12, and the two statements not held (the
    March one for `missing`)."""
    feed_charges = feed_charges or {}
    charge_days = charge_days or {}
    rows = [
        feed_row(
            feed_charges.get(position, charge_of(position, equal=equal)),
            charge_days.get(position, charge_day_of(position)),
            f"Plan Charge {position}",
            status=TransactionStatus.PENDING if position == 0 else TransactionStatus.BOOKED,
        )
        for position in range(len(CLOSINGS))
    ]
    rows.append(feed_row(missing, MARCH_CHARGE_DAY, "Plan Charge Missing"))
    rows.append(
        feed_row(AUGUST_CHARGE if not equal else CHARGES[0], AUGUST_CHARGE_DAY, "Plan Charge After")
    )
    return rows


def build_card(
    store: Store,
    root: Path,
    *,
    skip: frozenset[int] = frozenset(),
    extra_feed: Sequence[Transaction] = (),
    feed_charges: Mapping[int, int] | None = None,
    charge_days: Mapping[int, date] | None = None,
    equal_charges: bool = False,
    missing_charge: int | None = None,
    transfer: bool = True,
    transfer_row: str = "Transfer Leg",
) -> Card:
    """The whole corpus: statements in order (the missing month's statement is
    simply never held), then the feed. `skip` leaves held statements out, for
    scenarios that need fewer of them; `extra_feed` adds rows only the feed holds;
    `feed_charges` gives a statement's feed row another amount and `charge_days`
    another date; `equal_charges`
    makes every charge one amount; `missing_charge` sets the amount of the
    statement that is not held (the default is its own, or the one amount);
    `transfer` confirms the second statement's transfer leg, or, with
    `transfer_row` naming another of that statement's rows (the real card's
    shape: the CHARGE is the excused one), that row instead."""
    card = Card(store, root, equal=equal_charges)
    if missing_charge is not None:
        gap_charge = missing_charge
    else:
        gap_charge = CHARGES[0] if equal_charges else MARCH_CHARGE
    for position in range(len(CLOSINGS)):
        if position in skip:
            continue
        if position == GAP_POSITION:
            gap = sum(item.minor for item in gap_rows())
            card.owed += gap + gap_charge
            card.hold(position, opening=card.owed)
        else:
            card.hold(position)
    feed: list[Transaction] = [
        feed_row(item.minor, item.day, item.description) for item in purchases()
    ]
    feed += [feed_row(item.minor, item.day, item.description) for item in gap_rows()]
    feed += charge_feed_rows(
        equal=equal_charges, feed_charges=feed_charges, charge_days=charge_days, missing=gap_charge
    )
    feed += extra_feed
    land(store, *feed)
    if transfer and 1 not in skip:
        pair_with_savings(store, transfer_row)
    return card
