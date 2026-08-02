"""Pending transactions get a lifecycle; disappearance becomes evidence.

TrueLayer's pending endpoint returns the COMPLETE current pending set on
every pull - which makes absence meaningful: a stored PENDING row missing
from the latest pending payload has either settled (usually under a new id
and often a new amount - the fuel-pump hold, the bus tap-in amended under
daily capping) or been released without settling.

The resolution is deliberately conservative:

- a vanished pending row is marked VOID either way - kept in the store,
  visible in provenance, excluded from spending and from the budget replay,
  its bytes untouched in layer 0;
- a settlement counterpart is SOUGHT, amount-permissively, but only here:
  same account, booked, within a few days, same direction, matching
  description. Loosening the general matcher would license exactly the
  false merges the tiers exist to prevent, so the permissiveness lives in
  this one pass and nowhere else;
- what happened is recorded in the events outbox (its first writer):
  pending_settled with both amounts when a counterpart was found,
  pending_released when none was.

This applies ONLY to sources with complete-set pending semantics (the
TrueLayer pending endpoint). A Starling changesSince feed reports updates,
so absence there means nothing and must not void anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .store import Store

#: How long after its pending date a settlement counterpart may book.
SETTLEMENT_WINDOW_DAYS = 5


@dataclass
class PendingResolution:
    voided: int = 0
    settled: int = 0
    released: int = 0

    def describe(self) -> str:
        return (
            f"{self.voided} vanished pending row(s) voided "
            f"({self.settled} matched a settlement, {self.released} released)"
        )


def _condense(description: str) -> str:
    return "".join(description.casefold().split())


def resolve_vanished_pending(
    store: Store,
    account_id: str,
    *,
    present_source_ids: set[str],
    present_amount_dates: set[tuple[int, str]],
    emit_events: bool = True,
) -> PendingResolution:
    """Void stored pending rows absent from the latest complete pending set.

    Presence is judged by durable id when the row has one, else by the
    (amount, value date) pair - a deliberately loose fallback for the rare
    pending row without an id, erring towards NOT voiding.
    """
    resolution = PendingResolution()
    pending_rows = store.connection.execute(
        "SELECT entity_id, source_id, amount_minor, value_date, description "
        "FROM transactions WHERE account_id = ? AND status = 'pending'",
        (account_id,),
    ).fetchall()

    for row in pending_rows:
        source_id = row["source_id"]
        if source_id and str(source_id) in present_source_ids:
            continue
        if not source_id and (
            (int(row["amount_minor"]), str(row["value_date"]))
            in present_amount_dates
        ):
            continue

        entity_id = str(row["entity_id"])
        pending_amount = int(row["amount_minor"])
        pending_date = date.fromisoformat(str(row["value_date"]))
        condensed = _condense(str(row["description"]))

        # Amount-permissive settlement seek, confined to this pass: booked,
        # same account, same direction, within the window, description
        # matching by containment either way.
        counterpart = None
        candidates = store.connection.execute(
            "SELECT entity_id, amount_minor, description FROM transactions "
            "WHERE account_id = ? AND status = 'booked' "
            "AND value_date BETWEEN ? AND ?",
            (
                account_id,
                pending_date.isoformat(),
                (pending_date + timedelta(days=SETTLEMENT_WINDOW_DAYS)).isoformat(),
            ),
        ).fetchall()
        for candidate in candidates:
            if (int(candidate["amount_minor"]) < 0) != (pending_amount < 0):
                continue
            other = _condense(str(candidate["description"]))
            if condensed and other and (condensed in other or other in condensed):
                counterpart = candidate
                break

        store.connection.execute(
            "UPDATE transactions SET status = 'void' WHERE entity_id = ?",
            (entity_id,),
        )
        resolution.voided += 1
        if counterpart is not None:
            resolution.settled += 1
            if emit_events:
                store.append_event(
                    "pending_settled",
                    entity_id,
                    {
                        "pending_amount_minor": pending_amount,
                        "settled_amount_minor": int(counterpart["amount_minor"]),
                        "settled_entity_id": str(counterpart["entity_id"]),
                        "account_id": account_id,
                    },
                )
        else:
            resolution.released += 1
            if emit_events:
                store.append_event(
                    "pending_released",
                    entity_id,
                    {
                        "pending_amount_minor": pending_amount,
                        "account_id": account_id,
                    },
                )
    store.connection.commit()
    return resolution
