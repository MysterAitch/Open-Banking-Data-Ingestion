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

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from .balance_anchors import (
    CURRENCY,
    EffectiveOpening,
    FamilyWalk,
    effective_opening,
    known_account,
)
from .fault_structure import StructureReport, account_report, walk_report
from .masking import Structural

if TYPE_CHECKING:
    from .family_anchors import Families
    from .store import Store

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


def _lines(
    rows: list[tuple[date, str, int, int | None]],
) -> tuple[SourceLine, ...]:
    """Per source, in order of first appearance: (day, source, stated, predicted)."""
    order: dict[str, list[tuple[date, int, int]]] = {}
    for day, source, stated, predicted in rows:
        order.setdefault(source, []).append(
            (day, stated, stated if predicted is None else predicted)
        )
    return tuple(
        SourceLine(
            source,
            tuple(day for day, _, _ in found),
            tuple(stated for _, stated, _ in found),
            tuple(predicted for _, _, predicted in found),
        )
        for source, found in order.items()
    )


def _chart(
    ref: str,
    label: str,
    scope: str,
    spaces: tuple[str, ...],
    rows: list[tuple[date, str, int, int | None, int | None]],
    report: StructureReport,
) -> BalanceChart:
    """`rows` are (day, source, stated, predicted, difference) in walk order."""
    return BalanceChart(
        ref=ref,
        label=label,
        scope=scope,
        state="ok",
        withheld="",
        spaces=spaces,
        days=tuple(day for day, *_ in rows),
        differences=tuple(difference or 0 for *_, difference in rows),
        lines=_lines([row[:4] for row in rows]),
        structure=report,
        first_day=rows[0][0],
        last_day=rows[-1][0],
    )


def chart_of_walk(ref: str, walk: FamilyWalk, *, label: str = "") -> BalanceChart:
    """The chart of a whole-account walk, which must hold at least one reading."""
    rows = [
        (r.day, r.sources[0], r.balance_minor, r.expected_minor, r.difference_minor)
        for r in walk.readings
    ]
    return _chart(ref, label, WHOLE, walk.spaces, rows, walk_report(walk))


def chart_of_opening(ref: str, opening: EffectiveOpening, *, label: str = "") -> BalanceChart:
    """The chart the ledger page's walk for this account would draw."""
    walk = opening.family
    if walk is not None and walk.readings:
        return chart_of_walk(ref, walk, label=label)
    if walk is not None and walk.withheld:
        return empty_chart(ref, label, "withheld", walk.withheld)
    if not opening.readings:
        return empty_chart(ref, label, "no-balances")
    rows = [
        (
            r.anchor.day,
            r.anchor.source or r.anchor.basis,
            r.anchor.balance_minor,
            r.expected_minor,
            r.difference_minor,
        )
        for r in opening.readings
    ]
    return _chart(ref, label, OWN, (), rows, account_report(opening))


def build_balance_chart(
    store: Store, ref: str, *, label: str = "", families: Families | None = None
) -> BalanceChart:
    """The chart for one account, read from the store."""
    if not known_account(store, ref):
        return empty_chart(ref, label, "unknown")
    return chart_of_opening(
        ref, effective_opening(store, ref, families=families), label=label
    )
