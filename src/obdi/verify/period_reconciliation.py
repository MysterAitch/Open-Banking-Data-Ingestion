"""Where, between two statements, the rows held stop adding up - and what the gap looks like.

A statement's closing balance is an anchor (`balance_anchors`). When the rows
the store counts between two consecutive statement anchors do not add up to the
statement's own movement, the anchor check says THAT something is wrong and
nothing about WHERE or WHAT. When another source also covers the account, the
usual suspect is the same money held twice, once from each source, dated or
grouped differently: a statement printing one total where a feed itemises, or
the reverse. This report puts that suspicion to the test, period by period,
without stating a figure.

A PERIOD is the stretch a statement covers. For the statement closing on day D
after one that closed on day P it is the days after P up to and including D,
and the statement's movement is the difference between the two closings. The
first statement has no earlier closing, so its period runs from its own first
row. A statement whose printed opening balance is not the previous closing
balance (a statement missing, or two documents disagreeing) also gets the
period its own opening and closing state, because the two movements then
differ. That INSIDE period runs from the statement's own first row to its
closing and holds the rows THAT statement lists, not the days after the
previous closing: a long statement overlapping shorter ones starts earlier than
that closing, and a window clipped to it compared part of the statement's rows
with the whole of its movement. Two held files with the same closing and the
same rows are one statement (`statement_membership`), so a duplicate adds no
zero-length period.

Which period a row is in is `statement_membership`'s decision, the same one the
anchor checks use: the statement that lists a row places it, and only a row no
statement lists is placed by its date.

SURPLUS is what the store counts beyond the statement's movement: the rows
summed, less the movement. It carries the OPPOSITE sign to the difference
`balance_anchors` reports (anchor less prediction), so that a row held twice
shows as a surplus equal to that row.

The unmatched rows on each side are taken from `coverage.agreements`, the
pairing the cross-source page shows. This module has no matcher of its own: a
second one would be a second opinion about which rows are the same payment, and
the point of this report is to explain THAT page's leftovers.

From the surplus and the leftovers, each differing period is placed in the
first of these that holds, all compared exactly in minor units:

  same money      the statement-only and feed-only leftovers sum to the same
                  figure: the same money, described differently
  statement leftovers
                  the surplus equals the sum of the statement-only rows: the
                  store holds the statement's leftovers on top of the feed's
  feed leftovers  the surplus equals the sum of the feed-only rows
  one row         the surplus equals a single row the store counts in the
                  period, named by date and holder
  none            none of those, which is the answer worth most: the fault is
                  not a duplicate and needs a different explanation

More than one can hold at once. Where the two leftovers sum equal, the surplus
equals both sums, and the report says so rather than choosing.

A surplus that one period holds and the next period holds with the opposite
sign is a row dated on the wrong side of the statement date between them. A row
a statement lists cannot be one, whatever date it carries: that was the false
alarm a merged row's feed date raised, and membership removed it.

Masked, the report states dates, counts, and which of the above holds. Figures
and the leftover rows themselves appear only in the unmasked rendering.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from enum import StrEnum
from itertools import pairwise

from ..core import instrumentation
from ..core.models import Transaction
from ..core.money import format_amount
from ..core.plural import plural as _plural
from ..ingest.parsers.pdf_statements import PDF_PARSERS
from ..ingest.statement_membership import ListedStatement, Membership, statement_membership
from ..ingest.statement_terms import StatementBalance, held_statement_readings, statement_balances
from ..ingest.store import Store
from .balance_anchors import STATEMENT, _counts_toward
from .coverage import Agreement, agreements
from .same_money_outcome import AccountOutcome, dated_list

#: The sources whose rows are a statement's own, as opposed to a feed's.
STATEMENT_SOURCES = frozenset(parser.source for parser in PDF_PARSERS)

#: The rebuild phase the same-money pass runs under. Its steps record as
#: sub-phases named `<this>/<step>`, which is how the Admin page finds them.
SAME_MONEY_PHASE = "same-money-fold"

STATEMENT_SIDE = "statement"
FEED_SIDE = "feed"

class Locus(StrEnum):
    """Which explanation of a differing period holds."""

    SAME_MONEY = "same-money"
    STATEMENT_ON_TOP = "statement-on-top"
    FEED_ONLY_SUM = "feed-only-sum"
    SINGLE_ROW = "single-row"
    NONE = "none"


class PeriodKind(StrEnum):
    #: The first statement held: no earlier closing, so the period starts at its first row.
    FIRST = "first"
    #: Between two consecutive closings.
    BETWEEN = "between"
    #: A statement whose own opening is not the previous closing.
    INSIDE = "inside"


#: The excuse of a leftover that is a proven leg of an internal transfer.
TRANSFER_EXCUSE = "a proven internal transfer"
#: What an excuse for a row matched to a sibling account begins with; the account follows.
SIBLING_EXCUSE = "matched to a row filed under "


@dataclass(frozen=True)
class Leftover:
    """A row one side holds that the pairing did not match to the other side."""

    side: str
    source: str
    row_date: date
    amount_minor: int
    description: str
    #: Why the cross-source page already excuses it, or "" when it does not.
    excuse: str = ""
    #: The stored row, or "" for a sibling-account match, which names none.
    entity_id: str = ""


@dataclass(frozen=True)
class HeldRow:
    """A row the store counts, by date and by who holds it."""

    row_date: date
    holders: tuple[str, ...]
    amount_minor: int
    description: str
    #: STATEMENT_SIDE or FEED_SIDE when this row is itself one of the period's
    #: leftovers, "" when it is not (both sources hold it, or the pairing
    #: matched it).
    leftover: str = ""


@dataclass(frozen=True)
class Period:
    kind: PeriodKind
    #: The first and last day counted, both inclusive.
    first_day: date
    last_day: date
    movement_minor: int
    held_minor: int
    held_rows: int
    #: The other source the leftovers were taken against, or "" when none.
    feed: str
    #: Whether every row counted in the period lies inside the span the two
    #: sources were paired over. False where one source alone holds some of
    #: them, so the leftovers listed are only part.
    fully_paired: bool
    statement_only: tuple[Leftover, ...]
    feed_only: tuple[Leftover, ...]
    single_rows: tuple[HeldRow, ...]
    #: The last day of the neighbouring period whose surplus is this one's
    #: exact opposite, or None.
    cancelled_by_next: date | None = None
    cancels_previous: date | None = None
    #: Feed rows `same_money_fold` folded as the same money as this statement's
    #: leftover rows, and what they sum to. They no longer count, so the period's
    #: held rows already exclude them.
    folded_feed_rows: int = 0
    folded_minor: int = 0
    #: The statement's rows the folded ones are the same money as: its leftovers,
    #: counting those dated beyond the span the pairing compared. With the feed's
    #: row folded away the feed's latest row can fall before the statement's own,
    #: and the pairing's span shrinks past them.
    folded_statement_rows: int = 0

    @property
    def leftovers_unequal(self) -> bool:
        """Both sides hold leftovers and they are not the same money."""
        return bool(
            self.statement_only
            and self.feed_only
            and self.statement_only_minor != self.feed_only_minor
        )

    @property
    def surplus_minor(self) -> int:
        return self.held_minor - self.movement_minor

    @property
    def agrees(self) -> bool:
        return self.surplus_minor == 0

    @property
    def statement_only_minor(self) -> int:
        return sum(row.amount_minor for row in self.statement_only)

    @property
    def feed_only_minor(self) -> int:
        return sum(row.amount_minor for row in self.feed_only)

    @property
    def loci(self) -> tuple[Locus, ...]:
        """Every explanation that holds. Empty for a period that agrees."""
        if self.agrees:
            return ()
        found: list[Locus] = []
        if self.statement_only and self.feed_only and (
            self.statement_only_minor == self.feed_only_minor
        ):
            found.append(Locus.SAME_MONEY)
        if self.statement_only and self.surplus_minor == self.statement_only_minor:
            found.append(Locus.STATEMENT_ON_TOP)
        if self.feed_only and self.surplus_minor == self.feed_only_minor:
            found.append(Locus.FEED_ONLY_SUM)
        if self.single_rows:
            found.append(Locus.SINGLE_ROW)
        return tuple(found) or (Locus.NONE,)

    @property
    def cancelled(self) -> bool:
        return self.cancelled_by_next is not None or self.cancels_previous is not None


@dataclass(frozen=True)
class AccountPeriods:
    account: str
    statements: int
    periods: tuple[Period, ...] = ()
    #: Other sources holding rows for the account.
    feeds: tuple[str, ...] = ()
    #: Why nothing could be tested, or "".
    withheld: str = ""
    #: What the same-money rule did at each closing (`same_money_fold`), as
    #: sentences, for an account a feed is paired against.
    same_money: tuple[str, ...] = ()


@dataclass(frozen=True)
class PeriodReport:
    accounts: tuple[AccountPeriods, ...]

    def describe(self, *, masked: bool = True, unmask_hint: str = "") -> str:
        held = len(self.accounts)
        lines = [f"{_plural(held, 'account')} with a held statement"]
        if masked:
            lines.append(
                "  Masked: dates, counts, and which explanation holds only"
                + (f" - {unmask_hint}" if unmask_hint else "")
            )
        if not self.accounts:
            lines.append("  No statement is held for any account, so there is nothing to test.")
        for item in self.accounts:
            lines.extend(_account_lines(item, masked=masked))
        return "\n".join(lines)


def _leftover_dates(rows: Iterable[Leftover]) -> str:
    """Each leftover's date and the source that holds it, earliest first. Never
    an amount or a description: dates and sources are not private."""
    return dated_list(
        f"{row.row_date} ({row.source}{', excused: ' + row.excuse if row.excuse else ''})"
        for row in sorted(rows, key=lambda r: (r.row_date, r.source, r.excuse))
    )


def _span(period: Period) -> str:
    if period.first_day == period.last_day:
        return str(period.last_day)
    return f"{period.first_day} to {period.last_day}"


def _kind_phrase(period: Period) -> str:
    if period.kind is PeriodKind.FIRST:
        return "the first statement held, from its own first transaction"
    if period.kind is PeriodKind.BETWEEN:
        return "between this statement's closing balance and the previous one's"
    return "inside this statement, from its own opening balance to its closing one"


def _leftover_clause(row: HeldRow) -> str:
    """Whether the transaction a difference equals is itself one of the period's
    leftovers, which decides whether the difference is a transaction only one side
    holds or one both sides hold."""
    if row.leftover == FEED_SIDE:
        return " (one of this period's feed-only leftovers)"
    if row.leftover == STATEMENT_SIDE:
        return " (one of this period's statement-only leftovers)"
    if len(row.holders) > 1:
        return " (a transaction both sources hold, so not a leftover)"
    return " (not one of this period's leftovers)"


def _locus_sentence(period: Period, locus: Locus) -> str:
    if locus is Locus.SAME_MONEY:
        return (
            "The statement-only and feed-only transactions sum to the same figure: the "
            "leftovers are the same money, described differently."
        )
    if locus is Locus.STATEMENT_ON_TOP:
        return (
            "The difference equals the sum of the statement-only transactions: the store "
            "holds the statement's leftovers on top of the feed's."
        )
    if locus is Locus.FEED_ONLY_SUM:
        return "The difference equals the sum of the feed-only transactions."
    if locus is Locus.SINGLE_ROW:
        named = "; ".join(
            f"dated {row.row_date}, held by {' and '.join(row.holders)}"
            + (_leftover_clause(row) if period.feed else "")
            for row in period.single_rows
        )
        return f"The difference equals a single transaction held in this period: {named}."
    return (
        "None of the above: the difference is not the statement's leftovers, the "
        "feed's leftovers, or any single transaction held here."
    )


def _period_lines(period: Period, *, masked: bool) -> list[str]:
    lines = [f"  Period {_span(period)} ({_kind_phrase(period)}):"]
    held = _plural(period.held_rows, "transaction")
    if period.agrees:
        lines.append(f"    The {held} the store counts add up to the statement's movement.")
    else:
        lines.append(f"    The {held} the store counts do not add up to the statement's movement.")
    if not period.feed:
        lines.append(
            "    No other source is compared here: the period is tested against the "
            "statement's own transactions only."
        )
    else:
        lines.append(
            f"    Unmatched against {period.feed} in this period: "
            f"{_plural(len(period.statement_only), 'transaction')} only in the statements, "
            f"{_plural(len(period.feed_only), 'transaction')} only in the feed."
        )
        if period.statement_only:
            lines.append(
                "    Statement-only transactions are dated: "
                f"{_leftover_dates(period.statement_only)}."
            )
        if period.feed_only:
            lines.append(
                f"    Feed-only transactions are dated: {_leftover_dates(period.feed_only)}."
            )
        if not period.fully_paired:
            lines.append(
                f"    Some transactions counted here lie outside the span {period.feed} and the "
                "statements were paired over, so the unmatched transactions are counted only "
                "inside it."
            )
    if not masked:
        lines.append(
            f"    Statement movement {format_amount(period.movement_minor)}; transactions held "
            f"{format_amount(period.held_minor)}; surplus {format_amount(period.surplus_minor)}."
        )
        if period.feed:
            lines.append(
                "    Statement-only transactions sum to "
                f"{format_amount(period.statement_only_minor)}; "
                f"feed-only transactions sum to {format_amount(period.feed_only_minor)}."
            )
    if period.folded_feed_rows:
        lines.append(
            f"    {_plural(period.folded_feed_rows, 'feed transaction')} "
            f"{'was' if period.folded_feed_rows == 1 else 'were'} folded as the same money "
            f"as {_plural(period.folded_statement_rows, 'statement transaction')}: a folded "
            "transaction no longer counts and is withheld from the push (one already in Actual "
            "becomes an orphan the removal pass takes out). The statement's transactions stay "
            "counted."
        )
        if not masked:
            lines.append(
                f"    The folded transactions sum to {format_amount(period.folded_minor)}."
            )
    for locus in period.loci:
        lines.append("    " + _locus_sentence(period, locus))
    if period.leftovers_unequal and not period.agrees:
        lines.append(
            "    The statement-only and feed-only transactions sum to different figures: the "
            "leftovers are not the same money."
        )
    if period.cancelled_by_next is not None:
        lines.append(
            f"    The next period, ending {period.cancelled_by_next}, differs by exactly "
            "the opposite: a transaction is dated on the wrong side of the statement date "
            f"{period.last_day}."
        )
    if period.cancels_previous is not None:
        lines.append(
            f"    The previous period, ending {period.cancels_previous}, differs by exactly "
            "the opposite: a transaction is dated on the wrong side of the statement date "
            f"{period.cancels_previous}."
        )
    if not masked:
        for row in sorted(
            (*period.statement_only, *period.feed_only),
            key=lambda r: (r.row_date, r.side, r.amount_minor, r.description),
        ):
            excuse = f" ({row.excuse})" if row.excuse else ""
            lines.append(
                f"      {row.side}-only: {row.row_date} {format_amount(row.amount_minor)} "
                f"{row.description!r} ({row.source}){excuse}"
            )
        for single in period.single_rows:
            lines.append(
                f"      held row: {single.row_date} {format_amount(single.amount_minor)} "
                f"{single.description!r} ({' and '.join(single.holders)})"
            )
    return lines


def _account_lines(item: AccountPeriods, *, masked: bool) -> list[str]:
    lines = [f"{item.account}: {_plural(item.statements, 'statement')} held"]
    if item.withheld:
        lines.append(f"  {item.withheld}")
        lines.extend(f"  {sentence}" for sentence in item.same_money)
        return lines
    if item.feeds:
        lines.append(f"  Also held by: {', '.join(item.feeds)}.")
        if item.same_money:
            lines.append("  What the same-money rule did at each statement closing:")
            lines.extend(f"    {sentence}" for sentence in item.same_money)
    else:
        lines.append(
            "  No source other than the statements holds transactions for this account, so "
            "each period is tested against the statement's own transactions only."
        )
    for feed in item.feeds or ("",):
        if len(item.feeds) > 1:
            lines.append(f"  Against {feed}:")
        for period in item.periods:
            if period.feed == feed:
                lines.extend(_period_lines(period, masked=masked))
    return lines


def _held_row_sources(
    row: Transaction, sightings: Iterable[Transaction]
) -> tuple[str, ...]:
    """Everyone who saw this row on its date for its value, else the stored source."""
    sources = sorted(
        {
            s.source
            for s in sightings
            if s.amount_minor == row.amount_minor and s.value_date == row.value_date
        }
    )
    return tuple(sources) or (row.source,)


def _leftovers_of(
    found: Iterable[Agreement],
) -> tuple[list[Leftover], date, date] | None:
    """Every row either side of these pairings left unmatched, with the span they cover."""
    rows: list[Leftover] = []
    spans: list[tuple[date, date]] = []
    for agreement in found:
        spans.append((agreement.overlap_from, agreement.overlap_to))
        unmatched = [
            (leg.source, leg.row_date, leg.amount_minor, leg.description, "", leg.entity_id)
            for leg in agreement.unexplained
        ]
        unmatched += [
            (
                leg.source,
                leg.row_date,
                leg.amount_minor,
                leg.description,
                TRANSFER_EXCUSE,
                leg.entity_id,
            )
            for leg in agreement.confirmed_transfer_legs
        ]
        unmatched += [
            (
                match.source,
                match.row_date,
                match.amount_minor,
                match.description,
                f"{SIBLING_EXCUSE}{match.sibling_account}",
                "",
            )
            for match in agreement.attributed
        ]
        for source, row_date, amount, description, excuse, entity_id in unmatched:
            rows.append(
                Leftover(
                    STATEMENT_SIDE if source in STATEMENT_SOURCES else FEED_SIDE,
                    source,
                    row_date,
                    amount,
                    description,
                    excuse,
                    entity_id,
                )
            )
    if not spans:
        return None
    return rows, min(start for start, _ in spans), max(end for _, end in spans)


@dataclass(frozen=True)
class _Window:
    """One period to test: its days, the statement's movement, and whose it is."""

    kind: PeriodKind
    first_day: date
    last_day: date
    movement_minor: int
    statement: ListedStatement


