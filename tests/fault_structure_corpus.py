"""Invented accounts whose faults are decided before the walk is run.

One account of invented payments, opened at nil on 31 December 2025 and stated
at the end of EVERY day from 1 January to 31 August 2026 by one source. The
source states the true balance. The store's rows are the true rows with defects
planted in them, so each variant's structure has a KNOWN ANSWER recorded beside
it, decided before any reading was made:

  missing        one row of the true ledger is not held (25 February).
                 The difference steps once, by minus that row, and stays there:
                 one permanent step and one long level after it.
  late           one row is held three days later than the source dates it
                 (10 June to 13 June) and nothing places it. The difference
                 steps by minus the row on the 10th and back on the 13th:
                 one pair, gap 3, net nil.
  monthly twice  a charge on the 5th of every month is held twice. The
                 difference steps by plus the charge on each of eight 5ths:
                 one size class of eight, monthly, always on the 5th.
  all three      each of the above together: eleven steps, one pair, nine
                 permanent (the missing row and the eight charges).

Walks are built with `walk_family` over rows and anchors handed in, so no
database is written (`test_fixture_write_doors`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from obdi.balance_anchors import FamilyWalk, walk_family
from obdi.core.models import SourceTier, Transaction, TransactionStatus
from obdi.family_anchors import (
    CSV_SOURCE,
    OPENED,
    FamilyAnchor,
    FamilyAnchors,
    OpeningEvidence,
)
from obdi.identity import content_key

MAIN = "starling-personal"
OPENING_DAY = date(2025, 12, 31)
FIRST = date(2026, 1, 1)
LAST = date(2026, 8, 31)
#: One stated balance a day, from the first to the last.
STATED = (LAST - FIRST).days + 1

CHARGE = -999
MISSING_ROW = -4500
LATE_ROW = -2500
MISSING_ON = date(2026, 2, 25)
LATE_ON = date(2026, 6, 10)
LATE_BY = 3
#: The 5th of January to August.
CHARGE_DAYS = tuple(date(2026, month, 5) for month in range(1, 9))


@dataclass(frozen=True)
class Event:
    day: date
    minor: int
    name: str


def _row(day: date, minor: int, name: str) -> Transaction:
    return Transaction(
        account_id=MAIN,
        amount_minor=minor,
        value_date=day,
        booking_date=day,
        description=name,
        source="starling",
        source_id=name,
        content_key=content_key(amount_minor=minor, value_date=day, description=name),
        tier=SourceTier.AUTHORITATIVE,
        status=TransactionStatus.BOOKED,
    )


#: Descriptions all start with this, so a page can be searched for any of them.
NAME = "inv-"

#: A second source that states the true balance on Sundays.
SECOND_SOURCE = "truelayer"


def true_events(scale: int = 1) -> list[Event]:
    """The account's real movements: pay on the 25th, shopping every third day,
    the monthly charge, and the two rows the faulty variants then mishandle.

    `scale` multiplies the three sizes the defects act on, which changes every
    figure a defect shows and nothing about its structure.
    """
    events: list[Event] = []
    day = FIRST
    while day <= LAST:
        if day.day == 25:
            events.append(Event(day, 200_000, f"{NAME}pay-{day}"))
        if day.toordinal() % 3 == 0:
            events.append(Event(day, -(1_100 + day.toordinal() % 7 * 13), f"{NAME}shop-{day}"))
        day += timedelta(days=1)
    events += [Event(d, CHARGE * scale, f"{NAME}charge-{d}") for d in CHARGE_DAYS]
    events += [
        Event(MISSING_ON, MISSING_ROW * scale, f"{NAME}missing"),
        Event(LATE_ON, LATE_ROW * scale, f"{NAME}late"),
    ]
    return events


def walk(
    *,
    missing: bool = False,
    late: bool = False,
    monthly_twice: bool = False,
    scale: int = 1,
    second_source: bool = False,
) -> FamilyWalk:
    """The source's true balances walked against the store's rows with the chosen defects."""
    events = true_events(scale)
    anchors = []
    balance = 0
    for offset in range(STATED):
        day = FIRST + timedelta(days=offset)
        balance += sum(e.minor for e in events if e.day == day)
        anchors.append(FamilyAnchor(day, balance, CSV_SOURCE))
        if second_source and day.weekday() == 6:
            anchors.append(FamilyAnchor(day, balance, SECOND_SOURCE))
    rows = []
    for event in events:
        if missing and event.name == f"{NAME}missing":
            continue
        day = (
            event.day + timedelta(days=LATE_BY)
            if late and event.name == f"{NAME}late"
            else event.day
        )
        rows.append(_row(day, event.minor, event.name))
        if monthly_twice and event.name.startswith(f"{NAME}charge-"):
            rows.append(_row(day, event.minor, event.name + "-again"))
    evidence = OpeningEvidence(
        created=FIRST,
        opened=FamilyAnchor(OPENING_DAY, 0, OPENED),
        missing="",
        known_categories=frozenset(),
    )
    return walk_family(MAIN, FamilyAnchors(tuple(anchors), evidence=evidence), {MAIN: rows})
