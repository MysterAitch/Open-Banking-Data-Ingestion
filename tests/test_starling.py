import pytest

from obdi.models import TransactionStatus
from obdi.providers.starling import StarlingError, to_transaction


def feed_item(**overrides) -> dict:
    item = {
        "feedItemUid": "feed-1",
        "amount": {"currency": "GBP", "minorUnits": 1499},
        "direction": "OUT",
        "transactionTime": "2026-03-14T09:15:00.000Z",
        "source": "MASTER_CARD",
        "status": "SETTLED",
        "counterPartyName": "Tesco",
        "reference": "TESCO STORES",
    }
    item.update(overrides)
    return item


class TestAmountSign:
    def test_Payment_WhenDirectionIsOut_StoredAsNegative(self):
        # minorUnits is unsigned; ignoring direction makes every payment look
        # like income.
        assert to_transaction(feed_item(), account_id="a").amount_minor == -1499

    def test_Income_WhenDirectionIsIn_StoredAsPositive(self):
        item = feed_item(direction="IN", amount={"currency": "GBP", "minorUnits": 250000})
        assert to_transaction(item, account_id="a").amount_minor == 250000

    def test_Amount_WhenDirectionMissing_RefusedRatherThanGuessed(self):
        with pytest.raises(StarlingError, match="direction"):
            to_transaction(feed_item(direction=""), account_id="a")

    def test_Amount_WhenMinorUnitsNotAnInteger_RefusedRatherThanCoerced(self):
        item = feed_item(amount={"currency": "GBP", "minorUnits": 14.99})
        with pytest.raises(StarlingError, match="minorUnits"):
            to_transaction(item, account_id="a")


class TestInternalTransfers:
    def test_SpaceTransfer_WhenReportedInParentFeed_Dropped(self):
        # Movements between your own Spaces are real to the bank but would
        # double-count against spending.
        assert to_transaction(feed_item(source="INTERNAL_TRANSFER"), account_id="a") is None


class TestStatusHandling:
    def test_Payment_WhenSettled_StoredAsBooked(self):
        assert to_transaction(feed_item(), account_id="a").status is TransactionStatus.BOOKED

    def test_Payment_WhenPending_StoredAsPending(self):
        item = feed_item(status="PENDING")
        assert to_transaction(item, account_id="a").status is TransactionStatus.PENDING

    def test_Payment_WhenDeclined_NotStoredAtAll(self):
        # No money moved, so it is not a transaction.
        assert to_transaction(feed_item(status="DECLINED"), account_id="a") is None

    def test_Payment_WhenRefunded_StoredAsRealMovement(self):
        # A refund moved money and belongs in the ledger.
        assert to_transaction(feed_item(status="REFUNDED"), account_id="a") is not None


class TestFieldMapping:
    def test_Transaction_WhenMapped_FeedItemUidBecomesSourceId(self):
        assert to_transaction(feed_item(), account_id="a").source_id == "feed-1"

    def test_Transaction_WhenReferenceMissing_CounterpartyUsedAsDescription(self):
        item = feed_item(reference="")
        assert to_transaction(item, account_id="a").description == "Tesco"

    def test_Transaction_WhenMapped_DateTakenFromTransactionTime(self):
        transaction = to_transaction(feed_item(), account_id="a")
        assert (transaction.value_date.day, transaction.value_date.month) == (14, 3)

    def test_Transaction_WhenTransactionTimeAbsent_SettlementTimeUsed(self):
        item = feed_item(transactionTime=None, settlementTime="2026-03-16T00:00:00.000Z")
        assert to_transaction(item, account_id="a").value_date.day == 16

    def test_Transaction_WhenMapped_ContentKeyPopulatedForCrossSourceMatching(self):
        # Without this the Starling and aggregator views of one payment cannot
        # be reconciled.
        assert to_transaction(feed_item(), account_id="a").content_key