def _movement_periods(
    statements: Sequence[ListedStatement],
    openings: Mapping[tuple[date, int], tuple[int, date | None]],
) -> list[_Window]:
    """The periods to test, from the distinct statements, earliest closing first.

    The INSIDE period of a statement whose printed opening is not the previous
    closing runs from the statement's OWN first row, and is tested against the
    rows that statement lists (`_periods_against`): a statement whose span
    starts before the previous closing (a long statement over shorter annual
    ones) would otherwise be compared in part, clipped to the days after that
    closing, with the whole of its movement.
    """
    found: list[_Window] = []
    for position, statement in enumerate(statements):
        printed = openings.get((statement.day, statement.balance_minor))
        if position == 0:
            if printed is None or printed[1] is None:
                continue
            found.append(
                _Window(
                    PeriodKind.FIRST,
                    printed[1],
                    statement.day,
                    statement.balance_minor - printed[0],
                    statement,
                )
            )
            continue
        previous = statements[position - 1]
        first_day = previous.day + timedelta(days=1)
        found.append(
            _Window(
                PeriodKind.BETWEEN,
                first_day,
                statement.day,
                statement.balance_minor - previous.balance_minor,
                statement,
            )
        )
        if printed is not None and printed[0] != previous.balance_minor:
            found.append(
                _Window(
                    PeriodKind.INSIDE,
                    first_day if printed[1] is None else printed[1],
                    statement.day,
                    statement.balance_minor - printed[0],
                    statement,
                )
            )
    return found


