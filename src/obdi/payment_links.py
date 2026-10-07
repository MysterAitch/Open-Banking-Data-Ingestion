"""The identifiers by which two sources name one payment.

THE FACT. An aggregator item states, as `provider_transaction_id` (and again as
`meta.provider_id`), the uid of the first-party feed item it reports.
Measured on one month of a real Starling account: 58 of 58 aggregator items carried
a feed item's own `feedItemUid`, across the main account and its Spaces, all 58 agreed
with that feed item on size and direction, and 56 of 58 on the instant to the second.
The matcher read none of them and paired the two by amount, date window, and description.

This module says which sources are which and how an id is read; `matching` says what the
id is worth.
"""

from __future__ import annotations

from collections.abc import Mapping

from .core.models import Transaction

#: Sources that are the bank's own feed, whose item uid an aggregator can state.
FIRST_PARTY_FEEDS = frozenset({"starling"})

#: Sources that report a bank's payments through a third party and may state the feed's uid.
AGGREGATORS = frozenset({"truelayer"})

#: Where an item states the feed's uid, most direct first.
#: `transaction_id` and `normalised_provider_transaction_id` are not here: the first changes
#: between requests, and the second is the aggregator's own name for the payment.
LINK_PATHS: tuple[tuple[str, ...], ...] = (
    ("provider_transaction_id",),
    ("meta", "provider_transaction_id"),
    ("meta", "provider_id"),
)


def link_id_of(raw: Mapping[str, object]) -> str:
    """The first-party id an aggregator item states, or "" where it states none."""
    for path in LINK_PATHS:
        node: object = raw
        for step in path:
            node = node.get(step) if isinstance(node, Mapping) else None
        if isinstance(node, str) and node.strip():
            return node.strip()
    return ""


def stated_link_of(transaction: Transaction) -> str:
    """The feed uid this sighting states, or "" for a source that is not an aggregator."""
    if transaction.source not in AGGREGATORS:
        return ""
    return link_id_of(transaction.raw)


def feed_uid_of(transaction: Transaction) -> str:
    """The feed item's own uid, or "" for any other record, a derived round-up leg included.

    A round-up leg is derived from an item and is not the item, so the item's uid is not its own.
    """
    from .providers.starling import ROUND_UP_LEG_SUFFIX

    if transaction.source not in FIRST_PARTY_FEEDS or not transaction.source_id:
        return ""
    if transaction.source_id.endswith(ROUND_UP_LEG_SUFFIX):
        return ""
    return transaction.source_id
