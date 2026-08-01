"""The review queue: surfacing the cases the rules cannot settle.

The safety net that was documented but absent. matching.py promised unresolved
transactions were "flagged for human review, NOT silently inserted as new", and
nothing ever wrote to the queue - so when four matching defects were destroying
data, no backstop noticed.

The design question is what genuinely deserves attention. Flagging every new
transaction would be noise, since every genuinely new one is unresolved by
definition and there would be thousands. The interesting case is narrow: a
record that WOULD have matched on amount and date, and was kept apart only by
the same-source rule. That is precisely where a repeated payment and a
duplicate report are hardest to tell apart, and where being wrong is expensive
in both directions.
"""

from datetime import date

import pytest

from obdi.identity import content_key
from obdi.ingest import reconcile_batch
from obdi.models import SourceTier, Transaction, TransactionStatus
from obdi.store import Store


def txn(
    *,
    source: str,
    source_id: str | None = None,
    amount: int = -1499,
    day: int = 14,
    description: str = "COFFEE SHOP",
    status: TransactionStatus = TransactionStatus.BOOKED,
) -> Transaction:
    when = date(2026, 3, day)
    # A source supplying an id is authoritative by definition.
    tier = SourceTier.AUTHORITATIVE if source_id else SourceTier.SYNTHETIC
    return Transaction(
        account_id="halifax",
        amount_minor=amount,
        value_date=when,
        booking_date=when,
        description=description,
        source=source,
        source_id=source_id,
        tier=tier,
        status=status,
        content_key=content_key(
            account_id="halifax", amount_minor=amount, value_date=when, description=description
        ),
    )


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "s.sqlite3") as opened:
        yield opened


class TestAmbiguousRepeatsAreQueued:
    def test_Payment_WhenIdenticalToAnEarlierOneFromTheSameSource_QueuedForReview(self, store):
        # Kept apart by the same-source rule, which is right by default - but
        # it is also exactly the shape a genuine duplicate report takes, so a
        # human should confirm rather than the rule deciding silently.
        reconcile_batch(store, [txn(source="monzo-csv", source_id="tx-1")], digest="d1")
        summary = reconcile_batch(store, [txn(source="monzo-csv", source_id="tx-2")], digest="d2")

        assert summary.needs_review == 1
        assert len(store.transactions_for_account("halifax")) == 2

    def test_Payment_WhenQueued_StillStoredRatherThanHeldBack(self, store):
        # Review is a flag, not a gate. Withholding the transaction would make
        # the balance wrong until someone looked.
        reconcile_batch(store, [txn(source="qif", day=1)], digest="d1")
        reconcile_batch(store, [txn(source="qif", day=5)], digest="d2")

        rows = store.transactions_for_account("halifax")
        assert len(rows) == 2
        assert sum(t.amount_minor for t in rows) == -2998

    def test_Payment_WhenQueued_ReasonRecorded(self, store):
        reconcile_batch(store, [txn(source="qif", day=1)], digest="d1")
        reconcile_batch(store, [txn(source="qif", day=5)], digest="d2")

        queued = store.review_queue()
        assert len(queued) == 1
        assert "same source" in queued[0]["reason"].lower()


class TestOrdinaryTrafficIsNotQueued:
    def test_Payment_WhenGenuinelyNewAndUnlikeAnything_NotQueued(self, store):
        # Every new transaction is unresolved by definition. Flagging them all
        # would bury the cases that matter.
        summary = reconcile_batch(store, [txn(source="qif", description="TESCO")], digest="d1")
        assert summary.needs_review == 0
        assert store.review_queue() == []

    def test_Payment_WhenMatchedAcrossSources_NotQueued(self, store):
        reconcile_batch(store, [txn(source="qif")], digest="d1")
        summary = reconcile_batch(store, [txn(source="truelayer", source_id="tl-1")], digest="d2")

        assert summary.needs_review == 0
        assert store.review_queue() == []

    def test_Payment_WhenAmountDiffers_NotQueued(self, store):
        reconcile_batch(store, [txn(source="qif", amount=-1499)], digest="d1")
        summary = reconcile_batch(store, [txn(source="qif", amount=-2500)], digest="d2")

        assert summary.needs_review == 0

    def test_Payment_WhenFarApartInTime_NotQueued(self, store):
        reconcile_batch(store, [txn(source="qif", day=1)], digest="d1")
        summary = reconcile_batch(store, [txn(source="qif", day=25)], digest="d2")

        assert summary.needs_review == 0

    def test_Payment_WhenPendingSettles_MatchedNotQueued(self, store):
        pending = txn(source="truelayer", source_id="p-1", status=TransactionStatus.PENDING)
        reconcile_batch(store, [pending], digest="d1")
        settled = txn(source="truelayer", source_id="b-1", day=16)
        summary = reconcile_batch(store, [settled], digest="d2")

        assert summary.needs_review == 0
        assert len(store.transactions_for_account("halifax")) == 1


class TestQueueIsIdempotent:
    def test_Queue_WhenSameImportRepeated_EntryNotDuplicated(self, store):
        reconcile_batch(store, [txn(source="qif", day=1)], digest="d1")
        reconcile_batch(store, [txn(source="qif", day=5)], digest="d2")
        reconcile_batch(store, [txn(source="qif", day=5)], digest="d2")

        assert len(store.review_queue()) == 1
