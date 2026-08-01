"""Which TrueLayer field may be trusted as a durable identifier.

TrueLayer documents two ids with opposite guarantees:

    transaction_id                      "It may change between requests."
    normalised_provider_transaction_id  "It will not change between requests."

Tier one exists precisely because a source has told us two records are the same
payment. Built on an id that changes between requests, it asserts that on no
evidence at all - and the damage is asymmetric: a re-fetch that fails to match
duplicates real money, silently, in a store meant to be the authoritative copy.
"""

from __future__ import annotations

from obdi.models import SourceTier
from obdi.providers.truelayer import to_transaction

BASE = {
    "timestamp": "2026-03-14T00:00:00Z",
    "amount": -12.5,
    "currency": "GBP",
    "description": "COFFEE",
    "transaction_type": "DEBIT",
}


class TestDurableIdentity:
    def test_Identity_WhenStableIdIsPresent_UsesItRatherThanTheVolatileOne(self):
        record = dict(
            BASE,
            transaction_id="changes-between-requests",
            normalised_provider_transaction_id="stable-forever",
        )

        transaction = to_transaction(record, account_id="acc")

        assert transaction.source_id == "stable-forever"
        assert transaction.tier is SourceTier.AUTHORITATIVE

    def test_Identity_WhenOnlyTheVolatileIdExists_ClaimsNoDurableIdAtAll(self):
        record = dict(BASE, transaction_id="changes-between-requests")

        transaction = to_transaction(record, account_id="acc")

        # Storing a volatile value as source_id is worse than storing nothing:
        # tier one would match on it, and a re-fetch under a new id would be
        # read as a different payment.
        assert transaction.source_id is None
        assert transaction.tier is SourceTier.SYNTHETIC
        assert transaction.content_key, "content must carry identity when no id can"

    def test_Identity_WhenNoIdsAtAll_StillProducesAContentKey(self):
        transaction = to_transaction(dict(BASE), account_id="acc")

        assert transaction.source_id is None
        assert transaction.tier is SourceTier.SYNTHETIC
        assert transaction.content_key