def _mark_cancellations(periods: list[Period]) -> list[Period]:
    """Pair each period whose surplus is the exact opposite of its neighbour's.

    Only the chain of one period per statement is compared, in date order: an
    INSIDE period repeats a stretch the chain already holds.
    """
    chain = sorted(
        (p for p in periods if p.kind is not PeriodKind.INSIDE), key=lambda p: p.last_day
    )
    next_of: dict[date, date] = {}
    previous_of: dict[date, date] = {}
    for earlier, later in pairwise(chain):
        if earlier.surplus_minor != 0 and earlier.surplus_minor == -later.surplus_minor:
            next_of[earlier.last_day] = later.last_day
            previous_of[later.last_day] = earlier.last_day
    return [
        p
        if p.kind is PeriodKind.INSIDE
        else replace(
            p,
            cancelled_by_next=next_of.get(p.last_day),
            cancels_previous=previous_of.get(p.last_day),
        )
        for p in periods
    ]


def held_in(
    window: _Window, counted: Iterable[Transaction], membership: Membership
) -> list[Transaction]:
    """The rows the store counts in one period: the single statement of it, for
    the report and for the fold's proof that a fold makes a period agree."""
    if window.kind is PeriodKind.INSIDE:
        # The statement's own rows, whatever else the store holds in those days.
        return [r for r in counted if r.entity_id in window.statement.members]
    return [
        r for r in counted if window.first_day <= membership.placement(r) <= window.last_day
    ]


