"""How far an account is IN AGREEMENT: the one place the rule is written.

The words are defined in `clearing`; this module derives the second of them. The owner's framing,
asked to make the three ideas explicit: "the transactions to this date match the known balance
(and/or are verified as being correct)". And a balance cannot see movements that net to nil, so
"verified as being correct" means the movement checks hold as well as the money.

THE RULE. An account is IN AGREEMENT THROUGH the day C of the latest known balance such that:

  1  every known balance dated on or before C is met: the rows reproduce it. The one that
     defines the opening is met by construction, and a nil opening (`balance_anchors.OPENED`,
     `ASSUMED_NIL`) is a premise rather than a known balance, so it is neither counted nor shown;
  2  at least one known balance on or before C has actually been TESTED, that is met by
     reproducing rather than by defining. An opening derived from one known balance absorbs every
     missing row before it, so a lone known balance verifies nothing (state `UNTESTED`);
  3  no two known balances disagree with each other on a day on or before C. Two sources stating
     different figures for the same day are a conflict between SOURCES, said in its own words
     and never attributed to the rows. A balance stated for a moment (the bank's own, judged at
     its instant) is not compared with one stated for a day's end, because the figures differ by
     what moved in between;
  4  the movement checks (`movement_completeness`) report no fault dated on or before C in the
     account. For a whole family a fault in any member counts.

The frontier is held back by the EARLIEST of: the first unmet known balance, the first conflict,
and the first movement fault. Only a hold with a known balance still to be reached is reported:
a movement fault dated after the latest known balance verifies nothing and refutes nothing about
the days before it.

DERIVED ON DEMAND from the readings `effective_opening` and the movement report already hold.
Nothing here walks an account: the ledger reuses the opening it built for its own page, and the
Overview reads a cached standing.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from .balance_anchors import ASSUMED_NIL, EffectiveOpening, FamilyReading, FamilyWalk
from .family_anchors import OPENED
from .masking import Structural

if TYPE_CHECKING:  # pragma: no cover - imported for the annotation alone
    from .movement_completeness import MovementCompleteness

DEFINES, MET, UNMET = "defines", "met", "unmet"

#: The states of an account's agreement, and so of the sentence said about it.
NONE = "none"
UNTESTED = "untested"
AGREES = "agrees"
HELD_UNMET = "unmet"
HELD_CONFLICT = "conflict"
HELD_MOVEMENT = "movement"

#: Which hold wins when two begin on one day: a disagreement between sources makes the rows' own
#: verdict at that day meaningless, so it is said first.
_HOLD_ORDER = (HELD_CONFLICT, HELD_UNMET, HELD_MOVEMENT)

_NIL_BASES = (OPENED, ASSUMED_NIL)


@dataclass(frozen=True)
class Known:
    """One known balance as the rule reads it. The figure is compared and never shown."""

    day: date
    source: str
    verdict: str
    figure: int
    #: Stated for a moment rather than a day's end, so not comparable with one that is.
    instant: bool = False


@dataclass(frozen=True)
class Fault:
    """A movement fault, reduced to the day it is dated and the sentence the check wrote."""

    day: date
    says: str


@dataclass(frozen=True)
class Conflict:
    day: Structural[date]
    sources: Structural[tuple[str, ...]]


@dataclass(frozen=True)
class HeldBack:
    """What stops the frontier moving on: an unmet known balance, a conflict, or a fault."""

    kind: Structural[str]
    day: Structural[date]
    sources: Structural[tuple[str, ...]]
    #: The movement check's own sentence for a movement fault; empty otherwise.
    says: Structural[str]


@dataclass(frozen=True)
class Agreement:
    state: Structural[str]
    known_from: Structural[date | None]
    known_to: Structural[date | None]
    known_count: Structural[int]
    tested_count: Structural[int]
    through: Structural[date | None]
    held: Structural[HeldBack | None]
    #: Whether the movement faults were read at all; False says the figure is from balances alone.
    movement_checked: Structural[bool]
    conflicts: Structural[tuple[Conflict, ...]]


@dataclass(frozen=True)
class Standing:
    """An account's agreement, and for a main account with Spaces the whole family's too."""

    own: Structural[Agreement]
    whole: Structural[Agreement | None]


def derive_agreement(
    known: Iterable[Known], faults: Iterable[Fault], *, movement_checked: bool = True
) -> Agreement:
    """The rule above, over known balances and movement faults. Pure."""
    balances = sorted(known, key=lambda k: (k.day, k.source, k.figure))
    if not balances:
        return Agreement(NONE, None, None, 0, 0, None, None, movement_checked, ())
    by_day: dict[date, list[Known]] = defaultdict(list)
    for balance in balances:
        by_day[balance.day].append(balance)
    days = sorted(by_day)

    conflicts: dict[date, tuple[str, ...]] = {}
    for day in days:
        stated = [k for k in by_day[day] if not k.instant]
        if len({k.figure for k in stated}) > 1:
            conflicts[day] = tuple(sorted({k.source for k in stated}))
    unmet = {
        day: tuple(sorted({k.source for k in by_day[day] if k.verdict == UNMET}))
        for day in days
        if day not in conflicts and any(k.verdict == UNMET for k in by_day[day])
    }
    ordered_faults = sorted(faults, key=lambda f: (f.day, f.says))
    first_fault = ordered_faults[0] if ordered_faults else None

    starts: dict[str, date] = {}
    if conflicts:
        starts[HELD_CONFLICT] = min(conflicts)
    if unmet:
        starts[HELD_UNMET] = min(unmet)
    if first_fault is not None:
        starts[HELD_MOVEMENT] = first_fault.day
    blocked_from = min(starts.values(), default=None)

    tested = [d for d in days if d not in conflicts and any(k.verdict == MET for k in by_day[d])]
    clear = [d for d in days if blocked_from is None or d < blocked_from]
    first_tested = min(tested, default=None)
    through = max(
        (d for d in clear if first_tested is not None and d >= first_tested), default=None
    )

    held: HeldBack | None = None
    state = AGREES if through is not None else UNTESTED
    if blocked_from is not None and blocked_from <= days[-1]:
        kind = next(k for k in _HOLD_ORDER if starts.get(k) == blocked_from)
        if kind == HELD_CONFLICT:
            held = HeldBack(kind, blocked_from, conflicts[blocked_from], "")
        elif kind == HELD_UNMET:
            held = HeldBack(kind, blocked_from, unmet[blocked_from], "")
        else:
            held = HeldBack(kind, blocked_from, (), first_fault.says if first_fault else "")
        state = kind
    return Agreement(
        state=state,
        known_from=days[0],
        known_to=days[-1],
        known_count=len(days),
        tested_count=len(tested),
        through=through,
        held=held,
        movement_checked=movement_checked,
        conflicts=tuple(Conflict(day, sources) for day, sources in sorted(conflicts.items())),
    )


def known_of_opening(opening: EffectiveOpening) -> list[Known]:
    """The account's own known balances, each with whether the rows reproduce it."""
    found = []
    for reading in opening.readings:
        anchor = reading.anchor
        if anchor.basis in _NIL_BASES:
            continue
        verdict = DEFINES if reading.agrees is None else MET if reading.agrees else UNMET
        found.append(
            Known(
                anchor.day,
                anchor.stating,
                verdict,
                anchor.balance_minor,
                instant=anchor.at is not None,
            )
        )
    return found


