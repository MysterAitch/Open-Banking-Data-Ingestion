"""What a source's stated running balance MEANS, decided from the source's own arithmetic.

A source blind to an account's Spaces (the certified statement, the CSV export,
the aggregator) states a balance, and two things that balance could be were
once assumed to be one:

  WHOLE ACCOUNT   it moves by the value of every row the source lists, Space
                  payments included, and by nothing else. A transfer between
                  the main account and a Space never moves it.
  MAIN ONLY       it moves by the value of every listed row that is NOT a
                  Space payment, and also by each transfer to or from a Space,
                  which the source does not list.

The certified statement passes its own arithmetic with its Space payments
included, so it is the first. Nothing established that the export's balance
column is the same, and a household whose export is the second would show a
difference at nearly every day against the family's rows, and the difference
would keep changing. That is not a located fault, it is the wrong comparison.

THE TEST. Each pair of consecutive stated balances is a STEP: the movement
between them is known exactly. Both readings predict that movement from the
rows the store holds, and a step is explained by a reading when the prediction
is exact in minor units.

  rows         the entities of the account that the source itself sighted, dated
               as the source dated them. A row the fold filed as a Space payment
               (status folded) moves the balance under WHOLE and not under MAIN.
  transfers    the account's own internal-transfer legs held from the feed that
               the source did not sight: MAIN is explained when the movement not
               accounted for by its rows equals them, date for date and value
               for value. A balance stated after a day's LAST row leaves the
               legs dated on the two boundary days uncertain (they may fall
               before or after that row), so any subset of those is allowed,
               and nothing is allowed for a balance stated at a day's end.

Only steps that tell the readings apart are counted: a step with no Space
payment and no transfer in its window is predicted identically by both and says
nothing about which is right.

THE VERDICT. A reading is ADOPTED only when it explains at least
`READING_THRESHOLD` of those steps and the other does not. 0.9 tolerates a
handful of breaks from a re-ordered, truncated, or partly lost export without
flipping the verdict, and is far from the chance agreement of a wrong reading,
which explains only steps where a Space payment happens to equal a transfer. A
source for which both readings reach it, neither does, or no step discriminates
is used for NOTHING and the page says so: a guess here would turn a statement
about the account into a statement about something else. The steps the adopted
reading does not explain are kept, never dropped: their anchors are tested like
every other and show as the differences they are.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from itertools import accumulate, pairwise

from .models import TransactionStatus
from .store import FOLDED_SIGHTING_PREFIX, Store

#: The share of discriminating steps a reading must explain to be adopted.
READING_THRESHOLD = 0.9

#: More uncertain boundary legs than this and the subsets are not enumerated:
#: the step is then counted as unexplained rather than guessed at.
_MAX_UNCERTAIN_LEGS = 12

#: Statuses whose rows are in no booked balance a source states.
#: Folded rows are not here: a Space copy is still a sighting the source lists.
_NOT_IN_A_BOOKED_BALANCE = (
    TransactionStatus.VOID,
    TransactionStatus.PENDING,
    TransactionStatus.REVERSED,
)

WHOLE = "whole"
MAIN = "main"
BOTH = "both"
NEITHER = "neither"
UNDECIDED = "undecided"


@dataclass(frozen=True)
class SourceMeaning:
    """How one source's stated balances were read, in counts."""

    source: str
    #: Steps that tell the readings apart, and how many each reading explains.
    steps: int
    whole: int
    main: int
    verdict: str

    @property
    def adopted(self) -> bool:
        return self.verdict in (WHOLE, MAIN)


@dataclass(frozen=True)
class _Figure:
    day: date
    balance_minor: int
    #: Stated for the end of the day itself, not after its last row.
    day_end: bool


class _Dated:
    """Movements sorted by date, summed over any window in a bisection."""

    def __init__(self, items: Iterable[tuple[date, int]]) -> None:
        ordered = sorted(items)
        self.days = [day for day, _ in ordered]
        self.minors = [minor for _, minor in ordered]
        self._totals = [0, *accumulate(self.minors)]

    def sum_between(self, after: date, through: date) -> int:
        low, high = bisect_right(self.days, after), bisect_right(self.days, through)
        return self._totals[high] - self._totals[low]

    def count_between(self, after: date, through: date) -> int:
        return bisect_right(self.days, through) - bisect_right(self.days, after)

    def on(self, day: date) -> list[int]:
        return self.minors[bisect_left(self.days, day) : bisect_right(self.days, day)]


def _day(text: object, fallback: str) -> date | None:
    for candidate in (str(text or ""), fallback):
        try:
            return date.fromisoformat(candidate[:10])
        except ValueError:
            continue
    return None


def _subset_sums(values: Sequence[int]) -> set[int] | None:
    if len(values) > _MAX_UNCERTAIN_LEGS:
        return None
    sums = {0}
    for value in values:
        sums |= {s + value for s in sums}
    return sums


@dataclass(frozen=True)
class _Held:
    """The account's rows, by source sighted, and its transfer legs."""

    #: source -> (every listed row, the rows that are not Space payments, the
    #: count of Space payments), each as a dated movement.
    listed: Mapping[str, tuple[_Dated, _Dated, _Dated]]
    #: source -> the transfer legs that source did not sight.
    legs: Mapping[str, _Dated]