def _periods_against(
    feed: str,
    windows: list[_Window],
    counted: list[Transaction],
    sightings: list[Transaction],
    paired: tuple[list[Leftover], date, date] | None,
    membership: Membership,
    folded: Sequence[Transaction] = (),
) -> list[Period]:
    built: list[Period] = []
    for window in windows:
        kind, first_day, last_day = window.kind, window.first_day, window.last_day
        movement = window.movement_minor
        inside_kind = kind is PeriodKind.INSIDE
        held = held_in(window, counted, membership)
        held_minor = sum(r.amount_minor for r in held)
        folded_here = (
            [r for r in folded if r.source == feed and first_day <= r.value_date <= last_day]
            if feed and not inside_kind
            else []
        )
        statement_only: tuple[Leftover, ...] = ()
        feed_only: tuple[Leftover, ...] = ()
        fully_paired = False
        if paired is not None:
            rows, covered_from, covered_to = paired
            inside = [r for r in rows if first_day <= r.row_date <= last_day]
            statement_only = tuple(r for r in inside if r.side == STATEMENT_SIDE)
            # A feed row is not one of the statement's own, so it is no part of
            # what an INSIDE period holds.
            feed_only = () if inside_kind else tuple(r for r in inside if r.side == FEED_SIDE)
            # Judged by the rows rather than the days: a source with no row
            # after some date may simply have had nothing to report, and only
            # a row outside the pairing's span is one it never compared.
            fully_paired = all(covered_from <= r.value_date <= covered_to for r in held)
        statement_rows = len(statement_only)
        if folded_here and paired is not None:
            _, covered_from, covered_to = paired
            fed = {s.entity_id for s in sightings if s.source not in STATEMENT_SOURCES}
            statement_rows += sum(
                1
                for s in sightings
                if s.source in STATEMENT_SOURCES
                and s.entity_id not in fed
                and first_day <= s.value_date <= last_day
                and not covered_from <= s.value_date <= covered_to
            )
        surplus = held_minor - movement
        leftover_sides = {
            row.entity_id: row.side for row in (*statement_only, *feed_only) if row.entity_id
        }
        single = (
            tuple(
                HeldRow(
                    r.value_date,
                    _held_row_sources(r, sightings),
                    r.amount_minor,
                    r.description,
                    leftover_sides.get(r.entity_id, ""),
                )
                for r in sorted(held, key=lambda r: (r.value_date, r.description))
                if r.amount_minor == surplus
            )
            if surplus != 0
            else ()
        )
        built.append(
            Period(
                kind,
                first_day,
                last_day,
                movement,
                held_minor,
                len(held),
                feed,
                fully_paired,
                statement_only,
                feed_only,
                single,
                folded_feed_rows=len(folded_here),
                folded_minor=sum(r.amount_minor for r in folded_here),
                folded_statement_rows=statement_rows if folded_here else 0,
            )
        )
    return built


