"""What the bank's own feed says each of its items is, read from the landed feeds.

THE FEED, NOT THE ROW. An item's status is read from the landed feed artefacts
by the item's uid and never from a stored row's raw: a row keeps the raw of
whichever sighting created it, so a payment an export or an aggregator reported
first would show no feed status of its own and a count taken from rows would
change with the order the sources arrived in
(`family_anchors.feed_round_ups` is the reason, and the same rule).

THE NEWEST WINS. An item's status changes over time (settled, then refunded),
so the status of a uid is the one in the artefact landed last. Landing order is
the artefact's first landing time, then its position, so two fetches of one
item give the same answer whichever order they are read in.

The statuses are the bank's own words (`providers.starling.STATUS_MAP` says
which it maps), and a status name is not private: only names and counts are
ever said of them.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping

from .family_anchors import feed_digests, feed_payload
from .providers.starling import STATUS_MAP
from .round_up_accounts import feed_uids_by_entity
from .store import Store

#: How an item with no status of its own is named where statuses are named.
NO_STATUS = "no status"

#: Artefact digest -> its items' statuses by feed uid, upper-cased. The bytes
#: never change, so a reading is good for the life of the process.
_STATUSES_BY_DIGEST: dict[str, dict[str, str]] = {}


def _statuses_in(payload: bytes) -> dict[str, str]:
    try:
        decoded = json.loads(payload)
    except ValueError:
        return {}
    items = decoded.get("feedItems") if isinstance(decoded, dict) else None
    found: dict[str, str] = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        uid = str(item.get("feedItemUid") or "")
        if uid:
            found[uid] = str(item.get("status") or "").strip().upper()
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
    """The newest landed feed status of every item of some accounts, each artefact read once."""

    def __init__(self, store: Store, accounts: Iterable[str]) -> None:
        digests: set[str] = set()
        self._uids: dict[str, frozenset[str]] = {}
        for account in accounts:
            digests.update(feed_digests(store, account))
            self._uids.update(feed_uids_by_entity(store, account))
        order = _landing_order(store)
        #: Feed uid -> (the landing rank of the artefact that last named it, its status).
        self._newest: dict[str, tuple[int, str]] = {}
        for rank, digest in enumerate(sorted(digests, key=lambda d: order.get(d, ("", 0)))):
            if digest not in _STATUSES_BY_DIGEST:
                _STATUSES_BY_DIGEST[digest] = _statuses_in(feed_payload(store, digest))
            for uid, status in _STATUSES_BY_DIGEST[digest].items():
                self._newest[uid] = (rank, status)

    def of(self, entity: str) -> str:
        """The feed status of a stored row, or "" where no feed item is its own.

        A row the bank's feed named under several uids has the status of the
        one named in the artefact landed last.
        """
        named = [self._newest[uid] for uid in self._uids.get(entity, ()) if uid in self._newest]
        return (max(named)[1] or NO_STATUS) if named else ""

    def unmapped(self) -> Mapping[str, int]:
        """Status name -> how many feed items carry it, for the names the provider's map lacks.

        Such an item yields no row at all, so only the feed can say it exists.
        """
        names = Counter(
            status or NO_STATUS for _, status in self._newest.values() if status not in STATUS_MAP
        )
        return dict(sorted(names.items()))


__all__ = ["NO_STATUS", "FeedStatuses"]
