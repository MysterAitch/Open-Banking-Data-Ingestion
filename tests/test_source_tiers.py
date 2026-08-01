"""Source tiers: how much a source's own idea of identity can be trusted.

Three tiers, and the rules follow from them rather than being re-derived at
each comparison - which is how the same reasoning previously ended up
contradicting itself between two adjacent matching tiers.

Arrived at here from the failures, then found to match YNAB's published design
closely: their import id combines amount, date and an occurrence counter, and
they separately match imports against hand-entered transactions over a wider
window. Independent convergence is about the best evidence available that the
shape is right.
"""

from datetime import date

import pytest

from obdi.identity import content_key
from obdi.matching import resolve
from obdi.models import SourceTier, Transaction, TransactionStatus
from obdi.parsers.uk_banks import AmexUkCsvParser, MonzoCsvParser, StarlingCsvParser
from obdi.providers.starling import to_transaction as starling_txn
from obdi.providers.truelayer import to_transaction as truelayer_txn


def txn(
    *,
    tier: SourceTier,
    source: str,
    source_id: str | None = None,
    day: int = 14,
    amount: int = -1499,
    description: str = "TESCO",
    occurrence: int = 0,
) -> Transaction:
    when = date(2026, 3, day)
    return Transaction(
        account_id="a",
        amount_minor=amount,
        value_date=when,
        booking_date=when,
        description=description,
        source=source,
        source_id=source_id,
        tier=tier,
        occurrence=occurrence,
        content_key=content_key(
            account_id="a", amount_minor=amount, value_date=when, description=description
        ),
    )


class TestSourcesDeclareTheirTier:
    def test_Source_WhenItSuppliesADurableId_IsAuthoritative(self):
        payload = (
            b"Transaction ID,Date,Time,Type,Name,Description,Amount,Currency\n"
            b"tx_1,14/03/2026,09:15:00,Card payment,Tesco,TESCO,-14.99,GBP\n"
        ).decode().encode("utf-8-sig")
        row = next(iter(MonzoCsvParser().parse(payload, account_id="a")))
        assert row.tier is SourceTier.AUTHORITATIVE

    def test_Source_WhenItSuppliesNoId_IsSynthetic(self):
        payload = (
            b"Date,Counter Party,Reference,Type,Amount (GBP)\n"
            b"14/03/2026,Tesco,TESCO,CARD,-14.99\n"
        )
        row = next(iter(StarlingCsvParser().parse(payload, account_id="a")))
        assert row.tier is SourceTier.SYNTHETIC
        assert row.source_id is None

    def test_Source_WhenCardExportCarriesAReference_IsAuthoritative(self):
        payload = (
            b"Date,Description,Amount,Reference\n14/03/2026,TESCO,14.99,'AT26001\n"
        )
        row = next(iter(AmexUkCsvParser().parse(payload, account_id="a")))
        assert row.tier is SourceTier.AUTHORITATIVE

    def test_Provider_WhenAggregator_IsAuthoritative(self):
        record = {
            "transaction_id": "tl-1",
            "timestamp": "2026-03-14T00:00:00Z",
            "description": "TESCO",
            "amount": -14.99,
            "currency": "GBP",
            "transaction_type": "DEBIT",
        }
        assert truelayer_txn(record, account_id="a").tier is SourceTier.AUTHORITATIVE

    def test_Provider_WhenFirstPartyBank_IsAuthoritative(self):
        item = {
            "feedItemUid": "feed-1",
            "amount": {"currency": "GBP", "minorUnits": 1499},
            "direction": "OUT",
            "transactionTime": "2026-03-14T09:15:00.000Z",
            "source": "MASTER_CARD",
            "status": "SETTLED",
            "reference": "TESCO",
        }
        assert starling_txn(item, account_id="a").tier is SourceTier.AUTHORITATIVE