@dataclass(frozen=True)
class AccountEvidence:
    """Everything the report and the fold both need to know about one account.

    Gathered once, in `gather_evidence`, so the report's periods and the fold's
    decisions are made from the same pairing and cannot disagree.
    """

    account: str
    membership: Membership
    #: Statement rows' periods, earliest closing first; empty when none can be made.
    windows: list[_Window]
    #: The rows the store counts for a statement's arithmetic.
    counted: list[Transaction]
    #: Each source's sighting of each of the account's rows.
    sightings: list[Transaction]
    #: Sources other than the statements that hold rows for the account.
    feeds: tuple[str, ...]
    #: Per feed ("" when there is none): the leftovers of the pairing and the span
    #: it covers, or None when the sources share no days.
    paired: Mapping[str, tuple[list[Leftover], date, date] | None]
    #: Rows folded as the same money as a statement's, which `counted` omits.
    folded: list[Transaction]
    #: Why nothing could be tested, or "".
    withheld: str = ""
    #: Per statement as `(closing day, closing balance)`: the opening balance it states and the
    #: day of the first transaction it lists. Absent where it states no opening balance.
    openings: Mapping[tuple[date, int], tuple[int, date | None]] = field(default_factory=dict)

    @property
    def statements(self) -> int:
        return len(self.membership.statements)


