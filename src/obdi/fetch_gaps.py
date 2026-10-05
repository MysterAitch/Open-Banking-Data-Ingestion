"""Which statements and exports the owner still has to fetch, worked out from what is held.

THE QUESTION is "which file do I fetch next, for which account, covering which dates", asked
at a bank's download page. Every fact that answers it was already worked out somewhere: the
newest known balance and what is tested (`agreement`, through `standing_data`), the statements
held and the period each states (`statement_terms`), the rows each source holds
(`coverage`), the review flags a statement would settle (`review_report`). This module asks
those functions and states the answer as DATA, with no knowledge of any page: a `FetchGap` is an
account, a kind, the first and last day, whether the gap is a stated fact or an inference, the
source expected to read the file, and why it matters.

FACT AGAINST INFERENCE is carried in every gap, not left to wording. A gap is STATED where the
store holds the evidence for it: rows after the newest known balance, a statement whose own
period opens after the day the previous one closed, rows before the first known balance, a month
an export lacks while another source holds rows for it. It is INFERRED where the evidence is
the rhythm of the statements held: a hole between two statements that state only a closing day,
and the closing days of the statements probably waiting. A cadence is inferred only from three
or more statements whose usual gap is a month (`cadence_of`); below that nothing is inferred.

ONE RULE, ONE PLACE. "A statement is overdue" is `overview.statement_awaited`, which Today's
"rows after their last known balance" item reads too, so the two cannot name different accounts.
Unreadable months, the standing of an account, and the review flags' gaps are likewise called, not
restated.

THE HOLE'S THRESHOLD is one and a half cadences, not two. Two statements a month apart close
about 30 days apart, and with one between them missing the interval is 59 to 62 days; "twice the
cadence" read as 62 days would miss a hole of 61, the commonest case. The number of statements
missing is the interval over the cadence, rounded, less one.

WHAT IS NOT A GAP: an account archived by its closing date, an account that holds no rows, a
balance-only account (its balances are stated by hand, so there is no file to fetch), and a
trailing stretch shorter than a statement period (the next statement is not out yet, and the
report says when it is expected).
"""

from __future__ import annotations

import calendar
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from enum import StrEnum
from itertools import pairwise

from .accounts import is_balance_only
from .agreement import NONE, UNTESTED
from .coverage import coverage, gaps
from .fetch_marks import MarkSet, OutOfScope, SetAside, partition
from .namespaces import FILE_SOURCES
from .overview import first_row_dates, held_by_account, statement_awaited
from .parsers.pdf_statements import PDF_PARSERS
from .review_flags import settle_evidence
from .review_report import BalanceGap, assess_flags
from .standing_data import AccountStanding
from .statement_terms import StatementPeriod, statement_periods
from .store import Store

#: The sources whose rows are a statement's own.
STATEMENT_SOURCES = frozenset(parser.source for parser in PDF_PARSERS) | {"statement"}

#: The sources a person exports by hand from a bank's site: the file sources that are not
#: statements.
EXPORT_SOURCES = FILE_SOURCES - STATEMENT_SOURCES

#: The days between statements that make a cadence "monthly": a statement closes on about the
#: same day each month, and a month is 28 to 31 days with a day of slack.
MONTHLY_DAYS = (27, 32)

#: The fewest statements a cadence is inferred from. Two give one interval, which is a
#: coincidence as easily as a rhythm.
CADENCE_NEEDS = 3

#: How many cadences between two closing days make a hole. See the module's account.
HOLE_CADENCES = 1.5

#: How long an export may trail the other sources before it is called stopped. An export is
#: fetched alongside a monthly statement, so a shorter stretch is not yet worth a trip.
EXPORT_QUIET_DAYS = 31


class GapKind(StrEnum):
    NEWER_STATEMENT = "newer-statement"
    HOLE_BETWEEN = "hole-between"
    EXPORT_STOPS = "export-stops"
    EXPORT_MONTHS = "export-months"
    NO_BALANCE = "no-balance"
    AUTOMATIC_ONLY = "automatic-only"
    ONE_BALANCE = "one-balance"
    NOTHING_BEFORE = "nothing-before"
    FLAG_SETTLE = "flag-settle"


