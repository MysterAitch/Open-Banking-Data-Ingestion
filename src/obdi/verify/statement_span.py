"""Which days a held statement covers, how each end is known, and when the next is due.

THE QUESTION. "What to fetch next" and the coverage timeline both need the days a statement
accounts for. The documents give less than one would like - only four layouts print a period,
two more print the day the previous statement closed beside the opening balance (kept as the
period's start by their parsers), and every layout prints an opening and a closing balance -
so this module says, for each end of each statement, HOW it is known. A caller must be able to
tell a printed day from a placed one; `Known` is the one declaration of the ways.

ONE FUNCTION per reader: `statement_spans` reads the store, `describe_account` is the same
rules over statements handed in (for tests and callers that hold the evidence already).

WHAT THE BALANCES PROVE. Arithmetic is sound in one direction only:
  - a statement whose opening balance DIFFERS from the closing balance of the statement held
    before it proves that something lies between them: money moved that neither lists;
  - an opening balance that EQUALS the previous closing proves nothing of the kind. It is
    consistent with the two meeting, and equally with a whole missing statement whose
    movements net to nil (money in and straight out again, over a day or over weeks).
So equality never closes a gap that other evidence opens, and the sentences written for it
never say that nothing is missing. What does make two statements meet, in order of strength:
  1. a printed start day (or day beside the opening balance) that follows the earlier closing
     day: `Known.STATED`;
  2. equal balances AND closing days one period apart (within `HOLE_CADENCES` cadences):
     `Known.BALANCES_MEET`, an inference - it is defeated by a missing statement short enough
     to leave the closing days looking one period apart, and by nothing else it can see;
  3. equal balances and closing days two or more periods apart: NOT contiguous, a probable
     hole whose movements net to nil (`HoleReason.BALANCES_MEET_NET_NIL`, inferred);
  4. no balance to compare: the spacing of closing days alone (`Known.INFERRED`).
The rows other sources hold are evidence the documents cannot give: a row in the days between
two statements that no statement lists turns an inferred hole, or a presumed meeting, into a
stated one (`HoleReason.UNLISTED_ROWS`). A count of none is support only where another source
actually covers those days (`OtherSources`), and never proof.

WHERE A HOLE ENDS (`_reach`). The day after the earlier close is a fact. Where the later
statement prints no start, the missing statement's own close is not known, so the hole is taken
to end at the closing day the cadence expects (`Hole.closings`) and the later statement to
cover from the day after (INFERRED), unless its first listed row is earlier, when the row wins.
`Hole.known` says whether the hole exists; `Hole.last_known` says how its end is known.

A STATEMENT IS WHOLE unless it is partial. Its closing day is a whole day (the closing balance
is the balance at that day's end) and being a file does not make it partial. It is PARTIAL
only where its closing day is later than the day it was produced - the day the document prints
(`StatementPeriod.produced`), failing that the day obdi received it, failing that `today`: an
interim or ad hoc statement, or one whose period was still running. A partial statement covers
its start to that day, and that day may itself be partial; `Span.bounded_by` says which day
was used because "received" is weaker than "produced".

WHEN A STATEMENT IS DUE, stated here once (`next_statement`): the cadence is that of the
statements held (`cadence_of`, three or more, about monthly) and the statements close on
calendar-month steps from the newest held, so a month of 28 or 31 days does not drift. One is
due once its closing day plus the lag has passed. The lag is the shortest time any held
statement took to be received after its closing day (within `MAX_PLAUSIBLE_LAG_DAYS`), from the
kept times: a statement received N days after it closed proves one can be available by then,
and a later receipt may be only the owner's own delay, so the shortest is the best evidence and
later receipts are not averaged in. Where no kept time says, the lag is nil and the available
day is the closing day.
"""

from __future__ import annotations

import calendar
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum
from itertools import pairwise
from typing import TYPE_CHECKING

from ..core.models import Transaction, TransactionStatus
from ..ingest.parsers.pdf_statements import PDF_PARSERS
from ..ingest.statement_terms import StatementPeriod, statement_periods
from .coverage import coverage

if TYPE_CHECKING:
    from ..ingest.store import Store

#: The sources whose rows are a statement's own.
STATEMENT_SOURCES = frozenset(parser.source for parser in PDF_PARSERS) | {"statement"}

#: The days between statements that make a cadence "monthly": a statement closes on about the
#: same day each month, and a month is 28 to 31 days with a day of slack.
MONTHLY_DAYS = (27, 32)

