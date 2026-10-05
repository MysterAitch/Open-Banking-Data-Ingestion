"""How far an account is IN AGREEMENT: the one place the rule is written.

The words are defined in `clearing`; this module derives the second of them. The owner's framing,
asked to make the three ideas explicit: "the transactions to this date match the known balance
(and/or are verified as being correct)". And a balance cannot see movements that net to nil, so
"verified as being correct" means the movement checks hold as well as the money.

THE RULE. An account is IN AGREEMENT THROUGH the day C of the latest known balance such that:

  1  every known balance dated on or before C is met: the rows reproduce it. The one that
     defines the opening is met by construction, and a nil opening (`balance_anchors.OPENED`,
     `ASSUMED_NIL`) is a premise rather than a known balance, so it is neither counted nor shown;
  2  at least one known balance on or before C has actually been TESTED: met by the transactions
     and the arithmetic from a known balance on an EARLIER day, rather than by defining. An
     opening derived from one known balance absorbs every missing transaction before it, so a lone
     known balance verifies nothing (state `UNTESTED`), and neither do two balances for the same
     day (a statement's closing and a balance a person typed, or two sources) however well they
     agree: they test no transaction. So `through` is never the day of the earliest known
     balance, with one exception: an account created with its history held starts from a nil that
     is a premise, not derived from any balance (`balance_anchors.OPENED`), and its first known
     balance IS tested against that nil by every transaction since (`Known.premised`);
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

STRETCHES. Beside `through`, every stretch between two consecutive days that state a known
balance is judged by its own arithmetic (`Agreement.stretches`), so a page can say where the
transactions stop adding up and not only that they do. The later balance is reproduced when the
earlier one plus the transactions between them gives it, which is exactly when the distance of the
rows from the balance (`Known.distance`, counted from the one opening) did not change across the
stretch. Stretches say WHERE; they decide nothing: `through`, `held`, and `state` are exactly what
the rule above gives, and so is everything that reads them (the protected period, the Overview,
the push, the timeline). That a later stretch adds up despite an earlier failing one is not
carried as a conclusion, and no balance is declared "in doubt" from its neighbours: a lone
mis-stated balance simply fails both stretches it borders, and the page says what a failing
stretch can mean without choosing.

  - WHILE A CONFLICT STANDS on a day, the stretch beginning there is UNTESTED
    (`StretchResult.untested`), neither said to add up nor to fail: it would have to match either
    of two figures, and passing on either would let two disagreeing sources vouch for each other.
    A stretch ENDING on a conflict is the conflict, said in its own words, and not a failure.
  - nothing is decided by how plausible a balance looks. A stretch is reproduced, not reproduced,
    or untested.

A known balance a person has disregarded (`disregarded_balances`) is not read at all: it takes no
part in a stretch, in agreement, or in a conflict.

DERIVED ON DEMAND from the readings `effective_opening` and the movement report already hold.
Nothing here walks an account: the ledger reuses the opening it built for its own page, and the
Overview reads a cached standing.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from itertools import pairwise
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
    #: The account was created with its history held, so its opening is a nil premise rather
    #: than one derived from a balance, and the first known balance is tested by every
    #: transaction since (rule 2).
    premised: bool = False
    #: How far the figure is from what the rows predict from the one opening (stated minus
    #: predicted; nil for the balance that defines the opening). None where the reading derived no
    #: opening, and no stretch is then judged. Compared and never shown.
    distance: int | None = None


@dataclass(frozen=True)
class StretchResult:
    """One stretch between consecutive days that state a known balance, and whether it adds up."""

    start: Structural[date]
    end: Structural[date]
    reproduced: Structural[bool]
    #: Two sources state different figures for `end`: a conflict between sources, not a fault.
    conflict: Structural[bool] = False
    #: A conflict stands on `start`, so nothing is tested over the stretch (module docstring).
    untested: Structural[bool] = False


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
    #: The days on which a known balance was tested (rule 2), so that a reader which asks which
    #: days a span may reach (`protection.tested_days`) asks the rule and does not restate it.
    tested: Structural[tuple[date, ...]] = ()
    #: Every stretch between consecutive known-balance days, each judged by its own arithmetic
    #: (module docstring). Empty where a balance carries no distance (`Known.distance`).
    stretches: Structural[tuple[StretchResult, ...]] = ()

    @property
    def failing(self) -> tuple[StretchResult, ...]:
        """The stretches the transactions do not reproduce: not a conflict between sources, and
        not one that began on a conflict and so was never tested."""
        return tuple(
            s for s in self.stretches if not s.reproduced and not s.conflict and not s.untested
        )


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

    tested = [d for d in days if _tested(d, days[0], by_day[d], conflicts)]
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
        tested=tuple(tested),
        stretches=_stretch_chain(balances),
    )


def _stretch_chain(balances: Sequence[Known]) -> tuple[StretchResult, ...]:
    """Each stretch between consecutive days that state a day-end balance, judged by whether the
    rows' distance from the balance changed across it. Empty where any balance has no distance."""
    day_end = [k for k in balances if not k.instant]
    if not day_end or any(k.distance is None for k in day_end):
        return ()
    levels: dict[date, set[int]] = defaultdict(set)
    figures: dict[date, set[int]] = defaultdict(set)
    for k in day_end:
        if k.distance is not None:
            levels[k.day].add(k.distance)
        figures[k.day].add(k.figure)
    found = []
    for before, day in pairwise(sorted(levels)):
        conflict = len(figures[day]) > 1
        untested = len(figures[before]) > 1 and not conflict
        reproduced = not conflict and not untested and levels[day] <= levels[before]
        found.append(StretchResult(before, day, reproduced, conflict, untested))
    return tuple(found)