#: How pressing each kind is, most first: a missing statement or export stops verification and
#: hides recent rows; an untested early stretch is the least urgent.
_URGENCY = {kind: rank for rank, kind in enumerate(GapKind)}


class Basis(StrEnum):
    STATED = "stated"
    INFERRED = "inferred"


@dataclass(frozen=True)
class FetchGap:
    """One thing to fetch, for one account, covering the days `first_day` to `last_day`."""

    account: str
    kind: GapKind
    first_day: date
    last_day: date
    #: Whether the evidence for the gap is held (STATED) or is the rhythm of the statements
    #: held (INFERRED). For a newer statement the gap is stated and only `probably` is not.
    basis: Basis
    #: The source the file is expected to be read by, where the statements already held say; "".
    source: str
    #: Why the file matters, as a sentence with no account name and no figure.
    why: str
    #: How many statements are probably waiting, and their likely closing days: an inference
    #: from the cadence of the statements held, None where none can be drawn.
    probably: int | None = None
    closings: tuple[date, ...] = ()
    #: The newest day the account holds a row for, for a gap that runs on to it.
    rows_to: date | None = None
    #: The review flag's gap kind ("before", "after", "single"), for a flag a statement settles.
    flag: str = ""
    #: The days of the gap as found, where `fetch_marks.partition` cut part of it away.
    split_from: tuple[date, date] | None = None
    #: A sentence for a gap that returned to the list because a known gap's day to look again on
    #: came; "" otherwise.
    reminder: str = ""


@dataclass(frozen=True)
class AccountOutlook:
    """One account: what is to fetch for it, and where it stands where nothing is."""

    account: str
    gaps: tuple[FetchGap, ...]
    #: A balance-only account's balances are stated by hand; there is no file to fetch.
    balance_only: bool = False
    #: The statements held, and the expected closing day of the next, inferred from their
    #: cadence; None where none can be drawn or a statement is already overdue.
    statements: int = 0
    next_expected: date | None = None
    #: The source that read the newest statement held, "" where none has.
    source: str = ""


@dataclass(frozen=True)
class FetchReport:
    #: Accounts that need something first, most urgent first; the rest after, by reference.
    accounts: tuple[AccountOutlook, ...]
    today: date
    #: What the owner's decisions took out of the lists above, and the decisions themselves.
    marks: MarkSet = MarkSet()
    set_aside: tuple[SetAside, ...] = ()
    out_of_scope: tuple[OutOfScope, ...] = ()

    @property
    def gaps(self) -> tuple[FetchGap, ...]:
        return tuple(gap for outlook in self.accounts for gap in outlook.gaps)

    @property
    def needing(self) -> tuple[AccountOutlook, ...]:
        return tuple(a for a in self.accounts if a.gaps)

    @property
    def next_expected(self) -> date | None:
        """The earliest day a statement is expected among the accounts that need nothing."""
        days = [a.next_expected for a in self.accounts if not a.gaps and a.next_expected]
        return min(days) if days else None


@dataclass(frozen=True)
class FlagNeed:
    """A review flag a statement would settle, and the known balance it lacks."""

    account: str
    kind: str
    day: date


@dataclass(frozen=True)
class FetchEvidence:
    """What the store holds that the gaps are worked out from. Free of any date, so it can be
    kept for as long as the store is unchanged and `today` applied afresh to each request."""

    #: Per account, the trusted statements by closing day, one for each closing day.
    statements: Mapping[str, tuple[StatementPeriod, ...]]
    #: Per account and export source, the first and last day it holds rows for.
    export_spans: Mapping[str, Mapping[str, tuple[date, date]]]
    #: Per account, the newest day any source other than the named one holds a row for.
    latest_by_source: Mapping[str, Mapping[str, date]]
    #: Per (account, export source), the months it lacks that another source holds.
    export_months: Mapping[tuple[str, str], tuple[str, ...]]
    #: Per account, every source that has sighted a row of it.
    sources: Mapping[str, frozenset[str]]
    first_row: Mapping[str, date]
    rows: Mapping[str, int]
    flags: tuple[FlagNeed, ...]
    closed: Mapping[str, date | None]
    balance_only: frozenset[str]