class TestManualEntriesAreNeverMerged:
    def test_Entries_WhenBothTypedByAPerson_NeverMerged(self):
        # A person meant to record two things. No figures justify collapsing
        # that; it is the only input carrying intent rather than observation.
        first = txn(tier=SourceTier.MANUAL, source="manual", day=14)
        second = txn(tier=SourceTier.MANUAL, source="manual", day=15)
        assert resolve(second, [first]).is_new

    def test_Entry_WhenTypedTwiceIdentically_StillTwoEntries(self):
        first = txn(tier=SourceTier.MANUAL, source="manual")
        assert resolve(txn(tier=SourceTier.MANUAL, source="manual"), [first]).is_new


class TestImportsClaimManualEntries:
    def test_Entry_WhenLaterReportedByABank_ClaimedRatherThanDuplicated(self):
        # You note a payment; the feed reports it days later. One payment.
        typed = txn(tier=SourceTier.MANUAL, source="manual", day=14)
        imported = txn(
            tier=SourceTier.AUTHORITATIVE, source="truelayer", source_id="tl-1", day=17
        )
        assert resolve(imported, [typed]).existing is typed

    def test_Entry_WhenRememberedDateIsFurtherOut_StillClaimed(self):
        # A remembered date is approximate, so the window is wider than the one
        # between two machine-read sources.
        typed = txn(tier=SourceTier.MANUAL, source="manual", day=1)
        imported = txn(
            tier=SourceTier.AUTHORITATIVE, source="truelayer", source_id="tl-1", day=10
        )
        assert resolve(imported, [typed]).existing is typed

    def test_Entry_WhenFarBeyondEvenTheWiderWindow_NotClaimed(self):
        typed = txn(tier=SourceTier.MANUAL, source="manual", day=1)
        imported = txn(
            tier=SourceTier.AUTHORITATIVE, source="truelayer", source_id="tl-1", day=25
        )
        assert resolve(imported, [typed]).is_new


class TestAuthoritativeIdsAreDecisive:
    def test_Payments_WhenIdsDifferWithinOneSource_NeverMerged(self):
        first = txn(tier=SourceTier.AUTHORITATIVE, source="monzo-csv", source_id="tx-1")
        second = txn(tier=SourceTier.AUTHORITATIVE, source="monzo-csv", source_id="tx-2")
        assert resolve(second, [first]).is_new

    def test_Payment_WhenSettling_MatchedDespiteANewId(self):
        # The one case where a source deliberately reissues an id.
        pending = Transaction(
            account_id="a",
            amount_minor=-1499,
            value_date=date(2026, 3, 14),
            booking_date=date(2026, 3, 14),
            description="TESCO",
            source="truelayer",
            source_id="p-1",
            tier=SourceTier.AUTHORITATIVE,
            status=TransactionStatus.PENDING,
            content_key=content_key(
                account_id="a", amount_minor=-1499, value_date=date(2026, 3, 14),
                description="TESCO",
            ),
        )
        settled = txn(
            tier=SourceTier.AUTHORITATIVE, source="truelayer", source_id="b-1", day=16
        )
        assert resolve(settled, [pending]).existing is pending


class TestSyntheticFoldsIntoAuthoritative:
    def test_Export_WhenAlsoSeenByAnApi_FoldedTogether(self):
        from_file = txn(tier=SourceTier.SYNTHETIC, source="qif")
        from_api = txn(tier=SourceTier.AUTHORITATIVE, source="truelayer", source_id="tl-1")
        assert resolve(from_api, [from_file]).existing is from_file

    @pytest.mark.parametrize("occurrence", [0, 1, 2])
    def test_Export_WhenReimported_MatchedOccurrenceForOccurrence(self, occurrence):
        stored = txn(tier=SourceTier.SYNTHETIC, source="qif", occurrence=occurrence)
        again = txn(tier=SourceTier.SYNTHETIC, source="qif", occurrence=occurrence)
        assert resolve(again, [stored]).existing is stored

    def test_Export_WhenItRepeatsAPayment_RepeatsKeptApart(self):
        first = txn(tier=SourceTier.SYNTHETIC, source="qif", occurrence=0)
        second = txn(tier=SourceTier.SYNTHETIC, source="qif", occurrence=1)
        assert resolve(second, [first]).is_new