def between_periods(item: AccountEvidence) -> dict[tuple[date, int], Period]:
    """Each statement's period between the previous closing and its own, judged without a feed's
    leftovers: the same test the page makes, whose answer (`Period.agrees`) does not depend on
    them. Empty where the account's periods are withheld."""
    built = _periods_against("", item.windows, item.counted, item.sightings, None, item.membership)
    return {
        (w.statement.day, w.statement.balance_minor): p
        for w, p in zip(item.windows, built, strict=True)
        if w.kind is PeriodKind.BETWEEN
    }


def own_periods(item: AccountEvidence) -> dict[tuple[date, int], Period]:
    """For EVERY statement that states an opening balance, the period from that opening to its
    closing, holding the transactions THAT statement lists.

    `_movement_periods` gives this period only to a statement whose opening is not the previous
    closing, since for any other the period between the closings is the same test. A rule that
    judges a statement by what it lists needs the answer for each one, and for the first, and for
    an account holding one statement, which `withheld` leaves with no period at all.
    """
    windows = [
        _Window(
            PeriodKind.INSIDE,
            printed[1] or statement.day,
            statement.day,
            statement.balance_minor - printed[0],
            statement,
        )
        for statement in item.membership.statements
        if (printed := item.openings.get((statement.day, statement.balance_minor))) is not None
    ]
    if any(t.currency != "GBP" for t in item.counted):
        return {}
    built = _periods_against("", windows, item.counted, item.sightings, None, item.membership)
    return {
        (w.statement.day, w.statement.balance_minor): p
        for w, p in zip(windows, built, strict=True)
    }


