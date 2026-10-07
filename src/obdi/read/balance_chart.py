"""The data behind the balance-difference timeline and its values chart.

The chart is of the walk the ledger page already shows for the account, and
never of a second walk: the whole-account (family) walk where the account has
Spaces and a source states the family's balance, and the account's own stated
balances against its own rows otherwise. Which of the two it is stays on the
record (`scope`), so the page can say so in its heading.

Everything here is read from the walk's readings, which already hold each
stated balance, what the rows predict at its day, and the difference. Nothing
re-sums a row, so the cost is the number of stated balances.

The record is masked by field, as every record a page renders is
(`masking`): days, counts, kinds, and sources are structural, and every series
of figures is a value. The masked timeline never reads a series; the values
chart reads them from the record itself, on the branch where values are shown.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from ..core.masking import Structural
from ..core.models import Transaction
from ..ingest.accounts import AccountRef
from ..verify.balance_anchors import (
    CURRENCY,
    FAMILY,
    STATEMENT,
    STATEMENT_OPENING,
    EffectiveOpening,
    FamilyWalk,
    counted_by_day,
    effective_opening,
    known_account,
)
from ..verify.fault_structure import StructureReport, account_report, walk_report

if TYPE_CHECKING:
    from ..ingest.family_anchors import Families
    from ..ingest.store import Store

WHOLE = "whole"
OWN = "own"

#: Why there is nothing to draw: "unknown" (nothing held under the reference),
#: "no-balances" (no stated balance to judge), "withheld" (the rows cannot be
#: summed), or "ok".
STATES = ("ok", "unknown", "no-balances", "withheld")


@dataclass(frozen=True)
class SourceLine:
    """One stating source's balances, and what the rows predicted at the same days."""

    source: Structural[str]
    days: Structural[tuple[date, ...]]
    stated: tuple[int, ...]
    predicted: tuple[int, ...]
    #: True where a held statement states the balance (drawn hollow), False where a person or
    #: a feed does (drawn filled). Empty means none is a statement's.
    statement: Structural[tuple[bool, ...]] = ()


@dataclass(frozen=True)
class BalanceChart:
    ref: Structural[str]
    label: Structural[str]
    #: "whole" (the main account and its Spaces together), "own", or "" when nothing is drawn.
    scope: Structural[str]
    state: Structural[str]
    withheld: Structural[str]
    spaces: Structural[tuple[str, ...]]
    #: The day of every stated balance in walk order, and each one's difference.
    days: Structural[tuple[date, ...]]
    differences: tuple[int, ...]
    lines: Structural[tuple[SourceLine, ...]]
    structure: Structural[StructureReport | None]
    first_day: Structural[date | None]
    last_day: Structural[date | None]
    currency: Structural[str] = CURRENCY
    #: The account's running balance: its first known day, then each later day that holds a
    #: movement, with the balance at the end of that day (`running_balance`). A value series.
    running: tuple[tuple[date, int], ...] = ()
    #: The day the account closed (it is archived), where that is not before its first known
    #: balance. `last_day` is never later than it.
    closed: Structural[date | None] = None


def running_balance(
    held: Sequence[Transaction],
    basis: str,
    anchor: tuple[date, int],
    first: date,
    last: date,
) -> tuple[tuple[date, int], ...]:
    """The balance at the end of `first`, and of each later day to `last` that holds a movement.

    Anchored as the prediction at a known balance is: the running sum of the counted rows from
    the earliest known balance's own figure (`anchor`), so the line passes through each
    prediction counted the same way (`counted_by_day`).
    """
    series = counted_by_day(held, basis)
    days = [day for day, _ in series]

    def through(day: date) -> int:
        at = bisect_right(days, day)
        return series[at - 1][1] if at else 0

    offset = anchor[1] - through(anchor[0])
    return (
        (first, offset + through(first)),
        *((day, offset + total) for day, total in series if first < day <= last),
    )


def _joined(rows: list[tuple[date, str, int, int | None, int | None, bool]]) -> tuple[
    tuple[date, int], ...
]:
    """What the rows predict at each known balance's day, in order, for a chart built without
    the rows themselves: all there is to draw a line through."""
    by_day = {
        day: stated if predicted is None else predicted
        for day, _, stated, predicted, *_ in rows
    }
    return tuple(sorted(by_day.items()))


def empty_chart(ref: str, label: str, state: str, withheld: str = "") -> BalanceChart:
    return BalanceChart(
        ref=ref,
        label=label,
        scope="",
        state=state,
        withheld=withheld,
        spaces=(),
        days=(),
        differences=(),
        lines=(),
        structure=None,
        first_day=None,
        last_day=None,
    )


_Row = tuple[date, str, int, int | None, int | None, bool]


