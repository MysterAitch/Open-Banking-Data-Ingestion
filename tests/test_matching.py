from datetime import date

from obdi.identity import content_key
from obdi.matching import MatchTier, pair_internal_transfers, resolve, supersede
from obdi.models import Transaction, TransactionStatus


def txn(
    *,
    account: str = "acct-1",
    amount: int = -1499,
    day: int = 14,
    description: str = "TESCO",
    source: str = "test",
    source_id: str | None = None,
    entity_id: str = "",
    status: TransactionStatus = TransactionStatus.BOOKED,
) -> Transaction:
    # `source` is explicit because it is load-bearing: two records from ONE
    # source are two payments, so a test that means "the same payment seen
    # twice" must say which two doors it came through. Several tests here
    # originally left it at the default and so asserted a merge that would
    # silently destroy repeated payments.
    when = date(2026, 3, day)
    return Transaction(
        account_id=account,
        amount_minor=amount,
        value_date=when,
        booking_date=when,
        description=description,
        source=source,
        source_id=source_id,
        status=status,
        entity_id=entity_id,
        content_key=content_key(
            account_id=account, amount_minor=amount, value_date=when, description=description
        ),
    )


class TestSourceIdMatching:
    def test_Transaction_WhenSameProviderIdArrivesTwice_LinkedRatherThanDuplicated(self):
        existing = txn(source_id="tx-abc")
        result = resolve(txn(source_id="tx-abc"), [existing])
        assert result.tier is MatchTier.SOURCE_ID
        assert result.existing is existing

    def test_Transaction_WhenSameProviderIdButDifferentAccount_NotMatched(self):
        # Provider ids are only unique within an account.
        existing = txn(account="acct-1", source_id="tx-abc")
        result = resolve(txn(account="acct-2", source_id="tx-abc"), [existing])
        assert result.existing is None


class TestContentKeyMatching:
    def test_Transaction_WhenCsvBackfillOverlapsApiPull_MatchedOnContent(self):
        # The overlap is not optional: export caps force repeated downloads.
        from_api = txn(source="truelayer", source_id="tx-abc")
        from_csv = txn(source="qif", source_id=None)
        result = resolve(from_csv, [from_api])
        assert result.tier is MatchTier.CONTENT_KEY
        assert result.existing is from_api

    def test_Transaction_WhenDescriptionCosmeticallyDifferent_StillMatched(self):
        stored = txn(source="truelayer", description="TESCO STORES")
        incoming = txn(source="qif", description="Tesco Stores CARD 4912")
        assert resolve(incoming, [stored]).tier is MatchTier.CONTENT_KEY

    def test_Transaction_WhenTwoIdenticalPurchasesInOneSource_NotMatched(self):
        # The mirror case, and the one that was missing: two identical
        # purchases through ONE door are two payments, not one seen twice.
        stored = txn(source="monzo-csv", source_id="tx-1", description="COFFEE")
        incoming = txn(source="monzo-csv", source_id="tx-2", description="COFFEE")
        assert resolve(incoming, [stored]).tier is MatchTier.UNRESOLVED


class TestFuzzyMatching:
    def test_Transaction_WhenDateShiftsWithinWindow_MatchedFuzzily(self):
        stored = txn(source="qif", day=14, description="TESCO")
        incoming = txn(source="truelayer", day=18, description="TESCO STORES LTD")
        result = resolve(incoming, [stored])
        assert result.tier is MatchTier.FUZZY
        assert result.existing is stored

    def test_Transaction_WhenDateShiftsBeyondWindow_NotMatched(self):
        stored = txn(source="qif", day=1, description="TESCO")
        incoming = txn(source="truelayer", day=20, description="TESCO STORES LTD")
        assert resolve(incoming, [stored]).tier is MatchTier.UNRESOLVED

    def test_Transaction_WhenSeveralCandidatesInWindow_NearestDateChosen(self):
        near = txn(source="qif", day=15, description="A")
        far = txn(source="qif", day=19, description="B")
        result = resolve(txn(source="truelayer", day=16, description="C"), [far, near])
        assert result.existing is near

    def test_Transaction_WhenDateShiftsWithinOneSource_NotMatched(self):
        # A weekly standing order lands squarely inside the window. Merging
        # these was how three instalments became one row.
        stored = txn(source="qif", day=1, description="STANDING ORDER")
        incoming = txn(source="qif", day=8, description="STANDING ORDER")
        assert resolve(incoming, [stored]).tier is MatchTier.UNRESOLVED

    def test_Transaction_WhenBothSidesCarryDifferentProviderIds_NotFuzzyMatched(self):
        # Two authoritative ids that did not match are authoritatively
        # different transactions; collapsing them would be a false positive.
        stored = txn(day=14, source_id="tx-one", description="A")
        incoming = txn(day=15, source_id="tx-two", description="B")
        assert resolve(incoming, [stored]).tier is MatchTier.UNRESOLVED

    def test_Transaction_WhenAmountDiffers_NotMatchedEvenOnSameDay(self):
        stored = txn(amount=-1499)
        assert resolve(txn(amount=-2000), [stored]).tier is MatchTier.UNRESOLVED


