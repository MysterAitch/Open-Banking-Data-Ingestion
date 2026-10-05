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

THE LISTING RULE (R1 to R5), stated here once. A statement does not say "the balance on day D was
X"; it says: from this opening balance, THESE transactions, to this closing balance. Placing its
opening balance on a calendar day failed on a real store, because card statements list a purchase
by the day it was made (before the previous statement closed), one account's statements overlap,
and a purchase pending at one close appears on the next. So a statement is tested by what it
LISTS, with no date in the question. What it concludes is `statement_listing_measure`'s, read
here as `StatementCheck`s, and the arithmetic is its one implementation:

  R1  A statement that states an opening balance and adds up by what it lists (its lines found,
      read whole, every listed transaction held as listed, counted through its fold, tested with
      its Spaces where it cannot see them) tests its OWN closing balance, whatever known balance
      does or does not precede it. So an account whose only known balance is such a closing adds
      up through it, and the days from a first statement's start to its closing are tested. That
      is claimed only for days where no counting transaction is listed by no statement
      (`StatementCheck.days_tested`): where another source holds unlisted transactions in the
      span, the statement is verified and its days are not. A statement that "cannot say"
      verifies and faults nothing.
  R2  A statement's closing balance is the balance AFTER THE TRANSACTIONS IT LISTS, not the balance
      at the end of a calendar day. Where it and another source's balance for the same day differ
      by exactly ALL the counting transactions dated that day that it does not list, the
      statement is TAKEN TO HAVE CLOSED BEFORE THEM, which is strong evidence and not proof. It is
      then taken out of that day's comparison (as a balance stated for a moment is,
      `Known.instant`), the chain counts it as before them, and the other balance is judged with
      them counted. ONE hypothesis, all or nothing: a subset that happened to sum to the
      difference is arithmetic fitted to a hypothesis, and so is taking the other balance to be
      for another day (tried and withdrawn: the account then said it added up while every balance
      the page showed was out). Only a statement that itself adds up by what it lists explains a
      day away. The end-of-day balance may define the opening on such a day, never the
      statement's, whose closing the opening's count of "dated up to the day" would get wrong: the
      claim is refused where the statement would define it. `apply_checks` checks the claim
      against the figures and ignores one they do not bear out.
  R3  A statement IN USE (its closing is a known balance) whose lines were found and do not sum is
      a real fault: the account does not add up there (`HELD_STATEMENT`), said with the statement
      and the check it failed, and its exit is disregarding that statement's closing. One whose
      lines were not found, that the reader refused, or one of whose lines is held under another
      account (only the store's own Space fold puts one there) cannot say: it verifies nothing,
      faults nothing, and moves no verdict. A disregarded statement lists nothing.
  R4  Consecutive statements are linked by their balances (an opening equal to the previous
      closing is evidence of it, never proof). Two that share a listed transaction, or where the
      later starts inside the earlier, OVERLAP and nothing is concluded from their balances; two
      that do not overlap and do not meet prove a gap by arithmetic.
  R5  Nothing else changes. No opening balance is placed on a day or tested by date; the chain of
      closings, `through`, the opening figure the position and chart read, and the push are as
      before. An account that adds up without the listing checks adds up with them unless a
      statement is a fault.

DERIVED ON DEMAND from the readings `effective_opening` and the movement report already hold.
Nothing here walks an account: the ledger reuses the opening it built for its own page, and the
Overview reads a cached standing.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date
from itertools import pairwise
from typing import TYPE_CHECKING

from .balance_anchors import (
    ASSUMED_NIL,
    STATEMENT,
    EffectiveOpening,
    FamilyReading,
    FamilyWalk,
)
from .family_anchors import OPENED
from .masking import Structural
from .plural import plural
from .statement_checks import (
    DOES_NOT_REACH,
    HELD_TWICE,
    LISTED_TWICE,
    NO_LONGER_COUNTS,
    NOT_HELD,
    OTHER_AMOUNT,
    ClosedBefore,
    StatementCheck,
    StatementChecks,
)

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
HELD_STATEMENT = "statement"

#: Which hold wins when two begin on one day: a disagreement between sources makes the rows' own
#: verdict at that day meaningless, so it is said first, and a statement that does not add up by
#: what it lists is a more specific finding than a balance the transactions do not reproduce.
_HOLD_ORDER = (HELD_CONFLICT, HELD_STATEMENT, HELD_UNMET, HELD_MOVEMENT)

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
    #: How the balance is stated (`balance_anchors.STATEMENT` for a statement's closing), so that
    #: what the measurement concluded about a statement is attached to it and to no other source's
    #: balance that happens to state the same figure.
    basis: str = ""
    #: Tested by the transactions its own statement lists (R1), whatever balance precedes it.
    self_tested: bool = False
    #: How many transactions its statement lists, where `self_tested`.
    listed: int = 0
    #: Taken to have closed before some transactions (R2): not compared with another source's
    #: balance for the same day, which includes them.
    closed_before: ClosedBefore | None = None
    #: The first day its statement's listing tests, where `self_tested`.
    span_start: date | None = None


@dataclass(frozen=True)
class ListingTested:
    """A known balance tested by its own statement's listing and by nothing before it: the
    earliest, which no earlier balance can test."""

    day: Structural[date]
    listed: Structural[int]
    #: The first day the listing tests: the statement's start, where the days are placed.
    start: Structural[date | None] = None


@dataclass(frozen=True)
class StatementFault:
    """A statement in use that does not add up by what it lists, as its own finding: it is said
    whichever hold is earliest in the account's sentence."""

    day: Structural[date]
    says: Structural[str]


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
    #: The known balance tested only by its own statement's listing (R1), if any.
    listing_tested: Structural[tuple[ListingTested, ...]] = ()
    #: The days a statement is taken to have closed before transactions another source's balance
    #: counts (R2), where that was applied.
    closed_before: Structural[tuple[ClosedBefore, ...]] = ()
    #: The known balances that tested their days. Compared and never shown.
    tested_known: tuple[Known, ...] = ()
    #: Every statement in use that does not add up by what it lists (R3), earliest first.
    statement_faults: Structural[tuple[StatementFault, ...]] = ()

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
    known: Iterable[Known],
    faults: Iterable[Fault],
    *,
    movement_checked: bool = True,
    checks: StatementChecks | None = None,
) -> Agreement:
    """The rule above, over known balances, movement faults, and what each statement's own listing
    concludes (`checks`, None for a reading that has none). Pure."""
    balances = sorted(apply_checks(known, checks), key=lambda k: (k.day, k.source, k.figure))
    statement_fault = _first_statement_fault(checks)
    if not balances:
        if statement_fault is None:
            return Agreement(NONE, None, None, 0, 0, None, None, movement_checked, ())
        return Agreement(
            HELD_STATEMENT,
            None,
            None,
            0,
            0,
            None,
            _statement_hold(statement_fault),
            movement_checked,
            (),
        )
    by_day: dict[date, list[Known]] = defaultdict(list)
    for balance in balances:
        by_day[balance.day].append(balance)
    days = sorted(by_day)

    conflicts: dict[date, tuple[str, ...]] = {}
    for day in days:
        stated = [k for k in by_day[day] if not k.instant and k.closed_before is None]
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
    if statement_fault is not None:
        starts[HELD_STATEMENT] = statement_fault.day
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
        elif kind == HELD_STATEMENT and statement_fault is not None:
            held = _statement_hold(statement_fault)
        else:
            held = HeldBack(kind, blocked_from, (), first_fault.says if first_fault else "")
        state = kind
    elif statement_fault is not None:
        # A statement that does not add up is a finding in itself, so it is said even where it
        # closes after the latest known balance and no balance is still to be reached.
        held = _statement_hold(statement_fault)
        state = HELD_STATEMENT
    tested_days = set(tested)
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
        statement_faults=_statement_faults(checks),
        listing_tested=tuple(
            ListingTested(k.day, k.listed, k.span_start)
            for k in balances
            if k.self_tested and k.day == days[0] and k.day in tested_days and not k.premised
        ),
        closed_before=tuple(
            sorted(
                {k.closed_before for k in balances if k.closed_before is not None},
                key=lambda c: c.day,
            )
        ),
        tested_known=tuple(
            k
            for k in balances
            if k.day in tested_days
            and (
                k.verdict == MET
                or k.self_tested
                # On a day a statement closed before some transactions, the balance the span
                # reaches is the end-of-day one, even where it defines the opening.
                or (
                    k.verdict == DEFINES
                    and any(o.closed_before is not None for o in by_day[k.day])
                )
            )
        ),
    )


