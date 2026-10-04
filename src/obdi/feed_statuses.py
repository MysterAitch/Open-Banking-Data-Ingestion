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

NO ROW. An item whose newest status the provider's map drops (`STATUS_MAP`) or
does not list makes no row at all, so only the feed can say it exists.
An item that a stored row was made from is a row and is not counted as one that
makes none, even where its newest status is a status that makes none: that is a
row made from an earlier fetch, and `FeedStatuses.of` is what says so.
An item that states no time is counted but cannot be placed in a window.

The statuses are the bank's own words (`providers.starling.STATUS_MAP` says
which it maps), and a status name is not private: only names and counts are
ever said of them. A size and a recipient are values, compared in code and never
passed on.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from .family_anchors import feed_payload
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
    #: The item's STRUCTURE, for `feed_item_shape`: the top-level fields that carry a value,
    #: the (field, value) of each that is a short upper-case token (a candidate for a closed
    #: set; `feed_item_shape` decides which fields are and which may be said), the currencies
    #: of `amount` and `sourceAmount`, and the two times as stated.
    fields: frozenset[str] = frozenset()
    tokens: tuple[tuple[str, str], ...] = ()
    currency: str = ""
    source_currency: str = ""
    transacted: datetime | None = None
    settled: datetime | None = None


_TOKEN = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def makes_no_row(status: str) -> bool:
    """Whether an item with this status yields no row, by the provider's own map.

    The provider's map is the only place that decides it; a status it does not list
    is dropped like one it maps to nothing (`providers.starling.payment_unsettled`).
    """
    return STATUS_MAP.get(status.strip().upper()) is None


def _instant(text: object) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(text or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).astimezone(UTC)


def _currency(amount: object) -> str:
    named = amount.get("currency") if isinstance(amount, dict) else None
    return named if isinstance(named, str) and _CURRENCY.match(named) else ""


def _item(entry: Mapping[str, object], uid: str) -> FeedItem:
    amount = entry.get("amount")
    minor = amount.get("minorUnits") if isinstance(amount, dict) else None
    direction = str(entry.get("direction") or "").strip().lower()
    transacted = _instant(entry.get("transactionTime"))
    settled = _instant(entry.get("settlementTime"))
    return FeedItem(
        uid,
        str(entry.get("status") or "").strip().upper(),
        transacted or settled,
        direction if direction in ("in", "out") else "",
        minor if isinstance(minor, int) and not isinstance(minor, bool) else None,
        normalise_description(str(entry.get("counterPartyName") or "")),
        frozenset(name for name, value in entry.items() if value is not None),
        tuple(
            sorted(
                (name, value)
                for name, value in entry.items()
                if isinstance(value, str) and _TOKEN.match(value)
            )
        ),
        _currency(amount),
        _currency(entry.get("sourceAmount")),
        transacted,
        settled,
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


def _account_feeds(store: Store, account: str) -> list[str]:
    """Every landed feed artefact of the feeds that sighted a row of `account`.

    Not only the artefacts that sighted a row: a later fetch that reports a payment
    DECLINED sights no row of it, since the provider drops it, and is exactly the
    fetch whose newest status must be read. A feed is named by the account reference
    its artefacts land under.
    """
    return [
        str(row["digest"])
        for row in store.connection.execute(
            "SELECT DISTINCT later.digest AS digest FROM raw_artefacts later "
            "JOIN raw_artefacts seen ON seen.account_ref = later.account_ref "
            "WHERE later.source = 'starling-feed' AND seen.digest IN ("
            "  SELECT s.artefact_digest FROM transaction_sources s "
            "  JOIN transactions t ON t.entity_id = s.entity_id "
            "  WHERE t.account_id = ? AND s.source = 'starling' "
            "  AND s.artefact_digest IS NOT NULL)",
            (account,),
        )
    ]


class FeedStatuses:
    """The newest landed feed reading of every item of some accounts, each artefact read once."""

    def __init__(self, store: Store, accounts: Iterable[str]) -> None:
        digests: set[str] = set()
        self._uids: dict[str, frozenset[str]] = {}
        for account in accounts:
            digests.update(_account_feeds(store, account))
            self._uids.update(feed_uids_by_entity(store, account))
        order = _landing_order(store)
        #: Feed uid -> (the landing rank of the artefact that last named it, what it said).
        self._newest: dict[str, tuple[int, FeedItem]] = {}
        #: Feed uid -> what the artefact that first named it said.
        self._oldest: dict[str, FeedItem] = {}
        for rank, digest in enumerate(sorted(digests, key=lambda d: order.get(d, ("", 0)))):
            if digest not in _ITEMS_BY_DIGEST:
                _ITEMS_BY_DIGEST[digest] = _items_in(feed_payload(store, digest))
            for uid, item in _ITEMS_BY_DIGEST[digest].items():
                self._newest[uid] = (rank, item)
                self._oldest.setdefault(uid, item)
        self._row_uids = frozenset(uid for uids in self._uids.values() for uid in uids)

    def items(self) -> list[FeedItem]:
        """Every item of the accounts' feeds as the newest landing stated it, by uid."""
        return [item for _, item in self._newest.values()]

    def oldest_of(self, uid: str) -> FeedItem | None:
        """What the first artefact to name a feed uid said of it."""
        return self._oldest.get(uid)

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

    def no_row_items(self) -> list[FeedItem]:
        """The items whose newest status makes no row and that no stored row was made from.

        Earliest first, an item that states no time last, and the uid breaking a tie,
        so the order never depends on the order the artefacts were read in.
        """
        found = [
            item
            for uid, (_, item) in self._newest.items()
            if makes_no_row(item.status) and uid not in self._row_uids
        ]
        return sorted(found, key=lambda i: (i.at is None, i.at or _EPOCH, i.uid))

    def unmapped(self) -> Mapping[str, int]:
        """Status name -> how many no-row items carry it, for the names the provider's map lacks."""
        return self._counted(lambda status: status not in STATUS_MAP)

    def dropped(self) -> Mapping[str, int]:
        """Status name -> how many no-row items carry it, for the names the map drops on purpose."""
        return self._counted(lambda status: status in STATUS_MAP)

    def _counted(self, wanted: Callable[[str], bool]) -> Mapping[str, int]:
        names = Counter(
            item.status or NO_STATUS for item in self.no_row_items() if wanted(item.status)
        )
        return dict(sorted(names.items()))


__all__ = ["NO_STATUS", "FeedItem", "FeedStatuses", "makes_no_row"]