def gather_evidence(store: Store) -> FetchEvidence:
    """Read everything the gaps need, once. Costs a walk of the store: keep the result."""
    statements: dict[str, dict[date, StatementPeriod]] = {}
    for period in statement_periods(store):
        slot = statements.setdefault(period.account_ref, {})
        # Two held files with one closing day are one statement; keep the one that states a
        # period over one that does not.
        if period.closing not in slot or (period.opens and not slot[period.closing].opens):
            slot[period.closing] = period
    held_sightings = store.transactions_by_sighting()
    spans: dict[str, dict[str, tuple[date, date]]] = {}
    latest: dict[str, dict[str, date]] = {}
    for item in coverage(held_sightings):
        latest.setdefault(item.account_id, {})[item.source] = item.latest
        if item.source in EXPORT_SOURCES:
            spans.setdefault(item.account_id, {})[item.source] = (item.earliest, item.latest)
    months: dict[tuple[str, str], list[str]] = {}
    for gap in gaps(held_sightings):
        if gap.contradicted and gap.source in EXPORT_SOURCES:
            months.setdefault((gap.account_id, gap.source), []).append(gap.month)
    held, sources = held_by_account(store)
    need: list[FlagNeed] = []
    for assessment in assess_flags(store).values():
        found = assessment.balance_gap
        if found is not None:
            need.append(FlagNeed(assessment.account, found.kind, found.day))
    records = store.declared_accounts()
    return FetchEvidence(
        statements={
            ref: tuple(by_day[day] for day in sorted(by_day)) for ref, by_day in statements.items()
        },
        export_spans=spans,
        latest_by_source=latest,
        export_months={key: tuple(sorted(value)) for key, value in months.items()},
        sources={ref: frozenset(found) for ref, found in sources.items()},
        first_row=first_row_dates(store),
        rows={ref: count for ref, (count, _) in held.items()},
        flags=tuple(sorted(set(need), key=lambda n: (n.account, n.day, n.kind))),
        closed={str(r.ref): r.closed for r in records},
        balance_only=frozenset(str(r.ref) for r in records if is_balance_only(r.kind)),
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


def closings_after(last: date, today: date, *, limit: int = 24) -> list[date]:
    """The monthly closing days after `last` that have already passed by `today`."""
    found = []
    for step in range(1, limit + 1):
        day = add_months(last, step)
        if day > today:
            break
        found.append(day)
    return found


def _next_after(last: date, today: date) -> date:
    step = 1
    while add_months(last, step) <= today:
        step += 1
    return add_months(last, step)


def _holes(statements: Sequence[StatementPeriod], cadence: int | None) -> list[FetchGap]:
    found: list[FetchGap] = []
    for earlier, later in pairwise(statements):
        if later.opens is not None:
            if later.opens > earlier.closing + timedelta(days=1):
                found.append(
                    FetchGap(
                        earlier.account_ref,
                        GapKind.HOLE_BETWEEN,
                        earlier.closing + timedelta(days=1),
                        later.opens - timedelta(days=1),
                        Basis.STATED,
                        later.source or earlier.source,
                        "The later statement says its period begins after the earlier one "
                        "closed, so nothing known covers the days between.",
                    )
                )
            continue
        if cadence is None:
            continue
        interval = (later.closing - earlier.closing).days
        if interval <= cadence * HOLE_CADENCES:
            continue
        missing = max(round(interval / cadence) - 1, 1)
        found.append(
            FetchGap(
                earlier.account_ref,
                GapKind.HOLE_BETWEEN,
                earlier.closing + timedelta(days=1),
                later.closing - timedelta(days=1),
                Basis.INFERRED,
                later.source or earlier.source,
                "Two statements close further apart than the statements held usually do, so "
                "one is probably missing between them.",
                probably=missing,
                closings=tuple(add_months(earlier.closing, step) for step in range(1, missing + 1)),
            )
        )
    return found


def _month_ranges(months: Sequence[str]) -> list[tuple[date, date]]:
    """Runs of consecutive months as the first and last day of each run."""
    runs: list[list[date]] = []
    for text in sorted(months):
        first = date(int(text[:4]), int(text[5:]), 1)
        if runs and add_months(runs[-1][-1], 1) == first:
            runs[-1].append(first)
        else:
            runs.append([first])
    return [
        (run[0], add_months(run[-1], 1) - timedelta(days=1)) for run in runs
    ]


def _outlook(
    ref: str, standing: AccountStanding | None, evidence: FetchEvidence, today: date
) -> AccountOutlook:
    statements = evidence.statements.get(ref, ())
    files = sorted(evidence.sources.get(ref, frozenset()) & EXPORT_SOURCES)
    source = statements[-1].source if statements else (files[0] if len(files) == 1 else "")
    if ref in evidence.balance_only:
        return AccountOutlook(ref, (), balance_only=True)
    found: list[FetchGap] = []
    closings = [s.closing for s in statements]
    cadence = cadence_of(closings)
    newest_row = None if standing is None else standing.newest_row
    first_row = evidence.first_row.get(ref)
    own = None if standing is None else standing.standing.own
    awaiting = standing is not None and statement_awaited(standing, today) is not None

    if awaiting and standing is not None and own is not None and own.known_to is not None:
        waiting = [
            day
            for day in (closings_after(closings[-1], today) if cadence and closings else [])
            if day > own.known_to
        ]
        found.append(
            FetchGap(
                ref,
                GapKind.NEWER_STATEMENT,
                own.known_to + timedelta(days=1),
                newest_row or today,
                Basis.STATED,
                source,
                "Rows are held after the newest known balance and a statement period has "
                "passed, so the rows since cannot be tested.",
                probably=len(waiting) if waiting else None,
                closings=tuple(waiting),
                rows_to=newest_row,
            )
        )
    found.extend(_holes(statements, cadence))

    for export, (_first, last) in sorted(evidence.export_spans.get(ref, {}).items()):
        others = [
            day for name, day in evidence.latest_by_source.get(ref, {}).items() if name != export
        ]
        if others and max(others) > last and (today - last).days > EXPORT_QUIET_DAYS:
            found.append(
                FetchGap(
                    ref,
                    GapKind.EXPORT_STOPS,
                    last + timedelta(days=1),
                    today,
                    Basis.STATED,
                    export,
                    "Other sources hold rows after the export's newest, so the export's "
                    "history stops short of what the account has.",
                    rows_to=max(others),
                )
            )
        for begin, end in _month_ranges(evidence.export_months.get((ref, export), ())):
            found.append(
                FetchGap(
                    ref,
                    GapKind.EXPORT_MONTHS,
                    begin,
                    end,
                    Basis.STATED,
                    export,
                    "Another source holds rows for these months and the export holds none, "
                    "so the export is probably missing them.",
                )
            )

    if own is not None and first_row is not None and newest_row is not None:
        if own.state == NONE:
            automatic_only = not (evidence.sources.get(ref, frozenset()) & FILE_SOURCES) and not (
                statements
            )
            found.append(
                FetchGap(
                    ref,
                    GapKind.AUTOMATIC_ONLY if automatic_only else GapKind.NO_BALANCE,
                    first_row,
                    newest_row,
                    Basis.STATED,
                    source,
                    "No known balance is held, so nothing tests these rows. A statement, or a "
                    "balance you state, would let them be tested."
                    if not automatic_only
                    else "Only the bank's feed and the aggregator feed this account and no "
                    "known balance is held, so nothing tests its rows. A statement, or a "
                    "balance you state, would let them be tested.",
                )
            )
        elif own.known_from is not None:
            # A statement is a closing balance, but it also accounts for the rows it lists, so
            # the rows of the first statement itself are not "before" it: only rows before the
            # day it says its period begins (or its first row) are.
            first_statement = statements[0] if statements else None
            covered = (
                first_statement.covers_from
                if first_statement is not None and first_statement.closing == own.known_from
                else None
            )
            untested_to = (
                covered - timedelta(days=1) if covered is not None else own.known_from
            )
            if first_row <= untested_to and (covered is not None or first_row < own.known_from):
                found.append(
                    FetchGap(
                        ref,
                        GapKind.NOTHING_BEFORE,
                        first_row,
                        untested_to,
                        Basis.STATED,
                        source,
                        "These rows come before the first known balance, so they are worked "
                        "backwards from it and nothing tests them.",
                    )
                )
        if (
            own.state == UNTESTED
            and own.known_count == 1
            and own.known_from is not None
            and not awaiting
        ):
            found.append(
                FetchGap(
                    ref,
                    GapKind.ONE_BALANCE,
                    first_row,
                    max(newest_row, own.known_from),
                    Basis.STATED,
                    source,
                    "Only one known balance is held, so it sets the opening and nothing "
                    "tests the rows yet.",
                )
            )

    for need in evidence.flags:
        if need.account == ref:
            found.append(
                FetchGap(
                    ref,
                    GapKind.FLAG_SETTLE,
                    need.day,
                    need.day,
                    Basis.STATED,
                    source,
                    settle_evidence_text(need),
                    flag=need.kind,
                )
            )

    found.sort(key=lambda g: (_URGENCY[g.kind], g.first_day))
    next_expected = None
    if not found and cadence is not None and closings:
        next_expected = _next_after(closings[-1], today)
    return AccountOutlook(
        ref,
        tuple(found),
        statements=len(statements),
        next_expected=next_expected,
        source=source,
    )


def settle_evidence_text(need: FlagNeed) -> str:
    """The review flag's own sentence for the known balance it lacks."""
    (found,) = settle_evidence(BalanceGap(need.kind, need.day))
    return str(found.sentence)


def fetch_report(
    evidence: FetchEvidence,
    standings: Mapping[str, AccountStanding],
    today: date,
    marks: MarkSet | None = None,
) -> FetchReport:
    """The gaps of every account that is neither archived nor empty, most urgent first.

    `marks` are the owner's decisions, applied last and by `fetch_marks.partition`, which does not
    care how the gaps were found.
    """
    refs = {ref for ref, count in evidence.rows.items() if count > 0} | evidence.balance_only
    outlooks = []
    aside: list[SetAside] = []
    outside: list[OutOfScope] = []
    for ref in sorted(refs):
        closed = evidence.closed.get(ref)
        if closed is not None and closed <= today:
            continue
        outlook = _outlook(ref, standings.get(ref), evidence, today)
        if marks is not None:
            cut = partition(ref, outlook.gaps, marks, today, statement_sources=STATEMENT_SOURCES)
            aside.extend(cut.set_aside)
            outside.extend(cut.out_of_scope)
            outlook = replace(
                outlook,
                gaps=cut.remaining,
                next_expected=_next_after_set_aside(outlook, cut.remaining, evidence, today),
            )
        outlooks.append(outlook)

    def urgency(item: AccountOutlook) -> tuple[int, int, date, str]:
        if not item.gaps:
            return (len(_URGENCY), 0, date.max, item.account)
        top = item.gaps[0]
        return (_URGENCY[top.kind], 0, top.first_day, item.account)

    return FetchReport(
        tuple(sorted(outlooks, key=urgency)),
        today,
        marks or MarkSet(),
        tuple(aside),
        tuple(outside),
    )


def _next_after_set_aside(
    outlook: AccountOutlook,
    remaining: Sequence[FetchGap],
    evidence: FetchEvidence,
    today: date,
) -> date | None:
    """The expected next statement of an account whose every gap was set aside, as one that never
    had a gap would have it."""
    if remaining or not outlook.gaps:
        return outlook.next_expected
    closings = [s.closing for s in evidence.statements.get(outlook.account, ())]
    cadence = cadence_of(closings)
    return _next_after(closings[-1], today) if cadence is not None and closings else None


def gaps_for_account(
    store: Store,
    account: str,
    today: date,
    *,
    standings: Mapping[str, AccountStanding] | None = None,
    evidence: FetchEvidence | None = None,
) -> list[FetchGap]:
    """The gaps of one account, for any page that marks them (`FetchReport` for all of them).

    `standings` and `evidence` are the caller's held copies where it has them; without
    `standings` the account's is read from its balances alone, the rule the Overview uses when
    it is given none. Without `evidence` the store is walked.
    """
    from .standing_data import standings_for

    held = evidence if evidence is not None else gather_evidence(store)
    known = (
        standings
        if standings is not None
        else standings_for(store, [account], families=None, movement=None)
    )
    for outlook in fetch_report(held, known, today).accounts:
        if outlook.account == account:
            return list(outlook.gaps)
    return []
