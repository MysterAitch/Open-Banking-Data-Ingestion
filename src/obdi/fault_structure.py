"""What shape the difference between the stated balances and the rows takes.

Where the rows part company with a source's stated balances, the difference is a
step function: its value after each stated balance, changing at some and not at
others. Each change is a STEP with a day and a signed size. Reading hundreds of
them one by one cannot show whether they are a handful of faults repeated, so
this module sorts the steps by what each would mean:

  levels      stretches over which the difference is constant. Within one the
              day-to-day movements agree and only the level is offset, which is
              what a charge shown at the start of a period instead of its end
              looks like. The share of stated balances that move from the one
              before as the rows do is the measure that "the deltas are correct".
  transients  a step that a LATER step of exactly the opposite size undoes: the
              same money counted on different days by the two sides. A pair is
              ONE timing fault, however far apart its two days are.
  recurring   among the steps no pair explains, those of exactly one size that
              occur three or more times: a monthly charge held twice, say.
  net         what is left once the pairs cancel. These permanent steps sum to
              the present difference exactly, by construction.

THE PAIRING RULE, stated here and only here: steps are taken in date order and
each step is paired with the most recent unpaired step of exactly the opposite
size, so a pair is the nearest opposite pair and a step is never in two. A pair
across years is still a pair; its gap is reported so a coincidence of two
unrelated faults of one size shows as a long one rather than as a timing fault.

SIZES ARE FIGURES. A size is a balance difference, so every size field here is a
`Total` that masking replaces with one fixed token. Everything else - days,
counts, kinds, class letters, sources - is structural. Size classes are named by
letter, in order of how many members they have, so a masked page can refer to
"size class A" without saying how large it is.

The module is pure: it is handed the stated balances' differences and, optionally,
which steps `fault_explanation` already explained. It never re-sums rows.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from itertools import pairwise
from typing import TYPE_CHECKING

from .core.masking import Structural, Total
from .family_anchors import OPENED
from .fault_explanation import (
    EXPLAINED_CHANGES,
    NONE,
    UNHELD_SPACE,
    Selection,
    WalkExplanation,
)

if TYPE_CHECKING:
    from .balance_anchors import EffectiveOpening, FamilyWalk

TRANSIENT = "transient"
RECURRING = "recurring"
UNEXPLAINED = "unexplained"
UNHELD = "unheld-space"
EXPLAINED = "explained"

#: A size class is named only from this many members: two of one size are a
#: coincidence, three are a pattern worth a name.
RECURRING_FROM = 3

#: The longest stretches of constant difference that are named.
LONGEST_LEVELS = 3

#: Gaps between the two days of a pair, as (label, highest gap in days). The
#: last has no limit. Said once here; the page words them from these labels.
GAP_BUCKETS: tuple[tuple[str, int | None], ...] = (
    ("within 1 day", 1),
    ("2 days", 2),
    ("3 to 7 days", 7),
    ("8 to 35 days", 35),
    ("more than 35 days", None),
)

MONTHLY = "monthly"
WEEKLY = "weekly"
IRREGULAR = "irregular"

#: A gap that is a whole number of calendar months, up to this many, counts as
#: monthly: a month the class skipped is a member missing, not a different rhythm.
_MONTHS_SKIPPABLE = 3


@dataclass(frozen=True)
class Point:
    """One stated balance judged against the rows."""

    day: date
    source: str
    #: Stated minus predicted; None for the balance that defines the opening.
    difference_minor: int | None
    #: Where it sits in the walk it came from, which is what explanations are keyed by.
    position: int


@dataclass(frozen=True)
class Step:
    """A stated balance at which the difference from the rows changed."""

    day: Structural[date]
    #: The day the previous balance was stated; the step falls in (after, day].
    after: Structural[date]
    source: Structural[str]
    position: Structural[int]
    size_minor: Total[int]
    #: A transfer leg to a Space whose rows are not held falls in the window.
    unheld: Structural[bool]
    #: `fault_explanation` found an arithmetic explanation other than the Space.
    explained: Structural[bool]
    #: Number (into the steps) of the step it is paired with, or -1.
    partner: Structural[int]
    #: Days between the pair's two steps; 0 where there is no partner.
    gap_days: Structural[int]
    #: "A", "B", ... for a recurring size class; "" otherwise.
    size_class: Structural[str]
    kind: Structural[str]


@dataclass(frozen=True)
class Level:
    """A stretch over which the difference does not change."""

    first_day: Structural[date]
    #: The last stated balance in it.
    last_day: Structural[date]
    #: Where the stretch ends: the day the next one begins, or its own last day.
    until: Structural[date]
    balances: Structural[int]
    difference_minor: Total[int]
    #: How many days the stretch lasts, from `first_day` to `until`.
    days: Structural[int]


@dataclass(frozen=True)
class SizeClass:
    """Unpaired steps of one exact size, three or more of them."""

    letter: Structural[str]
    members: Structural[int]
    days: Structural[tuple[date, ...]]
    #: "monthly", "weekly", or "irregular".
    rhythm: Structural[str]
    same_day_of_month: Structural[bool]
    size_minor: Total[int]


@dataclass(frozen=True)
class FaultStructure:
    """The structure of one difference series."""

    balances: Structural[int]
    #: Balances that move from the one before as the rows do.
    agreeing: Structural[int]
    steps: Structural[tuple[Step, ...]]
    levels: Structural[tuple[Level, ...]]
    longest_levels: Structural[tuple[Level, ...]]
    pairs: Structural[int]
    #: How many pairs fall in each of `GAP_BUCKETS`.
    gap_counts: Structural[tuple[int, ...]]
    unpaired: Structural[int]
    classes: Structural[tuple[SizeClass, ...]]
    #: Numbers (into the steps) of the permanent ones, in date order.
    permanent: Structural[tuple[int, ...]]
    present_minor: Total[int]
    permanent_sum_minor: Total[int]
    #: The permanent steps sum to the present difference. True by construction,
    #: and kept as a field so a page can say aloud if it were ever false: the
    #: comparison cannot be made on a masked view, whose sizes are tokens.
    exact: Structural[bool]

    @property
    def share_agreeing(self) -> float:
        """The fraction of stated balances that agree with the one before; 1 where none."""
        return self.agreeing / self.balances if self.balances else 1.0

    @property
    def permanent_steps(self) -> tuple[Step, ...]:
        return tuple(self.steps[number] for number in self.permanent)

    @property
    def permanent_unheld(self) -> tuple[Step, ...]:
        return tuple(step for step in self.permanent_steps if step.unheld)

    @property
    def permanent_explained(self) -> tuple[Step, ...]:
        return tuple(step for step in self.permanent_steps if step.explained and not step.unheld)


@dataclass(frozen=True)
class SourceStructure:
    source: Structural[str]
    structure: Structural[FaultStructure]


@dataclass(frozen=True)
class StructureReport:
    """The whole series, and each stating source's own where there are several."""

    whole: Structural[FaultStructure]
    by_source: Structural[tuple[SourceStructure, ...]] = ()