def _first_statement_fault(checks: StatementChecks | None) -> StatementCheck | None:
    """The earliest statement that does not add up by what it lists (R3), if any."""
    if checks is None:
        return None
    found = [c for c in checks.statements if c.fault]
    return min(found, key=lambda c: c.day, default=None)


def _statement_faults(checks: StatementChecks | None) -> tuple[StatementFault, ...]:
    if checks is None:
        return ()
    return tuple(
        StatementFault(c.day, statement_fault_sentence(c))
        for c in sorted((c for c in checks.statements if c.fault), key=lambda c: c.day)
    )


def _statement_hold(check: StatementCheck) -> HeldBack:
    return HeldBack(HELD_STATEMENT, check.day, (), statement_fault_sentence(check))


def apply_checks(known: Iterable[Known], checks: StatementChecks | None) -> list[Known]:
    """The known balances with what each statement's own listing concludes laid on them.

    R1 marks a statement closing that adds up by what it lists, whose days are tested, as tested
    by that listing. R2 reads a statement's closing as the balance after the transactions it lists:
    where the measurement found that another source's balance for the same day differs from it by
    exactly the counting transactions nobody lists, the statement is shifted by those that the chain
    counted at its closing, and is taken out of the day's comparison. The measurement's claim is
    checked here against the figures themselves, and a claim they do not bear out is ignored, so
    the day stays the conflict it is today.
    """
    found = list(known)
    if checks is None or not checks.statements:
        return found
    # A reading with a balance that has no distance has no opening to measure from, and nothing
    # can be shifted.
    measurable = all(k.distance is not None for k in found)
    for check in checks.statements:
        at = [
            i
            for i, k in enumerate(found)
            if k.basis == STATEMENT and not k.instant and k.day == check.day
            and k.figure == check.figure
        ]
        # Only a statement that itself adds up by what it lists has a balance that is "the balance
        # after the transactions it lists"; one that cannot say, or whose document clashes with
        # another's, explains nothing away.
        if not at or check.adds_up is not True or check.clash:
            continue
        # R2 first: whether a closing is MET depends on what it is taken to have closed before.
        if check.closed_before is not None and measurable:
            _close_before(found, at, check, check.closed_before)
        if check.days_tested:
            for i in at:
                if found[i].verdict in (DEFINES, MET):
                    found[i] = replace(
                        found[i], self_tested=True, listed=check.listed, span_start=check.span_start
                    )
    return found