#: The fewest statements a cadence is inferred from. Two give one interval, which is a
#: coincidence as easily as a rhythm.
CADENCE_NEEDS = 3

#: How many cadences between two closing days make a hole. Two statements a month apart close
#: about 30 days apart, and with one between them missing the interval is 59 to 62 days; "twice
#: the cadence" read as 62 days would miss a hole of 61, the commonest case, so the line is
#: one and a half. The number of statements missing is the nearest whole number of calendar
#: months between the two closings, less one.
HOLE_CADENCES = 1.5


#: How long after its closing day a statement may be received and still say how soon one can be
#: available. A statement uploaded months later (a back-catalogue in one sitting) says only when
#: the owner got round to it, and counting it would make the newest statement look unavailable
#: for as long as the owner took to upload it.
MAX_PLAUSIBLE_LAG_DAYS = 14


class Known(StrEnum):
    """How one end of a statement's period is known. Strongest first, for a start."""

    #: The statement prints that day: a period start, a day beside its opening balance (kept
    #: as the start by the parser, in that layout's convention), a closing or statement date.
    #: Does not prove the printed day is right, only that the document says so.
    STATED = "stated"
    #: The statement's opening balance equals the closing balance of the statement held
    #: before it, and the two closing days are one period apart, so the period is taken to
    #: begin the day after that one closed. An INFERENCE, not a fact about the two documents:
    #: equal balances are also what a missing statement whose movements net to nil leaves.
    BALANCES_MEET = "balances-meet"
    #: Neither: the day is that of the statement's own first (or last) listed row, so the
    #: period is known to cover "at least" from there. Does not say the period began no earlier.
    OBSERVED = "observed"
    #: A start placed by the cadence of closing days alone, or an end placed by the day the
    #: document was received. The weakest: a caller must be able to tell it from the others.
    INFERRED = "inferred"


class Bound(StrEnum):
    """Which day ended a partial statement's coverage."""

    PRODUCED = "produced"
    RECEIVED = "received"
    TODAY = "today"


class Contradiction(StrEnum):
    """A statement that disagrees with itself or its neighbour, reported as data."""

    #: A listed row is dated before the period the statement states it begins.
    ROW_BEFORE_START = "row-before-start"
    #: A listed row is dated after the statement's own closing day.
    ROW_AFTER_END = "row-after-end"
    #: The periods meet (by printed dates) but the opening balance is not the previous
    #: closing balance: money moved that neither statement lists, on no day either names.
    BALANCES_BREAK = "balances-break"
    #: The statement states a start on or before the day the statement before it closed.
    OVERLAPS_PREVIOUS = "overlaps-previous"


class HoleReason(StrEnum):
    #: The later statement prints a start after the earlier one closed. Stated.
    STARTS_AFTER = "starts-after"
    #: The later statement's opening balance is not the earlier closing balance. Stated.
    BALANCES_DIFFER = "balances-differ"
    #: Other sources hold rows in the days between that no statement lists. Stated.
    UNLISTED_ROWS = "unlisted-rows"
    #: The balances meet but the closing days are two or more periods apart: a statement in
    #: between would have to net to nil. Inferred.
    BALANCES_MEET_NET_NIL = "balances-meet-net-nil"
    #: No balance to compare and the closing days are far apart. Inferred.
    SPACING = "spacing"


class OtherSources(StrEnum):
    """What the rows other sources hold say about two statements that appear to meet."""

    #: Another source holds rows across the days and none is unlisted: support for meeting,
    #: and still not proof.
    COVERED_NONE_UNLISTED = "covered-none-unlisted"
    #: No other source covers those days, so the absence of an unlisted row says nothing.
    NOT_COVERED = "not-covered"


@dataclass(frozen=True)
class Span:
    """The days one held statement covers, and how each end is known."""

    account: str
    closing: date
    source: str
    #: The first day covered; None where nothing places it (no printed start, no rows, no
    #: earlier statement or cadence to place it by).
    first: date | None
    first_known: Known
    #: The last day covered: the closing day, or for a partial statement the day it was
    #: produced or received.
    last: date
    last_known: Known
    #: False for a partial statement; `bounded_by` then says which day ended it.
    complete: bool = True
    bounded_by: Bound | None = None
    contradictions: tuple[Contradiction, ...] = ()
    #: For a start known as BALANCES_MEET, or placed by spacing, what other sources say.
    others: OtherSources | None = None