def _lines(rows: list[_Row]) -> tuple[SourceLine, ...]:
    """Per source, in order of first appearance."""
    order: dict[str, list[tuple[date, int, int, bool]]] = {}
    for day, source, stated, predicted, _, statement in rows:
        order.setdefault(source, []).append(
            (day, stated, stated if predicted is None else predicted, statement)
        )
    return tuple(
        SourceLine(
            source,
            tuple(day for day, *_ in found),
            tuple(stated for _, stated, _, _ in found),
            tuple(predicted for _, _, predicted, _ in found),
            tuple(statement for *_, statement in found),
        )
        for source, found in order.items()
    )


def _chart(
    ref: str,
    label: str,
    scope: str,
    spaces: tuple[str, ...],
    rows: list[_Row],
    report: StructureReport,
    *,
    running: tuple[tuple[date, int], ...] | None = None,
    closed: date | None = None,
) -> BalanceChart:
    """`rows` are (day, source, stated, predicted, difference, is a statement's) in walk order.

    `closed` cuts the chart at the account's closing day, whatever a statement's period says;
    a closing day before the first known balance is no cut at all.
    """
    first, last = _span(rows, closed)
    if closed is not None and closed < min(row[0] for row in rows):
        closed = None
    return BalanceChart(
        ref=ref,
        label=label,
        scope=scope,
        state="ok",
        withheld="",
        spaces=spaces,
        days=tuple(day for day, *_ in rows),
        differences=tuple(difference or 0 for _, _, _, _, difference, _ in rows),
        lines=_lines(rows),
        structure=report,
        first_day=first,
        last_day=last,
        running=_joined(rows) if running is None else running,
        closed=closed,
    )


def _span(rows: list[_Row], closed: date | None) -> tuple[date, date]:
    """The first and last day to draw: the known balances', cut at the closing day."""
    first = min(row[0] for row in rows)
    last = max(row[0] for row in rows)
    if closed is not None and first <= closed:
        last = min(last, closed)
    return first, last


def chart_of_walk(
    ref: str,
    walk: FamilyWalk,
    *,
    label: str = "",
    rows: Sequence[Transaction] | None = None,
    closed: date | None = None,
) -> BalanceChart:
    """The chart of a whole-account walk, which must hold at least one reading.

    `rows` are every row the walk summed (the main account's and its Spaces'). Without them
    the running line can only be drawn through what the rows predict at the known days.
    """
    found: list[_Row] = [
        (r.day, r.sources[0], r.balance_minor, r.expected_minor, r.difference_minor, False)
        for r in walk.readings
    ]
    running = None
    if rows is not None:
        head = walk.readings[0]
        figure = head.balance_minor if head.expected_minor is None else head.expected_minor
        running = running_balance(rows, FAMILY, (head.day, figure), *_span(found, closed))
    return _chart(
        ref, label, WHOLE, walk.spaces, found, walk_report(walk), running=running, closed=closed
    )


def chart_of_opening(
    ref: str,
    opening: EffectiveOpening,
    *,
    label: str = "",
    rows: Sequence[Transaction] | None = None,
    closed: date | None = None,
) -> BalanceChart:
    """The chart the ledger page's walk for this account would draw.

    `rows` are the rows the walk summed, with any derived for a balance-only account
    (`EffectiveOpening.unitemised`) and, for the whole account, its Spaces'.
    """
    walk = opening.family
    if walk is not None and walk.readings:
        return chart_of_walk(ref, walk, label=label, rows=rows, closed=closed)
    if walk is not None and walk.withheld:
        return empty_chart(ref, label, "withheld", walk.withheld)
    if not opening.readings:
        return empty_chart(ref, label, "no-balances")
    found: list[_Row] = [
        (
            r.anchor.day,
            r.anchor.source or r.anchor.basis,
            r.anchor.balance_minor,
            r.expected_minor,
            r.difference_minor,
            r.anchor.basis in (STATEMENT, STATEMENT_OPENING),
        )
        for r in opening.readings
    ]
    running = None
    if rows is not None and not opening.withheld:
        head = opening.readings[0].anchor
        running = running_balance(
            rows, head.basis, (head.day, head.balance_minor), *_span(found, closed)
        )
    return _chart(
        ref, label, OWN, (), found, account_report(opening), running=running, closed=closed
    )


def build_balance_chart(
    store: Store, ref: str, *, label: str = "", families: Families | None = None
) -> BalanceChart:
    """The chart for one account, read from the store."""
    if not known_account(store, ref):
        return empty_chart(ref, label, "unknown")
    held = store.transactions_for_account(ref)
    opening = effective_opening(store, ref, held, families=families)
    rows = [*held, *opening.unitemised]
    if opening.family is not None:
        for space in opening.family.spaces:
            rows.extend(store.transactions_for_account(space))
    record = store.declared_account(AccountRef(ref))
    return chart_of_opening(
        ref, opening, label=label, rows=rows, closed=record.closed if record else None
    )