class TestUnresolvedHandling:
    def test_Transaction_WhenNothingMatches_FlaggedForReviewNotSilentlyGuessed(self):
        result = resolve(txn(), [])
        assert result.tier is MatchTier.UNRESOLVED
        assert result.needs_review
        assert result.is_new


class TestSupersession:
    def test_Transaction_WhenPendingSettlesWithNewIdAndDate_SupersedesKeepingIdentity(self):
        pending = txn(
            day=14, source_id="pend-1", entity_id="ent-1", status=TransactionStatus.PENDING
        )
        settled_observation = txn(day=16, source_id="book-9", status=TransactionStatus.BOOKED)

        result = supersede(pending, settled_observation)

        assert result.entity_id == "ent-1"
        assert result.source_id == "book-9"
        assert result.status is TransactionStatus.BOOKED
        assert result.value_date == date(2026, 3, 16)

    def test_Transaction_WhenSuperseded_EarliestBookingDateRetained(self):
        pending = txn(day=14, entity_id="ent-1", status=TransactionStatus.PENDING)
        assert supersede(pending, txn(day=16)).booking_date == date(2026, 3, 14)


class TestInternalTransfers:
    def test_Transfer_WhenMovedBetweenOwnAccounts_BothSidesFlagged(self):
        # Otherwise this inflates spending on one account and income on the other.
        out = txn(account="current", amount=-50000, day=14, description="TO SAVINGS")
        into = txn(account="savings", amount=50000, day=14, description="FROM CURRENT")

        result = pair_internal_transfers([out, into])

        assert all(t.is_internal_transfer for t in result)

    def test_Transfer_WhenSidesSettleADayApart_StillPaired(self):
        out = txn(account="current", amount=-50000, day=14)
        into = txn(account="savings", amount=50000, day=15)
        assert all(t.is_internal_transfer for t in pair_internal_transfers([out, into]))

    def test_Transfer_WhenSidesFarApartInTime_NotPaired(self):
        out = txn(account="current", amount=-50000, day=1)
        into = txn(account="savings", amount=50000, day=20)
        assert not any(t.is_internal_transfer for t in pair_internal_transfers([out, into]))

    def test_Spending_WhenUnrelatedDebitAndCreditDifferInAmount_NotPaired(self):
        spend = txn(account="current", amount=-1499, day=14)
        salary = txn(account="current", amount=250000, day=14)
        assert not any(t.is_internal_transfer for t in pair_internal_transfers([spend, salary]))

    def test_Spending_WhenDebitAndCreditOnSameAccount_NotPairedAsTransfer(self):
        # A refund is not a transfer between accounts.
        spend = txn(account="current", amount=-1499, day=14)
        refund = txn(account="current", amount=1499, day=14)
        assert not any(t.is_internal_transfer for t in pair_internal_transfers([spend, refund]))

    def test_Transfer_WhenTwoIdenticalTransfersOccur_EachSideConsumedOnce(self):
        # A repeated standing order must not chain-match into one pair.
        out_a = txn(account="current", amount=-10000, day=14)
        out_b = txn(account="current", amount=-10000, day=15)
        in_a = txn(account="savings", amount=10000, day=14)
        in_b = txn(account="savings", amount=10000, day=15)

        result = pair_internal_transfers([out_a, out_b, in_a, in_b])

        assert sum(1 for t in result if t.is_internal_transfer) == 4
