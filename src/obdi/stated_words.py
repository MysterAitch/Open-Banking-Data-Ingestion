"""Every coded word a source states for a payment, kept against the sighting that stated it.

A feed item states what kind of payment it is in coded fields (`source`, `sourceSubType`,
`spendingCategory`, `counterPartyType`), and an aggregator's item in its own
(`transaction_category`, `transaction_classification`). The row keeps the raw of whichever
sighting created it, so a rule that needs what a SECOND source said had nothing to read
but the landed payload, parsed again for every row. So the words are kept per sighting
(`sighting_words`), exactly as the moments are (`stated_times`), and read from there.

WHICH FIELDS. A list, not a parse for upper-case tokens: a feed item's `reference` can be one
upper-case word that is a payee's name, and a word kept here is shown on a masked page. Each
listed field is a closed vocabulary the source owns (a payment's kind, never its text), and a
value that is not a short word of that kind is not kept. A field a source adds later is kept by
adding its name to `CODED_FIELDS`, which a rebuild then applies to every landed artefact.

WHICH SOURCES. The bank's feed and the aggregator state coded kinds. A file export and a
statement state none, so a sighting of theirs holds no words, and a round-up leg derived from
an item holds none of the item's (it is not the item).
"""

from __future__ import annotations

from collections.abc import Mapping

from .core.models import Transaction
from .payment_links import AGGREGATORS, FIRST_PARTY_FEEDS

#: The coded fields each source states, whose values are kept. The one place they are named.
CODED_FIELDS: Mapping[str, tuple[str, ...]] = {
    "starling": (
        "source",
        "sourceSubType",
        "spendingCategory",
        "counterPartyType",
        "status",
        "direction",
        "amount.currency",
        "sourceAmount.currency",
    ),
    "truelayer": (
        "transaction_type",
        "transaction_category",
        "transaction_classification",
        "currency",
    ),
}

#: The longest value kept. A coded word is short; anything longer is text and is not kept.
_LONGEST = 48


def words_in(source: str, raw: Mapping[str, object]) -> list[tuple[str, str]]:
    """Each (field, value) a record states in a coded field, a list-valued field word by word.

    A dotted name reaches a field nested in the record (`sourceAmount.currency`).
    In the order of `CODED_FIELDS`, then as stated, each pair once.
    """
    found: list[tuple[str, str]] = []
    for name in CODED_FIELDS.get(source, ()):
        value: object = raw
        for part in name.split("."):
            value = value.get(part) if isinstance(value, Mapping) else None
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, str) and 0 < len(item.strip()) <= _LONGEST:
                pair = (name, item.strip())
                if pair not in found:
                    found.append(pair)
    return found


def recorded_words(transaction: Transaction) -> list[tuple[str, str]]:
    """What to keep of this sighting: every coded word its source states for it."""
    from .providers.starling import ROUND_UP_LEG_SUFFIX

    if transaction.source not in FIRST_PARTY_FEEDS | AGGREGATORS:
        return []
    if transaction.source in FIRST_PARTY_FEEDS and (
        not transaction.source_id or transaction.source_id.endswith(ROUND_UP_LEG_SUFFIX)
    ):
        return []
    return words_in(transaction.source, transaction.raw)