def class_letter(number: int) -> str:
    """A, B, ... Z, AA, AB, ...: the name of the `number`th class, from 0."""
    letters = ""
    number += 1
    while number:
        number, rest = divmod(number - 1, 26)
        letters = chr(ord("A") + rest) + letters
    return letters


def _steps(
    points: Sequence[Point], opened: date | None, legs: Sequence[date]
) -> list[tuple[Point, date, int, bool]]:
    """Each point at which the difference changed: (point, window start, size, unheld).

    The one reading of "the difference changed": `FamilyWalk.changes` is the
    same rule over the whole walk, and a test holds the two together.
    """
    found: list[tuple[Point, date, int, bool]] = []
    earlier, latest = opened, opened
    before = 0
    for point in points:
        if latest is None or point.day > latest:
            earlier, latest = latest, point.day
        if point.difference_minor is None:
            continue
        if point.difference_minor != before:
            start = earlier if earlier is not None else point.day
            unheld = bisect_right(legs, point.day) > bisect_right(legs, start)
            found.append((point, start, point.difference_minor - before, unheld))
        before = point.difference_minor
    return found


def _levels(points: Sequence[Point]) -> tuple[Level, ...]:
    held = [(point.day, point.difference_minor or 0) for point in points]
    runs: list[list[tuple[date, int]]] = []
    for item in held:
        if runs and runs[-1][-1][1] == item[1]:
            runs[-1].append(item)
        else:
            runs.append([item])
    levels = []
    for index, run in enumerate(runs):
        following = runs[index + 1][0][0] if index + 1 < len(runs) else run[-1][0]
        first = run[0][0]
        levels.append(
            Level(first, run[-1][0], following, len(run), run[0][1], (following - first).days)
        )
    return tuple(levels)


