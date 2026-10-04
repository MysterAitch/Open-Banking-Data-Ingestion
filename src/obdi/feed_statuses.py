"""What the bank's own feed says each of its items is, read from the landed feeds.

THE FEED, NOT THE ROW. An item's status, time, and other facts are read from the
landed feed artefacts by the item's uid and never from a stored row's raw: a row
keeps the raw of whichever sighting created it, so a payment an export or an
aggregator reported first would show no feed status of its own and a count taken
from rows would change with the order the sources arrived in
(`family_anchors.feed_round_ups` is the reason, and the same rule).

THE NEWEST WINS. An item's status changes over time (settled, then refunded),
so what the feed says of a uid is what the artefact landed last says, whole: its
status, its time, and the rest are never mixed from two fetches. Landing order is
the artefact's first landing time, then its position, so two fetches of one
item give the same answer whichever order they are read in.

THE TIME is `transactionTime`, else `settlementTime`: the one the provider dates a
row by (`providers.starling.to_transaction`), read here as an instant in UTC.

The statuses are the bank's own words (`providers.starling.STATUS_MAP` says
which it maps), and a status name is not private: only names and counts are
ever said of them. A size and a recipient are values, compared in code and never
passed on.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from .family_anchors import feed_digests, feed_payload
from .identity import normalise_description
from .providers.starling import STATUS_MAP
from .round_up_accounts import feed_uids_by_entity
from .store import Store

#: How an item with no status of its own is named where statuses are named.
NO_STATUS = "no status"


@dataclass(frozen=True)
class FeedItem:
    """One feed item as one artefact stated it."""

    uid: str
    #: Upper-cased; "" where the item states none.
    status: str
    #: The instant the item states, in UTC, or None where it states none that can be read.
    at: datetime | None
    #: "in" or "out"; "" where the item states neither.
    direction: str
    #: The unsigned size in minor units; None where it states none that can be read.
    minor: int | None
    #: The counterparty as the matcher compares text (`identity.normalise_description`).
    recipient: str


def _instant(text: object) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(text or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).astimezone(UTC)


def _item(entry: Mapping[str, object], uid: str) -> FeedItem:
    amount = entry.get("amount")
    minor = amount.get("minorUnits") if isinstance(amount, dict) else None
    direction = str(entry.get("direction") or "").strip().lower()
    return FeedItem(
        uid,
        str(entry.get("status") or "").strip().upper(),
        _instant(entry.get("transactionTime")) or _instant(entry.get("settlementTime")),
        direction if direction in ("in", "out") else "",
        minor if isinstance(minor, int) and not isinstance(minor, bool) else None,
        normalise_description(str(entry.get("counterPartyName") or "")),
    )


#: Artefact digest -> its items by feed uid. The bytes never change, so a reading
#: is good for the life of the process.
_ITEMS_BY_DIGEST: dict[str, dict[str, FeedItem]] = {}


def _items_in(payload: bytes) -> dict[str, FeedItem]:
    try:
        decoded = json.loads(payload)
    except ValueError:
        return {}
    items = decoded.get("feedItems") if isinstance(decoded, dict) else None
    found: dict[str, FeedItem] = {}
    for entry in items if isinstance(items, list) else []:
        if not isinstance(entry, dict):
            continue
        uid = str(entry.get("feedItemUid") or "")
        if uid:
            found[uid] = _item(entry, uid)
    return found


def _landing_order(store: Store) -> dict[str, tuple[str, int]]:
    """Each landed feed artefact's digest -> (first landing time, first position)."""
    return {
        str(row["digest"]): (str(row["at"]), int(row["position"]))
        for row in store.connection.execute(
            "SELECT digest, MIN(fetched_at) AS at, MIN(rowid) AS position "
            "FROM raw_artefacts WHERE source = 'starling-feed' GROUP BY digest"
        )
    }


class FeedStatuses:
    """The newest landed feed reading of every item of some accounts, each artefact read once."""

    def __init__(self, store: Store, accounts: Iterable[str]) -> None:
        digests: set[str] = set()
        self._uids: dict[str, frozenset[str]] = {}
        for account in accounts:
            digests.update(feed_digests(store, account))
            self._uids.update(feed_uids_by_entity(store, account))
        order = _landing_order(store)
        #: Feed uid -> (the landing rank of the artefact that last named it, what it said).
        self._newest: dict[str, tuple[int, FeedItem]] = {}
        for rank, digest in enumerate(sorted(digests, key=lambda d: order.get(d, ("", 0)))):
            if digest not in _ITEMS_BY_DIGEST:
                _ITEMS_BY_DIGEST[digest] = _items_in(feed_payload(store, digest))
            for uid, item in _ITEMS_BY_DIGEST[digest].items():
                self._newest[uid] = (rank, item)

    def item_of(self, entity: str) -> FeedItem | None:
        """What the newest landed feed says of a stored row's own item, or None where none is.

        A row the bank's feed named under several uids is read from the one named in
        the artefact landed last, the uid breaking a tie inside one artefact.
        """
        named = [
            (self._newest[uid][0], uid)
            for uid in self._uids.get(entity, ())
            if uid in self._newest
        ]
        return self._newest[max(named)[1]][1] if named else None

    def of(self, entity: str) -> str:
        """The feed status of a stored row, or "" where no feed item is its own."""
        item = self.item_of(entity)
        return (item.status or NO_STATUS) if item is not None else ""

    def time_of(self, entity: str) -> datetime | None:
        """The instant the feed states for a row's item, in UTC; None where it states none."""
        item = self.item_of(entity)
        return item.at if item is not None else None

    def unmapped(self) -> Mapping[str, int]:
        """Status name -> how many feed items carry it, for the names the provider's map lacks.

        Such an item yields no row at all, so only the feed can say it exists.
        """
        names = Counter(
            item.status or NO_STATUS
            for _, item in self._newest.values()
            if item.status not in STATUS_MAP
        )
        return dict(sorted(names.items()))


__all__ = ["NO_STATUS", "FeedItem", "FeedStatuses"]