def _close_before(
    found: list[Known], at: Sequence[int], check: StatementCheck, claim: ClosedBefore
) -> None:
    """Apply one statement's R2 claim to `found`, in place, if the figures and the chain bear it
    out; otherwise leave the day exactly the conflict it is.

    The sum is taken HERE from the amounts, so a wrong sum upstream cannot pass. The opening is
    derived by counting the transactions dated up to and including a day, which is right for a
    balance stated for the end of that day and wrong for a statement that closed before some of
    them. So on a day where this applies the end-of-day balance is the one that may define the
    opening, never the statement's: where the statement would define it the claim is refused and
    the day stays the conflict it is, because every balance the account then shows would be
    out by the transactions the statement closed before. Where another balance defines the
    opening, the statement's distance moves by what the chain counted at its closing, and the
    claim stands only if every balance of the day is then reproduced.
    """
    if any(found[i].verdict == DEFINES for i in at):
        return
    others = [
        i
        for i, k in enumerate(found)
        if not k.instant and k.day == claim.day and k.figure != check.figure
    ]
    if not others or not claim.that_amounts:
        return
    if {found[i].figure - check.figure for i in others} != {sum(claim.that_amounts)}:
        return
    counted = sum(claim.counted_amounts)
    trial = list(found)
    for i in at:
        distance = found[i].distance
        if distance is None:
            return
        distance += counted
        trial[i] = replace(
            found[i],
            distance=distance,
            verdict=MET if distance == 0 else UNMET,
            closed_before=claim,
        )
    if all(trial[i].verdict in (MET, DEFINES) for i in (*at, *others)):
        found[:] = trial


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
        if k.closed_before is None:
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
    if any(k.self_tested and k.verdict in (DEFINES, MET) for k in here):
        return True
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
                basis=anchor.basis,
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
    checks: StatementChecks | None = None,
) -> Standing:
    """An account's standing from the opening already built for it.

    `members` is the account and its known Spaces, whose movement faults a whole-account reading
    counts; the account's own reading counts only its own. `checks` is what its statements' own
    listings conclude (`statement_listing_measure.statement_checks_of`), laid on the account's own
    reading alone: a whole-account reading is of balances no single statement states.
    """
    checked = movement is not None
    own_faults = faults_of(movement, members[:1]) if movement is not None else []
    own = derive_agreement(
        known_of_opening(opening), own_faults, movement_checked=checked, checks=checks
    )
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
    if held.kind == HELD_STATEMENT:
        return held.says
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