def _held(store: Store, main: str, sources: Iterable[str]) -> _Held:
    wanted = set(sources)
    # A Space fold leaves a copied sighting on the Space row naming the folded
    # one; the same-money fold (`same_money_fold`) leaves none, and a row it
    # folded is not a Space payment. The names are an uncorrelated subquery so
    # they are gathered once: no index serves a lookup by provider id, and a
    # per-row EXISTS scanned every sighting for every row.
    rows = store.connection.execute(
        "SELECT t.entity_id, t.amount_minor, t.status, t.value_date, "
        "t.is_internal_transfer, s.source, s.observed_date, "
        "t.entity_id IN (SELECT substr(c.source_id, ?) FROM transaction_sources c "
        "WHERE substr(c.source_id, 1, ?) = ?) AS space_folded "
        "FROM transactions t LEFT JOIN transaction_sources s "
        "ON s.entity_id = t.entity_id "
        "AND (s.source_id IS NULL OR s.source_id NOT LIKE ?) "
        "WHERE t.account_id = ?",
        (
            len(FOLDED_SIGHTING_PREFIX) + 1,
            len(FOLDED_SIGHTING_PREFIX),
            FOLDED_SIGHTING_PREFIX,
            FOLDED_SIGHTING_PREFIX + "%",
            main,
        ),
    ).fetchall()
    sighted: dict[str, dict[str, str]] = defaultdict(dict)
    facts: dict[str, tuple[int, str, str, bool, bool]] = {}
    for row in rows:
        entity = str(row["entity_id"])
        facts[entity] = (
            int(row["amount_minor"]),
            str(row["status"]),
            str(row["value_date"]),
            bool(row["is_internal_transfer"]),
            bool(row["space_folded"]),
        )
        if row["source"] is not None:
            known = sighted[entity].get(str(row["source"]))
            observed = str(row["observed_date"] or "")
            if known is None or (observed and observed < known):
                sighted[entity][str(row["source"])] = observed

    everything: dict[str, list[tuple[date, int]]] = {s: [] for s in wanted}
    unfolded: dict[str, list[tuple[date, int]]] = {s: [] for s in wanted}
    folded: dict[str, list[tuple[date, int]]] = {s: [] for s in wanted}
    legs: dict[str, list[tuple[date, int]]] = {s: [] for s in wanted}
    for entity, (amount, status, value_date, internal, space_folded) in facts.items():
        if status in _NOT_IN_A_BOOKED_BALANCE:
            continue
        for source in wanted:
            if source in sighted[entity]:
                when = _day(sighted[entity][source], value_date)
                if when is None:
                    continue
                everything[source].append((when, amount))
                if status == TransactionStatus.FOLDED and space_folded:
                    folded[source].append((when, amount))
                else:
                    unfolded[source].append((when, amount))
            elif internal and status == TransactionStatus.BOOKED:
                when = _day(value_date, value_date)
                if when is not None:
                    legs[source].append((when, amount))
    return _Held(
        {s: (_Dated(everything[s]), _Dated(unfolded[s]), _Dated(folded[s])) for s in wanted},
        {s: _Dated(legs[s]) for s in wanted},
    )


def _steps(figures: Sequence[_Figure]) -> list[tuple[_Figure, _Figure]]:
    """Consecutive stated balances, leaving out any day stated two ways."""
    by_day: dict[date, set[int]] = defaultdict(set)
    for figure in figures:
        by_day[figure.day].add(figure.balance_minor)
    unambiguous = [f for f in sorted(figures, key=lambda f: f.day) if len(by_day[f.day]) == 1]
    unique: list[_Figure] = []
    for figure in unambiguous:
        if not unique or unique[-1].day != figure.day:
            unique.append(figure)
    return list(pairwise(unique))


def _judge(
    source: str,
    figures: Sequence[_Figure],
    listed: tuple[_Dated, _Dated, _Dated],
    legs: _Dated,
) -> SourceMeaning:
    every, unfolded, folded = listed
    steps = whole = main = 0
    for before, after in _steps(figures):
        low, high = before.day, after.day
        moved = after.balance_minor - before.balance_minor
        # A balance stated after a day's last row leaves that day's legs, and
        # the previous day's, uncertain: they may fall either side of the row.
        uncertain: list[int] = []
        if not after.day_end:
            uncertain += legs.on(high)
        if not before.day_end:
            uncertain += legs.on(low)
        certain = legs.sum_between(low, high) - (sum(legs.on(high)) if not after.day_end else 0)
        count = legs.count_between(low, high) + (len(legs.on(low)) if not before.day_end else 0)
        if not folded.count_between(low, high) and not count:
            continue
        steps += 1
        if moved == every.sum_between(low, high):
            whole += 1
        sums = _subset_sums(uncertain)
        if sums is not None and moved - unfolded.sum_between(low, high) - certain in sums:
            main += 1
    verdict = UNDECIDED
    if steps:
        reaches = (whole >= READING_THRESHOLD * steps, main >= READING_THRESHOLD * steps)
        verdict = {
            (True, False): WHOLE,
            (False, True): MAIN,
            (True, True): BOTH,
            (False, False): NEITHER,
        }[reaches]
    return SourceMeaning(source, steps, whole, main, verdict)


def read_meanings(
    store: Store, main: str, figures: Mapping[str, Sequence[tuple[date, int, bool]]]
) -> tuple[SourceMeaning, ...]:
    """One verdict per source in `figures` (source -> (day, balance, day_end)).

    One read of the account's rows and sightings serves every source.
    """
    if not figures:
        return ()
    held = _held(store, main, figures)
    return tuple(
        _judge(
            source,
            [_Figure(day, minor, end) for day, minor, end in found],
            held.listed[source],
            held.legs[source],
        )
        for source, found in sorted(figures.items())
    )


__all__ = [
    "BOTH",
    "MAIN",
    "NEITHER",
    "READING_THRESHOLD",
    "UNDECIDED",
    "WHOLE",
    "SourceMeaning",
    "read_meanings",
]
