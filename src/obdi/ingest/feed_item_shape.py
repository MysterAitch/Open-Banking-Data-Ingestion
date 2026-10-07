"""How one feed item's STRUCTURE differs from the usual item like it.

Two payments the bank's feed, the aggregator, and the bank's own app all show as
ordinary settled card payments can still be absent from a statement and an export, so
the status alone does not tell them from money. This measures what else in the item
might: which fields it carries, which closed-set values, which currencies, which times.

SAID, never any value: field NAMES, closed-set values, currency codes, and counts.
An amount, a name, a reference, and free text are never read into the answer.

THE USUAL ITEM is the comparable items of the account's newest landed feed, those with
the same status, the same direction, and the same `source` (the feed's own field, e.g.
MASTER_CARD), not counting the item itself. With fewer than `MIN_COMPARABLE` of them
nothing is called usual and the page says so.

THE THRESHOLDS, stated once here and repeated nowhere: a field is USUAL when at least
`USUAL` of the comparable items carry it, and a value is RARE when fewer than `RARE` of
them carry it.

A CLOSED SET is a field whose every value, across all landed items, is a short upper-case
token and which has at most `CLOSED_VALUES` distinct ones. Its values are said only when
the field's own name is not one the classification registry withholds (a name, a
reference, an identifier) and is either approved there or known to it: a field nobody has
classified, or has classified as personal, never has a value said, however few distinct
values it holds, because a payee in capitals repeated through a small account is also a
closed set by count.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from math import ceil

from ..core.classification import CATEGORICAL, KNOWN_FIELDS, SHOW, UNCLASSIFIED, classify
from ..core.masking import Structural
from .feed_statuses import FeedItem, FeedStatuses

USUAL = 0.9
RARE = 0.1
MIN_COMPARABLE = 5
CLOSED_VALUES = 12

#: What the page says of the thresholds, beside the sentences that use them.
THRESHOLDS = (
    "A field is usual when at least nine in ten comparable items carry it, and a value is "
    "rare when fewer than one in ten do; comparable items have the same status, direction, "
    f"and source, and at least {MIN_COMPARABLE} are needed to say what is usual."
)


@dataclass(frozen=True)
class ItemShape:
    """How one feed item differs from the usual one, in names, codes, and counts."""

    #: The comparable group: "SETTLED", "out", "MASTER_CARD" (each "" where the item has none).
    status: Structural[str]
    direction: Structural[str]
    source: Structural[str]
    #: How many other items are comparable.
    comparable: Structural[int]
    #: Fields the usual item carries and this one lacks, and fields this one carries that
    #: fewer than one in ten comparable items do.
    lacks: Structural[tuple[str, ...]] = ()
    extra: Structural[tuple[str, ...]] = ()
    #: (field, value, percent of comparable items carrying it) for a closed-set value that
    #: fewer than one in ten comparable items carry; 0 means under one percent.
    rare_values: Structural[tuple[tuple[str, str, int], ...]] = ()
    #: The two currencies where `amount` and `sourceAmount` name different ones, else "".
    currencies: Structural[tuple[str, str]] = ("", "")
    #: Whole days between `transactionTime` and `settlementTime` where both are stated, at
    #: least one day apart, and fewer than one in ten comparable items have a gap at least
    #: that long, else None; and the percent of comparable items that do.
    days_apart: Structural[int | None] = None
    days_apart_percent: Structural[int] = 0
    #: What changed between the oldest and newest landing of the item, each as a phrase
    #: of field names and closed-set values; empty where it did not or landed once.
    changed: Structural[tuple[str, ...]] = ()


def differs(shape: ItemShape) -> bool:
    """Whether anything in the shape says the item is not like the usual one.

    A function and not a property because the page reads the record through its
    masking view (`masking.Disclosed`), which exposes fields only.
    """
    return bool(
        shape.lacks
        or shape.extra
        or shape.rare_values
        or shape.currencies != ("", "")
        or shape.days_apart is not None
        or shape.changed
    )


def _group(item: FeedItem) -> tuple[str, str, str]:
    return item.status, item.direction, dict(item.tokens).get("source", "")


def _days(item: FeedItem) -> int | None:
    if item.transacted is None or item.settled is None:
        return None
    # By calendar date, not elapsed time: what a gap is compared with is the DATE another
    # source gives the payment, and a payment made at ten and settled at six the next
    # morning is a day apart on every statement though twenty hours apart by the clock.
    return abs((item.settled.date() - item.transacted.date()).days)


def _percent(count: int, of: int) -> int:
    return count * 100 // of if of else 0


def _differing_currencies(item: FeedItem) -> tuple[str, str]:
    if item.currency and item.source_currency and item.currency != item.source_currency:
        return item.currency, item.source_currency
    return "", ""


def _sayable(field: str) -> bool:
    level = classify(field)
    return level in (SHOW, CATEGORICAL) or (level == UNCLASSIFIED and field in KNOWN_FIELDS)


class FeedShapes:
    """The comparisons of one account's feed items, the groups built once on first use."""

    def __init__(self, feed: FeedStatuses) -> None:
        self._feed = feed
        self._groups: dict[tuple[str, str, str], list[FeedItem]] | None = None
        self._closed: set[str] | None = None

    def _build(self) -> None:
        groups: dict[tuple[str, str, str], list[FeedItem]] = defaultdict(list)
        values: dict[str, set[str]] = defaultdict(set)
        spoilt: set[str] = set()
        for item in self._feed.items():
            groups[_group(item)].append(item)
            tokens = dict(item.tokens)
            for name in item.fields:
                if name in tokens:
                    values[name].add(tokens[name])
                else:
                    spoilt.add(name)
        self._groups = groups
        self._closed = {
            name
            for name, seen in values.items()
            if len(seen) <= CLOSED_VALUES and name not in spoilt and _sayable(name)
        }

    def of(self, entity: str) -> ItemShape | None:
        """The shape of a stored row's own feed item, or None where no feed item is its own."""
        item = self._feed.item_of(entity)
        if item is None:
            return None
        if self._groups is None or self._closed is None:
            self._build()
        assert self._groups is not None and self._closed is not None  # set by _build
        status, direction, source = _group(item)
        others = [other for other in self._groups.get(_group(item), []) if other is not item]
        changed = self._changed(item)
        if len(others) < MIN_COMPARABLE:
            return ItemShape(status, direction, source, len(others), changed=changed)
        total = len(others)
        carried = Counter(name for other in others for name in other.fields)
        enough = ceil(USUAL * total)
        lacks = tuple(
            sorted(
                name
                for name, count in carried.items()
                if count >= enough and name not in item.fields
            )
        )
        extra = tuple(
            sorted(
                name
                for name in item.fields
                if carried.get(name, 0) * 10 < total and name not in _ALWAYS_PRESENT
            )
        )
        held = Counter(pair for other in others for pair in other.tokens)
        mine = dict(item.tokens)
        rare = tuple(
            (name, mine[name], _percent(held[(name, mine[name])], total))
            for name in sorted(mine)
            if name in self._closed
            and name in carried
            and carried[name] >= enough
            and held[(name, mine[name])] * 10 < total
        )
        # A gap is said only where it is rare: most card payments settle a day or two after
        # they are made, and a sentence on each of them would bury the one that matters.
        # The one that mattered settled 135 days later, and the export dates a payment by
        # its settlement, so the store held it twice.
        gap = _days(item)
        gap_percent = 0
        if gap is not None and gap >= 1:
            as_long = sum(1 for other in others if (_days(other) or 0) >= gap)
            gap_percent = _percent(as_long, total)
            if as_long * 10 >= total:
                gap = None
        return ItemShape(
            status,
            direction,
            source,
            total,
            lacks,
            extra,
            rare,
            _differing_currencies(item),
            gap if gap is not None and gap >= 1 else None,
            gap_percent,
            changed,
        )

    def _changed(self, item: FeedItem) -> tuple[str, ...]:
        """What differs between the oldest and newest landing of the item, in names and values."""
        if self._closed is None:
            self._build()
        assert self._closed is not None  # set by _build
        first = self._feed.oldest_of(item.uid)
        if first is None or first is item:
            return ()
        said: list[str] = []
        said += [f"field {name} appeared" for name in sorted(item.fields - first.fields)]
        said += [f"field {name} disappeared" for name in sorted(first.fields - item.fields)]
        before, after = dict(first.tokens), dict(item.tokens)
        said += [
            f"{name} changed from {before[name]} to {after[name]}"
            for name in sorted(before.keys() & after.keys())
            if name in self._closed and before[name] != after[name]
        ]
        return tuple(said)


#: Fields that are present on every kind of item by construction, so carrying one is never
#: unusual: naming the item and its amount says nothing about its shape.
_ALWAYS_PRESENT = frozenset({"feedItemUid", "amount", "direction", "status", "source"})

__all__ = ["MIN_COMPARABLE", "THRESHOLDS", "FeedShapes", "ItemShape", "differs"]