@dataclass(frozen=True)
class Hole:
    """Days between two held statements that no statement is known to cover."""

    account: str
    first_day: date
    last_day: date
    #: STATED where held evidence proves the hole (`HoleReason.STARTS_AFTER`, `BALANCES_DIFFER`,
    #: `UNLISTED_ROWS`); INFERRED where only the closing days' spacing says so. Never other.
    known: Known
    reason: HoleReason
    earlier_closing: date
    later_closing: date
    source: str = ""
    #: How many statements are probably missing, and their likely closing days: an inference
    #: from the cadence, None where the hole is a stated span with no cadence to count by.
    probably: int | None = None
    closings: tuple[date, ...] = ()
    #: Rows other sources hold in the hole that no statement lists.
    unlisted_rows: int = 0
    #: How `last_day` is known, apart from `known`, which is about the hole's existence and its
    #: first day. A hole proven by balances that do not meet starts on the day after the earlier
    #: close (a fact); where it ends is INFERRED when placed at the closing day the missing
    #: statement is expected to have had (`closings[-1]`), or OBSERVED when it is the day before
    #: the later statement's first listed row, which is as far as nothing is known to cover.
    last_known: Known = Known.OBSERVED


@dataclass(frozen=True)
class NextStatement:
    """When the newest statement's successor is expected, an inference from the cadence."""

    cadence: int
    #: The day the next statement is expected to close, and the day it is expected to be
    #: available. Both inferred.
    expected_close: date
    expected_available: date
    #: Days between a statement's closing and its receipt, the shortest held; None where no kept
    #: time says (the available day is then the closing day).
    lag_days: int | None
    #: The closing days that have passed and are available by `today`: the statements due now.
    due: tuple[date, ...]


@dataclass(frozen=True)
class AccountSpans:
    account: str
    statements: tuple[Span, ...]
    holes: tuple[Hole, ...]
    cadence: int | None
    next: NextStatement | None


@dataclass(frozen=True)
class RowEvidence:
    """What other sources hold for each account, to be set against the statements.

    Built once from the store's rows by sighting (`from_sightings`), free of any date.
    """

    #: Per account, the sorted days of rows no statement lists.
    unlisted: Mapping[str, tuple[date, ...]] = field(default_factory=dict)
    #: Per (account, source other than a statement), the first and last day it holds rows for.
    covered: Mapping[tuple[str, str], tuple[date, date]] = field(default_factory=dict)

    @classmethod
    def from_sightings(cls, sightings: Iterable[Transaction]) -> RowEvidence:
        """The evidence in the rows as `Store.transactions_by_sighting` gives them.

        A row is LISTED when any statement source has sighted it; the sighting view is the one
        place that knows two sources saw one payment, so a feed row that a statement also lists
        is never counted as unlisted. Only booked rows count: a pending one is not yet owed to
        a statement.
        """
        seen: dict[str, list[Transaction]] = defaultdict(list)
        held = list(sightings)
        for item in held:
            seen[item.entity_id].append(item)
        unlisted: dict[str, list[date]] = defaultdict(list)
        for items in seen.values():
            if any(item.source in STATEMENT_SOURCES for item in items):
                continue
            first = items[0]
            if first.status is TransactionStatus.BOOKED:
                unlisted[first.account_id].append(min(item.value_date for item in items))
        spans = {
            (c.account_id, c.source): (c.earliest, c.latest)
            for c in coverage(held)
            if c.source not in STATEMENT_SOURCES
        }
        return cls({k: tuple(sorted(v)) for k, v in unlisted.items()}, spans)

    def unlisted_between(self, account: str, first: date, last: date) -> int:
        days = self.unlisted.get(account, ())
        return max(bisect_right(days, last) - bisect_left(days, first), 0)

    def covers(self, account: str, first: date, last: date) -> bool:
        """Whether one other source holds rows from on or before `first` to on or after `last`."""
        return any(
            ref == account and low <= first and last <= high
            for (ref, _), (low, high) in self.covered.items()
        )