def known_of_walk(walk: FamilyWalk) -> list[Known]:
    """The whole family's known balances, from the walk of the family's rows."""
    found = []
    for reading in walk.readings:
        found.append(_known_of_reading(reading, instant=False))
    for reading in walk.bank_readings:
        found.append(_known_of_reading(reading, instant=True))
    return found


def _known_of_reading(reading: FamilyReading, *, instant: bool) -> Known:
    verdict = DEFINES if reading.agrees is None else MET if reading.agrees else UNMET
    return Known(
        reading.day,
        reading.sources[0] if reading.sources else "",
        verdict,
        reading.balance_minor,
        instant=instant,
    )


def faults_of(report: MovementCompleteness, accounts: Collection[str]) -> list[Fault]:
    """The movement faults that concern any of `accounts`, as dated sentences."""
    wanted = set(accounts)
    found = [Fault(f.day, f.says()) for f in report.row_faults if f.account in wanted]
    found += [
        Fault(f.day, f.says())
        for f in report.leg_faults
        if f.account in wanted or (f.partner_account is not None and f.partner_account in wanted)
    ]
    found += [
        Fault(f.day, f.says())
        for f in report.chain_faults
        if f.from_account in wanted or f.to_account in wanted
    ]
    return found


def standing_of(
    opening: EffectiveOpening,
    members: Sequence[str],
    movement: MovementCompleteness | None,
) -> Standing:
    """An account's standing from the opening already built for it.

    `members` is the account and its known Spaces, whose movement faults a whole-account reading
    counts; the account's own reading counts only its own.
    """
    checked = movement is not None
    own_faults = faults_of(movement, members[:1]) if movement is not None else []
    own = derive_agreement(known_of_opening(opening), own_faults, movement_checked=checked)
    whole: Agreement | None = None
    walk = opening.family
    if walk is not None and (walk.readings or walk.bank_readings):
        whole_faults = faults_of(movement, members) if movement is not None else []
        whole = derive_agreement(known_of_walk(walk), whole_faults, movement_checked=checked)
    return Standing(own, whole)


def _day(day: date | None) -> str:
    return day.isoformat() if day else ""


def held_sentence(agreement: Agreement) -> str:
    """What holds the frontier back, in one sentence of days and source names."""
    held = agreement.held
    if held is None:
        if agreement.state == UNTESTED and agreement.known_count:
            return (
                f"Only the known balance for {_day(agreement.known_from)} defines the opening, "
                "so nothing tests the rows yet."
            )
        return ""
    day = _day(held.day)
    if held.kind == HELD_CONFLICT:
        return (
            f"Known balances disagree on {day}: {' and '.join(held.sources)} state different "
            "balances for the same day. That is a conflict between sources, not a fault in "
            "the rows."
        )
    if held.kind == HELD_UNMET:
        named = [s for s in held.sources if s and s != "stated"]
        if named:
            by = f" (stated by {', '.join(named)})"
        elif held.sources:
            by = " (a balance you stated)"
        else:
            by = ""
        return f"Held back by the known balance for {day}{by}, which the rows do not reproduce."
    return f"Held back by a movement fault dated {day}: {held.says}."


def standing_line(
    agreement: Agreement, protected_through: date | None, *, with_protection: bool = True
) -> str:
    """The one-line summary shown on the ledger, the Accounts page, and the Overview cards.

    `with_protection` is False for a reading of a whole family, which nothing protects: a person
    protects an account, and the clause would say "nowhere" of a thing that cannot be protected.
    """
    if agreement.state == NONE:
        return "No known balance: these rows cannot be verified."
    through = _day(agreement.through) if agreement.through else "no date yet"
    line = (
        f"Known balances from {_day(agreement.known_from)} to {_day(agreement.known_to)}; "
        f"in agreement through {through}"
    )
    if not with_protection:
        return line + "."
    protected = _day(protected_through) if protected_through else "nowhere"
    return f"{line}; protected through {protected}."
