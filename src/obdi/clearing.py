"""Which rows are CLEARED, and which of the three words a stretch of an account earns.

THE VOCABULARY, used on every page and defined here once:

  known balance   what a source states the balance was on a date: a claim, a target, and a
                  warning while the rows do not reproduce it. (`balance_anchors`)
  in agreement    the rows up to a date reproduce every known balance up to it, and the movement
                  checks hold as well. Worked out on demand and never declared, so it can change
                  after any rebuild. (`agreement`)
  protected       a person decided that a span is verified and must not change silently. Declared,
                  survives a rebuild, and is an alarm on change and never a freeze. (`protection`)

The owner's manual flow in another budgeting tool was to walk the statement and mark each
transaction found as CLEARED. This module is that step, done by the store: a row is cleared when
a source that is an AUTHORITATIVE LISTING of the account lists it.

WHICH SOURCES CLEAR (`namespaces.CLEARING_SOURCES`), decided once:

  clear      a statement the parsers read, an export (CSV, QIF), and the bank's own feed
             (`starling-feed`). Each is the bank's own record of the account, so a row it lists has
             been found in a record the owner could compare by hand.
  do not     the aggregator (`truelayer-*`): it relays the bank's records, can report a row and
             later drop or re-issue it, and the aggregator ALONE is exactly the evidence the owner
             walked a statement to avoid trusting. A typed row is the owner's own entry, which a
             statement has yet to confirm. A derived unitemised change is arithmetic, not a listing.

A pending row is never cleared, whoever lists it: a pending record is superseded by its settled
form. A void, folded, or reversed row is history and is in neither count.

Cleared and uncleared are counted per month and per account for the rows that count; the derived
unitemised rows of a balance-only account are in neither, because no source lists them.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from .masking import Structural
from .models import TransactionStatus
from .namespaces import CLEARING_SOURCES
from .store import Store


def cleared_by(sources: Iterable[str], status: str) -> tuple[str, ...]:
    """The clearing sources among those that sighted a row, for a row that can be cleared at all."""
    if status != TransactionStatus.BOOKED.value:
        return ()
    return tuple(sorted({source for source in sources if source in CLEARING_SOURCES}))


def cleared_entity_ids(store: Store) -> frozenset[str]:
    """Every stored row that is cleared, for the push to send as cleared."""
    marks = ",".join("?" for _ in CLEARING_SOURCES)
    ordered = sorted(CLEARING_SOURCES)
    found = store.connection.execute(
        "SELECT DISTINCT t.entity_id AS entity_id FROM transactions t "  # noqa: S608
        "LEFT JOIN transaction_sources s ON s.entity_id = t.entity_id "
        f"WHERE t.status = ? AND (t.source IN ({marks}) OR s.source IN ({marks}))",
        (TransactionStatus.BOOKED.value, *ordered, *ordered),
    )
    return frozenset(str(row["entity_id"]) for row in found)


@dataclass(frozen=True)
class ClearedMonth:
    month: Structural[str]
    cleared: Structural[int]
    uncleared: Structural[int]


@dataclass(frozen=True)
class ClearingView:
    """Cleared and uncleared rows per month and in all, as counts only."""

    cleared: Structural[int]
    uncleared: Structural[int]
    months: Structural[tuple[ClearedMonth, ...]]


def clearing_counts(marks: Iterable[tuple[str, bool]]) -> ClearingView:
    """(month, cleared) for each row that counts, as the per-month and total counts."""
    cleared: Counter[str] = Counter()
    uncleared: Counter[str] = Counter()
    for month, is_cleared in marks:
        (cleared if is_cleared else uncleared)[month] += 1
    months = sorted(set(cleared) | set(uncleared))
    return ClearingView(
        cleared=sum(cleared.values()),
        uncleared=sum(uncleared.values()),
        months=tuple(ClearedMonth(m, cleared[m], uncleared[m]) for m in months),
    )