def _rhythm(days: Sequence[date]) -> tuple[str, bool]:
    gaps = [(later - earlier).days for earlier, later in pairwise(days)]
    same_day = len({day.day for day in days}) == 1
    if gaps and all(gap == 7 for gap in gaps):
        return WEEKLY, same_day
    if gaps and all(
        any(28 * k <= gap <= 31 * k for k in range(1, _MONTHS_SKIPPABLE + 1)) for gap in gaps
    ):
        return MONTHLY, same_day
    return IRREGULAR, same_day


def structure(
    points: Sequence[Point],
    *,
    opened: date | None = None,
    unheld_days: Sequence[date] = (),
    explained: Mapping[int, bool] | None = None,
) -> FaultStructure:
    """The structure of a difference series, in one pass and a sort.

    `points` are in walk order. `unheld_days` are the days of transfer legs to a
    Space whose rows are not held, sorted. `explained` says which points
    (by `Point.position`) `fault_explanation` found an arithmetic explanation for.
    """
    said = explained or {}
    found = _steps(points, opened, unheld_days)
    sizes = [size for _, _, size, _ in found]
    days = [point.day for point, _, _, _ in found]

    partner = [-1] * len(found)
    waiting: dict[int, list[int]] = defaultdict(list)
    for number, size in enumerate(sizes):
        stack = waiting.get(-size)
        if stack:
            earlier = stack.pop()
            partner[number], partner[earlier] = earlier, number
        else:
            waiting[size].append(number)
    unpaired = [number for number, other in enumerate(partner) if other < 0]

    by_size: dict[int, list[int]] = defaultdict(list)
    for number in unpaired:
        by_size[sizes[number]].append(number)
    ranked = sorted(
        (members for members in by_size.values() if len(members) >= RECURRING_FROM),
        key=lambda members: (-len(members), days[members[0]]),
    )
    classes: list[SizeClass] = []
    letter_of: dict[int, str] = {}
    for rank, members in enumerate(ranked):
        letter = class_letter(rank)
        member_days = tuple(days[number] for number in members)
        rhythm, same_day = _rhythm(member_days)
        classes.append(
            SizeClass(letter, len(members), member_days, rhythm, same_day, sizes[members[0]])
        )
        letter_of.update(dict.fromkeys(members, letter))

    steps = tuple(
        Step(
            day=point.day,
            after=start,
            source=point.source,
            position=point.position,
            size_minor=size,
            unheld=unheld,
            explained=said.get(point.position, False),
            partner=partner[number],
            gap_days=(
                abs((days[number] - days[partner[number]]).days) if partner[number] >= 0 else 0
            ),
            size_class=letter_of.get(number, ""),
            kind=(
                TRANSIENT
                if partner[number] >= 0
                else RECURRING
                if number in letter_of
                else UNHELD
                if unheld
                else EXPLAINED
                if said.get(point.position, False)
                else UNEXPLAINED
            ),
        )
        for number, (point, start, size, unheld) in enumerate(found)
    )

    gap_counts = [0] * len(GAP_BUCKETS)
    for number, other in enumerate(partner):
        if other > number:
            gap = steps[number].gap_days
            for slot, (_, highest) in enumerate(GAP_BUCKETS):
                if highest is None or gap <= highest:
                    gap_counts[slot] += 1
                    break

    levels = _levels(points)
    longest = tuple(
        sorted(levels, key=lambda level: (-level.days, -level.balances, level.first_day))[
            :LONGEST_LEVELS
        ]
    )
    present = next(
        (p.difference_minor for p in reversed(points) if p.difference_minor is not None), 0
    )
    return FaultStructure(
        balances=len(points),
        agreeing=len(points) - len(steps),
        steps=steps,
        levels=levels,
        longest_levels=longest,
        pairs=sum(1 for number, other in enumerate(partner) if other > number),
        gap_counts=tuple(gap_counts),
        unpaired=len(unpaired),
        classes=tuple(classes),
        permanent=tuple(unpaired),
        present_minor=present,
        permanent_sum_minor=sum(sizes[number] for number in unpaired),
        exact=present == sum(sizes[number] for number in unpaired),
    )