def cadence_of(closings: Sequence[date]) -> int | None:
    """The usual days between statements, where there are enough to say and it is monthly.

    The lower median of the intervals, so one missing statement (a double interval) does not
    lift it. None for fewer than `CADENCE_NEEDS` statements and for any rhythm that is not a
    month: a quarterly or irregular account is left without an inference.
    """
    ordered = sorted(set(closings))
    if len(ordered) < CADENCE_NEEDS:
        return None
    intervals = sorted((later - earlier).days for earlier, later in pairwise(ordered))
    usual = intervals[(len(intervals) - 1) // 2]
    low, high = MONTHLY_DAYS
    return usual if low <= usual <= high else None


def add_months(day: date, months: int) -> date:
    """`day` that many months on, kept to the month's end where the day does not exist."""
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def months_between(earlier: date, later: date) -> int:
    """The whole number of calendar months from `earlier` to `later`, nearest to the day.

    The one count both `Hole.probably` and `Hole.closings` come from, so they cannot disagree:
    a statement closing on the 10th and the next on the 12th are one month apart, not 1.07.
    """
    return max(range(1, 61), key=lambda k: -abs((add_months(earlier, k) - later).days), default=1)


def due_closings(last: date, today: date, *, lag_days: int = 0, limit: int = 24) -> list[date]:
    """The monthly closing days after `last` whose statement is available by `today`.

    A closing day counts once the lag has passed too: a statement that closed yesterday and
    takes four days to appear is not yet a statement to go and fetch.
    """
    found = []
    for step in range(1, limit + 1):
        day = add_months(last, step)
        if day + timedelta(days=lag_days) > today:
            break
        found.append(day)
    return found


def _lag_days(statements: Sequence[StatementPeriod]) -> int | None:
    lags = [
        (item.received - item.closing).days
        for item in statements
        if item.received is not None
        and item.closing <= item.received <= item.closing + timedelta(days=MAX_PLAUSIBLE_LAG_DAYS)
    ]
    return min(lags) if lags else None


def next_statement(statements: Sequence[StatementPeriod], today: date) -> NextStatement | None:
    """When the newest of `statements` is followed by another; see the module's account."""
    ordered = sorted({item.closing: item for item in statements}.values(), key=lambda s: s.closing)
    cadence = cadence_of([item.closing for item in ordered])
    if cadence is None:
        return None
    lag = _lag_days(ordered)
    last = ordered[-1].closing
    close = add_months(last, 1)
    return NextStatement(
        cadence,
        close,
        close + timedelta(days=lag or 0),
        lag,
        tuple(due_closings(last, today, lag_days=lag or 0)),
    )


def _one_per_closing(statements: Iterable[StatementPeriod]) -> list[StatementPeriod]:
    """Two held files with one closing day are one statement: keep the one that says most."""

    def says(item: StatementPeriod) -> tuple[bool, bool]:
        return (item.opens is not None, item.opening_minor is not None)

    best: dict[date, StatementPeriod] = {}
    for item in statements:
        held = best.get(item.closing)
        if held is None or says(item) > says(held):
            best[item.closing] = item
    return [best[day] for day in sorted(best)]


def _end_of(item: StatementPeriod, today: date) -> tuple[date, Known, bool, Bound | None]:
    """The last day covered, how that is known, whether the statement is whole, and why not."""
    if item.produced is not None:
        bound, how, known = item.produced, Bound.PRODUCED, Known.STATED
    elif item.received is not None:
        bound, how, known = item.received, Bound.RECEIVED, Known.INFERRED
    else:
        bound, how, known = today, Bound.TODAY, Known.INFERRED
    if item.closing > bound:
        return bound, known, False, how
    return item.closing, Known.STATED, True, None


def _missing(earlier: date, later: date) -> tuple[int, tuple[date, ...]]:
    count = max(months_between(earlier, later) - 1, 1)
    return count, tuple(add_months(earlier, step) for step in range(1, count + 1))


def describe_account(
    statements: Sequence[StatementPeriod],
    today: date,
    rows: RowEvidence | None = None,
) -> AccountSpans:
    """Spans, holes and the next statement of one account's held statements.

    `statements` may be given in any order; two with one closing day are one. `rows` is what
    other sources hold; without it nothing is counted as unlisted and `Span.others` says
    `NOT_COVERED`.
    """
    held = _one_per_closing(statements)
    evidence = rows or RowEvidence()
    cadence = cadence_of([item.closing for item in held])
    spans: list[Span] = []
    holes: list[Hole] = []
    previous: StatementPeriod | None = None
    for item in held:
        last, last_known, complete, bounded_by = _end_of(item, today)
        first, first_known, others, contradictions, found = _start_of(
            item, previous, cadence, evidence
        )
        holes.extend(found)
        if item.opens is not None and item.first_row is not None and item.first_row < item.opens:
            contradictions = (*contradictions, Contradiction.ROW_BEFORE_START)
        if item.last_row is not None and item.last_row > item.closing:
            contradictions = (*contradictions, Contradiction.ROW_AFTER_END)
        spans.append(
            Span(
                item.account_ref,
                item.closing,
                item.source,
                first,
                first_known,
                last,
                last_known,
                complete,
                bounded_by,
                contradictions,
                others,
            )
        )
        previous = item
    account = held[0].account_ref if held else ""
    return AccountSpans(account, tuple(spans), tuple(holes), cadence, next_statement(held, today))


@dataclass(frozen=True)
class _Reach:
    """Where a hole before a statement ends, and from when that statement covers."""

    last: date
    last_known: Known
    first: date | None
    first_known: Known
    probably: int | None
    closings: tuple[date, ...]


def _reach(
    item: StatementPeriod,
    previous: StatementPeriod,
    cadence: int | None,
    one_period: bool,
) -> _Reach:
    """The hole between `previous` and `item`, which prints no start of its own.

    Where the closing days are far apart the missing statement's period runs from the day after
    the earlier close to ITS OWN close, which is not known: it is placed by the calendar-month
    steps of the cadence (`closings`), and `item` is taken to cover from the day after. Where
    `item`'s first listed row is earlier than that, the row wins and the hole ends the day
    before it: nothing is known to cover only the days before the first row. Where the closing
    days are one period apart (or no cadence is held) the hole is the days up to the first
    row. Whichever, the day the hole starts is a fact and the day it ends is not, and
    `last_known` says so.
    """
    ceiling = item.first_row - timedelta(days=1) if item.first_row else None
    if cadence is not None and not one_period:
        count, closings = _missing(previous.closing, item.closing)
        guess = closings[-1]
        if ceiling is not None and guess >= ceiling:
            return _Reach(ceiling, Known.OBSERVED, item.first_row, Known.OBSERVED, count, closings)
        return _Reach(
            guess, Known.INFERRED, guess + timedelta(days=1), Known.INFERRED, count, closings
        )
    last = ceiling if ceiling is not None else item.closing
    first = item.first_row
    return _Reach(
        last, Known.OBSERVED, first, Known.OBSERVED if first else Known.INFERRED, None, ()
    )


def _start_of(
    item: StatementPeriod,
    previous: StatementPeriod | None,
    cadence: int | None,
    rows: RowEvidence,
) -> tuple[date | None, Known, OtherSources | None, tuple[Contradiction, ...], list[Hole]]:
    """The first day `item` covers, how that is known, and the holes before it."""
    account = item.account_ref
    after = None if previous is None else previous.closing + timedelta(days=1)
    contradictions: tuple[Contradiction, ...] = ()

    earlier = item.closing if previous is None else previous.closing

    def hole(
        first_day: date,
        last_day: date,
        known: Known,
        reason: HoleReason,
        *,
        last_known: Known = Known.OBSERVED,
        probably: int | None = None,
        closings: tuple[date, ...] = (),
        unlisted_rows: int = 0,
    ) -> Hole:
        return Hole(
            account,
            first_day,
            last_day,
            known,
            reason,
            earlier,
            item.closing,
            item.source or (previous.source if previous is not None else ""),
            probably,
            closings,
            unlisted_rows,
            last_known,
        )

    observed = (item.first_row, Known.OBSERVED) if item.first_row else (None, Known.INFERRED)
    differ = (
        previous is not None
        and item.opening_minor is not None
        and previous.closing_minor is not None
        and item.opening_minor != previous.closing_minor
    )
    equal = (
        previous is not None
        and item.opening_minor is not None
        and previous.closing_minor is not None
        and item.opening_minor == previous.closing_minor
    )

    if item.opens is not None:
        found: list[Hole] = []
        if after is not None:
            if item.opens > after:
                found.append(
                    hole(
                        after, item.opens - timedelta(days=1), Known.STATED, HoleReason.STARTS_AFTER
                    )
                )
            elif item.opens < after:
                contradictions = (Contradiction.OVERLAPS_PREVIOUS,)
            elif differ:
                contradictions = (Contradiction.BALANCES_BREAK,)
        return item.opens, Known.STATED, None, contradictions, found

    if previous is None or after is None:
        if item.first_row is not None:
            return item.first_row, Known.OBSERVED, None, (), []
        placed = None if cadence is None else item.closing - timedelta(days=cadence - 1)
        return placed, Known.INFERRED, None, (), []

    window_end = item.first_row - timedelta(days=1) if item.first_row else item.closing
    interval = (item.closing - previous.closing).days
    one_period = cadence is not None and interval <= cadence * HOLE_CADENCES

    if differ:
        if item.first_row is not None and item.first_row <= after:
            return observed[0], observed[1], None, (Contradiction.BALANCES_BREAK,), []
        reach = _reach(item, previous, cadence, one_period)
        return (
            reach.first,
            reach.first_known,
            None,
            (),
            [
                hole(
                    after,
                    reach.last,
                    Known.STATED,
                    HoleReason.BALANCES_DIFFER,
                    last_known=reach.last_known,
                    probably=reach.probably,
                    closings=reach.closings,
                    unlisted_rows=rows.unlisted_between(account, after, reach.last),
                )
            ],
        )

    if cadence is None:
        return observed[0], observed[1], None, (), []

    if one_period:
        if item.first_row is not None and item.first_row <= after:
            return observed[0], observed[1], None, (), []
        unlisted = rows.unlisted_between(account, after, window_end)
        if unlisted:
            return (
                observed[0],
                observed[1],
                None,
                (),
                [
                    hole(
                        after,
                        window_end,
                        Known.STATED,
                        HoleReason.UNLISTED_ROWS,
                        unlisted_rows=unlisted,
                    )
                ],
            )
        others = (
            OtherSources.COVERED_NONE_UNLISTED
            if rows.covers(account, after, window_end)
            else OtherSources.NOT_COVERED
        )
        return after, Known.BALANCES_MEET if equal else Known.INFERRED, others, (), []

    if item.first_row is not None and item.first_row <= after:
        return observed[0], observed[1], None, (), []
    reach = _reach(item, previous, cadence, one_period)
    unlisted = rows.unlisted_between(account, after, reach.last)
    reason = HoleReason.BALANCES_MEET_NET_NIL if equal else HoleReason.SPACING
    return (
        reach.first,
        reach.first_known,
        None,
        (),
        [
            hole(
                after,
                reach.last,
                Known.STATED if unlisted else Known.INFERRED,
                HoleReason.UNLISTED_ROWS if unlisted else reason,
                last_known=reach.last_known,
                probably=reach.probably,
                closings=reach.closings,
                unlisted_rows=unlisted,
            )
        ],
    )


def describe_start(span: Span) -> str:
    """How a statement's first day is known, as a sentence a person can check, never naming
    the internal basis and never saying that nothing is missing."""
    if span.first is None:
        return "Nothing says which day this statement begins on."
    day = span.first.isoformat()
    if span.first_known is Known.STATED:
        return f"The statement says it covers the period from {day}."
    if span.first_known is Known.BALANCES_MEET:
        covered = (
            " Another source holds rows across those days and none is missing from the "
            "statements, which supports that without proving it."
            if span.others is OtherSources.COVERED_NONE_UNLISTED
            else " No other source covers those days, so nothing else checks it."
        )
        return (
            f"Its opening balance is the closing balance of the statement before, and the two "
            f"close about a month apart, so it is taken to begin on {day}. That does not rule "
            f"out a short statement between them whose movements net to nil.{covered}"
        )
    if span.first_known is Known.OBSERVED:
        return f"Its first listed payment is on {day}, so it covers at least from then."
    return f"It is taken to begin about {day}, from how regularly statements close."


def statement_spans(
    store: Store,
    today: date,
    *,
    periods: Sequence[StatementPeriod] | None = None,
    rows: RowEvidence | None = None,
) -> dict[str, AccountSpans]:
    """The spans of every account that holds a trusted statement, from the store.

    `periods` and `rows` are the caller's held copies where it has them (reading either walks
    the store). A statement whose kept reading predates a field its parser now reads gives the
    weaker answer its evidence supports: `keep_statement_readings` reads it again.
    """
    held = list(statement_periods(store) if periods is None else periods)
    evidence = (
        RowEvidence.from_sightings(store.transactions_by_sighting()) if rows is None else rows
    )
    by_account: dict[str, list[StatementPeriod]] = defaultdict(list)
    for item in held:
        by_account[item.account_ref].append(item)
    return {
        ref: describe_account(items, today, evidence) for ref, items in sorted(by_account.items())
    }
