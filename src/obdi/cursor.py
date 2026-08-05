"""The rolling changesSince cursor, with its canary.

The probe demonstrated update-time semantics with an inclusive boundary
(a cutoff equal to an item's updatedAt still returns it, and the filter
honours milliseconds), so a cursor at the newest updatedAt seen, minus a
comfort buffer, receives everything: new transactions, and every
amendment class the always-refetch behaviour existed to catch - fuel
preauths settling, transit caps batching days later, late refunds.

The buffer is not duplication tolerance; it is a PER-CYCLE EXPERIMENT.
Because the ask deliberately reaches back past the anchor item, that
item must appear in every response. The day it does not, one of two
things is true - the provider changed the filter semantics, or the item
itself was removed - and both demand attention rather than silence. The
fallback ladder is deliberate: step back through recent anchors first
(the removed-item case resolves there), and only then refetch the whole
history (the changed-semantics case, which also re-arms everything).

A ~30 minute buffer overlaps a handful of transactions per cycle.
Accepted: sightings deduplicate for free, and the canary is worth more
than the rows cost.

State lives in provider_facts - facts a pull learns, per connection,
latest observation wins - which is exactly what a cursor is.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from .jsontypes import JsonObject, text
from .store import Store

#: How far behind the anchor each routine ask reaches. Large enough that
#: clock skew and boundary conventions never matter; small enough that
#: the overlap is a handful of rows.
DEFAULT_OVERLAP_MINUTES = 30

#: How often a full-history sweep replaces the incremental ask. The
#: sweep is the second, slower canary: anything it inserts that the
#: incremental path should have caught is a finding about the pipe, and
#: it is REPORTED, never silently merged - a tier that cannot show its
#: catches is superstition.
DEFAULT_SWEEP_DAYS = 7

#: Prior anchors kept for the fallback ladder.
_HISTORY_KEEP = 5

_FACT_SOURCE = "starling"


def overlap() -> timedelta:
    raw = os.environ.get("OBDI_STARLING_OVERLAP_MINUTES", "").strip()
    try:
        minutes = int(raw) if raw else DEFAULT_OVERLAP_MINUTES
    except ValueError:
        minutes = DEFAULT_OVERLAP_MINUTES
    return timedelta(minutes=max(minutes, 1))


def sweep_interval() -> timedelta:
    raw = os.environ.get("OBDI_STARLING_SWEEP_DAYS", "").strip()
    try:
        days = int(raw) if raw else DEFAULT_SWEEP_DAYS
    except ValueError:
        days = DEFAULT_SWEEP_DAYS
    return timedelta(days=max(days, 1))


@dataclass
class FeedCursor:
    """Where the incremental ask starts, and the item that proves it."""

    anchor_uid: str
    anchor_updated_at: str
    #: (uid, updated_at) pairs, newest first - the fallback ladder.
    history: list[tuple[str, str]] = field(default_factory=list)

    def since_at(self) -> datetime:
        return _parse(self.anchor_updated_at) - overlap()

    def advanced(self, uid: str, updated_at: str) -> FeedCursor:
        if uid == self.anchor_uid and updated_at == self.anchor_updated_at:
            return self
        history = [(self.anchor_uid, self.anchor_updated_at), *self.history]
        return FeedCursor(
            anchor_uid=uid,
            anchor_updated_at=updated_at,
            history=history[:_HISTORY_KEEP],
        )


def _parse(stamp: str) -> datetime:
    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def item_update_stamp(item: JsonObject) -> str:
    """When this record last changed, by the provider's own account.

    updatedAt is what the filter provably keys on; transactionTime is
    the fallback for any item that omits it, correct in the only case
    that matters (a never-amended item was last changed when it
    happened)."""
    return text(item, "updatedAt") or text(item, "transactionTime")


def newest(items: list[JsonObject]) -> tuple[str, str] | None:
    """The (uid, updated_at) of the most recently changed item."""
    best: tuple[str, str] | None = None
    best_when: datetime | None = None
    for item in items:
        uid = text(item, "feedItemUid")
        stamp = item_update_stamp(item)
        if not uid or not stamp:
            continue
        try:
            when = _parse(stamp)
        except ValueError:
            continue
        if best_when is None or when > best_when:
            best_when = when
            best = (uid, stamp)
    return best


def canary_present(items: list[JsonObject], cursor: FeedCursor) -> bool:
    return any(text(item, "feedItemUid") == cursor.anchor_uid for item in items)


def load(store: Store, identity_key: str, connection_id: str) -> FeedCursor | None:
    raw = store.provider_fact(
        _FACT_SOURCE, connection_id, f"feed-cursor:{identity_key}"
    )
    if not raw:
        return None
    try:
        decoded = json.loads(raw)
        return FeedCursor(
            anchor_uid=str(decoded["anchor_uid"]),
            anchor_updated_at=str(decoded["anchor_updated_at"]),
            history=[
                (str(uid), str(stamp))
                for uid, stamp in decoded.get("history", [])
            ],
        )
    except (ValueError, KeyError, TypeError):
        # An unreadable cursor degrades to a full fetch, never to a guess.
        return None


def save(
    store: Store, identity_key: str, connection_id: str, cursor: FeedCursor
) -> None:
    store.record_provider_fact(
        _FACT_SOURCE,
        connection_id,
        f"feed-cursor:{identity_key}",
        json.dumps(
            {
                "anchor_uid": cursor.anchor_uid,
                "anchor_updated_at": cursor.anchor_updated_at,
                "history": cursor.history,
            }
        ),
    )


def sweep_due(store: Store, identity_key: str, connection_id: str) -> bool:
    raw = store.provider_fact(
        _FACT_SOURCE, connection_id, f"feed-last-sweep:{identity_key}"
    )
    if not raw:
        return True
    try:
        return datetime.now(UTC) - _parse(raw) >= sweep_interval()
    except ValueError:
        return True


def stamp_sweep(store: Store, identity_key: str, connection_id: str) -> None:
    store.record_provider_fact(
        _FACT_SOURCE,
        connection_id,
        f"feed-last-sweep:{identity_key}",
        datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )


def filter_leaks(
    items: list[JsonObject], cutoff: datetime
) -> list[tuple[str, str]]:
    """Items a response contained that its own ask should have excluded.

    Update-time semantics with an inclusive boundary were DEMONSTRATED,
    so every returned item's update stamp must be >= the asked cutoff.
    One that is not means either the provider's filter regressed or an
    item was inserted retroactively wearing a stale stamp - the "a 5pm
    transaction suddenly appears in a slot we had already checked four
    times" case. Both are worth an alarm; with this check the semantics
    are effectively RE-VERIFIED on every routine cycle, from the
    opposite direction to the canary (which proves inclusion; this
    proves exclusion).
    """
    leaked = []
    for item in items:
        uid = text(item, "feedItemUid")
        stamp = item_update_stamp(item)
        if not uid or not stamp:
            continue
        try:
            if _parse(stamp) < cutoff:
                leaked.append((uid, stamp))
        except ValueError:
            continue
    return leaked


def offsetless_stamps(items: list[JsonObject]) -> list[str]:
    """Uids of items whose stamps carry no timezone marking.

    Every stamp ever observed is Z-suffixed - UTC self-declared in-band.
    The cursor arithmetic ASSUMES a naked stamp is UTC; a provider that
    starts sending local time unmarked would make that assumption an
    hour wrong twice a year. This turns the assumption into a monitored
    invariant: the day a stamp arrives without an offset, the pull says
    so instead of silently guessing.
    """
    naked = []
    for item in items:
        uid = text(item, "feedItemUid")
        for stamp in (text(item, "updatedAt"), text(item, "transactionTime")):
            if stamp and not (
                stamp.endswith("Z") or "+" in stamp[10:] or "-" in stamp[10:]
            ):
                naked.append(uid)
                break
    return naked


def moved_transaction_times(
    items: list[JsonObject], stored_dates: dict[str, str]
) -> list[tuple[str, str, str]]:
    """(uid, stored_date, incoming_date) where the ECONOMIC time moved.

    Amendments legitimately change amounts and statuses; a transaction
    whose transactionTime itself moves is a different and rarer beast
    (the fuel-pump amendment kept its time; a moved time reshuffles
    which day money left). Not necessarily wrong - but always worth a
    line in the log.
    """
    moved = []
    for item in items:
        uid = text(item, "feedItemUid")
        stamp = text(item, "transactionTime")
        if not uid or not stamp or uid not in stored_dates:
            continue
        incoming_date = stamp[:10]
        if stored_dates[uid] != incoming_date:
            moved.append((uid, stored_dates[uid], incoming_date))
    return moved


def sweep_misses(
    items: list[JsonObject],
    known_source_ids: set[str],
    cursor: FeedCursor | None,
) -> list[str]:
    """Items a sweep found that the incremental path should have caught.

    "Should have caught" is precise: unknown to the store AND last
    changed before the previous anchor's reach. Anything newer is just
    the genuinely-new traffic this sweep happened to carry.
    """
    if cursor is None:
        return []
    threshold = cursor.since_at()
    missed = []
    for item in items:
        uid = text(item, "feedItemUid")
        if not uid or uid in known_source_ids:
            continue
        stamp = item_update_stamp(item)
        try:
            if stamp and _parse(stamp) < threshold:
                missed.append(uid)
        except ValueError:
            continue
    return missed