def _report(
    points: Sequence[Point],
    opened: date | None,
    unheld_days: Sequence[date],
    explained: Mapping[int, bool],
) -> StructureReport:
    whole = structure(points, opened=opened, unheld_days=unheld_days, explained=explained)
    sources = sorted({point.source for point in points})
    separate: tuple[SourceStructure, ...] = ()
    if len(sources) > 1:
        separate = tuple(
            SourceStructure(
                source,
                structure(
                    [point for point in points if point.source == source],
                    opened=opened,
                    unheld_days=unheld_days,
                    explained=explained,
                ),
            )
            for source in sources
        )
    return StructureReport(whole, separate)


def _explained(walk: FamilyWalk) -> dict[int, bool]:
    """Which changes of the walk `fault_explanation` found an explanation for, by
    the position of the stated balance. Its verdict is reused as it stands: the
    Space test is reported on its own, so it does not count here."""
    return _explained_by(walk.explanation)


def _explained_by(explanation: WalkExplanation | None) -> dict[int, bool]:
    if explanation is None:
        return {}
    return {
        explained.index: any(hold not in (NONE, UNHELD_SPACE) for hold in explained.holds)
        for explained in explanation.changes
    }


def select_explained(walk: FamilyWalk, cap: int | None = None) -> Selection:
    """Which changes of the walk are worth an explanation: each permanent one, and each pair once.

    A permanent change is explained by itself. A timing pair is one fault, so
    it is explained once, at its first change, and its later change is not.
    Past `cap` (`EXPLAINED_CHANGES` unless given) the permanent changes are kept
    before the pairs, and the rest are counted in `Selection.omitted` so the page
    can say so.
    Positions are into `FamilyWalk.changes`, which `structure` reads the same way
    (a test holds the two together).
    """
    cap = EXPLAINED_CHANGES if cap is None else cap
    shape = structure(
        walk_points(walk),
        opened=walk.opened.day if walk.opened else None,
        unheld_days=walk.unheld.days,
    )
    permanent = [number for number, step in enumerate(shape.steps) if step.partner < 0]
    firsts = {
        number: step.partner
        for number, step in enumerate(shape.steps)
        if step.partner > number
    }
    wanted = [*permanent, *firsts]
    kept = sorted(wanted[:cap])
    held = set(kept)
    return Selection(
        kept,
        {number: later for number, later in firsts.items() if number in held},
        omitted=len(wanted) - len(kept),
        bound=cap,
    )


def walk_points(walk: FamilyWalk) -> list[Point]:
    return [
        Point(reading.day, reading.sources[0], reading.difference_minor, index)
        for index, reading in enumerate(walk.readings)
    ]


def walk_report(walk: FamilyWalk) -> StructureReport:
    """The structure of the whole account's stated balances against the family's rows."""
    return _report(
        walk_points(walk),
        walk.opened.day if walk.opened else None,
        walk.unheld.days,
        _explained(walk),
    )


def account_report(opening: EffectiveOpening) -> StructureReport:
    """The structure of the account's OWN stated balances against its own rows.

    No Space legs are looked for here: a Space's rows are not part of what
    these balances state. Which changes are explained is what
    `EffectiveOpening.explanation` found, by the same rule as a walk's.
    """
    readings = opening.readings
    opened = readings[0].anchor.day if readings and readings[0].anchor.basis == OPENED else None
    points = [
        Point(r.anchor.day, r.anchor.source or r.anchor.basis, r.difference_minor, index)
        for index, r in enumerate(readings)
    ]
    return _report(points, opened, (), _explained_by(opening.explanation))
