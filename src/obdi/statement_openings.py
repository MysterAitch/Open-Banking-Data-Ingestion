"""Where a statement's opening balance falls, and what each stretch between known balances says.

A statement prints the balance its first transaction starts from. The owner decided it is a known
balance of its own: "if April is missing the May statement won't have an error for every
subsequent transaction giving an invalid running balance because the opening balance will have
planted a known starting position for that statement to anchor from".

WHERE IT IS PLACED (`place_opening`, the one statement of the rule). A known balance is a figure
for the END of a day, and an opening balance is the balance BEFORE the statement's first
transaction, so it is the figure for the end of the day before the statement's first covered day:

  - the statement prints a start day (a period, or the day beside its opening balance, which the
    parsers keep as the day after it): the day before that start;
  - it prints neither: the day before its first LISTED ROW. That is a fact and not a guess. A
    statement lists every transaction in its period, so no transaction lies between its start,
    wherever that is, and its first row, and the balance at the end of the day before the first
    row IS the opening balance.

THE ONE PREMISE is that a statement is complete over its own period. Where its first listed row is
dated before the start it prints, the premise is already contradicted by the document and the
row's day wins (`Placement.contradicted`). Where another source holds a row for the account in the
days before the placement that no statement lists, the premise is contradicted by the store, and
`statement_span.RowEvidence.unlisted_between` counts them; this module trusts neither silently.

A start day printed AT a layout's start (Halifax's account statement prints "Balance on" its first
day) is settled by arithmetic and not by the label: a statement is trusted only when its opening
balance plus every row it lists reaches its closing balance (`StatementReading.reconciles`), so a
row dated on the start day is inside the sum and the opening is the figure before it, the end of
the day before the start. What no fixture can show is a real statement with a row on its start
day.

WHAT A STRETCH SAYS (`stretches`). Each stretch between two consecutive known balances is tested on
its own: the later one is reproduced when the earlier one plus the rows between them gives it. The
account's reading already holds, for each balance, how far it is from the rows counted from the one
opening (`AnchorReading.difference_minor`), so a stretch is reproduced exactly when that distance
did not change across it. One missing stretch of rows therefore leaves ONE unproven stretch, and
the balances after it are tested from their own starting balance.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from itertools import pairwise

from .balance_anchors import (
    ASSUMED_NIL,
    STATEMENT_OPENING,
    Anchor,
    AnchorReading,
    EffectiveOpening,
)
from .ingest.family_anchors import OPENED
from .ingest.statement_terms import StatementPeriod

#: The layouts that print the day their opening balance is OF, beside the figure. The parsers keep
#: it as the day after (the period's start), so the placement is the same day either way and this
#: says only which words describe it.
DATES_ITS_OPENING = frozenset({"santander-cc-pdf", "nationwide-statement-pdf"})

_NIL_BASES = (OPENED, ASSUMED_NIL)


class PlacedBy(StrEnum):
    #: The statement prints the day its opening balance is of.
    DATED_BY_STATEMENT = "dated-by-statement"
    #: The statement prints the day its period starts, and the balance is that day's start.
    DAY_BEFORE_START = "day-before-start"
    #: The statement prints neither: the day before its first listed row.
    DAY_BEFORE_FIRST_ROW = "day-before-first-row"


@dataclass(frozen=True)
class Placement:
    """One statement's opening balance, the day it is a figure for, and how that day is known."""

    account: str
    #: The statement, by its closing day and its parser's source name.
    closing: date
    source: str
    day: date
    how: PlacedBy
    balance_minor: int
    #: The statement lists a row dated before the start it prints, so the document disagrees with
    #: itself and the day is the one before the row.
    contradicted: bool = False