def gather_evidence(
    store: Store,
    *,
    sibling_accounts: Mapping[str, Collection[str]] | None = None,
    account: str | None = None,
) -> list[AccountEvidence]:
    """The evidence for each account with a held statement, in account order.

    Each step is a named sub-phase of the same-money pass (`SAME_MONEY_PHASE`)
    so a slow rebuild says which of them it spent its time in.
    """
    with instrumentation.phase(f"{SAME_MONEY_PHASE}/reading-statements"):
        statements, _unusable = statement_balances(store, account)
        by_account: dict[str, list[StatementBalance]] = {}
        for anchor in statements:
            by_account.setdefault(anchor.account_ref, []).append(anchor)

        openings: dict[str, dict[tuple[date, int], tuple[int, date | None]]] = {}
        for filed_under, reading in held_statement_readings(store):
            if (
                filed_under not in by_account
                or reading.statement_date is None
                or reading.closing_balance_minor is None
                or reading.opening_balance_minor is None
            ):
                continue
            first_row = min((row.value_date for row in reading.transactions), default=None)
            openings.setdefault(filed_under, {})[
                (reading.statement_date, reading.closing_balance_minor)
            ] = (reading.opening_balance_minor, first_row)

    if not by_account:
        return []
    with instrumentation.phase(f"{SAME_MONEY_PHASE}/reading-sightings"):
        held = store.transactions_by_sighting()
        folded_ids = store.statement_folded_ids()
    with instrumentation.phase(f"{SAME_MONEY_PHASE}/pairing"):
        found = agreements(
            held,
            sibling_accounts=sibling_accounts or {},
            always_reconcile=True,
            only_accounts=set(by_account),
        )

    evidence: list[AccountEvidence] = []
    for ref, anchors in sorted(by_account.items()):
        with instrumentation.phase(f"{SAME_MONEY_PHASE}/membership"):
            membership = statement_membership(store, ref, anchors)
            rows = store.transactions_for_account(ref)
        counted = [t for t in rows if _counts_toward(STATEMENT, t)]
        folded = [t for t in rows if t.entity_id in folded_ids]
        withheld = ""
        if len(membership.statements) < 2:
            withheld = (
                "Only one statement is held, so no period between statements "
                "exists and nothing can be tested."
            )
        elif any(t.currency != "GBP" for t in counted):
            withheld = "The transactions are not all in GBP, so no sum of them is meaningful."
        sightings = [t for t in held if t.account_id == ref]
        feeds = tuple(sorted({t.source for t in sightings} - STATEMENT_SOURCES))
        paired: dict[str, tuple[list[Leftover], date, date] | None] = {}
        for feed in feeds or ("",):
            paired[feed] = _leftovers_of(
                [
                    a
                    for a in found
                    if a.account_id == ref
                    and feed
                    and feed in (a.left, a.right)
                    and ({a.left, a.right} - {feed}) <= STATEMENT_SOURCES
                ]
            )
        evidence.append(
            AccountEvidence(
                ref,
                membership,
                [] if withheld else _movement_periods(membership.statements, openings.get(ref, {})),
                counted,
                sightings,
                feeds,
                paired,
                folded,
                withheld,
                openings.get(ref, {}),
            )
        )
    return evidence


