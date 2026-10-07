"""The balance an account held after each transaction the ledger lists.

The day's figure is the balance chart's running line (`balance_chart.running_balance`, itself
`balance_anchors.counted_by_day` anchored at the earliest known balance), so a row's figure and the
chart's line at that day cannot come to differ. This module only resolves the day's figure down to
the rows on it.

A same-day order is the order the ledger lists the rows (`ledger.build_ledger`: the bank's own time
where it gave one, then booking date, then id), newest first. A day's figure is the balance at the
END of the day, so the row listed first carries it, and each row beneath carries the figure before
the one above moved the balance. Where the sources give no time the order is only a stable one, and
the figure after a row is then a reading in that order, not a fact the bank stated; the day's end is
a fact either way.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from ..core.models import Transaction
from ..verify.balance_anchors import Anchor, counted_by_day
from .balance_chart import running_balance


def balances_after(
    listed: Sequence[Transaction],
    every_row: Sequence[Transaction],
    head: Anchor,
) -> list[int | None]:
    """The balance after each of `listed` (newest first), in the same order.

    `every_row` is every row the account's balances count (the stored rows and any derived for a
    balance-only account), and `head` the earliest known balance, as the chart anchors its line.
    A row no balance of that basis counts (history, or pending against a bank's or statement's
    figure) has None and moves nothing.
    """
    if not listed:
        return []
    first = min(row.value_date for row in listed)
    last = max(row.value_date for row in listed)
    ends: dict[date, int] = dict(
        running_balance(every_row, head.basis, (head.day, head.balance_minor), first, last)
    )
    figures: list[int | None] = []
    day: date | None = None
    held = 0
    for row in listed:
        if row.value_date != day:
            day = row.value_date
            held = ends.get(day, 0)
        if _counts(row, head.basis):
            figures.append(held)
            held -= row.amount_minor
        else:
            figures.append(None)
    return figures


def _counts(row: Transaction, basis: str) -> bool:
    """Whether the rows of `basis` count this one, asked of the one counting rule."""
    return bool(counted_by_day([row], basis))