def place_opening(item: StatementPeriod) -> Placement | None:
    """Where `item`'s opening balance falls; None where it states none or nothing can place it.

    A statement that lists no row and prints no start cannot be placed: the figure equals its
    closing and holds for the whole period, so it adds nothing a closing does not say.
    """
    if item.opening_minor is None:
        return None
    contradicted = False
    if item.opens is not None and (item.first_row is None or item.first_row >= item.opens):
        day = item.opens - timedelta(days=1)
        how = (
            PlacedBy.DATED_BY_STATEMENT
            if item.source in DATES_ITS_OPENING
            else PlacedBy.DAY_BEFORE_START
        )
    elif item.first_row is not None:
        day = item.first_row - timedelta(days=1)
        how = PlacedBy.DAY_BEFORE_FIRST_ROW
        contradicted = item.opens is not None
    else:
        return None
    return Placement(
        item.account_ref, item.closing, item.source, day, how, item.opening_minor, contradicted
    )


def opening_anchor(placement: Placement) -> Anchor:
    """The placement as a known balance of the basis that tells it from a closing."""
    return Anchor(
        placement.day,
        placement.balance_minor,
        STATEMENT_OPENING,
        stated_by=placement.source,
    )


@dataclass(frozen=True)
class Stretch:
    """The days from one known balance to the next, and whether the rows between reproduce it."""

    start: date
    end: date
    reproduced: bool
    #: Two sources state different figures for `end`, which is not a fault in the rows.
    conflict: bool = False


def _distance(reading: AnchorReading) -> int:
    return reading.difference_minor or 0


def stretches(opening: EffectiveOpening) -> list[Stretch]:
    """Each stretch between consecutive days that state a known balance, earliest first.

    Empty where the reading derived no opening (the rows are not all in pounds): nothing is
    judged, and a distance of nothing would read as every stretch reproduced.
    """
    if opening.opening_minor is None:
        return []
    by_day: dict[date, list[AnchorReading]] = defaultdict(list)
    for reading in opening.readings:
        by_day[reading.anchor.day].append(reading)
    found = []
    for before, day in pairwise(sorted(by_day)):
        earlier = {_distance(r) for r in by_day[before]}
        stated = {
            r.anchor.balance_minor
            for r in by_day[day]
            if r.anchor.at is None and r.anchor.basis not in _NIL_BASES
        }
        conflict = len(stated) > 1
        reproduced = not conflict and all(_distance(r) in earlier for r in by_day[day])
        found.append(Stretch(before, day, reproduced, conflict))
    return found


@dataclass(frozen=True)
class Run:
    """Consecutive stretches that were reproduced, or consecutive ones that were not."""

    start: date
    end: date
    reproduced: bool


def runs(found: Iterable[Stretch], first: date | None = None) -> list[Run]:
    """The stretches merged into runs, clipped to start no earlier than `first`.

    A run that ends on or before `first` is dropped; the nil opening of an account (a premise,
    never a known balance) is thereby left out of what is said.
    """
    merged: list[Run] = []
    for stretch in found:
        if first is not None and stretch.end <= first:
            continue
        start = stretch.start if first is None else max(stretch.start, first)
        if merged and merged[-1].reproduced == stretch.reproduced:
            merged[-1] = Run(merged[-1].start, stretch.end, stretch.reproduced)
        else:
            merged.append(Run(start, stretch.end, stretch.reproduced))
    return merged


def days_in(spans: Iterable[tuple[date, date]]) -> set[date]:
    """The days covered by stretches `(start, end]`: a known balance is for a day's end."""
    covered: set[date] = set()
    for start, end in spans:
        covered.update(start + timedelta(days=n) for n in range(1, (end - start).days + 1))
    return covered


def first_known_day(opening: EffectiveOpening) -> date | None:
    """The day of the earliest known balance that is not a nil premise."""
    return min(
        (r.anchor.day for r in opening.readings if r.anchor.basis not in _NIL_BASES),
        default=None,
    )


def spans_text(found: Sequence[tuple[date, date]]) -> str:
    """The spans as a list in words, with the serial comma: "from A to B and from C to D"."""
    parts = [f"from {a.isoformat()} to {b.isoformat()}" for a, b in found]
    if len(parts) <= 1:
        return "".join(parts)
    if len(parts) == 2:
        return " and ".join(parts)
    return ", ".join(parts[:-1]) + ", and " + parts[-1]