def _tested(
    day: date, earliest: date, here: Sequence[Known], conflicts: Collection[date]
) -> bool:
    """Whether the known balances of `day` were tested (rule 2). The earliest day tests nothing
    however many balances state it, unless the account's nil opening is a premise."""
    if day in conflicts:
        return False
    if day == earliest:
        return any(k.premised and k.verdict == MET for k in here)
    return any(k.verdict == MET for k in here)


def known_of_opening(opening: EffectiveOpening) -> list[Known]:
    """The account's own known balances, each with whether the rows reproduce it."""
    found = []
    premised = any(r.anchor.basis == OPENED for r in opening.readings)
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
                premised=premised,
                distance=_distance(reading.difference_minor, reading.defines_opening),
            )
        )
    return found


def known_of_walk(walk: FamilyWalk) -> list[Known]:
    """The whole family's known balances, from the walk of the family's rows."""
    premised = walk.opened is not None
    found = []
    for reading in walk.readings:
        found.append(_known_of_reading(reading, instant=False, premised=premised))
    for reading in walk.bank_readings:
        found.append(_known_of_reading(reading, instant=True, premised=premised))
    return found


def _known_of_reading(reading: FamilyReading, *, instant: bool, premised: bool) -> Known:
    verdict = DEFINES if reading.agrees is None else MET if reading.agrees else UNMET
    return Known(
        reading.day,
        reading.sources[0] if reading.sources else "",
        verdict,
        reading.balance_minor,
        instant=instant,
        premised=premised,
        distance=_distance(reading.difference_minor, reading.defines_opening),
    )


def _distance(difference_minor: int | None, defines_opening: bool) -> int | None:
    """How far a reading is from the rows: nil for the one that defines the opening, and unknown
    where no opening was derived to measure from."""
    if difference_minor is not None:
        return difference_minor
    return 0 if defines_opening else None


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
                f"Only one known balance ({_day(agreement.known_from)}); a second is needed "
                "before the transactions can be checked."
            )
        return ""
    day = _day(held.day)
    if held.kind == HELD_CONFLICT:
        return (
            f"Two sources state different balances for {day} ({' and '.join(held.sources)}), "
            "so nothing after that day can be checked. That is a conflict between sources, "
            "not a fault in the transactions."
        )
    if held.kind == HELD_UNMET:
        named = [s for s in held.sources if s and s != "stated"]
        if named:
            by = f" (stated by {', '.join(named)})"
        elif held.sources:
            by = " (a balance you stated)"
        else:
            by = ""
        return f"The transactions do not add up to the known balance for {day}{by}."
    return f"The transactions stop adding up at {day}, because of a movement fault: {held.says}."


#: What a stretch that does not add up can mean, said once and folded on the page: the arithmetic
#: says that the transactions and the two balances disagree, and cannot say which of these is why.
STRETCH_MEANINGS = (
    "a transaction is missing from those days",
    "a transaction is counted twice",
    "a transaction has the wrong amount or sign",
    "a transaction is dated on the wrong side of either day",
    "either known balance is mis-stated or was misread",
)


def stretch_sentences(agreement: Agreement) -> list[str]:
    """Where the transactions stop adding up, and where nothing could be tested, in days only.

    Never a figure. Reads the fields and not `Agreement.failing`: a page holds a masked view of
    the agreement, which exposes its fields and not its properties.
    """
    lines = []
    for stretch in agreement.stretches:
        if stretch.reproduced or stretch.conflict:
            continue
        if stretch.untested:
            lines.append(
                f"Nothing is tested from {_day(stretch.start)} to {_day(stretch.end)}: two "
                f"known balances differ for {_day(stretch.start)}, so the transactions cannot be "
                "said to add up from either."
            )
            continue
        lines.append(
            f"The transactions held between {_day(stretch.start)} and {_day(stretch.end)} do "
            "not add up to the change between the two known balances."
        )
    return lines


def standing_line(
    agreement: Agreement, protected_through: date | None, *, with_protection: bool = True
) -> str:
    """The one-line summary shown on the ledger, the Accounts page, and the Overview cards.

    `with_protection` is False for a reading of a whole family, which nothing protects: a person
    protects an account, and the clause would say "not protected" of a thing that cannot be.
    """
    if agreement.state == NONE:
        return "No known balance, so there is nothing to check the transactions against."
    if not agreement.through:
        line = "The transactions do not yet add up to any known balance"
    else:
        line = (
            "The transactions add up to every known balance from "
            f"{_day(agreement.known_from)} to {_day(agreement.through)}"
        )
        if agreement.known_to and agreement.known_to != agreement.through:
            line += f"; the latest known balance is for {_day(agreement.known_to)}"
    if not with_protection:
        return line + "."
    if not protected_through:
        return f"{line}; not protected."
    return f"{line}; protected through {_day(protected_through)}."
