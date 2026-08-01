"""Which sources have seen a given payment, after they have been merged.

Merging is the point: one payment observed by an aggregator and by a CSV export
should end up as one transaction, not two. But the merged row can only carry
one source, so by default the act of merging destroys the very evidence that
made it trustworthy - namely that two independent routes agreed.

Without that record, "present in the API but missing from the export" is
unanswerable, because a payment both saw is indistinguishable from one only the
last writer saw.
"""

from __future__ import annotations

from datetime import date

from obdi.ingest import reconcile_batch
from obdi.models import SourceTier, Transaction
from obdi.store import Store


def txn(source, *, source_id=None, day=5, amount=-2500, tier=SourceTier.SYNTHETIC):
    return Transaction(
        account_id="current",
        amount_minor=amount,
        currency="GBP",
        value_date=date(2026, 3, day),
        booking_date=date(2026, 3, day),
        description="RENT",
        source=source,
        source_id=source_id,
        tier=tier,
        content_key=f"key-{day}-{amount}",
    )


class TestWhoHasSeenThisPayment:
    def test_Provenance_WhenTwoSourcesSeeOnePayment_BothAreRecorded(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(store, [txn("truelayer", source_id="tl-1")], digest="d1")
            reconcile_batch(store, [txn("halifax-qif")], digest="d2")

            held = store.all_transactions()
            assert len(held) == 1, "one payment, seen twice, is one transaction"
            assert store.sources_for(held[0].entity_id) == ["halifax-qif", "truelayer"]

    def test_Provenance_WhenOnlyOneSourceSeesIt_OnlyThatOneIsRecorded(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(store, [txn("truelayer", source_id="tl-1")], digest="d1")

            held = store.all_transactions()
            assert store.sources_for(held[0].entity_id) == ["truelayer"]

    def test_Provenance_WhenTheSameSourceSeesItTwice_IsNotDoubleCounted(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(store, [txn("truelayer", source_id="tl-1")], digest="d1")
            reconcile_batch(store, [txn("truelayer", source_id="tl-1")], digest="d2")

            held = store.all_transactions()
            # Re-pulling the same feed is routine and says nothing new about
            # corroboration - two sightings by one source are not two sources.
            assert store.sources_for(held[0].entity_id) == ["truelayer"]
