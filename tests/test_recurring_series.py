"""Not flagging predictable recurring payments for review.

A weekly standing order lands squarely inside the fuzzy window, so every
instalment after the first looks like the ambiguous case: same amount, same
description, held apart only by the source rules. Technically true, practically
about fifty flags a year per weekly commitment - and a queue that cries wolf
weekly is one you stop reading, which defeats it precisely when it matters.

Monthly payments were never affected, being outside the window.

The distinguishing signal is regularity. Two prior instalments at a consistent
interval make a series, and a third arriving on schedule is expected rather
than suspicious. A repeated payment with NO established rhythm still gets
flagged, because that is the shape a duplicate report takes.
"""

from datetime import date, timedelta

import pytest

from obdi.identity import content_key
from obdi.ingest import reconcile_batch
from obdi.models import SourceTier, Transaction
from obdi.store import Store


def txn(*, day: int, amount: int = -5000, description: str = "STANDING ORDER") -> Transaction:
    when = date(2026, 3, 1) + timedelta(days=day)
    return Transaction(
        account_id="halifax",
        amount_minor=amount,
        value_date=when,
        booking_date=when,
        description=description,
        source="qif",
        tier=SourceTier.SYNTHETIC,
        content_key=content_key(
            account_id="halifax", amount_minor=amount, value_date=when, description=description
        ),
    )


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "s.sqlite3") as opened:
        yield opened


class TestEstablishedSeriesAreNotFlagged:
    def test_StandingOrder_WhenThirdInstalmentArrivesOnSchedule_NotFlagged(self, store):
        # Two prior instalments seven days apart establish the rhythm; a third
        # on schedule is expected, not ambiguous.
        reconcile_batch(store, [txn(day=0)], digest="d1")
        reconcile_batch(store, [txn(day=7)], digest="d2")
        summary = reconcile_batch(store, [txn(day=14)], digest="d3")

        assert summary.needs_review == 0

    def test_StandingOrder_WhenRunningForMonths_NeverAccumulatesFlags(self, store):
        # The actual complaint: roughly fifty flags a year for one commitment.
        for week in range(12):
            reconcile_batch(store, [txn(day=week * 7)], digest=f"d{week}")

        # Only the second instalment can be flagged - before it there is no
        # rhythm to recognise.
        assert len(store.review_queue()) <= 1

    def test_StandingOrder_WhenEveryInstalmentKept_NoneAreMerged(self, store):
        for week in range(12):
            reconcile_batch(store, [txn(day=week * 7)], digest=f"d{week}")

        rows = store.transactions_for_account("halifax")
        assert len(rows) == 12
        assert sum(t.amount_minor for t in rows) == -60000


class TestGenuineAmbiguityIsStillFlagged:
    def test_Payment_WhenRepeatedWithNoRhythm_StillFlagged(self, store):
        # Two payments a few days apart with nothing regular about them is the
        # shape a duplicate report takes, and still warrants a look.
        reconcile_batch(store, [txn(day=0)], digest="d1")
        summary = reconcile_batch(store, [txn(day=3)], digest="d2")

        assert summary.needs_review == 1

    def test_Payment_WhenIntervalsAreIrregular_StillFlagged(self, store):
        # Two prior instalments, but at different gaps - no series to trust.
        reconcile_batch(store, [txn(day=0)], digest="d1")
        reconcile_batch(store, [txn(day=2)], digest="d2")
        summary = reconcile_batch(store, [txn(day=7)], digest="d3")

        assert summary.needs_review >= 1

    def test_Payment_WhenAmountDiffersFromTheSeries_TreatedSeparately(self, store):
        # A different amount is not part of the established series, whatever
        # the timing looks like.
        for week in range(3):
            reconcile_batch(store, [txn(day=week * 7)], digest=f"d{week}")
        summary = reconcile_batch(store, [txn(day=21, amount=-5001)], digest="dx")

        assert summary.needs_review == 0 or summary.inserted == 1

    def test_Payment_WhenDescriptionDiffersFromTheSeries_NotTreatedAsPartOfIt(self, store):
        for week in range(3):
            reconcile_batch(store, [txn(day=week * 7)], digest=f"d{week}")
        summary = reconcile_batch(
            store, [txn(day=21, description="CASH WITHDRAWAL")], digest="dx"
        )

        # Different description, so it is not the series - and nothing else
        # resembles it either, so it is simply new.
        assert summary.inserted == 1