def _same_money_lines(store: Store, item: AccountEvidence) -> tuple[str, ...]:
    """What the last same-money pass recorded for an account a feed is paired
    against, as sentences. The record is the pass's own (`same_money_fold`
    decides and records it); this only reads it, so the page never repeats the
    rule beside it."""
    if not item.feeds:
        return ()
    kept = store.same_money_outcome(item.account)
    if kept is None:
        return (
            "The same-money pass has recorded nothing for this account yet: it runs "
            "after every import, pull, and rebuild.",
        )
    try:
        outcome = AccountOutcome.from_text(kept)
    except (ValueError, KeyError, TypeError):
        return ("The same-money pass's record for this account cannot be read.",)
    if set(outcome.feeds) != set(item.feeds):
        return (
            f"The same-money pass last ran when {dated_list(outcome.feeds) or 'no feed'} held "
            f"rows for this account, and {dated_list(item.feeds)} does now: the next import, "
            "pull, or rebuild runs it again.",
        )
    return tuple(outcome.describe())


def period_reconciliation(
    store: Store,
    *,
    sibling_accounts: Mapping[str, Collection[str]] | None = None,
    account: str | None = None,
) -> PeriodReport:
    """Each account with a held statement, period by period.

    `sibling_accounts` is what the cross-source page passes to `agreements`;
    handing in the same mapping is what keeps this report's leftovers the ones
    that page shows. `account` limits the report to one account.
    """
    accounts: list[AccountPeriods] = []
    for item in gather_evidence(store, sibling_accounts=sibling_accounts, account=account):
        same_money = _same_money_lines(store, item)
        if item.withheld:
            accounts.append(
                AccountPeriods(
                    item.account, item.statements, withheld=item.withheld, same_money=same_money
                )
            )
            continue
        built: list[Period] = []
        for feed, paired in item.paired.items():
            built += _mark_cancellations(
                _periods_against(
                    feed,
                    item.windows,
                    item.counted,
                    item.sightings,
                    paired,
                    item.membership,
                    item.folded,
                )
            )
        accounts.append(
            AccountPeriods(
                item.account, item.statements, tuple(built), item.feeds, same_money=same_money
            )
        )
    return PeriodReport(tuple(accounts))