#: Which check a statement failed, in the words of the sentence (`StatementCheck.fault`).
_FAULT_REASONS = {
    NOT_HELD: "a transaction it lists is not held",
    OTHER_AMOUNT: (
        "a transaction it lists is held with a different amount from the one the statement "
        "prints"
    ),
    HELD_TWICE: "a transaction it lists is held twice",
    LISTED_TWICE: "it lists a transaction twice that is held once",
    NO_LONGER_COUNTS: "a transaction it lists is held as reversed or void, so it no longer counts",
    DOES_NOT_REACH: (
        "the transactions it lists, as held, do not carry its opening balance to its closing "
        "balance"
    ),
}


def statement_fault_sentence(check: StatementCheck) -> str:
    """Which statement does not add up by what it lists, and which check it failed (R3). Said
    only of a statement in use: one that did not read whole is a fact about the reading and is
    never a fault of the transactions."""
    reason = _FAULT_REASONS.get(check.fault, "it does not add up by what it lists")
    return f"The statement closing on {_day(check.day)} does not add up by what it lists: {reason}."


def listing_tested_sentence(tested: ListingTested) -> str:
    """A known balance tested by its own statement's listing, with the count that tested it."""
    if tested.listed == 0:
        return (
            f"The known balance for {_day(tested.day)} is its statement's opening balance: the "
            "statement lists no transactions and its two balances are equal."
        )
    return (
        f"The known balance for {_day(tested.day)} is tested by the "
        f"{plural(tested.listed, 'transaction')} its statement lists."
    )


def closed_before_sentence(claim: ClosedBefore) -> str:
    """Why two sources' balances for one day are not a conflict (R2), and how it is known."""
    counted = plural(claim.transactions, "transaction")
    them = "it" if claim.transactions == 1 else "them"
    return (
        f"The statement's balance and another source's for {_day(claim.day)} differ by exactly "
        f"the {counted} dated that day that this statement does not list, so the statement is "
        f"taken to have closed before {them}. It is taken so because the two differ by exactly "
        "that, which is strong evidence and not proof."
    )


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
    elif agreement.through == agreement.known_from and agreement.listing_tested:
        listed = agreement.listing_tested[0].listed
        line = (
            f"The transactions add up to the known balance for {_day(agreement.through)}, "
            + (
                f"tested by the {plural(listed, 'transaction')} its statement lists"
                if listed
                else "which its statement states as its opening balance: it lists no transactions"
            )
        )
        if agreement.known_to and agreement.known_to != agreement.through:
            line += f"; the latest known balance is for {_day(agreement.known_to)}"
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
